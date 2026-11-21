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

    def test_describe_returns_a_small_metadata_summary(self):
        project = self.project("summary")
        result = self.call("describe", {"project": project}, "describe-summary")
        self.assertTrue(result["ok"], result)
        data = result["data"]
        self.assertEqual(data["project"], project)
        self.assertEqual(data["sources"], ["main.md"])
        self.assertEqual(set(data["metadata"]), {"title", "authors", "abstract", "keywords", "sourceCount", "sectionCount"})
        self.assertEqual(data["metadata"]["sectionCount"], 1)
        self.assertLess(len(str(data)), 8000)

    def test_build_accepts_the_latest_revision_or_no_revision(self):
        project = self.project("latest")
        for name, ref in (("latest", {**project, "revision": "latest"}), ("none", {k: v for k, v in project.items() if k != "revision"})):
            result = self.call("build", {"project": ref, "formats": ["html"]}, f"build-{name}")
            self.assertTrue(result["ok"], result)
            self.assertIn("latest", result["data"])
            self.assertTrue((self.root / ".gewu/outputs/latest.json").is_file())

    def test_stale_build_is_retryable_and_reports_the_current_revision(self):
        project = self.project("stale")
        (self.root / "stale/main.md").write_text("Changed\n")
        result = self.call("build", {"project": project, "formats": ["html"]}, "stale")
        self.assertEqual(result["error"]["code"], "STALE_REVISION")
        self.assertTrue(result["error"]["retryable"])
        self.assertIn("revision", result["error"]["message"])

    def test_diagnose_reports_the_runtime_without_building(self):
        result = self.call("diagnose", {}, "diagnose")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["workspaceRoot"], str(self.root))
        self.assertIn("tectonic", result["data"])
        self.assertIn("paperVersion", result["data"])

    def test_pdf_without_tectonic_fails_before_starting_paper(self):
        project = self.project("pdf")
        self.app.find_tectonic = lambda: None
        result = self.call("build", {"project": project, "formats": ["pdf"]}, "pdf")
        self.assertEqual(result["error"]["code"], "MISSING_DEPENDENCY")
        self.assertIn("Tectonic", result["error"]["message"])

    def test_build_timeout_is_structured(self):
        project = self.project("timeout")
        (self.root / "timeout/slow").touch()
        self.app.timeout = 1
        result = self.call("build", {"project": project, "formats": ["html"]}, "timeout")
        self.assertEqual(result["error"]["code"], "TIMEOUT")
        self.assertTrue(result["error"]["retryable"])

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
