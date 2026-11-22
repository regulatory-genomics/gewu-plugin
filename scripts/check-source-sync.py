#!/usr/bin/env python3
"""Check that the HyperFrames adapter built in its monorepo is the canonical one."""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--hyperframes-root', type=Path, required=True)
    parser.add_argument('--check', action='store_true', required=True)
    args = parser.parse_args()
    metadata = json.loads(args.sources.read_text())['plugins']['hyperframes']
    src = args.sources.resolve().parent / metadata['plugin']
    dst = args.hyperframes_root.resolve() / 'plugins/gewu'
    subprocess.run(['git', '-C', args.hyperframes_root, 'merge-base', '--is-ancestor',
                    metadata['originalSourceCommit'], 'HEAD'], check=True)
    paths = sorted([*src.glob('src/*.ts'), *src.glob('web/*.*'), src / 'build.ts', src / 'plugin.json'])
    if not paths or not (dst / 'src').is_dir():
        parser.error('plugin build mirror is missing')
    differences = []
    for path in paths:
        relative = path.relative_to(src)
        target = dst / relative
        if not target.is_file() or path.read_bytes() != target.read_bytes():
            differences.append(str(relative))
    if differences:
        parser.error('HyperFrames integration differs: ' + ', '.join(differences))
    print(f'HyperFrames: {len(paths)} release inputs match the pinned build checkout')


if __name__ == '__main__':
    main()
