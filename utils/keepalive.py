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
_public_model_status: dict = {
    "schema_version": 1,
    "methodology_version": "2026.09",
    "network_era": "",
    "models": [],
    "probabilities_published": False,
    "updated_at": 0,
}
_public_health_history: list[dict] = []
_public_team_members: list[dict] = []
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


def _safe_public_metric(sample: dict, name: str) -> float | None:
    value = sample.get(name)
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return (
        round(max(0.0, min(60_000.0, parsed)), 2)
        if math.isfinite(parsed)
        else None
    )


def _safe_public_count(sample: dict, name: str) -> int:
    try:
        value = int(sample.get(name) or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(10_000, value))


def set_public_health_history(samples: list[dict]) -> None:
    safe_samples: list[dict] = []
    for sample in samples[-288:]:
        if not isinstance(sample, dict):
            continue
        try:
            sample_ts = max(0, int(sample.get("sample_ts") or 0))
        except (TypeError, ValueError):
            continue
        if not sample_ts:
            continue

        safe_samples.append(
            {
                "sample_ts": sample_ts,
                "healthy": bool(sample.get("healthy")),
                "database_ok": bool(sample.get("database_ok")),
                "gateway_latency_ms": _safe_public_metric(
                    sample, "gateway_latency_ms"
                ),
                "database_latency_ms": _safe_public_metric(
                    sample, "database_latency_ms"
                ),
                "provider_available": _safe_public_count(
                    sample, "provider_available"
                ),
                "provider_total": _safe_public_count(sample, "provider_total"),
            }
        )
    safe_samples.sort(key=lambda item: item["sample_ts"])
    with _status_lock:
        _public_health_history[:] = safe_samples


def set_public_team_data(members: list[dict]) -> None:
    safe_members: list[dict] = []
    for member in members[:50]:
        if not isinstance(member, dict):
            continue
        user_id = str(member.get("id") or "").strip()
        if not (user_id.isascii() and user_id.isdecimal() and 16 <= len(user_id) <= 20):
            continue
        avatar_url = str(member.get("avatar_url") or "").strip()
        if avatar_url and not avatar_url.casefold().startswith("https://"):
            avatar_url = ""
        safe_members.append(
            {
                "id": user_id,
                "display_name": str(member.get("display_name") or "")[:100],
                "avatar_url": avatar_url[:1000],
            }
        )
    with _status_lock:
        _public_team_members[:] = safe_members


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
        raw_priority_complete = level.get("priority_complete")
        priority_status = str(level.get("public_priority_status") or "").casefold()
        priority_complete = (
            bool(raw_priority_complete)
            if raw_priority_complete is not None
            else priority_band is not None
        )
        if priority_status not in {"ranked", "pending"}:
            priority_status = "ranked" if priority_complete else "pending"
        if priority_status == "pending":
            priority_complete = False
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
            "priority_complete": priority_complete,
            "public_priority_status": priority_status,
            "public_outreach_state": outreach_state,
            "public_outcome_state": outcome_state,
        }
        uploader_name = str(level.get("uploader_name") or "").strip()[:100]
        if uploader_name:
            payload["uploader_name"] = uploader_name
        probability = level.get("probability")
        if isinstance(probability, dict) and str(probability.get("status")) == "active":
            try:
                strengths = {"limited", "moderate", "strong"}

                def public_probability(prefix: str = "") -> tuple[int, list[int], str] | None:
                    point_key = f"{prefix}probability_percent"
                    interval_key = f"{prefix}credible_interval_90_percent"
                    strength_key = f"{prefix}evidence_strength"
                    if probability.get(point_key) is None:
                        return None
                    point_value = max(0, min(100, int(probability.get(point_key))))
                    raw_interval = probability.get(interval_key)
                    lower_value = max(0, min(100, int(raw_interval[0])))
                    upper_value = max(0, min(100, int(raw_interval[1])))
                    raw_strength = str(probability.get(strength_key) or "limited")
                    return (
                        point_value,
                        [min(lower_value, upper_value), max(lower_value, upper_value)],
                        raw_strength if raw_strength in strengths else "limited",
                    )

                safe_probability = {
                    "status": "active",
                    "generated_at": max(0, int(probability.get("generated_at") or 0)),
                    "data_cutoff": max(0, int(probability.get("data_cutoff") or 0)),
                }
                for prefix in ("access_", "rating_", ""):
                    component = public_probability(prefix)
                    if component is None:
                        continue
                    point, interval, strength = component
                    safe_probability[f"{prefix}probability_percent"] = point
                    safe_probability[f"{prefix}credible_interval_90_percent"] = interval
                    safe_probability[f"{prefix}evidence_strength"] = strength
                if any(key.endswith("probability_percent") for key in safe_probability):
                    payload["probability"] = safe_probability
            except (TypeError, ValueError, IndexError):
                pass
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


