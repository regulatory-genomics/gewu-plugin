"""One desktop-local Inkscape session with compare-and-apply, never file reloads."""
import argparse
from contextlib import nullcontext
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

from document import atomic, write_json
from native import Failure
import svg


BUS='org.inkscape.Inkscape.gewu_live'
PATH='/org/inkscape/Inkscape/gewu_live'
ACTION='org.gewu.figure.edit.noprefs'
ALLOWED={'move','resize','align','distribute','equalize','add_text','set_text','set_text_style','set_inner_text','set_inner_text_style'}


class Live:
    def __init__(self,session,figure,engine):
        self.session=Path(session).resolve(); self.figure=Path(figure).resolve(); self.engine=engine; self.path=PATH
        self.gdbus_bin=shutil.which('gdbus')
        if not self.gdbus_bin: raise Failure('DEPENDENCY_MISSING','Desktop live mode requires gdbus')
        try: info=json.loads((self.session/'session.json').read_bytes())
        except (OSError,ValueError) as e: raise Failure('BRIDGE_UNAVAILABLE','Start a dedicated desktop session first') from e
        if self.session.stat().st_uid!=os.getuid() or self.session.stat().st_mode&0o077 or info.get('figure')!=str(self.figure):
            raise Failure('WRONG_DOCUMENT','Desktop session must be private and bound to the exact checkout path')
        self.address=info['bus']
        if not self.address.startswith('unix:') or ';' in self.address:
            raise Failure('BRIDGE_UNAVAILABLE','Desktop session bus must be local')

    def gdbus(self,path,method,*args):
        try:
            answer=subprocess.run([self.gdbus_bin,'call','--session','--dest',BUS,'--object-path',path,
                                   '--method',method,'--timeout','8',*args],capture_output=True,text=True,timeout=9,
                                  env={**os.environ,'DBUS_SESSION_BUS_ADDRESS':self.address})
        except (OSError,subprocess.TimeoutExpired) as e:
            raise Failure('BRIDGE_UNAVAILABLE','Desktop D-Bus call did not settle') from e
        if answer.returncode:
            raise Failure('BRIDGE_UNAVAILABLE',answer.stderr.strip()[:350] or 'Desktop D-Bus call failed')
        return answer.stdout

    def children(self,kind):
        xml=self.gdbus(f'{self.path}/{kind}','org.freedesktop.DBus.Introspectable.Introspect')
        return re.findall(r'<node name="(\d+)"\s*/>',xml)

    def ready(self):
        actions=self.gdbus(self.path,'org.gtk.Actions.List')
        if ACTION not in actions: raise Failure('BRIDGE_UNAVAILABLE','Inkscape did not register the GeWu extension')
        if len(self.children('window'))!=1: raise Failure('AMBIGUOUS_DOCUMENT','Open exactly the selected checkout in this desktop session')

    def wait_ready(self,timeout):
        deadline=time.monotonic()+timeout
        while True:
            try: self.ready(); return
            except Failure:
                if time.monotonic()>=deadline: raise
                time.sleep(.15)

    def exchange(self,mode,expected=None,candidate=None):
        self.ready()
        key=uuid4().hex; req=self.session/'request.json'; claimed=self.session/'claimed.json'
        reply=self.session/(key+'.json'); snapshot=self.session/(key+'.svg'); prepared=self.session/(key+'.candidate')
        if req.exists() or claimed.exists() or (self.session/'uncertain').exists():
            raise Failure('OUTCOME_UNKNOWN','Previous native call did not settle; inspect in Inkscape and restart the desktop session')
        try:
            if candidate is not None: atomic(prepared,candidate,create=True)
            atomic(req,json.dumps({'id':key,'mode':mode,'path':str(self.figure),'expected':expected}).encode(),create=True)
            try:
                self.gdbus(self.path,'org.gtk.Actions.Activate',ACTION,'[]','{}')
                deadline=time.monotonic()+8
                while not reply.is_file() or claimed.exists():
                    if time.monotonic()>=deadline: raise Failure('BRIDGE_UNAVAILABLE','Desktop extension did not respond')
                    time.sleep(.025)
            except Failure as e:
                (self.session/'uncertain').touch(exist_ok=True)
                if mode=='edit': raise Failure('OUTCOME_UNKNOWN','Native mutation may have applied; do not replay') from e
                raise
            data=json.loads(reply.read_bytes())
            if data.get('id')!=key: raise Failure('OUTCOME_UNKNOWN','Native reply did not match request')
            if not data['ok']: raise Failure(data['code'],data['message'],data['code']=='STALE_REVISION')
            return key,data,svg.read_file(snapshot) if mode=='inspect' else None
        finally:
            if not (self.session/'uncertain').exists():
                for resource in (reply,snapshot,prepared): resource.unlink(missing_ok=True)

    def locked(self):
        return os.fdopen(os.open(self.session/'owner.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600),'rb')

    def capture(self,known=None):
        with self.locked() as handle:
            fcntl.flock(handle,fcntl.LOCK_EX)
            _,report,raw=self.exchange('inspect')
            return report,raw if report['revision']!=known else None

    def state(self):
        info,_=self.capture(); return {key:info[key] for key in ('revision','selection')}

    def snapshot(self,expected):
        info,raw=self.capture()
        if info['revision']!=expected: raise Failure('STALE_REVISION','Native document changed; refresh state',True)
        return raw

    def edit(self,expected,operations,workspace,control=None,*,native=False,commit_guard=None):
        if not operations or (not native and any(op['op'] not in ALLOWED for op in operations)):
            raise Failure('INVALID_ARGUMENT','Native mode only supports geometry and text operations')
        with self.locked() as handle:
            fcntl.flock(handle,fcntl.LOCK_EX)
            _,before,raw=self.exchange('inspect')
            if before['revision']!=expected: raise Failure('STALE_REVISION','Native document changed; refresh state',True)
            if native:
                from native_document import NativeDocument
                document=NativeDocument(raw,workspace,self.engine,self.figure.parent)
                affected=document.apply(operations);candidate=document.encode()
            else:
                root=svg.validate(svg.parse(raw),workspace,self.figure.parent)
                affected=[]
                with tempfile.TemporaryDirectory(dir=self.session) as temp:
                    source=Path(temp)/'source.svg'
                    def measure(candidate):
                        source.write_bytes(svg.encode(candidate)); return self.engine.query(source,control)
                    for op in operations: affected.extend(svg.apply(root,op,workspace,measure))
                candidate=svg.encode(root)
            if control and control.cancelled.is_set(): raise Failure('CANCELLED','Stopped before native mutation')
            with commit_guard() if commit_guard else nullcontext():
                key,_,_=self.exchange('edit',expected,candidate)
                try:
                    _,after,_=self.exchange('inspect')
                except Failure as e:
                    raise Failure('OUTCOME_UNKNOWN','Native commit could not be verified; inspect the canvas before another write') from e
                if after.get('commit')!=key:
                    raise Failure('OUTCOME_UNKNOWN','Native commit marker was not observed; inspect the canvas before another write')
            return {'revision':after['revision'],'selection':after['selection'],'affected':list(dict.fromkeys(affected))}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('launch','serve'))
    parser.add_argument('--session',type=Path,required=True)
    parser.add_argument('--figure',type=Path,required=True)
    parser.add_argument('--inkscape',required=True)
    args=parser.parse_args()
    session=args.session.resolve(); figure=args.figure.resolve(strict=True)
    if args.command=='launch':
        session.mkdir(mode=0o700,parents=False,exist_ok=False)
        return subprocess.call(['dbus-run-session','--',sys.executable,__file__,'serve','--session',str(session),'--figure',str(figure),'--inkscape',args.inkscape])
    profile=session/'profile'; extensions=profile/'extensions'; extensions.mkdir(parents=True)
    if os.environ.get('GEWU_INKSCAPE_DEFAULT_FONT'):
        prefs=svg.E.Element('inkscape',version='1')
        tools=svg.E.SubElement(prefs,'group',id='tools')
        svg.E.SubElement(tools,'eventcontext',id='text',usecurrent='0',style='font-family:Noto Sans CJK SC;font-size:12px;fill:black;stroke:none')
        (profile/'preferences.xml').write_bytes(svg.E.tostring(prefs,xml_declaration=True,encoding='UTF-8'))
    for ext in ('inx','py'):
        shutil.copyfile(Path(__file__).with_name('desktop')/('gewu_effect.'+ext),extensions/('gewu_effect.'+ext))
    env={**os.environ,'GEWU_INKSCAPE_SESSION':str(session),'INKSCAPE_PROFILE_DIR':str(profile)}
    write_json(session/'session.json',{'bus':env['DBUS_SESSION_BUS_ADDRESS'],'figure':str(figure),
                                      'originHash':svg.digest(svg.read_file(figure)),'inkscape':args.inkscape})
    status=subprocess.call([args.inkscape,'--app-id-tag=gewu_live',str(figure)],env=env)
    if status==0: write_json(session/'closed.json',{'sha256':svg.digest(svg.read_file(figure))})
    return status


if __name__=='__main__': raise SystemExit(main())
