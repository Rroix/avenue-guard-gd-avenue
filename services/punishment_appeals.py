from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import discord

from utils.workflows import new_correlation_id, record_workflow_event

APPEAL_VERSION = "appeals-v1"
ACTIVE_APPEAL_STATUSES = {
    "draft",
    "submitted",
    "triage",
    "under_review",
    "awaiting_information",
    "second_review",
}
FINAL_APPEAL_STATUSES = {"decided", "withdrawn", "ineligible", "duplicate", "expired_no_action"}
GROUNDS = (
    "Facts are incorrect",
    "Context or evidence was missed",
    "The rule was applied incorrectly",
    "The punishment is disproportionate",
    "I am asking for reconsideration",
    "The wrong account was punished",
    "Technical or automated error",
    "Other",
)
OUTCOMES = (
    "Remove the punishment",
    "Reduce the punishment",
    "Correct the record",
    "Review the decision",
    "Other",
)
DECISIONS = {
    "upheld",
    "reduced",
    "removed",
    "record_corrected",
    "returned_for_reconsideration",
    "ineligible",
    "duplicate",
}


def _row(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _text(value: Any, limit: int, *, required: bool = False) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise ValueError("A required field is missing")
    if len(result) > limit:
        raise ValueError("A submitted field is too long")
    return result


class PunishmentAppealService:
    """Punishment evidence, appeals, and communication independent of staff hiring."""

    def __init__(self, portal, error_type):
        self.portal = portal
        self.bot = portal.bot
        self.db = portal.db
        self.error_type = error_type
        self._locks: dict[int, asyncio.Lock] = {}

    def _error(self, status: int, code: str, message: str):
        return self.error_type(status, code, message)

    def _lock(self, user_id: int) -> asyncio.Lock:
        return self._locks.setdefault(int(user_id), asyncio.Lock())

    @property
    def enabled(self) -> bool:
        return bool(
            self.bot.config.get("staff_portal", "appeals_enabled", default=True)
        )

    @property
    def snapshot_ttl(self) -> int:
        return max(
            60,
            min(
                3600,
                self.bot.config.get_int(
                    "staff_portal", "appeal_snapshot_ttl_seconds", default=900
                ),
            ),
        )

    def form_questions(self) -> list[dict[str, Any]]:
        return [
            {
                "key": "primary_ground",
                "label": "What is the main reason for your appeal?",
                "type": "single_choice",
                "options": list(GROUNDS),
                "required": True,
                "section": "Grounds",
                "guidance": "Choose the closest answer. This does not require you to admit wrongdoing.",
            },
            {
                "key": "chronology",
                "label": "Describe what happened in chronological order",
                "type": "long_text",
                "required": True,
                "section": "Your account",
                "guidance": "Include only details relevant to the punishment and identify what staff should verify.",
                "recommended_words": 180,
            },
            {
                "key": "disputed_detail",
                "label": "What part of the record or decision is inaccurate, and what should it say instead?",
                "type": "long_text",
                "required": False,
                "section": "Your account",
                "show_for": {
                    "primary_ground": [
                        "Facts are incorrect",
                        "Context or evidence was missed",
                        "The rule was applied incorrectly",
                        "The wrong account was punished",
                        "Technical or automated error",
                    ]
                },
            },
            {
                "key": "reconsideration",
                "label": "What has changed, and how would you prevent the same situation from happening again?",
                "type": "long_text",
                "required": False,
                "section": "Your account",
                "show_for": {"primary_ground": ["I am asking for reconsideration"]},
            },
            {
                "key": "evidence_links",
                "label": "Evidence links and what each one shows",
                "type": "long_text",
                "required": False,
                "section": "Evidence",
                "guidance": "Use one public or staff-accessible link per line. Do not include unrelated private information.",
            },
            {
                "key": "requested_outcome",
                "label": "What outcome are you requesting?",
                "type": "single_choice",
                "options": list(OUTCOMES),
                "required": True,
                "section": "Requested outcome",
            },
            {
                "key": "confirmation",
                "label": "Confirm the appeal is accurate and submitted in good faith",
                "type": "single_choice",
                "options": ["I confirm"],
                "required": True,
                "section": "Confirmation",
            },
        ]

    async def snapshot_punishment(self, principal, *, force: bool = False) -> dict[str, Any]:
        now = int(time.time())
        cached = await self.db.fetchone(
            "SELECT * FROM moderation_punishments WHERE guild_id=? AND user_id=? "
            "AND punishment_type='ban' ORDER BY checked_ts DESC,id DESC LIMIT 1",
            (principal.guild_id, principal.user_id),
        )
        if cached and not force and int(cached["checked_ts"] or 0) >= now - self.snapshot_ttl:
            return _row(cached)

        guild = self.bot.get_guild(principal.guild_id)
        if guild is None:
            raise self._error(503, "guild_unavailable", "GD Avenue is temporarily unavailable")

        live_reason: str | None = None
        audit_reason: str | None = None
        audit_entry_id: int | None = None
        issued_ts: int | None = None
        issued_by_id: int | None = None
        status = "found"
        error = None
        active = 1
        try:
            ban = await guild.fetch_ban(discord.Object(id=principal.user_id))
            live_reason = str(getattr(ban, "reason", "") or "").strip() or None
        except discord.NotFound:
            active = 0
            status = "not_banned"
        except discord.Forbidden:
            active = int(cached["active"] or 0) if cached else 0
            status = "forbidden"
            error = "Avenue Guard cannot read the guild ban record"
        except discord.HTTPException as exc:
            active = int(cached["active"] or 0) if cached else 0
            status = "unavailable"
            error = f"Discord ban lookup failed ({getattr(exc, 'status', 'network')})"

        if cached and status in {"forbidden", "unavailable"}:
            # Preserve the last verified evidence for context. Eligibility still
            # fails closed because this snapshot is not a fresh successful lookup.
            reason = cached["reason"]
            reason_source = cached["reason_source"]
            conflict = bool(cached["reason_conflict"])
            issued_ts = cached["issued_ts"]
            issued_by_id = cached["issued_by_id"]
            audit_entry_id = cached["audit_log_entry_id"]
        else:
            reason = live_reason or audit_reason
            reason_source = "discord_ban" if live_reason else "discord_audit_log" if audit_reason else "unknown"
            conflict = False

        if active and status == "found":
            try:
                async for entry in guild.audit_logs(
                    limit=1000, action=discord.AuditLogAction.ban
                ):
                    if int(getattr(getattr(entry, "target", None), "id", 0) or 0) != principal.user_id:
                        continue
                    audit_entry_id = int(entry.id)
                    created_at = getattr(entry, "created_at", None)
                    issued_ts = int(created_at.timestamp()) if created_at else None
                    issued_by_id = int(getattr(getattr(entry, "user", None), "id", 0) or 0) or None
                    audit_reason = str(getattr(entry, "reason", "") or "").strip() or None
                    break
            except (discord.Forbidden, discord.HTTPException):
                # Discord only retains audit entries for a limited period. The live ban remains valid.
                pass

        if status == "found":
            reason = live_reason or audit_reason
            reason_source = "discord_ban" if live_reason else "discord_audit_log" if audit_reason else "unknown"
            conflict = bool(
                live_reason
                and audit_reason
                and live_reason.casefold().strip() != audit_reason.casefold().strip()
            )
        if status in {"forbidden", "unavailable"}:
            source_key = f"lookup:{status}:{principal.user_id}"
        elif audit_entry_id:
            source_key = f"audit:{audit_entry_id}"
        elif status == "found":
            source_key = f"ban:{principal.user_id}:{issued_ts or 'unknown'}"
        elif status == "not_banned":
            source_key = f"not-banned:{principal.user_id}"
        else:
            source_key = f"lookup:{status}:{principal.user_id}"
        detail = {
            "live_reason": live_reason,
            "audit_reason": audit_reason,
            "audit_log_entry_found": bool(audit_entry_id),
            "evidence_reused_from_snapshot_id": int(cached["id"])
            if cached and status in {"forbidden", "unavailable"}
            else None,
            "sapphire_note": (
                "If Sapphire supplied Discord's audit-log reason while banning, it is represented by the Discord reason fields above. "
                "No private Sapphire case database was queried."
            ),
        }
        if status in {"found", "not_banned"}:
            await self.db.execute(
                "UPDATE moderation_punishments SET active=0,updated_ts=? WHERE guild_id=? AND user_id=? "
                "AND punishment_type='ban' AND source_key!=? AND active=1",
                (now, principal.guild_id, principal.user_id, source_key),
            )
        await self.db.execute(
            "INSERT INTO moderation_punishments(guild_id,user_id,punishment_type,source_key,active,reason,"
            "reason_source,reason_conflict,issued_ts,issued_by_id,audit_log_entry_id,external_source,"
            "source_detail_json,checked_ts,lookup_status,lookup_error,created_ts,updated_ts) "
            "VALUES(?,?,'ban',?,?,?,?,?,?,?,?, 'discord_observed',?,?,?,?,?,?) "
            "ON CONFLICT(guild_id,user_id,punishment_type,source_key) DO UPDATE SET "
            "active=excluded.active,reason=excluded.reason,reason_source=excluded.reason_source,"
            "reason_conflict=excluded.reason_conflict,issued_ts=excluded.issued_ts,"
            "issued_by_id=excluded.issued_by_id,audit_log_entry_id=excluded.audit_log_entry_id,"
            "source_detail_json=excluded.source_detail_json,checked_ts=excluded.checked_ts,"
            "lookup_status=excluded.lookup_status,lookup_error=excluded.lookup_error,updated_ts=excluded.updated_ts",
            (
                principal.guild_id,
                principal.user_id,
                source_key,
                active,
                reason,
                reason_source,
                1 if conflict else 0,
                issued_ts,
                issued_by_id,
                audit_entry_id,
                json.dumps(detail, separators=(",", ":"), ensure_ascii=False),
                now,
                status,
                error,
                now,
                now,
            ),
        )
        return _row(
            await self.db.fetchone(
                "SELECT * FROM moderation_punishments WHERE guild_id=? AND user_id=? "
                "AND punishment_type='ban' AND source_key=?",
                (principal.guild_id, principal.user_id, source_key),
            )
        )

    @staticmethod
    def applicant_punishment(row: Any) -> dict[str, Any]:
        data = _row(row)
        lookup_status = data.get("lookup_status")
        return {
            "id": data.get("id"),
            "type": data.get("punishment_type"),
            "active": bool(data.get("active"))
            if lookup_status in {"found", "not_banned", "unbanned_by_appeal"}
            else None,
            "reason": data.get("reason"),
            "reason_known": bool(data.get("reason")),
            "reason_source": data.get("reason_source"),
            "reason_conflict": bool(data.get("reason_conflict")),
            "issued_ts": data.get("issued_ts"),
            "checked_ts": data.get("checked_ts"),
            "lookup_status": lookup_status,
            "lookup_error": data.get("lookup_error"),
        }

    def _validate_answers(self, value: Any, *, submit: bool) -> dict[str, str]:
        if not isinstance(value, dict):
            raise self._error(400, "invalid_answers", "Appeal answers are missing")
        safe = {question["key"]: _text(value.get(question["key"]), 4000) for question in self.form_questions()}
        if safe["primary_ground"] and safe["primary_ground"] not in GROUNDS:
            raise self._error(400, "invalid_appeal_ground", "Choose a valid appeal ground")
        if safe["requested_outcome"] and safe["requested_outcome"] not in OUTCOMES:
            raise self._error(400, "invalid_appeal_outcome", "Choose a valid requested outcome")
        if submit:
            for key in ("primary_ground", "chronology", "requested_outcome", "confirmation"):
                if not safe[key]:
                    raise self._error(400, "incomplete_appeal", "Complete every required appeal field")
            if safe["confirmation"] != "I confirm":
                raise self._error(400, "confirmation_required", "Confirm the appeal before submitting")
            dispute = safe["primary_ground"] in {
                "Facts are incorrect",
                "Context or evidence was missed",
                "The rule was applied incorrectly",
                "The wrong account was punished",
                "Technical or automated error",
            }
            if dispute and not safe["disputed_detail"]:
                raise self._error(400, "incomplete_appeal", "Explain what part of the record should be corrected")
            if safe["primary_ground"] == "I am asking for reconsideration" and not safe["reconsideration"]:
                raise self._error(400, "incomplete_appeal", "Explain what has changed since the punishment")
            if len(safe["chronology"]) < 40:
                raise self._error(400, "appeal_too_short", "Give enough chronological detail for staff to review")
        return safe

    async def form(self, principal) -> dict[str, Any]:
        configuration = (await self.portal.safe_configuration())["configuration"]
        if not self.enabled or not configuration.get("appeals_open", True):
            raise self._error(409, "appeals_closed", "Punishment appeals are currently closed")
        punishment = await self.snapshot_punishment(principal)
        draft = await self.db.fetchone(
            "SELECT * FROM punishment_appeals WHERE guild_id=? AND appellant_id=? AND punishment_id=? "
            "AND status IN('draft','submitted','triage','under_review','awaiting_information','second_review') "
            "ORDER BY id DESC LIMIT 1",
            (principal.guild_id, principal.user_id, int(punishment["id"])),
        )
        cooldown = await self.db.fetchone(
            "SELECT cooldown_until_ts FROM punishment_appeal_cooldowns WHERE guild_id=? AND appellant_id=? "
            "AND punishment_id=? AND cooldown_until_ts>?",
            (principal.guild_id, principal.user_id, int(punishment["id"]), int(time.time())),
        )
        eligible = bool(punishment.get("active")) and punishment.get("lookup_status") == "found"
        return {
            "application": self.applicant_appeal(draft) if draft else None,
            "form": {
                "application_type": "appeal",
                "label": "Punishment appeal",
                "description": "Ask GD Avenue to review the evidence and decision behind your current server ban.",
                "version": APPEAL_VERSION,
                "estimated_minutes": 12,
                "sections": ["Grounds", "Your account", "Evidence", "Requested outcome", "Confirmation"],
                "form_kind": "punishment_appeal",
            },
            "questions": self.form_questions(),
            "punishment": self.applicant_punishment(punishment),
            "eligibility": {
                "eligible": eligible and cooldown is None,
                "reason": "active_ban" if eligible else str(punishment.get("lookup_status") or "unavailable"),
                "cooldown_until_ts": int(cooldown["cooldown_until_ts"]) if cooldown else None,
            },
        }

    @staticmethod
    def applicant_appeal(row: Any) -> dict[str, Any]:
        data = _row(row)
        if not data:
            return {}
        return {
            "id": data.get("id"),
            "application_type": "appeal",
            "application_label": "Punishment appeal",
            "record_kind": "punishment_appeal",
            "status": data.get("status"),
            "answers": _object(data.get("answers_json")),
            "created_ts": data.get("created_ts"),
            "updated_ts": data.get("updated_ts"),
            "submitted_ts": data.get("submitted_ts"),
            "decided_ts": data.get("decided_ts"),
            "outcome": data.get("outcome"),
            "applicant_message": data.get("applicant_explanation"),
            "unban_outbox_id": data.get("unban_outbox_id"),
        }

    async def mine(self, principal) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT a.*,p.punishment_type,p.active AS punishment_active,p.reason,p.reason_source,"
            "p.issued_ts,p.checked_ts,p.lookup_status,o.status AS unban_status "
            "FROM punishment_appeals a JOIN moderation_punishments p ON p.id=a.punishment_id "
            "LEFT JOIN discord_outbox o ON o.id=a.unban_outbox_id "
            "WHERE a.guild_id=? AND a.appellant_id=? ORDER BY a.updated_ts DESC LIMIT 20",
            (principal.guild_id, principal.user_id),
        )
        items = []
        for row in rows:
            item = self.applicant_appeal(row)
            lookup_status = row["lookup_status"]
            item["punishment"] = {
                "type": row["punishment_type"],
                "active": bool(row["punishment_active"])
                if lookup_status
                in {"found", "not_banned", "unbanned_by_appeal"}
                else None,
                "reason": row["reason"],
                "reason_source": row["reason_source"],
                "issued_ts": row["issued_ts"],
                "checked_ts": row["checked_ts"],
                "lookup_status": lookup_status,
            }
            item["unban_status"] = row["unban_status"]
            item["messages"] = await self.applicant_messages(int(row["id"]), principal.user_id)
            items.append(item)
        return items

    async def save(self, principal, payload: dict[str, Any], *, submit: bool) -> dict[str, Any]:
        configuration = (await self.portal.safe_configuration())["configuration"]
        if submit and (not self.enabled or not configuration.get("appeals_open", True)):
            raise self._error(409, "appeals_closed", "Punishment appeals are currently closed")
        async with self._lock(principal.user_id):
            punishment = await self.snapshot_punishment(principal, force=submit)
            if not punishment.get("active") or punishment.get("lookup_status") != "found":
                raise self._error(409, "no_active_ban", "No active GD Avenue ban could be verified for this Discord account")
            cooldown = await self.db.fetchone(
                "SELECT cooldown_until_ts FROM punishment_appeal_cooldowns WHERE guild_id=? AND appellant_id=? "
                "AND punishment_id=? AND cooldown_until_ts>?",
                (principal.guild_id, principal.user_id, int(punishment["id"]), int(time.time())),
            )
            if cooldown:
                raise self._error(429, "appeal_cooldown", "This punishment is still on appeal cooldown")
            answers = self._validate_answers(payload.get("answers"), submit=submit)
            existing = await self.db.fetchone(
                "SELECT * FROM punishment_appeals WHERE guild_id=? AND appellant_id=? AND punishment_id=? "
                "AND status IN('draft','submitted','triage','under_review','awaiting_information','second_review') "
                "ORDER BY id DESC LIMIT 1",
                (principal.guild_id, principal.user_id, int(punishment["id"])),
            )
            if existing and str(existing["status"]) != "draft":
                if submit:
                    return {"application": self.applicant_appeal(existing)}
                raise self._error(409, "appeal_active", "You already have an active appeal for this punishment")
            now = int(time.time())
            encoded = json.dumps(answers, separators=(",", ":"), ensure_ascii=False)
            status = "submitted" if submit else "draft"
            snapshot = json.dumps(
                self.applicant_punishment(punishment), separators=(",", ":"), ensure_ascii=False
            )
            if existing:
                appeal_id = int(existing["id"])
                await self.db.execute(
                    "UPDATE punishment_appeals SET answers_json=?,status=?,primary_ground=?,requested_outcome=?,"
                    "submitted_snapshot_json=CASE WHEN ? THEN ? ELSE submitted_snapshot_json END,"
                    "submitted_ts=CASE WHEN ? THEN ? ELSE submitted_ts END,updated_ts=? "
                    "WHERE id=? AND status='draft'",
                    (encoded, status, answers["primary_ground"], answers["requested_outcome"], 1 if submit else 0, snapshot, 1 if submit else 0, now, now, appeal_id),
                )
            else:
                appeal_id = await self.db.execute_insert(
                    "INSERT INTO punishment_appeals(guild_id,appellant_id,punishment_id,status,answers_json,"
                    "submitted_snapshot_json,primary_ground,requested_outcome,created_ts,updated_ts,submitted_ts,version) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (principal.guild_id, principal.user_id, int(punishment["id"]), status, encoded, snapshot if submit else None, answers["primary_ground"], answers["requested_outcome"], now, now, now if submit else None, APPEAL_VERSION),
                )
            if submit:
                await self.event(appeal_id, principal.user_id, "submitted", "draft", "submitted", {})
                channel_id = self.bot.config.get_int(
                    "channels", "appeals_log_channel_id", default=0
                )
                if channel_id:
                    await self.bot.outbox.enqueue(
                        "send_channel",
                        guild_id=principal.guild_id,
                        channel_id=channel_id,
                        user_id=principal.user_id,
                        payload={"content": f"New punishment appeal from <@{principal.user_id}>. Review it in the GD Avenue Staff Portal.", "allow_user_mention": False},
                        correlation_id=f"punishment-appeal:{appeal_id}",
                        idempotency_key=f"punishment-appeal:{appeal_id}:staff-notice",
                    )
            row = await self.db.fetchone("SELECT * FROM punishment_appeals WHERE id=?", (appeal_id,))
            return {"application": self.applicant_appeal(row)}

    async def withdraw(self, principal, appeal_id: int) -> dict[str, Any]:
        now = int(time.time())
        changed = await self.db.execute_affected(
            "UPDATE punishment_appeals SET status='withdrawn',updated_ts=? WHERE id=? AND guild_id=? "
            "AND appellant_id=? AND status IN('draft','submitted','triage','under_review','awaiting_information','second_review')",
            (now, appeal_id, principal.guild_id, principal.user_id),
        )
        if not changed:
            raise self._error(409, "cannot_withdraw", "That appeal cannot be withdrawn")
        await self.event(appeal_id, principal.user_id, "withdrawn", None, "withdrawn", {})
        return {"ok": True}

    async def applicant_messages(self, appeal_id: int, appellant_id: int) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT id,author_type,body,created_ts FROM punishment_appeal_messages WHERE appeal_id=? ORDER BY created_ts,id",
            (appeal_id,),
        )
        await self.db.execute(
            "UPDATE punishment_appeal_messages SET applicant_read_ts=COALESCE(applicant_read_ts,?) "
            "WHERE appeal_id=? AND author_type!='applicant'",
            (int(time.time()), appeal_id),
        )
        return [
            {
                "id": row["id"],
                "author": "You" if str(row["author_type"]) == "applicant" else "GD Avenue Appeals Team",
                "author_type": row["author_type"],
                "body": row["body"],
                "created_ts": row["created_ts"],
            }
            for row in rows
        ]

    async def applicant_message(self, principal, appeal_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        appeal = await self.db.fetchone(
            "SELECT id,status FROM punishment_appeals WHERE id=? AND guild_id=? AND appellant_id=?",
            (appeal_id, principal.guild_id, principal.user_id),
        )
        if not appeal:
            raise self._error(404, "appeal_not_found", "That appeal was not found")
        if str(appeal["status"]) == "withdrawn":
            raise self._error(409, "appeal_withdrawn", "A withdrawn appeal cannot receive new messages")
        body = _text(payload.get("body"), 1800, required=True)
        message_id = await self.db.execute_insert(
            "INSERT INTO punishment_appeal_messages(appeal_id,author_type,author_id,body,created_ts,staff_read_ts) "
            "VALUES(?,'applicant',?,?,?,?)",
            (appeal_id, principal.user_id, body, int(time.time()), None),
        )
        await self.event(appeal_id, principal.user_id, "applicant_message", str(appeal["status"]), str(appeal["status"]), {})
        return {"ok": True, "message_id": message_id}

    async def list_staff(self, principal, query: dict[str, str]) -> dict[str, Any]:
        principal.require("appeals.review")
        status = str(query.get("status") or "active").casefold()
        claim = str(query.get("claim") or "all").casefold()
        valid = ACTIVE_APPEAL_STATUSES | FINAL_APPEAL_STATUSES | {"decided"}
        if status not in valid | {"active", "all"}:
            raise self._error(400, "invalid_appeal_filter", "Choose a valid appeal status")
        conditions = ["a.guild_id=?", "a.status!='draft'"]
        params: list[Any] = [principal.guild_id]
        if status == "active":
            conditions.append("a.status IN('submitted','triage','under_review','awaiting_information','second_review')")
        elif status != "all":
            conditions.append("a.status=?")
            params.append(status)
        if claim == "claimed":
            conditions.append("a.claimed_by IS NOT NULL")
        elif claim == "unclaimed":
            conditions.append("a.claimed_by IS NULL")
        elif claim == "mine":
            conditions.append("a.claimed_by=?")
            params.append(principal.user_id)
        elif claim != "all":
            raise self._error(400, "invalid_appeal_filter", "Choose a valid claim filter")
        rows = await self.db.fetchall(
            "SELECT a.*,p.punishment_type,p.active AS punishment_active,p.reason,p.reason_source,p.reason_conflict,"
            "p.issued_ts,p.issued_by_id,p.audit_log_entry_id,p.checked_ts,p.lookup_status,p.lookup_error,"
            "p.source_detail_json,o.status AS unban_status,o.last_error AS unban_error "
            "FROM punishment_appeals a JOIN moderation_punishments p ON p.id=a.punishment_id "
            "LEFT JOIN discord_outbox o ON o.id=a.unban_outbox_id WHERE "
            + " AND ".join(conditions)
            + " ORDER BY COALESCE(a.submitted_ts,a.created_ts),a.id LIMIT 200",
            params,
        )
        identity_ids = set()
        for row in rows:
            for key in ("appellant_id", "claimed_by", "decided_by", "issued_by_id"):
                if row[key] is not None:
                    identity_ids.add(int(row[key]))
        identities = await self.portal._resolve_identities(identity_ids)
        items = []
        for row in rows:
            appeal_id = int(row["id"])
            messages = [
                _row(message)
                for message in await self.db.fetchall(
                    "SELECT id,author_type,author_id,body,created_ts,dm_outbox_id FROM punishment_appeal_messages "
                    "WHERE appeal_id=? ORDER BY created_ts,id",
                    (appeal_id,),
                )
            ]
            unread = await self.db.fetchone(
                "SELECT COUNT(*) AS total FROM punishment_appeal_messages "
                "WHERE appeal_id=? AND author_type='applicant' AND staff_read_ts IS NULL",
                (appeal_id,),
            )
            assessments = []
            for assessment in await self.db.fetchall(
                "SELECT * FROM punishment_appeal_assessments WHERE appeal_id=? ORDER BY created_ts,id",
                (appeal_id,),
            ):
                item = _row(assessment)
                item["findings"] = _object(item.pop("findings_json", "{}"))
                reviewer_id = int(item["reviewer_id"])
                item["reviewer"] = identities.get(reviewer_id)
                if item["reviewer"] is None:
                    item["reviewer"] = await self.portal._resolve_identity(reviewer_id)
                assessments.append(item)
            events = []
            for event in await self.db.fetchall(
                "SELECT actor_id,event,from_status,to_status,detail_json,created_ts FROM punishment_appeal_events "
                "WHERE appeal_id=? ORDER BY created_ts,id",
                (appeal_id,),
            ):
                item = _row(event)
                item["detail"] = _object(item.pop("detail_json", "{}"))
                events.append(item)
            item = _row(row)
            item["answers"] = _object(item.pop("answers_json", "{}"))
            item["submitted_snapshot"] = _object(item.pop("submitted_snapshot_json", "{}"))
            item["source_detail"] = _object(item.pop("source_detail_json", "{}"))
            item["appellant"] = identities.get(int(row["appellant_id"]))
            item["claimed_by_identity"] = identities.get(int(row["claimed_by"])) if row["claimed_by"] else None
            item["issued_by_identity"] = identities.get(int(row["issued_by_id"])) if row["issued_by_id"] else None
            item["messages"] = messages
            item["staff_unread_count"] = int(unread["total"] or 0) if unread else 0
            item["assessments"] = assessments
            item["events"] = events
            item["decision_ready"] = self._decision_ready(row, assessments)
            items.append(item)
        return {"items": items}

    @staticmethod
    def _decision_ready(appeal: Any, assessments: list[dict[str, Any]]) -> dict[str, Any]:
        issuer = int(appeal["issued_by_id"] or 0)
        eligible = {
            int(item["reviewer_id"])
            for item in assessments
            if not int(item.get("recused") or 0) and int(item["reviewer_id"]) != issuer
        }
        return {
            "ready": len(eligible) >= 2,
            "eligible_assessments": len(eligible),
            "minimum": 2,
            "issuer_excluded": bool(issuer),
        }

    async def assessment(self, principal, appeal_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        principal.require("appeals.review")
        appeal = await self.db.fetchone(
            "SELECT a.*,p.issued_by_id FROM punishment_appeals a JOIN moderation_punishments p ON p.id=a.punishment_id "
            "WHERE a.id=? AND a.guild_id=?",
            (appeal_id, principal.guild_id),
        )
        if not appeal:
            raise self._error(404, "appeal_not_found", "That appeal was not found")
        if str(appeal["status"]) not in ACTIVE_APPEAL_STATUSES - {"draft"}:
            raise self._error(
                409,
                "appeal_not_reviewable",
                "Only an active submitted appeal can be assessed",
            )
        if int(appeal["issued_by_id"] or 0) == principal.user_id:
            raise self._error(409, "issuer_conflict", "The staff member who issued the ban cannot assess its appeal")
        recommendation = _text(payload.get("recommendation"), 80, required=True).casefold()
        if recommendation not in DECISIONS:
            raise self._error(400, "invalid_recommendation", "Choose a valid appeal recommendation")
        rationale = _text(payload.get("rationale"), 3000, required=True)
        findings = payload.get("findings")
        if not isinstance(findings, dict):
            raise self._error(400, "invalid_findings", "Record the evidence findings")
        allowed = {"factual_accuracy", "rule_applicability", "proportionality", "consistency", "new_evidence", "current_risk"}
        clean = {key: _text(value, 1000) for key, value in findings.items() if key in allowed}
        now = int(time.time())
        await self.db.execute(
            "INSERT INTO punishment_appeal_assessments(appeal_id,reviewer_id,findings_json,recommendation,rationale,created_ts,updated_ts) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(appeal_id,reviewer_id) DO UPDATE SET findings_json=excluded.findings_json,"
            "recommendation=excluded.recommendation,rationale=excluded.rationale,recused=0,updated_ts=excluded.updated_ts",
            (appeal_id, principal.user_id, json.dumps(clean, separators=(",", ":"), ensure_ascii=False), recommendation, rationale, now, now),
        )
        await self.event(appeal_id, principal.user_id, "assessment_saved", str(appeal["status"]), str(appeal["status"]), {"recommendation": recommendation})
        return {"ok": True}

    async def staff_message(self, principal, appeal_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        principal.require("appeals.review")
        appeal = await self.db.fetchone(
            "SELECT appellant_id,status FROM punishment_appeals WHERE id=? AND guild_id=?",
            (appeal_id, principal.guild_id),
        )
        if not appeal:
            raise self._error(404, "appeal_not_found", "That appeal was not found")
        body = _text(payload.get("body"), 1800, required=True)
        now = int(time.time())
        message_id = await self.db.execute_insert(
            "INSERT INTO punishment_appeal_messages(appeal_id,author_type,author_id,body,created_ts,staff_read_ts) "
            "VALUES(?,'staff',?,?,?,?)",
            (appeal_id, principal.user_id, body, now, now),
        )
        dm_outbox_id = None
        if payload.get("notify_dm") is True:
            dm_outbox_id = await self.bot.outbox.enqueue(
                "send_dm",
                guild_id=principal.guild_id,
                user_id=int(appeal["appellant_id"]),
                payload={"content": f"GD Avenue Appeals Team: {body}\n\nReply securely at https://gdavenue.netlify.app/apply/?type=appeal"},
                correlation_id=f"punishment-appeal:{appeal_id}",
                idempotency_key=f"punishment-appeal:{appeal_id}:message:{message_id}:dm",
            )
            await self.db.execute(
                "UPDATE punishment_appeal_messages SET dm_outbox_id=? WHERE id=?",
                (dm_outbox_id, message_id),
            )
        await self.event(appeal_id, principal.user_id, "staff_message", str(appeal["status"]), str(appeal["status"]), {"dm_requested": bool(dm_outbox_id)})
        return {"ok": True, "message_id": message_id, "dm_outbox_id": dm_outbox_id}

    async def action(self, principal, appeal_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        principal.require("appeals.review")
        appeal = await self.db.fetchone(
            "SELECT a.*,p.issued_by_id,p.active AS punishment_active FROM punishment_appeals a "
            "JOIN moderation_punishments p ON p.id=a.punishment_id WHERE a.id=? AND a.guild_id=?",
            (appeal_id, principal.guild_id),
        )
        if not appeal:
            raise self._error(404, "appeal_not_found", "That appeal was not found")
        action = str(payload.get("action") or "").casefold()
        old = str(appeal["status"])
        now = int(time.time())
        if action == "mark_read":
            await self.db.execute(
                "UPDATE punishment_appeal_messages SET staff_read_ts=COALESCE(staff_read_ts,?) "
                "WHERE appeal_id=? AND author_type='applicant'",
                (now, appeal_id),
            )
            return {"ok": True}
        if action == "claim":
            if old not in ACTIVE_APPEAL_STATUSES - {"draft"}:
                raise self._error(409, "appeal_not_active", "That appeal is not active")
            changed = await self.db.execute_affected(
                "UPDATE punishment_appeals SET claimed_by=COALESCE(claimed_by,?),status=CASE WHEN status='submitted' THEN 'triage' ELSE status END,"
                "first_review_ts=COALESCE(first_review_ts,?),updated_ts=? WHERE id=? AND (claimed_by IS NULL OR claimed_by=?)",
                (principal.user_id, now, now, appeal_id, principal.user_id),
            )
            if not changed:
                raise self._error(409, "appeal_claimed", "Another staff member already claimed this appeal")
            await self.event(appeal_id, principal.user_id, "claimed", old, "triage" if old == "submitted" else old, {})
            return {"ok": True}
        if action == "recuse":
            if old not in ACTIVE_APPEAL_STATUSES - {"draft"}:
                raise self._error(409, "appeal_not_active", "That appeal is not active")
            await self.db.execute(
                "UPDATE punishment_appeals SET claimed_by=CASE WHEN claimed_by=? THEN NULL ELSE claimed_by END,updated_ts=? WHERE id=?",
                (principal.user_id, now, appeal_id),
            )
            await self.db.execute(
                "UPDATE punishment_appeal_assessments SET recused=1,updated_ts=? WHERE appeal_id=? AND reviewer_id=?",
                (now, appeal_id, principal.user_id),
            )
            await self.event(appeal_id, principal.user_id, "recused", old, old, {})
            return {"ok": True}
        if action in {"review", "second_review"}:
            if old not in ACTIVE_APPEAL_STATUSES - {"draft"}:
                raise self._error(409, "appeal_not_active", "That appeal is not active")
            target = "under_review" if action == "review" else "second_review"
            await self.db.execute(
                "UPDATE punishment_appeals SET status=?,first_review_ts=COALESCE(first_review_ts,?),updated_ts=? WHERE id=?",
                (target, now, now, appeal_id),
            )
            await self.event(appeal_id, principal.user_id, action, old, target, {})
            return {"ok": True}
        if action == "request_information":
            if old not in ACTIVE_APPEAL_STATUSES - {"draft"}:
                raise self._error(409, "appeal_not_active", "That appeal is not active")
            body = _text(payload.get("body"), 1800, required=True)
            await self.staff_message(principal, appeal_id, {"body": body, "notify_dm": payload.get("notify_dm") is True})
            await self.db.execute("UPDATE punishment_appeals SET status='awaiting_information',updated_ts=? WHERE id=?", (now, appeal_id))
            await self.event(appeal_id, principal.user_id, "information_requested", old, "awaiting_information", {})
            return {"ok": True}
        if action == "reopen":
            principal.require("appeals.execute")
            if old not in FINAL_APPEAL_STATUSES:
                raise self._error(
                    409,
                    "appeal_not_final",
                    "Only a finalized appeal can be reopened",
                )
            await self.db.execute(
                "UPDATE punishment_appeals SET status='second_review',decided_ts=NULL,"
                "decided_by=NULL,outcome='',internal_rationale='',applicant_explanation='',updated_ts=? "
                "WHERE id=?",
                (now, appeal_id),
            )
            await self.db.execute(
                "DELETE FROM punishment_appeal_cooldowns "
                "WHERE guild_id=? AND appellant_id=? AND punishment_id=?",
                (
                    principal.guild_id,
                    int(appeal["appellant_id"]),
                    int(appeal["punishment_id"]),
                ),
            )
            await self.event(appeal_id, principal.user_id, "reopened", old, "second_review", {})
            return {"ok": True}
        if action != "decide":
            raise self._error(400, "invalid_appeal_action", "Choose a valid appeal action")

        principal.require("appeals.execute")
        if old not in ACTIVE_APPEAL_STATUSES - {"draft"}:
            raise self._error(409, "appeal_not_active", "That appeal is not active")
        outcome = str(payload.get("outcome") or "").casefold()
        if outcome not in DECISIONS:
            raise self._error(400, "invalid_appeal_outcome", "Choose a valid appeal outcome")
        rationale = _text(payload.get("internal_rationale"), 3000, required=True)
        explanation = _text(payload.get("applicant_explanation"), 1800, required=True)
        assessments = [
            _row(item)
            for item in await self.db.fetchall(
                "SELECT * FROM punishment_appeal_assessments WHERE appeal_id=?",
                (appeal_id,),
            )
        ]
        ready = self._decision_ready(appeal, assessments)
        if not ready["ready"]:
            raise self._error(409, "second_review_required", "Two independent non-conflicted assessments are required before deciding a ban appeal")
        if int(appeal["issued_by_id"] or 0) == principal.user_id:
            raise self._error(409, "issuer_conflict", "The staff member who issued the ban cannot make the final appeal decision")
        unban_outbox_id = int(appeal["unban_outbox_id"] or 0) or None
        if outcome == "removed" and payload.get("execute_unban") is True:
            unban_outbox_id = await self.bot.outbox.enqueue(
                "unban_member",
                guild_id=principal.guild_id,
                user_id=int(appeal["appellant_id"]),
                payload={"appeal_id": appeal_id, "punishment_id": int(appeal["punishment_id"]), "reason": f"Punishment appeal approved by staff ({appeal_id})"},
                correlation_id=f"punishment-appeal:{appeal_id}",
                idempotency_key=f"punishment-appeal:{appeal_id}:unban",
            )
        await self.db.execute(
            "UPDATE punishment_appeals SET status='decided',outcome=?,internal_rationale=?,applicant_explanation=?,"
            "decided_by=?,decided_ts=?,updated_ts=?,unban_outbox_id=? WHERE id=?",
            (outcome, rationale, explanation, principal.user_id, now, now, unban_outbox_id, appeal_id),
        )
        await self.db.execute_insert(
            "INSERT INTO punishment_appeal_messages(appeal_id,author_type,author_id,body,created_ts,staff_read_ts) VALUES(?,'system',?,?,?,?)",
            (appeal_id, principal.user_id, explanation, now, now),
        )
        if outcome == "upheld":
            await self.db.execute(
                "INSERT INTO punishment_appeal_cooldowns(guild_id,appellant_id,punishment_id,cooldown_until_ts,source,created_ts,updated_ts) "
                "VALUES(?,?,?,?, 'upheld',?,?) ON CONFLICT(guild_id,appellant_id,punishment_id) DO UPDATE SET "
                "cooldown_until_ts=excluded.cooldown_until_ts,source=excluded.source,updated_ts=excluded.updated_ts",
                (principal.guild_id, int(appeal["appellant_id"]), int(appeal["punishment_id"]), now + 30 * 86400, now, now),
            )
        await self.event(appeal_id, principal.user_id, "decided", old, "decided", {"outcome": outcome, "unban_queued": bool(unban_outbox_id)})
        return {"ok": True, "outcome": outcome, "unban_outbox_id": unban_outbox_id}

    async def event(self, appeal_id: int, actor_id: int | None, event: str, old: str | None, new: str | None, detail: dict[str, Any]) -> None:
        now = int(time.time())
        correlation = new_correlation_id("punishment-appeal")
        await self.db.execute(
            "INSERT INTO punishment_appeal_events(appeal_id,actor_id,event,from_status,to_status,detail_json,created_ts,correlation_id) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (appeal_id, actor_id, event, old, new, json.dumps(detail, separators=(",", ":"), ensure_ascii=False), now, correlation),
        )
        appeal = await self.db.fetchone("SELECT guild_id FROM punishment_appeals WHERE id=?", (appeal_id,))
        if appeal:
            await record_workflow_event(
                self.db,
                workflow_type="punishment_appeal",
                entity_id=str(appeal_id),
                event=event,
                correlation_id=correlation,
                guild_id=int(appeal["guild_id"]),
                actor_id=actor_id,
                payload={"from": old, "to": new, **detail},
            )
