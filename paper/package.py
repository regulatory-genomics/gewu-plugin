#!/usr/bin/env python3
"""Stage the Paper adapter with the matching GeWu SDK."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--sdk-root", type=Path, required=True)
parser.add_argument("--out", type=Path, required=True)
parser.add_argument('--python', help='Bind an executable Python for a local source install')
parser.add_argument("--runtime-meta", type=Path)
parser.add_argument("--runtime-base-url")
parser.add_argument("--runtime-plan-out", type=Path)
args = parser.parse_args()
if args.python:
    args.python = os.path.abspath(args.python)
    if not Path(args.python).is_file() or not os.access(args.python, os.X_OK):
        parser.error('--python must be an executable interpreter')
if any((args.runtime_meta, args.runtime_base_url, args.runtime_plan_out)) and not all(
    (args.runtime_meta, args.runtime_base_url, args.runtime_plan_out)
):
    parser.error("--runtime-meta, --runtime-base-url, and --runtime-plan-out must be used together")
here = Path(__file__).resolve().parent
out = args.out.absolute()
if out.exists() or out.is_symlink() or out.resolve().is_relative_to(here):
    parser.error("--out must be a new staging directory")
out.parent.mkdir(parents=True, exist_ok=True)
schema = args.sdk_root.resolve().parents[1] / "gewu-protocol/schema/plugin.v1.schema.json"
sdk = args.sdk_root / "gewu_plugin.py"
with tempfile.TemporaryDirectory(dir=out.parent) as temp:
    stage = Path(temp) / "package"
    (stage / "lib").mkdir(parents=True)
    for name in ("adapter.py", "document.py", "plugin.json", "tools.schema.json", "README.md"):
        shutil.copy2(here / name, stage / name)
    for name in ("bin", "skills", "web"):
        shutil.copytree(here / name, stage / name)
    shutil.copy2(sdk, stage / "lib/gewu_plugin.py")
    if args.python:
        launcher = stage / 'bin/paper-gewu'
        launcher.write_text('#!/bin/sh\nset -eu\ndir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\n'
            'export PYTHONPATH="$dir/lib"\n'
            f'exec {shlex.quote(args.python)} "$dir/adapter.py"\n')
        launcher.chmod(0o755)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=here, text=True).strip()
    hashes = {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in (schema, sdk)}
    (stage / "compatibility.json").write_text(json.dumps({"apiVersion":"gewu.plugin/v1", "sourceHead":commit,
        "files":hashes,"requires":["Python 3.11+","Paper CLI with describe, inspect and plan-edit","Tectonic for PDF builds"]}, indent=2) + "\n")
    if out.exists() or out.is_symlink():
        raise FileExistsError(out)
    stage.rename(out)

if args.runtime_meta:
    subprocess.run([
        sys.executable,
        str(here / "make-runtime-plan.py"),
        "--runtime-meta", str(args.runtime_meta),
        "--base-url", args.runtime_base_url,
        "--out", str(args.runtime_plan_out),
    ], check=True)
