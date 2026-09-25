from __future__ import annotations

import json
import time
from typing import Any

from utils.workflows import new_correlation_id


MAJOR_EVENTS = {
    "reached_moderator",
    "recommendation_changed",
    "requeued",
    "rated_observed",
    "outcome_window_closed",
}
FINAL_EVENTS = {"rated_observed", "outcome_window_closed", "withdrawn", "invalid"}


class LevelNotificationService:
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db

    async def subscribe(
        self,
        guild_id: int,
        level_id: str,
        user_id: int,
        *,
        source: str = "manual",
        major_updates: bool = True,
        priority_band_changes: bool = False,
        final_outcome: bool = True,
    ) -> None:
        now = int(time.time())
        await self.db.execute(
            "INSERT INTO level_notification_subscriptions(guild_id,level_id,user_id,source,major_updates,priority_band_changes,final_outcome,active,created_ts,updated_ts) "
            "VALUES(?,?,?,?,?,?,?,1,?,?) ON CONFLICT(guild_id,level_id,user_id) DO UPDATE SET source=excluded.source,major_updates=excluded.major_updates,priority_band_changes=excluded.priority_band_changes,final_outcome=excluded.final_outcome,active=1,updated_ts=excluded.updated_ts",
            (guild_id, str(level_id), user_id, str(source)[:30], int(major_updates), int(priority_band_changes), int(final_outcome), now, now),
        )

    async def subscribe_requester(self, guild_id: int, level_id: str, user_id: int) -> bool:
        row = await self.db.fetchone(
            "SELECT request_result_mode,level_major_updates,level_priority_band_changes,level_final_outcomes "
            "FROM user_notification_preferences WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )
        if row and str(row["request_result_mode"] or "") == "none":
            return False
        await self.subscribe(
            guild_id,
            level_id,
            user_id,
            source="requester",
            major_updates=bool(int(row["level_major_updates"])) if row else True,
            priority_band_changes=bool(int(row["level_priority_band_changes"])) if row else False,
            final_outcome=bool(int(row["level_final_outcomes"])) if row else True,
        )
        return True

    async def unsubscribe(self, guild_id: int, level_id: str, user_id: int) -> bool:
        now = int(time.time())
        changed = await self.db.execute_affected(
            "UPDATE level_notification_subscriptions SET active=0,updated_ts=? WHERE guild_id=? AND level_id=? AND user_id=? AND active=1",
            (now, guild_id, str(level_id), user_id),
        )
        return bool(changed)

    async def subscriptions(self, guild_id: int, user_id: int) -> list[Any]:
        return await self.db.fetchall(
            "SELECT level_id,source,major_updates,priority_band_changes,final_outcome,created_ts "
            "FROM level_notification_subscriptions WHERE guild_id=? AND user_id=? AND active=1 ORDER BY updated_ts DESC",
            (guild_id, user_id),
        )

    def _public_url(self, level_id: str) -> str:
        raw = self.bot.config.data.get("priority_system")
        raw = raw if isinstance(raw, dict) else {}
        base = str(raw.get("public_level_base_url") or "https://gdavenue.netlify.app/level").rstrip("/")
        return f"{base}/{level_id}"

    async def emit(
        self,
        guild_id: int,
        level_id: str,
        event_type: str,
        event_key: str,
        message: str,
        *,
        queue_id: int | None = None,
        episode_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> int:
        event_type = str(event_type)
        if event_type not in MAJOR_EVENTS | FINAL_EVENTS | {"priority_band_changed"}:
            raise ValueError("Unknown level notification event")
        now = int(time.time())
        correlation = new_correlation_id("level-notification")
        inserted = await self.db.execute_affected(
            "INSERT OR IGNORE INTO level_notification_events(guild_id,level_id,queue_id,episode_id,event_type,event_key,payload_json,created_ts) VALUES(?,?,?,?,?,?,?,?)",
            (guild_id, str(level_id), queue_id, episode_id, event_type, str(event_key)[:200], json.dumps(payload or {}, separators=(",", ":")), now),
        )
        if not inserted:
            return 0
        event = await self.db.fetchone(
            "SELECT id FROM level_notification_events WHERE event_key=?", (str(event_key)[:200],)
        )
        if not event:
            return 0
        event_id = int(event["id"])
        rows = await self.db.fetchall(
            "SELECT s.user_id,s.major_updates,s.priority_band_changes,s.final_outcome,"
            "COALESCE(p.request_result_mode,'dm') AS request_result_mode,"
            "COALESCE(p.level_major_updates,1) AS pref_major,COALESCE(p.level_priority_band_changes,0) AS pref_priority,COALESCE(p.level_final_outcomes,1) AS pref_final "
            "FROM level_notification_subscriptions s LEFT JOIN user_notification_preferences p ON p.guild_id=s.guild_id AND p.user_id=s.user_id "
            "WHERE s.guild_id=? AND s.level_id=? AND s.active=1",
            (guild_id, str(level_id)),
        )
        sent = 0
        for row in rows:
            if str(row["request_result_mode"] or "").casefold() == "none":
                continue
            allowed = (
                event_type == "priority_band_changed" and int(row["priority_band_changes"] or 0) and int(row["pref_priority"] or 0)
            ) or (
                event_type in FINAL_EVENTS and int(row["final_outcome"] or 0) and int(row["pref_final"] or 0)
            ) or (
                event_type in MAJOR_EVENTS and int(row["major_updates"] or 0) and int(row["pref_major"] or 0)
            )
            if not allowed:
                continue
            user_id = int(row["user_id"])
            body = f"{str(message)[:1500]}\n[View the level page]({self._public_url(str(level_id))})"
            idempotency_key = f"level-notification:{event_key}:{user_id}"
            await self.db.execute(
                "INSERT OR IGNORE INTO discord_outbox(correlation_id,idempotency_key,action_type,guild_id,channel_id,user_id,payload_json,status,attempts,next_attempt_ts,created_ts,updated_ts) "
                "VALUES(?,?,'send_dm',?,0,?,?,'pending',0,?,?,?)",
                (correlation, idempotency_key, guild_id, user_id, json.dumps({"content": body}, separators=(",", ":")), now, now, now),
            )
            outbox = await self.db.fetchone(
                "SELECT id,status,delivered_message_id,last_error FROM discord_outbox WHERE idempotency_key=?",
                (idempotency_key,),
            )
            await self.db.execute(
                "INSERT OR IGNORE INTO level_notification_deliveries(event_id,user_id,outbox_id,status,delivered_message_id,failure_reason,created_ts,updated_ts) VALUES(?,?,?,?,?,?,?,?)",
                (
                    event_id, user_id, int(outbox["id"]) if outbox else None,
                    str(outbox["status"] if outbox else "queued"),
                    outbox["delivered_message_id"] if outbox else None,
                    outbox["last_error"] if outbox else None, now, now,
                ),
            )
            sent += 1
        return sent

    async def sync_delivery_states(self) -> int:
        now = int(time.time())
        return int(
            await self.db.execute_affected(
                "UPDATE level_notification_deliveries SET "
                "status=COALESCE((SELECT status FROM discord_outbox o WHERE o.id=level_notification_deliveries.outbox_id),status),"
                "delivered_message_id=(SELECT delivered_message_id FROM discord_outbox o WHERE o.id=level_notification_deliveries.outbox_id),"
                "failure_reason=(SELECT last_error FROM discord_outbox o WHERE o.id=level_notification_deliveries.outbox_id),updated_ts=? "
                "WHERE outbox_id IS NOT NULL AND EXISTS(SELECT 1 FROM discord_outbox o WHERE o.id=level_notification_deliveries.outbox_id AND (o.status!=level_notification_deliveries.status OR COALESCE(o.delivered_message_id,0)!=COALESCE(level_notification_deliveries.delivered_message_id,0) OR COALESCE(o.last_error,'')!=COALESCE(level_notification_deliveries.failure_reason,'')))",
                (now,),
            )
            or 0
        )
