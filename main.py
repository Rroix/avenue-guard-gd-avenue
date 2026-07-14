from __future__ import annotations

import asyncio
import os
import re
import time
import traceback
from pathlib import Path
import discord

from utils.config import Config
from utils.db import Database
from utils.keepalive import get_keepalive_status, set_keepalive_status, start_keepalive, start_keepalive_thread
from utils.errors import setup_global_error_handlers, log_error
from utils.views import (
    TrackingDeclineConfirmView,
    TicketClosePromptView,
    HelpMenuView,
    TranscriptRequestView,
    LevelRequestButtonView,
    LevelRequestReviewView,
)
from utils.runtime_config import load_runtime_config_overrides

DEFAULT_DB_PATH = "data/bot.db"
TURSO_REPLICA_PATH = "data/turso-replica.db"
RENDER_DISK_DB_PATH = "/var/data/avenue-guard/bot.db"
DEFAULT_DISCORD_LOGIN_RETRY_SECONDS = 15 * 60
DEFAULT_STARTUP_ERROR_RETRY_SECONDS = 5 * 60


class PersistenceConfigurationError(RuntimeError):
    """Raised when a configured durable database would silently degrade."""


def startup_log(message: str) -> None:
    print(f"[Avenue Guard startup] {message}", flush=True)


def _discord_login_retry_seconds() -> int:
    raw = os.getenv("DISCORD_LOGIN_RETRY_SECONDS", "").strip()
    if not raw:
        return DEFAULT_DISCORD_LOGIN_RETRY_SECONDS
    try:
        return max(60, int(raw))
    except Exception:
        return DEFAULT_DISCORD_LOGIN_RETRY_SECONDS


def _startup_error_retry_seconds() -> int:
    raw = os.getenv("STARTUP_ERROR_RETRY_SECONDS", "").strip()
    if not raw:
        return DEFAULT_STARTUP_ERROR_RETRY_SECONDS
    try:
        return max(60, int(raw))
    except Exception:
        return DEFAULT_STARTUP_ERROR_RETRY_SECONDS


def _prepare_fresh_event_loop() -> None:
    asyncio.set_event_loop(asyncio.new_event_loop())


