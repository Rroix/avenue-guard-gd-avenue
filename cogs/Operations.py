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
from utils.errors import log_error
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
        if self._started and all(not task.done() for task in self._tasks.values()):
            return
        self._started = True
        retention_override = await self.bot.db.get_runtime_setting(
            "operations.retention_days", default={}
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
        factories = {
            "outbox": self._outbox_loop,
            "supervisor": self._supervisor_loop,
            "health": self._health_loop,
            "maintenance": self._maintenance_loop,
            "smoke": self._post_deploy_smoke_test,
        }
        for name, factory in factories.items():
            task = self._tasks.get(name)
            if task is None or task.done():
                self._tasks[name] = asyncio.create_task(
                    factory(), name=f"avenue-guard:{name}"
                )

    def task_snapshot(self) -> dict[str, str]:
        snapshot = dict(self._task_states)
        for name, task in self._tasks.items():
            snapshot[f"operations.{name}"] = "stopped" if task.done() else "running"
        return snapshot

    def _external_task_specs(self):
        return (
            ("tracking.weekly", "TrackingCog", "_weekly_task"),
            ("tracking.timeouts", "TrackingCog", "_timeout_task"),
            ("tracking.flush", "TrackingCog", "_activity_flush_task"),
            ("tracking.recap", "TrackingCog", "_recap_task"),
            ("help.ticket_scan", "HelpCog", "_ticket_scan_task"),
            ("requests.auto_close", "RequestLevelsCog", "_close_task"),
            ("requests.scheduled", "RequestLevelsCog", "_scheduled_open_task"),
            ("release.metrics", "ReleaseCog", "_metrics_task"),
            ("background.daily", "BackgroundCog", "daily_report"),
            ("background.snapshot", "BackgroundCog", "update_snapshot"),
            ("background.backup", "BackgroundCog", "database_backup"),
            ("background.status", "BackgroundCog", "rotate_status"),
            ("background.icon", "BackgroundCog", "rotate_server_icon"),
        )

    def _task_expected(self, label: str, cog: Any) -> bool:
        checks = {
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
            if force or state not in {"running", "missing"}:
                affected_cogs.add(cog_name)
            if force and value is not None:
                cancel = getattr(value, "cancel", None)
                if callable(cancel):
                    cancel()
                    if isinstance(value, asyncio.Task):
                        cancelled_tasks.append(value)
        if cancelled_tasks:
            await asyncio.gather(*cancelled_tasks, return_exceptions=True)
        if force:
            await asyncio.sleep(0)
        for cog_name in sorted(affected_cogs):
            cog = self.bot.get_cog(cog_name)
            start = getattr(cog, "start_background", None)
            if callable(start):
                try:
                    await start()
                except Exception as exc:
                    await log_error(
                        self.bot,
                        f"Task supervisor could not restart {cog_name}: {exc!r}",
                    )
        return before

    async def _outbox_loop(self) -> None:
        while not self.bot.is_closed():
            try:
                result = await self.bot.outbox.process_once(limit=15)
                if result.get("dead"):
                    await log_error(
                        self.bot,
                        f"Discord outbox moved {result['dead']} action(s) to dead-letter status",
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
                    if state.startswith(("failed", "stopped"))
                ]
                if failed:
                    await self.restart_stopped_tasks()
                internal_factories = {
                    "outbox": self._outbox_loop,
                    "health": self._health_loop,
                    "maintenance": self._maintenance_loop,
                }
                for name, factory in internal_factories.items():
                    task = self._tasks.get(name)
                    if task is not None and task.done():
                        self._tasks[name] = asyncio.create_task(
                            factory(),
                            name=f"avenue-guard:{name}",
                        )
                        changed.append((f"operations.{name}", "stopped", "running"))
                for label, previous, state in changed:
                    await record_workflow_event(
                        self.bot.db,
                        workflow_type="background_task",
                        entity_id=label,
                        event="state_changed",
                        payload={"from": previous, "to": state},
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await log_error(self.bot, f"Background task supervisor error: {exc!r}")
            await asyncio.sleep(
                operations_settings(self.bot.config.data).supervisor_interval_seconds
            )

    async def collect_health_sample(self) -> dict[str, Any]:
        started = time.perf_counter()
        db_ok = True
        try:
            await self.bot.db.fetchone("SELECT 1 AS ready")
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
        await self.bot.db.execute(
            "INSERT INTO health_metrics(guild_id,sample_ts,metric_type,value,payload_json) VALUES(?,?,?,?,?)",
            (
                guild_id,
                payload["sample_ts"],
                "runtime",
                db_probe_ms,
                json.dumps(payload, separators=(",", ":")),
            ),
        )
        for provider, provider_payload in providers.items():
            await self.bot.db.execute(
                "INSERT INTO health_metrics(guild_id,sample_ts,metric_type,value,payload_json) VALUES(?,?,?,?,?)",
                (
                    guild_id,
                    payload["sample_ts"],
                    f"provider:{provider}",
                    float(provider_payload.get("average_latency_ms", 0) or 0),
                    json.dumps(provider_payload, separators=(",", ":")),
                ),
            )
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
            removed[table] = await self.bot.db.execute_affected(  # nosec B608
                f"DELETE FROM {table} WHERE {column}<? {condition}",
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
        checks["outbox"] = not self._tasks.get("outbox", asyncio.current_task()).done()
        checks["request_cog"] = self.bot.get_cog("RequestLevelsCog") is not None
        schema_rows = await self.bot.db.fetchall(
            "SELECT component,schema_version FROM schema_metadata"
        )
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
            details["schema_versions"] = (
                f"expected={expected_schemas} actual={schema_versions}"
            )
        command_count = sum(1 for _ in self.bot.walk_application_commands())
        checks["slash_commands"] = command_count >= 17
        details["slash_commands"] = str(command_count)
        status = "passed" if all(checks.values()) else "failed"
        correlation = await record_workflow_event(
            self.bot.db,
            workflow_type="deployment",
            entity_id=str(getattr(self.bot.user, "id", 0) or 0),
            event=f"smoke_test_{status}",
            guild_id=guild_id,
            payload={"checks": checks, "details": details},
        )
        self._last_smoke_result = {
            "status": status,
            "checks": checks,
            "details": details,
            "correlation_id": correlation,
            "ts": int(time.time()),
        }
        if status == "failed":
            failed = ", ".join(key for key, passed in checks.items() if not passed)
            await log_error(
                self.bot, f"Post-deployment smoke test failed: {failed} [{correlation}]"
            )


def setup(bot: discord.Bot):
    bot.add_cog(OperationsCog(bot))
