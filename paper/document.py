"""Revision-checked document editing for the Paper plugin.

The native CLI owns the source map and validates every replacement; this module
owns the file transaction: expected revision, atomic write, and undo history.
Both the Preview and the agent go through these functions, so there is one edit
path with one conflict rule.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

HISTORY_LIMIT = 100
LOCK_TIMEOUT = 10.0


class DocumentError(Exception):
    """An edit that must not be written, with a stable code for the caller."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def file_revision(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def project_revision(root: Path, paths: list[str], uri: str) -> str:
    """The same revision `describe` reports, so a build can pin an edit.

    `describe` builds its resource URIs from the workspace root, so the project
    URI prefix must match exactly or a pinned build revision never agrees.
    """
    base = uri[:-1] if uri.endswith("/") else uri
    pairs = [(base + "/" + quote(name), file_revision(root / name)) for name in paths]
    return hashlib.sha256(json.dumps(pairs).encode()).hexdigest()


def run_inspect(executable: Path, root: Path) -> dict:
    result = subprocess.run(
        [str(executable), "inspect", str(root), "--json"],
        cwd=root, capture_output=True, timeout=120, check=False,
    )
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip()[-800:]
        raise DocumentError("INSPECT_FAILED", detail or "Paper could not inspect this project")
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise DocumentError("INSPECT_FAILED", "Paper returned unreadable inspection data") from error


def run_plan(executable: Path, root: Path, body: dict) -> dict:
    result = subprocess.run(
        [str(executable), "plan-edit", str(root), "--json"],
        cwd=root, input=json.dumps(body).encode(), capture_output=True, timeout=120, check=False,
    )
    try:
        answer = json.loads(result.stdout)
    except ValueError as error:
        detail = result.stderr.decode(errors="replace").strip()[-800:]
        raise DocumentError("EDIT_FAILED", detail or "Paper returned no edit plan") from error
    if not answer.get("ok"):
        problem = answer.get("error") or {}
        raise DocumentError(problem.get("code") or "EDIT_FAILED", problem.get("message") or "Paper rejected the edit")
    return answer


class ProjectLock:
    """Serialize adapters editing the same project.

    External editors do not honour this lock, which is why every write still
    checks the expected revision immediately before replacing the file.
    """

    def __init__(self, root: Path):
        self.path = root / ".gewu" / "paper.lock"

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+")
        deadline = time.monotonic() + LOCK_TIMEOUT
        while True:
            try:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() > deadline:
                    self.handle.close()
                    raise DocumentError("BUSY", "The Paper project is being edited by another call")
                time.sleep(0.05)

    def __exit__(self, *_):
        import fcntl
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


def history_path(root: Path) -> Path:
    return root / ".gewu" / "paper" / "history.json"


def read_history(root: Path) -> dict:
    path = history_path(root)
    if not path.is_file():
        return {"entries": [], "cursor": 0}
    try:
        data = json.loads(path.read_text())
    except ValueError:
        return {"entries": [], "cursor": 0}
    data.setdefault("entries", [])
    data.setdefault("cursor", len(data["entries"]))
    return data


def write_history(root: Path, history: dict) -> None:
    path = history_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = history["entries"][-HISTORY_LIMIT:]
    history = {"entries": entries, "cursor": min(history["cursor"], len(entries))}
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as handle:
        json.dump(history, handle)
        temp = Path(handle.name)
    os.replace(temp, path)


def encode(data: bytes) -> str:
    return base64.b64encode(data).decode()


def decode(text: str) -> bytes:
    return base64.b64decode(text.encode())


def snapshot(executable: Path, root: Path, uri: str) -> dict:
    """Inspection plus the revisions an edit must quote."""
    inspection = run_inspect(executable, root)
    names = ["metadata.yaml", *inspection["sources"], *inspection["supplement"]]
    for entry in inspection["files"]:
        path = root / entry["path"]
        entry["revision"] = file_revision(path) if path.is_file() else None
    inspection["project"] = inspection_project(uri, root, names)
    return inspection


def check_target(root: Path, inspection: dict, path: str, start: int, end: int) -> dict:
    for entry in inspection["files"]:
        if entry["path"] != path:
            continue
        if not entry.get("mapped"):
            raise DocumentError("NOT_EDITABLE", entry.get("reason") or "This file cannot be edited in place")
        for block in entry["blocks"]:
            if block["byteStart"] == start and block["byteEnd"] == end:
                if not block["editable"]:
                    raise DocumentError("NOT_EDITABLE", "This block is preserved as-is")
                return block
        raise DocumentError("STALE_BLOCK", "The block changed; refresh the document before editing")
    raise DocumentError("INVALID_PATH", "The edit must target a manuscript source file")


