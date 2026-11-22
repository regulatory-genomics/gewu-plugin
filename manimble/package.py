#!/usr/bin/env python3
"""Stage a Manimble GeWu adapter with a pinned copy of the host SDK."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile


def stage(sdk_root, out, python=None, engine_root=None):
    here = Path(__file__).resolve().parent
    host = sdk_root.resolve().parents[1]
    schema = host / "gewu-protocol/schema/plugin.v1.schema.json"
    sdk = sdk_root / "gewu_plugin.py"
    hashes = {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in (schema, sdk)}
    out = out.absolute()
    if out.exists() or out.is_symlink() or out.resolve().is_relative_to(here):
        raise ValueError("choose a new staging directory outside the adapter source tree")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent) as temp:
        stage = Path(temp) / "package"
        stage.mkdir()
        for name in ("adapter.py", "plugin.json", "tools.schema.json", "README.md"):
            shutil.copy2(here / name, stage / name)
        for name in ("bin", "skills", "web"):
            shutil.copytree(here / name, stage / name)
        (stage / "lib").mkdir()
        shutil.copy2(sdk, stage / "lib/gewu_plugin.py")
        engine = (engine_root or here / 'engine').resolve()
        version_file = engine / 'VERSION'
        version = version_file.read_text().strip() if version_file.is_file() else json.loads((here / 'plugin.json').read_text())['version']
        if python:
            python = os.path.abspath(python)
            if not Path(python).is_file() or not os.access(python, os.X_OK):
                raise ValueError('--python must be an executable interpreter')
            if not (engine / 'manimble/rpc.py').is_file():
                raise ValueError('--engine-root must contain manimble/rpc.py')
            shutil.copytree(engine, stage / 'engine', ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.git'))
            runtime = stage / '.local-runtime/bin'
            runtime.mkdir(parents=True)
            (runtime / 'python3').write_text(f'#!/bin/sh\nexec {shlex.quote(python)} "$@"\n')
            (runtime / 'python3').chmod(0o755)
            launcher = stage / 'bin/manimble-gewu'
            launcher.write_text('#!/bin/sh\nset -eu\ndir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\n'
                'export GEWU_PLUGIN_ROOT="$dir" GEWU_RUNTIME_ROOT="$dir/.local-runtime" PYTHONPATH="$dir/lib"\n'
                f'exec {shlex.quote(python)} "$dir/adapter.py"\n')
            launcher.chmod(0o755)
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=here, text=True).strip()
        (stage / "compatibility.json").write_text(json.dumps({"engineVersion": version, "sourceHead": commit,
            "apiVersion": "gewu.plugin/v1", "files": hashes, "requires": ["Python 3.11+", "Manimble engine"]}, indent=2) + "\n")
        if out.exists() or out.is_symlink():
            raise FileExistsError(out)
        stage.rename(out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument('--python', help='Bind an executable Python for a local source install')
    parser.add_argument('--engine-root', type=Path, help='Pinned engine containing manimble/rpc.py')
    args = parser.parse_args()
    stage(args.sdk_root, args.out, args.python, args.engine_root)
