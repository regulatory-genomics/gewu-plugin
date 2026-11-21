"""Invoke Paper's native CLI under GeWu's process ownership protocol."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
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

DEFAULT_TIMEOUT = 900
OUTPUT_KEEP = 10
PROGRESS_INTERVAL = 10
DIAGNOSTIC_LIMIT = 4000


def failure(code, message, resources=(), retryable=False):
    payload = ProtocolError(code, message, retryable=retryable).payload
    return {"ok": False, "error": payload, "resources": list(resources)}


def clip(text, limit=DIAGNOSTIC_LIMIT):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"


def with_log(message, log):
    return clip(f"{message}\nFull log: {log}" if log else message)


def summarize_stream(text, limit=DIAGNOSTIC_LIMIT):
    """Keep the useful lines of an engine log instead of an arbitrary tail."""
    lines = [line for line in (text or "").splitlines() if line.strip()]
    chosen = [line for line in lines
              if line.lstrip().startswith(("[ERROR]", "Error", "error:", "Warning", "WARNING", "warning:"))]
    if not chosen:
        chosen = lines[-20:]
    return clip("\n".join(chosen), limit) or "Paper produced no diagnostic output"


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


def plain_text(value):
    """Reduce Pandoc metadata to text; the full AST is for the editor, not an agent."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(plain_text(item) for item in value)
    if isinstance(value, dict):
        kind, content = value.get("t"), value.get("c")
        if kind == "Str":
            return content if isinstance(content, str) else ""
        if kind in ("Space", "SoftBreak", "LineBreak"):
            return " "
        if kind in ("MetaString",):
            return content if isinstance(content, str) else ""
        return plain_text(content)
    return str(value)


def author_names(metadata):
    node = metadata.get("author")
    if not isinstance(node, dict):
        return []
    items = node.get("c") if node.get("t") == "MetaList" else [node]
    names = []
    for item in items if isinstance(items, list) else [items]:
        if isinstance(item, dict) and item.get("t") == "MetaMap":
            fields = item.get("c") or {}
            text = plain_text(fields.get("name")) or plain_text(fields.get("family"))
        else:
            text = plain_text(item)
        text = " ".join(text.split())
        if text:
            names.append(text)
    return names


def section_count(project, sources):
    count = 0
    for name in sources:
        path = project / name
        if not path.is_file():
            continue
        with path.open(errors="replace") as stream:
            count += sum(1 for line in stream if re.match(r"^#{1,6}\s+", line))
    return count


def metadata_summary(project, metadata, sources):
    wide = " ".join(plain_text(metadata.get("abstract")).split())
    keywords = metadata.get("keywords")
    if isinstance(keywords, dict) and keywords.get("t") == "MetaList":
        words = [" ".join(plain_text(item).split()) for item in keywords.get("c") or []]
        keyword_text = ", ".join(word for word in words if word)
    else:
        keyword_text = " ".join(plain_text(keywords).split())
    return {
        "title": " ".join(plain_text(metadata.get("title")).split()),
        "authors": author_names(metadata),
        "abstract": wide[:600],
        "keywords": keyword_text,
        "sourceCount": len(sources),
        "sectionCount": section_count(project, sources),
    }


