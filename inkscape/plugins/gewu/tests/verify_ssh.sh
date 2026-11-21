#!/bin/bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../../.." && pwd)
plugin="$root/plugins/gewu"
export PYTHONPATH="$plugin"
: "${INKSCAPE_BIN:?}" "${XVFB_BIN:?}"
PY=${PYTHON:-python3}
run=$(mktemp -d "$root/.gewu-test/ssh-desktop.XXXXXX")
echo "$run"
cleanup(){ for pid in "${gui:-}" "${xvfb:-}" "${server:-}"; do if [[ -n $pid ]]; then kill "$pid" 2>/dev/null || true; fi; done; }
trap cleanup EXIT
ssh-keygen -q -t ed25519 -N '' -f "$run/host"
ssh-keygen -q -t ed25519 -N '' -f "$run/client"
port=$("$PY" -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])')
printf 'ListenAddress 127.0.0.1\nPort %s\nHostKey %s/host\nAuthorizedKeysFile %s/client.pub\nPidFile %s/sshd.pid\nStrictModes no\nUsePAM no\nPasswordAuthentication no\n' "$port" "$run" "$run" "$run" > "$run/sshd.conf"
printf '[127.0.0.1]:%s %s\n' "$port" "$(cut -d ' ' -f1,2 "$run/host.pub")" > "$run/known"
printf 'Host fixture\n HostName 127.0.0.1\n Port %s\n IdentityFile %s/client\n UserKnownHostsFile %s/known\n IdentitiesOnly yes\n' "$port" "$run" "$run" > "$run/ssh.conf"
/usr/sbin/sshd -f "$run/sshd.conf" -E "$run/sshd.log"
server=$(cat "$run/sshd.pid")
export GEWU_TEST_DESKTOP="$run/test.json"
"$PY" - "$run" "$plugin" <<'PY'
import json,sys
from pathlib import Path
import svg
root,plugin=map(Path,sys.argv[1:]); local=root/'sender'; local.mkdir(); desktop=root/'desktop'; desktop.mkdir()
figure=svg.new_page(90,70);svg.apply(figure,{'op':'add_text','id':'label','text':'Desktop fixture','x':10,'y':10},root,None)
data=svg.encode(figure)
for folder in (local,desktop): (folder/'checkout.svg').write_bytes(data)
remote={'host':'fixture','session':str(desktop/'session'),'python':sys.executable,'script':str(plugin/'remote.py'),'sshConfig':str(root/'ssh.conf')}
(root/'test.json').write_text(json.dumps({'remote':remote,'originHash':svg.digest(data)}))
PY
"$XVFB_BIN" -displayfd 3 -screen 0 1024x768x24 3>"$run/display" 2>"$run/xvfb.log" & xvfb=$!
for i in {1..50}; do [[ ! -s "$run/display" ]] || break; sleep .1; done
DISPLAY=":$(cat "$run/display")" NO_AT_BRIDGE=1 "$PY" "$plugin/live.py" launch --inkscape "$INKSCAPE_BIN" --figure "$run/desktop/checkout.svg" --session "$run/desktop/session" >"$run/gui.log" 2>&1 & gui=$!
"$PY" - "$run/desktop/session" "$run/desktop/checkout.svg" <<'PY'
import sys,time
from pathlib import Path
from live import Live
session,figure=map(Path,sys.argv[1:]); deadline=time.monotonic()+20
while not (session/'session.json').exists() and time.monotonic()<deadline: time.sleep(.1)
Live(session,figure,None).wait_ready(15)
PY
"$PY" -m unittest discover -s "$plugin/tests" -p test_remote.py -v
