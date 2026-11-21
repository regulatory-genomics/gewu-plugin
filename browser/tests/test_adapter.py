import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

PLUGIN = Path(__file__).resolve().parents[1] / "plugin"
sys.path.insert(0, str(PLUGIN))

import adapter as adapter_module  # noqa: E402
from backend import BrowserError  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class FakeBackend:
    instances = []

    def __init__(self, settings):
        self.settings = settings
        self._started = False
        self.stopped = False
        self.calls = []
        FakeBackend.instances.append(self)

    @property
    def started(self):
        return self._started

    def start(self):
        self._started = True

    def stop(self):
        self._started = False
        self.stopped = True

    def state(self):
        return {"connected": True, "tabs": [], "focusedTabId": None}

    def list_tabs(self):
        return {
            "tabs": [{"id": "tab_1", "url": "about:blank", "title": "Fake", "generation": 1}],
            "focusedTabId": None,
        }

    def open_tab(self, url, focus=True):
        self.calls.append(("open_tab", url))
        return {"id": "tab_1", "url": url or "about:blank", "title": "Fake", "generation": 1, "focused": focus}

    def focus_tab(self, tab_id):
        if tab_id != "tab_1":
            raise BrowserError("TAB_UNAVAILABLE", f"Unknown tab {tab_id!r}")
        return {"id": tab_id, "url": "about:blank", "title": "Fake", "generation": 1}

    def close_tab(self, tab_id):
        return {"tabs": [], "focusedTabId": None}

    def navigate(self, tab_id, url):
        return {"id": tab_id, "url": url, "title": "Fake", "generation": 2}

    def snapshot(self, tab_id, budget=400):
        return {
            "tabId": tab_id,
            "generation": 1,
            "url": "about:blank",
            "title": "Fake",
            "elements": 1,
            "content": 'e1 button "Save"',
            "truncated": False,
        }

    def click(self, tab_id, ref, button="left", count=1):
        self.calls.append(("click", ref))
        return {"id": tab_id, "url": "about:blank", "title": "Fake", "generation": 1}

    def fill(self, tab_id, ref, text):
        return {"id": tab_id, "url": "about:blank", "title": "Fake", "generation": 1}

    def select(self, tab_id, ref, values):
        return {"id": tab_id, "url": "about:blank", "title": "Fake", "generation": 1}

    def press(self, tab_id, key, ref=None):
        return {"id": tab_id, "url": "about:blank", "title": "Fake", "generation": 1}

    def wait(self, tab_id, condition, text, timeout_ms, cancel=None):
        while cancel is not None and not cancel.is_set():
            time.sleep(0.01)
        raise BrowserError("CANCELLED", "The wait was stopped")

    def screenshot(self, tab_id, full_page=False):
        return {"tabId": tab_id, "generation": 1, "url": "about:blank", "title": "Fake", "image": PNG}

    def frame(self, tab_id, quality=60):
        return {
            "tabId": tab_id, "generation": 1, "url": "about:blank", "title": "Fake",
            "width": 1280, "height": 900, "image": b"\xff\xd8\xff\xe0" + b"0" * 64,
        }

    def click_at(self, tab_id, x, y, button="left", click_count=1, modifiers=None):
        self.calls.append(("click_at", x, y, button, click_count, tuple(modifiers or ())))
        return {"id": tab_id, "url": "about:blank", "title": "Fake", "generation": 1}

    def mouse_move(self, tab_id, x, y):
        self.calls.append(("mouse_move", x, y))
        return {"id": tab_id, "url": "about:blank", "title": "Fake", "generation": 1}

    def mouse_down(self, tab_id, x, y, button="left"):
        self.calls.append(("mouse_down", x, y, button))
        return {"id": tab_id, "url": "about:blank", "title": "Fake", "generation": 1}

    def mouse_up(self, tab_id, x, y, button="left"):
        self.calls.append(("mouse_up", x, y, button))
        return {"id": tab_id, "url": "about:blank", "title": "Fake", "generation": 1}

    def type_text(self, tab_id, text):
        self.calls.append(("type_text", text))
        return {"id": tab_id, "url": "about:blank", "title": "Fake", "generation": 1}

    def key(self, tab_id, key):
        self.calls.append(("key", key))
        return {"id": tab_id, "url": "about:blank", "title": "Fake", "generation": 1}

    def wheel(self, tab_id, dx, dy):
        self.calls.append(("wheel", dx, dy))
        return {"id": tab_id, "url": "about:blank", "title": "Fake", "generation": 1}

    def evaluate(self, tab_id, script):
        return {"tabId": tab_id, "generation": 1, "url": "about:blank", "title": "Fake", "value": None}


