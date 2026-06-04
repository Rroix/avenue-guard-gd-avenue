from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, time as dtime
from typing import Dict, Optional, List, Tuple

import aiohttp
import discord
from discord.ext import commands, tasks

from utils.checks import ensure_allowed_guild_id
from utils.errors import log_error
from utils.server_icons import ensure_server_icon_config, normalize_server_icon_mode
from utils.timeutils import TZ, now_madrid, week_start_sunday

def _day_key(dt: Optional[datetime] = None) -> str:
    dt = dt or now_madrid()
    return dt.strftime("%Y-%m-%d")

def _parse_hhmm(value: str, default: Tuple[int, int] = (0, 0)) -> Tuple[int, int]:
    try:
        parts = value.strip().split(":")
        if len(parts) != 2:
            return default
        hh = max(0, min(23, int(parts[0])))
        mm = max(0, min(59, int(parts[1])))
        return hh, mm
    except Exception:
        return default

def _fmt_minutes(total_minutes: int) -> str:
    if total_minutes <= 0:
        return "0m"
    hours, mins = divmod(total_minutes, 60)
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"

def _fmt_num(value: int) -> str:
    return f"{int(value):,}"

def _fmt_delta(current: int, previous: Optional[int]) -> str:
    if previous is None:
        return "no previous day"
    diff = int(current) - int(previous)
    if diff == 0:
        return "no change"
    sign = "+" if diff > 0 else ""
    return f"{sign}{diff:,}"

def _fmt_percent(part: int, total: int) -> str:
    if total <= 0:
        return "0%"
    return f"{(part / total) * 100:.1f}%"

@dataclass
class DailyStats:
    messages: int = 0
    edits: int = 0
    deletes: int = 0
    reactions: int = 0

    joins: int = 0
    leaves: int = 0
    bans: int = 0
    unbans: int = 0

    boosts: int = 0
    unboosts: int = 0

    voice_minutes: int = 0
    peak_voice_users: int = 0
    peak_online_members: int = 0

    commands: int = 0
    command_errors: int = 0
    commands_by_name: Dict[str, int] = field(default_factory=dict)

    by_channel: Dict[int, int] = field(default_factory=dict)
    by_user: Dict[int, int] = field(default_factory=dict)

