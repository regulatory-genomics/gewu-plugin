"""GeWu v1 stdio helper. Adapters own application schemas and native cancellation."""
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

VERSION = "gewu.plugin/v1"
LIMIT = 1024 * 1024


class ProtocolError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.payload = {"code": code, "message": message, "retryable": retryable}


class CallControl:
    def __init__(self, request_id, call_id, send):
        self.request_id = request_id
        self.call_id = call_id
        self.cancelled = threading.Event()
        self._send = send
        self._groups = {}

    def _event(self, **event):
        self._send(type="event", id=self.request_id, event=event)

    def progress(self, message):
        self._event(type="progress", callId=self.call_id, message=message)

    def register_process(self, pid, pgid, timeout=5):
        """Called with a child still at its startup barrier; caller owns cleanup on error."""
        key = (pid, pgid)
        accepted = self._groups[key] = threading.Event()
        try:
            self._event(type="process.started", pid=pid, pgid=pgid)
            if not accepted.wait(timeout):
                raise ProtocolError("PROCESS_NOT_REGISTERED", "Host did not accept the process group")
        finally:
            self._groups.pop(key, None)

    def process_exited(self, pid, pgid):
        self._event(type="process.exited", pid=pid, pgid=pgid)


def serve(*, activate, call, resolve=None, deactivate=None, workers=4):
    """Serve callbacks; call(tool, args, context, control) runs on a worker."""
    lock = threading.RLock()
    active = {}
    pending = set()
    settled = {}
    binding = None
    pool = ThreadPoolExecutor(max_workers=workers)

    def send(**frame):
        data = json.dumps(frame, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()
        if len(data) > LIMIT:
            raise ProtocolError("FRAME_TOO_LARGE", "Result exceeds 1 MiB; return a resource reference instead")
        with lock:
            sys.stdout.buffer.write(data + b"\n")
            sys.stdout.buffer.flush()

    def invoke(rid, callback):
        try:
            send(type="result", id=rid, result=callback())
        except ProtocolError as exc:
            send(type="error", id=rid, error=exc.payload)
        except Exception as exc:
            print(f"Adapter callback raised {type(exc).__name__}", file=sys.stderr)
            send(type="error", id=rid, error={"code": "ADAPTER_FAILURE", "message": "Adapter callback failed", "retryable": False})
        finally:
            with lock:
                pending.discard(rid)

    def run_call(rid, params, control):
        def handler():
            return call(params["tool"], params["args"], params["context"], control)
        try:
            invoke(rid, handler)
        finally:
            with lock:
                active.pop(control.call_id, None)
                settled[control.call_id] = None
                if len(settled) > 256:
                    del settled[next(iter(settled))]

    def require(test, message):
        if not test:
            raise ProtocolError("INVALID_REQUEST", message)

    try:
        while line := sys.stdin.buffer.readline(LIMIT + 2):
            if len(line) > LIMIT + 1 or not line.endswith(b"\n"):
                raise ValueError("invalid host frame length")
            frame = json.loads(line.decode("utf-8"))
            rid = frame["id"]
            if not isinstance(rid, str) or not rid:
                raise ValueError("invalid request id")
            with lock:
                if frame.get("type") == "process.accepted":
                    control = next((item for item in active.values() if item.request_id == rid), None)
                    accepted = control and control._groups.get((frame["pid"], frame["pgid"]))
                    if not accepted:
                        raise ValueError("unexpected process acknowledgement")
                    accepted.set()
                    continue
                if rid in pending:
                    raise ValueError("duplicate pending request id")
                pending.add(rid)
            op, params = frame["op"], frame["params"]
            if op == "activate":
                def start():
                    nonlocal binding
                    require(binding is None and params.get("apiVersion") == VERSION, "Invalid activation or API version")
                    result = activate(params)
                    binding = {key: params[key] for key in ("instanceId", "workspaceId", "generation")}
                    return result
                invoke(rid, start)
            elif binding is None:
                invoke(rid, lambda: require(False, "Activate the adapter first"))
            elif op == "call":
                context = params["context"]
                with lock:
                    cid = context["callId"]
                    if not cid or any(context.get(key) != value for key, value in binding.items()) or cid in active or cid in settled or len(active) >= 256:
                        invoke(rid, lambda: require(False, "Stale, duplicate or over-capacity call"))
                        continue
                    control = active[cid] = CallControl(rid, cid, send)
                pool.submit(run_call, rid, params, control)
            elif op == "cancel":
                with lock:
                    cid = params["callId"]
                    control = active.get(cid)
                    state = "requested" if control else "already_settled" if cid in settled else "not_found"
                    invoke(rid, lambda: {"state": state})
                    if control:
                        control.cancelled.set()
            elif op == "resolve" and resolve:
                pool.submit(invoke, rid, lambda p=params: resolve(p))
            elif op == "deactivate":
                with lock:
                    for control in active.values():
                        control.cancelled.set()
                pool.shutdown(wait=True)
                def stop():
                    if deactivate:
                        deactivate(params)
                    return {"stopped": True}
                invoke(rid, stop)
                return
            else:
                invoke(rid, lambda: require(False, "Unsupported operation"))
    finally:
        with lock:
            for control in active.values():
                control.cancelled.set()
        pool.shutdown(wait=True)
