const $ = (id) => document.getElementById(id);
const canvas=$('canvas'), hits=$('hits'), image=canvas.querySelector('img');
const sleep=(ms)=>new Promise(resolve=>setTimeout(resolve,ms));
let rpc, model, zoom=1, pageNumber=1, selection=[], sequence=0, refreshWork, previewKey, drag, busy=false;
let liveWork,liveRevision,liveContext,livePage;

const report=(message,error=false)=>{ $('status').textContent=message; $('status').classList.toggle('error',error); };
const fail=(error)=>report(error.message,true);
const physicalPage=()=>model?.pages?.find(p=>p.number===pageNumber) ?? model?.pages?.[0];
const objects=()=>model?.objects ?? [];
const selected=()=>selection.map(id=>objects().find(o=>o.id===id)).filter(Boolean);
const val=(id)=>$(id).value;
const num=(id)=>$(id).valueAsNumber;
const canWrite=()=>!!model && !model.conflict && !model.checkedOut && !busy;
const controls=['export-svg','export-pdf','export-png','paper','manimble'];
function updateControls() {
  for (const id of controls) $(id).disabled=busy;
  $('undo').disabled=!model?.canUndo || !canWrite();
  $('redo').disabled=!model?.canRedo || !canWrite();
}
function bind(event) {
  if (event.source!==parent || event.data?.type!=='gewu.view.bound' || event.ports.length!==1 || rpc) return;
  const {viewEpoch,state}=event.data.context; const [port]=event.ports;
  const pending=new Map(); let n=0;
  port.onmessage=({data})=>{
    const reply=pending.get(data.id); if (!reply) return;
    pending.delete(data.id); clearTimeout(reply.timer);
    if (data.error) reply.reject(new Error(data.error.message ?? String(data.error))); else reply.resolve(data.result);
  };
  rpc=(method,params={})=>new Promise((resolve,reject)=>{
    const id=++n;
    const timer=setTimeout(()=>{pending.delete(id);reject(new Error(`GeWu ${method} timed out`));},35000);
    pending.set(id,{resolve,reject,timer}); port.postMessage({id,method,params,viewEpoch});
  });
  zoom=state?.zoom ?? 1; pageNumber=state?.page ?? 1; selection=state?.selection ?? [];
  $('zoom').value=zoom; refresh().catch(fail);
}
window.addEventListener('message',bind);
parent.postMessage({type:'gewu.view.ready'},'*');

async function invoke(tool,args={}) {
  const {callId}=await rpc('callTool',{tool,args});
  for (let i=0;i<900;i++) {
    const call=await rpc('getCall',{callId});
    if (['running','queued'].includes(call.status)) { await sleep(100); continue; }
    if (call.status!=='succeeded' || !call.result?.ok) {
      const error=call.result?.error;
      throw Object.assign(new Error(error?.message ?? call.error?.message ?? `${tool}: ${call.status}`),{code:error?.code});
    }
    return call.result.data;
  }
  throw new Error(`${tool} has not settled; refresh before retrying`);
}

function saveView() { rpc('saveState',{state:{zoom,page:pageNumber,selection}}).catch(()=>{}); }
function shareSelection() {
  const ids=selection.slice(), revision=model?.revision ?? null;
  const where=model?.figureId ? `Figure ${model.figureId}, page ${pageNumber}` : 'Figure layout';
  invoke('view.selection',{ids,sequence:++sequence,revision}).catch(()=>{});
  rpc('publishContext',{summary:`${where}. Selected: ${ids.join(', ').slice(0,1250) || 'none'}. Revision: ${revision ?? 'new'}`}).catch(()=>{});
  saveView();
}
function select(ids) {
  selection=[...new Set(ids)].slice(0,100); drawSelection(); shareSelection();
}

