// Manimble editor inside GeWu. The engine owns the deck, the source and the
// history; this page is the canvas over its `edit` commands, following the
// desktop app's rules: corner handles scale, text sides re-wrap, edges and
// centres snap at 6px, shift locks an axis and option turns snapping off.
const el = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const SNAP_PX = 6;
const num = (value) => (typeof value === 'number' ? Math.round(value * 100) / 100 : value);

let rpc;
let deck;
let scenes = [];
let current = null;
let build = 0;
let data = null;
let saved = {};
let zoom = 1;
let queue = Promise.resolve();
let scale = 1;               // canvas pixels per frame pixel
let sel = [];                // primary object first
let drag = null;
let firstLoad = true;
const urls = new Map();

function setStatus(text, error = false) {
  el('status').textContent = text;
  el('status').classList.toggle('error', error);
}

window.addEventListener('message', ({source, data: message, ports}) => {
  if (source !== parent || message?.type !== 'gewu.view.bound' || !ports[0]) return;
  const port = ports[0];
  const epoch = message.context.viewEpoch;
  saved = message.context.state ?? {};
  build = Number.isInteger(saved.build) ? saved.build : 0;
  zoom = typeof saved.zoom === 'number' ? saved.zoom : 1;
  el('zoom').value = String(zoom);
  const pending = new Map();
  let id = 0;
  port.onmessage = ({data: reply}) => {
    const entry = pending.get(reply.id);
    if (!entry) return;
    pending.delete(reply.id);
    if (reply.error) entry.reject(Error(reply.error)); else entry.resolve(reply.result);
  };
  rpc = (method, params) => new Promise((resolve, reject) => {
    pending.set(++id, {resolve, reject});
    port.postMessage({id, method, params, viewEpoch: epoch});
  });
  boot().catch((error) => setStatus(error.message, true));
}, {once: true});

async function invoke(tool, args = {}) {
  const {callId} = await rpc('callTool', {tool, args});
  for (let tries = 0; tries < 4800; tries++) {
    const call = await rpc('getCall', {callId});
    if (call.status === 'running') { await sleep(250); continue; }
    if (call.status !== 'succeeded' || !call.result?.ok) {
      throw Error(call.result?.error?.message ?? `The ${tool} call ${call.status}`);
    }
    return call.result;
  }
  throw Error(`The ${tool} call did not settle`);
}

async function resourceUrl(resource) {
  const key = `${resource.uri}@${resource.revision ?? ''}`;
  if (!urls.has(key)) urls.set(key, (await rpc('resolveResource', {resource})).url);
  return urls.get(key);
}

function report() {
  const where = [deck?.deck, current, `build ${build + 1}`].filter(Boolean).join(' · ');
  const what = !sel.length ? '' : sel.length === 1
    ? ` · selected ${objectInfo(sel[0]).kind ?? 'object'} ${objectInfo(sel[0]).value ?? ''}`.trimEnd()
    : ` · ${sel.length} objects selected`;
  rpc('publishContext', {summary: `${where}${what}`}).catch(() => {});
}

function saveState() {
  const state = {zoom, selection: sel.map(String)};
  if (current) state.scene = current;
  if (build) state.build = build;
  rpc('saveState', {state}).catch(() => {});
}

// -- deck ------------------------------------------------------------------

async function boot() {
  setStatus('Loading deck…');
  await refreshDeck();
  const wanted = saved.scene && scenes.some((scene) => scene.name === saved.scene) ? saved.scene : scenes[0]?.name;
  if (!wanted) throw Error('This deck has no slides yet');
  await loadScene(wanted);
  el('canvas').focus();
}

async function refreshDeck() {
  deck = (await invoke('state')).data;
  scenes = deck.scenes;
  el('undo').disabled = !deck.canUndo;
  el('redo').disabled = !deck.canRedo;
  el('undo').title = deck.undoLabel ? `Undo ${deck.undoLabel}` : 'Undo';
  await renderSlides();
}

