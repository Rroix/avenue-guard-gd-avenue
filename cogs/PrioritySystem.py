from __future__ import annotations

import asyncio
import json
import math
import time

import discord
from discord.ext import commands

from services.priority_system import PrioritySystemService
from utils.errors import log_error
from utils.keepalive import set_public_level_data, set_public_model_status
from utils.mentions import no_mentions
from utils.priority_system import (
    public_lifecycle_state,
    public_priority_band,
    send_type_label,
)


class PrioritySystemCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.service = PrioritySystemService(bot)
        self._maintenance_task: asyncio.Task | None = None
        self._model_task: asyncio.Task | None = None
        self._next_maintenance_ts = 0
        self._last_maintenance: dict[str, int] = {}
        self._last_model_maintenance: dict = {}

    def cog_unload(self) -> None:
        if self._maintenance_task:
            self._maintenance_task.cancel()
        if self._model_task:
            self._model_task.cancel()

    async def close_resources(self) -> None:
        tasks = [task for task in (self._maintenance_task, self._model_task) if task and task is not asyncio.current_task() and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def start_background(self) -> None:
        if self._maintenance_task is None or self._maintenance_task.done():
            self._maintenance_task = asyncio.create_task(
                self._maintenance_loop(), name="avenue-guard:priority-maintenance"
            )
        if self._model_task is None or self._model_task.done():
            self._model_task = asyncio.create_task(
                self._model_loop(), name="avenue-guard:bayesian-model-maintenance"
            )
        try:
            await self.refresh_public_level_cache()
        except Exception as exc:
            await log_error(self.bot, f"Public level cache startup refresh deferred: {exc!r}")

    async def refresh_public_level_cache(self) -> int:
        guild_id = self.bot.config.get_int("guild", "allowed_guild_id", default=0)
        if not guild_id:
            set_public_level_data([])
            return 0
        active_rows = await self.bot.db.fetchall(
            "SELECT id FROM level_outreach_queue WHERE guild_id=? "
            "AND queue_state IN('queued','in_cycle') "
            "ORDER BY CASE WHEN priority_complete=1 THEN 0 ELSE 1 END, "
            "priority_points DESC,waiting_cycles DESC,queued_ts ASC,id ASC",
            (int(guild_id),),
        )
        active_total = len(active_rows)
        active_positions = {
            int(row["id"]): position
            for position, row in enumerate(active_rows, start=1)
        }
        rows = await self.bot.db.fetchall(
            "SELECT q.*,s.data_json FROM level_outreach_queue q "
            "LEFT JOIN level_request_submissions s ON s.guild_id=q.guild_id "
            "AND s.request_message_id=q.request_message_id "
            "WHERE q.guild_id=? AND q.queue_state!='hidden' ORDER BY q.level_id,q.queued_ts DESC,q.id DESC",
            (int(guild_id),),
        )
        levels = []
        public_probabilities: dict[str, dict | None] = {}
        seen: set[str] = set()
        for row in rows:
            level_id = str(row["level_id"] or "")
            if level_id in seen:
                continue
            seen.add(level_id)
            try:
                source = json.loads(row["data_json"] or "{}")
            except Exception:
                source = {}
            queue_state = str(row["queue_state"] or "")
            queue_position = active_positions.get(int(row["id"]))
            lifecycle = public_lifecycle_state(
                queue_state,
                submitted_to_mod_at=row["submitted_to_mod_ts"],
                rated_observed_at=row["rated_observed_ts"],
                rated_within_window=row["rated_within_window"],
                outcome_window_completed_at=row["outcome_window_completed_ts"],
            )
            send_type = str(row["send_type"] or "")
            priority_band = public_priority_band(queue_position, active_total)
            previous_band = str(row["last_public_priority_band"] or "")
            if priority_band and priority_band != previous_band:
                now = int(time.time())
                raw_priority = self.bot.config.data.get("priority_system", {})
                raw_priority = raw_priority if isinstance(raw_priority, dict) else {}
                cooldown_hours = int(raw_priority.get("priority_band_notification_cooldown_hours") or 12)
                cooldown_seconds = max(3600, cooldown_hours * 3600)
                last_notified = int(row["last_public_priority_band_notified_ts"] or 0)
                should_notify = bool(previous_band) and last_notified <= now - cooldown_seconds
                await self.bot.db.execute(
                    "UPDATE level_outreach_queue SET last_public_priority_band=?,"
                    "last_public_priority_band_notified_ts=CASE WHEN ?=1 THEN ? ELSE last_public_priority_band_notified_ts END WHERE id=?",
                    (priority_band, int(should_notify), now, int(row["id"])),
                )
                if should_notify:
                    try:
                        await self.service.notifications.emit(
                            int(guild_id),
                            level_id,
                            "priority_band_changed",
                            f"queue:{int(row['id'])}:band:{priority_band}:window:{now // cooldown_seconds}",
                            f"The public priority band changed from {previous_band.replace('_', ' ').title()} to {priority_band.replace('_', ' ').title()}.",
                            queue_id=int(row["id"]),
                            payload={"from": previous_band, "to": priority_band},
                        )
                    except Exception as exc:
                        await log_error(self.bot, f"Priority-band notification deferred: {exc!r}")
            if send_type not in public_probabilities:
                public_probabilities[send_type] = await self.service.models.public_projection(
                    int(guild_id), send_type
                )
            probability = public_probabilities[send_type]
            levels.append(
                {
                    "level_id": level_id,
                    "level_name": str(row["current_level_name"] or source.get("level_name") or "Unknown level"),
                    "uploader_name": str(row["uploader_name"] or ""),
                    "recommended_at": int(row["queued_ts"] or 0),
                    "recommendation_type": str(row["send_type"] or ""),
                    **({"probability": probability} if probability else {}),
                    "public_priority_band": priority_band,
                    "submitted_to_mod_at": row["submitted_to_mod_ts"],
                    "rated_observed_at": row["rated_observed_ts"],
                    "last_updated_at": int(
                        row["updated_ts"] or row["queued_ts"] or 0
                    ),
                    **lifecycle,
                }
            )
        set_public_level_data(levels)
        latest = await self.service.models.latest_models(int(guild_id))
        public_models = [
            {
                "model_key": item["model_key"],
                "status": item["status"],
                "evidence_strength": item["evidence_strength"],
                "generated_at": item["generated_ts"],
                "reason": item["reason"],
            }
            for item in latest.get("models", [])
            if item.get("subgroup_key") == "global"
        ]
        access_model = next(
            (item for item in public_models if item["model_key"] == "access_model_v1"),
            None,
        )
        if access_model:
            public_models.append(
                {
                    **access_model,
                    "model_key": "capacity_model_v1",
                    "reason": (
                        "Capacity forecasts use the current access posterior."
                        if access_model["status"] == "active"
                        else "Capacity estimates remain in shadow mode with the access model."
                    ),
                }
            )
        set_public_model_status(
            {
                "methodology_version": "2026.09",
                "network_era": str((latest.get("era") or {}).get("name") or ""),
                "models": public_models,
                "probabilities_published": self.service.models.settings.public_auto_activate
                and any(
                    item["status"] == "active"
                    for item in public_models
                    if item["model_key"] in {"access_model_v1", "rating_model_v1"}
                ),
                "updated_at": int(time.time()),
            }
        )
        return len(levels)

    def is_owner(self, user_id: int) -> bool:
        return int(user_id) in self.bot.config.get_int_list(
            "impact", "allowed_user_ids"
        )

    async def _prepare(self, ctx: discord.ApplicationContext) -> int | None:
        await ctx.defer(ephemeral=True)
        guild_id = self.bot.config.get_int("guild", "allowed_guild_id", default=0)
        if (
            ctx.guild is None
            or int(ctx.guild.id) != int(guild_id or 0)
            or not self.is_owner(ctx.user.id)
        ):
            await ctx.respond(
                "Priority outreach management is available only to the configured bot owner",
                ephemeral=True,
            )
            return None
        return int(guild_id)

    @staticmethod
    def _score(value) -> str:
        if value is None:
            return "CP pending"
        number = round(float(value), 2)
        return f"{number:.2f}".rstrip("0").rstrip(".")

    @staticmethod
    def _age(ts: int | None) -> str:
        if not ts:
            return "unknown"
        return f"<t:{int(ts)}:R>"

    @staticmethod
    def _refresh_due(ts: int | None) -> str:
        if ts is None:
            return "no eligible entries"
        if int(ts) <= int(time.time()):
            return "due now"
        return f"<t:{int(ts)}:R>"

    def _queue_line(self, row) -> str:
        level_name = str(row["current_level_name"] or "").strip()
        identity = f"{level_name} (`{row['level_id']}`)" if level_name else f"`{row['level_id']}`"
        cp = "?" if row["current_creator_points"] is None else str(int(row["current_creator_points"]))
        return (
            f"**#{int(row['id'])}** {identity}\n"
            f"{send_type_label(row['send_type'])} | CP {cp} | W {int(row['waiting_cycles'] or 0)} | "
            f"P **{self._score(row['priority_points'])}** | `{row['queue_state']}`"
        )

    async def dashboard_command(self, ctx: discord.ApplicationContext) -> None:
        guild_id = await self._prepare(ctx)
        if guild_id is None:
            return
        data = await self.service.dashboard(guild_id)
        cycle = data["active_cycle"]
        embed = discord.Embed(
            title="PPS Outreach Dashboard",
            description="Private deterministic queue management. Priority is a score, not a probability.",
            color=discord.Color.teal(),
        )
        embed.add_field(
            name="Active queue",
            value=(
                f"Eligible: **{data['active_queue']}**\n"
                f"Scored: **{data['scored']}**\nCP pending: **{data['cp_pending']}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Outcomes",
            value=(
                f"Awaiting: **{data['awaiting_outcome']}**\n"
                f"Rated observed: **{data['rated']}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Active cycle",
            value=(
                f"#{int(cycle['id'])} started {self._age(cycle['started_ts'])}"
                if cycle
                else "No active cycle"
            ),
            inline=True,
        )
        embed.add_field(
            name="Model and maintenance",
            value=(
                f"Model: `{data['model_version']}`\n"
                f"Next level refresh: {self._refresh_due(data['next_level_refresh'])}\n"
                f"Next CP refresh: {self._refresh_due(data['next_cp_refresh'])}\n"
                f"Next worker pass: {self._age(self._next_maintenance_ts)}\n"
                f"Last batch: `{self._last_maintenance or 'not run yet'}`"
            ),
            inline=False,
        )
        warning_lines = []
        if data["cp_pending"]:
            warning_lines.append(f"CP pending: **{data['cp_pending']}**")
        if data["invalid"]:
            warning_lines.append(f"Invalid/unavailable levels: **{data['invalid']}**")
        for error in data["recent_errors"]:
            warning_lines.append(
                f"<t:{int(error['last_seen_ts'])}:R> {str(error['last_message'])[:180]} "
                f"(x{int(error['occurrence_count'] or 1)})"
            )
        embed.add_field(
            name="Warnings",
            value="\n".join(warning_lines)[:1024] or "No active PPS warnings",
            inline=False,
        )
        if data["top"]:
            embed.add_field(
                name="Queue leaders",
                value="\n\n".join(self._queue_line(row) for row in data["top"])[:1024],
                inline=False,
            )
        state = await self.bot.db.fetchone(
            "SELECT wave_id,review_system_version FROM level_request_state WHERE guild_id=?",
            (guild_id,),
        )
        embed.add_field(
            name="Request review systems",
            value=(
                f"Current wave #{int(state['wave_id']) if state else 0}: "
                f"`{str(state['review_system_version'] or 'legacy') if state else 'legacy'}`\n"
                f"Next newly created wave: `{self.service.settings.new_wave_version}`"
            ),
            inline=False,
        )
        await ctx.respond(embed=embed, ephemeral=True)

    async def queue_command(self, ctx: discord.ApplicationContext, page: int) -> None:
        guild_id = await self._prepare(ctx)
        if guild_id is None:
            return
        rows, total = await self.service.queue_rows(guild_id, page=page, page_size=8)
        pages = max(1, math.ceil(total / 8))
        embed = discord.Embed(
            title="PPS Outreach Queue",
            description=(
                "Complete scores are ranked first by P, then raw waiting cycles, age, and queue ID. "
                "Unknown CP never receives the CP 0 bonus."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name=f"Page {max(1, page)} of {pages}",
            value="\n\n".join(self._queue_line(row) for row in rows)[:1024]
            if rows
            else "No active queue entries",
            inline=False,
        )
        embed.set_footer(text=f"{total} active queue entries")
        await ctx.respond(embed=embed, ephemeral=True)

    async def level_command(self, ctx: discord.ApplicationContext, identity: str) -> None:
        guild_id = await self._prepare(ctx)
        if guild_id is None:
            return
        row = await self.service.queue_entry(guild_id, identity)
        if not row:
            await ctx.respond("No matching PPS queue entry was found", ephemeral=True)
            return
        queue_id = int(row["id"])
        source = await self.bot.db.fetchone(
            "SELECT reviewed_by,reviewed_ts,review_text FROM level_request_submissions "
            "WHERE guild_id=? AND request_message_id=?",
            (guild_id, int(row["request_message_id"])),
        )
        attempts = await self.bot.db.fetchall(
            "SELECT a.*,c.status AS cycle_status FROM level_outreach_attempts a "
            "JOIN level_outreach_cycles c ON c.id=a.cycle_id WHERE a.queue_id=? ORDER BY a.created_ts DESC LIMIT 8",
            (queue_id,),
        )
        memberships = await self.bot.db.fetchall(
            "SELECT e.*,c.status,c.started_ts,c.completed_ts FROM level_outreach_cycle_entries e "
            "JOIN level_outreach_cycles c ON c.id=e.cycle_id WHERE e.queue_id=? ORDER BY e.cycle_id DESC LIMIT 8",
            (queue_id,),
        )
        events = await self.bot.db.fetchall(
            "SELECT event,actor_id,created_ts FROM workflow_events WHERE workflow_type='priority_system' "
            "AND entity_id LIKE ? ORDER BY created_ts DESC LIMIT 8",
            (f"queue:{queue_id}%",),
        )
        embed = discord.Embed(
            title=f"PPS Queue Entry #{queue_id}",
            description=f"Level `{row['level_id']}` | `{row['queue_state']}`",
            color=discord.Color.teal(),
        )
        embed.add_field(
            name="Source",
            value=(
                f"Wave **{int(row['wave_id'])}** | request <@{int(row['requester_id'])}>\n"
                f"Review message `{int(row['request_message_id'])}` | queued {self._age(row['queued_ts'])}\n"
                + (
                    f"Reviewed by <@{int(source['reviewed_by'])}> {self._age(source['reviewed_ts'])}"
                    if source and source["reviewed_by"] is not None
                    else "Reviewer record unavailable"
                )
            ),
            inline=False,
        )
        embed.add_field(
            name="Recommendation",
            value=f"{send_type_label(row['send_type'])} | model `{row['model_version']}`",
            inline=True,
        )
        cp_at = "unknown" if row["creator_points_at_recommendation"] is None else str(int(row["creator_points_at_recommendation"]))
        cp_now = "unknown" if row["current_creator_points"] is None else str(int(row["current_creator_points"]))
        embed.add_field(
            name="Creator Points",
            value=f"At recommendation: **{cp_at}**\nCurrent: **{cp_now}**\nChecked: {self._age(row['current_creator_points_checked_ts'])}",
            inline=True,
        )
        embed.add_field(
            name="Score",
            value=(
                f"F {self._score(row['prestige_component_f'])} + "
                f"G {self._score(row['creator_component_g'])} + "
                f"H {self._score(row['waiting_component_h'])} = "
                f"**{self._score(row['priority_points'])}**\nW: {int(row['waiting_cycles'] or 0)}"
            ),
            inline=False,
        )
        rated_within = row["rated_within_window"]
        embed.add_field(
            name="Submission and outcome evidence",
            value=(
                f"Confirmed submission: {self._age(row['submitted_to_mod_ts'])}\n"
                f"Rating observed: {self._age(row['rated_observed_ts'])}\n"
                f"30-day check due: {self._age(row['outcome_window_due_ts'])}\n"
                f"Window result: **{('rated' if int(rated_within) else 'not rated') if rated_within is not None else 'pending'}**"
            ),
            inline=False,
        )
        if memberships:
            embed.add_field(
                name="Cycle history",
                value="\n".join(
                    f"#{int(item['cycle_id'])} `{item['status']}` | submitted {bool(item['submitted_to_mod'])} | waited +{int(item['waiting_incremented'])}"
                    for item in memberships
                )[:1024],
                inline=False,
            )
        if attempts:
            embed.add_field(
                name="Private outreach attempts",
                value="\n".join(
                    (
                        f"<t:{int(item['created_ts'])}:R> `{item['status']}` via `{item['route_type']}` "
                        f"by <@{int(item['actor_id'])}>"
                        + (
                            f" | target: {str(item['private_target_label'])[:80]}"
                            if str(item["private_target_label"] or "").strip()
                            else ""
                        )
                        + (
                            f"\n{str(item['private_notes'])[:180]}"
                            if str(item["private_notes"] or "").strip()
                            else ""
                        )
                    )
                    for item in attempts
                )[:1024],
                inline=False,
            )
        if events:
            embed.add_field(
                name="Audit events",
                value="\n".join(
                    f"<t:{int(item['created_ts'])}:R> `{item['event']}`"
                    for item in events
                )[:1024],
                inline=False,
            )
        await ctx.respond(embed=embed, ephemeral=True, allowed_mentions=no_mentions())

    async def cycle_command(
        self,
        ctx: discord.ApplicationContext,
        action: str,
        cycle_id: int,
        notes: str,
    ) -> None:
        guild_id = await self._prepare(ctx)
        if guild_id is None:
            return
        action = str(action).casefold()
        try:
            if action == "start":
                cycle = await self.service.start_cycle(guild_id, ctx.user.id, notes)
                entries = await self.service.cycle_entries(int(cycle["id"]))
                await self.refresh_public_level_cache()
                await ctx.respond(
                    f"Started outreach cycle **#{int(cycle['id'])}** with **{len(entries)}** start-of-cycle candidates",
                    ephemeral=True,
                )
                return
            cycle = None
            if cycle_id:
                cycle = await self.bot.db.fetchone(
                    "SELECT * FROM level_outreach_cycles WHERE id=? AND guild_id=?",
                    (cycle_id, guild_id),
                )
            else:
                cycle = await self.service.active_cycle(guild_id)
                if cycle is None:
                    cycle = await self.bot.db.fetchone(
                        "SELECT * FROM level_outreach_cycles WHERE guild_id=? ORDER BY id DESC LIMIT 1",
                        (guild_id,),
                    )
            if not cycle:
                raise ValueError("No outreach cycle was found")
            cycle_id = int(cycle["id"])
            if action == "complete":
                _saved, incremented = await self.service.complete_cycle(
                    guild_id, cycle_id, ctx.user.id
                )
                await self.refresh_public_level_cache()
                await ctx.respond(
                    f"Completed successful cycle **#{cycle_id}**; **{incremented}** eligible unsubmitted levels gained one waiting cycle",
                    ephemeral=True,
                )
                return
            if action == "cancel":
                await self.service.cancel_cycle(
                    guild_id, cycle_id, ctx.user.id, notes
                )
                await self.refresh_public_level_cache()
                await ctx.respond(
                    f"Closed cycle **#{cycle_id}** as unsuccessful without increasing waiting scores",
                    ephemeral=True,
                )
                return
            entries = await self.service.cycle_entries(cycle_id)
            submitted = sum(int(row["submitted_to_mod"] or 0) for row in entries)
            embed = discord.Embed(
                title=f"Outreach Cycle #{cycle_id}",
                description=f"Status: **{cycle['status']}** | started {self._age(cycle['started_ts'])}",
                color=discord.Color.blurple(),
            )
            embed.add_field(
                name="Snapshot",
                value=f"Eligible at start: **{len(entries)}**\nConfirmed submissions: **{submitted}**",
                inline=False,
            )
            if entries:
                embed.add_field(
                    name="Candidates",
                    value="\n".join(
                        f"#{int(row['queue_id'])} `{row['level_id']}` | P {self._score(row['priority_points_snapshot'])} | "
                        f"{'submitted' if row['submitted_to_mod'] else 'waiting'}"
                        for row in entries[:15]
                    )[:1024],
                    inline=False,
                )
            await ctx.respond(embed=embed, ephemeral=True)
        except ValueError as exc:
            await ctx.respond(str(exc), ephemeral=True)

    async def outreach_command(
        self,
        ctx: discord.ApplicationContext,
        cycle_id: int,
        queue_id: int,
        status: str,
        route_type: str,
        target_label: str,
        notes: str,
    ) -> None:
        guild_id = await self._prepare(ctx)
        if guild_id is None:
            return
        try:
            attempt = await self.service.record_attempt(
                guild_id,
                cycle_id,
                queue_id,
                ctx.user.id,
                status=status,
                route_type=route_type,
                notes=notes,
                target_label=target_label,
                idempotency_key=f"discord:{int(ctx.interaction.id)}",
            )
            await self.refresh_public_level_cache()
            await ctx.respond(
                f"Recorded `{attempt['status']}` for queue **#{queue_id}** in cycle **#{cycle_id}**",
                ephemeral=True,
            )
        except ValueError as exc:
            await ctx.respond(str(exc), ephemeral=True)

    async def refresh_command(
        self,
        ctx: discord.ApplicationContext,
        queue_id: int,
        mode: str,
        creator_points: int,
        reason: str,
    ) -> None:
        guild_id = await self._prepare(ctx)
        if guild_id is None:
            return
        try:
            if mode == "override":
                row = await self.service.manual_cp_override(
                    guild_id,
                    queue_id,
                    ctx.user.id,
                    creator_points,
                    reason,
                )
            else:
                existing = await self.bot.db.fetchone(
                    "SELECT id FROM level_outreach_queue WHERE id=? AND guild_id=?",
                    (queue_id, guild_id),
                )
                if not existing:
                    raise ValueError("Queue entry not found")
                row = await self.service.refresh_queue_entry(
                    queue_id, force_level=True, force_cp=True
                )
            await self.refresh_public_level_cache()
            await ctx.respond(
                f"Queue **#{queue_id}** refreshed: CP **{row['current_creator_points'] if row['current_creator_points'] is not None else 'unknown'}**, priority **{self._score(row['priority_points'])}**",
                ephemeral=True,
            )
        except ValueError as exc:
            await ctx.respond(str(exc), ephemeral=True)

    async def stats_command(self, ctx: discord.ApplicationContext) -> None:
        guild_id = await self._prepare(ctx)
        if guild_id is None:
            return
        totals = await self.bot.db.fetchone(
            "SELECT COUNT(*) AS total,SUM(CASE WHEN priority_complete=1 THEN 1 ELSE 0 END) AS scored,"
            "SUM(CASE WHEN submitted_to_mod_ts IS NOT NULL THEN 1 ELSE 0 END) AS submitted,"
            "SUM(CASE WHEN rated_observed_ts IS NOT NULL AND submitted_to_mod_ts IS NOT NULL THEN 1 ELSE 0 END) AS rated_after,"
            "SUM(CASE WHEN outcome_window_completed_ts IS NOT NULL THEN 1 ELSE 0 END) AS windows,"
            "SUM(CASE WHEN rated_within_window=1 THEN 1 ELSE 0 END) AS rated_windows "
            "FROM level_outreach_queue WHERE guild_id=? AND queue_state!='hidden'",
            (guild_id,),
        )
        tiers = await self.bot.db.fetchall(
            "SELECT send_type,COUNT(*) AS c FROM level_outreach_queue WHERE guild_id=? AND queue_state!='hidden' GROUP BY send_type ORDER BY c DESC",
            (guild_id,),
        )
        cycles = await self.bot.db.fetchone(
            "SELECT COUNT(*) AS total,SUM(CASE WHEN successful=1 THEN 1 ELSE 0 END) AS successful FROM level_outreach_cycles WHERE guild_id=?",
            (guild_id,),
        )
        embed = discord.Embed(
            title="PPS Prospective Evidence",
            description="Observed operational evidence only; no Bayesian probability is calculated or displayed.",
            color=discord.Color.teal(),
        )
        embed.add_field(
            name="Queue evidence",
            value=(
                f"Recommendations: **{int(totals['total'] or 0)}**\n"
                f"Complete scores: **{int(totals['scored'] or 0)}**\n"
                f"Confirmed mod submissions: **{int(totals['submitted'] or 0)}**\n"
                f"Rated observations after confirmation: **{int(totals['rated_after'] or 0)}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Fixed outcome windows",
            value=(
                f"Completed: **{int(totals['windows'] or 0)}**\n"
                f"Rated within window: **{int(totals['rated_windows'] or 0)}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Outreach cycles",
            value=f"Recorded: **{int(cycles['total'] or 0)}**\nSuccessful: **{int(cycles['successful'] or 0)}**",
            inline=True,
        )
        embed.add_field(
            name="Recommendation tiers",
            value="\n".join(
                f"{send_type_label(row['send_type'])}: **{int(row['c'])}**"
                for row in tiers
            )
            or "No recommendations yet",
            inline=False,
        )
        await ctx.respond(embed=embed, ephemeral=True)

    async def _maintenance_loop(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(90)
        while not self.bot.is_closed():
            interval = self.service.settings.maintenance_interval_seconds
            self._next_maintenance_ts = int(time.time()) + interval
            try:
                guild_id = self.bot.config.get_int(
                    "guild", "allowed_guild_id", default=0
                )
                if guild_id:
                    self._last_maintenance = await self.service.maintenance_once(
                        int(guild_id)
                    )
                    await self.refresh_public_level_cache()
                    if self._last_maintenance.get("failed"):
                        await log_error(
                            self.bot,
                            "PPS maintenance completed with "
                            f"{self._last_maintenance['failed']} deferred queue refresh(es)",
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await log_error(
                    self.bot,
                    f"PPS maintenance loop error; durable queue retained: {exc!r}",
                )
            await asyncio.sleep(interval)

    async def _model_loop(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(105)
        while not self.bot.is_closed():
            interval = self.service.models.settings.refresh_seconds
            try:
                guild_id = self.bot.config.get_int("guild", "allowed_guild_id", default=0)
                if guild_id:
                    self._last_model_maintenance = await self.service.models.maintenance_once(int(guild_id))
                    await self.refresh_public_level_cache()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await log_error(
                    self.bot,
                    f"Bayesian shadow-model loop error; live PPS behavior unchanged: {exc!r}",
                )
            await asyncio.sleep(interval)


def setup(bot):
    bot.add_cog(PrioritySystemCog(bot))
