import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parents[1]


class RuntimePackage(unittest.TestCase):
    def test_marketplace_manifest_does_not_require_a_development_executable(self):
        manifest = json.loads((HERE / "plugin.json").read_text())
        self.assertNotIn("executable", manifest["settingsSchema"].get("required", []))
        self.assertIn("GEWU_RUNTIME_ROOT", (HERE / "adapter.py").read_text())

    def test_runtime_plan_has_no_stale_bundled_release_reference(self):
        plan = json.loads((HERE / "runtime-plan.json").read_text())
        version = json.loads((HERE / "plugin.json").read_text())["version"]
        for provider in plan["providers"]:
            if provider["kind"] == "bundled":
                self.assertEqual(provider["version"], version)
                self.assertIn(f"paper-{version}/", provider["artifact"]["url"])
                self.assertIn(f"paper-{version}/", provider["manifest"]["url"])

    def test_runtime_plan_generator_uses_matching_runtime_metadata(self):
        meta = {
            "id": "paper-runtime",
            "plugin": "paper",
            "version": "0.3.1",
            "target": {"os": "linux", "arch": "x86_64", "libc": "glibc"},
            "runtime": {"path": "paper.runtime.zip", "sha256": "a" * 64, "bytes": 10},
            "manifest": {"sha256": "b" * 64, "bytes": 11},
        }
        with tempfile.TemporaryDirectory() as directory:
            meta_path = Path(directory) / "runtime.json"
            plan_path = Path(directory) / "runtime-plan.json"
            meta_path.write_text(json.dumps(meta))
            subprocess.run([
                sys.executable,
                str(HERE / "make-runtime-plan.py"),
                "--runtime-meta", str(meta_path),
                "--base-url", "https://plugin.regulogen.cn",
                "--out", str(plan_path),
            ], check=True)
            plan = json.loads(plan_path.read_text())
        bundled = next(provider for provider in plan["providers"] if provider["kind"] == "bundled")
        self.assertEqual(bundled["version"], "0.3.1")
        self.assertIn("paper-0.3.1/aaaaaaaaaaaa/", bundled["artifact"]["url"])


if __name__ == "__main__":
    unittest.main()
