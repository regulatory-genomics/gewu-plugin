// Browser Preview. Chromium runs in the adapter; this page pulls live JPEG
// frames and forwards the user's clicks, keys and wheel as remote input.
const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const ACTIONS = {
  'tabs.list': 'Listed tabs',
  'tabs.open': 'Opened tab',
  'tabs.focus': 'Focused tab',
  'tabs.close': 'Closed tab',
  'page.navigate': 'Navigated',
  'page.snapshot': 'Read page',
  'page.click': 'Clicked',
  'page.fill': 'Filled',
  'page.select': 'Selected option',
  'page.press': 'Pressed key',
  'page.wait': 'Waited',
  'page.screenshot': 'Screenshot',
  'page.evaluate': 'Evaluated script',
  'browser.state': 'Read state',
  'view.attach': 'Opened preview',
  'view.detach': 'Closed preview',
  'preview.click': 'Clicked page',
  'preview.type': 'Typed',
  'preview.key': 'Pressed key',
  'preview.scroll': 'Scrolled',
  'preview.navigate': 'Navigated',
  'preview.down': 'Pressed mouse',
  'preview.up': 'Released mouse',
};

let rpc;
let epoch;
let state = null;
let refreshing = false;
let urlDirty = false;
let statusError = false;
let frameBusy = false;
let frameKey = '';
let mouseMode = 'left';
const tickets = new Map();

function setStatus(text, error = false) {
  statusError = error;
  $('status').textContent = text;
  $('status').classList.toggle('error', error);
}

function describeAction(entry) {
  if (!entry) return '';
  let label = ACTIONS[entry.tool] ?? entry.tool;
  if (entry.tool === 'preview.click') {
    if (entry.detail?.button === 'right') label = 'Right-clicked page';
    else if (entry.detail?.button === 'middle') label = 'Middle-clicked page';
    else if (entry.detail?.count === 2) label = 'Double-clicked page';
  }
  const elapsed = Number.isFinite(entry.durationMs) ? ` · ${entry.durationMs} ms` : '';
  const failed = entry.status && entry.status !== 'succeeded'
    ? ` · ${entry.status}${entry.code ? ` (${entry.code})` : ''}`
    : '';
  return `${label}${elapsed}${failed}`;
}

window.addEventListener('message', ({ source, data: message, ports }) => {
  if (source !== parent || message?.type !== 'gewu.view.bound' || !ports[0]) return;
  const port = ports[0];
  epoch = message.context.viewEpoch;
  const pending = new Map();
  let nextId = 0;
  port.onmessage = ({ data: reply }) => {
    const entry = pending.get(reply.id);
    if (!entry) return;
    pending.delete(reply.id);
    if (reply.error) entry.reject(new Error(String(reply.error)));
    else entry.resolve(reply.result);
  };
  rpc = (method, params) => new Promise((resolve, reject) => {
    pending.set(++nextId, { resolve, reject });
    port.postMessage({ id: nextId, method, params, viewEpoch: epoch });
  });
  refresh().catch((error) => setStatus(error.message, true));
  setInterval(() => refresh().catch(() => {}), 1500);
  setInterval(() => pollFrame(), 350);
}, { once: true });

// GeWu binds the frame only after the view announces that its JS is ready.
parent.postMessage({ type: 'gewu.view.ready' }, '*');

async function ticket(path, method) {
  const key = `${method} ${path}`;
  const cached = tickets.get(key);
  if (cached && cached.expires > Date.now()) return cached.url;
  const { url } = await rpc('requestService', { serviceId: 'preview', path, method });
  tickets.set(key, { url, expires: Date.now() + 60_000 });
  return url;
}