function drawSelection() {
  const ids=new Set(selection);
  for (const hit of hits.querySelectorAll('.hit')) {
    const active=ids.has(hit.dataset.id); hit.classList.toggle('selected',active);
    hit.setAttribute('aria-pressed',String(active));
    const handle=hit.querySelector('.handle'); if (handle) handle.hidden=!active || ids.size!==1;
  }
  for (const row of $('objects').children) row.setAttribute('aria-pressed',String(ids.has(row.dataset.id)));
  $('selection').textContent=ids.size ? `${ids.size} selected` : 'Selection';
  const item=selected().find(o=>o.bbox); const b=item?.bbox;
  for (const key of ['x','y','width']) {
    if (document.activeElement!==$(key)) $(key).value=b ? Number(b[key].toFixed(2)) : '';
  }
  if (document.activeElement!==$('text')) $('text').value=item?.text ?? '';
}

function draw() {
  if (!model?.exists || drag) return;
  const p=physicalPage();
  const available=Math.max(120,canvas.parentElement.clientWidth-36);
  const width=available*zoom;
  canvas.style.width=`${width}px`; canvas.style.height=`${width*p.height/p.width}px`;
  hits.replaceChildren(); $('objects').replaceChildren();
  for (const item of objects()) {
    const title=(item.locked?'🔒 ':'')+(item.text || item.source?.split('/').at(-1) || `${item.kind} ${item.id}`);
    const row=document.createElement('button'); row.className='object-row'; row.textContent=title; row.dataset.id=item.id;
    row.onclick=(event)=>select(event.shiftKey ? selection.includes(item.id) ? selection.filter(x=>x!==item.id) : [...selection,item.id] : [item.id]);
    $('objects').append(row);
    const b=item.bbox;
    if (!b || b.x+b.width<p.x || b.x>p.x+p.width || b.y+b.height<p.y || b.y>p.y+p.height) continue;
    const hit=document.createElement('button'); hit.className='hit'; hit.dataset.id=item.id;
    hit.setAttribute('aria-label',title); hit.setAttribute('aria-disabled',String(item.locked));
    Object.assign(hit.style,{left:`${(b.x-p.x)/p.width*100}%`,top:`${(b.y-p.y)/p.height*100}%`,width:`${b.width/p.width*100}%`,height:`${b.height/p.height*100}%`});
    hit.onclick=(event)=>{ if (event.detail===0) select([item.id]); };
    if (!item.locked && b.width>0) { const handle=document.createElement('span'); handle.className='handle'; handle.hidden=true; hit.append(handle); }
    hits.append(hit);
  }
  drawSelection();
}

async function refresh() {
  if (!rpc) return;
  if (refreshWork) return refreshWork;
  refreshWork=(async()=>{
    const next=await invoke('state',{limit:50});
    for (let offset=next.nextOffset;offset!==null;) {
      const part=await invoke('inspect',{offset,limit:50,revision:next.revision});
      next.objects.push(...part.objects); offset=part.nextOffset;
    }
    const changed=!model || model.revision!==next.revision;
    model=next;
    $('empty').hidden=next.exists; $('editor').hidden=!next.exists;
    if (!next.exists) { if (!busy) report('New figure'); return; }
    updateControls();
    $('conflict').hidden=!next.conflict;
    $('checkout-path').textContent=next.checkoutPath ? `Desktop copy: ${next.checkoutPath}` : '';
    $('checkout').disabled=!!next.checkedOut; $('checkin').disabled=$('discard').disabled=!next.checkedOut;
    $('live-refresh').disabled=!next.checkedOut;
    $('live-follow').disabled=!next.checkedOut;
    if (!next.checkedOut) { $('live-follow').checked=false; $('live-preview').hidden=true; $('live-report').textContent=''; liveRevision=liveContext=livePage=undefined; }
    if (!next.pages.some(p=>p.number===pageNumber)) pageNumber=1;
    if (changed) {
      $('page').replaceChildren(...next.pages.map(p=>new Option(p.label,p.number))); $('page').value=pageNumber;
      selection=selection.filter(id=>next.objects.some(x=>x.id===id));
      if (!drag) for (const [key,value] of Object.entries({'page-w':next.page.width,'page-h':next.page.height,margin:next.page.margin})) {
        if (document.activeElement!==$(key)) $(key).value=value;
      }
      draw(); shareSelection();
    }
    canvas.dataset.revision=next.revision;
    const key=`${next.revision}:${pageNumber}`;
    if (previewKey!==key) {
      const {preview}=await invoke('render',{revision:next.revision,page:pageNumber});
      const {url}=await rpc('resolveResource',{resource:preview});
      if (`${model.revision}:${pageNumber}`===key) { image.src=url; previewKey=key; }
    }
    if (!busy && !$('status').classList.contains('error')) report(next.checkedOut?'Desktop checkout active':next.conflict?'External change detected':'Saved');
  })().finally(()=>{refreshWork=null;});
  return refreshWork;
}
image.addEventListener('error',()=>{previewKey=null;});
setInterval(()=>{if (rpc && !busy && !document.hidden) refresh().catch(fail);},1100);
window.addEventListener('focus',()=>refresh().catch(fail));
new ResizeObserver(draw).observe(canvas.parentElement);