def _compact_startup_exception(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    lower = text.casefold()
    if "<html" in lower or "cloudflare" in lower or "error 1015" in lower:
        ray_match = re.search(r"Cloudflare Ray ID:\s*<[^>]+>\s*([^<\s]+)", text, flags=re.I)
        if ray_match is None:
            ray_match = re.search(r"ray id[:\s]+([A-Za-z0-9_-]+)", text, flags=re.I)
        ray = f" Cloudflare Ray ID: {ray_match.group(1)}." if ray_match else ""
        return f"{type(exc).__name__}: Discord/Cloudflare rate limited startup login; full HTML omitted.{ray}"
    if len(text) > 2400:
        return text[:2400] + "\n...truncated..."
    return text


def _is_discord_startup_rate_limit(exc: Exception) -> bool:
    if not isinstance(exc, discord.HTTPException):
        return False
    text = str(exc).casefold()
    status = int(getattr(exc, "status", 0) or 0)
    return status == 429 or "error 1015" in text or "you are being rate limited" in text or "too many requests" in text


def _run_preflight_database_check(bot: discord.Bot) -> None:
    """Connect and migrate storage before Discord login advertises the bot online."""
    loop = asyncio.get_event_loop()
    loop.run_until_complete(bot.db.connect())


async def _close_runtime_storage(bot: discord.Bot) -> None:
    """Best-effort flush on Discord's event loop before it is torn down."""
    tracking = bot.get_cog("TrackingCog")
    flush_activity = getattr(tracking, "flush_activity_counts", None)
    if callable(flush_activity):
        try:
            await flush_activity()
        except Exception as e:
            startup_log(f"Activity flush during shutdown failed: {type(e).__name__}: {e}")

    background = bot.get_cog("BackgroundCog")
    persist_daily = getattr(background, "_persist_current_day", None)
    if callable(persist_daily):
        try:
            await persist_daily()
        except Exception as e:
            startup_log(f"Daily stats flush during shutdown failed: {type(e).__name__}: {e}")

    try:
        await bot.db.sync_remote()
    except Exception as e:
        startup_log(f"Final database sync failed: {type(e).__name__}: {e}")
    finally:
        await bot.db.close()


def _install_storage_close_hook(bot: discord.Bot) -> None:
    original_close = bot.close
    bot._runtime_storage_closed = False

    async def close_with_storage_flush() -> None:
        try:
            if not bot._runtime_storage_closed:
                bot._runtime_storage_closed = True
                await _close_runtime_storage(bot)
        finally:
            await original_close()

    bot.close = close_with_storage_flush


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
    require_remote = bool(config.get("database", "require_remote_when_configured", default=True))
    allow_local_fallback = os.getenv("ALLOW_LOCAL_DATABASE_FALLBACK", "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }
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
            message = f"Turso replica path is not writable: {replica_path} ({error})"
            if require_remote and not allow_local_fallback:
                raise PersistenceConfigurationError(
                    f"{message}. Refusing to start on disposable local storage. Fix TURSO_REPLICA_PATH or set "
                    "ALLOW_LOCAL_DATABASE_FALLBACK=1 for intentional local development."
                )
            warnings.append(f"{message}; falling back to local SQLite")
        else:
            message = "A Turso/libSQL database URL is set but TURSO_AUTH_TOKEN is missing"
            if require_remote and not allow_local_fallback:
                raise PersistenceConfigurationError(
                    f"{message}. Refusing to start on disposable local storage. Add the database token or set "
                    "ALLOW_LOCAL_DATABASE_FALLBACK=1 for intentional local development."
                )
            warnings.append(f"{message}; falling back to local SQLite")

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
    _install_storage_close_hook(bot)

    setup_global_error_handlers(bot)

    def _load_cogs():
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
            set_keepalive_status("startup_error", f"Database setup failed: {type(e).__name__}")
            await log_error(bot, f"Database setup failed on startup: {repr(e)}")
            await bot.close()
            return

        try:
            await load_runtime_config_overrides(bot)
            for cog in bot.cogs.values():
                reload_hook = getattr(cog, "on_config_reload", None)
                if callable(reload_hook):
                    reload_hook()
        except Exception as e:
            await log_error(bot, f"Runtime config override load failed: {repr(e)}")

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
                    set_keepalive_status("startup_error", "Allowed guild check failed")
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
        for cog_name in ("TrackingCog", "HelpCog", "RequestLevelsCog", "BackgroundCog"):
            cog = bot.get_cog(cog_name)
            start = getattr(cog, "start_background", None)
            if not callable(start):
                continue
            try:
                await start()
            except Exception as e:
                await log_error(bot, f"{cog_name} background startup failed: {repr(e)}")

        # Register persistent views (for interactions to survive restarts)
        if not getattr(bot, "_persistent_views_registered", False):
            await bot.register_persistent_views()
            bot._persistent_views_registered = True

        set_keepalive_status("online", f"Logged in as {bot.user}")
        startup_log(f"Logged in as {bot.user} (ID: {bot.user.id})")

    async def register_persistent_views():
        bot.add_view(TrackingDeclineConfirmView())
        bot.add_view(TicketClosePromptView())
        bot.add_view(HelpMenuView())
        bot.add_view(TranscriptRequestView())
        bot.add_view(LevelRequestButtonView())
        bot.add_view(LevelRequestReviewView())

    bot.register_persistent_views = register_persistent_views

    try:
        _load_cogs()
    except Exception as e:
        set_keepalive_status("startup_error", f"Cog load failed: {type(e).__name__}")
        startup_log(f"Cog load failed: {repr(e)}\n{traceback.format_exc()}")
        raise

    return bot

def run_bot_with_startup_backoff(token: str) -> None:
    set_keepalive_status("starting", "Starting health server")
    start_keepalive_thread()
    while True:
        _prepare_fresh_event_loop()
        set_keepalive_status("discord_login", "Attempting Discord login")
        try:
            bot = create_bot()
        except PersistenceConfigurationError as exc:
            seconds = _startup_error_retry_seconds()
            next_retry_ts = int(time.time()) + seconds
            set_keepalive_status(
                "startup_error",
                "Persistent database configuration is incomplete",
                retry_after_seconds=seconds,
                next_retry_ts=next_retry_ts,
            )
            startup_log(f"{exc} Waiting {seconds} seconds before retrying.")
            time.sleep(seconds)
            continue
        try:
            set_keepalive_status("database_check", "Checking database before Discord login")
            _run_preflight_database_check(bot)
        except Exception as exc:
            try:
                asyncio.get_event_loop().run_until_complete(bot.db.close())
            except Exception:
                pass
            seconds = _startup_error_retry_seconds()
            next_retry_ts = int(time.time()) + seconds
            detail = f"Database setup failed before Discord login: {type(exc).__name__}"
            set_keepalive_status(
                "startup_error",
                detail,
                retry_after_seconds=seconds,
                next_retry_ts=next_retry_ts,
            )
            startup_log(f"{detail}: {repr(exc)}. Waiting {seconds} seconds before retrying.")
            time.sleep(seconds)
            continue
        set_keepalive_status("discord_login", "Database ready; attempting Discord login")
        try:
            bot.run(token)
            set_keepalive_status("stopped", "Discord client stopped")
            return
        except discord.LoginFailure:
            set_keepalive_status("fatal_login_error", "Discord token login failed")
            startup_log("Discord login failed. Check DISCORD_TOKEN; this is not retryable.")
            raise
        except Exception as exc:
            if _is_discord_startup_rate_limit(exc):
                seconds = _discord_login_retry_seconds()
                next_retry_ts = int(time.time()) + seconds
                set_keepalive_status(
                    "waiting_rate_limit",
                    "Discord/Cloudflare rate limited startup login",
                    retry_after_seconds=seconds,
                    next_retry_ts=next_retry_ts,
                )
                startup_log(
                    f"{_compact_startup_exception(exc)} Waiting {seconds} seconds before retrying so Render does not amplify the rate limit."
                )
                time.sleep(seconds)
                continue
            if isinstance(exc, RuntimeError) and "event loop is closed" in str(exc).casefold():
                set_keepalive_status("discord_login", "Resetting closed event loop before retry")
                startup_log("Discord client left a closed event loop after a failed startup; resetting loop and retrying.")
                time.sleep(2)
                continue
            if isinstance(exc, RuntimeError) and "session is closed" in str(exc).casefold():
                status = get_keepalive_status()
                if str(status.get("state")) == "startup_error":
                    seconds = _startup_error_retry_seconds()
                    next_retry_ts = int(time.time()) + seconds
                    set_keepalive_status(
                        "startup_error",
                        str(status.get("detail") or "Startup failed before Discord became ready"),
                        retry_after_seconds=seconds,
                        next_retry_ts=next_retry_ts,
                    )
                    startup_log(
                        f"Discord session closed because startup failed: {status.get('detail')}. Waiting {seconds} seconds before retrying."
                    )
                    time.sleep(seconds)
                    continue
            set_keepalive_status("crashed", f"{type(exc).__name__}: {exc}")
            startup_log(f"Bot crashed during run:\n{traceback.format_exc()}")
            raise


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        startup_log("DISCORD_TOKEN environment variable is missing.")
        raise SystemExit("DISCORD_TOKEN environment variable is missing.")
    run_bot_with_startup_backoff(token)
