from __future__ import annotations

import asyncio
import io
import time
from urllib.parse import urlparse

import discord
from discord.ext import commands

from services.historical_audit import HistoricalAuditService
from utils.errors import log_error
from utils.historical_audit import MAX_CSV_BYTES, audit_settings, parse_sheet_csv


class HistoricalAuditCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.service = HistoricalAuditService(bot)
        self._audit_task = None
        self._wake = asyncio.Event()
        self._delivery_lock = asyncio.Lock()
        self._contexts = {}

    def enabled(self):
        try:
            return audit_settings(self.bot.config.data)["enabled"]
        except ValueError:
            return False

    async def start_background(self):
        if self._audit_task is None or self._audit_task.done():
            self._audit_task = asyncio.create_task(self._worker(), name="avenue-guard:history.audit")

    def cog_unload(self):
        if self._audit_task:
            self._audit_task.cancel()

    async def close_resources(self):
        if self._audit_task and self._audit_task is not asyncio.current_task():
            self._audit_task.cancel()
            await asyncio.gather(self._audit_task, return_exceptions=True)

    def is_owner(self, user_id: int) -> bool:
        # Unlike legacy impact fallback, absence of an allowlist fails closed.
        return int(user_id) in self.bot.config.get_int_list("impact", "allowed_user_ids")

    async def _worker(self):
        while True:
            self._wake.clear()
            try:
                if self.enabled():
                    run = await self.service.active_run()
                    if run:
                        await self.service.process(run)
                    ready = await self.bot.db.fetchone(
                        "SELECT * FROM historical_audit_runs WHERE status='completed' AND delivery_status='pending' ORDER BY started_ts LIMIT 1"
                    )
                    if ready:
                        await self.deliver(dict(ready))
            except Exception as exc:
                try:
                    active = await self.service.active_run()
                    if active:
                        await self.service._write([("UPDATE historical_audit_runs SET error_text=? WHERE run_id=? AND status IN ('queued','running')", (f"{type(exc).__name__}: {str(exc)[:400]}", active["run_id"]))])
                except Exception:
                    pass  # Error logging must stay independent of failed storage.
                await log_error(self.bot, f"Historical audit worker paused; durable progress retained: {type(exc).__name__}: {str(exc)[:400]}")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=30)
            except TimeoutError:
                pass

    async def _read_url(self, url: str) -> bytes:
        settings = audit_settings(self.bot.config.data)
        parsed = urlparse(url)
        if (len(url) > 2000 or parsed.scheme != "https" or parsed.hostname not in settings["allow_csv_hosts"]
                or parsed.port not in {None, 443} or parsed.username or parsed.password):
            raise ValueError("CSV URL must use HTTPS and an explicitly configured allow_csv_hosts hostname")
        # Explicit host allowlist and no redirects keep public CSV fetching narrow.
        session = await self.bot.get_cog("RequestLevelsCog")._get_level_validation_session()
        async with session.get(url, allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError(f"CSV URL returned HTTP {response.status}; upload its downloaded CSV instead")
            buffer = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                buffer.extend(chunk)
                if len(buffer) > MAX_CSV_BYTES:
                    raise ValueError("CSV exceeds the 2 MB import limit")
            return bytes(buffer)

    async def command(self, ctx, action, run_id, refresh_external, force, sheet_csv, csv_url):
        await ctx.defer(ephemeral=True)
        guild_id = self.bot.config.get_int("guild", "allowed_guild_id")
        if not ctx.guild or int(ctx.guild.id) != guild_id or not self.is_owner(ctx.user.id):
            await ctx.respond("This private audit is available only to the configured bot owner", ephemeral=True)
            return
        try:
            if action == "start":
                if sheet_csv and csv_url:
                    raise ValueError("Supply either a CSV attachment or a CSV URL, not both")
                labels, errors = [], []
                if sheet_csv:
                    if sheet_csv.size > MAX_CSV_BYTES or not sheet_csv.filename.casefold().endswith(".csv"):
                        raise ValueError("Upload a .csv file no larger than 2 MB")
                    labels, errors = await asyncio.to_thread(parse_sheet_csv, await sheet_csv.read(), sheet_csv.filename)
                elif csv_url:
                    labels, errors = await asyncio.to_thread(parse_sheet_csv, await self._read_url(csv_url), "public_csv")
                identity = await self.service.start(guild_id, ctx.user.id, refresh_external=refresh_external,
                                                    force=force, labels=labels, import_errors=errors)
                self._contexts = {key: value for key, value in self._contexts.items() if time.monotonic() - value[1] < 840}
                self._contexts[identity] = (ctx, time.monotonic())
                await self.start_background()
                self._wake.set()
                await ctx.respond(f"Private historical audit queued: `{identity}`\nUse this command with **status** to inspect progress or **report** to download it. Completion will be sent by DM", ephemeral=True)
                return
            run = await self.service.get_run(run_id or None)
            if not run or int(run["guild_id"]) != guild_id:
                raise ValueError("No matching historical audit run was found")
            if action == "cancel":
                await self.service.cancel(run["run_id"])
                await ctx.respond(f"Audit `{run['run_id']}` is cancelled if it was active; completed snapshots remain available", ephemeral=True)
            elif action == "report":
                await self.deliver(run, ctx=ctx)
            else:
                progress = await self.service.progress(run["run_id"])
                await ctx.respond(f"Audit `{run['run_id']}`: **{run['status']}**\nHistorical requests: {progress['requests']}\nLevels: {progress['levels']['done']}/{progress['levels']['total']}\nCreator accounts: {progress['creators']['done']}/{progress['creators']['total']}\nReport delivery: {run['delivery_status']}\n{'Paused: audit disabled in config.json' if not self.enabled() else 'Analysis only; no live decisions are changed'}\n{run['error_text'] or ''}", ephemeral=True)
        except (ValueError, UnicodeError) as exc:
            await ctx.respond(str(exc)[:1800], ephemeral=True)

    async def deliver(self, run, *, ctx=None):
        async with self._delivery_lock:
            await self._deliver_locked(run, ctx=ctx)

    async def _deliver_locked(self, run, *, ctx=None):
        if ctx is None:
            latest = await self.service.get_run(run["run_id"])
            if not latest or latest["delivery_status"] != "pending":
                return
        exports, summary = await self.service.report(run["run_id"])
        if ctx is None:
            # Reserve only after read/render succeeds, but before touching Discord.
            # Unknown delivery is not automatically resent; report downloads remain.
            claimed = await self.bot.db.execute_affected("UPDATE historical_audit_runs SET delivery_status='sending' WHERE run_id=? AND delivery_status='pending'", (run["run_id"],))
            if not claimed:
                return
        counts = summary["coverage"]
        embed = discord.Embed(title="Historical Request Audit", description="Private exploratory data, not a live ranking or causal impact measure", color=discord.Color.teal())
        embed.add_field(name="Historical requests", value=f"{counts['total_historical_requests']} requests\n{counts['total_distinct_level_ids']} distinct levels", inline=True)
        embed.add_field(name="Historical decisions", value=f"Sent: {counts['historical_sent']}\nRejected: {counts['historical_rejected']}\nOther: {counts['historical_other']}", inline=True)
        def show(entry):
            percentage = f"{entry['percentage']}%" if entry['percentage'] is not None else "n/a"
            return f"{entry['count']}/{entry['total']} ({percentage})"
        embed.add_field(name="Coverage", value=f"GD snapshot: {show(counts['gd_success_requests'])}\nCurrent CP: {show(counts['known_current_CP_requests'])}\nSent prestige labels: {show(counts['sent_prestige_labels'])}", inline=False)
        embed.add_field(name="Analysis limits", value=f"Sent full candidate components: {show(counts['sent_full_candidate_components'])}\nUnknown values remain unknown. Current CP and observed-wave age are not historical outreach evidence", inline=False)
        embed.set_footer(text=f"Run {run['run_id']} | {run['status']}")
        # Never send private files to an unchecked report channel. Owner DMs and
        # ephemeral interaction follow-ups are the only delivery destinations.
        target = None
        if ctx is None:
            if not self.is_owner(int(run["owner_id"])):
                await self.service._write([("UPDATE historical_audit_runs SET delivery_status='owner_unavailable' WHERE run_id=?", (run["run_id"],))])
                return
            try:
                target = self.bot.get_user(int(run["owner_id"])) or await self.bot.fetch_user(int(run["owner_id"]))
            except discord.HTTPException:
                target = None
        sender = ctx.respond if ctx is not None else target.send if target else None
        if sender is None:
            await self.service._write([("UPDATE historical_audit_runs SET delivery_status='dm_unavailable' WHERE run_id=?", (run["run_id"],))])
            await self._ephemeral_fallback(run)
            return
        try:
            # Preserve oversized files as byte parts; concatenating parts restores
            # the original CSV/JSON, including quoted multiline cells.
            files = await asyncio.to_thread(split_exports, exports, 7_000_000)
            for index, group in enumerate(attachment_groups(files)):
                attachments = [discord.File(io.BytesIO(payload), filename=name) for name, payload in group]
                try:
                    kwargs = {"files": attachments, "allowed_mentions": discord.AllowedMentions.none()}
                    if index == 0:
                        kwargs["embed"] = embed
                        if any(".part" in name for name, _payload in files):
                            kwargs["content"] = "Large files use numbered .part attachments. Concatenate each file's parts in numeric order to restore the original CSV/JSON"
                    if ctx is not None:
                        kwargs["ephemeral"] = True
                    await sender(**kwargs)
                finally:
                    for attachment in attachments:
                        attachment.close()
        except discord.HTTPException as exc:
            await self.service._write([("UPDATE historical_audit_runs SET delivery_status='dm_unavailable' WHERE run_id=?", (run["run_id"],))])
            if ctx:
                raise
            await self._ephemeral_fallback(run)
            await log_error(self.bot, f"Historical audit report DM unavailable run={run['run_id']}; use private report command: {type(exc).__name__}")
            return
        if run["status"] == "completed":
            await self.service._write([("UPDATE historical_audit_runs SET delivery_status='sent' WHERE run_id=?", (run["run_id"],))])
        self._contexts.pop(run["run_id"], None)

    async def _ephemeral_fallback(self, run):
        saved = self._contexts.pop(run["run_id"], None)
        if saved and time.monotonic() - saved[1] < 840:
            await self._deliver_locked(run, ctx=saved[0])


def split_exports(exports, limit):
    # Oversized files are raw numbered byte parts, requiring concatenation. This
    # preserves quoted multiline CSV cells/JSON exactly, unlike splitting lines.
    output = []
    for name, payload in exports.items():
        if len(payload) <= limit:
            output.append((name, payload))
        else:
            for index, offset in enumerate(range(0, len(payload), limit), 1):
                output.append((f"{name}.part{index:03d}", payload[offset:offset + limit]))
    return output


def attachment_groups(files, limit=7_000_000):
    group, size = [], 0
    for item in files:
        if group and (len(group) == 6 or size + len(item[1]) > limit):
            yield group
            group, size = [], 0
        group.append(item)
        size += len(item[1])
    if group:
        yield group


def setup(bot):
    bot.add_cog(HistoricalAuditCog(bot))
