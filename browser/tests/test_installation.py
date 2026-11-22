import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("runtime_plan", ROOT / "scripts/make-runtime-plan.py")
runtime_plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime_plan)


class InstallationTest(unittest.TestCase):
    def test_existing_chromium_file_is_not_enough_to_pass_probe(self):
        with TemporaryDirectory() as temp:
            env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": temp}
            path = subprocess.check_output([
                sys.executable, "-c",
                "from playwright.sync_api import sync_playwright; "
                "p=sync_playwright().start(); print(p.chromium.executable_path); p.stop()",
            ], env=env, text=True).strip()
            executable = Path(path)
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\nexit 1\n")
            executable.chmod(0o755)
            result = subprocess.run([sys.executable, "-c", runtime_plan.PROBE], env=env,
                                    capture_output=True, text=True, timeout=25)
            self.assertNotEqual(result.returncode, 0, "a broken Chromium must fall back to bundled")

    def test_real_chromium_can_probe_without_network(self):
        subprocess.run([sys.executable, "-c", runtime_plan.PROBE], check=True, timeout=25)

    def test_plan_prefers_external_and_preserves_bundled_fallback(self):
        with TemporaryDirectory() as temp:
            meta, out = Path(temp) / "runtime.json", Path(temp) / "plan.json"
            meta.write_text(json.dumps({
                "id": "gewu-browser-runtime", "plugin": "gewu-browser", "version": "0.1.2",
                "target": {"os": "linux", "arch": "x86_64", "libc": "glibc"},
                "runtime": {"path": "browser.runtime.zip", "sha256": "a" * 64, "bytes": 100},
                "manifest": {"sha256": "b" * 64, "bytes": 50},
            }))
            subprocess.run([sys.executable, str(ROOT / "scripts/make-runtime-plan.py"),
                            "--runtime-meta", str(meta), "--base-url", "https://plugin.regulogen.cn",
                            "--out", str(out)], check=True, capture_output=True)
            plan = json.loads(out.read_text())
            self.assertEqual(plan["policies"], ["external", "bundled"])
            bundled = next(p for p in plan["providers"] if p["kind"] == "bundled")
            self.assertEqual(bundled["artifact"]["sha256"], "a" * 64)

    def test_staged_launcher_preserves_literal_interpreter_and_browser_paths(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            interpreter = root / "python $HOME ' quoted"
            interpreter.symlink_to(sys.executable)
            browsers = root / "browsers $HOME ' quoted"
            out = root / "package"
            subprocess.run([sys.executable, str(ROOT / "scripts/stage.py"), "--out", str(out),
                            "--python", str(interpreter), "--browsers", str(browsers)],
                           check=True, capture_output=True)
            (out / "adapter.py").write_text(
                "import json,os,sys; print(json.dumps([sys.executable, os.environ['PLAYWRIGHT_BROWSERS_PATH']]))"
            )
            result = subprocess.run([str(out / "bin/browser-gewu")], cwd=root, env={},
                                    check=True, capture_output=True, text=True)
            self.assertEqual(json.loads(result.stdout), [str(interpreter), str(browsers)])

    def test_invalid_interpreter_leaves_no_staged_package(self):
        with TemporaryDirectory() as temp:
            out = Path(temp) / "package"
            result = subprocess.run([sys.executable, str(ROOT / "scripts/stage.py"), "--out", str(out),
                                     "--python", str(Path(temp) / "missing")], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
