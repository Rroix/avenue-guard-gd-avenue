from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import re
import time
import traceback
from uuid import uuid4
import discord

from utils.mentions import no_mentions
from utils.workflows import clear_workflow_context, current_correlation_id


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
    if "file is not a database" in lower or "sqlite_notadb" in lower:
        return (
            f"{_strip_trace_context(text)}\n\n"
            "Operational note: the local Turso replica was unreadable. The database wrapper "
            "will quarantine it and rebuild from the remote primary; persistent cloud data is not deleted."
        )
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


class ErrorReporter:
    """Independent Discord delivery and persistence; neither blocks the caller."""

    def __init__(self, bot):
        self.bot = bot
        self.entries: dict[str, dict] = {}
        self._delivery_event = asyncio.Event()
        self._persistence_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self.delivery_interval = 5.0
        self.delivery_timeout = 15.0
        self.persistence_retry_seconds = 10.0
        self._closed = False

    def record(self, message: str) -> None:
        if self._closed:
            return
        fingerprint = hashlib.sha256(_dedupe_key(message).encode("utf-8", errors="replace")).hexdigest()[:24]
        now = int(time.time())
        if fingerprint not in self.entries:
            if len(self.entries) >= 512:
                removable = [key for key, value in self.entries.items() if not value["pending"]]
                if removable:
                    self.entries.pop(removable[0])
                else:
                    print("[Avenue Guard error] Incident buffer full; full error remains in deployment logs", flush=True)
                    return
            self.entries[fingerprint] = {
                "first_ts": now, "last_ts": now, "count": 0, "pending": 0,
                "dirty": True, "message_id": 0, "last_delivery": 0.0,
            }
        entry = self.entries[fingerprint]
        entry.update(message=message, category=message.splitlines()[0][:120], last_ts=now,
                     correlation=current_correlation_id(), dirty=True)
        entry["count"] += 1
        entry["pending"] += 1
        self._delivery_event.set()
        self._persistence_event.set()
        if not self._tasks or any(task.done() for task in self._tasks):
            factories = (self._deliver_loop, self._persist_loop)
            previous = self._tasks
            self._tasks = [
                previous[index] if index < len(previous) and not previous[index].done()
                else asyncio.create_task(factory(), name=f"avenue-guard:error-{index}")
                for index, factory in enumerate(factories)
            ]

    def snapshot(self) -> list[dict]:
        return [
            {"fingerprint": key, "category": entry["category"], "count": entry["count"],
             "last_seen_ts": entry["last_ts"], "pending_persistence": entry["pending"]}
            for key, entry in self.entries.items()
        ]

    async def close(self) -> None:
        self._closed = True
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _deliver_loop(self):
        while True:
            await self._delivery_event.wait()
            self._delivery_event.clear()
            dirty = [(key, entry) for key, entry in self.entries.items() if entry["dirty"]]
            for key, entry in dirty:
                if time.monotonic() - entry["last_delivery"] < self.delivery_interval:
                    continue
                try:
                    config = getattr(self.bot, "config", None)
                    channel_id = config.get_int("channels", "global_error_log_channel_id") if config else 0
                    if not channel_id:
                        entry["dirty"] = False
                        continue
                    channel = self.bot.get_channel(channel_id)
                    if channel is None:
                        channel = await asyncio.wait_for(self.bot.fetch_channel(channel_id), self.delivery_timeout)
                    count = entry["count"]
                    message = entry["message"].replace("```", "` ` `")[:3600]
                    embed = discord.Embed(title="Bot Error", description=f"```py\n{message}\n```",
                                          color=discord.Color.red(), timestamp=datetime.now(timezone.utc))
                    embed.add_field(name="Incident", value=f"`{key}`", inline=True)
                    embed.add_field(name="Occurrences", value=str(count), inline=True)
                    embed.add_field(name="Last Seen", value=f"<t:{entry['last_ts']}:R>", inline=True)
                    if entry.get("correlation"):
                        embed.add_field(name="Workflow", value=f"`{entry['correlation']}`", inline=False)
                    if entry["pending"]:
                        embed.set_footer(text="Avenue Guard error log • persistence pending")
                    else:
                        embed.set_footer(text="Avenue Guard error log")
                    previous = None
                    if entry["message_id"]:
                        try:
                            previous = await asyncio.wait_for(channel.fetch_message(entry["message_id"]), self.delivery_timeout)
                        except discord.NotFound:
                            pass
                    if previous is not None:
                        await asyncio.wait_for(previous.edit(embed=embed, allowed_mentions=no_mentions()), self.delivery_timeout)
                    else:
                        sent = await asyncio.wait_for(channel.send(embed=embed, allowed_mentions=no_mentions()), self.delivery_timeout)
                        entry["message_id"] = int(sent.id)
                        self._persistence_event.set()
                    entry["last_delivery"] = time.monotonic()
                    entry["dirty"] = entry["count"] != count
                except Exception as exc:
                    entry["last_delivery"] = time.monotonic()
                    print(f"[Avenue Guard error] Discord incident delivery failed: {_redact_secrets(str(exc))}", flush=True)
            if any(entry["dirty"] for entry in self.entries.values()):
                await asyncio.sleep(self.delivery_interval)
                self._delivery_event.set()

    async def _persist_loop(self):
        while True:
            await self._persistence_event.wait()
            self._persistence_event.clear()
            for key, entry in list(self.entries.items()):
                delta = entry["pending"]
                try:
                    if delta:
                        batch_id, delta = entry.setdefault("batch", (uuid4().hex, delta))
                        await self.bot.db.execute_transaction([
                            (
                                "INSERT INTO error_incidents(fingerprint,category,status,first_seen_ts,last_seen_ts,occurrence_count,last_message,last_correlation_id,log_message_id) "
                                "SELECT ?,?,?,?,?,?,?,?,? WHERE NOT EXISTS(SELECT 1 FROM error_incident_batches WHERE batch_id=?) "
                                "ON CONFLICT(fingerprint) DO UPDATE SET status='open',last_seen_ts=excluded.last_seen_ts,"
                                "occurrence_count=error_incidents.occurrence_count+excluded.occurrence_count,last_message=excluded.last_message,"
                                "last_correlation_id=excluded.last_correlation_id,log_message_id=COALESCE(excluded.log_message_id,error_incidents.log_message_id),resolved_ts=NULL",
                                (key, entry["category"], "open", entry["first_ts"], entry["last_ts"], delta,
                                 entry["message"], entry.get("correlation") or None, entry["message_id"] or None, batch_id),
                            ),
                            ("INSERT OR IGNORE INTO error_incident_batches(batch_id,fingerprint,created_ts) VALUES(?,?,?)",
                             (batch_id, key, int(time.time()))),
                        ], retry_safe=True, queue_timeout=0.5, operation_label="errors.persist")
                        entry["pending"] -= delta
                        entry.pop("batch", None)
                        row = await self.bot.db.fetchone("SELECT occurrence_count,log_message_id FROM error_incidents WHERE fingerprint=?", (key,))
                        if row:
                            entry["count"] = int(row["occurrence_count"]) + entry["pending"]
                            entry["message_id"] = entry["message_id"] or int(row["log_message_id"] or 0)
                            entry["dirty"] = True
                            self._delivery_event.set()
                    elif entry["message_id"]:
                        await self.bot.db.execute("UPDATE error_incidents SET log_message_id=? WHERE fingerprint=?", (entry["message_id"], key))
                except Exception as exc:
                    print(f"[Avenue Guard error] Incident persistence deferred: {_compact_error_message(_redact_secrets(str(exc)), 300)}", flush=True)
            if any(entry["pending"] for entry in self.entries.values()):
                await asyncio.sleep(self.persistence_retry_seconds)
                self._persistence_event.set()


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
    print(f"[Avenue Guard error] {message}", flush=True)
    reporter = getattr(bot, "_error_reporter", None)
    if reporter is None:
        reporter = bot._error_reporter = ErrorReporter(bot)
    reporter.record(message)


