"""The vault's engine for the native apps: the same vault as the web server, reached over a private pipe.

    python -m backend.engine [any option of backend.api.app]

A native app (apps/macos) starts this as a hidden helper and talks to it over stdin/stdout, one JSON object per
line, so nothing listens on the network for the app itself. (Phone access, on by default, still listens on its own
HTTPS port: a phone can only reach the computer over the network.)

    app -> engine   {"id": 7, "method": "GET", "path": "/api/documents", "headers": {"cookie": "vault_session=…"}}
                    {"id": 8, "method": "POST", "path": "/api/chat", "json": {"question": "…"}}
                    {"id": 9, "method": "POST", "path": "/api/documents", "form": {"category": "auto"},
                     "files": [{"field": "files", "path": "/Users/me/Desktop/Bill.pdf"}]}
    engine -> app   {"event": "ready"} once the vault is open, or {"event": "failed", "message": "…"}
                    {"id": 7, "status": 200, "headers": {…}, "json": …}            an answer in JSON
                    {"id": 7, "status": 200, "headers": {…}, "base64": "…"}        anything else (page images, files)
                    {"id": 8, "status": 200, "headers": {…}, "stream": true}, then {"id": 8, "chunk": "<one line>"}
                    for each line of a streamed answer, then {"id": 8, "end": true}

Requests run concurrently and answers carry their id. Each request goes straight into the vault's own FastAPI app
through its ASGI interface, so the native apps behave exactly like the web app: same sign-in (the app keeps the
session cookie, like a browser), same checks, same activity log. The engine stops when the app closes the pipe.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import threading
from urllib.parse import urlsplit

STREAMED = ("application/x-ndjson", "text/event-stream")


class Pipe:
    """The engine's side of the pipe: whole JSON lines out, never interleaved."""

    def __init__(self, out):
        self.out, self.lock = out, threading.Lock()

    def send(self, message: dict) -> None:
        line = json.dumps(message, separators=(",", ":"))
        with self.lock:
            self.out.write(line + "\n")
            self.out.flush()


def _body(request: dict) -> tuple[bytes, dict]:
    """The request body and the headers it needs (JSON, or a multipart upload of files named by path)."""
    if request.get("files"):
        import httpx

        files = [(f.get("field", "files"), (os.path.basename(f["path"]), open(f["path"], "rb"))) for f in request["files"]]
        try:
            built = httpx.Request("POST", "http://engine/", data=request.get("form") or {}, files=files)
            return built.read(), {"content-type": built.headers["content-type"]}
        finally:
            for _, (_, handle) in files:
                handle.close()
    if "json" in request:
        return json.dumps(request["json"]).encode(), {"content-type": "application/json"}
    return b"", {}


