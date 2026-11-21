#!/usr/bin/env python3
"""Stage an installable GeWu plugin with the matching Python SDK."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile

from contract import NATIVE_TOOLS

HERE=Path(__file__).resolve().parent
FILES=('plugin.json','adapter.py','contract.py','document.py','svg.py','native.py','workflow.py','live.py','remote.py','native_document.py')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk-root',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    options=parser.parse_args()
    out=options.out.absolute()
    if out.exists() or out.is_symlink() or out.resolve().is_relative_to(HERE):
        parser.error('--out must be a new staging directory outside plugin source')
    out.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        package=Path(tmp)/'package'; package.mkdir(); (package/'lib').mkdir()
        for name in FILES:
            shutil.copy2(HERE/name,package/name)
        for name in ('bin','web','skills','desktop'):
            if (HERE/name).exists(): shutil.copytree(HERE/name,package/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        shutil.copy2(options.sdk_root/'gewu_plugin.py',package/'lib/gewu_plugin.py')
        (package/'tools.schema.json').write_text(json.dumps(NATIVE_TOOLS,indent=2)+'\n')
        (package/'bin/inkscape-gewu').chmod(0o755)
        if out.exists() or out.is_symlink(): raise FileExistsError(out)
        package.rename(out)


if __name__=='__main__': main()