async def _notify_interaction_failure(interaction: discord.Interaction, error: Exception) -> None:
    root = _unwrap_command_error(error)
    if int(getattr(root, "code", 0) or 0) == 10062:
        return
    if "Database busy" in str(root) or "Turso worker" in str(root):
        message = "Storage is temporarily unavailable. Please retry in a moment; check the current status before repeating a submission."
    else:
        message = "Something went wrong. Please try again; staff have been notified."
    try:
        if interaction.response.is_done():
            await asyncio.wait_for(interaction.followup.send(message, ephemeral=True, allowed_mentions=no_mentions()), timeout=5)
        else:
            await asyncio.wait_for(interaction.response.send_message(message, ephemeral=True, allowed_mentions=no_mentions()), timeout=5)
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
        if record["category"] != "interaction_timeout":
            await _notify_interaction_failure(ctx.interaction, error)
        clear_workflow_context()

    @bot.event
    async def on_view_error(error: Exception, item, interaction: discord.Interaction):
        label = str(getattr(item, "custom_id", None) or getattr(item, "label", None) or "component")
        bot._last_component_error = {"ts": int(time.time()), "component": label, "category": type(error).__name__}
        await log_error(bot, f"Component error [{label}]: {error!r}\n" + "".join(traceback.format_exception(type(error), error, error.__traceback__)))
        await _notify_interaction_failure(interaction, error)
        clear_workflow_context()

    @bot.event
    async def on_modal_error(error: Exception, interaction: discord.Interaction):
        custom_id = str((interaction.data or {}).get("custom_id") or "modal")
        bot._last_component_error = {"ts": int(time.time()), "component": custom_id, "category": type(error).__name__}
        await log_error(bot, f"Modal error [{custom_id}]: {error!r}\n" + "".join(traceback.format_exception(type(error), error, error.__traceback__)))
        await _notify_interaction_failure(interaction, error)
        clear_workflow_context()

    @bot.event
    async def on_error(event_method: str, *args, **kwargs):
        await log_error(bot, f"Event error in {event_method}\n{traceback.format_exc()}")
