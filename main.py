from __future__ import annotations

import asyncio
import os
import re
import time
import traceback
from pathlib import Path
import discord

from utils.components_v2 import install_components_v2_adapter
from utils.config import Config
from utils.db import Database, DatabaseBusyError
from utils.keepalive import (
    configure_staff_api,
    get_keepalive_status,
    set_keepalive_status,
    start_keepalive,
    start_keepalive_thread,
)
from utils.libsql_worker import DatabaseWorkerError
from utils.errors import setup_global_error_handlers, log_error
from utils.views import (
    TrackingDeclineConfirmView,
    TicketClosePromptView,
    HelpMenuView,
    BanInfoGiveInfoView,
    TranscriptRequestView,
    ReleaseApprovalView,
    LevelRequestButtonView,
    LevelRequestPPSReviewView,
    LevelRequestReviewView,
    configure_pps_send_type_emojis,
)
from utils.runtime_config import load_runtime_config_overrides
from utils.outbox import DiscordOutbox
from utils.workflows import begin_workflow_context, clear_workflow_context
from utils.supervision import start_cog_background

DEFAULT_DB_PATH = "data/bot.db"
TURSO_REPLICA_PATH = "data/turso-replica.db"
RENDER_DISK_DB_PATH = "/var/data/avenue-guard/bot.db"
DEFAULT_DISCORD_LOGIN_RETRY_SECONDS = 15 * 60
DEFAULT_STARTUP_ERROR_RETRY_SECONDS = 5 * 60
LEGACY_SNOWFLAKE_REPAIR_VERSION = 2


install_components_v2_adapter()


class PersistenceConfigurationError(RuntimeError):
    """Raised when a configured durable database would silently degrade."""


class AvenueBot(discord.Bot):
    async def process_application_commands(self, interaction, auto_sync=None):
        data = interaction.data or {}
        if interaction.type in {discord.InteractionType.application_command, discord.InteractionType.auto_complete}:
            command_id = data.get("id")
            if command_id not in self._application_commands:
                for command in self.application_commands + self.pending_application_commands:
                    guild_ids = command.guild_ids
                    if command.name == data.get("name") and (not guild_ids or interaction.guild_id in guild_ids):
                        self._application_commands[command_id] = command
                        break
        # A missing ID cache must not trigger a REST command-sync round trip
        # before an interaction is acknowledged. Known names are resolved above.
        await super().process_application_commands(interaction, auto_sync=False)


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


def _prepare_fresh_event_loop() -> asyncio.AbstractEventLoop:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


def _close_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Close an unused startup loop without relying on implicit loop lookup."""
    if not loop.is_closed():
        loop.close()
    asyncio.set_event_loop(None)


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


def _run_preflight_database_check(
    bot: discord.Bot,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Connect and migrate storage before Discord login advertises the bot online."""
    loop.run_until_complete(bot.register_persistent_views())
    loop.run_until_complete(bot.db.connect())


async def _gateway_startup_watchdog(bot, *, timeout: float = 300) -> None:
    await asyncio.sleep(timeout)
    if not bot.is_closed() and not getattr(bot, "_runtime_initialized", False):
        set_keepalive_status("startup_error", "Discord runtime initialization exceeded the startup deadline")
        await log_error(bot, "Discord runtime initialization exceeded the startup deadline; closing this session for a clean retry")
        await bot.close()


