"""Source mapping and revision-checked edits through the real Paper CLI."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest

PLUGIN = Path(__file__).resolve().parents[1]
FIXTURE = PLUGIN / "fixtures" / "editable"


def load_document():
    spec = importlib.util.spec_from_file_location("paper_document", PLUGIN / "document.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SourceMap(unittest.TestCase):
    """The native inspect contract; no editing is involved."""

    def setUp(self):
        self.cli = os.environ.get("PAPER_BIN")
        if not self.cli:
            self.skipTest("PAPER_BIN not configured")
        self.cli = Path(self.cli).resolve(strict=True)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "project"
        shutil.copytree(FIXTURE, self.root)

    def inspect(self):
        result = subprocess.run([str(self.cli), "inspect", str(self.root), "--json"],
                                capture_output=True, timeout=60, check=True)
        return json.loads(result.stdout)

    def file(self, data, name):
        return next(entry for entry in data["files"] if entry["path"] == name)

    def test_every_fixture_file_maps_and_definitions_stay_out_of_the_body(self):
        data = self.inspect()
        self.assertEqual(data["version"], 1)
        mapped = {entry["path"]: entry for entry in data["files"]}
        for name in ("main.md", "complex.md", "crlf.md", "supplement.md"):
            self.assertTrue(mapped[name]["mapped"], mapped[name].get("reason"))
        kinds = [block["kind"] for block in mapped["main.md"]["blocks"]]
        self.assertNotIn("Footnote", kinds)
        self.assertNotIn("Reference", kinds)

    def test_plain_rich_and_preserved_blocks_are_distinguished(self):
        main = self.file(self.inspect(), "main.md")
        by_prefix = lambda prefix: next(b for b in main["blocks"] if b["source"].startswith(prefix))
        self.assertEqual(by_prefix("A plain paragraph")["inline"], "plain")
        self.assertTrue(by_prefix("A plain paragraph")["editable"])
        self.assertEqual(by_prefix("A paragraph with")["inline"], "rich")
        self.assertFalse(by_prefix("```")["editable"])
        self.assertFalse(by_prefix("| Name")["editable"])
        self.assertFalse(by_prefix("$$")["editable"])

    def test_multibyte_offsets_address_the_right_bytes(self):
        main = self.file(self.inspect(), "main.md")
        block = next(b for b in main["blocks"] if b["source"].startswith("中文段落"))
        raw = (self.root / "main.md").read_bytes()
        self.assertEqual(raw[block["byteStart"]:block["byteEnd"]], block["source"].encode())
        self.assertIn("😀", block["source"])

    def test_crlf_file_keeps_its_line_endings(self):
        crlf = self.file(self.inspect(), "crlf.md")
        self.assertTrue(crlf["mapped"])
        self.assertTrue(all(block["source"].endswith("\r\n") for block in crlf["blocks"]))


class Edits(unittest.TestCase):
    def setUp(self):
        self.cli = os.environ.get("PAPER_BIN")
        if not self.cli:
            self.skipTest("PAPER_BIN not configured")
        self.cli = Path(self.cli).resolve(strict=True)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        self.root = self.workspace / "project"
        shutil.copytree(FIXTURE, self.root)
        self.document = load_document()
        self.before = {path.name: path.read_bytes() for path in self.root.iterdir() if path.is_file()}

    def block(self, name, prefix):
        data = self.document.snapshot(self.cli, self.root, "workspace:///project")
        entry = next(f for f in data["files"] if f["path"] == name)
        return entry, next(b for b in entry["blocks"] if b["source"].startswith(prefix))

    def edit(self, name, prefix, text, revision=None):
        entry, block = self.block(name, prefix)
        return self.document.apply_edit(self.cli, self.root, "workspace:///project", {
            "file": name, "byteStart": block["byteStart"], "byteEnd": block["byteEnd"],
            "text": text, "fileRevision": revision or entry["revision"],
        })

    def test_edit_replaces_only_the_block(self):
        self.edit("main.md", "A plain paragraph", "An edited paragraph.")
        raw = (self.root / "main.md").read_bytes()
        self.assertIn(b"An edited paragraph.\n", raw)
        self.assertNotIn(b"A plain paragraph", raw)
        # Every other file is byte-identical.
        for name, content in self.before.items():
            if name != "main.md":
                self.assertEqual((self.root / name).read_bytes(), content, name)
        # The blocks around the edit are untouched.
        main = (self.root / "main.md").read_bytes()
        self.assertIn(b"# Introduction\n", main)
        self.assertIn("中文段落带 emoji 😀".encode(), main)

    def test_edit_keeps_utf8_and_crlf_outside_the_block(self):
        self.edit("crlf.md", "First paragraph", "Replaced first paragraph.")
        raw = (self.root / "crlf.md").read_bytes()
        self.assertIn(b"Replaced first paragraph.\r\n", raw)
        self.assertIn(b"Second paragraph.\r\n", raw)

    def test_stale_revision_is_refused_and_nothing_is_written(self):
        entry, block = self.block("main.md", "A plain paragraph")
        # Someone else changes the file first.
        (self.root / "main.md").write_text((self.root / "main.md").read_text() + "\nExternal edit.\n")
        changed = (self.root / "main.md").read_bytes()
        with self.assertRaises(self.document.DocumentError) as caught:
            self.document.apply_edit(self.cli, self.root, "workspace:///project", {
                "file": "main.md", "byteStart": block["byteStart"], "byteEnd": block["byteEnd"],
                "text": "Should not land.", "fileRevision": entry["revision"],
            })
        self.assertEqual(caught.exception.code, "STALE_REVISION")
        self.assertEqual((self.root / "main.md").read_bytes(), changed)

    def test_a_replacement_that_changes_structure_is_refused(self):
        entry, block = self.block("main.md", "A plain paragraph")
        with self.assertRaises(self.document.DocumentError) as caught:
            self.document.apply_edit(self.cli, self.root, "workspace:///project", {
                "file": "main.md", "byteStart": block["byteStart"], "byteEnd": block["byteEnd"],
                "text": "First paragraph.\n\nA second block.", "fileRevision": entry["revision"],
            })
        self.assertEqual(caught.exception.code, "STRUCTURE_CHANGED")
        self.assertEqual((self.root / "main.md").read_bytes(), self.before["main.md"])

    def test_preserved_blocks_cannot_be_replaced(self):
        entry, block = self.block("main.md", "```")
        with self.assertRaises(self.document.DocumentError) as caught:
            self.document.apply_edit(self.cli, self.root, "workspace:///project", {
                "file": "main.md", "byteStart": block["byteStart"], "byteEnd": block["byteEnd"],
                "text": "print(1)", "fileRevision": entry["revision"],
            })
        self.assertEqual(caught.exception.code, "NOT_EDITABLE")

    def test_paths_outside_the_project_are_refused(self):
        entry, block = self.block("main.md", "A plain paragraph")
        with self.assertRaises(self.document.DocumentError) as caught:
            self.document.apply_edit(self.cli, self.root, "workspace:///project", {
                "file": "../escape.md", "byteStart": block["byteStart"], "byteEnd": block["byteEnd"],
                "text": "x", "fileRevision": entry["revision"],
            })
        self.assertEqual(caught.exception.code, "INVALID_PATH")

    def test_undo_and_redo_round_trip(self):
        edited = self.edit("main.md", "A plain paragraph", "An edited paragraph.")
        self.assertTrue(edited["canUndo"])
        undone = self.document.step_history(self.cli, self.root, "workspace:///project", edited["project"]["revision"], "undo")
        self.assertEqual((self.root / "main.md").read_bytes(), self.before["main.md"])
        self.assertFalse(undone["canUndo"])
        self.assertTrue(undone["canRedo"])
        redone = self.document.step_history(self.cli, self.root, "workspace:///project", undone["project"]["revision"], "redo")
        self.assertIn(b"An edited paragraph.\n", (self.root / "main.md").read_bytes())
        self.assertFalse(redone["canRedo"])

    def test_undo_refuses_after_an_external_change(self):
        edited = self.edit("main.md", "A plain paragraph", "An edited paragraph.")
        with (self.root / "main.md").open("ab") as stream:
            stream.write(b"\nExternal.\n")
        with self.assertRaises(self.document.DocumentError) as caught:
            self.document.step_history(self.cli, self.root, "workspace:///project", edited["project"]["revision"], "undo")
        self.assertEqual(caught.exception.code, "STALE_REVISION")

    def test_agent_and_view_edits_share_one_revision_rule(self):
        """Two clients race: the newer revision wins and the older is refused."""
        data = self.document.snapshot(self.cli, self.root, "workspace:///project")
        entry = next(f for f in data["files"] if f["path"] == "main.md")
        block = next(b for b in entry["blocks"] if b["source"].startswith("A plain paragraph"))
        first = self.document.apply_edit(self.cli, self.root, "workspace:///project", {
            "file": "main.md", "byteStart": block["byteStart"], "byteEnd": block["byteEnd"],
            "text": "From the view.", "fileRevision": entry["revision"],
        })
        # The agent still holds the old block range and revision.
        with self.assertRaises(self.document.DocumentError) as caught:
            self.document.apply_edit(self.cli, self.root, "workspace:///project", {
                "file": "main.md", "byteStart": block["byteStart"], "byteEnd": block["byteEnd"],
                "text": "From the agent.", "fileRevision": entry["revision"],
            })
        self.assertIn(caught.exception.code, ("STALE_REVISION", "STALE_BLOCK"))
        self.assertIn(b"From the view.", (self.root / "main.md").read_bytes())
        self.assertTrue(first["project"]["revision"])


class Adapter(unittest.TestCase):
    """The adapter surface the Preview and the agent actually call."""

    def setUp(self):
        self.cli = os.environ.get("PAPER_BIN")
        if not self.cli:
            self.skipTest("PAPER_BIN not configured")
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        self.root = self.workspace / "project"
        shutil.copytree(FIXTURE, self.root)
        spec = importlib.util.spec_from_file_location("paper_adapter", PLUGIN / "adapter.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.adapter = module.Adapter()
        self.adapter.activate({"workspaceRoot": str(self.workspace),
                               "settings": {"executable": str(Path(self.cli).resolve())}})
        self.control = SimpleNamespace(cancelled=threading.Event(), progress=lambda _: None,
                                       register_process=lambda *_: None, process_exited=lambda *_: None)

    def call(self, tool, args):
        return self.adapter.call(tool, args, {"callId": tool}, self.control)

    def project(self):
        return self.call("describe", {"project": {"uri": "workspace:///project", "type": "paper.project"}})["data"]["project"]

    def test_document_edit_and_undo_through_the_adapter(self):
        document = self.call("document", {"project": {"uri": "workspace:///project", "type": "paper.project"}})
        self.assertTrue(document["ok"], document)
        entry = next(f for f in document["data"]["files"] if f["path"] == "main.md")
        block = next(b for b in entry["blocks"] if b["source"].startswith("A plain paragraph"))
        edited = self.call("edit", {"project": {"uri": "workspace:///project", "type": "paper.project"},
                                    "file": "main.md", "byteStart": block["byteStart"],
                                    "byteEnd": block["byteEnd"], "text": "Edited through the adapter.",
                                    "fileRevision": entry["revision"]})
        self.assertTrue(edited["ok"], edited)
        self.assertIn("Edited through the adapter.", (self.root / "main.md").read_text())
        undone = self.call("undo", {"project": {"uri": "workspace:///project", "type": "paper.project"},
                                    "expectedRevision": edited["data"]["project"]["revision"]})
        self.assertTrue(undone["ok"], undone)
        self.assertIn("A plain paragraph", (self.root / "main.md").read_text())

    def test_build_rejects_a_revision_the_editor_never_made(self):
        project = self.project()
        result = self.call("build", {"project": {**project, "revision": "0" * 64}, "formats": ["html"]})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "STALE_REVISION")
