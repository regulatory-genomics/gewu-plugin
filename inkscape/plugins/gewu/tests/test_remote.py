"""Desktop transport contract against a second workspace and actual OpenSSH."""
import os
from pathlib import Path
import time
import unittest

from live import Live
from native import Failure
from remote import Remote


class RemoteDesktopTest(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('GEWU_TEST_DESKTOP'), 'requires the SSH desktop integration fixture')
    def test_authenticated_checkout_snapshot_and_native_mutation(self):
        import json
        settings=json.loads(Path(os.environ['GEWU_TEST_DESKTOP']).read_text())
        remote=Remote(settings['remote'],settings['originHash'])
        first,raw=remote.capture()
        self.assertIn(b'Desktop fixture',raw)
        unchanged,raw=remote.capture(first['revision'])
        self.assertEqual(unchanged['revision'],first['revision'])
        self.assertIsNone(raw)
        changed=remote.edit(first['revision'],[{'op':'set_text','id':'label','text':'Across SSH'}])
        self.assertNotEqual(changed['revision'],first['revision'])
        self.assertIn(b'Across SSH',remote.capture()[1])
        with self.assertRaises(Failure) as stale:
            remote.edit(first['revision'],[{'op':'set_text','id':'label','text':'stale'}])
        self.assertEqual(stale.exception.code,'STALE_REVISION')
        with self.assertRaises(Failure) as wrong:
            Remote(settings['remote'],'0'*64).capture()
        self.assertEqual(wrong.exception.code,'WRONG_DOCUMENT')
        with self.assertRaises(Failure) as open_document:
            remote.saved()
        self.assertEqual(open_document.exception.code,'DOCUMENT_CHECKED_OUT')
        session=Path(settings['remote']['session']); fixture=session.parent/'checkout.svg'
        native=Live(session,fixture,None)
        window=f'{native.path}/window/{native.children("window")[0]}'
        native.gdbus(window,'org.gtk.Actions.Activate','document-save','[]','{}')
        self.assertIn('Across SSH',fixture.read_text())
        try:
            native.gdbus(native.path,'org.gtk.Actions.Activate','quit','[]','{}')
        except Failure as error:
            if error.code!='BRIDGE_UNAVAILABLE': raise
        deadline=time.monotonic()+8
        while not (session/'closed.json').is_file() and time.monotonic()<deadline: time.sleep(.1)
        self.assertIn(b'Across SSH',remote.saved())
        self.assertIn('Desktop fixture',(session.parent.parent/'sender/checkout.svg').read_text())
