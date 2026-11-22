"""GeWu v1 stdio adapter for the figure editor."""
import json
import mimetypes
import os
from pathlib import Path
import threading
from urllib.parse import quote, unquote

from jsonschema import Draft202012Validator

from contract import TOOLS, NATIVE_TOOLS
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
        self.doc=None; self.native=None; self.views={}; self.view_lock=threading.Lock()
        self.validators={d['name']:Draft202012Validator(d['inputSchema']) for d in TOOLS}

    def activate(self,params):
        try:
            self.root=Path(params['workspaceRoot']).resolve(); ref=params.get('bindingResource') or {}
            if ref.get('type')!='inkscape.document': raise Failure('INVALID_PATH','Open an inkscape.document resource')
            binding=self.from_uri(ref['uri']); settings=params.get('settings',{})
            headless=os.environ.get('GEWU_INKSCAPE_HEADLESS')=='1'
            native_mode=not headless and settings.get('editorMode','native')=='native'
            if native_mode and params.get('instanceId') and 'plugin-websocket' not in os.environ.get('GEWU_PLUGIN_HOST_FEATURES','').split():
                raise Failure('HOST_UPGRADE_REQUIRED','Upgrade GeWu to the native WebSocket-enabled host before opening Inkscape 0.2; use editorMode=legacy with an older host')
            self.live_directory=settings.get('nativeSessionDirectory')
            self.live_remote=settings.get('nativeRemote')
            executable=(Path(os.environ['GEWU_RUNTIME_ROOT'])/'bin/inkscape') if headless else settings.get('inkscapeExecutable')
            self.doc=Document(self.root,binding,Engine(executable,settings.get('timeoutSeconds',30)))
            if native_mode:
                from desktop.session import NativeSession
                self.native=NativeSession(self.doc,settings)
            tools=NATIVE_TOOLS if self.native else TOOLS
            self.validators={d['name']:Draft202012Validator(d['inputSchema']) for d in tools}
            return {'tools':tools,'serviceEndpoints':self.native.endpoints() if self.native else []}
        except Failure as e:
            raise ProtocolError(e.code,str(e),e.retryable) from e

    def deactivate(self,_params):
        if self.native:self.native.close()
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
        if tool.startswith('view.'): return self.view(tool,args,context)
        if self.native:return self.native_dispatch(tool,args,context,control)
        if tool=='selection':return self.view(tool,args,context)
        if tool=='diagnose': return success({'version':d.engine.version,'document':d.uri_path,'formats':['svg','pdf','png'],
                                             'coordinateUnit':'mm','editorMode':'legacy','desktopLiveConfigured':bool(self.live_directory or self.live_remote)})
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

    def native_dispatch(self,tool,args,context,control):
        from native_document import NativeDocument, SCHEMAS
        s=self.native;d=self.doc
        if tool=='diagnose':return success({'version':d.engine.version,'document':d.uri_path,'editorMode':'native','serviceError':str(s.error) if s.error else None})
        if tool=='capabilities':
            names=list(SCHEMAS)
            if 'operation' in args:
                if args['operation'] not in SCHEMAS:raise Failure('INVALID_ARGUMENT','Unknown operation; query capabilities without operation first')
                return success({'operations':[args['operation']],'schema':SCHEMAS[args['operation']],'coordinateUnit':'mm; path data and node coordinates use SVG local units'})
            return success({'operations':names,'coordinateUnit':'mm; path data and node coordinates use SVG local units','nextAction':'Call capabilities with operation to obtain exact arguments'})
        if tool=='request_status':return success(s.results.get(args['requestId'],{'status':'not_found'}))
        if tool=='edit':return success(s.edit(args['expectedRevision'],args['operations'],args.get('requestId',context['callId']),control))
        if tool in ('undo','redo'):return success(s.history(args['expectedRevision'],tool))
        if tool=='save':return success(s.save(args['expectedRevision']))
        info,raw=s.capture(args.get('revision') if tool!='select' else args['expectedRevision'])
        revision=info['revision'];graph=NativeDocument(raw,self.root,d.engine,s.directory)
        if tool in ('selection','select'):
            if tool=='select':
                for key in args['ids']:graph.pick(key)
                if any(not svg.ID.fullmatch(key) for key in args['ids']):raise Failure('INVALID_ARGUMENT','Native selection requires stable SVG IDs')
                s.bridge.gdbus(s.bridge.path,'org.gtk.Actions.Activate','select-clear','[]','{}')
                if args['ids']:s.bridge.gdbus(s.bridge.path,'org.gtk.Actions.Activate','select-by-id',f"[<'{','.join(args['ids'])}'>]",'{}')
                info,_=s.capture()
            offset=args.get('offset',0);limit=args.get('limit',50);selected=info['selection'];end=offset+limit
            return success({'revision':info['revision'],'ids':selected[offset:end],'selectionCount':len(selected),'nextOffset':end if end<len(selected) else None})
        if tool in ('state','inspect'):
            options={k:v for k,v in args.items() if k in ('ids','parent','kind','text','offset','limit','fields')}
            objects=graph.inspect(**options)
            if tool=='inspect':return success({'revision':revision,**objects,'coordinateUnit':'mm'})
            pages=svg.pages(graph.root)
            return success({**s.state(info),**objects,'pages':pages,'page':pages[0],'selection':info['selection'][:100],
                'selectionCount':len(info['selection']),'figureId':graph.root.get(svg.M('figure')) or 'native-'+svg.digest(d.uri_path.encode())[:24],'coordinateUnit':'mm'})
        if tool=='render':
            key=svg.digest(raw);page=args.get('page')
            path=d.previews/f'{key}-{page or "all"}.png'
            if not path.exists():
                with __import__('tempfile').TemporaryDirectory(dir=d.cache) as temp:
                    source=Path(temp)/'input.svg';source.write_bytes(NativeDocument.relocate(raw,s.directory,source.parent,self.root,embed=True));d.engine.export(source,path,'png',page=page,control=control)
            ref=self.reference(path,'inkscape.preview');return success({'revision':revision,'preview':ref},[ref])
        if tool=='handoff_status':
            manifest=json.loads(svg.read_file(svg.inside(self.root,args['path'])))
            figure_id=graph.root.get(svg.M('figure')) or 'native-'+svg.digest(d.uri_path.encode())[:24]
            if manifest.get('figureId')!=figure_id or manifest.get('consumer') not in ('paper','manimble'):
                raise Failure('INVALID_ARGUMENT','Handoff manifest belongs to a different figure or consumer')
            return success({'figureId':manifest['figureId'],'revision':revision,'publishedRevision':manifest['revision'],
                'status':'current' if revision==manifest['revision'] else 'update_available'})
        if tool in ('export','handoff'):
            out=svg.inside(self.root,'outputs/'+svg.digest(context['callId'].encode())[:32],d.cache);out.mkdir(parents=True,exist_ok=False)
            fmt=args.get('format','png');path=out/('figure.'+fmt);source=out/'snapshot.svg';raw=NativeDocument.relocate(raw,s.directory,out,self.root,embed=True);source.write_bytes(raw)
            if tool=='handoff' and args['consumer']=='manimble' and fmt!='png':raise Failure('INVALID_ARGUMENT','Manimble requires a PNG handoff')
            if fmt=='svg':path.write_bytes(raw)
            else:d.engine.export(source,path,fmt,args.get('dpi',300),args.get('text','preserve'),page=args.get('page'),control=control)
            ref=self.reference(path,'inkscape.export');figure_id=graph.root.get(svg.M('figure')) or 'native-'+svg.digest(d.uri_path.encode())[:24]
            payload={'revision':revision,'figureId':figure_id,'artifact':ref}
            if tool=='handoff':
                payload.update(workflow.handoff(args['consumer'],path.relative_to(self.root).as_posix()))
                if 'project' in args:payload.update(workflow.publish(self.root,args['project'],args['consumer'],figure_id,revision,path))
            manifest=out/'provenance.json';payload['manifestPath']=manifest.relative_to(self.root).as_posix()
            write_json(manifest,{**payload,'engine':d.engine.version,'pageNumber':args.get('page'),'dpi':args.get('dpi',300) if fmt=='png' else None})
            provenance=self.reference(manifest,'inkscape.provenance');payload['provenance']=provenance
            return success(payload,[ref,provenance])
        raise Failure('INVALID_ARGUMENT','Tool is unavailable in native mode')


if __name__=='__main__':
    adapter=Adapter()
    serve(activate=adapter.activate,call=adapter.call,resolve=adapter.resolve,deactivate=adapter.deactivate)
