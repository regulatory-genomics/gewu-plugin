#!/usr/bin/env python3
"""Stage ELF-linked libraries for trusted build inputs on the target Linux baseline."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


GLIBC = {"libc.so.6", "libm.so.6", "libmvec.so.1", "libpthread.so.0", "libdl.so.2", "librt.so.1",
         "libresolv.so.2", "libutil.so.1", "libanl.so.1", "ld-linux-x86-64.so.2", "ld-linux-aarch64.so.1"}


def stage(executables, out, dlopen=()):
    # dlopen modules do not appear in the executable's ELF dependency table.
    libraries = {path.name: path.resolve(strict=True) for path in dlopen}
    for executable in [*executables, *dlopen]:
        result = subprocess.run(["ldd", str(executable)], check=False, capture_output=True, text=True)
        if result.returncode and "not a dynamic executable" not in result.stderr + result.stdout:
            raise RuntimeError(f"ldd failed for {executable}: {result.stderr.strip()}")
        for line in result.stdout.splitlines():
            if "=> not found" in line:
                raise RuntimeError(f"missing build input for {executable}: {line.strip()}")
            match = re.fullmatch(r"\s*(\S+) => (/.+) \(0x[0-9a-f]+\)\s*", line)
            if match and match[1] not in GLIBC:
                path = Path(match[2]).resolve(strict=True)
                # Libraries already carried beside the executable (for example Vulkan SwiftShader) stay there.
                if executable not in dlopen and path.is_relative_to(executable.resolve().parent):
                    continue
                if libraries.setdefault(match[1], path) != path:
                    raise RuntimeError(f"multiple sources for {match[1]}")
    out.mkdir(parents=True, exist_ok=False)
    entries = []
    for name, source in sorted(libraries.items()):
        shutil.copy2(source, out / name)
        with source.open("rb") as stream:
            entries.append({"file": name, "sha256": hashlib.file_digest(stream, "sha256").hexdigest(),
                            "sourceFile": source.name})
    (out / "inputs.json").write_text(json.dumps(entries, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--dlopen", action="append", default=[], type=Path,
                        help="Matching dynamically loaded module; include it and its ELF dependencies")
    parser.add_argument("executable", type=Path, nargs="+")
    args = parser.parse_args()
    stage(args.executable, args.out, args.dlopen)
