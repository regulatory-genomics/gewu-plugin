"""One SVG writer with durable undo and compare-and-replace semantics."""
import fcntl
import json
import os
from pathlib import Path
import tempfile
import threading
from uuid import uuid4

from lxml import etree as E

from native import Failure
import svg


def sync_dir(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def atomic(path,data,create=False):
    with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.gewu-',delete=False) as stream:
        tmp=Path(stream.name)
        try:
            if path.exists() and not create:
                os.fchmod(stream.fileno(),path.stat().st_mode & 0o777)
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
            if create: os.link(tmp,path)
            else: os.replace(tmp,path)
            sync_dir(path.parent)
        finally:
            tmp.unlink(missing_ok=True)


def write_json(path,data):
    atomic(path,json.dumps(data,ensure_ascii=False,separators=(',',':')).encode())


class Document:
    def __init__(self,root,binding,engine):
        self.root=root.resolve(); self.path=(binding/'figure.svg' if binding.is_dir() else binding).resolve()
        if not self.path.is_relative_to(self.root) or self.path.suffix.lower()!='.svg' or not self.path.parent.is_dir():
            raise Failure('INVALID_PATH','Bind a workspace SVG or an existing figure directory')
        self.uri_path=self.path.relative_to(self.root).as_posix(); self.engine=engine
        self.cache=svg.inside(self.root,'.gewu/inkscape/'+svg.digest(self.uri_path.encode())); self.cache.mkdir(parents=True,exist_ok=True)
        self.snapshots=svg.inside(self.root,'snapshots',self.cache); self.snapshots.mkdir(exist_ok=True)
        self.previews=svg.inside(self.root,'previews',self.cache); self.previews.mkdir(exist_ok=True)
        self.index=self.cache/'history.json'; self.pending=self.cache/'pending.json'; self.checkout_file=self.cache/'checkout.json'
        self.mutex=threading.RLock(); self.cached=None
        fd=os.open(self.cache/'owner.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        self.owner=os.fdopen(fd,'rb')
        try:
            try: fcntl.flock(self.owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as e: raise Failure('DOCUMENT_BUSY','Another editor owns this figure') from e
            self.recover()
            if not self.index.exists():
                self.record=self.fresh(self.current_hash())
                if self.record['hash'] is not None: self.store(self.raw())
                write_json(self.index,self.record)
            else: self.record=json.loads(self.index.read_bytes())
        except BaseException:
            self.close(); raise

    def close(self):
        self.owner.close()

    def raw(self):
        if self.path.is_symlink(): raise Failure('EXTERNAL_MODIFICATION','Document was replaced with a symlink')
        return svg.read_file(self.path) if self.path.exists() else None

    def current_hash(self):
        data=self.raw(); return svg.digest(data) if data is not None else None

    @staticmethod
    def fresh(content):
        return {'epoch':uuid4().hex,'sequence':0,'hash':content,'entries':[],'cursor':0}

    @property
    def revision(self):
        r=self.record
        return f"{r['epoch']}:{r['sequence']}:{r['hash'] or 'absent'}" if r['hash'] is not None else None

    def store(self,data):
        if data is None: return None
        key=svg.digest(data); path=self.snapshots/(key+'.svg')
        if not path.exists(): atomic(path,data,create=True)
        return key

    def snapshot(self,key):
        path=self.snapshots/(key+'.svg'); data=path.read_bytes()
        if svg.digest(data)!=key: raise Failure('OUTCOME_UNKNOWN','History snapshot digest mismatch')
        return data

    def recover(self):
        if not self.pending.is_file(): return
        pending=json.loads(self.pending.read_bytes()); observed=self.current_hash()
        if observed==pending['after']['hash']:
            write_json(self.index,pending['after'])
        elif observed!=pending['before']:
            raise Failure('OUTCOME_UNKNOWN','Unsettled transaction conflicts with an external edit; preserve the SVG and pending journal')
        self.pending.unlink(); sync_dir(self.cache)

    def check(self,expected, *, external=False, handoff=False):
        if self.pending.exists():
            self.recover(); self.record=json.loads(self.index.read_bytes())
        if expected!=self.revision:
            raise Failure('STALE_REVISION','Document changed; refresh state before editing',True)
        if not handoff and self.checkout_file.exists():
            raise Failure('DOCUMENT_CHECKED_OUT','Return or cancel the desktop checkout first')
        if not external and self.current_hash()!=self.record['hash']:
            raise Failure('EXTERNAL_MODIFICATION','SVG changed outside GeWu; review and explicitly reload')

    def validate(self,data):
        root=svg.parse(data)
        svg.validate(root,self.root,self.path.parent)
        svg.pages(root)
        return root

    def measure(self,root,control=None):
        data=svg.encode(root)
        with tempfile.TemporaryDirectory(dir=self.cache) as temp:
            path=Path(temp)/'measure.svg'; path.write_bytes(data)
            return self.engine.query(path,control)

    def prepare(self,data,control=None,page=None):
        root=self.validate(data)
        key=svg.digest(data)
        page=page or 1
        if not 1<=page<=len(svg.pages(root)): raise Failure('INVALID_GEOMETRY','Unknown page number')
        part=svg.digest((self.engine.version+':96:'+str(page)).encode())[:12]
        preview=self.previews/f'{key}-{part}.png'
        if not preview.is_file():
            with tempfile.TemporaryDirectory(dir=self.cache) as temp:
                source=Path(temp)/'figure.svg'; source.write_bytes(svg.encode(svg.page_copy(root,page))); target=source.with_suffix('.png')
                self.engine.export(source,target,'png',control=control)
                os.replace(target,preview)
        return root,preview

    def preview(self,expected,control=None,page=None):
        with self.mutex:
            if expected!=self.revision or self.record['hash'] is None:
                raise Failure('STALE_REVISION','Refresh state before requesting a preview',True)
            return self.prepare(self.snapshot(self.record['hash']),control,page)[1]

    def commit(self,raw,new,before, *, call=None):
        if self.current_hash()!=before:
            raise Failure('EXTERNAL_MODIFICATION','SVG changed before commit; review and explicitly reload')
        try:
            write_json(self.pending,{'before':before,'after':new,'call':call})
            if raw is None:
                self.path.unlink(); sync_dir(self.path.parent)
            else:
                atomic(self.path,raw,create=before is None)
            write_json(self.index,new)
            self.pending.unlink(); sync_dir(self.cache)
        except OSError as e:
            if self.pending.exists():
                try:
                    self.recover(); self.record=json.loads(self.index.read_bytes())
                    if self.record==new: return
                except (OSError,Failure,ValueError):
                    raise Failure('OUTCOME_UNKNOWN','Commit could not be reconciled; inspect state before any retry') from e
            raise Failure('WRITE_FAILED',f'Document commit stopped: {e.strerror}') from e
        self.record=new

    def result(self,affected,old):
        cursor=self.record['cursor']; entries=self.record['entries']
        return {'revision':self.revision,'beforeRevision':old,'affected':list(dict.fromkeys(affected)),
                'canUndo':cursor>0 and entries[cursor-1]['before'] is not None,'canRedo':cursor<len(entries)}

    def put(self,data,expected,affected,label,control=None,call=None):
        before=self.record['hash']; after=svg.digest(data)
        if before==after: return self.result(affected,expected)
        self.prepare(data,control)
        if control and control.cancelled.is_set(): raise Failure('CANCELLED','Cancelled before commit')
        self.store(data)
        entries=self.record['entries'][:self.record['cursor']]
        entries.append({'before':before,'after':after,'label':label})
        new={**self.record,'sequence':self.record['sequence']+1,'hash':after,'entries':entries,'cursor':len(entries)}
        self.commit(data,new,before,call=call)
        return self.result(affected,expected)

    def edit(self,expected,operations,control=None,call=None):
        with self.mutex:
            self.check(expected)
            if not 1<=len(operations)<=100: raise Failure('INVALID_ARGUMENT','Provide 1–100 operations')
            if self.revision is None:
                if len(operations)!=1 or operations[0]['op']!='create_document':
                    raise Failure('DOCUMENT_MISSING','Create the figure first')
                op=operations[0]; root=svg.new_page(op['width'],op['height'])
                if 'margin' in op: svg.set_page(root,op['width'],op['height'],op['margin'])
                affected=[]
            else:
                root=self.validate(self.snapshot(self.record['hash']))
                affected=[]
                for op in operations:
                    if control and control.cancelled.is_set(): raise Failure('CANCELLED','Cancelled before commit')
                    affected.extend(svg.apply(root,op,self.root,lambda candidate:self.measure(candidate,control)))
            return self.put(svg.encode(root),expected,affected,operations[0]['op'],control,call)

    def history(self,expected,step,control=None,call=None):
        with self.mutex:
            self.check(expected)
            r=self.record; target=r['cursor']+step
            if not 0<=target<=len(r['entries']): return self.result([],expected)
            after=r['entries'][target]['before'] if step<0 else r['entries'][target-1]['after']
            if after is None: raise Failure('INVALID_ARGUMENT','Creation is the history boundary')
            if control and control.cancelled.is_set(): raise Failure('CANCELLED','Cancelled before commit')
            self.commit(self.snapshot(after),{**r,'cursor':target,'hash':after,'sequence':r['sequence']+1},r['hash'],call=call)
            return self.result([],expected)

    def reload(self,expected,observed,control=None):
        with self.mutex:
            self.check(expected,external=True)
            data=self.raw()
            if data is None or svg.digest(data)!=observed:
                raise Failure('STALE_REVISION','External file changed again; refresh state',True)
            self.prepare(data,control)
            if self.current_hash()!=observed: raise Failure('STALE_REVISION','External file changed again; refresh state',True)
            self.store(data); new=self.fresh(observed); write_json(self.index,new); self.record=new
            return self.result([],expected)

    def checkout(self,expected,output):
        with self.mutex:
            self.check(expected)
            if self.record['hash'] is None: raise Failure('DOCUMENT_MISSING','Create the figure first')
            relative=output.relative_to(self.root).as_posix()
            atomic(output,self.snapshot(self.record['hash']),create=True)
            write_json(self.checkout_file,{'revision':expected,'path':relative,'hash':self.record['hash']})
            return relative

    def checkin(self,expected,accept,control=None,*,returned=None):
        with self.mutex:
            self.check(expected,handoff=True)
            if not self.checkout_file.exists(): raise Failure('INVALID_ARGUMENT','No desktop checkout is open')
            ticket=json.loads(self.checkout_file.read_bytes())
            if accept:
                if ticket['revision']!=expected: raise Failure('STALE_REVISION','Checkout baseline no longer matches',True)
                data=returned if returned is not None else svg.read_file(svg.inside(self.root,ticket['path']))
                before=self.validate(self.snapshot(ticket['hash'])); after=self.validate(data)
                if before.get(svg.M('figure')) and before.get(svg.M('figure'))!=after.get(svg.M('figure')):
                    raise Failure('INVALID_ARGUMENT','Returned SVG has a different figure identity')
                if after.attrib.pop(svg.M('nativeCommit'),None) is not None: data=svg.encode(after)
                result=self.put(data,expected,[],'desktop check-in',control)
            else: result=self.result([],expected)
            self.checkout_file.unlink(); sync_dir(self.cache)
            return result

    def state(self,control=None):
        with self.mutex:
            if self.pending.exists():
                self.recover(); self.record=json.loads(self.index.read_bytes())
            observed=self.current_hash(); r=self.record
            data={'exists':r['hash'] is not None,'revision':self.revision,'observedHash':observed,
                  'conflict':observed!=r['hash'],'checkedOut':self.checkout_file.exists(),
                  'canUndo':r['cursor']>0 and r['entries'][r['cursor']-1]['before'] is not None,
                  'canRedo':r['cursor']<len(r['entries']),'objects':[]}
            if 'checkedOut' in data and data['checkedOut']:
                data['checkoutPath']=json.loads(self.checkout_file.read_bytes())['path']
            if not data['exists']: return data
            if not self.cached or self.cached[0]!=r['hash']:
                root=self.validate(self.snapshot(r['hash']))
                width,height,*_=svg.page_frame(root)
                boxes=self.measure(root,control)
                objects=[]
                for key,node in svg.candidates(root):
                    item={'id':key,'kind':node.get(svg.M('role'),E.QName(node).localname),
                          'bbox':boxes.get(key),'locked':svg.locked(node)}
                    if node.tag==svg.Q('text'): item['text']=''.join(node.itertext())[:2048]
                    if node.get(svg.M('source')):
                        item.update(source=node.get(svg.M('source')),sourceHash=node.get(svg.M('sourceHash')))
                    if node.get(svg.M('rasterWidth')): item['rasterWidth']=int(node.get(svg.M('rasterWidth')))
                    objects.append(item)
                self.cached=(r['hash'],{'width':width,'height':height,'margin':float(root.get(svg.M('margin'),'0'))},objects,
                             root.get(svg.M('figure')) or svg.digest(self.uri_path.encode())[:24],svg.pages(root),
                             list(svg.meta(root,'rules')),list(svg.meta(root,'presets')))
            data.update(page=self.cached[1],objects=self.cached[2],figureId=self.cached[3],pages=self.cached[4],rules=self.cached[5],presets=self.cached[6]); return data

    def export(self,expected,output,fmt,dpi=96,text='preserve',control=None,page=None):
        with self.mutex:
            if expected!=self.revision or self.record['hash'] is None:
                raise Failure('STALE_REVISION','Refresh state before exporting',True)
            data=self.snapshot(self.record['hash'])
            root=self.validate(data); pages=svg.pages(root)
            if page is not None and not 1<=page<=len(pages): raise Failure('INVALID_GEOMETRY','Unknown page number')
            if fmt=='svg' and page is not None and len(pages)>1:
                raise Failure('INVALID_ARGUMENT','Editable SVG exports contain the whole figure; use PDF or PNG for one page')
            if fmt=='png':
                page=page or 1; p=pages[page-1]
                if p['width']*p['height']*(dpi/25.4)**2>60_000_000:
                    raise Failure('INVALID_GEOMETRY','PNG export exceeds 60 megapixels; reduce DPI')
            render_data=svg.encode(svg.page_copy(root,page)) if page and fmt!='svg' else data
        with tempfile.TemporaryDirectory(dir=output.parent) as temp:
            source=Path(temp)/'source.svg'; source.write_bytes(render_data)
            target=Path(temp)/('figure.'+fmt)
            if fmt=='svg': target.write_bytes(data)
            else: self.engine.export(source,target,fmt,dpi,text,control=control)
            os.link(target,output)
