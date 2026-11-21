// Paper inside GeWu. The manuscript stays Markdown; this page is a block view
// over the adapter's revision-checked `document`/`edit` tools. Plain paragraphs
// and headings edit as text; anything carrying protected syntax (citations,
// maths, tables, raw TeX) opens as its exact source so nothing is re-serialised
// from rendered output.
const el = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const HEADING = /^(#{1,6})\s/;

let rpc;
let project = null;
let snapshot = null;
let dirty = false;
let saving = Promise.resolve();
let scrollMemory = 0;

function setStatus(text, error = false) {
  el('status').textContent = text;
  el('status').classList.toggle('error', error);
}

window.addEventListener('message', ({source, data: message, ports}) => {
  if (source !== parent || message?.type !== 'gewu.view.bound' || !ports[0]) return;
  const port = ports[0];
  const epoch = message.context.viewEpoch;
  const saved = message.context.state ?? {};
  scrollMemory = typeof saved.scroll === 'number' ? saved.scroll : 0;
  const binding = message.context.bindingResource ?? message.context.resource;
  if (!binding?.uri) { setStatus('Open a Paper project folder to edit it.', true); return; }
  project = {uri: binding.uri, type: 'paper.project'};
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
  for (let tries = 0; tries < 2400; tries++) {
    const call = await rpc('getCall', {callId});
    if (call.status === 'running') { await sleep(200); continue; }
    if (call.status !== 'succeeded' || !call.result?.ok) {
      const code = call.result?.error?.code;
      const message = call.result?.error?.message ?? `The ${tool} call ${call.status}`;
      throw Object.assign(Error(message), {code});
    }
    return call.result;
  }
  throw Error(`The ${tool} call did not settle`);
}

function saveState() {
  rpc('saveState', {state: {scroll: Math.round(window.scrollY)}}).catch(() => {});
}

function publish(block, entry) {
  if (!block) return;
  const summary = `${entry.path} · ${block.kind} · ${block.text.slice(0, 200)}`;
  rpc('publishContext', {summary}).catch(() => {});
}

// -- document ---------------------------------------------------------------

async function boot() {
  setStatus('Loading manuscript…');
  await refresh();
  setStatus(`Ready · revision ${snapshot.project.revision.slice(0, 8)}`);
  window.scrollTo(0, scrollMemory);
  el('document').addEventListener('scroll', saveState, {passive: true});
}

async function refresh() {
  const result = await invoke('document', {project});
  apply(result.data);
}

function apply(data) {
  snapshot = data;
  project = data.project;
  el('project').textContent = decodeURIComponent(project.uri.replace('workspace:///', '')) || 'workspace';
  el('revision').textContent = project.revision.slice(0, 8);
  el('undo').disabled = !data.canUndo;
  el('redo').disabled = !data.canRedo;
  render();
}

function render() {
  const host = el('document');
  host.replaceChildren();
  for (const entry of snapshot.files) {
    if (entry.role === 'metadata' || !entry.blocks.length) continue;
    const head = document.createElement('div');
    head.className = 'file-head';
    head.textContent = entry.role === 'supplement' ? `${entry.path} · supplement` : entry.path;
    host.append(head);
    if (!entry.mapped) {
      const note = document.createElement('p');
      note.className = 'preserved';
      note.dataset.kind = 'not editable';
      note.textContent = entry.reason ?? 'This file cannot be edited in place.';
      host.append(note);
      continue;
    }
    for (const block of entry.blocks) host.append(renderBlock(entry, block));
  }
}

function renderBlock(entry, block) {
  if (!block.editable) {
    const pre = document.createElement('pre');
    pre.className = 'block preserved';
    pre.dataset.kind = block.kind.toLowerCase();
    pre.textContent = block.source.trimEnd();
    return pre;
  }
  const heading = HEADING.exec(block.source);
  if (block.kind === 'Heading' && heading) {
    const node = document.createElement(`h${Math.min(heading[1].length + 1, 6)}`);
    node.className = 'block';
    node.contentEditable = 'plaintext-only';
    node.spellcheck = true;
    node.textContent = block.text;
    wireText(node, entry, block);
    return node;
  }
  if (block.kind === 'Paragraph' && block.inline === 'plain') {
    const node = document.createElement('p');
    node.className = 'block';
    node.contentEditable = 'plaintext-only';
    node.spellcheck = true;
    node.textContent = block.text;
    wireText(node, entry, block);
    return node;
  }
  // Rich blocks are shown as their source, which is what gets written back.
  const node = document.createElement('div');
  node.className = 'block rich';
  node.dataset.kind = block.inline === 'rich' ? `${block.kind.toLowerCase()} · markdown` : block.kind.toLowerCase();
  node.tabIndex = 0;
  node.textContent = block.source.trimEnd();
  node.addEventListener('click', () => openSourceEditor(node, entry, block));
  node.addEventListener('focus', () => publish(block, entry));
  return node;
}

function wireText(node, entry, block) {
  const original = block.text;
  node.addEventListener('focus', () => publish(block, entry));
  node.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      // A new paragraph is a new block; the adapter edits one block at a time.
      event.preventDefault();
      commitText(node, entry, block, original);
      return;
    }
    if ((event.metaKey || event.ctrlKey) && event.key === 's') {
      event.preventDefault();
      commitText(node, entry, block, original);
    }
  });
  node.addEventListener('blur', () => commitText(node, entry, block, original));
}