async function renderSlides() {
  const list = el('slides');
  list.replaceChildren();
  for (const scene of scenes) {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'slide';
    item.dataset.scene = scene.name;
    item.setAttribute('aria-current', String(scene.name === current));
    const image = document.createElement('img');
    image.alt = '';
    image.loading = 'lazy';
    if (scene.thumb) image.src = await resourceUrl(scene.thumb).catch(() => '');
    const name = document.createElement('span');
    name.className = 'name';
    const title = document.createElement('span');
    title.textContent = scene.name;
    const state = document.createElement('span');
    state.className = `state ${scene.status}`;
    state.textContent = scene.hidden ? 'hidden' : scene.status;
    name.append(title, state);
    item.append(image, name);
    item.addEventListener('click', () => openScene(scene.name).catch((error) => setStatus(error.message, true)));
    list.append(item);
  }
}

function openScene(name) {
  if (name === current && data) return queue;
  const task = async () => { await loadScene(name); await renderSlides(); };
  queue = queue.then(task, task).catch((error) => setStatus(error.message, true));
  return queue;
}

async function loadScene(name) {
  current = name;
  setStatus(`Rendering ${name}…`);
  data = (await invoke('scene', {scene: name})).data;
  build = Math.min(build, data.builds - 1);
  if (firstLoad && saved.scene === name && Array.isArray(saved.selection)) sel = saved.selection.map(String);
  firstLoad = false;
  sel = sel.filter((oid) => data.objects[oid]);
  saved = {...saved, selection: sel};
  await draw();
  renderBuilds();
  setStatus(`${name} · ${data.builds} build${data.builds === 1 ? '' : 's'}`);
  saveState();
  report();
}

function renderBuilds() {
  const group = el('builds');
  group.replaceChildren();
  for (let index = 0; index < (data?.builds ?? 0); index++) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = String(index + 1);
    button.setAttribute('aria-pressed', String(index === build));
    button.setAttribute('aria-label', `Build ${index + 1}`);
    button.addEventListener('click', () => { build = index; draw().catch((error) => setStatus(error.message, true)); saveState(); renderBuilds(); });
    group.append(button);
  }
}

// -- canvas ----------------------------------------------------------------

function objectInfo(oid) {
  return data?.objects?.[String(oid)] ?? {};
}

function kindOf(oid) {
  return objectInfo(oid).kind ?? '';
}

function isText(oid) {
  return ['text', 'tex'].includes(kindOf(oid));
}

function isLine(oid) {
  return ['line', 'arrow', 'darrow'].includes(kindOf(oid));
}

function boxes() {
  const map = new Map();
  for (const entry of data?.stops?.[build]?.visible ?? []) {
    const [oid, x0, y0, x1, y1] = entry;
    if (x1 - x0 < 0.5 && y1 - y0 < 0.5) continue;   // an invisible anchor is not a box
    map.set(String(oid), [x0, y0, x1, y1]);
  }
  return map;
}

async function draw() {
  const canvas = el('canvas');
  // A layout notification must never discard the user's unfinished text.
  if (canvas.querySelector('#inline')) return;
  if (!data) return;
  const [width, height] = data.size;
  const stage = el('stage');
  // The dock can be hidden or mid-layout when a view boots: a zero size would
  // leave an unusable canvas, so fall back to the frame's own pixels.
  const room = {width: stage.clientWidth - 20, height: stage.clientHeight - 20};
  if (room.width < 40 || room.height < 30) { requestDraw(); return; }
  scale = Math.min(room.width / width, room.height / height) * zoom;
  canvas.style.width = `${Math.round(width * scale)}px`;
  canvas.style.height = `${Math.round(height * scale)}px`;
  canvas.replaceChildren();

  const image = document.createElement('img');
  image.alt = `${current} build ${build + 1}`;
  image.src = await resourceUrl(data.frames[build]);
  canvas.append(image);

  const layer = document.createElement('div');
  layer.id = 'objects';
  layer.style.cssText = 'position:absolute;inset:0';
  canvas.append(layer);

  const primary = sel[0];
  for (const [oid, [x0, y0, x1, y1]] of boxes()) {
    const hit = document.createElement('div');
    hit.className = 'hit';
    hit.dataset.oid = oid;
    if (sel.includes(oid)) hit.classList.add('sel');
    if (oid === primary) hit.classList.add('primary');
    const w = (x1 - x0) * scale;
    const h = (y1 - y0) * scale;
    // A 2px rule is a real object but an unhittable target: give every box a
    // grabbable minimum without moving the object itself.
    const gw = Math.max(w, 10);
    const gh = Math.max(h, 10);
    hit.style.left = `${x0 * scale - (gw - w) / 2}px`;
    hit.style.top = `${y0 * scale - (gh - h) / 2}px`;
    hit.style.width = `${gw}px`;
    hit.style.height = `${gh}px`;
    const info = objectInfo(oid);
    hit.setAttribute('role', 'option');
    hit.setAttribute('aria-selected', String(sel.includes(oid)));
    hit.setAttribute('aria-label', `${info.kind ?? 'object'} ${info.value ?? ''}`.trim());
    if (oid === primary) addHandles(hit, oid);
    layer.append(hit);
  }
  renderInspector();
  renderActions();
}

