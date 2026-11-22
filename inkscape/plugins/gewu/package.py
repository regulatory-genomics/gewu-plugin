#!/usr/bin/env python3
"""Stage an installable GeWu plugin with the matching Python SDK."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import tempfile

from contract import NATIVE_TOOLS, TOOLS

HERE=Path(__file__).resolve().parent
FILES=('plugin.json','adapter.py','contract.py','document.py','svg.py','native.py','workflow.py','live.py','remote.py','native_document.py')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk-root',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--headless',action='store_true',help='Stage the Web figure editor without the desktop session')
    parser.add_argument('--python',help='Bind an executable Python for a local source install')
    parser.add_argument('--inkscape',help='Bind the native Inkscape executable (requires --headless and --python)')
    options=parser.parse_args()
    if options.inkscape and not (options.headless and options.python):
        parser.error('--inkscape requires --headless and --python')
    if options.python and options.headless and not options.inkscape:
        parser.error('a local headless install needs --inkscape')
    for name in ('python','inkscape'):
        value=getattr(options,name)
        if value:
            value=os.path.abspath(value)
            if not Path(value).is_file() or not os.access(value,os.X_OK): parser.error(f'--{name} must be executable')
            setattr(options,name,value)
    out=options.out.absolute()
    if out.exists() or out.is_symlink() or out.resolve().is_relative_to(HERE):
        parser.error('--out must be a new staging directory outside plugin source')
    out.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        package=Path(tmp)/'package'; package.mkdir(); (package/'lib').mkdir()
        for name in FILES:
            if options.headless and name=='native_document.py': continue
            shutil.copy2(HERE/name,package/name)
        for name in ('bin','web','skills','desktop'):
            if options.headless and name=='desktop': continue
            if (HERE/name).exists(): shutil.copytree(HERE/name,package/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        if options.headless:
            (package/'web/legacy.html').replace(package/'web/index.html')
            index=package/'web/index.html'
            index.write_text(index.read_text().replace('<title>Figure layout (legacy)</title>', '<title>Figure layout</title>')
                             .replace('<details><summary>Desktop editing</summary>', '<details hidden><summary>Desktop editing</summary>'))
            for name in ('native.mjs','native.css','surface.mjs','novnc'):
                path=package/'web'/name
                if path.is_dir(): shutil.rmtree(path)
                elif path.exists(): path.unlink()
            manifest=json.loads((package/'plugin.json').read_text())
            manifest['settingsSchema']['properties']={'timeoutSeconds':manifest['settingsSchema']['properties']['timeoutSeconds']}
            (package/'plugin.json').write_text(json.dumps(manifest,indent=2)+'\n')
        shutil.copy2(options.sdk_root/'gewu_plugin.py',package/'lib/gewu_plugin.py')
        if options.python:
            local=''
            if options.headless:
                runtime=package/'.local-runtime/bin'; runtime.mkdir(parents=True)
                (runtime/'inkscape').write_text(f'#!/bin/sh\nexec {shlex.quote(options.inkscape)} "$@"\n')
                (runtime/'inkscape').chmod(0o755)
                local='export GEWU_INKSCAPE_HEADLESS=1 GEWU_RUNTIME_ROOT="$dir/.local-runtime"\n'
            (package/'bin/inkscape-gewu').write_text('#!/bin/sh\nset -eu\ndir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\n'
                'export PYTHONPATH="$dir/lib"\n'+local+f'exec {shlex.quote(options.python)} "$dir/adapter.py"\n')
        (package/'tools.schema.json').write_text(json.dumps(TOOLS if options.headless else NATIVE_TOOLS,indent=2)+'\n')
        (package/'bin/inkscape-gewu').chmod(0o755)
        if out.exists() or out.is_symlink(): raise FileExistsError(out)
        package.rename(out)


if __name__=='__main__': main()
