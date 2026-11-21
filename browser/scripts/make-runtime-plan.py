#!/usr/bin/env python3
"""Emit a gewu.runtime-plan/v1 for GeWu Browser from packed-runtime metadata."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

PROBE = (
    "from playwright.sync_api import sync_playwright;"
    "p=sync_playwright().start();"
    "e=p.chromium.executable_path;"
    "p.stop();"
    "import os,sys;"
    "sys.exit(0 if e and os.path.exists(e) else 1)"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-meta", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    runtime = json.loads(args.runtime_meta.read_text())
    plugin, version = runtime["plugin"], runtime["version"]
    tag = f"{plugin}-{version}"
    base = args.base_url.rstrip("/")

    def asset(path: str, digest: str) -> str:
        return f"{base}/releases/download/{tag}/{digest[:12]}/{Path(path).name}"

    target = runtime["target"]
    plan = {
        "schemaVersion": "gewu.runtime-plan/v1",
        "policies": ["bundled", "external"],
        "providers": [
            {
                "kind": "external",
                "id": "gewu-browser-python",
                "target": target,
                "executable": {"env": "GEWU_BROWSER_PYTHON", "commands": ["python3"]},
                "probe": {"args": ["-c", PROBE], "timeoutSeconds": 120},
                "binaries": {"python": "{executable}", "python3": "{executable}"},
            },
            {
                "kind": "bundled",
                "id": runtime["id"],
                "version": version,
                "target": target,
                "artifact": {
                    "url": asset(runtime["runtime"]["path"], runtime["runtime"]["sha256"]),
                    "sha256": runtime["runtime"]["sha256"],
                    "bytes": runtime["runtime"]["bytes"],
                },
                "manifest": {
                    "url": asset(runtime["runtime"]["path"] + ".json", runtime["manifest"]["sha256"]),
                    "sha256": runtime["manifest"]["sha256"],
                    "bytes": runtime["manifest"]["bytes"],
                },
            },
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(plan, sort_keys=True, indent=2) + "\n")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
