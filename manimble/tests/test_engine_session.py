import importlib.util
import shutil
from pathlib import Path
import os
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
import zipfile


PLUGIN = Path(__file__).resolve().parents[1]
TOOLS = {"describe", "source", "state", "scene", "render", "edit_source", "slide", "insert", "edit", "undo", "redo", "export"}


def load_adapter():
    spec = importlib.util.spec_from_file_location("manimble_adapter", PLUGIN / "adapter.py")
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    return adapter


class RealEngine(unittest.TestCase):
    def setUp(self):
        python = os.environ.get("MANIMBLE_ENGINE_PYTHON")
        if not python:
            if os.environ.get("MANIMBLE_REQUIRE_REAL"):
                self.fail("MANIMBLE_ENGINE_PYTHON must be set")
            self.skipTest("Manimble engine Python not configured")
        self.adapter = load_adapter()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        self.params = {"workspaceRoot": str(self.workspace), "settings": {
            "python": python, "engineRoot": str(PLUGIN.parents[1] / "engine"),
        }}

    def open(self, name, fixture="deck"):
        shutil.copytree(PLUGIN / "fixtures" / fixture, self.workspace / name)
        instance = self.adapter.Adapter()
        self.addCleanup(instance.deactivate, {"reason": "test"})
        params = {**self.params, "bindingResource": {"uri": f"workspace:///{name}", "type": "manimble.deck"}}
        catalog = instance.activate(params)
        self.assertEqual({item["name"] for item in catalog["tools"]}, TOOLS)
        return instance, params

    def call(self, instance, tool, args):
        ctrl = SimpleNamespace(cancelled=threading.Event(), progress=lambda _: None)
        result = instance.call(tool, args, {"callId": "test-" + tool}, ctrl)
        self.assertTrue(result["ok"], result)
        return result

    def test_open_edit_render_and_export_pptx_in_two_decks(self):
        first, params = self.open("first")
        second, _ = self.open("second")
        with self.assertRaisesRegex(self.adapter.ProtocolError, "already bound"):
            self.adapter.Adapter().activate(params)
        slide = self.call(first, "render", {"scene": "Intro"})
        png = next(ref for ref in slide["resources"] if ref["type"] == "manimble.preview")
        self.assertEqual(Path(first.resolve(png)["path"]).read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.call(first, "edit_source", {"scene": "Intro", "source": slide["data"]["source"],
            "expected": slide["data"]["text"], "text": slide["data"]["text"].replace("GeWu slide fixture", "First deck edited")})
        out = self.call(first, "export", {"format": "pptx"})
        with zipfile.ZipFile(first.resolve(out["resources"][0])["path"]) as archive:
            self.assertIn(b"First deck edited", archive.read("ppt/slides/slide1.xml"))
        deck = self.call(second, "describe", {})
        self.assertEqual(deck["data"]["scenes"][0]["name"], "Intro")
        self.assertIn("GeWu slide fixture", (self.workspace / "second/slides.py").read_text())

    def test_source_reads_code_without_rendering_and_patches_minimally(self):
        instance, _ = self.open("first")
        # No render: `source` must still return the class text.
        source = self.call(instance, "source", {"scene": "Intro"})
        self.assertNotIn("objects", source["data"])
        self.assertNotIn("frames", source["data"])
        self.assertIn("GeWu slide fixture", source["data"]["text"])
        ref = source["resources"][0] if source["resources"] else None
        # Patch one literal instead of resending the whole class.
        detail = self.call(instance, "describe", {})
        ref = detail["data"]["scenes"][0]["source"]
        self.call(instance, "edit_source", {"scene": "Intro", "source": ref,
            "expectedText": source["data"]["text"],
            "edits": [{"oldText": "GeWu slide fixture", "newText": "Patched slide"}]})
        self.assertIn("Patched slide", (self.workspace / "first/slides.py").read_text())
        # A stale expectedText is refused with an actionable message.
        fresh = self.call(instance, "describe", {})["data"]["scenes"][0]["source"]
        ctrl = SimpleNamespace(cancelled=threading.Event(), progress=lambda _: None)
        stale = instance.call("edit_source", {"scene": "Intro", "source": fresh,
            "expectedText": source["data"]["text"], "text": "class Intro: pass"},
            {"callId": "stale"}, ctrl)
        self.assertFalse(stale["ok"])
        self.assertIn("read class Intro again", stale["error"]["message"])

    def test_moving_an_object_keeps_a_deck_with_explicit_imports_renderable(self):
        # This deck imports RoundedRectangle and Text, not RIGHT/DOWN: a move
        # written with direction names would break it and be rolled back.
        instance, _ = self.open("explicit", "explicit")

        def box(scene, oid):
            entry = next(item for item in scene["stops"][0]["visible"] if str(item[0]) == oid)
            return entry[1:]

        before = self.call(instance, "scene", {"scene": "Intro"})["data"]
        card = next(oid for oid, info in before["objects"].items() if info.get("kind") == "shape")
        start = box(before, card)

        result = self.call(instance, "edit", {"scene": "Intro", "op": "move", "oid": card, "dx": 80, "dy": 0})
        self.assertTrue(result["ok"], result)
        # A move is committed optimistically and verified on a thread; the next
        # write waits for that verdict, so read the slide only after one.
        self.call(instance, "edit", {"scene": "Intro", "op": "rotate", "oid": card, "degrees": 0})
        after = self.call(instance, "scene", {"scene": "Intro"})["data"]
        moved = box(after, card)
        self.assertGreater(moved[0], start[0] + 5)
        self.assertAlmostEqual(moved[1], start[1], delta=2)

    def test_cancels_real_export_after_native_start(self):
        instance, _ = self.open("deck")
        script = self.workspace / "deck/slides.py"
        script.write_text(script.read_text().replace("self.add(Circle()", "__import__('time').sleep(1.5)\n        self.add(Circle()"))
        cancelled = threading.Event()

        def progress(message):
            if message.startswith("Building slide"):
                cancelled.set()

        control = SimpleNamespace(cancelled=cancelled, progress=progress)
        result = instance.call("export", {"format":"pptx"}, {"callId":"cancel-real"}, control)
        self.assertTrue(cancelled.is_set(), "Native export did not acknowledge its start")
        self.assertEqual(result["error"]["code"], "CANCELLED", result)
        self.assertEqual(result["resources"], [])

    def test_editor_tools_refuse_a_stale_deck_revision(self):
        instance, _ = self.open("edit")
        state = self.call(instance, "state", {})["data"]
        self.assertTrue(state["revision"])
        self.assertEqual([s["name"] for s in state["scenes"]], ["Intro"])

        scene = self.call(instance, "scene", {"scene": "Intro"})["data"]
        self.assertGreater(scene["size"][0], 0)
        self.assertEqual(len(scene["frames"]), scene["builds"])
        self.assertTrue(scene["objects"])
        # The thumbnail only exists once the slide has rendered.
        self.assertIn("thumb", self.call(instance, "state", {})["data"]["scenes"][0])

        self.call(instance, "insert", {"scene": "Intro", "kind": "text", "value": "From the editor"})
        script = self.workspace / "edit/slides.py"
        self.assertIn("From the editor", script.read_text())
        self.call(instance, "undo", {})
        self.assertNotIn("From the editor", script.read_text())

        # A revision read before another edit must not overwrite the newer deck.
        stale = state["revision"]
        self.call(instance, "insert", {"scene": "Intro", "kind": "circle"})
        ctrl = SimpleNamespace(cancelled=threading.Event(), progress=lambda _: None)
        refused = instance.call("slide", {"op": "new", "scene": "Intro", "layout": "title", "expected": stale},
                                {"callId": "stale-write"}, ctrl)
        self.assertFalse(refused["ok"], refused)
        self.assertEqual(refused["error"]["code"], "STALE_REVISION")

    def test_slide_operations_change_order_and_survive_undo(self):
        instance, _ = self.open("show")
        self.call(instance, "slide", {"op": "new", "scene": "Intro", "layout": "title"})
        state = self.call(instance, "state", {})["data"]
        self.assertEqual(len(state["scenes"]), 2)
        added = state["scenes"][1]["name"]
        self.call(instance, "slide", {"op": "up", "scene": added})
        state = self.call(instance, "state", {})["data"]
        self.assertEqual(state["scenes"][0]["name"], added)
        self.call(instance, "undo", {})
        self.call(instance, "undo", {})
        state = self.call(instance, "state", {})["data"]
        self.assertEqual([s["name"] for s in state["scenes"]], ["Intro"])

    def test_canvas_edits_reach_the_source_and_undo(self):
        instance, _ = self.open("canvas")
        scene = self.call(instance, "scene", {"scene": "Intro"})["data"]
        script = self.workspace / "canvas/slides.py"
        original = script.read_text()
        text = next(oid for oid, info in scene["objects"].items() if info.get("value") == "GeWu slide fixture")
        shape = next(oid for oid in scene["objects"] if oid != text)

        # The editor's inspector and handles are these three commands.
        self.call(instance, "edit", {"scene": "Intro", "op": "font", "oid": text, "value": 72})
        self.assertIn("font_size=72", script.read_text())
        self.call(instance, "edit", {"scene": "Intro", "op": "rotate", "oid": shape, "degrees": 45})
        self.assertIn(".rotate(", script.read_text())
        self.call(instance, "edit", {"scene": "Intro", "op": "resize", "oid": shape, "ratio": 1.5})
        self.assertIn(".scale(", script.read_text())

        # Multi-select commands take the primary id plus the whole selection.
        self.call(instance, "edit", {"scene": "Intro", "op": "group", "oid": text, "oids": [text, shape]})
        self.assertIn("Group(", script.read_text())
        # Stacking order may rewrite the add() order instead of z indexes; the
        # engine owns that choice, so assert the deck stays intact.
        self.call(instance, "edit", {"scene": "Intro", "op": "order", "value": "front", "oid": text, "oids": [text]})
        self.assertIn("Group(", script.read_text())

        ctrl = SimpleNamespace(cancelled=threading.Event(), progress=lambda _: None)
        for _ in range(8):
            if not instance.call("undo", {}, {"callId": "undo"}, ctrl)["ok"]:
                break
        self.assertEqual(script.read_text(), original)