function addHandles(hit, oid) {
  for (const corner of ['nw', 'ne', 'sw', 'se']) {
    const handle = document.createElement('div');
    handle.className = 'handle';
    handle.dataset.handle = corner;
    handle.style.left = corner[1] === 'w' ? '0' : '100%';
    handle.style.top = corner[0] === 'n' ? '0' : '100%';
    hit.append(handle);
  }
  if (isText(oid)) {
    for (const side of ['w', 'e']) {
      const handle = document.createElement('div');
      handle.className = 'handle';
      handle.dataset.handle = side;
      handle.style.left = side === 'w' ? '0' : '100%';
      handle.style.top = '50%';
      hit.append(handle);
    }
  }
}

function framePoint(event) {
  const rect = el('canvas').getBoundingClientRect();
  return {x: (event.clientX - rect.left) / scale, y: (event.clientY - rect.top) / scale};
}

function select(next) {
  sel = [...new Set(next.map(String))];
  paintSelection();
  saveState();
  report();
}

// Repaint outlines and handles only: a full draw() would rebuild the boxes
// under the pointer and race an in-flight drag.
function paintSelection() {
  const layer = el('canvas').querySelector('#objects');
  if (!layer) return;
  const primary = sel[0];
  for (const hit of layer.querySelectorAll('.hit')) {
    const oid = hit.dataset.oid;
    hit.classList.toggle('sel', sel.includes(oid));
    hit.classList.toggle('primary', oid === primary);
    hit.setAttribute('aria-selected', String(sel.includes(oid)));
    for (const handle of hit.querySelectorAll('.handle')) handle.remove();
    if (oid === primary) addHandles(hit, oid);
  }
  renderInspector();
  renderActions();
}

function toggleSelect(oid) {
  oid = String(oid);
  select(sel.includes(oid) ? sel.filter((item) => item !== oid) : [oid, ...sel]);
}

// -- dragging --------------------------------------------------------------

function targets(box, skip) {
  const [width, height] = data.size;
  const xs = [0, width / 2, width];
  const ys = [0, height / 2, height];
  for (const [oid, other] of boxes()) {
    if (skip.includes(oid)) continue;
    xs.push(other[0], (other[0] + other[2]) / 2, other[2]);
    ys.push(other[1], (other[1] + other[3]) / 2, other[3]);
  }
  return {xs, ys};
}

function snap(box, dx, dy) {
  const {xs, ys} = targets(box, sel);
  const tolerance = SNAP_PX / scale;
  const moved = [box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy];
  const best = (cands, options) => {
    let hit = null;
    for (const cand of cands) for (const target of options) {
      const delta = target - cand;
      if (Math.abs(delta) <= tolerance && (!hit || Math.abs(delta) < Math.abs(hit.delta))) hit = {delta, target};
    }
    return hit;
  };
  const guides = [];
  const onX = best([moved[0], (moved[0] + moved[2]) / 2, moved[2]], xs);
  if (onX) { dx += onX.delta; guides.push({vertical: true, pos: onX.target}); }
  const onY = best([moved[1], (moved[1] + moved[3]) / 2, moved[3]], ys);
  if (onY) { dy += onY.delta; guides.push({vertical: false, pos: onY.target}); }
  return {dx, dy, guides};
}

function showGuides(guides) {
  for (const old of el('canvas').querySelectorAll('.guide')) old.remove();
  for (const guide of guides) {
    const line = document.createElement('div');
    line.className = `guide ${guide.vertical ? 'v' : 'h'}`;
    if (guide.vertical) line.style.left = `${guide.pos * scale}px`;
    else line.style.top = `${guide.pos * scale}px`;
    el('canvas').append(line);
  }
}

function startMove(event, oid) {
  // Snap the object under the pointer, even when it is not the primary one.
  const box = boxes().get(String(oid));
  if (!box) return;
  drag = {kind: 'move', start: framePoint(event), box, dx: 0, dy: 0, moved: false};
}

