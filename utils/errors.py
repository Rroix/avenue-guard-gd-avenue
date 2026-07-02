from __future__ import annotations

from datetime import datetime, timezone
import re
import traceback
import discord


def _compact_error_message(message: str, limit: int = 3600) -> str:
    text = str(message or "")
    lower = text.casefold()
    if "<html" in lower or "cloudflare ray id" in lower or "cf-error" in lower:
        ray_match = re.search(r"Cloudflare Ray ID:\s*<[^>]+>\s*([^<\s]+)", text, flags=re.I)
        if ray_match is None:
            ray_match = re.search(r"ray id[:\s]+([A-Za-z0-9_-]+)", text, flags=re.I)
        ray = f" Cloudflare Ray ID: {ray_match.group(1)}." if ray_match else ""
        return f"External service returned a Cloudflare/HTML error page; full HTML omitted from logs.{ray}"
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...truncated..."


async def log_error(bot: discord.Client, message: str) -> None:
    message = _compact_error_message(message)
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
        message = _compact_error_message(message, limit=3800)
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
