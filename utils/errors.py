from __future__ import annotations

from datetime import datetime, timezone
import traceback
import discord

async def log_error(bot: discord.Client, message: str) -> None:
    try:
        print(f"[Avenue Guard error] {message}", flush=True)
    except Exception:
        pass
    try:
        cfg = getattr(bot, "config", None)
        if cfg is None:
            return
        ch_id = cfg.get_int("channels", "global_error_log_channel_id")
        if not ch_id:
            return
        channel = bot.get_channel(ch_id)
        if channel is None:
            return
        if len(message) > 3800:
            message = message[:3800] + "\n...truncated..."
        embed = discord.Embed(
            title="Bot Error",
            description=f"```py\n{message}\n```",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="Avenue Guard error log")
        try:
            await channel.send(embed=embed)
        except Exception:
            if len(message) > 1800:
                message = message[:1800] + "\n...truncated..."
            await channel.send(f"```py\n{message}\n```")
    except Exception:
        pass

def setup_global_error_handlers(bot: discord.Client) -> None:
    @bot.event
    async def on_application_command_error(ctx: discord.ApplicationContext, error: Exception):
        await log_error(bot, f"Command error: {repr(error)}\n{traceback.format_exc()}")
        try:
            await ctx.respond("Something went wrong while running that command.", ephemeral=True)
        except Exception:
            pass

    @bot.event
    async def on_error(event_method: str, *args, **kwargs):
        await log_error(bot, f"Event error in {event_method}\n{traceback.format_exc()}")
