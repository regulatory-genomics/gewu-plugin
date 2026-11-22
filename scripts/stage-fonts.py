#!/usr/bin/env python3
"""Stage a pinned DejaVu font and a private fontconfig database."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import urllib.request

URL = ("https://conda.anaconda.org/conda-forge/noarch/"
       "font-ttf-dejavu-sans-mono-2.37-hab24e00_0.tar.bz2")
SHA256 = "58d7f40d2940dd0a8aa28651239adbf5613254df0f75789919c4e6762054403b"

CONF = """<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <dir prefix="xdg">fonts</dir>
  <cachedir prefix="xdg">fontconfig/gewu</cachedir>
  <match target="pattern">
    <test name="family"><string>sans-serif</string></test>
    <edit name="family" mode="assign" binding="strong"><string>DejaVu Sans</string></edit>
  </match>
  <match target="pattern">
    <test name="family"><string>serif</string></test>
    <edit name="family" mode="assign" binding="strong"><string>DejaVu Serif</string></edit>
  </match>
</fontconfig>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--license", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("--out must be new")
    with tempfile.TemporaryDirectory() as temp:
        archive = Path(temp) / "font.tar.bz2"
        with urllib.request.urlopen(URL, timeout=60) as response, archive.open("wb") as target:
            shutil.copyfileobj(response, target)
        with archive.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != SHA256:
                raise SystemExit("font package digest mismatch")
        with tarfile.open(archive, "r:bz2") as source:
            member = source.getmember("fonts/DejaVuSans.ttf")
            data = source.extractfile(member).read()
    args.out.mkdir(parents=True)
    (args.out / "DejaVuSans.ttf").write_bytes(data)
    shutil.copyfile(args.license, args.out / "LICENSE-DejaVu.txt")
    (args.out / "fonts.conf").write_text(CONF)


if __name__ == "__main__":
    main()
