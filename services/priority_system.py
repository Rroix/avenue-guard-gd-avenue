from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from services.bayesian_models import BayesianModelService, observed_rating_tier
from services.creator_points import CreatorPointsResolver
from services.historical_audit import normalize_level_snapshot
from services.level_notifications import LevelNotificationService
from utils.errors import log_error
from utils.priority_system import (
    PPS_ATTEMPT_STATUSES,
    PPS_QUEUE_ACTIVE_STATES,
    PPS_QUEUE_REFRESH_STATES,
    PPS_ROUTE_TYPES,
    PPS_V1_REVIEW_SYSTEM,
    normalize_outreach_target,
    normalize_send_type,
    priority_settings,
    score_components,
)
from utils.workflows import new_correlation_id


class PrioritySystemService:
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self._cycle_lock = asyncio.Lock()
        self._refresh_locks: dict[int, asyncio.Lock] = {}
        self._level_locks: dict[str, asyncio.Lock] = {}
        self._account_locks: dict[int, asyncio.Lock] = {}
        self.models = BayesianModelService(bot)
        self.notifications = LevelNotificationService(bot)
        self.creator_points = CreatorPointsResolver(bot, self)

    @property
    def settings(self):
        return priority_settings(self.bot.config.data)

    async def ensure_queue_for_submission(self, row) -> bool:
        if (
            str(row["review_system_version"] or "") != PPS_V1_REVIEW_SYSTEM
            or str(row["status"] or "") != "reviewed"
            or str(row["result"] or "") != "sent"
        ):
            return False
        send_type = normalize_send_type(row["send_type"])
        if send_type is None:
            return False
        settings = self.settings
        score = score_components(send_type, None, 0, settings)
        now = int(time.time())
        correlation = str(row["correlation_id"] or new_correlation_id("pps-repair"))
        changed = await self.db.execute_affected(
            "INSERT OR IGNORE INTO level_outreach_queue("
            "guild_id,wave_id,requester_id,request_message_id,level_id,send_type,queued_ts,"
            "prestige_t,prestige_component_f,waiting_cycles,waiting_component_h,priority_complete,"
            "model_version,queue_state,correlation_id,updated_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                int(row["guild_id"]),
                int(row["wave_id"]),
                int(row["user_id"]),
                int(row["request_message_id"]),
                str(row["level_id"]),
                send_type,
                int(row["reviewed_ts"] or now),
                score["prestige_t"],
                score["prestige_component_f"],
                0,
                score["waiting_component_h"],
                0,
                settings.model_version,
                "queued",
                correlation,
                now,
            ),
        )
        if changed:
            queue_id = await self._queue_id_for_message(int(row["guild_id"]), int(row["request_message_id"]))
            await self.creator_points.enqueue(queue_id, priority=100)
            await self.notifications.subscribe_requester(
                int(row["guild_id"]), str(row["level_id"]), int(row["user_id"])
            )
            await self._event(
                "queue_repaired",
                queue_id=queue_id,
                actor_id=0,
                guild_id=int(row["guild_id"]),
                correlation_id=correlation,
                payload={"source": "request_repair"},
            )
        return bool(changed)

    async def _queue_id_for_message(self, guild_id: int, message_id: int) -> int:
        row = await self.db.fetchone(
            "SELECT id FROM level_outreach_queue WHERE guild_id=? AND request_message_id=?",
            (guild_id, message_id),
        )
        return int(row["id"] or 0) if row else 0

    async def _event(
        self,
        event: str,
        *,
        queue_id: int = 0,
        cycle_id: int = 0,
        actor_id: int = 0,
        guild_id: int = 0,
        correlation_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        entity = f"queue:{queue_id}" if queue_id else f"cycle:{cycle_id}"
        await self.db.execute(
            "INSERT INTO workflow_events(correlation_id,workflow_type,entity_id,event,guild_id,actor_id,payload_json,created_ts) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                correlation_id or new_correlation_id("pps"),
                "priority_system",
                entity,
                event,
                guild_id or None,
                actor_id or None,
                json.dumps(payload or {}, separators=(",", ":")),
                int(time.time()),
            ),
        )

    async def queue_rows(
        self,
        guild_id: int,
        *,
        page: int = 1,
        page_size: int = 10,
        states: tuple[str, ...] = PPS_QUEUE_ACTIVE_STATES,
    ) -> tuple[list[Any], int]:
        placeholders = ",".join("?" for _ in states)
        params = (guild_id, *states)
        count = await self.db.fetchone(
            f"SELECT COUNT(*) AS c FROM level_outreach_queue WHERE guild_id=? AND queue_state IN ({placeholders})",  # nosec B608
            params,
        )
        total = int(count["c"] or 0) if count else 0
        page_size = max(1, min(20, int(page_size)))
        page = max(1, int(page))
        rows = await self.db.fetchall(
            f"SELECT * FROM level_outreach_queue WHERE guild_id=? AND queue_state IN ({placeholders}) "  # nosec B608
            "ORDER BY CASE WHEN priority_complete=1 THEN 0 ELSE 1 END, priority_points DESC, "
            "waiting_cycles DESC, queued_ts ASC, id ASC LIMIT ? OFFSET ?",
            (*params, page_size, (page - 1) * page_size),
        )
        return rows, total

    async def queue_entry(
        self, guild_id: int, identity: int | str, *, include_hidden: bool = False
    ):
        text = str(identity or "").strip()
        visible = "" if include_hidden else " AND queue_state!='hidden'"
        if text.isascii() and text.isdecimal():
            row = await self.db.fetchone(
                f"SELECT * FROM level_outreach_queue WHERE guild_id=? AND id=?{visible}",  # nosec B608
                (guild_id, int(text)),
            )
            if row:
                return row
        return await self.db.fetchone(
            f"SELECT * FROM level_outreach_queue WHERE guild_id=? AND level_id=?{visible} "  # nosec B608
            "ORDER BY queued_ts DESC LIMIT 1",
            (guild_id, text),
        )

    async def active_cycle(self, guild_id: int):
        return await self.db.fetchone(
            "SELECT * FROM level_outreach_cycles WHERE guild_id=? AND status='active' ORDER BY id DESC LIMIT 1",
            (guild_id,),
        )

    async def start_cycle(self, guild_id: int, actor_id: int, notes: str = ""):
        async with self._cycle_lock:
            if await self.active_cycle(guild_id):
                raise ValueError("An outreach cycle is already active")
            now = int(time.time())
            correlation = new_correlation_id("pps-cycle")
            statements = [
                (
                    "INSERT OR IGNORE INTO level_outreach_cycles(guild_id,status,started_by,started_ts,notes,correlation_id) "
                    "VALUES(?,'active',?,?,?,?)",
                    (guild_id, actor_id, now, str(notes or "")[:2000], correlation),
                ),
                (
                    "INSERT OR IGNORE INTO level_outreach_cycle_entries("
                    "cycle_id,queue_id,priority_points_snapshot,priority_complete_snapshot,"
                    "prestige_component_f_snapshot,creator_component_g_snapshot,waiting_component_h_snapshot,"
                    "creator_points_snapshot,waiting_cycles_snapshot,model_version_snapshot) "
                    "SELECT c.id,q.id,q.priority_points,q.priority_complete,q.prestige_component_f,"
                    "q.creator_component_g,q.waiting_component_h,q.current_creator_points,q.waiting_cycles,q.model_version "
                    "FROM level_outreach_cycles c JOIN level_outreach_queue q ON q.guild_id=c.guild_id "
                    "WHERE c.correlation_id=? AND c.status='active' AND q.queue_state='queued' AND q.priority_complete=1",
                    (correlation,),
                ),
                (
                    "UPDATE level_outreach_queue SET queue_state='in_cycle',updated_ts=? "
                    "WHERE id IN(SELECT e.queue_id FROM level_outreach_cycle_entries e "
                    "JOIN level_outreach_cycles c ON c.id=e.cycle_id WHERE c.correlation_id=? AND c.status='active') "
                    "AND queue_state='queued'",
                    (now, correlation),
                ),
                (
                    "INSERT INTO workflow_events(correlation_id,workflow_type,entity_id,event,guild_id,actor_id,payload_json,created_ts) "
                    "SELECT ?,'priority_system','cycle:'||id,'cycle_started',?,?,?,? FROM level_outreach_cycles c "
                    "WHERE c.correlation_id=? AND NOT EXISTS(SELECT 1 FROM workflow_events WHERE correlation_id=? AND event='cycle_started')",
                    (correlation, guild_id, actor_id, json.dumps({"notes": str(notes or "")[:500]}), now, correlation, correlation),
                ),
            ]
            await self.db.execute_transaction(statements, retry_safe=True)
            cycle = await self.db.fetchone(
                "SELECT * FROM level_outreach_cycles WHERE correlation_id=?", (correlation,)
            )
            if not cycle:
                raise ValueError("An outreach cycle became active before this one could start")
            try:
                candidate = await self.db.fetchone(
                    "SELECT COUNT(*) AS c FROM level_outreach_cycle_entries WHERE cycle_id=?",
                    (int(cycle["id"]),),
                )
                await self.models.capacity(
                    guild_id,
                    int(candidate["c"] or 0) if candidate else 0,
                    cycle_id=int(cycle["id"]),
                    persist=True,
                )
            except Exception as exc:
                await log_error(
                    self.bot,
                    f"Shadow capacity forecast deferred for cycle {int(cycle['id'])}: {exc!r}",
                )
            return cycle

    async def cycle_entries(self, cycle_id: int):
        return await self.db.fetchall(
            "SELECT e.*,q.level_id,q.send_type,q.queue_state,q.current_level_name "
            "FROM level_outreach_cycle_entries e JOIN level_outreach_queue q ON q.id=e.queue_id "
            "WHERE e.cycle_id=? AND q.queue_state!='hidden' "
            "ORDER BY e.priority_points_snapshot DESC,e.waiting_cycles_snapshot DESC,q.queued_ts,e.queue_id",
            (cycle_id,),
        )

    async def _ensure_episode_for_attempt(self, guild_id: int, queue_id: int, actor_id: int):
        existing = await self.db.fetchone(
            "SELECT * FROM staff_outreach_episodes WHERE guild_id=? AND queue_id=? "
            "AND status IN('active','awaiting_outcome') ORDER BY episode_number DESC LIMIT 1",
            (guild_id, queue_id),
        )
        if existing:
            return existing
        row = await self.db.fetchone(
            "SELECT COALESCE(MAX(episode_number),0) AS n FROM staff_outreach_episodes WHERE queue_id=?",
            (queue_id,),
        )
        now = int(time.time())
        episode_id = await self.db.execute_insert(
            "INSERT INTO staff_outreach_episodes(guild_id,queue_id,episode_number,status,started_by,started_ts) "
            "VALUES(?,?,?,'active',?,?)",
            (guild_id, queue_id, int(row["n"] or 0) + 1, actor_id, now),
        )
        return await self.db.fetchone("SELECT * FROM staff_outreach_episodes WHERE id=?", (episode_id,))

    async def record_attempt(
        self,
        guild_id: int,
        cycle_id: int,
        queue_id: int,
        actor_id: int,
        *,
        status: str,
        route_type: str,
        notes: str = "",
        target_label: str = "",
        idempotency_key: str,
        episode_id: int | None = None,
        event_ts: int | None = None,
    ):
        status = str(status or "").casefold()
        route_type = str(route_type or "").casefold()
        if status not in PPS_ATTEMPT_STATUSES:
            raise ValueError("Unknown outreach attempt status")
        if route_type not in PPS_ROUTE_TYPES:
            raise ValueError("Unknown outreach route")
        normalized_target = normalize_outreach_target(target_label)
        normalized_episode_id = int(episode_id or 0) or None
        recorded_ts = int(event_ts or time.time())
        if recorded_ts < 1 or recorded_ts > int(time.time()) + 300:
            raise ValueError("Outreach timestamp is invalid")
        attempt_key = str(idempotency_key)
        existing = await self.db.fetchone(
            "SELECT a.* FROM level_outreach_attempts a "
            "JOIN level_outreach_cycles c ON c.id=a.cycle_id "
            "WHERE a.idempotency_key=? AND c.guild_id=?",
            (attempt_key, guild_id),
        )
        if existing:
            if (
                int(existing["cycle_id"]) == cycle_id
                and int(existing["queue_id"]) == queue_id
                and str(existing["status"]) == status
                and str(existing["route_type"]) == route_type
            ):
                return existing
            raise ValueError("That outreach interaction was already used for another action")
        if normalized_episode_id is None:
            episode = await self._ensure_episode_for_attempt(guild_id, queue_id, actor_id)
            normalized_episode_id = int(episode["id"])
        if (
            status == "follow_up"
            or status == "submitted_to_mod"
        ) and not normalized_target:
            raise ValueError("Confirmed submissions and follow-ups require a private target")
        cycle = await self.db.fetchone(
            "SELECT * FROM level_outreach_cycles WHERE id=? AND guild_id=?",
            (cycle_id, guild_id),
        )
        if not cycle or str(cycle["status"]) != "active":
            raise ValueError("That outreach cycle is not active")
        member = await self.db.fetchone(
            "SELECT q.queue_state,q.priority_complete FROM level_outreach_cycle_entries e "
            "JOIN level_outreach_queue q ON q.id=e.queue_id "
            "WHERE e.cycle_id=? AND e.queue_id=?",
            (cycle_id, queue_id),
        )
        if not member:
            raise ValueError("That queue entry was not eligible when this cycle started")
        if not bool(member["priority_complete"]):
            raise ValueError("Priority is still being calculated")
        allowed_states = (
            {"in_cycle", "awaiting_outcome"}
            if status in {"submitted_to_mod", "follow_up"}
            else {"in_cycle"}
        )
        if str(member["queue_state"]) not in allowed_states:
            raise ValueError("That queue entry is no longer eligible in this cycle")
        if status in {"submitted_to_mod", "follow_up"} and normalized_episode_id:
            prior_submission = await self.db.fetchone(
                "SELECT id FROM level_outreach_attempts WHERE episode_id=? AND queue_id=? "
                "AND private_target_key=? AND status='submitted_to_mod' LIMIT 1",
                (normalized_episode_id, queue_id, normalized_target),
            )
            if status == "follow_up" and prior_submission is None:
                raise ValueError("A follow-up needs an earlier confirmed submission to the same target")
            if status == "submitted_to_mod" and prior_submission is not None:
                raise ValueError("This target already has a confirmed submission; record a follow-up instead")
        now = int(time.time())
        correlation = str(cycle["correlation_id"] or new_correlation_id("pps-outreach"))
        attempt_guard = (
            "SELECT 1 FROM level_outreach_attempts WHERE idempotency_key=? "
            "AND cycle_id=? AND queue_id=? AND status=?"
        )
        attempt_guard_params = (attempt_key, cycle_id, queue_id, status)
        statements: list[tuple[str, tuple[Any, ...]]] = [
            (
                "INSERT OR IGNORE INTO level_outreach_attempts(cycle_id,queue_id,actor_id,status,route_type,"
                "private_notes,private_target_label,created_ts,correlation_id,idempotency_key,episode_id,"
                "private_target_key,event_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    cycle_id,
                    queue_id,
                    actor_id,
                    status,
                    route_type,
                    str(notes or "")[:2000],
                    str(target_label or "")[:300],
                    now,
                    correlation,
                    attempt_key,
                    normalized_episode_id,
                    normalized_target,
                    recorded_ts,
                ),
            ),
            (
                "UPDATE level_outreach_cycle_entries SET selected=1 WHERE cycle_id=? AND queue_id=? "
                f"AND EXISTS({attempt_guard})",  # nosec B608
                (cycle_id, queue_id, *attempt_guard_params),
            ),
        ]
        if status == "submitted_to_mod":
            due_ts = recorded_ts + self.settings.outcome_window_seconds
            statements.extend(
                [
                    (
                        "UPDATE level_outreach_cycle_entries SET submitted_to_mod=1 WHERE cycle_id=? AND queue_id=? "
                        f"AND EXISTS({attempt_guard})",  # nosec B608
                        (cycle_id, queue_id, *attempt_guard_params),
                    ),
                    (
                        "UPDATE level_outreach_queue SET queue_state='awaiting_outcome',submitted_to_mod_ts=COALESCE(submitted_to_mod_ts,?),"
                        "outcome_window_due_ts=COALESCE(outcome_window_due_ts,?),updated_ts=? WHERE id=? AND queue_state IN('queued','in_cycle','awaiting_outcome') "
                        f"AND EXISTS({attempt_guard})",  # nosec B608
                        (recorded_ts, due_ts, now, queue_id, *attempt_guard_params),
                    ),
                ]
            )
        statements.append(
            (
                "INSERT INTO workflow_events(correlation_id,workflow_type,entity_id,event,guild_id,actor_id,payload_json,created_ts) "
                "SELECT ?,'priority_system',?,'outreach_'||?,?,?,?,? "
                f"WHERE EXISTS({attempt_guard}) "  # nosec B608
                "AND NOT EXISTS(SELECT 1 FROM workflow_events WHERE correlation_id=? AND entity_id=? AND event='outreach_'||?)",
                (
                    correlation,
                    f"queue:{queue_id}:attempt:{idempotency_key}",
                    status,
                    guild_id,
                    actor_id,
                    json.dumps(
                        {
                            "cycle_id": cycle_id,
                            "queue_id": queue_id,
                            "route_type": route_type,
                            "episode_id": normalized_episode_id,
                        }
                    ),
                    now,
                    *attempt_guard_params,
                    correlation,
                    f"queue:{queue_id}:attempt:{attempt_key}",
                    status,
                ),
            )
        )
        await self.db.execute_transaction(statements, retry_safe=True)
        saved = await self.db.fetchone(
            "SELECT * FROM level_outreach_attempts WHERE idempotency_key=? "
            "AND cycle_id=? AND queue_id=? AND status=?",
            attempt_guard_params,
        )
        if not saved:
            raise ValueError("That outreach interaction was already used for another action")
        try:
            await self.models.project_attempt(int(saved["id"]))
        except Exception as exc:  # Shadow evidence must never break live outreach recording.
            await log_error(
                self.bot,
                f"Bayesian opportunity projection deferred for attempt {int(saved['id'])}: {exc!r}",
            )
        try:
            queue = await self.db.fetchone(
                "SELECT level_id FROM level_outreach_queue WHERE id=?", (queue_id,)
            )
            if queue and status == "submitted_to_mod":
                event = "reached_moderator"
                message = "A confirmed moderator submission was recorded for your level."
                await self.notifications.emit(
                    guild_id,
                    str(queue["level_id"]),
                    event,
                    f"attempt:{int(saved['id'])}:{event}",
                    message,
                    queue_id=queue_id,
                    episode_id=normalized_episode_id,
                )
        except Exception as exc:  # Delivery is isolated from outreach truth.
            await log_error(self.bot, f"Level update notification deferred: {exc!r}")
        return saved

    async def complete_cycle(self, guild_id: int, cycle_id: int, actor_id: int):
        async with self._cycle_lock:
            cycle = await self.db.fetchone(
                "SELECT * FROM level_outreach_cycles WHERE id=? AND guild_id=?",
                (cycle_id, guild_id),
            )
            if not cycle:
                raise ValueError("Outreach cycle not found")
            if str(cycle["status"]) == "completed":
                return cycle, 0
            if str(cycle["status"]) != "active":
                raise ValueError("Only an active outreach cycle can be completed")
            entries = await self.db.fetchall(
                "SELECT e.*,q.send_type,q.current_creator_points,q.waiting_cycles,q.queue_state "
                "FROM level_outreach_cycle_entries e JOIN level_outreach_queue q ON q.id=e.queue_id "
                "WHERE e.cycle_id=? AND q.queue_state!='hidden'",
                (cycle_id,),
            )
            if not any(int(row["submitted_to_mod"] or 0) == 1 for row in entries):
                raise ValueError("This cycle has no confirmed moderator submission; cancel it as unsuccessful instead")
            now = int(time.time())
            statements: list[tuple[str, tuple[Any, ...]]] = []
            incremented = 0
            for row in entries:
                if int(row["submitted_to_mod"] or 0) or int(row["waiting_incremented"] or 0):
                    continue
                if str(row["queue_state"]) != "in_cycle":
                    continue
                waiting_cycles = int(row["waiting_cycles"] or 0) + 1
                score = score_components(
                    str(row["send_type"]),
                    row["current_creator_points"],
                    waiting_cycles,
                    self.settings,
                )
                statements.extend(
                    [
                        (
                            "UPDATE level_outreach_queue SET waiting_cycles=?,waiting_component_h=?,creator_component_g=?,"
                            "priority_points=?,priority_complete=?,queue_state='queued',updated_ts=? WHERE id=? AND queue_state='in_cycle' "
                            "AND EXISTS(SELECT 1 FROM level_outreach_cycle_entries e JOIN level_outreach_cycles c ON c.id=e.cycle_id "
                            "WHERE e.cycle_id=? AND e.queue_id=? AND e.waiting_incremented=0 AND e.submitted_to_mod=0 AND c.status='active')",
                            (
                                waiting_cycles,
                                score["waiting_component_h"],
                                score["creator_component_g"],
                                score["priority_points"],
                                score["priority_complete"],
                                now,
                                int(row["queue_id"]),
                                cycle_id,
                                int(row["queue_id"]),
                            ),
                        ),
                        (
                            "UPDATE level_outreach_cycle_entries SET waiting_incremented=1 WHERE cycle_id=? AND queue_id=? "
                            "AND waiting_incremented=0 AND submitted_to_mod=0 "
                            "AND EXISTS(SELECT 1 FROM level_outreach_cycles WHERE id=? AND status='active')",
                            (cycle_id, int(row["queue_id"]), cycle_id),
                        ),
                    ]
                )
                incremented += 1
            statements.extend(
                [
                    (
                        "UPDATE level_outreach_cycles SET status='completed',completed_by=?,completed_ts=?,successful=1 "
                        "WHERE id=? AND guild_id=? AND status='active'",
                        (actor_id, now, cycle_id, guild_id),
                    ),
                    (
                        "INSERT INTO workflow_events(correlation_id,workflow_type,entity_id,event,guild_id,actor_id,payload_json,created_ts) "
                        "SELECT correlation_id,'priority_system',?,'cycle_completed',?,?,?,? FROM level_outreach_cycles c "
                        "WHERE c.id=? AND c.status='completed' AND NOT EXISTS(SELECT 1 FROM workflow_events w WHERE w.correlation_id=c.correlation_id AND w.event='cycle_completed')",
                        (f"cycle:{cycle_id}", guild_id, actor_id, json.dumps({"waiting_incremented": incremented}), now, cycle_id),
                    ),
                ]
            )
            await self.db.execute_transaction(statements, retry_safe=True)
            actual = await self.db.fetchone(
                "SELECT COUNT(*) AS c FROM level_outreach_cycle_entries WHERE cycle_id=? AND submitted_to_mod=1",
                (cycle_id,),
            )
            await self.db.execute(
                "UPDATE bayes_capacity_forecasts SET actual_submissions=?,resolved_ts=? WHERE cycle_id=? AND actual_submissions IS NULL",
                (int(actual["c"] or 0) if actual else 0, now, cycle_id),
            )
            saved = await self.db.fetchone(
                "SELECT * FROM level_outreach_cycles WHERE id=?", (cycle_id,)
            )
            return saved, incremented

    async def cancel_cycle(self, guild_id: int, cycle_id: int, actor_id: int, notes: str = ""):
        async with self._cycle_lock:
            cycle = await self.db.fetchone(
                "SELECT * FROM level_outreach_cycles WHERE id=? AND guild_id=?",
                (cycle_id, guild_id),
            )
            if not cycle:
                raise ValueError("Outreach cycle not found")
            if str(cycle["status"]) == "cancelled":
                return cycle
            if str(cycle["status"]) != "active":
                raise ValueError("Only an active outreach cycle can be cancelled")
            now = int(time.time())
            correlation = str(cycle["correlation_id"])
            await self.db.execute_transaction(
                [
                    (
                        "UPDATE level_outreach_queue SET queue_state='queued',updated_ts=? WHERE queue_state='in_cycle' "
                        "AND id IN(SELECT queue_id FROM level_outreach_cycle_entries WHERE cycle_id=?)",
                        (now, cycle_id),
                    ),
                    (
                        "UPDATE level_outreach_cycles SET status='cancelled',completed_by=?,completed_ts=?,successful=0,"
                        "notes=CASE WHEN ?='' THEN notes ELSE ? END WHERE id=? AND guild_id=? AND status='active'",
                        (actor_id, now, str(notes or "")[:2000], str(notes or "")[:2000], cycle_id, guild_id),
                    ),
                    (
                        "INSERT INTO workflow_events(correlation_id,workflow_type,entity_id,event,guild_id,actor_id,payload_json,created_ts) "
                        "SELECT ?,'priority_system',?,'cycle_cancelled',?,?,?,? WHERE EXISTS(SELECT 1 FROM level_outreach_cycles WHERE id=? AND status='cancelled') "
                        "AND NOT EXISTS(SELECT 1 FROM workflow_events WHERE correlation_id=? AND event='cycle_cancelled')",
                        (correlation, f"cycle:{cycle_id}", guild_id, actor_id, json.dumps({"notes": str(notes or "")[:500]}), now, cycle_id, correlation),
                    ),
                ],
                retry_safe=True,
            )
            actual = await self.db.fetchone(
                "SELECT COUNT(*) AS c FROM level_outreach_cycle_entries WHERE cycle_id=? AND submitted_to_mod=1",
                (cycle_id,),
            )
            await self.db.execute(
                "UPDATE bayes_capacity_forecasts SET actual_submissions=?,resolved_ts=? WHERE cycle_id=? AND actual_submissions IS NULL",
                (int(actual["c"] or 0) if actual else 0, now, cycle_id),
            )
            return await self.db.fetchone(
                "SELECT * FROM level_outreach_cycles WHERE id=?", (cycle_id,)
            )

    async def _provider_level_snapshot(self, level_id: str) -> dict[str, Any]:
        cog = self.bot.get_cog("RequestLevelsCog")
        if cog is None:
            return {
                "level_id": level_id,
                "gd_checked_ts": int(time.time()),
                "current_exists": None,
                "current_rated": None,
                "gd_lookup_status": "cog_unavailable",
                "gd_lookup_error": "Request validation cog unavailable",
            }
        providers = cog._level_validation_providers()
        session = await cog._get_level_validation_session()
        results: dict[str, dict[str, Any]] = {}
        for provider in ("gdhistory", "gdrateplus", "boomlings", "gdbrowser"):
            if not providers.get(provider):
                continue
            try:
                results[provider] = await cog._fetch_validation_provider(
                    provider, session, level_id
                )
            except Exception as exc:
                results[provider] = {
                    "provider": provider,
                    "ok": False,
                    "exists": None,
                    "error": type(exc).__name__,
                }
        return normalize_level_snapshot(level_id, results)

    async def _save_level_snapshot(self, row, snapshot: dict[str, Any]) -> None:
        now = int(snapshot.get("gd_checked_ts") or time.time())
        lookup_ok = str(snapshot.get("gd_lookup_status") or "") == "ok"
        refresh_after = now + (
            self.settings.level_refresh_seconds
            if lookup_ok
            else self.settings.failure_retry_seconds
        )
        exists = snapshot.get("current_exists")
        rated = snapshot.get("current_rated")
        current_state = str(row["queue_state"])
        next_state = current_state
        rated_observed = row["rated_observed_ts"]
        if exists is False:
            next_state = "invalid"
        elif rated is True:
            next_state = "rated"
            rated_observed = rated_observed or now
        await self.db.execute_transaction(
            [
                (
                    "INSERT INTO level_outreach_level_snapshots(queue_id,checked_ts,current_exists,current_rated,stars,"
                    "uploader_name,uploader_user_id,uploader_account_id,lookup_status,error_text,current_featured,current_epic,"
                    "current_epic_tier_raw,current_legendary,current_mythic) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        int(row["id"]),
                        now,
                        None if exists is None else int(bool(exists)),
                        None if rated is None else int(bool(rated)),
                        snapshot.get("current_stars"),
                        snapshot.get("current_uploader_name"),
                        snapshot.get("current_uploader_user_id"),
                        snapshot.get("current_uploader_account_id"),
                        str(snapshot.get("gd_lookup_status") or "unavailable"),
                        str(snapshot.get("gd_lookup_error") or "")[:500] or None,
                        snapshot.get("current_featured"),
                        snapshot.get("current_epic"),
                        snapshot.get("current_epic_tier_raw"),
                        snapshot.get("current_legendary"),
                        snapshot.get("current_mythic"),
                    ),
                ),
                (
                    "UPDATE level_outreach_queue SET current_exists=?,current_rated=?,current_stars=?,current_level_name=?,"
                    "uploader_name=COALESCE(?,uploader_name),uploader_user_id=COALESCE(?,uploader_user_id),"
                    "uploader_account_id=COALESCE(?,uploader_account_id),level_checked_ts=?,level_refresh_after_ts=?,queue_state=?,"
                    "rated_observed_ts=?,current_featured=?,current_epic=?,current_epic_tier_raw=?,current_legendary=?,current_mythic=?,"
                    "observed_rating_tier=COALESCE(?,observed_rating_tier),updated_ts=? WHERE id=?",
                    (
                        None if exists is None else int(bool(exists)),
                        None if rated is None else int(bool(rated)),
                        snapshot.get("current_stars"),
                        snapshot.get("current_level_name"),
                        snapshot.get("current_uploader_name"),
                        snapshot.get("current_uploader_user_id"),
                        snapshot.get("current_uploader_account_id"),
                        now,
                        refresh_after,
                        next_state,
                        rated_observed,
                        snapshot.get("current_featured"),
                        snapshot.get("current_epic"),
                        snapshot.get("current_epic_tier_raw"),
                        snapshot.get("current_legendary"),
                        snapshot.get("current_mythic"),
                        observed_rating_tier(snapshot),
                        now,
                        int(row["id"]),
                    ),
                ),
            ],
            retry_safe=True,
        )
        if rated is True and row["current_rated"] not in (1, "1", True):
            try:
                await self.notifications.emit(
                    int(row["guild_id"]),
                    str(row["level_id"]),
                    "rated_observed",
                    f"queue:{int(row['id'])}:rated:{now}",
                    "The level is currently shown as rated in Geometry Dash. This reports the current official state and does not claim Avenue caused the rating.",
                    queue_id=int(row["id"]),
                )
            except Exception as exc:
                await log_error(self.bot, f"Rated observation notification deferred: {exc!r}")

    async def _recent_level_snapshot(self, level_id: str) -> dict[str, Any] | None:
        cutoff = int(time.time()) - self.settings.level_refresh_seconds
        row = await self.db.fetchone(
            "SELECT s.*,q.current_level_name FROM level_outreach_level_snapshots s "
            "JOIN level_outreach_queue q ON q.id=s.queue_id WHERE q.level_id=? "
            "AND q.queue_state!='hidden' AND s.lookup_status='ok' AND s.checked_ts>=? "
            "ORDER BY s.checked_ts DESC LIMIT 1",
            (level_id, cutoff),
        )
        if not row:
            return None
        return {
            "level_id": level_id,
            "gd_checked_ts": int(row["checked_ts"]),
            "current_exists": None if row["current_exists"] is None else bool(row["current_exists"]),
            "current_rated": None if row["current_rated"] is None else bool(row["current_rated"]),
            "current_stars": row["stars"],
            "current_featured": row["current_featured"],
            "current_epic": row["current_epic"],
            "current_epic_tier_raw": row["current_epic_tier_raw"],
            "current_legendary": row["current_legendary"],
            "current_mythic": row["current_mythic"],
            "current_level_name": row["current_level_name"],
            "current_uploader_name": row["uploader_name"],
            "current_uploader_user_id": row["uploader_user_id"],
            "current_uploader_account_id": row["uploader_account_id"],
            "gd_lookup_status": "ok",
            "gd_lookup_error": None,
        }

    async def refresh_queue_entry(
        self,
        queue_id: int,
        *,
        force_level: bool = False,
        force_cp: bool = False,
    ):
        lock = self._refresh_locks.setdefault(int(queue_id), asyncio.Lock())
        async with lock:
            row = await self.db.fetchone(
                "SELECT * FROM level_outreach_queue WHERE id=?", (queue_id,)
            )
            if not row:
                raise ValueError("Queue entry not found")
            now = int(time.time())
            level_id = str(row["level_id"])
            if force_level or int(row["level_refresh_after_ts"] or 0) <= now:
                level_lock = self._level_locks.setdefault(level_id, asyncio.Lock())
                async with level_lock:
                    snapshot = None if force_level else await self._recent_level_snapshot(level_id)
                    if snapshot is None:
                        snapshot = await self._provider_level_snapshot(level_id)
                    await self._save_level_snapshot(row, snapshot)
                row = await self.db.fetchone(
                    "SELECT * FROM level_outreach_queue WHERE id=?", (queue_id,)
                )
            if row and (
                force_cp
                or row["current_creator_points"] is None
                or int(row["creator_points_refresh_after_ts"] or 0) <= now
            ):
                await self.creator_points.resolve_queue(int(queue_id), force=force_cp)
            return await self.db.fetchone(
                "SELECT * FROM level_outreach_queue WHERE id=?", (queue_id,)
            )

    async def manual_cp_override(
        self,
        guild_id: int,
        queue_id: int,
        actor_id: int,
        creator_points: int,
        reason: str,
    ):
        if creator_points < 0:
            raise ValueError("Creator Points cannot be negative")
        if not str(reason or "").strip():
            raise ValueError("A reason is required for a manual CP override")
        row = await self.db.fetchone(
            "SELECT * FROM level_outreach_queue WHERE id=? AND guild_id=?",
            (queue_id, guild_id),
        )
        if not row:
            raise ValueError("Queue entry not found")
        now = int(time.time())
        score = score_components(
            str(row["send_type"]), creator_points, int(row["waiting_cycles"] or 0), self.settings
        )
        correlation = str(row["correlation_id"] or new_correlation_id("pps-override"))
        await self.db.execute_transaction(
            [
                (
                    "INSERT INTO level_outreach_cp_snapshots(queue_id,account_id,checked_ts,creator_points,lookup_status,"
                    "source,actor_id,reason) VALUES(?,?,?,?,?,?,?,?)",
                    (queue_id, row["uploader_account_id"], now, creator_points, "manual_override", "manual_override", actor_id, str(reason)[:1000]),
                ),
                (
                    "UPDATE level_outreach_queue SET current_creator_points=?,current_creator_points_checked_ts=?,creator_points_refresh_after_ts=?,"
                    "creator_component_g=?,waiting_component_h=?,priority_points=?,priority_complete=?,creator_points_status='manual_override',"
                    "creator_points_source='manual_override',creator_points_confidence='manual_override',creator_points_observed_at=?,"
                    "creator_points_pending_reason=NULL,creator_points_last_error_category=NULL,last_public_priority_band=NULL,updated_ts=? WHERE id=?",
                    (creator_points, now, now + self.settings.cp_refresh_seconds, score["creator_component_g"], score["waiting_component_h"], score["priority_points"], score["priority_complete"], now, now, queue_id),
                ),
                (
                    "UPDATE creator_points_resolution_jobs SET state='manual_override',resolved_ts=?,next_attempt_ts=?,updated_ts=? WHERE queue_id=?",
                    (now, now + self.settings.cp_refresh_seconds, now, queue_id),
                ),
                (
                    "INSERT INTO workflow_events(correlation_id,workflow_type,entity_id,event,guild_id,actor_id,payload_json,created_ts) "
                    "VALUES(?,'priority_system',?,'cp_overridden',?,?,?,?)",
                    (correlation, f"queue:{queue_id}", guild_id, actor_id, json.dumps({"creator_points": creator_points, "reason": str(reason)[:500]}), now),
                ),
            ],
            retry_safe=False,
        )
        return await self.db.fetchone("SELECT * FROM level_outreach_queue WHERE id=?", (queue_id,))

    async def maintenance_once(self, guild_id: int) -> dict[str, int]:
        now = int(time.time())
        settings = self.settings
        placeholders = ",".join("?" for _ in PPS_QUEUE_REFRESH_STATES)
        rows = await self.db.fetchall(
            f"SELECT id FROM level_outreach_queue WHERE guild_id=? AND queue_state IN({placeholders}) "  # nosec B608
            "AND (COALESCE(level_refresh_after_ts,0)<=? OR (uploader_account_id IS NOT NULL AND COALESCE(creator_points_refresh_after_ts,0)<=?)) "
            "ORDER BY MIN(COALESCE(level_refresh_after_ts,0),COALESCE(creator_points_refresh_after_ts,0)),id LIMIT ?",
            (
                guild_id,
                *PPS_QUEUE_REFRESH_STATES,
                now,
                now,
                settings.maintenance_batch_size,
            ),
        )
        refreshed = failed = 0
        attempted_refreshes: set[int] = set()
        successful_refreshes: set[int] = set()
        for item in rows:
            queue_id = int(item["id"])
            attempted_refreshes.add(queue_id)
            try:
                await self.refresh_queue_entry(queue_id)
                successful_refreshes.add(queue_id)
                refreshed += 1
            except Exception as exc:
                failed += 1
                await log_error(
                    self.bot,
                    f"PPS queue refresh deferred queue_id={queue_id}: {type(exc).__name__}: {str(exc)[:300]}",
                )
        due = await self.db.fetchall(
            "SELECT id,level_id,rated_observed_ts,outcome_window_due_ts FROM level_outreach_queue "
            "WHERE guild_id=? AND submitted_to_mod_ts IS NOT NULL AND outcome_window_due_ts<=? "
            "AND outcome_window_completed_ts IS NULL AND queue_state!='hidden' "
            "ORDER BY outcome_window_due_ts LIMIT ?",
            (guild_id, now, settings.maintenance_batch_size),
        )
        completed = 0
        for row in due:
            queue_id = int(row["id"])
            if queue_id in attempted_refreshes and queue_id not in successful_refreshes:
                continue
            try:
                if queue_id in successful_refreshes:
                    refreshed_row = await self.db.fetchone(
                        "SELECT * FROM level_outreach_queue WHERE id=?", (queue_id,)
                    )
                else:
                    refreshed_row = await self.refresh_queue_entry(
                        queue_id, force_level=True
                    )
                latest_snapshot = await self.db.fetchone(
                    "SELECT lookup_status FROM level_outreach_level_snapshots WHERE queue_id=? ORDER BY checked_ts DESC,id DESC LIMIT 1",
                    (queue_id,),
                )
                if (
                    not refreshed_row
                    or not latest_snapshot
                    or str(latest_snapshot["lookup_status"]) != "ok"
                ):
                    failed += 1
                    continue
                row = refreshed_row
            except Exception as exc:
                failed += 1
                await log_error(
                    self.bot,
                    f"PPS outcome-window refresh deferred queue_id={queue_id}: {type(exc).__name__}: {str(exc)[:300]}",
                )
                continue
            rated_within = int(
                row["rated_observed_ts"] is not None
                and int(row["rated_observed_ts"]) <= int(row["outcome_window_due_ts"])
            )
            completed += await self.db.execute_affected(
                "UPDATE level_outreach_queue SET rated_within_window=?,outcome_window_completed_ts=?,updated_ts=? "
                "WHERE id=? AND outcome_window_completed_ts IS NULL",
                (rated_within, now, now, int(row["id"])),
            )
            if not rated_within:
                try:
                    await self.notifications.emit(
                        guild_id,
                        str(row["level_id"]),
                        "outcome_window_closed",
                        f"queue:{int(row['id'])}:outcome-window",
                        "No rating has been observed within Avenue's 30-day tracking window. This does not prevent the level from being rated later, and its Avenue history remains preserved.",
                        queue_id=int(row["id"]),
                        payload={"rated_within_window": False},
                    )
                except Exception as exc:
                    await log_error(self.bot, f"Outcome notification deferred: {exc!r}")
        cp_jobs = await self.creator_points.run_due_jobs(limit=settings.maintenance_batch_size)
        deliveries_synced = await self.notifications.sync_delivery_states()
        return {
            "refreshed": refreshed,
            "failed": failed,
            "outcomes_completed": completed,
            "notification_deliveries_synced": deliveries_synced,
            "creator_points_jobs": cp_jobs["attempted"],
            "creator_points_resolved": cp_jobs["resolved"],
        }

    async def dashboard(self, guild_id: int) -> dict[str, Any]:
        rows = await self.db.fetchall(
            "SELECT queue_state,priority_complete,COUNT(*) AS c FROM level_outreach_queue "
            "WHERE guild_id=? AND queue_state!='hidden' "
            "GROUP BY queue_state,priority_complete",
            (guild_id,),
        )
        state_counts: dict[str, int] = {}
        scored = pending = 0
        for row in rows:
            state = str(row["queue_state"])
            count = int(row["c"] or 0)
            state_counts[state] = state_counts.get(state, 0) + count
            if state in PPS_QUEUE_ACTIVE_STATES:
                if int(row["priority_complete"] or 0):
                    scored += count
                else:
                    pending += count
        top = await self.db.fetchall(
            "SELECT * FROM level_outreach_queue WHERE guild_id=? AND queue_state IN('queued','in_cycle') "
            "AND priority_complete=1 ORDER BY priority_points DESC,waiting_cycles DESC,queued_ts,id LIMIT 5",
            (guild_id,),
        )
        refresh = await self.db.fetchone(
            "SELECT COUNT(*) AS c,MIN(COALESCE(level_refresh_after_ts,0)) AS next_level," 
            "MIN(CASE WHEN uploader_account_id IS NOT NULL THEN COALESCE(creator_points_refresh_after_ts,0) END) AS next_cp "
            "FROM level_outreach_queue WHERE guild_id=? AND queue_state IN('queued','in_cycle','awaiting_outcome')",
            (guild_id,),
        )
        recent_errors = await self.db.fetchall(
            "SELECT last_message,last_seen_ts,occurrence_count FROM error_incidents "
            "WHERE status!='resolved' AND last_message LIKE 'PPS %' ORDER BY last_seen_ts DESC LIMIT 3"
        )
        return {
            "states": state_counts,
            "active_queue": sum(state_counts.get(state, 0) for state in ("queued", "in_cycle")),
            "scored": scored,
            "cp_pending": pending,
            "awaiting_outcome": state_counts.get("awaiting_outcome", 0),
            "rated": state_counts.get("rated", 0),
            "invalid": state_counts.get("invalid", 0),
            "active_cycle": await self.active_cycle(guild_id),
            "top": top,
            "model_version": self.settings.model_version,
            "next_level_refresh": (
                int(refresh["next_level"] or 0)
                if refresh and int(refresh["c"] or 0)
                else None
            ),
            "next_cp_refresh": (
                int(refresh["next_cp"] or 0)
                if refresh and refresh["next_cp"] is not None
                else None
            ),
            "recent_errors": recent_errors,
        }
