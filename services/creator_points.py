from __future__ import annotations

import asyncio
from collections import defaultdict
import json
import random
import statistics
import time
from typing import Any
from urllib.parse import quote

import aiohttp

from utils.creator_points import (
    GDBROWSER_LEVEL_HTML_URL,
    GDBROWSER_PROFILE_API_URL,
    GDBROWSER_PROFILE_HTML_URL,
    canonical_identity,
    creator_key,
    creator_points_settings,
    explicit_nonnegative_int,
    parse_gdbrowser_level_html,
    parse_gdbrowser_profile_api,
    parse_gdbrowser_profile_html,
    response_fingerprint,
    select_creator_points,
)
from utils.gd_profile import fetch_creator_profile
from utils.gd_validation import _read_provider_text
from utils.priority_system import PPS_QUEUE_REFRESH_STATES, score_components
from utils.workflows import new_correlation_id


class CreatorPointsResolver:
    """The only path that supplies current Creator Points to PPS."""

    def __init__(self, bot, priority_service):
        self.bot = bot
        self.db = bot.db
        self.priority = priority_service
        self._level_flights: dict[str, asyncio.Task] = {}
        self._profile_flights: dict[str, asyncio.Task] = {}
        self._queue_locks: dict[int, asyncio.Lock] = {}
        self._flight_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._semaphore: asyncio.Semaphore | None = None

    @property
    def settings(self):
        return creator_points_settings(self.bot.config.data)

    def wake(self) -> None:
        self._wake.set()

    async def wait(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=max(0.1, timeout))
        except asyncio.TimeoutError:
            pass
        self._wake.clear()

    async def enqueue(self, queue_id: int, *, priority: int = 100, immediate: bool = True) -> None:
        row = await self.db.fetchone(
            "SELECT id,guild_id,level_id,queue_state FROM level_outreach_queue WHERE id=?",
            (int(queue_id),),
        )
        if not row or str(row["queue_state"]) not in PPS_QUEUE_REFRESH_STATES:
            return
        now = int(time.time())
        await self.db.execute_transaction(
            [
                (
                    "INSERT INTO creator_points_resolution_jobs(queue_id,guild_id,level_id,priority,state,attempt_count,"
                    "next_attempt_ts,first_pending_ts,generation,updated_ts) VALUES(?,?,?,?, 'pending',0,?,?,1,?) "
                    "ON CONFLICT(queue_id) DO UPDATE SET priority=MAX(priority,excluded.priority),state='pending',"
                    "next_attempt_ts=CASE WHEN ? THEN MIN(next_attempt_ts,excluded.next_attempt_ts) ELSE next_attempt_ts END,"
                    "generation=generation+1,updated_ts=excluded.updated_ts",
                    (int(row["id"]), int(row["guild_id"]), str(row["level_id"]), int(priority), now, now, now, int(bool(immediate))),
                ),
                (
                    "UPDATE level_outreach_queue SET creator_points_status=CASE WHEN creator_points_status='manual_override' "
                    "THEN creator_points_status ELSE 'pending' END,creator_points_pending_reason=CASE WHEN creator_points_status='manual_override' "
                    "THEN NULL ELSE 'creator_points' END,updated_ts=? WHERE id=?",
                    (now, int(queue_id)),
                ),
            ],
            retry_safe=True,
        )
        self.wake()

    async def bootstrap(self, guild_id: int) -> int:
        now = int(time.time())
        states = ",".join("?" for _ in PPS_QUEUE_REFRESH_STATES)
        rows = await self.db.fetchall(
            f"SELECT id,guild_id,level_id FROM level_outreach_queue WHERE guild_id=? AND queue_state IN({states}) "  # nosec B608
            "AND (priority_complete=0 OR current_creator_points IS NULL)",
            (int(guild_id), *PPS_QUEUE_REFRESH_STATES),
        )
        statements: list[tuple[str, tuple[Any, ...]]] = []
        for row in rows:
            statements.append((
                "INSERT INTO creator_points_resolution_jobs(queue_id,guild_id,level_id,priority,state,attempt_count,next_attempt_ts,"
                "first_pending_ts,generation,updated_ts) VALUES(?,?,?,100,'pending',0,?,?,1,?) ON CONFLICT(queue_id) DO NOTHING",
                (int(row["id"]), int(row["guild_id"]), str(row["level_id"]), now, now, now),
            ))
        if statements:
            statements.append((
                f"UPDATE level_outreach_queue SET priority_points=NULL,creator_component_g=NULL,priority_complete=0,"  # nosec B608
                "creator_points_status=CASE WHEN creator_points_status='manual_override' THEN creator_points_status ELSE 'pending' END,"
                "creator_points_pending_reason=CASE WHEN creator_points_status='manual_override' THEN NULL ELSE 'creator_points' END,"
                "last_public_priority_band=NULL,updated_ts=? WHERE guild_id=? AND queue_state IN(" + states + ") "
                "AND current_creator_points IS NULL",
                (now, int(guild_id), *PPS_QUEUE_REFRESH_STATES),
            ))
            await self.db.execute_transaction(statements, retry_safe=True)
        if rows:
            self.wake()
        return len(rows)

    async def run_due_jobs(self, *, limit: int | None = None) -> dict[str, int]:
        if not self.settings.enabled:
            return {"attempted": 0, "resolved": 0, "failed": 0}
        now = int(time.time())
        batch = max(1, min(int(limit or self.settings.max_concurrency), 25))
        jobs = await self.db.fetchall(
            "SELECT j.* FROM creator_points_resolution_jobs j JOIN level_outreach_queue q ON q.id=j.queue_id "
            "WHERE j.state IN('pending','conflict','identity_unresolved','providers_unavailable','needs_attention') "
            "AND j.next_attempt_ts<=? AND q.queue_state IN('queued','in_cycle','awaiting_outcome') "
            "ORDER BY j.priority DESC,j.next_attempt_ts,j.queue_id LIMIT ?",
            (now, batch),
        )
        if not jobs:
            return {"attempted": 0, "resolved": 0, "failed": 0}
        self._semaphore = self._semaphore or asyncio.Semaphore(self.settings.max_concurrency)

        async def run(job):
            async with self._semaphore:
                try:
                    result = await self.resolve_queue(int(job["queue_id"]))
                    return bool(result and result.get("resolved"))
                except Exception as exc:
                    await self._schedule_failure(job, "internal", type(exc).__name__)
                    return False

        results = await asyncio.gather(*(run(job) for job in jobs))
        return {"attempted": len(results), "resolved": sum(results), "failed": len(results) - sum(results)}

    async def resolve_queue(self, queue_id: int, *, force: bool = False) -> dict[str, Any]:
        lock = self._queue_locks.setdefault(int(queue_id), asyncio.Lock())
        async with lock:
            return await self._resolve_queue_unlocked(int(queue_id), force=force)

    async def _resolve_queue_unlocked(self, queue_id: int, *, force: bool = False) -> dict[str, Any]:
        row = await self.db.fetchone("SELECT * FROM level_outreach_queue WHERE id=?", (int(queue_id),))
        if not row:
            raise ValueError("Queue entry not found")
        if str(row["queue_state"]) not in PPS_QUEUE_REFRESH_STATES:
            return {"resolved": False, "state": "inactive"}
        if str(row["creator_points_status"] or "") == "manual_override" and not force:
            return {"resolved": True, "state": "manual_override", "creator_points": row["current_creator_points"]}
        now = int(time.time())
        cached_identity = {
            "username": row["uploader_name"],
            "account_id": row["uploader_account_id"],
            "player_id": row["uploader_user_id"],
            "confidence": row["uploader_identity_confidence"] or "stored_identity",
            "creator_key": creator_key(
                account_id=row["uploader_account_id"],
                player_id=row["uploader_user_id"],
                username=row["uploader_name"],
            ),
        }
        if not force and cached_identity["creator_key"]:
            cached = await self._cached_creator(cached_identity)
            if cached:
                accepted = {
                    "resolved": True,
                    "state": "resolved",
                    "creator_points": int(cached["creator_points"]),
                    "source": cached.get("cached_source") or "cache",
                    "confidence": cached.get("cached_confidence") or "verified_single_source",
                    "observation": cached,
                }
                return await self._persist_resolution(row, cached_identity, accepted, [cached])
        correlation = str(row["correlation_id"] or new_correlation_id("cp-resolution"))
        await self.db.execute_transaction(
            [
                ("UPDATE creator_points_resolution_jobs SET state='resolving',last_attempt_ts=?,updated_ts=? WHERE queue_id=?", (now, now, int(queue_id))),
                ("UPDATE level_outreach_queue SET creator_points_status='resolving',creator_points_pending_reason='creator_points',updated_ts=? WHERE id=? AND creator_points_status!='manual_override'", (now, int(queue_id))),
                (
                    "INSERT INTO workflow_events(correlation_id,workflow_type,entity_id,event,guild_id,payload_json,created_ts) "
                    "SELECT ?,'priority_system',?,'cp_resolution_started',?,?,? WHERE NOT EXISTS(SELECT 1 FROM workflow_events "
                    "WHERE entity_id=? AND event='cp_resolution_started')",
                    (correlation, f"queue:{int(queue_id)}", int(row["guild_id"]), json.dumps({"level_id": str(row["level_id"])}, separators=(",", ":")), now, f"queue:{int(queue_id)}"),
                ),
            ], retry_safe=True,
        )
        level_id = str(row["level_id"])
        evidence = await self._singleflight_level(level_id, force=force)
        await self._persist_observations(int(queue_id), level_id, evidence["observations"])
        identity = evidence.get("identity")
        accepted = evidence.get("accepted") or {"resolved": False, "state": "providers_unavailable"}
        if accepted.get("resolved") and identity:
            return await self._persist_resolution(row, identity, accepted, evidence["observations"])
        state = str(accepted.get("state") or ("identity_unresolved" if not identity else "providers_unavailable"))
        category = "conflict" if state == "conflict" else ("identity_mismatch" if not identity else "provider_unavailable")
        job = await self.db.fetchone("SELECT * FROM creator_points_resolution_jobs WHERE queue_id=?", (int(queue_id),))
        retry_after = max(
            (int(item.get("retry_after_seconds") or 0) for item in evidence["observations"]),
            default=0,
        )
        await self._schedule_failure(
            job or {"queue_id": queue_id, "attempt_count": 0, "first_pending_ts": now},
            category,
            state,
            minimum_delay=retry_after,
        )
        return {"resolved": False, "state": state, "creator_points": None, "identity": identity}

    async def _singleflight_level(self, level_id: str, *, force: bool) -> dict[str, Any]:
        async with self._flight_lock:
            task = self._level_flights.get(level_id)
            if task is None or task.done():
                task = asyncio.create_task(self._resolve_level(level_id, force=force), name=f"cp-level-{level_id}")
                self._level_flights[level_id] = task
        try:
            return await task
        finally:
            if task.done():
                async with self._flight_lock:
                    if self._level_flights.get(level_id) is task:
                        self._level_flights.pop(level_id, None)

    async def _resolve_level(self, level_id: str, *, force: bool) -> dict[str, Any]:
        cog = self.bot.get_cog("RequestLevelsCog")
        if cog is None:
            return {"identity": None, "accepted": {"resolved": False, "state": "providers_unavailable"}, "observations": []}
        session = await cog._get_level_validation_session()
        coroutines = []
        cfg = self.settings.providers
        if cfg["gdbrowser_html"]:
            coroutines.append(self._gdbrowser_observations(session, cog, level_id, force=force))
        for provider in ("boomlings", "gdhistory", "gdrateplus"):
            if cfg[provider]:
                coroutines.append(self._validation_observations(cog, session, provider, level_id))
        observations: list[dict[str, Any]] = []
        tasks = [asyncio.create_task(item) for item in coroutines]
        done, pending = (
            await asyncio.wait(tasks, timeout=self.settings.initial_timeout_seconds)
            if tasks
            else (set(), set())
        )
        for task in done:
            try:
                observations.extend(task.result())
            except Exception:
                continue
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        identity = canonical_identity(observations)
        if identity:
            cached = None if force else await self._cached_creator(identity)
            if cached:
                observations.append(cached)
            accepted = select_creator_points(observations, identity)
        else:
            accepted = {"resolved": False, "state": "identity_unresolved", "creator_points": None}
        return {"identity": identity, "accepted": accepted, "observations": observations}

    async def _validation_observations(self, cog, session, provider: str, level_id: str) -> list[dict[str, Any]]:
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(cog._fetch_validation_provider(provider, session, level_id), timeout=self.settings.provider_timeout_seconds)
        except asyncio.TimeoutError:
            return [self._error_observation(provider, "level", "timeout", started)]
        metadata = result.get("audit_metadata") if isinstance(result.get("audit_metadata"), dict) else {}
        observation = {
            "provider": provider,
            "method": "level",
            "username": str(result.get("creator") or "").strip() or None,
            "account_id": metadata.get("uploader_account_id"),
            "player_id": metadata.get("uploader_user_id"),
            "creator_points": metadata.get("creator_points"),
            "provider_timestamp": result.get("snapshot_ts"),
            "observed_at": int(time.time()),
            "success": bool(result.get("ok") and result.get("exists") is True),
            "status_code": result.get("status_code"),
            "error_category": None if result.get("ok") else self._normalize_error(result.get("failure_kind")),
            "retry_after_seconds": int(result.get("retry_after_seconds") or 0),
            "response_fingerprint": response_fingerprint({k: result.get(k) for k in ("provider", "ok", "exists", "level_id", "creator", "audit_metadata")}),
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "archival": provider == "gdhistory",
        }
        items = [observation]
        if provider == "boomlings" and observation["success"] and observation.get("account_id"):
            items.append(await self._boomlings_profile(cog, session, observation))
        return items

    async def _boomlings_profile(self, cog, session, identity: dict[str, Any]) -> dict[str, Any]:
        key = f"boomlings:{identity['account_id']}"

        async def fetch():
            started = time.perf_counter()
            lock = cog._validation_provider_locks.setdefault("boomlings", asyncio.Lock())
            async with lock:
                if cog._provider_circuit_open("boomlings"):
                    return self._error_observation("boomlings", "direct_profile", "provider_unavailable", started, identity)
                try:
                    result = await asyncio.wait_for(fetch_creator_profile(session, str(identity["account_id"])), timeout=self.settings.provider_timeout_seconds)
                except asyncio.TimeoutError:
                    result = {"ok": False, "failure_kind": "timeout"}
                cog._record_provider_validation_result("boomlings", result)
            return {
                "provider": "boomlings", "method": "direct_profile", "username": result.get("name") or identity.get("username"),
                "account_id": explicit_nonnegative_int(result.get("account_id")) or identity.get("account_id"),
                "player_id": explicit_nonnegative_int(result.get("user_id")) or identity.get("player_id"),
                "creator_points": result.get("current_creator_points") if result.get("ok") else None,
                "observed_at": int(time.time()), "success": bool(result.get("ok")), "status_code": result.get("status_code"),
                "error_category": None if result.get("ok") else self._normalize_error(result.get("failure_kind")),
                "response_fingerprint": response_fingerprint({k: result.get(k) for k in ("ok", "account_id", "user_id", "name", "current_creator_points")}),
                "latency_ms": round((time.perf_counter() - started) * 1000, 2), "archival": False,
            }

        return await self._singleflight_profile(key, fetch)

    async def _gdbrowser_observations(self, session, cog, level_id: str, *, force: bool = False) -> list[dict[str, Any]]:
        level_started = time.perf_counter()
        parsed_level = None if force else await self._cached_level_identity(level_id)
        html_result: dict[str, Any] = {"status_code": None}
        identity_method = "identity_cache"
        if parsed_level is None:
            identity_method = "html_level"
            html_result = await self._gdbrowser_get(session, cog, GDBROWSER_LEVEL_HTML_URL.format(level_id=level_id), "html_level")
            if not html_result.get("ok"):
                return [self._error_observation("gdbrowser", "html_level", html_result.get("error_category") or "provider_unavailable", level_started, status_code=html_result.get("status_code"), retry_after_seconds=html_result.get("retry_after_seconds"))]
            parsed_level = parse_gdbrowser_level_html(html_result["text"], level_id)
            if parsed_level.get("ok"):
                await self._save_level_identity(level_id, parsed_level)
        identity_obs = {
            "provider": "gdbrowser", "method": identity_method, "username": parsed_level.get("username"),
            "account_id": parsed_level.get("account_id"), "player_id": parsed_level.get("player_id"), "creator_points": None,
            "profile_path": parsed_level.get("profile_path"), "observed_at": int(time.time()), "success": bool(parsed_level.get("ok")),
            "status_code": html_result.get("status_code"), "error_category": parsed_level.get("error_category"),
            "response_fingerprint": parsed_level.get("response_fingerprint"),
            "latency_ms": round((time.perf_counter() - level_started) * 1000, 2), "archival": False,
        }
        items = [identity_obs]
        if not parsed_level.get("ok"):
            return items
        key = creator_key(account_id=parsed_level.get("account_id"), username=parsed_level.get("username")) or f"profile:{parsed_level['profile_path']}"

        async def fetch_profile():
            started = time.perf_counter()
            result = await self._gdbrowser_get(session, cog, GDBROWSER_PROFILE_HTML_URL.format(profile_path=parsed_level["profile_path"]), "html_profile")
            if not result.get("ok"):
                return self._error_observation("gdbrowser", "html_profile", result.get("error_category") or "provider_unavailable", started, identity_obs, status_code=result.get("status_code"), retry_after_seconds=result.get("retry_after_seconds"))
            parsed = parse_gdbrowser_profile_html(result["text"], expected_username=parsed_level.get("username"), expected_account_id=parsed_level.get("account_id"))
            return {
                "provider": "gdbrowser", "method": "html_profile", "username": parsed.get("username"),
                "account_id": parsed.get("account_id") or parsed_level.get("account_id"), "player_id": parsed.get("player_id"),
                "creator_points": parsed.get("creator_points"), "observed_at": int(time.time()), "success": bool(parsed.get("ok")),
                "status_code": result.get("status_code"), "error_category": parsed.get("error_category"),
                "response_fingerprint": parsed.get("response_fingerprint"), "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "archival": False,
            }

        profile = await self._singleflight_profile(f"gdbrowser:{key}", fetch_profile)
        items.append(profile)
        if not profile.get("success") and self.settings.providers["gdbrowser_api"]:
            items.append(await self._gdbrowser_api_profile(session, cog, parsed_level))
        return items

    async def _cached_level_identity(self, level_id: str) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT * FROM creator_level_identities WHERE level_id=? AND expires_ts>?",
            (str(level_id), int(time.time())),
        )
        if not row:
            return None
        return {
            "ok": True,
            "username": row["username"],
            "account_id": row["account_id"],
            "player_id": row["player_id"],
            "profile_path": row["profile_path"],
            "response_fingerprint": row["response_fingerprint"],
            "error_category": None,
        }

    async def _save_level_identity(self, level_id: str, identity: dict[str, Any]) -> None:
        now = int(time.time())
        await self.db.execute(
            "INSERT INTO creator_level_identities(level_id,username,account_id,player_id,profile_path,source,confidence,"
            "observed_at,response_fingerprint,expires_ts,updated_ts) VALUES(?,?,?,?,?,'gdbrowser_html','profile_link',?,?,?,?) "
            "ON CONFLICT(level_id) DO UPDATE SET username=excluded.username,account_id=excluded.account_id,player_id=excluded.player_id,"
            "profile_path=excluded.profile_path,source=excluded.source,confidence=excluded.confidence,observed_at=excluded.observed_at,"
            "response_fingerprint=excluded.response_fingerprint,expires_ts=excluded.expires_ts,updated_ts=excluded.updated_ts",
            (
                str(level_id),
                identity.get("username"),
                identity.get("account_id"),
                identity.get("player_id"),
                identity.get("profile_path"),
                now,
                identity.get("response_fingerprint"),
                now + self.settings.level_identity_ttl_seconds,
                now,
            ),
        )

    async def _gdbrowser_api_profile(self, session, cog, identity: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        username = str(identity.get("username") or "")
        result = await self._gdbrowser_get(session, cog, GDBROWSER_PROFILE_API_URL.format(username=quote(username)), "api_profile", minimum_interval=1.5)
        if not result.get("ok"):
            return self._error_observation("gdbrowser", "api_profile", result.get("error_category") or "provider_unavailable", started, identity, status_code=result.get("status_code"), retry_after_seconds=result.get("retry_after_seconds"))
        try:
            payload = json.loads(result["text"])
        except (TypeError, ValueError):
            payload = None
        parsed = parse_gdbrowser_profile_api(payload, expected_username=username, expected_account_id=identity.get("account_id"))
        return {
            "provider": "gdbrowser", "method": "api_profile", "username": parsed.get("username"),
            "account_id": parsed.get("account_id") or identity.get("account_id"), "player_id": parsed.get("player_id"),
            "creator_points": parsed.get("creator_points"), "observed_at": int(time.time()), "success": bool(parsed.get("ok")),
            "status_code": result.get("status_code"), "error_category": parsed.get("error_category"),
            "response_fingerprint": parsed.get("response_fingerprint"), "latency_ms": round((time.perf_counter() - started) * 1000, 2), "archival": False,
        }

    async def _gdbrowser_get(self, session, cog, url: str, method: str, *, minimum_interval: float = 0.25) -> dict[str, Any]:
        lock = cog._validation_provider_locks.setdefault("gdbrowser", asyncio.Lock())
        async with lock:
            if cog._provider_circuit_open("gdbrowser"):
                return {"ok": False, "error_category": "provider_unavailable"}
            elapsed = time.monotonic() - cog._validation_provider_last_call.get("gdbrowser", 0.0)
            if elapsed < minimum_interval:
                await asyncio.sleep(minimum_interval - elapsed)
            try:
                async with asyncio.timeout(self.settings.provider_timeout_seconds):
                    async with session.get(url, headers={"Accept": "text/html,application/json;q=0.8", "User-Agent": "Avenue-Guard/1 creator-resolution"}) as response:
                        text, read_error = await _read_provider_text(response, "gdbrowser")
                        status = int(response.status)
                        retry_after = response.headers.get("Retry-After")
            except asyncio.TimeoutError:
                result = {"provider": "gdbrowser", "ok": False, "failure_kind": "timeout"}
            except aiohttp.ClientError:
                result = {"provider": "gdbrowser", "ok": False, "failure_kind": "network_error"}
            else:
                if read_error:
                    result = read_error
                elif status == 429:
                    result = {"provider": "gdbrowser", "ok": False, "failure_kind": "rate_limited", "status_code": status, "retry_after_seconds": int(retry_after) if str(retry_after or "").isdigit() else 60}
                elif status in {401, 403}:
                    result = {"provider": "gdbrowser", "ok": False, "failure_kind": "access_denied", "status_code": status}
                elif status == 404:
                    result = {"provider": "gdbrowser", "ok": False, "failure_kind": "not_found", "status_code": status}
                elif status >= 400:
                    result = {"provider": "gdbrowser", "ok": False, "failure_kind": "upstream_unavailable", "status_code": status}
                else:
                    result = {"provider": "gdbrowser", "ok": True, "text": text, "status_code": status}
            cog._validation_provider_last_call["gdbrowser"] = time.monotonic()
            cog._record_provider_validation_result("gdbrowser", result)
            return {
                "ok": bool(result.get("ok")),
                "text": result.get("text"),
                "status_code": result.get("status_code"),
                "retry_after_seconds": int(result.get("retry_after_seconds") or 0),
                "error_category": None if result.get("ok") else self._normalize_error(result.get("failure_kind")),
            }

    async def _singleflight_profile(self, key: str, factory) -> dict[str, Any]:
        async with self._flight_lock:
            task = self._profile_flights.get(key)
            if task is None or task.done():
                task = asyncio.create_task(factory(), name=f"cp-profile-{key[:40]}")
                self._profile_flights[key] = task
        try:
            return dict(await task)
        finally:
            if task.done():
                async with self._flight_lock:
                    if self._profile_flights.get(key) is task:
                        self._profile_flights.pop(key, None)

    async def _cached_creator(self, identity: dict[str, Any]) -> dict[str, Any] | None:
        now = int(time.time())
        key = identity.get("creator_key")
        row = await self.db.fetchone(
            "SELECT * FROM creator_points_current WHERE creator_key=? AND expires_ts>?",
            (str(key), now),
        ) if key else None
        if not row:
            return None
        return {
            "provider": "cache", "method": "creator_current", "username": row["username"], "account_id": row["account_id"],
            "player_id": row["player_id"], "creator_points": row["creator_points"], "observed_at": row["observed_at"],
            "provider_timestamp": row["provider_timestamp"], "success": True, "error_category": None,
            "response_fingerprint": row["response_fingerprint"], "archival": False,
            "cached_source": row["source"], "cached_confidence": row["confidence"],
        }

    async def _persist_observations(self, queue_id: int, level_id: str, observations: list[dict[str, Any]]) -> None:
        statements = []
        for item in observations:
            key = creator_key(account_id=item.get("account_id"), player_id=item.get("player_id"), username=item.get("username"))
            statements.append((
                "INSERT OR IGNORE INTO creator_points_provider_observations(queue_id,level_id,creator_key,provider,method,username,"
                "account_id,player_id,creator_points,observed_at,provider_timestamp,success,status_code,error_category,response_fingerprint,latency_ms) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (queue_id, level_id, key, str(item.get("provider") or "unknown"), str(item.get("method") or "unknown"), item.get("username"),
                 item.get("account_id"), item.get("player_id"), item.get("creator_points"), int(item.get("observed_at") or time.time()),
                 item.get("provider_timestamp"), int(bool(item.get("success"))), item.get("status_code"), item.get("error_category"),
                 item.get("response_fingerprint"), item.get("latency_ms")),
            ))
        if statements:
            await self.db.execute_transaction(statements, retry_safe=True)

    async def _persist_resolution(self, row, identity: dict[str, Any], accepted: dict[str, Any], observations: list[dict[str, Any]]) -> dict[str, Any]:
        now = int(time.time())
        points = int(accepted["creator_points"])
        source = str(accepted.get("source") or "unknown")
        confidence = str(accepted.get("confidence") or "verified_single_source")
        key = str(identity["creator_key"])
        observed_at = int((accepted.get("observation") or {}).get("observed_at") or now)
        fingerprint = (accepted.get("observation") or {}).get("response_fingerprint")
        states = ",".join("?" for _ in PPS_QUEUE_REFRESH_STATES)
        related = await self.db.fetchall(
            f"SELECT * FROM level_outreach_queue WHERE guild_id=? AND queue_state IN({states}) AND (id=? OR "  # nosec B608
            "(? IS NOT NULL AND uploader_account_id=?) OR (? IS NOT NULL AND uploader_user_id=?))",
            (int(row["guild_id"]), *PPS_QUEUE_REFRESH_STATES, int(row["id"]), identity.get("account_id"), identity.get("account_id"), identity.get("player_id"), identity.get("player_id")),
        )
        statements: list[tuple[str, tuple[Any, ...]]] = [
            (
                "INSERT INTO creator_points_current(creator_key,username,account_id,player_id,creator_points,source,confidence,observed_at,"
                "provider_timestamp,response_fingerprint,expires_ts,updated_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(creator_key) DO UPDATE SET "
                "username=excluded.username,account_id=excluded.account_id,player_id=excluded.player_id,creator_points=excluded.creator_points,"
                "source=excluded.source,confidence=excluded.confidence,observed_at=excluded.observed_at,provider_timestamp=excluded.provider_timestamp,"
                "response_fingerprint=excluded.response_fingerprint,expires_ts=excluded.expires_ts,updated_ts=excluded.updated_ts",
                (key, identity.get("username"), identity.get("account_id"), identity.get("player_id"), points, source, confidence, observed_at,
                 (accepted.get("observation") or {}).get("provider_timestamp"), fingerprint, now + self.settings.current_cp_ttl_seconds, now),
            )
        ]
        for item in related:
            if str(item["creator_points_status"] or "") == "manual_override":
                continue
            score = score_components(str(item["send_type"]), points, int(item["waiting_cycles"] or 0), self.priority.settings)
            queue_id = int(item["id"])
            statements.extend([
                (
                    "INSERT INTO level_outreach_cp_snapshots(queue_id,account_id,checked_ts,creator_points,lookup_status,error_text,source,confidence,"
                    "provider_timestamp,response_fingerprint) SELECT ?,?,?,?,?,NULL,?,?,?,? WHERE NOT EXISTS(SELECT 1 FROM level_outreach_cp_snapshots "
                    "WHERE queue_id=? AND creator_points=? AND source=? AND checked_ts>=?)",
                    (queue_id, identity.get("account_id"), observed_at, points, "ok", source, confidence,
                     (accepted.get("observation") or {}).get("provider_timestamp"), fingerprint, queue_id, points, source, observed_at - 300),
                ),
                (
                    "UPDATE level_outreach_queue SET uploader_name=COALESCE(?,uploader_name),uploader_account_id=COALESCE(?,uploader_account_id),"
                    "uploader_user_id=COALESCE(?,uploader_user_id),uploader_identity_confidence=?,creator_points_at_recommendation=COALESCE(creator_points_at_recommendation,?),"
                    "creator_points_checked_ts=COALESCE(creator_points_checked_ts,?),current_creator_points=?,current_creator_points_checked_ts=?,"
                    "creator_points_refresh_after_ts=?,creator_component_g=?,waiting_component_h=?,priority_points=?,priority_complete=1,"
                    "creator_points_status='resolved',creator_points_source=?,creator_points_confidence=?,creator_points_observed_at=?,"
                    "creator_points_pending_reason=NULL,creator_points_last_error_category=NULL,creator_points_profile_path=COALESCE(?,creator_points_profile_path),updated_ts=? WHERE id=?",
                    (identity.get("username"), identity.get("account_id"), identity.get("player_id"), identity.get("confidence"), points, observed_at,
                     points, observed_at, now + self.settings.current_cp_ttl_seconds, score["creator_component_g"], score["waiting_component_h"], score["priority_points"],
                     source, confidence, observed_at, next((x.get("profile_path") for x in observations if x.get("profile_path")), None), now, queue_id),
                ),
                ("UPDATE creator_points_resolution_jobs SET state='resolved',resolved_ts=?,last_error_category=NULL,last_error_summary=NULL,next_attempt_ts=?,updated_ts=? WHERE queue_id=?", (now, now + self.settings.current_cp_ttl_seconds, now, queue_id)),
            ])
        old = row["current_creator_points"]
        event = "cp_resolved" if old is None else ("cp_changed" if int(old) != points else "cp_verified")
        correlation = str(row["correlation_id"] or new_correlation_id("cp-resolution"))
        identity_changed = (
            row["uploader_account_id"] != identity.get("account_id")
            or row["uploader_user_id"] != identity.get("player_id")
            or (not row["uploader_name"] and bool(identity.get("username")))
        )
        if identity_changed:
            statements.append((
                "INSERT INTO workflow_events(correlation_id,workflow_type,entity_id,event,guild_id,actor_id,payload_json,created_ts) VALUES(?,?,?,?,?,NULL,?,?)",
                (correlation, "priority_system", f"queue:{int(row['id'])}", "cp_identity_resolved", int(row["guild_id"]), json.dumps({"username": identity.get("username"), "account_id": identity.get("account_id"), "player_id": identity.get("player_id"), "confidence": identity.get("confidence")}, separators=(",", ":")), now),
            ))
        statements.append((
            "INSERT INTO workflow_events(correlation_id,workflow_type,entity_id,event,guild_id,actor_id,payload_json,created_ts) VALUES(?,?,?,?,?,NULL,?,?)",
            (correlation, "priority_system", f"queue:{int(row['id'])}", event, int(row["guild_id"]), json.dumps({"old_creator_points": old, "creator_points": points, "source": source, "confidence": confidence}, separators=(",", ":")), now),
        ))
        await self.db.execute_transaction(statements, retry_safe=True)
        await self._refresh_public_cache()
        return {"resolved": True, "state": "resolved", "creator_points": points, "identity": identity, "source": source, "confidence": confidence}

    async def _schedule_failure(self, job, category: str, summary: str, *, minimum_delay: int = 0) -> None:
        if job is None:
            return
        now = int(time.time())
        attempt = int(job["attempt_count"] or 0) + 1
        schedule = self.settings.retry_schedule_seconds
        delay = schedule[min(attempt - 1, len(schedule) - 1)] if attempt <= len(schedule) else 86400
        jitter = min(30, max(1, delay // 20))
        delay = max(delay, max(0, int(minimum_delay or 0)))
        next_attempt = now + delay + random.randint(0, jitter)
        first = int(job["first_pending_ts"] or now)
        age = now - first
        state = "conflict" if category == "conflict" else ("identity_unresolved" if category == "identity_mismatch" else "providers_unavailable")
        attention = age >= self.settings.attention_after_seconds
        if attention:
            state = "needs_attention"
        queue_id = int(job["queue_id"])
        statements: list[tuple[str, tuple[Any, ...]]] = [
            ("UPDATE creator_points_resolution_jobs SET state=?,attempt_count=?,next_attempt_ts=?,last_error_category=?,last_error_summary=?,updated_ts=? WHERE queue_id=?", (state, attempt, next_attempt, category, str(summary)[:300], now, queue_id)),
            ("UPDATE level_outreach_queue SET current_creator_points=NULL,creator_component_g=NULL,priority_points=NULL,priority_complete=0,creator_points_status=?,creator_points_pending_reason='creator_points',creator_points_last_error_category=?,last_public_priority_band=NULL,updated_ts=? WHERE id=? AND creator_points_status!='manual_override'", (state, category, now, queue_id)),
        ]
        events = ["cp_resolution_retry_scheduled"]
        previous_error = job["last_error_category"] if "last_error_category" in job.keys() else None
        if category == "conflict" and previous_error != "conflict":
            events.append("cp_resolution_conflict")
        attention_emitted = job["attention_emitted_ts"] if "attention_emitted_ts" in job.keys() else None
        escalation_emitted = job["escalation_emitted_ts"] if "escalation_emitted_ts" in job.keys() else None
        if attention and not attention_emitted:
            statements.append(("UPDATE creator_points_resolution_jobs SET attention_emitted_ts=? WHERE queue_id=?", (now, queue_id)))
            events.append("cp_resolution_attention_required")
        if age >= self.settings.escalation_after_seconds and not escalation_emitted:
            statements.append(("UPDATE creator_points_resolution_jobs SET escalation_emitted_ts=?,priority=200 WHERE queue_id=?", (now, queue_id)))
            events.append("cp_resolution_escalated")
        for event in events:
            statements.append(("INSERT INTO workflow_events(correlation_id,workflow_type,entity_id,event,payload_json,created_ts) VALUES(?,'priority_system',?,?,?,?)", (new_correlation_id("cp-resolution"), f"queue:{queue_id}", event, json.dumps({"error_category": category, "next_attempt_ts": next_attempt}), now)))
        await self.db.execute_transaction(statements, retry_safe=True)

    async def retry(self, queue_id: int) -> dict[str, Any]:
        await self.enqueue(queue_id, priority=200, immediate=True)
        return await self.resolve_queue(queue_id, force=True)

    async def diagnostics(self, queue_id: int) -> dict[str, Any]:
        job = await self.db.fetchone("SELECT * FROM creator_points_resolution_jobs WHERE queue_id=?", (int(queue_id),))
        observations = await self.db.fetchall(
            "SELECT provider,method,username,account_id,player_id,creator_points,observed_at,provider_timestamp,success,status_code,error_category,latency_ms "
            "FROM creator_points_provider_observations WHERE queue_id=? ORDER BY observed_at DESC,id DESC LIMIT 50", (int(queue_id),),
        )
        return {"job": dict(job) if job else None, "observations": [dict(item) for item in observations]}

    async def health(self) -> dict[str, Any]:
        rows = await self.db.fetchall(
            "SELECT provider,method,COUNT(*) attempts,SUM(success) successes,SUM(CASE WHEN success=1 AND creator_points IS NOT NULL THEN 1 ELSE 0 END) cp_successes,"
            "SUM(CASE WHEN error_category IN('malformed','cp_missing') THEN 1 ELSE 0 END) parse_failures,AVG(latency_ms) average_latency_ms,"
            "MAX(CASE WHEN success=1 THEN observed_at END) last_success,MAX(CASE WHEN success=0 THEN observed_at END) last_error "
            "FROM creator_points_provider_observations WHERE observed_at>=? GROUP BY provider,method",
            (int(time.time()) - 86400,),
        )
        cog = self.bot.get_cog("RequestLevelsCog")
        circuits = cog.validation_provider_snapshot() if cog else {}
        providers: dict[str, dict[str, Any]] = defaultdict(lambda: {"attempts": 0, "successes": 0, "cp_successes": 0, "parse_failures": 0, "methods": []})
        for row in rows:
            item = dict(row)
            provider = str(item.pop("provider"))
            providers[provider]["attempts"] += int(item.get("attempts") or 0)
            providers[provider]["successes"] += int(item.get("successes") or 0)
            providers[provider]["cp_successes"] += int(item.get("cp_successes") or 0)
            providers[provider]["parse_failures"] += int(item.get("parse_failures") or 0)
            providers[provider]["methods"].append(item)
        latency_rows = await self.db.fetchall(
            "SELECT provider,latency_ms,success,username,account_id,player_id FROM creator_points_provider_observations "
            "WHERE observed_at>=? AND latency_ms IS NOT NULL",
            (int(time.time()) - 86400,),
        )
        latencies: dict[str, list[float]] = defaultdict(list)
        identity_attempts: dict[str, int] = defaultdict(int)
        identity_successes: dict[str, int] = defaultdict(int)
        for row in latency_rows:
            provider = str(row["provider"])
            latencies[provider].append(float(row["latency_ms"]))
            identity_attempts[provider] += 1
            if row["username"] or row["account_id"] or row["player_id"]:
                identity_successes[provider] += 1
        for provider in ("gdbrowser", "boomlings", "gdhistory", "gdrateplus"):
            entry = providers[provider]
            circuit = circuits.get(provider, {})
            entry["circuit_open"] = bool(circuit.get("circuit_open"))
            entry["status"] = "unavailable" if entry["circuit_open"] else ("healthy" if entry["successes"] else "degraded")
            entry["last_error_category"] = circuit.get("last_failure_kind")
            entry["success_rate"] = round(entry["successes"] / entry["attempts"], 4) if entry["attempts"] else None
            entry["cp_success_rate"] = round(entry["cp_successes"] / entry["attempts"], 4) if entry["attempts"] else None
            provider_latencies = latencies[provider]
            entry["average_latency_ms"] = round(sum(provider_latencies) / len(provider_latencies), 2) if provider_latencies else None
            entry["median_latency_ms"] = round(statistics.median(latencies[provider]), 2) if latencies[provider] else None
            entry["identity_success_rate"] = (
                round(identity_successes[provider] / identity_attempts[provider], 4)
                if identity_attempts[provider]
                else None
            )
            entry["last_success"] = max((int(item.get("last_success") or 0) for item in entry["methods"]), default=0) or None
            entry["last_error"] = max((int(item.get("last_error") or 0) for item in entry["methods"]), default=0) or None
            if not entry["circuit_open"]:
                entry["status"] = (
                    "healthy"
                    if entry["attempts"] and entry["success_rate"] is not None and entry["success_rate"] >= 0.5
                    else "degraded"
                )
        return dict(providers)

    async def _refresh_public_cache(self) -> None:
        cog = self.bot.get_cog("PrioritySystemCog")
        if cog and hasattr(cog, "refresh_public_level_cache"):
            await cog.refresh_public_level_cache()

    @staticmethod
    def _normalize_error(value: Any) -> str:
        text = str(value or "").casefold()
        return {
            "network_error": "provider_unavailable", "upstream_unavailable": "provider_unavailable", "access_denied": "forbidden",
            "invalid_response": "malformed", "not_indexed": "not_found", "circuit_open": "provider_unavailable",
        }.get(text, text if text in {"timeout", "rate_limited", "forbidden", "not_found", "malformed", "identity_mismatch", "cp_missing", "conflict", "internal"} else "provider_unavailable")

    @staticmethod
    def _error_observation(
        provider: str,
        method: str,
        category: str,
        started: float,
        identity: dict[str, Any] | None = None,
        *,
        status_code: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> dict[str, Any]:
        identity = identity or {}
        return {
            "provider": provider, "method": method, "username": identity.get("username"), "account_id": identity.get("account_id"),
            "player_id": identity.get("player_id"), "creator_points": None, "observed_at": int(time.time()), "success": False,
            "status_code": status_code, "error_category": category, "response_fingerprint": response_fingerprint(f"{provider}:{method}:{category}:{status_code}"),
            "retry_after_seconds": max(0, int(retry_after_seconds or 0)),
            "latency_ms": round((time.perf_counter() - started) * 1000, 2), "archival": False,
        }
