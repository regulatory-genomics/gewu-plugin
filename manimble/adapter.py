"""GeWu adapter for the existing Manimble JSON-lines engine."""

import fcntl
import hashlib
import itertools
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
from urllib.parse import quote, unquote
import zipfile

from gewu_plugin import ProtocolError, serve


MIME = {"manimble.source": "text/x-python", "manimble.preview": "image/png",
        "manimble.thumb": "image/jpeg",
        "manimble.pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}


def revision(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inside(root, value):
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise ProtocolError("INVALID_PATH", "Path is outside the bound workspace directory")
    return path


def from_uri(root, uri):
    if not uri.startswith("workspace:///"):
        raise ProtocolError("INVALID_RESOURCE", "Expected a workspace URI")
    value = unquote(uri.removeprefix("workspace:///"))
    if not value or value.startswith("/") or "\x00" in value or any(p == ".." for p in value.split("/")):
        raise ProtocolError("INVALID_RESOURCE", "Invalid workspace URI")
    return inside(root, value)


def success(data, resources=(), **extra):
    return {"ok": True, "data": data, "resources": list(resources), **extra}


def failure(error, resources=(), **extra):
    return {"ok": False, "error": error.payload, "resources": list(resources), **extra}


def engine_environment(root, python):
    """The packed runtime keeps its shared libraries and helpers under root."""
    env = {**os.environ, "PYTHONPATH": str(root), "MANIMBLE_PYTHON": str(python), "PYTHONDONTWRITEBYTECODE": "1"}
    env["PATH"] = str(root / "bin") + os.pathsep + env.get("PATH", "")
    library_path = str(root / "lib")
    if env.get("LD_LIBRARY_PATH"):
        library_path += os.pathsep + env["LD_LIBRARY_PATH"]
    env["LD_LIBRARY_PATH"] = library_path
    return env


class Engine:
    def __init__(self, python, root, deck, cache):
        env = engine_environment(root, python)
        self.process = subprocess.Popen([str(python), "-m", "manimble.rpc"], cwd=deck, env=env,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.serial = itertools.count(1)
        self.lock = threading.Lock()
        self.pending = {}
        self.export_events = None
        self.export_progress = None
        self.closed = threading.Event()
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()
        try:
            if not self.request("open", deck=str(deck), cache=str(cache), timeout=30).get("ok"):
                raise ProtocolError("DECK_OPEN_FAILED", "Manimble refused the deck")
            if not isinstance(self.request("state", timeout=30).get("scenes"), list):
                raise ProtocolError("DECK_OPEN_FAILED", "Manimble did not describe the deck")
        except BaseException:
            self.close()
            raise

    def read(self):
        try:
            while line := self.process.stdout.readline(16 * 1024 * 1024 + 1):
                if not line.endswith(b"\n") or len(line) > 16 * 1024 * 1024:
                    break
                message = json.loads(line)
                progress = None
                with self.lock:
                    response = self.pending.get(message.get("id"))
                    if response is not None:
                        response.put_nowait(message)
                    elif message.get("event") == "export" and self.export_events is not None:
                        if message.get("phase") in ("done", "error", "cancelled"):
                            self.export_events.put_nowait(message)
                        elif message.get("phase") == "building":
                            progress = self.export_progress
                if progress is not None:
                    progress(f"Building slide {message.get('done', 0)}/{message.get('total', 0)}")
        except (OSError, ValueError, TypeError, AttributeError, queue.Full):
            pass
        finally:
            self.closed.set()

    def wait(self, response, timeout, control=None):
        deadline = time.monotonic() + timeout
        cancel_at = None
        while True:
            try:
                return response.get(timeout=0.05)
            except queue.Empty:
                pass
            now = time.monotonic()
            if self.closed.is_set() or now > deadline or cancel_at is not None and now > cancel_at:
                self.close()
                raise ProtocolError("OUTCOME_UNKNOWN", "Manimble disconnected or did not confirm completion")
            if control and control.cancelled.is_set() and cancel_at is None:
                self.request("cancel", timeout=3)
                cancel_at = now + 4

    def request(self, command, *, timeout=180, control=None, **params):
        ticket = queue.Queue(maxsize=1)
        with self.lock:
            if self.closed.is_set():
                raise ProtocolError("ENGINE_CLOSED", "Manimble engine is no longer bound")
            request_id = next(self.serial)
            payload = json.dumps({"id": request_id, "cmd": command, **params}).encode() + b"\n"
            self.pending[request_id] = ticket
            try:
                self.process.stdin.write(payload)
                self.process.stdin.flush()
            except OSError as error:
                self.pending.pop(request_id, None)
                raise ProtocolError("OUTCOME_UNKNOWN", "Manimble pipe closed while sending a request") from error
        try:
            answer = self.wait(ticket, timeout, control)
        finally:
            with self.lock:
                self.pending.pop(request_id, None)
        if "error" in answer or not isinstance(answer.get("result"), dict):
            raise ProtocolError("ENGINE_ERROR", str(answer.get("error") or "Invalid Manimble reply")[:2000])
        return answer["result"]

    def export(self, path, control, args):
        events = queue.Queue(maxsize=1)
        with self.lock:
            self.export_events = events
            self.export_progress = control.progress
        try:
            accepted = self.request("export", kind="pptx", path=str(path), builds=args.get("builds", True),
                                    editable=args.get("editableText", True))
            if not accepted.get("ok"):
                raise ProtocolError("EXPORT_FAILED", str(accepted.get("error") or "Export refused"))
            event = self.wait(events, 29 * 60, control)
            if event["phase"] == "cancelled":
                raise ProtocolError("CANCELLED", "Manimble confirmed that export stopped")
            if event["phase"] != "done" or event.get("path") != str(path):
                raise ProtocolError("EXPORT_FAILED", str(event.get("error") or "Export did not confirm the expected file"))
        finally:
            with self.lock:
                self.export_events = None
                self.export_progress = None

    def close(self):
        self.closed.set()
        self.process.stdin.close()
        try:
            self.process.wait(timeout=4)
        except subprocess.TimeoutExpired as error:
            raise ProtocolError("CLEANUP_INCOMPLETE", "Manimble did not stop after its command pipe closed") from error
        self.reader.join(timeout=1)
        self.process.stdout.close()


class Adapter:
    def __init__(self):
        self.engine = None
        self.deck_lock = None
        self.foreground = threading.Lock()

    def activate(self, params):
        self.workspace = Path(params["workspaceRoot"]).resolve(strict=True)
        binding = params.get("bindingResource", {})
        if binding.get("type") != "manimble.deck":
            raise ProtocolError("DECK_REQUIRED", "Select a workspace directory as a Manimble deck")
        self.deck = from_uri(self.workspace, binding["uri"])
        if not self.deck.is_dir():
            raise ProtocolError("DECK_NOT_FOUND", "Manimble deck directory does not exist")
        config = params["settings"]
        runtime = os.environ.get("GEWU_RUNTIME_ROOT")
        if runtime:
            runtime_root = Path(runtime).resolve()
            python = runtime_root / "bin" / "python"
            if not python.is_file():
                python = runtime_root / "bin" / "python3"
            # The engine is plugin code and ships in the package. A packed
            # runtime may also carry a copy for backward compatibility.
            bundled = runtime_root if (runtime_root / "manimble/rpc.py").is_file() else None
            packaged = Path(os.environ.get("GEWU_PLUGIN_ROOT", "")) / "engine"
            root = bundled or packaged
        else:
            # Development-only compatibility for existing local verification.
            python_value = config.get("python")
            engine_value = config.get("engineRoot")
            if not python_value or not engine_value:
                raise ProtocolError("RUNTIME_UNAVAILABLE", "The host did not provide a Manimble runtime")
            python = Path(python_value).absolute()
            root = Path(engine_value).resolve()
        if not python.is_file() or not os.access(python, os.X_OK):
            raise ProtocolError("PYTHON_UNAVAILABLE", "Configure an executable Manimble Python interpreter")
        if not (root / "manimble/rpc.py").is_file():
            raise ProtocolError("ENGINE_UNAVAILABLE", "engineRoot must contain manimble/rpc.py")
        try:
            probe = subprocess.run([str(python), "-c", "import manim, manim_slides, PIL, pptx"],
                                   env=engine_environment(root, python),
                                   stdout=subprocess.DEVNULL, timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ProtocolError("PYTHON_UNAVAILABLE", "Manimble Python probe did not complete") from error
        if probe.returncode:
            raise ProtocolError("MANIM_UNAVAILABLE", "Manimble Python must import manim, manim_slides, PIL and pptx")
        key = hashlib.sha256(os.fsencode(self.deck)).hexdigest()
        self.cache = inside(self.workspace, f".gewu/manimble/{key}")
        self.cache.mkdir(parents=True, exist_ok=True)
        self.deck_lock = inside(self.cache, "engine.lock").open("a")
        try:
            fcntl.flock(self.deck_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self.deck_lock.close()
            self.deck_lock = None
            raise ProtocolError("DECK_IN_USE", "Deck is already bound to a Manimble engine") from error
        try:
            self.engine = Engine(python, root, self.deck, self.cache)
        except BaseException:
            self.deactivate({})
            raise
        return {"tools": json.loads(Path(__file__).with_name("tools.schema.json").read_text()), "serviceEndpoints": []}

    def deactivate(self, _params):
        if self.engine:
            self.engine.close()
            self.engine = None
        if self.deck_lock:
            self.deck_lock.close()
            self.deck_lock = None

    def reference(self, path, kind):
        if not path.is_file() or not path.is_relative_to(self.workspace):
            raise ProtocolError("FILE_UNAVAILABLE", "Manimble did not produce an accessible file")
        return {"uri": "workspace:///" + quote(path.relative_to(self.workspace).as_posix()),
                "type": kind, "mimeType": MIME[kind], "revision": revision(path)}

    def resolve(self, ref):
        path = from_uri(self.workspace, ref["uri"])
        kind = ref["type"]
        if not ((kind == "manimble.source" and path.is_relative_to(self.deck))
                or (kind in ("manimble.preview", "manimble.thumb") and path.is_relative_to(self.cache))
                or (kind == "manimble.pptx" and path.is_relative_to(inside(self.workspace, ".gewu/outputs")))):
            raise ProtocolError("INVALID_RESOURCE", "This file is not a resource of the deck")
        current = self.reference(path, kind)
        if ref.get("revision", current["revision"]) != current["revision"]:
            raise ProtocolError("STALE_REVISION", "Manimble resource changed; refresh it first")
        return {"kind": "file", "path": str(path), "mimeType": current["mimeType"], "revision": current["revision"]}

    def deck_revision(self):
        """Hash the deck's own Python sources and order.txt, not its caches."""
        parts = []
        for path in sorted(self.deck.glob("*.py")):
            parts.append(f"{path.name}:{revision(path)}")
        order = self.deck / "order.txt"
        if order.is_file():
            parts.append(f"order.txt:{revision(order)}")
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()

    def source_revisions(self):
        paths = list(sorted(self.deck.glob("*.py")))
        order = self.deck / "order.txt"
        if order.is_file():
            paths.append(order)
        return {path: revision(path) for path in paths}

    def deck_changes(self, before):
        after = self.source_revisions()
        changes, resources = [], []
        for path, now in after.items():
            was = before.get(path)
            if was == now:
                continue
            ref = self.reference(path, "manimble.source")
            changes.append({"uri": ref["uri"], "beforeRevision": was, "afterRevision": now})
            resources.append(ref)
        return changes, resources

    def write(self, tool, args, control):
        expected = args.pop("expected", None)
        if expected is not None and expected != self.deck_revision():
            raise ProtocolError("STALE_REVISION", "The deck changed since it was read; reload before writing")
        before = self.source_revisions()
        answer = self.engine.request(tool, control=control, **args)
        changes, resources = self.deck_changes(before)
        if not answer.get("ok"):
            return failure(ProtocolError("EDIT_FAILED", str(answer.get("error") or "Manimble refused the edit")[:2000]),
                           resources, changes=changes)
        data = {key: value for key, value in answer.items() if key not in ("ok", "changed")}
        data["revision"] = self.deck_revision()
        return success(data, resources, changes=changes)

    def source(self, name):
        return success(self.engine.request("source", scene=name, timeout=30))

    def call(self, tool, args, context, control):
        try:
            if tool == "describe":
                state = self.engine.request("state", timeout=30)
                scenes = [{"name": scene["name"], "status": scene["status"],
                           "source": self.reference(inside(self.deck, scene["file"]), "manimble.source")}
                          for scene in state["scenes"]]
                return success({"scenes": scenes}, list({s["source"]["uri"]: s["source"] for s in scenes}.values()))
            if tool == "source":
                return self.source(args["scene"])
            while not self.foreground.acquire(timeout=0.1):
                if control.cancelled.is_set():
                    break
            else:
                try:
                    if not control.cancelled.is_set():
                        return self.foreground_call(tool, args, context, control)
                finally:
                    self.foreground.release()
            return failure(ProtocolError("CANCELLED", "Manimble call was removed before execution"))
        except ProtocolError as error:
            return failure(error)
        except (ValueError, KeyError, OSError, TypeError) as error:
            return failure(ProtocolError("INVALID_CALL", str(error)[:1000] or "Invalid Manimble call"))

    def state(self):
        deck = self.engine.request("state", timeout=30)
        scenes, resources = [], []
        for scene in deck["scenes"]:
            item = {"name": scene["name"], "status": scene["status"], "hidden": bool(scene.get("hidden"))}
            path = scene.get("thumb")
            if path:
                with_path = Path(path)
                if with_path.is_file():
                    item["thumb"] = self.reference(with_path, "manimble.thumb")
                    resources.append(item["thumb"])
            scenes.append(item)
        return success({"deck": deck["deck"], "scenes": scenes, "canUndo": bool(deck["canUndo"]),
                        "canRedo": bool(deck["canRedo"]), "undoLabel": deck.get("undoLabel", ""),
                        "revision": self.deck_revision()}, resources)

    def scene(self, name, control):
        scene = self.engine.request("scene", name=name, control=control)
        if scene.get("status") != "ready":
            code = "CANCELLED" if scene.get("status") == "cancelled" else "RENDER_FAILED"
            raise ProtocolError(code, str(scene.get("error") or "The slide did not render")[:2000])
        source = self.reference(inside(self.deck, scene["source"]["file"]), "manimble.source")
        frames = [self.reference(inside(self.cache, stop["png"]), "manimble.preview")
                  for stop in scene["stops"]]
        objects = {oid: {key: info[key] for key in
                          ("kind", "value", "fontSize", "font", "weight", "slant", "color", "rotation", "dims", "group",
                          "format", "loc", "textOk", "shape", "follows") if key in info}
                   for oid, info in scene["objects"].items()}
        return success({"scene": name, "size": scene["size"], "frame": scene["frame"],
                        "builds": len(scene["stops"]),
                        "stops": [{"visible": stop["visible"]} for stop in scene["stops"]],
                        "objects": objects, "source": source, "text": scene["source"]["text"],
                        "frames": frames, "revision": self.deck_revision()}, [source, *frames])

    def foreground_call(self, tool, args, context, control):
        if tool == "state":
            return self.state()
        if tool == "scene":
            return self.scene(args["scene"], control)
        if tool in ("slide", "insert", "edit", "undo", "redo"):
            return self.write(tool, args, control)
        if tool == "render":
            control.progress(f"Rendering {args['scene']}")
            scene = self.engine.request("scene", name=args["scene"], control=control)
            if scene.get("status") != "ready" or not scene.get("stops"):
                code = "CANCELLED" if scene.get("status") == "cancelled" else "RENDER_FAILED"
                raise ProtocolError(code, str(scene.get("error") or "No Manimble frames were produced")[:2000])
            source = self.reference(inside(self.deck, scene["source"]["file"]), "manimble.source")
            frames = [self.reference(inside(self.cache, stop["png"]), "manimble.preview") for stop in scene["stops"]]
            data = {"scene": args["scene"], "source": source, "text": scene["source"]["text"], "frames": frames}
            # `include` lets an agent ask for the source alone instead of
            # paying for frames it will not use.
            if args.get("include"):
                keep = set(args["include"]) | {"scene", "source"}
                data = {key: value for key, value in data.items() if key in keep}
            return success(data, [source, *frames])
        if tool == "edit_source":
            source = args["source"]
            path = inside(self.deck, from_uri(self.workspace, source["uri"]))
            before = revision(path)
            if before != source["revision"]:
                raise ProtocolError("STALE_REVISION", "Slide source changed; reload before editing")
            payload = {key: args[key] for key in ("expectedText", "text", "edits") if key in args}
            if "expectedText" not in payload and "expected" in args:
                payload["expectedText"] = args["expected"]
            try:
                result = self.engine.request("edit_source", scene=args["scene"], file=path.relative_to(self.deck).as_posix(),
                                             control=control, **payload)
                error = None
                if result.get("error") or not result.get("ok"):
                    error = ProtocolError("EDIT_FAILED", str(result.get("error") or "Edit refused")[:2000])
            except ProtocolError as caught:
                error = caught
            ref = self.reference(path, "manimble.source")
            changes = [{"uri": ref["uri"], "beforeRevision": before, "afterRevision": ref["revision"]}] if ref["revision"] != before else []
            return failure(error, [ref], changes=changes) if error else success({"source": ref}, [ref], changes=changes)
        if tool == "export" and args.get("format") == "pptx":
            call_dir = hashlib.sha256(context["callId"].encode()).hexdigest()
            output = inside(self.workspace, f".gewu/outputs/{call_dir}")
            output.mkdir(parents=True, exist_ok=False)
            path = output / "deck.pptx"
            control.progress("Exporting deck to PowerPoint")
            self.engine.export(path, control, args)
            with zipfile.ZipFile(path) as pptx:
                if "ppt/presentation.xml" not in pptx.namelist():
                    raise ProtocolError("EXPORT_FAILED", "Manimble did not write a readable presentation")
            return success({"format": "pptx"}, [self.reference(path.resolve(), "manimble.pptx")])
        raise ProtocolError("INVALID_CALL", "Unsupported Manimble tool")


if __name__ == "__main__":
    adapter = Adapter()
    try:
        serve(activate=adapter.activate, call=adapter.call, resolve=adapter.resolve, deactivate=adapter.deactivate)
    finally:
        adapter.deactivate({})
