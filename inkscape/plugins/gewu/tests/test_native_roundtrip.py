"""An agent label edit must preserve unrelated rich SVG subtrees and native undo."""
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
from native import Engine


class RichRoundtrip(unittest.TestCase):
    def test_native_edit_keeps_defs_nested_text_and_lpe_then_undoes(self):
        self.assertIn('INKSCAPE_BIN', os.environ, 'native gate requires an explicit Inkscape')
        plugin=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); figure=root/'figure.svg'; session=root/'session'
            shutil.copyfile(plugin/'tests/fixtures/native-rich.svg',figure)
            with (root/'display').open('wb') as out, (root/'native.log').open('wb') as log:
                xvfb=subprocess.Popen([os.environ['XVFB_BIN'],'-displayfd',str(out.fileno()),'-screen','0','1440x900x24'],pass_fds=[out.fileno()],stderr=log)
                native=None
                try:
                    for _ in range(100):
                        if (root/'display').stat().st_size: break
                        time.sleep(.05)
                    native=subprocess.Popen([sys.executable,str(plugin/'live.py'),'launch','--inkscape',os.environ['INKSCAPE_BIN'],'--figure',str(figure),'--session',str(session)],env={**os.environ,'DISPLAY':':'+(root/'display').read_text().strip(),'NO_AT_BRIDGE':'1'},stdout=log,stderr=log,start_new_session=True)
                    for _ in range(200):
                        if (session/'session.json').exists(): break
                        time.sleep(.05)
                    b=Live(session,figure,Engine(os.environ['INKSCAPE_BIN'])); b.wait_ready(15)
                    state,raw=b.capture(); before=E.fromstring(raw)
                    protected=['gradient','clip','mask','blur','effect','nested','clone','live-path']
                    old={key:E.tostring(before.xpath('//*[@id=$id]',id=key)[0],method='c14n',exclusive=True) for key in protected}
                    result=b.edit(state['revision'],[{'op':'set_text','id':'label','text':'Agent updated title'}],root)
                    after=E.fromstring(b.snapshot(result['revision']))
                    self.assertEqual(after.xpath('string(//*[@id="label"])'),'Agent updated title')
                    for key,raw in old.items(): self.assertEqual(E.tostring(after.xpath('//*[@id=$id]',id=key)[0],method='c14n',exclusive=True),raw,key)
                    doc=b.children('document')[0]
                    b.gdbus(f'{b.path}/document/{doc}','org.gtk.Actions.Activate','undo','[]','{}')
                    undone=E.fromstring(b.capture()[1])
                    self.assertEqual(undone.xpath('string(//*[@id="label"])'),'Native fixture')
                finally:
                    if native:
                        import signal
                        try: os.killpg(native.pid,signal.SIGTERM)
                        except ProcessLookupError: pass
                        native.wait(timeout=10)
                    xvfb.terminate(); xvfb.wait(timeout=10)
