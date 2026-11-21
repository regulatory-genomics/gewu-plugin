"""Verify the default GUI install against a disposable, signed catalog and real runtime."""
import argparse
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
from urllib.parse import urlsplit


class RangeHandler(SimpleHTTPRequestHandler):
    def log_message(self,*_):pass

    def do_GET(self):
        header=self.headers.get('Range')
        if not header:return super().do_GET()
        match=re.fullmatch(r'bytes=(\d+)-(\d+)',header)
        path=Path(self.translate_path(self.path))
        if not match or not path.is_file():self.send_error(416);return
        size=path.stat().st_size;start,end=map(int,match.groups());end=min(end,size-1)
        if start>end:self.send_error(416);return
        self.send_response(206);self.send_header('Content-Type',self.guess_type(str(path)))
        self.send_header('Content-Range',f'bytes {start}-{end}/{size}');self.send_header('Content-Length',str(end-start+1));self.end_headers()
        with path.open('rb') as stream:
            stream.seek(start);remaining=end-start+1
            while remaining:
                block=stream.read(min(1024*1024,remaining))
                if not block:raise OSError('Fixture artifact changed during the download')
                self.wfile.write(block);remaining-=len(block)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('gewu-root','marketplace-root','runtime-root','runtime-artifact','out'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--signing-key',type=Path,default=Path.home()/'.config/gewu/marketplace/official-v1.pem')
    a=parser.parse_args();plugin=Path(__file__).resolve().parents[1];root=plugin.parents[1]
    a.out=a.out.resolve();a.out.mkdir(parents=True,exist_ok=False);site=a.out/'site';site.mkdir()
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(RangeHandler,directory=str(site)))
    base=f'http://127.0.0.1:{server.server_address[1]}'
    def run(*args):
        with (a.out/'build.log').open('a') as log:
            subprocess.run(list(map(str,args)),check=True,cwd=root,stdout=log,stderr=subprocess.STDOUT)
    run(sys.executable,plugin/'package.py','--sdk-root',a.gewu_root/'sdk/python','--out',a.out/'staged')
    run(sys.executable,a.gewu_root/'scripts/plugin-release.py','--descriptor',plugin/'release.json','--source-root',a.out/'staged',
        '--sdk-root',a.gewu_root/'sdk/python','--out',a.out/'package','--allow-dirty')
    now=datetime.now(timezone.utc);stamp=lambda value:value.strftime('%Y-%m-%dT%H:%M:%SZ')
    release=a.out/'inkscape-0.2.0.json'
    run(sys.executable,a.marketplace_root/'publisher/make_release.py','--package',a.out/'package/inkscape-0.2.0.package.json',
        '--runtime',a.runtime_artifact,'--runtime-plan',plugin/'runtime-plan.json','--base-url',base,
        '--source','ssh://git@172.16.211.53:2222/gilbert/gewu-inkscape.git','--created-at',stamp(now),
        '--channel','stable','--notes','Local verification-only native workspace candidate (not published)','--out',release)
    record=json.loads(release.read_bytes());record['gewu']['host']='>=0.1.8'
    record['runtimePlan']['providers'].extend({'kind':'bundled',**runtime} for runtime in record['runtimes'])
    record['provenance']['buildInputs'].append('Verification-only uncommitted worktree candidate; not a published source release')
    release.write_text(json.dumps(record,indent=2)+'\n')
    runtime=json.loads(a.runtime_artifact.read_bytes())
    assets=[(record['package'],a.out/'package/inkscape-0.2.0.package.zip'),
            (record['runtimes'][0]['artifact'],a.runtime_artifact.parent/runtime['runtime']['path']),
            (record['runtimes'][0]['manifest'],a.runtime_root/'runtime.json')]
    for artifact,source in assets:
        dest=site/urlsplit(artifact['url']).path.lstrip('/');dest.parent.mkdir(parents=True,exist_ok=True)
        os.link(source,dest)
    run(sys.executable,a.marketplace_root/'publisher/build_index.py','--release',release,'--generated-at',stamp(now),
        '--expires-at',stamp(now+timedelta(days=1)),'--out',site/'index.v1.json')
    run(sys.executable,a.marketplace_root/'publisher/sign_index.py','--index',site/'index.v1.json','--private-key',a.signing_key,
        '--out',site/'index.v1.json.sig')
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    env={**os.environ,'GEWU_MARKETPLACE_BASE_URL':base,'GEWU_TEST_TMP':str(a.out/'browser-home'),
         'GEWU_APP_PLUGIN':str(a.out/'package'),'GEWU_APP_SPEC':str(plugin/'tests/marketplace-install.spec.mjs')}
    (a.out/'browser-home').mkdir()
    try:
        subprocess.run(['npm','--prefix',str(a.gewu_root/'gewu-gui/tests/browser'),'test','--','application-plugin.spec.mjs',
                        '--output',str(a.out/'browser')],env=env,check=True,cwd=root)
    finally:server.shutdown();server.server_close();thread.join()
    print(json.dumps({'catalog':str(site/'index.v1.json'),'defaultGuiInstall':'passed','bundledRuntime':runtime['runtime']['sha256']}))


if __name__=='__main__':main()
