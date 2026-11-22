#!/usr/bin/env python3
"""Split a target-built HyperFrames Studio tree into package and ready runtime."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
TARGETS = {"linux-x86_64": {"os": "linux", "arch": "x86_64", "libc": "glibc"},
           "macos-aarch64": {"os": "macos", "arch": "aarch64"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("stage", "node", "chrome-dir", "ffmpeg", "ffprobe", "gewu-scripts", "out"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--target", choices=TARGETS, required=True)
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()
    out = args.out.resolve()
    script = args.gewu_scripts.resolve()
    stage = args.stage.resolve()
    desc = json.loads((stage / "release.json").read_text())
    desc["packageDirs"].remove("engine")
    desc["buildInputs"] = [x for x in desc["buildInputs"] if not x.endswith("(host)")]
    if out.exists():
        parser.error("--out must be new")
    with tempfile.TemporaryDirectory(prefix="hyperframes-runtime-") as directory:
        temp = Path(directory)
        runtime = temp / "runtime"
        shutil.copytree(HERE, runtime, ignore=shutil.ignore_patterns("build.py", "__pycache__"))
        shutil.copytree(stage / "engine", runtime / "engine")
        shutil.copytree(args.chrome_dir, runtime / "chrome")
        for name, source in (("node", args.node), ("ffmpeg", args.ffmpeg), ("ffprobe", args.ffprobe)):
            shutil.copy2(source, runtime / "bin" / name)
            (runtime / "bin" / name).chmod(0o755)
        for name in ("probe", "launch"):
            (runtime / "bin" / name).chmod(0o755)
        if args.target == "linux-x86_64":
            bundler = HERE.parents[1] / "scripts/stage-linux-libraries.py"
            subprocess.run([sys.executable, str(bundler), "--out", str(runtime / "linux-libs"),
                            str(runtime / "bin/node"), str(runtime / "chrome/chrome-headless-shell"),
                            str(runtime / "bin/ffmpeg"), str(runtime / "bin/ffprobe")], check=True)
        subprocess.run([sys.executable, str(HERE.parents[1] / "scripts/stage-fonts.py"),
                        "--out", str(runtime / "fonts"),
                        "--license", str(HERE.parents[1] / "assets/DejaVu-LICENSE.txt")], check=True)
        (runtime / "runtime.json").write_text(json.dumps({"schemaVersion": "gewu.runtime/v2",
            "plugin": "hyperframes", "id": "hyperframes-runtime", "version": desc["version"],
            "target": TARGETS[args.target], "launcher": "bin/launch", "probe": "bin/probe",
            "probeTimeoutSeconds": 240, "installation": {"kind": "ready"},
            "capabilities": ["studio", "snapshot", "render"]}, sort_keys=True) + "\n")
        (temp / "release.json").write_text(json.dumps(desc))
        subprocess.run([sys.executable, str(script / "plugin-release.py"), "--descriptor", str(temp / "release.json"),
                        "--source-root", str(stage), "--out", str(out / "package"),
                        *(["--allow-dirty"] if args.allow_dirty else [])], check=True)
        subprocess.run([sys.executable, str(script / "plugin-runtime.py"), "--root", str(runtime),
                        "--out", str(out / "runtime")], check=True)


if __name__ == "__main__":
    main()
