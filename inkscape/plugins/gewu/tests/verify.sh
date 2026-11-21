#!/bin/bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../../.." && pwd)
plugin="$root/plugins/gewu"
: "${GEWU_ROOT:?Set GEWU_ROOT to the GeWu checkout}"
: "${INKSCAPE_BIN:?Set INKSCAPE_BIN to a runnable Inkscape executable}"
: "${XVFB_BIN:?Set XVFB_BIN to Xvfb for the native desktop gate}"
PY=${PYTHON:-python3}
export GEWU_BIN=${GEWU_BIN:-"$GEWU_ROOT/target/debug/gewu"}
export INKSCAPE_BIN XVFB_BIN GEWU_ROOT PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$plugin"
mkdir -p "$root/.gewu-test"
run=$(mktemp -d "$root/.gewu-test/verify.XXXXXX")
export GEWU_TEST_TMP="$run"
echo "Verification artifacts: $run"
"$PY" -m unittest discover -s "$plugin/tests" -v
"$PY" "$plugin/package.py" --sdk-root "$GEWU_ROOT/sdk/python" --out "$run/package"
mkdir -p "$run/workspace/figure" "$run/home"
"$PY" -c 'import json,os; print(json.dumps({"inkscapeExecutable":os.environ["INKSCAPE_BIN"],"editorMode":"legacy"}))' > "$run/settings.json"
"$PY" "$GEWU_ROOT/scripts/plugin-conformance.py" --package "$run/package" --workspace "$run/workspace" \
  --settings "$run/settings.json" --scenario "$plugin/tests/conformance.json" --schema-root "$GEWU_ROOT/gewu-protocol/schema"
HOME="$run/home" "$GEWU_BIN" --no-update-check plugin install "$run/package"
HOME="$run/home" "$GEWU_BIN" --no-update-check plugin check inkscape --probe --workspace "$run/workspace" \
  --settings "$run/settings.json" --resource workspace:///figure --resource-type inkscape.document
export GEWU_APP_PLUGIN="$run/package" GEWU_APP_SPEC="$plugin/tests/gui.spec.mjs"
npm --prefix "$GEWU_ROOT/gewu-gui/tests/browser" test -- application-plugin.spec.mjs --output "$run/browser"
