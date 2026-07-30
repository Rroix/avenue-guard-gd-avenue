from __future__ import annotations

import json
import math
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

try:
    from aiohttp import web
except Exception:
    web = None

_thread_started = False
_status_lock = threading.Lock()
_process_started_ts = int(time.time())
_status = {
    "service": "Avenue Guard",
    "state": "starting",
    "detail": "Process is starting",
    "updated_ts": _process_started_ts,
    "online_since_ts": 0,
    "retry_after_seconds": 0,
    "next_retry_ts": 0,
}
_public_metrics = {
    "bot_name": "Avenue Guard",
    "avatar_url": "",
    "latency_ms": None,
    "guild_count": 0,
    "member_count": 0,
    "uptime_percentage": None,
    "uptime_tracking_since_ts": 0,
    "updated_ts": _process_started_ts,
}
_public_releases: list[dict] = []


def set_keepalive_status(
    state: str,
    detail: str = "",
    *,
    retry_after_seconds: int = 0,
    next_retry_ts: int = 0,
) -> None:
    now = int(time.time())
    with _status_lock:
        old_state = str(_status.get("state") or "")
        new_state = str(state or "unknown")
        online_since_ts = int(_status.get("online_since_ts") or 0)
        if new_state == "online" and old_state != "online":
            online_since_ts = now
        elif new_state != "online" and old_state == "online":
            online_since_ts = 0
        _status.update(
            {
                "state": new_state,
                "detail": str(detail or ""),
                "updated_ts": now,
                "online_since_ts": online_since_ts,
                "retry_after_seconds": int(retry_after_seconds or 0),
                "next_retry_ts": int(next_retry_ts or 0),
            }
        )


def get_keepalive_status() -> dict:
    with _status_lock:
        return dict(_status)


def set_public_bot_metrics(
    *,
    bot_name: str = "",
    avatar_url: str = "",
    latency_ms: int | None = None,
    guild_count: int = 0,
    member_count: int = 0,
    uptime_percentage: float | None = None,
    uptime_tracking_since_ts: int = 0,
) -> None:
    normalized_uptime_percentage = None
    if uptime_percentage is not None:
        value = float(uptime_percentage)
        if math.isfinite(value):
            normalized_uptime_percentage = round(max(0.0, min(100.0, value)), 3)
    with _status_lock:
        _public_metrics.update(
            {
                "bot_name": str(bot_name or "Avenue Guard")[:100],
                "avatar_url": str(avatar_url or "")[:1000],
                "latency_ms": (
                    max(0, min(60_000, int(latency_ms)))
                    if latency_ms is not None
                    else None
                ),
                "guild_count": max(0, int(guild_count or 0)),
                "member_count": max(0, int(member_count or 0)),
                "uptime_percentage": normalized_uptime_percentage,
                "uptime_tracking_since_ts": max(
                    0,
                    int(uptime_tracking_since_ts or 0),
                ),
                "updated_ts": int(time.time()),
            }
        )


def set_public_release_data(releases: list[dict]) -> None:
    safe_releases: list[dict] = []
    for release in releases[:50]:
        if not isinstance(release, dict):
            continue
        changes = release.get("changes")
        if not isinstance(changes, list):
            changes = []
        safe_releases.append(
            {
                "version": str(release.get("version") or "")[:40],
                "title": str(release.get("title") or "")[:100],
                "summary": str(release.get("summary") or "")[:600],
                "changes": [str(item)[:300] for item in changes[:20]],
                "published_ts": max(0, int(release.get("published_ts") or 0)),
            }
        )
    with _status_lock:
        _public_releases[:] = safe_releases


def _public_state_label(state: str) -> str:
    labels = {
        "online": "Operational",
        "starting": "Starting",
        "database_check": "Starting",
        "discord_login": "Connecting",
        "waiting_rate_limit": "Waiting to reconnect",
        "reconnecting": "Reconnecting",
        "startup_error": "Unavailable",
        "fatal_login_error": "Unavailable",
        "crashed": "Unavailable",
        "stopped": "Offline",
    }
    return labels.get(str(state or "").casefold(), "Unavailable")


