import os
import csv
import io
import json
import random
import secrets
import shutil
import asyncio
import sqlite3
import time
import re
import string
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import discord
from discord.ext import commands

from utils.checks import is_admin_or_owner, is_mod
from utils.mentions import no_mentions
from utils.server_icons import (
    ensure_server_icon_config,
    is_valid_icon_url,
    normalize_server_icon_mode,
    parse_server_icon_index,
    server_icon_url_warning,
    VALID_SERVER_ICON_MODES,
)
from utils.runtime_config import (
    load_runtime_config_overrides,
    persist_forum_required_rules,
    persist_server_icon_config,
)
from utils.timeutils import now_madrid, week_start_sunday
from utils.errors import log_error

DEFAULT_REQUEST_REVIEWER_ROLE_IDS = [785212232786640966, 1430214323720163498]
SQLITE_RESTORE_EXTENSIONS = {".sqlite3", ".sqlite", ".db", ".db3"}
SQLITE_ARCHIVE_EXTENSIONS = {".zip"}
SQLITE_RESTORE_MAX_BYTES = 100 * 1024 * 1024
AVENUE_GUARD_CORE_TABLES = {
    "activity_counts",
    "weekly_claims",
    "weekly_dm_log",
    "tickets",
    "ticket_transcripts",
    "help_submissions",
    "level_request_state",
    "level_request_submissions",
    "weekly_request_reviews",
    "daily_stats",
    "impact_snapshots",
    "database_backups",
}

TICKET_STATUS_LABELS = {
    "waiting_user": "Waiting for user",
    "waiting_staff": "Waiting for staff",
    "resolved": "Resolved",
}


def _fmt_num(value) -> str:
    try:
        return f"{int(value or 0):,}"
    except Exception:
        return "0"


def _fmt_percent(numerator, denominator) -> str:
    try:
        total = int(denominator or 0)
        if total <= 0:
            return "0%"
        return f"{(int(numerator or 0) / total) * 100:.1f}%"
    except Exception:
        return "0%"


def _ticket_status_key(value) -> str:
    text = re.sub(r"[_\-/]+", " ", str(value or "").strip().casefold())
    text = re.sub(r"\s+", " ", text).strip()
    if text in {"waiting user", "waiting for user", "user", "wfu"}:
        return "waiting_user"
    if text in {"waiting staff", "waiting for staff", "staff", "wfs"}:
        return "waiting_staff"
    if text in {"resolved", "resolve", "done", "closed"}:
        return "resolved"
    return ""


def _ticket_status_label(value) -> str:
    return TICKET_STATUS_LABELS.get(_ticket_status_key(value), "Waiting for staff")