async def _repair_legacy_turso_snowflakes(bot: discord.Bot, guild: discord.Guild) -> None:
    """Repair historical rounded IDs once without blocking live workflows."""
    if bool(getattr(bot, "_legacy_snowflake_repair_complete", False)):
        return
    saved = await bot.db.get_runtime_setting("maintenance.legacy_snowflake_repair", {})
    if isinstance(saved, dict) and int(saved.get("version", 0) or 0) >= LEGACY_SNOWFLAKE_REPAIR_VERSION:
        bot._legacy_snowflake_repair_complete = True
        bot._last_snowflake_repair = dict(saved)
        return

    # A complete member cache gives the precision repair exact IDs supplied by
    # Discord. This remains best effort when member intent is unavailable.
    if not bool(getattr(guild, "chunked", True)):
        try:
            await asyncio.wait_for(guild.chunk(cache=True), timeout=45)
        except Exception as chunk_error:
            startup_log(
                "Member cache chunk before Turso snowflake repair was unavailable: "
                f"{type(chunk_error).__name__}: {chunk_error}"
            )

    known_users = {
        int(member.id)
        for member in getattr(guild, "members", ())
        if getattr(member, "id", None)
    }
    known_users.update(
        int(user.id)
        for user in getattr(bot, "users", ())
        if getattr(user, "id", None)
    )
    known_users.update(bot.config.get_int_list("impact", "allowed_user_ids"))
    known_users.update(bot.config.get_int_list("release_updates", "owner_user_ids"))
    if bot.user is not None:
        known_users.add(int(bot.user.id))

    known_channels = {
        int(channel.id)
        for channel in (
            *tuple(getattr(guild, "channels", ()) or ()),
            *tuple(getattr(guild, "threads", ()) or ()),
        )
        if getattr(channel, "id", None)
    }
    repair = await bot.db.repair_legacy_snowflake_precision(
        guild_ids=(int(guild.id),),
        user_ids=tuple(known_users),
        channel_ids=tuple(known_channels),
    )
    bot._last_snowflake_repair = {**repair, "ts": int(time.time())}
    await bot.db.set_runtime_setting(
        "maintenance.legacy_snowflake_repair",
        {**bot._last_snowflake_repair, "version": LEGACY_SNOWFLAKE_REPAIR_VERSION},
    )
    bot._legacy_snowflake_repair_complete = True
    if any(
        repair.get(key)
        for key in ("updated", "conflicts", "ambiguous", "feedback_requeued")
    ):
        startup_log(
            "Legacy Turso snowflake repair: "
            f"updated={repair.get('updated', 0)} "
            f"conflicts={repair.get('conflicts', 0)} "
            f"ambiguous={repair.get('ambiguous', 0)} "
            f"feedback_requeued={repair.get('feedback_requeued', 0)}"
        )


async def _legacy_snowflake_repair_loop(
    bot: discord.Bot,
    guild: discord.Guild,
) -> None:
    attempts = 0
    while not bot.is_closed() and not bool(
        getattr(bot, "_legacy_snowflake_repair_complete", False)
    ):
        try:
            await _repair_legacy_turso_snowflakes(bot, guild)
            return
        except asyncio.CancelledError:
            raise
        except (DatabaseBusyError, DatabaseWorkerError) as e:
            attempts += 1
            if attempts in {3, 6}:
                await log_error(
                    bot,
                    "Legacy Turso snowflake repair remains deferred; compatibility "
                    f"lookups are active and the repair will retry: {e!r}",
                )
        except Exception as e:
            attempts += 1
            await log_error(
                bot,
                "Legacy Turso snowflake repair deferred; compatibility lookups "
                f"remain active and the repair will retry: {e!r}",
            )
        await asyncio.sleep(min(300, 5 * (2 ** min(attempts, 5))))