async function serviceJson(path, method = 'GET', body = undefined) {
  const key = `${method} ${path}`;
  const url = await ticket(path, method);
  const response = await fetch(url, {
    method,
    credentials: 'omit',
    headers: body === undefined ? {} : { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    tickets.delete(key);
    throw new Error(`Preview service returned HTTP ${response.status}`);
  }
  const payload = await response.json();
  if (!payload.ok) throw new Error(payload.error?.message ?? 'Preview request failed');
  return payload.data;
}

const control = (action, extra = {}) => serviceJson('/preview/control', 'POST', { action, ...extra });

async function invoke(tool, args) {
  const { callId } = await rpc('callTool', { tool, args });
  for (let tries = 0; tries < 600; tries += 1) {
    const call = await rpc('getCall', { callId });
    if (call.status === 'running') {
      await sleep(200);
      continue;
    }
    if (call.result && call.result.ok === false) {
      throw new Error(call.result.error?.message ?? 'The browser call failed');
    }
    return call.result?.data;
  }
  throw new Error('The browser call did not finish within 120 seconds');
}

function activeTab() {
  if (!state?.tabs?.length) return null;
  return state.tabs.find((tab) => tab.id === state.focusedTabId) ?? state.tabs[0];
}

function showFrameEmpty(text) {
  const image = $('live-frame');
  image.hidden = true;
  if (image.dataset.objectUrl) {
    URL.revokeObjectURL(image.dataset.objectUrl);
    delete image.dataset.objectUrl;
  }
  $('frame-empty').hidden = false;
  $('frame-empty').textContent = text;
}

async function pollFrame(force = false) {
  if (frameBusy || !rpc || !state?.connected || !activeTab()) return;
  frameBusy = true;
  try {
    const url = await ticket('/preview/frame', 'GET');
    const response = await fetch(url, { credentials: 'omit', cache: 'no-store' });
    if (response.status === 204) {
      showFrameEmpty('Open a page to start the live view.');
      return;
    }
    if (!response.ok) {
      tickets.delete('GET /preview/frame');
      showFrameEmpty('Live view unavailable.');
      return;
    }
    const blob = await response.blob();
    const next = URL.createObjectURL(blob);
    const image = $('live-frame');
    const previous = image.dataset.objectUrl;
    image.src = next;
    image.dataset.objectUrl = next;
    image.hidden = false;
    $('frame-empty').hidden = true;
    if (previous) URL.revokeObjectURL(previous);
  } catch (error) {
    showFrameEmpty('Live view unavailable.');
  } finally {
    frameBusy = false;
  }
}

async function input(payload) {
  try {
    const next = await serviceJson('/preview/input', 'POST', payload);
    statusError = false;
    apply(next);
    setTimeout(() => pollFrame(true), 120);
    setTimeout(() => pollFrame(true), 500);
  } catch (error) {
    setStatus(error.message, true);
  }
}

function render() {
  if (!state) return;
  $('mode').textContent = state.mode ?? 'managed';
  if (!statusError) {
    setStatus(
      state.connected
        ? `${state.tabs.length} tab${state.tabs.length === 1 ? '' : 's'}`
        : 'Browser is not running',
    );
  }

  $('takeover').hidden = !state.takeover;
  $('takeover-button').hidden = state.takeover;
  $('resume-button').hidden = !state.takeover;
  $('cancel').hidden = !(state.activeCalls?.length);
  $('cancel').textContent = state.activeCalls?.length > 1 ? 'Stop agent calls' : 'Stop agent';

  const active = activeTab();
  const inputEl = $('open-url');
  if (document.activeElement !== inputEl) urlDirty = false;
  if (!urlDirty) inputEl.value = active?.url ?? '';

  const tabList = $('tabs');
  tabList.replaceChildren();
  for (const tab of state.tabs ?? []) {
    const item = document.createElement('div');
    item.className = 'tab' + (tab.id === active?.id ? ' active' : '');
    item.dataset.tabId = tab.id;

    const main = document.createElement('button');
    main.type = 'button';
    main.className = 'tab-main';
    main.title = tab.url;
    const title = document.createElement('span');
    title.className = 'tab-title';
    title.textContent = tab.title || 'Untitled';
    const url = document.createElement('span');
    url.className = 'tab-url';
    url.textContent = tab.url;
    main.append(title, url);
    main.addEventListener('click', () => {
      urlDirty = false;
      control('focus', { tabId: tab.id }).then(apply).catch((error) => setStatus(error.message, true));
    });

    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'tab-close';
    close.setAttribute('aria-label', 'Close tab');
    close.title = 'Close tab';
    close.textContent = '×';
    close.addEventListener('click', (event) => {
      event.stopPropagation();
      invoke('tabs.close', { tabId: tab.id }).then(refresh).catch((error) => setStatus(error.message, true));
    });

    item.append(main, close);
    tabList.append(item);
  }

  $('frame-url').textContent = active?.url ?? 'No page';
  $('frame-state').textContent = state.connected && active ? 'LIVE' : '';
  $('page-meta').textContent = active
    ? `${active.title || 'Untitled'} · ${active.url} · generation ${active.generation}`
    : 'No tab yet. Open a page above.';
  $('last-action').textContent = describeAction(state.lastAction);
  $('refresh').disabled = !active;

  const key = active ? `${active.id}:${active.generation}` : '';
  if (key !== frameKey) {
    frameKey = key;
    if (active) {
      showFrameEmpty('Loading live view…');
      pollFrame(true);
    } else {
      showFrameEmpty('Open a page to start the live view.');
    }
  }
}

function apply(next) {
  state = next;
  render();
}

async function refresh() {
  if (refreshing || !rpc) return;
  refreshing = true;
  try {
    apply(await serviceJson('/preview/state'));
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    refreshing = false;
  }
}

async function go() {
  const url = $('open-url').value.trim();
  if (!url) return;
  const tab = activeTab();
  setStatus(tab ? 'Navigating…' : 'Opening…');
  try {
    if (tab) await invoke('page.navigate', { tabId: tab.id, url });
    else await invoke('tabs.open', { url });
    urlDirty = false;
    await refresh();
    pollFrame(true);
  } catch (error) {
    setStatus(error.message, true);
  }
}

function pagePoint(event) {
  const image = $('live-frame');
  if (image.hidden || !image.naturalWidth) return null;
  const rect = image.getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right
      || event.clientY < rect.top || event.clientY > rect.bottom) {
    return null;
  }
  return {
    x: (event.clientX - rect.left) * image.naturalWidth / rect.width,
    y: (event.clientY - rect.top) * image.naturalHeight / rect.height,
  };
}

$('go').addEventListener('click', go);
$('open-url').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    go();
  }
});
$('open-url').addEventListener('input', () => {
  urlDirty = true;
});

