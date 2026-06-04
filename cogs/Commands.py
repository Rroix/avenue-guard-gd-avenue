import os
import json
import random
import secrets
import asyncio
import time
import re
import string
from typing import Optional

import discord
from discord.ext import commands

from utils.checks import is_admin_or_owner, is_mod
from utils.mentions import no_mentions
from utils.server_icons import (
    ensure_server_icon_config,
    is_valid_icon_url,
    normalize_server_icon_mode,
    parse_server_icon_index,
    VALID_SERVER_ICON_MODES,
)
from utils.timeutils import now_madrid, week_start_sunday
from utils.errors import log_error

DEFAULT_REQUEST_REVIEWER_ROLE_IDS = [785212232786640966, 1430214323720163498]

class CommandsCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        cfg = bot.config
        self.allowed_guild_id = cfg.get_int("guild", "allowed_guild_id") or 0
        # Hardcoded anti-spam cooldowns
        self._gamble_last_ts: dict[int, float] = {}  # user_id -> last /gambling time
        self._rps_last_ts: dict[int, float] = {}  # user_id -> last /rock-paper-scissors time

        # Command groups (guild-scoped for fast sync)
        self.bot_group = discord.SlashCommandGroup("bot", "Bot diagnostics", guild_ids=[self.allowed_guild_id] if self.allowed_guild_id else None)
        self.tracking_group = discord.SlashCommandGroup("tracking", "Tracking commands", guild_ids=[self.allowed_guild_id] if self.allowed_guild_id else None)
        self.ticket_group = discord.SlashCommandGroup("ticket", "Ticket commands", guild_ids=[self.allowed_guild_id] if self.allowed_guild_id else None)
        self.forum_group = discord.SlashCommandGroup("forum", "Forum moderation commands", guild_ids=[self.allowed_guild_id] if self.allowed_guild_id else None)
        self.requests_group = discord.SlashCommandGroup("requests", "Level request staff tools", guild_ids=[self.allowed_guild_id] if self.allowed_guild_id else None)
        self.server_icon_group = discord.SlashCommandGroup("server_icon", "Server icon rotation tools", guild_ids=[self.allowed_guild_id] if self.allowed_guild_id else None)

        # register commands
        self.bot_group.command(name="health", description="Show bot health and live system status")(self.bot_health)
        self.bot_group.command(name="config_check", description="Check configured channels and roles")(self.bot_config_check)
        self.bot_group.command(name="doctor", description="Run deep bot permission diagnostics")(self.bot_doctor)

        self.tracking_group.command(name="top", description="Show the current week's top 20 active members")(self.tracking_top)
        self.tracking_group.command(name="reset", description="Reset current week's tracking stats")(self.tracking_reset)
        self.tracking_group.command(name="me", description="Show your activity stats for this week")(self.tracking_me)
        self.tracking_group.command(name="force_dm", description="Force-send the weekly request DM to a user")(self.tracking_force_dm)
        self.tracking_group.command(name="disable_reward", description="Disable this week's automatic weekly request reward")(self.tracking_disable_reward)
        self.tracking_group.command(name="enable_reward", description="Re-enable this week's automatic weekly request reward")(self.tracking_enable_reward)

        self.ticket_group.command(name="close", description="Close the current ticket channel")(self.ticket_close)
        self.forum_group.command(name="required_word", description="View or update a forum required word")(self.forum_required_word)
        self.requests_group.command(name="pending", description="Show and filter pending request reviews")(self.requests_pending)
        self.requests_group.command(name="history", description="Show request edit history")(self.requests_history)
        self.requests_group.command(name="repair", description="Repair request system messages")(self.requests_repair)
        self.server_icon_group.command(name="status", description="Show server icon rotation status")(self.server_icon_status)
        self.server_icon_group.command(name="mode", description="Set server icon rotation mode")(self.server_icon_mode)
        self.server_icon_group.command(name="add", description="Add a server icon URL")(self.server_icon_add)
        self.server_icon_group.command(name="replace", description="Replace a server icon URL by number")(self.server_icon_replace)
        self.server_icon_group.command(name="remove", description="Remove a server icon URL by number")(self.server_icon_remove)
        self.server_icon_group.command(name="set", description="Change to a specific configured server icon now")(self.server_icon_set)
        self.server_icon_group.command(name="next", description="Change to the next configured server icon now")(self.server_icon_next)

        bot.add_application_command(self.bot_group)
        bot.add_application_command(self.tracking_group)
        bot.add_application_command(self.ticket_group)
        bot.add_application_command(self.forum_group)
        bot.add_application_command(self.requests_group)
        bot.add_application_command(self.server_icon_group)

        @bot.slash_command(name="resync", description="Reload config, views, and responses without restart", guild_ids=[self.allowed_guild_id] if self.allowed_guild_id else None)
        async def resync(ctx: discord.ApplicationContext):
            await self._resync(ctx)

        @bot.slash_command(name="restart", description="Restart the bot", guild_ids=[self.allowed_guild_id] if self.allowed_guild_id else None)
        async def restart(ctx: discord.ApplicationContext):
            await self._restart(ctx)

        @bot.slash_command(name="dance", description="Send a dance GIF", guild_ids=[self.allowed_guild_id] if self.allowed_guild_id else None)
        async def dance(ctx: discord.ApplicationContext):
            await self._dance(ctx)

        @bot.slash_command(name="rock-paper-scissors", description="Play Rock Paper Scissors", guild_ids=[self.allowed_guild_id] if self.allowed_guild_id else None)
        async def rps(ctx: discord.ApplicationContext):
            await self._rps(ctx)

        @bot.slash_command(name="gambling", description="Try your luck in a quick slots game", guild_ids=[self.allowed_guild_id] if self.allowed_guild_id else None)
        async def gambling(ctx: discord.ApplicationContext):
            await self._gambling(ctx)

    def _in_allowed_guild(self, ctx: discord.ApplicationContext) -> bool:
        return ctx.guild is not None and ctx.guild.id == self.allowed_guild_id

    async def _defer(self, ctx: discord.ApplicationContext, ephemeral: bool = True) -> None:
        try:
            await ctx.defer(ephemeral=ephemeral)
        except Exception:
            pass

    async def _send(self, ctx: discord.ApplicationContext, *args, **kwargs):
        try:
            return await ctx.followup.send(*args, **kwargs)
        except Exception:
            return await ctx.respond(*args, **kwargs)

    async def _log_admin_action(self, guild: discord.Guild, user_id: int, action: str, detail: str = "") -> None:
        channel_id = self.bot.config.get_int("channels", "general_logging_channel_id", default=0)
        channel = guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            return
        embed = discord.Embed(
            title="Admin Action",
            description=str(action).replace("_", " ").title(),
            color=discord.Color.blurple(),
            timestamp=now_madrid(),
        )
        embed.add_field(name="Admin", value=f"<@{int(user_id)}>\n`{int(user_id)}`", inline=True)
        embed.add_field(name="Action", value=f"`{str(action)[:120]}`", inline=True)
        if detail:
            embed.add_field(name="Details", value=str(detail)[:1024], inline=False)
        try:
            await channel.send(embed=embed, allowed_mentions=no_mentions())
        except Exception as e:
            await log_error(self.bot, f"Could not log admin action {action}: {repr(e)}")

    async def _is_admin_ctx(self, ctx: discord.ApplicationContext) -> bool:
        if ctx.guild is None:
            return False
        member = await self._resolve_member(ctx.guild, ctx.user)
        admin_roles = self.bot.config.get_int_list("roles", "admin_owner_role_ids")
        return member is not None and is_admin_or_owner(member, admin_roles)

    async def _is_mod_ctx(self, ctx: discord.ApplicationContext) -> bool:
        if ctx.guild is None:
            return False
        member = await self._resolve_member(ctx.guild, ctx.user)
        mod_role_id = self.bot.config.get_int("roles", "MOD_ROLE_ID") or 0
        allow_manage_guild = bool(self.bot.config.get("permissions", "manage_guild_counts_as_mod", default=True))
        return member is not None and is_mod(member, mod_role_id, allow_manage_guild=allow_manage_guild)

    def _request_reviewer_role_ids(self) -> list[int]:
        configured = self.bot.config.get_int_list("level_requests", "reviewer_role_ids")
        return configured or list(DEFAULT_REQUEST_REVIEWER_ROLE_IDS)

    async def _is_request_staff_ctx(self, ctx: discord.ApplicationContext) -> bool:
        if ctx.guild is None:
            return False
        member = await self._resolve_member(ctx.guild, ctx.user)
        if member is None:
            return False
        allow_manage_guild = bool(self.bot.config.get("permissions", "manage_guild_counts_as_mod", default=True))
        if is_mod(member, self.bot.config.get_int("roles", "MOD_ROLE_ID") or 0, allow_manage_guild=allow_manage_guild):
            return True
        if is_admin_or_owner(member, self.bot.config.get_int_list("roles", "admin_owner_role_ids")):
            return True
        role_ids = set(self._request_reviewer_role_ids())
        return any(role.id in role_ids for role in getattr(member, "roles", []))

    def _server_icon_status_embed(self) -> discord.Embed:
        cfg = ensure_server_icon_config(self.bot.config)
        urls = list(cfg.get("urls", []) or [])
        mode = normalize_server_icon_mode(cfg.get("mode"))
        current_index = parse_server_icon_index(cfg.get("current_index", -1), len(urls))
        interval = int(cfg.get("interval_seconds", 86400) or 86400)
        last_changed = int(cfg.get("last_changed_ts", 0) or 0)
        last_error = str(cfg.get("last_error", "") or "").strip()
        last_error_ts = int(cfg.get("last_error_ts", 0) or 0)

        embed = discord.Embed(
            title="Server Icon Rotation",
            description="Configured server profile picture rotation.",
            color=discord.Color.blurple(),
            timestamp=now_madrid(),
        )
        embed.add_field(name="Mode", value=f"`{mode}`", inline=True)
        embed.add_field(name="Interval", value=f"{interval // 60} minutes", inline=True)
        if last_changed:
            embed.add_field(name="Last change", value=f"<t:{last_changed}:R>", inline=True)
        else:
            embed.add_field(name="Last change", value="Never", inline=True)

        if mode != "disabled" and urls:
            next_ts = (last_changed or int(time.time())) + interval
            embed.add_field(name="Next automatic change", value=f"<t:{next_ts}:R>", inline=False)
        else:
            embed.add_field(name="Next automatic change", value="Not scheduled while disabled or empty.", inline=False)

        if urls:
            lines = []
            for idx, url in enumerate(urls, start=1):
                marker = "current" if idx - 1 == current_index else ""
                suffix = f" - {marker}" if marker else ""
                lines.append(f"`{idx}` {url[:120]}{suffix}")
            embed.add_field(name=f"Configured icons ({len(urls)})", value="\n".join(lines)[:1024], inline=False)
        else:
            embed.add_field(name="Configured icons", value="No URLs configured.", inline=False)
        if last_error:
            when = f" <t:{last_error_ts}:R>" if last_error_ts else ""
            embed.add_field(name="Last error", value=f"{last_error[:900]}{when}", inline=False)
        return embed

    def _notify_background_config_reload(self) -> None:
        background = self.bot.get_cog("BackgroundCog")
        fn = getattr(background, "on_config_reload", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass

    async def _save_server_icon_config(self, ctx: discord.ApplicationContext, action: str, detail: str) -> bool:
        try:
            self.bot.config.save()
            self._notify_background_config_reload()
        except Exception as e:
            await log_error(self.bot, f"Failed to save server icon config: {repr(e)}")
            await ctx.respond("I couldn't save the server icon config.", ephemeral=True)
            return False
        await self._log_admin_action(ctx.guild, ctx.user.id, action, detail)
        return True

    async def server_icon_status(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        if not await self._is_admin_ctx(ctx):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)
        await ctx.respond(embed=self._server_icon_status_embed(), ephemeral=True, allowed_mentions=no_mentions())

    async def server_icon_mode(
        self,
        ctx: discord.ApplicationContext,
        mode: discord.Option(str, "Mode to use: random, linear, or disabled"),
    ):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        if not await self._is_admin_ctx(ctx):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

        raw_mode = str(mode or "").strip().casefold()
        if raw_mode not in VALID_SERVER_ICON_MODES:
            return await ctx.respond("Mode must be `random`, `linear`, or `disabled`.", ephemeral=True)
        cfg = ensure_server_icon_config(self.bot.config)
        cfg["mode"] = raw_mode
        if not await self._save_server_icon_config(ctx, "server_icon_mode_updated", f"mode={raw_mode}"):
            return
        await ctx.respond(f"Server icon rotation mode is now `{raw_mode}`.", ephemeral=True)

    async def server_icon_add(
        self,
        ctx: discord.ApplicationContext,
        url: discord.Option(str, "Direct image URL to add to the rotation list"),
    ):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        if not await self._is_admin_ctx(ctx):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)
        if not is_valid_icon_url(url):
            return await ctx.respond("That does not look like a valid HTTP image URL.", ephemeral=True)

        cfg = ensure_server_icon_config(self.bot.config)
        urls = list(cfg.get("urls", []) or [])
        cleaned = str(url).strip()
        if cleaned in urls:
            return await ctx.respond("That icon URL is already in the list.", ephemeral=True)
        if len(urls) >= 25:
            return await ctx.respond("The icon list is full. Remove one before adding another.", ephemeral=True)
        urls.append(cleaned)
        cfg["urls"] = urls
        if not await self._save_server_icon_config(ctx, "server_icon_url_added", f"count={len(urls)}"):
            return
        await ctx.respond(f"Added server icon URL as image #{len(urls)}.", ephemeral=True)

    async def server_icon_replace(
        self,
        ctx: discord.ApplicationContext,
        number: discord.Option(int, "One-based icon number to replace from /server_icon status"),
        url: discord.Option(str, "New direct image URL"),
    ):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        if not await self._is_admin_ctx(ctx):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)
        if not is_valid_icon_url(url):
            return await ctx.respond("That does not look like a valid HTTP image URL.", ephemeral=True)

        cfg = ensure_server_icon_config(self.bot.config)
        urls = list(cfg.get("urls", []) or [])
        idx = int(number) - 1
        if idx < 0 or idx >= len(urls):
            return await ctx.respond("That icon number does not exist.", ephemeral=True)
        old_url = urls[idx]
        urls[idx] = str(url).strip()
        cfg["urls"] = urls
        if parse_server_icon_index(cfg.get("current_index", -1), len(urls)) == idx or str(cfg.get("current_url", "") or "") == old_url:
            cfg["current_index"] = -1
            cfg["current_url"] = ""
        if not await self._save_server_icon_config(ctx, "server_icon_url_replaced", f"number={number}"):
            return
        await ctx.respond(f"Replaced server icon image #{number}.", ephemeral=True)

    async def server_icon_remove(
        self,
        ctx: discord.ApplicationContext,
        number: discord.Option(int, "One-based icon number to remove from /server_icon status"),
    ):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        if not await self._is_admin_ctx(ctx):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

        cfg = ensure_server_icon_config(self.bot.config)
        urls = list(cfg.get("urls", []) or [])
        idx = int(number) - 1
        if idx < 0 or idx >= len(urls):
            return await ctx.respond("That icon number does not exist.", ephemeral=True)
        removed = urls.pop(idx)
        current_index = parse_server_icon_index(cfg.get("current_index", -1), len(urls) + 1)
        if current_index == idx:
            cfg["current_index"] = -1
            cfg["current_url"] = ""
        elif current_index > idx:
            cfg["current_index"] = current_index - 1
        if str(cfg.get("current_url", "") or "") == removed:
            cfg["current_url"] = ""
        cfg["urls"] = urls
        if not await self._save_server_icon_config(ctx, "server_icon_url_removed", f"number={number} url={removed[:120]}"):
            return
        await ctx.respond(f"Removed server icon image #{number}.", ephemeral=True)

    async def server_icon_set(
        self,
        ctx: discord.ApplicationContext,
        number: discord.Option(int, "One-based icon number from /server_icon status"),
    ):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        if not await self._is_admin_ctx(ctx):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)
        await self._defer(ctx, ephemeral=True)
        cfg = ensure_server_icon_config(self.bot.config)
        urls = list(cfg.get("urls", []) or [])
        target_index = parse_server_icon_index(int(number) - 1, len(urls))
        if target_index < 0:
            return await self._send(ctx, "That icon number does not exist.", ephemeral=True)
        background = self.bot.get_cog("BackgroundCog")
        rotate = getattr(background, "rotate_server_icon_once", None)
        if not callable(rotate):
            return await self._send(ctx, "Server icon rotation is not available right now.", ephemeral=True)
        ok, message = await rotate(ctx.guild, force=True, actor_id=ctx.user.id, target_index=target_index)
        if ok:
            await self._log_admin_action(ctx.guild, ctx.user.id, "server_icon_set_now", message)
        await self._send(ctx, message, ephemeral=True)

    async def server_icon_next(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        if not await self._is_admin_ctx(ctx):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)
        await self._defer(ctx, ephemeral=True)
        background = self.bot.get_cog("BackgroundCog")
        rotate = getattr(background, "rotate_server_icon_once", None)
        if not callable(rotate):
            return await self._send(ctx, "Server icon rotation is not available right now.", ephemeral=True)
        ok, message = await rotate(ctx.guild, force=True, actor_id=ctx.user.id)
        if ok:
            await self._log_admin_action(ctx.guild, ctx.user.id, "server_icon_changed_now", message)
        await self._send(ctx, message, ephemeral=True)

    async def _resolve_member(self, guild: discord.Guild, user) -> Optional[discord.Member]:
        if isinstance(user, discord.Member):
            return user
        user_id = getattr(user, "id", user)
        try:
            user_id = int(user_id)
        except Exception:
            return None
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except Exception:
            return None

    # --- /bot diagnostics ---

    async def bot_health(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        if not await self._is_admin_ctx(ctx):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

        await self._defer(ctx, ephemeral=True)
        guild = ctx.guild

        async def _count(sql: str, params: tuple) -> int:
            try:
                row = await self.bot.db.fetchone(sql, params)
                return int(row["c"]) if row and row["c"] is not None else 0
            except Exception:
                return 0

        db_ok = True
        db_note = "Connected"
        try:
            await self.bot.db.fetchone("SELECT 1 AS c")
        except Exception as e:
            db_ok = False
            db_note = type(e).__name__

        open_tickets = await _count("SELECT COUNT(*) AS c FROM tickets WHERE guild_id=? AND status IN ('open','closing_prompted')", (guild.id,))
        active_weekly = await _count("SELECT COUNT(*) AS c FROM weekly_sessions WHERE guild_id=? AND active=1", (guild.id,))
        pending_live = await _count("SELECT COUNT(*) AS c FROM level_request_submissions WHERE guild_id=? AND status='pending'", (guild.id,))
        pending_weekly = await _count("SELECT COUNT(*) AS c FROM weekly_request_reviews WHERE guild_id=? AND status='pending'", (guild.id,))

        request_state = "Unknown"
        try:
            request_row = await self.bot.db.fetchone("SELECT state, wave_id, submitted_count FROM level_request_state WHERE guild_id=?", (guild.id,))
        except Exception:
            request_row = None
        if request_row:
            request_state = f"{request_row['state']} | wave {request_row['wave_id']} | submitted {request_row['submitted_count']}"

        def _task_state(cog_name: str, attr: str) -> str:
            cog = self.bot.get_cog(cog_name)
            task = getattr(cog, attr, None) if cog else None
            if task is None:
                return "missing"
            if hasattr(task, "is_running"):
                try:
                    return "running" if task.is_running() else "stopped"
                except Exception:
                    return "unknown"
            if hasattr(task, "done"):
                return "done" if task.done() else "running"
            return "unknown"

        embed = discord.Embed(title="Avenue Guard Health", color=discord.Color.green() if db_ok else discord.Color.red())
        embed.add_field(name="Database", value=db_note, inline=True)
        embed.add_field(name="Latency", value=f"{round(self.bot.latency * 1000)} ms", inline=True)
        embed.add_field(name="Loaded cogs", value=str(len(self.bot.cogs)), inline=True)
        embed.add_field(
            name="Live State",
            value=(
                f"Open tickets: **{open_tickets}**\n"
                f"Weekly sessions: **{active_weekly}**\n"
                f"Pending requests: **{pending_live}** live / **{pending_weekly}** weekly\n"
                f"Request state: **{request_state}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Background Tasks",
            value=(
                f"Weekly scan: `{_task_state('TrackingCog', '_weekly_task')}`\n"
                f"Weekly timeout/reminders: `{_task_state('TrackingCog', '_timeout_task')}`\n"
                f"Activity flush: `{_task_state('TrackingCog', '_activity_flush_task')}`\n"
                f"Ticket scan: `{_task_state('HelpCog', '_ticket_scan_task')}`\n"
                f"Daily snapshot: `{_task_state('BackgroundCog', 'update_snapshot')}`\n"
                f"Status rotation: `{_task_state('BackgroundCog', 'rotate_status')}`\n"
                f"Server icon rotation: `{_task_state('BackgroundCog', 'rotate_server_icon')}`"
            ),
            inline=False,
        )
        await self._send(ctx, embed=embed, ephemeral=True)

    async def bot_doctor(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        if not await self._is_admin_ctx(ctx):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

        await self._defer(ctx, ephemeral=True)
        guild = ctx.guild
        cfg = self.bot.config
        me = guild.me or guild.get_member(self.bot.user.id)
        issues: list[str] = []
        ok: list[str] = []
        if me is None:
            issues.append("bot member: could not resolve bot member in guild")

        def channel_perm_report(label: str, channel_id: int, required: tuple[str, ...]):
            if me is None:
                return
            if not channel_id:
                issues.append(f"{label}: not configured")
                return
            channel = guild.get_channel(int(channel_id))
            if channel is None:
                issues.append(f"{label}: channel `{channel_id}` missing")
                return
            perms = channel.permissions_for(me)
            missing = [perm for perm in required if not bool(getattr(perms, perm, False))]
            if missing:
                issues.append(f"{label}: missing {', '.join(missing)}")
            else:
                ok.append(f"{label}: OK")

        common_text_perms = ("view_channel", "send_messages", "embed_links", "read_message_history")
        channel_checks = {
            "general logs": cfg.get_int("channels", "general_logging_channel_id"),
            "global errors": cfg.get_int("channels", "global_error_log_channel_id"),
            "weekly requests": cfg.get_int("channels", "weekly_request_channel_ID"),
            "request button": cfg.get_int("level_requests", "request_channel"),
            "level requested": cfg.get_int("level_requests", "level_requested"),
            "sent results": cfg.get_int("level_requests", "sent_channel"),
            "rejected results": cfg.get_int("level_requests", "rejected_channel"),
            "appeals logs": cfg.get_int("channels", "appeals_log_channel_id"),
            "reports logs": cfg.get_int("channels", "reports_log_channel_id"),
            "bugs logs": cfg.get_int("channels", "bot_issues_log_channel_id"),
            "transcript requests": cfg.get_int("channels", "transcript_requests_channel_id"),
        }
        for label, channel_id in channel_checks.items():
            channel_perm_report(label, channel_id, common_text_perms)

        category_id = cfg.get_int("tickets", "ticket_category_id")
        category = guild.get_channel(category_id) if category_id else None
        if me is None:
            pass
        elif isinstance(category, discord.CategoryChannel):
            perms = category.permissions_for(me)
            missing = [perm for perm in ("view_channel", "manage_channels") if not bool(getattr(perms, perm, False))]
            if missing:
                issues.append(f"ticket category: missing {', '.join(missing)}")
            else:
                ok.append("ticket category: OK")
        else:
            issues.append("ticket category: missing or wrong type")

        bot_top_role = getattr(me, "top_role", None) if me is not None else None
        managed_role_ids = [
            cfg.get_int("roles", "restriction_role_ID"),
            cfg.get_int("level_requests", "has_requested_role_id"),
            cfg.get_int("level_requests", "request_banned_role_id"),
            cfg.get_int("roles", "gambling_reward_role_id"),
            cfg.get_int("roles", "rps_streak_role_id"),
        ]
        for role_id in [role_id for role_id in managed_role_ids if role_id]:
            role = guild.get_role(int(role_id))
            if role is None:
                issues.append(f"managed role `{role_id}`: missing")
                continue
            if bot_top_role is None:
                issues.append(f"{role.name}: bot role hierarchy could not be checked")
            elif role >= bot_top_role:
                issues.append(f"{role.name}: bot role is not above this role")
            else:
                ok.append(f"{role.name}: role hierarchy OK")

        staff_ping_role_id = cfg.get_int("tickets", "staff_ping_role_id", default=0)
        if staff_ping_role_id:
            if guild.get_role(staff_ping_role_id) is None:
                issues.append(f"ticket staff ping role `{staff_ping_role_id}`: missing")
            else:
                ok.append("ticket staff ping role: OK")

        if me is not None:
            guild_perms = getattr(me, "guild_permissions", None)
            if guild_perms is not None and bool(getattr(guild_perms, "manage_guild", False)):
                ok.append("server icon rotation permission: OK")
            else:
                issues.append("server icon rotation: bot needs Manage Server to edit the server icon")

        request_cog = self.bot.get_cog("RequestLevelsCog")
        if request_cog is None:
            issues.append("RequestLevelsCog: not loaded")
        else:
            state = await request_cog._get_state(guild.id)
            if not state:
                issues.append("request state: missing database row")
            elif not state["request_message_id"]:
                issues.append("request button: no saved message ID; run /refresh-request-button")
            else:
                ok.append("request state: OK")

        embed = discord.Embed(
            title="Bot Doctor",
            description=f"Deep diagnostics finished. **{len(ok)}** checks OK, **{len(issues)}** issues.",
            color=discord.Color.green() if not issues else discord.Color.orange(),
            timestamp=now_madrid(),
        )
        embed.add_field(name="Issues", value="\n".join(f"- {item}" for item in issues[:12])[:1024] or "No issues found.", inline=False)
        embed.add_field(name="Healthy Checks", value="\n".join(f"- {item}" for item in ok[:12])[:1024] or "No healthy checks recorded.", inline=False)
        if len(issues) > 12 or len(ok) > 12:
            embed.set_footer(text=f"Showing first 12 issues and first 12 healthy checks.")
        await self._send(ctx, embed=embed, ephemeral=True)

    def _template_variables(self, text: str) -> tuple[set[str], Optional[str]]:
        variables: set[str] = set()
        try:
            for _, field_name, _, _ in string.Formatter().parse(str(text or "")):
                if not field_name:
                    continue
                root = re.split(r"[.\[]", field_name, maxsplit=1)[0]
                if root:
                    variables.add(root)
        except Exception as e:
            return variables, type(e).__name__
        return variables, None

    def _request_template_allowed_vars(self) -> set[str]:
        return {
            "state",
            "wave_id",
            "submitted_count",
            "request_limit",
            "close_ts",
            "total_requests",
            "reviewed_count",
            "sent_count",
            "not_sent_count",
            "rejected_count",
            "other_count",
            "level_doesnt_exist_count",
            "stolen_level_count",
            "already_rated_count",
            "pending_count",
            "left_to_review",
            "reviewed_percent",
            "pending_percent",
            "sent_percent",
            "not_sent_percent",
            "sent_percent_reviewed",
            "not_sent_percent_reviewed",
            "reviewer_stats",
            "summary_color",
            "level_id",
            "level_name",
            "creators",
            "level_showcase",
            "showcase",
            "notes",
            "requester_id",
            "requester_mention",
            "submitted_ts",
            "submitted_ago",
            "edit_deadline_ts",
            "edit_deadline",
            "edit_count",
            "duplicate_history_warning",
            "level_validation_warning",
            "level_validation_sources",
            "level_validation_checked",
            "level_validation_refresh",
            "level_exists",
            "level_rated",
            "level_requires_showcase",
            "gd_level_name",
            "gd_creator",
            "gd_difficulty",
            "gd_length",
            "gd_stars",
            "gd_rated",
            "gd_demon",
            "gd_platformer",
            "gd_featured",
            "gd_epic",
            "gd_flags",
            "gd_info",
            "result",
            "result_key",
            "review",
            "reviewer_id",
            "reviewer_mention",
            "pending_color",
            "result_color",
            "review_kind",
            "week_start",
            "rank",
            "weekly_rank",
            "request_content",
            "user_id",
            "user_mention",
            "request_text",
            "deadline",
            "reminder_text",
        }

    def _looks_like_color_value(self, value: str) -> bool:
        text = str(value or "").strip()
        if not text or "{" in text:
            return True
        if re.fullmatch(r"#?[0-9a-fA-F]{6}", text):
            return True
        return text.casefold() in {
            "blue",
            "red",
            "green",
            "purple",
            "gold",
            "orange",
            "teal",
            "blurple",
            "dark",
            "light",
            "grey",
            "gray",
            "black",
            "white",
            "pink",
        }

    def _validate_request_templates(self, issues: list[str]) -> int:
        cfg = self.bot.config.get("level_requests", default={}) or {}
        if not isinstance(cfg, dict):
            issues.append("level_requests: must be an object")
            return 0

        allowed = self._request_template_allowed_vars()
        template_keys = (
            "request_button_embed",
            "wave_summary_embed",
            "level_requested_embed",
            "level_reviewed_embed",
            "sent_result_embed",
            "rejected_result_embed",
            "other_result_embed",
            "weekly_request_dm_embed",
            "weekly_request_reminder_embed",
            "weekly_request_submitted_embed",
        )
        checked = 0

        def check_text(label: str, value: str):
            variables, parse_error = self._template_variables(value)
            if parse_error:
                issues.append(f"{label}: invalid template braces ({parse_error})")
                return
            unknown = sorted(var for var in variables if var not in allowed)
            if unknown:
                issues.append(f"{label}: unknown template variable(s) {', '.join(unknown[:5])}")

        def walk(label: str, node):
            if isinstance(node, str):
                check_text(label, node)
            elif isinstance(node, list):
                for idx, item in enumerate(node, start=1):
                    walk(f"{label}[{idx}]", item)
            elif isinstance(node, dict):
                if "color" in node and not self._looks_like_color_value(str(node.get("color") or "")):
                    issues.append(f"{label}.color: unknown color `{node.get('color')}`")
                fields = node.get("fields")
                if fields is not None:
                    if not isinstance(fields, list):
                        issues.append(f"{label}.fields: must be a list")
                    else:
                        for idx, field in enumerate(fields, start=1):
                            if not isinstance(field, dict):
                                issues.append(f"{label}.fields[{idx}]: must be an object")
                                continue
                            if not str(field.get("name") or "").strip():
                                issues.append(f"{label}.fields[{idx}].name: missing")
                            if not str(field.get("value") or "").strip():
                                issues.append(f"{label}.fields[{idx}].value: missing")
                for key, value in node.items():
                    if key.startswith("_"):
                        continue
                    walk(f"{label}.{key}", value)

        for key in template_keys:
            template = cfg.get(key)
            if template is None:
                continue
            before = len(issues)
            if not isinstance(template, dict):
                issues.append(f"level_requests.{key}: must be an object")
                continue
            walk(f"level_requests.{key}", template)
            if len(issues) == before:
                checked += 1
        return checked

    async def bot_config_check(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        if not await self._is_admin_ctx(ctx):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

        await self._defer(ctx, ephemeral=True)
        guild = ctx.guild
        issues: list[str] = []
        ok_count = 0

        def check_channel(label: str, channel_id: int, expected_type=None):
            nonlocal ok_count
            if not channel_id:
                issues.append(f"{label}: not configured")
                return
            channel = guild.get_channel(int(channel_id))
            if channel is None:
                issues.append(f"{label}: missing `<#{channel_id}>`")
                return
            if expected_type is not None and not isinstance(channel, expected_type):
                issues.append(f"{label}: wrong channel type `{type(channel).__name__}`")
                return
            ok_count += 1

        def check_role(label: str, role_id: int):
            nonlocal ok_count
            if not role_id:
                issues.append(f"{label}: not configured")
                return
            if guild.get_role(int(role_id)) is None:
                issues.append(f"{label}: missing role `{role_id}`")
                return
            ok_count += 1

        cfg = self.bot.config
        for key in (
            "autodelete_channel_id",
            "weekly_request_channel_ID",
            "dm_fail_log_channel_id",
            "global_error_log_channel_id",
            "general_logging_channel_id",
            "appeals_log_channel_id",
            "reports_log_channel_id",
            "bot_issues_log_channel_id",
            "transcript_requests_channel_id",
        ):
            check_channel(f"channels.{key}", cfg.get_int("channels", key), discord.TextChannel)

        check_channel("tickets.ticket_category_id", cfg.get_int("tickets", "ticket_category_id"), discord.CategoryChannel)
        staff_ping_role_id = cfg.get_int("tickets", "staff_ping_role_id", default=0)
        if staff_ping_role_id:
            check_role("tickets.staff_ping_role_id", staff_ping_role_id)
        for key in ("request_channel", "level_requested", "sent_channel", "rejected_channel"):
            check_channel(f"level_requests.{key}", cfg.get_int("level_requests", key), discord.TextChannel)

        for key in ("MOD_ROLE_ID", "restriction_role_ID", "gambling_reward_role_id", "rps_streak_role_id"):
            check_role(f"roles.{key}", cfg.get_int("roles", key))
        for idx, role_id in enumerate(cfg.get_int_list("roles", "admin_owner_role_ids"), start=1):
            check_role(f"roles.admin_owner_role_ids[{idx}]", role_id)
        for idx, role_id in enumerate(cfg.get_int_list("roles", "excluded_tracking_role_id"), start=1):
            check_role(f"roles.excluded_tracking_role_id[{idx}]", role_id)
        for key in ("has_requested_role_id", "request_banned_role_id"):
            check_role(f"level_requests.{key}", cfg.get_int("level_requests", key))
        for idx, role_id in enumerate(cfg.get_int_list("level_requests", "required_role_ids"), start=1):
            check_role(f"level_requests.required_role_ids[{idx}]", role_id)
        for idx, role_id in enumerate(self._request_reviewer_role_ids(), start=1):
            check_role(f"level_requests.reviewer_role_ids[{idx}]", role_id)
        ok_count += self._validate_request_templates(issues)

        raw_server_icon_cfg = cfg.get("background", "server_icon_rotation", default={}) or {}
        raw_server_icon_mode = str(raw_server_icon_cfg.get("mode", "disabled") if isinstance(raw_server_icon_cfg, dict) else "disabled").strip().casefold()
        server_icon_cfg = ensure_server_icon_config(cfg)
        server_icon_mode = normalize_server_icon_mode(server_icon_cfg.get("mode"))
        server_icon_urls = list(server_icon_cfg.get("urls", []) or [])
        if raw_server_icon_mode not in VALID_SERVER_ICON_MODES:
            issues.append("background.server_icon_rotation.mode: must be random, linear, or disabled")
        else:
            ok_count += 1
        if server_icon_mode != "disabled" and not server_icon_urls:
            issues.append("background.server_icon_rotation.urls: at least one URL is needed unless mode is disabled")
        else:
            ok_count += len(server_icon_urls)
        if int(server_icon_cfg.get("interval_seconds", 0) or 0) < 600:
            issues.append("background.server_icon_rotation.interval_seconds: must be at least 600")
        else:
            ok_count += 1

        responses_cog = self.bot.get_cog("MessageResponsesCog")
        if responses_cog is not None and hasattr(responses_cog, "validate_rules"):
            response_issues = responses_cog.validate_rules()
            issues.extend(f"responses.json: {item}" for item in response_issues[:10])
            rules = getattr(responses_cog, "_rules", []) or []
            for idx, rule in enumerate(rules, start=1):
                if not isinstance(rule, dict):
                    continue
                channels = rule.get("Channels", [])
                if isinstance(channels, list):
                    for raw_channel_id in channels:
                        if not str(raw_channel_id or "").strip():
                            continue
                        try:
                            channel_id = int(str(raw_channel_id).strip())
                        except Exception:
                            issues.append(f"responses.json rule #{idx}: invalid channel `{raw_channel_id}`")
                            continue
                        if guild.get_channel(channel_id) is None:
                            issues.append(f"responses.json rule #{idx}: missing channel `<#{channel_id}>`")
                        else:
                            ok_count += 1
            ok_count += max(0, len(rules) - len(response_issues))
        elif cfg.get_str("responses", "rules_path", default="responses.json"):
            issues.append("responses.json: MessageResponsesCog is not loaded")

        entries = cfg.get("forum_first_message", "entries", default=[]) or []
        if isinstance(entries, list):
            for idx, entry in enumerate(entries, start=1):
                if isinstance(entry, dict):
                    try:
                        forum_id = int(entry.get("forum_channel_id") or 0)
                    except Exception:
                        forum_id = 0
                    check_channel(f"forum_first_message.entries[{idx}].forum_channel_id", forum_id, discord.ForumChannel)

        description = f"Checked **{ok_count + len(issues)}** configured references. **{ok_count}** OK, **{len(issues)}** issues."
        embed = discord.Embed(
            title="Config Check",
            description=description,
            color=discord.Color.green() if not issues else discord.Color.orange(),
        )
        if issues:
            embed.add_field(name="Issues", value="\n".join(f"- {item}" for item in issues[:20])[:1024], inline=False)
            if len(issues) > 20:
                embed.set_footer(text=f"{len(issues) - 20} more issues hidden to fit Discord's embed limit.")
        else:
            embed.add_field(name="Result", value="Everything checked out.", inline=False)
        await self._send(ctx, embed=embed, ephemeral=True)

    def _parse_snowflake_arg(self, value: str) -> int:
        match = re.search(r"\d{15,25}", str(value or ""))
        if not match:
            return 0
        try:
            return int(match.group(0))
        except Exception:
            return 0

    def _request_change_lines(self, old_data: dict, new_data: dict) -> str:
        labels = (
            ("Level ID", "level_id"),
            ("Level name", "level_name"),
            ("Creator(s)", "creators"),
            ("Showcase", "level_showcase"),
            ("Notes", "notes"),
        )

        def short(value) -> str:
            text = str(value or "blank").strip() or "blank"
            if len(text) > 120:
                text = text[:117] + "..."
            return text

        lines = []
        for label, key in labels:
            old_value = short(old_data.get(key))
            new_value = short(new_data.get(key))
            if old_value != new_value:
                lines.append(f"**{label}:** `{old_value}` -> `{new_value}`")
        return "\n".join(lines)[:1024] or "No visible form-field changes."

    async def requests_history(
        self,
        ctx: discord.ApplicationContext,
        message_id: discord.Option(str, "Request message ID or message link to inspect", required=False, default=""),
        user_id: discord.Option(str, "Requester ID or mention to inspect when no message ID is given", required=False, default=""),
        wave: discord.Option(int, "Optional wave number to narrow a user history search", required=False, default=0),
    ):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        if not await self._is_request_staff_ctx(ctx):
            return await ctx.respond("Only request reviewers can use this.", ephemeral=True)

        message_id_int = self._parse_snowflake_arg(message_id)
        user_id_int = self._parse_snowflake_arg(user_id)
        await self._defer(ctx, ephemeral=True)

        if message_id_int:
            rows = await self.bot.db.fetchall(
                "SELECT * FROM level_request_edit_audit WHERE guild_id=? AND request_message_id=? ORDER BY edited_ts DESC LIMIT 10",
                (ctx.guild.id, message_id_int),
            )
            title_tail = f"message `{message_id_int}`"
        elif user_id_int:
            if int(wave or 0) > 0:
                rows = await self.bot.db.fetchall(
                    "SELECT * FROM level_request_edit_audit WHERE guild_id=? AND user_id=? AND wave_id=? ORDER BY edited_ts DESC LIMIT 10",
                    (ctx.guild.id, user_id_int, int(wave)),
                )
                title_tail = f"<@{user_id_int}> in wave {int(wave)}"
            else:
                rows = await self.bot.db.fetchall(
                    "SELECT * FROM level_request_edit_audit WHERE guild_id=? AND user_id=? ORDER BY edited_ts DESC LIMIT 10",
                    (ctx.guild.id, user_id_int),
                )
                title_tail = f"<@{user_id_int}>"
        else:
            return await self._send(ctx, "Provide a request `message_id` or `user_id` to inspect.", ephemeral=True)

        embed = discord.Embed(title="Request Edit History", description=f"Showing recent edits for {title_tail}.", color=discord.Color.blurple())
        if not rows:
            embed.add_field(name="History", value="No edits found.", inline=False)
        for row in rows[:10]:
            try:
                old_data = json.loads(row["old_data_json"] or "{}")
            except Exception:
                old_data = {}
            try:
                new_data = json.loads(row["new_data_json"] or "{}")
            except Exception:
                new_data = {}
            embed.add_field(
                name=f"Edit #{int(row['id'])} - <t:{int(row['edited_ts'])}:R>",
                value=self._request_change_lines(old_data, new_data),
                inline=False,
            )
        await self._send(ctx, embed=embed, ephemeral=True)

    async def requests_repair(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        if not await self._is_admin_ctx(ctx):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

        cog = self.bot.get_cog("RequestLevelsCog")
        if cog is None or not hasattr(cog, "repair_request_system"):
            return await ctx.respond("Request system cog not loaded.", ephemeral=True)

        await self._defer(ctx, ephemeral=True)
        result = await cog.repair_request_system(ctx.guild)
        await self._log_admin_action(
            ctx.guild,
            ctx.user.id,
            "requests_repair",
            f"pending_recreated={result.get('pending_messages_recreated', 0)} errors={len(result.get('errors') or [])}",
        )
        embed = discord.Embed(title="Request System Repair", color=discord.Color.green() if not result.get("errors") else discord.Color.orange())
        embed.add_field(name="Request button", value="refreshed" if result.get("request_button_refreshed") else "not refreshed", inline=True)
        embed.add_field(name="Wave summary", value="refreshed" if result.get("wave_summary_refreshed") else "not refreshed", inline=True)
        embed.add_field(name="Recreated pending", value=str(result.get("pending_messages_recreated", 0)), inline=True)
        embed.add_field(name="Refreshed pending", value=str(result.get("pending_messages_refreshed", 0)), inline=True)
        embed.add_field(name="Locked reviewed", value=str(result.get("reviewed_messages_locked", 0)), inline=True)
        embed.add_field(name="Validation refreshed", value=str(result.get("stale_validations_refreshed", 0)), inline=True)
        embed.add_field(name="Cache cleanup", value="done" if result.get("validation_cache_pruned") else "skipped", inline=True)
        if result.get("errors"):
            embed.add_field(name="Notes", value="\n".join(f"- {item}" for item in result["errors"][:8])[:1024], inline=False)
        await self._send(ctx, embed=embed, ephemeral=True)

    async def requests_pending(
        self,
        ctx: discord.ApplicationContext,
        scope: discord.Option(str, "What to show: current_wave, all, weekly, or weekly_only", required=False, default="current_wave"),
        status: discord.Option(str, "Review status to show: pending, reviewed, or all", required=False, default="pending"),
        wave: discord.Option(int, "Specific live request wave to show; leave 0 for the current wave", required=False, default=0),
    ):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        if not await self._is_request_staff_ctx(ctx):
            return await ctx.respond("Only request reviewers can use this.", ephemeral=True)

        await self._defer(ctx, ephemeral=True)
        scope_key = str(scope or "current_wave").strip().casefold().replace("-", "_")
        status_key = str(status or "pending").strip().casefold()
        if status_key in {"unreviewed", "open"}:
            status_key = "pending"
        if status_key not in {"pending", "reviewed", "all"}:
            status_key = "pending"

        state_row = await self.bot.db.fetchone(
            "SELECT wave_id FROM level_request_state WHERE guild_id=?",
            (ctx.guild.id,),
        )
        current_wave = int(state_row["wave_id"]) if state_row else 0
        target_wave = int(wave) if int(wave or 0) > 0 else current_wave

        live_where = ["guild_id=?"]
        live_params: list = [ctx.guild.id]
        if scope_key in {"current", "current_wave", "wave"}:
            live_where.append("wave_id=?")
            live_params.append(target_wave)
        if status_key != "all":
            live_where.append("status=?")
            live_params.append(status_key)
        live_rows = []
        if scope_key not in {"weekly", "weekly_only"}:
            live_rows = await self.bot.db.fetchall(
                "SELECT wave_id, user_id, request_message_id, data_json, status, created_ts FROM level_request_submissions "
                f"WHERE {' AND '.join(live_where)} ORDER BY created_ts DESC LIMIT 15",
                tuple(live_params),
            )

        weekly_rows = []
        if scope_key in {"all", "weekly", "weekly_only"}:
            weekly_where = ["guild_id=?"]
            weekly_params: list = [ctx.guild.id]
            if status_key != "all":
                weekly_where.append("status=?")
                weekly_params.append(status_key)
            weekly_rows = await self.bot.db.fetchall(
                "SELECT week_start, user_id, request_message_id, channel_id, data_json, status, created_ts FROM weekly_request_reviews "
                f"WHERE {' AND '.join(weekly_where)} ORDER BY created_ts DESC LIMIT 15",
                tuple(weekly_params),
            )

        def request_name(row) -> str:
            try:
                data = json.loads(row["data_json"] or "{}")
            except Exception:
                data = {}
            level_name = str(data.get("level_name") or "Unknown level")
            level_id = str(data.get("level_id") or "unknown ID")
            return f"**{level_name}** (`{level_id}`)"

        live_channel_id = self.bot.config.get_int("level_requests", "level_requested")
        live_lines = []
        for row in live_rows:
            msg_id = row["request_message_id"]
            if msg_id and live_channel_id:
                link = f"[jump](https://discord.com/channels/{ctx.guild.id}/{live_channel_id}/{msg_id})"
            elif msg_id:
                link = f"message `{msg_id}`"
            else:
                link = "no message linked"
            live_lines.append(
                f"Wave **{row['wave_id']}** - {request_name(row)} by <@{row['user_id']}> "
                f"- `{row['status']}` - {link} - submitted <t:{int(row['created_ts'])}:R>"
            )

        weekly_lines = []
        for row in weekly_rows:
            msg_id = row["request_message_id"]
            if msg_id and row["channel_id"]:
                link = f"https://discord.com/channels/{ctx.guild.id}/{row['channel_id']}/{msg_id}"
                tail = f"[jump]({link})"
            else:
                tail = "no message linked"
            weekly_lines.append(
                f"Week **{row['week_start']}** - {request_name(row)} by <@{row['user_id']}> "
                f"- `{row['status']}` - {tail} - submitted <t:{int(row['created_ts'])}:R>"
            )

        embed = discord.Embed(
            title="Request Review Queue",
            description=f"Scope: **{scope_key}** | Status: **{status_key}** | Wave: **{target_wave or 'all'}**",
            color=discord.Color.blurple(),
        )
        embed.add_field(name=f"Live requests ({len(live_rows)} shown)", value="\n".join(live_lines)[:1024] or "No matching live requests.", inline=False)
        embed.add_field(name=f"Weekly requests ({len(weekly_rows)} shown)", value="\n".join(weekly_lines)[:1024] or "No matching weekly requests.", inline=False)
        await self._send(ctx, embed=embed, ephemeral=True)

    # --- /tracking top ---

    async def tracking_top(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)

        tracking = self.bot.get_cog("TrackingCog")
        if tracking is None:
            return await ctx.respond("Tracking cog not loaded.", ephemeral=True)

        await self._defer(ctx, ephemeral=False)
        ws = week_start_sunday(now_madrid()).isoformat()
        raw = await tracking.get_top(ctx.guild.id, ws, limit=50)  # pull more then filter

        if not raw:
            return await self._send(ctx, "No activity tracked yet this week.")

        excluded_role_ids = set(self.bot.config.get_int_list("roles", "excluded_tracking_role_id", default=[]))

        top = []
        for uid, cnt in raw:
            member = await self._resolve_member(ctx.guild, uid)
            if member is None or member.bot:
                continue
            if excluded_role_ids and any(r.id in excluded_role_ids for r in member.roles):
                continue
            top.append((uid, cnt))
            if len(top) >= 20:
                break

        if not top:
            return await self._send(ctx, "No eligible members tracked yet this week.")

        lines = []
        for i, (uid, cnt) in enumerate(top, start=1):
            lines.append(f"**#{i:02d}** <@{uid}> - **{cnt}** messages")

        week_label = week_start_sunday(now_madrid()).strftime("%Y-%m-%d")
        embed = discord.Embed(
            title="Weekly Activity Leaderboard",
            description=f"Week starting **{week_label}**",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Top Members", value="\n".join(lines)[:1024], inline=False)
        embed.add_field(name="Tracked Members", value=str(len(raw)), inline=True)
        embed.add_field(name="Eligible Shown", value=str(len(top)), inline=True)
        try:
            if ctx.guild and ctx.guild.icon:
                embed.set_thumbnail(url=ctx.guild.icon.url)
        except Exception:
            pass

        embed.set_footer(text="Madrid-time weekly tracking")
        await self._send(ctx, embed=embed)


    async def tracking_me(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)

        tracking = self.bot.get_cog("TrackingCog")
        if tracking is None:
            return await ctx.respond("Tracking cog not loaded.", ephemeral=True)

        await self._defer(ctx, ephemeral=True)
        ws = week_start_sunday(now_madrid()).isoformat()
        count, rank, eligible_total = await tracking.get_member_stats(ctx.guild, ws, ctx.user.id)

        if rank is None:
            return await self._send(ctx, "You are not eligible for weekly tracking (or have no tracked messages yet)", ephemeral=True)

        week_label = week_start_sunday(now_madrid()).strftime("%Y-%m-%d")
        embed = discord.Embed(
            title="Your Weekly Activity",
            description=f"Week starting **{week_label}**",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Messages counted", value=f"**{count}**", inline=True)
        embed.add_field(name="Rank", value=f"**#{rank}** of **{eligible_total}**", inline=True)
        embed.add_field(name="Status", value="Eligible for weekly tracking", inline=False)
        try:
            member = await self._resolve_member(ctx.guild, ctx.user)
            avatar = getattr(member or ctx.user, "display_avatar", None)
            if avatar:
                embed.set_thumbnail(url=avatar.url)
        except Exception:
            pass
        embed.set_footer(text="Madrid-time weekly tracking")
        await self._send(ctx, embed=embed, ephemeral=True)

    async def tracking_force_dm(
        self,
        ctx: discord.ApplicationContext,
        member: discord.Option(discord.Member, "Member who should receive the weekly request DM"),
    ):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)

        admin_roles = self.bot.config.get_int_list("roles", "admin_owner_role_ids")
        invoker = await self._resolve_member(ctx.guild, ctx.user)
        if invoker is None or not is_admin_or_owner(invoker, admin_roles):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

        tracking = self.bot.get_cog("TrackingCog")
        if tracking is None:
            return await ctx.respond("Tracking cog not loaded.", ephemeral=True)

        await self._defer(ctx, ephemeral=True)
        ws = week_start_sunday(now_madrid()).isoformat()
        ok, msg = await tracking.force_dm_for_user(ctx.guild, ws, member.id)
        await self._log_admin_action(ctx.guild, ctx.user.id, "tracking_force_dm", f"target_user={member.id} ok={ok}")
        await self._send(ctx, msg, ephemeral=True)

    async def tracking_reset(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)

        member = await self._resolve_member(ctx.guild, ctx.user)
        admin_roles = self.bot.config.get_int_list("roles", "admin_owner_role_ids")
        if member is None or not is_admin_or_owner(member, admin_roles):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

        tracking = self.bot.get_cog("TrackingCog")
        if tracking is None:
            return await ctx.respond("Tracking cog not loaded.", ephemeral=True)

        await self._defer(ctx, ephemeral=True)
        await tracking.reset_current_week(ctx.guild.id)
        await self._log_admin_action(ctx.guild, ctx.user.id, "tracking_reset", "current_week=true")
        await self._send(ctx, "Tracking stats for the current week have been reset.", ephemeral=True)

    async def tracking_disable_reward(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)

        member = await self._resolve_member(ctx.guild, ctx.user)
        admin_roles = self.bot.config.get_int_list("roles", "admin_owner_role_ids")
        if member is None or not is_admin_or_owner(member, admin_roles):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

        tracking = self.bot.get_cog("TrackingCog")
        if tracking is None:
            return await ctx.respond("Tracking cog not loaded.", ephemeral=True)

        week_start_iso = await tracking.disable_weekly_reward_for_current_week(ctx.guild, ctx.user.id)
        await self._log_admin_action(ctx.guild, ctx.user.id, "tracking_weekly_reward_disabled", f"week_start={week_start_iso}")
        await ctx.respond(
            f"Weekly request reward disabled for the current tracking week starting **{week_start_iso}**.",
            ephemeral=True,
        )

    async def tracking_enable_reward(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)

        member = await self._resolve_member(ctx.guild, ctx.user)
        admin_roles = self.bot.config.get_int_list("roles", "admin_owner_role_ids")
        if member is None or not is_admin_or_owner(member, admin_roles):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

        tracking = self.bot.get_cog("TrackingCog")
        if tracking is None:
            return await ctx.respond("Tracking cog not loaded.", ephemeral=True)

        week_start_iso, was_disabled = await tracking.enable_weekly_reward_for_current_week(ctx.guild, ctx.user.id)
        await self._log_admin_action(
            ctx.guild,
            ctx.user.id,
            "tracking_weekly_reward_enabled",
            f"week_start={week_start_iso} was_disabled={was_disabled}",
        )
        if was_disabled:
            msg = f"Weekly request reward re-enabled for the current tracking week starting **{week_start_iso}**."
        else:
            msg = f"Weekly request reward was already enabled for the current tracking week starting **{week_start_iso}**."
        await ctx.respond(msg, ephemeral=True)

    # --- /ticket close ---
    async def ticket_close(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)

        member = await self._resolve_member(ctx.guild, ctx.user)
        mod_role_id = self.bot.config.get_int("roles", "MOD_ROLE_ID") or 0
        allow_manage_guild = bool(self.bot.config.get("permissions", "manage_guild_counts_as_mod", default=True))
        if member is None or not is_mod(member, mod_role_id, allow_manage_guild=allow_manage_guild):
            return await ctx.respond("Only mods can close tickets.", ephemeral=True)

        # ensure this is a ticket channel
        row = await self.bot.db.fetchone("SELECT status FROM tickets WHERE channel_id=? AND status IN ('open','closing_prompted')", (ctx.channel_id,))
        if not row:
            return await ctx.respond("This isn't an active ticket channel.", ephemeral=True)

        helpcog = self.bot.get_cog("HelpCog")
        if helpcog is None:
            return await ctx.respond("Help cog not loaded.", ephemeral=True)

        await ctx.respond("Closing ticket...", ephemeral=True)
        ok = await helpcog.close_ticket_channel(ctx.guild, ctx.channel_id)
        if not ok:
            try:
                await ctx.followup.send("I couldn't close the ticket safely. Check the ticket channel for details.", ephemeral=True)
            except Exception:
                pass

    # --- /forum required_word ---
    def _parse_channel_id(self, value: Optional[str]) -> Optional[int]:
        if not value:
            return None
        match = re.search(r"\d{15,25}", str(value))
        if not match:
            return None
        try:
            return int(match.group(0))
        except Exception:
            return None

    def _configured_forum_entries(self) -> list[dict]:
        root = self.bot.config.data.setdefault("forum_first_message", {})
        entries = root.get("entries")
        if isinstance(entries, list):
            return [entry for entry in entries if isinstance(entry, dict)]

        forum_id = root.get("forum_channel_id")
        templates = root.get("templates")
        if forum_id and isinstance(templates, dict):
            entry = {
                "forum_channel_id": forum_id,
                "templates": templates,
            }
            for key in ("required_word", "missing_required_word_dm", "required_word_dm_message", "required_word_delete_delay_seconds"):
                if key in root:
                    entry[key] = root[key]
            root["entries"] = [entry]
            root.pop("forum_channel_id", None)
            root.pop("templates", None)
            return [entry]

        root["entries"] = []
        return root["entries"]

    def _resolve_forum_entry(self, ctx: discord.ApplicationContext, forum_channel_id: Optional[str]) -> tuple[Optional[dict], Optional[int], str]:
        entries = self._configured_forum_entries()
        parsed_id = self._parse_channel_id(forum_channel_id)

        if parsed_id is None:
            channel = getattr(ctx, "channel", None)
            parent_id = getattr(channel, "parent_id", None)
            channel_id = getattr(channel, "id", None)
            for candidate_id in (parent_id, channel_id):
                if candidate_id is None:
                    continue
                for entry in entries:
                    try:
                        if int(entry.get("forum_channel_id")) == int(candidate_id):
                            return entry, int(candidate_id), ""
                    except Exception:
                        continue

            if len(entries) == 1:
                try:
                    only_id = int(entries[0].get("forum_channel_id"))
                except Exception:
                    only_id = None
                return entries[0], only_id, ""

            configured = []
            for entry in entries:
                try:
                    configured.append(f"<#{int(entry.get('forum_channel_id'))}>")
                except Exception:
                    continue
            suffix = f" Configured forums: {', '.join(configured)}." if configured else ""
            return None, None, "Please provide a forum channel ID or run this inside a configured forum thread." + suffix

        for entry in entries:
            try:
                if int(entry.get("forum_channel_id")) == parsed_id:
                    return entry, parsed_id, ""
            except Exception:
                continue
        return None, parsed_id, f"That forum is not configured for first-message reminders: <#{parsed_id}>."

    async def forum_required_word(
        self,
        ctx: discord.ApplicationContext,
        word: discord.Option(str, "New required word; leave blank to view, or use off/none/clear to disable", required=False, default=""),
        forum_channel_id: discord.Option(str, "Forum channel ID or mention; needed when more than one forum is configured", required=False, default=""),
        match_mode: discord.Option(str, "Match mode: contains, whole_word, or regex", required=False, default=""),
    ):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)

        member = await self._resolve_member(ctx.guild, ctx.user)
        if member is None or not member.guild_permissions.administrator:
            return await ctx.respond("Nah, you can't use this", ephemeral=True)

        entry, forum_id, error = self._resolve_forum_entry(ctx, forum_channel_id)
        if entry is None:
            return await ctx.respond(error or "Forum config not found.", ephemeral=True)

        current = str(entry.get("required_word", "") or "").strip()
        if word is None or not str(word).strip():
            display = current or "disabled"
            mode = str(entry.get("required_word_match_mode") or "contains")
            target = f"<#{forum_id}>" if forum_id else "the selected forum"
            return await ctx.respond(f"Current required word for {target}: **{display}** | mode: **{mode}**", ephemeral=True)

        new_word = str(word).strip()
        if new_word.casefold() in {"off", "disable", "disabled", "none", "clear"}:
            new_word = ""

        entry["required_word"] = new_word
        mode = str(match_mode or "").strip().casefold()
        if mode:
            if mode not in {"contains", "whole_word", "regex"}:
                return await ctx.respond("Match mode must be `contains`, `whole_word`, or `regex`.", ephemeral=True)
            entry["required_word_match_mode"] = mode
        try:
            self.bot.config.save()
        except Exception as e:
            await log_error(self.bot, f"Failed to save forum required word: {repr(e)}")
            return await ctx.respond("I couldn't save the new required word...", ephemeral=True)

        sticky = self.bot.get_cog("StickyCog")
        if sticky:
            fn = getattr(sticky, "on_config_reload", None)
            if callable(fn):
                try:
                    fn()
                except Exception as e:
                    await log_error(self.bot, f"Failed to refresh StickyCog after required word update: {repr(e)}")

        target = f"<#{forum_id}>" if forum_id else "the selected forum"
        await self._log_admin_action(
            ctx.guild,
            ctx.user.id,
            "forum_required_word_updated",
            f"forum_id={forum_id} enabled={bool(new_word)} match_mode={entry.get('required_word_match_mode', 'contains')}",
        )
        if new_word:
            await ctx.respond(f"Updated required word for {target} to **{new_word}**.", ephemeral=True)
        else:
            await ctx.respond(f"Required word enforcement is now disabled for {target}.", ephemeral=True)

    # --- /resync ---
    async def _resync(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)

        member = await self._resolve_member(ctx.guild, ctx.user)
        admin_roles = self.bot.config.get_int_list("roles", "admin_owner_role_ids")
        if member is None or not is_admin_or_owner(member, admin_roles):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

        self.bot.config.reload()

        # notify cogs
        for cog in self.bot.cogs.values():
            fn = getattr(cog, "on_config_reload", None)
            if callable(fn):
                try:
                    fn()
                except Exception as e:
                    await log_error(self.bot, f"Config reload hook failed for {cog.__class__.__name__}: {repr(e)}")

        # re-register persistent views
        try:
            await self.bot.register_persistent_views()
        except Exception as e:
            await log_error(self.bot, f"Persistent view registration failed during resync: {repr(e)}")

        await self._log_admin_action(ctx.guild, ctx.user.id, "bot_resync", "config/views/responses reloaded")
        await ctx.respond("Resynced config, views, and responses.", ephemeral=True)

    # --- /restart ---
    async def _restart(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)

        member = await self._resolve_member(ctx.guild, ctx.user)
        admin_roles = self.bot.config.get_int_list("roles", "admin_owner_role_ids")
        if member is None or not is_admin_or_owner(member, admin_roles):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

        await self._log_admin_action(ctx.guild, ctx.user.id, "bot_restart", "manual restart command")
        await ctx.respond("Restarting...", ephemeral=True)
        tracking = self.bot.get_cog("TrackingCog")
        if tracking is not None:
            flush = getattr(tracking, "flush_activity_counts", None)
            if callable(flush):
                try:
                    await flush()
                except Exception as e:
                    await log_error(self.bot, f"Restart activity flush failed: {repr(e)}")

        background = self.bot.get_cog("BackgroundCog")
        if background is not None:
            persist = getattr(background, "_persist_current_day", None)
            if callable(persist):
                try:
                    await persist()
                except Exception as e:
                    await log_error(self.bot, f"Restart daily summary flush failed: {repr(e)}")

        try:
            await self.bot.db.close()
        except Exception as e:
            await log_error(self.bot, f"Restart database close failed: {repr(e)}")

        await self.bot.close()
        os._exit(0)

    # --- /dance ---
    async def _dance(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        url = self.bot.config.get_str("fun", "dance_gif_url", default="")
        if not url:
            return await ctx.respond("Dance GIF not configured.", ephemeral=True)
        await ctx.respond(url)

    # --- /rps ---
    async def _rps(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)

        # Anti-spam: hardcoded 10s cooldown per user for /rock-paper-scissors
        now_ts = time.time()
        last_ts = self._rps_last_ts.get(ctx.user.id, 0.0)
        if now_ts - last_ts < 10.0:
            remaining = int(10 - (now_ts - last_ts) + 0.999)
            return await ctx.respond(f"Slow down... try again in {remaining}s", ephemeral=True)
        self._rps_last_ts[ctx.user.id] = now_ts

        parent = self
        options = ["Rock", "Paper", "Scissors"]
        nonce = secrets.token_hex(4)

        def outcome(user: str, bot: str) -> str:
            if user == bot:
                return "tie"
            wins = {("Rock", "Scissors"), ("Paper", "Rock"), ("Scissors", "Paper")}
            return "win" if (user, bot) in wins else "lose"

        class RPSView(discord.ui.View):
            def __init__(self, user_id: int):
                super().__init__(timeout=60)
                self.user_id = user_id

                for opt in options:
                    btn = discord.ui.Button(
                        label=opt,
                        style=discord.ButtonStyle.primary,
                        custom_id=f"rps:{nonce}:{opt.lower()}",
                    )
                    btn.callback = self._make_callback(opt)
                    self.add_item(btn)

            def _make_callback(self, choice: str):
                async def _cb(interaction: discord.Interaction):
                    try:
                        if interaction.user.id != self.user_id:
                            return await interaction.response.send_message("This game isn't for you.", ephemeral=True)

                        bot_choice = random.choice(options)
                        o = outcome(choice, bot_choice)

                        guild_id = interaction.guild.id if interaction.guild else parent.allowed_guild_id
                        user_id = interaction.user.id

                        if o == "win":
                            streak = await parent._rps_update_streak(guild_id, user_id, new_value=None, increment=True)
                        elif o == "lose":
                            await parent._rps_update_streak(guild_id, user_id, new_value=0, increment=False)
                            streak = 0
                        else:
                            # Tie: do not reset or increment streak
                            streak = await parent._rps_get_streak(guild_id, user_id)

                        reward_text = ""
                        cfg = parent.bot.config
                        reward_role_id = cfg.get_int("roles", "rps_streak_role_id")
                        if o == "win" and reward_role_id and streak >= 5 and interaction.guild:
                            role = interaction.guild.get_role(reward_role_id)
                            member = await parent._resolve_member(interaction.guild, user_id)
                            if role and member and role not in member.roles:
                                try:
                                    await member.add_roles(role, reason="RPS 5-win streak reward")
                                    reward_text = f"\n\n🏆 **5-win streak!** You earned **{role.name}**."
                                except Exception:
                                    reward_text = "\n\n🏆 **5-win streak!** (Could not assign the role, permissions/role hierarchy.)"
                            # Reset after awarding so it doesn't award forever
                            await parent._rps_update_streak(guild_id, user_id, new_value=0, increment=False)
                            streak = 0

                        if o == "win":
                            result_line = "You **win**!"
                        elif o == "lose":
                            result_line = "You **lose**!"
                        else:
                            result_line = "It's a **tie**!"

                        content = (
                            f"You chose **{choice}**. I chose **{bot_choice}**. {result_line}"
                            f"\nWin streak: **{streak}**"
                            f"{reward_text}"
                        )

                        await interaction.response.defer()
                        await interaction.message.edit(content=content, view=None)
                    except Exception as e:
                        try:
                            if interaction.response.is_done():
                                await interaction.followup.send("Something went wrong.", ephemeral=True)
                            else:
                                await interaction.response.send_message("Something went wrong.", ephemeral=True)
                        except Exception:
                            pass
                        await log_error(parent.bot, f"RPS view error: {repr(e)}")
                return _cb

        await ctx.respond("Choose:", view=RPSView(ctx.user.id))

    async def _rps_get_streak(self, guild_id: int, user_id: int) -> int:
        """Return current RPS win streak without modifying it."""
        await self.bot.db.connect()
        row = await self.bot.db.fetchone(
            "SELECT streak FROM rps_streaks WHERE guild_id=? AND user_id=?",
            (guild_id, user_id)
        )
        return int(row["streak"]) if row else 0

    async def _rps_update_streak(self, guild_id: int, user_id: int, new_value: Optional[int], increment: bool) -> int:
        """Update and return a user's RPS win streak.

        - If increment=True, increments current streak by 1.
        - If new_value is not None, sets streak to that value (used for reset).
        """
        await self.bot.db.connect()

        if new_value is not None:
            await self.bot.db.execute(
                "INSERT INTO rps_streaks(guild_id,user_id,streak,updated_ts) VALUES(?,?,?,?) "
                "ON CONFLICT(guild_id,user_id) DO UPDATE SET streak=excluded.streak, updated_ts=excluded.updated_ts",
                (guild_id, user_id, int(new_value), int(time.time()))
            )
            return int(new_value)

        row = await self.bot.db.fetchone(
            "SELECT streak FROM rps_streaks WHERE guild_id=? AND user_id=?",
            (guild_id, user_id)
        )
        cur = int(row["streak"]) if row else 0
        cur = cur + 1 if increment else 0
        await self.bot.db.execute(
            "INSERT INTO rps_streaks(guild_id,user_id,streak,updated_ts) VALUES(?,?,?,?) "
            "ON CONFLICT(guild_id,user_id) DO UPDATE SET streak=excluded.streak, updated_ts=excluded.updated_ts",
            (guild_id, user_id, cur, int(time.time()))
        )
        return cur

    # --- /gambling ---
    async def _gambling(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)

        # Anti-spam: hardcoded 10s cooldown per user for /gambling
        now_ts = time.time()
        last_ts = self._gamble_last_ts.get(ctx.user.id, 0.0)
        if now_ts - last_ts < 10.0:
            remaining = int(10 - (now_ts - last_ts) + 0.999)
            return await ctx.respond(f"Slow down... try again in {remaining}s", ephemeral=True)
        self._gamble_last_ts[ctx.user.id] = now_ts
        cfg = self.bot.config
        gcfg = cfg.get("fun", "gambling", default={}) or {}
        emojis = gcfg.get("emojis", ["🍒","🍋","🍇","⭐","💎"])
        interval = float(gcfg.get("spin_interval_seconds", 0.5) or 0.5)
        total = float(gcfg.get("spin_total_seconds", 2.5) or 2.5)
        rare = float(gcfg.get("rare_win_chance", 0.01) or 0.01)
        win_combo = str(gcfg.get("win_combo", "💎💎💎") or "💎💎💎")

        reward_role_id = cfg.get_int("roles", "gambling_reward_role_id") or 0
        role = ctx.guild.get_role(reward_role_id) if reward_role_id else None

        await ctx.respond("Spinning…")
        msg = await ctx.interaction.original_response()

        # animate edits
        steps = max(1, int(total / interval))
        current = ""
        for _ in range(steps):
            current = "".join(random.choice(emojis) for _ in range(3))
            try:
                await msg.edit(content=f"{current}")
            except Exception:
                pass
            await asyncio.sleep(interval)

        # final result
        final = "".join(random.choice(emojis) for _ in range(3))
        won = False
        if random.random() < rare:
            final = win_combo
            won = True

        content = f"🎰 **{final}** 🎰\n"
        if won and role is not None:
            member = await self._resolve_member(ctx.guild, ctx.user)
            if member and role not in member.roles:
                try:
                    await member.add_roles(role, reason="Gambling win")
                    content += f"You hit a rare combo and earned **{role.name}**!"
                except Exception:
                    content += "You hit a rare combo, but I couldn't give the reward role (permissions/role hierarchy)."
            else:
                content += "You hit a rare combo!"
        else:
            content += "No win this time."

        try:
            await msg.edit(content=content)
        except Exception:
            pass

def setup(bot: discord.Bot):
    bot.add_cog(CommandsCog(bot))
