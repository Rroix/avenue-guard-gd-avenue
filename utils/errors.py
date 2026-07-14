from __future__ import annotations

from datetime import datetime, timezone
import re
import time
import traceback
import discord

from utils.mentions import no_mentions

_ERROR_DEDUPE_SECONDS = 300
_recent_error_logs: dict[str, float] = {}


def _redact_secrets(message: str) -> str:
    text = str(message or "")
    text = re.sub(
        r"(?i)\b(DISCORD_TOKEN|TURSO_AUTH_TOKEN|LIBSQL_AUTH_TOKEN|DATABASE_URL)\s*[:=]\s*([^\s,;]+)",
        lambda match: f"{match.group(1)}=[REDACTED]",
        text,
    )
    text = re.sub(
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
        "[REDACTED JWT]",
        text,
    )
    text = re.sub(
        r"\b(?:mfa\.[A-Za-z0-9_-]{20,}|[MN][A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{20,})\b",
        "[REDACTED DISCORD TOKEN]",
        text,
    )
    return text


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


def _unwrap_command_error(error: Exception) -> Exception:
    current = error
    seen: set[int] = set()
    for _ in range(5):
        if id(current) in seen:
            break
        seen.add(id(current))
        original = getattr(current, "original", None)
        if not isinstance(original, Exception):
            break
        current = original
    return current


def _command_error_record(ctx: discord.ApplicationContext, error: Exception) -> dict[str, object]:
    root = _unwrap_command_error(error)
    command = getattr(ctx, "command", None)
    command_name = str(getattr(command, "qualified_name", None) or getattr(command, "name", "unknown"))
    code = int(getattr(root, "code", 0) or 0)
    category = "interaction_timeout" if code == 10062 else type(root).__name__
    detail = _compact_error_message(_redact_secrets(str(root)), limit=300).replace("\n", " ")
    return {
        "ts": int(time.time()),
        "command": command_name[:100],
        "category": category[:100],
        "detail": detail[:300],
    }


async def log_error(bot: discord.Client, message: str) -> None:
    message = _compact_error_message(_redact_secrets(message))
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
            try:
                channel = await bot.fetch_channel(ch_id)
            except Exception:
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
        safe_message = message.replace("```", "` ` `")
        embed = discord.Embed(
            title="Bot Error",
            description=f"```py\n{safe_message}\n```",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="Avenue Guard error log")
        try:
            await channel.send(embed=embed, allowed_mentions=no_mentions())
        except Exception as send_error:
            if len(message) > 1800:
                message = message[:1800] + "\n...truncated..."
            try:
                await channel.send(
                    f"```py\n{message.replace('```', '` ` `')}\n```",
                    allowed_mentions=no_mentions(),
                )
            except Exception:
                try:
                    print(
                        f"[Avenue Guard error] Discord error-log delivery failed: "
                        f"{type(send_error).__name__}: {send_error}",
                        flush=True,
                    )
                except Exception:
                    pass
    except Exception as logging_error:
        try:
            print(
                f"[Avenue Guard error] Error logger failed: "
                f"{type(logging_error).__name__}: {logging_error}",
                flush=True,
            )
        except Exception:
            pass

def setup_global_error_handlers(bot: discord.Client) -> None:
    @bot.event
    async def on_application_command_error(ctx: discord.ApplicationContext, error: Exception):
        record = _command_error_record(ctx, error)
        bot._last_command_error = record
        error_trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        await log_error(
            bot,
            f"Command error in /{record['command']} [{record['category']}]: {repr(error)}\n{error_trace}",
        )
        if record["category"] == "interaction_timeout":
            return
        try:
            await ctx.respond("Something went wrong while running that command.", ephemeral=True)
        except Exception:
            pass

    @bot.event
    async def on_error(event_method: str, *args, **kwargs):
        await log_error(bot, f"Event error in {event_method}\n{traceback.format_exc()}")