class BackgroundCog(commands.Cog):

    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self._started = False
        self._current_day = _day_key()
        self.stats = DailyStats()
        self.voice_sessions: Dict[int, int] = {}  # user_id -> unix_ts join
        self._status_index = -1  # start at -1 so first rotation shows the first status
        self._last_status_swap = 0.0

    async def start_background(self):
        if self._started:
            return
        self._started = True

        try:
            await self.bot.db.connect()
        except Exception as e:
            await log_error(self.bot, f"Background DB setup failed: {repr(e)}")

        # Initialize voice sessions from current state (best-effort)
        cfg = self.bot.config
        allowed = cfg.get_int("guild", "allowed_guild_id")
        guild = self.bot.get_guild(allowed) if allowed else None
        if guild:
            saved = await self._load_daily_stats(guild.id, self._current_day)
            if saved is not None:
                self.stats = saved
            now_ts = int(time.time())
            self.voice_sessions = self._voice_sessions_from_guild(guild, now_ts)

        # Start loops
        if self._daily_summary_enabled():
            self._start_daily_report_loop()

        if self._status_rotation_enabled():
            try:
                if not self.rotate_status.is_running():
                    self.rotate_status.start()
            except Exception:
                pass

        if self._server_icon_rotation_enabled():
            try:
                if not self.rotate_server_icon.is_running():
                    self.rotate_server_icon.start()
            except Exception:
                pass

        try:
            if not self.update_snapshot.is_running():
                self.update_snapshot.start()
        except Exception:
            pass

    def on_config_reload(self) -> None:
        # Restart daily loop if time changed
        try:
            if self.daily_report.is_running():
                self.daily_report.cancel()
        except Exception:
            pass
        if self._started and self._daily_summary_enabled():
            self._start_daily_report_loop()
        try:
            if self._status_rotation_enabled():
                if not self.rotate_status.is_running():
                    self.rotate_status.start()
            elif self.rotate_status.is_running():
                self.rotate_status.cancel()
        except Exception:
            pass
        try:
            if self._server_icon_rotation_enabled():
                if not self.rotate_server_icon.is_running():
                    self.rotate_server_icon.start()
            elif self.rotate_server_icon.is_running():
                self.rotate_server_icon.cancel()
        except Exception:
            pass

    # --------------------
    # Config helpers
    # --------------------
    def _excluded_channels(self) -> set[int]:
        ids = self.bot.config.get_int_list("background", "exclude_channel_ids")
        return set(ids)

    def _status_rotation_enabled(self) -> bool:
        return bool(self.bot.config.get("background", "status_rotation", "enabled", default=False))

    def _status_rotation_interval(self) -> int:
        return int(self.bot.config.get("background", "status_rotation", "interval_seconds", default=600) or 600)

    def _status_list(self) -> List[dict]:
        items = self.bot.config.get("background", "status_rotation", "statuses", default=[]) or []
        if not isinstance(items, list):
            return []
        out: List[dict] = []
        for it in items:
            if isinstance(it, dict):
                t = str(it.get("type", "playing")).lower()
                txt = str(it.get("text", "")).strip()
                if txt:
                    out.append({"type": t, "text": txt})
            elif isinstance(it, str) and it.strip():
                out.append({"type": "playing", "text": it.strip()})
        return out

    def _server_icon_rotation_enabled(self) -> bool:
        cfg = ensure_server_icon_config(self.bot.config)
        return normalize_server_icon_mode(cfg.get("mode")) != "disabled" and bool(cfg.get("urls"))

    def _server_icon_interval(self) -> int:
        cfg = ensure_server_icon_config(self.bot.config)
        return max(600, int(cfg.get("interval_seconds", 86400) or 86400))

    def _server_icon_urls(self) -> list[str]:
        cfg = ensure_server_icon_config(self.bot.config)
        urls = cfg.get("urls", [])
        return list(urls) if isinstance(urls, list) else []

    def _choose_server_icon_index(self, mode: str, urls: list[str], current_index: int) -> int:
        if not urls:
            return -1
        if mode == "random":
            if len(urls) == 1:
                return 0
            choices = [idx for idx in range(len(urls)) if idx != current_index]
            return random.choice(choices or list(range(len(urls))))
        return (current_index + 1) % len(urls)

    async def _download_server_icon(self, url: str) -> bytes:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f"image URL returned HTTP {resp.status}")
                data = await resp.read()
        if not data:
            raise RuntimeError("image URL returned an empty file")
        if len(data) > 8 * 1024 * 1024:
            raise RuntimeError("image is larger than 8 MB")
        return data

    async def rotate_server_icon_once(
        self,
        guild: discord.Guild,
        *,
        force: bool = False,
        actor_id: int = 0,
    ) -> tuple[bool, str]:
        cfg = ensure_server_icon_config(self.bot.config)
        urls = self._server_icon_urls()
        if not urls:
            return False, "No server icon URLs are configured."

        mode = normalize_server_icon_mode(cfg.get("mode"))
        if mode == "disabled" and not force:
            return False, "Server icon rotation is disabled."
        if mode == "disabled":
            mode = "linear"

        current_index = int(cfg.get("current_index", -1) or -1)
        next_index = self._choose_server_icon_index(mode, urls, current_index)
        if next_index < 0:
            return False, "No usable server icon URL was found."

        url = urls[next_index]
        try:
            icon_bytes = await self._download_server_icon(url)
            reason = "Avenue Guard server icon rotation"
            if actor_id:
                reason = f"Avenue Guard server icon rotation by {actor_id}"
            await guild.edit(icon=icon_bytes, reason=reason[:512])
        except Exception as e:
            await log_error(self.bot, f"Server icon rotation failed for url={url}: {repr(e)}")
            return False, f"I couldn't change the server icon: {e}"

        now_ts = int(time.time())
        cfg["current_index"] = next_index
        cfg["last_changed_ts"] = now_ts
        try:
            self.bot.config.save()
        except Exception as e:
            await log_error(self.bot, f"Server icon rotation changed icon but failed to save config: {repr(e)}")
            return True, f"Changed the server icon to image #{next_index + 1}, but couldn't save the new index."

        return True, f"Changed the server icon to image #{next_index + 1}."

    async def _render_status_text(self, guild: discord.Guild, text: str) -> str:
        """Replace supported placeholders inside status rotation text."""
        class _SafeDict(dict):
            def __missing__(self, key):
                return "0"

        now = now_madrid()
        members = guild.member_count or len(getattr(guild, 'members', []) or [])
        # online can be approximate depending on intents/Discord caching
        try:
            online = sum(1 for m in guild.members if (not m.bot) and m.status != discord.Status.offline)
        except Exception:
            online = 0

        ws_iso = week_start_sunday(now).isoformat()
        week_msgs = 0
        week_top = ""
        open_tickets = 0
        today_msgs = int(getattr(self.stats, 'messages', 0) or 0)

        try:
            await self.bot.db.connect()
            row = await self.bot.db.fetchone(
                "SELECT COALESCE(SUM(count),0) AS total FROM activity_counts WHERE guild_id=? AND week_start=?",
                (guild.id, ws_iso)
            )
            week_msgs = int(row["total"]) if row else 0
            row2 = await self.bot.db.fetchone(
                "SELECT user_id FROM activity_counts WHERE guild_id=? AND week_start=? ORDER BY count DESC LIMIT 1",
                (guild.id, ws_iso)
            )
            if row2:
                uid = int(row2["user_id"])
                mem = guild.get_member(uid)
                week_top = mem.display_name if mem else str(uid)
            row3 = await self.bot.db.fetchone(
                "SELECT COUNT(*) AS c FROM tickets WHERE guild_id=? AND status IN ('open','closing_prompted')",
                (guild.id,)
            )
            open_tickets = int(row3["c"]) if row3 else 0
        except Exception:
            pass

        mapping = _SafeDict({
            "members": str(members),
            "online": str(online),
            "week_msgs": str(week_msgs),
            "week_top": str(week_top),
            "open_tickets": str(open_tickets),
            "today_msgs": str(today_msgs),
        })
        try:
            return str(text).format_map(mapping)
        except Exception:
            return str(text)

    def _daily_summary_enabled(self) -> bool:
        return bool(self.bot.config.get("background", "daily_summary", "enabled", default=False))

    def _daily_summary_channel_id(self) -> int:
        cid = self.bot.config.get_int("background", "daily_summary", "channel_id")
        if cid:
            return cid
        return self.bot.config.get_int("channels", "general_logging_channel_id") or 0

    def _daily_reset_after_report(self) -> bool:
        return bool(self.bot.config.get("background", "daily_summary", "reset_after_report", default=True))

    def _voice_sessions_from_guild(self, guild: discord.Guild, now_ts: int) -> Dict[int, int]:
        sessions: Dict[int, int] = {}
        for m in guild.members:
            try:
                if not m.bot and m.voice and m.voice.channel:
                    sessions[m.id] = now_ts
            except Exception:
                continue
        return sessions

    def _stats_payload(self, day_key: str, snapshot: DailyStats) -> dict:
        return {
            "day_key": day_key,
            "messages": snapshot.messages,
            "edits": snapshot.edits,
            "deletes": snapshot.deletes,
            "reactions": snapshot.reactions,
            "joins": snapshot.joins,
            "leaves": snapshot.leaves,
            "bans": snapshot.bans,
            "unbans": snapshot.unbans,
            "boosts": snapshot.boosts,
            "unboosts": snapshot.unboosts,
            "voice_minutes": snapshot.voice_minutes,
            "peak_voice_users": snapshot.peak_voice_users,
            "peak_online_members": snapshot.peak_online_members,
            "commands": snapshot.commands,
            "command_errors": snapshot.command_errors,
            "by_channel": snapshot.by_channel,
            "by_user": snapshot.by_user,
            "commands_by_name": snapshot.commands_by_name,
        }

    def _stats_from_payload(self, payload: dict) -> DailyStats:
        snapshot = DailyStats()
        for attr in (
            "messages", "edits", "deletes", "reactions", "joins", "leaves", "bans", "unbans",
            "boosts", "unboosts", "voice_minutes", "peak_voice_users", "peak_online_members",
            "commands", "command_errors",
        ):
            try:
                setattr(snapshot, attr, int(payload.get(attr, 0) or 0))
            except Exception:
                pass
        snapshot.by_channel = {int(k): int(v) for k, v in (payload.get("by_channel") or {}).items()}
        snapshot.by_user = {int(k): int(v) for k, v in (payload.get("by_user") or {}).items()}
        snapshot.commands_by_name = {str(k): int(v) for k, v in (payload.get("commands_by_name") or {}).items()}
        return snapshot

    async def _load_daily_stats(self, guild_id: int, day_key: str) -> Optional[DailyStats]:
        row = await self.bot.db.fetchone(
            "SELECT payload_json FROM daily_stats WHERE guild_id=? AND day_key=?",
            (guild_id, day_key),
        )
        if not row:
            return None
        try:
            return self._stats_from_payload(json.loads(row["payload_json"] or "{}"))
        except Exception:
            return None

    async def _persist_daily_stats(self, guild_id: int, day_key: str, snapshot: DailyStats) -> None:
        payload = self._stats_payload(day_key, snapshot)
        await self.bot.db.execute(
            "INSERT OR REPLACE INTO daily_stats(guild_id, day_key, payload_json, created_ts) VALUES(?,?,?,?)",
            (guild_id, day_key, json.dumps(payload, separators=(',', ':')), int(time.time())),
        )

    async def _persist_current_day(self) -> None:
        allowed = self.bot.config.get_int("guild", "allowed_guild_id")
        if allowed:
            guild = self.bot.get_guild(allowed)
            now_ts = int(time.time())
            if guild is not None:
                self._add_voice_until(self.stats, now_ts)
                self.voice_sessions = self._voice_sessions_from_guild(guild, now_ts)
            await self._persist_daily_stats(allowed, self._current_day, self.stats)

    def _rollover_boundary_ts(self, old_day: str) -> int:
        try:
            boundary = datetime.strptime(old_day, "%Y-%m-%d").replace(tzinfo=TZ) + timedelta(days=1)
            return int(boundary.timestamp())
        except Exception:
            return int(time.time())

    def _add_voice_until(self, snapshot: DailyStats, boundary_ts: int) -> None:
        for joined_ts in list(self.voice_sessions.values()):
            try:
                minutes = int((boundary_ts - int(joined_ts)) // 60)
                if minutes > 0:
                    snapshot.voice_minutes += minutes
            except Exception:
                continue

    def _track_background_persist(self, task: asyncio.Task) -> None:
        def _done(done: asyncio.Task):
            try:
                exc = done.exception()
            except asyncio.CancelledError:
                return
            except Exception as e:
                exc = e
            if exc:
                try:
                    asyncio.create_task(log_error(self.bot, f"Daily stats persist failed: {repr(exc)}"))
                except Exception:
                    pass

        task.add_done_callback(_done)

    def _rollover_if_needed(self, guild: Optional[discord.Guild] = None):
        today = _day_key()
        if today != self._current_day:
            allowed = self.bot.config.get_int("guild", "allowed_guild_id")
            old_day = self._current_day
            old_stats = self.stats
            boundary_ts = self._rollover_boundary_ts(old_day)
            if allowed:
                self._add_voice_until(old_stats, boundary_ts)
                try:
                    task = asyncio.create_task(self._persist_daily_stats(allowed, old_day, old_stats))
                    self._track_background_persist(task)
                except Exception as e:
                    try:
                        asyncio.create_task(log_error(self.bot, f"Daily stats rollover persist could not start: {repr(e)}"))
                    except Exception:
                        pass
            else:
                boundary_ts = int(time.time())
            self._current_day = today
            self.stats = DailyStats()
            if guild is not None:
                self.voice_sessions = self._voice_sessions_from_guild(guild, boundary_ts)
            else:
                self.voice_sessions.clear()

    # --------------------
    # Event listeners
    # --------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        allowed = self.bot.config.get_int("guild", "allowed_guild_id")
        if not ensure_allowed_guild_id(message.guild, allowed):
            return
        if message.channel.id in self._excluded_channels():
            return

        self._rollover_if_needed(message.guild)
        snapshot = self.stats
        snapshot.messages += 1
        snapshot.by_channel[message.channel.id] = snapshot.by_channel.get(message.channel.id, 0) + 1
        snapshot.by_user[message.author.id] = snapshot.by_user.get(message.author.id, 0) + 1

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if after.guild is None or after.author.bot:
            return
        allowed = self.bot.config.get_int("guild", "allowed_guild_id")
        if not ensure_allowed_guild_id(after.guild, allowed):
            return
        if after.channel.id in self._excluded_channels():
            return
        self._rollover_if_needed(after.guild)
        snapshot = self.stats
        snapshot.edits += 1

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.guild is None or (message.author and message.author.bot):
            return
        allowed = self.bot.config.get_int("guild", "allowed_guild_id")
        if not ensure_allowed_guild_id(message.guild, allowed):
            return
        if message.channel and message.channel.id in self._excluded_channels():
            return
        self._rollover_if_needed(message.guild)
        snapshot = self.stats
        snapshot.deletes += 1

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        if user.bot or reaction.message.guild is None:
            return
        allowed = self.bot.config.get_int("guild", "allowed_guild_id")
        if not ensure_allowed_guild_id(reaction.message.guild, allowed):
            return
        if reaction.message.channel.id in self._excluded_channels():
            return
        self._rollover_if_needed(reaction.message.guild)
        snapshot = self.stats
        snapshot.reactions += 1

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        allowed = self.bot.config.get_int("guild", "allowed_guild_id")
        if member.bot or not ensure_allowed_guild_id(member.guild, allowed):
            return
        self._rollover_if_needed(member.guild)
        snapshot = self.stats
        snapshot.joins += 1

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        allowed = self.bot.config.get_int("guild", "allowed_guild_id")
        if member.bot or not ensure_allowed_guild_id(member.guild, allowed):
            return
        self._rollover_if_needed(member.guild)
        snapshot = self.stats
        snapshot.leaves += 1

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        allowed = self.bot.config.get_int("guild", "allowed_guild_id")
        if not ensure_allowed_guild_id(guild, allowed):
            return
        self._rollover_if_needed(guild)
        snapshot = self.stats
        snapshot.bans += 1

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        allowed = self.bot.config.get_int("guild", "allowed_guild_id")
        if not ensure_allowed_guild_id(guild, allowed):
            return
        self._rollover_if_needed(guild)
        snapshot = self.stats
        snapshot.unbans += 1

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        allowed = self.bot.config.get_int("guild", "allowed_guild_id")
        if after.bot or not ensure_allowed_guild_id(after.guild, allowed):
            return
        self._rollover_if_needed(after.guild)
        snapshot = self.stats
        if before.premium_since is None and after.premium_since is not None:
            snapshot.boosts += 1
        elif before.premium_since is not None and after.premium_since is None:
            snapshot.unboosts += 1

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        allowed = self.bot.config.get_int("guild", "allowed_guild_id")
        if member.bot or not ensure_allowed_guild_id(member.guild, allowed):
            return
        self._rollover_if_needed(member.guild)
        snapshot = self.stats
        now_ts = int(time.time())

        if before.channel and not after.channel:
            joined_ts = self.voice_sessions.pop(member.id, None)
            if joined_ts:
                minutes = int((now_ts - joined_ts) // 60)
                if minutes > 0:
                    snapshot.voice_minutes += minutes
        elif after.channel and not before.channel:
            self.voice_sessions[member.id] = now_ts

        # Peak voice users snapshot
        try:
            in_voice = sum(1 for m in member.guild.members if (not m.bot) and m.voice and m.voice.channel)
            snapshot.peak_voice_users = max(snapshot.peak_voice_users, in_voice)
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_application_command_completion(self, ctx: discord.ApplicationContext):
        if ctx.guild is None:
            return
        allowed = self.bot.config.get_int("guild", "allowed_guild_id")
        if not ensure_allowed_guild_id(ctx.guild, allowed):
            return
        self._rollover_if_needed(ctx.guild)
        snapshot = self.stats
        snapshot.commands += 1
        name = getattr(ctx.command, "qualified_name", None) or getattr(ctx.command, "name", "unknown")
        snapshot.commands_by_name[str(name)] = snapshot.commands_by_name.get(str(name), 0) + 1

    @commands.Cog.listener()
    async def on_application_command_error(self, ctx: discord.ApplicationContext, error: Exception):
        if ctx.guild is None:
            return
        allowed = self.bot.config.get_int("guild", "allowed_guild_id")
        if not ensure_allowed_guild_id(ctx.guild, allowed):
            return
        self._rollover_if_needed(ctx.guild)
        snapshot = self.stats
        snapshot.command_errors += 1

    # --------------------
    # Tasks
    # --------------------
    @tasks.loop(minutes=5)
    async def update_snapshot(self):
        allowed = self.bot.config.get_int("guild", "allowed_guild_id")
        guild = self.bot.get_guild(allowed) if allowed else None
        if guild is None:
            return
        self._rollover_if_needed(guild)
        snapshot = self.stats
        try:
            online = sum(1 for m in guild.members if (not m.bot) and m.status != discord.Status.offline)
            snapshot.peak_online_members = max(snapshot.peak_online_members, online)
        except Exception:
            pass
        try:
            await self._persist_daily_stats(guild.id, self._current_day, snapshot)
        except Exception:
            pass

    @update_snapshot.before_loop
    async def _before_snapshot(self):
        await self.bot.wait_until_ready()

    @update_snapshot.error
    async def _snapshot_error(self, error: Exception):
        await log_error(self.bot, f"Daily snapshot task error: {repr(error)}")

    @tasks.loop(seconds=10)
    async def rotate_status(self):
        if not self._status_rotation_enabled():
            return
        allowed = self.bot.config.get_int("guild", "allowed_guild_id")
        guild = self.bot.get_guild(allowed) if allowed else None
        if guild is None:
            return
        interval = max(10, self._status_rotation_interval())
        now = time.time()
        if now - self._last_status_swap < interval:
            return
        self._last_status_swap = now

        statuses = self._status_list()
        if not statuses:
            return

        self._status_index = (self._status_index + 1) % len(statuses)
        item = statuses[self._status_index]
        t = item["type"]
        txt = item["text"]
        txt = await self._render_status_text(guild, txt)

        atype = discord.ActivityType.playing
        if t == "watching":
            atype = discord.ActivityType.watching
        elif t == "listening":
            atype = discord.ActivityType.listening
        elif t == "competing":
            atype = discord.ActivityType.competing

        try:
            await self.bot.change_presence(activity=discord.Activity(type=atype, name=txt))
        except Exception:
            pass

    @rotate_status.before_loop
    async def _before_rotate(self):
        await self.bot.wait_until_ready()

    @rotate_status.error
    async def _rotate_error(self, error: Exception):
        await log_error(self.bot, f"Status rotation task error: {repr(error)}")

    @tasks.loop(seconds=60)
    async def rotate_server_icon(self):
        if not self._server_icon_rotation_enabled():
            return
        allowed = self.bot.config.get_int("guild", "allowed_guild_id")
        guild = self.bot.get_guild(allowed) if allowed else None
        if guild is None:
            return

        cfg = ensure_server_icon_config(self.bot.config)
        interval = self._server_icon_interval()
        now_ts = int(time.time())
        last_changed = int(cfg.get("last_changed_ts", 0) or 0)
        if last_changed and now_ts - last_changed < interval:
            return

        ok, message = await self.rotate_server_icon_once(guild)
        if not ok:
            await log_error(self.bot, f"Server icon rotation skipped: {message}")

    @rotate_server_icon.before_loop
    async def _before_server_icon_rotate(self):
        await self.bot.wait_until_ready()

    @rotate_server_icon.error
    async def _server_icon_rotate_error(self, error: Exception):
        await log_error(self.bot, f"Server icon rotation task error: {repr(error)}")

    def _start_daily_report_loop(self):
        hh, mm = _parse_hhmm(str(self.bot.config.get("background", "daily_summary", "time", default="00:00") or "00:00"))
        target = dtime(hour=hh, minute=mm, tzinfo=TZ)

        try:
            self.daily_report.change_interval(time=target)
        except Exception:
            pass

        try:
            if not self.daily_report.is_running():
                self.daily_report.start()
        except Exception:
            pass

    def _top_channel_lines(self, items: List[Tuple[int, int]], total_messages: int) -> str:
        if not items:
            return "No channel activity recorded."
        lines = []
        for idx, (channel_id, count) in enumerate(items[:5], start=1):
            lines.append(f"**{idx}.** <#{channel_id}> - **{_fmt_num(count)}** ({_fmt_percent(count, total_messages)})")
        return "\n".join(lines)

    def _top_member_lines(self, items: List[Tuple[int, int]], total_messages: int) -> str:
        if not items:
            return "No member activity recorded."
        lines = []
        for idx, (user_id, count) in enumerate(items[:5], start=1):
            lines.append(f"**{idx}.** <@{user_id}> - **{_fmt_num(count)}** ({_fmt_percent(count, total_messages)})")
        return "\n".join(lines)

    def _top_command_lines(self, items: List[Tuple[str, int]], total_commands: int) -> str:
        if not items:
            return "No slash commands recorded."
        lines = []
        for idx, (name, count) in enumerate(items[:5], start=1):
            lines.append(f"**{idx}.** `/{name}` - **{_fmt_num(count)}** ({_fmt_percent(count, total_commands)})")
        return "\n".join(lines)

    def _summary_color(self, snapshot: DailyStats, net_members: int) -> discord.Color:
        if snapshot.bans or snapshot.command_errors >= 5:
            return discord.Color.orange()
        if net_members < 0:
            return discord.Color.red()
        if snapshot.boosts > snapshot.unboosts or snapshot.messages >= 500:
            return discord.Color.green()
        return discord.Color.blurple()

    @tasks.loop(time=dtime(hour=0, minute=0, tzinfo=TZ))
    async def daily_report(self):
        allowed = self.bot.config.get_int("guild", "allowed_guild_id")
        guild = self.bot.get_guild(allowed) if allowed else None
        if guild is None:
            return

        today = _day_key()
        yesterday = _day_key(now_madrid() - timedelta(days=1))
        report_day = self._current_day if self._current_day != today else yesterday
        snapshot = self.stats if report_day == self._current_day else None
        if snapshot is None:
            snapshot = await self._load_daily_stats(guild.id, report_day)
        if snapshot is None:
            snapshot = DailyStats()
        day_key = report_day

        prev_snapshot = None
        try:
            report_dt = datetime.strptime(day_key, "%Y-%m-%d").replace(tzinfo=TZ)
            prev_day_key = (report_dt - timedelta(days=1)).strftime("%Y-%m-%d")
            prev_snapshot = await self._load_daily_stats(guild.id, prev_day_key)
        except Exception:
            prev_snapshot = None

        voice_minutes = int(snapshot.voice_minutes)
        report_boundary_ts = self._rollover_boundary_ts(day_key)
        if day_key == self._current_day:
            now_ts = report_boundary_ts if day_key != today else int(time.time())
            for joined_ts in self.voice_sessions.values():
                try:
                    minutes = int((now_ts - int(joined_ts)) // 60)
                    if minutes > 0:
                        voice_minutes += minutes
                except Exception:
                    continue

        active_channels = len(snapshot.by_channel)
        active_members = len(snapshot.by_user)
        net_members = int(snapshot.joins) - int(snapshot.leaves)
        moderation_actions = int(snapshot.deletes) + int(snapshot.bans) + int(snapshot.unbans)
        command_successes = max(int(snapshot.commands) - int(snapshot.command_errors), 0)
        command_success_rate = _fmt_percent(command_successes, int(snapshot.commands))
        avg_messages = (snapshot.messages / active_members) if active_members else 0.0
        reaction_rate = _fmt_percent(snapshot.reactions, snapshot.messages)

        top_channels = sorted(snapshot.by_channel.items(), key=lambda kv: kv[1], reverse=True)[:5]
        top_users = sorted(snapshot.by_user.items(), key=lambda kv: kv[1], reverse=True)[:5]
        top_cmds = sorted(snapshot.commands_by_name.items(), key=lambda kv: kv[1], reverse=True)[:5]

        busiest_channel = f"<#{top_channels[0][0]}> with **{_fmt_num(top_channels[0][1])}** messages" if top_channels else "No active channel"
        most_active_member = f"<@{top_users[0][0]}> with **{_fmt_num(top_users[0][1])}** messages" if top_users else "No active member"
        top_command = f"`/{top_cmds[0][0]}` used **{_fmt_num(top_cmds[0][1])}** times" if top_cmds else "No commands used"
        previous_messages = int(prev_snapshot.messages) if prev_snapshot else None
        previous_commands = int(prev_snapshot.commands) if prev_snapshot else None

        embed = discord.Embed(
            title=f"Daily Server Summary - {day_key}",
            description=(
                f"Messages: **{_fmt_num(snapshot.messages)}** ({_fmt_delta(snapshot.messages, previous_messages)} vs previous day)\n"
                f"Active members: **{_fmt_num(active_members)}** across **{_fmt_num(active_channels)}** channels\n"
                f"Member movement: **{net_members:+,}** net"
            ),
            color=self._summary_color(snapshot, net_members),
            timestamp=now_madrid(),
        )
        try:
            if guild.icon:
                embed.set_thumbnail(url=guild.icon.url)
        except Exception:
            pass

        embed.add_field(
            name="Activity",
            value=(
                f"Messages: **{_fmt_num(snapshot.messages)}**\n"
                f"Edits / deletes: **{_fmt_num(snapshot.edits)}** / **{_fmt_num(snapshot.deletes)}**\n"
                f"Reactions: **{_fmt_num(snapshot.reactions)}** ({reaction_rate} of messages)\n"
                f"Avg per active member: **{avg_messages:.1f}** messages"
            ),
            inline=False,
        )
        embed.add_field(
            name="Community",
            value=(
                f"Joins / leaves: **{_fmt_num(snapshot.joins)}** / **{_fmt_num(snapshot.leaves)}**\n"
                f"Net change: **{net_members:+,}**\n"
                f"Boosts / unboosts: **{_fmt_num(snapshot.boosts)}** / **{_fmt_num(snapshot.unboosts)}**\n"
                f"Moderation actions: **{_fmt_num(moderation_actions)}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Voice And Presence",
            value=(
                f"Total voice time: **{_fmt_minutes(voice_minutes)}**\n"
                f"Peak in voice: **{_fmt_num(snapshot.peak_voice_users)}**\n"
                f"Peak online: **{_fmt_num(snapshot.peak_online_members)}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Commands",
            value=(
                f"Used: **{_fmt_num(snapshot.commands)}** ({_fmt_delta(snapshot.commands, previous_commands)} vs previous day)\n"
                f"Errors: **{_fmt_num(snapshot.command_errors)}**\n"
                f"Success rate: **{command_success_rate}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Highlights",
            value=(
                f"Busiest channel: {busiest_channel}\n"
                f"Most active member: {most_active_member}\n"
                f"Top command: {top_command}"
            ),
            inline=False,
        )
        embed.add_field(name="Top Channels", value=self._top_channel_lines(top_channels, snapshot.messages), inline=False)
        embed.add_field(name="Top Members", value=self._top_member_lines(top_users, snapshot.messages), inline=False)
        embed.add_field(name="Top Commands", value=self._top_command_lines(top_cmds, snapshot.commands), inline=False)
        embed.set_footer(text="Madrid-time daily report")

        ch_id = self._daily_summary_channel_id()
        channel = guild.get_channel(ch_id) if ch_id else None
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(embed=embed)
            except Exception:
                pass

        # persist
        try:
            await self._persist_daily_stats(guild.id, day_key, snapshot)
        except Exception:
            pass

        if self._daily_reset_after_report() and self._current_day == day_key:
            self._current_day = today
            self.stats = DailyStats()
            reset_ts = report_boundary_ts if day_key != today else int(time.time())
            self.voice_sessions = self._voice_sessions_from_guild(guild, reset_ts)

    @daily_report.before_loop
    async def _before_daily(self):
        await self.bot.wait_until_ready()

    @daily_report.error
    async def _daily_error(self, error: Exception):
        await log_error(self.bot, f"Daily report task error: {repr(error)}")

def setup(bot: discord.Bot):
    bot.add_cog(BackgroundCog(bot))