def atomic_write(path: Path, data: bytes) -> None:
    mode = path.stat().st_mode if path.exists() else None
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        handle.write(data)
        temp = Path(handle.name)
    if mode is not None:
        os.chmod(temp, mode)
    os.replace(temp, path)


def apply_edit(executable: Path, root: Path, uri: str, request: dict) -> dict:
    """Validate and write one block replacement.

    `request` carries the block range, the client's file revision and the new
    text. The native plan is produced before the file is touched, and the
    revision is re-checked inside the lock.
    """
    path_name = request.get("file")
    start = request.get("byteStart")
    end = request.get("byteEnd")
    text = request.get("text")
    expected = request.get("fileRevision")
    if not isinstance(path_name, str) or not isinstance(text, str):
        raise DocumentError("INVALID_ARGUMENT", "An edit needs a file and text")
    if not isinstance(start, int) or not isinstance(end, int) or end < start:
        raise DocumentError("INVALID_ARGUMENT", "An edit needs a byte range")
    with ProjectLock(root):
        inspection = run_inspect(executable, root)
        block = check_target(root, inspection, path_name, start, end)
        target = (root / path_name).resolve()
        if not target.is_relative_to(root):
            raise DocumentError("INVALID_PATH", "The edit must stay inside the project")
        if expected and file_revision(target) != expected:
            current = file_revision(target)
            raise DocumentError("STALE_REVISION",
                                f"The file changed (current revision {current}); refresh before saving")
        answer = run_plan(executable, root, {"path": path_name, "byteStart": start, "byteEnd": end, "text": text})
        before = target.read_bytes()
        after = answer["text"].encode()
        atomic_write(target, after)
        history = read_history(root)
        # A new edit after an undo discards the redo branch.
        history["entries"] = history["entries"][:history["cursor"]]
        history["entries"].append({
            "path": path_name, "before": encode(before), "after": encode(after),
        })
        history["cursor"] = len(history["entries"])
        write_history(root, history)
        refreshed = snapshot(executable, root, uri)
        return {
            "project": refreshed["project"],
            "files": refreshed["files"],
            "edit": {"file": path_name, "index": block["index"], "kind": block["kind"]},
            "canUndo": history["cursor"] > 0,
            "canRedo": False,
        }


def step_history(executable: Path, root: Path, uri: str, expected: str, direction: str) -> dict:
    """Move one step in the undo history, refusing to overwrite other changes."""
    with ProjectLock(root):
        inspection = run_inspect(executable, root)
        names = ["metadata.yaml", *inspection["sources"], *inspection["supplement"]]
        current = project_revision(root, names, uri)
        if expected and expected != current:
            raise DocumentError("STALE_REVISION",
                                f"The project is at revision {current}; refresh before {direction}ing")
        history = read_history(root)
        entries = history["entries"]
        cursor = history["cursor"]
        if direction == "undo":
            if cursor == 0:
                return {"project": inspection_project(uri, root, names), "files": inspection["files"], "canUndo": False, "canRedo": bool(entries)}
            entry = entries[cursor - 1]
            target = (root / entry["path"]).resolve()
            if not target.is_relative_to(root):
                raise DocumentError("INVALID_PATH", "History points outside the project")
            if target.read_bytes() != decode(entry["after"]):
                raise DocumentError("STALE_REVISION", "The file changed since that edit; refresh before undoing")
            atomic_write(target, decode(entry["before"]))
            cursor -= 1
        else:
            if cursor >= len(entries):
                return {"project": inspection_project(uri, root, names), "files": inspection["files"], "canUndo": bool(entries), "canRedo": False}
            entry = entries[cursor]
            target = (root / entry["path"]).resolve()
            if not target.is_relative_to(root):
                raise DocumentError("INVALID_PATH", "History points outside the project")
            if target.read_bytes() != decode(entry["before"]):
                raise DocumentError("STALE_REVISION", "The file changed since that edit; refresh before redoing")
            atomic_write(target, decode(entry["after"]))
            cursor += 1
        history["cursor"] = cursor
        write_history(root, history)
        refreshed = snapshot(executable, root, uri)
        return {
            "project": refreshed["project"],
            "files": refreshed["files"],
            "canUndo": cursor > 0,
            "canRedo": cursor < len(entries),
        }


def inspection_project(uri: str, root: Path, names: list[str]) -> dict:
    return {"uri": uri, "type": "paper.project", "revision": project_revision(root, names, uri)}
