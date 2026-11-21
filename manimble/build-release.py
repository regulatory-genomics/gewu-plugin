#!/usr/bin/env python3
"""Assemble Manimble's GeWu package and packed runtime from a prepared Python env."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
PLUGIN = "manimble"
VERSION = json.loads((HERE / "plugin.json").read_text())["version"]

SKIP_DIRS = {"__pycache__", "conda-meta", "pkgs", ".git"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_tree(source: Path, destination: Path, *, skip_conda: bool) -> None:
    root = source.resolve()
    for path in sorted(source.rglob("*")):
        relative = PurePosixPath(path.relative_to(source).as_posix())
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if path.suffix in SKIP_SUFFIXES:
            continue
        if path.is_symlink():
            # conda installs `python` as a relative symlink; dereference links
            # that stay inside the environment and reject anything external.
            resolved = path.resolve()
            if not resolved.is_relative_to(root) or not resolved.exists():
                raise SystemExit(f"runtime input contains an external symlink: {path}")
            path = resolved
        target = destination / Path(*relative.parts)
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python-env", type=Path, required=True, help="conda/venv prefix with manim installed")
    parser.add_argument("--engine", type=Path, required=True, help="directory containing manimble/rpc.py")
    parser.add_argument("--gewu-scripts", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()
    env = args.python_env.resolve()
    engine = args.engine.resolve()
    out = args.out.resolve()
    if not (env / "bin/python").exists():
        parser.error(f"no bin/python in {env}")
    if not (engine / "manimble/rpc.py").is_file():
        parser.error(f"no manimble/rpc.py under {engine}")
    if out.exists():
        parser.error(f"output already exists: {out}")
    out.mkdir(parents=True)
    with tempfile.TemporaryDirectory(dir=out) as temp:
        stage = Path(temp)
        package = stage / "package"
        runtime = stage / "runtime"
        runtime.mkdir(parents=True)
        copy_tree(env, runtime, skip_conda=True)
        copy_tree(engine / "manimble", runtime / "manimble", skip_conda=False)
        (runtime / "runtime.json").write_text(json.dumps({
            "schemaVersion": "gewu.runtime/v1",
            "id": "manimble-runtime",
            "version": VERSION,
            "plugin": PLUGIN,
            "target": {"os": "linux", "arch": "x86_64", "libc": "glibc"},
            "launcher": "bin/launch",
            "probe": "bin/probe",
            "capabilities": ["deck", "render", "export"],
        }, sort_keys=True, indent=2) + "\n")
        (runtime / "bin/launch").write_text(
            "#!/bin/sh\nset -eu\n"
            "dir=$(CDPATH='' cd -- \"$(dirname -- \"$0\")/..\" && pwd)\n"
            "export PATH=\"$dir/bin:$PATH\"\n"
            "export LD_LIBRARY_PATH=\"$dir/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}\"\n"
            "export PYTHONPATH=\"$dir${PYTHONPATH:+:$PYTHONPATH}\"\n"
            "exec \"$dir/bin/python\" \"$@\"\n"
        )
        (runtime / "bin/probe").write_text(
            "#!/bin/sh\nset -eu\n"
            "dir=$(CDPATH='' cd -- \"$(dirname -- \"$0\")/..\" && pwd)\n"
            "export PATH=\"$dir/bin:$PATH\"\n"
            "export LD_LIBRARY_PATH=\"$dir/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}\"\n"
            "export PYTHONPATH=\"$dir${PYTHONPATH:+:$PYTHONPATH}\"\n"
            "test -f \"$dir/manimble/rpc.py\"\n"
            "\"$dir/bin/python\" - <<'PY'\n"
            "import manim, manim_slides, PIL, pptx, manimble\n"
            "print(manim.__version__)\n"
            "PY\n"
        )
        (runtime / "bin/launch").chmod(0o755)
        (runtime / "bin/probe").chmod(0o755)
        subprocess.run([
            sys.executable, str(args.gewu_scripts / "plugin-release.py"),
            "--descriptor", str(HERE / "release.json"),
            "--source-root", str(HERE),
            "--sdk-root", str(Path(args.gewu_scripts).parent / "sdk/python"),
            "--out", str(out / "package"),
            "--add-dir", f"engine={engine}",
            *(["--allow-dirty"] if args.allow_dirty else []),
        ], check=True)
        subprocess.run([
            sys.executable, str(args.gewu_scripts / "plugin-runtime.py"),
            "--root", str(runtime),
            "--out", str(out / "runtime"),
        ], check=True)
        package_meta = json.loads((out / "package" / f"{PLUGIN}-{VERSION}.package.json").read_text())
        runtime_meta = json.loads((out / "runtime" / f"{PLUGIN}-{VERSION}.runtime.json").read_text())
        record = {
            "plugin": PLUGIN,
            "version": VERSION,
            "package": package_meta["package"],
            "runtime": runtime_meta["runtime"],
            "manifest": runtime_meta["manifest"],
        }
        (out / "release-record.json").write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
    print(json.dumps(record, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
