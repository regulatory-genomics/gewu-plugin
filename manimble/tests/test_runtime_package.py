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

    def test_managed_plan_uses_prebuilt_pycairo_and_publishes_install_guide(self):
        plan = json.loads((HERE / "runtime-plan.json").read_text())
        managed = next(p for p in plan["providers"] if p["kind"] == "managed")
        self.assertIn("pycairo", managed["packages"])
        self.assertFalse(any(p.startswith("pycairo") for p in managed["pip"]))
        release = json.loads((HERE / "release.json").read_text())
        manifest = json.loads((HERE / "plugin.json").read_text())
        self.assertEqual(release["version"], manifest["version"])
        self.assertIn("INSTALL.md", release["packageFiles"])
        guide = (HERE / "INSTALL.md").read_text()
        self.assertIn("--runtime managed", guide)
        self.assertIn("--runtime external", guide)
        self.assertIn("--resource workspace:///deck --resource-type manimble.deck", guide)


if __name__ == "__main__":
    unittest.main()