class Control:
    def __init__(self):
        self.cancelled = threading.Event()


def activate(root: Path, settings=None):
    instance = adapter_module.Adapter(backend_factory=FakeBackend)
    params = {
        "apiVersion": "gewu.plugin/v1",
        "instanceId": "i",
        "generation": 1,
        "workspaceId": "w",
        "workspaceRoot": str(root),
        "settings": {"mode": "managed", **(settings or {})},
    }
    activated = instance.activate(params)
    return instance, activated


def request_raw(url, token=None):
    headers = {"x-gewu-browser": token} if token is not None else {}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status, response.headers.get_content_type(), response.read()


def request(url, token=None, method="GET", body=None):
    headers = {"content-type": "application/json"}
    if token is not None:
        headers["x-gewu-browser"] = token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


class AdapterTest(unittest.TestCase):
    def setUp(self):
        FakeBackend.instances.clear()
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_activate_exposes_tools_and_a_tokenized_preview_service(self):
        instance, activated = activate(self.root)
        names = {tool["name"] for tool in activated["tools"]}
        for expected in ("tabs.list", "tabs.open", "page.snapshot", "page.click", "page.screenshot"):
            self.assertIn(expected, names)
        self.assertNotIn("view.attach", {tool["name"] for tool in activated["tools"] if tool["exposeToAgent"]})
        endpoint = activated["serviceEndpoints"][0]
        self.assertEqual(endpoint["id"], "preview")
        self.assertEqual(endpoint["routes"][0]["pathPrefix"], "/preview")
        self.assertTrue(endpoint["headers"]["x-gewu-browser"])
        self.assertFalse(instance.backend)
        instance.deactivate({})

    def test_preview_service_requires_the_activation_token(self):
        instance, activated = activate(self.root)
        base = activated["serviceEndpoints"][0]["baseUrl"]
        token = activated["serviceEndpoints"][0]["headers"]["x-gewu-browser"]
        status, body = request(base + "/preview/state")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "FORBIDDEN")
        status, body = request(base + "/preview/state", token)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertFalse(body["data"]["connected"])
        self.assertEqual(body["data"]["mode"], "managed")
        instance.deactivate({})

    def test_takeover_blocks_agent_writes_but_not_user_actions(self):
        instance, activated = activate(self.root)
        token = activated["serviceEndpoints"][0]["headers"]["x-gewu-browser"]
        base = activated["serviceEndpoints"][0]["baseUrl"]
        instance.call("tabs.open", {"url": "about:blank"}, {"callId": "open"}, Control())
        status, _ = request(base + "/preview/control", token, "POST", {"action": "take_over"})
        self.assertEqual(status, 200)
        control = Control()
        blocked = instance.call(
            "page.click",
            {"tabId": "tab_1", "ref": "e1"},
            {"callId": "agent", "viewId": None},
            control,
        )
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["error"]["code"], "PERMISSION_REQUIRED")
        allowed = instance.call(
            "page.click",
            {"tabId": "tab_1", "ref": "e1"},
            {"callId": "user", "viewId": "view-1"},
            control,
        )
        self.assertTrue(allowed["ok"])
        status, body = request(base + "/preview/control", token, "POST", {"action": "resume"})
        self.assertEqual(status, 200)
        self.assertFalse(body["data"]["takeover"])
        instance.deactivate({})

    def test_cancel_reaches_the_running_call(self):
        instance, activated = activate(self.root)
        token = activated["serviceEndpoints"][0]["headers"]["x-gewu-browser"]
        base = activated["serviceEndpoints"][0]["baseUrl"]
        instance.call("tabs.open", {"url": "about:blank"}, {"callId": "open"}, Control())
        result = {}

        def run():
            result.update(
                instance.call(
                    "page.wait",
                    {"tabId": "tab_1", "condition": "text", "text": "never", "timeoutMs": 10000},
                    {"callId": "call-1"},
                    Control(),
                )
            )

        thread = threading.Thread(target=run)
        thread.start()
        for _ in range(200):
            if "call-1" in instance.state_data()["activeCalls"]:
                break
            time.sleep(0.005)
        status, body = request(base + "/preview/control", token, "POST", {"action": "cancel", "callId": "call-1"})
        self.assertEqual(status, 200)
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "CANCELLED")
        instance.deactivate({})

    def test_screenshot_is_stored_inside_the_workspace(self):
        instance, _ = activate(self.root)
        instance.call("tabs.open", {"url": "about:blank"}, {"callId": "open"}, Control())
        out = instance.call(
            "page.screenshot",
            {"tabId": "tab_1"},
            {"callId": "shot"},
            Control(),
        )
        self.assertTrue(out["ok"])
        resource = out["data"]["resource"]
        self.assertTrue(resource["uri"].startswith("workspace:///.gewu-browser/"))
        self.assertEqual(resource["type"], "image")
        relative = resource["uri"].removeprefix("workspace:///")
        path = self.root / relative
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_bytes(), PNG)
        preview = instance.preview_state()["data"]
        self.assertEqual(preview["lastScreenshot"]["revision"], resource["revision"])
        self.assertEqual(preview["lastScreenshotTab"], "tab_1")
        self.assertEqual(preview["lastScreenshotGeneration"], 1)
        instance.deactivate({})

    def test_live_frame_and_input_go_through_the_tokenized_service(self):
        instance, activated = activate(self.root)
        token = activated["serviceEndpoints"][0]["headers"]["x-gewu-browser"]
        base = activated["serviceEndpoints"][0]["baseUrl"]
        instance.call("tabs.open", {"url": "about:blank"}, {"callId": "open"}, Control())

        status, content_type, data = request_raw(base + "/preview/frame", token)
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "image/jpeg")
        self.assertTrue(data.startswith(b"\xff\xd8"))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            request_raw(base + "/preview/frame")
        self.assertEqual(caught.exception.code, 403)

        status, body = request(base + "/preview/input", token, "POST", {"action": "click", "x": 10, "y": 20})
        self.assertEqual(status, 200)
        self.assertIn(("click_at", 10, 20, "left", 1, ()), instance.backend.calls)
        self.assertEqual(body["data"]["lastAction"]["tool"], "preview.click")
        status, _ = request(
            base + "/preview/input",
            token,
            "POST",
            {"action": "click", "x": 30, "y": 40, "button": "right", "count": 2, "modifiers": ["Shift"]},
        )
        self.assertEqual(status, 200)
        self.assertIn(("click_at", 30, 40, "right", 2, ("Shift",)), instance.backend.calls)
        status, _ = request(base + "/preview/input", token, "POST", {"action": "down", "x": 5, "y": 6})
        self.assertEqual(status, 200)
        self.assertIn(("mouse_down", 5, 6, "left"), instance.backend.calls)
        status, _ = request(base + "/preview/input", token, "POST", {"action": "move", "x": 7, "y": 8})
        self.assertEqual(status, 200)
        status, _ = request(base + "/preview/input", token, "POST", {"action": "up", "x": 7, "y": 8})
        self.assertEqual(status, 200)
        status, _ = request(base + "/preview/input", token, "POST", {"action": "type", "text": "hello"})
        self.assertEqual(status, 200)
        status, _ = request(base + "/preview/input", token, "POST", {"action": "scroll", "dx": 0, "dy": 120})
        self.assertEqual(status, 200)
        status, body = request(base + "/preview/input", token, "POST", {"action": "nope"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "INVALID_ARGUMENT")
        status, body = request(base + "/preview/input", token, "POST", {"action": "click"})
        self.assertEqual(status, 400)
        instance.deactivate({})

    def test_unknown_tool_and_unknown_control_are_typed(self):
        instance, activated = activate(self.root)
        token = activated["serviceEndpoints"][0]["headers"]["x-gewu-browser"]
        base = activated["serviceEndpoints"][0]["baseUrl"]
        out = instance.call("nope", {}, {"callId": "x"}, Control())
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "UNKNOWN_TOOL")
        status, body = request(base + "/preview/control", token, "POST", {"action": "nope"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "INVALID_ARGUMENT")
        instance.deactivate({})

    def test_deactivate_stops_the_backend_and_the_service(self):
        instance, activated = activate(self.root)
        instance.call("tabs.open", {"url": "about:blank"}, {"callId": "open"}, Control())
        backend = instance.backend
        self.assertTrue(backend.started)
        base = activated["serviceEndpoints"][0]["baseUrl"]
        self.assertEqual(instance.deactivate({})["stopped"], True)
        self.assertTrue(backend.stopped)
        with self.assertRaises(urllib.error.URLError):
            request(base + "/preview/state", "x")

    def test_cdp_mode_requires_an_endpoint(self):
        with self.assertRaises(adapter_module.ProtocolError) as caught:
            activate(self.root, {"mode": "cdp"})
        self.assertEqual(caught.exception.payload["code"], "INVALID_SETTINGS")


class ContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_manifest_and_activation_match_the_public_schema(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema is not installed")
        schema_path = Path(__file__).resolve().parents[2] / "GeWu" / "gewu-protocol" / "schema" / "plugin.v1.schema.json"
        if not schema_path.is_file():
            self.skipTest("GeWu protocol schema is not available")
        schema = json.loads(schema_path.read_text())
        manifest = json.loads((PLUGIN / "plugin.json").read_text())
        jsonschema.Draft202012Validator(schema).validate(manifest)
        instance, activated = activate(self.root)
        jsonschema.Draft202012Validator({**schema, "$ref": "#/$defs/Activated"}).validate(activated)
        instance.deactivate({})

    def test_every_tool_schema_is_valid_and_sample_outputs_match(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema is not installed")
        tools = json.loads((PLUGIN / "tools.schema.json").read_text())
        instance, _ = activate(self.root)
        control = Control()
        samples = {
            "tabs.list": {"tabs": [], "focusedTabId": None},
            "tabs.open": {"id": "tab_1", "url": "about:blank", "title": "Fake", "generation": 1},
            "tabs.focus": {"id": "tab_1", "url": "about:blank", "title": "Fake", "generation": 1},
            "tabs.close": {"tabs": [], "focusedTabId": None},
            "page.navigate": {"id": "tab_1", "url": "https://example.com", "title": "Fake", "generation": 2},
            "page.snapshot": {
                "tabId": "tab_1",
                "generation": 1,
                "url": "about:blank",
                "title": "Fake",
                "elements": 1,
                "content": "e1 button \"Save\"",
                "truncated": False,
            },
            "page.click": {"id": "tab_1", "url": "about:blank", "title": "Fake", "generation": 1},
            "page.fill": {"id": "tab_1", "url": "about:blank", "title": "Fake", "generation": 1},
            "page.select": {"id": "tab_1", "url": "about:blank", "title": "Fake", "generation": 1},
            "page.press": {"id": "tab_1", "url": "about:blank", "title": "Fake", "generation": 1},
            "page.wait": {"id": "tab_1", "url": "about:blank", "title": "Fake", "generation": 1},
            "page.screenshot": {
                "tabId": "tab_1",
                "generation": 1,
                "url": "about:blank",
                "title": "Fake",
                "resource": {
                    "uri": "workspace:///.gewu-browser/tab_1/latest.png",
                    "type": "image",
                    "mimeType": "image/png",
                    "revision": "abc",
                },
            },
            "page.evaluate": {"tabId": "tab_1", "generation": 1, "url": "about:blank", "title": "Fake", "value": None},
            "browser.state": {
                "connected": False,
                "mode": "managed",
                "takeover": False,
                "tabs": [],
                "focusedTabId": None,
                "activeCalls": [],
            },
            "view.attach": {},
            "view.detach": {},
        }
        self.assertEqual({tool["name"] for tool in tools}, set(samples))
        for tool in tools:
            for key in ("inputSchema", "outputSchema"):
                jsonschema.Draft202012Validator.check_schema(tool[key])
            jsonschema.Draft202012Validator(tool["outputSchema"]).validate(samples[tool["name"]])
        instance.deactivate({})


if __name__ == "__main__":
    unittest.main()
