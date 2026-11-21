import json
from pathlib import Path
import unittest


HERE = Path(__file__).resolve().parents[1]


class RuntimePackage(unittest.TestCase):
    def test_marketplace_manifest_does_not_require_a_development_executable(self):
        manifest = json.loads((HERE / "plugin.json").read_text())
        self.assertNotIn("executable", manifest["settingsSchema"].get("required", []))
        self.assertIn("GEWU_RUNTIME_ROOT", (HERE / "adapter.py").read_text())


if __name__ == "__main__":
    unittest.main()
