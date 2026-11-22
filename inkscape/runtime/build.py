#!/usr/bin/env python3
"""Stage the Linux Web/editor runtime from a verified AppImage and standalone Python."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request

HERE = Path(__file__).resolve().parent


def exe(path, text):
    path.write_text("#!/bin/sh\nset -eu\n" + text)
    path.chmod(0o755)


def verify(path, expected):
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
            raise ValueError(f"component digest mismatch: {path.name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--appimage", required=True, type=Path)
    parser.add_argument("--standalone-python", required=True, type=Path, help="Extracted python-build-standalone install tree")
    parser.add_argument("--uv", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    components = json.loads((HERE / "components.lock.json").read_text())
    verify(args.appimage, components["appImage"]["sha256"])
    out = args.out.resolve()
    out.mkdir(parents=True)
    (out / "bin").mkdir()
    with tempfile.TemporaryDirectory() as stage:
        app = Path(stage) / "inkscape.AppImage"
        shutil.copyfile(args.appimage, app)
        app.chmod(0o755)
        subprocess.run([app, "--appimage-extract"], cwd=stage, check=True, stdout=subprocess.DEVNULL)
        shutil.copytree(Path(stage) / "squashfs-root", out / "inkscape", ignore_dangling_symlinks=True)
    shutil.copytree(args.standalone_python, out / "python")
    subprocess.run([str(args.uv.resolve()), "pip", "install", "--python", str(out / "python/bin/python3"),
                    "--only-binary", ":all:", "--require-hashes", "--target", str(out / "site-packages"),
                    "-r", str(HERE.parent / "requirements-web.lock")], check=True)
    font = components["font"]
    for url, digest, name in [(font["url"], font["sha256"], "fonts/NotoSansCJKsc-Regular.otf"),
                              (font["licenseUrl"], font["licenseSha256"], "licenses/NotoSansCJK.txt")]:
        path = out / name
        path.parent.mkdir(exist_ok=True)
        with urllib.request.urlopen(url, timeout=45) as src, path.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        verify(path, digest)
    fonts = out / "inkscape/etc/fonts/fonts.conf"
    fonts.write_text(fonts.read_text().replace("<fontconfig>", '<fontconfig>\n  <dir prefix="relative">../../../fonts</dir>', 1))
    exe(out / "bin/inkscape", 'unset DISPLAY WAYLAND_DISPLAY\n'
        'root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\nexec "$root/inkscape/AppRun" "$@"\n')
    exe(out / "bin/launch", 'export GEWU_INKSCAPE_HEADLESS=1 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1\n'
        'export PYTHONPATH="$GEWU_PLUGIN_ROOT/lib:$GEWU_RUNTIME_ROOT/site-packages"\n'
        'exec "$GEWU_RUNTIME_ROOT/python/bin/python3" "$GEWU_PLUGIN_ROOT/adapter.py"\n')
    exe(out / "bin/python3", 'root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\n'
        'export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1\n'
        'export PYTHONPATH="${GEWU_PLUGIN_ROOT:+$GEWU_PLUGIN_ROOT/lib:}$root/site-packages${PYTHONPATH:+:$PYTHONPATH}"\n'
        'export FONTCONFIG_FILE="$root/inkscape/etc/fonts/fonts.conf"\n'
        'exec "$root/python/bin/python3" "$@"\n')
    exe(out / "bin/python", (out / "bin/python3").read_text())
    exe(out / "bin/probe", 'export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1\n'
        'export PYTHONPATH="$GEWU_PLUGIN_ROOT:$GEWU_PLUGIN_ROOT/lib:$GEWU_RUNTIME_ROOT/site-packages"\n'
        'exec "$GEWU_RUNTIME_ROOT/python/bin/python3" "$GEWU_RUNTIME_ROOT/smoke.py"\n')
    shutil.copyfile(HERE / "smoke.py", out / "smoke.py")
    version = json.loads((HERE.parent / "plugins/gewu/plugin.json").read_text())["version"]
    (out / "runtime.json").write_text(json.dumps({
        "schemaVersion": "gewu.runtime/v2", "plugin": "inkscape", "id": "inkscape-headless",
        "version": version, "target": {"os": "linux", "arch": "x86_64", "libc": "glibc"},
        "launcher": "bin/launch", "probe": "bin/probe", "installation": {"kind": "ready"},
        "probeTimeoutSeconds": 90, "capabilities": ["figure-layout", "png", "pdf"],
    }, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
