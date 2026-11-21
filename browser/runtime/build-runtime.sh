#!/usr/bin/env bash
# Stage a runtime tree that can be packed with scripts/plugin-runtime.py.
#
# The tree is relocatable: bin/python wraps the host Python, adds the bundled
# site-packages and points Playwright at the bundled Chromium. A fully
# self-contained Python (python-build-standalone) can replace the wrapper later
# without changing the adapter.
set -euo pipefail
if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 OUT_DIR [BASE_PYTHON]" >&2
  exit 2
fi
out=$(realpath -m "$1")
python=${2:-python3}
if [[ -e "$out" ]]; then
  echo "refusing to replace existing output: $out" >&2
  exit 2
fi
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
version=$("$python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
site="lib/python${version}/site-packages"
mkdir -p "$out/bin" "$out/$site" "$out/ms-playwright"
"$python" -m pip install --disable-pip-version-check --target "$out/$site" "playwright==1.59.0"
PLAYWRIGHT_BROWSERS_PATH="$out/ms-playwright" PYTHONPATH="$out/$site" \
  "$python" -m playwright install chromium
cat > "$out/bin/python" <<EOF
#!/bin/sh
set -eu
here=\$(CDPATH= cd -- "\$(dirname -- "\$0")" && pwd)
export PYTHONPATH="\$here/../$site\${PYTHONPATH:+:\$PYTHONPATH}"
export PLAYWRIGHT_BROWSERS_PATH="\$here/../ms-playwright"
exec "\${GEWU_BROWSER_BASE_PYTHON:-$python}" "\$@"
EOF
chmod 755 "$out/bin/python"
install -m 755 "$here/linux-x86_64/bin/launch" "$out/bin/launch"
install -m 755 "$here/linux-x86_64/bin/probe" "$out/bin/probe"
install -m 644 "$here/linux-x86_64/runtime.json" "$out/runtime.json"
echo "runtime staged at $out (base python: $python)"
