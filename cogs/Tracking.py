from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timedelta
from typing import Optional, List, Tuple

import discord
from discord.ext import commands

from utils.checks import ensure_allowed_guild_id, basic_color
from utils.errors import log_error
from utils.timeutils import now_madrid, week_start_sunday, TZ
from utils.views import LevelRequestReviewView, TrackingDeclineConfirmView

REQUEST_DM_TEXT = (
    "Congratulations! You have been the most active member this week, so you have earned a **level request**. "
    "Please answer this message with the following format:\n"
    "> Level Name:\n"
    "> Level ID:\n"
    "> Creator:\n"
    "> Video (required for demons and platformers):\n"
    "> Notes (Optional):\n"
    "If you don’t want to claim this request, please answer with “I do not want this request”\n"
    "If you have any problems, please contact any of the Admins/Owners.\n"
    "Thanks for bringing so much dedication to our community!"
)


class TrackingCog(commands.Cog):
    """Tracks weekly message activity (Top N) and handles weekly request DM workflow.

    Public methods expected by Commands.py:
      - get_top(guild_id, week_start_iso, limit)
      - get_member_stats(guild, week_start_iso, user_id)
      - force_dm_for_user(guild, week_start_iso, user_id, timeout_hours=None)
      - reset_current_week(guild_id)
      - user_in_weekly_process(user_id)
      - handle_decline_confirm(interaction, confirmed)
    """

    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self._started = False
        self._weekly_task: Optional[asyncio.Task] = None
        self._timeout_task: Optional[asyncio.Task] = None
        self._activity_flush_task: Optional[asyncio.Task] = None
        self._activity_lock = asyncio.Lock()
        self._pending_activity_counts: dict[tuple[int, int, str], int] = {}
        self._pending_last_counted: dict[tuple[int, int], int] = {}
        self._last_counted_cache: dict[tuple[int, int], int] = {}
        self._last_error_log: dict[str, float] = {}
        self._anti_farm_cache: dict[tuple[int, int], list[tuple[int, str]]] = {}
        self._anti_farm_last_log: dict[tuple[int, int, str], int] = {}

    # ----------------------------
    # Config helpers (robust)
    # ----------------------------
    def _cfg_int(self, section: str, key: str, default: int = 0) -> int:
        cfg = self.bot.config
        try:
            return int(cfg.get_int(section, key, default=default))
        except TypeError:
            # older Config without default kwarg
            try:
                v = cfg.get_int(section, key)
                return int(v) if v is not None else int(default)
            except Exception:
                return int(default)
        except Exception:
            try:
                v = cfg.get(section, key, default=default)
                return int(v)
            except Exception:
                return int(default)

    def _cfg_int_list(self, section: str, key: str) -> list[int]:
        cfg = self.bot.config
        vals = None
        try:
            vals = cfg.get_int_list(section, key)
        except Exception:
            # fallback if config stores list of strings
            try:
                raw = cfg.get(section, key, default=[])
                vals = raw
            except Exception:
                vals = []
        if not vals:
            return []
        out: list[int] = []
        for v in vals:
            try:
                out.append(int(v))
            except Exception:
                continue
        return out

    def _format_template(self, value: object, variables: dict[str, object]) -> str:
        class _SafeDict(dict):
            def __missing__(self, key):
                return ""

        try:
            return str(value or "").format_map(_SafeDict({k: str(v) for k, v in variables.items()}))
        except Exception:
            return str(value or "")

    def _embed_from_template(self, template: dict, variables: dict[str, object], default_title: str, default_color: str) -> discord.Embed:
        if not isinstance(template, dict):
            template = {}

        embed = discord.Embed(
            title=self._format_template(template.get("title", default_title), variables) or None,
            description=self._format_template(template.get("description", ""), variables) or None,
            color=basic_color(self._format_template(template.get("color", default_color), variables) or default_color),
        )
        for field in template.get("fields", []) or []:
            if not isinstance(field, dict):
                continue
            name = self._format_template(field.get("name", ""), variables)
            value = self._format_template(field.get("value", ""), variables)
            if name and value:
                embed.add_field(name=name[:256], value=value[:1024], inline=bool(field.get("inline", False)))
        footer = self._format_template(template.get("footer", ""), variables)
        if footer:
            embed.set_footer(text=footer[:2048])
        thumbnail_url = self._format_template(template.get("thumbnail_url", ""), variables)
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        image_url = self._format_template(template.get("image_url", ""), variables)
        if image_url:
            embed.set_image(url=image_url)
        return embed

    def _weekly_request_review_data(self, content: str, apply_defaults: bool = True) -> dict[str, str]:
        aliases = {
            "name": "level_name",
            "level name": "level_name",
            "id": "level_id",
            "level id": "level_id",
            "creator": "creators",
            "creators": "creators",
            "creator s": "creators",
            "level showcase": "level_showcase",
            "showcase": "level_showcase",
            "video": "level_showcase",
            "notes": "notes",
            "note": "notes",
        }
        data: dict[str, str] = {"request_content": str(content or "").strip()}
        current_key: Optional[str] = None

        for raw_line in str(content or "").splitlines():
            line = raw_line.strip()
            if line.startswith(">"):
                line = line.lstrip("> ").strip()
            if not line:
                continue

            if ":" in line:
                raw_key, value = line.split(":", 1)
                normalized = re.sub(r"[^a-z0-9]+", " ", raw_key.casefold()).strip()
                field_key = aliases.get(normalized)
                if field_key:
                    current_key = field_key
                    if value.strip() or field_key not in data:
                        data[field_key] = value.strip()
                    continue

            if current_key:
                previous = str(data.get(current_key) or "").strip()
                data[current_key] = f"{previous}\n{line}".strip() if previous else line

        if not data.get("level_id"):
            match = re.search(r"\b(?:level\s*)?id\s*[:#-]?\s*([0-9]{3,})\b", str(content or ""), flags=re.I)
            if match:
                data["level_id"] = match.group(1)
        if not data.get("level_name"):
            match = re.search(r"\b(?:level\s*)?name\s*[:#-]?\s*(.+)", str(content or ""), flags=re.I)
            if match:
                data["level_name"] = match.group(1).strip()
        if not data.get("creators"):
            match = re.search(r"\bcreators?\s*[:#-]?\s*(.+)", str(content or ""), flags=re.I)
            if match:
                data["creators"] = match.group(1).strip()

        if not apply_defaults:
            return data

        data["level_id"] = str(data.get("level_id") or "Not provided").strip()
        data["level_id_normalized"] = data["level_id"].casefold()
        data["level_name"] = str(data.get("level_name") or "Weekly request").strip()
        data["creators"] = str(data.get("creators") or "Not provided").strip()
        data["level_showcase"] = str(data.get("level_showcase") or "Not provided").strip()
        data["notes"] = str(data.get("notes") or data["request_content"] or "No notes provided").strip()
        return data

    def _weekly_request_missing_fields(self, content: str) -> list[str]:
        data = self._weekly_request_review_data(content, apply_defaults=False)
        missing = []
        if not str(data.get("level_name") or "").strip():
            missing.append("Level Name")
        if not str(data.get("level_id") or "").strip():
            missing.append("Level ID")
        if not str(data.get("creators") or "").strip():
            missing.append("Creator")
        return missing

    def _weekly_request_max_chars(self) -> int:
        try:
            return max(500, min(5000, int(self.bot.config.get("tracking", "weekly_request_max_chars", default=3000) or 3000)))
        except Exception:
            return 3000

    async def _resolve_member(self, guild: discord.Guild, user_or_id) -> Optional[discord.Member]:
        if isinstance(user_or_id, discord.Member):
            return user_or_id
        user_id = getattr(user_or_id, "id", user_or_id)
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

    async def _configured_channel(self, guild: discord.Guild, channel_id: int) -> Optional[discord.TextChannel]:
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None and channel_id:
            try:
                channel = await guild.fetch_channel(channel_id)
            except Exception:
                channel = None
        return channel if isinstance(channel, discord.TextChannel) else None

    async def _log_background_error(self, key: str, message: str) -> None:
        now = time.time()
        if now - self._last_error_log.get(key, 0.0) < 300:
            return
        self._last_error_log[key] = now
        await log_error(self.bot, message)

    async def _dm_user(self, user_id: int, message: str) -> None:
        try:
            user = await self.bot.fetch_user(int(user_id))
            await user.send(str(message)[:2000])
        except Exception:
            pass

    async def _validate_weekly_request_for_review(
        self,
        guild: discord.Guild,
        user_id: int,
        week_start_iso: str,
        review_data: dict[str, str],
    ) -> tuple[bool, dict[str, str]]:
        request_cog = self.bot.get_cog("RequestLevelsCog")
        if request_cog is None:
            return True, review_data

        data = dict(review_data)
        if str(data.get("level_showcase") or "").strip().casefold() == "not provided":
            data["level_showcase"] = ""
        try:
            data["level_id"] = str(data.get("level_id") or "").strip()
            data["level_id_normalized"] = request_cog._normalize_level_id(data.get("level_id"))
            validation_errors = request_cog._validate_request_data(data)
        except Exception as e:
            await log_error(self.bot, f"Weekly request local validation failed for user_id={user_id}: {repr(e)}")
            return True, review_data

        if validation_errors:
            reason = " ".join(validation_errors)
            await self._log_weekly(guild, week_start_iso, user_id, "request_record_failed", f"reason=validation_error detail={reason[:180]}")
            await self._dm_user(user_id, f"Please fix your weekly request before submitting it: {reason}")
            return False, review_data

        try:
            external_errors, level_validation = await request_cog._validate_level_external(data, guild.id, user_id)
        except Exception as e:
            await log_error(self.bot, f"Weekly request external validation failed for user_id={user_id}: {repr(e)}")
            external_errors, level_validation = [], {}

        if external_errors:
            reason = " ".join(external_errors)
            await self._log_weekly(guild, week_start_iso, user_id, "request_record_failed", f"reason=external_validation detail={reason[:180]}")
            await self._dm_user(user_id, f"Please fix your weekly request before submitting it: {reason}")
            return False, review_data

        try:
            data = request_cog._apply_level_validation_vars(data, level_validation)
        except Exception as e:
            await log_error(self.bot, f"Weekly request validation variables failed for user_id={user_id}: {repr(e)}")
            return True, review_data

        if not str(data.get("level_showcase") or "").strip():
            data["level_showcase"] = "Not provided"
        return True, data

    # ----------------------------
    # Startup / schema
    # ----------------------------
    async def start_background(self):
        if self._started:
            return
        self._started = True

        # Ensure DB is ready and schema exists (best-effort; db.py migrations may already do this)
        try:
            await self.bot.db.connect()
            await self.bot.db.execute(
                """CREATE TABLE IF NOT EXISTS weekly_runs(
                    guild_id INTEGER NOT NULL,
                    week_start TEXT NOT NULL,
                    ran_ts INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, week_start)
                );"""
            )
            await self.bot.db.execute(
                """CREATE TABLE IF NOT EXISTS weekly_reward_disabled(
                    guild_id INTEGER NOT NULL,
                    week_start TEXT NOT NULL,
                    disabled_ts INTEGER NOT NULL,
                    disabled_by INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, week_start)
                );"""
            )
            await self.bot.db.execute(
                """CREATE TABLE IF NOT EXISTS weekly_dm_log(
                    guild_id INTEGER NOT NULL,
                    week_start TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    event TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    ts INTEGER NOT NULL
                );"""
            )
            await self.bot.db.execute(
                """CREATE TABLE IF NOT EXISTS weekly_reminders(
                    guild_id INTEGER NOT NULL,
                    week_start TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    reminded_ts INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, week_start, user_id)
                );"""
            )
        except Exception as e:
            await self._log_background_error("tracking_schema", f"Tracking schema setup failed: {repr(e)}")

        self._weekly_task = asyncio.create_task(self._weekly_loop())
        self._timeout_task = asyncio.create_task(self._timeout_loop())
        self._activity_flush_task = asyncio.create_task(self._activity_flush_loop())

    def on_config_reload(self) -> None:
        # no cached config in this cog
        pass

    # ----------------------------
    # Public API: used by Help cog
    # ----------------------------
    async def user_in_weekly_process(self, user_id: int) -> bool:
        allowed_guild_id = self._cfg_int("guild", "allowed_guild_id", 0)
        if not allowed_guild_id:
            return False
        row = await self.bot.db.fetchone(
            "SELECT 1 FROM weekly_sessions WHERE guild_id=? AND user_id=? AND active=1 LIMIT 1",
            (allowed_guild_id, user_id),
        )
        return row is not None

    async def weekly_reward_disabled(self, guild_id: int, week_start_iso: str) -> bool:
        row = await self.bot.db.fetchone(
            "SELECT 1 FROM weekly_reward_disabled WHERE guild_id=? AND week_start=?",
            (guild_id, week_start_iso),
        )
        return row is not None

    async def disable_weekly_reward_for_current_week(self, guild: discord.Guild, disabled_by: int) -> str:
        week_start_iso = week_start_sunday(now_madrid()).isoformat()
        await self.bot.db.execute(
            "INSERT OR REPLACE INTO weekly_reward_disabled(guild_id, week_start, disabled_ts, disabled_by) VALUES(?,?,?,?)",
            (guild.id, week_start_iso, int(time.time()), int(disabled_by)),
        )
        await self.bot.db.execute(
            "UPDATE weekly_claims SET status='disabled' WHERE guild_id=? AND week_start=? AND status='pending'",
            (guild.id, week_start_iso),
        )
        await self.bot.db.execute(
            "UPDATE weekly_sessions SET active=0 WHERE guild_id=? AND week_start=?",
            (guild.id, week_start_iso),
        )
        await self._log_weekly(guild, week_start_iso, disabled_by, "weekly_reward_disabled", "Reward disabled for this tracking week")
        return week_start_iso

    async def enable_weekly_reward_for_current_week(self, guild: discord.Guild, enabled_by: int) -> tuple[str, bool]:
        week_start_iso = week_start_sunday(now_madrid()).isoformat()
        was_disabled = await self.weekly_reward_disabled(guild.id, week_start_iso)

        await self.bot.db.execute(
            "DELETE FROM weekly_reward_disabled WHERE guild_id=? AND week_start=?",
            (guild.id, week_start_iso),
        )
        await self.bot.db.execute(
            "UPDATE weekly_sessions SET active=1, stage='awaiting_request' "
            "WHERE guild_id=? AND week_start=? AND EXISTS ("
            "SELECT 1 FROM weekly_claims c "
            "WHERE c.guild_id=weekly_sessions.guild_id "
            "AND c.week_start=weekly_sessions.week_start "
            "AND c.user_id=weekly_sessions.user_id "
            "AND c.status='disabled'"
            ")",
            (guild.id, week_start_iso),
        )
        await self.bot.db.execute(
            "UPDATE weekly_claims SET status='pending' WHERE guild_id=? AND week_start=? AND status='disabled'",
            (guild.id, week_start_iso),
        )

        await self._log_weekly(guild, week_start_iso, enabled_by, "weekly_reward_enabled", "Reward enabled for this tracking week")
        return week_start_iso, was_disabled

    # ----------------------------
    # Logging helpers
    # ----------------------------
    def _weekly_log_meta(self, event: str) -> tuple[str, discord.Color]:
        mapping = {
            "weekly_job_start": ("Weekly scan started", discord.Color.blurple()),
            "weekly_job_done": ("Weekly scan finished", discord.Color.green()),
            "dm_sent": ("Request DM sent", discord.Color.green()),
            "dm_failed": ("Request DM failed", discord.Color.red()),
            "dm_closed": ("DMs closed", discord.Color.red()),
            "timeout_dm_sent": ("Timeout notice sent", discord.Color.orange()),
            "timed_out": ("Request timed out", discord.Color.orange()),
            "reminder_sent": ("Reminder sent", discord.Color.gold()),
            "reminder_failed": ("Reminder failed", discord.Color.red()),
            "request_recorded": ("Request recorded", discord.Color.green()),
            "request_record_failed": ("Request record failed", discord.Color.red()),
            "declined": ("Request declined", discord.Color.orange()),
            "offered_next": ("Offered to next member", discord.Color.blurple()),
            "no_eligible_member": ("No eligible member", discord.Color.dark_grey()),
            "skipped_already_contacted": ("Skipped already contacted member", discord.Color.dark_grey()),
            "weekly_reward_disabled": ("Weekly reward disabled", discord.Color.red()),
            "weekly_reward_enabled": ("Weekly reward enabled", discord.Color.green()),
            "weekly_reward_skipped": ("Weekly reward skipped", discord.Color.red()),
            "skipped_reward_disabled": ("Skipped while reward disabled", discord.Color.dark_grey()),
            "next_offer_skipped_reward_disabled": ("Next offer skipped", discord.Color.dark_grey()),
            "force_dm_sent": ("Force DM sent", discord.Color.green()),
            "force_dm_failed": ("Force DM failed", discord.Color.red()),
            "force_dm_blocked": ("Force DM blocked", discord.Color.orange()),
            "force_dm_override": ("Force DM override", discord.Color.gold()),
        }
        return mapping.get(str(event), (str(event).replace("_", " ").title(), discord.Color.blurple()))

    def _weekly_detail_lines(self, detail: str) -> str:
        detail = str(detail or "").strip()
        if not detail:
            return "No extra details."

        parts = detail.split()
        if parts and all("=" in part for part in parts):
            lines = []
            for part in parts:
                key, value = part.split("=", 1)
                label = key.replace("_", " ").title()
                lines.append(f"**{label}:** {value}")
            return "\n".join(lines)[:1024]
        return detail[:1024]

    async def _log_weekly(self, guild: discord.Guild, week_start: str, user_id: int, event: str, detail: str = "") -> None:
        # DB log (best-effort)
        try:
            await self.bot.db.execute(
                "INSERT INTO weekly_dm_log(guild_id, week_start, user_id, event, detail, ts) VALUES(?,?,?,?,?,?)",
                (guild.id, week_start, int(user_id), str(event), str(detail)[:500], int(time.time())),
            )
        except Exception:
            pass

        # Optional channel log
        log_channel_id = self._cfg_int("tracking", "log_channel_id", 0)
        if not log_channel_id:
            log_channel_id = self._cfg_int("channels", "general_logging_channel_id", 0)

        ch = guild.get_channel(log_channel_id) if log_channel_id else None
        if isinstance(ch, discord.TextChannel):
            try:
                label, color = self._weekly_log_meta(event)
                emb = discord.Embed(
                    title=f"Weekly Request: {label}",
                    description="A weekly request workflow event was recorded.",
                    color=color,
                    timestamp=now_madrid(),
                )
                emb.add_field(name="Event", value=f"`{event}`", inline=True)
                emb.add_field(name="Week", value=week_start, inline=True)
                if user_id:
                    emb.add_field(name="Member", value=f"<@{user_id}>\n`{user_id}`", inline=True)
                else:
                    emb.add_field(name="Member", value="Server-wide", inline=True)
                emb.add_field(name="Details", value=self._weekly_detail_lines(detail), inline=False)
                emb.set_footer(text="Weekly request workflow")
                await ch.send(embed=emb)
            except Exception:
                pass

    def _anti_farm_cfg(self) -> dict:
        cfg = self.bot.config.get("tracking", "anti_farm", default={}) or {}
        return cfg if isinstance(cfg, dict) else {}

    def _anti_farm_enabled(self) -> bool:
        return bool(self._anti_farm_cfg().get("enabled", False))

    def _message_signature(self, content: str) -> str:
        text = re.sub(r"https?://\S+", "", str(content or "").casefold())
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    async def _anti_farm_reason(self, message: discord.Message, now: int) -> str:
        if not self._anti_farm_enabled():
            return ""
        cfg = self._anti_farm_cfg()
        try:
            min_unique = max(1, int(cfg.get("min_unique_chars", 3)))
        except Exception:
            min_unique = 3
        try:
            min_words = max(1, int(cfg.get("min_words", 2)))
        except Exception:
            min_words = 2
        try:
            window = max(10, int(cfg.get("repeat_window_seconds", 120)))
        except Exception:
            window = 120
        try:
            threshold = max(2, int(cfg.get("repeat_threshold", 3)))
        except Exception:
            threshold = 3

        signature = self._message_signature(message.content or "")
        if not signature:
            return ""
        compact = signature.replace(" ", "")
        words = [word for word in signature.split() if word]
        low_effort = len(set(compact)) < min_unique or len(words) < min_words

        key = (message.guild.id, message.author.id)
        history = [(ts, sig) for ts, sig in self._anti_farm_cache.get(key, []) if now - int(ts) <= window]
        repeat_count = 1 + sum(1 for _ts, sig in history if sig == signature)
        history.append((now, signature))
        self._anti_farm_cache[key] = history[-25:]

        if low_effort and repeat_count >= threshold:
            return "repeated_low_effort"
        return ""

    async def _record_anti_farm_event(self, message: discord.Message, reason: str, now: int) -> None:
        sample = str(message.content or "").strip().replace("\n", " ")[:180]
        log_key = (message.guild.id, message.author.id, reason)
        last_log = self._anti_farm_last_log.get(log_key, 0)
        if last_log and now - last_log < 300:
            return
        self._anti_farm_last_log[log_key] = now
        try:
            await self.bot.db.execute(
                "INSERT INTO anti_farm_events(guild_id,user_id,channel_id,reason,sample,ts) VALUES(?,?,?,?,?,?)",
                (message.guild.id, message.author.id, message.channel.id, reason, sample, now),
            )
        except Exception:
            pass

        cfg = self._anti_farm_cfg()
        try:
            channel_id = int(cfg.get("log_channel_id") or 0)
        except Exception:
            channel_id = 0
        if not channel_id:
            channel_id = self._cfg_int("channels", "general_logging_channel_id", 0)
        channel = message.guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            return
        embed = discord.Embed(
            title="Anti-Farm Detection",
            description="A repeated low-effort message pattern was skipped from weekly tracking.",
            color=discord.Color.orange(),
            timestamp=now_madrid(),
        )
        embed.add_field(name="Member", value=f"{message.author.mention}\n`{message.author.id}`", inline=True)
        embed.add_field(name="Channel", value=getattr(message.channel, "mention", str(message.channel.id)), inline=True)
        embed.add_field(name="Reason", value=reason.replace("_", " ").title(), inline=True)
        if sample:
            embed.add_field(name="Sample", value=sample[:1024], inline=False)
        try:
            await channel.send(embed=embed)
        except Exception:
            pass

    # ----------------------------
    # Activity counting
    # ----------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # DM handling for weekly request process
        if message.guild is None:
            await self._handle_dm(message)
            return

        allowed_guild_id = self._cfg_int("guild", "allowed_guild_id", 0)
        if not ensure_allowed_guild_id(message.guild, allowed_guild_id):
            return

        # Exclude blacklisted roles
        excluded_role_ids = set(self._cfg_int_list("roles", "excluded_tracking_role_id"))
        if excluded_role_ids:
            m = await self._resolve_member(message.guild, message.author)
            if m and any(r.id in excluded_role_ids for r in m.roles):
                return

        # Exclude channels
        excluded_channels = set(self._cfg_int_list("channels", "excluded_tracking_channel_ids")) | set(
            self._cfg_int_list("channels", "bot_commands_channel_ids")
        )
        review_access_channel_id = self._cfg_int("channels", "review_access_channel_id", 0)
        if review_access_channel_id:
            excluded_channels.add(review_access_channel_id)
        if message.channel.id in excluded_channels:
            return

        now = int(time.time())
        anti_farm_reason = await self._anti_farm_reason(message, now)
        if anti_farm_reason:
            await self._record_anti_farm_event(message, anti_farm_reason, now)
            return

        cd = self._cfg_int("tracking", "count_cooldown_seconds", 10)
        cache_key = (message.guild.id, message.author.id)

        last_counted = self._last_counted_cache.get(cache_key)
        if last_counted is None:
            row = await self.bot.db.fetchone(
                "SELECT last_counted_ts FROM activity_last_counted WHERE guild_id=? AND user_id=?",
                (message.guild.id, message.author.id),
            )
            last_counted = int(row["last_counted_ts"]) if row else 0
            self._last_counted_cache[cache_key] = last_counted

        if last_counted and now - int(last_counted) < cd:
            return

        ws_iso = week_start_sunday(now_madrid()).isoformat()
        self._last_counted_cache[cache_key] = now

        async with self._activity_lock:
            count_key = (message.guild.id, message.author.id, ws_iso)
            self._pending_activity_counts[count_key] = self._pending_activity_counts.get(count_key, 0) + 1
            self._pending_last_counted[cache_key] = now

    async def _activity_flush_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                await asyncio.sleep(max(5, self._cfg_int("tracking", "activity_flush_seconds", 30) or 30))
                await self.flush_activity_counts()
            except asyncio.CancelledError:
                return
            except Exception as e:
                await self._log_background_error("activity_flush", f"Activity flush loop error: {repr(e)}")

    async def flush_activity_counts(self) -> None:
        async with self._activity_lock:
            counts = self._pending_activity_counts
            last_seen = self._pending_last_counted
            self._pending_activity_counts = {}
            self._pending_last_counted = {}

        if not counts and not last_seen:
            return

        try:
            if counts:
                await self.bot.db.executemany(
                    "INSERT INTO activity_counts(guild_id,user_id,week_start,count) VALUES(?,?,?,?) "
                    "ON CONFLICT(guild_id,user_id,week_start) DO UPDATE SET count=count+excluded.count",
                    [(guild_id, user_id, week_start, amount) for (guild_id, user_id, week_start), amount in counts.items()],
                )
            if last_seen:
                await self.bot.db.executemany(
                    "INSERT INTO activity_last_counted(guild_id,user_id,last_counted_ts) VALUES(?,?,?) "
                    "ON CONFLICT(guild_id,user_id) DO UPDATE SET last_counted_ts=excluded.last_counted_ts",
                    [(guild_id, user_id, ts) for (guild_id, user_id), ts in last_seen.items()],
                )
        except Exception as e:
            async with self._activity_lock:
                for key, amount in counts.items():
                    self._pending_activity_counts[key] = self._pending_activity_counts.get(key, 0) + amount
                for key, ts in last_seen.items():
                    self._pending_last_counted[key] = max(self._pending_last_counted.get(key, 0), ts)
            await self._log_background_error("activity_flush_db", f"Activity flush failed: {repr(e)}")

    # ----------------------------
    # Weekly DM workflow in DMs
    # ----------------------------
    async def _handle_dm(self, message: discord.Message):
        allowed_guild_id = self._cfg_int("guild", "allowed_guild_id", 0)
        guild = self.bot.get_guild(allowed_guild_id) if allowed_guild_id else None
        if guild is None:
            return
        if await self._resolve_member(guild, message.author.id) is None:
            return

        # Find active session for this user (latest week_start)
        rows = await self.bot.db.fetchall(
            "SELECT week_start, stage, expires_ts FROM weekly_sessions "
            "WHERE guild_id=? AND user_id=? AND active=1 ORDER BY week_start DESC LIMIT 1",
            (allowed_guild_id, message.author.id),
        )
        if not rows:
            return

        sess = rows[0]
        expires_ts = int(sess["expires_ts"])
        if int(time.time()) > expires_ts:
            return

        content = (message.content or "").strip()
        if len(content) > self._weekly_request_max_chars():
            try:
                await message.channel.send(
                    f"That request is too long. Please keep it under {self._weekly_request_max_chars()} characters."
                )
            except Exception:
                pass
            return

        if content.casefold() == "i do not want this request".casefold():
            embed = discord.Embed(
                title="Are you sure?",
                description="If you confirm, the request will be offered to the next eligible member.",
            )
            try:
                await message.channel.send(embed=embed, view=TrackingDeclineConfirmView())
                await self.bot.db.execute(
                    "UPDATE weekly_sessions SET stage='confirm_decline' WHERE guild_id=? AND user_id=? AND week_start=?",
                    (allowed_guild_id, message.author.id, sess["week_start"]),
                )
            except Exception:
                pass
            return

        if sess["stage"] == "confirm_decline":
            return

        missing = self._weekly_request_missing_fields(content)
        if not missing:
            await self._record_request(guild, message.author.id, sess["week_start"], content)
            return

        try:
            await message.channel.send(
                "Please send your request using the format provided. "
                f"Missing: **{', '.join(missing)}**."
            )
        except Exception:
            pass

    async def _record_request(self, guild: discord.Guild, user_id: int, week_start_iso: str, content: str):
        weekly_channel_id = self._cfg_int("channels", "weekly_request_channel_ID", 0)
        channel = await self._configured_channel(guild, weekly_channel_id)
        if channel is None:
            await self._log_weekly(guild, week_start_iso, user_id, "request_record_failed", "reason=weekly_request_channel_missing")
            await log_error(self.bot, f"Weekly request from user_id={user_id} could not be recorded: weekly_request_channel_ID is missing or invalid.")
            try:
                user = await self.bot.fetch_user(user_id)
                await user.send("I couldn't record your request because the staff request channel is not configured correctly. Please contact staff.")
            except Exception:
                pass
            return

        row = await self.bot.db.fetchone(
            "SELECT rank FROM weekly_claims WHERE guild_id=? AND week_start=? AND user_id=?",
            (guild.id, week_start_iso, user_id),
        )
        rank = int(row["rank"]) if row else None

        review_data = self._weekly_request_review_data(content)
        ok, review_data = await self._validate_weekly_request_for_review(guild, user_id, week_start_iso, review_data)
        if not ok:
            return
        created_ts = int(time.time())
        variables = {
            **review_data,
            "user_id": user_id,
            "user_mention": f"<@{user_id}>",
            "requester_id": user_id,
            "requester_mention": f"<@{user_id}>",
            "rank": f"#{rank}" if rank else "Unknown",
            "weekly_rank": f"#{rank}" if rank else "Unknown",
            "week_start": week_start_iso,
            "request_content": content,
            "created_ts": created_ts,
            "submitted_ts": created_ts,
            "submitted_ago": f"<t:{created_ts}:R>",
        }
        template = self.bot.config.get("level_requests", "weekly_request_submitted_embed", default={}) or {}
        if isinstance(template, dict) and template:
            embed = self._embed_from_template(template, variables, "Weekly Request Submitted", "gold")
        else:
            embed = discord.Embed(title="Weekly Request Submitted")
            embed.add_field(name="User", value=f"<@{user_id}> ({user_id})", inline=False)
            if rank:
                embed.add_field(name="Rank", value=f"#{rank}", inline=True)
            embed.add_field(name="Week start", value=week_start_iso, inline=False)
            embed.add_field(name="Content", value=content[:1024], inline=False)

        msg = None
        try:
            msg = await channel.send(embed=embed, view=LevelRequestReviewView())
        except Exception as e:
            await self._log_weekly(guild, week_start_iso, user_id, "request_record_failed", f"reason=weekly_request_send_failed error={type(e).__name__}")
            await log_error(self.bot, f"Weekly request from user_id={user_id} could not be sent to staff channel {channel.id}: {repr(e)}")
            try:
                user = await self.bot.fetch_user(user_id)
                await user.send("I couldn't record your request right now because I could not send it to the staff channel. Please contact staff.")
            except Exception:
                pass
            return

        try:
            await self.bot.db.execute(
                "INSERT OR REPLACE INTO weekly_request_reviews("
                "guild_id,request_message_id,channel_id,user_id,week_start,rank,status,created_ts,data_json"
                ") VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    guild.id,
                    msg.id,
                    channel.id,
                    user_id,
                    week_start_iso,
                    rank,
                    "pending",
                    created_ts,
                    json.dumps(review_data, separators=(",", ":")),
                ),
            )
        except Exception as e:
            try:
                await msg.delete()
            except Exception:
                try:
                    await msg.edit(view=LevelRequestReviewView(disabled=True))
                except Exception:
                    pass
            await self._log_weekly(guild, week_start_iso, user_id, "request_record_failed", f"reason=weekly_review_db_failed error={type(e).__name__}")
            await log_error(self.bot, f"Weekly request review row could not be saved for message_id={msg.id}: {repr(e)}")
            try:
                user = await self.bot.fetch_user(user_id)
                await user.send("I couldn't finish recording your request because of a database issue. Please try again or contact staff.")
            except Exception:
                pass
            return

        # Mark claimed & close session
        await self.bot.db.execute(
            "UPDATE weekly_claims SET status='claimed' WHERE guild_id=? AND week_start=? AND user_id=?",
            (guild.id, week_start_iso, user_id),
        )
        await self.bot.db.execute(
            "UPDATE weekly_sessions SET active=0 WHERE guild_id=? AND week_start=? AND user_id=?",
            (guild.id, week_start_iso, user_id),
        )
        await self._log_weekly(guild, week_start_iso, user_id, "request_recorded", f"rank={rank if rank is not None else 'unknown'}")

        try:
            user = await self.bot.fetch_user(user_id)
            await user.send(f"Thanks! Your request has been recorded: {msg.jump_url}")
        except Exception:
            pass

    # Button callback entrypoint (view calls this)
    async def handle_decline_confirm(self, interaction: discord.Interaction, confirmed: bool):
        allowed_guild_id = self._cfg_int("guild", "allowed_guild_id", 0)
        guild = self.bot.get_guild(allowed_guild_id) if allowed_guild_id else None
        if guild is None:
            try:
                await interaction.response.send_message("Guild not found.", ephemeral=True)
            except Exception:
                pass
            return

        row = await self.bot.db.fetchone(
            "SELECT week_start FROM weekly_sessions "
            "WHERE guild_id=? AND user_id=? AND active=1 AND stage='confirm_decline' "
            "ORDER BY week_start DESC LIMIT 1",
            (guild.id, interaction.user.id),
        )
        if not row:
            try:
                await interaction.response.send_message("No pending confirmation found.", ephemeral=True)
            except Exception:
                pass
            return

        week_start_iso = row["week_start"]

        if not confirmed:
            try:
                await interaction.response.send_message("Request resumed, please send your request with the format!", ephemeral=True)
            except Exception:
                pass
            await self.bot.db.execute(
                "UPDATE weekly_sessions SET stage='awaiting_request' WHERE guild_id=? AND user_id=? AND week_start=?",
                (guild.id, interaction.user.id, week_start_iso),
            )
            return

        await self.bot.db.execute(
            "UPDATE weekly_claims SET status='declined' WHERE guild_id=? AND week_start=? AND user_id=?",
            (guild.id, week_start_iso, interaction.user.id),
        )
        await self._log_weekly(guild, week_start_iso, interaction.user.id, "declined", "User confirmed decline")
        await self.bot.db.execute(
            "UPDATE weekly_sessions SET active=0 WHERE guild_id=? AND week_start=? AND user_id=?",
            (guild.id, week_start_iso, interaction.user.id),
        )
        try:
            await interaction.response.send_message("Confirmed. Offering the request to the next eligible member.", ephemeral=True)
        except Exception:
            pass
        await self._contact_next_eligible(guild, week_start_iso)

    # ----------------------------
    # Weekly scheduler loops
    # ----------------------------
    async def _weekly_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                await asyncio.sleep(60)
                now = now_madrid()

                allowed_guild_id = self._cfg_int("guild", "allowed_guild_id", 0)
                guild = self.bot.get_guild(allowed_guild_id) if allowed_guild_id else None
                if guild is None:
                    continue

                # This Sunday's 00:00 (start of current week)
                this_sunday = week_start_sunday(now)
                this_sunday_iso = this_sunday.isoformat()

                # If we already processed this Sunday, skip
                row = await self.bot.db.fetchone(
                    "SELECT ran_ts FROM weekly_runs WHERE guild_id=? AND week_start=?",
                    (guild.id, this_sunday_iso),
                )
                if row is not None:
                    continue

                # Run job for previous week
                await self.flush_activity_counts()
                prev_week_start = week_start_sunday(this_sunday - timedelta(seconds=1)).isoformat()
                await self.run_weekly_job(prev_week_start)

                await self.bot.db.execute(
                    "INSERT OR REPLACE INTO weekly_runs(guild_id, week_start, ran_ts) VALUES(?,?,?)",
                    (guild.id, this_sunday_iso, int(time.time())),
                )

                # Optional: clear last_counted so first post after reset always counts
                try:
                    await self.bot.db.execute("DELETE FROM activity_last_counted WHERE guild_id=?", (guild.id,))
                except Exception:
                    pass
            except asyncio.CancelledError:
                return
            except Exception as e:
                await self._log_background_error("weekly_loop", f"Weekly loop error: {repr(e)}")

    async def _timeout_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                await asyncio.sleep(600)  # every 10 minutes
                await self._process_timeouts()
                await self._process_reminders()
            except asyncio.CancelledError:
                return
            except Exception as e:
                await self._log_background_error("timeout_loop", f"Weekly timeout/reminder loop error: {repr(e)}")

    async def _process_timeouts(self):
        allowed_guild_id = self._cfg_int("guild", "allowed_guild_id", 0)
        guild = self.bot.get_guild(allowed_guild_id) if allowed_guild_id else None
        if guild is None:
            return

        now_ts = int(time.time())
        rows = await self.bot.db.fetchall(
            "SELECT week_start, user_id FROM weekly_sessions WHERE guild_id=? AND active=1 AND expires_ts<=?",
            (guild.id, now_ts),
        )
        for r in rows:
            week_start_iso = r["week_start"]
            user_id = int(r["user_id"])

            try:
                user = await self.bot.fetch_user(user_id)
                await user.send("Request timed out")
                await self._log_weekly(guild, week_start_iso, user_id, "timeout_dm_sent", "")
            except Exception:
                pass

            await self.bot.db.execute(
                "UPDATE weekly_claims SET status='timed_out' WHERE guild_id=? AND week_start=? AND user_id=?",
                (guild.id, week_start_iso, user_id),
            )
            await self._log_weekly(guild, week_start_iso, user_id, "timed_out", "No reply before deadline")
            await self.bot.db.execute(
                "UPDATE weekly_sessions SET active=0 WHERE guild_id=? AND week_start=? AND user_id=?",
                (guild.id, week_start_iso, user_id),
            )

            await self._contact_next_eligible(guild, week_start_iso)

    async def _update_weekly_streaks(self, guild: discord.Guild, week_start_iso: str, ranked: list[int]) -> None:
        try:
            top_rank = max(1, int(self.bot.config.get("tracking", "streak_top_rank", default=5) or 5))
        except Exception:
            top_rank = 5
        top_users = [int(uid) for uid in ranked[:top_rank]]
        now_ts = int(time.time())
        try:
            week_dt = datetime.fromisoformat(week_start_iso)
            prev_week = (week_dt - timedelta(days=7)).date().isoformat()
        except Exception:
            prev_week = ""

        for uid in top_users:
            row = await self.bot.db.fetchone(
                "SELECT streak, best_streak, last_week_start FROM weekly_streaks WHERE guild_id=? AND user_id=?",
                (guild.id, uid),
            )
            if row and str(row["last_week_start"] or "") == week_start_iso:
                streak = max(1, int(row["streak"] or 1))
                best = max(streak, int(row["best_streak"] or 0))
            elif row and prev_week and str(row["last_week_start"] or "") == prev_week:
                streak = int(row["streak"] or 0) + 1
                best = max(streak, int(row["best_streak"] or 0))
            else:
                streak = 1
                best = max(1, int(row["best_streak"] or 0)) if row else 1
            await self.bot.db.execute(
                "INSERT INTO weekly_streaks(guild_id,user_id,streak,best_streak,last_week_start,updated_ts) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(guild_id,user_id) DO UPDATE SET streak=excluded.streak, best_streak=excluded.best_streak, last_week_start=excluded.last_week_start, updated_ts=excluded.updated_ts",
                (guild.id, uid, streak, best, week_start_iso, now_ts),
            )

        if top_users:
            placeholders = ",".join("?" for _ in top_users)
            await self.bot.db.execute(
                f"UPDATE weekly_streaks SET streak=0, last_week_start=?, updated_ts=? WHERE guild_id=? AND user_id NOT IN ({placeholders}) AND streak>0",
                (week_start_iso, now_ts, guild.id, *top_users),
            )
        else:
            await self.bot.db.execute(
                "UPDATE weekly_streaks SET streak=0, last_week_start=?, updated_ts=? WHERE guild_id=? AND streak>0",
                (week_start_iso, now_ts, guild.id),
            )

    async def _send_weekly_recap(self, guild: discord.Guild, week_start_iso: str, ranked_rows) -> None:
        recap_cfg = self.bot.config.get("background", "weekly_recap", default={}) or {}
        if isinstance(recap_cfg, dict) and not bool(recap_cfg.get("enabled", True)):
            return
        existing = await self.bot.db.fetchone(
            "SELECT 1 FROM weekly_recaps WHERE guild_id=? AND week_start=?",
            (guild.id, week_start_iso),
        )
        if existing:
            return

        channel_id = 0
        if isinstance(recap_cfg, dict):
            try:
                channel_id = int(recap_cfg.get("channel_id") or 0)
            except Exception:
                channel_id = 0
        if not channel_id:
            try:
                channel_id = int(self.bot.config.get("background", "daily_summary", "channel_id", default=0) or 0)
            except Exception:
                channel_id = 0
        if not channel_id:
            channel_id = self._cfg_int("channels", "general_logging_channel_id", 0)
        channel = guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            return

        try:
            week_start_ts = int(datetime.fromisoformat(week_start_iso).replace(tzinfo=TZ).timestamp())
        except Exception:
            week_start_ts = 0
        active_row = await self.bot.db.fetchone(
            "SELECT COUNT(*) AS c, COALESCE(SUM(count), 0) AS total FROM activity_counts WHERE guild_id=? AND week_start=? AND count>0",
            (guild.id, week_start_iso),
        )
        active_count = int(active_row["c"] or 0) if active_row else 0
        total_messages = int(active_row["total"] or 0) if active_row else sum(int(row["count"]) for row in ranked_rows)
        claim_rows = await self.bot.db.fetchall(
            "SELECT status, COUNT(*) AS c FROM weekly_claims WHERE guild_id=? AND week_start=? GROUP BY status",
            (guild.id, week_start_iso),
        )
        claim_counts = {str(row["status"]): int(row["c"]) for row in claim_rows}
        review_rows = await self.bot.db.fetchall(
            "SELECT status, result, COUNT(*) AS c FROM weekly_request_reviews WHERE guild_id=? AND week_start=? GROUP BY status, result",
            (guild.id, week_start_iso),
        )
        reviewed = sum(int(row["c"]) for row in review_rows if str(row["status"]) == "reviewed")
        pending_reviews = sum(int(row["c"]) for row in review_rows if str(row["status"]) == "pending")
        farm_row = await self.bot.db.fetchone(
            "SELECT COUNT(*) AS c FROM anti_farm_events WHERE guild_id=? AND ts>=?",
            (guild.id, week_start_ts),
        )
        farm_count = int(farm_row["c"] or 0) if farm_row else 0

        streak_rows = await self.bot.db.fetchall(
            "SELECT user_id, streak FROM weekly_streaks WHERE guild_id=? AND streak>1",
            (guild.id,),
        )
        streaks = {int(row["user_id"]): int(row["streak"]) for row in streak_rows}
        emoji = str(self.bot.config.get("tracking", "streak_emoji", default="🔥") or "🔥")
        top_lines = []
        for idx, row in enumerate(ranked_rows[:10], start=1):
            uid = int(row["user_id"])
            streak = streaks.get(uid, 0)
            streak_text = f" {emoji}{streak}" if streak > 1 else ""
            top_lines.append(f"**#{idx}** <@{uid}> - **{int(row['count'])}** messages{streak_text}")

        embed = discord.Embed(
            title="Weekly Server Recap",
            description=f"Week starting **{week_start_iso}**",
            color=discord.Color.blurple(),
            timestamp=now_madrid(),
        )
        embed.add_field(name="Activity", value=f"Active members: **{active_count}**\nMessages counted: **{total_messages}**", inline=True)
        embed.add_field(
            name="Weekly Requests",
            value=(
                f"Contacted: **{sum(claim_counts.values())}**\n"
                f"Claimed: **{claim_counts.get('claimed', 0)}**\n"
                f"Pending: **{claim_counts.get('pending', 0)}**\n"
                f"Declined/timed out: **{claim_counts.get('declined', 0) + claim_counts.get('timed_out', 0)}**"
            ),
            inline=True,
        )
        embed.add_field(name="Review Queue", value=f"Reviewed: **{reviewed}**\nPending: **{pending_reviews}**", inline=True)
        embed.add_field(name="Top Members", value="\n".join(top_lines)[:1024] or "No eligible activity.", inline=False)
        embed.add_field(name="Signals", value=f"Anti-farm skips logged: **{farm_count}**", inline=True)
        embed.set_footer(text="Private weekly recap")
        try:
            msg = await channel.send(embed=embed)
            await self.bot.db.execute(
                "INSERT OR REPLACE INTO weekly_recaps(guild_id,week_start,message_id,channel_id,created_ts) VALUES(?,?,?,?,?)",
                (guild.id, week_start_iso, msg.id, channel.id, int(time.time())),
            )
        except Exception as e:
            await log_error(self.bot, f"Weekly recap send failed for {week_start_iso}: {repr(e)}")

    # ----------------------------
    # Weekly job execution
    # ----------------------------
    async def run_weekly_job(self, week_start_iso: str):
        allowed_guild_id = self._cfg_int("guild", "allowed_guild_id", 0)
        guild = self.bot.get_guild(allowed_guild_id) if allowed_guild_id else None
        if guild is None:
            return
        await self.flush_activity_counts()

        top_limit = self._cfg_int("tracking", "top_limit", 20)
        winners_to_dm = self._cfg_int("tracking", "winners_to_dm", 1)
        timeout_h = self._cfg_int("tracking", "dm_timeout_hours", 48)

        excluded_role_ids = set(self._cfg_int_list("roles", "excluded_tracking_role_id"))
        await self._log_weekly(guild, week_start_iso, 0, "weekly_job_start", f"top_limit={top_limit} winners_to_dm={winners_to_dm} timeout_h={timeout_h}")

        rows = await self.bot.db.fetchall(
            "SELECT user_id, count FROM activity_counts WHERE guild_id=? AND week_start=? ORDER BY count DESC LIMIT ?",
            (guild.id, week_start_iso, top_limit),
        )

        ranked: List[int] = []
        ranked_rows = []
        for r in rows:
            uid = int(r["user_id"])
            member = await self._resolve_member(guild, uid)
            if member is None or member.bot:
                continue
            if excluded_role_ids and any(role.id in excluded_role_ids for role in member.roles):
                continue
            ranked.append(uid)
            ranked_rows.append(r)

        try:
            await self._update_weekly_streaks(guild, week_start_iso, ranked)
        except Exception as e:
            await self._log_background_error("weekly_streaks", f"Weekly streak update failed: {repr(e)}")

        if await self.weekly_reward_disabled(guild.id, week_start_iso):
            await self._log_weekly(guild, week_start_iso, 0, "weekly_reward_skipped", "Reward disabled for this tracking week")
            try:
                await self._send_weekly_recap(guild, week_start_iso, ranked_rows)
            except Exception as e:
                await self._log_background_error("weekly_recap", f"Weekly recap failed: {repr(e)}")
            await self._log_weekly(guild, week_start_iso, 0, "weekly_job_done", f"contacted=0 eligible_ranked={len(ranked)}")
            return

        contacted = 0
        for idx, uid in enumerate(ranked, start=1):
            if contacted >= winners_to_dm:
                break
            ok = await self._contact_user_for_week(guild, week_start_iso, uid, rank=idx, timeout_hours=timeout_h)
            if ok:
                contacted += 1

        await self._log_weekly(guild, week_start_iso, 0, "weekly_job_done", f"contacted={contacted} eligible_ranked={len(ranked)}")
        try:
            await self._send_weekly_recap(guild, week_start_iso, ranked_rows)
        except Exception as e:
            await self._log_background_error("weekly_recap", f"Weekly recap failed: {repr(e)}")

    async def _contact_user_for_week(self, guild: discord.Guild, week_start_iso: str, user_id: int, rank: int, timeout_hours: int, force: bool = False) -> bool:
        if not force and await self.weekly_reward_disabled(guild.id, week_start_iso):
            await self._log_weekly(guild, week_start_iso, user_id, "skipped_reward_disabled", "")
            return False

        # don't contact if already contacted this week
        row = await self.bot.db.fetchone(
            "SELECT status FROM weekly_claims WHERE guild_id=? AND week_start=? AND user_id=?",
            (guild.id, week_start_iso, user_id),
        )
        if row is not None:
            await self._log_weekly(guild, week_start_iso, user_id, "skipped_already_contacted", f"status={row['status']}")
            return False

        now_ts = int(time.time())
        expires = now_ts + int(timeout_hours) * 3600

        try:
            user = await self.bot.fetch_user(user_id)
            content, embed = self._build_request_dm_message(int(timeout_hours), expires)
            await user.send(content=content or None, embed=embed)
            await self._log_weekly(guild, week_start_iso, user_id, "dm_sent", f"rank={rank} timeout_hours={timeout_hours}")
        except Exception as e:
            await self.bot.db.execute(
                "INSERT INTO weekly_claims(guild_id,week_start,user_id,rank,status,contacted_ts) VALUES(?,?,?,?,?,?)",
                (guild.id, week_start_iso, user_id, rank, "dm_closed", now_ts),
            )
            await self._log_weekly(guild, week_start_iso, user_id, "dm_failed", type(e).__name__)

            log_ch_id = self._cfg_int("channels", "dm_fail_log_channel_id", 0)
            log_ch = guild.get_channel(log_ch_id) if log_ch_id else None
            if isinstance(log_ch, discord.TextChannel):
                embed = discord.Embed(
                    title="Weekly Request DM Failed",
                    description=f"A weekly request DM could not be delivered to <@{user_id}>",
                    color=discord.Color.red(),
                    timestamp=now_madrid(),
                )
                embed.add_field(name="Member", value=f"<@{user_id}>\n`{user_id}`", inline=True)
                embed.add_field(name="Week", value=week_start_iso, inline=True)
                embed.add_field(name="Reason", value=type(e).__name__, inline=True)
                embed.set_footer(text="Weekly request workflow")
                try:
                    await log_ch.send(embed=embed)
                except Exception:
                    pass
            return False

        await self.bot.db.execute(
            "INSERT INTO weekly_claims(guild_id,week_start,user_id,rank,status,contacted_ts) VALUES(?,?,?,?,?,?)",
            (guild.id, week_start_iso, user_id, rank, "pending", now_ts),
        )
        await self.bot.db.execute(
            "INSERT INTO weekly_sessions(guild_id,week_start,user_id,stage,expires_ts,active) VALUES(?,?,?,?,?,1) "
            "ON CONFLICT(guild_id,week_start,user_id) DO UPDATE SET stage='awaiting_request', expires_ts=excluded.expires_ts, active=1",
            (guild.id, week_start_iso, user_id, "awaiting_request", expires),
        )
        return True

    async def _contact_next_eligible(self, guild: discord.Guild, week_start_iso: str):
        if await self.weekly_reward_disabled(guild.id, week_start_iso):
            await self._log_weekly(guild, week_start_iso, 0, "next_offer_skipped_reward_disabled", "")
            return

        cfg_top_limit = self._cfg_int("tracking", "top_limit", 20)
        timeout_h = self._cfg_int("tracking", "dm_timeout_hours", 48)
        excluded_role_ids = set(self._cfg_int_list("roles", "excluded_tracking_role_id"))

        rows = await self.bot.db.fetchall(
            "SELECT user_id, count FROM activity_counts WHERE guild_id=? AND week_start=? ORDER BY count DESC LIMIT ?",
            (guild.id, week_start_iso, cfg_top_limit),
        )

        skipped_missing = 0
        skipped_excluded = 0
        skipped_existing = 0

        for idx, r in enumerate(rows, start=1):
            uid = int(r["user_id"])
            member = await self._resolve_member(guild, uid)
            if member is None or member.bot:
                skipped_missing += 1
                continue
            if excluded_role_ids and any(role.id in excluded_role_ids for role in member.roles):
                skipped_excluded += 1
                continue

            existing = await self.bot.db.fetchone(
                "SELECT 1 FROM weekly_claims WHERE guild_id=? AND week_start=? AND user_id=?",
                (guild.id, week_start_iso, uid),
            )
            if existing:
                skipped_existing += 1
                continue

            await self._log_weekly(
                guild,
                week_start_iso,
                uid,
                "offered_next",
                f"rank={idx} skipped_missing={skipped_missing} skipped_excluded={skipped_excluded} skipped_existing={skipped_existing}",
            )
            await self._contact_user_for_week(guild, week_start_iso, uid, rank=idx, timeout_hours=timeout_h)
            return

        await self._log_weekly(
            guild,
            week_start_iso,
            0,
            "no_eligible_member",
            f"top_limit={cfg_top_limit} skipped_missing={skipped_missing} skipped_excluded={skipped_excluded} skipped_existing={skipped_existing}",
        )

    # ----------------------------
    # Reminder messages
    # ----------------------------
    def _format_deadline(self, expires_ts: int) -> str:
        return f"<t:{int(expires_ts)}:F>"

    def _build_request_dm_text(self, timeout_hours: int, expires_ts: int) -> str:
        deadline = self._format_deadline(expires_ts)
        return f"{REQUEST_DM_TEXT}\n\nClaim window: **{timeout_hours} hours** (until **{deadline}**).\n"

    def _build_request_dm_message(self, timeout_hours: int, expires_ts: int) -> tuple[str, Optional[discord.Embed]]:
        text = self._build_request_dm_text(timeout_hours, expires_ts)
        cfg = self.bot.config.get("level_requests", "weekly_request_dm_embed", default={}) or {}
        if not isinstance(cfg, dict) or not bool(cfg.get("enabled", False)):
            return text, None

        deadline = self._format_deadline(expires_ts)
        variables = {
            "request_text": text,
            "timeout_hours": timeout_hours,
            "deadline": deadline,
            "expires_ts": expires_ts,
        }
        embed = self._embed_from_template(cfg, variables, "Weekly Request Earned", "gold")
        return "", embed

    def _build_reminder_text(self, expires_ts: int) -> str:
        deadline = self._format_deadline(expires_ts)
        return f"Reminder: you still have an unclaimed weekly request. Please reply with the request format.\nDeadline: **{deadline}**."

    def _build_reminder_message(self, expires_ts: int) -> tuple[str, Optional[discord.Embed]]:
        text = self._build_reminder_text(expires_ts)
        cfg = self.bot.config.get("level_requests", "weekly_request_reminder_embed", default={}) or {}
        if not isinstance(cfg, dict) or not bool(cfg.get("enabled", False)):
            return text, None

        deadline = self._format_deadline(expires_ts)
        variables = {
            "reminder_text": text,
            "deadline": deadline,
            "expires_ts": expires_ts,
        }
        embed = self._embed_from_template(cfg, variables, "Weekly Request Reminder", "gold")
        return "", embed

    async def _process_reminders(self):
        allowed_guild_id = self._cfg_int("guild", "allowed_guild_id", 0)
        guild = self.bot.get_guild(allowed_guild_id) if allowed_guild_id else None
        if guild is None:
            return

        reminder_after_h = self._cfg_int("tracking", "reminder_after_hours", 24)
        repeat_h = self._cfg_int("tracking", "reminder_repeat_hours", 0)

        now_ts = int(time.time())
        rows = await self.bot.db.fetchall(
            "SELECT s.week_start AS week_start, s.user_id AS user_id, s.expires_ts AS expires_ts, c.contacted_ts AS contacted_ts "
            "FROM weekly_sessions s "
            "JOIN weekly_claims c ON c.guild_id=s.guild_id AND c.week_start=s.week_start AND c.user_id=s.user_id "
            "WHERE s.guild_id=? AND s.active=1 AND s.stage='awaiting_request' AND c.status='pending'",
            (guild.id,),
        )

        for r in rows:
            week_start_iso = r["week_start"]
            user_id = int(r["user_id"])
            expires_ts = int(r["expires_ts"])
            contacted_ts = int(r["contacted_ts"])

            if expires_ts <= now_ts:
                continue
            if now_ts < contacted_ts + reminder_after_h * 3600:
                continue

            prev = await self.bot.db.fetchone(
                "SELECT reminded_ts FROM weekly_reminders WHERE guild_id=? AND week_start=? AND user_id=?",
                (guild.id, week_start_iso, user_id),
            )
            if prev is not None:
                if repeat_h <= 0:
                    continue
                if now_ts < int(prev["reminded_ts"]) + repeat_h * 3600:
                    continue

            try:
                user = await self.bot.fetch_user(user_id)
                content, embed = self._build_reminder_message(expires_ts)
                await user.send(content=content or None, embed=embed)
                await self.bot.db.execute(
                    "INSERT OR REPLACE INTO weekly_reminders(guild_id,week_start,user_id,reminded_ts) VALUES(?,?,?,?)",
                    (guild.id, week_start_iso, user_id, now_ts),
                )
                await self._log_weekly(guild, week_start_iso, user_id, "reminder_sent", f"expires={self._format_deadline(expires_ts)}")
            except Exception as e:
                await self._log_weekly(guild, week_start_iso, user_id, "reminder_failed", type(e).__name__)

    # ----------------------------
    # Public helpers used by Commands.py
    # ----------------------------
    async def get_top(self, guild_id: int, week_start_iso: str, limit: int = 20) -> List[Tuple[int, int]]:
        await self.flush_activity_counts()
        rows = await self.bot.db.fetchall(
            "SELECT user_id, count FROM activity_counts WHERE guild_id=? AND week_start=? ORDER BY count DESC LIMIT ?",
            (guild_id, week_start_iso, limit),
        )
        return [(int(r["user_id"]), int(r["count"])) for r in rows]

    async def get_member_stats(self, guild: discord.Guild, week_start_iso: str, user_id: int) -> tuple[int, Optional[int], int]:
        """Return (count, rank among eligible, eligible_total). Rank is 1-based, or None if not ranked/eligible."""
        await self.flush_activity_counts()
        excluded_role_ids = set(self._cfg_int_list("roles", "excluded_tracking_role_id"))

        row = await self.bot.db.fetchone(
            "SELECT count FROM activity_counts WHERE guild_id=? AND week_start=? AND user_id=?",
            (guild.id, week_start_iso, user_id),
        )
        count = int(row["count"]) if row else 0

        member = await self._resolve_member(guild, user_id)
        if member is None or member.bot:
            return count, None, 0
        if excluded_role_ids and any(r.id in excluded_role_ids for r in member.roles):
            return count, None, 0

        rows = await self.bot.db.fetchall(
            "SELECT user_id, count FROM activity_counts WHERE guild_id=? AND week_start=? ORDER BY count DESC",
            (guild.id, week_start_iso),
        )

        rank = None
        eligible_total = 0
        for r in rows:
            uid = int(r["user_id"])
            m = await self._resolve_member(guild, uid)
            if m is None or m.bot:
                continue
            if excluded_role_ids and any(role.id in excluded_role_ids for role in m.roles):
                continue
            eligible_total += 1
            if uid == user_id:
                rank = eligible_total
                break

        return count, rank, eligible_total

    async def force_dm_for_user(self, guild: discord.Guild, week_start_iso: str, user_id: int, timeout_hours: Optional[int] = None) -> tuple[bool, str]:
        await self.flush_activity_counts()
        timeout_h = int(timeout_hours or self._cfg_int("tracking", "dm_timeout_hours", 48) or 48)

        if await self.weekly_reward_disabled(guild.id, week_start_iso):
            await self._log_weekly(guild, week_start_iso, user_id, "force_dm_override", "reason=weekly_reward_disabled")

        member = await self._resolve_member(guild, user_id)
        if member is None:
            await self._log_weekly(guild, week_start_iso, user_id, "force_dm_failed", "reason=user_not_in_server")
            return False, "User is not in the server."
        if member.bot:
            await self._log_weekly(guild, week_start_iso, user_id, "force_dm_failed", "reason=bot_user")
            return False, "Bots cannot receive weekly requests."

        existing = await self.bot.db.fetchone(
            "SELECT status FROM weekly_claims WHERE guild_id=? AND week_start=? AND user_id=?",
            (guild.id, week_start_iso, user_id),
        )
        if existing is not None:
            status = str(existing["status"])
            if status not in {"dm_closed", "disabled"}:
                await self._log_weekly(guild, week_start_iso, user_id, "force_dm_blocked", f"reason=existing_status status={status}")
                return False, f"Cannot force DM: user already has status '{status}' for this week."
            await self.bot.db.execute(
                "DELETE FROM weekly_claims WHERE guild_id=? AND week_start=? AND user_id=?",
                (guild.id, week_start_iso, user_id),
            )
            await self.bot.db.execute(
                "DELETE FROM weekly_sessions WHERE guild_id=? AND week_start=? AND user_id=?",
                (guild.id, week_start_iso, user_id),
            )

        # Estimate rank among eligible (best-effort)
        excluded_role_ids = set(self._cfg_int_list("roles", "excluded_tracking_role_id"))
        rows = await self.bot.db.fetchall(
            "SELECT user_id, count FROM activity_counts WHERE guild_id=? AND week_start=? ORDER BY count DESC",
            (guild.id, week_start_iso),
        )
        rank = 1
        for r in rows:
            uid = int(r["user_id"])
            m = await self._resolve_member(guild, uid)
            if m is None or m.bot:
                continue
            if excluded_role_ids and any(role.id in excluded_role_ids for role in m.roles):
                continue
            if uid == user_id:
                break
            rank += 1

        ok = await self._contact_user_for_week(guild, week_start_iso, user_id, rank=rank, timeout_hours=timeout_h, force=True)
        if ok:
            await self._log_weekly(guild, week_start_iso, user_id, "force_dm_sent", f"rank={rank} timeout_hours={timeout_h}")
            return True, f"Weekly request DM sent (rank {rank})."
        await self._log_weekly(guild, week_start_iso, user_id, "force_dm_failed", f"reason=dm_send_failed rank={rank}")
        return False, "Could not DM that user (DMs likely closed)."

    async def reset_current_week(self, guild_id: int) -> None:
        await self.flush_activity_counts()
        ws = week_start_sunday(now_madrid()).isoformat()
        await self.bot.db.execute("DELETE FROM activity_counts WHERE guild_id=? AND week_start=?", (guild_id, ws))
        await self.bot.db.execute("DELETE FROM activity_last_counted WHERE guild_id=?", (guild_id,))
        async with self._activity_lock:
            self._pending_activity_counts = {
                key: value for key, value in self._pending_activity_counts.items()
                if not (key[0] == guild_id and key[2] == ws)
            }
            self._pending_last_counted = {
                key: value for key, value in self._pending_last_counted.items()
                if key[0] != guild_id
            }
        self._last_counted_cache = {key: value for key, value in self._last_counted_cache.items() if key[0] != guild_id}


def setup(bot: discord.Bot):
    bot.add_cog(TrackingCog(bot))
