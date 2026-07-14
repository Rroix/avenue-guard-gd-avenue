from __future__ import annotations

import asyncio
import io
import json
import re
import time
from typing import Optional, Dict, Any, Tuple

import discord
from discord.ext import commands

from utils.checks import ensure_allowed_guild_id, is_mod
from utils.errors import log_error
from utils.mentions import no_mentions, user_and_role_mentions, user_mentions
from utils.views import HelpMenuView, TicketClosePromptView, TranscriptRequestView
from utils.transcript import build_text_transcript
from utils.timeutils import now_madrid, week_start_sunday


def _format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h"
    days = hours // 24
    return f"{days}d"


TICKET_STATUS_LABELS = {
    "waiting_user": "Waiting for user",
    "waiting_staff": "Waiting for staff",
    "resolved": "Resolved",
}


def _ticket_status_key(value: Any) -> str:
    text = re.sub(r"[_\-/]+", " ", str(value or "").strip().casefold())
    text = re.sub(r"\s+", " ", text).strip()
    if text in {"waiting user", "waiting for user", "user", "wfu"}:
        return "waiting_user"
    if text in {"waiting staff", "waiting for staff", "staff", "wfs"}:
        return "waiting_staff"
    if text in {"resolved", "resolve", "done", "closed"}:
        return "resolved"
    return ""


def _ticket_status_label(value: Any) -> str:
    return TICKET_STATUS_LABELS.get(_ticket_status_key(value), "Waiting for staff")


