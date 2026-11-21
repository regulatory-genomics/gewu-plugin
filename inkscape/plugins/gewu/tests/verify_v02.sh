#!/bin/bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../../.." && pwd)
export PYTHONPATH="$root/plugins/gewu"
export INKSCAPE_BIN=${INKSCAPE_BIN:-"$root/.gewu-test/squashfs-root/AppRun"}
export XVFB_BIN=${XVFB_BIN:-/data/litian/micromamba/envs/py311/bin/Xvfb}
PY=${PYTHON:-"$root/.runtime/bin/python"}
gate=${1:?Choose spike, transport, session, interaction, agent, assets, install, reliability, performance, acceptance or release}
mkdir -p "$root/.gewu-test/v02/$gate"
run=$(mktemp -d "$root/.gewu-test/v02/$gate/run.XXXXXX")
echo "Artifacts: $run"
export GEWU_TEST_TMP="$run"
export GEWU_ROOT=${GEWU_ROOT:?Set GEWU_ROOT to the WebSocket-enabled GeWu checkout}
export GEWU_BIN=${GEWU_BIN:-"$GEWU_ROOT/target/debug/gewu"}
export PATH="$(dirname "$PY"):$PATH"
tests(){ "$PY" -m unittest discover -s "$root/plugins/gewu/tests" -p "$1" -v 2>&1 | tee "$run/$1.log";if grep -q 'skipped=' "$run/$1.log";then exit 1;fi; }
browser(){
  if [[ -z ${GEWU_APP_PLUGIN:-} ]];then "$PY" "$root/plugins/gewu/package.py" --sdk-root "$GEWU_ROOT/sdk/python" --out "$run/package";export GEWU_APP_PLUGIN="$run/package";fi
  GEWU_APP_SPEC="$root/plugins/gewu/tests/$1" npm --prefix "$GEWU_ROOT/gewu-gui/tests/browser" test -- application-plugin.spec.mjs --output "$run/browser/${1%.mjs}"
}
case "$gate" in
  spike) tests test_native_roundtrip.py;browser native-surface-spike.mjs ;;
  transport)
    cargo test --manifest-path "$GEWU_ROOT/Cargo.toml" -p gewu-protocol --test plugin_websocket_contract
    cargo test --manifest-path "$GEWU_ROOT/Cargo.toml" -p gewu-server --test plugin_resources
    openssl req -x509 -newkey rsa:2048 -nodes -keyout "$run/tls.key" -out "$run/tls.crt" -days 1 -subj /CN=127.0.0.1 2>"$run/tls.log"
    export GEWU_TEST_TLS_CERT="$run/tls.crt" GEWU_TEST_TLS_KEY="$run/tls.key"
    browser native-workspace.spec.mjs ;;
  session) tests test_native_session.py ;;
  interaction) browser native-workspace.spec.mjs;browser native-surface-spike.mjs ;;
  agent) tests test_native_document.py;tests test_native_session.py ;;
  assets) tests test_native_assets.py;browser native-assets.spec.mjs ;;
  install)
    : "${GEWU_PACKED_RUNTIME:?Set GEWU_PACKED_RUNTIME to the built packed runtime}"
    : "${GEWU_RUNTIME_ARTIFACT:?Set GEWU_RUNTIME_ARTIFACT to packed .runtime.json metadata}"
    : "${GEWU_MARKETPLACE_ROOT:?Set GEWU_MARKETPLACE_ROOT to the publisher checkout}"
    bash "$root/plugins/gewu/tests/verify_clean_runtime.sh" "$GEWU_PACKED_RUNTIME"
    "$PY" "$root/plugins/gewu/tests/verify_marketplace_install.py" --gewu-root "$GEWU_ROOT" --marketplace-root "$GEWU_MARKETPLACE_ROOT" --runtime-root "$GEWU_PACKED_RUNTIME" --runtime-artifact "$GEWU_RUNTIME_ARTIFACT" --out "$run/catalog" ;;
  reliability) browser native-recovery.spec.mjs;tests test_native_session.py ;;
  performance) "$PY" "$root/plugins/gewu/tests/verify_native_performance.py" --out "$run/native" ;;
  acceptance) : "${GEWU_REAL_CONFIG:?Set the real provider configuration}";browser native-real-model.spec.mjs ;;
  release) tests test_native_document.py;tests test_native_session.py;bash "$root/plugins/gewu/tests/verify.sh" ;;
  *) echo "Gate '$gate' has not been implemented" >&2; exit 2 ;;
esac
