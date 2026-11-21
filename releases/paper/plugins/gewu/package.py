#!/usr/bin/env python3
"""Stage the Paper adapter with the matching GeWu SDK."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--sdk-root", type=Path, required=True)
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()
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
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=here, text=True).strip()
    hashes = {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in (schema, sdk)}
    (stage / "compatibility.json").write_text(json.dumps({"apiVersion":"gewu.plugin/v1", "sourceHead":commit,
        "files":hashes,"requires":["Python 3.11+","Paper CLI with describe, inspect and plan-edit","Tectonic for PDF"]}, indent=2) + "\n")
    if out.exists() or out.is_symlink():
        raise FileExistsError(out)
    stage.rename(out)