def get_public_bot_payload() -> dict:
    now = int(time.time())
    with _status_lock:
        status = dict(_status)
        metrics = dict(_public_metrics)
        current_release = dict(_public_releases[0]) if _public_releases else None

    state = str(status.get("state") or "unknown")
    online_since_ts = int(status.get("online_since_ts") or 0)
    return {
        "schema_version": 2,
        "service": "Avenue Guard",
        "bot_name": str(metrics.get("bot_name") or "Avenue Guard"),
        "avatar_url": str(metrics.get("avatar_url") or ""),
        "state": state,
        "status": _public_state_label(state),
        "online": state == "online",
        "version": (
            str(current_release.get("version") or "")
            if current_release
            else "Version unavailable"
        ),
        "process_started_ts": _process_started_ts,
        "process_uptime_seconds": max(0, now - _process_started_ts),
        "online_since_ts": online_since_ts,
        "online_uptime_seconds": (
            max(0, now - online_since_ts)
            if state == "online" and online_since_ts
            else 0
        ),
        "latency_ms": metrics.get("latency_ms"),
        "guild_count": int(metrics.get("guild_count") or 0),
        "member_count": int(metrics.get("member_count") or 0),
        "uptime_percentage": metrics.get("uptime_percentage"),
        "uptime_tracking_since_ts": int(
            metrics.get("uptime_tracking_since_ts") or 0
        ),
        "updated_ts": max(
            int(status.get("updated_ts") or 0),
            int(metrics.get("updated_ts") or 0),
        ),
        "current_release": current_release,
    }


def get_public_releases_payload() -> dict:
    with _status_lock:
        releases = [dict(release) for release in _public_releases]
    return {
        "schema_version": 1,
        "count": len(releases),
        "releases": releases,
    }


def _response_for_path(raw_path: str) -> tuple[bytes, str, str, bool]:
    path = urlsplit(str(raw_path or "/")).path.rstrip("/") or "/"
    if path == "/api/bot":
        body = json.dumps(
            get_public_bot_payload(),
            separators=(",", ":"),
        ).encode("utf-8")
        return body, "application/json; charset=utf-8", "no-store", True
    if path == "/api/releases":
        body = json.dumps(
            get_public_releases_payload(),
            separators=(",", ":"),
        ).encode("utf-8")
        return body, "application/json; charset=utf-8", "public, max-age=30", True

    status = get_keepalive_status()
    if path in {"/status", "/health"}:
        body = json.dumps(status, separators=(",", ":")).encode("utf-8")
        content_type = "application/json; charset=utf-8"
    else:
        body = (
            f"OK\n"
            f"state={status.get('state', 'unknown')}\n"
            f"detail={status.get('detail', '')}\n"
        ).encode("utf-8")
        content_type = "text/plain; charset=utf-8"
    return body, content_type, "no-store", False


class _HealthHandler(BaseHTTPRequestHandler):
    def _health_response(self) -> tuple[bytes, str, str, bool]:
        return _response_for_path(self.path)

    def _send_health_headers(
        self,
        body: bytes,
        content_type: str,
        cache_control: str,
        public_api: bool,
    ) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        if public_api:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def do_GET(self) -> None:
        body, content_type, cache_control, public_api = self._health_response()
        self._send_health_headers(body, content_type, cache_control, public_api)
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # Health monitors may disconnect as soon as they receive headers.
            return

    def do_HEAD(self) -> None:
        body, content_type, cache_control, public_api = self._health_response()
        self._send_health_headers(body, content_type, cache_control, public_api)

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
        global _thread_started
        try:
            server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)  # nosec B104
            server.serve_forever()
        except OSError as e:
            _thread_started = False
            print(f"[Avenue Guard startup] Keepalive port {port} could not start: {type(e).__name__}: {e}", flush=True)

    thread = threading.Thread(target=_run, name="avenue-guard-keepalive", daemon=True)
    thread.start()


async def _handle(request: web.Request) -> web.Response:
    body, content_type, cache_control, public_api = _response_for_path(request.path)
    headers = {
        "Cache-Control": cache_control,
        "X-Content-Type-Options": "nosniff",
    }
    if public_api:
        headers["Access-Control-Allow-Origin"] = "*"
    return web.Response(
        body=body,
        content_type=content_type.split(";", 1)[0],
        charset="utf-8",
        headers=headers,
    )

async def start_keepalive() -> None:
    if _thread_started:
        return
    if web is None:
        start_keepalive_thread()
        return
    app = web.Application()
    app.router.add_route("*", "/", _handle)
    app.router.add_route("*", "/health", _handle)
    app.router.add_route("*", "/status", _handle)
    app.router.add_route("*", "/api/bot", _handle)
    app.router.add_route("*", "/api/releases", _handle)
    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "8080"))
    site = web.TCPSite(runner, "0.0.0.0", port)  # nosec B104
    await site.start()
