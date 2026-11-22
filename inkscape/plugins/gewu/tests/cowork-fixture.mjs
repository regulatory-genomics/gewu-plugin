// Resource-scoped app tests need a provider that accepts the host's appended view context.
import {once} from 'node:events';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {mkdir,mkdtemp,readFile,rm,symlink,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {createServer as tlsServer} from 'node:tls';
import {connect as tcpConnect} from 'node:net';

export async function startCoworkFixture({realConfig,modelName,keep=false,settings={},installOnly=false,cleanPATH=false,installed}={}) {
  const home=await mkdtemp(join(process.env.GEWU_TEST_TMP ?? tmpdir(),'inkscape-'));
  if(installed)await symlink(join(installed.home,'.gewu'),join(home,'.gewu'),'dir');
  const root=join(home,'workspace'); await mkdir(root);
  const requests=[],children=[]; let logs='',secureProxy;
  const listen=async(server)=>{server.listen(0,'127.0.0.1');await once(server,'listening');return server.address().port;};
  const reserve=async()=>{const s=createServer();const port=await listen(s);await new Promise(resolve=>s.close(resolve));return port;};
  const provider=createServer(async(req,res)=>{
    if(req.url!=='/v1/chat/completions'){res.writeHead(404).end();return;}
    let body='';for await(const c of req) body+=c;
    const turn=JSON.parse(body); requests.push(turn);
    const user=turn.messages.findLastIndex(m=>m.role==='user');
    const content=turn.messages[user].content;
    const text=typeof content==='string'?content:content.map(x=>x.text??'').join('');
    const after=turn.messages.slice(user+1).some(m=>m.role==='tool');
    const match=/^call (\S+) (\{[^\n]+\}|\{\})(?:\n|$)/.exec(text);
    const send=(delta,finish_reason=null)=>res.write(`data: ${JSON.stringify({id:'fixture',model:'fixture',object:'chat.completion.chunk',choices:[{index:0,delta,finish_reason}]})}\n\n`);
    res.writeHead(200,{'content-type':'text/event-stream'});
    if(!after && match && turn.tools?.some(x=>x.function.name===match[1])){
      send({role:'assistant',tool_calls:[{index:0,id:'call-'+requests.length,type:'function',function:{name:match[1],arguments:match[2]}}]});send({},'tool_calls');
    } else {send({role:'assistant',content:after?'The adapter returned a result.':'No matching tool.'});send({},'stop');}
    res.end('data: [DONE]\n\n');
  });
  const model=await listen(provider);
  const close=async()=>{
    if(keep)await writeFile(join(home,'host.log'),logs);
    for(const p of children.reverse()) if(p.exitCode===null&&p.signalCode===null){const stop=once(p,'exit');p.kill('SIGINT');const kill=setTimeout(()=>p.kill('SIGKILL'),5000);await stop;clearTimeout(kill);}
    provider.closeAllConnections();await new Promise(resolve=>provider.close(resolve));
    if(secureProxy)await new Promise(resolve=>secureProxy.close(resolve));
    await rm(join(home,'config.json'),{force:true});if(!keep)await rm(home,{recursive:true,force:true});
  };
  try {
    const config=join(home,'config.json');
    const input=realConfig?JSON.parse(await readFile(realConfig,'utf8')):null;
    const preset=input?{model:modelName||input.model,endpoints:input.endpoints}:{model:'local/fixture',endpoints:{local:{base_url:`http://127.0.0.1:${model}/v1`,api_key:'test-only',models:{fixture:{provider:'openai-compatible'}}}}};
    await writeFile(config,JSON.stringify({...preset,
      plugins:installOnly?{}:{inkscape:{package:installed?'':process.env.GEWU_APP_PLUGIN,enabled:true,settings:installed?settings:{inkscapeExecutable:process.env.INKSCAPE_BIN,editorMode:'legacy',...settings}}}}),{mode:0o600});
    const ws=await reserve(),tcp=await reserve(),http=await reserve(),gui=await reserve();
    const start=(args)=>{
      const env={...process.env,HOME:home,GEWU_GUI_TOKEN:'',GEWU_SERVE_TOKEN:'',NO_PROXY:'127.0.0.1,localhost',no_proxy:'127.0.0.1,localhost'};
      if(cleanPATH){env.PATH='/usr/bin:/bin';for(const key of ['GEWU_PLUGIN_PYTHON','GEWU_INKSCAPE_PYTHON','GEWU_RUNTIME_ROOT','INKSCAPE_BIN','DISPLAY','PYTHONPATH','PYTHONHOME','LD_LIBRARY_PATH'])delete env[key];}
      for(const key of ['OPENAI_MODEL','GEWU_PROVIDER','OPENAI_API_KEY','OPENAI_BASE_URL','OPENAI_ENDPOINT']) delete env[key];
      const child=spawn(process.env.GEWU_BIN,[...args],{cwd:root,env,stdio:['ignore','pipe','pipe']});children.push(child);
      child.on('error',e=>{logs+=e.message;});for(const stream of [child.stdout,child.stderr])stream.on('data',x=>{logs=(logs+x).slice(-16000);});
    };
    const ready=async(url,requireOK=true)=>{
      for(let i=0;i<150;i++){try{const response=await fetch(url,{signal:AbortSignal.timeout(200)});if(!requireOK||response.ok)return;}catch{}await new Promise(r=>setTimeout(r,100));}
      throw new Error(`GeWu did not become ready: ${logs}`);
    };
    start(['--no-update-check','serve','--llm-config',config,'--db-path',join(home,'test.db'),'--ws-port',String(ws),'--tcp-port',String(tcp),'--http-port',String(http)]);
    await ready(`http://127.0.0.1:${http}/download`,false);
    start(['--no-update-check','gui','--port',String(gui),'--server',`ws://127.0.0.1:${ws}`,'--server-http',`http://127.0.0.1:${http}`]);
    let url=`http://127.0.0.1:${gui}`;await ready(url+'/api/config');
    if(process.env.GEWU_TEST_TLS_CERT){
      secureProxy=tlsServer({cert:await readFile(process.env.GEWU_TEST_TLS_CERT),key:await readFile(process.env.GEWU_TEST_TLS_KEY)},socket=>{
        const upstream=tcpConnect(gui,'127.0.0.1');socket.pipe(upstream);upstream.pipe(socket);
        socket.on('error',()=>upstream.destroy());upstream.on('error',()=>socket.destroy());socket.on('close',()=>upstream.destroy());upstream.on('close',()=>socket.destroy());
      });
      url=`https://127.0.0.1:${await listen(secureProxy)}`;
    }
    return {root,url,close,requests};
  }catch(error){await close();throw error;}
}