class AdminDashboardView(discord.ui.View):
    def __init__(self, cog, user_id: int, page: str = "overview"):
        super().__init__(timeout=600)
        self.cog = cog
        self.user_id = int(user_id)
        self.page = page

    async def _show(self, interaction: discord.Interaction, page: str):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This dashboard belongs to another admin.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message("Wrong server.", ephemeral=True)
            return

        await interaction.response.defer()
        member = await self.cog._resolve_member(interaction.guild, interaction.user)
        admin_roles = self.cog.bot.config.get_int_list("roles", "admin_owner_role_ids")
        if member is None or not is_admin_or_owner(member, admin_roles):
            await interaction.followup.send("You don't have permission to use this.", ephemeral=True)
            return
        self.page = page
        embed = await self.cog._admin_dashboard_embed(interaction.guild, page)
        await interaction.message.edit(embed=embed, view=self)

    @discord.ui.button(label="Overview", style=discord.ButtonStyle.primary)
    async def overview(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._show(interaction, "overview")

    @discord.ui.button(label="Config", style=discord.ButtonStyle.secondary)
    async def config(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._show(interaction, "config")

    @discord.ui.button(label="Repair Tips", style=discord.ButtonStyle.secondary)
    async def repairs(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._show(interaction, "repairs")

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.success)
    async def refresh(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._show(interaction, self.page)


class CommandsCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        cfg = bot.config
        self.allowed_guild_id = cfg.get_int("guild", "allowed_guild_id") or 0
        # Hardcoded anti-spam cooldowns
        self._gamble_last_ts: dict[int, float] = {}  # user_id -> last /gambling time
        self._rps_last_ts: dict[int, float] = {}  # user_id -> last /rock-paper-scissors time
        self._server_icon_config_lock = asyncio.Lock()
        self._forum_config_lock = asyncio.Lock()

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
        self.bot_group.command(name="dashboard", description="Open the admin system dashboard")(self.bot_dashboard)
        self.bot_group.command(name="impact", description="Generate a persistent community impact report")(self.bot_impact)
        self.bot_group.command(name="backup", description="Create a durable database backup")(self.bot_backup)
        self.bot_group.command(name="restore", description="Restore the database from an uploaded SQLite backup")(self.bot_restore)
        self.bot_group.command(name="storage", description="Show database storage and backup status")(self.bot_storage)

        self.tracking_group.command(name="top", description="Show the current week's top 20 active members")(self.tracking_top)
        self.tracking_group.command(name="reset", description="Reset current week's tracking stats")(self.tracking_reset)
        self.tracking_group.command(name="me", description="Show your activity stats for this week")(self.tracking_me)
        self.tracking_group.command(name="force_dm", description="Force-send the weekly request DM to a user")(self.tracking_force_dm)
        self.tracking_group.command(name="disable_reward", description="Disable this week's automatic weekly request reward")(self.tracking_disable_reward)
        self.tracking_group.command(name="enable_reward", description="Re-enable this week's automatic weekly request reward")(self.tracking_enable_reward)

        self.ticket_group.command(name="close", description="Close the current ticket channel")(self.ticket_close)
        self.ticket_group.command(name="status", description="Set the current ticket status")(self.ticket_status)
        self.ticket_group.command(name="transcripts", description="Search saved ticket transcripts")(self.ticket_transcripts)
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
        response = getattr(getattr(ctx, "interaction", None), "response", None)
        if response is not None and response.is_done():
            return
        # Do not swallow an expired interaction here. Mutating commands must
        # stop instead of completing an operation the user sees as failed.
        await ctx.defer(ephemeral=ephemeral)

    async def _send(self, ctx: discord.ApplicationContext, *args, **kwargs):
        # Pycord routes Interaction.respond to the initial response or the
        # follow-up webhook depending on whether the command was deferred.
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

    def _impact_owner_ids(self) -> list[int]:
        return self.bot.config.get_int_list("impact", "allowed_user_ids")

    async def _is_impact_owner_ctx(self, ctx: discord.ApplicationContext) -> bool:
        if ctx.guild is None:
            return False
        owner_ids = self._impact_owner_ids()
        if owner_ids:
            return int(ctx.user.id) in owner_ids
        return await self._is_admin_ctx(ctx)

    def _backup_channel_id(self) -> int:
        channel_id = self.bot.config.get_int("database", "backups", "channel_id", default=0)
        if not channel_id:
            channel_id = self.bot.config.get_int("impact", "report_channel_id", default=0)
        if not channel_id:
            channel_id = self.bot.config.get_int("channels", "general_logging_channel_id", default=0)
        return int(channel_id or 0)

    def _backup_local_dir(self) -> Path:
        raw = str(self.bot.config.get("database", "backups", "local_dir", default="backups") or "backups").strip()
        return Path(raw or "backups")

    def _restore_upload_dir(self) -> Path:
        path = self._backup_local_dir() / "restore_uploads"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _backup_retention_count(self) -> int:
        try:
            value = int(self.bot.config.get("database", "backups", "local_retention_count", default=10) or 10)
        except Exception:
            value = 10
        return max(1, min(100, value))

    def _prune_local_backups(self) -> None:
        backup_dir = self._backup_local_dir()
        if not backup_dir.exists():
            return
        files = sorted(
            backup_dir.glob("avenue-guard-db-*.sqlite3.zip"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for old_path in files[self._backup_retention_count():]:
            try:
                old_path.unlink()
            except FileNotFoundError:
                pass

    def _database_path(self) -> Path:
        return Path(str(getattr(self.bot, "db_path", getattr(self.bot.db, "path", "data/bot.db"))))

    def _database_storage_note(self) -> tuple[str, bool]:
        path = self._database_path()
        text = str(path)
        remote_url = str(getattr(getattr(self.bot, "db", None), "remote_url", "") or getattr(self.bot, "db_remote_url", "") or "").strip()
        if remote_url:
            parsed = urlparse(remote_url)
            host = parsed.netloc or remote_url.replace("libsql://", "").split("/", 1)[0]
            return f"Turso/libSQL remote persistence is active on `{host}` with local replica `{text}`.", True

        env_path = os.getenv("AVENUE_GUARD_DB_PATH", "").strip()
        source = str(getattr(self.bot, "db_path_source", "") or "")
        if not source:
            if env_path:
                source = "environment variable"
            elif str(self.bot.config.get("database", "path", default="") or "").strip():
                source = "config.json"
            else:
                source = "default"
        startup_warning = str(getattr(self.bot, "db_path_warning", "") or "").strip()

        lowered = text.casefold()
        likely_ephemeral = (
            lowered.startswith("data/")
            or "/data/" in lowered and "var/data" not in lowered
            # This classifies a configured path; it does not create a temp file.
            or lowered.startswith("/tmp")  # nosec B108
            or lowered.startswith("/private/tmp")
            or "/opt/render/project/src" in lowered
        )
        likely_persistent = lowered.startswith("/var/data") or bool(env_path and not likely_ephemeral)
        suffix = f"\nWarning: {startup_warning}" if startup_warning else ""
        if likely_persistent:
            return f"`{text}` from {source} looks persistent.{suffix}", True
        if likely_ephemeral:
            return f"`{text}` from {source} may be wiped by Render cache clears.{suffix}", False
        return f"`{text}` from {source}; confirm this path is on persistent storage.{suffix}", False

    def _zip_backup_file(self, source_path: Path, slug: str) -> Path:
        backup_dir = self._backup_local_dir()
        backup_dir.mkdir(parents=True, exist_ok=True)
        zip_path = backup_dir / f"avenue-guard-db-{slug}.sqlite3.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(source_path, arcname=f"avenue-guard-db-{slug}.sqlite3")
        return zip_path

    async def _post_database_backup(self, guild: discord.Guild, reason: str = "manual", requested_by: int = 0) -> discord.Message | None:
        backup_dir = self._backup_local_dir()
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        slug = now_madrid().strftime("%Y%m%d-%H%M%S")
        raw_path = backup_dir / f"avenue-guard-db-{slug}.sqlite3"
        try:
            size_bytes = await self.bot.db.backup_to(raw_path)
            await self._validate_restore_database(raw_path)
            zip_path = self._zip_backup_file(raw_path, slug)
        finally:
            try:
                raw_path.unlink()
            except FileNotFoundError:
                pass
        self._prune_local_backups()
        channel_id = self._backup_channel_id()
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None and channel_id:
            try:
                channel = await guild.fetch_channel(channel_id)
            except Exception:
                channel = None
        if not isinstance(channel, discord.TextChannel):
            await log_error(self.bot, f"Database backup created locally but backup channel is missing: {zip_path}")
            return None

        zipped_size = int(zip_path.stat().st_size)
        storage_note, storage_ok = self._database_storage_note()
        embed = discord.Embed(
            title="Database Backup",
            description="A zipped copy of the bot database is attached.",
            color=discord.Color.green() if storage_ok else discord.Color.gold(),
            timestamp=now_madrid(),
        )
        embed.add_field(name="Reason", value=str(reason).replace("_", " ").title(), inline=True)
        embed.add_field(name="Raw Size", value=f"{_fmt_num(size_bytes)} bytes", inline=True)
        embed.add_field(name="Zip Size", value=f"{_fmt_num(zipped_size)} bytes", inline=True)
        embed.add_field(name="Storage", value=storage_note[:1024], inline=False)
        if requested_by:
            embed.add_field(name="Requested By", value=f"<@{int(requested_by)}>\n`{int(requested_by)}`", inline=True)

        if zipped_size > 24 * 1024 * 1024:
            await channel.send(
                "Database backup was created locally, but the compressed file is too large for a Discord attachment.",
                embed=embed,
                allowed_mentions=no_mentions(),
            )
            await log_error(self.bot, f"Database backup too large for Discord attachment: {zip_path} ({zipped_size} bytes)")
            return None

        sent = await channel.send(
            embed=embed,
            file=discord.File(str(zip_path), filename=zip_path.name),
            allowed_mentions=no_mentions(),
        )
        try:
            await self.bot.db.execute(
                "INSERT OR REPLACE INTO database_backups(guild_id,backup_ts,channel_id,message_id,size_bytes,reason,requested_by,filename) VALUES(?,?,?,?,?,?,?,?)",
                (
                    int(guild.id),
                    ts,
                    int(channel.id),
                    int(sent.id),
                    int(zipped_size),
                    str(reason),
                    int(requested_by or 0),
                    str(zip_path.name),
                ),
            )
        except Exception as e:
            # The durable Discord copy already exists. Do not make the
            # scheduler post a duplicate merely because its audit row failed.
            await log_error(self.bot, f"Database backup posted but audit row failed: {repr(e)}")
        return sent

    def _restore_safe_filename(self, filename: str) -> str:
        name = Path(str(filename or "uploaded.sqlite3")).name
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-")
        return safe or "uploaded.sqlite3"

    async def _save_restore_attachment(self, attachment: discord.Attachment) -> tuple[Path, str]:
        size = int(getattr(attachment, "size", 0) or 0)
        if size <= 0:
            raise ValueError("The uploaded backup is empty.")
        if size > SQLITE_RESTORE_MAX_BYTES:
            raise ValueError(f"The uploaded backup is too large. Maximum supported size is {SQLITE_RESTORE_MAX_BYTES // (1024 * 1024)} MB.")

        original_name = self._restore_safe_filename(str(getattr(attachment, "filename", "uploaded.sqlite3") or "uploaded.sqlite3"))
        suffix = Path(original_name).suffix.casefold()
        if suffix not in SQLITE_RESTORE_EXTENSIONS | SQLITE_ARCHIVE_EXTENSIONS:
            raise ValueError("Upload a `.sqlite3`, `.sqlite`, `.db`, `.db3`, or `.zip` backup file.")

        upload_dir = self._restore_upload_dir()
        slug = f"{int(time.time())}-{secrets.token_hex(4)}"
        uploaded_path = upload_dir / f"{slug}-{original_name}"
        await attachment.save(str(uploaded_path))
        return uploaded_path, original_name

    def _extract_sqlite_restore_file(self, uploaded_path: Path) -> Path:
        suffix = uploaded_path.suffix.casefold()
        if suffix in SQLITE_RESTORE_EXTENSIONS:
            return uploaded_path
        if suffix not in SQLITE_ARCHIVE_EXTENSIONS:
            raise ValueError("Unsupported backup file type.")

        with zipfile.ZipFile(uploaded_path, "r") as zf:
            members = [info for info in zf.infolist() if not info.is_dir()]
            sqlite_members = []
            for info in members:
                member_path = Path(info.filename)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise ValueError("The zip archive contains an unsafe file path.")
                if member_path.suffix.casefold() in SQLITE_RESTORE_EXTENSIONS:
                    sqlite_members.append(info)
            if len(sqlite_members) != 1:
                raise ValueError("The zip archive must contain exactly one SQLite database file.")

            selected = sqlite_members[0]
            if int(selected.file_size or 0) > SQLITE_RESTORE_MAX_BYTES:
                raise ValueError("The SQLite file inside the zip is too large.")
            extracted_path = uploaded_path.with_suffix("").with_name(f"{uploaded_path.stem}-extracted{Path(selected.filename).suffix}")
            with zf.open(selected, "r") as src, extracted_path.open("wb") as dest:
                shutil.copyfileobj(src, dest)
        return extracted_path

    async def _validate_restore_database(self, db_path: Path) -> dict:
        def _run() -> dict:
            if not db_path.exists() or not db_path.is_file():
                raise ValueError("The uploaded database file could not be found after upload.")
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
                integrity_row = conn.execute("PRAGMA integrity_check;").fetchone()
                integrity = str(integrity_row[0] if integrity_row else "")
                if integrity.casefold() != "ok":
                    raise ValueError(f"SQLite integrity check failed: {integrity}")
                table_rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                tables = {str(row[0]) for row in table_rows}
                known_tables = sorted(tables & AVENUE_GUARD_CORE_TABLES)
                if not known_tables:
                    raise ValueError("This SQLite file does not look like an Avenue Guard database.")
            return {
                "size_bytes": int(db_path.stat().st_size),
                "tables_count": len(tables),
                "known_tables": known_tables,
            }

        return await asyncio.to_thread(_run)

    async def _impact_scalar(self, sql: str, params: tuple = ()) -> int:
        row = await self.bot.db.fetchone(sql, params)
        if not row:
            return 0
        try:
            return int(row["value"] or 0)
        except Exception:
            try:
                return int(row[0] or 0)
            except Exception:
                return 0

    async def _impact_float(self, sql: str, params: tuple = ()) -> float:
        row = await self.bot.db.fetchone(sql, params)
        if not row:
            return 0.0
        try:
            return round(float(row["value"] or 0), 2)
        except Exception:
            try:
                return round(float(row[0] or 0), 2)
            except Exception:
                return 0.0

    async def _impact_group_counts(self, table: str, column: str, guild_id: int, extra_where: str = "") -> dict[str, int]:
        # Map semantic keys to complete statements. Identifiers cannot be SQL
        # parameters, so accepting arbitrary table/column strings here would
        # make a harmless internal helper too easy to misuse later.
        queries = {
            ("level_request_submissions", "result", "AND status='reviewed'"): (
                "SELECT COALESCE(result, 'unknown') AS key, COUNT(*) AS value FROM level_request_submissions "
                "WHERE guild_id=? AND status='reviewed' GROUP BY COALESCE(result, 'unknown') ORDER BY value DESC"
            ),
            ("weekly_request_reviews", "result", "AND status='reviewed'"): (
                "SELECT COALESCE(result, 'unknown') AS key, COUNT(*) AS value FROM weekly_request_reviews "
                "WHERE guild_id=? AND status='reviewed' GROUP BY COALESCE(result, 'unknown') ORDER BY value DESC"
            ),
            ("weekly_claims", "status", ""): (
                "SELECT COALESCE(status, 'unknown') AS key, COUNT(*) AS value FROM weekly_claims "
                "WHERE guild_id=? GROUP BY COALESCE(status, 'unknown') ORDER BY value DESC"
            ),
            ("tickets", "status_tag", ""): (
                "SELECT COALESCE(status_tag, 'unknown') AS key, COUNT(*) AS value FROM tickets "
                "WHERE guild_id=? GROUP BY COALESCE(status_tag, 'unknown') ORDER BY value DESC"
            ),
            ("help_submissions", "kind", ""): (
                "SELECT COALESCE(kind, 'unknown') AS key, COUNT(*) AS value FROM help_submissions "
                "WHERE guild_id=? GROUP BY COALESCE(kind, 'unknown') ORDER BY value DESC"
            ),
            ("help_submissions", "status", ""): (
                "SELECT COALESCE(status, 'unknown') AS key, COUNT(*) AS value FROM help_submissions "
                "WHERE guild_id=? GROUP BY COALESCE(status, 'unknown') ORDER BY value DESC"
            ),
            ("transcript_requests", "status", ""): (
                "SELECT COALESCE(status, 'unknown') AS key, COUNT(*) AS value FROM transcript_requests "
                "WHERE guild_id=? GROUP BY COALESCE(status, 'unknown') ORDER BY value DESC"
            ),
        }
        sql = queries.get((str(table), str(column), str(extra_where)))
        if sql is None:
            raise ValueError("Unsupported impact grouping")
        rows = await self.bot.db.fetchall(sql, (guild_id,))
        return {str(row["key"] or "unknown"): int(row["value"] or 0) for row in rows}

    async def _impact_daily_totals(self, guild_id: int) -> dict:
        rows = await self.bot.db.fetchall(
            "SELECT day_key, payload_json FROM daily_stats WHERE guild_id=? ORDER BY day_key ASC",
            (guild_id,),
        )
        totals = {
            "days": len(rows),
            "messages": 0,
            "edits": 0,
            "deletes": 0,
            "reactions": 0,
            "joins": 0,
            "leaves": 0,
            "bans": 0,
            "unbans": 0,
            "boosts": 0,
            "unboosts": 0,
            "voice_minutes": 0,
            "commands": 0,
            "command_errors": 0,
            "latest_day": "",
            "top_command": "",
            "top_command_count": 0,
            "series": [],
            "command_totals": {},
            "unique_user_ids": [],
            "calendar_days": 0,
        }
        command_totals: dict[str, int] = {}
        unique_user_ids: set[int] = set()
        for row in rows:
            day_key = str(row["day_key"] or totals["latest_day"])
            totals["latest_day"] = day_key
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except Exception:
                payload = {}
            day_record = {
                "day": day_key,
                "messages": 0,
                "edits": 0,
                "deletes": 0,
                "reactions": 0,
                "joins": 0,
                "leaves": 0,
                "bans": 0,
                "unbans": 0,
                "boosts": 0,
                "unboosts": 0,
                "voice_minutes": 0,
                "peak_voice_users": int(payload.get("peak_voice_users", 0) or 0),
                "peak_online_members": int(payload.get("peak_online_members", 0) or 0),
                "commands": 0,
                "command_errors": 0,
                "active_members": len(payload.get("by_user") or {}),
                "active_channels": len(payload.get("by_channel") or {}),
                "top_command": "",
                "top_command_count": 0,
            }
            for source in (payload.get("by_user") or {}, payload.get("commands_by_user") or {}):
                if not isinstance(source, dict):
                    continue
                for raw_user_id in source:
                    try:
                        user_id = int(raw_user_id)
                    except Exception:
                        continue
                    if user_id > 0:
                        unique_user_ids.add(user_id)
            for key in (
                "messages",
                "edits",
                "deletes",
                "reactions",
                "joins",
                "leaves",
                "bans",
                "unbans",
                "boosts",
                "unboosts",
                "voice_minutes",
                "commands",
                "command_errors",
            ):
                try:
                    value = int(payload.get(key, 0) or 0)
                    totals[key] += value
                    day_record[key] = value
                except Exception:
                    pass
            day_commands: dict[str, int] = {}
            for name, count in (payload.get("commands_by_name") or {}).items():
                try:
                    value = int(count or 0)
                    command_totals[str(name)] = command_totals.get(str(name), 0) + value
                    day_commands[str(name)] = day_commands.get(str(name), 0) + value
                except Exception:
                    continue
            if day_commands:
                name, count = max(day_commands.items(), key=lambda item: item[1])
                day_record["top_command"] = name
                day_record["top_command_count"] = int(count or 0)
            totals["series"].append(day_record)
        totals["unique_user_ids"] = sorted(unique_user_ids)
        valid_days: list[date] = []
        for row in totals["series"]:
            try:
                valid_days.append(date.fromisoformat(str(row.get("day") or "")))
            except Exception:
                continue
        if valid_days:
            totals["calendar_days"] = (max(valid_days) - min(valid_days)).days + 1
        if command_totals:
            name, count = max(command_totals.items(), key=lambda item: item[1])
            totals["top_command"] = name
            totals["top_command_count"] = int(count or 0)
            totals["command_totals"] = dict(sorted(command_totals.items(), key=lambda item: item[1], reverse=True))
        return totals

    def _impact_window_rows(self, series: list[dict], days: int, offset_days: int = 0) -> list[dict]:
        if not series or days <= 0:
            return []
        end_day = now_madrid().date() - timedelta(days=max(0, offset_days))
        start_day = end_day - timedelta(days=days - 1)
        rows: list[dict] = []
        for row in series:
            try:
                row_day = date.fromisoformat(str(row.get("day") or ""))
            except Exception:
                continue
            if start_day <= row_day <= end_day:
                rows.append(row)
        return rows

    def _impact_window_sum(self, series: list[dict], key: str, days: int, offset_days: int = 0) -> int:
        rows = self._impact_window_rows(series, days, offset_days)
        return int(sum(int(row.get(key, 0) or 0) for row in rows))

    def _impact_window_average(self, series: list[dict], key: str, days: int, offset_days: int = 0) -> float:
        window = self._impact_window_rows(series, days, offset_days)
        if not window:
            return 0.0
        return round(sum(float(row.get(key, 0) or 0) for row in window) / len(window), 2)

    def _impact_percent_change(self, current: int | float, previous: int | float) -> float:
        try:
            previous = float(previous or 0)
            current = float(current or 0)
            if previous <= 0:
                return 100.0 if current > 0 else 0.0
            return round(((current - previous) / previous) * 100, 1)
        except Exception:
            return 0.0

    def _impact_forecast(self, series: list[dict], review_backlog: int) -> dict:
        days_recorded = len(series or [])
        coverage_7 = len(self._impact_window_rows(series, 7))
        coverage_14 = len(self._impact_window_rows(series, 14))
        last_7_messages = self._impact_window_sum(series, "messages", 7)
        previous_7_messages = self._impact_window_sum(series, "messages", 7, 7)
        last_7_commands = self._impact_window_sum(series, "commands", 7)
        previous_7_commands = self._impact_window_sum(series, "commands", 7, 7)
        last_30_commands = self._impact_window_sum(series, "commands", 30)
        last_30_errors = self._impact_window_sum(series, "command_errors", 30)
        message_change = self._impact_percent_change(last_7_messages, previous_7_messages)
        command_change = self._impact_percent_change(last_7_commands, previous_7_commands)

        projected_messages = int(max(0, round(last_7_messages + ((last_7_messages - previous_7_messages) * 0.5))))
        if coverage_7 < 5:
            projected_messages = int(round(self._impact_window_average(series, "messages", 7) * 7))
        projected_commands = int(max(0, round(last_7_commands + ((last_7_commands - previous_7_commands) * 0.5))))
        if coverage_7 < 5:
            projected_commands = int(round(self._impact_window_average(series, "commands", 7) * 7))

        if days_recorded < 7 or coverage_7 < 5 or coverage_14 < 10:
            signal = "Limited data"
        elif message_change >= 15 or command_change >= 15:
            signal = "Growing"
        elif message_change <= -15 and command_change <= -10:
            signal = "Declining"
        else:
            signal = "Stable"

        command_error_rate = _fmt_percent(last_30_errors, last_30_commands)
        recommendations: list[str] = []
        if review_backlog >= 10:
            recommendations.append("Review backlog is high; schedule a staff review pass.")
        if last_30_commands and (last_30_errors / max(last_30_commands, 1)) >= 0.05:
            recommendations.append("Command error rate is elevated; check recent error logs.")
        if signal == "Declining":
            recommendations.append("Recent activity is down; compare daily summaries with recent events or request openings.")
        if coverage_7 < 5:
            recommendations.append("Recent data coverage is incomplete; keep the bot online before relying on the forecast.")
        if not recommendations:
            recommendations.append("No urgent trend warning from the tracked data.")

        return {
            "days_recorded": days_recorded,
            "data_coverage_7d": _fmt_percent(coverage_7, 7),
            "data_coverage_14d": _fmt_percent(coverage_14, 14),
            "last_7_messages": int(last_7_messages),
            "previous_7_messages": int(previous_7_messages),
            "message_growth_percent": message_change,
            "projected_next_7_messages": int(projected_messages),
            "last_7_commands": int(last_7_commands),
            "previous_7_commands": int(previous_7_commands),
            "command_growth_percent": command_change,
            "projected_next_7_commands": int(projected_commands),
            "avg_daily_active_members_7d": self._impact_window_average(series, "active_members", 7),
            "avg_daily_active_channels_7d": self._impact_window_average(series, "active_channels", 7),
            "command_error_rate_30d": command_error_rate,
            "review_backlog": int(review_backlog),
            "engagement_signal": signal,
            "recommendations": recommendations,
        }

    async def _collect_impact_metrics(self, guild: discord.Guild, generated_by_id: int) -> dict:
        guild_id = int(guild.id)
        snapshot_ts = int(time.time())
        daily = await self._impact_daily_totals(guild_id)

        unique_rows = await self.bot.db.fetchall(
            """
            SELECT DISTINCT user_id FROM (
                SELECT user_id FROM activity_counts WHERE guild_id=?
                UNION SELECT user_id FROM weekly_claims WHERE guild_id=?
                UNION SELECT user_id FROM weekly_sessions WHERE guild_id=?
                UNION SELECT user_id FROM weekly_dm_log WHERE guild_id=?
                UNION SELECT user_id FROM weekly_request_reviews WHERE guild_id=?
                UNION SELECT creator_id AS user_id FROM tickets WHERE guild_id=?
                UNION SELECT user_id FROM help_submissions WHERE guild_id=?
                UNION SELECT requester_id AS user_id FROM transcript_requests WHERE guild_id=?
                UNION SELECT user_id FROM level_request_submissions WHERE guild_id=?
                UNION SELECT user_id FROM anti_farm_events WHERE guild_id=?
            ) WHERE user_id IS NOT NULL AND user_id>0
            """,
            (guild_id,) * 10,
        )
        unique_user_ids = {
            int(row["user_id"])
            for row in unique_rows
            if row["user_id"] is not None and int(row["user_id"]) > 0
        }
        unique_user_ids.update(int(user_id) for user_id in daily.get("unique_user_ids", []) if int(user_id) > 0)
        unique_touched = len(unique_user_ids)
        activity_messages = await self._impact_scalar(
            "SELECT COALESCE(SUM(count), 0) AS value FROM activity_counts WHERE guild_id=?",
            (guild_id,),
        )
        active_members = await self._impact_scalar(
            "SELECT COUNT(DISTINCT user_id) AS value FROM activity_counts WHERE guild_id=?",
            (guild_id,),
        )
        tracked_weeks = await self._impact_scalar(
            "SELECT COUNT(DISTINCT week_start) AS value FROM activity_counts WHERE guild_id=?",
            (guild_id,),
        )

        live_requests = await self._impact_scalar(
            "SELECT COUNT(*) AS value FROM level_request_submissions WHERE guild_id=?",
            (guild_id,),
        )
        live_reviewed = await self._impact_scalar(
            "SELECT COUNT(*) AS value FROM level_request_submissions WHERE guild_id=? AND status='reviewed'",
            (guild_id,),
        )
        live_pending = await self._impact_scalar(
            "SELECT COUNT(*) AS value FROM level_request_submissions WHERE guild_id=? AND status='pending'",
            (guild_id,),
        )
        live_avg_review_hours = await self._impact_float(
            "SELECT AVG(reviewed_ts - created_ts) / 3600.0 AS value FROM level_request_submissions "
            "WHERE guild_id=? AND status='reviewed' AND reviewed_ts IS NOT NULL AND reviewed_ts>=created_ts",
            (guild_id,),
        )
        live_waves = await self._impact_scalar(
            "SELECT COUNT(DISTINCT wave_id) AS value FROM level_request_submissions WHERE guild_id=?",
            (guild_id,),
        )
        request_edits = await self._impact_scalar(
            "SELECT COUNT(*) AS value FROM level_request_edit_audit WHERE guild_id=?",
            (guild_id,),
        )
        request_level_ids = await self._impact_scalar(
            "SELECT COUNT(DISTINCT level_id) AS value FROM level_request_submissions WHERE guild_id=?",
            (guild_id,),
        )
        wave_summaries = await self._impact_scalar(
            "SELECT COUNT(*) AS value FROM level_request_wave_summaries WHERE guild_id=?",
            (guild_id,),
        )
        pending_openings = await self._impact_scalar(
            "SELECT COUNT(*) AS value FROM level_request_scheduled_openings WHERE guild_id=? AND status='pending'",
            (guild_id,),
        )

        weekly_claims = await self._impact_scalar(
            "SELECT COUNT(*) AS value FROM weekly_claims WHERE guild_id=?",
            (guild_id,),
        )
        weekly_dm_logs = await self._impact_scalar(
            "SELECT COUNT(*) AS value FROM weekly_dm_log WHERE guild_id=?",
            (guild_id,),
        )
        weekly_reviews = await self._impact_scalar(
            "SELECT COUNT(*) AS value FROM weekly_request_reviews WHERE guild_id=?",
            (guild_id,),
        )
        weekly_reviewed = await self._impact_scalar(
            "SELECT COUNT(*) AS value FROM weekly_request_reviews WHERE guild_id=? AND status='reviewed'",
            (guild_id,),
        )
        weekly_pending = await self._impact_scalar(
            "SELECT COUNT(*) AS value FROM weekly_request_reviews WHERE guild_id=? AND status='pending'",
            (guild_id,),
        )
        weekly_avg_review_hours = await self._impact_float(
            "SELECT AVG(reviewed_ts - created_ts) / 3600.0 AS value FROM weekly_request_reviews "
            "WHERE guild_id=? AND status='reviewed' AND reviewed_ts IS NOT NULL AND reviewed_ts>=created_ts",
            (guild_id,),
        )
        best_streak = await self._impact_scalar(
            "SELECT COALESCE(MAX(best_streak), 0) AS value FROM weekly_streaks WHERE guild_id=?",
            (guild_id,),
        )
        streak_members = await self._impact_scalar(
            "SELECT COUNT(*) AS value FROM weekly_streaks WHERE guild_id=? AND best_streak>=2",
            (guild_id,),
        )

        tickets = await self._impact_scalar(
            "SELECT COUNT(*) AS value FROM tickets WHERE guild_id=?",
            (guild_id,),
        )
        tickets_open = await self._impact_scalar(
            "SELECT COUNT(*) AS value FROM tickets WHERE guild_id=? AND status IN ('open','closing_prompted')",
            (guild_id,),
        )
        tickets_closed = await self._impact_scalar(
            "SELECT COUNT(*) AS value FROM tickets WHERE guild_id=? AND (closed_ts IS NOT NULL OR status='closed')",
            (guild_id,),
        )
        avg_ticket_close_hours = await self._impact_float(
            "SELECT AVG(closed_ts - created_ts) / 3600.0 AS value FROM tickets "
            "WHERE guild_id=? AND closed_ts IS NOT NULL AND closed_ts>=created_ts",
            (guild_id,),
        )
        transcripts_saved = await self._impact_scalar(
            "SELECT COUNT(*) AS value FROM ticket_transcripts WHERE guild_id=?",
            (guild_id,),
        )
        satisfaction_row = await self.bot.db.fetchone(
            "SELECT COUNT(satisfaction_score) AS responses, AVG(satisfaction_score) AS average "
            "FROM tickets WHERE guild_id=? AND satisfaction_score IS NOT NULL",
            (guild_id,),
        )
        satisfaction_responses = int(satisfaction_row["responses"] or 0) if satisfaction_row else 0
        try:
            satisfaction_average = round(float(satisfaction_row["average"] or 0), 2) if satisfaction_row else 0
        except Exception:
            satisfaction_average = 0

        help_submissions = await self._impact_scalar(
            "SELECT COUNT(*) AS value FROM help_submissions WHERE guild_id=?",
            (guild_id,),
        )
        transcript_requests = await self._impact_scalar(
            "SELECT COUNT(*) AS value FROM transcript_requests WHERE guild_id=?",
            (guild_id,),
        )
        anti_farm_events = await self._impact_scalar(
            "SELECT COUNT(*) AS value FROM anti_farm_events WHERE guild_id=?",
            (guild_id,),
        )
        daily_recaps = await self._impact_scalar(
            "SELECT COUNT(*) AS value FROM daily_stats WHERE guild_id=?",
            (guild_id,),
        )
        daily_summaries_sent = await self._impact_scalar(
            "SELECT COUNT(*) AS value FROM daily_summary_reports WHERE guild_id=?",
            (guild_id,),
        )
        weekly_recaps = await self._impact_scalar(
            "SELECT COUNT(*) AS value FROM weekly_recaps WHERE guild_id=?",
            (guild_id,),
        )
        database_backups = await self._impact_scalar(
            "SELECT COUNT(*) AS value FROM database_backups WHERE guild_id=?",
            (guild_id,),
        )
        database_restores = await self._impact_scalar(
            "SELECT COUNT(*) AS value FROM database_restore_log WHERE guild_id=?",
            (guild_id,),
        )

        request_state = await self.bot.db.fetchone(
            "SELECT state, wave_id, submitted_count, request_limit, close_ts, request_type FROM level_request_state WHERE guild_id=?",
            (guild_id,),
        )
        state_payload = {
            "state": str(request_state["state"] if request_state else "closed"),
            "wave_id": int(request_state["wave_id"] or 0) if request_state else 0,
            "submitted_count": int(request_state["submitted_count"] or 0) if request_state else 0,
            "request_limit": int(request_state["request_limit"]) if request_state and request_state["request_limit"] is not None else 0,
            "close_ts": int(request_state["close_ts"]) if request_state and request_state["close_ts"] is not None else 0,
            "request_type": str(request_state["request_type"] or "") if request_state else "",
        }

        message_events = max(int(activity_messages), int(daily.get("messages", 0) or 0))
        review_events = int(live_reviewed) + int(weekly_reviewed)
        tracked_event_total = (
            message_events
            + int(daily.get("reactions", 0) or 0)
            + int(daily.get("commands", 0) or 0)
            + int(live_requests)
            + int(weekly_reviews)
            + int(weekly_claims)
            + int(weekly_dm_logs)
            + int(tickets)
            + int(help_submissions)
            + int(transcript_requests)
            + int(transcripts_saved)
            + int(request_edits)
            + int(review_events)
            + int(anti_farm_events)
            + int(database_backups)
            + int(database_restores)
        )

        support_items = int(tickets) + int(help_submissions) + int(transcript_requests)
        level_requests_total = int(live_requests) + int(weekly_reviews)
        current_members = int(getattr(guild, "member_count", 0) or 0)
        cached_members = len(getattr(guild, "members", []) or [])
        forecast = self._impact_forecast(daily.get("series", []), int(live_pending) + int(weekly_pending))

        return {
            "report": {
                "guild_id": guild_id,
                "guild_name": str(guild.name),
                "snapshot_ts": snapshot_ts,
                "snapshot_label": now_madrid().strftime("%Y-%m-%d %H:%M"),
                "generated_by_user_id": int(generated_by_id),
            },
            "headline": {
                "current_members": current_members,
                "unique_members_touched": int(unique_touched),
                "tracked_event_total": int(tracked_event_total),
                "support_items": int(support_items),
                "level_requests_total": int(level_requests_total),
            },
            "community": {
                "current_members": current_members,
                "cached_members": int(cached_members),
                "unique_members_touched": int(unique_touched),
                "tracked_active_members": int(active_members),
                "tracked_weeks": int(tracked_weeks),
            },
            "activity": {
                "tracked_messages": int(message_events),
                "eligible_weekly_messages": int(activity_messages),
                "daily_messages": int(daily.get("messages", 0) or 0),
                "reactions": int(daily.get("reactions", 0) or 0),
                "voice_minutes": int(daily.get("voice_minutes", 0) or 0),
                "commands": int(daily.get("commands", 0) or 0),
                "command_errors": int(daily.get("command_errors", 0) or 0),
                "top_command": str(daily.get("top_command") or ""),
                "top_command_count": int(daily.get("top_command_count", 0) or 0),
            },
            "requests": {
                "current_state": state_payload,
                "live_total": int(live_requests),
                "live_reviewed": int(live_reviewed),
                "live_pending": int(live_pending),
                "live_review_rate": _fmt_percent(live_reviewed, live_requests),
                "live_avg_review_hours": live_avg_review_hours,
                "live_waves": int(live_waves),
                "unique_live_level_ids": int(request_level_ids),
                "wave_summaries": int(wave_summaries),
                "pending_openings": int(pending_openings),
                "edit_audit_entries": int(request_edits),
                "live_results": await self._impact_group_counts("level_request_submissions", "result", guild_id, "AND status='reviewed'"),
                "weekly_total": int(weekly_reviews),
                "weekly_reviewed": int(weekly_reviewed),
                "weekly_pending": int(weekly_pending),
                "weekly_review_rate": _fmt_percent(weekly_reviewed, weekly_reviews),
                "weekly_avg_review_hours": weekly_avg_review_hours,
                "weekly_results": await self._impact_group_counts("weekly_request_reviews", "result", guild_id, "AND status='reviewed'"),
            },
            "weekly": {
                "claims": int(weekly_claims),
                "dm_log_events": int(weekly_dm_logs),
                "claim_statuses": await self._impact_group_counts("weekly_claims", "status", guild_id),
                "members_with_streaks": int(streak_members),
                "best_top5_streak": int(best_streak),
            },
            "support": {
                "tickets_total": int(tickets),
                "tickets_open": int(tickets_open),
                "tickets_closed": int(tickets_closed),
                "avg_ticket_close_hours": avg_ticket_close_hours,
                "ticket_statuses": await self._impact_group_counts("tickets", "status_tag", guild_id),
                "ticket_transcripts_saved": int(transcripts_saved),
                "satisfaction_responses": int(satisfaction_responses),
                "satisfaction_average": satisfaction_average,
                "help_submissions_total": int(help_submissions),
                "help_kinds": await self._impact_group_counts("help_submissions", "kind", guild_id),
                "help_statuses": await self._impact_group_counts("help_submissions", "status", guild_id),
                "transcript_requests_total": int(transcript_requests),
                "transcript_request_statuses": await self._impact_group_counts("transcript_requests", "status", guild_id),
            },
            "operations": {
                "daily_stat_days": int(daily_recaps),
                "daily_summaries_sent": int(daily_summaries_sent),
                "daily_summary_snapshots": int(daily_recaps),
                "weekly_recap_snapshots": int(weekly_recaps),
                "database_backups": int(database_backups),
                "database_restores": int(database_restores),
                "anti_farm_events": int(anti_farm_events),
                "joins": int(daily.get("joins", 0) or 0),
                "leaves": int(daily.get("leaves", 0) or 0),
                "bans": int(daily.get("bans", 0) or 0),
                "unbans": int(daily.get("unbans", 0) or 0),
                "latest_daily_summary_day": str(daily.get("latest_day") or ""),
            },
            "forecast": forecast,
            "daily_series": daily.get("series", []),
            "command_breakdown": daily.get("command_totals", {}),
        }

    def _impact_metric_rows(self, metrics: dict) -> list[tuple[str, str, str]]:
        rows: list[tuple[str, str, str]] = []

        def add(section: str, name: str, value) -> None:
            rows.append((section, name, str(value if value is not None else "")))

        add("Report", "Guild", metrics["report"]["guild_name"])
        add("Report", "Generated at", metrics["report"]["snapshot_label"])
        add("Headline", "Current server members", metrics["headline"]["current_members"])
        add("Headline", "Unique members touched by tracked workflows", metrics["headline"]["unique_members_touched"])
        add("Headline", "Tracked interaction events", metrics["headline"]["tracked_event_total"])
        add("Headline", "Support/help items handled", metrics["headline"]["support_items"])
        add("Headline", "Level requests coordinated", metrics["headline"]["level_requests_total"])

        for key, value in metrics["community"].items():
            add("Community", key.replace("_", " ").title(), value)
        for key, value in metrics["activity"].items():
            add("Activity", key.replace("_", " ").title(), value)
        for key, value in metrics["requests"].items():
            if isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    add("Requests", f"{key}.{sub_key}", sub_value)
            else:
                add("Requests", key.replace("_", " ").title(), value)
        for key, value in metrics["weekly"].items():
            if isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    add("Weekly", f"{key}.{sub_key}", sub_value)
            else:
                add("Weekly", key.replace("_", " ").title(), value)
        for key, value in metrics["support"].items():
            if isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    add("Support", f"{key}.{sub_key}", sub_value)
            else:
                add("Support", key.replace("_", " ").title(), value)
        for key, value in metrics["operations"].items():
            add("Operations", key.replace("_", " ").title(), value)
        for key, value in metrics.get("forecast", {}).items():
            if isinstance(value, list):
                add("Forecast", key.replace("_", " ").title(), " | ".join(str(item) for item in value))
            else:
                add("Forecast", key.replace("_", " ").title(), value)
        for name, count in list((metrics.get("command_breakdown") or {}).items())[:20]:
            add("Top Commands", f"/{name}", count)
        return rows

    def _impact_csv(self, metrics: dict) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["section", "metric", "value"])
        writer.writerows(self._impact_metric_rows(metrics))
        return output.getvalue()

    def _impact_daily_csv(self, metrics: dict) -> str:
        output = io.StringIO()
        columns = [
            "day",
            "messages",
            "active_members",
            "active_channels",
            "commands",
            "command_errors",
            "reactions",
            "voice_minutes",
            "joins",
            "leaves",
            "peak_voice_users",
            "peak_online_members",
            "top_command",
            "top_command_count",
        ]
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in metrics.get("daily_series", []) or []:
            writer.writerow({key: row.get(key, "") for key in columns})
        return output.getvalue()

    def _impact_breakdown_csv(self, metrics: dict) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["section", "key", "value"])
        sections = {
            "live_results": metrics.get("requests", {}).get("live_results", {}),
            "weekly_results": metrics.get("requests", {}).get("weekly_results", {}),
            "weekly_claim_statuses": metrics.get("weekly", {}).get("claim_statuses", {}),
            "ticket_statuses": metrics.get("support", {}).get("ticket_statuses", {}),
            "help_kinds": metrics.get("support", {}).get("help_kinds", {}),
            "help_statuses": metrics.get("support", {}).get("help_statuses", {}),
            "transcript_request_statuses": metrics.get("support", {}).get("transcript_request_statuses", {}),
            "commands": metrics.get("command_breakdown", {}),
        }
        for section, values in sections.items():
            for key, value in (values or {}).items():
                writer.writerow([section, key, value])
        return output.getvalue()

    def _impact_markdown(self, metrics: dict) -> str:
        headline = metrics["headline"]
        requests = metrics["requests"]
        support = metrics["support"]
        activity = metrics["activity"]
        weekly = metrics["weekly"]
        operations = metrics["operations"]
        forecast = metrics.get("forecast", {})
        live_state = requests["current_state"]
        recommendations = forecast.get("recommendations") or []

        return "\n".join(
            [
                "# Avenue Guard Impact Report",
                "",
                f"Generated for **{metrics['report']['guild_name']}** on **{metrics['report']['snapshot_label']}**.",
                "",
                "## CV-Ready Summary",
                "",
                (
                    f"Avenue Guard supports a community of **{_fmt_num(headline['current_members'])} members**, "
                    f"has touched **{_fmt_num(headline['unique_members_touched'])} unique members** through tracked workflows, "
                    f"has recorded **{_fmt_num(headline['tracked_event_total'])} tracked interaction events**, "
                    f"has handled **{_fmt_num(headline['support_items'])} support/help items**, and has coordinated "
                    f"**{_fmt_num(headline['level_requests_total'])} level requests**."
                ),
                "",
                "## Engagement Forecast",
                "",
                f"- Engagement signal: **{forecast.get('engagement_signal', 'Unknown')}**",
                f"- Last 7 days: **{_fmt_num(forecast.get('last_7_messages', 0))} messages** and **{_fmt_num(forecast.get('last_7_commands', 0))} commands**",
                f"- Previous 7 days: **{_fmt_num(forecast.get('previous_7_messages', 0))} messages** and **{_fmt_num(forecast.get('previous_7_commands', 0))} commands**",
                f"- Projected next 7 days: **{_fmt_num(forecast.get('projected_next_7_messages', 0))} messages** and **{_fmt_num(forecast.get('projected_next_7_commands', 0))} commands**",
                f"- Average daily active members, 7d: **{forecast.get('avg_daily_active_members_7d', 0)}**",
                f"- Average daily active channels, 7d: **{forecast.get('avg_daily_active_channels_7d', 0)}**",
                f"- 30-day command error rate: **{forecast.get('command_error_rate_30d', '0%')}**",
                f"- Current review backlog: **{_fmt_num(forecast.get('review_backlog', 0))}** pending requests",
                "",
                "### Suggested Actions",
                "",
                *[f"- {item}" for item in recommendations],
                "",
                "## Community Reach",
                "",
                f"- Current server size: **{_fmt_num(metrics['community']['current_members'])}** members",
                f"- Unique members touched by tracked workflows: **{_fmt_num(metrics['community']['unique_members_touched'])}**",
                f"- Members with tracked weekly activity: **{_fmt_num(metrics['community']['tracked_active_members'])}**",
                f"- Weeks with activity history: **{_fmt_num(metrics['community']['tracked_weeks'])}**",
                "",
                "## Activity And Commands",
                "",
                f"- Tracked messages: **{_fmt_num(activity['tracked_messages'])}**",
                f"- Reactions recorded in daily summaries: **{_fmt_num(activity['reactions'])}**",
                f"- Slash commands recorded in daily summaries: **{_fmt_num(activity['commands'])}**",
                f"- Command errors recorded: **{_fmt_num(activity['command_errors'])}**",
                f"- Voice time recorded: **{_fmt_num(activity['voice_minutes'])} minutes**",
                f"- Top command: **/{activity['top_command'] or 'none'}** ({_fmt_num(activity['top_command_count'])} uses)",
                "",
                "## Level Requests",
                "",
                f"- Current request state: **{live_state['state']}**, wave **{live_state['wave_id']}**",
                f"- Live wave requests: **{_fmt_num(requests['live_total'])}** total, **{_fmt_num(requests['live_reviewed'])}** reviewed, **{_fmt_num(requests['live_pending'])}** pending ({requests['live_review_rate']} reviewed)",
                f"- Average live request review time: **{requests['live_avg_review_hours']} hours**",
                f"- Weekly request submissions: **{_fmt_num(requests['weekly_total'])}** total, **{_fmt_num(requests['weekly_reviewed'])}** reviewed, **{_fmt_num(requests['weekly_pending'])}** pending ({requests['weekly_review_rate']} reviewed)",
                f"- Average weekly request review time: **{requests['weekly_avg_review_hours']} hours**",
                f"- Request waves handled: **{_fmt_num(requests['live_waves'])}**",
                f"- Unique live level IDs submitted: **{_fmt_num(requests['unique_live_level_ids'])}**",
                f"- Request edit audit entries: **{_fmt_num(requests['edit_audit_entries'])}**",
                f"- Scheduled openings currently pending: **{_fmt_num(requests['pending_openings'])}**",
                "",
                "## Tickets And Help",
                "",
                f"- Tickets opened: **{_fmt_num(support['tickets_total'])}**",
                f"- Tickets closed/resolved: **{_fmt_num(support['tickets_closed'])}**",
                f"- Average ticket close time: **{support['avg_ticket_close_hours']} hours**",
                f"- Ticket transcripts saved: **{_fmt_num(support['ticket_transcripts_saved'])}**",
                f"- Satisfaction responses: **{_fmt_num(support['satisfaction_responses'])}** with average **{support['satisfaction_average']}**",
                f"- Help submissions: **{_fmt_num(support['help_submissions_total'])}**",
                f"- Transcript requests: **{_fmt_num(support['transcript_requests_total'])}**",
                "",
                "## Weekly Rewards And Safety",
                "",
                f"- Weekly reward claim records: **{_fmt_num(weekly['claims'])}**",
                f"- Weekly DM log events: **{_fmt_num(weekly['dm_log_events'])}**",
                f"- Members with repeated top-5 streaks: **{_fmt_num(weekly['members_with_streaks'])}**",
                f"- Best top-5 streak: **{_fmt_num(weekly['best_top5_streak'])} weeks**",
                f"- Anti-farm events logged: **{_fmt_num(operations['anti_farm_events'])}**",
                "",
                "## Persistence Notes",
                "",
                f"- Database backups recorded: **{_fmt_num(operations['database_backups'])}**",
                f"- Database restores recorded: **{_fmt_num(operations['database_restores'])}**",
                "This report was saved into the bot database and posted as Markdown, CSV, trend CSV, breakdown CSV, and raw JSON attachments. The CSV files can be imported directly into Google Sheets for charts, CV evidence, forecasting, or future portfolio reporting.",
                "",
            ]
        )

    def _impact_report_embed(self, metrics: dict) -> discord.Embed:
        headline = metrics["headline"]
        requests = metrics["requests"]
        support = metrics["support"]
        activity = metrics["activity"]
        forecast = metrics.get("forecast", {})
        embed = discord.Embed(
            title="Avenue Guard Impact And Forecast Report",
            description=(
                f"Generated <t:{int(metrics['report']['snapshot_ts'])}:R>. "
                "Files are attached for long-term records, spreadsheet import, and trend tracking."
            ),
            color=discord.Color.blurple(),
            timestamp=now_madrid(),
        )
        embed.add_field(
            name="CV Headline",
            value=(
                f"Community: **{_fmt_num(headline['current_members'])}** members\n"
                f"Unique members touched: **{_fmt_num(headline['unique_members_touched'])}**\n"
                f"Tracked events: **{_fmt_num(headline['tracked_event_total'])}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Requests",
            value=(
                f"Live: **{_fmt_num(requests['live_total'])}** ({requests['live_review_rate']} reviewed)\n"
                f"Weekly: **{_fmt_num(requests['weekly_total'])}** ({requests['weekly_review_rate']} reviewed)\n"
                f"Waves: **{_fmt_num(requests['live_waves'])}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Support",
            value=(
                f"Tickets: **{_fmt_num(support['tickets_total'])}**\n"
                f"Help submissions: **{_fmt_num(support['help_submissions_total'])}**\n"
                f"Transcripts: **{_fmt_num(support['ticket_transcripts_saved'])}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Activity",
            value=(
                f"Messages: **{_fmt_num(activity['tracked_messages'])}**\n"
                f"Commands: **{_fmt_num(activity['commands'])}**\n"
                f"Voice minutes: **{_fmt_num(activity['voice_minutes'])}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Forecast",
            value=(
                f"Signal: **{forecast.get('engagement_signal', 'Unknown')}**\n"
                f"Next 7d messages: **{_fmt_num(forecast.get('projected_next_7_messages', 0))}**\n"
                f"Review backlog: **{_fmt_num(forecast.get('review_backlog', 0))}**"
            ),
            inline=True,
        )
        return embed

    def _impact_files(self, metrics: dict) -> list[discord.File]:
        slug = now_madrid().strftime("%Y%m%d-%H%M%S")
        md_bytes = self._impact_markdown(metrics).encode("utf-8")
        summary_csv = self._impact_csv(metrics).encode("utf-8")
        daily_csv = self._impact_daily_csv(metrics).encode("utf-8")
        breakdown_csv = self._impact_breakdown_csv(metrics).encode("utf-8")
        json_bytes = json.dumps(metrics, indent=2, ensure_ascii=False).encode("utf-8")
        return [
            discord.File(io.BytesIO(md_bytes), filename=f"avenue-guard-impact-{slug}.md"),
            discord.File(io.BytesIO(summary_csv), filename=f"avenue-guard-impact-summary-{slug}.csv"),
            discord.File(io.BytesIO(daily_csv), filename=f"avenue-guard-impact-daily-trends-{slug}.csv"),
            discord.File(io.BytesIO(breakdown_csv), filename=f"avenue-guard-impact-breakdowns-{slug}.csv"),
            discord.File(io.BytesIO(json_bytes), filename=f"avenue-guard-impact-raw-{slug}.json"),
        ]

    async def bot_impact(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)

        await self._defer(ctx, ephemeral=True)
        if not await self._is_impact_owner_ctx(ctx):
            return await self._send(ctx, "You don't have permission to use this.", ephemeral=True)

        tracking = self.bot.get_cog("TrackingCog")
        if tracking is not None:
            try:
                await tracking.flush_activity_counts()
            except Exception as e:
                await log_error(self.bot, f"Impact report activity flush failed: {repr(e)}")
        background = self.bot.get_cog("BackgroundCog")
        if background is not None:
            try:
                await background._persist_current_day()
            except Exception as e:
                await log_error(self.bot, f"Impact report daily snapshot flush failed: {repr(e)}")
        metrics = await self._collect_impact_metrics(ctx.guild, ctx.user.id)
        embed = self._impact_report_embed(metrics)
        channel_id = self.bot.config.get_int("impact", "report_channel_id", default=0)
        if not channel_id:
            channel_id = self.bot.config.get_int("channels", "general_logging_channel_id", default=0)
        channel = ctx.guild.get_channel(channel_id) if channel_id else None

        sent = None
        if isinstance(channel, discord.TextChannel):
            try:
                sent = await channel.send(
                    content="Avenue Guard impact report generated. The CSV exports can be imported into Google Sheets.",
                    embed=embed,
                    files=self._impact_files(metrics),
                    allowed_mentions=no_mentions(),
                )
            except Exception as e:
                await log_error(self.bot, f"Could not send impact report attachments: {repr(e)}")

        if sent is None:
            try:
                await self._send(
                    ctx,
                    "Impact report generated, but no valid report channel was available. Here are the files directly.",
                    embed=embed,
                    files=self._impact_files(metrics),
                    ephemeral=True,
                )
            except Exception as e:
                await log_error(self.bot, f"Could not send fallback impact report attachments: {repr(e)}")
                await self._send(ctx, "Impact report generated, but I could not attach the files. Check the error log.", embed=embed, ephemeral=True)

        metrics["report"]["report_channel_id"] = int(getattr(getattr(sent, "channel", None), "id", 0) or 0)
        metrics["report"]["report_message_id"] = int(getattr(sent, "id", 0) or 0)
        await self.bot.db.execute(
            "INSERT OR REPLACE INTO impact_snapshots(guild_id,snapshot_ts,report_channel_id,report_message_id,payload_json) VALUES(?,?,?,?,?)",
            (
                int(ctx.guild.id),
                int(metrics["report"]["snapshot_ts"]),
                int(metrics["report"]["report_channel_id"]),
                int(metrics["report"]["report_message_id"]),
                json.dumps(metrics, separators=(",", ":")),
            ),
        )

        if sent is not None:
            link = f"https://discord.com/channels/{ctx.guild.id}/{sent.channel.id}/{sent.id}"
            msg = f"Impact report saved and posted: {link}"
        else:
            msg = "Impact report snapshot saved in the database. Configure `impact.report_channel_id` for persistent Discord attachments."
        await self._log_admin_action(ctx.guild, ctx.user.id, "impact_report_generated", f"message_id={int(getattr(sent, 'id', 0) or 0)}")
        await self._send(ctx, msg, ephemeral=True)

    async def bot_backup(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        await self._defer(ctx, ephemeral=True)
        if not await self._is_impact_owner_ctx(ctx):
            return await self._send(ctx, "You don't have permission to use this.", ephemeral=True)

        try:
            sent = await self._post_database_backup(ctx.guild, reason="manual", requested_by=ctx.user.id)
        except Exception as e:
            await log_error(self.bot, f"Manual database backup failed: {repr(e)}")
            return await self._send(ctx, "I couldn't create the database backup. Check the error log.", ephemeral=True)

        if sent is None:
            return await self._send(ctx, "Backup created locally, but I could not post it to a valid backup channel.", ephemeral=True)
        link = f"https://discord.com/channels/{ctx.guild.id}/{sent.channel.id}/{sent.id}"
        await self._log_admin_action(ctx.guild, ctx.user.id, "database_backup_created", f"message_id={sent.id}")
        await self._send(ctx, f"Database backup posted: {link}", ephemeral=True)

    async def bot_restore(
        self,
        ctx: discord.ApplicationContext,
        archive: discord.Option(discord.Attachment, "Upload a .sqlite3/.db backup or a .zip created by /bot backup"),
        confirm: discord.Option(str, "Type RESTORE to confirm replacing the live database"),
    ):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        await self._defer(ctx, ephemeral=True)
        if not await self._is_impact_owner_ctx(ctx):
            return await self._send(ctx, "You don't have permission to use this.", ephemeral=True)
        if str(confirm or "").strip() != "RESTORE":
            return await self._send(ctx, "Type `RESTORE` in the confirm option to replace the live database.", ephemeral=True)
        if bool(getattr(self.bot.db, "uses_remote", False)):
            return await self._send(
                ctx,
                "Turso/libSQL remote storage is active, so uploaded SQLite restore is disabled to protect the remote primary. "
                "Use Turso backup/import tools for a full remote restore.",
                ephemeral=True,
            )

        uploaded_path: Path | None = None
        restore_path: Path | None = None
        original_name = str(getattr(archive, "filename", "uploaded.sqlite3") or "uploaded.sqlite3")
        try:
            uploaded_path, original_name = await self._save_restore_attachment(archive)
            restore_path = self._extract_sqlite_restore_file(uploaded_path)
            validation = await self._validate_restore_database(restore_path)
        except Exception as e:
            await log_error(self.bot, f"Database restore upload validation failed: {repr(e)}")
            return await self._send(ctx, f"I couldn't use that backup: {e}", ephemeral=True)

        pre_backup = None
        try:
            pre_backup = await self._post_database_backup(ctx.guild, reason="pre_restore", requested_by=ctx.user.id)
        except Exception as e:
            await log_error(self.bot, f"Pre-restore database backup failed: {repr(e)}")

        try:
            restored_size = await self.bot.db.restore_from(restore_path)
        except Exception as e:
            await log_error(self.bot, f"Database restore failed after validation: {repr(e)}")
            return await self._send(ctx, "The uploaded database passed validation, but restoring it failed. The previous database was kept when possible; check the error log.", ephemeral=True)

        restore_ts = int(time.time())
        pre_channel_id = int(getattr(getattr(pre_backup, "channel", None), "id", 0) or 0)
        pre_message_id = int(getattr(pre_backup, "id", 0) or 0)
        pre_filename = ""
        if pre_backup is not None:
            try:
                attachments = list(getattr(pre_backup, "attachments", []) or [])
                if attachments:
                    pre_filename = str(getattr(attachments[0], "filename", "") or "")
            except Exception:
                pre_filename = ""
        await self.bot.db.execute(
            """
            INSERT OR REPLACE INTO database_restore_log(
                guild_id, restore_ts, uploaded_by, source_filename, size_bytes,
                pre_restore_backup_channel_id, pre_restore_backup_message_id,
                pre_restore_backup_filename, tables_count, known_tables_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(ctx.guild.id),
                restore_ts,
                int(ctx.user.id),
                str(original_name)[:240],
                int(restored_size or validation.get("size_bytes") or 0),
                pre_channel_id,
                pre_message_id,
                pre_filename[:240],
                int(validation.get("tables_count") or 0),
                json.dumps(validation.get("known_tables") or [], separators=(",", ":"))[:4000],
            ),
        )

        await self._log_admin_action(
            ctx.guild,
            ctx.user.id,
            "database_restored",
            f"source={original_name} size={restored_size} pre_backup_message_id={pre_message_id}",
        )
        embed = discord.Embed(
            title="Database Restored",
            description="The uploaded SQLite backup was validated, migrated, and swapped into the live database path.",
            color=discord.Color.green(),
            timestamp=now_madrid(),
        )
        embed.add_field(name="Source File", value=f"`{str(original_name)[:120]}`", inline=False)
        embed.add_field(name="Restored Size", value=f"{_fmt_num(restored_size)} bytes", inline=True)
        embed.add_field(name="Tables Found", value=f"{_fmt_num(validation.get('tables_count'))}", inline=True)
        known = ", ".join(validation.get("known_tables") or [])
        embed.add_field(name="Recognized Tables", value=(known[:1024] or "None"), inline=False)
        if pre_backup is not None:
            link = f"https://discord.com/channels/{ctx.guild.id}/{pre_backup.channel.id}/{pre_backup.id}"
            embed.add_field(name="Pre-Restore Backup", value=f"Created before restore: {link}", inline=False)
        else:
            embed.add_field(name="Pre-Restore Backup", value="Could not post a backup before restore. The restore still completed.", inline=False)
        embed.set_footer(text="Recommended: run /bot storage and /bot dashboard after restore.")
        await self._send(ctx, embed=embed, ephemeral=True)

    async def bot_storage(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        await self._defer(ctx, ephemeral=True)
        if not await self._is_impact_owner_ctx(ctx):
            return await self._send(ctx, "You don't have permission to use this.", ephemeral=True)

        storage_note, storage_ok = self._database_storage_note()
        channel_id = self._backup_channel_id()
        backup_row = await self.bot.db.fetchone(
            "SELECT backup_ts, channel_id, message_id, size_bytes, reason, filename FROM database_backups WHERE guild_id=? ORDER BY backup_ts DESC LIMIT 1",
            (int(ctx.guild.id),),
        )
        backup_text = "No backup record yet."
        if backup_row:
            backup_text = (
                f"Last backup: <t:{int(backup_row['backup_ts'])}:R>\n"
                f"Size: **{_fmt_num(backup_row['size_bytes'])} bytes**\n"
                f"Reason: `{str(backup_row['reason'] or 'unknown')}`\n"
                f"File: `{str(backup_row['filename'] or '')[:80]}`"
            )
        restore_row = await self.bot.db.fetchone(
            "SELECT restore_ts, uploaded_by, source_filename, size_bytes, pre_restore_backup_message_id FROM database_restore_log WHERE guild_id=? ORDER BY restore_ts DESC LIMIT 1",
            (int(ctx.guild.id),),
        )
        restore_text = "No restore record yet."
        if restore_row:
            restore_text = (
                f"Last restore: <t:{int(restore_row['restore_ts'])}:R>\n"
                f"Uploaded by: <@{int(restore_row['uploaded_by'] or 0)}>\n"
                f"Size: **{_fmt_num(restore_row['size_bytes'])} bytes**\n"
                f"File: `{str(restore_row['source_filename'] or '')[:80]}`"
            )
        backup_enabled = bool(self.bot.config.get("database", "backups", "enabled", default=True))
        interval_hours = int(self.bot.config.get("database", "backups", "interval_hours", default=12) or 12)
        embed = discord.Embed(
            title="Avenue Guard Storage",
            description="Database persistence and backup status.",
            color=discord.Color.green() if storage_ok and channel_id else discord.Color.gold(),
            timestamp=now_madrid(),
        )
        embed.add_field(name="Database Path", value=storage_note[:1024], inline=False)
        embed.add_field(
            name="Automatic Backups",
            value=(
                f"Enabled: **{'yes' if backup_enabled else 'no'}**\n"
                f"Interval: **{interval_hours}h**\n"
                f"Channel: <#{channel_id}>"
            ),
            inline=True,
        )
        embed.add_field(name="Latest Backup", value=backup_text[:1024], inline=True)
        embed.add_field(name="Latest Restore", value=restore_text[:1024], inline=True)
        if not storage_ok:
            embed.add_field(
                name="Recommended Fix",
                value="Configure `TURSO_AUTH_TOKEN` with `database.turso_url`, or set `AVENUE_GUARD_DB_PATH`/`database.path` to durable storage.",
                inline=False,
            )
        await self._send(ctx, embed=embed, ephemeral=True)

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
                warning = server_icon_url_warning(url)
                warning_suffix = " - expires" if warning else ""
                suffix = f" - {marker}{warning_suffix}" if marker else warning_suffix
                lines.append(f"`{idx}` {url[:120]}{suffix}")
            embed.add_field(name=f"Configured icons ({len(urls)})", value="\n".join(lines)[:1024], inline=False)
        else:
            embed.add_field(name="Configured icons", value="No URLs configured.", inline=False)
        warnings = [f"`{idx}` {server_icon_url_warning(url)}" for idx, url in enumerate(urls, start=1) if server_icon_url_warning(url)]
        if warnings:
            embed.add_field(name="Icon URL Warnings", value="\n".join(warnings)[:1024], inline=False)
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

    def _server_icon_operation_lock(self) -> asyncio.Lock:
        background = self.bot.get_cog("BackgroundCog")
        lock = getattr(background, "_server_icon_lock", None)
        return lock if isinstance(lock, asyncio.Lock) else self._server_icon_config_lock

    async def _save_server_icon_config(self, ctx: discord.ApplicationContext, action: str, detail: str) -> bool:
        try:
            cfg = ensure_server_icon_config(self.bot.config)
            if str(action or "").startswith("server_icon_"):
                cfg["last_error"] = ""
                cfg["last_error_ts"] = 0
            await persist_server_icon_config(self.bot, cfg)
            try:
                self.bot.config.save()
            except Exception as local_error:
                await log_error(
                    self.bot,
                    f"Server icon config persisted remotely but local config save failed: {repr(local_error)}",
                )
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
        await self._defer(ctx, ephemeral=True)
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
        await self._defer(ctx, ephemeral=True)
        if not await self._is_admin_ctx(ctx):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

        raw_mode = str(mode or "").strip().casefold()
        if raw_mode not in VALID_SERVER_ICON_MODES:
            return await ctx.respond("Mode must be `random`, `linear`, or `disabled`.", ephemeral=True)
        async with self._server_icon_operation_lock():
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
        await self._defer(ctx, ephemeral=True)
        if not await self._is_admin_ctx(ctx):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)
        if not is_valid_icon_url(url):
            return await ctx.respond("That does not look like a valid HTTP image URL.", ephemeral=True)
        warning = server_icon_url_warning(url)
        if warning:
            return await ctx.respond(f"I can't use that URL for rotation: {warning}", ephemeral=True)

        async with self._server_icon_operation_lock():
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
        await self._defer(ctx, ephemeral=True)
        if not await self._is_admin_ctx(ctx):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)
        if not is_valid_icon_url(url):
            return await ctx.respond("That does not look like a valid HTTP image URL.", ephemeral=True)
        warning = server_icon_url_warning(url)
        if warning:
            return await ctx.respond(f"I can't use that URL for rotation: {warning}", ephemeral=True)

        async with self._server_icon_operation_lock():
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
        await self._defer(ctx, ephemeral=True)
        if not await self._is_admin_ctx(ctx):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

        async with self._server_icon_operation_lock():
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
        await self._defer(ctx, ephemeral=True)
        if not await self._is_admin_ctx(ctx):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)
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
        await self._defer(ctx, ephemeral=True)
        if not await self._is_admin_ctx(ctx):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)
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

    def _task_state(self, cog_name: str, attr: str) -> str:
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

    async def _count_db(self, sql: str, params: tuple) -> int:
        try:
            row = await self.bot.db.fetchone(sql, params)
            return int(row["c"]) if row and row["c"] is not None else 0
        except Exception:
            return 0

    async def _dashboard_issues(self, guild: discord.Guild) -> tuple[list[str], list[str]]:
        cfg = self.bot.config
        issues: list[str] = []
        repairs: list[str] = []
        me = guild.me or guild.get_member(self.bot.user.id)

        channel_checks = {
            "general logs": cfg.get_int("channels", "general_logging_channel_id"),
            "weekly requests": cfg.get_int("channels", "weekly_request_channel_ID"),
            "request button": cfg.get_int("level_requests", "request_channel"),
            "level requested": cfg.get_int("level_requests", "level_requested"),
            "sent results": cfg.get_int("level_requests", "sent_channel"),
            "rejected results": cfg.get_int("level_requests", "rejected_channel"),
            "impact reports": cfg.get_int("impact", "report_channel_id", default=0) or cfg.get_int("channels", "general_logging_channel_id"),
            "database backups": self._backup_channel_id(),
            "ticket transcripts": cfg.get_int("channels", "general_logging_channel_id"),
            "transcript requests": cfg.get_int("channels", "transcript_requests_channel_id"),
        }
        for label, channel_id in channel_checks.items():
            if not channel_id or guild.get_channel(int(channel_id)) is None:
                issues.append(f"{label}: missing channel `{channel_id or 'not set'}`")
                repairs.append(f"Update the `{label}` channel ID in `config.json`")

        if me is not None:
            guild_perms = getattr(me, "guild_permissions", None)
            if guild_perms is not None and not bool(getattr(guild_perms, "manage_guild", False)):
                icon_cfg = ensure_server_icon_config(cfg)
                if normalize_server_icon_mode(icon_cfg.get("mode")) != "disabled":
                    issues.append("server icon rotation: bot needs Manage Server")
                    repairs.append("Grant the bot Manage Server, or set server icon rotation mode to disabled")

        storage_note, storage_ok = self._database_storage_note()
        if not storage_ok:
            issues.append("database storage: path may not be persistent")
            repairs.append("Configure Turso/libSQL with `TURSO_AUTH_TOKEN`, or set `AVENUE_GUARD_DB_PATH`/`database.path` to durable storage")
        if bool(getattr(self.bot.db, "uses_remote", False)) and bool(getattr(self.bot.db, "_remote_dirty", False)):
            issues.append("database replication: local commits are waiting to sync to Turso")
            repairs.append("Check Turso status and credentials, then run `/resync`")

        command_error = getattr(self.bot, "_last_command_error", None)
        if isinstance(command_error, dict):
            error_ts = int(command_error.get("ts", 0) or 0)
            if error_ts and int(time.time()) - error_ts <= 3600:
                command_name = str(command_error.get("command") or "unknown")[:100]
                category = str(command_error.get("category") or "error")[:100]
                issues.append(f"recent command failure: `/{command_name}` ({category}) <t:{error_ts}:R>")
                if category == "interaction_timeout":
                    repairs.append("The command missed Discord's response window; verify the current deployment includes early command deferrals")
                else:
                    repairs.append("Check the global error log for the most recent command traceback")

        category_id = cfg.get_int("tickets", "ticket_category_id")
        category = guild.get_channel(category_id) if category_id else None
        if not isinstance(category, discord.CategoryChannel):
            issues.append("ticket category: missing or invalid")
            repairs.append("Set `tickets.ticket_category_id` to the ticket category")
        elif me is not None and not category.permissions_for(me).manage_channels:
            issues.append("ticket category: bot cannot manage channels")
            repairs.append("Give the bot Manage Channels in the ticket category")

        request_cog = self.bot.get_cog("RequestLevelsCog")
        if request_cog is None:
            issues.append("request system: cog not loaded")
            repairs.append("Check startup logs for RequestLevelsCog import errors")
        else:
            try:
                state = await request_cog._get_state(guild.id)
                if not state or not state["request_message_id"]:
                    issues.append("request button: no saved message")
                    repairs.append("Run `/refresh-request-button`")
            except Exception as e:
                issues.append(f"request state: {type(e).__name__}")
                repairs.append("Run `/requests repair`, then check the error log")

        template_issues: list[str] = []
        self._validate_request_templates(template_issues)
        if template_issues:
            issues.extend(template_issues[:4])
            repairs.append("Fix the listed request template variable or color entries in `config.json`")

        return issues, repairs

    async def _admin_dashboard_embed(self, guild: discord.Guild, page: str = "overview") -> discord.Embed:
        page = str(page or "overview").casefold()
        issues, repairs = await self._dashboard_issues(guild)
        db_ok = True
        db_note = "Connected"
        try:
            await self.bot.db.fetchone("SELECT 1 AS c")
        except Exception as e:
            db_ok = False
            db_note = type(e).__name__
        if db_ok and bool(getattr(self.bot.db, "uses_remote", False)):
            db_note = "Connected to Turso"
            if bool(getattr(self.bot.db, "_remote_dirty", False)):
                db_note = "Connected; replication pending"
        storage_note, storage_ok = self._database_storage_note()

        try:
            request_row = await self.bot.db.fetchone(
                "SELECT state, wave_id, submitted_count, request_limit, close_ts, request_type "
                "FROM level_request_state WHERE guild_id=?",
                (guild.id,),
            )
        except Exception:
            request_row = None
        request_state = "Not initialized"
        if request_row:
            limit = "none" if request_row["request_limit"] is None else str(int(request_row["request_limit"]))
            request_state = f"{str(request_row['state']).title()} wave **{int(request_row['wave_id'])}**\nSubmitted: **{int(request_row['submitted_count'])}** / **{limit}**"
            if request_row["request_type"]:
                request_state += f"\nType: **{str(request_row['request_type']).replace('_', ' ').title()}**"
            if request_row["close_ts"] and str(request_row["state"]) == "open":
                request_state += f"\nCloses <t:{int(request_row['close_ts'])}:R>"

        icon_cfg = ensure_server_icon_config(self.bot.config)
        icon_mode = normalize_server_icon_mode(icon_cfg.get("mode"))
        icon_interval = int(icon_cfg.get("interval_seconds", 0) or 0)
        icon_current = parse_server_icon_index(icon_cfg.get("current_index", -1), len(icon_cfg.get("urls", []) or []))
        icon_text = f"Mode: **{icon_mode}**\nInterval: **{icon_interval}s**\nCurrent: **{icon_current + 1 if icon_current >= 0 else 'unknown'}** / **{len(icon_cfg.get('urls', []) or [])}**"

        if page == "config":
            embed = discord.Embed(
                title="Admin Dashboard - Config",
                description=f"Configuration scan found **{len(issues)}** issue(s).",
                color=discord.Color.green() if not issues else discord.Color.orange(),
                timestamp=now_madrid(),
            )
            embed.add_field(name="Issues", value="\n".join(f"- {item}" for item in issues[:12])[:1024] or "No obvious config issues found.", inline=False)
            embed.add_field(
                name="Key State",
                value=(
                    f"Database: **{db_note}**\n"
                    f"Storage: {'persistent-looking' if storage_ok else 'needs review'}\n"
                    f"Request state: {request_state.splitlines()[0]}\n"
                    f"Icon rotation: **{icon_mode}**"
                ),
                inline=False,
            )
            return embed

        if page == "repairs":
            embed = discord.Embed(
                title="Admin Dashboard - Repair Tips",
                description="Suggested next actions based on the current scan.",
                color=discord.Color.green() if not issues else discord.Color.gold(),
                timestamp=now_madrid(),
            )
            deduped_repairs = []
            for item in repairs:
                if item not in deduped_repairs:
                    deduped_repairs.append(item)
            embed.add_field(name="Suggestions", value="\n".join(f"- {item}" for item in deduped_repairs[:12])[:1024] or "No repairs suggested right now.", inline=False)
            embed.add_field(name="Fast Repairs", value="`/requests repair`\n`/refresh-request-button`\n`/resync`", inline=True)
            embed.add_field(name="Remaining Issues", value=str(len(issues)), inline=True)
            return embed

        open_tickets = await self._count_db("SELECT COUNT(*) AS c FROM tickets WHERE guild_id=? AND status IN ('open','closing_prompted')", (guild.id,))
        pending_live = await self._count_db("SELECT COUNT(*) AS c FROM level_request_submissions WHERE guild_id=? AND status='pending'", (guild.id,))
        pending_weekly = await self._count_db("SELECT COUNT(*) AS c FROM weekly_request_reviews WHERE guild_id=? AND status='pending'", (guild.id,))
        active_weekly = await self._count_db("SELECT COUNT(*) AS c FROM weekly_sessions WHERE guild_id=? AND active=1", (guild.id,))
        farm_events = await self._count_db("SELECT COUNT(*) AS c FROM anti_farm_events WHERE guild_id=? AND ts>=?", (guild.id, int(time.time()) - 7 * 86400))
        current_week = week_start_sunday(now_madrid()).isoformat()
        reward_disabled = bool(
            await self._count_db(
                "SELECT COUNT(*) AS c FROM weekly_reward_disabled WHERE guild_id=? AND week_start=?",
                (guild.id, current_week),
            )
        )

        embed = discord.Embed(
            title="Avenue Guard Admin Dashboard",
            description="Live system overview for staff.",
            color=discord.Color.green() if db_ok and not issues else discord.Color.orange(),
            timestamp=now_madrid(),
        )
        embed.add_field(name="Core", value=f"Database: **{db_note}**\nLatency: **{round(self.bot.latency * 1000)} ms**\nLoaded cogs: **{len(self.bot.cogs)}**", inline=True)
        embed.add_field(name="Requests", value=f"{request_state}\nPending reviews: **{pending_live}** live / **{pending_weekly}** weekly", inline=True)
        embed.add_field(
            name="Tracking",
            value=(
                f"Weekly reward: **{'Disabled' if reward_disabled else 'Enabled'}**\n"
                f"Active weekly sessions: **{active_weekly}**\n"
                f"Anti-farm events, 7d: **{farm_events}**"
            ),
            inline=True,
        )
        embed.add_field(name="Tickets", value=f"Open tickets: **{open_tickets}**", inline=True)
        embed.add_field(name="Icon Rotation", value=icon_text, inline=True)
        embed.add_field(
            name="Background Tasks",
            value=(
                f"Weekly scan: `{self._task_state('TrackingCog', '_weekly_task')}`\n"
                f"Activity flush: `{self._task_state('TrackingCog', '_activity_flush_task')}`\n"
                f"Ticket scan: `{self._task_state('HelpCog', '_ticket_scan_task')}`\n"
                f"Daily summary: `{self._task_state('BackgroundCog', 'daily_report')}`\n"
                f"Icon rotation: `{self._task_state('BackgroundCog', 'rotate_server_icon')}`\n"
                f"DB backups: `{self._task_state('BackgroundCog', 'database_backup')}`"
            ),
            inline=False,
        )
        if issues:
            embed.add_field(name="Needs Attention", value="\n".join(f"- {item}" for item in issues[:5])[:1024], inline=False)
        return embed

    async def bot_dashboard(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        await self._defer(ctx, ephemeral=True)
        if not await self._is_admin_ctx(ctx):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)
        embed = await self._admin_dashboard_embed(ctx.guild, "overview")
        await self._send(ctx, embed=embed, view=AdminDashboardView(self, ctx.user.id), ephemeral=True)

    # --- /bot diagnostics ---

    async def bot_health(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        await self._defer(ctx, ephemeral=True)
        if not await self._is_admin_ctx(ctx):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

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
        if db_ok and bool(getattr(self.bot.db, "uses_remote", False)):
            db_note = "Connected to Turso"
            if bool(getattr(self.bot.db, "_remote_dirty", False)):
                db_note = "Connected; replication pending"

        storage_note, storage_ok = self._database_storage_note()
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
        embed.add_field(name="Storage", value="Persistent-looking" if storage_ok else "Needs review", inline=True)
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
                f"Server icon rotation: `{_task_state('BackgroundCog', 'rotate_server_icon')}`\n"
                f"DB backups: `{_task_state('BackgroundCog', 'database_backup')}`"
            ),
            inline=False,
        )
        if not storage_ok:
            embed.add_field(name="Storage Warning", value=storage_note[:1024], inline=False)
        await self._send(ctx, embed=embed, ephemeral=True)

    async def bot_doctor(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        await self._defer(ctx, ephemeral=True)
        if not await self._is_admin_ctx(ctx):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

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
            "impact reports": cfg.get_int("impact", "report_channel_id", default=0) or cfg.get_int("channels", "general_logging_channel_id"),
            "database backups": self._backup_channel_id(),
        }
        for label, channel_id in channel_checks.items():
            channel_perm_report(label, channel_id, common_text_perms)

        storage_note, storage_ok = self._database_storage_note()
        if storage_ok:
            ok.append("database storage: persistent-looking")
        else:
            issues.append(f"database storage: {storage_note}")
        if bool(getattr(self.bot.db, "uses_remote", False)) and bool(getattr(self.bot.db, "_remote_dirty", False)):
            last_sync_error = str(getattr(self.bot.db, "_last_remote_sync_error", "") or "")
            issues.append(f"Turso replication pending: {last_sync_error[:180] or 'sync retry queued'}")

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

        icon_cfg = ensure_server_icon_config(cfg)
        if me is not None and normalize_server_icon_mode(icon_cfg.get("mode")) != "disabled":
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
            embed.set_footer(text="Showing first 12 issues and first 12 healthy checks.")
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
            "request_type",
            "request_type_label",
            "request_type_line",
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
        await self._defer(ctx, ephemeral=True)
        if not await self._is_admin_ctx(ctx):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

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
            "review_access_channel_id",
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
        check_channel(
            "impact.report_channel_id",
            cfg.get_int("impact", "report_channel_id", default=0) or cfg.get_int("channels", "general_logging_channel_id"),
            discord.TextChannel,
        )
        check_channel("database.backups.channel_id", self._backup_channel_id(), discord.TextChannel)
        storage_note, storage_ok = self._database_storage_note()
        if storage_ok:
            ok_count += 1
        else:
            issues.append(f"database.path: {storage_note}")

        for key in ("MOD_ROLE_ID", "restriction_role_ID", "gambling_reward_role_id", "rps_streak_role_id", "review_access_role_id"):
            check_role(f"roles.{key}", cfg.get_int("roles", key))
        for idx, role_id in enumerate(cfg.get_int_list("roles", "admin_owner_role_ids"), start=1):
            check_role(f"roles.admin_owner_role_ids[{idx}]", role_id)
        for idx, role_id in enumerate(cfg.get_int_list("roles", "excluded_tracking_role_id"), start=1):
            check_role(f"roles.excluded_tracking_role_id[{idx}]", role_id)
        for idx, role_id in enumerate(cfg.get_int_list("roles", "whitelisted_deletion_ID_roles"), start=1):
            check_role(f"roles.whitelisted_deletion_ID_roles[{idx}]", role_id)
        for key in ("has_requested_role_id", "request_banned_role_id"):
            check_role(f"level_requests.{key}", cfg.get_int("level_requests", key))
        for idx, role_id in enumerate(cfg.get_int_list("level_requests", "required_role_ids"), start=1):
            check_role(f"level_requests.required_role_ids[{idx}]", role_id)
        for idx, role_id in enumerate(self._request_reviewer_role_ids(), start=1):
            check_role(f"level_requests.reviewer_role_ids[{idx}]", role_id)
        ok_count += self._validate_request_templates(issues)

        def check_hhmm(label: str, value: object) -> None:
            nonlocal ok_count
            match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value or "").strip())
            if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
                issues.append(f"{label}: use a 24-hour HH:MM value")
            else:
                ok_count += 1

        check_hhmm("background.daily_summary.time", cfg.get("background", "daily_summary", "time", default=""))
        check_hhmm("background.weekly_recap.time", cfg.get("background", "weekly_recap", "time", default=""))

        try:
            session_timeout = int(cfg.get("help", "session_timeout_seconds", default=3600) or 3600)
            if not 60 <= session_timeout <= 86400:
                raise ValueError
            ok_count += 1
        except (TypeError, ValueError):
            issues.append("help.session_timeout_seconds: must be between 60 and 86400")

        validation_cfg = cfg.get("level_requests", "level_validation", default={}) or {}
        if not isinstance(validation_cfg, dict):
            issues.append("level_requests.level_validation: must be an object")
        else:
            providers = validation_cfg.get("providers", {}) or {}
            enabled_providers = (
                [name for name in ("gdbrowser", "boomlings") if bool(providers.get(name))]
                if isinstance(providers, dict)
                else []
            )
            if bool(validation_cfg.get("enabled", True)) and not enabled_providers:
                issues.append("level_requests.level_validation.providers: enable gdbrowser, boomlings, or both")
            else:
                ok_count += len(enabled_providers)
            numeric_limits = {
                "cache_seconds": (60, 86400),
                "request_timeout_seconds": (2, 20),
                "per_user_cooldown_seconds": (0, 3600),
                "per_user_window_seconds": (10, 86400),
                "per_user_max_checks": (1, 100),
                "provider_failure_threshold": (1, 100),
                "provider_circuit_breaker_seconds": (30, 86400),
            }
            for key, (minimum, maximum) in numeric_limits.items():
                try:
                    value = int(validation_cfg.get(key))
                    if not minimum <= value <= maximum:
                        raise ValueError
                    ok_count += 1
                except (TypeError, ValueError):
                    issues.append(f"level_requests.level_validation.{key}: must be {minimum}-{maximum}")
            intervals = validation_cfg.get("provider_min_interval_seconds", {}) or {}
            if not isinstance(intervals, dict):
                issues.append("level_requests.level_validation.provider_min_interval_seconds: must be an object")
            else:
                for provider in ("gdbrowser", "boomlings"):
                    try:
                        interval = float(intervals.get(provider))
                        if not 0 <= interval <= 10:
                            raise ValueError
                        ok_count += 1
                    except (TypeError, ValueError):
                        issues.append(
                            f"level_requests.level_validation.provider_min_interval_seconds.{provider}: must be 0-10"
                        )

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
        for idx, url in enumerate(server_icon_urls, start=1):
            warning = server_icon_url_warning(url)
            if warning:
                issues.append(f"background.server_icon_rotation.urls[{idx}]: {warning}")
        if int(server_icon_cfg.get("interval_seconds", 0) or 0) < 300:
            issues.append("background.server_icon_rotation.interval_seconds: must be at least 300")
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
                    mode = str(entry.get("required_word_match_mode") or "contains").strip().casefold()
                    if mode not in {"contains", "whole_word", "regex"}:
                        issues.append(
                            f"forum_first_message.entries[{idx}].required_word_match_mode: use contains, whole_word, or regex"
                        )
                    else:
                        ok_count += 1
                    required_word = str(entry.get("required_word") or "").strip()
                    if mode == "regex" and required_word:
                        sticky = self.bot.get_cog("StickyCog")
                        safe_check = getattr(sticky, "_required_regex_is_safe", None)
                        if not callable(safe_check) or not safe_check(required_word):
                            issues.append(f"forum_first_message.entries[{idx}].required_word: unsafe regex")
                        else:
                            ok_count += 1
                    try:
                        delay = float(entry.get("required_word_delete_delay_seconds", 10))
                        if not 0 <= delay <= 86400:
                            raise ValueError
                        ok_count += 1
                    except (TypeError, ValueError):
                        issues.append(
                            f"forum_first_message.entries[{idx}].required_word_delete_delay_seconds: must be 0-86400"
                        )
                else:
                    issues.append(f"forum_first_message.entries[{idx}]: must be an object")
        else:
            issues.append("forum_first_message.entries: must be a list")

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
        await self._defer(ctx, ephemeral=True)
        if not await self._is_request_staff_ctx(ctx):
            return await ctx.respond("Only request reviewers can use this.", ephemeral=True)

        message_id_int = self._parse_snowflake_arg(message_id)
        user_id_int = self._parse_snowflake_arg(user_id)

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
        await self._defer(ctx, ephemeral=True)
        if not await self._is_admin_ctx(ctx):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

        cog = self.bot.get_cog("RequestLevelsCog")
        if cog is None or not hasattr(cog, "repair_request_system"):
            return await ctx.respond("Request system cog not loaded.", ephemeral=True)

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
        embed.add_field(name="Weekly recreated", value=str(result.get("weekly_pending_messages_recreated", 0)), inline=True)
        embed.add_field(name="Weekly locked", value=str(result.get("weekly_reviewed_messages_locked", 0)), inline=True)
        embed.add_field(name="Validation refreshed", value=str(result.get("stale_validations_refreshed", 0)), inline=True)
        embed.add_field(
            name="Wave count",
            value="reconciled" if result.get("state_count_reconciled") else "already correct",
            inline=True,
        )
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
        await self._defer(ctx, ephemeral=True)
        if not await self._is_request_staff_ctx(ctx):
            return await ctx.respond("Only request reviewers can use this.", ephemeral=True)

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
                # Every clause is selected from the fixed list above; values remain bound parameters.
                "SELECT wave_id, user_id, request_message_id, data_json, status, created_ts FROM level_request_submissions "  # nosec
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
                # Every clause is selected from the fixed list above; values remain bound parameters.
                "SELECT week_start, user_id, request_message_id, channel_id, data_json, status, created_ts FROM weekly_request_reviews "  # nosec
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
        raw = await tracking.get_top(ctx.guild.id, ws, limit=500)

        if not raw:
            return await self._send(ctx, "No activity tracked yet this week.")

        excluded_role_ids = set(self.bot.config.get_int_list("roles", "excluded_tracking_role_id", default=[]))

        top = []
        for uid, cnt in raw:
            member = ctx.guild.get_member(uid)
            if member is not None and member.bot:
                continue
            if member is not None and excluded_role_ids and any(r.id in excluded_role_ids for r in member.roles):
                continue
            top.append((uid, cnt))
            if len(top) >= 20:
                break

        if not top:
            return await self._send(ctx, "No eligible members tracked yet this week.")

        streak_rows = await self.bot.db.fetchall(
            "SELECT user_id, streak FROM weekly_streaks WHERE guild_id=? AND streak>1",
            (ctx.guild.id,),
        )
        streaks = {int(row["user_id"]): int(row["streak"]) for row in streak_rows}
        streak_emoji = str(self.bot.config.get("tracking", "streak_emoji", default="🔥") or "🔥")
        lines = []
        for i, (uid, cnt) in enumerate(top, start=1):
            streak = streaks.get(int(uid), 0)
            streak_text = f" {streak_emoji}{streak}" if streak > 1 else ""
            lines.append(f"**#{i:02d}** <@{uid}> - **{cnt}** messages{streak_text}")

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

        embed.set_footer(text="Weekly tracking")
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
        streak_row = await self.bot.db.fetchone(
            "SELECT streak, best_streak FROM weekly_streaks WHERE guild_id=? AND user_id=?",
            (ctx.guild.id, ctx.user.id),
        )
        if streak_row and int(streak_row["streak"] or 0) > 1:
            streak_emoji = str(self.bot.config.get("tracking", "streak_emoji", default="🔥") or "🔥")
            embed.add_field(
                name="Top 5 Streak",
                value=f"{streak_emoji} **{int(streak_row['streak'])}** weeks\nBest: **{int(streak_row['best_streak'] or 0)}**",
                inline=True,
            )
        embed.add_field(name="Status", value="Eligible for weekly tracking", inline=False)
        try:
            member = await self._resolve_member(ctx.guild, ctx.user)
            avatar = getattr(member or ctx.user, "display_avatar", None)
            if avatar:
                embed.set_thumbnail(url=avatar.url)
        except Exception:
            pass
        embed.set_footer(text="Weekly tracking")
        await self._send(ctx, embed=embed, ephemeral=True)

    async def tracking_force_dm(
        self,
        ctx: discord.ApplicationContext,
        member: discord.Option(discord.Member, "Member who should receive the weekly request DM"),
    ):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)

        await self._defer(ctx, ephemeral=True)
        admin_roles = self.bot.config.get_int_list("roles", "admin_owner_role_ids")
        invoker = await self._resolve_member(ctx.guild, ctx.user)
        if invoker is None or not is_admin_or_owner(invoker, admin_roles):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

        tracking = self.bot.get_cog("TrackingCog")
        if tracking is None:
            return await ctx.respond("Tracking cog not loaded.", ephemeral=True)

        ws = week_start_sunday(now_madrid()).isoformat()
        ok, msg = await tracking.force_dm_for_user(ctx.guild, ws, member.id)
        await self._log_admin_action(ctx.guild, ctx.user.id, "tracking_force_dm", f"target_user={member.id} ok={ok}")
        await self._send(ctx, msg, ephemeral=True)

    async def tracking_reset(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)

        await self._defer(ctx, ephemeral=True)
        member = await self._resolve_member(ctx.guild, ctx.user)
        admin_roles = self.bot.config.get_int_list("roles", "admin_owner_role_ids")
        if member is None or not is_admin_or_owner(member, admin_roles):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

        tracking = self.bot.get_cog("TrackingCog")
        if tracking is None:
            return await ctx.respond("Tracking cog not loaded.", ephemeral=True)

        await tracking.reset_current_week(ctx.guild.id)
        await self._log_admin_action(ctx.guild, ctx.user.id, "tracking_reset", "current_week=true")
        await self._send(ctx, "Tracking stats for the current week have been reset.", ephemeral=True)

    async def tracking_disable_reward(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)

        await self._defer(ctx, ephemeral=True)
        member = await self._resolve_member(ctx.guild, ctx.user)
        admin_roles = self.bot.config.get_int_list("roles", "admin_owner_role_ids")
        if member is None or not is_admin_or_owner(member, admin_roles):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

        tracking = self.bot.get_cog("TrackingCog")
        if tracking is None:
            return await ctx.respond("Tracking cog not loaded.", ephemeral=True)

        week_start_iso = await tracking.disable_weekly_reward_for_current_week(ctx.guild, ctx.user.id)
        await self._log_admin_action(ctx.guild, ctx.user.id, "tracking_weekly_reward_disabled", f"week_start={week_start_iso}")
        await self._send(
            ctx,
            f"Weekly request reward disabled for the current tracking week starting **{week_start_iso}**.",
            ephemeral=True,
        )

    async def tracking_enable_reward(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)

        await self._defer(ctx, ephemeral=True)
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
        await self._send(ctx, msg, ephemeral=True)

    # --- /ticket close ---
    async def ticket_close(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)

        await self._defer(ctx, ephemeral=True)
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

    async def ticket_status(
        self,
        ctx: discord.ApplicationContext,
        status: discord.Option(str, "Status to set: waiting_user, waiting_staff, or resolved"),
    ):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)

        await self._defer(ctx, ephemeral=False)
        member = await self._resolve_member(ctx.guild, ctx.user)
        mod_role_id = self.bot.config.get_int("roles", "MOD_ROLE_ID") or 0
        allow_manage_guild = bool(self.bot.config.get("permissions", "manage_guild_counts_as_mod", default=True))
        if member is None or not is_mod(member, mod_role_id, allow_manage_guild=allow_manage_guild):
            return await ctx.respond("Only mods can update tickets.", ephemeral=True)

        status_key = _ticket_status_key(status)
        if not status_key:
            return await ctx.respond("Use `waiting_user`, `waiting_staff`, or `resolved`.", ephemeral=True)

        row = await self.bot.db.fetchone(
            "SELECT ticket_id, creator_id, status FROM tickets WHERE channel_id=? AND status IN ('open','closing_prompted')",
            (ctx.channel_id,),
        )
        if not row:
            return await ctx.respond("This isn't an active ticket channel.", ephemeral=True)

        await self.bot.db.execute(
            "UPDATE tickets SET status='open', status_tag=? WHERE channel_id=?",
            (status_key, ctx.channel_id),
        )
        helpcog = self.bot.get_cog("HelpCog")
        update_status = getattr(helpcog, "update_ticket_opening_status", None) if helpcog else None
        if callable(update_status):
            await update_status(ctx.guild, ctx.channel_id, status_key)
        ticket_label = f"`T{int(row['ticket_id'])}`" if row["ticket_id"] is not None else "this ticket"
        embed = discord.Embed(
            title="Ticket Status Updated",
            description=f"{ticket_label} is now **{_ticket_status_label(status_key)}**.",
            color=discord.Color.blurple() if status_key != "resolved" else discord.Color.green(),
            timestamp=now_madrid(),
        )
        if row["creator_id"] is not None:
            embed.add_field(name="User", value=f"<@{int(row['creator_id'])}>", inline=True)
        embed.add_field(name="Updated by", value=ctx.user.mention, inline=True)
        await ctx.respond(embed=embed, allowed_mentions=no_mentions())
        await self._log_admin_action(ctx.guild, ctx.user.id, "ticket_status_updated", f"channel={ctx.channel_id} status={status_key}")

    async def ticket_transcripts(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Option(discord.Member, "User whose ticket transcripts to search", required=False, default=None),
        ticket_id: discord.Option(int, "Ticket ID number, for example 21 for T21", required=False, default=0),
    ):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)

        await self._defer(ctx, ephemeral=True)
        member = await self._resolve_member(ctx.guild, ctx.user)
        mod_role_id = self.bot.config.get_int("roles", "MOD_ROLE_ID") or 0
        allow_manage_guild = bool(self.bot.config.get("permissions", "manage_guild_counts_as_mod", default=True))
        if member is None or not is_mod(member, mod_role_id, allow_manage_guild=allow_manage_guild):
            return await ctx.respond("Only mods can search transcripts.", ephemeral=True)

        if not user and not ticket_id:
            return await ctx.respond("Provide a user or a ticket ID.", ephemeral=True)

        params: list = [ctx.guild.id]
        where = ["t.guild_id=?"]
        if ticket_id:
            where.append("t.ticket_id=?")
            params.append(int(ticket_id))
        if user:
            where.append("t.creator_id=?")
            params.append(int(user.id))

        rows = await self.bot.db.fetchall(
            # Optional clauses are fixed locally; user values remain bound parameters.
            "SELECT t.ticket_id, t.channel_id, t.creator_id, t.created_ts, t.closed_ts, t.status, t.status_tag, "  # nosec
            "tt.log_channel_id, tt.log_message_id, tt.created_ts AS transcript_ts "
            "FROM tickets t "
            "LEFT JOIN ticket_transcripts tt ON tt.guild_id=t.guild_id AND tt.ticket_id=t.ticket_id "
            f"WHERE {' AND '.join(where)} ORDER BY COALESCE(t.closed_ts, t.created_ts) DESC LIMIT 10",
            tuple(params),
        )

        title = "Transcript Search"
        if ticket_id:
            title += f" T{int(ticket_id)}"
        elif user:
            title += f" - {user.display_name}"
        embed = discord.Embed(title=title, color=discord.Color.blurple(), timestamp=now_madrid())
        if not rows:
            embed.description = "No matching tickets were found."
        for row in rows:
            tid = int(row["ticket_id"]) if row["ticket_id"] is not None else 0
            label = f"T{tid}" if tid else str(row["channel_id"])
            creator = f"<@{int(row['creator_id'])}>" if row["creator_id"] is not None else "Unknown"
            created = f"<t:{int(row['created_ts'])}:R>" if row["created_ts"] is not None else "Unknown"
            status = _ticket_status_label(row["status_tag"])
            transcript = "Not indexed yet"
            if row["log_channel_id"] and row["log_message_id"]:
                url = f"https://discord.com/channels/{ctx.guild.id}/{int(row['log_channel_id'])}/{int(row['log_message_id'])}"
                transcript = f"[Open transcript]({url})"
            value = f"User: {creator}\nCreated: {created}\nStatus: **{status}**\nTranscript: {transcript}"
            embed.add_field(name=label, value=value[:1024], inline=False)
        await ctx.respond(embed=embed, ephemeral=True, allowed_mentions=no_mentions())

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
            for key in (
                "required_word",
                "missing_required_word_dm",
                "required_word_dm_message",
                "required_word_delete_delay_seconds",
                "required_word_match_mode",
            ):
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

        await self._defer(ctx, ephemeral=True)
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

        mode = str(match_mode or "").strip().casefold()
        if mode:
            if mode not in {"contains", "whole_word", "regex"}:
                return await ctx.respond("Match mode must be `contains`, `whole_word`, or `regex`.", ephemeral=True)
        effective_mode = mode or str(entry.get("required_word_match_mode") or "contains")
        if effective_mode == "regex":
            sticky = self.bot.get_cog("StickyCog")
            safe_check = getattr(sticky, "_required_regex_is_safe", None)
            if not callable(safe_check) or not safe_check(new_word):
                return await ctx.respond(
                    "That regex is too complex for safe forum matching. Use literals, character classes, anchors, or a simpler pattern.",
                    ephemeral=True,
                )

        async with self._forum_config_lock:
            had_word = "required_word" in entry
            old_word = entry.get("required_word")
            had_mode = "required_word_match_mode" in entry
            old_mode = entry.get("required_word_match_mode")
            entry["required_word"] = new_word
            if mode:
                entry["required_word_match_mode"] = mode
            try:
                await persist_forum_required_rules(self.bot)
                try:
                    self.bot.config.save()
                except Exception as local_error:
                    await log_error(
                        self.bot,
                        f"Forum required word persisted remotely but local config save failed: {repr(local_error)}",
                    )
            except Exception as e:
                if had_word:
                    entry["required_word"] = old_word
                else:
                    entry.pop("required_word", None)
                if had_mode:
                    entry["required_word_match_mode"] = old_mode
                else:
                    entry.pop("required_word_match_mode", None)
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

        await self._defer(ctx, ephemeral=True)
        member = await self._resolve_member(ctx.guild, ctx.user)
        admin_roles = self.bot.config.get_int_list("roles", "admin_owner_role_ids")
        if member is None or not is_admin_or_owner(member, admin_roles):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

        self.bot.config.reload()
        try:
            await load_runtime_config_overrides(self.bot)
        except Exception as e:
            await log_error(self.bot, f"Runtime config override reload failed: {repr(e)}")

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

        await self._defer(ctx, ephemeral=True)
        member = await self._resolve_member(ctx.guild, ctx.user)
        admin_roles = self.bot.config.get_int_list("roles", "admin_owner_role_ids")
        if member is None or not is_admin_or_owner(member, admin_roles):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

        await ctx.respond("Restarting...", ephemeral=True)
        await self._log_admin_action(ctx.guild, ctx.user.id, "bot_restart", "manual restart command")
        # bot.close is wrapped in main.py and flushes tracking, daily metrics,
        # remote replication, and the database exactly once.
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

                        await interaction.response.defer()
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
