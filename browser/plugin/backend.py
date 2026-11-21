"""Playwright browser backend owned by the adapter.

Every Playwright object is created and used on one dedicated thread. The sync
API is not thread-safe, and the adapter runs tool calls on a worker pool.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:  # activation still works; tabs.open reports the missing runtime
    class _MissingPlaywright(Exception):
        pass

    PlaywrightError = _MissingPlaywright
    PlaywrightTimeoutError = _MissingPlaywright
    sync_playwright = None
    PLAYWRIGHT_AVAILABLE = False

SNAPSHOT_JS = """
(arg) => {
  const budget = (arg && arg.budget) || 400;
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none'
      && style.visibility !== 'hidden' && Number(style.opacity || '1') > 0;
  };
  const ROLES = {a: 'link', button: 'button', input: 'textbox', textarea: 'textbox', select: 'combobox', summary: 'button'};
  const nodes = document.querySelectorAll('a[href],button,input,textarea,select,summary,[role],[contenteditable="true"],[tabindex]:not([tabindex="-1"])');
  const refs = [];
  const lines = [];
  for (const el of nodes) {
    if (refs.length >= budget) break;
    if (!visible(el)) continue;
    const ref = 'e' + (refs.length + 1);
    el.setAttribute('data-gewu-ref', ref);
    refs.push(ref);
    const tag = el.tagName.toLowerCase();
    const role = (el.getAttribute('role') || ROLES[tag] || (el.isContentEditable ? 'textbox' : tag)).trim();
    const name = (el.getAttribute('aria-label') || el.getAttribute('alt') || el.getAttribute('placeholder')
      || (tag === 'input' ? el.value : '') || el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 120);
    const state = [];
    if (el.disabled) state.push('disabled');
    if (el.checked) state.push('checked');
    if (tag === 'input' || tag === 'textarea' || tag === 'select') state.push('value=' + JSON.stringify(String(el.value).slice(0, 60)));
    lines.push(ref + ' ' + role + ' ' + JSON.stringify(name) + (state.length ? ' [' + state.join(' ') + ']' : ''));
  }
  const body = (document.body ? document.body.innerText : '').replace(/\\s+/g, ' ').trim().slice(0, 2000);
  return {
    url: location.href,
    title: document.title,
    refs: refs,
    elements: refs.length,
    content: lines.join('\\n') + (body ? '\\n\\nTEXT: ' + body : ''),
    truncated: refs.length >= budget,
  };
}
"""


class BrowserError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def payload(self) -> dict:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


@dataclass
class Tab:
    id: str
    page: object
    owned: bool
    generation: int = 0
    snapshot_generation: int = -1
    refs: set[str] = field(default_factory=set)
    cached_url: str = "about:blank"
    cached_title: str = ""

    def public(self) -> dict:
        """Cached metadata only; safe to read while a long tool call is running."""
        return {
            "id": self.id,
            "url": self.cached_url,
            "title": self.cached_title,
            "generation": self.generation,
            "loading": False,
        }

    def refresh(self) -> None:
        """Runs on the Playwright thread."""
        try:
            self.cached_url = self.page.url
            self.cached_title = self.page.title()
        except PlaywrightError:
            pass


class _PlaywrightThread(threading.Thread):
    """Owns sync_playwright and runs one job at a time."""

    def __init__(self):
        super().__init__(name="gewu-browser-playwright", daemon=True)
        self.jobs: queue.Queue = queue.Queue()
        self.ready = threading.Event()
        self.error: BaseException | None = None
        self.playwright = None

    def run(self) -> None:
        try:
            self.playwright = sync_playwright().start()
        except BaseException as error:  # pragma: no cover - environment failure
            self.error = error
            self.ready.set()
            return
        self.ready.set()
        while True:
            job, box, done = self.jobs.get()
            if job is None:
                break
            try:
                box.append(("ok", job(self.playwright)))
            except BaseException as error:  # noqa: BLE001 - forwarded to caller
                box.append(("error", error))
            finally:
                done.set()
        try:
            self.playwright.stop()
        except BaseException:  # noqa: BLE001 - shutdown best effort
            pass

    def call(self, job):
        if not self.is_alive():
            raise BrowserError("BROWSER_UNAVAILABLE", "The browser worker is not running")
        box: list = []
        done = threading.Event()
        self.jobs.put((job, box, done))
        done.wait()
        kind, value = box[0]
        if kind == "error":
            raise value
        return value

    def stop(self) -> None:
        if not self.is_alive():
            return
        box: list = []
        done = threading.Event()
        self.jobs.put((None, box, done))
        done.wait(timeout=10)
        self.join(timeout=10)


class PlaywrightBackend:
    def __init__(self, settings: dict):
        self.mode = settings.get("mode", "managed")
        self.headless = bool(settings.get("headless", True))
        self.max_tabs = int(settings.get("maxTabs", 8))
        self.allow_evaluate = bool(settings.get("allowEvaluate", False))
        self.adopt_existing = bool(settings.get("adoptExistingTabs", False))
        self.timeout_ms = int(settings.get("timeoutMs", 15000))
        cdp = settings.get("cdpEndpoint")
        if self.mode == "cdp" and not cdp:
            raise BrowserError("INVALID_SETTINGS", "mode 'cdp' requires cdpEndpoint")
        self.cdp_endpoint = cdp
        self._thread: _PlaywrightThread | None = None
        self._tabs: dict[str, Tab] = {}
        self._state_lock = threading.RLock()
        self._browser = None
        self._context = None
        self._closed = False

    @property
    def started(self) -> bool:
        return self._thread is not None and self._browser is not None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if not PLAYWRIGHT_AVAILABLE:
            raise BrowserError(
                "RUNTIME_UNAVAILABLE",
                "Playwright is not installed in this runtime; install the packed GeWu Browser runtime",
            )
        if self.started:
            return
        if self._thread is not None:
            raise BrowserError("BROWSER_UNAVAILABLE", "The browser is still shutting down")
        thread = _PlaywrightThread()
        thread.start()
        thread.ready.wait(timeout=30)
        if thread.error is not None:
            raise BrowserError("BROWSER_UNAVAILABLE", "Playwright could not start", retryable=False)
        self._thread = thread
        try:
            thread.call(self._launch)
        except BaseException:
            try:
                thread.call(self._shutdown)
            except BaseException:  # noqa: BLE001 - cleanup best effort
                pass
            self._thread = None
            thread.stop()
            raise

    def _launch(self, _playwright):
        if self.mode == "cdp":
            self._browser = _playwright.chromium.connect_over_cdp(self.cdp_endpoint)
            if self._browser.contexts:
                self._context = self._browser.contexts[0]
            else:
                self._context = self._browser.new_context()
            if self.adopt_existing:
                for page in self._context.pages:
                    self._adopt(page)
        else:
            self._browser = _playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-dev-shm-usage",
                    "--no-first-run",
                    "--disable-blink-features=AutomationControlled",
                    "--lang=en-US",
                ],
            )
            # A default headless Playwright UA is rejected by some sites (NCBI
            # returns 403). Use a normal Chrome fingerprint for managed mode.
            # CDP mode keeps the user's own context untouched.
            self._context = self._browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                timezone_id="America/New_York",
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )
            self._context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
        return True

    def _adopt(self, page) -> Tab:
        tab_id = "tab_" + str(uuid.uuid4())
        tab = Tab(id=tab_id, page=page, owned=False)
        page.on("framenavigated", lambda frame, t=tab: self._navigated(t, frame))
        page.on("close", lambda _page, t=tab: self._forget(t.id))
        tab.refresh()
        with self._state_lock:
            self._tabs[tab_id] = tab
        return tab

    def _forget(self, tab_id: str) -> None:
        with self._state_lock:
            self._tabs.pop(tab_id, None)

    def _navigated(self, tab: Tab, frame) -> None:
        try:
            if frame == tab.page.main_frame:
                with self._state_lock:
                    tab.generation += 1
                    tab.refs.clear()
                    tab.snapshot_generation = -1
                    tab.cached_url = tab.page.url
        except PlaywrightError:
            pass

    def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._thread is None:
            return
        try:
            self._thread.call(self._shutdown)
        finally:
            self._thread.stop()
            self._thread = None
            self._browser = None
            self._context = None
            with self._state_lock:
                self._tabs.clear()

    def _shutdown(self, _playwright):
        with self._state_lock:
            tabs = list(self._tabs.values())
        for tab in tabs:
            if tab.owned:
                try:
                    tab.page.close()
                except PlaywrightError:
                    pass
        with self._state_lock:
            self._tabs.clear()
        if self.mode == "managed" and self._browser is not None:
            try:
                self._browser.close()
            except PlaywrightError:
                pass
        # A CDP browser belongs to the user; disconnect only.
        return True

    # -- helpers -----------------------------------------------------------

    def _run(self, job):
        if self._thread is None:
            raise BrowserError("BROWSER_DISCONNECTED", "The browser is not started")
        try:
            return self._thread.call(job)
        except BrowserError:
            raise
        except PlaywrightTimeoutError as error:
            raise BrowserError("TIMED_OUT", f"Browser operation timed out: {error}", retryable=True) from error
        except PlaywrightError as error:
            raise BrowserError("BROWSER_ERROR", str(error), retryable=False) from error

    def _tab(self, tab_id: str) -> Tab:
        with self._state_lock:
            tab = self._tabs.get(tab_id)
        if tab is None:
            raise BrowserError(
                "TAB_UNAVAILABLE",
                f"Unknown tab {tab_id!r}; call tabs.list and use an exact returned tab id",
            )
        if tab.page.is_closed():
            self._forget(tab_id)
            raise BrowserError("TAB_UNAVAILABLE", f"Tab {tab_id!r} is closed")
        return tab

    def _locator(self, tab: Tab, ref: str):
        ref = ref.removeprefix("@")
        if tab.generation != tab.snapshot_generation or ref not in tab.refs:
            raise BrowserError(
                "STALE_REF",
                f"Element ref {ref!r} is from an older snapshot; call page.snapshot again",
            )
        locator = tab.page.locator(f'[data-gewu-ref="{ref}"]').first
        if locator.count() == 0:
            raise BrowserError(
                "STALE_REF",
                f"Element ref {ref!r} is no longer on the page; call page.snapshot again",
            )
        return locator

    @staticmethod
    def _public_url(url: str) -> str:
        url = (url or "").strip()
        if url in ("", "about:blank"):
            return "about:blank"
        if not url.lower().startswith(("http://", "https://")):
            raise BrowserError(
                "INVALID_URL",
                "Only http, https and about:blank can be opened; use a file resource for local files",
            )
        return url

    # -- operations --------------------------------------------------------

    def _inventory(self) -> dict:
        with self._state_lock:
            return {"tabs": [tab.public() for tab in self._tabs.values()], "focusedTabId": None}

    def list_tabs(self) -> dict:
        if not self.started:
            return {"tabs": [], "focusedTabId": None}
        return self._inventory()

    def state(self) -> dict:
        if not self.started:
            return {"connected": False, "tabs": [], "focusedTabId": None}
        # Reads the cache, so a long page.wait cannot freeze the Preview.
        return {"connected": True, **self._inventory()}

    def open_tab(self, url: str | None, focus: bool = True) -> dict:
        target = self._public_url(url or "about:blank")
        if not self.started:
            self.start()

        def job(_pw):
            if len(self._tabs) >= self.max_tabs:
                raise BrowserError(
                    "TOO_MANY_TABS",
                    f"This session already has {self.max_tabs} tabs; close one before opening another",
                )
            page = self._context.new_page()
            tab = Tab(id="tab_" + str(uuid.uuid4()), page=page, owned=True)
            page.on("framenavigated", lambda frame, t=tab: self._navigated(t, frame))
            page.on("close", lambda _page, t=tab: self._forget(t.id))
            with self._state_lock:
                self._tabs[tab.id] = tab
            if target != "about:blank":
                page.goto(target, wait_until="domcontentloaded", timeout=self.timeout_ms)
            tab.refresh()
            result = tab.public()
            result["focused"] = bool(focus)
            return result

        return self._run(job)

    def focus_tab(self, tab_id: str) -> dict:
        # Selection is session state; do not queue behind a running page tool.
        with self._state_lock:
            tab = self._tabs.get(tab_id)
        if tab is None:
            raise BrowserError("TAB_UNAVAILABLE", f"Unknown tab {tab_id!r}")
        return tab.public()

    def close_tab(self, tab_id: str) -> dict:
        def job(_pw):
            tab = self._tab(tab_id)
            if tab.owned:
                try:
                    tab.page.close()
                except PlaywrightError:
                    pass
            self._forget(tab_id)
            return True

        self._run(job)
        return self._inventory()

    def navigate(self, tab_id: str, url: str) -> dict:
        target = self._public_url(url)

        def job(_pw):
            tab = self._tab(tab_id)
            tab.refs.clear()
            tab.snapshot_generation = -1
            tab.page.goto(target, wait_until="domcontentloaded", timeout=self.timeout_ms)
            tab.refresh()
            return tab.public()

        return self._run(job)

    def snapshot(self, tab_id: str, budget: int = 400) -> dict:
        budget = max(1, min(int(budget), 1000))

        def job(_pw):
            tab = self._tab(tab_id)
            data = tab.page.evaluate(SNAPSHOT_JS, {"budget": budget})
            with self._state_lock:
                tab.refs = set(data.get("refs", []))
                tab.snapshot_generation = tab.generation
            tab.refresh()
            return {
                "tabId": tab.id,
                "generation": tab.generation,
                "url": data["url"],
                "title": data["title"],
                "elements": data["elements"],
                "content": data["content"],
                "truncated": data["truncated"],
            }

        return self._run(job)

    def click(self, tab_id: str, ref: str, button: str = "left", count: int = 1) -> dict:
        if button not in ("left", "right", "middle"):
            raise BrowserError("INVALID_ARGUMENT", "button must be left, right or middle")
        count = 1 if int(count) not in (1, 2) else int(count)

        def job(_pw):
            tab = self._tab(tab_id)
            self._locator(tab, ref).click(
                button=button, click_count=count, timeout=self.timeout_ms
            )
            try:
                tab.page.wait_for_load_state("domcontentloaded", timeout=2000)
            except PlaywrightTimeoutError:
                pass
            tab.refresh()
            return tab.public()

        return self._run(job)

    def fill(self, tab_id: str, ref: str, text: str) -> dict:
        def job(_pw):
            tab = self._tab(tab_id)
            self._locator(tab, ref).fill(str(text), timeout=self.timeout_ms)
            tab.refresh()
            return tab.public()

        return self._run(job)

    def select(self, tab_id: str, ref: str, values: list[str]) -> dict:
        def job(_pw):
            tab = self._tab(tab_id)
            self._locator(tab, ref).select_option([str(value) for value in values], timeout=self.timeout_ms)
            tab.refresh()
            return tab.public()

        return self._run(job)

    def press(self, tab_id: str, key: str, ref: str | None = None) -> dict:
        def job(_pw):
            tab = self._tab(tab_id)
            if ref:
                self._locator(tab, ref).press(str(key), timeout=self.timeout_ms)
            else:
                tab.page.keyboard.press(str(key))
            try:
                tab.page.wait_for_load_state("domcontentloaded", timeout=1000)
            except PlaywrightTimeoutError:
                pass
            tab.refresh()
            return tab.public()

        return self._run(job)

    def wait(self, tab_id: str, condition: str, text: str | None, timeout_ms: int, cancel=None) -> dict:
        if condition not in ("load", "text", "textGone"):
            raise BrowserError("INVALID_ARGUMENT", "condition must be load, text or textGone")
        if condition != "load" and not text:
            raise BrowserError("INVALID_ARGUMENT", f"{condition} requires literal text")
        timeout_ms = max(1, min(int(timeout_ms), 30000))

        def job(_pw):
            tab = self._tab(tab_id)
            deadline = time.monotonic() + timeout_ms / 1000
            while time.monotonic() < deadline:
                if cancel is not None and cancel.is_set():
                    raise BrowserError("CANCELLED", "The wait was stopped")
                if condition == "load":
                    try:
                        tab.page.wait_for_load_state("load", timeout=250)
                        tab.refresh()
                        return tab.public()
                    except PlaywrightTimeoutError:
                        pass
                else:
                    try:
                        body = tab.page.locator("body").inner_text(timeout=1000)
                    except PlaywrightTimeoutError:
                        body = ""
                    found = bool(text) and text in body
                    if found == (condition == "text"):
                        tab.refresh()
                        return tab.public()
                time.sleep(0.1)
            raise BrowserError(
                "TIMED_OUT",
                f"The {condition} condition did not become true within {timeout_ms} ms; "
                "take page.snapshot to inspect the current page",
                retryable=True,
            )

        return self._run(job)

    def screenshot(self, tab_id: str, full_page: bool = False) -> dict:
        def job(_pw):
            tab = self._tab(tab_id)
            image = tab.page.screenshot(full_page=bool(full_page), type="png", timeout=self.timeout_ms)
            if len(image) > 8 * 1024 * 1024:
                raise BrowserError("SCREENSHOT_TOO_LARGE", "Screenshot exceeds 8 MiB")
            tab.refresh()
            return {
                "tabId": tab.id,
                "generation": tab.generation,
                "url": tab.cached_url,
                "title": tab.cached_title,
                "image": image,
            }

        return self._run(job)

    def frame(self, tab_id: str, quality: int = 60) -> dict:
        quality = max(20, min(int(quality), 90))

        def job(_pw):
            tab = self._tab(tab_id)
            image = tab.page.screenshot(type="jpeg", quality=quality, timeout=self.timeout_ms)
            if len(image) > 8 * 1024 * 1024:
                raise BrowserError("FRAME_TOO_LARGE", "Live frame exceeds 8 MiB")
            size = tab.page.evaluate("() => ({width: innerWidth, height: innerHeight})")
            tab.refresh()
            return {
                "tabId": tab.id,
                "generation": tab.generation,
                "url": tab.cached_url,
                "title": tab.cached_title,
                "width": int(size.get("width") or 0),
                "height": int(size.get("height") or 0),
                "image": image,
            }

        return self._run(job)

    def click_at(
        self,
        tab_id: str,
        x: float,
        y: float,
        button: str = "left",
        click_count: int = 1,
        modifiers: list[str] | None = None,
    ) -> dict:
        if button not in ("left", "right", "middle"):
            raise BrowserError("INVALID_ARGUMENT", "button must be left, right or middle")
        click_count = 2 if int(click_count) == 2 else 1
        mods = [name for name in (modifiers or []) if name in ("Alt", "Control", "Meta", "Shift")]
        x, y = float(x), float(y)
        if not (0 <= x <= 20000 and 0 <= y <= 20000):
            raise BrowserError("INVALID_ARGUMENT", "click coordinates are out of range")

        def job(_pw):
            tab = self._tab(tab_id)
            for name in mods:
                tab.page.keyboard.down(name)
            try:
                tab.page.mouse.click(x, y, button=button, click_count=click_count)
            finally:
                for name in reversed(mods):
                    tab.page.keyboard.up(name)
            try:
                tab.page.wait_for_load_state("domcontentloaded", timeout=1000)
            except PlaywrightTimeoutError:
                pass
            tab.refresh()
            return tab.public()

        return self._run(job)

    def mouse_move(self, tab_id: str, x: float, y: float) -> dict:
        x, y = float(x), float(y)
        if not (0 <= x <= 20000 and 0 <= y <= 20000):
            raise BrowserError("INVALID_ARGUMENT", "pointer coordinates are out of range")

        def job(_pw):
            tab = self._tab(tab_id)
            tab.page.mouse.move(x, y)
            return tab.public()

        return self._run(job)

    def mouse_down(self, tab_id: str, x: float, y: float, button: str = "left") -> dict:
        if button not in ("left", "right", "middle"):
            raise BrowserError("INVALID_ARGUMENT", "button must be left, right or middle")
        x, y = float(x), float(y)
        if not (0 <= x <= 20000 and 0 <= y <= 20000):
            raise BrowserError("INVALID_ARGUMENT", "pointer coordinates are out of range")

        def job(_pw):
            tab = self._tab(tab_id)
            tab.page.mouse.move(x, y)
            tab.page.mouse.down(button=button)
            return tab.public()

        return self._run(job)

    def mouse_up(self, tab_id: str, x: float, y: float, button: str = "left") -> dict:
        if button not in ("left", "right", "middle"):
            raise BrowserError("INVALID_ARGUMENT", "button must be left, right or middle")
        x, y = float(x), float(y)
        if not (0 <= x <= 20000 and 0 <= y <= 20000):
            raise BrowserError("INVALID_ARGUMENT", "pointer coordinates are out of range")

        def job(_pw):
            tab = self._tab(tab_id)
            tab.page.mouse.move(x, y)
            tab.page.mouse.up(button=button)
            tab.refresh()
            return tab.public()

        return self._run(job)

    def type_text(self, tab_id: str, text: str) -> dict:
        text = str(text)
        if len(text) > 2000:
            raise BrowserError("INVALID_ARGUMENT", "typed text must be at most 2000 characters")

        def job(_pw):
            tab = self._tab(tab_id)
            tab.page.keyboard.type(text)
            tab.refresh()
            return tab.public()

        return self._run(job)

    def key(self, tab_id: str, key: str) -> dict:
        key = str(key)
        if not key or len(key) > 64:
            raise BrowserError("INVALID_ARGUMENT", "key must be between 1 and 64 characters")

        def job(_pw):
            tab = self._tab(tab_id)
            tab.page.keyboard.press(key)
            try:
                tab.page.wait_for_load_state("domcontentloaded", timeout=1000)
            except PlaywrightTimeoutError:
                pass
            tab.refresh()
            return tab.public()

        return self._run(job)

    def wheel(self, tab_id: str, dx: float, dy: float) -> dict:
        dx, dy = float(dx), float(dy)
        if not (-10000 <= dx <= 10000 and -10000 <= dy <= 10000):
            raise BrowserError("INVALID_ARGUMENT", "scroll delta is out of range")

        def job(_pw):
            tab = self._tab(tab_id)
            tab.page.mouse.wheel(dx, dy)
            return tab.public()

        return self._run(job)

    def evaluate(self, tab_id: str, script: str):
        if not self.allow_evaluate:
            raise BrowserError(
                "PERMISSION_REQUIRED",
                "page.evaluate is disabled; enable allowEvaluate only for trusted sessions",
            )
        if len(script) > 4000:
            raise BrowserError("INVALID_ARGUMENT", "script must be at most 4000 characters")

        def job(_pw):
            tab = self._tab(tab_id)
            value = tab.page.evaluate(script)
            try:
                json.dumps(value)
            except TypeError as error:
                raise BrowserError("INVALID_RESULT", "The evaluated value is not JSON-serializable") from error
            tab.refresh()
            return {
                "tabId": tab.id,
                "generation": tab.generation,
                "url": tab.cached_url,
                "title": tab.cached_title,
                "value": value,
            }

        return self._run(job)