async def handle(app, request: dict, pipe: Pipe) -> None:
    """Run one request through the app and send back its answer (or the lines of a streamed one)."""
    rid = request.get("id")
    try:
        body, extra = _body(request)
    except OSError as e:
        pipe.send({"id": rid, "status": 400, "headers": {}, "json": {"detail": f"Can't read the file: {e.strerror or e}"}})
        return
    target = urlsplit(request.get("path", "/"))
    headers = {"host": "localhost", **{k.lower(): str(v) for k, v in (request.get("headers") or {}).items()}, **extra}
    if body:
        headers["content-length"] = str(len(body))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": request.get("method", "GET").upper(),
        "scheme": "http", "path": target.path, "raw_path": target.path.encode(), "query_string": target.query.encode(),
        "root_path": "", "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
        "client": ("127.0.0.1", 0), "server": ("localhost", 80),
    }
    done = asyncio.Event()
    delivered = False
    state = {"status": 500, "headers": {}, "stream": False, "buffer": b"", "pending": b""}

    async def receive() -> dict:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await done.wait()  # the app (a streamed answer) waits here for a disconnect that only comes at the end
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        if message["type"] == "http.response.start":
            state["status"] = message["status"]
            for k, v in message.get("headers", []):
                key = k.decode("latin-1").lower()
                value = v.decode("latin-1")
                state["headers"][key] = f"{state['headers'][key]}, {value}" if key in state["headers"] else value
            state["stream"] = state["headers"].get("content-type", "").split(";")[0].strip() in STREAMED
            if state["stream"]:
                pipe.send({"id": rid, "status": state["status"], "headers": state["headers"], "stream": True})
        elif message["type"] == "http.response.body":
            chunk = message.get("body", b"")
            if state["stream"]:
                state["pending"] += chunk
                *lines, state["pending"] = state["pending"].split(b"\n")
                for line in lines:
                    if line.strip():
                        pipe.send({"id": rid, "chunk": line.decode("utf-8", "replace")})
            else:
                state["buffer"] += chunk

    try:
        await app(scope, receive, send)
    except Exception as e:  # noqa: BLE001 (one failed request must not stop the engine)
        pipe.send({"id": rid, "status": 500, "headers": {}, "json": {"detail": f"{type(e).__name__}: {e}"}})
        return
    finally:
        done.set()
    if state["stream"]:
        if state["pending"].strip():
            pipe.send({"id": rid, "chunk": state["pending"].decode("utf-8", "replace")})
        pipe.send({"id": rid, "end": True})
        return
    answer = {"id": rid, "status": state["status"], "headers": state["headers"]}
    if state["headers"].get("content-type", "").startswith("application/json"):
        answer["json"] = json.loads(state["buffer"] or b"null")
    else:
        answer["base64"] = base64.b64encode(state["buffer"]).decode("ascii")
    pipe.send(answer)


def serve(app, pipe: Pipe, lines) -> None:
    """Answer requests read from `lines` (the app's side of the pipe) until it closes."""
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, name="engine", daemon=True).start()
    pending = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            pipe.send({"event": "error", "message": "A request wasn't valid JSON."})
            continue
        pending.append(asyncio.run_coroutine_threadsafe(handle(app, request, pipe), loop))
    for future in pending:  # the app closed the pipe: finish what is running, then stop
        try:
            future.result(timeout=30)
        except Exception:  # noqa: BLE001
            pass
    loop.call_soon_threadsafe(loop.stop)


def engine_arguments(argv: list[str]) -> list[str]:
    """The vault's options for the apps' engine: phone access on, and the local AI downloaded by itself (a computer
    that keeps the vault needs it to answer), unless VAULT_AI_AUTO_DOWNLOAD=0 (the automatic tests)."""
    argv = list(argv)
    if os.environ.get("VAULT_AI_AUTO_DOWNLOAD", "1").lower() not in ("0", "off", "no", "false") and "--download-ai" not in argv:
        argv.append("--download-ai")
    if "--no-phones" in argv:
        argv.remove("--no-phones")
    elif "--phones" not in argv:
        argv.append("--phones")
    return argv


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    pipe = Pipe(os.fdopen(os.dup(1), "w", encoding="utf-8"))  # the pipe keeps the real stdout…
    os.dup2(2, 1)  # …and anything else printed goes to stderr (the app's log), never into the pipe
    sys.stdout = sys.stderr
    # The same for the requests: a private copy of stdin, and stdin itself pointed at nothing. On Windows, while one
    # thread waits to read a pipe, anything else touching that pipe (a library checking stdin as it loads, in the
    # search model's background thread) freezes until the next request arrives, which may be never.
    requests = os.fdopen(os.dup(0), "r", encoding="utf-8")
    nothing = os.open(os.devnull, os.O_RDONLY)
    os.dup2(nothing, 0)
    os.close(nothing)
    sys.stdin = open(os.devnull, encoding="utf-8")
    from backend.api.app import parse_args, start_vault

    argv = engine_arguments(argv)
    try:
        app, stop = start_vault(parse_args(argv))
    except SystemExit as e:  # the vault explains startup problems this way (no key, database unreachable…)
        pipe.send({"event": "failed", "message": str(e.code)})
        raise SystemExit(1) from e
    except Exception as e:  # noqa: BLE001
        pipe.send({"event": "failed", "message": f"{type(e).__name__}: {e}"})
        raise SystemExit(1) from e
    pipe.send({"event": "ready"})
    try:
        serve(app, pipe, requests)
    finally:
        stop()