function startResize(event, handle) {
  const oid = sel[0];
  const box = boxes().get(oid);
  if (!box) return;
  drag = {kind: handle.length === 1 ? 'wrap' : 'resize', handle, oid, box, start: framePoint(event), moved: false};
}

function startMarquee(event) {
  const rect = document.createElement('div');
  rect.className = 'marquee';
  el('canvas').append(rect);
  const start = framePoint(event);
  drag = {kind: 'marquee', start, rect, moved: false};
}

function updateDrag(event) {
  if (!drag) return;
  const p = framePoint(event);
  const hit = (oid) => el('canvas').querySelector(`.hit[data-oid="${oid}"]`);
  if (drag.kind === 'move') {
    let dx = p.x - drag.start.x;
    let dy = p.y - drag.start.y;
    if (event.shiftKey) { if (Math.abs(dx) > Math.abs(dy)) dy = 0; else dx = 0; }
    const snapped = event.altKey ? {dx, dy, guides: []} : snap(drag.box, dx, dy);
    dx = snapped.dx;
    dy = snapped.dy;
    drag.dx = dx;
    drag.dy = dy;
    drag.moved = drag.moved || Math.abs(dx) + Math.abs(dy) > 0.5;
    for (const oid of sel) {
      const node = hit(oid);
      if (node) node.style.transform = `translate(${dx * scale}px, ${dy * scale}px)`;
    }
    showGuides(snapped.guides);
    return;
  }
  if (drag.kind === 'resize') {
    const [x0, y0, x1, y1] = drag.box;
    const cx = (x0 + x1) / 2;
    const cy = (y0 + y1) / 2;
    const corner = {nw: [x0, y0], ne: [x1, y0], sw: [x0, y1], se: [x1, y1]}[drag.handle];
    const from = Math.hypot(corner[0] - cx, corner[1] - cy);
    drag.ratio = Math.max(0.05, Math.hypot(p.x - cx, p.y - cy) / Math.max(from, 1));
    drag.moved = drag.moved || Math.abs(drag.ratio - 1) > 0.01;
    const node = hit(drag.oid);
    if (node) node.style.transform = `scale(${drag.ratio})`;
    return;
  }
  if (drag.kind === 'wrap') {
    const [x0, , x1] = drag.box;
    drag.width = Math.max(24, drag.handle === 'e' ? p.x - x0 : x1 - p.x);
    drag.moved = drag.moved || Math.abs(drag.width - (x1 - x0)) > 2;
    const node = hit(drag.oid);
    if (node) {
      if (drag.handle === 'e') node.style.width = `${drag.width * scale}px`;
      else { node.style.left = `${(x1 - drag.width) * scale}px`; node.style.width = `${drag.width * scale}px`; }
    }
    return;
  }
  if (drag.kind === 'marquee') {
    const x0 = Math.min(drag.start.x, p.x), x1 = Math.max(drag.start.x, p.x);
    const y0 = Math.min(drag.start.y, p.y), y1 = Math.max(drag.start.y, p.y);
    Object.assign(drag.rect.style, {
      left: `${x0 * scale}px`, top: `${y0 * scale}px`,
      width: `${(x1 - x0) * scale}px`, height: `${(y1 - y0) * scale}px`,
    });
    drag.area = [x0, y0, x1, y1];
  }
}

function endDrag(event) {
  if (!drag) return;
  const finished = drag;
  drag = null;
  showGuides([]);
  if (finished.kind === 'marquee') {
    finished.rect.remove();
    const area = finished.area;
    if (!area) return;
    const picked = [...boxes()].filter(([, b]) => b[0] < area[2] && b[2] > area[0] && b[1] < area[3] && b[3] > area[1]).map(([oid]) => oid);
    if (picked.length) select([...new Set([...sel, ...picked])]);
    return;
  }
  if (!finished.moved) return;
  if (finished.kind === 'move') {
    const dx = Math.round(finished.dx);
    const dy = Math.round(finished.dy);
    if (!dx && !dy) { draw().catch(() => {}); return; }
    const stop = build < data.builds - 1 ? {build: true, stop: build} : {};
    commit(sel.map((oid) => ({scene: current, op: 'move', oid, dx, dy, ...stop})));
    return;
  }
  if (finished.kind === 'resize') {
    commit([{scene: current, op: 'resize', oid: finished.oid, ratio: Number(finished.ratio.toFixed(4))}]);
    return;
  }
  if (finished.kind === 'wrap') {
    commit([{scene: current, op: 'wrap', oid: finished.oid, width: Math.round(finished.width), side: finished.handle === 'e' ? 'right' : 'left'}]);
  }
}

