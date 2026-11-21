import os
from pathlib import Path
import subprocess
import unittest


@unittest.skipUnless(os.environ.get("INKSCAPE_TEST_RUNTIME"), "set INKSCAPE_TEST_RUNTIME to a prepared runtime tree")
class PackedProbeTest(unittest.TestCase):
    def test_old_panel_probe_exports_without_websocket_host_support(self):
        probe = Path(os.environ.get("INKSCAPE_TEST_PROBE", Path(__file__).parent / "bin/probe"))
        env = dict(os.environ, GEWU_RUNTIME_ROOT=os.environ["INKSCAPE_TEST_RUNTIME"])
        env.pop("GEWU_PLUGIN_HOST_FEATURES", None)
        result = subprocess.run(["sh", str(probe)], env=env, text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Inkscape 1.4.4", result.stdout)


if __name__ == "__main__":
    unittest.main()
