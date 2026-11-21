"""Invoke Paper's native CLI under GeWu's process ownership protocol."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import quote, unquote

from gewu_plugin import ProtocolError, serve
# The adapter may be imported by a test harness, so do not rely on the script
# directory already being on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import document  # noqa: E402

MIME = {"html": "text/html", "pdf": "application/pdf", "tex": "text/x-tex", "md": "text/markdown",
        "yaml": "text/yaml", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def failure(code, message, resources=()):
    return {"ok": False, "error": ProtocolError(code, message).payload, "resources": list(resources)}


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def contained(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ProtocolError("INVALID_PATH", "The Paper path must stay inside its workspace or project")
    return path


def kill_group(pid, sig):
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        return False
    return True


def stop_group(child):
    for sig, limit in ((signal.SIGTERM, 0.8), (signal.SIGKILL, 1.5)):
        kill_group(child.pid, sig)
        end = time.monotonic() + limit
        while time.monotonic() < end:
            child.poll()
            if not kill_group(child.pid, 0):
                return
            time.sleep(0.025)
    raise ProtocolError("CLEANUP_INCOMPLETE", "Paper process group did not exit")


class Adapter:
    def activate(self, params):
        self.root = Path(params["workspaceRoot"]).resolve(strict=True)
        runtime = os.environ.get("GEWU_RUNTIME_ROOT")
        if runtime:
            self.executable = Path(runtime) / "bin" / "paper"
            if not self.executable.is_file() or not os.access(self.executable, os.X_OK):
                raise ProtocolError("RUNTIME_UNAVAILABLE", "The selected Paper runtime does not contain bin/paper")
        else:
            configured = params["settings"].get("executable")
            if not configured:
                raise ProtocolError("RUNTIME_UNAVAILABLE", "The host did not provide a Paper runtime")
            self.executable = Path(configured).absolute()
        self.queues = {}
        self.queues_lock = threading.Lock()
        for command, expected in (("describe", b"--json"), ("build", b"--output-stem"),
                                  ("inspect", b"--json"), ("plan-edit", b"--json")):
            try:
                probe = subprocess.run([self.executable, command, "--help"], cwd=self.root,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=8, check=False)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ProtocolError("PAPER_UNAVAILABLE", "Cannot launch the configured Paper CLI") from error
            if probe.returncode or expected not in probe.stdout:
                raise ProtocolError("PAPER_VERSION_UNSUPPORTED", "Use Paper built with describe and --output-stem support")
        tools = json.loads(Path(__file__).with_name("tools.schema.json").read_text())
        return {"tools": tools, "serviceEndpoints": []}

    def run_paper(self, argv, control):
        if control.cancelled.is_set():
            raise ProtocolError("CANCELLED", "Paper call stopped before the CLI started")
        gate, release = os.pipe()
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            try:
                child = subprocess.Popen([sys.executable, "-c",
                    "import os,sys; fd=int(sys.argv[1]); ready=os.read(fd,1); os.close(fd); "
                    "ready and os.execv(sys.argv[2],sys.argv[2:])", str(gate), str(self.executable), *map(str, argv)],
                    cwd=self.root, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                    pass_fds=(gate,), process_group=0)
            except BaseException:
                os.close(release)
                raise
            finally:
                os.close(gate)
            registered = False
            try:
                control.register_process(child.pid, child.pid)
                registered = True
                if not control.cancelled.is_set():
                    os.write(release, b"1")
                while child.poll() is None and not control.cancelled.wait(0.06):
                    pass
                if child.returncode is None:
                    stop_group(child)
                    raise ProtocolError("CANCELLED", "Paper process group stopped")
                if kill_group(child.pid, 0):
                    stop_group(child)
                    raise ProtocolError("OUTCOME_UNKNOWN", "Paper exited before its child processes completed")
                stdout.seek(0)
                stderr.seek(max(0, stderr.tell() - 2048))
                return child.returncode, stdout.read(950_000), stderr.read(2048).decode(errors="replace").strip()
            finally:
                os.close(release)
                if kill_group(child.pid, 0):
                    stop_group(child)
                child.wait()
                if registered:
                    control.process_exited(child.pid, child.pid)

    def ref(self, path, kind, rev=None):
        result = {"uri": "workspace:///" + quote(path.relative_to(self.root).as_posix()), "type": kind,
                  "revision": rev if rev is not None else file_hash(path)}
        if path.is_file():
            result["mimeType"] = MIME.get(path.suffix.lstrip("."), "application/octet-stream")
        return result

    def project_from_ref(self, ref):
        uri = ref["uri"]
        if ref.get("type") != "paper.project" or not uri.startswith("workspace:///"):
            raise ProtocolError("INVALID_RESOURCE", "Expected a Paper project workspace reference")
        value = unquote(uri.removeprefix("workspace:///"))
        if not value or value.startswith("/") or value == "." or "\x00" in value or ".." in value.split("/"):
            raise ProtocolError("INVALID_RESOURCE", "Invalid Paper project path")
        return contained(self.root, value)

    def describe(self, project, control):
        if not (project / "metadata.yaml").is_file():
            raise ProtocolError("PROJECT_NOT_FOUND", "Project needs a metadata.yaml file")
        rc, body, error = self.run_paper(["describe", project, "--json"], control)
        if rc:
            raise ProtocolError("DESCRIBE_FAILED", error or "Paper could not describe the project")
        desc = json.loads(body)
        if desc.get("version") != 1:
            raise ProtocolError("PAPER_VERSION_UNSUPPORTED", "Unrecognized Paper describe version")
        files = [contained(project, name) for name in ["metadata.yaml", *desc["sources"], *desc["supplement"]]]
        refs = [self.ref(path, "paper.source") for path in files]
        rev = hashlib.sha256(json.dumps([(r["uri"], r["revision"]) for r in refs]).encode()).hexdigest()
        desc["project"] = self.ref(project, "paper.project", rev)
        return desc, refs

    def resolve(self, ref):
        uri = ref["uri"]
        if not uri.startswith("workspace:///") or ref.get("type") not in ("paper.source", "paper.output"):
            raise ProtocolError("INVALID_RESOURCE", "Expected a Paper source or output file")
        path = contained(self.root, unquote(uri.removeprefix("workspace:///")))
        if not path.is_file():
            raise ProtocolError("FILE_NOT_FOUND", "Paper file does not exist")
        actual = self.ref(path, ref["type"])
        if ref.get("revision", actual["revision"]) != actual["revision"]:
            raise ProtocolError("STALE_REVISION", "Paper file changed; refresh before using it")
        return {"kind": "file", "path": str(path), "mimeType": actual["mimeType"], "revision": actual["revision"]}

    def call(self, tool, args, context, control):
        try:
            if tool in ("document", "edit", "undo", "redo"):
                return self.document_call(tool, self.project_from_ref(args["project"]), args)
            if tool == "init":
                path = Path(args["directory"])
                if path.is_absolute() or not path.parts or ".." in path.parts:
                    return failure("INVALID_PATH", "Provide a relative project directory")
                project = contained(self.root, path)
            else:
                project = self.project_from_ref(args["project"])
            with self.queues_lock:
                guard = self.queues.setdefault(project, threading.Lock())
            while not guard.acquire(timeout=0.08):
                if control.cancelled.is_set():
                    return failure("CANCELLED", "Removed from the Paper project queue")
            try:
                if tool == "init":
                    rc, _, error = self.run_paper(["init", project], control)
                    if rc:
                        return failure("INIT_FAILED", error or "Paper could not initialize the project")
                desc, refs = self.describe(project, control)
                if tool in ("describe", "init"):
                    return {"ok": True, "data": desc, "resources": [desc["project"], *refs]}
                if tool == "build":
                    return self.build(project, desc, args, context, control)
                return failure("INVALID_CALL", "Unsupported Paper tool")
            finally:
                guard.release()
        except ProtocolError as error:
            return {"ok": False, "error": error.payload, "resources": []}
        except (OSError, TypeError, ValueError, KeyError) as error:
            return failure("PAPER_ERROR", str(error)[:2000] or "Paper call failed")

    def document_call(self, tool, project, args):
        """The editable document API, shared by the Preview and the agent."""
        try:
            if tool == "document":
                data = document.snapshot(self.executable, project, args["project"]["uri"])
            elif tool == "edit":
                data = document.apply_edit(self.executable, project, args["project"]["uri"], args)
            else:
                data = document.step_history(self.executable, project, args["project"]["uri"],
                                             args.get("expectedRevision"), tool)
        except document.DocumentError as error:
            return failure(error.code, error.message)
        refs = [data["project"]]
        for entry in data.get("files", ()):
            if entry.get("revision"):
                refs.append(self.ref(project / entry["path"], "paper.source", entry["revision"]))
        return {"ok": True, "data": data, "resources": refs}

    def build(self, project, desc, args, context, control):
        rev = desc["project"]["revision"]
        if args["project"].get("revision") != rev:
            return failure("STALE_REVISION", "The Paper sources changed; describe the project again before building")
        formats = args["formats"]
        if not formats or len(set(formats)) != len(formats) or any(f not in ("pdf", "docx", "html") for f in formats):
            return failure("INVALID_FORMAT", "Choose at least one of pdf, docx or html")
        call_id = context["callId"]
        component = hashlib.sha256(call_id.encode()).hexdigest()
        out = contained(self.root, f".gewu/outputs/{component}")
        out.mkdir(parents=True, exist_ok=False)
        control.progress("Compiling Paper manuscript")
        try:
            rc, _, diagnostic = self.run_paper(["build", project, "--out-dir", out, "--output-stem", "paper",
                *["--output-" + fmt for fmt in formats], *(["--self-contained"] if "html" in formats else [])], control)
        except ProtocolError as error:
            return {"ok": False, "error": error.payload, "resources": []}
        expected = [out / ("paper." + fmt) for fmt in ["tex", *formats]]
        resources = [self.ref(path.resolve(), "paper.output") for path in expected if path.is_file() and path.stat().st_size]
        if rc or len(resources) != len(expected):
            return failure("BUILD_FAILED", diagnostic or "Paper did not produce every requested output", resources)
        try:
            after, _ = self.describe(project, control)
        except (ProtocolError, OSError, ValueError):
            return failure("SOURCE_CHANGED", "The output exists, but the source revision could not be reconfirmed", resources)
        if after["project"]["revision"] != rev:
            return failure("SOURCE_CHANGED", "Sources changed during the build", resources)
        result = {"ok": True, "data": {"formats": ["tex", *formats], "sourceRevision": rev}, "resources": resources}
        if diagnostic:
            result["warnings"] = [diagnostic]
        return result


if __name__ == "__main__":
    adapter = Adapter()
    serve(activate=adapter.activate, call=adapter.call, resolve=adapter.resolve)
