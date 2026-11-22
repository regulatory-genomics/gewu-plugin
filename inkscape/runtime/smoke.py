from pathlib import Path
import os
import tempfile

from native import Engine
import svg  # also verifies the Web editor's pinned Python dependencies

with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
    root = Path(tmp)
    fixture = root / 'figure.svg'
    fixture.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96"><rect id="square" width="48" height="48" fill="red"/></svg>')
    engine = Engine(Path(os.environ['GEWU_RUNTIME_ROOT']) / 'bin/inkscape')
    assert engine.query(fixture)['square']['width'] > 12
    engine.export(fixture, root / 'figure.png', 'png')
    engine.export(fixture, root / 'figure.pdf', 'pdf')
    print('Inkscape: SVG query, PNG and PDF verified')
