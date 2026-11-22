#!/usr/bin/env python3
"""Stage a verified Paper CLI alongside its standalone adapter interpreter."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
TARGETS = {"linux-x86_64": {"os": "linux", "arch": "x86_64", "libc": "glibc"},
           "macos-aarch64": {"os": "macos", "arch": "aarch64"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper", required=True, type=Path)
    parser.add_argument("--paper-sha256", required=True, help="SHA-256 from the pinned Paper build job")
    parser.add_argument("--standalone-python", required=True, type=Path)
    parser.add_argument("--pandoc-data", required=True, type=Path, help="Pandoc share/data tree used by the Paper CLI")
    parser.add_argument("--target", required=True, choices=TARGETS)
    parser.add_argument("--tectonic", type=Path, help="Portable renderer for the optional PDF profile")
    parser.add_argument("--tex-bundle", type=Path, help="Local pinned Tectonic directory or ZIP bundle")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if bool(args.tectonic) != bool(args.tex_bundle):
        parser.error("PDF profile needs both --tectonic and --tex-bundle")
    with args.paper.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != args.paper_sha256:
            parser.error("Paper binary hash does not match the build receipt")
    for verb in ("describe", "inspect", "plan-edit"):
        check = subprocess.check_output([args.paper.resolve(), verb, "--help"], stderr=subprocess.STDOUT)
        if b"--json" not in check:
            parser.error(f"Paper CLI does not implement {verb} --json")
    out = args.out.resolve()
    out.mkdir(parents=True)
    (out / "bin").mkdir()
    shutil.copy2(args.paper, out / "bin/paper")
    capabilities = ["edit", "html", "docx"]
    if args.tectonic:
        shutil.copy2(args.tectonic, out / "bin/tectonic-engine")
        if args.tex_bundle.is_dir():
            shutil.copytree(args.tex_bundle, out / "tex-bundle")
        else:
            shutil.copy2(args.tex_bundle, out / "tex-bundle")
        wrapper = out / "bin/tectonic"
        wrapper.write_text('#!/bin/sh\nset -eu\nroot=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\n'
                           'exec "$root/bin/tectonic-engine" --only-cached --bundle "$root/tex-bundle" "$@"\n')
        wrapper.chmod(0o755)
        capabilities.append("pdf")
    shutil.copytree(args.standalone_python, out / "python")
    shutil.copytree(args.pandoc_data, out / "pandoc-data")
    for name in ("launch", "probe"):
        shutil.copy2(HERE / name, out / "bin" / name)
        (out / "bin" / name).chmod(0o755)
    shutil.copy2(HERE / "smoke.py", out / "smoke.py")
    version = json.loads((HERE.parent / "plugin.json").read_text())["version"]
    (out / "runtime.json").write_text(json.dumps({"schemaVersion": "gewu.runtime/v2", "id": "paper-runtime",
        "plugin": "paper", "version": version, "target": TARGETS[args.target], "launcher": "bin/launch",
        "probe": "bin/probe", "installation": {"kind": "ready"}, "probeTimeoutSeconds": 120,
        "capabilities": capabilities}, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
