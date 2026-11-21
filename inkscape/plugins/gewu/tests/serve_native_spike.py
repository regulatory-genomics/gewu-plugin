"""Isolated browser fixture; the production path uses GeWu service capabilities."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import signal
import sys

from aiohttp import web
from document import Document
from native import Engine
from desktop.session import NativeSession


async def main():
    root=Path(sys.argv[1]); root.mkdir(parents=True,exist_ok=True)
    plugin=Path(__file__).resolve().parents[1]
    figure=root/'figure.svg'; shutil.copyfile(plugin/'tests/fixtures/native-rich.svg',figure)
    doc=Document(root,figure,Engine(os.environ['INKSCAPE_BIN']))
    native=await asyncio.to_thread(NativeSession,doc,{})
    app=web.Application()
    @web.middleware
    async def cors(request,handler):
        response=await handler(request);response.headers['Access-Control-Allow-Origin']='null';return response
    app.middlewares.append(cors)
    async def index(request):
        return web.Response(text='<iframe id="native" sandbox="allow-scripts" src="/view.html" style="width:100%;height:95vh;border:0"></iframe>',content_type='text/html')
    async def view(request):
        return web.Response(text='''<style>body{margin:0}#screen{height:100vh}textarea{position:absolute;left:2px;top:2px;width:1px;height:1px;opacity:.01}</style><div id="screen"></div><script type="module">import {desktopSurface} from '/surface.mjs';const surface=desktopSurface(document.querySelector('#screen'),`ws://${location.host}/screen`,()=>{});window.surface=surface;</script>''',content_type='text/html')
    async def screen(request):
        return await native._screen(request.clone(headers={**request.headers,'Authorization':'Bearer '+native.token}))
    async def snapshot(request):
        _,raw=await asyncio.to_thread(native.capture);return web.Response(body=raw,content_type='image/svg+xml')
    app.router.add_get('/',index);app.router.add_get('/view.html',view);app.router.add_get('/screen',screen)
    app.router.add_get('/snapshot',snapshot)
    app.router.add_get('/surface.mjs',lambda _:web.FileResponse(plugin/'web/surface.mjs'))
    app.router.add_static('/vendor',plugin/'web/vendor')
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    print(json.dumps({'url':f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}','figure':str(figure)}),flush=True)
    stop=asyncio.Event()
    for sig in (signal.SIGINT,signal.SIGTERM): asyncio.get_running_loop().add_signal_handler(sig,stop.set)
    try: await stop.wait()
    finally:
        await runner.cleanup(); await asyncio.to_thread(native.close);doc.close()


if __name__=='__main__': asyncio.run(main())
