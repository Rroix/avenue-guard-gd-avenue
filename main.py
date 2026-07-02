from __future__ import annotations

import os
import traceback
from pathlib import Path
import discord

from utils.config import Config
from utils.db import Database
from utils.keepalive import start_keepalive
from utils.errors import setup_global_error_handlers, log_error
from utils.views import (
    TrackingDeclineConfirmView,
    TicketClosePromptView,
    HelpMenuView,
    HelpModConfirmView,
    TranscriptRequestView,
    LevelRequestButtonView,
    LevelRequestReviewView,
)
from utils.checks import ensure_allowed_guild_id

DEFAULT_DB_PATH = "data/bot.db"
TURSO_REPLICA_PATH = "data/turso-replica.db"
RENDER_DISK_DB_PATH = "/var/data/avenue-guard/bot.db"


def startup_log(message: str) -> None:
    print(f"[Avenue Guard startup] {message}", flush=True)


def _database_path_usable(path: str) -> tuple[bool, str]:
    try:
        candidate = Path(path)
        candidate.parent.mkdir(parents=True, exist_ok=True)
        probe = candidate.parent / ".avenue_guard_write_test"
        probe.write_text("ok", encoding="utf-8")
        try:
            probe.unlink()
        except Exception:
            pass
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def resolve_db_path(config: Config) -> tuple[str, str, str, str, str]:
    warnings: list[str] = []
    turso_url = (
        os.getenv("TURSO_DATABASE_URL", "")
        or os.getenv("LIBSQL_URL", "")
        or str(config.get("database", "turso_url", default="") or "")
    ).strip()
    turso_token = (os.getenv("TURSO_AUTH_TOKEN", "") or os.getenv("LIBSQL_AUTH_TOKEN", "")).strip()
    if turso_url:
        if turso_token:
            replica_path = (
                os.getenv("TURSO_REPLICA_PATH", "").strip()
                or str(config.get("database", "turso_replica_path", default="") or "").strip()
                or TURSO_REPLICA_PATH
            )
            ok, error = _database_path_usable(replica_path)
            if ok:
                return replica_path, "Turso/libSQL embedded replica", "", turso_url, turso_token
            warnings.append(
                f"Turso replica path is not writable: {replica_path} ({error}); falling back to local SQLite"
            )
        else:
            warnings.append("A Turso/libSQL database URL is set but TURSO_AUTH_TOKEN is missing; falling back to local SQLite")

    env_path = os.getenv("AVENUE_GUARD_DB_PATH", "").strip()
    candidates: list[tuple[str, str, bool]] = []
    if env_path:
        candidates.append(("AVENUE_GUARD_DB_PATH", env_path, True))
    config_path = str(config.get("database", "path", default="") or "").strip()
    if config_path:
        candidates.append(("config.json database.path", config_path, False))
    candidates.append(("Render Persistent Disk auto-detect", RENDER_DISK_DB_PATH, False))
    candidates.append(("local fallback", DEFAULT_DB_PATH, False))

    for source, path, explicit in candidates:
        ok, error = _database_path_usable(path)
        if ok:
            warning = " | ".join(warnings)
            if warning:
                startup_log(warning)
            if source == "local fallback":
                warning = (
                    f"{warning} | " if warning else ""
                ) + "Using local fallback database; data can be lost if Render clears cache and no Persistent Disk is mounted."
            return path, source, warning, "", ""
        message = f"Database path from {source} is not writable: {path} ({error})"
        if explicit:
            message += "; falling back so the bot can start"
        warnings.append(message)

    return DEFAULT_DB_PATH, "local fallback", "All configured database paths failed; using local fallback.", "", ""

def create_bot() -> discord.Bot:
    intents = discord.Intents.default()
    for intent_name in (
        "bans",
        "dm_messages",
        "guild_messages",
        "guild_reactions",
        "members",
        "message_content",
        "messages",
        "moderation",
        "presences",
        "reactions",
        "voice_states",
    ):
        if hasattr(intents, intent_name):
            setattr(intents, intent_name, True)
    bot = discord.Bot(intents=intents)

    bot.config = Config("config.json")
    bot.db_path, bot.db_path_source, bot.db_path_warning, bot.db_remote_url, bot.db_remote_token = resolve_db_path(bot.config)
    startup_log(f"Using database path: {bot.db_path} ({bot.db_path_source})")
    bot.db = Database(bot.db_path, remote_url=bot.db_remote_url, auth_token=bot.db_remote_token)

    setup_global_error_handlers(bot)

    async def _load_cogs():
        bot.load_extension("cogs.Mod")
        bot.load_extension("cogs.Tracking")
        bot.load_extension("cogs.Help")
        bot.load_extension("cogs.MessageResponses")
        bot.load_extension("cogs.Sticky")
        bot.load_extension("cogs.RequestLevels")
        bot.load_extension("cogs.Commands")
        bot.load_extension("cogs.Background")

    @bot.event
    async def on_ready():
        try:
            await bot.db.connect()
        except Exception as e:
            await log_error(bot, f"Database setup failed on startup: {repr(e)}")
            await bot.close()
            return

        # Ensure only in allowed guild
        allowed = bot.config.get_int("guild", "allowed_guild_id")
        if allowed:
            g = bot.get_guild(allowed)
            if g is None:
                try:
                    g = await bot.fetch_guild(allowed)
                    startup_log(f"Allowed guild {allowed} was not cached, but fetch succeeded.")
                except Exception as e:
                    message = f"Bot is not in allowed guild_id={allowed}, or cannot fetch it: {type(e).__name__}: {e}. Shutting down."
                    startup_log(message)
                    await log_error(bot, message)
                    await bot.close()
                    return

        # Start keepalive server
        try:
            # start once
            if not getattr(bot, "_keepalive_started", False):
                bot._keepalive_started = True
                bot.loop.create_task(start_keepalive())
        except Exception:
            pass

        # Start background tasks in cogs
        tracking = bot.get_cog("TrackingCog")
        if tracking:
            await tracking.start_background()

        helpcog = bot.get_cog("HelpCog")
        if helpcog:
            await helpcog.start_background()

        requestcog = bot.get_cog("RequestLevelsCog")
        if requestcog:
            await requestcog.start_background()
        
        bgcog = bot.get_cog("BackgroundCog")
        if bgcog:
            await bgcog.start_background()

        # Register persistent views (for interactions to survive restarts)
        await bot.register_persistent_views()

        startup_log(f"Logged in as {bot.user} (ID: {bot.user.id})")

    async def register_persistent_views():
        # It's okay to add multiple times; discord.py ignores duplicates by custom_id mapping.
        bot.add_view(TrackingDeclineConfirmView())
        bot.add_view(TicketClosePromptView())
        bot.add_view(HelpMenuView())
        bot.add_view(HelpModConfirmView())
        bot.add_view(TranscriptRequestView())
        bot.add_view(LevelRequestButtonView())
        bot.add_view(LevelRequestReviewView())

    bot.register_persistent_views = register_persistent_views

    async def _load_cogs_logged():
        try:
            await _load_cogs()
        except Exception as e:
            details = f"Cog load failed: {repr(e)}\n{traceback.format_exc()}"
            startup_log(details)
            try:
                await log_error(bot, details)
            except Exception:
                pass
            await bot.close()

    bot.loop.create_task(_load_cogs_logged())
    return bot

if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        startup_log("DISCORD_TOKEN environment variable is missing.")
        raise SystemExit("DISCORD_TOKEN environment variable is missing.")
    bot = create_bot()
    try:
        bot.run(token)
    except Exception:
        startup_log(f"Bot crashed during run:\n{traceback.format_exc()}")
        raise
