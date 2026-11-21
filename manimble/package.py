#!/usr/bin/env python3
"""Stage a Manimble GeWu adapter with a pinned copy of the host SDK."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def stage(sdk_root, out):
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
        version = (here.parents[1] / "VERSION").read_text().strip()
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
    args = parser.parse_args()
    stage(args.sdk_root, args.out)