class Adapter:
    def activate(self, params):
        self.root = Path(params["workspaceRoot"]).resolve(strict=True)
        settings = params.get("settings") or {}
        if not isinstance(settings, dict):
            raise ProtocolError("INVALID_SETTINGS", "Paper settings must be an object")
        self.settings = settings
        self.child_env = os.environ.copy()

        prepend = settings.get("pathPrepend") or []
        if isinstance(prepend, str):
            prepend = [prepend]
        if not isinstance(prepend, list) or any(not isinstance(item, str) or not item for item in prepend):
            raise ProtocolError("INVALID_SETTINGS", "pathPrepend must be a list of directories")
        prepend = list(prepend)

        tectonic = settings.get("tectonic")
        if tectonic is not None:
            if not isinstance(tectonic, str) or not tectonic:
                raise ProtocolError("INVALID_SETTINGS", "tectonic must be a path")
            path = Path(tectonic).expanduser()
            if path.is_file() and os.access(path, os.X_OK):
                prepend.insert(0, str(path.parent))
            elif path.is_dir():
                prepend.insert(0, str(path))
            else:
                raise ProtocolError("INVALID_SETTINGS", "The configured tectonic path does not exist")
        if prepend:
            self.child_env["PATH"] = os.pathsep.join([*prepend, self.child_env.get("PATH", "")])

        timeout = settings.get("timeoutSeconds", DEFAULT_TIMEOUT)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not (1 <= timeout <= 86400):
            raise ProtocolError("INVALID_SETTINGS", "timeoutSeconds must be an integer between 1 and 86400")
        self.timeout = timeout

        runtime = os.environ.get("GEWU_RUNTIME_ROOT")
        self.runtime = bool(runtime)
        if runtime:
            self.executable = Path(runtime) / "bin" / "paper"
            if not self.executable.is_file() or not os.access(self.executable, os.X_OK):
                raise ProtocolError("RUNTIME_UNAVAILABLE", "The selected Paper runtime does not contain bin/paper")
        else:
            configured = settings.get("executable")
            if not configured:
                raise ProtocolError("RUNTIME_UNAVAILABLE", "The host did not provide a Paper runtime")
            self.executable = Path(configured).absolute()
            if not self.executable.is_file() or not os.access(self.executable, os.X_OK):
                raise ProtocolError("RUNTIME_UNAVAILABLE", "The configured Paper executable is not runnable")

        self.queues = {}
        self.queues_lock = threading.Lock()
        for command, expected in (("describe", b"--json"), ("build", b"--output-stem"),
                                  ("inspect", b"--json"), ("plan-edit", b"--json")):
            try:
                probe = subprocess.run([self.executable, command, "--help"], cwd=self.root, env=self.child_env,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=8, check=False)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ProtocolError("PAPER_UNAVAILABLE", "Cannot launch the configured Paper CLI") from error
            if probe.returncode or expected not in probe.stdout:
                raise ProtocolError("PAPER_VERSION_UNSUPPORTED", "Use Paper built with describe, inspect and plan-edit support")
        self.paper_version = self.read_version()
        self.tectonic = self.find_tectonic()
        tools = json.loads(Path(__file__).with_name("tools.schema.json").read_text())
        return {"tools": tools, "serviceEndpoints": []}

    def read_version(self, executable=None, flag="--help"):
        try:
            probe = subprocess.run([str(executable or self.executable), flag], cwd=self.root,
                                   env=self.child_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   timeout=8, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        text = probe.stdout.decode(errors="replace")
        match = re.search(r"\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.-]+)?", text)
        return match.group(0) if match else None

    def find_tectonic(self):
        configured = self.settings.get("tectonic")
        if configured:
            path = Path(configured).expanduser()
            if path.is_file():
                return str(path)
            if path.is_dir():
                return shutil.which("tectonic", path=str(path))
            return None
        return shutil.which("tectonic", path=self.child_env.get("PATH", ""))

    def diagnose(self):
        tectonic = self.find_tectonic()
        runs = {"paperVersion": self.paper_version or "unknown",
                "tectonic": {"path": tectonic, "version": None}}
        if tectonic:
            runs["tectonic"]["version"] = self.read_version(Path(tectonic), flag="--version") or "unknown"
        else:
            runs["tectonic"]["error"] = "tectonic was not found; PDF builds will fail"
        runs.update({
            "workspaceRoot": str(self.root),
            "executable": str(self.executable),
            "runtime": "selected" if self.runtime else "external",
            "settings": self.settings,
            "outputsRoot": "workspace:///.gewu/outputs",
            "logsRoot": "workspace:///.gewu/logs",
        })
        return runs

    def write_log(self, call_id, label, argv, stdout, stderr):
        try:
            logs = self.root / ".gewu" / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            safe_call = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(call_id))[:80] or "call"
            safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(label))[:40] or "paper"
            path = logs / f"{safe_call}-{safe_label}.log"
            body = (f"$ {self.executable} {' '.join(map(str, argv))}\n\n"
                    f"[stdout]\n{stdout.decode(errors='replace')}\n"
                    f"[stderr]\n{stderr.decode(errors='replace')}\n").encode()
            with tempfile.NamedTemporaryFile("wb", dir=logs, delete=False) as handle:
                handle.write(body)
                temp = Path(handle.name)
            os.replace(temp, path)
            return path.relative_to(self.root).as_posix()
        except OSError:
            return None

    def run_paper(self, argv, control, *, call_id=None, label="paper", keep_log=False):
        if control.cancelled.is_set():
            raise ProtocolError("CANCELLED", "Paper call stopped before the CLI started")
        gate, release = os.pipe()
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            try:
                child = subprocess.Popen([sys.executable, "-c",
                    "import os,sys; fd=int(sys.argv[1]); ready=os.read(fd,1); os.close(fd); "
                    "ready and os.execv(sys.argv[2],sys.argv[2:])", str(gate), str(self.executable), *map(str, argv)],
                    cwd=self.root, env=self.child_env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                    pass_fds=(gate,), process_group=0)
            except BaseException:
                os.close(release)
                raise
            finally:
                os.close(gate)
            registered = False
            started = time.monotonic()
            last_progress = started
            try:
                control.register_process(child.pid, child.pid)
                registered = True
                if not control.cancelled.is_set():
                    os.write(release, b"1")
                while child.poll() is None and not control.cancelled.wait(0.06):
                    now = time.monotonic()
                    if now - started > self.timeout:
                        stop_group(child)
                        stdout.seek(0)
                        stderr.seek(0)
                        log = self.write_log(call_id, label, argv, stdout.read(), stderr.read()) if call_id else None
                        raise ProtocolError("TIMEOUT",
                                            with_log(f"Paper {label} exceeded {self.timeout}s; the process was stopped", log),
                                            retryable=True)
                    if now - last_progress >= PROGRESS_INTERVAL:
                        control.progress(f"{label}: still running ({now - started:.0f}s)")
                        last_progress = now
                if child.returncode is None:
                    stop_group(child)
                    raise ProtocolError("CANCELLED", "Paper process group stopped")
                if kill_group(child.pid, 0):
                    stop_group(child)
                    raise ProtocolError("OUTCOME_UNKNOWN", "Paper exited before its child processes completed")
                stdout.seek(0)
                stderr.seek(0)
                out, err = stdout.read(), stderr.read()
                log = self.write_log(call_id, label, argv, out, err) if call_id and (keep_log or child.returncode) else None
                return child.returncode, out[:950_000], err.decode(errors="replace"), log
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

    def describe(self, project, control, call_id=None, label="describe"):
        if not (project / "metadata.yaml").is_file():
            raise ProtocolError("PROJECT_NOT_FOUND", "Project needs a metadata.yaml file")
        rc, body, error, log = self.run_paper(["describe", project, "--json"], control,
                                             call_id=call_id, label=label)
        if rc:
            raise ProtocolError("DESCRIBE_FAILED",
                                with_log(summarize_stream(error) or "Paper could not describe the project", log))
        try:
            desc = json.loads(body)
        except ValueError as failure_error:
            raise ProtocolError("DESCRIBE_FAILED", with_log("Paper returned unreadable describe data", log)) from failure_error
        if desc.get("version") != 1:
            raise ProtocolError("PAPER_VERSION_UNSUPPORTED", "Unrecognized Paper describe version")
        sources = desc.get("sources") or []
        supplement = desc.get("supplement") or []
        if not isinstance(sources, list) or not isinstance(supplement, list) or any(
                not isinstance(name, str) or not name for name in [*sources, *supplement]):
            raise ProtocolError("DESCRIBE_FAILED", "Paper returned an invalid source list")
        files = [contained(project, name) for name in ["metadata.yaml", *sources, *supplement]]
        refs = [self.ref(path, "paper.source") for path in files]
        rev = hashlib.sha256(json.dumps([(item["uri"], item["revision"]) for item in refs]).encode()).hexdigest()
        output_stem = desc.get("outputStem")
        if not isinstance(output_stem, str) or not output_stem:
            output_stem = "paper"
        metadata = desc.get("metadata")
        return {
            "version": 1,
            "project": self.ref(project, "paper.project", rev),
            "sources": sources,
            "supplement": supplement,
            "outputStem": output_stem,
            "metadata": metadata_summary(project, metadata if isinstance(metadata, dict) else {}, sources),
            "paperVersion": self.paper_version or "unknown",
        }, refs

    def resolve(self, ref):
        uri = ref["uri"]
        if not uri.startswith("workspace:///") or ref.get("type") not in ("paper.source", "paper.output"):
            raise ProtocolError("INVALID_RESOURCE", "Expected a Paper source or output file")
        path = contained(self.root, unquote(uri.removeprefix("workspace:///")))
        if not path.is_file():
            raise ProtocolError("FILE_NOT_FOUND", "Paper file does not exist")
        actual = self.ref(path, ref["type"])
        if ref.get("revision", actual["revision"]) != actual["revision"]:
            raise ProtocolError("STALE_REVISION",
                                f"Paper file changed; current revision is {actual['revision']}", retryable=True)
        return {"kind": "file", "path": str(path), "mimeType": actual["mimeType"], "revision": actual["revision"]}

    def call(self, tool, args, context, control):
        try:
            if tool == "diagnose":
                return {"ok": True, "data": self.diagnose(), "resources": []}
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
                    rc, _, error, log = self.run_paper(["init", project], control,
                                                      call_id=context["callId"], label="init")
                    if rc:
                        return failure("INIT_FAILED",
                                       with_log(summarize_stream(error) or "Paper could not initialize the project", log))
                desc, refs = self.describe(project, control, call_id=context["callId"])
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
            return failure(error.code, error.message, retryable=error.code in ("STALE_REVISION", "BUSY"))
        refs = [data["project"]]
        for entry in data.get("files", ()):
            if entry.get("revision"):
                refs.append(self.ref(project / entry["path"], "paper.source", entry["revision"]))
        return {"ok": True, "data": data, "resources": refs}

    def record_output(self, component, call_id, formats, revision):
        outputs = self.root / ".gewu" / "outputs"
        payload = {"callId": call_id, "directory": component, "formats": formats,
                   "sourceRevision": revision, "createdAt": time.time()}
        with tempfile.NamedTemporaryFile("w", dir=outputs, delete=False) as handle:
            json.dump(payload, handle)
            temp = Path(handle.name)
        pointer = outputs / "latest.json"
        os.replace(temp, pointer)
        directories = []
        for path in outputs.iterdir():
            if path.is_dir() and re.fullmatch(r"[0-9a-f]{64}", path.name):
                directories.append((path.stat().st_mtime, path))
        for _, path in sorted(directories, reverse=True)[OUTPUT_KEEP:]:
            shutil.rmtree(path, ignore_errors=True)
        return "workspace:///" + quote(pointer.relative_to(self.root).as_posix())

    @staticmethod
    def diagnostic_warnings(text):
        warnings = []
        for line in (text or "").splitlines():
            match = re.match(r"(?:\[(WARNING|ERROR)\]|(warning|error):)\s*(.+)", line.strip(), re.IGNORECASE)
            if match:
                severity = (match.group(1) or match.group(2)).lower()
                warnings.append({"severity": severity, "message": match.group(3).strip()[:500]})
        return warnings[:50]

    def build(self, project, desc, args, context, control):
        rev = desc["project"]["revision"]
        requested = args["project"].get("revision")
        if requested not in (None, "", "latest") and requested != rev:
            return failure("STALE_REVISION",
                           f"Paper sources are at revision {rev}; call describe again before building",
                           retryable=True)
        formats = args["formats"]
        if not formats or len(set(formats)) != len(formats) or any(f not in ("pdf", "docx", "html") for f in formats):
            return failure("INVALID_FORMAT", "Choose at least one of pdf, docx or html")
        if "pdf" in formats and not self.find_tectonic():
            return failure("MISSING_DEPENDENCY",
                           "PDF builds need Tectonic. Install it, add its directory to settings.pathPrepend, "
                           "or set settings.tectonic.")
        call_id = context["callId"]
        component = hashlib.sha256(call_id.encode()).hexdigest()
        out = contained(self.root, f".gewu/outputs/{component}")
        out.mkdir(parents=True, exist_ok=False)
        control.progress("Compiling Paper manuscript")
        try:
            rc, _, diagnostic, log = self.run_paper(["build", project, "--out-dir", out, "--output-stem", "paper",
                *["--output-" + fmt for fmt in formats], *(["--self-contained"] if "html" in formats else [])],
                control, call_id=call_id, label="build", keep_log=True)
        except ProtocolError as error:
            return {"ok": False, "error": error.payload, "resources": []}
        expected = [out / ("paper." + fmt) for fmt in ["tex", *formats]]
        resources = [self.ref(path.resolve(), "paper.output") for path in expected if path.is_file() and path.stat().st_size]
        if rc or len(resources) != len(expected):
            return failure("BUILD_FAILED",
                           with_log(summarize_stream(diagnostic) or "Paper did not produce every requested output", log),
                           resources, retryable=True)
        try:
            after, _ = self.describe(project, control, call_id=call_id, label="describe-final")
        except (ProtocolError, OSError, ValueError):
            return failure("SOURCE_CHANGED",
                           "The output exists, but the source revision could not be reconfirmed",
                           resources, retryable=True)
        after_rev = after["project"]["revision"]
        if after_rev != rev:
            return failure("SOURCE_CHANGED",
                           f"Sources changed during the build; current revision is {after_rev}",
                           resources, retryable=True)
        latest = self.record_output(component, call_id, formats, rev)
        warnings = self.diagnostic_warnings(diagnostic)
        data = {
            "formats": ["tex", *formats],
            "sourceRevision": rev,
            "callId": call_id,
            "latest": latest,
            "outputs": [{"format": ref["uri"].rsplit(".", 1)[-1], "uri": ref["uri"], "bytes": path.stat().st_size}
                        for ref, path in zip(resources, [p for p in expected if p.is_file() and p.stat().st_size])],
            "diagnostics": {"log": log, "warnings": warnings, "summary": summarize_stream(diagnostic)},
        }
        result = {"ok": True, "data": data, "resources": resources}
        if warnings:
            result["warnings"] = [f"{item['severity']}: {item['message']}" for item in warnings][:20]
        return result


if __name__ == "__main__":
    adapter = Adapter()
    serve(activate=adapter.activate, call=adapter.call, resolve=adapter.resolve)
