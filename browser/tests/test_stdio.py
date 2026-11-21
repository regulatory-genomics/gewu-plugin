import http.server
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import unittest
import urllib.request
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory

PLUGIN = Path(__file__).resolve().parents[1] / "plugin"

INDEX = """<!doctype html><html><head><title>Stdio fixture</title></head><body>
<h1>Stdio fixture</h1>
<input id="name" placeholder="Name">
<button id="save" onclick="document.getElementById('out').textContent='Saved: '+document.getElementById('name').value">Save</button>
<p id="out"></p><a href="/second.html">Second</a>
</body></html>"""
SECOND = "<!doctype html><html><head><title>Second</title></head><body><h1>Second page</h1></body></html>"


class Reader:
    def __init__(self, stream):
        self.stream = stream
        self.frames = []
        self.pending = {}

    def read(self, timeout=45):
        result = queue.Queue()

        def line():
            result.put(self.stream.readline())

        thread = threading.Thread(target=line, daemon=True)
        thread.start()
        try:
            value = result.get(timeout=timeout)
        except queue.Empty as error:
            raise AssertionError("timed out waiting for adapter output") from error
        if not value:
            raise AssertionError("adapter exited without a reply")
        frame = json.loads(value)
        self.frames.append(frame)
        return frame

    def result(self, request_id, timeout=45):
        if request_id in self.pending:
            return self.pending.pop(request_id)
        while True:
            frame = self.read(timeout)
            if frame.get("type") not in ("result", "error"):
                continue
            if frame.get("id") == request_id:
                return frame
            self.pending[frame["id"]] = frame


class StdioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import playwright  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("Playwright is not installed")
        cls.temp = TemporaryDirectory()
        root = Path(cls.temp.name)
        (root / "index.html").write_text(INDEX, encoding="utf-8")
        (root / "second.html").write_text(SECOND, encoding="utf-8")
        handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        port = cls.server.server_address[1]
        cls.url = f"http://127.0.0.1:{port}/index.html"
        cls.second = f"http://127.0.0.1:{port}/second.html"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def setUp(self):
        self.workspace = TemporaryDirectory()
        env = {
            **os.environ,
            "GEWU_BROWSER_PYTHON": sys.executable,
        }
        self.process = subprocess.Popen(
            [str(PLUGIN / "bin" / "browser-gewu")],
            cwd=self.workspace.name,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        self.reader = Reader(self.process.stdout)
        self.next_id = 0

    def tearDown(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.communicate(timeout=10)
        self.workspace.cleanup()

    def send(self, op, params):
        self.next_id += 1
        request_id = f"r{self.next_id}"
        frame = {"id": request_id, "op": op, "params": params}
        self.process.stdin.write(json.dumps(frame) + "\n")
        self.process.stdin.flush()
        return request_id

    def call(self, tool, args, call_id, **extra):
        context = {
            "instanceId": "fixture",
            "generation": 1,
            "workspaceId": "fixture",
            "callId": call_id,
            **extra,
        }
        request_id = self.send("call", {"tool": tool, "args": args, "context": context})
        return self.reader.result(request_id)

    def test_the_stdio_path_drives_chromium_and_the_preview_service(self):
        request_id = self.send(
            "activate",
            {
                "apiVersion": "gewu.plugin/v1",
                "instanceId": "fixture",
                "generation": 1,
                "workspaceId": "fixture",
                "workspaceRoot": self.workspace.name,
                "settings": {"mode": "managed", "headless": True, "timeoutMs": 8000},
            },
        )
        activated = self.reader.result(request_id)["result"]
        endpoint = activated["serviceEndpoints"][0]
        token = endpoint["headers"]["x-gewu-browser"]
        base = endpoint["baseUrl"]

        opened = self.call("tabs.open", {"url": self.url}, "open")["result"]
        self.assertTrue(opened["ok"], opened)
        tab_id = opened["data"]["id"]

        snapshot = self.call("page.snapshot", {"tabId": tab_id}, "snapshot")["result"]
        self.assertTrue(snapshot["ok"], snapshot)
        content = snapshot["data"]["content"]
        name = next(line.split(" ", 1)[0] for line in content.splitlines() if '"Name"' in line)
        save = next(line.split(" ", 1)[0] for line in content.splitlines() if '"Save"' in line)

        self.assertTrue(self.call("page.fill", {"tabId": tab_id, "ref": name, "text": "hello"}, "fill")["result"]["ok"])
        self.assertTrue(self.call("page.click", {"tabId": tab_id, "ref": save}, "click")["result"]["ok"])
        waited = self.call("page.wait", {"tabId": tab_id, "condition": "text", "text": "Saved: hello"}, "wait")["result"]
        self.assertTrue(waited["ok"], waited)

        shot = self.call("page.screenshot", {"tabId": tab_id}, "shot")["result"]
        self.assertTrue(shot["ok"], shot)
        resource = shot["data"]["resource"]
        path = Path(self.workspace.name) / resource["uri"].removeprefix("workspace:///")
        self.assertTrue(path.is_file())
        self.assertTrue(path.read_bytes().startswith(b"\x89PNG"))

        with urllib.request.urlopen(
            urllib.request.Request(
                base + "/preview/state",
                headers={"x-gewu-browser": token},
            ),
            timeout=10,
        ) as response:
            state = json.loads(response.read())["data"]
        self.assertTrue(state["connected"])
        self.assertEqual(state["mode"], "managed")
        self.assertEqual(len(state["tabs"]), 1)
        self.assertEqual(state["lastScreenshot"]["revision"], resource["revision"])
        self.assertEqual(state["lastScreenshotTab"], tab_id)
        self.assertEqual(state["lastScreenshotGeneration"], 1)

        with urllib.request.urlopen(
            urllib.request.Request(
                base + "/preview/control",
                method="POST",
                data=json.dumps({"action": "take_over"}).encode(),
                headers={"x-gewu-browser": token, "content-type": "application/json"},
            ),
            timeout=10,
        ) as response:
            self.assertTrue(json.loads(response.read())["data"]["takeover"])
        blocked = self.call("page.click", {"tabId": tab_id, "ref": save}, "blocked")["result"]
        self.assertEqual(blocked["error"]["code"], "PERMISSION_REQUIRED")

        # User actions from the bound view stay allowed during takeover.
        resumed_request = self.send(
            "call",
            {
                "tool": "page.click",
                "args": {"tabId": tab_id, "ref": save},
                "context": {
                    "instanceId": "fixture",
                    "generation": 1,
                    "workspaceId": "fixture",
                    "callId": "user",
                    "viewId": "view",
                    "viewEpoch": 1,
                },
            },
        )
        allowed = self.reader.result(resumed_request)["result"]
        self.assertTrue(allowed["ok"], allowed)
        with urllib.request.urlopen(
            urllib.request.Request(
                base + "/preview/control",
                method="POST",
                data=json.dumps({"action": "resume"}).encode(),
                headers={"x-gewu-browser": token, "content-type": "application/json"},
            ),
            timeout=10,
        ) as response:
            self.assertFalse(json.loads(response.read())["data"]["takeover"])

        self.assertTrue(self.call("page.navigate", {"tabId": tab_id, "url": self.second}, "nav")["result"]["ok"])
        stale = self.call("page.click", {"tabId": tab_id, "ref": save}, "stale")["result"]
        self.assertEqual(stale["error"]["code"], "STALE_REF")

        # Cancellation arrives through the adapter protocol, not an HTTP shortcut.
        wait_request = self.send(
            "call",
            {
                "tool": "page.wait",
                "args": {"tabId": tab_id, "condition": "text", "text": "never", "timeoutMs": 10000},
                "context": {
                    "instanceId": "fixture",
                    "generation": 1,
                    "workspaceId": "fixture",
                    "callId": "slow",
                },
            },
        )
        time.sleep(0.5)
        cancel_request = self.send("cancel", {"callId": "slow"})
        cancelled = self.reader.result(wait_request)["result"]
        self.assertFalse(cancelled["ok"])
        self.assertEqual(cancelled["error"]["code"], "CANCELLED")
        ack = self.reader.result(cancel_request)["result"]
        self.assertIn(ack["state"], ("requested", "already_settled"))

        stop_request = self.send("deactivate", {"reason": "test complete"})
        self.assertTrue(self.reader.result(stop_request)["result"]["stopped"])
        self.process.wait(timeout=30)
        self.assertEqual(self.process.poll(), 0)


if __name__ == "__main__":
    unittest.main()