async function action(tool,args,label='Saved') {
  if (busy) return;
  busy=true; updateControls(); report('Working…');
  try {
    const result=await invoke(tool,args);
    if (refreshWork) await refreshWork.catch(()=>{});
    await refresh(); report(label); return result;
  } catch (error) {
    if (error.code==='STALE_REVISION' || error.code==='EXTERNAL_MODIFICATION') await refresh().catch(()=>{});
    fail(error);
  } finally {busy=false;updateControls();}
}
const edit=(operations,expectedRevision=model?.revision??null)=>action('edit',{expectedRevision,operations});
const safeEdit=(operations)=>canWrite()?edit(operations):report('Figure is not writable',true);
const click=(id,fn)=>{$(id).onclick=()=>Promise.resolve().then(fn).catch(fail);};

function boxInPage(event) {
  const bounds=canvas.getBoundingClientRect(); const p=physicalPage();
  return {x:(event.clientX-bounds.left)*p.width/bounds.width+p.x,y:(event.clientY-bounds.top)*p.height/bounds.height+p.y};
}
canvas.onpointerdown=(event)=>{
  if (!model?.exists || event.button!==0 || drag) return;
  const hit=event.target.closest('.hit'); const key=hit?.dataset.id;
  if (key) {
    if (event.shiftKey) {select(selection.includes(key)?selection.filter(x=>x!==key):[...selection,key]);return;}
    if (!selection.includes(key)) select([key]);
    if (!canWrite() || selected().some(o=>o.locked)) return;
  } else if (!event.shiftKey) select([]);
  canvas.setPointerCapture(event.pointerId); canvas.focus();
  const mode=key?(event.target.classList.contains('handle')?'resize':'move'):'marquee';
  drag={mode,point:boxInPage(event),clientX:event.clientX,clientY:event.clientY,revision:model.revision,ids:[...selection],
        startWidth:selected()[0]?.bbox?.width,add:event.shiftKey};
  if (mode==='marquee') {drag.rect=document.createElement('div'); drag.rect.className='marquee'; hits.append(drag.rect);}
};
canvas.onpointermove=(event)=>{
  if (!drag) return;
  const dx=event.clientX-drag.clientX,dy=event.clientY-drag.clientY;
  if (drag.mode==='marquee') {
    const b=canvas.getBoundingClientRect();
    Object.assign(drag.rect.style,{left:`${Math.min(event.clientX,drag.clientX)-b.left}px`,top:`${Math.min(event.clientY,drag.clientY)-b.top}px`,width:`${Math.abs(dx)}px`,height:`${Math.abs(dy)}px`});
  } else for (const hit of hits.querySelectorAll('.selected')) {
    if (drag.mode==='move') hit.style.transform=`translate(${dx}px,${dy}px)`;
    else {hit.style.transformOrigin='top left';hit.style.transform=`scale(${Math.max(.05,(hit.offsetWidth+dx)/hit.offsetWidth)})`;}
  }
};
canvas.onpointerup=(event)=>{
  if (!drag) return;
  const gesture=drag, p=boxInPage(event); drag=null;
  const dx=p.x-gesture.point.x,dy=p.y-gesture.point.y;
  draw();
  if (gesture.mode==='marquee') {
    const [l,r]=[p.x,gesture.point.x].sort((a,b)=>a-b),[t,b]=[p.y,gesture.point.y].sort((a,b)=>a-b);
    if (Math.abs(dx)+Math.abs(dy)>.4) select([...(gesture.add?gesture.ids:[]),...objects().filter(o=>o.bbox && o.bbox.x<=r && o.bbox.x+o.bbox.width>=l && o.bbox.y<=b && o.bbox.y+o.bbox.height>=t).map(o=>o.id)]);
  } else if (Math.hypot(event.clientX-gesture.clientX,event.clientY-gesture.clientY)>3) {
    const op=gesture.mode==='move'?{op:'move',ids:gesture.ids,dx,dy}:{op:'resize',ids:gesture.ids,width:Math.max(.1,gesture.startWidth+dx)};
    edit([op],gesture.revision);
  }
};
canvas.onpointercancel=()=>{drag=null;draw();};
canvas.onkeydown=(event)=>{
  const step=event.shiftKey?1:.25;
  const vector={ArrowLeft:[-step,0],ArrowRight:[step,0],ArrowUp:[0,-step],ArrowDown:[0,step]}[event.key];
  if (vector && selection.length) {event.preventDefault();safeEdit([{op:'move',ids:selection,dx:vector[0],dy:vector[1]}]);}
  if ((event.ctrlKey||event.metaKey) && event.key.toLowerCase()==='z') {
    event.preventDefault(); if (canWrite()) action(event.shiftKey?'redo':'undo',{expectedRevision:model.revision});
  }
};

