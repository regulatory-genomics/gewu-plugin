"""Real isolated GUI test; run with Xvfb available as XVFB_BIN."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from lxml import etree as E

from live import Live
from native import Engine, Failure
import svg


class NativeGUI(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('XVFB_BIN'),'set XVFB_BIN to require native GUI integration')
    def test_unsaved_edit_and_native_undo(self):
        plugin=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); figure=root/'figure.svg'; session=root/'desktop'; display=root/'display'; log=root/'app.log'
            r=svg.new_page(90,70); svg.apply(r,{'op':'add_text','id':'label','text':'Before','x':10,'y':10},root,None)
            figure.write_bytes(svg.encode(r)); baseline=figure.read_bytes()
            with display.open('wb') as handle, log.open('wb') as output:
                xvfb=subprocess.Popen([os.environ['XVFB_BIN'],'-displayfd',str(handle.fileno()),'-screen','0','1024x768x24'],
                                      pass_fds=[handle.fileno()],stdout=output,stderr=output)
                try:
                    for _ in range(100):
                        if display.stat().st_size: break
                        time.sleep(.05)
                    self.assertTrue(display.stat().st_size,'Xvfb never provided a display')
                    env={**os.environ,'DISPLAY':':'+display.read_text().strip(),'NO_AT_BRIDGE':'1'}
                    proc=subprocess.Popen([sys.executable,str(plugin/'live.py'),'launch','--inkscape',os.environ['INKSCAPE_BIN'],
                                           '--figure',str(figure),'--session',str(session)],env=env,stdout=output,stderr=output,start_new_session=True)
                    try:
                        for _ in range(150):
                            if (session/'session.json').is_file(): break
                            if proc.poll() is not None: self.fail(log.read_text())
                            time.sleep(.05)
                        bridge=Live(session,figure,Engine(os.environ['INKSCAPE_BIN']))
                        bridge.wait_ready(12)
                        first=bridge.state()
                        bridge.gdbus(bridge.path,'org.gtk.Actions.Activate','select-by-id',"[<'label'>]",'{}')
                        self.assertEqual(bridge.state()['selection'],['label'])
                        with self.assertRaises(Failure) as bad:
                            bridge.edit('stale',[{'op':'set_text','id':'label','text':'bad'}],root)
                        self.assertEqual(bad.exception.code,'STALE_REVISION')
                        changed=bridge.edit(first['revision'],[{'op':'set_text','id':'label','text':'After'}],root)
                        self.assertNotEqual(changed['revision'],first['revision'])
                        self.assertIn(b'>After</text>',bridge.snapshot(changed['revision']))
                        self.assertEqual(figure.read_bytes(),baseline)
                        docs=bridge.children('document')
                        self.assertEqual(len(docs),1)
                        bridge.gdbus(f'{bridge.path}/document/{docs[0]}','org.gtk.Actions.Activate','undo','[]','{}')
                        undone=bridge.state()
                        self.assertIn(b'>Before</text>',bridge.snapshot(undone['revision']))
                        other=root/'other.svg'; shutil.copyfile(figure,other)
                        bridge.gdbus(bridge.path,'org.freedesktop.Application.Open',f"['{other.as_uri()}']",'{}')
                        with self.assertRaises(Failure) as ambiguous: bridge.state()
                        self.assertIn(ambiguous.exception.code,('AMBIGUOUS_DOCUMENT','WRONG_DOCUMENT'))
                    finally:
                        import signal
                        try: os.killpg(proc.pid,signal.SIGTERM)
                        except ProcessLookupError: pass
                        proc.wait(timeout=10)
                finally:
                    xvfb.terminate(); xvfb.wait(timeout=10)
