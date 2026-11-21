"""GeWu stdio adapter for a managed Chromium browser.

The adapter owns the browser, the tool contract and a loopback Preview service.
The Preview webview never receives the service token or any browser credential;
GeWu injects the token on its service proxy.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))

from gewu_plugin import ProtocolError, serve  # noqa: E402

import backend as backend_module  # noqa: E402

SERVICE_ID = "preview"
MAX_BODY = 16 * 1024


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def failure(code: str, message: str, retryable: bool = False) -> dict:
    return {
        "ok": False,
        "error": {"code": code, "message": message, "retryable": bool(retryable)},
        "resources": [],
    }


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:  # keep stderr clean for adapter logs
        pass

    @property
    def adapter(self) -> "Adapter":
        return self.server.adapter  # type: ignore[attr-defined]

    def _send(self, status: int, value: dict) -> None:
        data = json.dumps(value, ensure_ascii=True, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_bytes(self, status: int, content_type: str, data: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if data:
            self.wfile.write(data)

    def _authorized(self) -> bool:
        presented = self.headers.get("x-gewu-browser", "")
        return hmac.compare_digest(presented, self.adapter.token)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._authorized():
            return self._send(403, failure("FORBIDDEN", "Preview service token is missing"))
        if self.path == "/preview/state":
            return self._send(200, self.adapter.preview_state())
        if self.path == "/preview/frame":
            frame = self.adapter.frame_payload()
            if frame is None:
                return self._send_bytes(204, "text/plain", b"")
            return self._send_bytes(200, frame[1], frame[0])
        if self.path == "/preview/health":
            return self._send(200, {"ok": True, "data": {"status": "ok"}})
        return self._send(404, failure("NOT_FOUND", "Unknown preview route"))

    def _read_body(self) -> bytes:
        # GeWu streams the proxied body, so a POST may be chunked with no
        # content-length. BaseHTTPRequestHandler does not decode that itself.
        if "chunked" in self.headers.get("transfer-encoding", "").lower():
            data = bytearray()
            while True:
                line = self.rfile.readline(128).strip()
                if not line:
                    break
                try:
                    size = int(line.split(b";", 1)[0], 16)
                except ValueError as error:
                    raise ValueError("invalid chunk size") from error
                if size == 0:
                    while self.rfile.readline(128).strip():
                        pass
                    break
                if len(data) + size > MAX_BODY:
                    raise ValueError("body too large")
                data.extend(self.rfile.read(size))
                self.rfile.read(2)
            return bytes(data)
        try:
            length = int(self.headers.get("content-length") or 0)
        except ValueError as error:
            raise ValueError("invalid content length") from error
        if length < 0 or length > MAX_BODY:
            raise ValueError("body too large")
        return self.rfile.read(length)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._authorized():
            return self._send(403, failure("FORBIDDEN", "Preview service token is missing"))
        if self.path not in ("/preview/control", "/preview/input"):
            return self._send(404, failure("NOT_FOUND", "Unknown preview route"))
        try:
            raw = self._read_body()
        except ValueError:
            return self._send(413, failure("BODY_TOO_LARGE", "Control request is too large"))
        try:
            body = json.loads(raw or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._send(400, failure("INVALID_JSON", "Control request is not JSON"))
        if not isinstance(body, dict):
            return self._send(400, failure("INVALID_JSON", "Control request must be an object"))
        if self.path == "/preview/input":
            status, value = self.adapter.input_action(body)
        else:
            status, value = self.adapter.control(body)
        return self._send(status, value)


class Adapter:
    def __init__(self, backend_factory=None):
        self.backend_factory = backend_factory or backend_module.PlaywrightBackend
        self.lock = threading.RLock()
        self.backend_lock = threading.Lock()
        self.settings: dict = {}
        self.root: Path | None = None
        self.tools: list[dict] = []
        self.effects: dict[str, str] = {}
        self.backend = None
        self.takeover = False
        self.focused: str | None = None
        self.last_action: dict | None = None
        self.last_screenshot: dict | None = None
        self.last_screenshot_tab: str | None = None
        self.last_screenshot_generation: int | None = None
        self.active: dict[str, object] = {}
        self.token = secrets.token_urlsafe(24)
        self.service: ThreadingHTTPServer | None = None
        self.service_thread: threading.Thread | None = None

    # -- protocol ----------------------------------------------------------

    def activate(self, params: dict) -> dict:
        settings = params.get("settings") or {}
        mode = settings.get("mode", "managed")
        if mode not in ("managed", "cdp"):
            raise ProtocolError("INVALID_SETTINGS", "mode must be managed or cdp")
        if mode == "cdp" and not settings.get("cdpEndpoint"):
            raise ProtocolError("INVALID_SETTINGS", "mode cdp requires cdpEndpoint")
        max_tabs = settings.get("maxTabs", 8)
        if not isinstance(max_tabs, int) or not 1 <= max_tabs <= 16:
            raise ProtocolError("INVALID_SETTINGS", "maxTabs must be between 1 and 16")
        timeout_ms = settings.get("timeoutMs", 15000)
        if not isinstance(timeout_ms, int) or not 1000 <= timeout_ms <= 60000:
            raise ProtocolError("INVALID_SETTINGS", "timeoutMs must be between 1000 and 60000")
        self.root = Path(params["workspaceRoot"]).resolve(strict=True)
        self.settings = {
            "mode": mode,
            "headless": bool(settings.get("headless", True)),
            "maxTabs": max_tabs,
            "allowEvaluate": bool(settings.get("allowEvaluate", False)),
            "adoptExistingTabs": bool(settings.get("adoptExistingTabs", False)),
            "timeoutMs": timeout_ms,
        }
        if mode == "cdp":
            self.settings["cdpEndpoint"] = settings["cdpEndpoint"]
        self.tools = json.loads((HERE / "tools.schema.json").read_text(encoding="utf-8"))
        self.effects = {tool["name"]: tool["effect"] for tool in self.tools}
        self.start_service()
        return {
            "tools": self.tools,
            "serviceEndpoints": [
                {
                    "id": SERVICE_ID,
                    "baseUrl": f"http://127.0.0.1:{self.service.server_address[1]}",
                    "protocol": "http",
                    "headers": {"x-gewu-browser": self.token},
                    "routes": [
                        {"pathPrefix": "/preview", "methods": ["GET", "POST"]},
                    ],
                }
            ],
        }

    def start_service(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.daemon_threads = True
        server.adapter = self  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, name="gewu-browser-preview", daemon=True)
        thread.start()
        self.service = server
        self.service_thread = thread

    def stop_service(self) -> None:
        if self.service is not None:
            self.service.shutdown()
            self.service.server_close()
            self.service = None
            self.service_thread = None

    def deactivate(self, params: dict) -> dict:
        with self.backend_lock:
            if self.backend is not None:
                try:
                    self.backend.stop()
                except Exception as error:  # noqa: BLE001 - shutdown continues
                    print(f"browser backend stop failed: {type(error).__name__}", file=sys.stderr)
                self.backend = None
        self.stop_service()
        return {"stopped": True}

    # -- tools -------------------------------------------------------------

    def ensure_backend(self):
        with self.backend_lock:
            if self.backend is None:
                try:
                    backend = self.backend_factory(self.settings)
                    backend.start()
                except backend_module.BrowserError:
                    raise
                except Exception as error:  # noqa: BLE001 - environment failure
                    raise backend_module.BrowserError(
                        "BROWSER_UNAVAILABLE",
                        "Chromium could not start; check the packed runtime and browser installation",
                    ) from error
                self.backend = backend
            return self.backend

    def call(self, tool: str, args: dict, context: dict, control) -> dict:
        call_id = context.get("callId", "")
        with self.lock:
            self.active[call_id] = control
        started = time.monotonic()
        try:
            if tool in ("view.attach", "view.detach"):
                data = {
                    "mode": self.settings.get("mode"),
                    "serviceId": SERVICE_ID,
                    "takeover": self.takeover,
                }
                return {"ok": True, "data": data, "resources": []}
            if tool not in self.effects:
                return failure("UNKNOWN_TOOL", f"Unknown browser tool {tool!r}")
            if (
                self.takeover
                and self.effects[tool] == "write"
                and not context.get("viewId")
            ):
                return failure(
                    "PERMISSION_REQUIRED",
                    "The user has taken control of the browser. Wait until they choose Resume agent "
                    "in the Browser Preview before running another write operation.",
                )
            data, resources = self.dispatch(tool, args, control, context)
            self.record(tool, "succeeded", started)
            return {"ok": True, "data": data, "resources": resources}
        except backend_module.BrowserError as error:
            self.record(tool, "failed", started, error.code)
            return failure(error.code, error.message, error.retryable)
        except ProtocolError:
            raise
        except Exception as error:  # noqa: BLE001 - never leak a trace to the agent
            print(f"browser tool {tool} failed: {type(error).__name__}", file=sys.stderr)
            self.record(tool, "failed", started, "ADAPTER_FAILURE")
            return failure("ADAPTER_FAILURE", "The browser adapter failed to complete this call")
        finally:
            with self.lock:
                self.active.pop(call_id, None)

    def dispatch(self, tool: str, args: dict, control, context: dict):
        if tool == "tabs.list":
            state = self.backend.state() if self.backend and self.backend.started else {
                "connected": False,
                "tabs": [],
                "focusedTabId": None,
            }
            return state, []
        if tool == "browser.state":
            return self.state_data(exclude=context.get("callId")), []
        if tool == "tabs.open":
            backend = self.ensure_backend()
            tab = backend.open_tab(args.get("url"), bool(args.get("focus", True)))
            with self.lock:
                self.focused = tab["id"]
            return tab, []
        if tool == "tabs.focus":
            tab = self.require_backend().focus_tab(args["tabId"])
            with self.lock:
                self.focused = tab["id"]
            return tab, []
        if tool == "tabs.close":
            state = self.require_backend().close_tab(args["tabId"])
            with self.lock:
                if self.focused == args["tabId"]:
                    self.focused = None
                if self.last_screenshot_tab == args["tabId"]:
                    self.last_screenshot = None
                    self.last_screenshot_tab = None
                    self.last_screenshot_generation = None
            return state, []
        if tool == "page.navigate":
            tab = self.require_backend().navigate(args["tabId"], args["url"])
            with self.lock:
                if self.last_screenshot_tab == args["tabId"]:
                    self.last_screenshot = None
                    self.last_screenshot_tab = None
                    self.last_screenshot_generation = None
            return tab, []
        if tool == "page.snapshot":
            return self.require_backend().snapshot(args["tabId"], int(args.get("budget", 400))), []
        if tool == "page.click":
            return self.require_backend().click(
                args["tabId"],
                args["ref"],
                args.get("button", "left"),
                int(args.get("count", 1)),
            ), []
        if tool == "page.fill":
            return self.require_backend().fill(args["tabId"], args["ref"], args["text"]), []
        if tool == "page.select":
            return self.require_backend().select(args["tabId"], args["ref"], args["values"]), []
        if tool == "page.press":
            return self.require_backend().press(args["tabId"], args["key"], args.get("ref")), []
        if tool == "page.wait":
            return self.require_backend().wait(
                args["tabId"],
                args["condition"],
                args.get("text"),
                int(args.get("timeoutMs", 10000)),
                control.cancelled,
            ), []
        if tool == "page.screenshot":
            capture = self.require_backend().screenshot(args["tabId"], bool(args.get("fullPage", False)))
            resource = self.save_screenshot(capture)
            with self.lock:
                self.last_screenshot = resource
                self.last_screenshot_tab = capture["tabId"]
                self.last_screenshot_generation = capture["generation"]
                self.focused = capture["tabId"]
            return {
                "tabId": capture["tabId"],
                "generation": capture["generation"],
                "url": capture["url"],
                "title": capture["title"],
                "resource": resource,
            }, [resource]
        if tool == "page.evaluate":
            return self.require_backend().evaluate(args["tabId"], args["script"]), []
        raise backend_module.BrowserError("UNKNOWN_TOOL", f"Unknown browser tool {tool!r}")

    def require_backend(self):
        if self.backend is None or not self.backend.started:
            raise backend_module.BrowserError(
                "BROWSER_DISCONNECTED",
                "No browser is running; call tabs.open to start one",
            )
        return self.backend

    def save_screenshot(self, capture: dict) -> dict:
        directory = self.root / ".gewu-browser" / capture["tabId"]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "latest.png"
        temporary = directory / "latest.png.tmp"
        temporary.write_bytes(capture["image"])
        os.replace(temporary, path)
        digest = hashlib.sha256(capture["image"]).hexdigest()[:16]
        relative = path.relative_to(self.root).as_posix()
        return {
            "uri": "workspace:///" + quote(relative, safe="/"),
            "type": "image",
            "mimeType": "image/png",
            "revision": digest,
        }

    def record(
        self,
        tool: str,
        status: str,
        started: float,
        code: str | None = None,
        detail: dict | None = None,
    ) -> None:
        entry = {
            "tool": tool,
            "status": status,
            "at": now_iso(),
            "durationMs": int((time.monotonic() - started) * 1000),
        }
        if code:
            entry["code"] = code
        if detail:
            entry["detail"] = detail
        with self.lock:
            self.last_action = entry

    def active_tab_id(self) -> str | None:
        backend = self.backend
        if backend is None or not backend.started:
            return None
        with self.lock:
            focused = self.focused
        tabs = backend.list_tabs().get("tabs", [])
        if focused and any(tab["id"] == focused for tab in tabs):
            return focused
        return tabs[0]["id"] if tabs else None

    def frame_payload(self):
        tab_id = self.active_tab_id()
        if tab_id is None:
            return None
        try:
            frame = self.backend.frame(tab_id)
        except backend_module.BrowserError:
            return None
        except Exception as error:  # noqa: BLE001 - the stream must not kill the service
            print(f"live frame failed: {type(error).__name__}", file=sys.stderr)
            return None
        return frame["image"], "image/jpeg"

    def input_action(self, body: dict):
        action = body.get("action")
        started = time.monotonic()
        tab_id = body.get("tabId") or self.active_tab_id()
        if not tab_id:
            return 409, failure("TAB_UNAVAILABLE", "No browser tab is open")
        try:
            backend = self.require_backend()
            numeric = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool)
            detail = None
            if action == "click":
                x, y = body.get("x"), body.get("y")
                if not numeric(x) or not numeric(y):
                    return 400, failure("INVALID_ARGUMENT", "click requires numeric x and y")
                button = body.get("button", "left")
                count = body.get("count", 1)
                if button not in ("left", "right", "middle") or count not in (1, 2):
                    return 400, failure("INVALID_ARGUMENT", "invalid click button or count")
                modifiers = body.get("modifiers", [])
                if not isinstance(modifiers, list) or not all(
                    name in ("Alt", "Control", "Meta", "Shift") for name in modifiers
                ):
                    return 400, failure("INVALID_ARGUMENT", "invalid click modifiers")
                backend.click_at(tab_id, x, y, button, count, modifiers)
                detail = {"button": button, "count": count}
            elif action in ("down", "up", "move"):
                x, y = body.get("x"), body.get("y")
                if not numeric(x) or not numeric(y):
                    return 400, failure("INVALID_ARGUMENT", f"{action} requires numeric x and y")
                button = body.get("button", "left")
                if button not in ("left", "right", "middle"):
                    return 400, failure("INVALID_ARGUMENT", "invalid mouse button")
                if action == "down":
                    backend.mouse_down(tab_id, x, y, button)
                elif action == "up":
                    backend.mouse_up(tab_id, x, y, button)
                else:
                    backend.mouse_move(tab_id, x, y)
                detail = {"button": button}
            elif action == "type":
                text = body.get("text")
                if not isinstance(text, str):
                    return 400, failure("INVALID_ARGUMENT", "text is required")
                backend.type_text(tab_id, text)
            elif action == "key":
                key = body.get("key")
                if not isinstance(key, str):
                    return 400, failure("INVALID_ARGUMENT", "key is required")
                backend.key(tab_id, key)
            elif action == "scroll":
                dx, dy = body.get("dx", 0), body.get("dy", 0)
                if not numeric(dx) or not numeric(dy):
                    return 400, failure("INVALID_ARGUMENT", "scroll requires numeric dx and dy")
                backend.wheel(tab_id, dx, dy)
            elif action == "navigate":
                url = body.get("url")
                if not isinstance(url, str):
                    return 400, failure("INVALID_ARGUMENT", "url is required")
                backend.navigate(tab_id, url)
                with self.lock:
                    self.focused = tab_id
            else:
                return 400, failure("INVALID_ARGUMENT", "Unknown preview input action")
            # Hover moves are frequent; do not turn them into status noise.
            if action != "move":
                self.record(f"preview.{action}", "succeeded", started, detail=detail)
            return 200, self.preview_state()
        except (TypeError, ValueError):
            return 400, failure("INVALID_ARGUMENT", "Invalid preview input")
        except backend_module.BrowserError as error:
            self.record(f"preview.{action}", "failed", started, error.code)
            return 409, failure(error.code, error.message, error.retryable)
        except Exception as error:  # noqa: BLE001 - never close the service on a bad input
            print(f"preview input failed: {type(error).__name__}", file=sys.stderr)
            return 500, failure("ADAPTER_FAILURE", "The browser input failed")

    def state_data(self, exclude: str | None = None) -> dict:
        backend = self.backend
        base = (
            backend.state()
            if backend is not None and backend.started
            else {"connected": False, "tabs": [], "focusedTabId": None}
        )
        with self.lock:
            base["mode"] = self.settings.get("mode")
            base["takeover"] = self.takeover
            base["focusedTabId"] = self.focused or base.get("focusedTabId")
            base["lastAction"] = self.last_action
            base["activeCalls"] = sorted(call for call in self.active if call != exclude)
        return base

    # -- preview service ---------------------------------------------------

    def preview_state(self) -> dict:
        data = self.state_data()
        with self.lock:
            data["lastScreenshot"] = self.last_screenshot
            data["lastScreenshotTab"] = self.last_screenshot_tab
            data["lastScreenshotGeneration"] = self.last_screenshot_generation
        return {"ok": True, "data": data}

    def control(self, body: dict):
        action = body.get("action")
        if action == "take_over":
            with self.lock:
                self.takeover = True
            return 200, self.preview_state()
        if action == "resume":
            with self.lock:
                self.takeover = False
            return 200, self.preview_state()
        if action == "focus":
            tab_id = body.get("tabId")
            if not isinstance(tab_id, str) or not tab_id:
                return 400, failure("INVALID_ARGUMENT", "tabId is required")
            try:
                tab = self.require_backend().focus_tab(tab_id)
            except backend_module.BrowserError as error:
                return 409, failure(error.code, error.message)
            with self.lock:
                self.focused = tab["id"]
            return 200, self.preview_state()
        if action == "cancel":
            call_id = body.get("callId")
            if not isinstance(call_id, str) or not call_id:
                return 400, failure("INVALID_ARGUMENT", "callId is required")
            with self.lock:
                control = self.active.get(call_id)
            if control is None:
                return 404, failure("CALL_NOT_FOUND", "That call is no longer running")
            control.cancelled.set()
            return 200, self.preview_state()
        return 400, failure("INVALID_ARGUMENT", "Unknown preview action")


if __name__ == "__main__":
    adapter = Adapter()
    serve(activate=adapter.activate, call=adapter.call, deactivate=adapter.deactivate)