$('new-tab').addEventListener('click', async () => {
  const url = $('open-url').value.trim() || 'about:blank';
  setStatus('Opening…');
  try {
    await invoke('tabs.open', { url, focus: true });
    urlDirty = false;
    await refresh();
    pollFrame(true);
  } catch (error) {
    setStatus(error.message, true);
  }
});

$('refresh').addEventListener('click', () => {
  pollFrame(true);
});

function modifiersFrom(event) {
  const names = [];
  if (event.ctrlKey) names.push('Control');
  if (event.metaKey) names.push('Meta');
  if (event.altKey) names.push('Alt');
  if (event.shiftKey) names.push('Shift');
  return names;
}

const drag = { active: false, moved: false, start: null, last: null, pointer: null };
let hoverTimer = null;
let lastHover = null;

function sendClick(event, point, button = null, count = null) {
  const chosen = button ?? (mouseMode === 'right'
    ? 'right' : mouseMode === 'middle' ? 'middle' : 'left');
  const clicks = count ?? (mouseMode === 'double' ? 2 : 1);
  input({
    action: 'click',
    ...point,
    button: chosen,
    count: clicks,
    modifiers: modifiersFrom(event),
  });
}

$('live').addEventListener('pointerdown', (event) => {
  const point = pagePoint(event);
  if (!point) return;
  $('live').focus();
  if (event.button !== 0) return; // right/middle are handled by contextmenu/auxclick
  drag.active = true;
  drag.moved = false;
  drag.start = point;
  drag.last = point;
  drag.pointer = event.pointerId;
  try { $('live').setPointerCapture(event.pointerId); } catch {}
});

