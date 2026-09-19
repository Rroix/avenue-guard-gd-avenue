from __future__ import annotations

import asyncio
import json
import math
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

try:
    from aiohttp import web
except ImportError:
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
_public_levels: dict[str, dict] = {}
_runtime_heartbeat = 0.0
_runtime_health: dict = {}
_staff_api_service = None
_staff_api_loop = None


def configure_staff_api(service, loop) -> None:
    """Attach the private portal service to the already-running health server."""
    global _staff_api_service, _staff_api_loop
    with _status_lock:
        _staff_api_service = service
        _staff_api_loop = loop


def set_runtime_heartbeat(*, lag_ms: float, tasks: dict, database: dict, incidents: list) -> None:
    global _runtime_heartbeat
    with _status_lock:
        _runtime_heartbeat = time.monotonic()
        _runtime_health.update({
            "event_loop_lag_ms": round(max(0.0, lag_ms), 2),
            "tasks": dict(tasks),
            "database": {key: database.get(key) for key in (
                "connected", "uses_remote", "isolated_worker", "worker_alive", "waiting_operations",
                "active_operation", "active_operation_seconds", "active_operation_task", "queue_timeouts",
                "primary_write_degraded", "last_operation_error_ts",
                "read_waiting", "background_queue_deferrals", "write_queue_stalled",
            )},
            "incident_count": len(incidents),
            "incident_occurrences": sum(int(item.get("count", 0)) for item in incidents),
            "unpersisted_occurrences": sum(int(item.get("pending_persistence", 0)) for item in incidents),
        })


def get_runtime_health() -> dict:
    with _status_lock:
        heartbeat = _runtime_heartbeat
        health = dict(_runtime_health)
        state = str(_status.get("state") or "")
    age = time.monotonic() - heartbeat if heartbeat else None
    responsive = bool(heartbeat and age is not None and age < 15)
    database = health.get("database", {})
    database_ok = (database.get("connected") is not False and (not database.get("uses_remote") or database.get("worker_alive") is True)
                   and not database.get("primary_write_degraded") and not database.get("write_queue_stalled"))
    auxiliary = {"operations.smoke", "operations.bootstrap", "operations.restarts", "operations.timeline", "release.bootstrap"}
    tasks_ok = not any(str(value).startswith(("failed", "stopped", "missing"))
                       for name, value in health.get("tasks", {}).items() if name not in auxiliary)
    return {**health, "heartbeat_age_seconds": round(age, 2) if age is not None else None,
            "responsive": responsive, "ready": state == "online" and responsive and database_ok and tasks_ok}


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


def set_public_level_data(levels: list[dict]) -> None:
    """Replace the public, privacy-filtered level page cache atomically."""
    safe_levels: dict[str, dict] = {}
    for level in levels:
        if not isinstance(level, dict):
            continue
        level_id = str(level.get("level_id") or "").strip()
        if not (level_id.isascii() and level_id.isdecimal() and 7 <= len(level_id) <= 9):
            continue
        recommendation_type = str(level.get("recommendation_type") or "").casefold()
        if recommendation_type not in {"rate", "feature", "epic", "legendary", "mythic"}:
            continue
        public_queue_state = str(
            level.get("public_queue_state") or "unknown"
        ).casefold()
        if public_queue_state not in {
            "queued",
            "in_cycle",
            "awaiting_outcome",
            "rated",
            "withdrawn",
            "invalid",
            "unknown",
        }:
            public_queue_state = "unknown"
        priority_band = level.get("public_priority_band")
        if priority_band not in {
            "top_priority",
            "high_priority",
            "standard_priority",
            "lower_priority",
        }:
            priority_band = None
        if public_queue_state not in {"queued", "in_cycle"}:
            priority_band = None
        outreach_state = str(
            level.get("public_outreach_state") or "unknown"
        ).casefold()
        if outreach_state not in {
            "queued_for_outreach",
            "outreach_in_progress",
            "reached_moderator",
            "outreach_complete",
            "withdrawn",
            "level_unavailable",
            "unknown",
        }:
            outreach_state = "unknown"
        outcome_state = str(
            level.get("public_outcome_state") or "unknown"
        ).casefold()
        if outcome_state not in {
            "awaiting_outcome",
            "rated",
            "not_observed_rated_within_window",
            "unknown",
        }:
            outcome_state = "unknown"

        payload = {
            "schema_version": 2,
            "level_id": level_id,
            "level_name": str(level.get("level_name") or "Unknown level")[:100],
            "recommendation_type": recommendation_type,
            "public_queue_state": public_queue_state,
            "public_priority_band": priority_band,
            "public_outreach_state": outreach_state,
            "public_outcome_state": outcome_state,
        }
        uploader_name = str(level.get("uploader_name") or "").strip()[:100]
        if uploader_name:
            payload["uploader_name"] = uploader_name
        for key in (
            "recommended_at",
            "submitted_to_mod_at",
            "rated_observed_at",
            "last_updated_at",
        ):
            try:
                timestamp = int(level.get(key) or 0)
            except (TypeError, ValueError):
                timestamp = 0
            if timestamp > 0:
                payload[key] = timestamp
        safe_levels[level_id] = payload
    with _status_lock:
        _public_levels.clear()
        _public_levels.update(safe_levels)


