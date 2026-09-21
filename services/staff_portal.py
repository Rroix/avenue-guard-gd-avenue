from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import secrets
import time
import unicodedata
from collections import defaultdict, deque
from dataclasses import replace
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
    ROLE_ORDER,
    StaffPrincipal,
    capability_set,
    new_csrf_token,
    new_session_token,
    resolve_staff_role,
    role_label,
    token_hash,
    tokens_match,
)
from utils.workflows import new_correlation_id, record_workflow_event

MAX_SAFE_JS_INTEGER = 9_007_199_254_740_991
PORTAL_API_VERSION = 7
PORTAL_FEATURES = (
    "application_data_reset",
    "application_interviews",
    "application_type_availability",
    "application_repeat_interviews",
    "application_review_embeds",
    "application_review_threads",
    "application_staff_dm",
    "application_state_actions",
    "application_cooldown",
    "multi_type_applications",
    "hidden_queue_entries",
    "staff_manual_management",
    "staff_assignee_directory",
    "task_assignment_dm",
    "task_recipient_dm",
    "view_role_preview",
    "application_form_sections",
    "application_autosave",
    "application_submission_review",
    "application_immutable_snapshots",
    "application_rubrics",
    "application_calibration",
    "application_probation",
    "application_process_analytics",
)
DISCORD_ID_FIELDS = {
    "actor_id",
    "allowed_guild_id",
    "applicant_id",
    "assignee_id",
    "author_id",
    "channel_id",
    "claimed_by",
    "created_by",
    "completed_by",
    "decided_by",
    "ended_by",
    "forum_channel_id",
    "guild_id",
    "log_message_id",
    "legacy_id",
    "message_id",
    "new_owner_id",
    "offer_channel_id",
    "offer_message_id",
    "previous_owner_id",
    "qa_by",
    "request_message_id",
    "requester_id",
    "released_by",
    "reviewed_by",
    "reviewer_id",
    "satisfaction_user_id",
    "role_id",
    "started_by",
    "thread_id",
    "updated_by",
    "uploader_user_id",
    "user_id",
}