def set_public_model_status(status: dict) -> None:
    allowed_statuses = {"collecting", "provisional", "active", "degraded", "paused"}
    safe_models = []
    for model in status.get("models", []) if isinstance(status, dict) else []:
        model_key = str(model.get("model_key") or "")
        model_status = str(model.get("status") or "collecting")
        if model_key not in {"access_model_v1", "rating_model_v1", "capacity_model_v1"} or model_status not in allowed_statuses:
            continue
        safe_models.append(
            {
                "model_key": model_key,
                "status": model_status,
                "evidence_strength": str(model.get("evidence_strength") or "limited")[:30],
                "generated_at": max(0, int(model.get("generated_at") or 0)),
                "reason": str(model.get("reason") or "")[:300],
            }
        )
    with _status_lock:
        _public_model_status.clear()
        _public_model_status.update(
            {
                "schema_version": 1,
                "methodology_version": str(status.get("methodology_version") or "2026.09")[:40],
                "network_era": str(status.get("network_era") or "")[:120],
                "models": safe_models,
                "probabilities_published": bool(status.get("probabilities_published")) and bool(safe_models),
                "updated_at": max(0, int(status.get("updated_at") or 0)),
            }
        )


def get_public_model_status_payload() -> dict:
    with _status_lock:
        return json.loads(json.dumps(_public_model_status))


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
        health_history = [dict(sample) for sample in _public_health_history]

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
    database = runtime.get("database") if isinstance(runtime.get("database"), dict) else {}
    database_failed = (
        database.get("connected") is False
        or (database.get("uses_remote") and database.get("worker_alive") is not True)
    )
    database_degraded = bool(
        database.get("primary_write_degraded")
        or database.get("write_queue_stalled")
    )
    task_states = runtime.get("tasks") if isinstance(runtime.get("tasks"), dict) else {}
    auxiliary_tasks = {
        "operations.smoke",
        "operations.bootstrap",
        "operations.restarts",
        "operations.timeline",
        "release.bootstrap",
    }
    task_failed = any(
        str(value).startswith(("failed", "stopped", "missing"))
        for name, value in task_states.items()
        if name not in auxiliary_tasks
    )
    latest_history = health_history[-1] if health_history else {}
    provider_total = int(latest_history.get("provider_total") or 0)
    provider_available = int(latest_history.get("provider_available") or 0)

    def system(name: str, status_value: str, detail: str) -> dict[str, str]:
        return {"name": name, "status": status_value, "detail": detail}

    systems = [
        system(
            "Discord gateway",
            "operational" if state == "online" and runtime["responsive"] else "degraded" if state == "online" else "unavailable",
            "Connected and responsive" if state == "online" and runtime["responsive"] else "Connection is recovering" if state == "online" else "Not connected",
        ),
        system(
            "Database",
            "unavailable" if database_failed else "degraded" if database_degraded else "operational" if database else "unknown",
            "Connected" if database and not database_failed and not database_degraded else "Write service is degraded" if database_degraded else "Connection unavailable" if database_failed else "No recent signal",
        ),
        system(
            "Background services",
            "unavailable" if not runtime["responsive"] else "degraded" if task_failed else "operational",
            "Core tasks running" if runtime["responsive"] and not task_failed else "A core task needs attention" if task_failed else "No recent heartbeat",
        ),
        system(
            "GD validation providers",
            "unknown" if provider_total <= 0 else "operational" if provider_available == provider_total else "degraded" if provider_available else "unavailable",
            "No recent provider sample" if provider_total <= 0 else f"{provider_available} of {provider_total} available",
        ),
    ]
    return {
        "schema_version": 4,
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
        "systems": systems,
        "health_history": health_history,
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


def get_public_team_payload() -> dict:
    with _status_lock:
        members = [dict(member) for member in _public_team_members]
    return {"schema_version": 1, "count": len(members), "members": members}


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
    if path == "/api/team":
        body = json.dumps(
            get_public_team_payload(), separators=(",", ":")
        ).encode("utf-8")
        return body, "application/json; charset=utf-8", "public, max-age=300", True
    if path == "/api/levels":
        query = parse_qs(parsed.query).get("q", [""])[-1]
        body = json.dumps(
            get_public_levels_payload(query), separators=(",", ":")
        ).encode("utf-8")
        return body, "application/json; charset=utf-8", "public, max-age=30", True
    if path == "/api/methodology/queue/status":
        body = json.dumps(get_public_model_status_payload(), separators=(",", ":")).encode("utf-8")
        return body, "application/json; charset=utf-8", "public, max-age=60", True
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


def _private_api_error(
    code: str, message: str, correlation_id: str = ""
) -> dict[str, object]:
    error: dict[str, str] = {
        "code": str(code),
        "message": str(message),
    }
    if correlation_id:
        error["correlation_id"] = str(correlation_id)
    return {"ok": False, "error": error}


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
            self._send_json(404, _private_api_error("not_found", "Resource not found"))
            return
        with _status_lock:
            service = _staff_api_service
            loop = _staff_api_loop
        if service is None or loop is None or loop.is_closed():
            self._send_json(
                503,
                _private_api_error(
                    "portal_starting", "The staff portal is still starting"
                ),
            )
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > 1_048_576:
            self._send_json(
                413, _private_api_error("body_too_large", "Request body is too large")
            )
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
            self._send_json(
                504,
                _private_api_error(
                    "portal_timeout", "The portal took too long to respond"
                ),
            )
            return
        except Exception as exc:  # noqa: BLE001 - bridge futures can surface any service failure.
            cause = exc.__cause__ or exc
            status = int(getattr(cause, "status", 500) or 500)
            if isinstance(cause, PermissionError):
                status = 403
            code = str(getattr(cause, "code", "forbidden" if status == 403 else "portal_error"))
            message = str(getattr(cause, "message", "You do not have access" if status == 403 else "The portal could not complete this request"))
            self._send_json(
                status,
                _private_api_error(
                    code,
                    message,
                    str(getattr(cause, "correlation_id", "") or ""),
                ),
            )
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
                _private_api_error(
                    "portal_starting", "The staff portal is still starting"
                ),
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
            return web.json_response(
                _private_api_error("forbidden", "You do not have access"), status=403
            )
        except Exception as exc:  # noqa: BLE001 - translate service failures into private API errors.
            return web.json_response(
                _private_api_error(
                    str(getattr(exc, "code", "portal_error")),
                    str(
                        getattr(
                            exc,
                            "message",
                            "The portal could not complete this request",
                        )
                    ),
                    str(getattr(exc, "correlation_id", "") or ""),
                ),
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
    app.router.add_route("*", "/api/team", _handle)
    app.router.add_route("*", "/api/levels", _handle)
    app.router.add_route("*", "/api/methodology/queue/status", _handle)
    app.router.add_route("*", "/api/level/{level_id}", _handle)
    app.router.add_route("*", "/api/levels/{level_id}", _handle)
    app.router.add_route("*", "/api/staff/{tail:.*}", _handle)
    app.router.add_route("*", "/api/apply/{tail:.*}", _handle)
    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "8080"))
    site = web.TCPSite(runner, "0.0.0.0", port)  # nosec B104
    await site.start()
