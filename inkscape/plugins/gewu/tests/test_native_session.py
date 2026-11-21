"""Save must publish native memory, detect outside writes, and keep process ownership."""
import importlib.util
import os
from pathlib import Path
import tempfile
import time
import stat
import urllib.request
import urllib.error
import subprocess
import sys
import json
import signal
import unittest

from document import Document
from native import Engine, Failure


class NativeSessionTest(unittest.TestCase):
    def test_killing_adapter_group_does_not_leave_native_desktop_processes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ready=root/'ready.json'
            script='''import sys,json,time
from pathlib import Path
from document import Document
from native import Engine
from desktop.session import NativeSession
r=Path(sys.argv[1]);d=Document(r,r,Engine(__import__('os').environ['INKSCAPE_BIN']));s=NativeSession(d,{})
(r/'ready.json').write_text(json.dumps([p.pid for p in s.children]))
while True:time.sleep(1)
'''
            worker=subprocess.Popen([sys.executable,'-c',script,str(root)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            children=[]
            try:
                deadline=time.monotonic()+25
                while not ready.exists() and time.monotonic()<deadline:
                    if worker.poll() is not None:self.fail('Native supervisor worker exited before readiness')
                    time.sleep(.05)
                children=json.loads(ready.read_bytes());os.killpg(worker.pid,signal.SIGKILL);worker.wait(timeout=5)
                def alive(pid):
                    try:return Path(f'/proc/{pid}/stat').read_text().rsplit(') ',1)[1][0]!='Z'
                    except FileNotFoundError:return False
                deadline=time.monotonic()+3
                while any(alive(pid) for pid in children) and time.monotonic()<deadline:time.sleep(.05)
                self.assertFalse(any(alive(pid) for pid in children),'Native desktop children escaped the host-owned adapter process group')
            finally:
                if worker.poll() is None:os.killpg(worker.pid,signal.SIGKILL);worker.wait(timeout=5)
                for pid in children:
                    try:os.killpg(pid,signal.SIGKILL)
                    except ProcessLookupError:pass

    def test_private_display_sockets_and_authenticated_http_boundary(self):
        from desktop.session import NativeSession
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);doc=Document(root,root,Engine(os.environ['INKSCAPE_BIN']));native=None
            try:
                native=NativeSession(doc,{})
                self.assertIsNotNone(getattr(native,'sockets',None),'Internal RFB/WebSocket transports must not expose unauthenticated TCP ports')
                folder=Path(native.sockets.name)
                self.assertEqual(folder.stat().st_mode&0o077,0)
                for name in ('rfb.sock','screen.sock'):
                    path=folder/name;self.assertTrue(stat.S_ISSOCK(path.stat().st_mode));self.assertEqual(path.stat().st_mode&0o077,0)
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(f'http://127.0.0.1:{native.http_port}/screen')
                self.assertEqual(denied.exception.code,401)
            finally:
                if native:native.close()
                doc.close()

    def test_save_keeps_native_memory_and_refuses_external_overwrite(self):
        self.assertIsNotNone(importlib.util.find_spec('desktop.session'), 'Native session supervisor is missing')
        from desktop.session import NativeSession
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); figure=root/'figure.svg'
            figure.write_bytes((Path(__file__).parent/'fixtures/native-rich.svg').read_bytes())
            d=Document(root,figure,Engine(os.environ['INKSCAPE_BIN']))
            session=None
            try:
                session=NativeSession(d,{})
                first=session.state()
                answer=session.edit(first['revision'],[{'op':'set_text','id':'label','text':'Unsaved native value'}],request_id='test-save')
                self.assertNotIn(b'Unsaved native value',figure.read_bytes())
                replay=session.edit(first['revision'],[{'op':'set_text','id':'label','text':'Unsaved native value'}],request_id='test-save')
                self.assertEqual(answer,replay,'A repeated request ID must not apply again')
                session.save(answer['revision'])
                self.assertIn(b'Unsaved native value',figure.read_bytes())
                self.assertEqual(session.state()['revision'],answer['revision'])
                figure.write_bytes(figure.read_bytes().replace(b'Unsaved native value',b'Outside editor value'))
                with self.assertRaises(Failure) as conflict: session.save(answer['revision'])
                self.assertEqual(conflict.exception.code,'EXTERNAL_MODIFICATION')
                self.assertIn(b'Outside editor value',figure.read_bytes())
            finally:
                if session: session.close()
                d.close()

    def test_recovery_preserves_unsaved_nested_edit_and_new_epoch_rejects_old_revision(self):
        self.assertIsNotNone(importlib.util.find_spec('desktop.session'))
        from desktop.session import NativeSession
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);figure=root/'figure.svg';figure.write_bytes((Path(__file__).parent/'fixtures/native-rich.svg').read_bytes())
            d=Document(root,figure,Engine(os.environ['INKSCAPE_BIN']));native=None
            try:
                native=NativeSession(d,{})
                first=native.state()['revision']
                result=native.edit(first,[{'op':'set_text','id':'axis-unit','text':'milliseconds'}, {'op':'set_style','ids':['curve-a'],'style':{'stroke':'#0000ff'}}],request_id='recovery')
                native.close()
                native=NativeSession(d,{})
                _,raw=native.capture()
                self.assertIn(b'milliseconds',raw,'Closing a session must checkpoint its last unsaved edit')
                self.assertTrue(native.state()['recovered'])
                with self.assertRaises(Failure) as old:
                    native.edit(result['revision'],[{'op':'set_text','id':'axis-unit','text':'wrong epoch'}],request_id='old')
                self.assertEqual(old.exception.code,'STALE_REVISION')
            finally:
                if native:native.close()
                d.close()
