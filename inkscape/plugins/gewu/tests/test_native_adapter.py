"""A pre-WebSocket host must receive an actionable upgrade error before desktop launch."""
import os
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch



class HostCompatibility(unittest.TestCase):
    def test_old_host_gets_explicit_upgrade_error(self):
        self.assertIn('GEWU_ROOT',os.environ)
        sys.path.insert(0,str(Path(os.environ['GEWU_ROOT'])/'sdk/python'))
        from adapter import Adapter
        from gewu_plugin import ProtocolError
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'GEWU_PLUGIN_HOST_FEATURES':''}):
            adapter=Adapter()
            try:
                with self.assertRaises(ProtocolError) as error:
                    adapter.activate({'instanceId':'old-host','workspaceRoot':tmp,'bindingResource':{'uri':'workspace:///','type':'inkscape.document'},
                        'settings':{'inkscapeExecutable':os.environ['INKSCAPE_BIN']}})
                self.assertEqual(error.exception.payload['code'],'HOST_UPGRADE_REQUIRED')
            finally:adapter.deactivate({})