// -- writing ---------------------------------------------------------------

function commit(edits) {
  if (!edits.length) return queue;
  const how = edits.length > 1 ? `${edits.length} objects` : (objectInfo(edits[0].oid).kind ?? 'edit');
  const task = async () => {
    setStatus(`Saving ${how}…`);
    for (const args of edits) await invoke('edit', args);
    await refreshDeck();
    if (!scenes.some((scene) => scene.name === current)) current = scenes[0]?.name ?? null;
    if (current) await loadScene(current);
    await renderSlides();
    if (current) setStatus(`Saved ${current}`);
  };
  queue = queue.then(task, task).catch((error) => { setStatus(error.message, true); draw().catch(() => {}); });
  return queue;
}

/// Ids follow creation order, so the highest goes first and the rest keep theirs.
function deleteEdits() {
  return [...sel].sort((a, b) => Number(b) - Number(a)).map((oid) => ({scene: current, op: 'delete', oid}));
}

function run(tool, args = {}) {
  const task = async () => {
    setStatus(`Saving ${tool}…`);
    const result = await invoke(tool, args);
    await refreshDeck();
    const next = result.data?.select;
    if (next && scenes.some((scene) => scene.name === next)) current = next;
    if (current && !scenes.some((scene) => scene.name === current)) current = scenes[0]?.name ?? null;
    if (current) await loadScene(current);
    await renderSlides();
    if (current) setStatus(`Saved ${current}`);
  };
  queue = queue.then(task, task).catch((error) => setStatus(error.message, true));
  return queue;
}

// -- inspector -------------------------------------------------------------

function field(label, id, value, commitValue, attrs = {}) {
  const wrap = document.createElement('div');
  const name = document.createElement('label');
  name.textContent = label;
  name.htmlFor = id;
  const input = document.createElement('input');
  input.id = id;
  input.type = attrs.type ?? 'text';
  input.value = String(value ?? '');
  for (const [key, extra] of Object.entries(attrs)) input.setAttribute(key, String(extra));
  input.addEventListener('change', () => commitValue(input.value));
  wrap.append(name, input);
  return wrap;
}

function renderInspector() {
  const panel = el('inspector');
  panel.replaceChildren();
  const heading = document.createElement('h2');
  heading.textContent = sel.length > 1 ? `${sel.length} selected` : 'Selection';
  panel.append(heading);
  const primary = sel[0];
  const info = primary ? objectInfo(primary) : null;
  if (!info) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = 'Click an object, shift-click to add, drag an empty area to marquee.';
    panel.append(empty);
    return;
  }
  const meta = document.createElement('p');
  meta.className = 'meta';
  meta.textContent = `${info.kind}${info.loc ? ` · ${info.loc}` : ''}`;
  panel.append(meta);

  if (isText(primary)) {
    panel.append(field('Font size', 'f-size', num(info.fontSize) ?? '', (value) => commit([{scene: current, op: 'font', oid: primary, value: Number(value)}]), {type: 'number', min: 4, step: 1}));
    panel.append(field('Colour', 'f-color', info.color ?? '#ffffff', (value) => commit([{scene: current, op: 'color', oid: primary, value}]), {type: 'color'}));
    const align = document.createElement('div');
    const name = document.createElement('label');
    name.textContent = 'Align';
    const row = document.createElement('div');
    row.className = 'row';
    for (const value of ['left', 'center', 'right']) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = value[0].toUpperCase();
      button.title = `Align ${value}`;
      button.setAttribute('aria-pressed', String((info.format?.align ?? 'left') === value));
      button.addEventListener('click', () => commit([{scene: current, op: 'align', oid: primary, value}]));
      row.append(button);
    }
    align.append(name, row);
    panel.append(align);
    panel.append(field('Line spacing', 'f-spacing', num(1 + (info.format?.lineSpacing ?? 0.3)), (value) => commit([{scene: current, op: 'spacing', oid: primary, value: Number(value) - 1}]), {type: 'number', min: 1, step: 0.1}));
  }

  panel.append(field('Rotate °', 'f-rotate', num(info.rotation) ?? 0, (value) => commit([{scene: current, op: 'rotate', oid: primary, degrees: Number(value)}]), {type: 'number', step: 15}));

  if (isText(primary)) {
    const style = document.createElement('div');
    const styleName = document.createElement('label');
    styleName.textContent = 'Weight';
    const styleRow = document.createElement('div');
    styleRow.className = 'row';
    for (const [label, op, values] of [['B', 'style', {weight: 'BOLD'}], ['I', 'style', {slant: 'ITALIC'}], ['Clear', 'clear_format', {}]]) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = label;
      button.addEventListener('click', () => commit([{scene: current, op, oid: primary, ...values}]));
      styleRow.append(button);
    }
    style.append(styleName, styleRow);
    panel.append(style);
  }

  const remove = document.createElement('button');
  remove.type = 'button';
  remove.textContent = 'Delete object';
  remove.style.marginTop = '10px';
  remove.addEventListener('click', () => commit(deleteEdits()));
  panel.append(remove);
}