def get_public_level_payload(level_id: str) -> dict | None:
    clean_id = str(level_id or "").strip()
    with _status_lock:
        payload = _public_levels.get(clean_id)
        return dict(payload) if payload else None


def get_public_levels_payload(query: str = "", *, limit: int = 25) -> dict:
    """Search only the public recommendation cache; private requests never enter it."""
    term = str(query or "").strip().casefold()[:100]
    bounded_limit = max(1, min(50, int(limit or 25)))
    with _status_lock:
        levels = [dict(value) for value in _public_levels.values()]
    if term:
        def rank(item: dict) -> tuple[int, int, str]:
            level_id = str(item.get("level_id") or "").casefold()
            name = str(item.get("level_name") or "").casefold()
            creator = str(item.get("uploader_name") or "").casefold()
            if level_id == term:
                score = 0
            elif name == term or creator == term:
                score = 1
            elif level_id.startswith(term) or name.startswith(term) or creator.startswith(term):
                score = 2
            elif term in name or term in creator:
                score = 3
            else:
                score = 99
            return score, -int(item.get("recommended_at") or 0), level_id

        levels = [item for item in levels if rank(item)[0] < 99]
        levels.sort(key=rank)
    else:
        levels.sort(
            key=lambda item: (
                -int(item.get("recommended_at") or 0),
                str(item.get("level_id") or ""),
            )
        )
    return {
        "schema_version": 1,
        "query": str(query or "").strip()[:100],
        "count": min(len(levels), bounded_limit),
        "levels": levels[:bounded_limit],
    }


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
    runtime = get_runtime_health()
    stalled = state == "online" and runtime["heartbeat_age_seconds"] is not None and not runtime["responsive"]
    online_since_ts = int(status.get("online_since_ts") or 0)
    service_uptime_seconds = max(0, now - _process_started_ts)
    discord_connection_uptime_seconds = (
        max(0, now - online_since_ts)
        if state == "online" and online_since_ts
        else 0
    )
    return {
        "schema_version": 3,
        "service": "Avenue Guard",
        "bot_name": str(metrics.get("bot_name") or "Avenue Guard"),
        "avatar_url": str(metrics.get("avatar_url") or ""),
        "state": state,
        "status": "Unavailable" if stalled else ("Degraded" if state == "online" and not runtime["ready"] else _public_state_label(state)),
        "online": state == "online" and not stalled,
        "ready": runtime["ready"],
        "responsive": runtime["responsive"],
        "version": (
            str(current_release.get("version") or "")
            if current_release
            else "Version unavailable"
        ),
        # Canonical names distinguish the Render process lifetime from a
        # Discord gateway session, which can reconnect without a bot restart.
        "service_started_ts": _process_started_ts,
        "service_uptime_seconds": service_uptime_seconds,
        "discord_connected_since_ts": online_since_ts,
        "discord_connection_uptime_seconds": discord_connection_uptime_seconds,
        # Keep schema-v2 names so older status-page deployments remain valid.
        "process_started_ts": _process_started_ts,
        "process_uptime_seconds": service_uptime_seconds,
        "online_since_ts": online_since_ts,
        "online_uptime_seconds": discord_connection_uptime_seconds,
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
    parsed = urlsplit(str(raw_path or "/"))
    path = parsed.path.rstrip("/") or "/"
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
    if path == "/api/levels":
        query = parse_qs(parsed.query).get("q", [""])[-1]
        body = json.dumps(
            get_public_levels_payload(query), separators=(",", ":")
        ).encode("utf-8")
        return body, "application/json; charset=utf-8", "public, max-age=30", True
    level_prefix = next(
        (prefix for prefix in ("/api/level/", "/api/levels/") if path.startswith(prefix)),
        "",
    )
    if level_prefix:
        level_id = path[len(level_prefix):]
        payload = get_public_level_payload(level_id)
        body = json.dumps(
            payload or {"schema_version": 1, "error": "level_not_found"},
            separators=(",", ":"),
        ).encode("utf-8")
        return body, "application/json; charset=utf-8", "public, max-age=30", True

    status = get_keepalive_status()
    if path in {"/status", "/health", "/ready"}:
        status["runtime"] = get_runtime_health()
        body = json.dumps(status, separators=(",", ":")).encode("utf-8")
        content_type = "application/json; charset=utf-8"
    else:
        body = (
            f"OK\n"
            f"state={status.get('state', 'unknown')}\n"
            f"detail={status.get('detail', '')}\n"
        ).encode()
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
        path = urlsplit(self.path).path.rstrip("/")
        is_missing_level = path.startswith(("/api/level/", "/api/levels/")) and get_public_level_payload(path.rsplit("/", 1)[-1]) is None
        self.send_response(
            404
            if is_missing_level
            else 503
            if path == "/ready" and not get_runtime_health()["ready"]
            else 200
        )
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        if public_api:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def do_GET(self) -> None:
        if self._is_staff_api():
            self._staff_response("GET")
            return
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

    def do_POST(self) -> None:
        self._staff_response("POST")

    def do_PATCH(self) -> None:
        self._staff_response("PATCH")

    def do_DELETE(self) -> None:
        self._staff_response("DELETE")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Allow", "GET,HEAD,POST,PATCH,DELETE,OPTIONS")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _is_staff_api(self) -> bool:
        path = urlsplit(self.path).path
        return path.startswith(("/api/staff", "/api/apply"))

    def _staff_response(self, method: str) -> None:
        if not self._is_staff_api():
            self._send_json(404, {"error": "not_found", "message": "Resource not found"})
            return
        with _status_lock:
            service = _staff_api_service
            loop = _staff_api_loop
        if service is None or loop is None or loop.is_closed():
            self._send_json(
                503,
                {"error": "portal_starting", "message": "The staff portal is still starting"},
            )
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > 1_048_576:
            self._send_json(413, {"error": "body_too_large", "message": "Request body is too large"})
            return
        body = self.rfile.read(length) if length else b""
        headers = {str(key).casefold(): str(value) for key, value in self.headers.items()}
        future = asyncio.run_coroutine_threadsafe(
            service.handle_request(method, self.path, headers, body), loop
        )
        try:
            status, payload = future.result(timeout=28)
        except TimeoutError:
            future.cancel()
            self._send_json(504, {"error": "portal_timeout", "message": "The portal took too long to respond"})
            return
        except Exception as exc:  # noqa: BLE001 - bridge futures can surface any service failure.
            cause = exc.__cause__ or exc
            status = int(getattr(cause, "status", 500) or 500)
            if isinstance(cause, PermissionError):
                status = 403
            code = str(getattr(cause, "code", "forbidden" if status == 403 else "portal_error"))
            message = str(getattr(cause, "message", "You do not have access" if status == 403 else "The portal could not complete this request"))
            self._send_json(status, {"error": code, "message": message})
            return
        self._send_json(status, payload)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

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
    if request.path.startswith(("/api/staff", "/api/apply")):
        with _status_lock:
            service = _staff_api_service
        if service is None:
            return web.json_response(
                {"error": "portal_starting", "message": "The staff portal is still starting"},
                status=503,
            )
        try:
            status, payload = await service.handle_request(
                request.method,
                str(request.rel_url),
                {str(key).casefold(): str(value) for key, value in request.headers.items()},
                await request.read(),
            )
        except PermissionError:
            return web.json_response({"error": "forbidden", "message": "You do not have access"}, status=403)
        except Exception as exc:  # noqa: BLE001 - translate service failures into private API errors.
            return web.json_response(
                {
                    "error": str(getattr(exc, "code", "portal_error")),
                    "message": str(getattr(exc, "message", "The portal could not complete this request")),
                },
                status=int(getattr(exc, "status", 500) or 500),
            )
        return web.json_response(payload, status=status, headers={"Cache-Control": "no-store"})
    body, content_type, cache_control, public_api = _response_for_path(str(request.rel_url))
    headers = {
        "Cache-Control": cache_control,
        "X-Content-Type-Options": "nosniff",
    }
    if public_api:
        headers["Access-Control-Allow-Origin"] = "*"
    path = request.path.rstrip("/")
    is_missing_level = path.startswith(("/api/level/", "/api/levels/")) and get_public_level_payload(path.rsplit("/", 1)[-1]) is None
    return web.Response(
        body=body,
        content_type=content_type.split(";", 1)[0],
        charset="utf-8",
        headers=headers,
        status=(
            404
            if is_missing_level
            else 503
            if path == "/ready" and not get_runtime_health()["ready"]
            else 200
        ),
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
    app.router.add_route("*", "/ready", _handle)
    app.router.add_route("*", "/api/bot", _handle)
    app.router.add_route("*", "/api/releases", _handle)
    app.router.add_route("*", "/api/levels", _handle)
    app.router.add_route("*", "/api/level/{level_id}", _handle)
    app.router.add_route("*", "/api/levels/{level_id}", _handle)
    app.router.add_route("*", "/api/staff/{tail:.*}", _handle)
    app.router.add_route("*", "/api/apply/{tail:.*}", _handle)
    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "8080"))
    site = web.TCPSite(runner, "0.0.0.0", port)  # nosec B104
    await site.start()
