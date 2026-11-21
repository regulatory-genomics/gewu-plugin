#!/bin/bash
set -euo pipefail
runtime=${1:?Pass the built packed-runtime directory}
plugin=$(cd "$(dirname "$0")/.." && pwd)
root=$(cd "$plugin/../.." && pwd)
if [[ ! -x "$runtime/bin/python3" ]]; then echo 'Packed runtime interpreter is missing' >&2;exit 1;fi
run=$(mktemp -d "$root/.gewu-test/clean-runtime.XXXXXX")
mkdir -p "$run/home" "$run/workspace"
env -i HOME="$run/home" LANG=C.UTF-8 PATH=/usr/bin:/bin PYTHONPATH="$plugin" "$runtime/bin/python3" - "$run/workspace" "$runtime" <<'PY'
import json,sys,shutil
from pathlib import Path
from document import Document
from native import Engine
from desktop.session import NativeSession
root,runtime=map(Path,sys.argv[1:])
for name in ('Xvnc','dbus-run-session','gdbus','inkscape'):
    executable=shutil.which(name);assert executable and Path(executable).is_relative_to(runtime),(name,executable)
doc=Document(root,root,Engine());native=None
try:
    native=NativeSession(doc,{})
    revision=native.state()['revision']
    answer=native.edit(revision,[{'op':'add_text','text':'Clean runtime','x':10,'y':15}],request_id='clean')
    native.save(answer['revision'])
    assert b'Clean runtime' in (root/'figure.svg').read_bytes()
    print(json.dumps({'cleanEnvironment':True,'nativeSession':True,'saved':True,'runtime':str(runtime)}))
finally:
    if native:native.close()
    doc.close()
PY