function renderActions() {
  el('group').disabled = sel.length < 2;
  el('ungroup').disabled = !sel.some((oid) => objectInfo(oid).group != null);
  for (const id of ['front', 'forward', 'backward', 'back']) el(id).disabled = !sel.length;
  el('undo').disabled = !deck?.canUndo;
  el('redo').disabled = !deck?.canRedo;
  el('hint').textContent = sel.length
    ? `arrows move ${sel.length > 1 ? 'all ' : ''}(shift 10px) · ⌥ no snap · ⌘Z undo`
    : 'drag to move · corners scale · text sides re-wrap';
}

// -- inline text -----------------------------------------------------------

async function editText(oid) {
  const box = boxes().get(oid);
  if (!box) return;
  const stash = el('canvas').querySelector('#inline');
  stash?.remove();
  const area = document.createElement('textarea');
  area.id = 'inline';
  area.setAttribute('aria-label', 'Edit text');
  area.wrap = 'off';
  area.value = objectInfo(oid).value ?? '';
  const info = objectInfo(oid);
  const family = info.font ? `${JSON.stringify(info.font)}, sans-serif` : 'sans-serif';
  const weight = info.weight === 'BOLD' ? 'bold' : 'normal';
  const slant = info.slant === 'ITALIC' ? 'italic' : 'normal';
  // Like the desktop editor, fit the browser font to the rendered ink width.
  const measure = document.createElement('canvas').getContext('2d');
  measure.font = `${slant} ${weight} 100px ${family}`;
  const lines = area.value.split('\n');
  const widest = Math.max(...lines.map(line => measure.measureText(line).width));
  const byHeight = (box[3] - box[1]) / (lines.length * 1.3 * 0.8);
  const fontPx = widest > 1 ? Math.min(Math.max(100 * (box[2] - box[0]) / widest, byHeight * 0.5), byHeight * 2) : byHeight;
  const size = Math.max(1, fontPx * scale);
  Object.assign(area.style, {
    left: `${box[0] * scale}px`, top: `${box[1] * scale}px`,
    width: `${(box[2] - box[0]) * scale + 8}px`, height: `${Math.max(box[3] - box[1], fontPx * lines.length * 1.3) * scale + 8}px`,
    fontSize: `${size}px`, fontFamily: family, fontWeight: weight, fontStyle: slant,
    color: info.color ?? '#ffffff', textAlign: info.format?.align ?? 'left',
    lineHeight: String(1 + (info.format?.lineSpacing ?? 0.3)),
  });
  el('canvas').append(area);
  area.focus();
  area.setSelectionRange(area.value.length, area.value.length);
  let finished = false;
  const done = (save) => {
    if (finished) return;
    finished = true;
    area.remove();
    if (save && area.value !== (info.value ?? '')) {
      commit([{scene: current, op: 'text', oid, value: area.value}]);
    }
    requestDraw();
  };
  area.addEventListener('blur', () => done(true));
  area.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { event.preventDefault(); area.value = info.value ?? ''; done(false); }
    else if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) { event.preventDefault(); done(true); }
    event.stopPropagation();
  });
}

// -- events ----------------------------------------------------------------

