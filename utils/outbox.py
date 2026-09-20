from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any

import discord

from utils.mentions import no_mentions
from utils.workflows import OUTBOX_STATES, new_correlation_id, record_workflow_event

SUPPORTED_ACTIONS = {
    "send_channel",
    "send_dm",
    "edit_message",
    "delete_message",
    "add_role",
    "remove_role",
    "create_application_thread",
    "create_interview_ticket",
}


class PermanentOutboxError(RuntimeError):
    pass


class DiscordOutbox:
    """Durable, retryable Discord side effects stored in the primary database."""

    def __init__(self, bot, *, max_attempts: int = 8):
        self.bot = bot
        self.max_attempts = max(1, min(20, int(max_attempts)))
        self._worker_lock = asyncio.Lock()
        self._delivery_receipts: dict[int, int] = {}
        self._last_dead_letter_ids: list[int] = []

    def last_dead_letter_ids(self) -> tuple[int, ...]:
        return tuple(self._last_dead_letter_ids)

    async def enqueue(
        self,
        action_type: str,
        *,
        payload: dict[str, Any] | None = None,
        guild_id: int = 0,
        channel_id: int = 0,
        user_id: int = 0,
        message_id: int = 0,
        correlation_id: str = "",
        idempotency_key: str = "",
    ) -> int:
        action = str(action_type).strip().casefold()
        if action not in SUPPORTED_ACTIONS:
            raise ValueError(f"unsupported outbox action: {action}")
        correlation = str(correlation_id or new_correlation_id("outbox"))
        key = str(idempotency_key or f"{correlation}:{action}")[:180]
        now = int(time.time())
        await self.bot.db.execute(
            "INSERT OR IGNORE INTO discord_outbox(correlation_id,idempotency_key,action_type,guild_id,channel_id,user_id,message_id,payload_json,status,attempts,next_attempt_ts,created_ts,updated_ts) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                correlation,
                key,
                action,
                int(guild_id or 0),
                int(channel_id or 0),
                int(user_id or 0),
                int(message_id or 0),
                json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=False),
                "pending",
                0,
                now,
                now,
                now,
            ),
        )
        row = await self.bot.db.fetchone(
            "SELECT id FROM discord_outbox WHERE idempotency_key=?",
            (key,),
        )
        return int(row["id"] or 0) if row else 0

    async def recover_stale(self, *, stale_seconds: int = 120) -> int:
        cutoff = int(time.time()) - max(30, int(stale_seconds))
        stale = await self.bot.db.fetchone("SELECT 1 FROM discord_outbox WHERE status='processing' AND updated_ts<? LIMIT 1", (cutoff,))
        if stale is None:
            return 0
        return await self.bot.db.execute_affected(
            "UPDATE discord_outbox SET status='pending',next_attempt_ts=?,updated_ts=?,last_error='worker interrupted before completion' "
            "WHERE status='processing' AND updated_ts<?",
            (int(time.time()), int(time.time()), cutoff),
        )

    async def process_once(self, *, limit: int = 10) -> dict[str, int]:
        result = {"delivered": 0, "retried": 0, "dead": 0}
        if self._worker_lock.locked():
            return result
        async with self._worker_lock:
            self._last_dead_letter_ids = []
            if self._delivery_receipts:
                receipt_ids = list(self._delivery_receipts)
                for start in range(0, len(receipt_ids), 100):
                    chunk = receipt_ids[start:start + 100]
                    placeholders = ",".join("?" for _ in chunk)  # Only placeholders are interpolated; IDs remain bound.
                    confirmed = await self.bot.db.fetchall(
                        f"SELECT id FROM discord_outbox WHERE id IN ({placeholders}) AND status IN ('delivered','dead')",  # nosec B608
                        tuple(chunk),
                    )
                    for receipt in confirmed:
                        self._delivery_receipts.pop(int(receipt["id"]), None)
            now = int(time.time())
            rows = await self.bot.db.fetchall(
                "SELECT * FROM discord_outbox WHERE status IN ('pending','failed') AND next_attempt_ts<=? "
                "ORDER BY created_ts ASC,id ASC LIMIT ?",
                (now, max(1, min(50, int(limit)))),
            )
            for row in rows:
                outcome = await self._process_row(row)
                result[outcome] += 1
                if outcome == "dead":
                    self._last_dead_letter_ids.append(int(row["id"]))
        return result

    async def _process_row(self, row) -> str:
        outbox_id = int(row["id"])
        source = str(row["status"])
        OUTBOX_STATES.require(source, "processing")
        now = int(time.time())
        claimed_count = await self.bot.db.execute_affected(
            "UPDATE discord_outbox SET status='processing',attempts=attempts+1,updated_ts=? "
            "WHERE id=? AND status=?",
            (now, outbox_id, source),
        )
        if claimed_count != 1:
            return "retried"
        claimed = await self.bot.db.fetchone(
            "SELECT * FROM discord_outbox WHERE id=? AND status='processing'",
            (outbox_id,),
        )
        if claimed is None:
            return "retried"
        attempts = int(claimed["attempts"] or 1)
        try:
            if outbox_id in self._delivery_receipts:
                delivered_message_id = self._delivery_receipts[outbox_id]
            else:
                delivered_message_id = await asyncio.wait_for(self._deliver(claimed), timeout=45)
                self._delivery_receipts[outbox_id] = int(delivered_message_id or 0)
        except Exception as exc:  # noqa: BLE001 - all delivery failures must be durably retried or dead-lettered.
            terminal = self._terminal_failure(exc) or attempts >= self.max_attempts
            target = "dead" if terminal else "pending"
            OUTBOX_STATES.require("processing", target)
            delay = 0 if terminal else min(3600, 5 * (2 ** min(attempts - 1, 9)))
            await self.bot.db.execute(
                "UPDATE discord_outbox SET status=?,next_attempt_ts=?,updated_ts=?,last_error=? WHERE id=?",
                (
                    target,
                    int(time.time()) + delay,
                    int(time.time()),
                    f"{type(exc).__name__}: {exc}"[:1000],
                    outbox_id,
                ),
            )
            await record_workflow_event(
                self.bot.db,
                workflow_type="discord_outbox",
                entity_id=str(outbox_id),
                event=target,
                correlation_id=str(claimed["correlation_id"] or ""),
                guild_id=int(claimed["guild_id"] or 0),
                payload={
                    "action": claimed["action_type"],
                    "attempts": attempts,
                    "error": str(exc)[:300],
                },
            )
            return "dead" if terminal else "retried"
        OUTBOX_STATES.require("processing", "delivered")
        await self.bot.db.execute(
            "UPDATE discord_outbox SET status='delivered',delivered_ts=?,updated_ts=?,"
            "delivered_message_id=?,last_error=NULL WHERE id=?",
            (
                int(time.time()),
                int(time.time()),
                int(delivered_message_id or 0) or None,
                outbox_id,
            ),
        )
        self._delivery_receipts.pop(outbox_id, None)
        await record_workflow_event(
            self.bot.db,
            workflow_type="discord_outbox",
            entity_id=str(outbox_id),
            event="delivered",
            correlation_id=str(claimed["correlation_id"] or ""),
            guild_id=int(claimed["guild_id"] or 0),
            payload={"action": claimed["action_type"], "attempts": attempts},
        )
        return "delivered"

    @staticmethod
    def _terminal_failure(exc: Exception) -> bool:
        code = int(getattr(exc, "code", 0) or 0)
        return isinstance(
            exc, (discord.Forbidden, discord.NotFound, PermanentOutboxError)
        ) or code in {10003, 10008, 10013, 10014}

    @staticmethod
    def _payload(row) -> dict[str, Any]:
        try:
            value = json.loads(str(row["payload_json"] or "{}"))
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _embed(payload: dict[str, Any]) -> discord.Embed | None:
        value = payload.get("embed")
        return discord.Embed.from_dict(value) if isinstance(value, dict) else None

    @staticmethod
    def _mentions(payload: dict[str, Any]) -> discord.AllowedMentions:
        if payload.get("allow_user_mention"):
            return discord.AllowedMentions(
                users=True, roles=False, everyone=False, replied_user=False
            )
        return no_mentions()

    async def _channel(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(channel_id)
        return channel

    async def _guild(self, guild_id: int):
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            guild = await self.bot.fetch_guild(guild_id)
        return guild

    async def _deliver(self, row) -> int:
        action = str(row["action_type"])
        payload = self._payload(row)
        channel_id = int(row["channel_id"] or 0)
        user_id = int(row["user_id"] or 0)
        message_id = int(row["message_id"] or 0)
        content = str(payload.get("content") or "")[:2000] or None
        embed = self._embed(payload)
        mentions = self._mentions(payload)
        nonce = hashlib.sha256(str(row["idempotency_key"]).encode()).hexdigest()[:24]

        if action == "create_application_thread":
            application_id = int(payload.get("application_id") or 0)
            application = await self.bot.db.fetchone(
                "SELECT review_thread_id,applicant_id,application_type FROM staff_applications WHERE id=? AND guild_id=?",
                (application_id, int(row["guild_id"] or 0)),
            )
            if application is None:
                raise PermanentOutboxError("application no longer exists")
            thread_id = int(application["review_thread_id"] or 0)
            thread = self.bot.get_channel(thread_id) if thread_id else None
            if thread is None and thread_id:
                try:
                    thread = await self.bot.fetch_channel(thread_id)
                except discord.NotFound:
                    thread = None
            if thread is None:
                destination = await self._channel(channel_id)
                application_type = str(application["application_type"] or "judge")
                application_label = str(
                    payload.get("application_label")
                    or ("Reviewer application" if application_type == "judge" else f"{application_type.replace('_', ' ').title()} application")
                )[:100]
                submitted_ts = int(payload.get("submitted_ts") or 0)
                thread_name = (
                    f"{application_type}-application-"
                    f"{int(application['applicant_id'])}-{submitted_ts or 'submitted'}"
                )[:100]
                existing = next(
                    (
                        item
                        for item in getattr(destination, "threads", ())
                        if str(getattr(item, "name", "")) == thread_name
                    ),
                    None,
                )
                if existing is not None:
                    thread = existing
                else:
                    applicant_id = int(application["applicant_id"])
                    review_url = str(payload.get("review_url") or "").strip()
                    starter_parts = [
                        f"## New {application_label.lower()} by <@{applicant_id}>",
                    ]
                    if submitted_ts:
                        starter_parts.append(f"**Submitted:** <t:{submitted_ts}:F> (<t:{submitted_ts}:R>)")
                    if review_url:
                        starter_parts.append(f"[Open this application in the Staff Portal](<{review_url}>)")
                    starter_parts.append("The submitted questions and answers are copied below.")
                    starter = "\n".join(starter_parts)[:2000]
                    reason = f"{application_label} submitted"
                    if isinstance(destination, discord.ForumChannel):
                        created = await destination.create_thread(
                            name=thread_name,
                            content=starter,
                            allowed_mentions=no_mentions(),
                            nonce=nonce,
                            reason=reason,
                        )
                        thread = getattr(created, "thread", created)
                    elif isinstance(destination, discord.TextChannel):
                        notification = await destination.send(
                            content=starter,
                            allowed_mentions=no_mentions(),
                            nonce=nonce,
                            enforce_nonce=True,
                        )
                        thread = await destination.create_thread(
                            name=thread_name,
                            message=notification,
                            reason=reason,
                        )
                    else:
                        raise PermanentOutboxError(
                            "application review channel must be a forum or text channel "
                            f"(received {type(destination).__name__})"
                        )
                thread_id = int(getattr(thread, "id", 0) or 0)
                if not thread_id:
                    raise RuntimeError("Discord did not return the application thread")
                await self.bot.db.execute(
                    "UPDATE staff_applications SET review_thread_id=?,updated_ts=? WHERE id=?",
                    (thread_id, int(time.time()), application_id),
                )
            for index, response in enumerate(payload.get("responses") or []):
                if not isinstance(response, dict):
                    continue
                question = str(response.get("question") or "Question")[:500]
                answer = str(response.get("answer") or "No answer provided")
                chunks = [answer[start:start + 1750] for start in range(0, len(answer), 1750)] or ["No answer provided"]
                for chunk_index, chunk in enumerate(chunks):
                    label = f"**{question}**\n" if chunk_index == 0 else f"**{question} (continued)**\n"
                    answer_nonce = hashlib.sha256(f"application:{application_id}:{index}:{chunk_index}".encode()).hexdigest()[:24]
                    await thread.send(
                        content=f"{label}{chunk}"[:2000],
                        allowed_mentions=no_mentions(),
                        nonce=answer_nonce,
                        enforce_nonce=True,
                    )
            return thread_id

        if action == "create_interview_ticket":
            application_id = int(payload.get("application_id") or 0)
            application = await self.bot.db.fetchone(
                "SELECT applicant_id,application_type,interview_ticket_channel_id FROM staff_applications WHERE id=? AND guild_id=?",
                (application_id, int(row["guild_id"] or 0)),
            )
            if application is None:
                raise PermanentOutboxError("application no longer exists")
            saved_channel_id = int(application["interview_ticket_channel_id"] or 0)
            guild = await self._guild(int(row["guild_id"] or 0))
            applicant_id = int(application["applicant_id"])
            application_type = str(application["application_type"] or "judge")
            application_label = str(
                payload.get("application_label")
                or ("Reviewer application" if application_type == "judge" else f"{application_type.replace('_', ' ').title()} application")
            )[:100]
            interview_run_id = str(payload.get("interview_run_id") or "").strip()[:80]
            repeat_interview = payload.get("repeat_interview") is True
            member = guild.get_member(applicant_id)
            if member is None:
                member = await guild.fetch_member(applicant_id)
            category_id = self.bot.config.get_int("tickets", "ticket_category_id", default=0)
            category = guild.get_channel(category_id)
            if not isinstance(category, discord.CategoryChannel):
                raise PermanentOutboxError("ticket category is unavailable")
            marker = f"avenue-application-interview:{application_id}"
            if interview_run_id:
                marker = f"{marker}:{interview_run_id}"
            interview = (
                guild.get_channel(saved_channel_id)
                if saved_channel_id and not repeat_interview
                else None
            )
            if interview is None and saved_channel_id and not repeat_interview:
                try:
                    interview = await self.bot.fetch_channel(saved_channel_id)
                except discord.NotFound:
                    interview = None
            if interview is None:
                interview = next((item for item in getattr(guild, "channels", ()) if str(getattr(item, "topic", "")) == marker), None)
            if interview is None:
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
                }
                for role_id in {
                    *self.bot.config.get_int_list("staff_portal", "judge_role_ids"),
                    *self.bot.config.get_int_list("staff_portal", "head_judge_role_ids"),
                    *self.bot.config.get_int_list("staff_portal", "admin_role_ids"),
                    *self.bot.config.get_int_list("staff_portal", "mod_role_ids"),
                }:
                    role = guild.get_role(role_id)
                    if role is not None:
                        overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
                interview = await guild.create_text_channel(
                    name=f"{application_type}-interview-{getattr(member, 'name', 'applicant')}"[:90],
                    category=category,
                    topic=marker,
                    overwrites=overwrites,
                    reason=f"{application_label} interview",
                )
            now = int(time.time())
            ticket = await self.bot.db.fetchone("SELECT ticket_id,opening_message_id FROM tickets WHERE channel_id=?", (int(interview.id),))
            if ticket is None:
                ticket_id = await self.bot.db.next_ticket_id(int(row["guild_id"] or 0))
                await self.bot.db.execute(
                    "INSERT INTO tickets(guild_id,channel_id,creator_id,created_ts,last_user_activity_ts,status,ticket_id,status_tag,correlation_id) "
                    "VALUES(?,?,?,?,?,'open',?,'waiting_staff',?)",
                    (
                        int(row["guild_id"] or 0),
                        int(interview.id),
                        applicant_id,
                        now,
                        now,
                        ticket_id,
                        interview_run_id or f"staff-application:{application_id}",
                    ),
                )
                opening_message_id = 0
            else:
                opening_message_id = int(ticket["opening_message_id"] or 0)
            if not opening_message_id:
                opening = await interview.send(
                    content=f"Welcome {member.mention}. This private channel is your GD Avenue {application_label} interview.\nStatus: **Waiting for staff**",
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False, replied_user=False),
                    nonce=hashlib.sha256(
                        f"application:{application_id}:{interview_run_id or 'initial'}:interview-opening".encode()
                    ).hexdigest()[:24],
                    enforce_nonce=True,
                )
                await self.bot.db.execute(
                    "UPDATE tickets SET opening_message_id=? WHERE channel_id=?",
                    (int(opening.id), int(interview.id)),
                )
            await self.bot.db.execute(
                "UPDATE staff_applications SET interview_ticket_channel_id=?,updated_ts=? WHERE id=?",
                (int(interview.id), now, application_id),
            )
            await self.enqueue(
                "send_dm",
                guild_id=int(row["guild_id"] or 0),
                user_id=applicant_id,
                payload={
                    "content": (
                        f"Your GD Avenue {application_label} is moving to "
                        f"{'another ' if repeat_interview else 'an '}interview: {interview.mention}"
                    )
                },
                correlation_id=interview_run_id or f"staff-application:{application_id}",
                idempotency_key=(
                    f"{interview_run_id}:interview-dm"
                    if interview_run_id
                    else f"staff-application:{application_id}:interview-dm"
                ),
            )
            help_cog = self.bot.get_cog("HelpCog")
            if help_cog is not None and hasattr(help_cog, "_active_ticket_channels"):
                help_cog._active_ticket_channels.add(int(interview.id))
            return int(interview.id)

        if action == "send_channel":
            channel = await self._channel(channel_id)
            sent = await channel.send(
                content=content, embed=embed, allowed_mentions=mentions,
                nonce=nonce, enforce_nonce=True,
            )
            return int(getattr(sent, "id", 0) or 0)
        if action == "send_dm":
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            sent = await user.send(
                content=content, embed=embed, allowed_mentions=no_mentions(),
                nonce=nonce, enforce_nonce=True,
            )
            return int(getattr(sent, "id", 0) or 0)
        if action in {"edit_message", "delete_message"}:
            guard = payload.get("request_validation_guard")
            if action == "edit_message" and isinstance(guard, dict):
                kind = guard.get("kind")
                if kind not in {"wave", "weekly"} or not isinstance(guard.get("data_json"), str):
                    raise PermanentOutboxError("invalid request validation guard")
                cog = self.bot.get_cog("RequestLevelsCog")
                if cog is None:
                    raise RuntimeError("request validation delivery is waiting for its cog")
                table = "weekly_request_reviews" if kind == "weekly" else "level_request_submissions"
                async with cog._review_lock:
                    current = await self.bot.db.fetchone(
                        f"SELECT * FROM {table} WHERE guild_id=? AND request_message_id=? AND status='pending' AND data_json=?",  # nosec B608
                        (int(row["guild_id"]), message_id, guard["data_json"]),
                    )
                    if current is None:
                        return message_id
                    from utils.views import request_review_view
                    channel = await self._channel(channel_id)
                    message = await asyncio.wait_for(channel.fetch_message(message_id), timeout=15)
                    review_version = (
                        "legacy"
                        if kind == "weekly"
                        else str(current["review_system_version"] or "legacy")
                    )
                    await asyncio.wait_for(message.edit(content=content, embed=embed, allowed_mentions=mentions,
                                                        view=request_review_view(review_version)), timeout=15)
                return message_id
            channel = await self._channel(channel_id)
            try:
                message = await channel.fetch_message(message_id)
            except discord.NotFound:
                if action == "delete_message":
                    return
                raise
            if action == "delete_message":
                await message.delete()
            else:
                options = {}
                if payload.get("request_review_disabled"):
                    from utils.views import request_review_view
                    options["view"] = request_review_view(
                        str(payload.get("request_review_version") or "legacy"),
                        disabled=True,
                    )
                await message.edit(
                    content=content, embed=embed, allowed_mentions=mentions, **options
                )
            return message_id

        guild = await self._guild(int(row["guild_id"] or 0))
        member = guild.get_member(user_id)
        if member is None:
            member = await guild.fetch_member(user_id)
        role_id = int(payload.get("role_id") or 0)
        role = guild.get_role(role_id)
        if role is None:
            raise PermanentOutboxError("configured role is unavailable")
        reason = str(payload.get("reason") or "Avenue Guard durable workflow")[:512]
        if action == "add_role":
            await member.add_roles(role, reason=reason)
        elif action == "remove_role":
            await member.remove_roles(role, reason=reason)
        return 0
