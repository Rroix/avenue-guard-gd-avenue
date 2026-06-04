from __future__ import annotations

import os
import traceback
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

DB_PATH = "data/bot.db"


def startup_log(message: str) -> None:
    print(f"[Avenue Guard startup] {message}", flush=True)

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
    bot.db = Database(DB_PATH)

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