$('live').addEventListener('pointermove', (event) => {
  const point = pagePoint(event);
  if (!point) return;
  if (drag.active && event.pointerId === drag.pointer) {
    drag.last = point;
    const dx = point.x - drag.start.x;
    const dy = point.y - drag.start.y;
    if (!drag.moved && Math.hypot(dx, dy) > 4) {
      drag.moved = true;
      input({ action: 'down', ...drag.start, button: 'left' });
    }
    if (drag.moved) input({ action: 'move', ...point, button: 'left' });
    return;
  }
  lastHover = point;
  if (hoverTimer) return;
  hoverTimer = setTimeout(() => {
    hoverTimer = null;
    if (lastHover && !drag.active) input({ action: 'move', ...lastHover });
  }, 120);
});

$('live').addEventListener('pointerup', (event) => {
  if (!drag.active || event.pointerId !== drag.pointer) return;
  drag.active = false;
  const point = pagePoint(event) ?? drag.last;
  if (drag.moved && point) {
    input({ action: 'up', ...point, button: 'left' });
  } else if (point) {
    sendClick(event, point);
  }
});

$('live').addEventListener('pointercancel', () => {
  if (!drag.active) return;
  drag.active = false;
  if (drag.moved && drag.last) input({ action: 'up', ...drag.last, button: 'left' });
});

$('live').addEventListener('contextmenu', (event) => {
  const point = pagePoint(event);
  if (!point) return;
  event.preventDefault();
  $('live').focus();
  sendClick(event, point, 'right', 1);
});

$('live').addEventListener('auxclick', (event) => {
  const point = pagePoint(event);
  if (!point || event.button !== 1) return;
  event.preventDefault();
  sendClick(event, point, 'middle', 1);
});

$('mouse-mode').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-mode]');
  if (!button) return;
  mouseMode = button.dataset.mode;
  for (const item of $('mouse-mode').querySelectorAll('button')) {
    item.classList.toggle('active', item === button);
  }
});

const NAMED_KEYS = new Set([
  'Enter', 'Tab', 'Backspace', 'Delete', 'Escape', 'Home', 'End',
  'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'PageUp', 'PageDown', 'Insert',
]);

$('live').addEventListener('keydown', (event) => {
  if (['Meta', 'Control', 'Alt', 'Shift'].includes(event.key)) return;
  const modified = event.ctrlKey || event.metaKey || event.altKey;
  if (modified && event.key.length === 1) {
    const modifier = event.metaKey ? 'Meta' : event.ctrlKey ? 'Control' : 'Alt';
    event.preventDefault();
    input({ action: 'key', key: `${modifier}+${event.key}` });
  } else if (event.key.length === 1) {
    event.preventDefault();
    input({ action: 'type', text: event.key });
  } else if (NAMED_KEYS.has(event.key)) {
    event.preventDefault();
    input({ action: 'key', key: event.key });
  }
});

let wheel = { dx: 0, dy: 0, timer: null };
$('live').addEventListener('wheel', (event) => {
  event.preventDefault();
  wheel.dx += event.deltaX;
  wheel.dy += event.deltaY;
  if (wheel.timer) return;
  wheel.timer = setTimeout(() => {
    const { dx, dy } = wheel;
    wheel = { dx: 0, dy: 0, timer: null };
    if (dx || dy) input({ action: 'scroll', dx, dy });
  }, 90);
}, { passive: false });

$('takeover-button').addEventListener('click', () => {
  control('take_over').then(apply).catch((error) => setStatus(error.message, true));
});

$('resume-button').addEventListener('click', () => {
  control('resume').then(apply).catch((error) => setStatus(error.message, true));
});

$('cancel').addEventListener('click', () => {
  const callId = state?.activeCalls?.[0];
  if (!callId) return;
  control('cancel', { callId }).then(apply).catch((error) => setStatus(error.message, true));
});