class HelpSessionControlView(discord.ui.View):
    def __init__(self, cog, user_id: int, guild_id: int, allow_back: bool = True):
        super().__init__(timeout=900)
        self.cog = cog
        self.user_id = int(user_id)
        self.guild_id = int(guild_id)
        for item in self.children:
            if getattr(item, "label", "") == "Back":
                item.disabled = not allow_back

    async def _allowed(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This help flow is not for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not await self._allowed(interaction):
            return
        await self.cog.handle_help_session_control(interaction, self.guild_id, "back")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not await self._allowed(interaction):
            return
        await self.cog.handle_help_session_control(interaction, self.guild_id, "cancel")

    @discord.ui.button(label="Start over", style=discord.ButtonStyle.primary)
    async def start_over(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not await self._allowed(interaction):
            return
        await self.cog.handle_help_session_control(interaction, self.guild_id, "start_over")


class HelpSubmissionPreviewView(discord.ui.View):
    def __init__(self, cog, user_id: int, guild_id: int, kind: str):
        super().__init__(timeout=900)
        self.cog = cog
        self.user_id = int(user_id)
        self.guild_id = int(guild_id)
        self.kind = str(kind)

    async def _allowed(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This preview is not for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Submit", style=discord.ButtonStyle.success)
    async def submit(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not await self._allowed(interaction):
            return
        await self.cog.handle_help_submission_preview(interaction, self.guild_id, self.kind, "submit")

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.secondary)
    async def edit(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not await self._allowed(interaction):
            return
        await self.cog.handle_help_submission_preview(interaction, self.guild_id, self.kind, "edit")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not await self._allowed(interaction):
            return
        await self.cog.handle_help_submission_preview(interaction, self.guild_id, self.kind, "cancel")

    @discord.ui.button(label="Start over", style=discord.ButtonStyle.primary)
    async def start_over(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not await self._allowed(interaction):
            return
        await self.cog.handle_help_submission_preview(interaction, self.guild_id, self.kind, "start_over")


class HelpTicketTopicView(discord.ui.View):
    def __init__(self, cog, user_id: int, guild_id: int):
        super().__init__(timeout=600)
        self.cog = cog
        self.user_id = int(user_id)
        self.guild_id = int(guild_id)

    async def _allowed(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This ticket prompt is not for you.", ephemeral=True)
            return False
        return True

    def _make_topic_callback(self, key: str, label: str):
        async def _callback(interaction: discord.Interaction):
            if not await self._allowed(interaction):
                return
            await self.cog.handle_ticket_topic(interaction, self.guild_id, key, label)
        return _callback

    @discord.ui.button(label="Moderation", style=discord.ButtonStyle.secondary)
    async def moderation(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._make_topic_callback("moderation", "Moderation or punishment help")(interaction)

    @discord.ui.button(label="Level requests", style=discord.ButtonStyle.secondary)
    async def requests(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._make_topic_callback("level-requests", "Level request help")(interaction)

    @discord.ui.button(label="Server help", style=discord.ButtonStyle.secondary)
    async def server(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._make_topic_callback("server-help", "Server help")(interaction)

    @discord.ui.button(label="Other", style=discord.ButtonStyle.secondary)
    async def other(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._make_topic_callback("other", "Other staff help")(interaction)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not await self._allowed(interaction):
            return
        await self.cog.handle_ticket_topic(interaction, self.guild_id, "cancel", "Cancel")

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not await self._allowed(interaction):
            return
        await self.cog.handle_ticket_topic(interaction, self.guild_id, "back", "Back")

    @discord.ui.button(label="Start over", style=discord.ButtonStyle.primary, row=1)
    async def start_over(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not await self._allowed(interaction):
            return
        await self.cog.handle_ticket_topic(interaction, self.guild_id, "start_over", "Start over")


class TicketSatisfactionView(discord.ui.View):
    def __init__(self, cog, guild_id: int, ticket_id: int, user_id: int):
        # Discord only restores views whose timeout is None and whose
        # components have stable custom IDs. Eligibility is still limited to
        # seven days in handle_ticket_satisfaction.
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = int(guild_id)
        self.ticket_id = int(ticket_id)
        self.user_id = int(user_id)

        for score in range(1, 6):
            button = discord.ui.Button(
                label=str(score),
                style=discord.ButtonStyle.secondary,
                custom_id=f"ticket_satisfaction:{self.guild_id}:{self.ticket_id}:{score}",
            )
            button.callback = self._make_callback(score)
            self.add_item(button)

    def _make_callback(self, score: int):
        async def _callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message("This rating prompt is not for you.", ephemeral=True)
            await self.cog.handle_ticket_satisfaction(interaction, self.guild_id, self.ticket_id, score)
        return _callback


class HelpCog(commands.Cog):
    """DM help system + ticket system helpers + transcript requests."""

    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self._ticket_scan_task: Optional[asyncio.Task] = None
        self._started = False
        self._active_ticket_channels: set[int] = set()
        self._ticket_cache_ready = False
        self._last_error_log: Dict[str, float] = {}
        self._flow_start_attempts: Dict[int, list[int]] = {}
        self._ticket_create_lock = asyncio.Lock()
        self._submission_reply_lock = asyncio.Lock()
        self._transcript_request_lock = asyncio.Lock()
        self._transcript_decision_lock = asyncio.Lock()
        self._submission_preview_lock = asyncio.Lock()
        self._ticket_close_locks: Dict[int, asyncio.Lock] = {}
        self._satisfaction_lock = asyncio.Lock()
        self._satisfaction_views_registered = False

    def cog_unload(self) -> None:
        if self._ticket_scan_task and not self._ticket_scan_task.done():
            self._ticket_scan_task.cancel()

    async def start_background(self):
        if self._started and self._ticket_scan_task and not self._ticket_scan_task.done():
            return
        try:
            await self._reconcile_missing_ticket_channels()
        except Exception as e:
            await self._log_background_error("ticket_startup_reconcile", f"Ticket startup reconciliation failed: {repr(e)}")
        try:
            await self._load_active_ticket_channels()
        except Exception as e:
            await self._log_background_error("ticket_startup_cache", f"Active ticket cache load failed: {repr(e)}")
        try:
            await self._restore_ticket_satisfaction_views()
        except Exception as e:
            # Feedback restoration is useful, but it must never prevent the
            # inactivity scanner and ticket status updates from starting.
            await self._log_background_error("ticket_satisfaction_restore", f"Ticket feedback view restore failed: {repr(e)}")
        self._ticket_scan_task = asyncio.create_task(self._ticket_scan_loop())
        self._started = True

    def on_config_reload(self) -> None:
        pass

    async def _resolve_member(self, guild: discord.Guild, user_or_id) -> Optional[discord.Member]:
        if isinstance(user_or_id, discord.Member):
            return user_or_id
        user_id = getattr(user_or_id, "id", user_or_id)
        try:
            user_id = int(user_id)
        except Exception:
            return None
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except Exception:
            return None

    def _help_color(self, name: str = "blurple") -> discord.Color:
        colors = {
            "blurple": discord.Color.blurple(),
            "green": discord.Color.green(),
            "gold": discord.Color.gold(),
            "orange": discord.Color.orange(),
            "red": discord.Color.red(),
            "grey": discord.Color.dark_grey(),
            "gray": discord.Color.dark_grey(),
        }
        return colors.get(str(name or "blurple").casefold(), discord.Color.blurple())

    def _help_embed(self, title: str, description: str = "", color: str = "blurple") -> discord.Embed:
        embed = discord.Embed(
            title=title,
            description=description or None,
            color=self._help_color(color),
        )
        embed.set_footer(text="Avenue Guard help desk")
        return embed

    async def _delete_interaction_source(self, interaction: discord.Interaction) -> None:
        message = getattr(interaction, "message", None)
        if message is not None:
            try:
                await message.delete()
                return
            except Exception:
                pass
        try:
            await interaction.delete_original_response()
            return
        except Exception:
            pass

    async def _respond_interaction(self, interaction: discord.Interaction, *args, **kwargs):
        if interaction.response.is_done():
            return await interaction.followup.send(*args, **kwargs)
        return await interaction.response.send_message(*args, **kwargs)

    def _cooldowns(self) -> Dict[str, tuple[str, int]]:
        return {
            "appeal": ("Appeal punishment", 48 * 3600),
            "report_user": ("Report a user/message", 24 * 3600),
            "bot_issue": ("Report a bot issue", 6 * 3600),
            "transcript": ("Request transcript", 8 * 3600),
        }

    def _submission_label(self, kind: str) -> str:
        return {
            "appeal": "Appeal",
            "report": "Report",
            "bot_issue": "Bot issue",
        }.get(str(kind), str(kind).replace("_", " ").title())

    def _submission_prefix(self, kind: str) -> str:
        return {
            "appeal": "A",
            "report": "R",
            "bot_issue": "B",
        }.get(str(kind), "H")

    def _submission_code(self, kind: str, submission_id: int) -> str:
        return f"{self._submission_prefix(kind)}-{int(submission_id)}"

    def _attachment_data(self, message: discord.Message) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for attachment in getattr(message, "attachments", []) or []:
            out.append(
                {
                    "filename": str(getattr(attachment, "filename", "attachment") or "attachment"),
                    "url": str(getattr(attachment, "url", "") or ""),
                }
            )
        return out

    def _merge_attachments(self, data: Dict[str, Any], attachments: list[dict[str, str]]) -> None:
        existing = data.get("attachments")
        if not isinstance(existing, list):
            existing = []
        seen = {str(item.get("url") or "") for item in existing if isinstance(item, dict)}
        for item in attachments:
            url = str(item.get("url") or "")
            if url and url not in seen:
                existing.append(item)
                seen.add(url)
        data["attachments"] = existing[:10]

    def _attachments_text(self, data: Dict[str, Any]) -> str:
        attachments = data.get("attachments")
        if not isinstance(attachments, list) or not attachments:
            return "No attachments provided."
        lines = []
        for idx, item in enumerate(attachments[:10], start=1):
            if not isinstance(item, dict):
                continue
            filename = str(item.get("filename") or f"Attachment {idx}")[:80]
            url = str(item.get("url") or "")
            if url:
                lines.append(f"{idx}. [{filename}]({url})")
        return "\n".join(lines)[:1024] or "No attachments provided."

    def _has_attachments(self, data: Dict[str, Any]) -> bool:
        attachments = data.get("attachments")
        return isinstance(attachments, list) and bool(attachments)

    def _short_text(self, value: Any, limit: int = 700) -> str:
        text = str(value or "").strip()
        if not text:
            return "Not provided."
        return text[: limit - 3] + "..." if len(text) > limit else text

    def _embed_char_count(self, embed: discord.Embed) -> int:
        total = len(str(embed.title or "")) + len(str(embed.description or ""))
        total += len(str(getattr(embed.footer, "text", "") or ""))
        total += len(str(getattr(embed.author, "name", "") or ""))
        for field in embed.fields:
            total += len(str(field.name or "")) + len(str(field.value or ""))
        return total

    def _add_bounded_field(
        self,
        embed: discord.Embed,
        *,
        name: str,
        value: str,
        inline: bool = False,
        total_limit: int = 5800,
    ) -> bool:
        name = str(name or "\u200b")[:256]
        value = str(value or "\u200b")[:1024]
        if self._embed_char_count(embed) + len(name) + len(value) > total_limit:
            return False
        embed.add_field(name=name, value=value, inline=inline)
        return True

    def _normalize_duplicate_text(self, value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "").casefold()).strip()

    # -----------------------------
    # Ticket inactivity scanning
    # -----------------------------
    async def _ticket_scan_loop(self):
        while True:
            try:
                await asyncio.sleep(600)  # every 10 minutes
                await self._scan_tickets()
            except asyncio.CancelledError:
                return
            except Exception as e:
                await self._log_background_error("ticket_scan", f"Ticket scan loop error: {repr(e)}")

    async def _log_background_error(self, key: str, message: str) -> None:
        now = time.time()
        if now - self._last_error_log.get(key, 0.0) < 300:
            return
        self._last_error_log[key] = now
        await log_error(self.bot, message)

    async def _load_active_ticket_channels(self) -> None:
        cfg = self.bot.config
        allowed_guild_id = cfg.get_int("guild", "allowed_guild_id")
        if not allowed_guild_id:
            self._ticket_cache_ready = True
            return
        rows = await self.bot.db.fetchall(
            "SELECT channel_id FROM tickets WHERE guild_id=? AND status IN ('open','closing_prompted')",
            (allowed_guild_id,),
        )
        self._active_ticket_channels = {int(row["channel_id"]) for row in rows}
        self._ticket_cache_ready = True

    async def _reconcile_missing_ticket_channels(self) -> None:
        """Close DB ticket rows whose Discord channels no longer exist."""
        guild_id = self.bot.config.get_int("guild", "allowed_guild_id")
        guild = self.bot.get_guild(guild_id) if guild_id else None
        if guild is None:
            return
        rows = await self.bot.db.fetchall(
            "SELECT channel_id FROM tickets WHERE guild_id=? AND status IN ('open','closing_prompted')",
            (guild.id,),
        )
        missing: list[int] = []
        for row in rows:
            channel_id = int(row["channel_id"])
            channel = guild.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await guild.fetch_channel(channel_id)
                except discord.NotFound:
                    channel = None
                except Exception as e:
                    await self._log_background_error(
                        "ticket_reconcile_fetch",
                        f"Ticket reconciliation could not fetch channel_id={channel_id}: {repr(e)}",
                    )
                    continue
            if not isinstance(channel, discord.TextChannel):
                missing.append(channel_id)
        if not missing:
            return
        now_ts = int(time.time())
        await self.bot.db.executemany(
            "UPDATE tickets SET status='closed', status_tag='resolved', closed_ts=COALESCE(closed_ts, ?), "
            "closing_prompt_message_id=NULL WHERE guild_id=? AND channel_id=? AND status IN ('open','closing_prompted')",
            [(now_ts, guild.id, channel_id) for channel_id in missing],
        )
        for channel_id in missing:
            self._active_ticket_channels.discard(channel_id)

    async def _scan_tickets(self):
        cfg = self.bot.config
        allowed_guild_id = cfg.get_int("guild", "allowed_guild_id")
        guild = self.bot.get_guild(allowed_guild_id) if allowed_guild_id else None
        if guild is None:
            return

        inactivity_hours = int(cfg.get("tickets", "ticket_inactivity_hours", default=24) or 24)
        cutoff = int(time.time()) - inactivity_hours * 3600

        rows = await self.bot.db.fetchall(
            "SELECT channel_id FROM tickets WHERE guild_id=? AND status='open' AND last_user_activity_ts<=?",
            (guild.id, cutoff),
        )
        for r in rows:
            channel_id = int(r["channel_id"])
            channel = guild.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await guild.fetch_channel(channel_id)
                except discord.NotFound:
                    channel = None
                except Exception as e:
                    await self._log_background_error(
                        "ticket_scan_channel_fetch",
                        f"Ticket scan could not fetch channel_id={channel_id}: {repr(e)}",
                    )
                    continue
            if not isinstance(channel, discord.TextChannel):
                self._active_ticket_channels.discard(channel_id)
                await self.bot.db.execute(
                    "UPDATE tickets SET status='closed', status_tag='resolved', closed_ts=COALESCE(closed_ts, ?) "
                    "WHERE guild_id=? AND channel_id=? AND status IN ('open','closing_prompted')",
                    (int(time.time()), guild.id, channel_id),
                )
                continue
            prompt = None
            try:
                prompt = await channel.send(
                    "Do you want to close the ticket?",
                    view=TicketClosePromptView(),
                    allowed_mentions=no_mentions(),
                )
                await self.bot.db.execute(
                    "UPDATE tickets SET status='closing_prompted', closing_prompt_message_id=? WHERE channel_id=?",
                    (prompt.id, channel_id),
                )
                self._active_ticket_channels.add(channel_id)
            except Exception as e:
                if prompt is not None:
                    try:
                        await prompt.delete()
                    except discord.NotFound:
                        pass
                    except Exception:
                        pass
                await self._log_background_error(
                    "ticket_scan_prompt",
                    f"Ticket inactivity prompt failed for channel_id={channel_id}: {repr(e)}",
                )

    # -----------------------------
    # Listener: ticket activity + DM help
    # -----------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        cfg = self.bot.config
        allowed_guild_id = cfg.get_int("guild", "allowed_guild_id")
        guild = self.bot.get_guild(allowed_guild_id) if allowed_guild_id else None

        # Ticket activity in guild
        if message.guild is not None and ensure_allowed_guild_id(message.guild, allowed_guild_id):
            if await self._handle_staff_help_reply(message):
                return
            if not self._ticket_cache_ready:
                await self._load_active_ticket_channels()
            if message.channel.id not in self._active_ticket_channels:
                return
            row = await self.bot.db.fetchone(
                "SELECT status, creator_id, closing_prompt_message_id FROM tickets WHERE channel_id=?",
                (message.channel.id,),
            )
            if row and row["status"] in ("open", "closing_prompted"):
                status_tag = "waiting_staff" if int(row["creator_id"] or 0) == message.author.id else "waiting_user"
                await self.bot.db.execute(
                    "UPDATE tickets SET last_user_activity_ts=?, status='open', status_tag=?, "
                    "closing_prompt_message_id=NULL WHERE channel_id=?",
                    (int(time.time()), status_tag, message.channel.id),
                )
                prompt_message_id = int(row["closing_prompt_message_id"] or 0)
                if prompt_message_id:
                    try:
                        prompt = await message.channel.fetch_message(prompt_message_id)
                        await prompt.edit(content="Ticket activity resumed.", view=None, allowed_mentions=no_mentions())
                    except discord.NotFound:
                        pass
                    except Exception as e:
                        await self._log_background_error(
                            "ticket_prompt_cleanup",
                            f"Could not disable stale ticket prompt message_id={prompt_message_id}: {repr(e)}",
                        )
                await self.update_ticket_opening_status(message.guild, message.channel.id, status_tag)
            else:
                self._active_ticket_channels.discard(message.channel.id)
            return

        # DM help
        if message.guild is None:
            if guild is None:
                return
            member = await self._resolve_member(guild, message.author.id)
            if member is None:
                return  # ignore DMs from non-members

            tracking = self.bot.get_cog("TrackingCog")
            if tracking and await tracking.user_in_weekly_process(message.author.id):
                return

            if await self._handle_help_session_message(guild, message):
                return

            content = (message.content or "").strip()
            if content.casefold().startswith(("faq ", "search ")):
                query = re.sub(r"^(faq|search)\s+", "", content, flags=re.I).strip()
                if query:
                    await self._send_faq_search_results(message.channel, query)
                    return
            if content and len(content) <= 60:
                matches = self._faq_matches(content)
                if matches:
                    await self._send_faq_search_results(message.channel, content)
                    return

            try:
                await self._send_dm_dashboard(message.channel, guild, message.author.id)
            except Exception:
                pass

    # -----------------------------
    # Cooldowns (help actions)
    # -----------------------------
    async def _remaining_help_cooldown(self, guild_id: int, user_id: int, action: str, cooldown_seconds: int) -> int:
        row = await self.bot.db.fetchone(
            "SELECT last_used_ts FROM help_cooldowns WHERE guild_id=? AND user_id=? AND action=?",
            (guild_id, user_id, action),
        )
        if not row:
            return 0
        last_ts = int(row["last_used_ts"])
        return max(0, cooldown_seconds - (int(time.time()) - last_ts))

    async def _touch_help_cooldown(self, guild_id: int, user_id: int, action: str) -> None:
        await self.bot.db.execute(
            "INSERT INTO help_cooldowns(guild_id,user_id,action,last_used_ts) VALUES(?,?,?,?) "
            "ON CONFLICT(guild_id,user_id,action) DO UPDATE SET last_used_ts=excluded.last_used_ts",
            (guild_id, user_id, action, int(time.time())),
        )

    async def _cooldown_until(self, guild_id: int, user_id: int, action: str, cooldown_seconds: int) -> int:
        row = await self.bot.db.fetchone(
            "SELECT last_used_ts FROM help_cooldowns WHERE guild_id=? AND user_id=? AND action=?",
            (guild_id, user_id, action),
        )
        if not row:
            return 0
        until_ts = int(row["last_used_ts"]) + int(cooldown_seconds)
        return until_ts if until_ts > int(time.time()) else 0

    async def _cooldown_embed(self, guild_id: int, user_id: int, action: str, title: str, seconds: int) -> discord.Embed:
        until_ts = await self._cooldown_until(guild_id, user_id, action, seconds)
        embed = self._help_embed("Still cooling down", color="orange")
        if until_ts:
            embed.description = f"`{title}` will be available <t:{until_ts}:R>.\nYou can still use the dashboard, FAQ, status, or open an existing ticket."
        else:
            embed.description = f"`{title}` should be available now. Please try again."
        return embed

    def _flow_start_limit_message(self, user_id: int) -> str:
        now = int(time.time())
        try:
            window = max(10, int(self.bot.config.get("help", "flow_start_window_seconds", default=60) or 60))
        except Exception:
            window = 60
        try:
            max_starts = max(1, int(self.bot.config.get("help", "max_flow_starts_per_window", default=6) or 6))
        except Exception:
            max_starts = 6
        attempts = [ts for ts in self._flow_start_attempts.get(user_id, []) if now - int(ts) < window]
        if len(self._flow_start_attempts) > 5000:
            stale_users = [uid for uid, values in self._flow_start_attempts.items() if not values or now - int(values[-1]) >= window]
            for stale_user_id in stale_users[:1000]:
                self._flow_start_attempts.pop(stale_user_id, None)
        if len(attempts) >= max_starts:
            self._flow_start_attempts[user_id] = attempts
            retry_ts = int(attempts[0]) + window
            return f"You're starting help flows too quickly. Try again <t:{retry_ts}:R>."
        attempts.append(now)
        self._flow_start_attempts[user_id] = attempts
        return ""

    async def _weekly_status_text(self, guild: discord.Guild, user_id: int) -> str:
        cfg = self.bot.config
        excluded_role_ids = set(cfg.get_int_list("roles", "excluded_tracking_role_id"))
        member = await self._resolve_member(guild, user_id)
        if member is None:
            return "You are not currently visible as a server member."
        if excluded_role_ids and any(r.id in excluded_role_ids for r in member.roles):
            return "You are excluded from weekly tracking."
        ws = week_start_sunday(now_madrid()).isoformat()
        tracking = self.bot.get_cog("TrackingCog")
        if tracking:
            count, rank, _eligible_total = await tracking.get_member_stats(guild, ws, member.id)
        else:
            row = await self.bot.db.fetchone(
                "SELECT count FROM activity_counts WHERE guild_id=? AND user_id=? AND week_start=?",
                (guild.id, member.id, ws),
            )
            count = int(row["count"]) if row else 0
            rank = None
        return f"**{count}** messages this week\nRank: **{f'#{rank}' if rank and rank <= 20 else 'Not in top 20'}**"

    async def _request_state_text(self, guild_id: int, user_id: int) -> str:
        row = await self.bot.db.fetchone("SELECT state, wave_id, submitted_count, request_limit, close_ts FROM level_request_state WHERE guild_id=?", (guild_id,))
        if not row:
            return "Live requests are not initialized yet."
        state = str(row["state"]).title()
        limit = "none" if row["request_limit"] is None else str(int(row["request_limit"]))
        close_text = f" | closes <t:{int(row['close_ts'])}:R>" if row["close_ts"] is not None and str(row["state"]) == "open" else ""
        submission = await self.bot.db.fetchone(
            "SELECT status, result, created_ts FROM level_request_submissions WHERE guild_id=? AND wave_id=? AND user_id=?",
            (guild_id, int(row["wave_id"]), user_id),
        )
        mine = ""
        if submission:
            result = str(submission["result"] or submission["status"] or "pending").replace("_", " ")
            mine = f"\nYour request: **{result.title()}**"
        return f"**{state}** wave {int(row['wave_id'])}{close_text}\nSubmitted: **{int(row['submitted_count'])}** / **{limit}**{mine}"

    async def _active_ticket_text(self, guild: discord.Guild, user_id: int) -> str:
        rows = await self.bot.db.fetchall(
            "SELECT channel_id, ticket_id, status, status_tag, created_ts FROM tickets WHERE guild_id=? AND creator_id=? AND status IN ('open','closing_prompted') ORDER BY created_ts DESC LIMIT 3",
            (guild.id, user_id),
        )
        if not rows:
            return "No active staff tickets."
        lines = []
        for row in rows:
            ticket = self._ticket_label(int(row["ticket_id"]) if row["ticket_id"] is not None else None, int(row["channel_id"]))
            lines.append(f"{ticket} - <#{int(row['channel_id'])}> - **{_ticket_status_label(row['status_tag'])}**")
        return "\n".join(lines)

    async def _recent_help_status_text(self, guild_id: int, user_id: int) -> str:
        lines: list[str] = []
        rows = await self.bot.db.fetchall(
            "SELECT id, kind, status, created_ts, responded_ts FROM help_submissions WHERE guild_id=? AND user_id=? ORDER BY created_ts DESC LIMIT 5",
            (guild_id, user_id),
        )
        for row in rows:
            code = self._submission_code(str(row["kind"]), int(row["id"]))
            ts = int(row["responded_ts"] or row["created_ts"])
            lines.append(f"`{code}` {self._submission_label(row['kind'])}: **{str(row['status']).title()}** (<t:{ts}:R>)")
        t_rows = await self.bot.db.fetchall(
            "SELECT ticket_id, status, created_ts FROM transcript_requests WHERE guild_id=? AND requester_id=? ORDER BY created_ts DESC LIMIT 3",
            (guild_id, user_id),
        )
        for row in t_rows:
            ticket = f"T{int(row['ticket_id'])}" if row["ticket_id"] is not None else "ticket"
            lines.append(f"`TR-{ticket}` Transcript: **{str(row['status']).title()}** (<t:{int(row['created_ts'])}:R>)")
        return "\n".join(lines[:3]) or "No recent help submissions."

    async def _cooldown_status_text(self, guild_id: int, user_id: int) -> str:
        lines = []
        for action, (label, seconds) in self._cooldowns().items():
            until_ts = await self._cooldown_until(guild_id, user_id, action, seconds)
            if until_ts:
                lines.append(f"**{label}:** <t:{until_ts}:R>")
        return "\n".join(lines) or "All help actions are available."

    async def _send_dm_dashboard(self, channel, guild: discord.Guild, user_id: int) -> None:
        embed = self._help_embed(
            "Avenue Guard Help Desk",
            "Choose what you need below, or type `faq request` to search quickly.",
            "blurple",
        )
        embed.add_field(name="Staff Ticket", value=await self._active_ticket_text(guild, user_id), inline=False)
        embed.add_field(name="Level Requests", value=await self._request_state_text(guild.id, user_id), inline=False)
        embed.add_field(name="Weekly Activity", value=await self._weekly_status_text(guild, user_id), inline=True)
        embed.add_field(name="Cooldowns", value=await self._cooldown_status_text(guild.id, user_id), inline=True)
        recent = await self._recent_help_status_text(guild.id, user_id)
        if recent != "No recent help submissions.":
            embed.add_field(name="Recent Help", value=recent, inline=False)
        await channel.send(embed=embed, view=HelpMenuView(exclude_values={"dashboard"}), allowed_mentions=no_mentions())

    def _faq_entries(self) -> list[str]:
        faq = self.bot.config.get("help", "faq", default={}) or {}
        entries = faq.get("entries", [])
        return [str(item) for item in entries if str(item or "").strip()] if isinstance(entries, list) else []

    def _faq_matches(self, query: str) -> list[str]:
        terms = [term for term in re.split(r"\s+", str(query or "").casefold()) if len(term) >= 3]
        if not terms:
            return []
        matches = []
        for entry in self._faq_entries():
            lowered = entry.casefold()
            score = sum(1 for term in terms if term in lowered)
            if score:
                matches.append((score, entry))
        matches.sort(key=lambda item: item[0], reverse=True)
        return [entry for _, entry in matches[:5]]

    async def _send_faq_search_results(self, channel, query: str, user_id: int = 0, guild_id: int = 0) -> None:
        matches = self._faq_matches(query)
        embed = self._help_embed("FAQ Search", f"Search: `{str(query)[:80]}`", "blurple")
        if matches:
            for idx, item in enumerate(matches[:4], start=1):
                embed.add_field(name=f"Match {idx}", value=self._short_text(item, 850), inline=False)
        else:
            embed.add_field(name="Matches", value="I couldn't find a matching FAQ entry. Try a simpler keyword like `request`, `collab`, `appeal`, or `ticket`.", inline=False)
        view = HelpSessionControlView(self, user_id, guild_id, allow_back=False) if user_id and guild_id else HelpMenuView(exclude_values={"faq_search"})
        await channel.send(embed=embed, view=view, allowed_mentions=no_mentions())

    async def _send_ticket_faq_suggestions(self, channel, user_id: int, guild_id: int) -> None:
        queries = ("ticket staff help", "request rules", "appeal report")
        suggestions: list[str] = []
        seen: set[str] = set()
        for query in queries:
            for entry in self._faq_matches(query):
                key = entry.casefold()
                if key in seen:
                    continue
                seen.add(key)
                suggestions.append(entry)
                if len(suggestions) >= 3:
                    break
            if len(suggestions) >= 3:
                break

        embed = self._help_embed(
            "Before Opening A Ticket",
            "These FAQ entries might answer it faster. If not, choose a ticket topic below.",
            "blurple",
        )
        if suggestions:
            for idx, item in enumerate(suggestions, start=1):
                embed.add_field(name=f"FAQ {idx}", value=self._short_text(item, 700), inline=False)
        else:
            embed.add_field(name="FAQ", value="No matching FAQ entries are configured right now.", inline=False)
        await channel.send(embed=embed, view=HelpTicketTopicView(self, user_id, guild_id), allowed_mentions=no_mentions())

    # -----------------------------
    # Menu handler (DM only)
    # -----------------------------
    async def handle_help_selection(self, interaction: discord.Interaction, value: str):
        cfg = self.bot.config
        allowed_guild_id = cfg.get_int("guild", "allowed_guild_id")
        guild = self.bot.get_guild(allowed_guild_id) if allowed_guild_id else None
        if guild is None:
            return await interaction.response.send_message("Guild not found.")

        if interaction.guild is not None:
            return await interaction.response.send_message("Please DM me to use the help menu.")

        await self._delete_interaction_source(interaction)
        try:
            await interaction.response.defer()
        except Exception:
            pass

        if value == "dashboard":
            return await self._send_dm_dashboard(interaction.channel, guild, interaction.user.id)

        if value == "faq":
            return await self._send_faq(interaction)

        if value == "faq_search":
            limit_msg = self._flow_start_limit_message(interaction.user.id)
            if limit_msg:
                return await interaction.channel.send(limit_msg, allowed_mentions=no_mentions())
            await self._start_help_session(interaction.user.id, guild.id, "faq_search", {})
            embed = self._help_embed(
                "Search FAQ",
                "Send one or two keywords, like `request`, `collab`, `appeal`, `ticket`, or `weekly`.",
            )
            return await interaction.channel.send(
                embed=embed,
                view=HelpSessionControlView(self, interaction.user.id, guild.id, allow_back=False),
                allowed_mentions=no_mentions(),
            )

        if value == "weekly_status":
            return await self._send_weekly_status(interaction, guild)

        if value == "submission_status":
            embed = self._help_embed("My Help Status", "Recent submissions and transcript requests.", "blurple")
            embed.add_field(name="Recent Items", value=await self._recent_help_status_text(guild.id, interaction.user.id), inline=False)
            embed.add_field(name="Cooldowns", value=await self._cooldown_status_text(guild.id, interaction.user.id), inline=False)
            return await interaction.channel.send(embed=embed, view=HelpMenuView(exclude_values={"submission_status"}), allowed_mentions=no_mentions())

        cds = self._cooldowns()
        if value in {"appeal", "report", "bot_issue", "transcript", "mod_contact"}:
            limit_msg = self._flow_start_limit_message(interaction.user.id)
            if limit_msg:
                return await interaction.channel.send(limit_msg, allowed_mentions=no_mentions())

        if value == "appeal":
            remaining = await self._remaining_help_cooldown(guild.id, interaction.user.id, "appeal", cds["appeal"][1])
            if remaining:
                embed = await self._cooldown_embed(guild.id, interaction.user.id, "appeal", cds["appeal"][0], cds["appeal"][1])
                return await interaction.channel.send(embed=embed, view=HelpMenuView(exclude_values={"appeal"}), allowed_mentions=no_mentions())

            await self._start_help_session(interaction.user.id, guild.id, "appeal_punishment", {})
            embed = self._help_embed(
                title="Appeal punishment",
                description="You can use our [Google form](https://forms.gle/1fgqKtyo6okiQzjBA), or continue here.\n\nIf you continue here, send the punishment you are appealing and what happened. Attach screenshots if they help. You will preview before staff sees it.",
            )
            return await interaction.channel.send(
                embed=embed,
                view=HelpSessionControlView(self, interaction.user.id, guild.id, allow_back=False),
                allowed_mentions=no_mentions(),
            )

        if value == "report":
            remaining = await self._remaining_help_cooldown(guild.id, interaction.user.id, "report_user", cds["report_user"][1])
            if remaining:
                embed = await self._cooldown_embed(guild.id, interaction.user.id, "report_user", cds["report_user"][0], cds["report_user"][1])
                return await interaction.channel.send(embed=embed, view=HelpMenuView(exclude_values={"report"}), allowed_mentions=no_mentions())

            warning = bool(cfg.get("help", "report_warning_enabled", default=True))
            text = "Send the message link if you have it, or the user ID/name, plus the reason and evidence. Attach screenshots if useful. You will preview before staff sees it."
            if warning:
                text = "**False reports can lead to punishment.**\n\n" + text

            await self._start_help_session(interaction.user.id, guild.id, "report_details", {})
            embed = self._help_embed(title="Report a user", description=text, color="orange")
            return await interaction.channel.send(
                embed=embed,
                view=HelpSessionControlView(self, interaction.user.id, guild.id, allow_back=False),
                allowed_mentions=no_mentions(),
            )

        if value == "bot_issue":
            remaining = await self._remaining_help_cooldown(guild.id, interaction.user.id, "bot_issue", cds["bot_issue"][1])
            if remaining:
                embed = await self._cooldown_embed(guild.id, interaction.user.id, "bot_issue", cds["bot_issue"][0], cds["bot_issue"][1])
                return await interaction.channel.send(embed=embed, view=HelpMenuView(exclude_values={"bot_issue"}), allowed_mentions=no_mentions())

            await self._start_help_session(interaction.user.id, guild.id, "bot_issue_details", {})
            embed = self._help_embed(
                title="Report a bot issue",
                description="Describe what broke, where it happened, and the steps to reproduce it. Attach screenshots if useful. You will preview before staff sees it.",
            )
            return await interaction.channel.send(
                embed=embed,
                view=HelpSessionControlView(self, interaction.user.id, guild.id, allow_back=False),
                allowed_mentions=no_mentions(),
            )

        if value == "transcript":
            remaining = await self._remaining_help_cooldown(guild.id, interaction.user.id, "transcript", cds["transcript"][1])
            if remaining:
                embed = await self._cooldown_embed(guild.id, interaction.user.id, "transcript", cds["transcript"][0], cds["transcript"][1])
                return await interaction.channel.send(embed=embed, view=HelpMenuView(exclude_values={"transcript"}), allowed_mentions=no_mentions())

            await self._start_help_session(interaction.user.id, guild.id, "transcript_ticket", {})
            embed = self._help_embed(
                title="Request transcript",
                description="Send your **ticket channel** (mention like <#123> or ID), or your **Ticket ID** (example: `T21`).\n\nType **cancel** to stop.",
            )
            return await interaction.channel.send(
                embed=embed,
                view=HelpSessionControlView(self, interaction.user.id, guild.id, allow_back=False),
                allowed_mentions=no_mentions(),
            )

        if value == "mod_contact":
            return await self._send_ticket_faq_suggestions(interaction.channel, interaction.user.id, guild.id)

        return await interaction.channel.send("That option isn't available yet.", allowed_mentions=no_mentions())

    async def _send_faq(self, interaction: discord.Interaction):
        cfg = self.bot.config
        faq = cfg.get("help", "faq", default={}) or {}
        title = str(faq.get("title", "FAQ") or "FAQ")
        entries = faq.get("entries", [])
        if not isinstance(entries, list):
            entries = []
        embed = self._help_embed(title, "Use Search FAQ if you want a specific topic.", "blurple")
        displayed = 0
        for idx, entry in enumerate(entries[:12], start=1):
            if not self._add_bounded_field(
                embed,
                name=f"FAQ {idx}",
                value=self._short_text(entry, 850),
            ):
                break
            displayed += 1
        if displayed < len(entries):
            remaining = len(entries) - displayed
            embed.set_footer(text=f"{remaining} more FAQ entr{'y' if remaining == 1 else 'ies'} hidden to fit Discord's message limit")
        if not entries:
            embed.description = "Not available right now, sorry"
        await interaction.channel.send(embed=embed, view=HelpMenuView(exclude_values={"faq"}), allowed_mentions=no_mentions())

    async def _send_weekly_status(self, interaction: discord.Interaction, guild: discord.Guild):
        cfg = self.bot.config
        excluded_role_ids = set(cfg.get_int_list("roles", "excluded_tracking_role_id"))
        member = await self._resolve_member(guild, interaction.user.id)
        if member is None:
            return await interaction.channel.send("You must be in the server... If you want to appeal a ban, please use our google form", allowed_mentions=no_mentions())

        if excluded_role_ids and any(r.id in excluded_role_ids for r in member.roles):
            return await interaction.channel.send("You are excluded from weekly tracking.", allowed_mentions=no_mentions())

        ws = week_start_sunday(now_madrid()).isoformat()
        tracking = self.bot.get_cog("TrackingCog")
        if tracking:
            count, rank, _eligible_total = await tracking.get_member_stats(guild, ws, member.id)
        else:
            row = await self.bot.db.fetchone(
                "SELECT count FROM activity_counts WHERE guild_id=? AND user_id=? AND week_start=?",
                (guild.id, member.id, ws),
            )
            count = int(row["count"]) if row else 0
            rows = await self.bot.db.fetchall(
                "SELECT user_id, count FROM activity_counts WHERE guild_id=? AND week_start=? ORDER BY count DESC LIMIT 20",
                (guild.id, ws),
            )
            rank = None
            eligible_rank = 0
            for r in rows:
                candidate = guild.get_member(int(r["user_id"]))
                if candidate is None or candidate.bot:
                    continue
                if excluded_role_ids and any(role.id in excluded_role_ids for role in candidate.roles):
                    continue
                eligible_rank += 1
                if candidate.id == member.id:
                    rank = eligible_rank
                    break

        embed = self._help_embed("Weekly status", color="blurple")
        embed.add_field(name="Messages counted", value=str(count), inline=True)
        embed.add_field(name="Top 20 rank", value=(f"#{rank}" if rank and rank <= 20 else "Not in top 20"), inline=True)
        await interaction.channel.send(embed=embed, view=HelpMenuView(exclude_values={"weekly_status"}), allowed_mentions=no_mentions())

    # -----------------------------
    # Help sessions
    # -----------------------------
    async def _start_help_session(self, user_id: int, guild_id: int, stage: str, data: Dict[str, Any]):
        await self.bot.db.execute(
            "INSERT INTO help_sessions(guild_id,user_id,stage,created_ts,data_json) VALUES(?,?,?,?,?) "
            "ON CONFLICT(guild_id,user_id) DO UPDATE SET stage=excluded.stage, created_ts=excluded.created_ts, data_json=excluded.data_json",
            (guild_id, user_id, stage, int(time.time()), json.dumps(data)),
        )

    async def _clear_help_session(self, user_id: int, guild_id: int):
        await self.bot.db.execute("DELETE FROM help_sessions WHERE guild_id=? AND user_id=?", (guild_id, user_id))

    async def _get_help_session(self, user_id: int, guild_id: int) -> Optional[Dict[str, Any]]:
        row = await self.bot.db.fetchone(
            "SELECT stage, created_ts, data_json FROM help_sessions WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )
        if not row:
            return None
        try:
            lifetime = max(
                300,
                min(24 * 3600, int(self.bot.config.get("help", "session_timeout_seconds", default=3600) or 3600)),
            )
        except Exception:
            lifetime = 3600
        if int(time.time()) - int(row["created_ts"] or 0) > lifetime:
            await self._clear_help_session(user_id, guild_id)
            return None
        try:
            data = json.loads(row["data_json"] or "{}")
        except Exception:
            data = {}
        return {"stage": str(row["stage"]), "data": data}

    def _preview_stage(self, kind: str) -> str:
        return f"preview_{kind}"

    def _edit_stage_for_kind(self, kind: str) -> str:
        return {
            "appeal": "appeal_reason",
            "report": "report_details",
            "bot_issue": "bot_issue_details",
        }.get(kind, "")

    def _submission_core_text(self, kind: str, data: Dict[str, Any]) -> str:
        if kind == "report":
            return str(data.get("report") or "")
        if kind == "bot_issue":
            return str(data.get("issue") or "")
        if kind == "appeal":
            return f"{data.get('punishment', '')}\n{data.get('reason', '')}"
        return json.dumps(data, sort_keys=True)

    def _submission_preview_embed(self, kind: str, data: Dict[str, Any]) -> discord.Embed:
        embed = self._help_embed(f"Review {self._submission_label(kind)}", "Check the details before staff sees them.", "gold")
        if kind == "appeal":
            embed.add_field(name="Punishment / What happened", value=self._short_text(data.get("punishment"), 1024), inline=False)
            embed.add_field(name="Why it should be lifted", value=self._short_text(data.get("reason"), 1024), inline=False)
        elif kind == "report":
            embed.add_field(name="Report details", value=self._short_text(data.get("report"), 1024), inline=False)
        elif kind == "bot_issue":
            embed.add_field(name="Issue details", value=self._short_text(data.get("issue"), 1024), inline=False)
        if self._has_attachments(data):
            embed.add_field(name="Attachments", value=self._attachments_text(data), inline=False)
        embed.set_footer(text="Submit sends this to staff. Edit rewrites the last answer.")
        return embed

    async def _show_submission_preview(self, channel, user_id: int, guild_id: int, kind: str, data: Dict[str, Any]) -> None:
        await self._start_help_session(user_id, guild_id, self._preview_stage(kind), data)
        await channel.send(
            embed=self._submission_preview_embed(kind, data),
            view=HelpSubmissionPreviewView(self, user_id, guild_id, kind),
            allowed_mentions=no_mentions(),
        )

    async def _is_duplicate_help_submission(self, guild_id: int, user_id: int, kind: str, data: Dict[str, Any]) -> bool:
        if kind not in {"report", "bot_issue"}:
            return False
        try:
            window_hours = max(1, int(self.bot.config.get("help", "duplicate_window_hours", default=24) or 24))
        except Exception:
            window_hours = 24
        cutoff = int(time.time()) - window_hours * 3600
        new_text = self._normalize_duplicate_text(self._submission_core_text(kind, data))
        if not new_text:
            return False
        rows = await self.bot.db.fetchall(
            "SELECT data_json FROM help_submissions WHERE guild_id=? AND user_id=? AND kind=? AND created_ts>=? ORDER BY created_ts DESC LIMIT 10",
            (guild_id, user_id, kind, cutoff),
        )
        for row in rows:
            try:
                old_data = json.loads(row["data_json"] or "{}")
            except Exception:
                old_data = {}
            if self._normalize_duplicate_text(self._submission_core_text(kind, old_data)) == new_text:
                return True
        return False

    async def _submission_log_channel(self, guild: discord.Guild, kind: str) -> Optional[discord.TextChannel]:
        key = {
            "appeal": "appeals_log_channel_id",
            "report": "reports_log_channel_id",
            "bot_issue": "bot_issues_log_channel_id",
        }.get(kind)
        if not key:
            return None
        channel_id = self.bot.config.get_int("channels", key)
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None and channel_id:
            try:
                channel = await guild.fetch_channel(channel_id)
            except Exception:
                channel = None
        return channel if isinstance(channel, discord.TextChannel) else None

    def _submission_staff_embed(self, guild: discord.Guild, user_id: int, kind: str, submission_id: int, data: Dict[str, Any]) -> discord.Embed:
        color = {"appeal": discord.Color.gold(), "report": discord.Color.orange(), "bot_issue": discord.Color.blurple()}.get(kind, discord.Color.blurple())
        embed = self._staff_log_embed(
            guild,
            f"{self._submission_label(kind)} {self._submission_code(kind, submission_id)}",
            f"<@{user_id}> submitted a {self._submission_label(kind).casefold()}",
            color,
        )
        embed.add_field(name="Submitter", value=f"<@{user_id}>\n`{user_id}`", inline=True)
        embed.add_field(name="Status", value="Pending staff response", inline=True)
        embed.add_field(name="Help ID", value=f"`{self._submission_code(kind, submission_id)}`", inline=True)
        if kind == "appeal":
            embed.add_field(name="Punishment / What happened", value=self._short_text(data.get("punishment"), 1024), inline=False)
            embed.add_field(name="Why lift?", value=self._short_text(data.get("reason"), 1024), inline=False)
        elif kind == "report":
            embed.add_field(name="Details", value=self._short_text(data.get("report"), 1024), inline=False)
        elif kind == "bot_issue":
            embed.add_field(name="Issue", value=self._short_text(data.get("issue"), 1024), inline=False)
        if self._has_attachments(data):
            embed.add_field(name="Attachments", value=self._attachments_text(data), inline=False)
        embed.set_footer(text="Reply to this message to DM the submitter and mark it responded")
        return embed

    async def _insert_help_submission(self, guild_id: int, user_id: int, kind: str, data: Dict[str, Any]) -> int:
        now = int(time.time())
        return await self.bot.db.execute_insert(
            "INSERT INTO help_submissions(guild_id,kind,user_id,status,created_ts,updated_ts,data_json) VALUES(?,?,?,?,?,?,?)",
            (guild_id, kind, user_id, "pending", now, now, json.dumps(data, separators=(",", ":"))),
        )

    async def _submit_help_submission(self, guild: discord.Guild, user_id: int, kind: str, data: Dict[str, Any]) -> tuple[bool, str, str]:
        if await self._is_duplicate_help_submission(guild.id, user_id, kind, data):
            return False, "This looks like a duplicate of a recent submission. Add new details or wait before sending it again.", ""
        channel = await self._submission_log_channel(guild, kind)
        if channel is None:
            await log_error(self.bot, f"{kind} submission failed: configured log channel is missing or invalid.")
            return False, "I couldn't send this to staff because the log channel is not configured correctly.", ""

        submission_id = await self._insert_help_submission(guild.id, user_id, kind, data)
        if not submission_id:
            return False, "I couldn't create a help submission ID. Please try again.", ""
        code = self._submission_code(kind, submission_id)
        msg = None
        try:
            msg = await channel.send(
                embed=self._submission_staff_embed(guild, user_id, kind, submission_id, data),
                allowed_mentions=no_mentions(),
            )
            await self.bot.db.execute(
                "UPDATE help_submissions SET log_channel_id=?, log_message_id=?, updated_ts=? WHERE id=?",
                (channel.id, msg.id, int(time.time()), submission_id),
            )
        except Exception as e:
            await log_error(self.bot, f"{kind} submission failed: {repr(e)}")
            if msg is not None:
                try:
                    await msg.delete()
                except Exception:
                    pass
            await self.bot.db.execute(
                "UPDATE help_submissions SET status='failed', updated_ts=? WHERE id=?",
                (int(time.time()), submission_id),
            )
            return False, "I created the submission but couldn't send it to staff. Please contact staff directly.", code

        action = {"appeal": "appeal", "report": "report_user", "bot_issue": "bot_issue"}.get(kind)
        if action:
            await self._touch_help_cooldown(guild.id, user_id, action)
        await self._log_help_action(guild, user_id, f"{kind}_submitted", f"id={code}")
        return True, f"Sent to staff as `{code}`. You can check it later from **My submissions**.", code

    def _help_max_submission_chars(self) -> int:
        try:
            return max(500, min(5000, int(self.bot.config.get("help", "max_submission_chars", default=3000) or 3000)))
        except Exception:
            return 3000

    async def _handle_help_session_message(self, guild: discord.Guild, message: discord.Message) -> bool:
        sess = await self._get_help_session(message.author.id, guild.id)
        if not sess:
            return False

        stage = sess["stage"]
        data = sess["data"]
        content = (message.content or "").strip()

        if content.casefold() in {"cancel", "stop", "never mind", "nevermind"}:
            await self._clear_help_session(message.author.id, guild.id)
            await message.channel.send("Cancelled.", view=HelpMenuView(), allowed_mentions=no_mentions())
            return True

        if content.casefold() in {"back", "go back"}:
            await self._handle_typed_back(guild, message)
            return True

        if content.casefold() in {"start over", "restart help", "home", "dashboard"}:
            await self._clear_help_session(message.author.id, guild.id)
            await self._send_dm_dashboard(message.channel, guild, message.author.id)
            return True

        if len(content) > self._help_max_submission_chars():
            await message.channel.send(
                f"That message is too long. Please keep it under {self._help_max_submission_chars()} characters.",
                view=HelpSessionControlView(self, message.author.id, guild.id, allow_back=True),
                allowed_mentions=no_mentions(),
            )
            return True

        if stage == "faq_search":
            await self._send_faq_search_results(message.channel, content, message.author.id, guild.id)
            return True

        if stage == "appeal_punishment":
            data["punishment"] = content
            self._merge_attachments(data, self._attachment_data(message))
            await self._start_help_session(message.author.id, guild.id, "appeal_reason", data)
            embed = self._help_embed(
                "Appeal punishment",
                "Why should this punishment be lifted? Add context staff should know. Attach evidence if useful.",
                "gold",
            )
            await message.channel.send(
                embed=embed,
                view=HelpSessionControlView(self, message.author.id, guild.id, allow_back=True),
                allowed_mentions=no_mentions(),
            )
            return True

        if stage == "appeal_reason":
            data["reason"] = content
            self._merge_attachments(data, self._attachment_data(message))
            await self._show_submission_preview(message.channel, message.author.id, guild.id, "appeal", data)
            return True

        if stage == "report_details":
            data["report"] = content
            self._merge_attachments(data, self._attachment_data(message))
            await self._show_submission_preview(message.channel, message.author.id, guild.id, "report", data)
            return True

        if stage == "bot_issue_details":
            data["issue"] = content
            self._merge_attachments(data, self._attachment_data(message))
            await self._show_submission_preview(message.channel, message.author.id, guild.id, "bot_issue", data)
            return True

        if stage == "transcript_ticket":
            ticket_channel_id, ticket_id = self._parse_ticket_reference(content)
            if ticket_channel_id is None and ticket_id is None:
                await message.channel.send(
                    "Couldn't parse that. Send a ticket channel mention/ID or Ticket ID like `T123`.",
                    view=HelpSessionControlView(self, message.author.id, guild.id, allow_back=False),
                    allowed_mentions=no_mentions(),
                )
                return True

            row = None
            if ticket_channel_id is not None:
                row = await self.bot.db.fetchone(
                    "SELECT ticket_id, channel_id, creator_id FROM tickets WHERE channel_id=?",
                    (ticket_channel_id,),
                )
            elif ticket_id is not None:
                row = await self.bot.db.fetchone(
                    "SELECT ticket_id, channel_id, creator_id FROM tickets WHERE guild_id=? AND ticket_id=?",
                    (guild.id, ticket_id),
                )

            if not row:
                await message.channel.send(
                    "I couldn't find that ticket :/",
                    view=HelpSessionControlView(self, message.author.id, guild.id, allow_back=False),
                    allowed_mentions=no_mentions(),
                )
                return True

            if int(row["creator_id"]) != message.author.id:
                await message.channel.send(
                    "That is not your ticket though",
                    view=HelpSessionControlView(self, message.author.id, guild.id, allow_back=False),
                    allowed_mentions=no_mentions(),
                )
                return True

            t_id = int(row["ticket_id"]) if row["ticket_id"] is not None else None
            ch_id = int(row["channel_id"])

            ok, reason = await self._create_transcript_request(
                guild,
                requester_id=message.author.id,
                ticket_channel_id=ch_id,
                ticket_id=t_id,
            )
            await self._clear_help_session(message.author.id, guild.id)

            if ok:
                await self._touch_help_cooldown(guild.id, message.author.id, "transcript")
                await message.channel.send("Your transcript request has been sent to staff for approval, thanks for your patience!")
            else:
                await message.channel.send(reason or "Transcript request failed.")
            return True

        if stage.startswith("preview_"):
            kind = stage.removeprefix("preview_")
            await message.channel.send(
                "Use the preview buttons to submit, edit, cancel, or start over.",
                embed=self._submission_preview_embed(kind, data),
                view=HelpSubmissionPreviewView(self, message.author.id, guild.id, kind),
                allowed_mentions=no_mentions(),
            )
            return True

        await self._clear_help_session(message.author.id, guild.id)
        return False

    async def _handle_typed_back(self, guild: discord.Guild, message: discord.Message) -> None:
        sess = await self._get_help_session(message.author.id, guild.id)
        if not sess:
            await self._send_dm_dashboard(message.channel, guild, message.author.id)
            return
        stage = str(sess["stage"])
        data = sess["data"]
        if stage == "appeal_reason":
            await self._start_help_session(message.author.id, guild.id, "appeal_punishment", data)
            embed = self._help_embed("Appeal punishment", "Back to the first appeal step. Send the punishment and what happened.", "gold")
            await message.channel.send(embed=embed, view=HelpSessionControlView(self, message.author.id, guild.id, allow_back=False), allowed_mentions=no_mentions())
            return
        if stage.startswith("preview_"):
            kind = stage.removeprefix("preview_")
            edit_stage = self._edit_stage_for_kind(kind)
            if edit_stage:
                await self._start_help_session(message.author.id, guild.id, edit_stage, data)
                await message.channel.send(
                    embed=self._edit_prompt_embed(kind),
                    view=HelpSessionControlView(self, message.author.id, guild.id, allow_back=True),
                    allowed_mentions=no_mentions(),
                )
                return
        await self._clear_help_session(message.author.id, guild.id)
        await self._send_dm_dashboard(message.channel, guild, message.author.id)

    def _edit_prompt_embed(self, kind: str) -> discord.Embed:
        if kind == "appeal":
            return self._help_embed("Edit appeal", "Send the appeal reason again. It will replace the previous reason.", "gold")
        if kind == "report":
            return self._help_embed("Edit report", "Send the full report again. Include message links, user IDs, evidence, and attachments if useful.", "orange")
        if kind == "bot_issue":
            return self._help_embed("Edit bot issue", "Send the full bug report again, including steps to reproduce and screenshots if useful.", "blurple")
        return self._help_embed("Edit submission", "Send the updated text.")

    async def handle_help_session_control(self, interaction: discord.Interaction, guild_id: int, action: str) -> None:
        guild = self.bot.get_guild(int(guild_id))
        if guild is None:
            return await interaction.response.send_message("Guild not found.")
        await self._delete_interaction_source(interaction)
        try:
            await interaction.response.defer()
        except Exception:
            pass
        if action == "cancel":
            await self._clear_help_session(interaction.user.id, guild.id)
            return await interaction.channel.send("Cancelled.", view=HelpMenuView(), allowed_mentions=no_mentions())
        if action == "start_over":
            await self._clear_help_session(interaction.user.id, guild.id)
            return await self._send_dm_dashboard(interaction.channel, guild, interaction.user.id)
        if action == "back":
            sess = await self._get_help_session(interaction.user.id, guild.id)
            if not sess:
                return await self._send_dm_dashboard(interaction.channel, guild, interaction.user.id)
            stage = str(sess["stage"])
            data = sess["data"]
            if stage == "appeal_reason":
                await self._start_help_session(interaction.user.id, guild.id, "appeal_punishment", data)
                embed = self._help_embed("Appeal punishment", "Back to the first appeal step. Send the punishment and what happened.", "gold")
                return await interaction.channel.send(
                    embed=embed,
                    view=HelpSessionControlView(self, interaction.user.id, guild.id, allow_back=False),
                    allowed_mentions=no_mentions(),
                )
            if stage.startswith("preview_"):
                kind = stage.removeprefix("preview_")
                edit_stage = self._edit_stage_for_kind(kind)
                if edit_stage:
                    await self._start_help_session(interaction.user.id, guild.id, edit_stage, data)
                    return await interaction.channel.send(
                        embed=self._edit_prompt_embed(kind),
                        view=HelpSessionControlView(self, interaction.user.id, guild.id, allow_back=True),
                        allowed_mentions=no_mentions(),
                    )
            await self._clear_help_session(interaction.user.id, guild.id)
            return await self._send_dm_dashboard(interaction.channel, guild, interaction.user.id)

    async def handle_help_submission_preview(self, interaction: discord.Interaction, guild_id: int, kind: str, action: str) -> None:
        async with self._submission_preview_lock:
            return await self._handle_help_submission_preview_locked(interaction, guild_id, kind, action)

    async def _handle_help_submission_preview_locked(
        self,
        interaction: discord.Interaction,
        guild_id: int,
        kind: str,
        action: str,
    ) -> None:
        guild = self.bot.get_guild(int(guild_id))
        if guild is None:
            return await interaction.response.send_message("Guild not found.")
        await self._delete_interaction_source(interaction)
        try:
            await interaction.response.defer()
        except Exception:
            pass
        sess = await self._get_help_session(interaction.user.id, guild.id)
        data = sess["data"] if sess else {}
        if action == "cancel":
            await self._clear_help_session(interaction.user.id, guild.id)
            return await interaction.channel.send("Cancelled.", view=HelpMenuView(), allowed_mentions=no_mentions())
        if action == "start_over":
            await self._clear_help_session(interaction.user.id, guild.id)
            return await self._send_dm_dashboard(interaction.channel, guild, interaction.user.id)
        expected_stage = self._preview_stage(kind)
        if not sess or str(sess.get("stage") or "") != expected_stage:
            embed = self._help_embed(
                "Preview expired",
                "That preview is no longer active. Start again from the help dashboard so an old button cannot submit stale information.",
                "orange",
            )
            return await interaction.channel.send(embed=embed, view=HelpMenuView(), allowed_mentions=no_mentions())
        if action == "edit":
            edit_stage = self._edit_stage_for_kind(kind)
            if not edit_stage:
                return await interaction.channel.send("I don't know how to edit that submission.", allowed_mentions=no_mentions())
            await self._start_help_session(interaction.user.id, guild.id, edit_stage, data)
            return await interaction.channel.send(
                embed=self._edit_prompt_embed(kind),
                view=HelpSessionControlView(self, interaction.user.id, guild.id, allow_back=True),
                allowed_mentions=no_mentions(),
            )
        if action != "submit":
            return await interaction.channel.send("Unknown action.", allowed_mentions=no_mentions())

        ok, message, code = await self._submit_help_submission(guild, interaction.user.id, kind, data)
        if ok:
            await self._clear_help_session(interaction.user.id, guild.id)
            embed = self._help_embed("Submitted", message, "green")
            embed.add_field(name="Help ID", value=f"`{code}`", inline=True)
            return await interaction.channel.send(embed=embed, view=HelpMenuView(), allowed_mentions=no_mentions())
        embed = self._help_embed("Not submitted", message, "orange")
        return await interaction.channel.send(
            embed=embed,
            view=HelpSubmissionPreviewView(self, interaction.user.id, guild.id, kind),
            allowed_mentions=no_mentions(),
        )

    def _parse_ticket_reference(self, text: str) -> Tuple[Optional[int], Optional[int]]:
        """Return (channel_id, ticket_id). Only one will be non-None."""
        m = re.search(r"<#(\d{15,25})>", text)
        if m:
            return int(m.group(1)), None

        m = re.search(r"\bT?(\d{1,25})\b", text.strip(), flags=re.I)
        if not m:
            return None, None

        digits = m.group(1)
        if len(digits) >= 15:
            return int(digits), None
        return None, int(digits)

    def _staff_log_embed(self, guild: discord.Guild, title: str, description: str, color: discord.Color) -> discord.Embed:
        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=now_madrid(),
        )
        try:
            if guild.icon:
                embed.set_thumbnail(url=guild.icon.url)
        except Exception:
            pass
        embed.set_footer(text="Avenue Guard staff log")
        return embed

    async def _log_help_action(self, guild: discord.Guild, user_id: int, action: str, detail: str = "") -> None:
        channel_id = self.bot.config.get_int("channels", "general_logging_channel_id", default=0)
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None and channel_id:
            try:
                channel = await guild.fetch_channel(channel_id)
            except Exception:
                channel = None
        if not isinstance(channel, discord.TextChannel):
            return
        embed = self._staff_log_embed(
            guild,
            "Help Desk Action",
            str(action).replace("_", " ").title(),
            discord.Color.blurple(),
        )
        if user_id:
            embed.add_field(name="Actor", value=f"<@{int(user_id)}>\n`{int(user_id)}`", inline=True)
        else:
            embed.add_field(name="Actor", value="System", inline=True)
        embed.add_field(name="Action", value=f"`{str(action)[:120]}`", inline=True)
        if detail:
            embed.add_field(name="Details", value=str(detail)[:1024], inline=False)
        try:
            await channel.send(embed=embed, allowed_mentions=no_mentions())
        except Exception as e:
            await log_error(self.bot, f"Could not log help action {action}: {repr(e)}")

    def _ticket_label(self, ticket_id: Optional[int], fallback_channel_id: int) -> str:
        return f"`T{ticket_id}`" if ticket_id is not None else f"`{fallback_channel_id}`"

    async def _handle_staff_help_reply(self, message: discord.Message) -> bool:
        if message.author.bot or message.guild is None:
            return False
        ref = getattr(message, "reference", None)
        ref_message_id = getattr(ref, "message_id", None)
        if not ref_message_id:
            return False
        async with self._submission_reply_lock:
            return await self._handle_staff_help_reply_locked(message, int(ref_message_id))

    async def _handle_staff_help_reply_locked(self, message: discord.Message, ref_message_id: int) -> bool:
        row = await self.bot.db.fetchone(
            "SELECT * FROM help_submissions WHERE guild_id=? AND log_channel_id=? AND log_message_id=?",
            (message.guild.id, message.channel.id, ref_message_id),
        )
        if not row:
            return False

        member = message.guild.get_member(message.author.id)
        mod_role_id = self.bot.config.get_int("roles", "MOD_ROLE_ID") or 0
        allow_manage_guild = bool(self.bot.config.get("permissions", "manage_guild_counts_as_mod", default=True))
        if member is None or not is_mod(member, mod_role_id, allow_manage_guild=allow_manage_guild):
            return False

        if str(row["status"] or "pending") != "pending":
            try:
                await message.reply("This submission has already been answered.", allowed_mentions=no_mentions())
            except Exception:
                pass
            return True

        response_text = str(message.content or "").strip()
        attachments = self._attachment_data(message)
        if not response_text and not attachments:
            try:
                await message.reply("Add text or an attachment to relay a response.", allowed_mentions=no_mentions())
            except Exception:
                pass
            return True

        kind = str(row["kind"])
        submission_id = int(row["id"])
        code = self._submission_code(kind, submission_id)
        requester_id = int(row["user_id"])
        embed = self._help_embed(f"Staff response: {code}", color="blurple")
        embed.description = f"Staff responded to your {self._submission_label(kind).casefold()}."
        if response_text:
            embed.add_field(name="Response", value=self._short_text(response_text, 1024), inline=False)
        if attachments:
            embed.add_field(name="Attachments", value=self._attachments_text({"attachments": attachments}), inline=False)

        try:
            user = await self.bot.fetch_user(requester_id)
            await user.send(embed=embed, allowed_mentions=no_mentions())
        except Exception as e:
            await log_error(self.bot, f"Could not relay staff response for {code}: {repr(e)}")
            try:
                await message.reply("I couldn't DM the user. Their DMs may be closed.", allowed_mentions=no_mentions())
            except Exception:
                pass
            return True

        now = int(time.time())
        await self.bot.db.execute(
            "UPDATE help_submissions SET status='responded', response_text=?, responded_by=?, responded_ts=?, updated_ts=? WHERE id=?",
            (response_text[:1500], message.author.id, now, now, submission_id),
        )

        try:
            original = await message.channel.fetch_message(ref_message_id)
            if original.embeds:
                original_embed = original.embeds[0]
                replaced = False
                for idx, field in enumerate(original_embed.fields):
                    if str(field.name).casefold() == "status":
                        original_embed.set_field_at(idx, name="Status", value=f"Responded by <@{message.author.id}> <t:{now}:R>", inline=True)
                        replaced = True
                        break
                if not replaced:
                    original_embed.add_field(name="Status", value=f"Responded by <@{message.author.id}> <t:{now}:R>", inline=True)
                await original.edit(embed=original_embed, allowed_mentions=no_mentions())
        except Exception as e:
            await log_error(self.bot, f"Could not update staff submission embed for {code}: {repr(e)}")

        await self._log_help_action(message.guild, message.author.id, "staff_response_relayed", f"id={code} requester={requester_id}")
        try:
            await message.reply(f"Relayed to the user and marked `{code}` as responded.", allowed_mentions=no_mentions())
        except Exception:
            pass
        return True

    # -----------------------------
    # Staff submissions
    # -----------------------------
    async def _submit_appeal(self, guild: discord.Guild, user_id: int, data: Dict[str, Any]) -> bool:
        ok, _message, _code = await self._submit_help_submission(guild, user_id, "appeal", data)
        return ok

    async def _submit_report(self, guild: discord.Guild, user_id: int, data: Dict[str, Any]) -> bool:
        ok, _message, _code = await self._submit_help_submission(guild, user_id, "report", data)
        return ok

    async def _submit_bot_issue(self, guild: discord.Guild, user_id: int, data: Dict[str, Any]) -> bool:
        ok, _message, _code = await self._submit_help_submission(guild, user_id, "bot_issue", data)
        return ok

    # -----------------------------
    # Transcript requests (staff approval)
    # -----------------------------
    async def _create_transcript_request(self, guild: discord.Guild, requester_id: int, ticket_channel_id: int, ticket_id: Optional[int]) -> Tuple[bool, str]:
        async with self._transcript_request_lock:
            return await self._create_transcript_request_locked(
                guild,
                requester_id,
                ticket_channel_id,
                ticket_id,
            )

    async def _create_transcript_request_locked(
        self,
        guild: discord.Guild,
        requester_id: int,
        ticket_channel_id: int,
        ticket_id: Optional[int],
    ) -> Tuple[bool, str]:
        cfg = self.bot.config
        req_ch_id = cfg.get_int("channels", "transcript_requests_channel_id")
        channel = guild.get_channel(req_ch_id) if req_ch_id else None
        if channel is None and req_ch_id:
            try:
                channel = await guild.fetch_channel(req_ch_id)
            except Exception:
                channel = None
        if not isinstance(channel, discord.TextChannel):
            return False, "Transcript requests channel is not configured, DM Average Hollow Knight Fan."

        if ticket_id is not None:
            existing = await self.bot.db.fetchone(
                "SELECT status FROM transcript_requests WHERE guild_id=? AND ticket_id=? ORDER BY created_ts DESC LIMIT 1",
                (guild.id, ticket_id),
            )
        else:
            existing = await self.bot.db.fetchone(
                "SELECT status FROM transcript_requests WHERE guild_id=? AND ticket_channel_id=? ORDER BY created_ts DESC LIMIT 1",
                (guild.id, ticket_channel_id),
            )

        if existing:
            status = str(existing["status"])
            if status == "pending":
                return False, "There is already a **pending** transcript request for that ticket."
            if status in ("approved", "denied"):
                return False, f"That ticket's transcript request is already **{status}**."
            if status == "delivery_failed":
                return False, "That transcript request is waiting for staff to retry delivery."

        embed = self._staff_log_embed(
            guild,
            "Transcript Request",
            f"<@{requester_id}> requested a ticket transcript",
            discord.Color.blurple(),
        )
        embed.add_field(name="Requester", value=f"<@{requester_id}>\n`{requester_id}`", inline=True)
        embed.add_field(name="Ticket", value=self._ticket_label(ticket_id, ticket_channel_id), inline=True)
        embed.add_field(name="Ticket Channel", value=f"<#{ticket_channel_id}>\n`{ticket_channel_id}`", inline=False)
        if ticket_id is not None:
            embed.add_field(name="Ticket ID", value=f"`T{ticket_id}`", inline=True)
        embed.set_footer(text="Avenue Guard staff log - approve or deny")

        msg = await channel.send(embed=embed, view=TranscriptRequestView(), allowed_mentions=no_mentions())

        try:
            await self.bot.db.execute(
                "INSERT INTO transcript_requests(guild_id, request_message_id, ticket_channel_id, requester_id, status, created_ts, ticket_id) "
                "VALUES(?,?,?,?,?,?,?)",
                (guild.id, msg.id, ticket_channel_id, requester_id, "pending", int(time.time()), ticket_id),
            )
        except Exception:
            try:
                await msg.delete()
            except Exception:
                pass
            raise
        return True, ""

    async def handle_transcript_request_decision(self, interaction: discord.Interaction, approved: bool):
        async with self._transcript_decision_lock:
            return await self._handle_transcript_request_decision_locked(interaction, approved)

    async def _handle_transcript_request_decision_locked(self, interaction: discord.Interaction, approved: bool):
        cfg = self.bot.config
        allowed_guild_id = cfg.get_int("guild", "allowed_guild_id")
        if interaction.guild is None or interaction.guild.id != allowed_guild_id:
            return await interaction.response.send_message("Wrong server.", ephemeral=True)

        mod_role_id = cfg.get_int("roles", "MOD_ROLE_ID") or 0
        member = interaction.guild.get_member(interaction.user.id)
        allow_manage_guild = bool(cfg.get("permissions", "manage_guild_counts_as_mod", default=True))
        if member is None or not is_mod(member, mod_role_id, allow_manage_guild=allow_manage_guild):
            return await interaction.response.send_message("Only mods can do that.", ephemeral=True)

        row = await self.bot.db.fetchone(
            "SELECT ticket_channel_id, requester_id, status, ticket_id FROM transcript_requests WHERE request_message_id=?",
            (interaction.message.id,),
        )
        if not row:
            return await interaction.response.send_message("Request not found.", ephemeral=True)

        current_status = str(row["status"])
        if current_status not in ("pending", "delivery_failed"):
            return await interaction.response.send_message("This request is already processed.", ephemeral=True)

        ticket_channel_id = int(row["ticket_channel_id"])
        requester_id = int(row["requester_id"])
        ticket_id = int(row["ticket_id"]) if row["ticket_id"] is not None else None

        if not approved:
            await self.bot.db.execute("UPDATE transcript_requests SET status='denied' WHERE request_message_id=?", (interaction.message.id,))
            await self._log_help_action(
                interaction.guild,
                interaction.user.id,
                "transcript_request_denied",
                f"ticket={self._ticket_label(ticket_id, ticket_channel_id)} requester={requester_id}",
            )
            try:
                await interaction.message.edit(content="Denied", view=None)
            except Exception:
                pass
            try:
                await interaction.response.send_message("Denied", ephemeral=True)
            except Exception:
                pass
            try:
                user = await self.bot.fetch_user(requester_id)
                await user.send(
                    f"Your transcript request was **denied** by staff. "
                    f"(Ticket {('T'+str(ticket_id)) if ticket_id else ticket_channel_id})",
                    allowed_mentions=no_mentions(),
                )
            except Exception as e:
                await log_error(
                    self.bot,
                    f"Could not notify requester_id={requester_id} of transcript denial: {repr(e)}",
                )
            return

        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass

        ok = await self._dm_transcript(interaction.guild, requester_id, ticket_channel_id, ticket_id)
        if not ok:
            await self.bot.db.execute(
                "UPDATE transcript_requests SET status='delivery_failed' WHERE request_message_id=?",
                (interaction.message.id,),
            )
            try:
                await interaction.message.edit(
                    content="Delivery failed. Staff can press Approve to retry.",
                    view=TranscriptRequestView(),
                )
            except Exception as e:
                await log_error(self.bot, f"Could not restore transcript retry buttons: {repr(e)}")
            await log_error(
                self.bot,
                f"Transcript delivery failed requester_id={requester_id} "
                f"ticket={self._ticket_label(ticket_id, ticket_channel_id)}",
            )
            try:
                await interaction.followup.send(
                    "I couldn't deliver the transcript. The request remains available for another attempt.",
                    ephemeral=True,
                )
            except Exception:
                pass
            return

        await self.bot.db.execute(
            "UPDATE transcript_requests SET status='approved' WHERE request_message_id=?",
            (interaction.message.id,),
        )
        await self._log_help_action(
            interaction.guild,
            interaction.user.id,
            "transcript_request_approved",
            f"ticket={self._ticket_label(ticket_id, ticket_channel_id)} requester={requester_id}",
        )
        try:
            await interaction.message.edit(content="Approved and sent", view=None)
        except Exception as e:
            await log_error(self.bot, f"Could not finalize transcript request message: {repr(e)}")
        try:
            await interaction.followup.send("Approved and delivered.", ephemeral=True)
        except Exception:
            pass

    async def _dm_transcript(self, guild: discord.Guild, requester_id: int, ticket_channel_id: int, ticket_id: Optional[int]) -> bool:
        user: Optional[discord.User]
        try:
            user = await self.bot.fetch_user(requester_id)
        except Exception:
            return False

        # If channel exists: build transcript live
        channel = guild.get_channel(ticket_channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(ticket_channel_id)
            except Exception:
                channel = None
        if isinstance(channel, discord.TextChannel):
            try:
                transcript_path = await build_text_transcript(channel)
                await user.send(
                    content=f"Here is your transcript for {channel.name} ({channel.id}).",
                    file=discord.File(transcript_path, filename=f"transcript-{ticket_id or channel.id}.txt"),
                )
                return True
            except Exception as e:
                await log_error(
                    self.bot,
                    f"Live transcript delivery failed channel_id={ticket_channel_id} requester_id={requester_id}: {repr(e)}",
                )

        # Fallback: fetch stored transcript from log channel
        if ticket_id is None:
            return False

        ptr = await self.bot.db.fetchone(
            "SELECT log_channel_id, log_message_id FROM ticket_transcripts WHERE guild_id=? AND ticket_id=?",
            (guild.id, ticket_id),
        )
        if not ptr:
            return False

        log_channel_id = int(ptr["log_channel_id"])
        log_ch = guild.get_channel(log_channel_id)
        if log_ch is None:
            try:
                log_ch = await guild.fetch_channel(log_channel_id)
            except Exception:
                log_ch = None
        if not isinstance(log_ch, discord.TextChannel):
            return False

        try:
            msg = await log_ch.fetch_message(int(ptr["log_message_id"]))
            if not msg.attachments:
                return False
            att = msg.attachments[0]
            data = await att.read()
            await user.send(
                content=f"Here is your transcript for Ticket T{ticket_id}.",
                file=discord.File(fp=io.BytesIO(data), filename=f"transcript-T{ticket_id}.txt"),
            )
            return True
        except Exception as e:
            await log_error(
                self.bot,
                f"Stored transcript delivery failed ticket_id={ticket_id} requester_id={requester_id}: {repr(e)}",
            )
            return False

    # -----------------------------
    # Ticket creation (mod contact)
    # -----------------------------
    async def handle_ticket_topic(self, interaction: discord.Interaction, guild_id: int, topic_key: str, topic_label: str):
        guild = self.bot.get_guild(int(guild_id))
        if guild is None:
            return await interaction.response.send_message("Guild not found.", ephemeral=True)
        await self._delete_interaction_source(interaction)
        if topic_key == "cancel":
            try:
                await interaction.response.defer()
            except Exception:
                pass
            return await interaction.channel.send("Cancelled.", view=HelpMenuView(), allowed_mentions=no_mentions())
        if topic_key in {"back", "start_over"}:
            try:
                await interaction.response.defer()
            except Exception:
                pass
            return await self._send_dm_dashboard(interaction.channel, guild, interaction.user.id)
        await self._create_staff_ticket(interaction, guild, topic_key, topic_label)

    async def update_ticket_opening_status(self, guild: discord.Guild, channel_id: int, status_tag: str) -> None:
        row = await self.bot.db.fetchone(
            "SELECT opening_message_id FROM tickets WHERE guild_id=? AND channel_id=?",
            (guild.id, channel_id),
        )
        if not row or row["opening_message_id"] is None:
            return
        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except Exception:
                channel = None
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            msg = await channel.fetch_message(int(row["opening_message_id"]))
        except Exception:
            return

        label = _ticket_status_label(status_tag)
        content = str(msg.content or "")
        status_text = f"Status: **{label}**"
        if re.search(r"Status:\s*\*\*[^*]+\*\*", content):
            updated = re.sub(r"Status:\s*\*\*[^*]+\*\*", status_text, content, count=1)
        elif content:
            updated = f"{content}\n{status_text}"
        else:
            updated = status_text
        if updated == content:
            return
        try:
            await msg.edit(content=updated, allowed_mentions=no_mentions())
        except Exception as e:
            await log_error(self.bot, f"Could not update ticket opening status channel_id={channel_id}: {repr(e)}")

    async def _create_staff_ticket(self, interaction: discord.Interaction, guild: discord.Guild, topic_key: str, topic_label: str):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        async with self._ticket_create_lock:
            return await self._create_staff_ticket_locked(interaction, guild, topic_key, topic_label)

    async def _create_staff_ticket_locked(self, interaction: discord.Interaction, guild: discord.Guild, topic_key: str, topic_label: str):
        cfg = self.bot.config

        member = await self._resolve_member(guild, interaction.user.id)
        if member is None:
            return await self._respond_interaction(interaction, "You must be in the server to create a ticket", ephemeral=True)

        cooldown_h = int(cfg.get("tickets", "ticket_creation_cooldown_hours", default=24) or 24)
        row = await self.bot.db.fetchone(
            "SELECT last_created_ts FROM ticket_cooldowns WHERE guild_id=? AND user_id=?",
            (guild.id, member.id),
        )
        now = int(time.time())
        if row and now - int(row["last_created_ts"]) < cooldown_h * 3600:
            remaining = cooldown_h * 3600 - (now - int(row["last_created_ts"]))
            until_ts = now + remaining
            return await self._respond_interaction(
                interaction,
                f"You can create another ticket <t:{until_ts}:R>.",
                ephemeral=True,
            )

        category_id = cfg.get_int("tickets", "ticket_category_id")
        mod_role_id = cfg.get_int("roles", "MOD_ROLE_ID")
        if not category_id or not mod_role_id:
            return await self._respond_interaction(interaction, "Ticket system is not configured (Average's fault, please contact him)", ephemeral=True)

        category = guild.get_channel(category_id)
        if not isinstance(category, discord.CategoryChannel):
            return await self._respond_interaction(interaction, "Ticket category is missing or invalid (please contact staff)", ephemeral=True)

        mod_role = guild.get_role(mod_role_id)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        if mod_role:
            overwrites[mod_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        ticket_id = await self.bot.db.next_ticket_id(guild.id)
        topic_slug = re.sub(r"[^a-z0-9-]+", "-", str(topic_key or "help").casefold()).strip("-") or "help"
        name = f"ticket-{ticket_id}-{topic_slug}-{member.name}".lower().replace(" ", "-")[:90]

        try:
            channel = await guild.create_text_channel(name=name, category=category, overwrites=overwrites, reason=f"Ticket created: {topic_label}")
        except Exception as e:
            await log_error(self.bot, f"Ticket channel creation failed for user_id={member.id}: {repr(e)}")
            return await self._respond_interaction(interaction, "Failed to create ticket (missing permissions?)", ephemeral=True)

        try:
            await self.bot.db.execute_transaction(
                (
                    (
                        "INSERT OR REPLACE INTO ticket_cooldowns(guild_id, user_id, last_created_ts) VALUES(?,?,?)",
                        (guild.id, member.id, now),
                    ),
                    (
                        "INSERT INTO tickets(guild_id, channel_id, creator_id, created_ts, last_user_activity_ts, status, ticket_id, status_tag) "
                        "VALUES(?,?,?,?,?, 'open', ?, 'waiting_staff')",
                        (guild.id, channel.id, member.id, now, now, ticket_id),
                    ),
                ),
                retry_safe=True,
            )
        except Exception as e:
            try:
                await channel.delete(reason="Ticket database setup failed")
            except Exception as cleanup_error:
                await log_error(self.bot, f"Orphan ticket channel cleanup failed channel_id={channel.id}: {repr(cleanup_error)}")
            await log_error(self.bot, f"Ticket database setup failed for channel_id={channel.id}: {repr(e)}")
            return await self._respond_interaction(
                interaction,
                "I couldn't save the new ticket, so the channel was rolled back. Please try again.",
                ephemeral=True,
            )
        self._active_ticket_channels.add(channel.id)
        await self._log_help_action(guild, member.id, "ticket_created", f"ticket=T{ticket_id} topic={topic_key} channel={channel.id}")

        # DM includes Ticket ID
        try:
            dm = await member.create_dm()
            embed = self._help_embed(
                title="Ticket created",
                description=f"Your ticket has been created: {channel.mention}\n\nTicket ID: `T{ticket_id}`\nTopic: **{topic_label}**",
                color="green",
            )
            await dm.send(embed=embed, allowed_mentions=user_mentions())
        except Exception as e:
            await log_error(self.bot, f"Ticket creation DM failed user_id={member.id} ticket_id={ticket_id}: {repr(e)}")

        try:
            await self._respond_interaction(interaction, f"Ticket created: {channel.mention}", ephemeral=True)
        except Exception as e:
            await log_error(self.bot, f"Ticket creation confirmation failed channel_id={channel.id}: {repr(e)}")

        try:
            staff_role_id = cfg.get_int("tickets", "staff_ping_role_id", default=0)
            staff_role = guild.get_role(staff_role_id) if staff_role_id else None
            staff_ping = staff_role.mention if staff_role else "staff"
            opening_msg = await channel.send(
                f"Please say what you need {member.mention}. Topic: **{topic_label}**. Status: **Waiting for staff**. {staff_ping} will be shortly with you ;)",
                allowed_mentions=user_and_role_mentions() if staff_role else user_mentions(),
            )
            await self.bot.db.execute(
                "UPDATE tickets SET opening_message_id=? WHERE guild_id=? AND channel_id=?",
                (opening_msg.id, guild.id, channel.id),
            )
        except Exception as e:
            await log_error(self.bot, f"Ticket opening message failed channel_id={channel.id}: {repr(e)}")

    async def _next_ticket_id(self, guild_id: int) -> int:
        return await self.bot.db.next_ticket_id(guild_id)

    async def handle_ticket_close_prompt(self, interaction: discord.Interaction, confirmed: bool):
        cfg = self.bot.config
        allowed_guild_id = cfg.get_int("guild", "allowed_guild_id")
        if interaction.guild is None or interaction.guild.id != allowed_guild_id:
            return await interaction.response.send_message("Wrong server.", ephemeral=True)

        mod_role_id = cfg.get_int("roles", "MOD_ROLE_ID")
        if mod_role_id:
            member = interaction.guild.get_member(interaction.user.id)
            allow_manage_guild = bool(cfg.get("permissions", "manage_guild_counts_as_mod", default=True))
            if member is None or not is_mod(member, mod_role_id, allow_manage_guild=allow_manage_guild):
                return await interaction.response.send_message("Only staff can close tickets", ephemeral=True)

        ticket_row = await self.bot.db.fetchone(
            "SELECT status, closing_prompt_message_id FROM tickets WHERE guild_id=? AND channel_id=?",
            (interaction.guild.id, interaction.channel_id),
        )
        interaction_message_id = int(getattr(interaction.message, "id", 0) or 0)
        if (
            not ticket_row
            or str(ticket_row["status"]) != "closing_prompted"
            or int(ticket_row["closing_prompt_message_id"] or 0) != interaction_message_id
        ):
            return await interaction.response.send_message("This close prompt is no longer active.", ephemeral=True)

        if not confirmed:
            await interaction.response.send_message("Keeping ticket open.", ephemeral=True)
            await self.bot.db.execute(
                "UPDATE tickets SET status='open', closing_prompt_message_id=NULL WHERE channel_id=?",
                (interaction.channel_id,),
            )
            try:
                await interaction.message.edit(content="Ticket kept open.", view=None, allowed_mentions=no_mentions())
            except Exception:
                pass
            return

        await interaction.response.send_message("Closing ticket...", ephemeral=True)
        ok = await self.close_ticket_channel(interaction.guild, interaction.channel_id)
        if not ok:
            try:
                await interaction.followup.send("I couldn't close the ticket safely. Check the ticket channel for details.", ephemeral=True)
            except Exception:
                pass

    async def _send_ticket_satisfaction_prompt(self, guild: discord.Guild, creator_id: int, ticket_id: Optional[int]) -> None:
        if not creator_id or ticket_id is None:
            return
        if not bool(self.bot.config.get("tickets", "satisfaction_enabled", default=True)):
            return
        async with self._satisfaction_lock:
            existing = await self.bot.db.fetchone(
                "SELECT satisfaction_score, satisfaction_message_id FROM tickets "
                "WHERE guild_id=? AND ticket_id=? AND creator_id=?",
                (guild.id, int(ticket_id), creator_id),
            )
            if not existing or existing["satisfaction_score"] is not None or existing["satisfaction_message_id"] is not None:
                return
            prompt = str(
                self.bot.config.get("tickets", "satisfaction_prompt", default="How was your staff ticket experience?")
                or "How was your staff ticket experience?"
            )
            user = await self._resolve_member(guild, creator_id) or self.bot.get_user(creator_id)
            if user is None:
                try:
                    user = await self.bot.fetch_user(creator_id)
                except Exception:
                    user = None
            if user is None:
                return
            embed = self._help_embed(
                "Ticket Feedback",
                f"{prompt}\n\nTicket: `T{int(ticket_id)}`\nChoose a score from **1** to **5**.",
                "blurple",
            )
            msg = None
            try:
                msg = await user.send(
                    embed=embed,
                    view=TicketSatisfactionView(self, guild.id, int(ticket_id), creator_id),
                    allowed_mentions=no_mentions(),
                )
                await self.bot.db.execute(
                    "UPDATE tickets SET satisfaction_message_id=? WHERE guild_id=? AND ticket_id=? AND creator_id=?",
                    (msg.id, guild.id, int(ticket_id), creator_id),
                )
            except Exception as e:
                if msg is not None:
                    try:
                        await msg.delete()
                    except Exception:
                        pass
                await self._log_background_error(
                    "ticket_satisfaction_prompt",
                    f"Ticket satisfaction prompt failed ticket_id={ticket_id} creator_id={creator_id}: {repr(e)}",
                )

    async def _restore_ticket_satisfaction_views(self) -> None:
        if self._satisfaction_views_registered:
            return
        guild_id = self.bot.config.get_int("guild", "allowed_guild_id")
        if not guild_id:
            self._satisfaction_views_registered = True
            return
        cutoff = int(time.time()) - 7 * 24 * 3600
        rows = await self.bot.db.fetchall(
            "SELECT ticket_id, creator_id, satisfaction_message_id FROM tickets "
            "WHERE guild_id=? AND closed_ts>=? AND satisfaction_score IS NULL "
            "AND satisfaction_message_id IS NOT NULL AND ticket_id IS NOT NULL",
            (guild_id, cutoff),
        )
        for row in rows:
            self.bot.add_view(
                TicketSatisfactionView(self, guild_id, int(row["ticket_id"]), int(row["creator_id"])),
                message_id=int(row["satisfaction_message_id"]),
            )
        self._satisfaction_views_registered = True

    async def handle_ticket_satisfaction(self, interaction: discord.Interaction, guild_id: int, ticket_id: int, score: int):
        score = max(1, min(5, int(score)))
        async with self._satisfaction_lock:
            row = await self.bot.db.fetchone(
                "SELECT closed_ts, satisfaction_score FROM tickets WHERE guild_id=? AND ticket_id=? AND creator_id=?",
                (int(guild_id), int(ticket_id), interaction.user.id),
            )
            if not row:
                return await interaction.response.send_message("That ticket feedback request could not be found.", ephemeral=True)
            if row["satisfaction_score"] is not None:
                return await interaction.response.send_message("Feedback for this ticket was already saved.", ephemeral=True)
            closed_ts = int(row["closed_ts"] or 0)
            if not closed_ts or int(time.time()) - closed_ts > 7 * 24 * 3600:
                try:
                    return await interaction.response.edit_message(
                        embed=self._help_embed("Feedback Expired", "This feedback window has closed.", "grey"),
                        view=None,
                    )
                except Exception:
                    return await interaction.response.send_message("This feedback window has closed.", ephemeral=True)
            await self.bot.db.execute(
                "UPDATE tickets SET satisfaction_score=?, satisfaction_user_id=?, satisfaction_ts=?, satisfaction_message_id=NULL "
                "WHERE guild_id=? AND ticket_id=? AND creator_id=?",
                (score, interaction.user.id, int(time.time()), int(guild_id), int(ticket_id), interaction.user.id),
            )
        embed = self._help_embed(
            "Feedback Saved",
            f"Thanks. Your **{score}/5** rating for ticket `T{int(ticket_id)}` was saved.",
            "green",
        )
        try:
            await interaction.response.edit_message(embed=embed, view=None)
        except Exception:
            await interaction.response.send_message("Feedback saved, thank you!", ephemeral=True)

    async def close_ticket_channel(self, guild: discord.Guild, channel_id: int) -> bool:
        channel_id = int(channel_id)
        lock = self._ticket_close_locks.setdefault(channel_id, asyncio.Lock())
        async with lock:
            return await self._close_ticket_channel_locked(guild, channel_id)

    async def _close_ticket_channel_locked(self, guild: discord.Guild, channel_id: int) -> bool:
        cfg = self.bot.config
        log_channel_id = cfg.get_int("channels", "general_logging_channel_id")
        log_channel = guild.get_channel(log_channel_id) if log_channel_id else None
        if log_channel is None and log_channel_id:
            try:
                log_channel = await guild.fetch_channel(log_channel_id)
            except Exception:
                log_channel = None
        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except Exception:
                channel = None

        if not isinstance(channel, discord.TextChannel):
            return False

        row = await self.bot.db.fetchone("SELECT ticket_id, creator_id, created_ts, status_tag FROM tickets WHERE channel_id=?", (channel_id,))
        ticket_id = int(row["ticket_id"]) if row and row["ticket_id"] is not None else None
        creator_id = int(row["creator_id"]) if row and row["creator_id"] is not None else 0
        created_ts = int(row["created_ts"]) if row and row["created_ts"] is not None else 0
        previous_status_tag = str(row["status_tag"] or "waiting_staff") if row else "waiting_staff"

        async def _restore_open_status() -> None:
            try:
                await self.bot.db.execute(
                    "UPDATE tickets SET status='open', status_tag=?, closed_ts=NULL, closing_prompt_message_id=NULL "
                    "WHERE channel_id=? AND status<>'closed'",
                    (previous_status_tag, channel_id),
                )
                self._active_ticket_channels.add(channel_id)
                await self.update_ticket_opening_status(guild, channel_id, previous_status_tag)
            except Exception as restore_error:
                await log_error(self.bot, f"Ticket status restore failed channel_id={channel_id}: {repr(restore_error)}")

        if not isinstance(log_channel, discord.TextChannel):
            try:
                await channel.send("I couldn't close this ticket because the transcript log channel is not configured.")
            except Exception:
                pass
            return False

        try:
            try:
                await self.bot.db.execute(
                    "UPDATE tickets SET status_tag='resolved', closed_ts=? WHERE channel_id=?",
                    (int(time.time()), channel_id),
                )
                await self.update_ticket_opening_status(guild, channel_id, "resolved")
            except Exception as status_error:
                await log_error(self.bot, f"Ticket resolved status update before transcript failed channel_id={channel_id}: {repr(status_error)}")

            transcript_path = await build_text_transcript(channel)
            embed = self._staff_log_embed(
                guild,
                "Ticket Transcript",
                f"{channel.mention} was closed and its transcript was saved",
                discord.Color.dark_grey(),
            )
            embed.add_field(name="Ticket", value=self._ticket_label(ticket_id, channel.id), inline=True)
            embed.add_field(name="Channel", value=f"{channel.mention}\n`{channel.id}`", inline=True)
            if creator_id:
                embed.add_field(name="Created by", value=f"<@{creator_id}>\n`{creator_id}`", inline=True)
            if created_ts:
                embed.add_field(name="Opened", value=f"<t:{created_ts}:R>", inline=True)
            embed.add_field(name="Closed", value=f"<t:{int(time.time())}:R>", inline=True)
            sent = await log_channel.send(
                embed=embed,
                file=discord.File(transcript_path, filename=f"transcript-{ticket_id or channel.id}.txt"),
                allowed_mentions=no_mentions(),
            )
        except Exception as e:
            await _restore_open_status()
            await log_error(self.bot, f"Ticket close failed before deletion for channel_id={channel_id}: {repr(e)}")
            try:
                await channel.send("I couldn't save the transcript, so I did not delete this ticket.")
            except Exception:
                pass
            return False

        async def _cleanup_transcript_artifact() -> None:
            if ticket_id is not None:
                try:
                    await self.bot.db.execute(
                        "DELETE FROM ticket_transcripts WHERE guild_id=? AND ticket_id=? AND log_message_id=?",
                        (guild.id, ticket_id, sent.id),
                    )
                except Exception as cleanup_error:
                    await log_error(
                        self.bot,
                        f"Ticket transcript index cleanup failed ticket_id={ticket_id}: {repr(cleanup_error)}",
                    )
            try:
                await sent.delete()
            except discord.NotFound:
                pass
            except Exception as cleanup_error:
                await log_error(
                    self.bot,
                    f"Ticket transcript message cleanup failed message_id={sent.id}: {repr(cleanup_error)}",
                )

        if ticket_id is not None:
            try:
                await self.bot.db.execute(
                    "INSERT OR REPLACE INTO ticket_transcripts(guild_id, ticket_id, log_channel_id, log_message_id, created_ts) "
                    "VALUES(?,?,?,?,?)",
                    (guild.id, ticket_id, sent.channel.id, sent.id, int(time.time())),
                )
            except Exception as e:
                await _cleanup_transcript_artifact()
                await _restore_open_status()
                await log_error(self.bot, f"Ticket transcript index failed for ticket_id={ticket_id}: {repr(e)}")
                try:
                    await channel.send("I saved the transcript, but couldn't index it. I did not delete this ticket.")
                except Exception:
                    pass
                return False

        try:
            await channel.delete(reason="Ticket closed")
            self._active_ticket_channels.discard(channel_id)
            try:
                await self.bot.db.execute(
                    "UPDATE tickets SET status='closed', status_tag='resolved', closed_ts=?, "
                    "closing_prompt_message_id=NULL WHERE channel_id=?",
                    (int(time.time()), channel_id),
                )
            except Exception as e:
                await log_error(self.bot, f"Ticket status update failed after deletion for channel_id={channel_id}: {repr(e)}")
            try:
                await self._send_ticket_satisfaction_prompt(guild, creator_id, ticket_id)
            except Exception as e:
                await log_error(self.bot, f"Ticket satisfaction prompt failed for ticket_id={ticket_id}: {repr(e)}")
            return True
        except Exception as e:
            await _cleanup_transcript_artifact()
            await _restore_open_status()
            await log_error(self.bot, f"Ticket delete failed for channel_id={channel_id}: {repr(e)}")
            return False


def setup(bot: discord.Bot):
    bot.add_cog(HelpCog(bot))
