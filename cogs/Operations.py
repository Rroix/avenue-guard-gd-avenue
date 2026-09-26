from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta
from typing import Any

import discord
from discord.ext import commands

from services.backups import run_restore_drill
from services.diagnostics import persist_permission_drift, scan_permission_drift
from utils.config_schema import (
    CONFIG_SCHEMA_VERSION,
    DATABASE_SCHEMA_VERSION,
    EMBED_SCHEMA_VERSION,
    RUNTIME_SCHEMA_VERSION,
    operations_settings,
)
from utils.db import DatabaseBusyError
from utils.errors import log_error
from utils.keepalive import set_runtime_heartbeat
from utils.supervision import start_cog_background
from utils.timeutils import now_madrid
from utils.workflows import new_correlation_id, record_workflow_event

RETENTION_TARGETS = {
    "health_metrics": ("sample_ts", ""),
    "workflow_events": ("created_ts", ""),
    "error_incidents": ("last_seen_ts", "AND status='resolved'"),
    "discord_outbox": ("updated_ts", "AND status IN ('delivered','dead')"),
    "gd_level_validation_cache": ("expires_ts", ""),
    "impact_snapshots": ("snapshot_ts", ""),
}


class OperationsCog(commands.Cog):
    """Supervises durable work that should survive independent feature failures."""

    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self._started = False
        self._tasks: dict[str, asyncio.Task] = {}
        self._task_states: dict[str, str] = {}
        self._maintenance_lock = asyncio.Lock()
        self._last_permission_scan = 0
        self._last_retention_run = 0
        self._last_restore_drill = 0
        self._last_monthly_check = 0
        self._last_smoke_result: dict[str, Any] = {}
        self._bootstrap_ready = asyncio.Event()
        self._last_health_sample: dict[str, Any] = {}
        self._deferred_health_samples = 0

    def cog_unload(self) -> None:
        for task in self._tasks.values():
            if not task.done():
                task.cancel()

    async def close_resources(self) -> None:
        current = asyncio.current_task()
        tasks = [
            task
            for task in self._tasks.values()
            if task is not current and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def start_background(self) -> None:
        self._started = True
        factories = {
            "outbox": self._outbox_loop,
            "supervisor": self._supervisor_loop,
            "watchdog": self._watchdog_loop,
            "health": self._health_loop,
            "maintenance": self._maintenance_loop,
            "smoke": self._post_deploy_smoke_test,
            "bootstrap": self._bootstrap_loop,
        }
        for name, factory in factories.items():
            task = self._tasks.get(name)
            if name in {"smoke", "bootstrap"} and task is not None and task.done() and not task.cancelled() and task.exception() is None:
                continue
            if task is None or task.done():
                self._tasks[name] = asyncio.create_task(factory(), name=f"avenue-guard:{name}")

    async def _bootstrap_loop(self) -> None:
        while True:
            try:
                await self._load_persisted_operations()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await log_error(self.bot, f"Operations bootstrap deferred: {exc!r}")
            finally:
                self._bootstrap_ready.set()
            await asyncio.sleep(30)

    async def _load_persisted_operations(self) -> None:
        retention_override = await self.bot.db.get_runtime_setting(
            "operations.retention_days", default=None
        )
        if isinstance(retention_override, dict):
            operations = self.bot.config.data.setdefault("operations", {})
            operations["retention_days"] = dict(retention_override)
        timestamps = await self.bot.db.get_runtime_setting(
            "operations.timestamps", default={}
        )
        if isinstance(timestamps, dict):
            self._last_permission_scan = int(timestamps.get("permission_scan", 0) or 0)
            self._last_retention_run = int(timestamps.get("retention", 0) or 0)
        drill_row = await self.bot.db.fetchone(
            "SELECT MAX(drill_ts) AS ts FROM restore_drills"
        )
        self._last_restore_drill = int(drill_row["ts"] or 0) if drill_row else 0
        await self.bot.outbox.recover_stale()
        portal = getattr(self.bot, "staff_portal", None)
        reconcile_applications = getattr(
            portal, "reconcile_application_deliveries", None
        )
        if callable(reconcile_applications):
            repaired = await reconcile_applications(limit=50)
            if repaired:
                await record_workflow_event(
                    self.bot.db,
                    workflow_type="staff_portal",
                    entity_id="application-delivery",
                    event="application_deliveries_requeued",
                    guild_id=self.bot.config.get_int(
                        "guild", "allowed_guild_id", default=0
                    ),
                    payload={"count": repaired},
                )

    def task_snapshot(self) -> dict[str, str]:
        snapshot = dict(self._task_states)
        for name, task in self._tasks.items():
            state = self._task_state(task)
            if name in {"smoke", "bootstrap", "restarts", "timeline"} and task.done() and not task.cancelled() and task.exception() is None:
                state = "completed"
            snapshot[f"operations.{name}"] = state
        release = self.bot.get_cog("ReleaseCog")
        if release is not None:
            bootstrap = getattr(release, "_bootstrap_task", None)
            snapshot["release.bootstrap"] = "completed" if getattr(release, "_bootstrap_complete", False) else self._task_state(bootstrap)
        return snapshot

    async def _watchdog_loop(self) -> None:
        previous = time.monotonic()
        while not self.bot.is_closed():
            now = time.monotonic()
            lag_ms = max(0.0, (now - previous - 1) * 1000)
            previous = now
            reporter = getattr(self.bot, "_error_reporter", None)
            set_runtime_heartbeat(
                lag_ms=lag_ms,
                tasks=self.task_snapshot(),
                database=self.bot.db.health_snapshot(),
                incidents=reporter.snapshot() if reporter else [],
            )
            supervisor = self._tasks.get("supervisor")
            if supervisor is None or supervisor.done():
                await self.start_background()
            if lag_ms >= 2000:
                await log_error(self.bot, f"Event loop stalled for {lag_ms / 1000:.1f}s; Discord acknowledgements may have expired")
            await asyncio.sleep(1)

    def _external_task_specs(self):
        return (
            ("tracking.weekly", "TrackingCog", "_weekly_task"),
            ("tracking.timeouts", "TrackingCog", "_timeout_task"),
            ("tracking.flush", "TrackingCog", "_activity_flush_task"),
            ("tracking.recap", "TrackingCog", "_recap_task"),
            ("help.ticket_scan", "HelpCog", "_ticket_scan_task"),
            ("help.feedback_restore", "HelpCog", "_satisfaction_restore_task"),
            ("requests.auto_close", "RequestLevelsCog", "_close_task"),
            ("requests.scheduled", "RequestLevelsCog", "_scheduled_open_task"),
            ("priority.maintenance", "PrioritySystemCog", "_maintenance_task"),
            ("priority.creator_points", "PrioritySystemCog", "_creator_points_task"),
            ("priority.models", "PrioritySystemCog", "_model_task"),
            ("history.audit", "HistoricalAuditCog", "_audit_task"),
            ("release.metrics", "ReleaseCog", "_metrics_task"),
            ("background.daily", "BackgroundCog", "daily_report"),
            ("background.snapshot", "BackgroundCog", "update_snapshot"),
            ("background.backup", "BackgroundCog", "database_backup"),
            ("background.status", "BackgroundCog", "rotate_status"),
            ("background.icon", "BackgroundCog", "rotate_server_icon"),
        )

    def _task_expected(self, label: str, cog: Any) -> bool:
        checks = {
            "help.feedback_restore": "_ticket_feedback_restore_expected",
            "background.daily": "_daily_summary_enabled",
            "background.backup": "_database_backup_enabled",
            "background.status": "_status_rotation_enabled",
            "background.icon": "_server_icon_rotation_enabled",
        }
        method_name = checks.get(label)
        if method_name is None:
            return True
        check = getattr(cog, method_name, None)
        if not callable(check):
            return False
        try:
            return bool(check())
        except Exception:
            return False

    @staticmethod
    def _task_state(value: Any) -> str:
        if value is None:
            return "missing"
        is_running = getattr(value, "is_running", None)
        if callable(is_running):
            try:
                return "running" if is_running() else "stopped"
            except Exception:
                return "unknown"
        done = getattr(value, "done", None)
        if callable(done):
            try:
                if not done():
                    return "running"
                exception = value.exception()
                return f"failed:{type(exception).__name__}" if exception else "stopped"
            except (asyncio.CancelledError, Exception):
                return "stopped"
        return "unknown"

    async def restart_stopped_tasks(self, *, force: bool = False) -> dict[str, str]:
        affected_cogs: set[str] = set()
        before: dict[str, str] = {}
        cancelled_tasks: list[asyncio.Task] = []
        for label, cog_name, attr in self._external_task_specs():
            cog = self.bot.get_cog(cog_name)
            if cog is not None and not self._task_expected(label, cog):
                before[label] = "disabled"
                continue
            value = getattr(cog, attr, None) if cog else None
            state = self._task_state(value)
            before[label] = state
            if cog is not None and (force or state not in {"running", "unknown"}):
                affected_cogs.add(cog_name)
            if force and value is not None:
                cancel = getattr(value, "cancel", None)
                if callable(cancel):
                    cancel()
                    actual_task = value if isinstance(value, asyncio.Task) else getattr(value, "get_task", lambda: None)()
                    if actual_task is not None:
                        cancelled_tasks.append(actual_task)
        if cancelled_tasks:
            await asyncio.gather(*cancelled_tasks, return_exceptions=True)
        if force:
            await asyncio.sleep(0)
        for cog_name in sorted(affected_cogs):
            cog = self.bot.get_cog(cog_name)
            start = getattr(cog, "start_background", None)
            if callable(start):
                try:
                    await start_cog_background(self.bot, cog_name)
                except Exception as exc:
                    await log_error(
                        self.bot,
                        f"Task supervisor could not restart {cog_name}: {exc!r}",
                    )
        return before

    async def _outbox_loop(self) -> None:
        await self._bootstrap_ready.wait()
        while not self.bot.is_closed():
            try:
                await self.bot.outbox.recover_stale()
                result = await self.bot.outbox.process_once(limit=15)
                if result.get("dead"):
                    dead_ids = tuple(self.bot.outbox.last_dead_letter_ids())
                    if dead_ids:
                        placeholders = ",".join("?" for _ in dead_ids)
                        dead_rows = await self.bot.db.fetchall(
                            "SELECT id,action_type,channel_id,user_id,attempts,last_error "
                            f"FROM discord_outbox WHERE id IN ({placeholders}) "  # nosec B608
                            "ORDER BY id",
                            dead_ids,
                        )
                    else:
                        dead_rows = []
                    details = "; ".join(
                        (
                            f"#{int(row['id'])} action={row['action_type']} "
                            f"channel={int(row['channel_id'] or 0)} "
                            f"user={int(row['user_id'] or 0)} "
                            f"attempts={int(row['attempts'] or 0)} "
                            f"error={str(row['last_error'] or 'unknown')[:300]}"
                        )
                        for row in dead_rows
                    )
                    if not details:
                        details = "details unavailable; inspect Admin > System > Delivery"
                    await log_error(
                        self.bot,
                        "Discord outbox moved "
                        f"{result['dead']} action(s) to dead-letter status: {details}",
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await log_error(self.bot, f"Discord outbox worker error: {exc!r}")
            await asyncio.sleep(
                operations_settings(self.bot.config.data).outbox_poll_seconds
            )

    async def _supervisor_loop(self) -> None:
        while not self.bot.is_closed():
            try:
                changed: list[tuple[str, str, str]] = []
                for label, cog_name, attr in self._external_task_specs():
                    cog = self.bot.get_cog(cog_name)
                    if cog is not None and not self._task_expected(label, cog):
                        state = "disabled"
                    else:
                        state = self._task_state(
                            getattr(cog, attr, None) if cog else None
                        )
                    previous = self._task_states.get(label)
                    self._task_states[label] = state
                    if previous is not None and state != previous:
                        changed.append((label, previous, state))
                failed = [
                    label
                    for label, state in self._task_states.items()
                    if state.startswith(("failed", "stopped", "missing"))
                ]
                initializing = hasattr(self.bot, "_runtime_initialized") and not self.bot._runtime_initialized
                if failed and not initializing:
                    restart = self._tasks.get("restarts")
                    if restart is None or restart.done():
                        self._tasks["restarts"] = asyncio.create_task(self.restart_stopped_tasks(), name="avenue-guard:restarts")
                internal_factories = {
                    "outbox": self._outbox_loop,
                    "health": self._health_loop,
                    "maintenance": self._maintenance_loop,
                    "watchdog": self._watchdog_loop,
                }
                for name, factory in internal_factories.items():
                    task = self._tasks.get(name)
                    if task is None or task.done():
                        if task is not None and not task.cancelled() and task.exception() is not None:
                            await log_error(self.bot, f"Operations {name} task failed; restarting: {task.exception()!r}")
                        self._tasks[name] = asyncio.create_task(
                            factory(),
                            name=f"avenue-guard:{name}",
                        )
                        changed.append((f"operations.{name}", "stopped", "running"))
                timeline = self._tasks.get("timeline")
                if changed and (timeline is None or timeline.done()):
                    self._tasks["timeline"] = asyncio.create_task(self._record_task_changes(changed), name="avenue-guard:timeline")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await log_error(self.bot, f"Background task supervisor error: {exc!r}")
            await asyncio.sleep(
                operations_settings(self.bot.config.data).supervisor_interval_seconds
            )

    async def _record_task_changes(self, changed):
        try:
            for label, previous, state in changed:
                await record_workflow_event(self.bot.db, workflow_type="background_task", entity_id=label,
                                            event="state_changed", payload={"from": previous, "to": state})
        except Exception as exc:
            await log_error(self.bot, f"Task timeline persistence deferred: {exc!r}")

    async def collect_health_sample(self) -> dict[str, Any]:
        started = time.perf_counter()
        db_ok = True
        try:
            db_ok = await self.bot.db.fetchone_local("SELECT 1 AS ready") is not None
        except Exception:
            db_ok = False
        db_probe_ms = round((time.perf_counter() - started) * 1000, 2)
        background = self.bot.get_cog("BackgroundCog")
        stats = getattr(background, "stats", None)
        request_cog = self.bot.get_cog("RequestLevelsCog")
        providers = (
            request_cog.validation_provider_snapshot()
            if request_cog is not None
            and hasattr(request_cog, "validation_provider_snapshot")
            else {}
        )
        payload = {
            "gateway_latency_ms": round(
                float(getattr(self.bot, "latency", 0.0) or 0.0) * 1000, 2
            ),
            "db_ok": db_ok,
            "db_probe_ms": db_probe_ms,
            "db": self.bot.db.health_snapshot(),
            "query_timing": self.bot.db.query_timing_snapshot(reset=True),
            "tasks": self.task_snapshot(),
            "daily_commands": int(getattr(stats, "commands", 0) or 0),
            "daily_command_errors": int(getattr(stats, "command_errors", 0) or 0),
            "providers": providers,
            "sample_ts": int(time.time()),
        }
        guild_id = int(
            self.bot.config.get_int("guild", "allowed_guild_id", default=0) or 0
        )
        samples = [(
                guild_id,
                payload["sample_ts"],
                "runtime",
                db_probe_ms,
                json.dumps(payload, separators=(",", ":")),
            )]
        for provider, provider_payload in providers.items():
            samples.append((
                    guild_id,
                    payload["sample_ts"],
                    f"provider:{provider}",
                    float(provider_payload.get("average_latency_ms", 0) or 0),
                    json.dumps(provider_payload, separators=(",", ":")),
                ))
        placeholders = ",".join("(?,?,?,?,?)" for _ in samples)
        payload["persisted"] = False
        self._last_health_sample = payload
        try:
            await self.bot.db.execute_transaction([(
                f"INSERT INTO health_metrics(guild_id,sample_ts,metric_type,value,payload_json) VALUES {placeholders}",  # nosec B608
                tuple(value for sample in samples for value in sample),
            )], queue_timeout=0.25)
        except DatabaseBusyError:
            self._deferred_health_samples += 1
            payload["deferred_samples"] = self._deferred_health_samples
            return payload
        payload["persisted"] = True
        payload["deferred_samples"] = self._deferred_health_samples
        return payload

    async def _health_loop(self) -> None:
        while not self.bot.is_closed():
            try:
                await self.collect_health_sample()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await log_error(self.bot, f"Historical health sampler error: {exc!r}")
            await asyncio.sleep(
                operations_settings(self.bot.config.data).health_sample_interval_seconds
            )

    async def scan_permissions(self) -> list:
        guild_id = int(
            self.bot.config.get_int("guild", "allowed_guild_id", default=0) or 0
        )
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return []
        findings = scan_permission_drift(self.bot, guild)
        await persist_permission_drift(self.bot.db, guild_id, findings)
        self._last_permission_scan = int(time.time())
        await self._persist_maintenance_timestamps()
        return findings

    async def _persist_maintenance_timestamps(self) -> None:
        await self.bot.db.set_runtime_setting(
            "operations.timestamps",
            {
                "permission_scan": self._last_permission_scan,
                "retention": self._last_retention_run,
            },
        )

    async def run_retention(self) -> dict[str, int]:
        settings = operations_settings(self.bot.config.data)
        now = int(time.time())
        removed: dict[str, int] = {}
        for table, days in settings.retention_days.items():
            target = RETENTION_TARGETS.get(table)
            if target is None:
                continue
            column, condition = target
            cutoff = now - int(days) * 86400
            # Identifiers and predicates come only from RETENTION_TARGETS.
            removed[table] = await self.bot.db.execute_affected(
                f"DELETE FROM {table} WHERE {column}<? {condition}",  # nosec B608
                (cutoff,),
            )
        await self.bot.db.execute(
            "UPDATE error_incidents SET status='resolved',resolved_ts=? WHERE status='open' AND last_seen_ts<?",
            (now, now - 7 * 86400),
        )
        self._last_retention_run = now
        await self._persist_maintenance_timestamps()
        return removed

    async def run_restore_drill(self, *, trigger: str = "manual") -> dict[str, object]:
        guild_id = int(
            self.bot.config.get_int("guild", "allowed_guild_id", default=0) or 0
        )
        result = await run_restore_drill(
            self.bot.db, guild_id=guild_id, trigger=trigger
        )
        self._last_restore_drill = int(time.time())
        return result

    async def generate_monthly_report(self, *, force: bool = False) -> bool:
        settings = operations_settings(self.bot.config.data)
        now = now_madrid()
        month_key = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        guild_id = int(
            self.bot.config.get_int("guild", "allowed_guild_id", default=0) or 0
        )
        guild = self.bot.get_guild(guild_id)
        commands_cog = self.bot.get_cog("CommandsCog")
        if guild is None or commands_cog is None:
            return False
        exists = await self.bot.db.fetchone(
            "SELECT status FROM monthly_impact_reports WHERE guild_id=? AND month_key=?",
            (guild_id, month_key),
        )
        if exists and not force:
            return False
        metrics = await commands_cog._collect_impact_metrics(guild, 0)
        embed = commands_cog._impact_report_embed(metrics)
        channel_id = int(
            settings.monthly_report_channel_id
            or self.bot.config.get_int("impact", "report_channel_id", default=0)
            or 0
        )
        if not channel_id:
            return False
        correlation = new_correlation_id("impact")
        await self.bot.outbox.enqueue(
            "send_channel",
            guild_id=guild_id,
            channel_id=channel_id,
            correlation_id=correlation,
            idempotency_key=f"monthly-impact:{guild_id}:{month_key}",
            payload={
                "content": f"Avenue Guard monthly impact report for {month_key}",
                "embed": embed.to_dict(),
            },
        )
        await self.bot.db.execute(
            "INSERT OR REPLACE INTO monthly_impact_reports(guild_id,month_key,generated_ts,channel_id,payload_json,status) VALUES(?,?,?,?,?,?)",
            (
                guild_id,
                month_key,
                int(time.time()),
                channel_id,
                json.dumps(metrics, separators=(",", ":")),
                "queued",
            ),
        )
        return True

    async def reconcile_monthly_reports(self) -> int:
        rows = await self.bot.db.fetchall(
            "SELECT guild_id,month_key,status FROM monthly_impact_reports WHERE status='queued'"
        )
        changed = 0
        for row in rows:
            guild_id = int(row["guild_id"] or 0)
            month_key = str(row["month_key"] or "")
            delivery = await self.bot.db.fetchone(
                "SELECT status,delivered_message_id,last_error FROM discord_outbox "
                "WHERE idempotency_key=?",
                (f"monthly-impact:{guild_id}:{month_key}",),
            )
            if delivery is None or str(delivery["status"]) not in {"delivered", "dead"}:
                continue
            status = "delivered" if str(delivery["status"]) == "delivered" else "failed"
            await self.bot.db.execute(
                "UPDATE monthly_impact_reports SET status=?,message_id=? WHERE guild_id=? AND month_key=?",
                (
                    status,
                    int(delivery["delivered_message_id"] or 0) or None,
                    guild_id,
                    month_key,
                ),
            )
            changed += 1
        return changed

    async def _maintenance_loop(self) -> None:
        await self._bootstrap_ready.wait()
        await asyncio.sleep(60)
        while not self.bot.is_closed():
            try:
                async with self._maintenance_lock:
                    now = int(time.time())
                    settings = operations_settings(self.bot.config.data)
                    await self.reconcile_monthly_reports()
                    if (
                        now - self._last_permission_scan
                        >= settings.permission_scan_interval_seconds
                    ):
                        await self.scan_permissions()
                    if now - self._last_retention_run >= 86400:
                        await self.run_retention()
                    if now - self._last_restore_drill >= 7 * 86400:
                        await self.run_restore_drill(trigger="scheduled")
                    current = now_madrid()
                    if (
                        current.day == settings.monthly_report_day
                        and current.hour >= settings.monthly_report_hour
                        and now - self._last_monthly_check >= 3600
                    ):
                        self._last_monthly_check = now
                        await self.generate_monthly_report()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await log_error(self.bot, f"Operations maintenance loop error: {exc!r}")
            await asyncio.sleep(300)

    async def _post_deploy_smoke_test(self) -> None:
        delay = operations_settings(self.bot.config.data).smoke_test_delay_seconds
        if delay:
            await asyncio.sleep(delay)
        checks: dict[str, bool] = {}
        details: dict[str, str] = {}
        try:
            checks["database"] = bool(await self.bot.db.fetchone("SELECT 1 AS ready"))
        except Exception as exc:
            checks["database"] = False
            details["database"] = f"{type(exc).__name__}: {exc}"[:300]
        guild_id = int(
            self.bot.config.get_int("guild", "allowed_guild_id", default=0) or 0
        )
        checks["guild"] = self.bot.get_guild(guild_id) is not None
        checks["config"] = not any(
            issue.severity == "error" for issue in self.bot.config.validation_issues
        )
        outbox_task = self._tasks.get("outbox")
        checks["outbox"] = outbox_task is not None and not outbox_task.done()
        checks["request_cog"] = self.bot.get_cog("RequestLevelsCog") is not None
        try:
            schema_rows = await self.bot.db.fetchall(
                "SELECT component,schema_version FROM schema_metadata"
            )
        except Exception as exc:
            schema_rows = []
            details["schema_versions"] = f"{type(exc).__name__}: {exc}"[:300]
        schema_versions = {
            str(row["component"]): int(row["schema_version"]) for row in schema_rows
        }
        expected_schemas = {
            "database": DATABASE_SCHEMA_VERSION,
            "config": CONFIG_SCHEMA_VERSION,
            "runtime_settings": RUNTIME_SCHEMA_VERSION,
            "embed_templates": EMBED_SCHEMA_VERSION,
        }
        checks["schema_versions"] = schema_versions == expected_schemas
        if not checks["schema_versions"]:
            details.setdefault("schema_versions", (
                f"expected={expected_schemas} actual={schema_versions}"
            ))
        command_count = sum(1 for _ in self.bot.walk_application_commands())
        checks["slash_commands"] = command_count >= 17
        details["slash_commands"] = str(command_count)
        status = "passed" if all(checks.values()) else "failed"
        correlation = new_correlation_id("deployment")
        self._last_smoke_result = {
            "status": status,
            "checks": checks,
            "details": details,
            "correlation_id": correlation,
            "ts": int(time.time()),
        }
        try:
            await record_workflow_event(
                self.bot.db,
                workflow_type="deployment",
                entity_id=str(getattr(self.bot.user, "id", 0) or 0),
                event=f"smoke_test_{status}",
                correlation_id=correlation,
                guild_id=guild_id,
                payload={"checks": checks, "details": details},
            )
        except Exception as exc:
            await log_error(self.bot, f"Smoke test history persistence deferred: {exc!r}")
        if status == "failed":
            failed = ", ".join(key for key, passed in checks.items() if not passed)
            await log_error(
                self.bot, f"Post-deployment smoke test failed: {failed} [{correlation}]"
            )


def setup(bot: discord.Bot):
    bot.add_cog(OperationsCog(bot))
