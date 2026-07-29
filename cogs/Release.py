from __future__ import annotations

import asyncio
import json
import math
import re
import time
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands

from utils.errors import log_error
from utils.keepalive import set_public_bot_metrics, set_public_release_data
from utils.mentions import no_mentions
from utils.releases import (
    ReleaseValidationError,
    load_release_manifest,
    row_to_public_release,
    validate_release_payload,
)
from utils.views import ReleaseApprovalView


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    try:
        value = row[key]
    except Exception:
        return default
    return default if value is None else value


def _field_chunks(lines: list[str], *, limit: int = 1024) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}".strip()
        if current and len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


class ReleaseCog(commands.Cog):
    """Owner-approved release publishing and public bot telemetry."""

    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self._background_started = False
        self._metrics_task: asyncio.Task | None = None

    def cog_unload(self) -> None:
        if self._metrics_task is not None:
            self._metrics_task.cancel()

    def _enabled(self) -> bool:
        return bool(self.bot.config.get("release_updates", "enabled", default=True))

    def owner_ids(self) -> list[int]:
        owner_ids = self.bot.config.get_int_list("release_updates", "owner_user_ids")
        if not owner_ids:
            owner_id = self.bot.config.get_int("release_updates", "owner_user_id", default=0)
            if owner_id:
                owner_ids = [owner_id]
        if not owner_ids:
            owner_ids = self.bot.config.get_int_list("impact", "allowed_user_ids")
        return list(dict.fromkeys(int(user_id) for user_id in owner_ids if int(user_id) > 0))

    def is_owner(self, user_id: int) -> bool:
        return int(user_id) in self.owner_ids()

    def _manifest_path(self) -> Path:
        raw = self.bot.config.get_str(
            "release_updates",
            "manifest_path",
            default="release.json",
        ).strip()
        return Path(raw or "release.json")

    def _public_release_limit(self) -> int:
        value = self.bot.config.get_int(
            "release_updates",
            "public_release_limit",
            default=20,
        )
        return max(1, min(50, int(value or 20)))

    def website_url(self) -> str:
        return self.bot.config.get_str(
            "release_updates",
            "website_url",
            default="https://gdavenue.netlify.app/bot",
        ).strip()

    async def _resolve_owner(self) -> discord.User | discord.Member | None:
        owner_ids = self.owner_ids()
        if not owner_ids:
            return None
        owner_id = owner_ids[0]
        user = self.bot.get_user(owner_id)
        if user is not None:
            return user
        for guild in self.bot.guilds:
            member = guild.get_member(owner_id)
            if member is not None:
                return member
        try:
            return await self.bot.fetch_user(owner_id)
        except Exception:
            return None

    def _approval_embed(self, row: Any) -> discord.Embed:
        status = str(_row_value(row, "status", "pending"))
        colors = {
            "approved": discord.Color.green(),
            "rejected": discord.Color.red(),
            "pending": discord.Color.blurple(),
        }
        embed = discord.Embed(
            title=str(_row_value(row, "title", "Avenue Guard release"))[:256],
            description=(
                str(_row_value(row, "summary", "")).strip()
                or "Review this release before it appears on the public Avenue Guard page."
            )[:4096],
            color=colors.get(status, discord.Color.blurple()),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Version",
            value=f"`{str(_row_value(row, 'version', 'unknown'))[:40]}`",
            inline=True,
        )
        embed.add_field(
            name="Status",
            value=status.replace("_", " ").title(),
            inline=True,
        )
        embed.add_field(
            name="Proposal ID",
            value=f"`{int(_row_value(row, 'id', 0) or 0)}`",
            inline=True,
        )

        try:
            changes = json.loads(str(_row_value(row, "changes_json", "[]")))
        except json.JSONDecodeError:
            changes = []
        if not isinstance(changes, list):
            changes = []
        chunks = _field_chunks([f"- {str(change)}" for change in changes])
        for index, chunk in enumerate(chunks[:4]):
            embed.add_field(
                name="Changes" if index == 0 else "Changes continued",
                value=chunk,
                inline=False,
            )

        source = str(_row_value(row, "source", "command")).replace("_", " ").title()
        if status == "pending":
            embed.set_footer(text=f"Source: {source} | Nothing is public until you approve")
        else:
            decided_ts = int(_row_value(row, "decided_ts", 0) or 0)
            embed.set_footer(
                text=(
                    f"{status.title()} <t:{decided_ts}:R>"
                    if decided_ts
                    else status.title()
                )
            )
        return embed

    def _proposal_id_from_interaction(self, interaction: discord.Interaction) -> int:
        message = getattr(interaction, "message", None)
        for embed in list(getattr(message, "embeds", []) or []):
            for field in list(getattr(embed, "fields", []) or []):
                if str(getattr(field, "name", "")).casefold() != "proposal id":
                    continue
                match = re.search(r"\d+", str(getattr(field, "value", "")))
                if match:
                    return int(match.group(0))
        return 0

    async def _send_approval_dm(self, row: Any) -> tuple[bool, str]:
        proposal_id = int(_row_value(row, "id", 0) or 0)
        owner = await self._resolve_owner()
        if owner is None:
            error = "Configured release owner could not be resolved"
            await self.bot.db.execute(
                "UPDATE bot_releases SET error_text=? WHERE id=? AND status='pending'",
                (error, proposal_id),
            )
            return False, error

        try:
            message = await owner.send(
                embed=self._approval_embed(row),
                view=ReleaseApprovalView(),
                allowed_mentions=no_mentions(),
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:1000]
            await self.bot.db.execute(
                "UPDATE bot_releases SET error_text=? WHERE id=? AND status='pending'",
                (error, proposal_id),
            )
            await log_error(
                self.bot,
                f"Release approval DM failed proposal_id={proposal_id} owner_id={getattr(owner, 'id', 0)}: {exc!r}",
            )
            return False, error

        await self.bot.db.execute(
            "UPDATE bot_releases SET approval_message_id=?, error_text=NULL "
            "WHERE id=? AND status='pending'",
            (int(message.id), proposal_id),
        )
        return True, ""

    async def propose_release(
        self,
        *,
        version: Any,
        title: Any,
        summary: Any,
        changes: Any,
        created_by: int,
        source: str = "command",
    ) -> tuple[bool, str]:
        if not self._enabled():
            return False, "Release publishing is disabled in config.json"
        try:
            payload = validate_release_payload(version, title, summary, changes)
        except ReleaseValidationError as exc:
            return False, str(exc)

        existing = await self.bot.db.fetchone(
            "SELECT * FROM bot_releases WHERE version=? AND status IN ('pending','approved') "
            "ORDER BY id DESC LIMIT 1",
            (payload["version"],),
        )
        if existing is not None:
            status = str(existing["status"])
            if status == "approved":
                return False, f"Version `{payload['version']}` is already published"
            sent, error = await self._send_approval_dm(existing)
            if sent:
                return True, f"Pending version `{payload['version']}` was sent to your DMs again"
            return False, f"The proposal is still pending, but I could not DM you: {error}"

        proposal_id = await self.bot.db.execute_insert(
            """
            INSERT INTO bot_releases(
                version, title, summary, changes_json, status, source,
                created_by, created_ts
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                payload["version"],
                payload["title"],
                payload["summary"],
                json.dumps(payload["changes"], separators=(",", ":"), ensure_ascii=False),
                "pending",
                str(source or "command")[:40],
                int(created_by or 0) or None,
                int(time.time()),
            ),
        )
        row = await self.bot.db.fetchone(
            "SELECT * FROM bot_releases WHERE id=?",
            (proposal_id,),
        )
        if row is None:
            return False, "The release proposal could not be read back from storage"

        sent, error = await self._send_approval_dm(row)
        if not sent:
            return False, (
                f"Version `{payload['version']}` was saved as pending, but I could not DM you: {error}. "
                "Run the command again after opening your DMs"
            )
        return True, (
            f"Version `{payload['version']}` is pending. Approve or reject it from the DM I sent you"
        )

    async def _ensure_manifest_proposal(self) -> None:
        if not self._enabled():
            return
        try:
            payload = load_release_manifest(self._manifest_path())
        except ReleaseValidationError as exc:
            await log_error(self.bot, f"Release manifest is invalid: {exc}")
            return
        if payload is None:
            return

        existing = await self.bot.db.fetchone(
            "SELECT * FROM bot_releases WHERE version=? ORDER BY id DESC LIMIT 1",
            (payload["version"],),
        )
        if existing is not None:
            if (
                str(existing["status"]) == "pending"
                and not int(_row_value(existing, "approval_message_id", 0) or 0)
            ):
                await self._send_approval_dm(existing)
            return

        owner_ids = self.owner_ids()
        ok, message = await self.propose_release(
            **payload,
            created_by=owner_ids[0] if owner_ids else 0,
            source="deployment_manifest",
        )
        if not ok:
            await log_error(self.bot, f"Automatic release proposal failed: {message}")

    async def refresh_public_release_cache(self) -> None:
        rows = await self.bot.db.fetchall(
            "SELECT version,title,summary,changes_json,decided_ts "
            "FROM bot_releases WHERE status='approved' "
            "ORDER BY decided_ts DESC,id DESC LIMIT ?",
            (self._public_release_limit(),),
        )
        set_public_release_data([row_to_public_release(row) for row in rows])

    async def refresh_public_metrics(self) -> None:
        latency = float(getattr(self.bot, "latency", 0.0) or 0.0)
        latency_ms = round(latency * 1000) if math.isfinite(latency) else None
        guilds = list(getattr(self.bot, "guilds", []) or [])
        member_count = sum(
            max(0, int(getattr(guild, "member_count", 0) or len(getattr(guild, "members", []) or [])))
            for guild in guilds
        )
        user = getattr(self.bot, "user", None)
        avatar = getattr(getattr(user, "display_avatar", None), "url", "")
        set_public_bot_metrics(
            bot_name=str(user or "Avenue Guard"),
            avatar_url=str(avatar or ""),
            latency_ms=latency_ms,
            guild_count=len(guilds),
            member_count=member_count,
        )

    async def _metrics_loop(self) -> None:
        refresh_count = 0
        while not self.bot.is_closed():
            await asyncio.sleep(30)
            try:
                await self.refresh_public_metrics()
                refresh_count += 1
                if refresh_count % 10 == 0:
                    await self.refresh_public_release_cache()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await log_error(self.bot, f"Public bot status refresh failed: {exc!r}")

    async def start_background(self) -> None:
        if self._background_started:
            return
        self._background_started = True
        try:
            await self.refresh_public_release_cache()
        except Exception as exc:
            await log_error(self.bot, f"Initial public release cache load failed: {exc!r}")
        try:
            await self.refresh_public_metrics()
        except Exception as exc:
            await log_error(self.bot, f"Initial public bot status refresh failed: {exc!r}")
        try:
            await self._ensure_manifest_proposal()
        except Exception as exc:
            await log_error(self.bot, f"Initial release manifest check failed: {exc!r}")
        self._metrics_task = asyncio.create_task(
            self._metrics_loop(),
            name="avenue-guard-public-status",
        )

    async def release_overview(self) -> dict[str, Any]:
        approved = await self.bot.db.fetchone(
            "SELECT version,title,summary,changes_json,decided_ts "
            "FROM bot_releases WHERE status='approved' "
            "ORDER BY decided_ts DESC,id DESC LIMIT 1"
        )
        pending = await self.bot.db.fetchall(
            "SELECT id,version,title,created_ts,approval_message_id,error_text "
            "FROM bot_releases WHERE status='pending' ORDER BY created_ts DESC,id DESC LIMIT 10"
        )
        return {
            "current": row_to_public_release(approved) if approved is not None else None,
            "pending": [
                {
                    "id": int(row["id"]),
                    "version": str(row["version"]),
                    "title": str(row["title"]),
                    "created_ts": int(row["created_ts"]),
                    "approval_message_id": int(row["approval_message_id"] or 0),
                    "error_text": str(row["error_text"] or ""),
                }
                for row in pending
            ],
            "website_url": self.website_url(),
        }

    async def handle_release_decision(
        self,
        interaction: discord.Interaction,
        *,
        approved: bool,
    ) -> None:
        if not self.is_owner(int(interaction.user.id)):
            return await interaction.response.send_message(
                "Only the configured release owner can use this control.",
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )
        await interaction.response.defer(ephemeral=True)

        proposal_id = self._proposal_id_from_interaction(interaction)
        row = await self.bot.db.fetchone(
            "SELECT * FROM bot_releases WHERE id=?",
            (proposal_id,),
        )
        if row is None:
            return await interaction.followup.send(
                "That release proposal could not be found.",
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )

        expected_message_id = int(_row_value(row, "approval_message_id", 0) or 0)
        actual_message_id = int(getattr(getattr(interaction, "message", None), "id", 0) or 0)
        if expected_message_id and expected_message_id != actual_message_id:
            return await interaction.followup.send(
                "That approval panel was replaced by a newer one. Use the latest DM.",
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )
        if str(row["status"]) != "pending":
            return await interaction.followup.send(
                f"Version `{row['version']}` was already {row['status']}.",
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )

        status = "approved" if approved else "rejected"
        decided_ts = int(time.time())
        await self.bot.db.execute(
            "UPDATE bot_releases SET status=?,decided_by=?,decided_ts=?,error_text=NULL "
            "WHERE id=? AND status='pending'",
            (status, int(interaction.user.id), decided_ts, proposal_id),
        )
        decided = await self.bot.db.fetchone(
            "SELECT * FROM bot_releases WHERE id=?",
            (proposal_id,),
        )
        if decided is None:
            return await interaction.followup.send(
                "The release decision was saved, but I could not reload the proposal.",
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )
        if str(decided["status"]) != status:
            return await interaction.followup.send(
                f"Version `{decided['version']}` was already {decided['status']}.",
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )

        if approved:
            await self.refresh_public_release_cache()

        try:
            await interaction.message.edit(
                embed=self._approval_embed(decided),
                view=ReleaseApprovalView(disabled=True),
            )
        except Exception as exc:
            await log_error(
                self.bot,
                f"Release approval panel edit failed proposal_id={proposal_id}: {exc!r}",
            )

        if approved:
            website = self.website_url()
            message = f"Version `{decided['version']}` is now published"
            if website:
                message += f": {website}"
        else:
            message = f"Version `{decided['version']}` was rejected and remains private"
        await interaction.followup.send(
            message,
            ephemeral=True,
            allowed_mentions=no_mentions(),
        )


def setup(bot: discord.Bot):
    bot.add_cog(ReleaseCog(bot))
