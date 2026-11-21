import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "project"


class PaperCLI(unittest.TestCase):
    def setUp(self):
        self.binary = Path(os.environ["PAPER_BIN"]).resolve(strict=True)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = self.root / "project"
        shutil.copytree(FIXTURE, self.project)
        (self.project / "methods.md").write_text("# Methods\n\nSecond body segment.\n")
        (self.project / "appendix.md").write_text("# Appendix\n\nSupplement segment.\n")

    def paper(self, *args, check=True):
        proc = subprocess.run([self.binary, *args], cwd=self.root, capture_output=True, timeout=60)
        if check:
            self.assertEqual(proc.returncode, 0, proc.stderr.decode(errors="replace"))
        return proc

    def test_describe_is_read_only_and_uses_merged_metadata(self):
        before = {p.name: p.read_bytes() for p in self.project.iterdir()}
        result = json.loads(self.paper("describe", self.project, "--json").stdout)
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["sources"], ["main.md", "methods.md"])
        self.assertEqual(result["supplement"], ["appendix.md"])
        self.assertEqual(result["outputStem"], "Legacy Stem")
        title = result["metadata"]["title"]["c"]
        self.assertEqual(" ".join(x["c"] for x in title if x["t"] == "Str"), "Project title")
        self.assertIn("custom-field", result["metadata"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.project.iterdir()})
        self.assertEqual({p.name for p in self.root.iterdir()}, {"project"})

    def test_describe_missing_source_fails(self):
        (self.project / "methods.md").unlink()
        result = self.paper("describe", self.project, "--json", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(result.stdout)

    def test_named_outputs_and_legacy_default(self):
        self.paper("build", self.project, "--out-dir", "out", "--output-stem", "paper", "--output-html", "--disable-cache")
        files = {p.name for p in (self.root / "out").iterdir()}
        self.assertEqual(files, {"paper.tex", "paper.html"})
        text = (self.root / "out/paper.html").read_text()
        self.assertLess(text.index("First body segment"), text.index("Second body segment"))
        self.paper("build", self.project, "--out-dir", "legacy", "--disable-cache")
        self.assertTrue((self.root / "legacy/Legacy Stem.tex").is_file())

    def test_rejects_unsafe_stem_before_creating_outputs(self):
        for name in ("../escape", "a/b", "a\\b", ".", "..", ""):
            with self.subTest(stem=name):
                proc = self.paper("build", self.project, "--out-dir", "out", "--output-stem", name, check=False)
                self.assertNotEqual(proc.returncode, 0)
        self.assertEqual({p.name for p in self.root.iterdir()}, {"project"})
