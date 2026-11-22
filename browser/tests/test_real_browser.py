import http.server
import re
import sys
import threading
import time
import unittest
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory

PLUGIN = Path(__file__).resolve().parents[1] / "plugin"
sys.path.insert(0, str(PLUGIN))

from backend import BrowserError, PlaywrightBackend  # noqa: E402

INDEX = """<!doctype html>
<html><head><title>Fixture</title></head>
<body>
<h1>Fixture page</h1>
<label>Name <input id="name" placeholder="Name"></label>
<select id="choice" aria-label="Choice"><option value="a">A</option><option value="b">B</option></select>
<button id="save" type="button"
  onclick="document.getElementById('out').textContent = 'Saved: ' + document.getElementById('name').value">Save</button>
<p id="out"></p>
<a href="/second.html">Second page</a>
</body></html>
"""

SECOND = """<!doctype html>
<html><head><title>Second</title></head><body><h1>Second page</h1></body></html>
"""

CLICK = """<!doctype html>
<html><head><title>Click fixture</title></head>
<body style="margin:0">
<input id="name" style="position:absolute;left:20px;top:20px;width:200px;height:30px">
<button style="position:absolute;left:20px;top:70px;width:140px;height:30px"
  onclick="document.getElementById('out').textContent='Saved: '+document.getElementById('name').value">Save</button>
<p id="out" style="position:absolute;left:20px;top:120px"></p>
<div id="drag" style="position:absolute;left:300px;top:20px;width:200px;height:100px;background:#eee"></div>
<script>
const out = document.getElementById('out');
document.addEventListener('contextmenu', (event) => {
  event.preventDefault();
  out.textContent = 'Right click';
});
document.addEventListener('dblclick', () => { out.textContent = 'Double click'; });
const drag = document.getElementById('drag');
drag.addEventListener('mouseenter', () => { out.textContent = 'Hover'; });
drag.addEventListener('mousedown', () => { out.textContent = 'Drag start'; });
drag.addEventListener('mousemove', (event) => { if (event.buttons) out.textContent = 'Dragging'; });
window.addEventListener('mouseup', () => {
  if (out.textContent === 'Drag start' || out.textContent === 'Dragging') out.textContent = 'Drag end';
});
</script>
</body></html>
"""


def ref_for(content: str, needle: str) -> str:
    for line in content.splitlines():
        if needle in line and re.match(r"^e[1-9][0-9]* ", line):
            return line.split(" ", 1)[0]
    raise AssertionError(f"no ref for {needle!r} in:\n{content}")


class RealBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        root = Path(cls.temp.name)
        (root / "index.html").write_text(INDEX, encoding="utf-8")
        (root / "second.html").write_text(SECOND, encoding="utf-8")
        (root / "click.html").write_text(CLICK, encoding="utf-8")
        handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/index.html"
        cls.second = f"http://127.0.0.1:{cls.server.server_address[1]}/second.html"
        cls.click_url = f"http://127.0.0.1:{cls.server.server_address[1]}/click.html"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def setUp(self):
        self.backend = PlaywrightBackend(
            {"mode": "managed", "headless": True, "timeoutMs": 8000, "allowEvaluate": False}
        )
        self.backend.start()
        self.addCleanup(self.backend.stop)

    def test_snapshot_fill_click_wait_select_and_screenshot(self):
        tab = self.backend.open_tab(self.url)
        self.assertEqual(tab["title"], "Fixture")

        snapshot = self.backend.snapshot(tab["id"])
        self.assertIn("Fixture page", snapshot["content"])
        self.assertGreaterEqual(snapshot["elements"], 4)
        name = ref_for(snapshot["content"], '"Name"')
        save = ref_for(snapshot["content"], '"Save"')
        choice = ref_for(snapshot["content"], '"Choice"')

        self.backend.fill(tab["id"], name, "hello")
        self.backend.select(tab["id"], choice, ["b"])
        self.backend.click(tab["id"], "@" + save)
        self.backend.wait(tab["id"], "text", "Saved: hello", 5000)

        snapshot = self.backend.snapshot(tab["id"])
        self.assertIn('Saved: hello', snapshot["content"])
        self.assertIn('value="b"', snapshot["content"])

        capture = self.backend.screenshot(tab["id"])
        self.assertTrue(capture["image"].startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(capture["image"]), 1000)
        self.assertEqual(capture["generation"], self.backend.state()["tabs"][0]["generation"])

    def test_live_frame_click_type_and_scroll(self):
        tab = self.backend.open_tab(self.click_url)
        frame = self.backend.frame(tab["id"])
        self.assertTrue(frame["image"].startswith(b"\xff\xd8"))
        self.assertGreater(frame["width"], 0)
        self.assertGreater(frame["height"], 0)

        self.backend.click_at(tab["id"], 100, 35)
        self.backend.type_text(tab["id"], "hello")
        self.backend.click_at(tab["id"], 80, 85)
        self.backend.wait(tab["id"], "text", "Saved: hello", 5000)
        self.backend.wheel(tab["id"], 0, 100)
        self.backend.key(tab["id"], "Enter")
        self.assertTrue(self.backend.frame(tab["id"])["image"].startswith(b"\xff\xd8"))

        with self.assertRaises(BrowserError) as caught:
            self.backend.click_at(tab["id"], -1, 0)
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(BrowserError) as caught:
            self.backend.type_text(tab["id"], "x" * 2001)
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_live_frame_right_click_double_hover_and_drag(self):
        tab = self.backend.open_tab(self.click_url)
        self.backend.click_at(tab["id"], 100, 35, "right")
        self.backend.wait(tab["id"], "text", "Right click", 5000)
        self.backend.click_at(tab["id"], 100, 35, "left", 2)
        self.backend.wait(tab["id"], "text", "Double click", 5000)
        self.backend.mouse_move(tab["id"], 400, 60)
        self.backend.wait(tab["id"], "text", "Hover", 5000)
        self.backend.mouse_down(tab["id"], 350, 60)
        self.backend.mouse_move(tab["id"], 400, 80)
        self.backend.mouse_up(tab["id"], 400, 80)
        self.backend.wait(tab["id"], "text", "Drag end", 5000)

    def test_wait_requires_text_for_text_conditions(self):
        tab = self.backend.open_tab(self.url)
        with self.assertRaises(BrowserError) as caught:
            self.backend.wait(tab["id"], "text", None, 1000)
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_navigation_invalidates_old_refs(self):
        tab = self.backend.open_tab(self.url)
        snapshot = self.backend.snapshot(tab["id"])
        save = ref_for(snapshot["content"], '"Save"')

        self.backend.navigate(tab["id"], self.second)
        with self.assertRaises(BrowserError) as caught:
            self.backend.click(tab["id"], "@" + save)
        self.assertEqual(caught.exception.code, "STALE_REF")

        snapshot = self.backend.snapshot(tab["id"])
        self.assertIn("Second page", snapshot["content"])
        self.assertTrue(snapshot["generation"] > 0)

    def test_wait_is_cancellable_and_leave_no_pending_job(self):
        tab = self.backend.open_tab(self.url)
        cancel = threading.Event()
        result = {}

        def run():
            try:
                self.backend.wait(tab["id"], "text", "never appears", 10000, cancel)
            except BrowserError as error:
                result["code"] = error.code

        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(0.3)
        cancel.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result["code"], "CANCELLED")
        # The worker is responsive again after cancellation.
        self.backend.snapshot(tab["id"])

    def test_state_stays_responsive_during_a_long_wait(self):
        tab = self.backend.open_tab(self.url)
        cancel = threading.Event()

        def run():
            try:
                self.backend.wait(tab["id"], "text", "never", 8000, cancel)
            except BrowserError:
                pass

        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(0.2)
        started = time.monotonic()
        state = self.backend.state()
        elapsed = time.monotonic() - started
        cancel.set()
        thread.join(5)
        self.assertLess(elapsed, 0.5, "Preview state must not queue behind a running page tool")
        self.assertEqual(state["tabs"][0]["title"], "Fixture")
        self.assertEqual(state["tabs"][0]["url"], self.url)

    def test_evaluate_is_denied_by_default(self):
        tab = self.backend.open_tab(self.url)
        with self.assertRaises(BrowserError) as caught:
            self.backend.evaluate(tab["id"], "1 + 1")
        self.assertEqual(caught.exception.code, "PERMISSION_REQUIRED")

    def test_evaluate_can_be_enabled_explicitly(self):
        backend = PlaywrightBackend(
            {"mode": "managed", "headless": True, "timeoutMs": 8000, "allowEvaluate": True}
        )
        backend.start()
        try:
            tab = backend.open_tab(self.url)
            self.assertEqual(backend.evaluate(tab["id"], "1 + 1")["value"], 2)
        finally:
            backend.stop()

    def test_unknown_tab_and_unsupported_url_are_typed(self):
        with self.assertRaises(BrowserError) as caught:
            self.backend.focus_tab("tab_missing")
        self.assertEqual(caught.exception.code, "TAB_UNAVAILABLE")
        with self.assertRaises(BrowserError) as caught:
            self.backend.open_tab("file:///etc/passwd")
        self.assertEqual(caught.exception.code, "INVALID_URL")

    def test_stop_disconnects_the_browser(self):
        self.backend.open_tab(self.url)
        self.assertTrue(self.backend.started)
        self.backend.stop()
        self.assertFalse(self.backend.started)
        self.assertEqual(self.backend.list_tabs(), {"tabs": [], "focusedTabId": None})

    def test_closed_context_is_reported_and_next_open_recovers(self):
        tab = self.backend.open_tab(self.url)
        self.backend._thread.call(lambda _pw: self.backend._context.close())
        self.assertFalse(self.backend.state()["connected"])
        self.assertEqual(self.backend.list_tabs()["tabs"], [])
        recovered = self.backend.open_tab(self.url)
        self.assertNotEqual(recovered["id"], tab["id"])
        self.assertIn("Fixture page", self.backend.snapshot(recovered["id"])["content"])
        with self.assertRaises(BrowserError) as caught:
            self.backend.snapshot(tab["id"])
        self.assertEqual(caught.exception.code, "TAB_UNAVAILABLE")

    def test_browser_disconnect_is_reported_and_next_open_recovers(self):
        self.backend.open_tab(self.url)
        self.backend._thread.call(lambda _pw: self.backend._browser.close())
        self.assertFalse(self.backend.state()["connected"])
        tab = self.backend.open_tab(self.url)
        self.assertIn("Fixture page", self.backend.snapshot(tab["id"])["content"])

    def test_closed_backend_cannot_leak_a_restarted_browser(self):
        self.backend.stop()
        with self.assertRaises(BrowserError) as caught:
            self.backend.open_tab(self.url)
        self.assertEqual(caught.exception.code, "BROWSER_DISCONNECTED")


class CdpModeTest(unittest.TestCase):
    def test_cdp_requires_an_endpoint(self):
        with self.assertRaises(BrowserError) as caught:
            PlaywrightBackend({"mode": "cdp"})
        self.assertEqual(caught.exception.code, "INVALID_SETTINGS")


if __name__ == "__main__":
    unittest.main()