class PortalError(RuntimeError):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        correlation_id: str = "",
    ):
        self.status = int(status)
        self.code = str(code)
        self.message = str(message)
        self.correlation_id = str(correlation_id or "")
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


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value or "[]"))
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _bounded_text(value: Any, limit: int, *, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise PortalError(400, "required_field", "A required field is missing")
    if len(text) > limit:
        raise PortalError(400, "field_too_long", "One of the submitted fields is too long")
    return text


def _youtube_embed_url(value: Any) -> str:
    parsed = urlsplit(str(value or "").strip())
    host = str(parsed.hostname or "").casefold()
    video_id = ""
    if parsed.scheme == "https" and host == "youtu.be":
        video_id = parsed.path.strip("/").split("/", 1)[0]
    elif parsed.scheme == "https" and host in {"youtube.com", "www.youtube.com"}:
        if parsed.path.rstrip("/") == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[-1]
        elif parsed.path.startswith("/embed/"):
            video_id = parsed.path.split("/embed/", 1)[1].split("/", 1)[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        return ""
    return f"https://www.youtube-nocookie.com/embed/{video_id}"


def _discord_id(value: Any, *, field: str = "Discord ID") -> int:
    if isinstance(value, bool):
        raise PortalError(422, "invalid_discord_id", f"{field} must be a Discord ID string")
    if isinstance(value, int):
        if value > MAX_SAFE_JS_INTEGER:
            raise PortalError(
                422,
                "unsafe_discord_id",
                f"{field} must be sent as a string to preserve all digits",
            )
        parsed = value
    else:
        text = str(value or "").strip()
        if not text.isascii() or not text.isdecimal():
            raise PortalError(422, "invalid_discord_id", f"{field} must contain only digits")
        parsed = int(text)
    if parsed <= 0:
        raise PortalError(422, "invalid_discord_id", f"{field} is invalid")
    return parsed


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            name = str(key)
            if name in DISCORD_ID_FIELDS and item is not None:
                safe[name] = str(item)
            elif name == "role_ids" and isinstance(item, (list, tuple, set)):
                safe[name] = [str(role_id) for role_id in item]
            else:
                safe[name] = _json_safe(item)
        return safe
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    row = _row_dict(value)
    return _json_safe(row) if row else str(value)


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
        self._identity_cache: dict[int, tuple[float, dict[str, Any]]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.bot.config.get("staff_portal", "enabled", default=False))

    @property
    def guild_id(self) -> int:
        return self.bot.config.get_int("guild", "allowed_guild_id", default=0)

    @property
    def service_token(self) -> str:
        return str(os.getenv("STAFF_API_TOKEN", "") or "").strip()

    @staticmethod
    def _api_contract() -> dict[str, Any]:
        return {
            "version": PORTAL_API_VERSION,
            "features": list(PORTAL_FEATURES),
        }

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
        discord_display_name = str(
            getattr(member, "display_name", "") or getattr(member, "name", "Staff")
        )[:100]
        global_display_name = str(getattr(member, "global_name", "") or "")[:100]
        username = str(getattr(member, "name", "") or "")[:100]
        return StaffPrincipal(
            user_id=int(member.id),
            guild_id=self.guild_id,
            display_name=discord_display_name,
            avatar_url=str(avatar or "")[:1000],
            role=role,
            role_ids=role_ids,
            capabilities=capability_set(role),
            session_hash=session_hash,
            discord_display_name=discord_display_name,
            global_display_name=global_display_name,
            username=username,
        )

    async def _principal_with_profile(
        self, principal: StaffPrincipal
    ) -> StaffPrincipal:
        row = await self.db.fetchone(
            "SELECT portal_nickname FROM staff_portal_profiles WHERE guild_id=? AND user_id=?",
            (principal.guild_id, principal.user_id),
        )
        nickname = str(row["portal_nickname"] or "").strip() if row else ""
        display_name = (
            nickname
            or principal.discord_display_name
            or principal.global_display_name
            or principal.username
            or f"Reviewer {principal.user_id}"
        )
        return replace(
            principal,
            display_name=display_name[:100],
            portal_nickname=nickname[:40],
        )

    @property
    def _identity_cache_ttl(self) -> int:
        return max(
            30,
            min(
                3600,
                self.bot.config.get_int(
                    "staff_portal", "identity_cache_ttl_seconds", default=300
                ),
            ),
        )

    def _invalidate_identity(self, user_id: int) -> None:
        self._identity_cache.pop(int(user_id), None)

    async def _resolve_identity(self, user_id: int) -> dict[str, Any]:
        user_id = int(user_id)
        cached = self._identity_cache.get(user_id)
        if cached and cached[0] > time.monotonic():
            return dict(cached[1])
        guild = self.bot.get_guild(self.guild_id)
        member = guild.get_member(user_id) if guild is not None else None
        profile = await self.db.fetchone(
            "SELECT portal_nickname FROM staff_portal_profiles "
            "WHERE guild_id=? AND user_id=?",
            (self.guild_id, user_id),
        )
        nickname = str(profile["portal_nickname"] or "").strip() if profile else ""
        discord_display = str(getattr(member, "display_name", "") or "")[:100]
        global_display = str(getattr(member, "global_name", "") or "")[:100]
        username = str(getattr(member, "name", "") or "")[:100]
        unresolved = member is None and not nickname
        display_name = (
            nickname
            or discord_display
            or global_display
            or username
            or "Unresolved staff identity"
        )
        roles = tuple(
            int(role.id)
            for role in getattr(member, "roles", ())
            if getattr(role, "id", 0)
        )
        role = resolve_staff_role(user_id, roles, self.bot.config)
        avatar = str(
            getattr(getattr(member, "display_avatar", None), "url", "") or ""
        )[:1000]
        identity = {
            "id": str(user_id),
            "portal_nickname": nickname[:40],
            "discord_display_name": discord_display,
            "global_display_name": global_display,
            "username": username,
            "display_name": display_name[:100],
            "formatted_name": f"{display_name[:100]} ({user_id})",
            "avatar_url": avatar,
            "role": role,
            "role_label": role_label(role),
            "identity_status": "unresolved" if unresolved else "resolved",
            "legacy_id": "",
        }
        self._identity_cache[user_id] = (
            time.monotonic() + self._identity_cache_ttl,
            identity,
        )
        return dict(identity)

    async def _resolve_identities(
        self, user_ids: set[int] | list[int] | tuple[int, ...]
    ) -> dict[int, dict[str, Any]]:
        resolved: dict[int, dict[str, Any]] = {}
        for user_id in dict.fromkeys(int(item) for item in user_ids if item):
            resolved[user_id] = await self._resolve_identity(user_id)
        return resolved

    @staticmethod
    def _normalize_portal_nickname(value: Any) -> str:
        nickname = " ".join(str(value or "").strip().split())
        if len(nickname) > 40:
            raise PortalError(
                422,
                "nickname_too_long",
                "Portal nickname must be 40 characters or fewer.",
            )
        if any(unicodedata.category(char).startswith("C") for char in nickname):
            raise PortalError(
                422,
                "nickname_unsafe",
                "Portal nickname contains unsupported control characters.",
            )
        lowered = nickname.casefold()
        if "@everyone" in lowered or "@here" in lowered or "://" in lowered:
            raise PortalError(
                422,
                "nickname_unsafe",
                "Portal nickname cannot contain mentions or links.",
            )
        reserved = {
            "admin",
            "avenue guard",
            "dev",
            "discord",
            "gd avenue",
            "head reviewer",
            "owner",
            "reviewer",
        }
        if lowered in reserved:
            raise PortalError(
                422,
                "nickname_reserved",
                "That portal nickname could be mistaken for an official role or service.",
            )
        return nickname

    async def profile(self, principal: StaffPrincipal) -> dict[str, Any]:
        return {"profile": await self._resolve_identity(principal.user_id)}

    async def update_portal_nickname(
        self,
        principal: StaffPrincipal,
        target_user_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        target_user_id = int(target_user_id)
        changing_other = target_user_id != principal.user_id
        if changing_other:
            principal.require("staff.manage_nicknames")
        nickname = self._normalize_portal_nickname(payload.get("portal_nickname"))
        reason = _bounded_text(payload.get("reason"), 500)
        if changing_other and not reason:
            raise PortalError(
                422,
                "reason_required",
                "A reason is required when changing another staff member's nickname.",
            )
        current = await self.db.fetchone(
            "SELECT portal_nickname FROM staff_portal_profiles "
            "WHERE guild_id=? AND user_id=?",
            (principal.guild_id, target_user_id),
        )
        old_nickname = str(current["portal_nickname"] or "") if current else ""
        now = int(time.time())
        correlation = new_correlation_id("staff-nickname")
        event_payload = {
            "old_nickname": old_nickname,
            "new_nickname": nickname,
            "reason": reason,
        }
        await self.db.execute_transaction(
            [
                (
                    (
                        "INSERT INTO staff_portal_profiles("
                        "guild_id,user_id,portal_nickname,updated_ts,updated_by"
                        ") VALUES(?,?,?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET "
                        "portal_nickname=excluded.portal_nickname,"
                        "updated_ts=excluded.updated_ts,updated_by=excluded.updated_by"
                    ),
                    (
                        principal.guild_id,
                        target_user_id,
                        nickname,
                        now,
                        principal.user_id,
                    ),
                ),
                (
                    (
                        "INSERT INTO staff_portal_nickname_history("
                        "guild_id,user_id,actor_id,old_nickname,new_nickname,reason,"
                        "created_ts,correlation_id) VALUES(?,?,?,?,?,?,?,?)"
                    ),
                    (
                        principal.guild_id,
                        target_user_id,
                        principal.user_id,
                        old_nickname,
                        nickname,
                        reason,
                        now,
                        correlation,
                    ),
                ),
                (
                    (
                        "INSERT INTO workflow_events("
                        "correlation_id,workflow_type,entity_id,event,guild_id,actor_id,"
                        "payload_json,created_ts) VALUES(?,'staff_portal',?,"
                        "'portal_nickname_changed',?,?,?,?)"
                    ),
                    (
                        correlation,
                        f"staff:{target_user_id}",
                        principal.guild_id,
                        principal.user_id,
                        json.dumps(event_payload, separators=(",", ":")),
                        now,
                    ),
                ),
            ],
            retry_safe=True,
        )
        self._invalidate_identity(target_user_id)
        return {
            "ok": True,
            "profile": await self._resolve_identity(target_user_id),
        }

    async def nickname_history(
        self, principal: StaffPrincipal, target_user_id: int
    ) -> dict[str, Any]:
        principal.require("audit.view_full")
        rows = await self.db.fetchall(
            "SELECT id,user_id,actor_id,old_nickname,new_nickname,reason,"
            "created_ts,correlation_id FROM staff_portal_nickname_history "
            "WHERE guild_id=? AND user_id=? ORDER BY created_ts DESC,id DESC LIMIT 100",
            (principal.guild_id, int(target_user_id)),
        )
        identities = await self._resolve_identities(
            {
                int(row["actor_id"])
                for row in rows
                if row["actor_id"] is not None
            }
        )
        items = []
        for row in rows:
            item = _row_dict(row)
            actor_id = row["actor_id"]
            item["actor"] = (
                identities.get(int(actor_id)) if actor_id is not None else None
            )
            items.append(item)
        return {"items": items}

    async def create_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        user_id = _discord_id(payload.get("user_id"), field="Discord user ID")
        _guild, member = await self._member(user_id)
        if member is None:
            raise PortalError(403, "not_a_member", "You must be a GD Avenue member to continue")
        principal = await self._principal_with_profile(self._principal_from_member(member))
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
            "api": self._api_contract(),
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
        principal = await self._principal_with_profile(
            self._principal_from_member(member, session_hash=digest)
        )
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
            "portal_nickname": principal.portal_nickname,
            "discord_display_name": principal.discord_display_name,
            "global_display_name": principal.global_display_name,
            "username": principal.username,
            "avatar_url": principal.avatar_url,
            "role": principal.role,
            "role_label": role_label(principal.role),
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
        correlation = new_correlation_id("staff-api")
        try:
            status, response = await self._handle_request_inner(
                method, raw_path, headers, body
            )
            return status, _json_safe(response)
        except (PortalError, PermissionError):
            raise
        except Exception as exc:
            path = urlsplit(str(raw_path or "")).path
            code = (
                "STAFF_OVERVIEW_FAILED"
                if path == "/api/staff/overview"
                else "STAFF_API_FAILED"
            )
            await log_error(
                self.bot,
                f"Staff portal API failure correlation={correlation} method={method} "
                f"path={path}: {type(exc).__name__}: {exc}",
            )
            raise PortalError(
                500,
                code,
                "Could not load staff overview."
                if code == "STAFF_OVERVIEW_FAILED"
                else "The portal could not complete this request.",
                correlation_id=correlation,
            ) from exc

    async def _handle_request_inner(
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
        actual_principal = principal
        requested_view_role = str(headers.get("x-staff-view-role") or "").strip().casefold()
        if requested_view_role:
            if not actual_principal.can("developer.access"):
                raise PortalError(403, "view_mode_denied", "Only a Dev can use role view mode")
            if requested_view_role not in {"reviewer", "head_reviewer", "admin", "owner", "dev"}:
                raise PortalError(400, "invalid_view_role", "Choose a valid staff role to preview")
            if mutation:
                raise PortalError(409, "view_mode_read_only", "Leave role view mode before making changes")
            principal = replace(
                actual_principal,
                role=requested_view_role,
                capabilities=capability_set(requested_view_role),
            )
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
            response = {
                "user": self._principal_payload(principal),
                "api": self._api_contract(),
            }
            if actual_principal.can("developer.access"):
                response["view_mode"] = {
                    "active": bool(requested_view_role),
                    "actual_role": actual_principal.role,
                    "roles": [
                        {
                            "key": role,
                            "label": role_label(role),
                            "capabilities": sorted(capability_set(role)),
                        }
                        for role in ("reviewer", "head_reviewer", "admin", "owner", "dev")
                    ],
                }
            return 200, response
        if path == "/api/apply/session" and method == "GET":
            return 200, {
                "user": self._principal_payload(principal),
                "api": self._api_contract(),
            }

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
        if path == "/api/staff/profile":
            if method == "GET":
                return 200, await self.profile(principal)
            if method == "PATCH":
                return 200, await self.update_portal_nickname(
                    principal, principal.user_id, payload
                )
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
        if path == "/api/staff/assignees" and method == "GET":
            return 200, await self.staff_assignees(principal)
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
            principal.require("staff.view")
            return 200, await self.staff_list(principal)
        if path == "/api/staff/staff" and method == "POST":
            principal.require("developer.access")
            return 201, await self.add_staff(principal, payload)
        if path == "/api/staff/operations" and method == "GET":
            principal.require("operations.view")
            return 200, await self.operations(principal)
        if path == "/api/staff/requests":
            principal.require("requests.manage")
            if method == "GET":
                return 200, await self.requests_admin(principal)
            if method == "POST":
                return 200, await self.requests_action(principal, payload)
        if path == "/api/staff/community":
            principal.require("admin.access")
            if method == "GET":
                return 200, await self.community(principal)
            if method == "POST":
                return 200, await self.community_action(principal, payload)
        if path == "/api/staff/system":
            principal.require("developer.access")
            if method == "GET":
                return 200, await self.system(principal)
            if method == "POST":
                return 200, await self.system_action(principal, payload)
        if path == "/api/staff/pps":
            principal.require("pps.manage_cycles")
            if method == "GET":
                return 200, await self.pps_admin(principal)
            if method == "POST":
                return 200, await self.pps_action(principal, payload)
        if path == "/api/staff/audit" and method == "GET":
            if not (
                principal.can("audit.view_limited") or principal.can("audit.view")
            ):
                raise PermissionError("Missing capability: audit.view_limited")
            return 200, await self.audit(principal, query)
        if path == "/api/staff/configuration":
            principal.require("config.manage_safe")
            if method == "GET":
                return 200, await self.safe_configuration()
            if method == "PATCH":
                return 200, await self.update_safe_configuration(principal, payload)
        if path == "/api/staff/search" and method == "GET":
            return 200, await self.search(principal, query.get("q", ""))

        queue_match = re.fullmatch(r"/api/staff/queue/(\d+)(?:/(claim|release|reassign|state|requeue|tier|hide|restore))?", path)
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
        app_match = re.fullmatch(
            r"/api/staff/applications/(\d+)/(action|note|assessment|interview|probation)",
            path,
        )
        if app_match and method == "POST":
            principal.require("applications.review_judge")
            if app_match.group(2) == "note":
                return 201, await self.application_note(principal, int(app_match.group(1)), payload)
            if app_match.group(2) == "assessment":
                return 200, await self.application_assessment(
                    principal, int(app_match.group(1)), payload
                )
            if app_match.group(2) == "interview":
                return 200, await self.application_interview_outcome(
                    principal, int(app_match.group(1)), payload
                )
            if app_match.group(2) == "probation":
                return 200, await self.application_probation_action(
                    principal, int(app_match.group(1)), payload
                )
            return 200, await self.application_action(principal, int(app_match.group(1)), payload)
        staff_match = re.fullmatch(r"/api/staff/staff/(\d+)/action", path)
        if staff_match and method == "POST":
            principal.require("staff.manage_standard_roles")
            return 200, await self.staff_action(principal, staff_match.group(1), payload)
        nickname_match = re.fullmatch(
            r"/api/staff/staff/(\d+)/nickname(?:/(history))?", path
        )
        if nickname_match:
            target_user_id = _discord_id(
                nickname_match.group(1), field="Staff member ID"
            )
            if method == "PATCH" and not nickname_match.group(2):
                return 200, await self.update_portal_nickname(
                    principal, target_user_id, payload
                )
            if method == "GET" and nickname_match.group(2) == "history":
                return 200, await self.nickname_history(
                    principal, target_user_id
                )
        incident_match = re.fullmatch(
            r"/api/staff/operations/incidents/([a-f0-9]{8,64})", path
        )
        if incident_match and method == "GET":
            principal.require("operations.view")
            return 200, await self.incident_detail(
                principal, incident_match.group(1)
            )
        raise PortalError(404, "not_found", "That portal resource does not exist")

    async def overview(self, principal: StaffPrincipal) -> dict[str, Any]:
        correlation = new_correlation_id("staff-overview")
        warnings: list[dict[str, str]] = []
        failures = 0

        async def part(name: str, operation, fallback):
            nonlocal failures
            try:
                return await operation
            except Exception as exc:  # noqa: BLE001 - overview supports partial data.
                failures += 1
                warning_id = f"{correlation}:{name}"
                warnings.append(
                    {
                        "section": name,
                        "message": "Some data could not be refreshed",
                        "correlation_id": warning_id,
                    }
                )
                await log_error(
                    self.bot,
                    f"Staff overview partial failure correlation={warning_id}: "
                    f"{type(exc).__name__}: {exc}",
                )
                return fallback

        await part(
            "attention_tasks",
            self._sync_system_tasks(principal.guild_id),
            None,
        )
        now = int(time.time())
        month_start = now - 31 * 86400
        stale_cutoff = now - await self._claim_stale_seconds()
        claim_row = _row_dict(
            await part(
                "claims",
                self.db.fetchone(
                    "SELECT COUNT(*) AS active,"
                    "SUM(CASE WHEN claimed_ts<? THEN 1 ELSE 0 END) AS stale "
                    "FROM staff_queue_claims WHERE guild_id=? AND claimed_by=? "
                    "AND claim_state='active'",
                    (stale_cutoff, principal.guild_id, principal.user_id),
                ),
                {},
            )
        )
        task_row = _row_dict(
            await part(
                "tasks",
                self.db.fetchone(
                    "SELECT COUNT(*) AS total,"
                    "SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done,"
                    "SUM(CASE WHEN status IN('todo','in_progress') "
                    "AND due_ts IS NOT NULL AND due_ts<=? THEN 1 ELSE 0 END) AS due "
                    "FROM staff_tasks WHERE guild_id=? AND "
                    "(assignee_id=? OR (task_type='personal' AND created_by=?)) "
                    "AND status!='cancelled'",
                    (
                        now + 86400,
                        principal.guild_id,
                        principal.user_id,
                        principal.user_id,
                    ),
                ),
                {},
            )
        )
        reviews = _row_dict(
            await part(
                "reviews",
                self.db.fetchone(
                    "SELECT COUNT(*) AS total,"
                    "SUM(CASE WHEN reviewed_ts>=? THEN 1 ELSE 0 END) AS c "
                    "FROM level_request_submissions WHERE guild_id=? "
                    "AND reviewed_by=? AND status='reviewed'",
                    (month_start, principal.guild_id, principal.user_id),
                ),
                {},
            )
        )
        outreach = _row_dict(
            await part(
                "outreach",
                self.db.fetchone(
                    "SELECT SUM(CASE WHEN a.created_ts>=? THEN 1 ELSE 0 END) "
                    "AS attempts,"
                    "SUM(CASE WHEN a.created_ts>=? "
                    "AND a.status='submitted_to_mod' THEN 1 ELSE 0 END) AS submitted,"
                    "SUM(CASE WHEN a.status='submitted_to_mod' THEN 1 ELSE 0 END) "
                    "AS submitted_total "
                    "FROM level_outreach_attempts a "
                    "JOIN level_outreach_cycles c ON c.id=a.cycle_id "
                    "WHERE c.guild_id=? AND a.actor_id=?",
                    (
                        month_start,
                        month_start,
                        principal.guild_id,
                        principal.user_id,
                    ),
                ),
                {},
            )
        )
        active_days = _row_dict(
            await part(
                "active_days",
                self.db.fetchone(
                    "SELECT COUNT(DISTINCT day) AS c FROM ("
                    "SELECT date(reviewed_ts,'unixepoch') AS day "
                    "FROM level_request_submissions WHERE guild_id=? "
                    "AND reviewed_by=? AND reviewed_ts>=? "
                    "UNION SELECT date(a.created_ts,'unixepoch') "
                    "FROM level_outreach_attempts a "
                    "JOIN level_outreach_cycles c ON c.id=a.cycle_id "
                    "WHERE c.guild_id=? AND a.actor_id=? AND a.created_ts>=? "
                    "UNION SELECT date(completed_ts,'unixepoch') FROM staff_tasks "
                    "WHERE guild_id=? AND assignee_id=? AND completed_ts>=?)",
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
                ),
                {},
            )
        )
        followups = _row_dict(
            await part(
                "followups",
                self.db.fetchone(
                    "SELECT COUNT(*) AS c FROM level_outreach_queue q "
                    "JOIN staff_queue_claims c ON c.queue_id=q.id "
                    "WHERE q.guild_id=? AND c.claim_state='active' "
                    "AND c.claimed_by=? AND q.queue_state='awaiting_outcome' "
                    "AND q.outcome_window_due_ts IS NOT NULL "
                    "AND q.outcome_window_due_ts<=?",
                    (principal.guild_id, principal.user_id, now + 86400),
                ),
                {},
            )
        )
        pipeline_rows = await part(
            "pipeline",
            self.db.fetchall(
                "SELECT queue_state,COUNT(*) AS c FROM level_outreach_queue "
                "WHERE guild_id=? AND queue_state!='hidden' GROUP BY queue_state",
                (principal.guild_id,),
            ),
            [],
        )
        application_count = _row_dict(
            await part(
                "applications",
                self.db.fetchone(
                    "SELECT COUNT(*) AS c FROM staff_applications WHERE guild_id=? "
                    "AND status IN('submitted','under_review','interview','hold')",
                    (principal.guild_id,),
                ),
                {},
            )
        )
        events = await part(
            "activity",
            self.db.fetchall(
                "SELECT event,entity_id,actor_id,created_ts FROM workflow_events "
                "WHERE guild_id=? ORDER BY created_ts DESC,id DESC LIMIT 8",
                (principal.guild_id,),
            ),
            [],
        )
        if failures >= 9:
            raise PortalError(
                503,
                "STAFF_OVERVIEW_UNAVAILABLE",
                "Staff overview data is temporarily unavailable.",
                correlation_id=correlation,
            )
        milestones = self._milestones(
            int(reviews.get("total") or 0),
            int(outreach.get("submitted_total") or 0),
        )
        await part(
            "milestones",
            self._persist_milestones(principal, milestones),
            None,
        )
        activity_identities = await part(
            "activity_identities",
            self._resolve_identities(
                {
                    int(row["actor_id"])
                    for row in events
                    if row["actor_id"] is not None
                }
            ),
            {},
        )
        recent_activity = []
        for row in events:
            item = _row_dict(row)
            actor_id = row["actor_id"]
            item["actor"] = (
                activity_identities.get(int(actor_id))
                if actor_id is not None
                else None
            )
            recent_activity.append(item)
        return {
            "user": self._principal_payload(principal),
            "summary": {
                "active_claims": int(claim_row.get("active") or 0),
                "stale_claims": int(claim_row.get("stale") or 0),
                "tasks_remaining": max(
                    0,
                    int(task_row.get("total") or 0)
                    - int(task_row.get("done") or 0),
                ),
                "tasks_due": int(task_row.get("due") or 0),
                "followups_due": int(followups.get("c") or 0),
            },
            "progress": {
                "reviews_month": int(reviews.get("c") or 0),
                "tasks_done": int(task_row.get("done") or 0),
                "tasks_total": int(task_row.get("total") or 0),
                "outreach_attempts": int(outreach.get("attempts") or 0),
                "confirmed_submissions": int(outreach.get("submitted") or 0),
                "active_days": int(active_days.get("c") or 0),
                "milestones": milestones,
            },
            "pipeline": {str(row["queue_state"]): int(row["c"] or 0) for row in pipeline_rows},
            "pending_applications": int(application_count.get("c") or 0),
            "recent_activity": recent_activity,
            "warnings": warnings,
            "correlation_id": correlation,
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
            "SELECT id,application_type FROM staff_applications WHERE guild_id=? AND status IN('submitted','under_review','interview','hold') "
            "AND updated_ts<=? LIMIT 100",
            (guild_id, now - 7 * 86400),
        )
        for row in application_rows:
            key = f"application-waiting:{int(row['id'])}"
            desired[key] = (
                guild_id,
                "system",
                f"{self._application_label(str(row['application_type']))} needs attention",
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
        if filter_key == "hidden":
            principal.require("developer.access")
            where.append("q.queue_state='hidden'")
        else:
            where.append("q.queue_state!='hidden'")
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
        claim_user_id = int(data.get("claimed_by") or 0)
        claim_identity = (
            await self._resolve_identity(claim_user_id) if claim_user_id else None
        )
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
                    "identity": claim_identity,
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
        if str(row["queue_state"] or "") == "hidden" and not principal.can("developer.access"):
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
        actor_ids = {
            int(item[key])
            for item in (*attempts, *events)
            for key in ("actor_id",)
            if item[key] is not None
        }
        identities = await self._resolve_identities(actor_ids)
        outreach_items = []
        for item in attempts:
            payload = _row_dict(item)
            payload["actor"] = identities.get(int(item["actor_id"]))
            outreach_items.append(payload)
        history_items = []
        for item in events:
            payload = _row_dict(item)
            payload["actor"] = identities.get(int(item["actor_id"]))
            history_items.append(payload)
        return {
            "queue": await self._queue_payload(row),
            "outreach": outreach_items,
            "history": history_items,
            "notes": notes,
        }

    async def queue_action(self, principal, queue_id: int, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if action not in {"hide", "restore"}:
            queue_row = await self.priority.queue_entry(
                principal.guild_id, queue_id, include_hidden=True
            )
            if queue_row is None:
                raise PortalError(404, "queue_not_found", "Queue entry not found")
            if str(queue_row["queue_state"] or "") == "hidden":
                raise PortalError(
                    409,
                    "queue_hidden",
                    "Restore this hidden level before performing another action",
                )
        if action == "claim":
            principal.require("queue.claim")
            assignee = (
                _discord_id(payload.get("assignee_id"), field="Assignee ID")
                if payload.get("assignee_id")
                else principal.user_id
            )
            if assignee != principal.user_id:
                principal.require("queue.reassign")
                await self._require_active_staff_assignee(principal, assignee)
            return await self._claim(principal, queue_id, assignee, _bounded_text(payload.get("reason"), 500))
        if action == "release":
            return await self._release_claim(principal, queue_id, _bounded_text(payload.get("reason"), 500))
        if action == "reassign":
            principal.require("queue.reassign")
            assignee = _discord_id(payload.get("assignee_id"), field="Assignee ID")
            await self._require_active_staff_assignee(principal, assignee)
            return await self._claim(
                principal,
                queue_id,
                assignee,
                _bounded_text(payload.get("reason"), 500, required=True),
                reassign=True,
            )
        if action == "state":
            principal.require("queue.manage_state")
            return await self._transition_queue(principal, queue_id, payload)
        if action == "requeue":
            principal.require("queue.manage_state")
            return await self._requeue(principal, queue_id, payload)
        if action == "tier":
            principal.require("review.adjust_tier")
            return await self._adjust_tier(principal, queue_id, payload)
        if action == "hide":
            principal.require("developer.access")
            return await self._hide_queue(principal, queue_id, payload)
        if action == "restore":
            principal.require("developer.access")
            return await self._restore_hidden_queue(principal, queue_id, payload)
        raise PortalError(404, "unknown_action", "Unknown queue action")

    async def _hide_queue(self, principal, queue_id: int, payload: dict[str, Any]):
        if payload.get("confirmed") is not True:
            raise PortalError(400, "confirmation_required", "Confirm that this level should be hidden")
        reason = _bounded_text(payload.get("reason"), 1000, required=True)
        row = await self.priority.queue_entry(
            principal.guild_id, queue_id, include_hidden=True
        )
        if row is None:
            raise PortalError(404, "queue_not_found", "Queue entry not found")
        old_state = str(row["queue_state"] or "queued")
        if old_state == "hidden":
            return {"ok": True, "state": "hidden"}
        now = int(time.time())
        correlation = new_correlation_id("queue-hidden")
        await self.db.execute_transaction(
            [
                (
                    (
                        "UPDATE level_outreach_queue SET queue_state='hidden',"
                        "hidden_from_state=?,updated_ts=? "
                        "WHERE id=? AND guild_id=? AND queue_state=?"
                    ),
                    (old_state, now, queue_id, principal.guild_id, old_state),
                ),
                (
                    (
                        "UPDATE staff_queue_claims SET claim_state='released',"
                        "released_by=?,released_ts=?,updated_ts=? "
                        "WHERE queue_id=? AND guild_id=? AND claim_state='active'"
                    ),
                    (principal.user_id, now, now, queue_id, principal.guild_id),
                ),
                (
                    (
                        "INSERT INTO workflow_events(correlation_id,workflow_type,"
                        "entity_id,event,guild_id,actor_id,payload_json,created_ts) "
                        "VALUES(?,'priority_system',?,'queue_hidden',?,?,?,?)"
                    ),
                    (correlation, f"queue:{queue_id}", principal.guild_id, principal.user_id, json.dumps({"old_state": old_state, "reason": reason}), now),
                ),
            ],
            retry_safe=True,
        )
        await self._refresh_public_cache()
        return {"ok": True, "state": "hidden", "hidden_from_state": old_state}

    async def _restore_hidden_queue(self, principal, queue_id: int, payload: dict[str, Any]):
        if payload.get("confirmed") is not True:
            raise PortalError(400, "confirmation_required", "Confirm that this level should be restored")
        reason = _bounded_text(payload.get("reason"), 1000, required=True)
        row = await self.priority.queue_entry(
            principal.guild_id, queue_id, include_hidden=True
        )
        if row is None:
            raise PortalError(404, "queue_not_found", "Queue entry not found")
        if str(row["queue_state"] or "") != "hidden":
            raise PortalError(409, "queue_not_hidden", "That level is not hidden")
        restored_state = str(row["hidden_from_state"] or "queued")
        if restored_state not in {"queued", "paused", "withdrawn", "invalid", "in_cycle", "awaiting_outcome", "rated"}:
            restored_state = "queued"
        now = int(time.time())
        correlation = new_correlation_id("queue-restored")
        changed = await self.db.execute_affected(
            "UPDATE level_outreach_queue SET queue_state=?,hidden_from_state=NULL,updated_ts=? "
            "WHERE id=? AND guild_id=? AND queue_state='hidden'",
            (restored_state, now, queue_id, principal.guild_id),
        )
        if changed != 1:
            raise PortalError(409, "queue_changed", "That level changed while it was being restored")
        await self.db.execute(
            "INSERT INTO workflow_events(correlation_id,workflow_type,entity_id,event,guild_id,actor_id,payload_json,created_ts) "
            "VALUES(?,'priority_system',?,'queue_restored',?,?,?,?)",
            (correlation, f"queue:{queue_id}", principal.guild_id, principal.user_id, json.dumps({"restored_state": restored_state, "reason": reason}), now),
        )
        await self._refresh_public_cache()
        return {"ok": True, "state": restored_state}

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
                raise PortalError(400, "reason_required", "A reason is required to release another Reviewer's claim")
            stale = int(claim["claimed_ts"] or 0) < int(time.time()) - await self._claim_stale_seconds()
            if not principal.can("pps.override") and not stale:
                raise PortalError(409, "claim_not_stale", "Only stale claims can be released by another Reviewer")
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
            "AND q.queue_state!='hidden' "
            "ORDER BY COALESCE(a.event_ts,a.created_ts) DESC LIMIT 100",
            (principal.guild_id,),
        )
        counts = await self.db.fetchall(
            "SELECT e.status,COUNT(*) AS c FROM staff_outreach_episodes e "
            "JOIN level_outreach_queue q ON q.id=e.queue_id "
            "WHERE e.guild_id=? AND q.queue_state!='hidden' GROUP BY e.status",
            (principal.guild_id,),
        )
        identities = await self._resolve_identities(
            {int(row["actor_id"]) for row in rows if row["actor_id"] is not None}
        )
        items = []
        for row in rows:
            item = _row_dict(row)
            actor_id = int(row["actor_id"]) if row["actor_id"] is not None else 0
            item["actor"] = identities.get(actor_id)
            if not principal.can("audit.view_full"):
                item.pop("payload_json", None)
            items.append(item)
        return {
            "items": items,
            "pipeline": {
                str(row["status"]): int(row["c"] or 0) for row in counts
            },
        }

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
        identities = await self._resolve_identities(
            {
                int(item[field])
                for item in items
                for field in ("assignee_id", "created_by")
                if item.get(field)
            }
        )
        for item in items:
            if item.get("assignee_id"):
                item["assignee"] = identities.get(int(item["assignee_id"]))
            if item.get("created_by"):
                item["creator"] = identities.get(int(item["created_by"]))
        total = sum(item["status"] != "cancelled" for item in items)
        done = sum(item["status"] == "done" for item in items)
        return {"items": items, "progress": {"done": done, "total": total}}

    async def staff_assignees(self, principal):
        if not (
            principal.can("tasks.assign") or principal.can("queue.reassign")
        ):
            raise PermissionError("Missing capability: tasks.assign or queue.reassign")
        directory = await self.staff_list(principal)
        return {
            "items": [
                {
                    "id": str(item["id"]),
                    "display_name": str(item["display_name"]),
                    "role": str(item["role"]),
                    "role_label": str(item["role_label"]),
                    "avatar_url": str(item.get("avatar_url") or ""),
                }
                for item in directory["items"]
                if item.get("active")
            ]
        }

    async def _require_active_staff_assignee(self, principal, user_id: int) -> None:
        directory = await self.staff_assignees(principal)
        if int(user_id) not in {int(item["id"]) for item in directory["items"]}:
            raise PortalError(
                400,
                "invalid_staff_assignee",
                "Choose an active member of the GD Avenue staff team",
            )

    @staticmethod
    def _task_notification_embed(
        *,
        task_type: str,
        title: str,
        description: str,
        priority: str,
        due_ts: int | None,
    ) -> discord.Embed:
        embed = discord.Embed(
            title=f"New {task_type} staff task",
            description=description or "No description was provided.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Title", value=title, inline=False)
        embed.add_field(name="Priority", value=priority.title(), inline=True)
        embed.add_field(name="Type", value=task_type.title(), inline=True)
        embed.add_field(
            name="Due date",
            value=(f"<t:{due_ts}:F>\n<t:{due_ts}:R>" if due_ts else "No due date"),
            inline=False,
        )
        embed.set_footer(text="Open the GD Avenue Staff Portal to update this task.")
        return embed

    async def create_task(self, principal, payload):
        principal.require("tasks.create")
        task_type = str(payload.get("task_type") or "personal").casefold()
        if task_type not in {"personal", "assigned", "team"}:
            raise PortalError(400, "invalid_task_type", "Choose a valid task type")
        raw_assignee = payload.get("assignee_id")
        staff_directory = None
        if task_type == "personal":
            assignee_id = principal.user_id
            if raw_assignee and _discord_id(raw_assignee, field="Assignee ID") != principal.user_id:
                raise PortalError(
                    400,
                    "invalid_personal_assignee",
                    "Personal tasks can only be assigned to yourself",
                )
        elif task_type == "assigned":
            principal.require("tasks.assign")
            if not raw_assignee:
                raise PortalError(
                    400,
                    "assignee_required",
                    "Choose a staff member for an assigned task",
                )
            assignee_id = _discord_id(raw_assignee, field="Assignee ID")
            staff_directory = await self.staff_assignees(principal)
            active_staff_ids = {int(item["id"]) for item in staff_directory["items"]}
            if assignee_id not in active_staff_ids:
                raise PortalError(
                    400,
                    "invalid_staff_assignee",
                    "Choose an active member of the GD Avenue staff team",
                )
        else:
            principal.require("tasks.assign")
            assignee_id = None
            staff_directory = await self.staff_assignees(principal)
        title = _bounded_text(payload.get("title"), 160, required=True)
        description = _bounded_text(payload.get("description"), 4000)
        priority = str(payload.get("priority") or "normal").casefold()
        if priority not in {"low", "normal", "high", "urgent"}:
            priority = "normal"
        try:
            due_ts = int(payload.get("due_ts") or 0) or None
        except (TypeError, ValueError) as exc:
            raise PortalError(
                400, "invalid_due_date", "Choose a valid task due date"
            ) from exc
        linked_entity_type = _bounded_text(payload.get("linked_entity_type"), 40).casefold()
        linked_entity_id = _bounded_text(payload.get("linked_entity_id"), 100)
        if linked_entity_type not in {"", "level", "application", "task"}:
            raise PortalError(
                400,
                "invalid_linked_entity_type",
                "Choose a supported linked record type",
            )
        if bool(linked_entity_type) != bool(linked_entity_id):
            raise PortalError(
                400,
                "incomplete_linked_entity",
                "Choose both a linked record type and its internal ID, or leave both blank",
            )
        if linked_entity_id and not linked_entity_id.isdigit():
            raise PortalError(
                400,
                "invalid_linked_entity_id",
                "The linked record ID must contain digits only",
            )
        now = int(time.time())
        task_id = await self.db.execute_insert(
            "INSERT INTO staff_tasks(guild_id,task_type,title,description,priority,status,created_by,assignee_id,created_ts,updated_ts,due_ts,linked_entity_type,linked_entity_id) VALUES(?,?,?,?,?,'todo',?,?,?,?,?,?,?)",
            (principal.guild_id, task_type, title, description, priority, principal.user_id, assignee_id, now, now, due_ts, linked_entity_type or None, linked_entity_id or None),
        )
        if task_type == "team":
            recipient_ids = {
                int(item["id"])
                for item in (staff_directory or {"items": []})["items"]
            } | {principal.user_id}
        else:
            recipient_ids = {int(assignee_id)}
        embed = self._task_notification_embed(
            task_type=task_type,
            title=title,
            description=description,
            priority=priority,
            due_ts=due_ts,
        )
        for recipient_id in sorted(recipient_ids):
            await self.bot.outbox.enqueue(
                "send_dm",
                guild_id=principal.guild_id,
                user_id=recipient_id,
                payload={"embed": embed.to_dict()},
                correlation_id=f"staff-task:{task_id}",
                idempotency_key=f"staff-task:{task_id}:created:{recipient_id}",
            )
        return {
            "task": _row_dict(
                await self.db.fetchone("SELECT * FROM staff_tasks WHERE id=?", (task_id,))
            ),
            "notification_recipient_count": len(recipient_ids),
        }

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
        items = [
            item
            for row in rows
            if (item := _row_dict(row)) and self._note_visible(principal, item, scopes)
        ]
        identities = await self._resolve_identities(
            {int(item["author_id"]) for item in items if item.get("author_id")}
        )
        for item in items:
            item["author"] = identities.get(int(item["author_id"]))
        return {"items": items}

    def _note_visible(self, principal, item, scopes=None):
        scopes = scopes or self._note_scopes(principal)
        scope = str(item.get("scope"))
        if scope == "private":
            return int(item.get("author_id") or 0) == principal.user_id
        return scope in scopes

    async def _notes_for_entity(self, principal, entity_type, entity_id):
        rows = await self.db.fetchall("SELECT * FROM staff_notes WHERE guild_id=? AND entity_type=? AND entity_id=? AND archived_ts IS NULL ORDER BY updated_ts DESC LIMIT 100", (principal.guild_id, entity_type, entity_id))
        items = [
            item
            for row in rows
            if (item := _row_dict(row)) and self._note_visible(principal, item)
        ]
        identities = await self._resolve_identities(
            {int(item["author_id"]) for item in items if item.get("author_id")}
        )
        for item in items:
            item["author"] = identities.get(int(item["author_id"]))
        return items

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
        queue_counts = await self.db.fetchall("SELECT queue_state,COUNT(*) AS c FROM level_outreach_queue WHERE guild_id=? AND queue_state!='hidden' GROUP BY queue_state", (principal.guild_id,))
        claims = await self.db.fetchone("SELECT COUNT(*) AS active,SUM(CASE WHEN claimed_ts<? THEN 1 ELSE 0 END) AS stale FROM staff_queue_claims WHERE guild_id=? AND claim_state='active'", (now - await self._claim_stale_seconds(), principal.guild_id))
        outreach = await self.db.fetchone(
            "SELECT COUNT(*) AS attempts,SUM(CASE WHEN a.status='submitted_to_mod' THEN 1 ELSE 0 END) AS submissions "
            "FROM level_outreach_attempts a JOIN level_outreach_cycles c ON c.id=a.cycle_id "
            "JOIN level_outreach_queue q ON q.id=a.queue_id "
            "WHERE c.guild_id=? AND q.queue_state!='hidden' "
            "AND COALESCE(a.event_ts,a.created_ts)>=?",
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
        workload_identities = await self._resolve_identities(
            {
                int(row["claimed_by"])
                for row in workload
                if row["claimed_by"] is not None
            }
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
                {
                    "user_id": str(row["claimed_by"]),
                    "identity": workload_identities.get(int(row["claimed_by"])),
                    "active_claims": int(row["c"] or 0),
                }
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
            "JOIN level_outreach_queue q ON q.id=a.queue_id "
            f"WHERE c.guild_id=? AND q.queue_state!='hidden'{outreach_filter}",  # nosec B608
            outreach_params,
        )
        routes = await self.db.fetchall(
            "SELECT a.route_type,COUNT(*) AS attempts,"
            "SUM(CASE WHEN a.status='submitted_to_mod' THEN 1 ELSE 0 END) AS submissions "
            "FROM level_outreach_attempts a JOIN level_outreach_cycles c ON c.id=a.cycle_id "
            "JOIN level_outreach_queue q ON q.id=a.queue_id "
            f"WHERE c.guild_id=? AND q.queue_state!='hidden'{outreach_filter} "  # nosec B608
            "GROUP BY a.route_type ORDER BY attempts DESC",
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
            "JOIN level_outreach_queue q ON q.id=a.queue_id "
            f"WHERE c.guild_id=? AND q.queue_state!='hidden'{outreach_filter})",  # nosec B608
            (*params, *outreach_params),
        )
        queue_counts = await self.db.fetchall(
            "SELECT queue_state,COUNT(*) AS c FROM level_outreach_queue WHERE guild_id=? AND queue_state!='hidden' GROUP BY queue_state",
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
        application_timing = await self.db.fetchone(
            "SELECT COUNT(*) AS submitted,"
            "SUM(CASE WHEN status IN('submitted','under_review','interview','hold',"
            "'accepted_pending_role','accepted','rejected') THEN 1 ELSE 0 END) AS active_or_decided,"
            "AVG(CASE WHEN first_review_ts>=submitted_ts THEN first_review_ts-submitted_ts END) "
            "AS average_first_review_seconds,"
            "AVG(CASE WHEN decided_ts>=submitted_ts THEN decided_ts-submitted_ts END) "
            "AS average_decision_seconds,"
            "SUM(CASE WHEN status IN('submitted','under_review','interview','hold',"
            "'accepted_pending_role') THEN 1 ELSE 0 END) AS pending "
            "FROM staff_applications WHERE guild_id=? AND submitted_ts IS NOT NULL",
            (principal.guild_id,),
        )
        application_outcomes = await self.db.fetchall(
            "SELECT application_type,status,COUNT(*) AS c FROM staff_applications "
            "WHERE guild_id=? AND submitted_ts IS NOT NULL GROUP BY application_type,status",
            (principal.guild_id,),
        )
        reason_rows = await self.db.fetchall(
            "SELECT decision_category,COUNT(*) AS c FROM staff_applications "
            "WHERE guild_id=? AND decision_category!='' GROUP BY decision_category "
            "ORDER BY c DESC",
            (principal.guild_id,),
        )
        interview_count = await self.db.fetchone(
            "SELECT COUNT(DISTINCT i.application_id) AS c FROM staff_application_interviews i "
            "JOIN staff_applications a ON a.id=i.application_id WHERE a.guild_id=?",
            (principal.guild_id,),
        )
        probation_rows = await self.db.fetchall(
            "SELECT status,COUNT(*) AS c FROM staff_application_probations "
            "WHERE guild_id=? GROUP BY status",
            (principal.guild_id,),
        )
        assessment_rows = await self.db.fetchall(
            "SELECT a.id,a.application_type,a.calibration_resolved_ts,s.scores_json,"
            "s.evidence_json,s.recommendation FROM staff_applications a "
            "JOIN staff_application_assessments s ON s.application_id=a.id "
            "WHERE a.guild_id=? ORDER BY a.id",
            (principal.guild_id,),
        )
        assessments_by_application: dict[int, list[dict[str, Any]]] = defaultdict(list)
        application_assessment_meta: dict[int, tuple[str, bool]] = {}
        for assessment in assessment_rows:
            application_id = int(assessment["id"])
            application_assessment_meta[application_id] = (
                str(assessment["application_type"]),
                bool(assessment["calibration_resolved_ts"]),
            )
            assessments_by_application[application_id].append(
                {
                    "scores": _json_object(assessment["scores_json"]),
                    "evidence": _json_object(assessment["evidence_json"]),
                    "recommendation": str(assessment["recommendation"]),
                }
            )
        comparable = 0
        disagreements = 0
        for application_id, assessment_items in assessments_by_application.items():
            application_type, resolved = application_assessment_meta[application_id]
            summary = self._application_assessment_summary(
                application_type,
                assessment_items,
                calibration_resolved=resolved,
            )
            if summary["complete"]:
                comparable += 1
                disagreements += int(summary["disagreement"])
        reviewer_identities = await self._resolve_identities(
            {
                int(row["reviewed_by"])
                for row in reviews
                if row["reviewed_by"] is not None
            }
        )
        reviewer_items = []
        for row in reviews:
            item = _row_dict(row)
            reviewer_id = int(item["reviewed_by"])
            item["reviewed_by"] = str(reviewer_id)
            item["identity"] = reviewer_identities.get(reviewer_id)
            reviewer_items.append(item)
        return {
            "reviewers": reviewer_items,
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
            "pending_applications": int(application_timing["pending"] or 0),
            "application_process": {
                "submitted": int(application_timing["submitted"] or 0),
                "pending": int(application_timing["pending"] or 0),
                "average_first_review_seconds": round(
                    float(application_timing["average_first_review_seconds"] or 0)
                ),
                "average_decision_seconds": round(
                    float(application_timing["average_decision_seconds"] or 0)
                ),
                "interviewed": int(interview_count["c"] or 0),
                "interview_rate_percent": round(
                    (int(interview_count["c"] or 0) / int(application_timing["submitted"] or 1))
                    * 100,
                    1,
                ),
                "outcomes_by_type": [
                    _row_dict(row) for row in application_outcomes
                ],
                "reason_breakdown": {
                    str(row["decision_category"]): int(row["c"] or 0)
                    for row in reason_rows
                },
                "rubric_comparable": comparable,
                "rubric_disagreements": disagreements,
                "rubric_disagreement_percent": round(
                    (disagreements / comparable) * 100, 1
                )
                if comparable
                else 0,
                "probation": {
                    str(row["status"]): int(row["c"] or 0)
                    for row in probation_rows
                },
            },
            "scope": "team" if scope_all else "self",
        }

    async def qa_list(self, principal, query):
        rows = await self.db.fetchall("SELECT s.request_message_id,s.level_id,s.user_id,s.result,s.send_type,s.review_text,s.reviewed_by,s.reviewed_ts,q.qa_status,q.adjusted_send_type,q.reason FROM level_request_submissions s LEFT JOIN staff_review_qa q ON q.guild_id=s.guild_id AND q.request_message_id=s.request_message_id WHERE s.guild_id=? AND s.status='reviewed' ORDER BY s.reviewed_ts DESC LIMIT 100", (principal.guild_id,))
        identities = await self._resolve_identities(
            {
                int(row["reviewed_by"])
                for row in rows
                if row["reviewed_by"] is not None
            }
        )
        items = []
        for row in rows:
            item = _row_dict(row)
            if row["reviewed_by"] is not None:
                item["reviewer"] = identities.get(int(row["reviewed_by"]))
            items.append(item)
        return {"items": items}

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
        if path == "/api/apply/options" and method == "GET":
            return 200, await self.application_options(principal)
        if path == "/api/apply/form" and method == "GET":
            return 200, await self.application_form(principal)
        form_match = re.fullmatch(r"/api/apply/form/([a-z][a-z0-9_]{1,39})", path)
        if form_match and method == "GET":
            return 200, await self.application_form(principal, form_match.group(1))
        if path == "/api/apply/mine" and method == "GET":
            await self._reconcile_application_roles()
            rows = await self.db.fetchall(
                "SELECT id,application_type,status,answers_json,created_ts,updated_ts,"
                "submitted_ts,decided_ts,applicant_message FROM staff_applications "
                "WHERE guild_id=? AND applicant_id=? ORDER BY updated_ts DESC LIMIT 20",
                (principal.guild_id, principal.user_id),
            )
            return 200, {
                "items": [
                    {
                        **_row_dict(row),
                        "application_label": self._application_label(
                            str(row["application_type"])
                        ),
                        "answers": _json_object(row["answers_json"]),
                    }
                    for row in rows
                ]
            }
        if path == "/api/apply/mine" and method == "DELETE":
            return 200, await self.reset_own_application_data(principal, payload)
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

    async def reset_own_application_data(
        self,
        principal: StaffPrincipal,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if str(payload.get("confirmation") or "").strip() != "DELETE":
            raise PortalError(
                400,
                "confirmation_required",
                "Enter DELETE to remove your application data",
            )
        async with self._application_lock:
            rows = await self.db.fetchall(
                "SELECT id,application_type,status,submitted_ts,"
                "review_thread_outbox_id,review_thread_id,"
                "interview_ticket_outbox_id,interview_ticket_channel_id "
                "FROM staff_applications WHERE guild_id=? AND applicant_id=?",
                (principal.guild_id, principal.user_id),
            )
            idempotency_rows = await self.db.fetchall(
                "SELECT idempotency_key FROM staff_idempotency WHERE user_id=? "
                "AND operation LIKE '% /api/apply/%'",
                (principal.user_id,),
            )
            protected = {
                str(row["status"] or "")
                for row in rows
                if str(row["status"] or "")
                not in {"draft", "withdrawn", "rejected"}
            }
            if protected and not principal.can("developer.access"):
                raise PortalError(
                    409,
                    "application_reset_restricted",
                    "Withdraw the active application or ask a Dev to remove it",
                )
            application_ids = [int(row["id"]) for row in rows]
            outbox_ids = {
                int(value)
                for row in rows
                for value in (
                    row["review_thread_outbox_id"],
                    row["interview_ticket_outbox_id"],
                )
                if value is not None
            }
            external_records = sum(
                1
                for row in rows
                if row["review_thread_id"] is not None
                or row["interview_ticket_channel_id"] is not None
            )
            statements: list[tuple[str, tuple[Any, ...]]] = []
            now = int(time.time())
            regular_cooldown_seconds = self._application_cooldown_days() * 86400
            reset_cooldown_types = {
                str(row["application_type"])
                for row in rows
                if row["submitted_ts"] is not None
                and int(row["submitted_ts"]) + regular_cooldown_seconds > now
            }
            reset_cooldown_until = now + 86400
            for outbox_id in sorted(outbox_ids):
                statements.append(
                    (
                        (
                            "UPDATE discord_outbox SET status='dead',updated_ts=?,"
                            "last_error='application data removed by applicant' "
                            "WHERE id=? AND status IN('pending','failed','processing')"
                        ),
                        (now, outbox_id),
                    )
                )
            for application_id in application_ids:
                statements.extend(
                    (
                        (
                            "DELETE FROM staff_application_notes WHERE application_id=?",
                            (application_id,),
                        ),
                        (
                            "DELETE FROM staff_application_events WHERE application_id=?",
                            (application_id,),
                        ),
                        (
                            "DELETE FROM staff_application_assessments WHERE application_id=?",
                            (application_id,),
                        ),
                        (
                            "DELETE FROM staff_application_interviews WHERE application_id=?",
                            (application_id,),
                        ),
                        (
                            "DELETE FROM staff_application_probations WHERE application_id=?",
                            (application_id,),
                        ),
                        (
                            "DELETE FROM staff_applications WHERE id=?",
                            (application_id,),
                        ),
                    )
                )
            for idempotency_row in idempotency_rows:
                statements.append(
                    (
                        "DELETE FROM staff_idempotency WHERE idempotency_key=?",
                        (str(idempotency_row["idempotency_key"]),),
                    )
                )
            for application_type in sorted(reset_cooldown_types):
                statements.append(
                    (
                        (
                            "INSERT INTO staff_application_cooldowns("
                            "guild_id,applicant_id,application_type,cooldown_until_ts,"
                            "source,created_ts,updated_ts) VALUES(?,?,?,?,'application_data_reset',?,?) "
                            "ON CONFLICT(guild_id,applicant_id,application_type) DO UPDATE SET "
                            "cooldown_until_ts=MAX(cooldown_until_ts,excluded.cooldown_until_ts),"
                            "source=excluded.source,updated_ts=excluded.updated_ts"
                        ),
                        (
                            principal.guild_id,
                            principal.user_id,
                            application_type,
                            reset_cooldown_until,
                            now,
                            now,
                        ),
                    )
                )
            statements.append(
                (
                    (
                        "INSERT INTO workflow_events(correlation_id,workflow_type,entity_id,event,"
                        "guild_id,actor_id,payload_json,created_ts) VALUES(?,'staff_portal',"
                        "?,'application_data_reset',?,?,?,?)"
                    ),
                    (
                        new_correlation_id("application-reset"),
                        f"applicant:{principal.user_id}",
                        principal.guild_id,
                        principal.user_id,
                        json.dumps(
                            {
                                "applications_removed": len(application_ids),
                                "external_records_preserved": external_records,
                                "cooldown_types_preserved": sorted(reset_cooldown_types),
                            },
                            separators=(",", ":"),
                        ),
                        now,
                    ),
                )
            )
            await self.db.execute_transaction(statements)
        return {
            "ok": True,
            "applications_removed": len(application_ids),
            "external_records_preserved": external_records,
            "cooldown_types_preserved": sorted(reset_cooldown_types),
        }

    def _configured_application_types(self) -> list[str]:
        configured = self.bot.config.get(
            "staff_portal", "application_types", default=["judge"]
        )
        if not isinstance(configured, list):
            return ["judge"]
        return list(
            dict.fromkeys(
                str(value).strip().casefold()
                for value in configured
                if str(value).strip()
            )
        )

    def _application_form_config(self, application_type: str) -> dict[str, Any]:
        application_type = str(application_type or "judge").strip().casefold()
        forms = self.bot.config.get("staff_portal", "application_forms", default={})
        if isinstance(forms, dict) and isinstance(forms.get(application_type), dict):
            return dict(forms[application_type])
        if application_type == "judge":
            return {
                "label": "Reviewer application",
                "description": "Apply to join the GD Avenue review team.",
                "questions": self.bot.config.get(
                    "staff_portal", "application_questions", default=[]
                ),
            }
        return {}

    def _application_form_version(self) -> str:
        value = str(
            self.bot.config.get(
                "staff_portal",
                "application_form_version",
                default="applications-v2",
            )
            or "applications-v2"
        ).strip()
        return value[:80]

    def _application_rubric(self, application_type: str) -> dict[str, Any]:
        rubrics = self.bot.config.get(
            "staff_portal", "application_rubrics", default={}
        )
        raw = rubrics.get(application_type, {}) if isinstance(rubrics, dict) else {}
        dimensions = []
        for item in raw.get("dimensions", []) if isinstance(raw, dict) else []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()[:80]
            label = str(item.get("label") or "").strip()[:160]
            if key and label:
                dimensions.append({"key": key, "label": label})
        return {
            "version": str(raw.get("version") or f"{application_type}-rubric-v1")[:80]
            if isinstance(raw, dict)
            else f"{application_type}-rubric-v1",
            "dimensions": dimensions,
            "scale": {"min": 1, "max": 5},
            "minimum_assessments": max(
                1,
                min(
                    5,
                    self.bot.config.get_int(
                        "staff_portal",
                        "application_minimum_assessments",
                        default=2,
                    ),
                ),
            ),
        }

    def _application_decision_categories(self, action: str) -> list[str]:
        configured = self.bot.config.get(
            "staff_portal", "application_decision_categories", default={}
        )
        values = configured.get(action, []) if isinstance(configured, dict) else []
        return [
            str(value).strip().casefold()[:80]
            for value in values
            if str(value).strip()
        ]

    def _application_probation_days(self) -> int:
        return max(
            1,
            min(
                365,
                self.bot.config.get_int(
                    "staff_portal", "application_probation_days", default=30
                ),
            ),
        )

    @staticmethod
    def _applicant_application_payload(row: Any) -> dict[str, Any]:
        data = _row_dict(row)
        return {
            key: data.get(key)
            for key in (
                "id",
                "application_type",
                "status",
                "created_ts",
                "updated_ts",
                "submitted_ts",
                "decided_ts",
                "applicant_message",
            )
        } | {"answers": _json_object(data.get("answers_json"))}

    def _application_label(self, application_type: str) -> str:
        configured = self._application_form_config(application_type)
        label = str(configured.get("label") or "").strip()
        if label:
            return label
        return f"{str(application_type or 'staff').replace('_', ' ').title()} application"

    @staticmethod
    def _application_type_is_open(
        configuration: dict[str, Any], application_type: str
    ) -> bool:
        if not bool(configuration.get("applications_open", True)):
            return False
        per_type = configuration.get("application_open_by_type")
        if not isinstance(per_type, dict):
            return True
        return bool(per_type.get(application_type, True))

    def _application_role_ids(self, application_type: str) -> list[int]:
        key = "judge_role_ids" if application_type == "judge" else f"{application_type}_role_ids"
        return self.bot.config.get_int_list("staff_portal", key)

    @staticmethod
    def _can_review_application_type(principal: StaffPrincipal, application_type: str) -> bool:
        if principal.can("applications.review_all"):
            return True
        if application_type == "judge":
            return principal.can("applications.review_judge")
        if application_type == "mod":
            return principal.can("applications.review_standard")
        return False

    @staticmethod
    def _application_actions_for_status(
        status: str, *, interview_delivery_pending: bool = False
    ) -> list[str]:
        """Return the server-authoritative actions for an application state."""
        actions = {
            "submitted": ["claim", "assess", "interview", "hold", "accept", "reject", "message"],
            "under_review": ["assess", "interview", "hold", "accept", "reject", "message"],
            "hold": ["claim", "assess", "interview", "accept", "reject", "message"],
            "interview": ["assess", "interview", "hold", "accept", "reject", "message"],
            "accepted_pending_role": ["accept", "message"],
            "accepted": ["message"],
            "rejected": ["message"],
            "withdrawn": ["message"],
        }
        available = list(actions.get(str(status or "").strip().casefold(), ()))
        if interview_delivery_pending and "interview" in available:
            available.remove("interview")
        return available

    def _application_cooldown_days(self) -> int:
        return max(
            1,
            min(
                365,
                self.bot.config.get_int(
                    "staff_portal", "application_cooldown_days", default=5
                ),
            ),
        )

    async def _application_cooldown(
        self, principal: StaffPrincipal, application_type: str
    ) -> dict[str, Any]:
        latest = await self.db.fetchone(
            "SELECT id,application_type,status,submitted_ts FROM staff_applications "
            "WHERE guild_id=? AND applicant_id=? AND application_type=? "
            "AND submitted_ts IS NOT NULL "
            "ORDER BY submitted_ts DESC,id DESC LIMIT 1",
            (principal.guild_id, principal.user_id, application_type),
        )
        reset = await self.db.fetchone(
            "SELECT cooldown_until_ts FROM staff_application_cooldowns "
            "WHERE guild_id=? AND applicant_id=? AND application_type=?",
            (principal.guild_id, principal.user_id, application_type),
        )
        days = self._application_cooldown_days()
        now = int(time.time())
        submitted_ts = int(latest["submitted_ts"] or 0) if latest else 0
        submission_until_ts = submitted_ts + days * 86400 if submitted_ts else 0
        reset_until_ts = int(reset["cooldown_until_ts"] or 0) if reset else 0
        until_ts = max(submission_until_ts, reset_until_ts)
        return {
            "days": days,
            "active": bool(until_ts > now),
            "until_ts": until_ts or None,
            "remaining_seconds": max(0, until_ts - now),
            "source_application_id": int(latest["id"]) if latest else None,
            "source": "application_data_reset"
            if reset_until_ts >= submission_until_ts and reset_until_ts > now
            else "submission"
            if submission_until_ts > now
            else None,
        }

    async def application_options(self, principal: StaffPrincipal) -> dict[str, Any]:
        configured_types = self._configured_application_types()
        configuration = (await self.safe_configuration())["configuration"]
        forms = []
        cooldowns: dict[str, dict[str, Any]] = {}
        for application_type in configured_types:
            config = self._application_form_config(application_type)
            if not config:
                continue
            cooldown = await self._application_cooldown(principal, application_type)
            cooldowns[application_type] = cooldown
            forms.append(
                {
                    "application_type": application_type,
                    "label": self._application_label(application_type),
                    "description": str(config.get("description") or "").strip()[:500],
                    "enabled": True,
                    "open": self._application_type_is_open(
                        configuration, application_type
                    ),
                    "cooldown": cooldown,
                }
            )
        if "appeal" not in configured_types:
            forms.append(
                {
                    "application_type": "appeal",
                    "label": "Appeal application",
                    "description": "This application will be added in a future update.",
                    "enabled": False,
                    "open": False,
                }
            )
        active_rows = await self.db.fetchall(
            "SELECT id,application_type,status FROM staff_applications "
            "WHERE guild_id=? AND applicant_id=? AND status IN"
            "('draft','submitted','under_review','interview','hold','accepted_pending_role') "
            "ORDER BY updated_ts DESC,id DESC",
            (principal.guild_id, principal.user_id),
        )
        active_applications = [_row_dict(row) for row in active_rows]
        return {
            "items": forms,
            "applications_open": bool(configuration["applications_open"]),
            "application_open_by_type": dict(
                configuration["application_open_by_type"]
            ),
            "cooldown": {"days": self._application_cooldown_days(), "active": False},
            "cooldowns": cooldowns,
            "active_applications": active_applications,
            "active_application": active_applications[0] if active_applications else None,
        }

    def _application_questions(
        self,
        review_prompt: dict[str, Any] | None = None,
        application_type: str = "judge",
    ) -> list[dict[str, Any]]:
        form_config = self._application_form_config(application_type)
        raw_questions = form_config.get("questions", [])
        questions: list[dict[str, Any]] = []
        for raw in raw_questions if isinstance(raw_questions, list) else []:
            if not isinstance(raw, dict):
                continue
            key = str(raw.get("key") or "").strip()[:80]
            label = str(raw.get("label") or "").strip()[:500]
            kind = str(raw.get("type") or "long_text").strip().casefold()
            if not key or not label or kind not in {"short_text", "long_text", "single_choice"}:
                continue
            options = [str(item).strip()[:120] for item in raw.get("options", []) if str(item).strip()]
            question = {
                "key": key,
                "label": label,
                "type": kind,
                "required": bool(raw.get("required")),
                "options": options if kind == "single_choice" else [],
                "help_url": str(raw.get("help_url") or "").strip()[:500],
                "uses_review_prompt": bool(raw.get("uses_review_prompt")),
                "section": str(raw.get("section") or "Application").strip()[:100],
                "guidance": str(raw.get("guidance") or "").strip()[:500],
                "recommended_words": int(raw.get("recommended_words") or 0),
            }
            if raw.get("uses_review_prompt"):
                question["review_prompt"] = review_prompt or None
            questions.append(question)
        return questions

    def _application_review_levels(self) -> list[dict[str, Any]]:
        raw_levels = self.bot.config.get("staff_portal", "application_review_levels", default=[])
        levels: list[dict[str, Any]] = []
        for raw in raw_levels if isinstance(raw_levels, list) else []:
            if not isinstance(raw, dict):
                continue
            key = str(raw.get("key") or "").strip()[:100]
            level_id = str(raw.get("level_id") or "").strip()
            url = str(raw.get("youtube_url") or "").strip()[:500]
            embed_url = _youtube_embed_url(url)
            try:
                weight = max(1, min(1000, int(raw.get("weight") or 1)))
            except (TypeError, ValueError):
                weight = 1
            if (
                not key
                or not re.fullmatch(r"\d{7,10}", level_id)
                or not embed_url
            ):
                continue
            levels.append(
                {
                    "key": key,
                    "name": str(raw.get("name") or f"Level {level_id}").strip()[:160],
                    "level_id": level_id,
                    "youtube_url": url,
                    "youtube_embed_url": embed_url,
                    "weight": weight,
                }
            )
        return levels

    def _choose_application_review_prompt(self) -> dict[str, Any] | None:
        levels = self._application_review_levels()
        if not levels:
            return None
        total = sum(level["weight"] for level in levels)
        pick = secrets.randbelow(total)
        for level in levels:
            if pick < level["weight"]:
                return level
            pick -= level["weight"]
        return levels[-1]

    async def application_form(
        self, principal: StaffPrincipal, application_type: str = "judge"
    ) -> dict[str, Any]:
        application_type = str(application_type or "judge").strip().casefold()
        if (
            application_type not in self._configured_application_types()
            or not self._application_form_config(application_type)
        ):
            raise PortalError(404, "application_closed", "That application is not available")
        async with self._application_lock:
            row = await self.db.fetchone(
                "SELECT * FROM staff_applications WHERE guild_id=? AND applicant_id=? AND application_type=? "
                "AND status IN('draft','submitted','under_review','interview','hold','accepted_pending_role') "
                "ORDER BY id DESC LIMIT 1",
                (principal.guild_id, principal.user_id, application_type),
            )
            if row is None:
                configuration = (await self.safe_configuration())["configuration"]
                if not self._application_type_is_open(
                    configuration, application_type
                ):
                    raise PortalError(
                        409,
                        "applications_closed",
                        f"{self._application_label(application_type)} submissions are currently closed",
                    )
                cooldown = await self._application_cooldown(principal, application_type)
                if cooldown["active"]:
                    raise PortalError(
                        429,
                        "application_cooldown",
                        f"That {self._application_label(application_type)} is still on cooldown. "
                        "Check the application chooser for its availability time.",
                    )
                uses_review_prompt = any(
                    bool(question.get("uses_review_prompt"))
                    for question in self._application_questions(
                        application_type=application_type
                    )
                )
                prompt = self._choose_application_review_prompt() if uses_review_prompt else None
                now = int(time.time())
                application_id = await self.db.execute_insert(
                    "INSERT INTO staff_applications(guild_id,applicant_id,application_type,status,answers_json,form_version,created_ts,updated_ts,review_prompt_key) "
                    "VALUES(?,?,?,'draft','{}',?,?,?,?)",
                    (
                        principal.guild_id,
                        principal.user_id,
                        application_type,
                        self._application_form_version(),
                        now,
                        now,
                        prompt["key"] if prompt else None,
                    ),
                )
                row = await self.db.fetchone("SELECT * FROM staff_applications WHERE id=?", (application_id,))
            prompt_key = str(row["review_prompt_key"] or "")
            prompt = next((item for item in self._application_review_levels() if item["key"] == prompt_key), None)
            if not prompt and any(
                question.get("uses_review_prompt")
                for question in self._application_questions(
                    application_type=application_type
                )
            ):
                prompt = self._choose_application_review_prompt()
                if prompt and str(row["status"]) == "draft":
                    await self.db.execute(
                        "UPDATE staff_applications SET review_prompt_key=?,updated_ts=? WHERE id=? AND status='draft'",
                        (prompt["key"], int(time.time()), int(row["id"])),
                    )
                    row = await self.db.fetchone(
                        "SELECT * FROM staff_applications WHERE id=?", (int(row["id"]),)
                    )
            return {
                "application": {
                    **self._applicant_application_payload(row),
                },
                "form": {
                    "application_type": application_type,
                    "label": self._application_label(application_type),
                    "description": str(
                        self._application_form_config(application_type).get("description")
                        or ""
                    )[:500],
                    "version": str(row["form_version"] or self._application_form_version()),
                    "estimated_minutes": max(
                        1,
                        min(
                            120,
                            int(
                                self._application_form_config(application_type).get(
                                    "estimated_minutes", 10
                                )
                                or 10
                            ),
                        ),
                    ),
                    "sections": [
                        str(item)[:100]
                        for item in self._application_form_config(application_type).get(
                            "sections", []
                        )
                        if str(item).strip()
                    ],
                },
                "questions": self._application_questions(prompt, application_type),
            }

    def _validate_application_answers(
        self,
        answers: dict[str, Any],
        prompt: dict[str, Any] | None,
        application_type: str,
        *,
        submit: bool,
    ) -> dict[str, str]:
        questions = self._application_questions(prompt, application_type)
        if not questions:
            raise PortalError(503, "application_form_missing", "The application form is not configured")
        safe: dict[str, str] = {}
        for question in questions:
            key = question["key"]
            value = _bounded_text(answers.get(key), 4000)
            if question["type"] == "single_choice" and value and value not in question["options"]:
                raise PortalError(400, "invalid_application_choice", f"Choose a valid answer for {question['label']}")
            if submit and question["required"] and not value:
                raise PortalError(400, "incomplete_application", f"Complete: {question['label']}")
            safe[key] = value
        if any(
            question["uses_review_prompt"]
            and question.get("review_prompt") is None
            for question in questions
        ):
            raise PortalError(503, "review_prompt_missing", "No application review level is configured")
        return safe

    def _application_review_url(self) -> str:
        origins = self.bot.config.get(
            "staff_portal", "allowed_origins", default=[]
        )
        if isinstance(origins, list):
            origin = next(
                (
                    str(item).strip().rstrip("/")
                    for item in origins
                    if str(item).strip().startswith("https://")
                ),
                "",
            )
            if origin:
                return f"{origin}/staff/#team/applications"
        return "https://gdavenue.netlify.app/staff/#team/applications"

    def _application_thread_payload(self, row) -> dict[str, Any]:
        application_type = str(row["application_type"] or "judge")
        prompt_key = str(row["review_prompt_key"] or "")
        prompt = next(
            (
                item
                for item in self._application_review_levels()
                if item["key"] == prompt_key
            ),
            None,
        )
        answers = _json_object(
            row["submitted_answers_json"] or row["answers_json"]
        )
        submitted_questions = _json_list(row["submitted_questions_json"])
        questions = (
            submitted_questions
            if submitted_questions
            else self._application_questions(prompt, application_type)
        )
        responses = []
        for question in questions:
            if not isinstance(question, dict):
                continue
            label = str(question.get("label") or "Question")
            if question.get("uses_review_prompt") and prompt:
                label = f"{label} - {prompt['youtube_url']}"
            responses.append(
                {
                    "section": str(question.get("section") or "Application"),
                    "question": label,
                    "answer": answers.get(str(question.get("key") or ""), ""),
                }
            )
        return {
            "application_id": int(row["id"]),
            "application_type": application_type,
            "application_label": self._application_label(application_type),
            "submitted_ts": int(row["submitted_ts"] or row["updated_ts"] or 0),
            "responses": responses,
            "review_prompt": prompt,
            "review_url": self._application_review_url(),
        }

    async def _ensure_application_thread_outbox(self, application_id: int) -> int:
        row = await self.db.fetchone("SELECT * FROM staff_applications WHERE id=?", (application_id,))
        if row is None or str(row["status"]) == "draft":
            return 0
        channel_id = self.bot.config.get_int("staff_portal", "application_review_channel_id", default=0)
        if not channel_id:
            raise PortalError(503, "application_channel_missing", "The application review channel is not configured")
        payload = self._application_thread_payload(row)
        linked_outbox_id = int(row["review_thread_outbox_id"] or 0)
        if linked_outbox_id:
            linked = await self.db.fetchone(
                "SELECT id,status FROM discord_outbox WHERE id=?",
                (linked_outbox_id,),
            )
            if linked is not None and str(linked["status"]) != "dead":
                return linked_outbox_id
            if linked is not None:
                now = int(time.time())
                revived = await self.db.execute_affected(
                    "UPDATE discord_outbox SET channel_id=?,user_id=?,payload_json=?,"
                    "status='pending',attempts=0,next_attempt_ts=?,updated_ts=?,"
                    "delivered_ts=NULL,delivered_message_id=NULL,last_error=NULL "
                    "WHERE id=? AND status='dead' AND action_type='create_application_thread'",
                    (
                        channel_id,
                        int(row["applicant_id"]),
                        json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
                        now,
                        now,
                        linked_outbox_id,
                    ),
                )
                if revived:
                    await record_workflow_event(
                        self.db,
                        workflow_type="discord_outbox",
                        entity_id=str(linked_outbox_id),
                        event="requeued",
                        correlation_id=f"staff-application:{application_id}",
                        guild_id=int(row["guild_id"]),
                        payload={
                            "action": "create_application_thread",
                            "reason": "application review delivery recovery",
                        },
                    )
                    return linked_outbox_id
        outbox_id = await self.bot.outbox.enqueue(
            "create_application_thread",
            guild_id=int(row["guild_id"]),
            channel_id=channel_id,
            user_id=int(row["applicant_id"]),
            payload=payload,
            correlation_id=f"staff-application:{application_id}",
            idempotency_key=f"staff-application:{application_id}:review-thread",
        )
        await self.db.execute(
            "UPDATE staff_applications SET review_thread_outbox_id=?,updated_ts=? WHERE id=?",
            (outbox_id, int(time.time()), application_id),
        )
        return outbox_id

    async def reconcile_application_deliveries(self, *, limit: int = 50) -> int:
        """Restore missing/dead application notifications after an interruption."""
        rows = await self.db.fetchall(
            "SELECT a.id FROM staff_applications a "
            "LEFT JOIN discord_outbox o ON o.id=a.review_thread_outbox_id "
            "WHERE a.status NOT IN('draft','withdrawn') AND "
            "(a.review_thread_outbox_id IS NULL OR o.id IS NULL OR o.status='dead') "
            "ORDER BY COALESCE(a.submitted_ts,a.created_ts),a.id LIMIT ?",
            (max(1, min(200, int(limit))),),
        )
        repaired = 0
        for pending in rows:
            application_id = int(pending["id"])
            try:
                if await self._ensure_application_thread_outbox(application_id):
                    repaired += 1
            except Exception as exc:  # noqa: BLE001 - another startup may retry it.
                await log_error(
                    self.bot,
                    "Staff application delivery reconciliation deferred "
                    f"application_id={application_id}: {exc!r}",
                )
        return repaired

    async def save_application(self, principal, payload, *, submit):
        configuration = (await self.safe_configuration())["configuration"]
        app_type = str(payload.get("application_type") or "judge").casefold()
        if (
            app_type not in self._configured_application_types()
            or not self._application_form_config(app_type)
        ):
            raise PortalError(400, "application_closed", "That application is not available")
        if submit and not self._application_type_is_open(configuration, app_type):
            raise PortalError(
                409,
                "applications_closed",
                f"{self._application_label(app_type)} submissions are currently closed",
            )
        answers = payload.get("answers")
        if not isinstance(answers, dict):
            raise PortalError(400, "invalid_answers", "Application answers are missing")
        now = int(time.time())
        async with self._application_lock:
            row = await self.db.fetchone("SELECT * FROM staff_applications WHERE guild_id=? AND applicant_id=? AND application_type=? AND status='draft' ORDER BY id DESC LIMIT 1", (principal.guild_id, principal.user_id, app_type))
            active = None
            if row is None:
                active = await self.db.fetchone(
                    "SELECT * FROM staff_applications WHERE guild_id=? AND applicant_id=? "
                    "AND application_type=? "
                    "AND status IN('submitted','under_review','interview','hold','accepted_pending_role') "
                    "ORDER BY id DESC LIMIT 1",
                    (principal.guild_id, principal.user_id, app_type),
                )
            if active is not None:
                if submit:
                    application_id = int(active["id"])
                    result = {
                        "application": self._applicant_application_payload(active)
                    }
                    active_retry = True
                else:
                    raise PortalError(409, "application_active", "You already have an active application")
            else:
                active_retry = False
            if active_retry:
                pass
            else:
                cooldown = await self._application_cooldown(principal, app_type)
                if cooldown["active"]:
                    raise PortalError(
                        429,
                        "application_cooldown",
                        f"That {self._application_label(app_type)} is still on cooldown. "
                        "Check the application chooser for its availability time.",
                    )
                prompt_key = str(row["review_prompt_key"] or "") if row else ""
                prompt = next((item for item in self._application_review_levels() if item["key"] == prompt_key), None)
                if row is None:
                    uses_review_prompt = any(
                        question.get("uses_review_prompt")
                        for question in self._application_questions(
                            application_type=app_type
                        )
                    )
                    prompt = self._choose_application_review_prompt() if uses_review_prompt else None
                    prompt_key = prompt["key"] if prompt else ""
                questions = self._application_questions(prompt, app_type)
                safe_answers = self._validate_application_answers(
                    answers, prompt, app_type, submit=submit
                )
                target_status = "submitted" if submit else "draft"
                encoded_answers = json.dumps(
                    safe_answers, separators=(",", ":"), ensure_ascii=False
                )
                encoded_questions = json.dumps(
                    questions, separators=(",", ":"), ensure_ascii=False
                )
                form_version = self._application_form_version()
                if row:
                    application_id = int(row["id"])
                    await self.db.execute(
                        "UPDATE staff_applications SET answers_json=?,status=?,"
                        "form_version=?,updated_ts=?,submitted_ts=?,"
                        "submitted_answers_json=CASE WHEN ? THEN ? ELSE submitted_answers_json END,"
                        "submitted_questions_json=CASE WHEN ? THEN ? ELSE submitted_questions_json END,"
                        "review_prompt_key=COALESCE(review_prompt_key,?) "
                        "WHERE id=? AND status='draft'",
                        (
                            encoded_answers,
                            target_status,
                            form_version,
                            now,
                            now if submit else None,
                            1 if submit else 0,
                            encoded_answers,
                            1 if submit else 0,
                            encoded_questions,
                            prompt_key or None,
                            application_id,
                        ),
                    )
                else:
                    application_id = await self.db.execute_insert(
                        "INSERT INTO staff_applications("
                        "guild_id,applicant_id,application_type,status,answers_json,"
                        "form_version,submitted_answers_json,submitted_questions_json,"
                        "created_ts,updated_ts,submitted_ts,review_prompt_key"
                        ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            principal.guild_id,
                            principal.user_id,
                            app_type,
                            target_status,
                            encoded_answers,
                            form_version,
                            encoded_answers if submit else None,
                            encoded_questions if submit else None,
                            now,
                            now,
                            now if submit else None,
                            prompt_key or None,
                        ),
                    )
                if submit:
                    await self._application_event(application_id, principal.user_id, "submitted", "draft", "submitted", {})
                result = {
                    "application": self._applicant_application_payload(
                        await self.db.fetchone(
                            "SELECT * FROM staff_applications WHERE id=?",
                            (application_id,),
                        )
                    )
                }
        if submit:
            await self._ensure_application_thread_outbox(application_id)
            result["application"] = self._applicant_application_payload(
                await self.db.fetchone(
                    "SELECT * FROM staff_applications WHERE id=?", (application_id,)
                )
            )
        return result

    async def _application_event(self, application_id, actor_id, event, old, new, detail):
        now = int(time.time())
        await self.db.execute("INSERT INTO staff_application_events(application_id,actor_id,event,from_status,to_status,detail_json,created_ts,correlation_id) VALUES(?,?,?,?,?,?,?,?)", (application_id, actor_id, event, old, new, json.dumps(detail, separators=(",", ":")), now, new_correlation_id("application")))

    async def _reconcile_application_roles(self):
        pending = await self.db.fetchall(
            "SELECT id,guild_id,applicant_id,application_type,role_outbox_id FROM staff_applications "
            "WHERE status='accepted_pending_role' LIMIT 50"
        )
        for row in pending:
            application_type = str(row["application_type"] or "judge")
            role_ids = self._application_role_ids(application_type)
            if row["role_outbox_id"] is not None:
                continue
            if not role_ids:
                if application_type != "judge":
                    await self.db.execute(
                        "UPDATE staff_applications SET status='accepted',updated_ts=? "
                        "WHERE id=? AND status='accepted_pending_role'",
                        (int(time.time()), int(row["id"])),
                    )
                continue
            try:
                outbox_id = await self.bot.outbox.enqueue(
                    "add_role",
                    guild_id=int(row["guild_id"]),
                    user_id=int(row["applicant_id"]),
                    payload={
                        "role_id": role_ids[0],
                        "reason": f"{self._application_label(application_type)} #{int(row['id'])} accepted",
                    },
                    correlation_id=f"staff-application:{int(row['id'])}",
                    idempotency_key=f"staff-application:{int(row['id'])}:{application_type}-role",
                )
                await self.db.execute(
                    "UPDATE staff_applications SET role_outbox_id=?,updated_ts=? "
                    "WHERE id=? AND status='accepted_pending_role' AND role_outbox_id IS NULL",
                    (outbox_id, int(time.time()), int(row["id"])),
                )
            except Exception as exc:  # noqa: BLE001 - keep the durable pending state retryable.
                await log_error(
                    self.bot,
                    f"Staff application role outbox reconciliation deferred application_id={int(row['id'])}: {exc!r}",
                )
        rows = await self.db.fetchall("SELECT a.id,a.role_outbox_id,o.status FROM staff_applications a JOIN discord_outbox o ON o.id=a.role_outbox_id WHERE a.status='accepted_pending_role' LIMIT 50")
        for row in rows:
            if str(row["status"]) == "delivered":
                await self.db.execute("UPDATE staff_applications SET status='accepted',updated_ts=? WHERE id=? AND status='accepted_pending_role'", (int(time.time()), int(row["id"])))

    def _application_assessment_summary(
        self,
        application_type: str,
        assessments: list[dict[str, Any]],
        *,
        calibration_resolved: bool,
    ) -> dict[str, Any]:
        rubric = self._application_rubric(application_type)
        minimum = int(rubric["minimum_assessments"])
        threshold = max(
            1,
            min(
                4,
                self.bot.config.get_int(
                    "staff_portal",
                    "application_disagreement_threshold",
                    default=2,
                ),
            ),
        )
        spreads: dict[str, int] = {}
        averages: dict[str, float] = {}
        for dimension in rubric["dimensions"]:
            key = dimension["key"]
            values = [
                int(item.get("scores", {}).get(key))
                for item in assessments
                if str(item.get("scores", {}).get(key, "")).isdigit()
            ]
            if values:
                spreads[key] = max(values) - min(values)
                averages[key] = round(sum(values) / len(values), 2)
        recommendations = {
            str(item.get("recommendation") or "")
            for item in assessments
            if str(item.get("recommendation") or "")
        }
        enough = len(assessments) >= minimum
        disagreement = any(value >= threshold for value in spreads.values()) or len(
            recommendations
        ) > 1
        calibration_required = enough and disagreement and not calibration_resolved
        return {
            "count": len(assessments),
            "minimum": minimum,
            "complete": enough,
            "disagreement": disagreement,
            "calibration_required": calibration_required,
            "decision_ready": enough and not calibration_required,
            "dimension_averages": averages,
            "dimension_spreads": spreads,
            "recommendations": sorted(recommendations),
        }

    @staticmethod
    def _submitted_application_questions(row: Any) -> list[dict[str, Any]]:
        return [
            item
            for item in _json_list(row["submitted_questions_json"])
            if isinstance(item, dict)
        ]

    async def applications(self, principal, query):
        await self._reconcile_application_roles()
        configured_types = self._configured_application_types()
        allowed_types = [
            application_type
            for application_type in configured_types
            if self._can_review_application_type(principal, application_type)
        ]
        review_all = principal.can("applications.review_all")
        visible_types = configured_types if review_all else allowed_types
        if not allowed_types and not review_all:
            raise PortalError(403, "application_scope_denied", "You cannot review staff applications")

        requested_type = str(query.get("type") or "all").strip().casefold()
        if requested_type != "all" and not self._can_review_application_type(
            principal, requested_type
        ):
            raise PortalError(403, "application_scope_denied", "You cannot review that application type")
        status_filter = str(query.get("status") or "all").strip().casefold()
        valid_statuses = {
            "submitted",
            "under_review",
            "interview",
            "hold",
            "accepted_pending_role",
            "accepted",
            "rejected",
            "withdrawn",
        }
        if status_filter not in {"all", "active", *valid_statuses}:
            raise PortalError(400, "invalid_application_filter", "Choose a valid application status")
        claim_filter = str(query.get("claim") or "all").strip().casefold()
        if claim_filter not in {"all", "claimed", "unclaimed", "mine"}:
            raise PortalError(400, "invalid_application_filter", "Choose a valid claim filter")

        conditions = ["guild_id=?", "status!='draft'"]
        params: list[Any] = [principal.guild_id]
        if requested_type != "all":
            conditions.append("application_type=?")
            params.append(requested_type)
        elif not review_all:
            placeholders = ",".join("?" for _ in allowed_types)
            conditions.append(f"application_type IN ({placeholders})")
            params.extend(allowed_types)
        if status_filter == "active":
            conditions.append("status IN('submitted','under_review','interview','hold','accepted_pending_role')")
        elif status_filter != "all":
            conditions.append("status=?")
            params.append(status_filter)
        if claim_filter == "claimed":
            conditions.append("claimed_by IS NOT NULL")
        elif claim_filter == "unclaimed":
            conditions.append("claimed_by IS NULL")
        elif claim_filter == "mine":
            conditions.append("claimed_by=?")
            params.append(principal.user_id)
        rows = await self.db.fetchall(
            "SELECT staff_applications.*,"
            "(SELECT status FROM discord_outbox WHERE id=staff_applications.interview_ticket_outbox_id) "
            "AS interview_delivery_status FROM staff_applications WHERE "
            + " AND ".join(conditions)  # nosec B608
            + " "
            "ORDER BY CASE status WHEN 'submitted' THEN 0 WHEN 'under_review' THEN 1 "
            "WHEN 'interview' THEN 2 WHEN 'hold' THEN 3 ELSE 4 END,"
            "COALESCE(submitted_ts,created_ts) LIMIT 200",
            params,
        )
        if not rows:
            return {"items": [], "application_types": visible_types}
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
        assessments = await self.db.fetchall(
            f"SELECT * FROM staff_application_assessments WHERE application_id IN ({placeholders}) "  # nosec B608
            "ORDER BY created_ts,id",
            application_ids,
        )
        interviews = await self.db.fetchall(
            f"SELECT * FROM staff_application_interviews WHERE application_id IN ({placeholders}) "  # nosec B608
            "ORDER BY created_ts,id",
            application_ids,
        )
        probations = await self.db.fetchall(
            f"SELECT * FROM staff_application_probations WHERE application_id IN ({placeholders})",  # nosec B608
            application_ids,
        )
        notes_by_application: dict[int, list[dict[str, Any]]] = defaultdict(list)
        events_by_application: dict[int, list[dict[str, Any]]] = defaultdict(list)
        assessments_by_application: dict[int, list[dict[str, Any]]] = defaultdict(list)
        interviews_by_application: dict[int, list[dict[str, Any]]] = defaultdict(list)
        probation_by_application: dict[int, dict[str, Any]] = {}
        identity_ids = {
            int(row["applicant_id"])
            for row in rows
            if row["applicant_id"] is not None
        }
        for row in rows:
            if row["claimed_by"] is not None:
                identity_ids.add(int(row["claimed_by"]))
            if row["decided_by"] is not None:
                identity_ids.add(int(row["decided_by"]))
        for note in notes:
            identity_ids.add(int(note["author_id"]))
            notes_by_application[int(note["application_id"])].append(_row_dict(note))
        for event in events:
            identity_ids.add(int(event["actor_id"]))
            event_payload = _row_dict(event)
            event_payload["detail"] = _json_object(event_payload.pop("detail_json", "{}"))
            events_by_application[int(event["application_id"])].append(event_payload)
        for assessment in assessments:
            identity_ids.add(int(assessment["reviewer_id"]))
            assessment_payload = _row_dict(assessment)
            assessment_payload["scores"] = _json_object(
                assessment_payload.pop("scores_json", "{}")
            )
            assessment_payload["evidence"] = _json_object(
                assessment_payload.pop("evidence_json", "{}")
            )
            assessments_by_application[int(assessment["application_id"])].append(
                assessment_payload
            )
        for interview in interviews:
            identity_ids.add(int(interview["requested_by"]))
            if interview["completed_by"] is not None:
                identity_ids.add(int(interview["completed_by"]))
            interview_payload = _row_dict(interview)
            interview_payload["questions"] = _json_list(
                interview_payload.pop("questions_json", "[]")
            )
            interviews_by_application[int(interview["application_id"])].append(
                interview_payload
            )
        for probation in probations:
            if probation["completed_by"] is not None:
                identity_ids.add(int(probation["completed_by"]))
            probation_by_application[int(probation["application_id"])] = _row_dict(
                probation
            )
        identities = await self._resolve_identities(identity_ids)
        for note_items in notes_by_application.values():
            for note in note_items:
                note["author"] = identities.get(int(note["author_id"]))
        for event_items in events_by_application.values():
            for event in event_items:
                event["actor"] = identities.get(int(event["actor_id"]))
        for assessment_items in assessments_by_application.values():
            for assessment in assessment_items:
                assessment["reviewer"] = identities.get(
                    int(assessment["reviewer_id"])
                )
        for interview_items in interviews_by_application.values():
            for interview in interview_items:
                interview["requested_by_identity"] = identities.get(
                    int(interview["requested_by"])
                )
                if interview.get("completed_by") is not None:
                    interview["completed_by_identity"] = identities.get(
                        int(interview["completed_by"])
                    )
        for probation in probation_by_application.values():
            if probation.get("completed_by") is not None:
                probation["completed_by_identity"] = identities.get(
                    int(probation["completed_by"])
                )
        return {
            "items": [
                {
                    **_row_dict(row),
                    "applicant": identities.get(int(row["applicant_id"])),
                    "application_label": self._application_label(
                        str(row["application_type"])
                    ),
                    "claimed_by_identity": identities.get(int(row["claimed_by"]))
                    if row["claimed_by"] is not None
                    else None,
                    "decided_by_identity": identities.get(int(row["decided_by"]))
                    if row["decided_by"] is not None
                    else None,
                    "answers": _json_object(
                        row["submitted_answers_json"] or row["answers_json"]
                    ),
                    "questions": self._submitted_application_questions(row),
                    "rubric": self._application_rubric(
                        str(row["application_type"])
                    ),
                    "decision_categories": {
                        action: self._application_decision_categories(action)
                        for action in ("hold", "accept", "reject")
                    },
                    "assessments": assessments_by_application[int(row["id"])],
                    "assessment_summary": self._application_assessment_summary(
                        str(row["application_type"]),
                        assessments_by_application[int(row["id"])],
                        calibration_resolved=bool(row["calibration_resolved_ts"]),
                    ),
                    "interviews": interviews_by_application[int(row["id"])],
                    "probation": probation_by_application.get(int(row["id"])),
                    "internal_notes": notes_by_application[int(row["id"])],
                    "timeline": events_by_application[int(row["id"])],
                    "available_actions": self._application_actions_for_status(
                        str(row["status"]),
                        interview_delivery_pending=str(
                            row["interview_delivery_status"] or ""
                        )
                        in {"pending", "processing", "failed"},
                    )
                    + (
                        ["calibrate"]
                        if principal.can("applications.review_all")
                        and self._application_assessment_summary(
                            str(row["application_type"]),
                            assessments_by_application[int(row["id"])],
                            calibration_resolved=bool(
                                row["calibration_resolved_ts"]
                            ),
                        )["calibration_required"]
                        else []
                    ),
                }
                for row in rows
            ],
            "application_types": visible_types,
        }

    async def application_assessment(self, principal, application_id, payload):
        row = await self.db.fetchone(
            "SELECT * FROM staff_applications WHERE id=? AND guild_id=?",
            (application_id, principal.guild_id),
        )
        if row is None:
            raise PortalError(404, "application_not_found", "Application not found")
        application_type = str(row["application_type"] or "judge")
        if not self._can_review_application_type(principal, application_type):
            raise PortalError(
                403, "application_scope_denied", "You cannot assess that application"
            )
        if str(row["status"]) not in {
            "submitted",
            "under_review",
            "interview",
            "hold",
        }:
            raise PortalError(
                409,
                "assessment_unavailable",
                "Assessments are closed for this application",
            )
        rubric = self._application_rubric(application_type)
        raw_scores = payload.get("scores")
        raw_evidence = payload.get("evidence")
        if not isinstance(raw_scores, dict) or not isinstance(raw_evidence, dict):
            raise PortalError(
                400,
                "invalid_assessment",
                "Every rubric dimension needs a score and evidence note",
            )
        scores: dict[str, int] = {}
        evidence: dict[str, str] = {}
        for dimension in rubric["dimensions"]:
            key = dimension["key"]
            value = raw_scores.get(key)
            if isinstance(value, bool):
                value = 0
            try:
                score = int(value)
            except (TypeError, ValueError) as exc:
                raise PortalError(
                    400,
                    "invalid_assessment_score",
                    f"Score {dimension['label']} from 1 to 5",
                ) from exc
            if not 1 <= score <= 5:
                raise PortalError(
                    400,
                    "invalid_assessment_score",
                    f"Score {dimension['label']} from 1 to 5",
                )
            scores[key] = score
            evidence[key] = _bounded_text(
                raw_evidence.get(key), 1000, required=True
            )
        recommendation = str(payload.get("recommendation") or "").casefold()
        if recommendation not in {"hold", "interview", "accept", "reject"}:
            raise PortalError(
                400,
                "invalid_assessment_recommendation",
                "Choose Hold, Interview, Accept, or Reject",
            )
        now = int(time.time())
        correlation = new_correlation_id("application-assessment")
        await self.db.execute_transaction(
            [
                (
                    (
                        "INSERT INTO staff_application_assessments("
                        "application_id,reviewer_id,rubric_version,scores_json,"
                        "evidence_json,recommendation,created_ts,updated_ts"
                        ") VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(application_id,reviewer_id) "
                        "DO UPDATE SET rubric_version=excluded.rubric_version,"
                        "scores_json=excluded.scores_json,evidence_json=excluded.evidence_json,"
                        "recommendation=excluded.recommendation,updated_ts=excluded.updated_ts"
                    ),
                    (
                        application_id,
                        principal.user_id,
                        rubric["version"],
                        json.dumps(scores, separators=(",", ":")),
                        json.dumps(evidence, separators=(",", ":"), ensure_ascii=False),
                        recommendation,
                        now,
                        now,
                    ),
                ),
                (
                    (
                        "UPDATE staff_applications SET claimed_by=COALESCE(claimed_by,?),"
                        "first_review_ts=COALESCE(first_review_ts,?),updated_ts=?,"
                        "calibration_resolved_ts=NULL,calibration_resolved_by=NULL,"
                        "calibration_note='' WHERE id=?"
                    ),
                    (principal.user_id, now, now, application_id),
                ),
                (
                    (
                        "INSERT INTO staff_application_events("
                        "application_id,actor_id,event,from_status,to_status,detail_json,"
                        "created_ts,correlation_id) VALUES(?,?,?,?,?,?,?,?)"
                    ),
                    (
                        application_id,
                        principal.user_id,
                        "assessment_recorded",
                        str(row["status"]),
                        str(row["status"]),
                        json.dumps(
                            {
                                "rubric_version": rubric["version"],
                                "recommendation": recommendation,
                            },
                            separators=(",", ":"),
                        ),
                        now,
                        correlation,
                    ),
                ),
            ],
            retry_safe=True,
        )
        stored = await self.db.fetchall(
            "SELECT reviewer_id,scores_json,evidence_json,recommendation FROM "
            "staff_application_assessments WHERE application_id=?",
            (application_id,),
        )
        items = [
            {
                "reviewer_id": str(item["reviewer_id"]),
                "scores": _json_object(item["scores_json"]),
                "evidence": _json_object(item["evidence_json"]),
                "recommendation": str(item["recommendation"]),
            }
            for item in stored
        ]
        return {
            "ok": True,
            "assessment_summary": self._application_assessment_summary(
                application_type, items, calibration_resolved=False
            ),
        }

    async def application_interview_outcome(self, principal, application_id, payload):
        async with self._application_lock:
            return await self._application_interview_outcome_unlocked(
                principal, application_id, payload
            )

    async def _application_interview_outcome_unlocked(
        self, principal, application_id, payload
    ):
        if payload.get("confirmed") is not True:
            raise PortalError(
                400, "confirmation_required", "Confirm the interview outcome"
            )
        try:
            interview_id = int(payload.get("interview_id"))
        except (TypeError, ValueError) as exc:
            raise PortalError(
                400, "invalid_interview", "Choose the interview to complete"
            ) from exc
        notes = _bounded_text(payload.get("notes"), 4000, required=True)
        recommendation = str(payload.get("recommendation") or "").casefold()
        if recommendation not in {"hold", "accept", "reject"}:
            raise PortalError(
                400,
                "invalid_interview_recommendation",
                "Choose Hold, Accept, or Reject",
            )
        row = await self.db.fetchone(
            "SELECT i.*,a.application_type,a.status AS application_status "
            "FROM staff_application_interviews i "
            "JOIN staff_applications a ON a.id=i.application_id "
            "WHERE i.id=? AND i.application_id=? AND a.guild_id=?",
            (interview_id, application_id, principal.guild_id),
        )
        if row is None:
            raise PortalError(404, "interview_not_found", "Interview record not found")
        application_type = str(row["application_type"] or "judge")
        if not self._can_review_application_type(principal, application_type):
            raise PortalError(
                403,
                "application_scope_denied",
                "You cannot complete that interview",
            )
        if str(row["status"]) != "open":
            raise PortalError(
                409,
                "interview_not_open",
                "Only an open interview can be completed",
            )
        now = int(time.time())
        correlation = new_correlation_id("application-interview-outcome")
        await self.db.execute_transaction(
            [
                (
                    (
                        "UPDATE staff_application_interviews SET status='completed',"
                        "notes=?,recommendation=?,completed_ts=?,completed_by=? "
                        "WHERE id=? AND application_id=? AND status='open'"
                    ),
                    (
                        notes,
                        recommendation,
                        now,
                        principal.user_id,
                        interview_id,
                        application_id,
                    ),
                ),
                (
                    (
                        "UPDATE staff_applications SET calibration_resolved_ts=?,"
                        "calibration_resolved_by=?,calibration_note=?,updated_ts=? "
                        "WHERE id=? AND guild_id=?"
                    ),
                    (
                        now,
                        principal.user_id,
                        f"Interview completed: {notes}",
                        now,
                        application_id,
                        principal.guild_id,
                    ),
                ),
                (
                    (
                        "INSERT INTO staff_application_events("
                        "application_id,actor_id,event,from_status,to_status,"
                        "detail_json,created_ts,correlation_id) VALUES(?,?,?,?,?,?,?,?)"
                    ),
                    (
                        application_id,
                        principal.user_id,
                        "interview_completed",
                        str(row["application_status"]),
                        str(row["application_status"]),
                        json.dumps(
                            {
                                "interview_id": interview_id,
                                "recommendation": recommendation,
                            },
                            separators=(",", ":"),
                        ),
                        now,
                        correlation,
                    ),
                ),
            ],
            retry_safe=True,
        )
        return {
            "ok": True,
            "interview_id": interview_id,
            "status": "completed",
            "recommendation": recommendation,
            "calibration_resolved": True,
        }

    async def application_probation_action(self, principal, application_id, payload):
        principal.require("applications.review_all")
        action = str(payload.get("action") or "").casefold()
        if action not in {"complete", "extend", "end"}:
            raise PortalError(
                400, "invalid_probation_action", "Choose a valid probation action"
            )
        if payload.get("confirmed") is not True:
            raise PortalError(400, "confirmation_required", "Confirm this action")
        reason = _bounded_text(payload.get("reason"), 1000, required=True)
        row = await self.db.fetchone(
            "SELECT p.*,a.status AS application_status FROM staff_application_probations p "
            "JOIN staff_applications a ON a.id=p.application_id "
            "WHERE p.application_id=? AND p.guild_id=?",
            (application_id, principal.guild_id),
        )
        if row is None:
            raise PortalError(404, "probation_not_found", "Probation record not found")
        if str(row["status"]) != "active":
            raise PortalError(409, "probation_closed", "This probation is already closed")
        now = int(time.time())
        if action == "extend":
            try:
                extra_days = max(1, min(90, int(payload.get("days") or 7)))
            except (TypeError, ValueError) as exc:
                raise PortalError(400, "invalid_probation_extension", "Enter valid days") from exc
            due_ts = int(row["due_ts"]) + extra_days * 86400
            status = "active"
            outcome = "extended"
            completed_ts = None
        else:
            due_ts = int(row["due_ts"])
            status = "completed" if action == "complete" else "ended"
            outcome = "passed" if action == "complete" else "ended_early"
            completed_ts = now
        correlation = new_correlation_id("application-probation")
        await self.db.execute_transaction(
            [
                (
                    (
                        "UPDATE staff_application_probations SET status=?,due_ts=?,"
                        "completed_ts=?,completed_by=?,outcome=?,notes=?,updated_ts=? "
                        "WHERE application_id=? AND status='active'"
                    ),
                    (
                        status,
                        due_ts,
                        completed_ts,
                        principal.user_id if completed_ts else None,
                        outcome,
                        reason,
                        now,
                        application_id,
                    ),
                ),
                (
                    (
                        "INSERT INTO staff_application_events("
                        "application_id,actor_id,event,from_status,to_status,detail_json,"
                        "created_ts,correlation_id) VALUES(?,?,?,?,?,?,?,?)"
                    ),
                    (
                        application_id,
                        principal.user_id,
                        f"probation_{action}",
                        str(row["application_status"]),
                        str(row["application_status"]),
                        json.dumps(
                            {"reason": reason, "due_ts": due_ts},
                            separators=(",", ":"),
                        ),
                        now,
                        correlation,
                    ),
                ),
            ],
            retry_safe=True,
        )
        if action in {"complete", "end"}:
            await self.db.execute(
                "UPDATE staff_tasks SET status='done',completed_ts=?,updated_ts=? "
                "WHERE guild_id=? AND linked_entity_type='application' "
                "AND linked_entity_id=? AND task_type='system' AND status!='done'",
                (now, now, principal.guild_id, str(application_id)),
            )
        await self.bot.outbox.enqueue(
            "send_dm",
            guild_id=principal.guild_id,
            user_id=int(row["applicant_id"]),
            payload={
                "content": (
                    f"Your GD Avenue probation has been extended until <t:{due_ts}:D>."
                    if action == "extend"
                    else "Your GD Avenue probation has been completed. Thank you for your work."
                    if action == "complete"
                    else "Your GD Avenue probation has ended. Staff will contact you if more context is needed."
                )
            },
            correlation_id=correlation,
            idempotency_key=f"{correlation}:probation-dm",
        )
        return {"ok": True, "status": status, "due_ts": due_ts}

    async def application_action(self, principal, application_id, payload):
        action = str(payload.get("action") or "").casefold()
        targets = {
            "claim": "under_review",
            "interview": "interview",
            "hold": "hold",
            "accept": "accepted_pending_role",
            "reject": "rejected",
        }
        if action not in {*targets, "message", "calibrate"}:
            raise PortalError(400, "invalid_application_action", "Choose a valid application action")
        if action in {"interview", "accept", "reject", "calibrate"} and payload.get("confirmed") is not True:
            raise PortalError(400, "confirmation_required", "Confirm the final application decision")
        reason = _bounded_text(
            payload.get("reason"),
            1000,
            required=action in {"hold", "accept", "reject", "calibrate"},
        )
        applicant_message = _bounded_text(payload.get("applicant_message"), 1000)
        decision_category = str(payload.get("category") or "").strip().casefold()[:80]
        async with self._application_lock:
            row = await self.db.fetchone("SELECT * FROM staff_applications WHERE id=? AND guild_id=?", (application_id, principal.guild_id))
            if row is None:
                raise PortalError(404, "application_not_found", "Application not found")
            application_type = str(row["application_type"] or "judge")
            if not self._can_review_application_type(principal, application_type):
                raise PortalError(403, "application_scope_denied", "You cannot manage that application type")
            application_label = self._application_label(application_type)
            old = str(row["status"])
            is_v2 = str(row["form_version"] or "") == self._application_form_version()
            if is_v2 and action == "interview" and not reason:
                raise PortalError(
                    400,
                    "interview_reason_required",
                    "Document why an interview is needed",
                )
            if is_v2 and action in {"hold", "accept", "reject"}:
                categories = self._application_decision_categories(action)
                if decision_category not in categories:
                    raise PortalError(
                        400,
                        "decision_category_required",
                        "Choose an internal decision category",
                    )
            interview_delivery_pending = False
            if old == "interview" and row["interview_ticket_outbox_id"] is not None:
                delivery = await self.db.fetchone(
                    "SELECT status FROM discord_outbox WHERE id=?",
                    (int(row["interview_ticket_outbox_id"]),),
                )
                interview_delivery_pending = bool(
                    delivery
                    and str(delivery["status"] or "")
                    in {"pending", "processing", "failed"}
                )
            if action == "calibrate":
                if not principal.can("applications.review_all"):
                    raise PortalError(
                        403,
                        "calibration_permission_required",
                        "A Head Reviewer or higher must resolve calibration",
                    )
                assessments = await self.db.fetchall(
                    "SELECT scores_json,evidence_json,recommendation FROM "
                    "staff_application_assessments WHERE application_id=?",
                    (application_id,),
                )
                summary = self._application_assessment_summary(
                    application_type,
                    [
                        {
                            "scores": _json_object(item["scores_json"]),
                            "evidence": _json_object(item["evidence_json"]),
                            "recommendation": str(item["recommendation"]),
                        }
                        for item in assessments
                    ],
                    calibration_resolved=False,
                )
                if not summary["calibration_required"]:
                    raise PortalError(
                        409,
                        "calibration_not_required",
                        "This application does not have an unresolved scoring disagreement",
                    )
                now = int(time.time())
                correlation = new_correlation_id("application-calibration")
                await self.db.execute_transaction(
                    [
                        (
                            (
                                "UPDATE staff_applications SET calibration_resolved_ts=?,"
                                "calibration_resolved_by=?,calibration_note=?,updated_ts=? WHERE id=?"
                            ),
                            (now, principal.user_id, reason, now, application_id),
                        ),
                        (
                            (
                                "INSERT INTO staff_application_events("
                                "application_id,actor_id,event,from_status,to_status,"
                                "detail_json,created_ts,correlation_id) VALUES(?,?,?,?,?,?,?,?)"
                            ),
                            (
                                application_id,
                                principal.user_id,
                                "calibration_resolved",
                                old,
                                old,
                                json.dumps({"reason": reason}, separators=(",", ":")),
                                now,
                                correlation,
                            ),
                        ),
                    ],
                    retry_safe=True,
                )
                return {"ok": True, "status": old, "calibration_resolved": True}
            if action not in self._application_actions_for_status(old):
                raise PortalError(
                    409,
                    "application_action_unavailable",
                    "That action is not available at this stage of the application",
                )
            if action == "interview" and interview_delivery_pending:
                raise PortalError(
                    409,
                    "interview_delivery_pending",
                    "The current interview is still being created. Try again after delivery completes",
                )
            now = int(time.time())
            correlation = new_correlation_id("application")
            if action == "message":
                message = _bounded_text(payload.get("message"), 1800, required=True)
                await self.db.execute(
                    "INSERT INTO staff_application_events("
                    "application_id,actor_id,event,from_status,to_status,"
                    "detail_json,created_ts,correlation_id) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        application_id,
                        principal.user_id,
                        "message",
                        old,
                        old,
                        json.dumps({"message": message}, separators=(",", ":")),
                        now,
                        correlation,
                    ),
                )
                outbox_id = await self.bot.outbox.enqueue(
                    "send_dm",
                    guild_id=principal.guild_id,
                    user_id=int(row["applicant_id"]),
                    payload={
                        "content": (
                            f"GD Avenue staff sent you a message about your "
                            f"{application_label}:\n\n{message}"
                        )
                    },
                    correlation_id=correlation,
                    idempotency_key=f"{correlation}:applicant-dm",
                )
                return {
                    "ok": True,
                    "status": old,
                    "message_delivery": "pending",
                    "outbox_id": outbox_id,
                }
            if old == "accepted_pending_role" and action == "accept":
                await self._reconcile_application_roles()
                return {"ok": True, "status": old, "role_delivery": "pending"}
            if is_v2 and action in {"accept", "reject"}:
                assessment_rows = await self.db.fetchall(
                    "SELECT scores_json,evidence_json,recommendation FROM "
                    "staff_application_assessments WHERE application_id=?",
                    (application_id,),
                )
                summary = self._application_assessment_summary(
                    application_type,
                    [
                        {
                            "scores": _json_object(item["scores_json"]),
                            "evidence": _json_object(item["evidence_json"]),
                            "recommendation": str(item["recommendation"]),
                        }
                        for item in assessment_rows
                    ],
                    calibration_resolved=bool(row["calibration_resolved_ts"]),
                )
                if not summary["complete"]:
                    raise PortalError(
                        409,
                        "assessments_incomplete",
                        f"Record {summary['minimum']} independent rubric assessments before a final decision",
                    )
                if summary["calibration_required"]:
                    raise PortalError(
                        409,
                        "calibration_required",
                        "Resolve the scoring disagreement or interview the applicant before a final decision",
                    )
            target = targets[action]
            interview_questions: list[str] = []
            if action == "interview":
                interview_questions = [
                    line.strip()[:500]
                    for line in str(
                        payload.get("clarification_questions") or ""
                    ).splitlines()
                    if line.strip()
                ][:12]
                if is_v2 and not interview_questions:
                    raise PortalError(
                        400,
                        "interview_questions_required",
                        "Add at least one question the interview should clarify",
                    )
            role_ids: list[int] = []
            if action == "accept":
                role_ids = self._application_role_ids(application_type)
                if application_type == "judge" and not role_ids:
                    raise PortalError(503, "reviewer_role_missing", "The Reviewer role is not configured")
                if not role_ids:
                    target = "accepted"
            await self.db.execute_transaction(
                [
                    (
                        (
                            "UPDATE staff_applications SET status=?,"
                            "claimed_by=COALESCE(claimed_by,?),updated_ts=?,"
                            "decided_by=?,decided_ts=?,decision_reason=?,"
                            "decision_category=?,applicant_message=?,"
                            "first_review_ts=COALESCE(first_review_ts,?) "
                            "WHERE id=? AND guild_id=?"
                        ),
                        (
                            target,
                            principal.user_id,
                            now,
                            principal.user_id if action in {"accept", "reject"} else None,
                            now if action in {"accept", "reject"} else None,
                            reason,
                            decision_category,
                            applicant_message,
                            now,
                            application_id,
                            principal.guild_id,
                        ),
                    ),
                    (
                        (
                            "INSERT INTO staff_application_events("
                            "application_id,actor_id,event,from_status,to_status,"
                            "detail_json,created_ts,correlation_id) "
                            "VALUES(?,?,?,?,?,?,?,?)"
                        ),
                        (
                            application_id,
                            principal.user_id,
                            action,
                            old,
                            target,
                            json.dumps(
                                {
                                    "reason": reason,
                                    "category": decision_category,
                                    "applicant_message": applicant_message,
                                },
                                separators=(",", ":"),
                            ),
                            now,
                            correlation,
                        ),
                    ),
                ],
                retry_safe=True,
            )
            outbox_id = None
            if action == "accept":
                probation_days = self._application_probation_days()
                probation_due_ts = now + probation_days * 86400
                await self.db.execute(
                    "INSERT INTO staff_application_probations("
                    "application_id,guild_id,applicant_id,application_type,status,"
                    "started_ts,due_ts,updated_ts) VALUES(?,?,?,?, 'active',?,?,?) "
                    "ON CONFLICT(application_id) DO NOTHING",
                    (
                        application_id,
                        principal.guild_id,
                        int(row["applicant_id"]),
                        application_type,
                        now,
                        probation_due_ts,
                        now,
                    ),
                )
                await self.db.execute(
                    "INSERT INTO staff_tasks("
                    "guild_id,task_type,title,description,priority,status,created_by,"
                    "assignee_id,created_ts,updated_ts,due_ts,linked_entity_type,"
                    "linked_entity_id) VALUES(?, 'system', ?, ?, 'normal',"
                    "'todo', ?, NULL, ?, ?, ?, 'application', ?)",
                    (
                        principal.guild_id,
                        f"{application_label} probation checkpoint",
                        "Review the new staff member's probation with documented examples and record the outcome.",
                        principal.user_id,
                        now,
                        now,
                        probation_due_ts,
                        str(application_id),
                    ),
                )
                if role_ids:
                    outbox_id = await self.bot.outbox.enqueue(
                        "add_role",
                        guild_id=principal.guild_id,
                        user_id=int(row["applicant_id"]),
                        payload={
                            "role_id": role_ids[0],
                            "reason": f"{application_label} accepted",
                        },
                        correlation_id=f"staff-application:{application_id}",
                        idempotency_key=f"staff-application:{application_id}:{application_type}-role",
                    )
                    await self.db.execute(
                        "UPDATE staff_applications SET role_outbox_id=?,updated_ts=? "
                        "WHERE id=? AND status='accepted_pending_role'",
                        (outbox_id, int(time.time()), application_id),
                    )
                await self.bot.outbox.enqueue(
                    "send_dm",
                    guild_id=principal.guild_id,
                    user_id=int(row["applicant_id"]),
                    payload={
                        "content": (
                            f"Your GD Avenue {application_label} was accepted. Welcome to the team. "
                            f"Your {probation_days}-day probation checkpoint is <t:{probation_due_ts}:D>."
                            + (f"\n\n{applicant_message}" if applicant_message else "")
                        )
                    },
                    correlation_id=f"staff-application:{application_id}",
                    idempotency_key=f"staff-application:{application_id}:accepted-dm",
                )
            elif action == "reject":
                message = f"Your GD Avenue {application_label} was not accepted."
                if applicant_message:
                    message += f"\n\n{applicant_message}"
                await self.bot.outbox.enqueue(
                    "send_dm",
                    guild_id=principal.guild_id,
                    user_id=int(row["applicant_id"]),
                    payload={"content": message},
                    correlation_id=f"staff-application:{application_id}",
                    idempotency_key=f"staff-application:{application_id}:rejected-dm",
                )
            elif action == "interview":
                repeat_interview = bool(row["interview_ticket_channel_id"])
                await self.db.execute(
                    "INSERT INTO staff_application_interviews("
                    "application_id,requested_by,reason,questions_json,recommendation,"
                    "status,created_ts) VALUES(?,?,?,?,?,'requested',?)",
                    (
                        application_id,
                        principal.user_id,
                        reason,
                        json.dumps(interview_questions, separators=(",", ":"), ensure_ascii=False),
                        str(payload.get("recommendation") or "")[:80],
                        now,
                    ),
                )
                interview_outbox_id = await self.bot.outbox.enqueue(
                    "create_interview_ticket",
                    guild_id=principal.guild_id,
                    user_id=int(row["applicant_id"]),
                    payload={
                        "application_id": application_id,
                        "application_type": application_type,
                        "application_label": application_label,
                        "interview_run_id": correlation,
                        "repeat_interview": repeat_interview,
                        "clarification_questions": interview_questions,
                    },
                    correlation_id=correlation,
                    idempotency_key=f"{correlation}:interview-ticket",
                )
                await self.db.execute(
                    "UPDATE staff_applications SET interview_ticket_outbox_id=?,updated_ts=? WHERE id=?",
                    (interview_outbox_id, int(time.time()), application_id),
                )
        return {
            "ok": True,
            "status": target,
            "role_delivery": (
                "pending" if action == "accept" and outbox_id else
                "manual" if action == "accept" else None
            ),
            "interview_delivery": "pending" if action == "interview" else None,
        }

    async def application_note(self, principal, application_id, payload):
        body = _bounded_text(payload.get("body"), 4000, required=True)
        exists = await self.db.fetchone("SELECT application_type FROM staff_applications WHERE id=? AND guild_id=?", (application_id, principal.guild_id))
        if exists is None:
            raise PortalError(404, "application_not_found", "Application not found")
        if not self._can_review_application_type(
            principal, str(exists["application_type"] or "judge")
        ):
            raise PortalError(403, "application_scope_denied", "You cannot note that application")
        now = int(time.time())
        note_id = await self.db.execute_insert("INSERT INTO staff_application_notes(application_id,author_id,body,created_ts,updated_ts) VALUES(?,?,?,?,?)", (application_id, principal.user_id, body, now, now))
        return {"note": {"id": note_id, "body": body, "author_id": str(principal.user_id), "created_ts": now}}

    async def staff_list(self, principal):
        guild, _member = await self._member(principal.user_id)
        role_ids_by_key = {
            "reviewer": self.bot.config.get_int_list(
                "staff_portal", "judge_role_ids"
            ),
            "head_reviewer": self.bot.config.get_int_list(
                "staff_portal", "head_judge_role_ids"
            ),
            "admin": self.bot.config.get_int_list(
                "staff_portal", "admin_role_ids"
            ),
            "owner": self.bot.config.get_int_list(
                "staff_portal", "owner_role_ids"
            ),
        }
        role_ids = {
            role_id for values in role_ids_by_key.values() for role_id in values
        }
        allowlisted_users = set(
            self.bot.config.get_int_list("staff_portal", "owner_user_ids")
        ) | set(self.bot.config.get_int_list("staff_portal", "dev_user_ids"))
        persisted_rows = await self.db.fetchall(
            "SELECT s.*,o.status AS role_delivery_status FROM staff_members s "
            "LEFT JOIN discord_outbox o ON o.id=s.role_outbox_id WHERE s.guild_id=?",
            (principal.guild_id,),
        )
        persisted = {int(row["user_id"]): _row_dict(row) for row in persisted_rows}
        member_by_id = {int(member.id): member for member in getattr(guild, "members", ())}
        tracked_ids = set(persisted) | allowlisted_users
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
        identities = await self._resolve_identities(tracked_ids)
        items = []
        for user_id in tracked_ids:
            member = member_by_id.get(user_id)
            member_roles = {int(role.id) for role in getattr(member, "roles", ())}
            role = resolve_staff_role(user_id, member_roles, self.bot.config)
            saved = persisted.get(user_id, {})
            if saved.get("status") == "removed" and role == "applicant" and saved.get("role_delivery_status") in {None, "delivered", "dead"}:
                continue
            displayed_role = role
            if role == "applicant" and saved.get("status") == "active" and saved.get("desired_role") in {"reviewer", "head_reviewer", "admin", "owner"}:
                displayed_role = str(saved["desired_role"])
            review = reviews_by_user.get(user_id, {})
            identity = identities.get(user_id) or {
                "id": str(user_id),
                "display_name": f"Former staff {user_id}",
                "portal_nickname": "",
                "discord_display_name": "",
                "global_display_name": "",
                "username": "",
                "avatar_url": "",
            }
            items.append(
                {
                    "id": str(user_id),
                    **identity,
                    "role": displayed_role,
                    "role_label": role_label(displayed_role),
                    "active": displayed_role != "applicant",
                    "desired_role": saved.get("desired_role"),
                    "desired_status": saved.get("status"),
                    "role_delivery_status": saved.get("role_delivery_status"),
                    "reviews": int(review.get("c") or 0),
                    "workload": claims_by_user.get(user_id, 0),
                    "last_activity_ts": review.get("last_ts"),
                }
            )
        items.sort(
            key=lambda item: (
                -ROLE_ORDER.get(str(item["role"]), -1),
                str(item["display_name"]).casefold(),
            )
        )
        return {"items": items}

    async def add_staff(self, principal, payload):
        principal.require("developer.access")
        user_id = _discord_id(payload.get("user_id"), field="Discord user ID")
        desired = str(payload.get("role") or "reviewer").strip().casefold()
        actions = {
            "reviewer": "restore",
            "head_reviewer": "promote",
            "admin": "set_admin",
            "owner": "set_owner",
        }
        action = actions.get(desired)
        if action is None:
            raise PortalError(400, "invalid_staff_role", "Choose Reviewer, Head Reviewer, Admin, or Owner")
        result = await self.staff_action(
            principal,
            str(user_id),
            {
                "action": action,
                "reason": _bounded_text(payload.get("reason"), 1000, required=True),
                "confirmed": payload.get("confirmed") is True,
            },
        )
        return {**result, "user_id": str(user_id)}

    async def staff_action(self, principal, user_id, payload):
        action = str(payload.get("action") or "").casefold()
        reason = _bounded_text(payload.get("reason"), 1000, required=True)
        role_map = {
            "promote": "head_reviewer",
            "demote": "reviewer",
            "deactivate": "inactive",
            "restore": "reviewer",
            "set_admin": "admin",
            "revoke_admin": "head_reviewer",
            "set_owner": "owner",
            "revoke_owner": "admin",
            "remove": "inactive",
        }
        desired = role_map.get(action)
        if desired is None:
            raise PortalError(400, "invalid_staff_action", "Choose a valid staff action")
        if payload.get("confirmed") is not True:
            raise PortalError(400, "confirmation_required", "Confirm the staff access change")
        user_id = _discord_id(user_id, field="Staff member ID")
        if user_id == principal.user_id:
            raise PortalError(409, "protected_staff_account", "That staff account cannot be changed here")
        dev_users = set(self.bot.config.get_int_list("staff_portal", "dev_user_ids"))
        protected_users = dev_users | set(self.bot.config.get_int_list("staff_portal", "owner_user_ids"))
        if user_id in protected_users:
            raise PortalError(
                409,
                "protected_developer_account",
                "Config-managed Dev and Owner access cannot be changed in the portal",
            )

        guild, _actor = await self._member(principal.user_id)
        target_member = guild.get_member(user_id)
        if target_member is None:
            try:
                target_member = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                raise PortalError(
                    404, "staff_member_not_found", "That guild member could not be found"
                ) from exc
        target_role_ids = {
            int(role.id) for role in getattr(target_member, "roles", ())
        }
        current = resolve_staff_role(user_id, target_role_ids, self.bot.config)

        if action == "remove" or desired == "owner" or current == "owner":
            principal.require("developer.access")
        elif desired == "admin" or current == "admin":
            principal.require("staff.manage_all")
        else:
            principal.require("staff.manage_standard_roles")

        role_ids_by_key = {
            "reviewer": self.bot.config.get_int_list(
                "staff_portal", "judge_role_ids"
            ),
            "head_reviewer": self.bot.config.get_int_list(
                "staff_portal", "head_judge_role_ids"
            ),
            "admin": self.bot.config.get_int_list(
                "staff_portal", "admin_role_ids"
            ),
            "owner": self.bot.config.get_int_list(
                "staff_portal", "owner_role_ids"
            ),
        }
        if desired != "inactive" and not role_ids_by_key.get(desired):
            raise PortalError(
                503,
                "staff_role_missing",
                f"The {role_label(desired)} Discord role is not configured",
            )
        correlation = new_correlation_id("staff-role")
        managed_role_ids = {
            role_id for values in role_ids_by_key.values() for role_id in values
        }
        desired_role_id = (
            role_ids_by_key[desired][0] if desired != "inactive" else None
        )
        actions = []
        if desired_role_id is not None and desired_role_id not in target_role_ids:
            actions.append(("add_role", desired_role_id))
        for role_id in sorted(managed_role_ids & target_role_ids):
            if role_id != desired_role_id:
                actions.append(("remove_role", role_id))
        outbox_ids = []
        for index, (kind, role_id) in enumerate(actions):
            outbox_ids.append(await self.bot.outbox.enqueue(kind, guild_id=principal.guild_id, user_id=user_id, payload={"role_id": role_id, "reason": reason}, correlation_id=correlation, idempotency_key=f"staff-role:{correlation}:{index}"))
        now = int(time.time())
        membership_status = "removed" if action == "remove" else "inactive" if desired == "inactive" else "active"
        await self.db.execute("INSERT INTO staff_members(guild_id,user_id,desired_role,status,updated_by,updated_ts,reason,role_outbox_id) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET desired_role=excluded.desired_role,status=excluded.status,updated_by=excluded.updated_by,updated_ts=excluded.updated_ts,reason=excluded.reason,role_outbox_id=excluded.role_outbox_id", (principal.guild_id, user_id, desired, membership_status, principal.user_id, now, reason, outbox_ids[0] if outbox_ids else None))
        await self.db.execute("INSERT INTO workflow_events(correlation_id,workflow_type,entity_id,event,guild_id,actor_id,payload_json,created_ts) VALUES(?,'staff_portal',?,'staff_role_requested',?,?,?,?)", (correlation, f"staff:{user_id}", principal.guild_id, principal.user_id, json.dumps({"action": action, "from_role": current, "to_role": desired, "reason": reason}), now))
        self._invalidate_identity(user_id)
        return {
            "ok": True,
            "delivery": "pending" if actions else "unchanged",
            "from_role": current,
            "to_role": desired,
        }

    async def _record_admin_event(
        self,
        principal: StaffPrincipal,
        event: str,
        *,
        entity_id: str = "",
        payload: dict[str, Any] | None = None,
        correlation_id: str = "",
    ) -> str:
        correlation = correlation_id or new_correlation_id("staff-admin")
        await self.db.execute(
            "INSERT INTO workflow_events(correlation_id,workflow_type,entity_id,"
            "event,guild_id,actor_id,payload_json,created_ts) VALUES(?,"
            "'staff_portal',?,?,?,?,?,?)",
            (
                correlation,
                str(entity_id),
                str(event),
                principal.guild_id,
                principal.user_id,
                json.dumps(payload or {}, separators=(",", ":")),
                int(time.time()),
            ),
        )
        return correlation

    async def requests_admin(self, principal: StaffPrincipal) -> dict[str, Any]:
        state = await self.db.fetchone(
            "SELECT * FROM level_request_state WHERE guild_id=?",
            (principal.guild_id,),
        )
        scheduled = await self.db.fetchall(
            "SELECT id,request_limit,close_minutes,open_ts,request_type,"
            "open_message,created_by,created_ts,status FROM "
            "level_request_scheduled_openings WHERE guild_id=? AND "
            "status='pending' ORDER BY open_ts,id",
            (principal.guild_id,),
        )
        state_data = _row_dict(state)
        wave_id = int(state_data.get("wave_id") or 0)
        review_counts = await self.db.fetchone(
            "SELECT COUNT(*) AS total,"
            "SUM(CASE WHEN status='reviewed' THEN 1 ELSE 0 END) AS reviewed,"
            "SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending "
            "FROM level_request_submissions WHERE guild_id=? AND wave_id=?",
            (principal.guild_id, wave_id),
        )
        return {
            "state": state_data,
            "progress": {
                "total": int(review_counts["total"] or 0),
                "reviewed": int(review_counts["reviewed"] or 0),
                "pending": int(review_counts["pending"] or 0),
            },
            "scheduled": [_row_dict(row) for row in scheduled],
            "request_types": [
                "any",
                "needs_showcase",
                "only_demons",
                "only_plats",
                "only_classic",
                "only_classic_non_demons",
                "only_plats_non_demons",
                "long_level",
            ],
        }

    async def requests_action(
        self, principal: StaffPrincipal, payload: dict[str, Any]
    ) -> dict[str, Any]:
        principal.require("requests.manage")
        action = str(payload.get("action") or "").strip().casefold()
        if payload.get("confirmed") is not True:
            raise PortalError(422, "confirmation_required", "Confirm the request action")
        guild = self.bot.get_guild(principal.guild_id)
        cog = self.bot.get_cog("RequestLevelsCog")
        if guild is None or cog is None:
            raise PortalError(
                503,
                "request_system_unavailable",
                "The request system is not available right now",
            )
        reason = _bounded_text(payload.get("reason"), 500)
        result: dict[str, Any]
        if action == "open":
            request_limit = int(payload.get("request_limit") or 0) or None
            close_minutes = int(payload.get("close_minutes") or 0) or None
            if request_limit is not None and not 1 <= request_limit <= 10000:
                raise PortalError(422, "invalid_request_limit", "Request limit must be from 1 to 10,000")
            if close_minutes is not None and not 1 <= close_minutes <= 43200:
                raise PortalError(422, "invalid_close_time", "Close time must be from 1 to 43,200 minutes")
            request_type = str(payload.get("request_type") or "").strip()
            normalized = cog._normalize_request_type(request_type)
            if normalized is None:
                raise PortalError(422, "invalid_request_type", "Choose a supported request type")
            announcement = cog._clean_open_message(payload.get("open_message"))
            wave_id, close_ts = await cog._open_requests_now(
                guild,
                request_limit,
                close_minutes,
                normalized or "",
                announcement,
            )
            result = {"wave_id": wave_id, "close_ts": close_ts}
        elif action == "close":
            row = await cog._get_state(principal.guild_id)
            if str(row["state"]) != "closed":
                await cog._set_state_closed(
                    guild, reason=f"staff portal by {principal.user_id}: {reason}"
                )
            result = {"state": "closed", "wave_id": int(row["wave_id"] or 0)}
        elif action == "schedule":
            principal.require("requests.schedule")
            open_ts = int(payload.get("open_ts") or 0)
            if open_ts <= int(time.time()):
                raise PortalError(422, "invalid_open_time", "Opening time must be in the future")
            request_limit = int(payload.get("request_limit") or 0) or None
            close_minutes = int(payload.get("close_minutes") or 0) or None
            request_type = str(payload.get("request_type") or "").strip()
            normalized = cog._normalize_request_type(request_type)
            if normalized is None:
                raise PortalError(422, "invalid_request_type", "Choose a supported request type")
            announcement = cog._clean_open_message(payload.get("open_message"))
            opening_id = await self.db.execute_insert(
                "INSERT INTO level_request_scheduled_openings(guild_id,"
                "request_limit,close_minutes,open_ts,created_by,created_ts,status,"
                "request_type,open_message,correlation_id) VALUES(?,?,?,?,?,?,"
                "'pending',?,?,?)",
                (
                    principal.guild_id,
                    request_limit,
                    close_minutes,
                    open_ts,
                    principal.user_id,
                    int(time.time()),
                    normalized or None,
                    announcement,
                    new_correlation_id("scheduled"),
                ),
            )
            result = {"opening_id": opening_id, "open_ts": open_ts}
        elif action == "edit_scheduled":
            principal.require("requests.schedule")
            opening_id = int(payload.get("opening_id") or 0)
            open_ts = int(payload.get("open_ts") or 0)
            if open_ts <= int(time.time()):
                raise PortalError(
                    422,
                    "invalid_open_time",
                    "Opening time must be in the future",
                )
            request_limit = int(payload.get("request_limit") or 0) or None
            close_minutes = int(payload.get("close_minutes") or 0) or None
            if request_limit is not None and not 1 <= request_limit <= 10000:
                raise PortalError(
                    422,
                    "invalid_request_limit",
                    "Request limit must be from 1 to 10,000",
                )
            if close_minutes is not None and not 1 <= close_minutes <= 43200:
                raise PortalError(
                    422,
                    "invalid_close_time",
                    "Close time must be from 1 to 43,200 minutes",
                )
            normalized = cog._normalize_request_type(
                str(payload.get("request_type") or "").strip()
            )
            if normalized is None:
                raise PortalError(
                    422,
                    "invalid_request_type",
                    "Choose a supported request type",
                )
            announcement = cog._clean_open_message(payload.get("open_message"))
            changed = await self.db.execute_affected(
                "UPDATE level_request_scheduled_openings SET request_limit=?,"
                "close_minutes=?,open_ts=?,request_type=?,open_message=? "
                "WHERE guild_id=? AND id=? AND status='pending'",
                (
                    request_limit,
                    close_minutes,
                    open_ts,
                    normalized or None,
                    announcement,
                    principal.guild_id,
                    opening_id,
                ),
            )
            if not changed:
                raise PortalError(
                    404,
                    "scheduled_opening_not_found",
                    "That pending opening no longer exists",
                )
            result = {"opening_id": opening_id, "open_ts": open_ts}
        elif action == "cancel_scheduled":
            principal.require("requests.schedule")
            opening_id = int(payload.get("opening_id") or 0)
            changed = await self.db.execute_affected(
                "UPDATE level_request_scheduled_openings SET status='deleted' "
                "WHERE guild_id=? AND id=? AND status='pending'",
                (principal.guild_id, opening_id),
            )
            if not changed:
                raise PortalError(404, "scheduled_opening_not_found", "That pending opening no longer exists")
            result = {"opening_id": opening_id, "status": "deleted"}
        elif action == "refresh_button":
            message = await cog.refresh_or_create_request_button(guild)
            result = {
                "message_id": str(message.id) if message is not None else None,
                "refreshed": message is not None,
            }
        elif action == "repair":
            result = await cog.repair_request_system(guild)
        else:
            raise PortalError(400, "invalid_request_action", "Choose a valid request action")
        await self._record_admin_event(
            principal,
            f"requests_{action}",
            entity_id="request-system",
            payload={"reason": reason, "result": result},
        )
        return {"ok": True, "result": result}

    async def community(self, principal: StaffPrincipal) -> dict[str, Any]:
        ticket_rows = await self.db.fetchall(
            "SELECT status,COUNT(*) AS c FROM tickets GROUP BY status"
        )
        tracking = self.bot.get_cog("TrackingCog")
        icon = self.bot.config.get("server_icon_rotation", default={}) or {}
        forum_entries = self.bot.config.get("forum_first_message", "entries", default=[])
        return {
            "tracking": {
                "available": tracking is not None,
                "weekly_reward": "managed_per_week",
            },
            "support": {
                "tickets": {
                    str(row["status"]): int(row["c"] or 0) for row in ticket_rows
                }
            },
            "forum": {
                "rules": [
                    {
                        "forum_channel_id": str(entry.get("forum_channel_id") or ""),
                        "required_word": str(entry.get("required_word") or ""),
                        "match_mode": str(
                            entry.get("required_word_match_mode") or "contains"
                        ),
                    }
                    for entry in forum_entries
                    if isinstance(entry, dict)
                ]
            },
            "server_presentation": {
                "icon_rotation_mode": str(icon.get("mode") or "disabled"),
                "icon_count": len(icon.get("urls") or []),
                "current_index": int(icon.get("current_index") or -1),
            },
        }

    async def community_action(
        self, principal: StaffPrincipal, payload: dict[str, Any]
    ) -> dict[str, Any]:
        action = str(payload.get("action") or "").strip().casefold()
        if payload.get("confirmed") is not True:
            raise PortalError(422, "confirmation_required", "Confirm the community action")
        if action not in {"enable_weekly_reward", "disable_weekly_reward"}:
            raise PortalError(400, "invalid_community_action", "Choose a valid community action")
        principal.require("tracking.manage")
        tracking = self.bot.get_cog("TrackingCog")
        guild = self.bot.get_guild(principal.guild_id)
        if tracking is None or guild is None:
            raise PortalError(503, "tracking_unavailable", "Tracking is not available right now")
        if action == "disable_weekly_reward":
            week_start = await tracking.disable_weekly_reward_for_current_week(
                guild, principal.user_id
            )
            result = {"week_start": week_start, "disabled": True}
        else:
            week_start, was_disabled = await tracking.enable_weekly_reward_for_current_week(
                guild, principal.user_id
            )
            result = {
                "week_start": week_start,
                "disabled": False,
                "was_disabled": was_disabled,
            }
        await self._record_admin_event(
            principal, f"tracking_{action}", entity_id="tracking", payload=result
        )
        return {"ok": True, "result": result}

    @staticmethod
    def _incident_summary(message: Any) -> tuple[str, str]:
        text = str(message or "").strip()
        first_line = text.splitlines()[0] if text else "Unknown error"
        match = re.search(r"\b([A-Z][A-Za-z]+(?:Error|Exception))\b", text)
        error_type = match.group(1) if match else "Error"
        normalized = re.sub(r"\s+", " ", first_line)[:180]
        return error_type, normalized

    @staticmethod
    def _sanitize_incident_detail(message: Any) -> str:
        text = str(message or "")[:20000]
        text = re.sub(
            r"(?i)\b(DISCORD_TOKEN|TURSO_AUTH_TOKEN|LIBSQL_AUTH_TOKEN|DATABASE_URL|"
            r"STAFF_API_TOKEN|DISCORD_CLIENT_SECRET)\s*[:=]\s*([^\s,;]+)",
            lambda match: f"{match.group(1)}=[REDACTED]",
            text,
        )
        text = re.sub(
            r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
            "[REDACTED JWT]",
            text,
        )
        return text

    async def operations(self, principal):
        runtime = get_runtime_health()
        database = self.db.health_snapshot()
        outbox = await self.db.fetchall("SELECT status,COUNT(*) AS c FROM discord_outbox GROUP BY status")
        incidents = await self.db.fetchall(
            "SELECT fingerprint,category,last_message,first_seen_ts,last_seen_ts,"
            "occurrence_count,last_correlation_id,status FROM error_incidents "
            "WHERE status='open' ORDER BY last_seen_ts DESC LIMIT 20"
        )
        request_state = await self.db.fetchone("SELECT state,wave_id,submitted_count,request_limit,close_ts FROM level_request_state WHERE guild_id=?", (principal.guild_id,))
        request_cog = self.bot.get_cog("RequestLevelsCog")
        provider_data = (
            request_cog.validation_provider_snapshot()
            if request_cog is not None
            and hasattr(request_cog, "validation_provider_snapshot")
            else {}
        )
        operations_cog = self.bot.get_cog("OperationsCog")
        tasks = (
            operations_cog.task_snapshot()
            if operations_cog is not None
            and hasattr(operations_cog, "task_snapshot")
            else runtime.get("tasks", {})
        )
        incident_items = []
        for row in incidents:
            error_type, summary = self._incident_summary(row["last_message"])
            incident_items.append(
                {
                    "fingerprint": str(row["fingerprint"]),
                    "component": str(row["category"] or "Component error")[:120],
                    "error_type": error_type,
                    "summary": summary,
                    "first_seen_ts": row["first_seen_ts"],
                    "last_seen_ts": row["last_seen_ts"],
                    "occurrence_count": int(row["occurrence_count"] or 0),
                    "correlation_id": str(row["last_correlation_id"] or ""),
                    "status": str(row["status"] or "open"),
                    "details_available": principal.can("audit.view_full"),
                }
            )
        outbox_counts = {
            str(row["status"]): int(row["c"] or 0) for row in outbox
        }
        return {
            "service": get_keepalive_status(),
            "runtime": runtime,
            "database": database,
            "outbox": {
                "pending": outbox_counts.get("pending", 0),
                "processing": outbox_counts.get("processing", 0),
                "dead": outbox_counts.get("dead", 0),
                "delivered": outbox_counts.get("delivered", 0),
            },
            "background_workers": tasks,
            "providers": provider_data,
            "request_wave": _row_dict(request_state),
            "pps_worker": str(tasks.get("priority.maintenance", "unknown")),
            "public_cache": {
                "state": "available"
                if self.bot.get_cog("PrioritySystemCog") is not None
                else "unavailable"
            },
            "incidents": incident_items,
        }

    async def incident_detail(
        self, principal: StaffPrincipal, fingerprint: str
    ) -> dict[str, Any]:
        if not principal.can("audit.view_full"):
            raise PortalError(
                403,
                "incident_detail_denied",
                "Full incident traces are limited to Owner and Dev",
            )
        row = await self.db.fetchone(
            "SELECT fingerprint,category,status,first_seen_ts,last_seen_ts,"
            "occurrence_count,last_message,last_correlation_id,resolved_ts "
            "FROM error_incidents WHERE fingerprint=?",
            (fingerprint,),
        )
        if row is None:
            raise PortalError(404, "incident_not_found", "That incident no longer exists")
        error_type, summary = self._incident_summary(row["last_message"])
        return {
            "incident": {
                "fingerprint": str(row["fingerprint"]),
                "component": str(row["category"] or "Component error")[:120],
                "error_type": error_type,
                "summary": summary,
                "trace": self._sanitize_incident_detail(row["last_message"]),
                "correlation_id": str(row["last_correlation_id"] or ""),
                "first_seen_ts": row["first_seen_ts"],
                "last_seen_ts": row["last_seen_ts"],
                "occurrence_count": int(row["occurrence_count"] or 0),
                "status": str(row["status"] or "open"),
                "resolved_ts": row["resolved_ts"],
            }
        }

    async def system(self, principal: StaffPrincipal) -> dict[str, Any]:
        principal.require("developer.access")
        operations = await self.operations(principal)
        schema_rows = await self.db.fetchall(
            "SELECT component,schema_version,updated_ts FROM schema_metadata "
            "ORDER BY component"
        )
        dead_rows = await self.db.fetchall(
            "SELECT id,correlation_id,action_type,attempts,created_ts,updated_ts,"
            "last_error FROM discord_outbox WHERE status='dead' "
            "ORDER BY updated_ts DESC LIMIT 50"
        )
        restore = await self.db.fetchone(
            "SELECT drill_ts,status,duration_ms,size_bytes,table_count,"
            "missing_tables_json,error_text,trigger FROM restore_drills "
            "ORDER BY drill_ts DESC LIMIT 1"
        )
        identity_repair_rows = await self.db.fetchall(
            "SELECT status,COUNT(*) AS count,COALESCE(SUM(rows_changed),0) AS rows_changed "
            "FROM staff_snowflake_repairs GROUP BY status ORDER BY status"
        )
        database = dict(operations["database"])
        database.pop("remote_url", None)
        database.pop("auth_token", None)
        return {
            **operations,
            "database": database,
            "schemas": [_row_dict(row) for row in schema_rows],
            "dead_outbox": [
                {
                    **_row_dict(row),
                    "last_error": self._sanitize_incident_detail(row["last_error"])[
                        :1000
                    ],
                }
                for row in dead_rows
            ],
            "last_restore_drill": _row_dict(restore),
            "identity_repairs": {
                str(row["status"]): {
                    "records": int(row["count"] or 0),
                    "rows_changed": int(row["rows_changed"] or 0),
                }
                for row in identity_repair_rows
            },
            "available_actions": [
                "restart_stopped_tasks",
                "rebuild_public_cache",
                "request_repair",
                "restore_drill",
            ],
        }

    async def system_action(
        self, principal: StaffPrincipal, payload: dict[str, Any]
    ) -> dict[str, Any]:
        principal.require("developer.access")
        action = str(payload.get("action") or "").strip().casefold()
        if payload.get("confirmed") is not True:
            raise PortalError(422, "confirmation_required", "Confirm the system action")
        reason = _bounded_text(payload.get("reason"), 1000, required=True)
        guild = self.bot.get_guild(principal.guild_id)
        if action == "restart_stopped_tasks":
            operations_cog = self.bot.get_cog("OperationsCog")
            if operations_cog is None:
                raise PortalError(503, "operations_unavailable", "Operations supervisor is unavailable")
            result = await operations_cog.restart_stopped_tasks()
        elif action == "rebuild_public_cache":
            priority_cog = self.bot.get_cog("PrioritySystemCog")
            if priority_cog is None:
                raise PortalError(503, "pps_unavailable", "PPS is unavailable")
            result = {"levels": await priority_cog.refresh_public_level_cache()}
        elif action == "request_repair":
            request_cog = self.bot.get_cog("RequestLevelsCog")
            if request_cog is None or guild is None:
                raise PortalError(503, "request_system_unavailable", "Request repair is unavailable")
            result = await request_cog.repair_request_system(guild)
        elif action == "restore_drill":
            principal.require("restore_drills.manage")
            operations_cog = self.bot.get_cog("OperationsCog")
            if operations_cog is None:
                raise PortalError(503, "operations_unavailable", "Restore drill is unavailable")
            result = await operations_cog.run_restore_drill(trigger="staff_portal")
        else:
            raise PortalError(400, "invalid_system_action", "Choose a supported recovery action")
        correlation = await self._record_admin_event(
            principal,
            f"system_{action}",
            entity_id="avenue-guard",
            payload={"reason": reason, "result": result},
        )
        return {"ok": True, "result": result, "correlation_id": correlation}

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
        identities = await self._resolve_identities(
            {int(row["actor_id"]) for row in rows if row["actor_id"] is not None}
        )
        items = []
        for row in rows:
            item = _row_dict(row)
            actor_id = row["actor_id"]
            item["actor"] = (
                identities.get(int(actor_id)) if actor_id is not None else None
            )
            items.append(item)
        return {"items": items}

    async def safe_configuration(self):
        saved = await self.db.get_runtime_setting("staff_portal.safe_config", {})
        saved = saved if isinstance(saved, dict) else {}
        saved_by_type = saved.get("application_open_by_type")
        saved_by_type = saved_by_type if isinstance(saved_by_type, dict) else {}
        configured_types = self._configured_application_types()
        return {
            "configuration": {
                "claim_stale_hours": int(
                    saved.get("claim_stale_hours")
                    or self.bot.config.get_int(
                        "staff_portal", "claim_stale_hours", default=48
                    )
                ),
                "applications_open": bool(saved.get("applications_open", True)),
                "application_open_by_type": {
                    application_type: bool(
                        saved_by_type.get(application_type, True)
                    )
                    for application_type in configured_types
                },
            }
        }

    async def update_safe_configuration(self, principal, payload):
        current = (await self.safe_configuration())["configuration"]
        if "claim_stale_hours" in payload:
            value = int(payload["claim_stale_hours"])
            if not 1 <= value <= 720:
                raise PortalError(400, "invalid_stale_threshold", "Stale threshold must be from 1 to 720 hours")
            current["claim_stale_hours"] = value
        if "applications_open" in payload:
            current["applications_open"] = bool(payload["applications_open"])
        if "application_open_by_type" in payload:
            incoming = payload["application_open_by_type"]
            if not isinstance(incoming, dict):
                raise PortalError(
                    400,
                    "invalid_application_availability",
                    "Application availability must be an object",
                )
            configured_types = set(self._configured_application_types())
            for application_type, is_open in incoming.items():
                if application_type not in configured_types or not isinstance(
                    is_open, bool
                ):
                    raise PortalError(
                        400,
                        "invalid_application_availability",
                        "Choose a configured application type and a valid open state",
                    )
                current["application_open_by_type"][application_type] = is_open
        await self.db.set_runtime_setting("staff_portal.safe_config", current)
        return {"configuration": current}

    async def search(self, principal, term):
        term = str(term or "").strip()[:100]
        if len(term) < 2:
            return {"items": []}
        like = f"%{term.casefold()}%"
        levels = await self.db.fetchall("SELECT id,level_id,current_level_name,uploader_name,queue_state FROM level_outreach_queue WHERE guild_id=? AND queue_state!='hidden' AND (level_id=? OR LOWER(COALESCE(current_level_name,'')) LIKE ? OR LOWER(COALESCE(uploader_name,'')) LIKE ?) ORDER BY updated_ts DESC LIMIT 8", (principal.guild_id, term, like, like))
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
                + self.bot.config.get_int_list("staff_portal", "admin_role_ids")
                + self.bot.config.get_int_list("staff_portal", "owner_role_ids")
            )
            owner_users = set(
                self.bot.config.get_int_list("staff_portal", "owner_user_ids")
            ) | set(self.bot.config.get_int_list("staff_portal", "dev_user_ids"))
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