async def _close_runtime_storage(bot: discord.Bot) -> None:
    """Best-effort flush on Discord's event loop before it is torn down."""
    repair_task = getattr(bot, "_legacy_snowflake_repair_task", None)
    if repair_task is not None and not repair_task.done():
        repair_task.cancel()
        await asyncio.gather(repair_task, return_exceptions=True)

    operations = bot.get_cog("OperationsCog")
    close_operations = getattr(operations, "close_resources", None)
    if callable(close_operations):
        try:
            await close_operations()
        except Exception as e:
            startup_log(f"Operations shutdown failed: {type(e).__name__}: {e}")
    pending_tasks = set()
    if operations is not None:
        for _label, cog_name, attribute in operations._external_task_specs():
            cog = bot.get_cog(cog_name)
            value = getattr(cog, attribute, None) if cog else None
            task = value if isinstance(value, asyncio.Task) else getattr(value, "get_task", lambda: None)()
            if task is not None and task is not asyncio.current_task() and not task.done():
                task.cancel()
                pending_tasks.add(task)
    if pending_tasks:
        await asyncio.gather(*pending_tasks, return_exceptions=True)

    requests = bot.get_cog("RequestLevelsCog")
    close_request_resources = getattr(requests, "close_resources", None)
    if callable(close_request_resources):
        try:
            await close_request_resources()
        except Exception as e:
            startup_log(
                f"Request validation session shutdown failed: {type(e).__name__}: {e}"
            )

    priority = bot.get_cog("PrioritySystemCog")
    close_priority = getattr(priority, "close_resources", None)
    if callable(close_priority):
        try:
            await close_priority()
        except Exception as e:
            startup_log(f"Priority maintenance shutdown failed: {type(e).__name__}: {e}")

    release = bot.get_cog("ReleaseCog")
    close_release = getattr(release, "close_resources", None)
    if callable(close_release):
        try:
            await close_release()
        except Exception as e:
            startup_log(f"Release/status tasks shutdown failed: {type(e).__name__}: {e}")
    record_uptime = getattr(release, "record_uptime_transition", None)
    if callable(record_uptime):
        try:
            await record_uptime(
                was_online=str(get_keepalive_status().get("state") or "")
                == "online"
            )
        except Exception as e:
            startup_log(f"Final uptime sample failed: {type(e).__name__}: {e}")

    tracking = bot.get_cog("TrackingCog")
    flush_activity = getattr(tracking, "flush_activity_counts", None)
    if callable(flush_activity):
        try:
            await flush_activity(drain=True)
        except Exception as e:
            startup_log(f"Activity flush during shutdown failed: {type(e).__name__}: {e}")

    background = bot.get_cog("BackgroundCog")
    persist_daily = getattr(background, "_persist_current_day", None)
    if callable(persist_daily):
        try:
            await persist_daily()
        except Exception as e:
            startup_log(f"Daily stats flush during shutdown failed: {type(e).__name__}: {e}")
    close_background_resources = getattr(background, "close_resources", None)
    if callable(close_background_resources):
        try:
            await close_background_resources()
        except Exception as e:
            startup_log(f"Background HTTP session shutdown failed: {type(e).__name__}: {e}")

    try:
        await bot.db.sync_remote()
    except Exception as e:
        startup_log(f"Final database replica refresh failed: {type(e).__name__}: {e}")
    finally:
        reporter = getattr(bot, "_error_reporter", None)
        if reporter is not None:
            await reporter.close()
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
    bot = AvenueBot(intents=intents)
    bot.config_write_lock = asyncio.Lock()
    bot._runtime_initialization_lock = asyncio.Lock()
    bot._runtime_initialized = False

    bot.config = Config("config.json")
    configure_pps_send_type_emojis(
        bot.config.get("priority_system", "send_type_emojis", default={})
    )
    bot.db_path, bot.db_path_source, bot.db_path_warning, bot.db_remote_url, bot.db_remote_token = resolve_db_path(bot.config)
    startup_log(f"Using database path: {bot.db_path} ({bot.db_path_source})")
    bot.db = Database(bot.db_path, remote_url=bot.db_remote_url, auth_token=bot.db_remote_token)
    bot.outbox = DiscordOutbox(bot)
    _install_storage_close_hook(bot)

    @bot.check_once
    async def attach_command_correlation(ctx: discord.ApplicationContext) -> bool:
        command = getattr(ctx, "command", None)
        command_name = str(
            getattr(command, "qualified_name", None)
            or getattr(command, "name", None)
            or "command"
        )
        ctx.correlation_id = begin_workflow_context(prefix=command_name)
        return True

    @bot.event
    async def on_application_command_completion(
        ctx: discord.ApplicationContext,
    ) -> None:
        clear_workflow_context()

    setup_global_error_handlers(bot)

    def _load_cogs():
        bot.load_extension("cogs.Mod")
        bot.load_extension("cogs.Tracking")
        bot.load_extension("cogs.Help")
        bot.load_extension("cogs.MessageResponses")
        bot.load_extension("cogs.Sticky")
        bot.load_extension("cogs.RequestLevels")
        bot.load_extension("cogs.PrioritySystem")
        bot.load_extension("cogs.HistoricalAudit")
        bot.load_extension("cogs.Release")
        bot.load_extension("cogs.Commands")
        bot.load_extension("cogs.Background")
        bot.load_extension("cogs.Operations")

    async def initialize_runtime():
        await bot.register_persistent_views()
        previous_gateway_state = str(
            get_keepalive_status().get("state") or ""
        )
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

            bot._legacy_snowflake_repair_guild = g

        # The keepalive server runs in a native thread. Its private portal bridge
        # schedules every operation back onto this Discord loop so all Turso work
        # continues through the established Database wrapper.
        from services.staff_portal import StaffPortalService

        bot.staff_portal = StaffPortalService(bot)
        configure_staff_api(bot.staff_portal, asyncio.get_running_loop())

        # Start keepalive server
        try:
            # start once
            if not getattr(bot, "_keepalive_started", False):
                bot._keepalive_started = True
                asyncio.create_task(start_keepalive())
        except Exception:
            pass

        # Establish the uptime boundary before background writers and their
        # supervisor compete for storage. Busy writes remain buffered for retry.
        release_cog = bot.get_cog("ReleaseCog")
        record_uptime = getattr(release_cog, "record_uptime_transition", None)
        if previous_gateway_state != "online" and callable(record_uptime):
            try:
                await record_uptime(was_online=False)
            except Exception as e:
                await log_error(bot, f"Uptime boundary setup deferred before background startup: {e!r}")

        # Start background tasks in cogs
        for cog_name in (
            "OperationsCog",
            "TrackingCog",
            "HelpCog",
            "RequestLevelsCog",
            "PrioritySystemCog",
            "HistoricalAuditCog",
            "ReleaseCog",
            "BackgroundCog",
        ):
            cog = bot.get_cog(cog_name)
            start = getattr(cog, "start_background", None)
            if not callable(start):
                continue
            try:
                await start_cog_background(bot, cog_name)
            except Exception as e:
                await log_error(bot, f"{cog_name} background startup failed: {repr(e)}")

        # Register persistent views (for interactions to survive restarts)
        if not getattr(bot, "_persistent_views_registered", False):
            await bot.register_persistent_views()
            bot._persistent_views_registered = True

        if callable(record_uptime):
            try:
                await record_uptime(was_online=False)
            except Exception as e:
                await log_error(bot, f"Uptime boundary after background startup deferred: {e!r}")
        set_keepalive_status("online", f"Logged in as {bot.user}")
        refresh_metrics = getattr(release_cog, "refresh_public_metrics", None)
        if callable(refresh_metrics):
            try:
                await refresh_metrics(record_availability=False)
            except Exception as e:
                await log_error(
                    bot,
                    f"Public bot status refresh after ready failed: {e!r}",
                )
        startup_log(f"Logged in as {bot.user} (ID: {bot.user.id})")
        bot._runtime_initialized = True
        repair_guild = getattr(bot, "_legacy_snowflake_repair_guild", None)
        repair_task = getattr(bot, "_legacy_snowflake_repair_task", None)
        if repair_guild is not None and (repair_task is None or repair_task.done()):
            bot._legacy_snowflake_repair_task = asyncio.create_task(
                _legacy_snowflake_repair_loop(bot, repair_guild),
                name="avenue-guard:snowflake-repair",
            )

    @bot.event
    async def on_ready():
        if bot._runtime_initialization_lock.locked():
            return
        async with bot._runtime_initialization_lock:
            if bot._runtime_initialized:
                await on_resumed()
                return
            try:
                await initialize_runtime()
            except Exception as exc:
                set_keepalive_status("startup_error", f"Runtime initialization failed: {type(exc).__name__}")
                await log_error(bot, f"Runtime initialization failed: {exc!r}\n{traceback.format_exc()}")
                await bot.close()

    @bot.event
    async def on_disconnect():
        state = str(get_keepalive_status().get("state") or "")
        if state not in {"startup_error", "fatal_login_error", "crashed", "stopped"}:
            set_keepalive_status("reconnecting", "Discord gateway connection was interrupted")
            release_cog = bot.get_cog("ReleaseCog")
            record_uptime = getattr(
                release_cog,
                "record_uptime_transition",
                None,
            )
            if state == "online" and callable(record_uptime):
                try:
                    await record_uptime(was_online=True)
                except Exception as e:
                    await log_error(
                        bot,
                        f"Uptime transition record after disconnect failed: {e!r}",
                    )
            refresh_metrics = getattr(
                release_cog,
                "refresh_public_metrics",
                None,
            )
            if callable(refresh_metrics):
                try:
                    await refresh_metrics(record_availability=False)
                except Exception as e:
                    await log_error(
                        bot,
                        f"Public bot status refresh after disconnect failed: {e!r}",
                    )

    @bot.event
    async def on_resumed():
        previous_state = str(get_keepalive_status().get("state") or "")
        set_keepalive_status("online", f"Logged in as {bot.user}")
        release_cog = bot.get_cog("ReleaseCog")
        record_uptime = getattr(release_cog, "record_uptime_transition", None)
        if previous_state != "online" and callable(record_uptime):
            try:
                await record_uptime(was_online=False)
            except Exception as e:
                await log_error(
                    bot,
                    f"Uptime transition record after resume failed: {e!r}",
                )
        refresh_metrics = getattr(release_cog, "refresh_public_metrics", None)
        if callable(refresh_metrics):
            try:
                await refresh_metrics(record_availability=False)
            except Exception as e:
                await log_error(bot, f"Public bot status refresh after resume failed: {e!r}")

    async def register_persistent_views():
        if getattr(bot, "_persistent_views_registered", False):
            return
        bot.add_view(TrackingDeclineConfirmView())
        bot.add_view(TicketClosePromptView())
        bot.add_view(HelpMenuView())
        bot.add_view(BanInfoGiveInfoView())
        bot.add_view(TranscriptRequestView())
        bot.add_view(ReleaseApprovalView())
        bot.add_view(LevelRequestButtonView())
        bot.add_view(LevelRequestReviewView())
        bot.add_view(LevelRequestPPSReviewView())
        bot._persistent_views_registered = True

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
        loop = _prepare_fresh_event_loop()
        set_keepalive_status("discord_login", "Attempting Discord login")
        try:
            bot = create_bot()
        except PersistenceConfigurationError as exc:
            _close_event_loop(loop)
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
            _run_preflight_database_check(bot, loop)
        except Exception as exc:
            try:
                if not loop.is_closed():
                    loop.run_until_complete(bot.db.close())
            except Exception:
                pass
            finally:
                _close_event_loop(loop)
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
        loop.create_task(_gateway_startup_watchdog(bot), name="avenue-guard:gateway-startup-watchdog")
        try:
            bot.run(token)
            status = get_keepalive_status()
            if str(status.get("state")) == "startup_error":
                seconds = _startup_error_retry_seconds()
                set_keepalive_status("startup_error", str(status.get("detail") or "Runtime initialization failed"),
                                     retry_after_seconds=seconds, next_retry_ts=int(time.time()) + seconds)
                time.sleep(seconds)
                continue
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
