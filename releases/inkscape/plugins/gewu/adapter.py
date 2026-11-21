"""GeWu v1 stdio adapter for the figure editor."""
import json
import mimetypes
from pathlib import Path
import threading
from urllib.parse import quote, unquote

from jsonschema import Draft202012Validator

from contract import TOOLS
from gewu_plugin import ProtocolError, serve
from document import Document, write_json
from live import Live
from remote import Remote
from native import Engine, Failure
import svg
import workflow


MIME={'inkscape.document':'image/svg+xml','inkscape.preview':'image/png','inkscape.provenance':'application/json'}


def success(data,resources=(),changes=()):
    return {'ok':True,'data':data,'resources':list(resources),**({'changes':list(changes)} if changes else {})}


def bounded(items,args):
    start=args.get('offset',0); out=[]; size=0
    for item in items[start:start+args.get('limit',20)]:
        cost=len(json.dumps(item).encode())+1
        if size+cost>4200:
            if not out: raise Failure('RESULT_TOO_LARGE','Inspect a narrower set or resource')
            break
        out.append(item); size+=cost
    end=start+len(out)
    return out,end if end<len(items) else None


class Adapter:
    def __init__(self):
        self.doc=None; self.views={}; self.view_lock=threading.Lock()
        self.validators={d['name']:Draft202012Validator(d['inputSchema']) for d in TOOLS}

    def activate(self,params):
        try:
            self.root=Path(params['workspaceRoot']).resolve(); ref=params.get('bindingResource') or {}
            if ref.get('type')!='inkscape.document': raise Failure('INVALID_PATH','Open an inkscape.document resource')
            binding=self.from_uri(ref['uri']); settings=params.get('settings',{})
            self.live_directory=settings.get('nativeSessionDirectory')
            self.live_remote=settings.get('nativeRemote')
            self.doc=Document(self.root,binding,Engine(settings.get('inkscapeExecutable'),settings.get('timeoutSeconds',30)))
            return {'tools':TOOLS,'serviceEndpoints':[]}
        except Failure as e:
            raise ProtocolError(e.code,str(e),e.retryable) from e

    def deactivate(self,_params):
        if self.doc: self.doc.close()

    def from_uri(self,uri):
        prefix='workspace:///'
        if not uri.startswith(prefix): raise Failure('INVALID_PATH','Use a workspace resource URI')
        return svg.inside(self.root,unquote(uri[len(prefix):],errors='strict'))

    def reference(self,path,kind):
        mimetype=MIME.get(kind) or mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        revision=self.doc.revision if kind=='inkscape.document' else svg.digest(path.read_bytes())
        return {'uri':'workspace:///'+quote(path.relative_to(self.root).as_posix()),'type':kind,'mimeType':mimetype,'revision':revision}

    def resolve(self,ref):
        try:
            path=self.from_uri(ref['uri']); kind=ref['type']; d=self.doc
            allowed=(kind=='inkscape.document' and path==d.path and d.current_hash()==d.record['hash']) or (
                kind=='inkscape.preview' and path.parent==d.previews and path.suffix=='.png') or (
                kind in ('inkscape.export','inkscape.provenance') and path.is_relative_to(d.cache/'outputs') and path.suffix in ('.svg','.png','.pdf','.json'))
            if not allowed or not path.is_file(): raise Failure('INVALID_PATH','Resource is not an owned figure artifact')
            current=self.reference(path,kind)
            if ref.get('revision')!=current['revision']: raise Failure('STALE_REVISION','Resolve the exact current resource revision',True)
            return {'kind':'file','path':str(path.relative_to(self.root)),'mimeType':current['mimeType'],'revision':current['revision']}
        except Failure as e:
            raise ProtocolError(e.code,str(e),e.retryable) from e

    def call(self,tool,args,context,control):
        try:
            validator=self.validators.get(tool)
            error=next(validator.iter_errors(args),None) if validator else None
            if validator is None or error:
                raise Failure('INVALID_ARGUMENT',f'Invalid {tool} arguments at {list(error.path) if error else []}')
            if control.cancelled.is_set(): raise Failure('CANCELLED','Call cancelled before execution')
            return self.dispatch(tool,args,context,control)
        except Failure as e:
            return {'ok':False,'error':{'code':e.code,'message':str(e)[:1000],'retryable':e.retryable},'resources':[]}
        except (ValueError,KeyError,TypeError,OverflowError) as e:
            return {'ok':False,'error':{'code':'INVALID_ARGUMENT','message':str(e)[:500],'retryable':False},'resources':[]}
        except OSError as e:
            return {'ok':False,'error':{'code':'IO_FAILED','message':e.strerror or 'Workspace I/O failed','retryable':False},'resources':[]}

    def view(self,tool,args,context):
        key=(context.get('viewId'),context.get('viewEpoch'))
        if None in key: raise Failure('VIEW_NOT_READY','Focus a live figure view first')
        with self.view_lock:
            if tool=='view.attach': self.views[key]=(-1,[]); return success({})
            if tool=='view.detach': self.views.pop(key,None); return success({})
            if key not in self.views: raise Failure('VIEW_NOT_READY','Figure view is no longer attached')
            seq,selected=self.views[key]
            with self.doc.mutex:
                state=self.doc.state()
                available={x['id'] for x in state['objects']}
                if tool=='view.selection':
                    if state['revision']!=args['revision']: raise Failure('STALE_REVISION','Refresh before selecting',True)
                    if args['sequence']>seq:
                        self.views[key]=(args['sequence'],[x for x in dict.fromkeys(args['ids']) if x in available])
                    return success({})
                return success({'ids':[x for x in selected if x in available],'revision':state['revision']})

    def dispatch(self,tool,args,context,control):
        d=self.doc
        if tool.startswith('view.') or tool=='selection': return self.view(tool,args,context)
        if tool=='diagnose': return success({'version':d.engine.version,'document':d.uri_path,'formats':['svg','pdf','png'],
                                             'coordinateUnit':'mm','desktopLiveConfigured':bool(self.live_directory or self.live_remote)})
        if tool.startswith('live_'):
            with d.mutex:
                if not (self.live_directory or self.live_remote) or not d.checkout_file.is_file():
                    raise Failure('BRIDGE_UNAVAILABLE','Configure and launch the dedicated desktop session for this checkout')
                ticket=json.loads(d.checkout_file.read_bytes())
                bridge=Remote(self.live_remote,ticket['hash']) if self.live_remote else Live(self.live_directory,svg.inside(self.root,ticket['path']),d.engine)
                if tool=='live_edit':
                    return success(bridge.edit(args['expectedRevision'],args['operations'],self.root,control))
                if tool=='live_follow':
                    info,data=bridge.capture(args.get('knownRevision'))
                    payload={'revision':info['revision'],'selection':info['selection'][:100],'selectionCount':len(info['selection'])}
                    if data is None: return success(payload)
                    _,path=d.prepare(data,control,args.get('page')); ref=self.reference(path,'inkscape.preview')
                    return success({**payload,'preview':ref},[ref])
                info,data=bridge.capture(); revision=info['revision']
                if 'revision' in args and args['revision']!=revision:
                    raise Failure('STALE_REVISION','Native document changed; refresh live state',True)
                if tool=='live_render':
                    _,path=d.prepare(data,control,args.get('page')); ref=self.reference(path,'inkscape.preview')
                    return success({'revision':revision,'preview':ref},[ref])
                root=d.validate(data); boxes=d.measure(root,control)
                items=[{'id':key,'kind':node.get(svg.M('role'),svg.E.QName(node).localname),'bbox':boxes.get(key),'locked':svg.locked(node)}
                       for key,node in svg.candidates(root)]
                page,cursor=bounded(items,args)
                return success({'revision':revision,'selection':info['selection'][:100],'selectionCount':len(info['selection']),
                                'pages':svg.pages(root),'objects':page,'total':len(items),'nextOffset':cursor,'coordinateUnit':'mm'})
        if tool=='handoff_status':
            info=json.loads(svg.read_file(svg.inside(self.root,args['path'])))
            state=d.state(control)
            if not isinstance(info,dict) or info.get('figureId')!=state.get('figureId') or info.get('consumer') not in ('paper','manimble'):
                raise Failure('INVALID_ARGUMENT','Handoff manifest belongs to a different figure or consumer')
            return success({'figureId':state['figureId'],'revision':state['revision'],'publishedRevision':info['revision'],
                            'consumer':info['consumer'],'status':'current' if info['revision']==state['revision'] else 'update_available'})
        if tool in ('state','inspect','sources','check_layout','inspect_inner'):
            with d.mutex:
                state=d.state(control)
                if 'revision' in args and args['revision']!=state['revision']:
                    raise Failure('STALE_REVISION','Refresh state before paging',True)
                items=state.pop('objects')
                if tool=='sources': items=workflow.sources(self.root,items); key='sources'
                elif tool=='check_layout': items=workflow.check_layout(state,items); key='issues'
                elif tool=='inspect_inner':
                    items=workflow.inner(d.validate(d.snapshot(d.record['hash'])),args['panel']) if state['exists'] else []; key='objects'
                else:
                    key='objects'
                    if 'ids' in args:
                        by_id={x['id']:x for x in items}
                        missing=[x for x in args['ids'] if x not in by_id]
                        if missing: raise Failure('OBJECT_NOT_FOUND',f'Unknown object {missing[0]}')
                        items=[by_id[x] for x in args['ids']]
                page,cursor=bounded(items,args)
                info=state if tool=='state' else {'revision':state['revision']}
                return success({**info,key:page,'total':len(items),'nextOffset':cursor,'coordinateUnit':'mm'})
        if tool=='render':
            path=d.preview(args['revision'],control,args.get('page')); ref=self.reference(path,'inkscape.preview')
            return success({'revision':args['revision'],'preview':ref},[ref])
        call=context['callId']
        if tool in ('export','handoff','checkout'):
            out=svg.inside(self.root,'outputs/'+svg.digest(call.encode())[:32],d.cache); out.mkdir(parents=True,exist_ok=False)
        if tool=='checkout':
            where=d.checkout(args['expectedRevision'],out/'desktop.svg')
            ref=self.reference(out/'desktop.svg','inkscape.export')
            return success({'path':where,'revision':d.revision,'artifact':ref},[ref])
        if tool in ('export','handoff'):
            with d.mutex:
                state=d.state(control); fmt=args.get('format','png')
                if tool=='handoff' and args['consumer']=='manimble' and fmt!='png':
                    raise Failure('INVALID_ARGUMENT','Manimble handoff requires PNG')
                path=out/('figure.'+fmt)
                d.export(args['revision'],path,fmt,args.get('dpi',300),args.get('text','preserve'),control,args.get('page'))
                ref=self.reference(path,'inkscape.export'); info=out/'provenance.json'
                details={'revision':args['revision'],'figureId':state['figureId'],'engine':d.engine.version,'page':state['page'],
                         'pageNumber':args.get('page'),'artifact':ref,'dpi':args.get('dpi',300) if fmt=='png' else None,
                         'textPolicy':args.get('text','preserve') if fmt=='pdf' else None,
                         'sources':[{k:o[k] for k in ('id','source','sourceHash')} for o in state['objects'] if 'source' in o]}
                handoff={}
                if tool=='handoff':
                    handoff=workflow.handoff(args['consumer'],path.relative_to(self.root).as_posix())
                    handoff['manifestPath']=info.relative_to(self.root).as_posix()
                    if 'project' in args:
                        handoff.update(workflow.publish(self.root,args['project'],args['consumer'],state['figureId'],args['revision'],path))
                    details.update(handoff)
                write_json(info,details); provenance=self.reference(info,'inkscape.provenance')
                payload={'revision':args['revision'],'figureId':state['figureId'],'artifact':ref,'provenance':provenance}
                payload.update(handoff)
                return success(payload,[ref,provenance])
        expected=args['expectedRevision']
        with d.mutex:
            if tool=='edit': result=d.edit(expected,args['operations'],control,call)
            elif tool in ('undo','redo'): result=d.history(expected,-1 if tool=='undo' else 1,control,call)
            elif tool=='reload': result=d.reload(expected,args['observedHash'],control)
            elif tool=='checkin':
                returned=None
                if self.live_remote and args['accept'] and d.checkout_file.exists():
                    ticket=json.loads(d.checkout_file.read_bytes())
                    d.check(expected,handoff=True)
                    returned=Remote(self.live_remote,ticket['hash']).saved()
                result=d.checkin(expected,args['accept'],control,returned=returned)
            else: raise Failure('INVALID_ARGUMENT','Unknown tool')
            changes=[]; resources=[]
            if result['revision'] is not None and result['revision']!=expected:
                ref=self.reference(d.path,'inkscape.document'); resources=[ref]
                changes=[{'uri':ref['uri'],'afterRevision':result['revision'],**({'beforeRevision':expected} if expected else {})}]
            if len(json.dumps(result['affected']))>3800:
                affected=result['affected']; result['affected']=affected[:20]; result['affectedCount']=len(affected)
                out=svg.inside(self.root,'outputs/'+svg.digest(call.encode())[:32],d.cache); out.mkdir(parents=True,exist_ok=False)
                evidence=out/'affected.json'; write_json(evidence,affected); ref=self.reference(evidence,'inkscape.provenance')
                result['affectedResource']=ref; resources.append(ref)
            return success(result,resources,changes)


if __name__=='__main__':
    adapter=Adapter()
    serve(activate=adapter.activate,call=adapter.call,resolve=adapter.resolve,deactivate=adapter.deactivate)
