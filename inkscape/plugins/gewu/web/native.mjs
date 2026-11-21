import {desktopSurface} from './surface.mjs';
const $=id=>document.getElementById(id);
let rpc,surface,state,work,lastInput=0,activityURL,lastContext,closed=false;
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function error(e){$('error').hidden=false;$('error').textContent=e.message||String(e);}
function clearError(){$('error').hidden=true;}
async function invoke(tool,args={}){
  const {callId}=await rpc('callTool',{tool,args});
  for(let i=0;i<900;i++){
    const call=await rpc('getCall',{callId});
    if(['queued','running'].includes(call.status)){await wait(100);continue;}
    if(call.status!=='succeeded'||!call.result?.ok)throw Object.assign(Error(call.result?.error?.message||call.error?.message||`${tool}: ${call.status}`),{code:call.result?.error?.code});
    return call.result.data;
  }
  throw Error('Native request did not settle; read state before retrying');
}
async function activity(data){
  lastInput=Date.now();
  if(!activityURL||data.composing!==undefined||data.release){
    try{
      if(!activityURL)activityURL=(await rpc('requestService',{serviceId:'native-control',path:'/activity',method:'POST'})).url;
      const response=await fetch(activityURL,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
      if(!response.ok)activityURL=null;
    }catch{activityURL=null;}
  }
}
async function refresh(){
  for(let retry=0;;retry++){
    try{state=await invoke('state',{limit:1});break;}
    catch(e){if(e.code!=='HUMAN_EDIT_ACTIVE'||retry>=8)throw e;await wait(150);}
  }
  $('document').textContent=state.document;
  $('status').textContent=state.conflict?'External file conflict':state.dirty?'Unsaved changes':'Saved to project';
  $('selection').textContent=`${state.selectionCount} selected · ${state.total} native objects${state.checkpointAt?' · recovery '+new Date(state.checkpointAt*1000).toLocaleTimeString():''}`;
  const summary=`Native Inkscape ${state.document}. Revision: ${state.revision}. Selected IDs: ${state.selection.join(', ').slice(0,1500)||'none'}. Use state/inspect/capabilities before edit; native document owns undo.`;
  if(summary!==lastContext){await rpc('publishContext',{summary});lastContext=summary;}
  return state;
}
async function connect(){
  clearError();$('status').textContent='Connecting native Inkscape…';
  if(surface){surface.close();surface=null;}
  const result=await rpc('requestService',{serviceId:'native-screen',path:'/screen',method:'GET'});
  const url=new URL(result.url,location.href);url.protocol=url.protocol==='https:'?'wss:':'ws:';
  surface=desktopSurface($('screen'),url.href,activity);
  surface.rfb.addEventListener('connect',()=>{
    $('reconnect').hidden=true;document.querySelectorAll('header button,footer button').forEach(b=>b.disabled=false);
    refresh().catch(error);
  });
  surface.rfb.addEventListener('disconnect',()=>{if(!closed){$('status').textContent='Disconnected — document remains open';$('reconnect').hidden=false;}});
}
async function action(fn){
  if(work)await work;
  const running=(async()=>{clearError();try{await fn();await refresh();}catch(e){error(e);}})();
  work=running;try{await running;}finally{if(work===running)work=null;}
}
window.addEventListener('message',async event=>{
  if(event.source!==parent||event.data?.type!=='gewu.view.bound'||event.ports.length!==1||rpc)return;
  const port=event.ports[0],pending=new Map();let next=0;
  port.onmessage=({data})=>{const call=pending.get(data.id);if(!call)return;pending.delete(data.id);clearTimeout(call.timer);data.error?call.reject(Error(data.error.message||String(data.error))):call.resolve(data.result);};
  rpc=(method,params={})=>new Promise((resolve,reject)=>{
    const id=++next;const timer=setTimeout(()=>{pending.delete(id);reject(Error(`${method} timed out`));},45000);
    pending.set(id,{resolve,reject,timer});port.postMessage({id,method,params,viewEpoch:event.data.context.viewEpoch});
  });
  try{
    const mode=await invoke('diagnose');
    if(mode.editorMode==='legacy'){
      const response=await fetch(new URL('legacy.html',import.meta.url));
      const legacy=new DOMParser().parseFromString(await response.text(),'text/html');
      document.head.querySelector('link').href=new URL('editor.css',import.meta.url).href;
      document.body.replaceChildren(...legacy.body.childNodes);closed=true;rpc=null;
      const {bind}=await import('./editor.mjs');bind(event);return;
    }
    await connect();
  }catch(e){error(e);$('reconnect').hidden=false;}
});
parent.postMessage({type:'gewu.view.ready'},'*');
$('save').onclick=()=>action(async()=>{const s=await refresh();await invoke('save',{expectedRevision:s.revision});});
for(const button of document.querySelectorAll('[data-format]'))button.onclick=()=>action(async()=>{
  const s=await refresh(),format=button.dataset.format;
  const result=await invoke('export',{revision:s.revision,format,page:$('page').valueAsNumber,dpi:$('dpi').valueAsNumber,text:$('pdf-text').value});
  const {url}=await rpc('resolveResource',{resource:result.artifact});
  const link=document.createElement('a');link.href=url;link.textContent=`Download ${format.toUpperCase()}`;
  link.onclick=event=>{event.preventDefault();rpc('downloadResource',{resource:result.artifact}).catch(error);};$('download').replaceChildren(link);
});
$('reconnect').onclick=()=>action(connect);
$('maximize').onclick=()=>{rpc('maximize').catch(error);};
$('fit').onclick=()=>{surface.rfb.resizeSession=true;surface.rfb.scaleViewport=!surface.rfb.scaleViewport;};
$('paste-send').onclick=()=>{surface.paste($('paste').value);$('paste').value='';surface.input.focus();};
async function importFiles(files){
  for(const file of files){await action(async()=>{
    const {url}=await rpc('requestService',{serviceId:'native-control',path:'/assets/'+encodeURIComponent(file.name),method:'POST'});
    const raw=Uint8Array.from(atob(file.base64),c=>c.charCodeAt(0));
    const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:raw});
    const result=await response.json();if(!response.ok)throw Error(result.error||'Import upload failed');
    const s=await refresh();await invoke('edit',{expectedRevision:s.revision,operations:[{op:'import_panel',path:result.path,...(file.name.toLowerCase().endsWith('.pdf')?{page:$('page').valueAsNumber,text:$('pdf-text').value}:{})}]});
  });}
}
$('import').onclick=()=>rpc('pickFiles',{accept:'.svg,.png,.jpg,.jpeg,.pdf',multiple:true}).then(({files})=>importFiles(files)).catch(error);
$('screen').addEventListener('dragover',event=>{if(event.dataTransfer.types.includes('Files'))event.preventDefault();});
$('screen').addEventListener('drop',async event=>{
  const files=[...event.dataTransfer.files];if(!files.length)return;event.preventDefault();
  try{
    if(files.length>8||files.some(f=>f.size>32*1024*1024))throw Error('Choose at most 8 files, each no larger than 32 MiB');
    const prepared=await Promise.all(files.map(file=>new Promise((resolve,reject)=>{
      const reader=new FileReader();reader.onerror=()=>reject(Error('Could not read file'));reader.onload=()=>resolve({name:file.name,base64:reader.result.split(',')[1]});reader.readAsDataURL(file);
    })));
    await importFiles(prepared);
  }catch(e){error(e);}
});
setInterval(()=>{if(!closed&&rpc&&!work&&!document.hidden&&Date.now()-lastInput>2000)action(refresh);},2500);
window.addEventListener('pagehide',()=>{closed=true;surface?.close();});
