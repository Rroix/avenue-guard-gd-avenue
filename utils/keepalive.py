from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from aiohttp import web
except Exception:
    web = None

_thread_started = False
_status_lock = threading.Lock()
_status = {
    "service": "Avenue Guard",
    "state": "starting",
    "detail": "Process is starting",
    "updated_ts": int(time.time()),
    "retry_after_seconds": 0,
    "next_retry_ts": 0,
}


def set_keepalive_status(
    state: str,
    detail: str = "",
    *,
    retry_after_seconds: int = 0,
    next_retry_ts: int = 0,
) -> None:
    with _status_lock:
        _status.update(
            {
                "state": str(state or "unknown"),
                "detail": str(detail or ""),
                "updated_ts": int(time.time()),
                "retry_after_seconds": int(retry_after_seconds or 0),
                "next_retry_ts": int(next_retry_ts or 0),
            }
        )


def get_keepalive_status() -> dict:
    with _status_lock:
        return dict(_status)


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        status = get_keepalive_status()
        if self.path.rstrip("/") in {"/status", "/health"}:
            body = json.dumps(status, separators=(",", ":")).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        else:
            body = (
                f"OK\n"
                f"state={status.get('state', 'unknown')}\n"
                f"detail={status.get('detail', '')}\n"
            ).encode("utf-8")
            content_type = "text/plain; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return


def start_keepalive_thread() -> None:
    """Bind Render's health port before Discord login can block startup."""
    global _thread_started
    if _thread_started:
        return
    _thread_started = True
    port = int(os.getenv("PORT", "8080"))

    def _run() -> None:
        try:
            server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
            server.serve_forever()
        except OSError as e:
            print(f"[Avenue Guard startup] Keepalive port {port} could not start: {type(e).__name__}: {e}", flush=True)

    thread = threading.Thread(target=_run, name="avenue-guard-keepalive", daemon=True)
    thread.start()


async def _handle(request: web.Request) -> web.Response:
    status = get_keepalive_status()
    if request.path.rstrip("/") in {"/status", "/health"}:
        return web.json_response(status)
    return web.Response(text=f"OK\nstate={status.get('state', 'unknown')}\ndetail={status.get('detail', '')}\n")

async def start_keepalive() -> None:
    if _thread_started:
        return
    if web is None:
        start_keepalive_thread()
        return
    app = web.Application()
    app.router.add_get("/", _handle)
    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "8080"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
