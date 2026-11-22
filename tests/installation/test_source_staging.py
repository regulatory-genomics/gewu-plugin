"""Local packages retain their selected runtime after a Host restart."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SDK = Path(os.environ.get('GEWU_ROOT', ROOT.parents[1] / 'GeWu')) / 'sdk/python'


class SourceStagingTests(unittest.TestCase):
    def test_local_python_bindings_survive_minimal_path_and_spaces(self):
        with tempfile.TemporaryDirectory(prefix='source staging ') as tmp:
            for plugin, script, flags, entry in [
                ('manimble', 'manimble/package.py', ['--engine-root', str(ROOT / 'manimble/engine')], 'manimble-gewu'),
                ('paper', 'paper/package.py', [], 'paper-gewu'),
                ('inkscape', 'inkscape/plugins/gewu/package.py', ['--headless', '--inkscape', '/usr/bin/true'], 'inkscape-gewu'),
            ]:
                with self.subTest(plugin=plugin):
                    out = Path(tmp) / plugin
                    result = subprocess.run([sys.executable, str(ROOT / script), '--sdk-root',str(SDK),
                        '--out',str(out),'--python',sys.executable,*flags], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads((out / 'plugin.json').read_text())['id'], plugin)
                    (out / 'adapter.py').write_text('import json,os,sys; print(json.dumps({"python":sys.executable,"runtime":os.environ.get("GEWU_RUNTIME_ROOT"),"headless":os.environ.get("GEWU_INKSCAPE_HEADLESS")}))')
                    run = subprocess.run([str(out / 'bin' / entry)], env={'PATH':'/usr/bin:/bin'}, capture_output=True, text=True)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    values = json.loads(run.stdout)
                    self.assertEqual(values['python'], sys.executable)
                    if plugin != 'paper':
                        self.assertEqual(values['runtime'], str(out / '.local-runtime'))
                    if plugin == 'inkscape':
                        self.assertEqual(values['headless'], '1')
                    self.assertFalse(any(p.is_symlink() for p in out.rglob('*')))
