#!/usr/bin/env python3
"""Build Manimble from locked binary artifacts; never copy a developer prefix."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
TOOL_DIGESTS = {
    "gewu-linux": (
        "d058b725d1e9e13f4762c6a45484668703c8feaa2317e5431ab952a9cd49fc60",
        "70bddd77078ac2fe320075f8a03e7731e67e0583f732034ff2d7004b0a0b817c",
    ),
    "gewu-macos": (
        "4d7c3a4533d1354c68787685d6b2b9b24c3a72140920ccaad1f4bb48bf004e47",
        "f127bd8a2dd9fbc27ea8c45512fe206167ecab4445e57759881879979bb8c928",
    ),
}
TARGETS = {
    "gewu-linux": {"os": "linux", "arch": "x86_64", "libc": "glibc"},
    "gewu-macos": {"os": "macos", "arch": "aarch64"},
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pixi-pack", type=Path, required=True)
    parser.add_argument("--pixi-unpack", type=Path, required=True)
    parser.add_argument("--target", choices=TARGETS, required=True)
    parser.add_argument("--gewu-scripts", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tex-root", type=Path, help="Verified relocatable TeX environment for the optional profile")
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()
    for path, digest in zip((args.pixi_pack, args.pixi_unpack), TOOL_DIGESTS[args.target], strict=True):
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                parser.error(f"unexpected SHA-256 for pixi-pack v0.7.12 tool: {path}")
    descriptor = json.loads((HERE / "release.json").read_text())
    scripts = args.gewu_scripts.resolve()
    out = args.out.resolve()
    if out.exists():
        parser.error(f"output already exists: {out}")
    with tempfile.TemporaryDirectory(prefix="manimble-release-") as stage:
        runtime = Path(stage) / "runtime"
        shutil.copytree(HERE / "runtime", runtime, ignore=shutil.ignore_patterns("__pycache__"))
        (runtime / "payload").mkdir()
        (runtime / "tools").mkdir()
        helper = runtime / "tools/pixi-unpack"
        shutil.copyfile(args.pixi_unpack, helper)
        for path in (helper, runtime / "bin/launch", runtime / "bin/probe"):
            path.chmod(0o755)
        subprocess.run([
            str(args.pixi_pack.resolve()), "--platform", args.target, "--output-file",
            str(runtime / "payload/environment.tar"), str(HERE / "pixi.toml"),
        ], check=True)
        shutil.copyfile(HERE / "pixi.lock", runtime / "pixi.lock")
        # pixi-unpack constructs a TLS registry client even for offline unpack;
        # a clean host may have no system trust store.
        (runtime / "certs").mkdir()
        shutil.copyfile(HERE.parent / "assets/ca-bundle.crt", runtime / "certs/ca-bundle.crt")
        capabilities = ["deck", "render", "export"]
        if args.tex_root:
            for executable in ("latex", "dvisvgm"):
                if not (args.tex_root / "bin" / executable).is_file():
                    parser.error(f"TeX profile is missing bin/{executable}")
            shutil.copytree(args.tex_root, runtime / "tex")
            capabilities.append("tex")
        (runtime / "runtime.json").write_text(json.dumps({
            "schemaVersion": "gewu.runtime/v2", "id": "manimble-runtime",
            "plugin": "manimble", "version": descriptor["version"], "target": TARGETS[args.target],
            "launcher": "bin/launch", "probe": "bin/probe", "probeTimeoutSeconds": 240,
            "capabilities": capabilities,
            "installation": {"kind": "pixi-pack", "archive": "payload/environment.tar",
                             "unpacker": "tools/pixi-unpack", "environmentName": "env"},
        }, sort_keys=True) + "\n")
        subprocess.run([
            sys.executable, str(scripts / "plugin-release.py"), "--descriptor", str(HERE / "release.json"),
            "--source-root", str(HERE), "--sdk-root", str(scripts.parent / "sdk/python"),
            "--out", str(out / "package"), "--add-dir", f"engine={HERE / 'engine'}",
            *(["--allow-dirty"] if args.allow_dirty else []),
        ], check=True)
        subprocess.run([
            sys.executable, str(scripts / "plugin-runtime.py"), "--root", str(runtime),
            "--out", str(out / "runtime"), "--profile", "tex" if args.tex_root else "core",
        ], check=True)


if __name__ == "__main__":
    main()
