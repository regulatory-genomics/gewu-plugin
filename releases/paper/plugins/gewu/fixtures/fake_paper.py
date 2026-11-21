#!/usr/bin/env python3
"""Small fixture for the Paper machine CLI; never staged for release."""
import json
from pathlib import Path
import sys
import time

cmd = sys.argv[1:]
if "--help" in cmd:
    print("describe PROJECT --json; build PROJECT --output-stem NAME")
elif cmd[0] == "init":
    project = Path(cmd[1]); project.mkdir()
    (project / "metadata.yaml").write_text("contents: [main.md]\n")
    (project / "main.md").write_text("# Test manuscript\n")
elif cmd[0] == "describe":
    print(json.dumps({"version":1, "sources":["main.md"], "supplement":[], "metadata":{}, "outputStem":"legacy"}))
elif cmd[0] == "build":
    project = Path(cmd[1])
    if (project / "slow").exists():
        (project / "started").touch()
        time.sleep(30)
    out = Path(cmd[cmd.index("--out-dir") + 1]); stem = cmd[cmd.index("--output-stem") + 1]
    (out / (stem + ".tex")).write_text("TeX fixture")
    for extension in ("html", "pdf", "docx"):
        if "--output-" + extension in cmd:
            (out / (stem + "." + extension)).write_text(extension)
