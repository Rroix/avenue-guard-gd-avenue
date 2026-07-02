from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from aiohttp import web
except Exception:
    web = None

_thread_started = False


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK")

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
    return web.Response(text="OK")

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
