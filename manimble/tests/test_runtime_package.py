import json
from pathlib import Path
import unittest


HERE = Path(__file__).resolve().parents[1]


class RuntimePackage(unittest.TestCase):
    def test_marketplace_manifest_does_not_require_python_or_engine_paths(self):
        manifest = json.loads((HERE / "plugin.json").read_text())
        self.assertEqual(manifest["settingsSchema"].get("required", []), [])
        source = (HERE / "adapter.py").read_text()
        self.assertIn("GEWU_RUNTIME_ROOT", source)


if __name__ == "__main__":
    unittest.main()
