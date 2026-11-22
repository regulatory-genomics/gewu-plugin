#!/usr/bin/env python3
"""Stage an unpacked GeWu Browser package for local install or GUI tests."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat

FILES = ["adapter.py", "backend.py", "plugin.json", "tools.schema.json", "README.md"]
DIRS = ["bin", "lib", "skills", "web"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--python",
        help="local source install: bind this executable Python (venv supported)",
    )
    parser.add_argument(
        "--browsers",
        help="local source install: bind a non-default Playwright browser cache (requires --python)",
    )
    args = parser.parse_args()
    if args.browsers and not args.python:
        parser.error("--browsers requires --python")
    if args.python:
        args.python = os.path.abspath(args.python)
        if not Path(args.python).is_file() or not os.access(args.python, os.X_OK):
            parser.error("--python must point to an executable interpreter")
    source = Path(__file__).resolve().parents[1] / "plugin"
    out = args.out.resolve()
    if out.exists() or out.is_relative_to(source):
        parser.error("--out must be a new directory outside the plugin source")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.mkdir()
    for name in FILES:
        shutil.copy2(source / name, out / name)
    for name in DIRS:
        shutil.copytree(source / name, out / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    entry = out / "bin" / "browser-gewu"
    entry.chmod(entry.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if args.python:
        browsers = f"export PLAYWRIGHT_BROWSERS_PATH={shlex.quote(os.path.abspath(args.browsers))}\n" if args.browsers else ""
        entry.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            'here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
            f"{browsers}"
            f'exec {shlex.quote(args.python)} "$here/../adapter.py"\n',
            encoding="utf-8",
        )
        entry.chmod(0o755)
    manifest = json.loads((out / "plugin.json").read_text(encoding="utf-8"))
    if manifest.get("apiVersion") != "gewu.plugin/v1" or manifest.get("id") != "gewu-browser":
        parser.error("plugin.json is not the expected GeWu Browser manifest")
    staged = {
        str(path.relative_to(out)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(out.rglob("*"))
        if path.is_file()
    }
    (out / "staged.json").write_text(json.dumps(staged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
