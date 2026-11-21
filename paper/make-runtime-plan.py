#!/usr/bin/env python3
"""Generate Paper's runtime plan from the exact bundled runtime metadata."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-meta", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    plugin = json.loads((here / "plugin.json").read_text())
    runtime = json.loads(args.runtime_meta.read_text())
    if runtime["plugin"] != plugin["id"] or runtime["version"] != plugin["version"]:
        parser.error("runtime metadata plugin/version does not match plugin.json")

    tag = f"{plugin['id']}-{plugin['version']}"
    base = args.base_url.rstrip("/")

    def asset(path: str, digest: str) -> str:
        return f"{base}/releases/download/{tag}/{digest[:12]}/{Path(path).name}"

    target = runtime["target"]
    plan = {
        "schemaVersion": "gewu.runtime-plan/v1",
        "policies": ["external", "bundled"],
        "providers": [
            {
                "kind": "external",
                "id": "paper-cli",
                "target": target,
                "executable": {"env": "GEWU_PAPER_EXECUTABLE", "commands": ["paper"]},
                "probe": {"args": ["build", "--help"], "timeoutSeconds": 60},
                "binaries": {"paper": "{executable}"},
            },
            {
                "kind": "bundled",
                "id": runtime["id"],
                "version": runtime["version"],
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
