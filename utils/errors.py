from __future__ import annotations

from datetime import datetime, timezone
import re
import time
import traceback
import discord

_ERROR_DEDUPE_SECONDS = 300
_recent_error_logs: dict[str, float] = {}


def _compact_error_message(message: str, limit: int = 3600) -> str:
    text = str(message or "")
    lower = text.casefold()
    if "turso-diskless-wal" in lower or "s3 error" in lower or "hrana" in lower or "connection has reached an invalid state" in lower:
        if "connection has reached an invalid state" in lower or "started with txn" in lower:
            detail = "The local libSQL replica connection entered an invalid transaction state and should recover after reconnect/retry."
        elif "internalservererror" in lower or "code=500" in lower:
            detail = "Turso/S3 returned a temporary 500 storage error and the database wrapper will retry/reconnect where possible."
        else:
            detail = "Turso/libSQL returned a remote sync or stream error."
        return f"{_strip_trace_context(text)}\n\nOperational note: {detail}"
    if "<html" in lower or "cloudflare ray id" in lower or "cf-error" in lower:
        ray_match = re.search(r"Cloudflare Ray ID:\s*<[^>]+>\s*([^<\s]+)", text, flags=re.I)
        if ray_match is None:
            ray_match = re.search(r"ray id[:\s]+([A-Za-z0-9_-]+)", text, flags=re.I)
        ray = f" Cloudflare Ray ID: {ray_match.group(1)}." if ray_match else ""
        return f"External service returned a Cloudflare/HTML error page; full HTML omitted from logs.{ray}"
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...truncated..."


def _strip_trace_context(text: str, limit: int = 900) -> str:
    cleaned = re.sub(r"<\?xml[^>]*>.*", "[remote XML error body omitted]", text, flags=re.I | re.S)
    cleaned = re.sub(r"body=<.*", "body=[remote error body omitted]", cleaned, flags=re.I | re.S)
    if len(cleaned) > limit:
        cleaned = cleaned[:limit] + "\n...truncated..."
    return cleaned


def _dedupe_key(message: str) -> str:
    text = re.sub(r"\d{12,}", "<id>", str(message or ""))
    text = re.sub(r"RequestId>[A-Za-z0-9_-]+<", "RequestId><", text)
    return text[:600]


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
        now = time.monotonic()
        stale_keys = [old_key for old_key, old_ts in _recent_error_logs.items() if now - old_ts >= _ERROR_DEDUPE_SECONDS]
        for old_key in stale_keys[:100]:
            _recent_error_logs.pop(old_key, None)
        key = _dedupe_key(message)
        last_sent = _recent_error_logs.get(key, 0)
        if now - last_sent < _ERROR_DEDUPE_SECONDS:
            return
        _recent_error_logs[key] = now
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
