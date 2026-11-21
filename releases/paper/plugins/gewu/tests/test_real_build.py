import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import threading
from types import SimpleNamespace
import unittest
import zipfile


class RealBuild(unittest.TestCase):
    def test_paper_builds_and_returns_real_preview_and_download(self):
        cli = os.environ.get("PAPER_BIN")
        require_pdf = os.environ.get("PAPER_REQUIRE_PDF") == "1"
        if not cli:
            if require_pdf:
                self.fail("PAPER_BIN is required for release smoke")
            self.skipTest("PAPER_BIN not configured")
        if require_pdf:
            self.assertIsNotNone(shutil.which("tectonic"), "Tectonic is required for release smoke")
        spec = importlib.util.spec_from_file_location("paper", Path(__file__).resolve().parents[1] / "adapter.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            adapter = module.Adapter()
            adapter.activate({"workspaceRoot": temp, "settings": {"executable": cli}})
            control = SimpleNamespace(cancelled=threading.Event(), progress=lambda _: None,
                                      register_process=lambda *_: None, process_exited=lambda *_: None)

            def call(tool, args):
                result = adapter.call(tool, args, {"callId": tool}, control)
                self.assertTrue(result["ok"], result)
                return result

            initial = call("init", {"directory": "manuscript"})
            self.assertEqual(initial["resources"][0]["type"], "paper.project")
            project = root / "manuscript"
            (project / "metadata.yaml").write_text("title: GeWu smoke\ncontents: [main.md]\n")
            (project / "main.md").write_text("# Conclusion\n\nRendered by the real Paper CLI.\n")
            actual = call("describe", {"project": initial["data"]["project"]})["data"]["project"]
            formats = ["html", "docx"] + (["pdf"] if require_pdf else [])
            built = call("build", {"project": actual, "formats": formats})
            files = {ref["uri"].rsplit(".", 1)[-1]: Path(adapter.resolve(ref)["path"]) for ref in built["resources"]}
            self.assertEqual(set(files), set(formats + ["tex"]))
            self.assertIn("Rendered by the real Paper CLI.", files["html"].read_text())
            with zipfile.ZipFile(files["docx"]) as archive:
                self.assertIn(b"Rendered by the real Paper CLI.", archive.read("word/document.xml"))
            if require_pdf:
                self.assertEqual(files["pdf"].read_bytes()[:5], b"%PDF-")
            self.assertEqual({p.name for p in root.iterdir()}, {"manuscript", ".gewu"})