const canvas = el('canvas');
canvas.addEventListener('pointerdown', (event) => {
  if (event.button !== 0) return;
  if (event.target.closest('#inline')) return;
  canvas.focus();
  const handle = event.target.closest('.handle');
  if (handle) { startResize(event, handle.dataset.handle); return; }
  const hit = event.target.closest('.hit');
  if (hit) {
    const oid = hit.dataset.oid;
    if (event.shiftKey || event.metaKey || event.ctrlKey) toggleSelect(oid);
    else if (!sel.includes(oid)) select([oid]);
    startMove(event, oid);
    return;
  }
  if (!event.shiftKey) select([]);
  startMarquee(event);
});
canvas.addEventListener('dblclick', (event) => {
  const hit = event.target.closest('.hit');
  if (hit && isText(hit.dataset.oid)) editText(hit.dataset.oid);
});
window.addEventListener('pointermove', (event) => { if (drag) updateDrag(event); });
window.addEventListener('pointerup', (event) => { if (drag) endDrag(event); });

canvas.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') { select([]); return; }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
    event.preventDefault();
    run(event.shiftKey ? 'redo' : 'undo');
    return;
  }
  if (!sel.length) return;
  if (event.key === 'Enter' && isText(sel[0])) { event.preventDefault(); editText(sel[0]); return; }
  if (event.key === 'Delete' || event.key === 'Backspace') {
    event.preventDefault();
    commit(deleteEdits());
    return;
  }
  const step = event.shiftKey ? 10 : 1;
  const delta = {ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step]}[event.key];
  if (!delta) return;
  event.preventDefault();
  commit(sel.map((oid) => ({scene: current, op: 'move', oid, dx: delta[0], dy: delta[1]})));
});

function bind(id, action) {
  el(id).addEventListener('click', () => action()?.catch?.((error) => setStatus(error.message, true)));
}

bind('add', () => run('slide', {op: 'new', scene: current, layout: 'title_content'}));
bind('duplicate', () => run('slide', {op: 'duplicate', scene: current}));
bind('delete-slide', () => run('slide', {op: 'delete', scene: current}));
bind('up', () => run('slide', {op: 'up', scene: current}));
bind('down', () => run('slide', {op: 'down', scene: current}));
bind('undo', () => run('undo'));
bind('redo', () => run('redo'));
bind('insert-text', () => {
  const value = el('text').value.trim() || 'Text';
  run('insert', {scene: current, kind: 'text', value});
});
bind('insert-rect', () => run('insert', {scene: current, kind: 'rectangle'}));
bind('insert-circle', () => run('insert', {scene: current, kind: 'circle'}));
bind('insert-arrow', () => run('insert', {scene: current, kind: 'arrow'}));
bind('group', () => commit([{scene: current, op: 'group', oid: sel[0], oids: sel}]));
bind('ungroup', () => commit([{scene: current, op: 'ungroup', oid: sel[0], oids: sel}]));
for (const [id, how] of [['front', 'front'], ['forward', 'forward'], ['backward', 'backward'], ['back', 'back']]) {
  bind(id, () => commit([{scene: current, op: 'order', oid: sel[0], oids: sel, value: how}]));
}

el('zoom').addEventListener('input', () => {
  zoom = Number(el('zoom').value);
  if (data) draw().catch((error) => setStatus(error.message, true));
  saveState();
});

el('export').addEventListener('click', async () => {
  try {
    setStatus('Exporting PowerPoint…');
    const result = await invoke('export', {format: 'pptx', builds: true, editableText: true});
    const resource = result.resources.find((item) => item.type === 'manimble.pptx');
    if (!resource) throw Error('The export produced no PowerPoint file');
    const {url} = await rpc('resolveResource', {resource});
    const link = el('download');
    link.href = url;
    link.hidden = false;
    link.textContent = 'Download PowerPoint';
    setStatus('PowerPoint ready');
  } catch (error) { setStatus(error.message, true); }
});

// The dock resizes by drag or by tab switches, which never fires a window
// resize, so watch the stage itself.
let drawFrame = 0;
function requestDraw() {
  if (drawFrame) return;
  drawFrame = requestAnimationFrame(() => {
    drawFrame = 0;
    draw().catch(() => {});
  });
}
new ResizeObserver(() => { if (data) requestDraw(); }).observe(el('stage'));

parent.postMessage({type: 'gewu.view.ready'}, '*');