function openSourceEditor(node, entry, block) {
  if (node.querySelector('.editor-text')) return;
  const original = block.source;
  const area = document.createElement('textarea');
  area.className = 'editor-text';
  area.spellcheck = false;
  area.value = original.trimEnd() + '\n';
  node.textContent = '';
  node.append(area);
  area.focus();
  area.setSelectionRange(area.value.length, area.value.length);
  const finish = (send) => {
    if (!area.isConnected) return;
    const value = area.value;
    node.textContent = value.trimEnd();
    if (send && value.trimEnd() !== original.trimEnd()) {
      enqueue(() => editBlock(entry, block, value));
    }
  };
  area.addEventListener('blur', () => finish(true));
  area.addEventListener('keydown', (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === 's') {
      event.preventDefault();
      finish(true);
    }
    if (event.key === 'Escape') { event.preventDefault(); finish(false); }
  });
}

function commitText(node, entry, block, original) {
  if (!node.isConnected) return;
  const value = node.textContent ?? '';
  if (value === original) return;
  enqueue(() => editBlock(entry, block, value));
}

function enqueue(task, {stale = true, label = 'Saving…'} = {}) {
  dirty = true;
  el('status').textContent = label;
  el('status').classList.remove('error');
  saving = saving.then(task, task).catch((error) => {
    const conflict = stale && (error.code === 'STALE_REVISION' || error.code === 'STALE_BLOCK');
    setStatus(conflict ? 'This block changed elsewhere; reloading the manuscript.' : error.message, true);
    if (conflict) refresh().catch(() => {});
  }).finally(() => { dirty = false; });
  return saving;
}

async function editBlock(entry, block, text) {
  const result = await invoke('edit', {
    project,
    file: entry.path,
    byteStart: block.byteStart,
    byteEnd: block.byteEnd,
    text,
    fileRevision: entry.revision,
  });
  apply(result.data);
  setStatus(`Saved · revision ${result.data.project.revision.slice(0, 8)}`);
}

async function history(direction) {
  await enqueue(async () => {
    const result = await invoke(direction, {project, expectedRevision: project.revision});
    apply(result.data);
    setStatus(`${direction === 'undo' ? 'Undone' : 'Redone'} · revision ${result.data.project.revision.slice(0, 8)}`);
  });
}

async function buildHtml() {
  await saving;
  await enqueue(async () => {
    setStatus('Building HTML…');
    const result = await invoke('build', {project, formats: ['html']});
    const html = result.resources.find((r) => r.uri.endsWith('.html'));
    const docx = result.resources.find((r) => r.uri.endsWith('.docx'));
    if (html) await rpc('openResource', {resource: html});
    const link = el('download');
    if (docx) {
      const {url} = await rpc('resolveResource', {resource: docx});
      link.href = url;
      link.download = docx.uri.split('/').pop();
      link.hidden = false;
    }
    setStatus(`Built HTML from revision ${result.data.sourceRevision.slice(0, 8)}`);
  }, {stale: false, label: 'Building HTML…'});
}

el('undo').addEventListener('click', () => history('undo'));
el('redo').addEventListener('click', () => history('redo'));
el('build-html').addEventListener('click', () => buildHtml().catch((error) => setStatus(error.message, true)));
window.addEventListener('beforeunload', saveState);

parent.postMessage({type: 'gewu.view.ready'}, '*');
