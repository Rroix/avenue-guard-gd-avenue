from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import time
from collections import defaultdict, deque
from typing import Any
from urllib.parse import parse_qs, urlsplit

import discord

from services.priority_system import PrioritySystemService
from utils.errors import log_error
from utils.keepalive import get_keepalive_status, get_runtime_health
from utils.priority_system import (
    normalize_send_type,
    priority_settings,
    score_components,
)
from utils.staff_auth import (
    StaffPrincipal,
    capability_set,
    new_csrf_token,
    new_session_token,
    resolve_staff_role,
    token_hash,
    tokens_match,
)
from utils.workflows import new_correlation_id


class PortalError(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        self.status = int(status)
        self.code = str(code)
        self.message = str(message)
        super().__init__(message)


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _bounded_text(value: Any, limit: int, *, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise PortalError(400, "required_field", "A required field is missing")
    if len(text) > limit:
        raise PortalError(400, "field_too_long", "One of the submitted fields is too long")
    return text


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    row = _row_dict(value)
    return {key: _json_safe(item) for key, item in row.items()} if row else str(value)


class StaffPortalService:
    """Authoritative staff portal service shared by HTTP and Discord recovery tools."""

    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        priority_cog = bot.get_cog("PrioritySystemCog")
        self.priority = (
            priority_cog.service if priority_cog is not None else PrioritySystemService(bot)
        )
        self._claim_lock = asyncio.Lock()
        self._application_lock = asyncio.Lock()
        self._episode_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._rate_lock = asyncio.Lock()
        self._rate_windows: dict[str, deque[float]] = defaultdict(deque)

    @property
    def enabled(self) -> bool:
        return bool(self.bot.config.get("staff_portal", "enabled", default=False))

    @property
    def guild_id(self) -> int:
        return self.bot.config.get_int("guild", "allowed_guild_id", default=0)

    @property
    def service_token(self) -> str:
        return str(os.getenv("STAFF_API_TOKEN", "") or "").strip()

    def _require_service_token(self, headers: dict[str, str]) -> None:
        expected = self.service_token
        supplied = str(headers.get("x-avenue-portal-key") or "")
        if not self.enabled or not expected:
            raise PortalError(503, "portal_unavailable", "The staff portal is not configured")
        if not supplied or not secrets_compare(supplied, expected):
            raise PortalError(401, "service_auth_failed", "The portal service could not authenticate")

    async def _member(self, user_id: int):
        guild = self.bot.get_guild(self.guild_id)
        if guild is None:
            raise PortalError(503, "guild_unavailable", "GD Avenue is temporarily unavailable")
        member = guild.get_member(int(user_id))
        if member is None:
            try:
                member = await guild.fetch_member(int(user_id))
            except (discord.NotFound, discord.Forbidden):
                member = None
            except Exception as exc:
                raise PortalError(503, "discord_unavailable", "Discord membership could not be verified") from exc
        return guild, member

    def _principal_from_member(self, member, *, session_hash: str = "") -> StaffPrincipal:
        role_ids = tuple(int(role.id) for role in getattr(member, "roles", ()) if getattr(role, "id", 0))
        role = resolve_staff_role(member.id, role_ids, self.bot.config)
        avatar = getattr(getattr(member, "display_avatar", None), "url", "")
        return StaffPrincipal(
            user_id=int(member.id),
            guild_id=self.guild_id,
            display_name=str(getattr(member, "display_name", "") or getattr(member, "name", "Staff"))[:100],
            avatar_url=str(avatar or "")[:1000],
            role=role,
            role_ids=role_ids,
            capabilities=capability_set(role),
            session_hash=session_hash,
        )

    async def create_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            user_id = int(payload.get("user_id") or 0)
        except (TypeError, ValueError):
            user_id = 0
        if user_id <= 0:
            raise PortalError(400, "invalid_identity", "Discord identity was not supplied")
        _guild, member = await self._member(user_id)
        if member is None:
            raise PortalError(403, "not_a_member", "You must be a GD Avenue member to continue")
        principal = self._principal_from_member(member)
        purpose = str(payload.get("purpose") or "staff").strip().casefold()
        if purpose not in {"staff", "apply"}:
            raise PortalError(400, "invalid_session_purpose", "The requested sign-in flow is invalid")
        if purpose == "staff" and not principal.can("staff.access"):
            raise PortalError(
                403,
                "staff_role_required",
                "Your Discord account does not currently have staff portal access",
            )
        raw_session = new_session_token()
        raw_csrf = new_csrf_token()
        now = int(time.time())
        ttl_hours = self.bot.config.get_int("staff_portal", "session_ttl_hours", default=8)
        expires_ts = now + max(1, min(168, ttl_hours)) * 3600
        await self.db.execute(
            "INSERT INTO staff_web_sessions(token_hash,guild_id,user_id,display_name,avatar_url,role_key,csrf_hash,"
            "created_ts,last_seen_ts,expires_ts,revoked_ts) VALUES(?,?,?,?,?,?,?,?,?,?,NULL)",
            (
                token_hash(raw_session),
                self.guild_id,
                principal.user_id,
                principal.display_name,
                principal.avatar_url,
                principal.role,
                token_hash(raw_csrf),
                now,
                now,
                expires_ts,
            ),
        )
        return {
            "session_token": raw_session,
            "csrf_token": raw_csrf,
            "expires_ts": expires_ts,
            "user": self._principal_payload(principal),
        }

    async def _session_principal(self, headers: dict[str, str]) -> StaffPrincipal:
        raw = str(headers.get("x-staff-session") or "").strip()
        if not raw:
            raise PortalError(401, "session_required", "Sign in with Discord to continue")
        digest = token_hash(raw)
        now = int(time.time())
        row = await self.db.fetchone(
            "SELECT * FROM staff_web_sessions WHERE token_hash=? AND revoked_ts IS NULL AND expires_ts>?",
            (digest, now),
        )
        if row is None:
            raise PortalError(401, "session_expired", "Your session expired; sign in again")
        _guild, member = await self._member(int(row["user_id"]))
        if member is None:
            await self.db.execute(
                "UPDATE staff_web_sessions SET revoked_ts=? WHERE token_hash=? AND revoked_ts IS NULL",
                (now, digest),
            )
            raise PortalError(403, "membership_required", "GD Avenue membership is required")
        principal = self._principal_from_member(member, session_hash=digest)
        if (
            now - int(row["last_seen_ts"] or 0) >= 300
            or str(row["display_name"] or "") != principal.display_name
            or str(row["avatar_url"] or "") != principal.avatar_url
            or str(row["role_key"] or "") != principal.role
        ):
            await self.db.execute(
                "UPDATE staff_web_sessions SET display_name=?,avatar_url=?,role_key=?,last_seen_ts=? WHERE token_hash=?",
                (principal.display_name, principal.avatar_url, principal.role, now, digest),
            )
        return principal

    async def revoke_session(self, principal: StaffPrincipal) -> None:
        await self.db.execute(
            "UPDATE staff_web_sessions SET revoked_ts=? WHERE token_hash=? AND revoked_ts IS NULL",
            (int(time.time()), principal.session_hash),
        )

    def _require_csrf(self, headers: dict[str, str], session_row: Any) -> None:
        supplied = str(headers.get("x-csrf-token") or "")
        if not supplied or not tokens_match(supplied, str(session_row["csrf_hash"] or "")):
            raise PortalError(403, "csrf_failed", "This action could not be verified; refresh and try again")

    async def _csrf_row(self, principal: StaffPrincipal):
        return await self.db.fetchone(
            "SELECT csrf_hash FROM staff_web_sessions WHERE token_hash=? AND revoked_ts IS NULL",
            (principal.session_hash,),
        )

    @staticmethod
    def _principal_payload(principal: StaffPrincipal) -> dict[str, Any]:
        return {
            "id": str(principal.user_id),
            "display_name": principal.display_name,
            "avatar_url": principal.avatar_url,
            "role": principal.role,
            "staff_access": principal.can("staff.access"),
            "capabilities": sorted(principal.capabilities),
        }

    async def _rate_limit(self, principal: StaffPrincipal, *, mutation: bool) -> None:
        key = f"{principal.user_id}:{'w' if mutation else 'r'}"
        limit = 45 if mutation else 180
        now = time.monotonic()
        async with self._rate_lock:
            window = self._rate_windows[key]
            while window and now - window[0] > 60:
                window.popleft()
            if len(window) >= limit:
                raise PortalError(429, "rate_limited", "Too many requests; wait a moment and try again")
            window.append(now)

    async def handle_request(
        self,
        method: str,
        raw_path: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, Any]]:
        self._require_service_token(headers)
        method = str(method or "GET").upper()
        parsed = urlsplit(raw_path)
        path = parsed.path.rstrip("/") or "/"
        query = {key: values[-1] for key, values in parse_qs(parsed.query).items() if values}
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PortalError(400, "invalid_json", "The request body is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise PortalError(400, "invalid_body", "The request body must be an object")

        if path == "/api/staff/auth/session" and method == "POST":
            return 201, await self.create_session(payload)

        principal = await self._session_principal(headers)
        mutation = method in {"POST", "PATCH", "PUT", "DELETE"}
        await self._rate_limit(principal, mutation=mutation)
        if mutation:
            session_row = await self._csrf_row(principal)
            if session_row is None:
                raise PortalError(401, "session_expired", "Your session expired; sign in again")
            self._require_csrf(headers, session_row)

        if path == "/api/staff/session":
            if method == "DELETE":
                await self.revoke_session(principal)
                return 200, {"ok": True}
            principal.require("staff.access")
            return 200, {"user": self._principal_payload(principal)}
        if path == "/api/apply/session" and method == "GET":
            return 200, {"user": self._principal_payload(principal)}

        idempotency_key = str(headers.get("idempotency-key") or "").strip()
        if mutation and path != "/api/staff/session":
            if not 12 <= len(idempotency_key) <= 180:
                raise PortalError(400, "idempotency_required", "Refresh the page and try this action again")
            previous = await self.db.fetchone(
                "SELECT operation,response_json FROM staff_idempotency WHERE idempotency_key=? AND user_id=? AND expires_ts>?",
                (idempotency_key, principal.user_id, int(time.time())),
            )
            if previous:
                if str(previous["operation"]) != f"{method} {path}":
                    raise PortalError(409, "idempotency_conflict", "That action key was already used")
                return 200, _json_object(previous["response_json"])

        if path.startswith("/api/apply"):
            principal.require("applications.apply")
            status, response = await self._handle_apply(method, path, principal, payload)
            if mutation:
                await self._store_idempotent_response(
                    idempotency_key, principal, method, path, response
                )
            return status, response

        principal.require("staff.access")

        status, response = await self._handle_staff(method, path, query, principal, payload, idempotency_key)
        if mutation and path != "/api/staff/session":
            await self._store_idempotent_response(
                idempotency_key, principal, method, path, response
            )
        return status, response

    async def _store_idempotent_response(
        self,
        idempotency_key: str,
        principal: StaffPrincipal,
        method: str,
        path: str,
        response: dict[str, Any],
    ) -> None:
        now = int(time.time())
        await self.db.execute(
            "INSERT OR IGNORE INTO staff_idempotency(idempotency_key,user_id,operation,response_json,created_ts,expires_ts) "
            "VALUES(?,?,?,?,?,?)",
            (
                idempotency_key,
                principal.user_id,
                f"{method} {path}",
                json.dumps(response, separators=(",", ":")),
                now,
                now + 86400,
            ),
        )

    async def _handle_staff(
        self,
        method: str,
        path: str,
        query: dict[str, str],
        principal: StaffPrincipal,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> tuple[int, dict[str, Any]]:
        if path == "/api/staff/overview" and method == "GET":
            return 200, await self.overview(principal)
        if path == "/api/staff/queue" and method == "GET":
            return 200, await self.queue(principal, query)
        if path == "/api/staff/outreach" and method == "GET":
            return 200, await self.outreach_list(principal, query)
        if path == "/api/staff/outreach" and method == "POST":
            return 201, await self.record_outreach(principal, payload, idempotency_key)
        if path == "/api/staff/tasks":
            if method == "GET":
                return 200, await self.tasks(principal, query)
            if method == "POST":
                return 201, await self.create_task(principal, payload)
        if path == "/api/staff/notes":
            if method == "GET":
                return 200, await self.notes(principal, query)
            if method == "POST":
                return 201, await self.create_note(principal, payload)
        if path == "/api/staff/team" and method == "GET":
            return 200, await self.team(principal)
        if path == "/api/staff/statistics" and method == "GET":
            return 200, await self.statistics(principal)
        if path == "/api/staff/qa" and method == "GET":
            principal.require("review.qa")
            return 200, await self.qa_list(principal, query)
        if path == "/api/staff/applications" and method == "GET":
            principal.require("applications.review_judge")
            return 200, await self.applications(principal, query)
        if path == "/api/staff/staff" and method == "GET":
            principal.require("staff.manage")
            return 200, await self.staff_list(principal)
        if path == "/api/staff/operations" and method == "GET":
            principal.require("operations.view")
            return 200, await self.operations(principal)
        if path == "/api/staff/pps":
            principal.require("pps.manage_cycles")
            if method == "GET":
                return 200, await self.pps_admin(principal)
            if method == "POST":
                return 200, await self.pps_action(principal, payload)
        if path == "/api/staff/audit" and method == "GET":
            principal.require("audit.view")
            return 200, await self.audit(principal, query)
        if path == "/api/staff/configuration":
            principal.require("config.manage_safe")
            if method == "GET":
                return 200, await self.safe_configuration()
            if method == "PATCH":
                return 200, await self.update_safe_configuration(principal, payload)
        if path == "/api/staff/search" and method == "GET":
            return 200, await self.search(principal, query.get("q", ""))

        queue_match = re.fullmatch(r"/api/staff/queue/(\d+)(?:/(claim|release|reassign|state|requeue|tier))?", path)
        if queue_match:
            queue_id = int(queue_match.group(1))
            action = queue_match.group(2)
            if method == "GET" and not action:
                return 200, await self.queue_detail(principal, queue_id)
            if method == "POST" and action:
                return 200, await self.queue_action(principal, queue_id, action, payload)

        task_match = re.fullmatch(r"/api/staff/tasks/(\d+)", path)
        if task_match and method == "PATCH":
            return 200, await self.update_task(principal, int(task_match.group(1)), payload)
        note_match = re.fullmatch(r"/api/staff/notes/(\d+)", path)
        if note_match and method == "PATCH":
            return 200, await self.update_note(principal, int(note_match.group(1)), payload)
        qa_match = re.fullmatch(r"/api/staff/qa/(\d+)", path)
        if qa_match and method == "POST":
            principal.require("review.qa")
            return 200, await self.qa_action(principal, int(qa_match.group(1)), payload)
        app_match = re.fullmatch(r"/api/staff/applications/(\d+)/(action|note)", path)
        if app_match and method == "POST":
            principal.require("applications.review_judge")
            if app_match.group(2) == "note":
                return 201, await self.application_note(principal, int(app_match.group(1)), payload)
            return 200, await self.application_action(principal, int(app_match.group(1)), payload)
        staff_match = re.fullmatch(r"/api/staff/staff/(\d+)/action", path)
        if staff_match and method == "POST":
            principal.require("staff.manage")
            return 200, await self.staff_action(principal, int(staff_match.group(1)), payload)
        raise PortalError(404, "not_found", "That portal resource does not exist")

    async def overview(self, principal: StaffPrincipal) -> dict[str, Any]:
        await self._sync_system_tasks(principal.guild_id)
        now = int(time.time())
        month_start = now - 31 * 86400
        stale_cutoff = now - await self._claim_stale_seconds()
        claim_row = await self.db.fetchone(
            "SELECT COUNT(*) AS active,SUM(CASE WHEN claimed_ts<? THEN 1 ELSE 0 END) AS stale "
            "FROM staff_queue_claims WHERE guild_id=? AND claimed_by=? AND claim_state='active'",
            (stale_cutoff, principal.guild_id, principal.user_id),
        )
        task_row = await self.db.fetchone(
            "SELECT COUNT(*) AS total,SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done,"
            "SUM(CASE WHEN status IN('todo','in_progress') AND due_ts IS NOT NULL AND due_ts<=? THEN 1 ELSE 0 END) AS due "
            "FROM staff_tasks WHERE guild_id=? AND (assignee_id=? OR (task_type='personal' AND created_by=?)) "
            "AND status!='cancelled'",
            (now + 86400, principal.guild_id, principal.user_id, principal.user_id),
        )
        reviews = await self.db.fetchone(
            "SELECT COUNT(*) AS total,SUM(CASE WHEN reviewed_ts>=? THEN 1 ELSE 0 END) AS c "
            "FROM level_request_submissions WHERE guild_id=? AND reviewed_by=? AND status='reviewed'",
            (month_start, principal.guild_id, principal.user_id),
        )
        outreach = await self.db.fetchone(
            "SELECT SUM(CASE WHEN a.created_ts>=? THEN 1 ELSE 0 END) AS attempts,"
            "SUM(CASE WHEN a.created_ts>=? AND status='submitted_to_mod' THEN 1 ELSE 0 END) AS submitted,"
            "SUM(CASE WHEN status='submitted_to_mod' THEN 1 ELSE 0 END) AS submitted_total "
            "FROM level_outreach_attempts a JOIN level_outreach_cycles c ON c.id=a.cycle_id "
            "WHERE c.guild_id=? AND a.actor_id=?",
            (month_start, month_start, principal.guild_id, principal.user_id),
        )
        active_days = await self.db.fetchone(
            "SELECT COUNT(DISTINCT day) AS c FROM ("
            "SELECT date(reviewed_ts,'unixepoch') AS day FROM level_request_submissions "
            "WHERE guild_id=? AND reviewed_by=? AND reviewed_ts>=? "
            "UNION SELECT date(a.created_ts,'unixepoch') FROM level_outreach_attempts a "
            "JOIN level_outreach_cycles c ON c.id=a.cycle_id WHERE c.guild_id=? AND a.actor_id=? AND a.created_ts>=? "
            "UNION SELECT date(completed_ts,'unixepoch') FROM staff_tasks WHERE guild_id=? AND assignee_id=? "
            "AND completed_ts>=?)",
            (
                principal.guild_id,
                principal.user_id,
                month_start,
                principal.guild_id,
                principal.user_id,
                month_start,
                principal.guild_id,
                principal.user_id,
                month_start,
            ),
        )
        followups = await self.db.fetchone(
            "SELECT COUNT(*) AS c FROM level_outreach_queue q JOIN staff_queue_claims c ON c.queue_id=q.id "
            "WHERE q.guild_id=? AND c.claim_state='active' AND c.claimed_by=? "
            "AND q.queue_state='awaiting_outcome' AND q.outcome_window_due_ts IS NOT NULL "
            "AND q.outcome_window_due_ts<=?",
            (principal.guild_id, principal.user_id, now + 86400),
        )
        pipeline_rows = await self.db.fetchall(
            "SELECT queue_state,COUNT(*) AS c FROM level_outreach_queue WHERE guild_id=? GROUP BY queue_state",
            (principal.guild_id,),
        )
        application_count = await self.db.fetchone(
            "SELECT COUNT(*) AS c FROM staff_applications WHERE guild_id=? AND status IN('submitted','under_review','interview','hold')",
            (principal.guild_id,),
        )
        events = await self.db.fetchall(
            "SELECT event,entity_id,actor_id,created_ts FROM workflow_events WHERE guild_id=? "
            "ORDER BY created_ts DESC,id DESC LIMIT 8",
            (principal.guild_id,),
        )
        milestones = self._milestones(
            int(reviews["total"] or 0), int(outreach["submitted_total"] or 0)
        )
        await self._persist_milestones(principal, milestones)
        return {
            "user": self._principal_payload(principal),
            "summary": {
                "active_claims": int(claim_row["active"] or 0),
                "stale_claims": int(claim_row["stale"] or 0),
                "tasks_remaining": max(0, int(task_row["total"] or 0) - int(task_row["done"] or 0)),
                "tasks_due": int(task_row["due"] or 0),
                "followups_due": int(followups["c"] or 0),
            },
            "progress": {
                "reviews_month": int(reviews["c"] or 0),
                "tasks_done": int(task_row["done"] or 0),
                "tasks_total": int(task_row["total"] or 0),
                "outreach_attempts": int(outreach["attempts"] or 0),
                "confirmed_submissions": int(outreach["submitted"] or 0),
                "active_days": int(active_days["c"] or 0),
                "milestones": milestones,
            },
            "pipeline": {str(row["queue_state"]): int(row["c"] or 0) for row in pipeline_rows},
            "pending_applications": int(application_count["c"] or 0),
            "recent_activity": [_row_dict(row) for row in events],
        }

    @staticmethod
    def _milestones(reviews: int, submissions: int) -> list[dict[str, Any]]:
        items = []
        for target in (10, 25, 50, 100):
            if reviews >= target:
                items.append({"key": f"reviews_{target}", "label": f"{target} reviews"})
        for target in (1, 10):
            if submissions >= target:
                label = "First confirmed submission" if target == 1 else "10 confirmed submissions"
                items.append({"key": f"submissions_{target}", "label": label})
        return items

    async def _claim_stale_seconds(self) -> int:
        saved = await self.db.get_runtime_setting("staff_portal.safe_config", {})
        hours = int(
            (saved or {}).get("claim_stale_hours")
            or self.bot.config.get_int("staff_portal", "claim_stale_hours", default=48)
        )
        return max(1, min(720, hours)) * 3600

    async def _persist_milestones(
        self, principal: StaffPrincipal, milestones: list[dict[str, Any]]
    ) -> None:
        now = int(time.time())
        existing = await self.db.fetchall(
            "SELECT milestone_key FROM staff_milestones WHERE guild_id=? AND user_id=?",
            (principal.guild_id, principal.user_id),
        )
        achieved = {str(row["milestone_key"]) for row in existing}
        for milestone in milestones:
            if milestone["key"] in achieved:
                continue
            await self.db.execute(
                "INSERT OR IGNORE INTO staff_milestones(guild_id,user_id,milestone_key,achieved_ts) "
                "VALUES(?,?,?,?)",
                (principal.guild_id, principal.user_id, milestone["key"], now),
            )

    async def _sync_system_tasks(self, guild_id: int) -> None:
        """Materialize actionable attention items without duplicating them."""
        now = int(time.time())
        stale_cutoff = now - await self._claim_stale_seconds()
        rows = await self.db.fetchall(
            "SELECT c.queue_id,c.claimed_by,q.level_id,q.current_level_name,c.claimed_ts "
            "FROM staff_queue_claims c JOIN level_outreach_queue q ON q.id=c.queue_id "
            "WHERE c.guild_id=? AND c.claim_state='active' AND c.claimed_ts<? LIMIT 100",
            (guild_id, stale_cutoff),
        )
        desired: dict[str, tuple[Any, ...]] = {}
        for row in rows:
            key = f"stale-claim:{int(row['queue_id'])}"
            title = f"Review stale claim for {row['current_level_name'] or row['level_id']}"
            desired[key] = (
                guild_id,
                "system",
                title[:180],
                "This queue claim passed the configured stale threshold.",
                "high",
                "todo",
                0,
                int(row["claimed_by"]),
                now,
                now,
                now,
                "level",
                str(row["queue_id"]),
                key,
            )
        cp_rows = await self.db.fetchall(
            "SELECT id,level_id,current_level_name FROM level_outreach_queue WHERE guild_id=? "
            "AND queue_state IN('queued','in_cycle') AND current_creator_points IS NULL LIMIT 100",
            (guild_id,),
        )
        for row in cp_rows:
            key = f"cp-unresolved:{int(row['id'])}"
            title = f"Resolve creator points for {row['current_level_name'] or row['level_id']}"
            desired[key] = (
                guild_id,
                "system",
                title[:180],
                "Creator Points are unknown, so the priority score is incomplete.",
                "normal",
                "todo",
                0,
                None,
                now,
                now,
                None,
                "level",
                str(row["id"]),
                key,
            )
        outcome_rows = await self.db.fetchall(
            "SELECT q.id,q.level_id,q.current_level_name,q.outcome_window_due_ts,c.claimed_by "
            "FROM level_outreach_queue q LEFT JOIN staff_queue_claims c ON c.queue_id=q.id "
            "AND c.claim_state='active' WHERE q.guild_id=? AND q.queue_state='awaiting_outcome' "
            "AND q.outcome_window_due_ts IS NOT NULL AND q.outcome_window_due_ts<=? LIMIT 100",
            (guild_id, now + 86400),
        )
        for row in outcome_rows:
            key = f"outcome-window:{int(row['id'])}"
            title = f"Check outcome for {row['current_level_name'] or row['level_id']}"
            desired[key] = (
                guild_id,
                "system",
                title[:180],
                "The outcome observation window is due or ends within one day.",
                "high",
                "todo",
                0,
                row["claimed_by"],
                now,
                now,
                row["outcome_window_due_ts"],
                "level",
                str(row["id"]),
                key,
            )
        application_rows = await self.db.fetchall(
            "SELECT id FROM staff_applications WHERE guild_id=? AND status IN('submitted','under_review','interview','hold') "
            "AND updated_ts<=? LIMIT 100",
            (guild_id, now - 7 * 86400),
        )
        for row in application_rows:
            key = f"application-waiting:{int(row['id'])}"
            desired[key] = (
                guild_id,
                "system",
                f"Application #{int(row['id'])} needs attention",
                "This application has not changed stage in seven days.",
                "normal",
                "todo",
                0,
                None,
                now,
                now,
                now,
                "application",
                str(row["id"]),
                key,
            )
        active_keys = set(desired)
        existing = await self.db.fetchall(
            "SELECT id,system_key,title,description,priority,status,assignee_id,due_ts FROM staff_tasks "
            "WHERE guild_id=? AND system_key IS NOT NULL",
            (guild_id,),
        )
        existing_by_key = {str(row["system_key"]): row for row in existing}
        for key, values in desired.items():
            current = existing_by_key.get(key)
            if current is None:
                await self.db.execute(
                    "INSERT INTO staff_tasks(guild_id,task_type,title,description,priority,status,created_by,"
                    "assignee_id,created_ts,updated_ts,due_ts,linked_entity_type,linked_entity_id,system_key) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                    values,
                )
                continue
            if str(current["status"]) == "done":
                continue
            changed = (
                str(current["title"]) != values[2]
                or str(current["description"]) != values[3]
                or str(current["priority"]) != values[4]
                or current["assignee_id"] != values[7]
                or current["due_ts"] != values[10]
            )
            if changed:
                await self.db.execute(
                    "UPDATE staff_tasks SET title=?,description=?,priority=?,assignee_id=?,due_ts=?,updated_ts=? "
                    "WHERE id=? AND status!='done'",
                    (
                        values[2],
                        values[3],
                        values[4],
                        values[7],
                        values[10],
                        now,
                        int(current["id"]),
                    ),
                )
        for row in existing:
            if (
                str(row["system_key"]) not in active_keys
                and str(row["status"]) in {"todo", "in_progress"}
            ):
                await self.db.execute(
                    "UPDATE staff_tasks SET status='done',completed_ts=?,updated_ts=? WHERE id=?",
                    (now, now, int(row["id"])),
                )

    async def queue(self, principal: StaffPrincipal, query: dict[str, str]) -> dict[str, Any]:
        principal.require("queue.view")
        try:
            page = max(1, int(query.get("page", "1") or 1))
            limit = max(1, min(50, int(query.get("limit", "25") or 25)))
        except (TypeError, ValueError) as exc:
            raise PortalError(400, "invalid_pagination", "Page and limit must be numbers") from exc
        search = str(query.get("q", "")).strip()[:100]
        filter_key = str(query.get("filter", "all")).casefold()
        tier = normalize_send_type(query.get("tier"))
        where = ["q.guild_id=?"]
        params: list[Any] = [principal.guild_id]
        if search:
            where.append("(q.level_id=? OR LOWER(COALESCE(q.current_level_name,'')) LIKE ? OR LOWER(COALESCE(q.uploader_name,'')) LIKE ?)")
            params.extend([search, f"%{search.casefold()}%", f"%{search.casefold()}%"])
        if tier:
            where.append("q.send_type=?")
            params.append(tier)
        if filter_key == "unclaimed":
            where.append("(c.queue_id IS NULL OR c.claim_state!='active')")
        elif filter_key == "mine":
            where.append("c.claim_state='active' AND c.claimed_by=?")
            params.append(principal.user_id)
        elif filter_key == "claimed":
            where.append("c.claim_state='active'")
        elif filter_key == "cp_zero":
            where.append("q.current_creator_points=0")
        elif filter_key == "cp_unknown":
            where.append("q.current_creator_points IS NULL")
        elif filter_key == "waiting_3":
            where.append("q.waiting_cycles>=3")
        elif filter_key == "outreach":
            where.append("q.queue_state='in_cycle'")
        elif filter_key == "awaiting":
            where.append("q.queue_state='awaiting_outcome'")
        elif filter_key == "stale":
            where.append("c.claim_state='active' AND c.claimed_ts<?")
            params.append(int(time.time()) - await self._claim_stale_seconds())
        elif filter_key == "top_priority":
            where.append("q.priority_complete=1")
        clause = " AND ".join(where)
        count = await self.db.fetchone(
            f"SELECT COUNT(*) AS c FROM level_outreach_queue q LEFT JOIN staff_queue_claims c ON c.queue_id=q.id WHERE {clause}",  # nosec B608
            tuple(params),
        )
        ranked_clause = " AND ".join(where[1:]) or "1=1"
        rows = await self.db.fetchall(
            "WITH ranked_base AS (SELECT q.*,SUM(CASE WHEN q.queue_state IN('queued','in_cycle') THEN 1 ELSE 0 END) OVER(ORDER BY "
            "CASE WHEN q.priority_complete=1 THEN 0 ELSE 1 END,q.priority_points DESC,"
            "q.waiting_cycles DESC,q.queued_ts,q.id) AS active_rank "
            "FROM level_outreach_queue q WHERE q.guild_id=?), "
            "ranked AS (SELECT ranked_base.*,CASE WHEN queue_state IN('queued','in_cycle') THEN active_rank END AS exact_rank "
            "FROM ranked_base) "
            "SELECT q.*,c.claimed_by,c.claimed_ts,c.claim_state FROM ranked q "
            "LEFT JOIN staff_queue_claims c ON c.queue_id=q.id "
            f"WHERE {ranked_clause} ORDER BY q.exact_rank LIMIT ? OFFSET ?",  # nosec B608
            (principal.guild_id, *params[1:], limit, (page - 1) * limit),
        )
        return {
            "items": [await self._queue_payload(row) for row in rows],
            "page": page,
            "limit": limit,
            "total": int(count["c"] or 0),
        }

    async def _queue_fallback_rows(self, principal, query, page, limit):
        rows, _total = await self.priority.queue_rows(
            principal.guild_id,
            page=page,
            page_size=limit,
            states=("queued", "in_cycle", "awaiting_outcome", "paused", "withdrawn", "invalid", "rated"),
        )
        return rows

    async def _queue_payload(self, row: Any) -> dict[str, Any]:
        data = _row_dict(row)
        queued_ts = int(data.get("queued_ts") or 0)
        claim_ts = int(data.get("claimed_ts") or 0)
        stale = bool(claim_ts and claim_ts < int(time.time()) - await self._claim_stale_seconds())
        return {
            "id": int(data.get("id") or 0),
            "rank": int(data.get("exact_rank") or 0) or None,
            "level_id": str(data.get("level_id") or ""),
            "level_name": str(data.get("current_level_name") or "Unknown level"),
            "creator": str(data.get("uploader_name") or "Unknown creator"),
            "tier": str(data.get("send_type") or "rate"),
            "cp": data.get("current_creator_points"),
            "waiting_cycles": int(data.get("waiting_cycles") or 0),
            "components": {
                "f": data.get("prestige_component_f"),
                "g": data.get("creator_component_g"),
                "h": data.get("waiting_component_h"),
                "p": data.get("priority_points"),
                "complete": bool(data.get("priority_complete")),
            },
            "state": str(data.get("queue_state") or "queued"),
            "queued_ts": queued_ts,
            "claim": (
                {
                    "user_id": str(data.get("claimed_by")),
                    "claimed_ts": claim_ts,
                    "stale": stale,
                }
                if str(data.get("claim_state") or "") == "active"
                else None
            ),
            "submitted_to_mod_ts": data.get("submitted_to_mod_ts"),
            "outcome_window_due_ts": data.get("outcome_window_due_ts"),
        }

    async def queue_detail(self, principal: StaffPrincipal, queue_id: int) -> dict[str, Any]:
        principal.require("queue.view")
        row = await self.db.fetchone(
            "SELECT q.*,c.claimed_by,c.claimed_ts,c.claim_state FROM level_outreach_queue q "
            "LEFT JOIN staff_queue_claims c ON c.queue_id=q.id WHERE q.guild_id=? AND q.id=?",
            (principal.guild_id, queue_id),
        )
        if row is None:
            raise PortalError(404, "queue_not_found", "Queue entry not found")
        attempts = await self.db.fetchall(
            "SELECT a.id,a.actor_id,a.status,a.route_type,a.private_target_label,a.private_notes,a.created_ts,a.event_ts,"
            "a.episode_id FROM level_outreach_attempts a JOIN level_outreach_cycles c ON c.id=a.cycle_id "
            "WHERE c.guild_id=? AND a.queue_id=? ORDER BY COALESCE(a.event_ts,a.created_ts) DESC LIMIT 100",
            (principal.guild_id, queue_id),
        )
        events = await self.db.fetchall(
            "SELECT event,actor_id,payload_json,created_ts FROM workflow_events WHERE guild_id=? AND entity_id LIKE ? "
            "ORDER BY created_ts DESC,id DESC LIMIT 100",
            (principal.guild_id, f"queue:{queue_id}%"),
        )
        notes = await self._notes_for_entity(principal, "level", str(queue_id))
        return {
            "queue": await self._queue_payload(row),
            "outreach": [_row_dict(item) for item in attempts],
            "history": [_row_dict(item) for item in events],
            "notes": notes,
        }

    async def queue_action(self, principal, queue_id: int, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if action == "claim":
            principal.require("queue.claim")
            assignee = int(payload.get("assignee_id") or principal.user_id)
            if assignee != principal.user_id:
                principal.require("queue.reassign")
            return await self._claim(principal, queue_id, assignee, _bounded_text(payload.get("reason"), 500))
        if action == "release":
            return await self._release_claim(principal, queue_id, _bounded_text(payload.get("reason"), 500))
        if action == "reassign":
            principal.require("queue.reassign")
            return await self._claim(principal, queue_id, int(payload.get("assignee_id") or 0), _bounded_text(payload.get("reason"), 500, required=True), reassign=True)
        if action == "state":
            principal.require("queue.manage_state")
            return await self._transition_queue(principal, queue_id, payload)
        if action == "requeue":
            principal.require("queue.manage_state")
            return await self._requeue(principal, queue_id, payload)
        if action == "tier":
            principal.require("review.adjust_tier")
            return await self._adjust_tier(principal, queue_id, payload)
        raise PortalError(404, "unknown_action", "Unknown queue action")

    async def _claim(self, principal, queue_id: int, assignee_id: int, reason: str, *, reassign: bool = False):
        if assignee_id <= 0:
            raise PortalError(400, "invalid_assignee", "Choose a valid assignee")
        async with self._claim_lock:
            queue = await self.priority.queue_entry(principal.guild_id, queue_id)
            if queue is None or str(queue["queue_state"]) not in {"queued", "in_cycle", "awaiting_outcome"}:
                raise PortalError(409, "not_claimable", "This level is not currently claimable")
            existing = await self.db.fetchone("SELECT * FROM staff_queue_claims WHERE queue_id=?", (queue_id,))
            previous = int(existing["claimed_by"] or 0) if existing and str(existing["claim_state"]) == "active" else 0
            if previous and not reassign:
                if previous == assignee_id:
                    return {"ok": True, "claim": _row_dict(existing)}
                raise PortalError(409, "already_claimed", "This level is already claimed")
            if previous and reassign:
                principal.require("queue.reassign")
            now = int(time.time())
            correlation = new_correlation_id("staff-claim")
            conflict_guard = (
                ""
                if reassign
                else " WHERE staff_queue_claims.claim_state!='active'"
            )
            await self.db.execute_transaction(
                [
                    (
                        (
                            "INSERT INTO staff_queue_claims(queue_id,guild_id,claimed_by,claimed_ts,claim_state,released_by,released_ts,updated_ts) "
                            "VALUES(?,?,?,?,'active',NULL,NULL,?) ON CONFLICT(queue_id) DO UPDATE SET claimed_by=excluded.claimed_by,"
                            "claimed_ts=excluded.claimed_ts,claim_state='active',released_by=NULL,released_ts=NULL,updated_ts=excluded.updated_ts"
                            f"{conflict_guard}"  # nosec B608
                        ),
                        (queue_id, principal.guild_id, assignee_id, now, now),
                    ),
                    (
                        (
                            "INSERT INTO staff_queue_claim_events(guild_id,queue_id,actor_id,event,previous_owner_id,new_owner_id,reason,created_ts,correlation_id) "
                            "SELECT ?,?,?,?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM staff_queue_claims "
                            "WHERE queue_id=? AND claimed_by=? AND claim_state='active' AND updated_ts=?)"
                        ),
                        (principal.guild_id, queue_id, principal.user_id, "reassigned" if previous else "claimed", previous or None, assignee_id, reason, now, correlation, queue_id, assignee_id, now),
                    ),
                    (
                        (
                            "INSERT INTO workflow_events(correlation_id,workflow_type,entity_id,event,guild_id,actor_id,payload_json,created_ts) "
                            "SELECT ?,'staff_portal',?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM staff_queue_claims "
                            "WHERE queue_id=? AND claimed_by=? AND claim_state='active' AND updated_ts=?)"
                        ),
                        (correlation, f"queue:{queue_id}", "claim_reassigned" if previous else "claim_created", principal.guild_id, principal.user_id, json.dumps({"assignee_id": str(assignee_id), "reason": reason}), now, queue_id, assignee_id, now),
                    ),
                ],
                retry_safe=True,
            )
            claim = await self.db.fetchone("SELECT * FROM staff_queue_claims WHERE queue_id=?", (queue_id,))
            if claim is None or int(claim["claimed_by"] or 0) != assignee_id or str(claim["claim_state"]) != "active":
                raise PortalError(409, "already_claimed", "This level is already claimed")
            return {"ok": True, "claim": _row_dict(claim)}

    async def _release_claim(self, principal, queue_id: int, reason: str):
        claim = await self.db.fetchone(
            "SELECT * FROM staff_queue_claims WHERE queue_id=? AND guild_id=? AND claim_state='active'",
            (queue_id, principal.guild_id),
        )
        if claim is None:
            return {"ok": True, "claim": None}
        owner = int(claim["claimed_by"] or 0)
        if owner != principal.user_id:
            principal.require("queue.reassign")
            if not reason:
                raise PortalError(400, "reason_required", "A reason is required to release another Judge's claim")
            stale = int(claim["claimed_ts"] or 0) < int(time.time()) - await self._claim_stale_seconds()
            if not principal.can("pps.override") and not stale:
                raise PortalError(409, "claim_not_stale", "Only stale claims can be released by another Judge")
        else:
            principal.require("queue.release_own")
        now = int(time.time())
        changed = await self.db.execute_affected(
            "UPDATE staff_queue_claims SET claim_state='released',released_by=?,released_ts=?,updated_ts=? "
            "WHERE queue_id=? AND guild_id=? AND claim_state='active' AND claimed_by=?",
            (principal.user_id, now, now, queue_id, principal.guild_id, owner),
        )
        if changed:
            correlation = new_correlation_id("staff-claim")
            await self.db.execute_transaction(
                [
                    ("INSERT INTO staff_queue_claim_events(guild_id,queue_id,actor_id,event,previous_owner_id,new_owner_id,reason,created_ts,correlation_id) VALUES(?,?,?,?,?,NULL,?,?,?)", (principal.guild_id, queue_id, principal.user_id, "released", owner, reason, now, correlation)),
                    ("INSERT INTO workflow_events(correlation_id,workflow_type,entity_id,event,guild_id,actor_id,payload_json,created_ts) VALUES(?,'staff_portal',?,'claim_released',?,?,?,?)", (correlation, f"queue:{queue_id}", principal.guild_id, principal.user_id, json.dumps({"reason": reason}), now)),
                ],
                retry_safe=True,
            )
        return {"ok": True, "claim": None}

    async def _transition_queue(self, principal, queue_id: int, payload: dict[str, Any]):
        if payload.get("confirmed") is not True:
            raise PortalError(400, "confirmation_required", "Confirm the queue state change")
        new_state = str(payload.get("state") or "").casefold()
        reason = _bounded_text(payload.get("reason"), 1000, required=True)
        allowed = {"queued", "paused", "withdrawn", "invalid", "in_cycle", "awaiting_outcome", "rated"}
        if new_state not in allowed:
            raise PortalError(400, "invalid_state", "Choose a valid queue state")
        row = await self.priority.queue_entry(principal.guild_id, queue_id)
        if row is None:
            raise PortalError(404, "queue_not_found", "Queue entry not found")
        old_state = str(row["queue_state"])
        if old_state == new_state:
            return {"ok": True, "state": new_state}
        if new_state in {"invalid", "in_cycle", "awaiting_outcome", "rated"} and not principal.can("pps.override"):
            raise PortalError(
                403,
                "owner_required",
                "Only the owner can make that corrective transition directly",
            )
        now = int(time.time())
        correlation = new_correlation_id("queue-state")
        await self.db.execute_transaction(
            [
                ("UPDATE level_outreach_queue SET queue_state=?,updated_ts=? WHERE id=? AND guild_id=? AND queue_state=?", (new_state, now, queue_id, principal.guild_id, old_state)),
                ("INSERT INTO workflow_events(correlation_id,workflow_type,entity_id,event,guild_id,actor_id,payload_json,created_ts) VALUES(?,'priority_system',?,'queue_state_changed',?,?,?,?)", (correlation, f"queue:{queue_id}", principal.guild_id, principal.user_id, json.dumps({"old_state": old_state, "new_state": new_state, "reason": reason}), now)),
            ],
            retry_safe=True,
        )
        await self._refresh_public_cache()
        return {"ok": True, "state": new_state}

    async def _active_episode(self, queue_id: int):
        return await self.db.fetchone(
            "SELECT * FROM staff_outreach_episodes WHERE queue_id=? AND status IN('active','awaiting_outcome') ORDER BY episode_number DESC LIMIT 1",
            (queue_id,),
        )

    async def _ensure_episode(self, principal, queue_id: int):
        async with self._episode_locks[int(queue_id)]:
            existing = await self._active_episode(queue_id)
            if existing:
                return existing
            last = await self.db.fetchone(
                "SELECT MAX(episode_number) AS n FROM staff_outreach_episodes WHERE queue_id=?",
                (queue_id,),
            )
            number = int(last["n"] or 0) + 1
            now = int(time.time())
            episode_id = await self.db.execute_insert(
                "INSERT INTO staff_outreach_episodes(guild_id,queue_id,episode_number,status,started_by,started_ts) VALUES(?,?,?,'active',?,?)",
                (principal.guild_id, queue_id, number, principal.user_id, now),
            )
            return await self.db.fetchone(
                "SELECT * FROM staff_outreach_episodes WHERE id=?", (episode_id,)
            )

    async def record_outreach(self, principal, payload: dict[str, Any], idempotency_key: str):
        principal.require("outreach.record")
        queue_id = int(payload.get("queue_id") or 0)
        status = str(payload.get("event") or "").casefold()
        route = str(payload.get("route") or "other").casefold()
        target = _bounded_text(payload.get("target"), 300)
        notes = _bounded_text(payload.get("notes"), 2000)
        event_ts = int(payload.get("timestamp") or time.time())
        if status == "submitted_to_mod":
            principal.require("outreach.confirm_submission")
            if payload.get("confirmed") is not True:
                raise PortalError(400, "confirmation_required", "Confirm that the level reached a moderator")
        cycle = await self.priority.active_cycle(principal.guild_id)
        if cycle is None:
            raise PortalError(409, "cycle_required", "An outreach cycle must be active before recording outreach")
        eligible = await self.db.fetchone(
            "SELECT 1 FROM level_outreach_cycle_entries e "
            "JOIN level_outreach_queue q ON q.id=e.queue_id "
            "WHERE e.cycle_id=? AND e.queue_id=? AND q.guild_id=? "
            "AND q.queue_state IN('in_cycle','awaiting_outcome')",
            (int(cycle["id"]), queue_id, principal.guild_id),
        )
        if eligible is None:
            raise PortalError(
                409,
                "queue_not_in_cycle",
                "This level is not part of the active outreach cycle",
            )
        episode = await self._ensure_episode(principal, queue_id)
        try:
            attempt = await self.priority.record_attempt(
                principal.guild_id,
                int(cycle["id"]),
                queue_id,
                principal.user_id,
                status=status,
                route_type=route,
                notes=notes,
                target_label=target,
                idempotency_key=idempotency_key,
                episode_id=int(episode["id"]),
                event_ts=event_ts,
            )
        except ValueError as exc:
            raise PortalError(409, "outreach_rejected", str(exc)) from exc
        if status == "submitted_to_mod":
            await self.db.execute(
                "UPDATE staff_outreach_episodes SET status='awaiting_outcome' WHERE id=? AND status='active'",
                (int(episode["id"]),),
            )
        await self._refresh_public_cache()
        return {"ok": True, "attempt": _row_dict(attempt)}

    async def outreach_list(self, principal, query):
        principal.require("outreach.view")
        rows = await self.db.fetchall(
            "SELECT a.id,a.queue_id,a.actor_id,a.status,a.route_type,a.private_target_label,a.private_notes,"
            "COALESCE(a.event_ts,a.created_ts) AS event_ts,a.episode_id,q.level_id,q.current_level_name,q.send_type "
            "FROM level_outreach_attempts a JOIN level_outreach_cycles c ON c.id=a.cycle_id "
            "JOIN level_outreach_queue q ON q.id=a.queue_id WHERE c.guild_id=? "
            "ORDER BY COALESCE(a.event_ts,a.created_ts) DESC LIMIT 100",
            (principal.guild_id,),
        )
        counts = await self.db.fetchall(
            "SELECT status,COUNT(*) AS c FROM staff_outreach_episodes WHERE guild_id=? GROUP BY status",
            (principal.guild_id,),
        )
        return {"items": [_row_dict(row) for row in rows], "pipeline": {str(row["status"]): int(row["c"] or 0) for row in counts}}

    async def _requeue(self, principal, queue_id: int, payload: dict[str, Any]):
        if payload.get("confirmed") is not True:
            raise PortalError(400, "confirmation_required", "Confirm the new outreach episode")
        reason = _bounded_text(payload.get("reason"), 1000, required=True)
        row = await self.priority.queue_entry(principal.guild_id, queue_id)
        if row is None:
            raise PortalError(404, "queue_not_found", "Queue entry not found")
        if str(row["queue_state"]) not in {"awaiting_outcome", "paused", "withdrawn"}:
            raise PortalError(409, "not_requeueable", "This entry is not ready for a new outreach episode")
        score = score_components(str(row["send_type"]), row["current_creator_points"], 0, self.priority.settings)
        now = int(time.time())
        old_episode = await self._active_episode(queue_id)
        last = await self.db.fetchone("SELECT MAX(episode_number) AS n FROM staff_outreach_episodes WHERE queue_id=?", (queue_id,))
        new_number = int(last["n"] or 0) + 1
        correlation = new_correlation_id("outreach-requeue")
        statements = []
        if old_episode:
            statements.append(("UPDATE staff_outreach_episodes SET status='completed',ended_by=?,ended_ts=?,reason=?,outcome='not_observed' WHERE id=? AND status IN('active','awaiting_outcome')", (principal.user_id, now, reason, int(old_episode["id"]))))
        statements.extend(
            [
                ("INSERT INTO staff_outreach_episodes(guild_id,queue_id,episode_number,status,started_by,started_ts,reason) VALUES(?,?,?,'active',?,?,?)", (principal.guild_id, queue_id, new_number, principal.user_id, now, reason)),
                ("UPDATE level_outreach_queue SET queue_state='queued',waiting_cycles=0,waiting_component_h=?,creator_component_g=?,priority_points=?,priority_complete=?,submitted_to_mod_ts=NULL,rated_observed_ts=NULL,outcome_window_due_ts=NULL,rated_within_window=NULL,outcome_window_completed_ts=NULL,updated_ts=? WHERE id=? AND guild_id=?", (score["waiting_component_h"], score["creator_component_g"], score["priority_points"], score["priority_complete"], now, queue_id, principal.guild_id)),
                ("INSERT INTO workflow_events(correlation_id,workflow_type,entity_id,event,guild_id,actor_id,payload_json,created_ts) VALUES(?,'priority_system',?,'outreach_episode_requeued',?,?,?,?)", (correlation, f"queue:{queue_id}", principal.guild_id, principal.user_id, json.dumps({"reason": reason, "episode_number": new_number}), now)),
            ]
        )
        await self.db.execute_transaction(statements, retry_safe=True)
        try:
            await self.priority.refresh_queue_entry(queue_id, force_cp=True)
        except Exception as exc:  # noqa: BLE001 - external refresh must not undo a durable requeue.
            await log_error(
                self.bot,
                f"Staff portal requeue CP refresh failed queue_id={queue_id}: {exc!r}",
            )
        await self._refresh_public_cache()
        return {"ok": True, "episode_number": new_number}

    async def _adjust_tier(self, principal, queue_id: int, payload: dict[str, Any]):
        if payload.get("confirmed") is not True:
            raise PortalError(400, "confirmation_required", "Confirm the recommendation tier adjustment")
        new_tier = normalize_send_type(payload.get("tier"))
        reason = _bounded_text(payload.get("reason"), 1000, required=True)
        if new_tier is None:
            raise PortalError(400, "invalid_tier", "Choose a valid recommendation tier")
        row = await self.priority.queue_entry(principal.guild_id, queue_id)
        if row is None:
            raise PortalError(404, "queue_not_found", "Queue entry not found")
        confirmed_submission = await self.db.fetchone(
            "SELECT id FROM level_outreach_attempts "
            "WHERE queue_id=? AND status='submitted_to_mod' LIMIT 1",
            (queue_id,),
        )
        if (
            confirmed_submission is not None or row["submitted_to_mod_ts"] is not None
        ) and not principal.can("pps.override"):
            raise PortalError(
                403,
                "tier_locked_after_submission",
                "Only the owner can change a tier after confirmed moderator submission",
            )
        old_tier = str(row["send_type"])
        if old_tier == new_tier:
            return {"ok": True, "tier": new_tier}
        score = score_components(new_tier, row["current_creator_points"], int(row["waiting_cycles"] or 0), self.priority.settings)
        now = int(time.time())
        correlation = new_correlation_id("qa-tier")
        review = await self.db.fetchone(
            "SELECT reviewed_by FROM level_request_submissions "
            "WHERE guild_id=? AND request_message_id=?",
            (principal.guild_id, int(row["request_message_id"])),
        )
        await self.db.execute_transaction(
            [
                ("UPDATE level_outreach_queue SET send_type=?,prestige_t=?,prestige_component_f=?,creator_component_g=?,waiting_component_h=?,priority_points=?,priority_complete=?,updated_ts=? WHERE id=? AND guild_id=?", (new_tier, score["prestige_t"], score["prestige_component_f"], score["creator_component_g"], score["waiting_component_h"], score["priority_points"], score["priority_complete"], now, queue_id, principal.guild_id)),
                ("INSERT INTO staff_review_qa(guild_id,request_message_id,reviewer_id,qa_status,original_send_type,adjusted_send_type,qa_by,reason,created_ts,updated_ts) VALUES(?,?,?,'adjusted',?,?,?,?,?,?) ON CONFLICT(guild_id,request_message_id) DO UPDATE SET qa_status='adjusted',adjusted_send_type=excluded.adjusted_send_type,qa_by=excluded.qa_by,reason=excluded.reason,updated_ts=excluded.updated_ts", (principal.guild_id, int(row["request_message_id"]), review["reviewed_by"] if review else None, old_tier, new_tier, principal.user_id, reason, now, now)),
                ("INSERT INTO workflow_events(correlation_id,workflow_type,entity_id,event,guild_id,actor_id,payload_json,created_ts) VALUES(?,'priority_system',?,'tier_adjusted',?,?,?,?)", (correlation, f"queue:{queue_id}", principal.guild_id, principal.user_id, json.dumps({"old_tier": old_tier, "new_tier": new_tier, "reason": reason}), now)),
            ],
            retry_safe=True,
        )
        await self._refresh_public_cache()
        return {"ok": True, "tier": new_tier}

    async def tasks(self, principal, query):
        await self._sync_system_tasks(principal.guild_id)
        if principal.can("tasks.manage_team") and query.get("scope") == "team":
            rows = await self.db.fetchall("SELECT * FROM staff_tasks WHERE guild_id=? ORDER BY status,COALESCE(due_ts,9223372036854775807),priority DESC,id DESC LIMIT 200", (principal.guild_id,))
        else:
            rows = await self.db.fetchall("SELECT * FROM staff_tasks WHERE guild_id=? AND (assignee_id=? OR task_type='team' OR (task_type='personal' AND created_by=?)) ORDER BY status,COALESCE(due_ts,9223372036854775807),priority DESC,id DESC LIMIT 200", (principal.guild_id, principal.user_id, principal.user_id))
        items = [_row_dict(row) for row in rows]
        total = sum(item["status"] != "cancelled" for item in items)
        done = sum(item["status"] == "done" for item in items)
        return {"items": items, "progress": {"done": done, "total": total}}

    async def create_task(self, principal, payload):
        principal.require("tasks.create")
        task_type = str(payload.get("task_type") or "personal").casefold()
        if task_type not in {"personal", "assigned", "team"}:
            raise PortalError(400, "invalid_task_type", "Choose a valid task type")
        raw_assignee = payload.get("assignee_id")
        assignee_id = (
            None
            if task_type == "team" and not raw_assignee
            else int(raw_assignee or principal.user_id)
        )
        if task_type != "personal" or assignee_id != principal.user_id:
            principal.require("tasks.assign")
        title = _bounded_text(payload.get("title"), 160, required=True)
        description = _bounded_text(payload.get("description"), 4000)
        priority = str(payload.get("priority") or "normal").casefold()
        if priority not in {"low", "normal", "high", "urgent"}:
            priority = "normal"
        due_ts = int(payload.get("due_ts") or 0) or None
        now = int(time.time())
        task_id = await self.db.execute_insert(
            "INSERT INTO staff_tasks(guild_id,task_type,title,description,priority,status,created_by,assignee_id,created_ts,updated_ts,due_ts,linked_entity_type,linked_entity_id) VALUES(?,?,?,?,?,'todo',?,?,?,?,?,?,?)",
            (principal.guild_id, task_type, title, description, priority, principal.user_id, assignee_id, now, now, due_ts, _bounded_text(payload.get("linked_entity_type"), 40) or None, _bounded_text(payload.get("linked_entity_id"), 100) or None),
        )
        return {"task": _row_dict(await self.db.fetchone("SELECT * FROM staff_tasks WHERE id=?", (task_id,)))}

    async def update_task(self, principal, task_id: int, payload):
        row = await self.db.fetchone("SELECT * FROM staff_tasks WHERE id=? AND guild_id=?", (task_id, principal.guild_id))
        if row is None:
            raise PortalError(404, "task_not_found", "Task not found")
        status = str(payload.get("status") or row["status"]).casefold()
        if status not in {"todo", "in_progress", "done", "cancelled"}:
            raise PortalError(400, "invalid_status", "Choose a valid task status")
        owner = int(row["created_by"] or 0) == principal.user_id or int(row["assignee_id"] or 0) == principal.user_id
        shared_team_update = str(row["task_type"]) == "team" and status in {"in_progress", "done"}
        if not owner and not shared_team_update:
            principal.require("tasks.manage_team")
        now = int(time.time())
        await self.db.execute("UPDATE staff_tasks SET status=?,updated_ts=?,completed_ts=? WHERE id=? AND guild_id=?", (status, now, now if status == "done" else None, task_id, principal.guild_id))
        return {"task": _row_dict(await self.db.fetchone("SELECT * FROM staff_tasks WHERE id=?", (task_id,)))}

    def _note_scopes(self, principal) -> set[str]:
        scopes = {"private", "reviewer_team", "entity"}
        if principal.can("notes.head"):
            scopes.add("head_judges")
        if principal.can("notes.owner"):
            scopes.add("owners")
        return scopes

    async def notes(self, principal, query):
        scopes = self._note_scopes(principal)
        rows = await self.db.fetchall("SELECT * FROM staff_notes WHERE guild_id=? AND archived_ts IS NULL ORDER BY updated_ts DESC LIMIT 200", (principal.guild_id,))
        return {"items": [item for row in rows if (item := _row_dict(row)) and self._note_visible(principal, item, scopes)]}

    def _note_visible(self, principal, item, scopes=None):
        scopes = scopes or self._note_scopes(principal)
        scope = str(item.get("scope"))
        if scope == "private":
            return int(item.get("author_id") or 0) == principal.user_id
        return scope in scopes

    async def _notes_for_entity(self, principal, entity_type, entity_id):
        rows = await self.db.fetchall("SELECT * FROM staff_notes WHERE guild_id=? AND entity_type=? AND entity_id=? AND archived_ts IS NULL ORDER BY updated_ts DESC LIMIT 100", (principal.guild_id, entity_type, entity_id))
        return [item for row in rows if (item := _row_dict(row)) and self._note_visible(principal, item)]

    async def create_note(self, principal, payload):
        scope = str(payload.get("scope") or "private").casefold()
        if scope not in self._note_scopes(principal):
            raise PortalError(403, "note_scope_denied", "You cannot use that note scope")
        if scope == "private":
            principal.require("notes.private")
        elif scope == "reviewer_team":
            principal.require("notes.team")
        body = _bounded_text(payload.get("body"), 10000, required=True)
        now = int(time.time())
        note_id = await self.db.execute_insert("INSERT INTO staff_notes(guild_id,author_id,scope,body,entity_type,entity_id,created_ts,updated_ts) VALUES(?,?,?,?,?,?,?,?)", (principal.guild_id, principal.user_id, scope, body, _bounded_text(payload.get("entity_type"), 40) or None, _bounded_text(payload.get("entity_id"), 100) or None, now, now))
        return {"note": _row_dict(await self.db.fetchone("SELECT * FROM staff_notes WHERE id=?", (note_id,)))}

    async def update_note(self, principal, note_id, payload):
        row = await self.db.fetchone("SELECT * FROM staff_notes WHERE id=? AND guild_id=? AND archived_ts IS NULL", (note_id, principal.guild_id))
        if row is None:
            raise PortalError(404, "note_not_found", "Note not found")
        if int(row["author_id"]) != principal.user_id and not principal.can("notes.owner"):
            raise PortalError(403, "note_denied", "You cannot edit that note")
        body = _bounded_text(payload.get("body"), 10000, required=True)
        await self.db.execute("UPDATE staff_notes SET body=?,updated_ts=? WHERE id=?", (body, int(time.time()), note_id))
        return {"note": _row_dict(await self.db.fetchone("SELECT * FROM staff_notes WHERE id=?", (note_id,)))}

    async def team(self, principal):
        now = int(time.time())
        current_wave = await self.db.fetchone("SELECT wave_id,submitted_count FROM level_request_state WHERE guild_id=?", (principal.guild_id,))
        reviewed = 0
        total = int(current_wave["submitted_count"] or 0) if current_wave else 0
        if current_wave:
            count = await self.db.fetchone("SELECT COUNT(*) AS c FROM level_request_submissions WHERE guild_id=? AND wave_id=? AND status='reviewed'", (principal.guild_id, int(current_wave["wave_id"])))
            reviewed = int(count["c"] or 0)
        queue_counts = await self.db.fetchall("SELECT queue_state,COUNT(*) AS c FROM level_outreach_queue WHERE guild_id=? GROUP BY queue_state", (principal.guild_id,))
        claims = await self.db.fetchone("SELECT COUNT(*) AS active,SUM(CASE WHEN claimed_ts<? THEN 1 ELSE 0 END) AS stale FROM staff_queue_claims WHERE guild_id=? AND claim_state='active'", (now - await self._claim_stale_seconds(), principal.guild_id))
        outreach = await self.db.fetchone(
            "SELECT COUNT(*) AS attempts,SUM(CASE WHEN a.status='submitted_to_mod' THEN 1 ELSE 0 END) AS submissions "
            "FROM level_outreach_attempts a JOIN level_outreach_cycles c ON c.id=a.cycle_id "
            "WHERE c.guild_id=? AND COALESCE(a.event_ts,a.created_ts)>=?",
            (principal.guild_id, now - 7 * 86400),
        )
        applications = await self.db.fetchone(
            "SELECT COUNT(*) AS c FROM staff_applications WHERE guild_id=? "
            "AND status IN('submitted','under_review','interview','hold','accepted_pending_role')",
            (principal.guild_id,),
        )
        workload = await self.db.fetchall(
            "SELECT claimed_by,COUNT(*) AS c FROM staff_queue_claims WHERE guild_id=? "
            "AND claim_state='active' GROUP BY claimed_by ORDER BY c DESC,claimed_by LIMIT 20",
            (principal.guild_id,),
        )
        return {
            "review_progress": {"done": reviewed, "total": total},
            "queue": {str(row["queue_state"]): int(row["c"] or 0) for row in queue_counts},
            "claims": {"active": int(claims["active"] or 0), "stale": int(claims["stale"] or 0)},
            "outreach_week": {
                "attempts": int(outreach["attempts"] or 0),
                "submissions": int(outreach["submissions"] or 0),
            },
            "pending_applications": int(applications["c"] or 0),
            "workload": [
                {"user_id": str(row["claimed_by"]), "active_claims": int(row["c"] or 0)}
                for row in workload
            ],
        }

    async def statistics(self, principal):
        scope_all = principal.can("review.qa")
        reviewer_filter = "" if scope_all else " AND reviewed_by=?"
        params = (principal.guild_id,) if scope_all else (principal.guild_id, principal.user_id)
        reviews = await self.db.fetchall(
            "WITH durations AS (SELECT reviewed_by,reviewed_ts-created_ts AS duration FROM level_request_submissions "
            f"WHERE guild_id=? AND status='reviewed' AND reviewed_ts>created_ts{reviewer_filter}), "  # nosec B608
            "ranked AS (SELECT reviewed_by,duration,ROW_NUMBER() OVER(PARTITION BY reviewed_by ORDER BY duration) AS rn,"
            "COUNT(*) OVER(PARTITION BY reviewed_by) AS n FROM durations) "
            "SELECT reviewed_by,MAX(n) AS reviews,AVG(CASE WHEN rn IN((n+1)/2,(n+2)/2) THEN duration END) "
            "AS median_turnaround FROM ranked GROUP BY reviewed_by ORDER BY reviews DESC LIMIT 100",
            params,
        )
        tiers = await self.db.fetchall(f"SELECT send_type,COUNT(*) AS c FROM level_request_submissions WHERE guild_id=? AND status='reviewed'{reviewer_filter} GROUP BY send_type", params)  # nosec B608
        results = await self.db.fetchall(
            f"SELECT COALESCE(result,'unknown') AS result,COUNT(*) AS c FROM level_request_submissions "  # nosec B608
            f"WHERE guild_id=? AND status='reviewed'{reviewer_filter} GROUP BY result",  # nosec B608
            params,
        )
        outreach_filter = "" if scope_all else " AND a.actor_id=?"
        outreach_params = params
        outreach = await self.db.fetchone(
            "SELECT COUNT(*) AS attempts,"
            "SUM(CASE WHEN a.status='submitted_to_mod' THEN 1 ELSE 0 END) AS submissions,"
            "SUM(CASE WHEN a.status='follow_up' THEN 1 ELSE 0 END) AS followups "
            "FROM level_outreach_attempts a JOIN level_outreach_cycles c ON c.id=a.cycle_id "
            f"WHERE c.guild_id=?{outreach_filter}",  # nosec B608
            outreach_params,
        )
        routes = await self.db.fetchall(
            "SELECT a.route_type,COUNT(*) AS attempts,"
            "SUM(CASE WHEN a.status='submitted_to_mod' THEN 1 ELSE 0 END) AS submissions "
            "FROM level_outreach_attempts a JOIN level_outreach_cycles c ON c.id=a.cycle_id "
            f"WHERE c.guild_id=?{outreach_filter} GROUP BY a.route_type ORDER BY attempts DESC",  # nosec B608
            outreach_params,
        )
        task_filter = "" if scope_all else " AND assignee_id=?"
        tasks = await self.db.fetchone(
            f"SELECT COUNT(*) AS completed FROM staff_tasks WHERE guild_id=? AND status='done'{task_filter}",  # nosec B608
            params,
        )
        active_weeks = await self.db.fetchone(
            "SELECT COUNT(DISTINCT week) AS c FROM ("
            "SELECT strftime('%Y-%W',reviewed_ts,'unixepoch') AS week FROM level_request_submissions "
            f"WHERE guild_id=? AND status='reviewed'{reviewer_filter} "  # nosec B608
            "UNION SELECT strftime('%Y-%W',COALESCE(a.event_ts,a.created_ts),'unixepoch') FROM level_outreach_attempts a "
            "JOIN level_outreach_cycles c ON c.id=a.cycle_id "
            f"WHERE c.guild_id=?{outreach_filter})",  # nosec B608
            (*params, *outreach_params),
        )
        queue_counts = await self.db.fetchall(
            "SELECT queue_state,COUNT(*) AS c FROM level_outreach_queue WHERE guild_id=? GROUP BY queue_state",
            (principal.guild_id,),
        )
        waiting = await self.db.fetchall(
            "SELECT CASE WHEN waiting_cycles>=4 THEN '4+' ELSE CAST(waiting_cycles AS TEXT) END AS bucket,"
            "COUNT(*) AS c FROM level_outreach_queue WHERE guild_id=? AND queue_state IN('queued','in_cycle') "
            "GROUP BY bucket ORDER BY MIN(waiting_cycles)",
            (principal.guild_id,),
        )
        cp = await self.db.fetchall(
            "SELECT CASE WHEN current_creator_points IS NULL THEN 'unknown' "
            "WHEN current_creator_points>=4 THEN '4+' ELSE CAST(current_creator_points AS TEXT) END AS bucket,"
            "COUNT(*) AS c FROM level_outreach_queue WHERE guild_id=? AND queue_state IN('queued','in_cycle') "
            "GROUP BY bucket ORDER BY CASE bucket WHEN 'unknown' THEN 99 WHEN '4+' THEN 4 ELSE CAST(bucket AS INTEGER) END",
            (principal.guild_id,),
        )
        stale = await self.db.fetchone(
            "SELECT COUNT(*) AS c FROM staff_queue_claims WHERE guild_id=? AND claim_state='active' AND claimed_ts<?",
            (principal.guild_id, int(time.time()) - await self._claim_stale_seconds()),
        )
        return {
            "reviewers": [_row_dict(row) for row in reviews],
            "tiers": {_row_dict(row).get("send_type") or "rejected": int(row["c"] or 0) for row in tiers},
            "results": {str(row["result"]): int(row["c"] or 0) for row in results},
            "outreach": {
                "attempts": int(outreach["attempts"] or 0),
                "submissions": int(outreach["submissions"] or 0),
                "followups": int(outreach["followups"] or 0),
                "routes": [_row_dict(row) for row in routes],
            },
            "tasks_completed": int(tasks["completed"] or 0),
            "active_weeks": int(active_weeks["c"] or 0),
            "queue": {str(row["queue_state"]): int(row["c"] or 0) for row in queue_counts},
            "stale_claims": int(stale["c"] or 0),
            "waiting_distribution": {str(row["bucket"]): int(row["c"] or 0) for row in waiting},
            "cp_distribution": {str(row["bucket"]): int(row["c"] or 0) for row in cp},
            "scope": "team" if scope_all else "self",
        }

    async def qa_list(self, principal, query):
        rows = await self.db.fetchall("SELECT s.request_message_id,s.level_id,s.user_id,s.result,s.send_type,s.review_text,s.reviewed_by,s.reviewed_ts,q.qa_status,q.adjusted_send_type,q.reason FROM level_request_submissions s LEFT JOIN staff_review_qa q ON q.guild_id=s.guild_id AND q.request_message_id=s.request_message_id WHERE s.guild_id=? AND s.status='reviewed' ORDER BY s.reviewed_ts DESC LIMIT 100", (principal.guild_id,))
        return {"items": [_row_dict(row) for row in rows]}

    async def qa_action(self, principal, request_message_id, payload):
        action = str(payload.get("action") or "").casefold()
        statuses = {"ok": "reviewed_ok", "discussion": "needs_discussion", "rereview": "re_review_requested"}
        if action == "adjust":
            queue = await self.db.fetchone("SELECT id FROM level_outreach_queue WHERE guild_id=? AND request_message_id=?", (principal.guild_id, request_message_id))
            if queue is None:
                raise PortalError(404, "queue_not_found", "This review has no PPS queue entry")
            return await self._adjust_tier(principal, int(queue["id"]), payload)
        status = statuses.get(action)
        if not status:
            raise PortalError(400, "invalid_qa_action", "Choose a valid QA action")
        reason = _bounded_text(payload.get("reason"), 1000, required=action != "ok")
        source = await self.db.fetchone("SELECT reviewed_by,send_type FROM level_request_submissions WHERE guild_id=? AND request_message_id=?", (principal.guild_id, request_message_id))
        if source is None:
            raise PortalError(404, "review_not_found", "Review not found")
        now = int(time.time())
        await self.db.execute("INSERT INTO staff_review_qa(guild_id,request_message_id,reviewer_id,qa_status,original_send_type,qa_by,reason,created_ts,updated_ts) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(guild_id,request_message_id) DO UPDATE SET qa_status=excluded.qa_status,qa_by=excluded.qa_by,reason=excluded.reason,updated_ts=excluded.updated_ts", (principal.guild_id, request_message_id, source["reviewed_by"], status, source["send_type"], principal.user_id, reason, now, now))
        return {"ok": True, "status": status}

    async def _handle_apply(self, method, path, principal, payload):
        if path == "/api/apply/mine" and method == "GET":
            await self._reconcile_application_roles()
            rows = await self.db.fetchall("SELECT id,application_type,status,answers_json,created_ts,updated_ts,submitted_ts,decided_ts,decision_reason FROM staff_applications WHERE guild_id=? AND applicant_id=? ORDER BY updated_ts DESC LIMIT 20", (principal.guild_id, principal.user_id))
            return 200, {"items": [{**_row_dict(row), "answers": _json_object(row["answers_json"])} for row in rows]}
        if path == "/api/apply/save" and method == "POST":
            return 200, await self.save_application(principal, payload, submit=False)
        if path == "/api/apply/submit" and method == "POST":
            return 200, await self.save_application(principal, payload, submit=True)
        match = re.fullmatch(r"/api/apply/(\d+)/withdraw", path)
        if match and method == "POST":
            application_id = int(match.group(1))
            changed = await self.db.execute_affected("UPDATE staff_applications SET status='withdrawn',updated_ts=? WHERE id=? AND guild_id=? AND applicant_id=? AND status IN('draft','submitted','under_review','hold')", (int(time.time()), application_id, principal.guild_id, principal.user_id))
            if not changed:
                raise PortalError(409, "cannot_withdraw", "That application cannot be withdrawn")
            return 200, {"ok": True}
        raise PortalError(404, "not_found", "That application resource does not exist")

    async def save_application(self, principal, payload, *, submit):
        configuration = (await self.safe_configuration())["configuration"]
        if submit and not configuration["applications_open"]:
            raise PortalError(409, "applications_closed", "Applications are currently closed")
        app_type = str(payload.get("application_type") or "judge").casefold()
        allowed = self.bot.config.get("staff_portal", "application_types", default=["judge"])
        if app_type not in allowed:
            raise PortalError(400, "application_closed", "That application is not available")
        answers = payload.get("answers")
        if not isinstance(answers, dict):
            raise PortalError(400, "invalid_answers", "Application answers are missing")
        safe_answers = {str(key)[:80]: _bounded_text(value, 4000) for key, value in list(answers.items())[:30]}
        if submit and not all(safe_answers.get(key) for key in ("experience", "motivation", "availability")):
            raise PortalError(400, "incomplete_application", "Complete every required answer before submitting")
        now = int(time.time())
        async with self._application_lock:
            row = await self.db.fetchone("SELECT * FROM staff_applications WHERE guild_id=? AND applicant_id=? AND application_type=? AND status='draft' ORDER BY id DESC LIMIT 1", (principal.guild_id, principal.user_id, app_type))
            active = None
            if row is None:
                active = await self.db.fetchone(
                    "SELECT * FROM staff_applications WHERE guild_id=? AND applicant_id=? AND application_type=? "
                    "AND status IN('submitted','under_review','interview','hold','accepted_pending_role') "
                    "ORDER BY id DESC LIMIT 1",
                    (principal.guild_id, principal.user_id, app_type),
                )
            if active is not None:
                if submit:
                    return {"application": _row_dict(active)}
                raise PortalError(409, "application_active", "You already have an active application")
            target_status = "submitted" if submit else "draft"
            if row:
                application_id = int(row["id"])
                await self.db.execute("UPDATE staff_applications SET answers_json=?,status=?,updated_ts=?,submitted_ts=? WHERE id=? AND status='draft'", (json.dumps(safe_answers, separators=(",", ":")), target_status, now, now if submit else None, application_id))
            else:
                application_id = await self.db.execute_insert("INSERT INTO staff_applications(guild_id,applicant_id,application_type,status,answers_json,created_ts,updated_ts,submitted_ts) VALUES(?,?,?,?,?,?,?,?)", (principal.guild_id, principal.user_id, app_type, target_status, json.dumps(safe_answers, separators=(",", ":")), now, now, now if submit else None))
            if submit:
                await self._application_event(application_id, principal.user_id, "submitted", "draft", "submitted", {})
        return {"application": _row_dict(await self.db.fetchone("SELECT * FROM staff_applications WHERE id=?", (application_id,)))}

    async def _application_event(self, application_id, actor_id, event, old, new, detail):
        now = int(time.time())
        await self.db.execute("INSERT INTO staff_application_events(application_id,actor_id,event,from_status,to_status,detail_json,created_ts,correlation_id) VALUES(?,?,?,?,?,?,?,?)", (application_id, actor_id, event, old, new, json.dumps(detail, separators=(",", ":")), now, new_correlation_id("application")))

    async def _reconcile_application_roles(self):
        pending = await self.db.fetchall(
            "SELECT id,guild_id,applicant_id,role_outbox_id FROM staff_applications "
            "WHERE status='accepted_pending_role' LIMIT 50"
        )
        judge_roles = self.bot.config.get_int_list("staff_portal", "judge_role_ids")
        for row in pending:
            if row["role_outbox_id"] is not None or not judge_roles:
                continue
            try:
                outbox_id = await self.bot.outbox.enqueue(
                    "add_role",
                    guild_id=int(row["guild_id"]),
                    user_id=int(row["applicant_id"]),
                    payload={
                        "role_id": judge_roles[0],
                        "reason": f"Judge application #{int(row['id'])} accepted",
                    },
                    correlation_id=f"staff-application:{int(row['id'])}",
                    idempotency_key=f"staff-application:{int(row['id'])}:judge-role",
                )
                await self.db.execute(
                    "UPDATE staff_applications SET role_outbox_id=?,updated_ts=? "
                    "WHERE id=? AND status='accepted_pending_role' AND role_outbox_id IS NULL",
                    (outbox_id, int(time.time()), int(row["id"])),
                )
            except Exception as exc:  # noqa: BLE001 - keep the durable pending state retryable.
                await log_error(
                    self.bot,
                    f"Judge application role outbox reconciliation deferred application_id={int(row['id'])}: {exc!r}",
                )
        rows = await self.db.fetchall("SELECT a.id,a.role_outbox_id,o.status FROM staff_applications a JOIN discord_outbox o ON o.id=a.role_outbox_id WHERE a.status='accepted_pending_role' LIMIT 50")
        for row in rows:
            if str(row["status"]) == "delivered":
                await self.db.execute("UPDATE staff_applications SET status='accepted',updated_ts=? WHERE id=? AND status='accepted_pending_role'", (int(time.time()), int(row["id"])))

    async def applications(self, principal, query):
        await self._reconcile_application_roles()
        type_filter = "" if principal.can("applications.review_all") else " AND application_type='judge'"
        rows = await self.db.fetchall(
            "SELECT * FROM staff_applications WHERE guild_id=? AND status!='draft'"
            f"{type_filter} "  # nosec B608
            "ORDER BY CASE status WHEN 'submitted' THEN 0 WHEN 'under_review' THEN 1 "
            "WHEN 'interview' THEN 2 WHEN 'hold' THEN 3 ELSE 4 END,"
            "COALESCE(submitted_ts,created_ts) LIMIT 200",
            (principal.guild_id,),
        )
        if not rows:
            return {"items": []}
        application_ids = [int(row["id"]) for row in rows]
        placeholders = ",".join("?" for _ in application_ids)
        notes = await self.db.fetchall(
            f"SELECT id,application_id,author_id,body,created_ts,updated_ts FROM staff_application_notes "
            f"WHERE application_id IN ({placeholders}) ORDER BY created_ts",  # nosec B608
            application_ids,
        )
        events = await self.db.fetchall(
            f"SELECT application_id,actor_id,event,from_status,to_status,detail_json,created_ts "
            f"FROM staff_application_events WHERE application_id IN ({placeholders}) "  # nosec B608
            "ORDER BY created_ts",
            application_ids,
        )
        notes_by_application: dict[int, list[dict[str, Any]]] = defaultdict(list)
        events_by_application: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for note in notes:
            notes_by_application[int(note["application_id"])].append(_row_dict(note))
        for event in events:
            event_payload = _row_dict(event)
            event_payload["detail"] = _json_object(event_payload.pop("detail_json", "{}"))
            events_by_application[int(event["application_id"])].append(event_payload)
        return {
            "items": [
                {
                    **_row_dict(row),
                    "answers": _json_object(row["answers_json"]),
                    "internal_notes": notes_by_application[int(row["id"])],
                    "timeline": events_by_application[int(row["id"])],
                }
                for row in rows
            ]
        }

    async def application_action(self, principal, application_id, payload):
        action = str(payload.get("action") or "").casefold()
        targets = {"claim": "under_review", "interview": "interview", "hold": "hold", "accept": "accepted_pending_role", "reject": "rejected"}
        target = targets.get(action)
        if target is None:
            raise PortalError(400, "invalid_application_action", "Choose a valid application action")
        if action in {"accept", "reject"} and payload.get("confirmed") is not True:
            raise PortalError(400, "confirmation_required", "Confirm the final application decision")
        reason = _bounded_text(payload.get("reason"), 1000, required=action in {"hold", "accept", "reject"})
        async with self._application_lock:
            row = await self.db.fetchone("SELECT * FROM staff_applications WHERE id=? AND guild_id=?", (application_id, principal.guild_id))
            if row is None:
                raise PortalError(404, "application_not_found", "Application not found")
            if str(row["application_type"]) != "judge" and not principal.can("applications.review_all"):
                raise PortalError(403, "application_scope_denied", "Only the owner can manage that application type")
            old = str(row["status"])
            if old in {"accepted", "rejected", "withdrawn"}:
                raise PortalError(409, "application_final", "That application already has a final decision")
            if old == "accepted_pending_role" and action == "accept":
                await self._reconcile_application_roles()
                return {"ok": True, "status": old, "role_delivery": "pending"}
            now = int(time.time())
            judge_roles = []
            if action == "accept":
                judge_roles = self.bot.config.get_int_list("staff_portal", "judge_role_ids")
                if not judge_roles:
                    raise PortalError(503, "judge_role_missing", "The Judge role is not configured")
            correlation = new_correlation_id("application")
            await self.db.execute_transaction(
                [
                    (
                        (
                            "UPDATE staff_applications SET status=?,claimed_by=COALESCE(claimed_by,?),updated_ts=?,"
                            "decided_by=?,decided_ts=?,decision_reason=? WHERE id=? AND guild_id=?"
                        ),
                        (
                            target,
                            principal.user_id,
                            now,
                            principal.user_id if action in {"accept", "reject"} else None,
                            now if action in {"accept", "reject"} else None,
                            reason,
                            application_id,
                            principal.guild_id,
                        ),
                    ),
                    (
                        (
                            "INSERT INTO staff_application_events(application_id,actor_id,event,from_status,to_status,detail_json,created_ts,correlation_id) "
                            "VALUES(?,?,?,?,?,?,?,?)"
                        ),
                        (
                            application_id,
                            principal.user_id,
                            action,
                            old,
                            target,
                            json.dumps({"reason": reason}, separators=(",", ":")),
                            now,
                            correlation,
                        ),
                    ),
                ],
                retry_safe=True,
            )
            outbox_id = None
            if action == "accept":
                outbox_id = await self.bot.outbox.enqueue(
                    "add_role",
                    guild_id=principal.guild_id,
                    user_id=int(row["applicant_id"]),
                    payload={
                        "role_id": judge_roles[0],
                        "reason": f"Judge application #{application_id} accepted",
                    },
                    correlation_id=f"staff-application:{application_id}",
                    idempotency_key=f"staff-application:{application_id}:judge-role",
                )
                await self.db.execute(
                    "UPDATE staff_applications SET role_outbox_id=?,updated_ts=? "
                    "WHERE id=? AND status='accepted_pending_role'",
                    (outbox_id, int(time.time()), application_id),
                )
        return {
            "ok": True,
            "status": target,
            "role_delivery": "pending" if action == "accept" else None,
        }

    async def application_note(self, principal, application_id, payload):
        body = _bounded_text(payload.get("body"), 4000, required=True)
        exists = await self.db.fetchone("SELECT application_type FROM staff_applications WHERE id=? AND guild_id=?", (application_id, principal.guild_id))
        if exists is None:
            raise PortalError(404, "application_not_found", "Application not found")
        if str(exists["application_type"]) != "judge" and not principal.can("applications.review_all"):
            raise PortalError(403, "application_scope_denied", "Only the owner can note that application")
        now = int(time.time())
        note_id = await self.db.execute_insert("INSERT INTO staff_application_notes(application_id,author_id,body,created_ts,updated_ts) VALUES(?,?,?,?,?)", (application_id, principal.user_id, body, now, now))
        return {"note": {"id": note_id, "body": body, "author_id": str(principal.user_id), "created_ts": now}}

    async def staff_list(self, principal):
        guild, _member = await self._member(principal.user_id)
        role_ids = set(self.bot.config.get_int_list("staff_portal", "judge_role_ids") + self.bot.config.get_int_list("staff_portal", "head_judge_role_ids") + self.bot.config.get_int_list("staff_portal", "owner_role_ids"))
        owner_users = set(self.bot.config.get_int_list("staff_portal", "owner_user_ids"))
        persisted_rows = await self.db.fetchall(
            "SELECT s.*,o.status AS role_delivery_status FROM staff_members s "
            "LEFT JOIN discord_outbox o ON o.id=s.role_outbox_id WHERE s.guild_id=?",
            (principal.guild_id,),
        )
        persisted = {int(row["user_id"]): _row_dict(row) for row in persisted_rows}
        member_by_id = {int(member.id): member for member in getattr(guild, "members", ())}
        tracked_ids = set(persisted) | owner_users
        for member in member_by_id.values():
            member_roles = {int(role.id) for role in getattr(member, "roles", ())}
            if member_roles & role_ids:
                tracked_ids.add(int(member.id))
        review_rows = await self.db.fetchall(
            "SELECT reviewed_by,COUNT(*) AS c,MAX(reviewed_ts) AS last_ts "
            "FROM level_request_submissions WHERE guild_id=? AND reviewed_by IS NOT NULL "
            "GROUP BY reviewed_by",
            (principal.guild_id,),
        )
        claim_rows = await self.db.fetchall(
            "SELECT claimed_by,COUNT(*) AS c FROM staff_queue_claims "
            "WHERE guild_id=? AND claim_state='active' GROUP BY claimed_by",
            (principal.guild_id,),
        )
        reviews_by_user = {int(row["reviewed_by"]): _row_dict(row) for row in review_rows}
        claims_by_user = {int(row["claimed_by"]): int(row["c"] or 0) for row in claim_rows}
        items = []
        for user_id in tracked_ids:
            member = member_by_id.get(user_id)
            member_roles = {int(role.id) for role in getattr(member, "roles", ())}
            role = resolve_staff_role(user_id, member_roles, self.bot.config)
            saved = persisted.get(user_id, {})
            review = reviews_by_user.get(user_id, {})
            items.append(
                {
                    "id": str(user_id),
                    "display_name": str(
                        getattr(member, "display_name", "") or f"Former staff {user_id}"
                    ),
                    "avatar_url": str(
                        getattr(getattr(member, "display_avatar", None), "url", "") or ""
                    ),
                    "role": role,
                    "active": role in {"judge", "head_judge", "owner"},
                    "desired_role": saved.get("desired_role"),
                    "desired_status": saved.get("status"),
                    "role_delivery_status": saved.get("role_delivery_status"),
                    "reviews": int(review.get("c") or 0),
                    "workload": claims_by_user.get(user_id, 0),
                    "last_activity_ts": review.get("last_ts"),
                }
            )
        order = {"owner": 0, "head_judge": 1, "judge": 2, "applicant": 3}
        items.sort(key=lambda item: (order.get(str(item["role"]), 4), str(item["display_name"]).casefold()))
        return {"items": items}

    async def staff_action(self, principal, user_id, payload):
        action = str(payload.get("action") or "").casefold()
        reason = _bounded_text(payload.get("reason"), 1000, required=True)
        role_map = {"promote": "head_judge", "demote": "judge", "deactivate": "inactive", "restore": "judge"}
        desired = role_map.get(action)
        if desired is None:
            raise PortalError(400, "invalid_staff_action", "Choose a valid staff action")
        if payload.get("confirmed") is not True:
            raise PortalError(400, "confirmation_required", "Confirm the staff access change")
        owner_users = set(self.bot.config.get_int_list("staff_portal", "owner_user_ids"))
        if int(user_id) in owner_users or int(user_id) == principal.user_id:
            raise PortalError(409, "protected_staff_account", "That staff account cannot be changed here")
        judge_roles = self.bot.config.get_int_list("staff_portal", "judge_role_ids")
        head_roles = self.bot.config.get_int_list("staff_portal", "head_judge_role_ids")
        if not judge_roles or not head_roles:
            raise PortalError(503, "staff_roles_missing", "Judge roles are not fully configured")
        correlation = new_correlation_id("staff-role")
        actions = []
        if action == "promote":
            actions = [("add_role", head_roles[0]), ("remove_role", judge_roles[0])]
        elif action == "demote":
            actions = [("add_role", judge_roles[0]), ("remove_role", head_roles[0])]
        elif action == "deactivate":
            actions = [("remove_role", judge_roles[0]), ("remove_role", head_roles[0])]
        else:
            actions = [("add_role", judge_roles[0])]
        outbox_ids = []
        for index, (kind, role_id) in enumerate(actions):
            outbox_ids.append(await self.bot.outbox.enqueue(kind, guild_id=principal.guild_id, user_id=user_id, payload={"role_id": role_id, "reason": reason}, correlation_id=correlation, idempotency_key=f"staff-role:{correlation}:{index}"))
        now = int(time.time())
        await self.db.execute("INSERT INTO staff_members(guild_id,user_id,desired_role,status,updated_by,updated_ts,reason,role_outbox_id) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET desired_role=excluded.desired_role,status=excluded.status,updated_by=excluded.updated_by,updated_ts=excluded.updated_ts,reason=excluded.reason,role_outbox_id=excluded.role_outbox_id", (principal.guild_id, user_id, desired, "inactive" if desired == "inactive" else "active", principal.user_id, now, reason, outbox_ids[0] if outbox_ids else None))
        await self.db.execute("INSERT INTO workflow_events(correlation_id,workflow_type,entity_id,event,guild_id,actor_id,payload_json,created_ts) VALUES(?,'staff_portal',?,'staff_role_requested',?,?,?,?)", (correlation, f"staff:{user_id}", principal.guild_id, principal.user_id, json.dumps({"action": action, "reason": reason}), now))
        return {"ok": True, "delivery": "pending"}

    async def operations(self, principal):
        runtime = get_runtime_health()
        database = self.db.health_snapshot()
        outbox = await self.db.fetchall("SELECT status,COUNT(*) AS c FROM discord_outbox GROUP BY status")
        incidents = await self.db.fetchall("SELECT fingerprint,last_message,last_seen_ts,occurrence_count FROM error_incidents WHERE status='open' ORDER BY last_seen_ts DESC LIMIT 10")
        request_state = await self.db.fetchone("SELECT state,wave_id,submitted_count,request_limit,close_ts FROM level_request_state WHERE guild_id=?", (principal.guild_id,))
        return {"service": get_keepalive_status(), "runtime": runtime, "database": database, "outbox": {str(row["status"]): int(row["c"] or 0) for row in outbox}, "request_wave": _row_dict(request_state), "incidents": [_row_dict(row) for row in incidents]}

    async def pps_admin(self, principal):
        data = await self.priority.dashboard(principal.guild_id)
        settings = priority_settings(self.bot.config.data)
        return {
            "dashboard": _json_safe(data),
            "model_version": settings.model_version,
            "outcome_window_seconds": settings.outcome_window_seconds,
        }

    async def pps_action(self, principal, payload):
        principal.require("pps.manage_cycles")
        action = str(payload.get("action") or "").casefold()
        reason = _bounded_text(payload.get("reason"), 1000)
        if payload.get("confirmed") is not True:
            raise PortalError(400, "confirmation_required", "Confirm the PPS action")
        try:
            if action == "start_cycle":
                cycle = await self.priority.start_cycle(
                    principal.guild_id, principal.user_id, reason
                )
            elif action in {"complete_cycle", "cancel_cycle"}:
                cycle_id = int(payload.get("cycle_id") or 0)
                if cycle_id <= 0:
                    raise PortalError(400, "cycle_required", "Choose an outreach cycle")
                if action == "complete_cycle":
                    cycle = await self.priority.complete_cycle(
                        principal.guild_id, cycle_id, principal.user_id
                    )
                else:
                    cycle = await self.priority.cancel_cycle(
                        principal.guild_id, cycle_id, principal.user_id, reason
                    )
            elif action == "override_cp":
                principal.require("pps.override")
                if not reason:
                    raise PortalError(400, "reason_required", "A reason is required")
                queue_id = int(payload.get("queue_id") or 0)
                creator_points = int(payload.get("creator_points"))
                cycle = await self.priority.manual_cp_override(
                    principal.guild_id,
                    queue_id,
                    principal.user_id,
                    creator_points,
                    reason,
                )
            else:
                raise PortalError(400, "invalid_pps_action", "Choose a valid PPS action")
        except (TypeError, ValueError) as exc:
            raise PortalError(409, "pps_action_rejected", str(exc)) from exc
        await self._refresh_public_cache()
        return {"ok": True, "result": _json_safe(cycle)}

    async def audit(self, principal, query):
        where = ["guild_id=?"]
        params: list[Any] = [principal.guild_id]
        for key, column in (("actor", "actor_id"), ("action", "event"), ("entity", "entity_id")):
            value = str(query.get(key) or "").strip()[:100]
            if value:
                where.append(f"CAST({column} AS TEXT) LIKE ?")  # nosec B608
                params.append(f"%{value}%")
        for key, operator in (("from", ">="), ("to", "<=")):
            value = str(query.get(key) or "").strip()
            if value:
                try:
                    timestamp = int(value)
                except ValueError as exc:
                    raise PortalError(400, "invalid_date_filter", "Audit dates must be Unix timestamps") from exc
                where.append(f"created_ts{operator}?")  # nosec B608
                params.append(timestamp)
        rows = await self.db.fetchall(
            "SELECT id,workflow_type,entity_id,event,guild_id,actor_id,payload_json,created_ts "
            f"FROM workflow_events WHERE {' AND '.join(where)} "  # nosec B608
            "ORDER BY created_ts DESC,id DESC LIMIT 250",
            tuple(params),
        )
        return {"items": [_row_dict(row) for row in rows]}

    async def safe_configuration(self):
        saved = await self.db.get_runtime_setting("staff_portal.safe_config", {})
        return {"configuration": {"claim_stale_hours": int((saved or {}).get("claim_stale_hours") or self.bot.config.get_int("staff_portal", "claim_stale_hours", default=48)), "applications_open": bool((saved or {}).get("applications_open", True))}}

    async def update_safe_configuration(self, principal, payload):
        current = (await self.safe_configuration())["configuration"]
        if "claim_stale_hours" in payload:
            value = int(payload["claim_stale_hours"])
            if not 1 <= value <= 720:
                raise PortalError(400, "invalid_stale_threshold", "Stale threshold must be from 1 to 720 hours")
            current["claim_stale_hours"] = value
        if "applications_open" in payload:
            current["applications_open"] = bool(payload["applications_open"])
        await self.db.set_runtime_setting("staff_portal.safe_config", current)
        return {"configuration": current}

    async def search(self, principal, term):
        term = str(term or "").strip()[:100]
        if len(term) < 2:
            return {"items": []}
        like = f"%{term.casefold()}%"
        levels = await self.db.fetchall("SELECT id,level_id,current_level_name,uploader_name,queue_state FROM level_outreach_queue WHERE guild_id=? AND (level_id=? OR LOWER(COALESCE(current_level_name,'')) LIKE ? OR LOWER(COALESCE(uploader_name,'')) LIKE ?) ORDER BY updated_ts DESC LIMIT 8", (principal.guild_id, term, like, like))
        if principal.can("tasks.manage_team"):
            tasks = await self.db.fetchall("SELECT id,title,status,assignee_id FROM staff_tasks WHERE guild_id=? AND LOWER(title) LIKE ? ORDER BY updated_ts DESC LIMIT 6", (principal.guild_id, like))
        else:
            tasks = await self.db.fetchall(
                "SELECT id,title,status,assignee_id FROM staff_tasks WHERE guild_id=? AND LOWER(title) LIKE ? "
                "AND (assignee_id=? OR task_type='team' OR (task_type='personal' AND created_by=?)) "
                "ORDER BY updated_ts DESC LIMIT 6",
                (principal.guild_id, like, principal.user_id, principal.user_id),
            )
        items = [{"type": "level", **_row_dict(row)} for row in levels] + [{"type": "task", **_row_dict(row)} for row in tasks]
        if principal.can("applications.review_judge"):
            apps = await self.db.fetchall("SELECT id,application_type,status,applicant_id FROM staff_applications WHERE guild_id=? AND CAST(applicant_id AS TEXT) LIKE ? ORDER BY updated_ts DESC LIMIT 6", (principal.guild_id, like))
            items.extend({"type": "application", **_row_dict(row)} for row in apps)
        guild = self.bot.get_guild(principal.guild_id)
        if guild is not None:
            staff_role_ids = set(
                self.bot.config.get_int_list("staff_portal", "judge_role_ids")
                + self.bot.config.get_int_list("staff_portal", "head_judge_role_ids")
                + self.bot.config.get_int_list("staff_portal", "owner_role_ids")
            )
            owner_users = set(
                self.bot.config.get_int_list("staff_portal", "owner_user_ids")
            )
            lowered = term.casefold()
            for member in getattr(guild, "members", ()):
                roles = {int(role.id) for role in getattr(member, "roles", ())}
                if not (roles & staff_role_ids or int(member.id) in owner_users):
                    continue
                display_name = str(getattr(member, "display_name", ""))
                if lowered not in display_name.casefold() and lowered not in str(member.id):
                    continue
                items.append(
                    {
                        "type": "staff",
                        "id": str(member.id),
                        "display_name": display_name,
                        "role": resolve_staff_role(member.id, roles, self.bot.config),
                    }
                )
                if sum(item.get("type") == "staff" for item in items) >= 5:
                    break
        return {"items": items[:20]}

    async def _refresh_public_cache(self):
        cog = self.bot.get_cog("PrioritySystemCog")
        refresh = getattr(cog, "refresh_public_level_cache", None)
        if callable(refresh):
            try:
                await refresh()
            except Exception as exc:  # noqa: BLE001 - cache refresh is secondary to durable state.
                await log_error(
                    self.bot,
                    f"Staff portal public cache refresh deferred: {exc!r}",
                )


def secrets_compare(left: str, right: str) -> bool:
    return hmac.compare_digest(str(left), str(right))
