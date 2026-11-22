#!/usr/bin/env bash
# Stage a relocatable GeWu Browser runtime: Python, Playwright, and Chromium.
#
#   scripts/stage-bundled-runtime.sh /tmp/gewu-browser-runtime
#   scripts/stage-bundled-runtime.sh /tmp/out /path/to/python3.11
#
# Uses uv's standalone Python by default (relocatable, no conda solve). The
# matching Chromium is copied from the local Playwright cache when present, so
# a rebuild does not re-download ~600 MB.
set -euo pipefail
if [[ $# -lt 1 || $# -gt 2 ]]; then
    echo "usage: $0 OUT_DIR [PYTHON]" >&2
    exit 2
fi
out=$(realpath -m "$1")
if [[ -e "$out" ]]; then
    echo "refusing to replace existing output: $out" >&2
    exit 2
fi
uv=${UV:-$(command -v uv || true)}
if [[ -z "$uv" ]]; then
    echo "uv is required (https://docs.astral.sh/uv/)" >&2
    exit 2
fi
python=${2:-$("$uv" python find 3.11)}
if [[ ! -x "$python" ]]; then
    echo "python is not executable: $python" >&2
    exit 2
fi

here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
mkdir -p "$out/bin" "$out/ms-playwright" "$out/site-packages"

# Copy the interpreter so the runtime does not depend on a developer path.
python_root=$(dirname "$(dirname "$python")")
cp -aL "$python_root/." "$out/python/"

"$uv" pip install --quiet --python "$out/python/bin/python3" \
    --target "$out/site-packages" --require-hashes \
    --index-url "${GEWU_BROWSER_PYPI_INDEX:-https://pypi.org/simple}" \
    -r "$here/requirements-runtime.lock"

if [[ -n "${GEWU_BROWSER_CHROMIUM_ZIP:-}" ]]; then
    chromium_zip=$(realpath "$GEWU_BROWSER_CHROMIUM_ZIP")
    expected=${GEWU_BROWSER_CHROMIUM_SHA256:?set GEWU_BROWSER_CHROMIUM_SHA256 with GEWU_BROWSER_CHROMIUM_ZIP}
    actual=$(sha256sum "$chromium_zip" | cut -d' ' -f1)
    [[ "$actual" == "$expected" ]] || { echo "Chromium archive hash mismatch: $actual" >&2; exit 2; }
    mkdir -p "$out/ms-playwright/chromium-1217"
    unzip -q "$chromium_zip" -d "$out/ms-playwright/chromium-1217"
    browser_path="$out/ms-playwright/chromium-1217/chrome-linux64/chrome"
else
    browser_path=$(PLAYWRIGHT_BROWSERS_PATH="$out/ms-playwright" PYTHONPATH="$out/site-packages" \
        "$out/python/bin/python3" -c 'from playwright.sync_api import sync_playwright; p = sync_playwright().start(); print(p.chromium.executable_path); p.stop()')
    revision_dir=${browser_path#"$out/ms-playwright/"}
    revision_dir=${revision_dir%%/*}
    cache=${PLAYWRIGHT_CACHE:-$HOME/.cache/ms-playwright}
    for name in "$revision_dir" "ffmpeg-1011"; do
        if [[ -d "$cache/$name" ]]; then
            cp -a "$cache/$name" "$out/ms-playwright/"
            rm -rf "$out/ms-playwright/$name/.links"
        fi
    done
fi
if [[ ! -x "$browser_path" ]]; then
    echo "Chromium executable is missing: $browser_path" >&2
    exit 2
fi
python3 "$here/../scripts/stage-linux-libraries.py" --out "$out/chromium-libs" "$browser_path"
python3 "$here/../scripts/stage-fonts.py" --out "$out/fonts" --license "$here/../assets/DejaVu-LICENSE.txt"

cat > "$out/bin/python" <<'EOF'
#!/bin/sh
set -eu
here=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
export PYTHONPATH="$here/site-packages${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export GEWU_BROWSER_FULL_CHROMIUM=1
export PLAYWRIGHT_BROWSERS_PATH="$here/ms-playwright"
export LD_LIBRARY_PATH="$here/chromium-libs"
export FONTCONFIG_FILE="$here/fonts/fonts.conf"
export XDG_DATA_HOME="$here"
exec "$here/python/bin/python3" "$@"
EOF
chmod 755 "$out/bin/python"
install -m 755 "$here/runtime/linux-x86_64/bin/launch" "$out/bin/launch"
install -m 755 "$here/runtime/linux-x86_64/bin/probe" "$out/bin/probe"

python3 - "$here/plugin/plugin.json" "$out/runtime.json" <<'PY'
import json, sys
from pathlib import Path
plugin = json.loads(Path(sys.argv[1]).read_text())
manifest = {
    "schemaVersion": "gewu.runtime/v2",
    "id": "gewu-browser-runtime",
    "version": plugin["version"],
    "plugin": plugin["id"],
    "target": {"os": "linux", "arch": "x86_64", "libc": "glibc"},
    "launcher": "bin/launch",
    "probe": "bin/probe",
    "installation": {"kind": "ready"},
    "capabilities": ["chromium", "playwright", "managed-browser"],
}
Path(sys.argv[2]).write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
PY

GEWU_RUNTIME_ROOT="$out" "$out/bin/probe"
echo "runtime staged at $out (${revision_dir:-chromium-1217})"
