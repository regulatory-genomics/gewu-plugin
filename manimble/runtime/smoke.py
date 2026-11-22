"""Exercise the adapter and its actual render/export workers in a private deck."""
from pathlib import Path
import os
import shutil
import tempfile
import threading
from types import SimpleNamespace
import zipfile

from adapter import Adapter


def main():
    with tempfile.TemporaryDirectory(prefix="manimble-smoke-", dir=Path.cwd()) as directory:
        workspace = Path(directory)
        fixture = Path(os.environ["GEWU_PLUGIN_ROOT"]) / "fixtures/deck/slides.py"
        (workspace / "deck").mkdir()
        shutil.copyfile(fixture, workspace / "deck/slides.py")
        tex = (Path(os.environ["GEWU_RUNTIME_ROOT"]) / "tex/bin").is_dir()
        if tex:
            scene = workspace / "deck/slides.py"
            scene.write_text(scene.read_text().replace("self.add(circle, label)",
                "self.add(circle, label, MathTex(r'x^2').shift(RIGHT * 2))"))
        os.environ["MPLCONFIGDIR"] = str(workspace / "matplotlib")
        adapter = Adapter()
        try:
            adapter.activate({"workspaceRoot": directory, "settings": {},
                              "bindingResource": {"type": "manimble.deck", "uri": "workspace:///deck"}})
            control = SimpleNamespace(cancelled=threading.Event(), progress=lambda _: None)
            for tool, args, mime in [("render", {"scene": "Intro"}, "manimble.preview"),
                                     ("export", {"format": "pptx"}, "manimble.pptx")]:
                result = adapter.call(tool, args, {"callId": "smoke-" + tool}, control)
                if not result["ok"]:
                    raise RuntimeError(result["error"])
                ref = next(ref for ref in result["resources"] if ref["type"] == mime)
                out = Path(adapter.resolve(ref)["path"])
                if tool == "render":
                    with out.open("rb") as stream:
                        if stream.read(8) != b"\x89PNG\r\n\x1a\n":
                            raise RuntimeError("renderer did not produce PNG")
                else:
                    with zipfile.ZipFile(out) as deck:
                        if b"GeWu slide fixture" not in deck.read("ppt/slides/slide1.xml"):
                            raise RuntimeError("PPTX missing the rendered slide text")
            print("Manimble: PNG and PPTX verified")
        finally:
            adapter.deactivate({"reason": "smoke complete"})


if __name__ == "__main__":
    main()
