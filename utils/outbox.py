from __future__ import annotations

import asyncio
import json
import hashlib
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
        except Exception as exc:
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
