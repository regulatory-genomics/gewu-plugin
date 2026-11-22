from pathlib import Path
import os
import tempfile
import threading
from types import SimpleNamespace
import zipfile

from adapter import Adapter

with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
    app = Adapter()
    app.activate({'workspaceRoot': directory, 'settings': {}})
    ctrl = SimpleNamespace(cancelled=threading.Event(), progress=lambda _: None,
                           register_process=lambda *_: None, process_exited=lambda *_: None)

    def call(verb, args):
        result = app.call(verb, args, {'callId': verb}, ctrl)
        if not result['ok']:
            raise RuntimeError(result['error'])
        return result

    project = call('init', {'directory': 'paper-fixture'})['data']['project']
    fixture = Path(directory) / 'paper-fixture'
    sentence = 'Built with the packaged Paper engine.'
    (fixture / 'metadata.yaml').write_text('title: GeWu installation check\ncontents: [main.md]\n')
    (fixture / 'main.md').write_text('# Conclusion\n\n' + sentence + '\n')
    project = call('describe', {'project': project})['data']['project']
    pdf = (Path(os.environ['GEWU_RUNTIME_ROOT']) / 'bin/tectonic').is_file()
    artifacts = call('build', {'project': project, 'formats': ['html', 'docx'] + (['pdf'] if pdf else [])})['resources']
    files = {r['uri'].rsplit('.', 1)[-1]: Path(app.resolve(r)['path']) for r in artifacts}
    assert sentence in files['html'].read_text()
    with zipfile.ZipFile(files['docx']) as docx:
        assert sentence.encode() in docx.read('word/document.xml')
    if pdf:
        assert files['pdf'].read_bytes().startswith(b'%PDF-')
    print('Paper: HTML, DOCX' + (', and PDF' if pdf else '') + ' verified')
