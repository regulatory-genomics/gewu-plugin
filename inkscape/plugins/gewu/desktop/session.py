"""Own one private Inkscape desktop and publish only revision-checked saved bytes."""
import asyncio
import contextlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
from uuid import uuid4

from aiohttp import web, ClientSession, WSMsgType, UnixConnector

from document import atomic, write_json
from live import Live
from native import Failure
import svg


class NativeSession:
    def __init__(self,document,settings):
        self.doc=document; self.settings=settings; self.children=[]; self.mutex=threading.RLock()
        self.closed=False; self.lease=False; self.last_input=0.; self.buttons=0; self.keys=set(); self.composing=False
        self.viewer=None; self.loop=None; self.error=None; self.stop=threading.Event()
        self.epoch=uuid4().hex; self.token=secrets.token_urlsafe(32); self.results={}
        self.directory=document.cache/('native-'+self.epoch); self.directory.mkdir(mode=0o700)
        self.sockets=tempfile.TemporaryDirectory(prefix='gewu-native-',dir='/tmp/opencode' if os.access('/tmp/opencode',os.W_OK) else '/tmp')
        self.rfb_socket=Path(self.sockets.name)/'rfb.sock';self.screen_socket=Path(self.sockets.name)/'screen.sock'
        self.working=self.directory/'working.svg'; self.checkpoint=document.cache/'native-checkpoint.svg'
        self.record=document.cache/'native-state.json'
        self.original=document.current_hash(); self.published=None; self.saved_fingerprint=None
        self.log=(self.directory/'desktop.log').open('ab',buffering=0)
        try:
            if document.checkout_file.exists(): raise Failure('DOCUMENT_CHECKED_OUT','Return the existing desktop checkout before using native mode')
            raw=document.raw()
            if raw is None: raw=svg.encode(svg.new_page(180,120))
            self.recovered=False
            if self.record.exists() and self.checkpoint.exists():
                previous=json.loads(self.record.read_bytes())
                if previous.get('originalHash')==self.original and previous.get('dirty'):
                    raw=svg.read_file(self.checkpoint); self.recovered=True
                elif previous.get('dirty'):
                    preserved=document.cache/('recovery-conflict-'+self.epoch+'.svg');atomic(preserved,self.checkpoint.read_bytes(),create=True)
                    self.recovery_conflict=preserved.relative_to(document.root).as_posix()
            from native_document import NativeDocument
            raw=NativeDocument.relocate(raw,document.path.parent,self.directory,document.root)
            NativeDocument(raw,document.root,base=self.directory);atomic(self.working,raw,create=True)
            self._start()
            self.initial=self.bridge.capture()[0]['revision']; self.saved_fingerprint=self.initial if not self.recovered else None
            self.working_hash=svg.digest(self.working.read_bytes())
            self._http_ready=threading.Event()
            self.thread=threading.Thread(target=self._serve,daemon=True); self.thread.start()
            if not self._http_ready.wait(10) or self.error: raise Failure('RUNTIME_MISSING',str(self.error or 'Desktop service did not start'))
            self.monitor=threading.Thread(target=self._monitor,daemon=True); self.monitor.start()
        except BaseException:
            self.close(); raise

    def _spawn(self,argv,env=None,pass_fds=()):
        p=subprocess.Popen(list(map(str,argv)),env=env,stdin=subprocess.DEVNULL,stdout=self.log,stderr=self.log,
                           pass_fds=pass_fds)
        self.children.append(p); return p

    def _start(self):
        vnc=self.settings.get('xvncExecutable') or shutil.which('Xvnc') or shutil.which('Xtigervnc')
        wm=self.settings.get('windowManagerExecutable') or shutil.which('openbox') or shutil.which('metacity')
        if not vnc or not wm: raise Failure('RUNTIME_MISSING','Native mode needs Xvnc and Openbox (or Metacity); use the bundled runtime')
        with (self.directory/'display').open('wb') as display:
            self._spawn([vnc,'-displayfd',display.fileno(),'-geometry','1440x900','-depth','24','-rfbport','-1',
                         '-rfbunixpath',self.rfb_socket,'-rfbunixmode','384','-SecurityTypes','None','-AlwaysShared','-ac','-nolisten','tcp'],pass_fds=[display.fileno()])
            self._wait(lambda:(self.directory/'display').stat().st_size,10,'Xvnc display')
        self.display=':'+(self.directory/'display').read_text().strip()
        env={**os.environ,'DISPLAY':self.display,'NO_AT_BRIDGE':'1','GEWU_INKSCAPE_DEFAULT_FONT':'Noto Sans CJK SC'}
        self._spawn([wm,*(['--sm-disable','--no-composite'] if Path(wm).name=='metacity' else [])],env)
        self._spawn([sys.executable,'-m','websockify','--unix-listen',self.screen_socket,'--unix-target',self.rfb_socket],env)
        self._wait(lambda:self.screen_socket.exists() and self.rfb_socket.exists(),10,'Private display sockets')
        self._spawn([sys.executable,str(Path(__file__).resolve().parents[1]/'live.py'),'launch','--session',self.directory/'bridge',
                     '--figure',self.working,'--inkscape',self.doc.engine.executable],env)
        self._wait(lambda:(self.directory/'bridge/session.json').exists(),15,'Inkscape D-Bus')
        self.bridge=Live(self.directory/'bridge',self.working,self.doc.engine); self.bridge.wait_ready(15)
        window=self.bridge.children('window')[0]
        self.bridge.gdbus(f'{self.bridge.path}/window/{window}','org.gtk.Actions.Activate','canvas-zoom-page','[]','{}')

    def _wait(self,condition,timeout,label):
        deadline=time.monotonic()+timeout
        while not condition():
            if any(p.poll() is not None for p in self.children):
                raise Failure('RUNTIME_MISSING',f'{label} exited; see {self.directory}/desktop.log')
            if time.monotonic()>deadline: raise Failure('RUNTIME_MISSING',f'{label} did not become ready')
            time.sleep(.05)

    def _revision(self,value): return self.epoch+':'+value

    def _expected(self,revision):
        prefix=self.epoch+':'
        if not isinstance(revision,str) or not revision.startswith(prefix):
            raise Failure('STALE_REVISION','Read the current native session revision before editing',True)
        return revision[len(prefix):]

    def active(self):
        return self.buttons or self.keys or self.composing or time.monotonic()-self.last_input<.25

    @contextlib.contextmanager
    def commit_guard(self):
        if self.active():raise Failure('HUMAN_EDIT_ACTIVE','The user is editing; retry after input stops',True)
        self.lease=True
        try:yield
        finally:self.lease=False

    def capture(self,expected=None):
        with self.mutex:
            if self.closed: raise Failure('BRIDGE_UNAVAILABLE','Native session has closed')
            if self.active(): raise Failure('HUMAN_EDIT_ACTIVE','Finish the current drag or text input before querying',True)
            info,raw=self.bridge.capture()
            revision=self._revision(info['revision'])
            if expected is not None and expected!=revision: raise Failure('STALE_REVISION','Native document changed; read state again',True)
            return {**info,'revision':revision},raw

    def state(self,info=None):
        if info is None:info,_=self.capture()
        return {**info,'exists':True,'sessionEpoch':self.epoch,'dirty':self._expected(info['revision'])!=self.saved_fingerprint,
                'conflict':self.doc.current_hash()!=self.original,'recovered':self.recovered,'document':self.doc.uri_path,
                'recoveryConflict':getattr(self,'recovery_conflict',None),
                'runtimePid':self.children[-1].pid,'checkpointAt':getattr(self,'checkpoint_at',None)}

    def edit(self,expected,operations,request_id,control=None):
        with self.mutex:
            signature=svg.digest(json.dumps([expected,operations],sort_keys=True).encode())
            if request_id in self.results:
                item=self.results[request_id]
                if item['signature']!=signature: raise Failure('INVALID_ARGUMENT','requestId was already used for different arguments')
                if item['status']!='applied': raise Failure('OUTCOME_UNKNOWN','Inspect native state before a new write; previous request did not settle')
                return item['result']
            if self.active(): raise Failure('HUMAN_EDIT_ACTIVE','The user is editing; retry after input stops',True)
            native_expected=self._expected(expected)
            self.results[request_id]={'status':'pending','signature':signature}; self._journal()
            try:
                result=self.bridge.edit(native_expected,operations,self.doc.root,control,native=True,commit_guard=self.commit_guard)
                result['revision']=self._revision(result['revision'])
                self.results[request_id].update(status='applied',result=result); self._journal()
                return result
            except Failure as error:
                if error.code!='OUTCOME_UNKNOWN': self.results.pop(request_id,None)
                self._journal(); raise

    def _journal(self):
        write_json(self.directory/'requests.json',self.results)

    def history(self,expected,action):
        with self.mutex:
            with self.commit_guard():
                self.capture(expected)
                doc=self.bridge.children('document')[0]
                self.bridge.gdbus(f'{self.bridge.path}/document/{doc}','org.gtk.Actions.Activate',action,'[]','{}')
                return self.state()

    def _publish(self,raw,fingerprint):
        from native_document import NativeDocument
        native_raw=raw
        raw=NativeDocument.relocate(raw,self.directory,self.doc.path.parent,self.doc.root)
        if self.doc.current_hash()!=self.original:
            raise Failure('EXTERNAL_MODIFICATION','Project SVG changed outside this native session; use Save copy or explicitly reload')
        digest=self.doc.store(raw)
        self.doc.commit(raw,self.doc.fresh(digest),self.original)
        self.original=digest; self.published=digest; self.saved_fingerprint=fingerprint
        self._checkpoint(native_raw,False)

    def save(self,expected):
        with self.mutex:
            self.capture(expected)
            if self.doc.current_hash()!=self.original:raise Failure('EXTERNAL_MODIFICATION','Project SVG changed outside the native session; preserve both versions')
            self.lease=True
            try:
                window=self.bridge.children('window')[0]
                self.bridge.gdbus(f'{self.bridge.path}/window/{window}','org.gtk.Actions.Activate','document-save','[]','{}')
                raw=svg.read_file(self.working);info,_=self.bridge.capture()
                self._publish(raw,info['revision']);self.working_hash=svg.digest(raw)
            finally:self.lease=False
            return self.state()

    def _checkpoint(self,raw,dirty):
        from native_document import NativeDocument
        raw=NativeDocument.relocate(raw,self.directory,self.doc.path.parent,self.doc.root)
        atomic(self.checkpoint,raw); self.checkpoint_at=time.time()
        write_json(self.record,{'sessionEpoch':self.epoch,'originalHash':self.original,'dirty':dirty,'checkpointAt':self.checkpoint_at})

    def _monitor(self):
        last_checkpoint=0.
        while not self.stop.wait(1):
            if self.active() or self.lease: continue
            try:
                with self.mutex:
                    digest=svg.digest(self.working.read_bytes())
                    if digest!=self.working_hash:
                        raw=self.working.read_bytes(); info,_=self.bridge.capture()
                        self._publish(raw,info['revision']); self.working_hash=digest
                    if time.monotonic()-last_checkpoint>=10 and time.monotonic()-self.last_input>=2:
                        info,raw=self.bridge.capture()
                        self._checkpoint(raw,info['revision']!=self.saved_fingerprint); last_checkpoint=time.monotonic()
            except (Failure,OSError,ValueError) as error: self.error=str(error)

    def endpoints(self):
        common={'baseUrl':f'http://127.0.0.1:{self.http_port}','headers':{'Authorization':'Bearer '+self.token}}
        return [{**common,'id':'native-screen','protocol':'websocket','routes':[{'pathPrefix':'/screen','methods':['GET']}]},
                {**common,'id':'native-control','protocol':'http','routes':[{'pathPrefix':'/activity','methods':['POST']},{'pathPrefix':'/assets','methods':['POST']}]}]

    def _serve(self):
        self.loop=asyncio.new_event_loop(); asyncio.set_event_loop(self.loop)
        async def start():
            app=web.Application(client_max_size=32*1024*1024)
            app.router.add_get('/screen',self._screen); app.router.add_post('/activity',self._activity);app.router.add_post('/assets/{name}',self._upload)
            self.runner=web.AppRunner(app); await self.runner.setup()
            site=web.TCPSite(self.runner,'127.0.0.1',0); await site.start()
            self.http_port=site._server.sockets[0].getsockname()[1]
        try:
            self.loop.run_until_complete(start()); self._http_ready.set(); self.loop.run_forever()
        except BaseException as e: self.error=e; self._http_ready.set()
        finally:
            if hasattr(self,'runner'): self.loop.run_until_complete(self.runner.cleanup())
            self.loop.close()

    def _authorized(self,request): return hmac.compare_digest(request.headers.get('Authorization',''),'Bearer '+self.token)

    async def _activity(self,request):
        if not self._authorized(request): raise web.HTTPUnauthorized()
        data=await request.json()
        if not isinstance(data,dict) or set(data)-{'composing','release'} or any(not isinstance(v,bool) for v in data.values()): raise web.HTTPBadRequest()
        self.composing=data.get('composing',False)
        if data.get('release'): self.keys.clear(); self.buttons=0; self.composing=False
        self.last_input=time.monotonic()
        return web.json_response({'busy':bool(self.lease)})

    async def _upload(self,request):
        if not self._authorized(request):raise web.HTTPUnauthorized()
        name=request.match_info['name']
        if Path(name).name!=name or '\\' in name or len(name)>200 or Path(name).suffix.lower() not in ('.svg','.png','.jpg','.jpeg','.pdf'):
            raise web.HTTPBadRequest(text='Unsupported asset name')
        raw=await request.read()
        suffix=Path(name).suffix.lower()
        try:
            if suffix=='.svg':
                from native_document import NativeDocument
                NativeDocument(raw,self.doc.root)
            elif suffix=='.pdf':
                if not raw.startswith(b'%PDF-'):raise ValueError('Expected a PDF')
            else:
                from PIL import Image
                from io import BytesIO
                with Image.open(BytesIO(raw)) as image:image.verify()
        except (ValueError,OSError,Failure) as error:return web.json_response({'error':str(error)},status=400)
        folder=svg.inside(self.doc.root,'.gewu/inkscape-assets');folder.mkdir(parents=True,exist_ok=True)
        path=folder/(svg.digest(raw)[:20]+'-'+name)
        if not path.exists():atomic(path,raw,create=True)
        return web.json_response({'path':path.relative_to(self.doc.root).as_posix(),'sha256':svg.digest(raw)})

    def _input(self,data):
        if len(data)==8 and data[0]==4:
            key=struct.unpack('>I',data[4:8])[0]
            if data[1]: self.keys.add(key)
            else: self.keys.discard(key)
            self.last_input=time.monotonic()
        elif len(data)==12 and data[0]==255 and data[1]==0:
            pressed=struct.unpack('>H',data[2:4])[0];key=struct.unpack('>I',data[4:8])[0]
            if pressed:self.keys.add(key)
            else:self.keys.discard(key)
            self.last_input=time.monotonic()
        elif len(data)==6 and data[0]==5:
            self.buttons=data[1]&7; self.last_input=time.monotonic()

    async def _screen(self,request):
        if not self._authorized(request): raise web.HTTPUnauthorized()
        if self.viewer is not None: raise web.HTTPConflict(text='This document already has an input controller')
        viewer=web.WebSocketResponse(max_msg_size=8*1024*1024,heartbeat=20)
        self.viewer=viewer
        try:
            async with ClientSession(connector=UnixConnector(path=str(self.screen_socket))) as client:
                async with client.ws_connect('http://localhost/screen',max_msg_size=8*1024*1024) as upstream:
                    await viewer.prepare(request)
                    async def send_input():
                        async for message in viewer:
                            if message.type!=WSMsgType.BINARY: break
                            while self.lease: await asyncio.sleep(.01)
                            self._input(message.data); await upstream.send_bytes(message.data)
                    async def send_screen():
                        async for message in upstream:
                            if message.type!=WSMsgType.BINARY: break
                            await viewer.send_bytes(message.data)
                    tasks=[asyncio.create_task(send_input()),asyncio.create_task(send_screen())]
                    try:
                        await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for key in self.keys.copy():
                            with contextlib.suppress(Exception): await upstream.send_bytes(struct.pack('>BBHI',4,0,0,key))
                        with contextlib.suppress(Exception): await upstream.send_bytes(bytes([5,0,0,0,0,0]))
                        for task in tasks: task.cancel()
                        await asyncio.gather(*tasks,return_exceptions=True)
        finally:
            self.viewer=None; self.keys.clear(); self.buttons=0; self.composing=False
            await viewer.close()
        return viewer

    def close(self):
        if self.closed: return
        if hasattr(self,'bridge') and not self.active():
            with contextlib.suppress(Failure,OSError,ValueError):
                with self.mutex:
                    info,raw=self.bridge.capture();self._checkpoint(raw,info['revision']!=self.saved_fingerprint)
        self.closed=True; self.stop.set()
        if self.loop and self.loop.is_running(): self.loop.call_soon_threadsafe(self.loop.stop)
        for p in reversed(self.children):
            with contextlib.suppress(ProcessLookupError): p.terminate()
        for p in self.children:
            try: p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError): p.kill()
                p.wait(timeout=5)
        if hasattr(self,'thread') and self.thread is not threading.current_thread(): self.thread.join(timeout=5)
        if hasattr(self,'monitor') and self.monitor is not threading.current_thread():self.monitor.join(timeout=5)
        self.sockets.cleanup()
        self.log.close()
