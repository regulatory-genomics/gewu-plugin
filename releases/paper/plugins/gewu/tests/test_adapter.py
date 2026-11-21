import importlib.util
from pathlib import Path
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace


HERE = Path(__file__).resolve().parents[1]


class AdapterTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("paper_adapter", HERE / "adapter.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)
        self.app = self.module.Adapter()
        self.app.activate({"workspaceRoot": str(self.root), "settings": {"executable": str(HERE / "fixtures/fake_paper.py")}})

    def call(self, name, args, call_id, control=None):
        control = control or SimpleNamespace(cancelled=threading.Event(), progress=lambda _: None,
                                             register_process=lambda *_: None, process_exited=lambda *_: None)
        return self.app.call(name, args, {"callId": call_id}, control)

    def project(self, name):
        result = self.call("init", {"directory": name}, f"init-{name}")
        self.assertTrue(result["ok"], result)
        return result["resources"][0]

    def test_build_checks_revision_and_returns_exact_outputs(self):
        project = self.project("manuscript")
        self.assertEqual(project["type"], "paper.project")
        result = self.call("build", {"project": project, "formats": ["html", "docx"]}, "build-1")
        self.assertTrue(result["ok"], result)
        self.assertEqual({Path(r["uri"]).name for r in result["resources"]}, {"paper.html", "paper.docx", "paper.tex"})
        (self.root / "manuscript/main.md").write_text("Concurrent source edit\n")
        result = self.call("build", {"project": project, "formats": ["html"]}, "stale")
        self.assertEqual(result["error"]["code"], "STALE_REVISION")

    def test_cancel_a_project_does_not_cancel_another_project(self):
        slow = self.project("slow"); fast = self.project("fast")
        (self.root / "slow/slow").touch()
        control = SimpleNamespace(cancelled=threading.Event(), progress=lambda _: None,
                                  register_process=lambda *_: None, process_exited=lambda *_: None)
        with ThreadPoolExecutor() as pool:
            future = pool.submit(self.call, "build", {"project": slow, "formats": ["html"]}, "slow-job", control)
            for _ in range(100):
                if (self.root / "slow/started").exists():
                    break
                time.sleep(0.02)
            self.assertTrue((self.root / "slow/started").exists())
            good = self.call("build", {"project": fast, "formats": ["html"]}, "fast-job")
            control.cancelled.set()
            self.assertEqual(future.result(timeout=5)["error"]["code"], "CANCELLED")
            self.assertTrue(good["ok"], good)
