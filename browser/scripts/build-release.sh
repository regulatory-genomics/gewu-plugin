#!/usr/bin/env bash
# Build the immutable package, and optionally the packed runtime.
#
#   scripts/build-release.sh /tmp/gewu-browser-release
#   scripts/build-release.sh /tmp/gewu-browser-release /tmp/gewu-browser-runtime
#
# The packer rejects generated Python bytecode, so clear it first. The runtime
# must already exist; runtime/build-runtime.sh stages one on the target.
set -euo pipefail
if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 OUT_DIR [RUNTIME_ROOT]" >&2
  exit 2
fi
here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
gewu=${GEWU_ROOT:-$(realpath "$here/../../GeWu")}
out=$(realpath -m "$1")
find "$here/plugin" -name __pycache__ -type d -prune -exec rm -rf {} +
python3 "$gewu/scripts/plugin-release.py" \
  --descriptor "$here/plugin/release.json" \
  --source-root "$here/plugin" \
  --sdk-root "$gewu/sdk/python" \
  --out "$out"
if [[ -n "${2:-}" ]]; then
  runtime_out="$out-runtime"
  python3 "$gewu/scripts/plugin-runtime.py" --root "$(realpath "$2")" --out "$runtime_out"
  echo "runtime metadata: $runtime_out" >&2
fi