click('create',()=>safeEdit([{op:'create_document',width:num('new-width'),height:num('new-height')}]));
click('import',()=>{
  const p=physicalPage(); const paths=val('assets').split('\n').map(x=>x.trim()).filter(Boolean);
  const columns=Math.min(3,paths.length), width=Math.min(40,(p.width-8-columns*4)/Math.max(1,columns));
  if (paths.length) return safeEdit(paths.map((path,i)=>({op:'import_panel',path,width,x:p.x+6+i%columns*(width+4),y:p.y+9+Math.floor(i/columns)*(width+5)})));
});
click('undo',()=>action('undo',{expectedRevision:model.revision}));
click('redo',()=>action('redo',{expectedRevision:model.revision}));
click('reload',()=>action('reload',{expectedRevision:model.revision,observedHash:model.observedHash}));
$('page').onchange=()=>{pageNumber=Number(val('page'));draw();shareSelection();refresh().catch(fail);};
$('zoom').oninput=()=>{zoom=Number(val('zoom'));draw();saveView();};
for (const field of ['x','y','width','text']) {
  $(field).onfocus=()=>{$(field).dataset.base=model?.revision??'';};
}
for (const key of ['x','y','width']) $(key).onchange=()=>{
  const b=selected()[0]?.bbox, value=num(key); if (!b || !Number.isFinite(value)) return;
  const op=key==='width'?{op:'resize',ids:selection,width:value}:{op:'move',ids:selection,dx:key==='x'?value-b.x:0,dy:key==='y'?value-b.y:0};
  edit([op],$(key).dataset.base || model.revision);
};
for (const button of document.querySelectorAll('[data-op]')) button.onclick=()=>{
  if (!selection.length) return;
  const op=button.dataset.op, ids=[...selection], reference=ids[0];
  const edits={left:{op:'align',edge:'left',reference},top:{op:'align',edge:'top',reference},'spread-x':{op:'distribute',axis:'x',gap:num('gap')},'spread-y':{op:'distribute',axis:'y',gap:num('gap')},
    equalize:{op:'equalize',dimension:'width',reference},group:{op:'group'},ungroup:{op:'ungroup'},adopt:{op:'adopt_objects'},front:{op:'reorder',position:'front'},back:{op:'reorder',position:'back'},lock:{op:'set_locked',locked:selected().some(o=>!o.locked)},delete:{op:'delete'}};
  safeEdit([{...edits[op],ids}]);
};
click('add-text',()=>{const p=physicalPage();return safeEdit([{op:'add_text',text:val('text'),size:num('size'),color:val('color'),x:p.x+5,y:p.y+7}]);});
click('set-text',()=>{const id=selection[0];if(id && canWrite()) return edit([{op:'set_text',id,text:val('text')},{op:'set_text_style',id,size:num('size'),color:val('color')}],$('text').dataset.base || model.revision);});
click('letter',()=>safeEdit(selected().filter(o=>o.bbox).slice(0,26).map((o,i)=>({op:'add_text',text:String.fromCharCode(65+i),x:o.bbox.x,y:o.bbox.y-1,size:num('size'),color:val('color')}))));
const pageSize=()=>({width:num('page-w'),height:num('page-h')});
click('set-page',()=>safeEdit([{op:'set_page',...pageSize(),margin:num('margin')}]));
click('add-page',()=>safeEdit([{op:'add_page',...pageSize(),...(val('page-name')?{label:val('page-name')}:{})}]));
click('rename-page',()=>safeEdit([{op:'rename_page',page:pageNumber,label:val('page-name')}]));
click('source-check',async()=>{$('report').textContent=(await invoke('sources')).sources.map(x=>`${x.path}: ${x.status}`).join(' · ') || 'No linked panel sources';});
click('replace',()=>{if(selection.length===1) return safeEdit([{op:'replace_panel',id:selection[0]}]);});
click('quality',async()=>{$('report').textContent=(await invoke('check_layout')).issues.map(x=>`${x.code}: ${x.ids.join(', ')}`).join(' · ') || 'No detected layout issues';});
click('checkout',()=>action('checkout',{expectedRevision:model.revision},'Desktop checkout active'));
click('checkin',()=>action('checkin',{expectedRevision:model.revision,accept:true}));
click('discard',()=>action('checkin',{expectedRevision:model.revision,accept:false}));
function followDesktop(){
  if(liveWork)return liveWork;
  const expected=model?.revision, targetPage=pageNumber;
  liveWork=(async()=>{
    const state=await invoke('live_follow',{page:targetPage,...(livePage===targetPage&&liveRevision?{knownRevision:liveRevision}:{})});
    if(!model?.checkedOut || model.revision!==expected)return;
    if(state.preview){
      const {url}=await rpc('resolveResource',{resource:state.preview});
      if(!model?.checkedOut || model.revision!==expected || pageNumber!==targetPage)return;
      $('live-preview').src=url; $('live-preview').hidden=false; liveRevision=state.revision; livePage=targetPage;
    }
    $('live-report').textContent=`Unsaved desktop revision · ${state.selectionCount} selected`;
    const summary=`Desktop Inkscape is active. Live revision ${state.revision}; selected: ${state.selection.join(', ').slice(0,1200) || 'none'}. Use live_state before live_edit.`;
    if(summary!==liveContext){await rpc('publishContext',{summary});liveContext=summary;}
  })().catch(error=>{$('live-report').textContent=error.message;}).finally(()=>{liveWork=null;});
  return liveWork;
}
click('live-refresh',followDesktop);
$('live-follow').onchange=()=>{if($('live-follow').checked)followDesktop();};
setInterval(()=>{if(rpc&&model?.checkedOut&&$('live-follow').checked&&!document.hidden&&!busy)followDesktop();},2800);
async function download(tool,args,label) {
  const result=await action(tool,{revision:model.revision,...args},'Export ready'); if (!result) return;
  const link=document.createElement('a'); const resolve=async()=>{const {url}=await rpc('resolveResource',{resource:result.artifact});link.href=url;};
  await resolve(); link.textContent=label; link.target='_blank'; link.rel='noopener';
  let minted=Date.now(); link.addEventListener('pointerenter',()=>{if(Date.now()-minted>90000) resolve().then(()=>{minted=Date.now();}).catch(fail);});
  $('download').replaceChildren(link);
  if (result.instructions) $('report').textContent=`${result.instructions} ${result.workspacePath}`;
}
for (const format of ['svg','pdf','png']) click('export-'+format,()=>download('export',{format,...(format==='png'?{page:pageNumber,dpi:300}:{})},`Download ${format.toUpperCase()}`));
click('paper',()=>download('handoff',{consumer:'paper'},'Download for Paper'));
click('manimble',()=>download('handoff',{consumer:'manimble',page:pageNumber},'Download for Manimble'));
