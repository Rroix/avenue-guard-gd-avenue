import asyncio
import calendar
import json
import re
import time as time_module
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import aiohttp
import discord
from discord.ext import commands

from utils.checks import basic_color, is_admin_or_owner, is_mod, member_has_any_role
from utils.errors import log_error
from utils.gd_validation import combine_level_validation, fetch_boomlings_level, fetch_gdbrowser_level, validation_notice
from utils.mentions import no_mentions, user_mentions
from utils.timeutils import TZ, now_madrid
from utils.views import LevelRequestButtonView, LevelRequestReviewView


STATE_OPEN = "open"
STATE_CLOSED = "closed"

OTHER_REASONS = {
    "level_doesnt_exist": "Level doesn't exist",
    "stolen_level": "Stolen level",
    "already_rated": "Already rated",
}

DEFAULT_REVIEWER_ROLE_IDS = [785212232786640966, 1430214323720163498]

REQUEST_TYPE_LABELS = {
    "": "Any",
    "needs_showcase": "Needs showcase video",
    "only_demons": "Only demons",
    "only_plats": "Only platformers",
    "only_classic": "Only classic",
    "only_classic_non_demons": "Only classic non-demons",
    "only_plats_non_demons": "Only platformer non-demons",
    "long_level": "Long or XL levels",
}

REQUEST_TYPE_ALIASES = {
    "": "",
    "any": "",
    "none": "",
    "no type": "",
    "needs showcase": "needs_showcase",
    "needs showcase video": "needs_showcase",
    "showcase": "needs_showcase",
    "only demons": "only_demons",
    "demons": "only_demons",
    "demon": "only_demons",
    "only plats": "only_plats",
    "only platformers": "only_plats",
    "platformers": "only_plats",
    "plats": "only_plats",
    "only classic": "only_classic",
    "classic": "only_classic",
    "only classic non demons": "only_classic_non_demons",
    "only classic non-demons": "only_classic_non_demons",
    "classic non demons": "only_classic_non_demons",
    "classic non-demons": "only_classic_non_demons",
    "only plats non demons": "only_plats_non_demons",
    "only plats non-demons": "only_plats_non_demons",
    "only platformers non demons": "only_plats_non_demons",
    "only platformers non-demons": "only_plats_non_demons",
    "plats non demons": "only_plats_non_demons",
    "plats non-demons": "only_plats_non_demons",
    "long level": "long_level",
    "long": "long_level",
    "long or xl": "long_level",
    "long xl": "long_level",
    "xl": "long_level",
}


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


class LevelRequestModal(discord.ui.Modal):
    def __init__(self, cog, user_id: int, edit: bool = False, initial: Optional[Dict[str, Any]] = None):
        super().__init__(title="Edit your request" if edit else "Request your level")
        self.cog = cog
        self.user_id = user_id
        self.edit = edit
        initial = initial or {}

        self.level_id = discord.ui.InputText(label="Level ID", required=True, max_length=100, value=str(initial.get("level_id") or "")[:100])
        self.level_name = discord.ui.InputText(label="Level name", required=True, max_length=150, value=str(initial.get("level_name") or "")[:150])
        self.creators = discord.ui.InputText(label="Creator(s)", required=True, max_length=200, value=str(initial.get("creators") or "")[:200])
        self.showcase = discord.ui.InputText(
            label="Level showcase",
            required=False,
            style=discord.InputTextStyle.long,
            max_length=1000,
            placeholder="Optional, but demons and platformers need a showcase.",
            value=str(initial.get("level_showcase") or "")[:1000],
        )
        self.notes = discord.ui.InputText(
            label="Notes",
            required=False,
            style=discord.InputTextStyle.long,
            max_length=1000,
            value=str(initial.get("notes") or "")[:1000],
        )

        self.add_item(self.level_id)
        self.add_item(self.level_name)
        self.add_item(self.creators)
        self.add_item(self.showcase)
        self.add_item(self.notes)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This form is not for you.", ephemeral=True)

        data = {
            "level_id": str(self.level_id.value or "").strip(),
            "level_name": str(self.level_name.value or "").strip(),
            "creators": str(self.creators.value or "").strip(),
            "level_showcase": str(self.showcase.value or "").strip(),
            "notes": str(self.notes.value or "").strip(),
        }
        if self.edit:
            await self.cog.handle_request_edit_form(interaction, data)
        else:
            await self.cog.handle_request_form(interaction, data)


class ReviewModal(discord.ui.Modal):
    def __init__(self, cog, message_id: int, result_key: str):
        title = "Send level" if result_key == "sent" else "Reject level"
        super().__init__(title=title)
        self.cog = cog
        self.message_id = message_id
        self.result_key = result_key
        self.review = discord.ui.InputText(
            label="Review",
            required=False,
            style=discord.InputTextStyle.long,
            max_length=1000,
        )
        self.add_item(self.review)

    async def callback(self, interaction: discord.Interaction):
        await self.cog.handle_review_submission(
            interaction,
            self.message_id,
            self.result_key,
            str(self.review.value or "").strip(),
        )


class FirstRequestChoiceView(discord.ui.View):
    def __init__(self, cog, user_id: int):
        super().__init__(timeout=180)
        self.cog = cog
        self.user_id = user_id

        will = discord.ui.Button(label="I will", style=discord.ButtonStyle.secondary)
        wont = discord.ui.Button(label="I won't", style=discord.ButtonStyle.secondary)
        will.callback = self._will
        wont.callback = self._wont
        self.add_item(will)
        self.add_item(wont)

    async def _will(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This prompt is not for you.", ephemeral=True)
        await self.cog.handle_first_choice(interaction, will_request_again=True)

    async def _wont(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This prompt is not for you.", ephemeral=True)
        await self.cog.handle_first_choice(interaction, will_request_again=False)


class OtherReasonView(discord.ui.View):
    def __init__(self, cog, message_id: int):
        super().__init__(timeout=180)
        self.cog = cog
        self.message_id = message_id

        for key, label in OTHER_REASONS.items():
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary)
            button.callback = self._make_callback(key)
            self.add_item(button)

    def _make_callback(self, reason_key: str):
        async def _callback(interaction: discord.Interaction):
            await self.cog.handle_other_reason(interaction, self.message_id, reason_key)
        return _callback


class ScheduledOpeningEditModal(discord.ui.Modal):
    def __init__(self, cog, user_id: int, opening_id: int, initial: Optional[Dict[str, Any]] = None):
        super().__init__(title=f"Edit opening #{opening_id}")
        self.cog = cog
        self.user_id = user_id
        self.opening_id = int(opening_id)
        initial = initial or {}

        self.when = discord.ui.InputText(
            label="Open time (HH:MM, Madrid)",
            required=True,
            max_length=5,
            value=str(initial.get("when") or "")[:5],
            placeholder="18:30",
        )
        self.day = discord.ui.InputText(
            label="Day of month",
            required=False,
            max_length=2,
            value=str(initial.get("day") or "")[:2],
            placeholder="Leave blank for next matching time",
        )
        self.number = discord.ui.InputText(
            label="Request limit",
            required=False,
            max_length=6,
            value=str(initial.get("number") or "")[:6],
            placeholder="Blank or 0 for no limit",
        )
        self.close_minutes = discord.ui.InputText(
            label="Close after minutes",
            required=False,
            max_length=6,
            value=str(initial.get("time") or "")[:6],
            placeholder="Blank or 0 for no timer",
        )
        self.request_type = discord.ui.InputText(
            label="Request type",
            required=False,
            max_length=40,
            value=str(initial.get("request_type") or "")[:40],
            placeholder="Example: only demons, long level, needs showcase",
        )
        self.add_item(self.when)
        self.add_item(self.day)
        self.add_item(self.number)
        self.add_item(self.close_minutes)
        self.add_item(self.request_type)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This editor is not for you.", ephemeral=True)
        await self.cog.handle_scheduled_opening_edit_modal(
            interaction,
            self.opening_id,
            str(self.when.value or "").strip(),
            str(self.day.value or "").strip(),
            str(self.number.value or "").strip(),
            str(self.close_minutes.value or "").strip(),
            str(self.request_type.value or "").strip(),
        )


class ScheduledOpeningsView(discord.ui.View):
    def __init__(self, cog, user_id: int, rows):
        super().__init__(timeout=300)
        self.cog = cog
        self.user_id = int(user_id)
        self.rows = list(rows or [])
        self.selected_id = int(self.rows[0]["id"]) if self.rows else 0

        if self.rows:
            options = []
            for row in self.rows[:25]:
                limit = int(row["request_limit"]) if row["request_limit"] is not None else "none"
                close = f"{int(row['close_minutes'])}m" if row["close_minutes"] is not None else "none"
                request_type = self.cog._request_type_label(row["request_type"] if "request_type" in row.keys() else "")
                options.append(
                    discord.SelectOption(
                        label=f"#{int(row['id'])} - {datetime.fromtimestamp(int(row['open_ts']), TZ).strftime('%b %d %H:%M')}",
                        description=f"Limit {limit} | Close {close} | {request_type}",
                        value=str(int(row["id"])),
                    )
                )
            select = discord.ui.Select(placeholder="Choose a scheduled opening", min_values=1, max_values=1, options=options)
            select.callback = self._select
            self.add_item(select)

        for label, style, callback in (
            ("Refresh", discord.ButtonStyle.secondary, self._refresh),
            ("Edit", discord.ButtonStyle.primary, self._edit),
            ("Delete", discord.ButtonStyle.danger, self._delete),
            ("Open now", discord.ButtonStyle.success, self._open_now),
        ):
            button = discord.ui.Button(label=label, style=style, disabled=not self.rows and label != "Refresh")
            button.callback = callback
            self.add_item(button)

    async def _allowed(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This panel belongs to another admin.", ephemeral=True)
            return False
        if interaction.guild is None:
            await interaction.response.send_message("Wrong server.", ephemeral=True)
            return False
        member = await self.cog._resolve_member(interaction.guild, interaction.user)
        if member is None or not self.cog._is_admin(member):
            await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)
            return False
        return True

    async def _select(self, interaction: discord.Interaction):
        if not await self._allowed(interaction):
            return
        try:
            self.selected_id = int(interaction.data.get("values", [self.selected_id])[0])
        except Exception:
            pass
        await interaction.response.defer()

    async def _refresh(self, interaction: discord.Interaction):
        if not await self._allowed(interaction):
            return
        await self.cog.refresh_pending_openings_panel(interaction, self.user_id)

    async def _edit(self, interaction: discord.Interaction):
        if not await self._allowed(interaction):
            return
        row = await self.cog.get_scheduled_opening(interaction.guild.id, self.selected_id)
        if not row:
            return await interaction.response.send_message("That opening is no longer pending.", ephemeral=True)
        dt = datetime.fromtimestamp(int(row["open_ts"]), TZ)
        initial = {
            "when": dt.strftime("%H:%M"),
            "day": str(dt.day),
            "number": "" if row["request_limit"] is None else str(int(row["request_limit"])),
            "time": "" if row["close_minutes"] is None else str(int(row["close_minutes"])),
            "request_type": self.cog._request_type_label(row["request_type"] if "request_type" in row.keys() else ""),
        }
        await interaction.response.send_modal(ScheduledOpeningEditModal(self.cog, self.user_id, self.selected_id, initial))

    async def _delete(self, interaction: discord.Interaction):
        if not await self._allowed(interaction):
            return
        await self.cog.delete_scheduled_opening(interaction, self.selected_id)

    async def _open_now(self, interaction: discord.Interaction):
        if not await self._allowed(interaction):
            return
        await self.cog.open_scheduled_opening_now(interaction, self.selected_id)


class ScheduledOpenNowConfirmView(discord.ui.View):
    def __init__(self, cog, user_id: int, opening_id: int):
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = int(user_id)
        self.opening_id = int(opening_id)

    async def _allowed(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This confirmation is not for you.", ephemeral=True)
            return False
        if interaction.guild is None:
            await interaction.response.send_message("Wrong server.", ephemeral=True)
            return False
        member = await self.cog._resolve_member(interaction.guild, interaction.user)
        if member is None or not self.cog._is_admin(member):
            await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm open now", style=discord.ButtonStyle.danger)
    async def confirm(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not await self._allowed(interaction):
            return
        await self.cog.open_scheduled_opening_now(interaction, self.opening_id, force=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not await self._allowed(interaction):
            return
        await interaction.response.edit_message(content="Cancelled. The scheduled opening was not opened.", embed=None, view=None)


class RequestLevelsCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.allowed_guild_id = bot.config.get_int("guild", "allowed_guild_id") or 0
        self._started = False
        self._close_task: Optional[asyncio.Task] = None
        self._scheduled_open_task: Optional[asyncio.Task] = None
        self._submit_lock = asyncio.Lock()
        self._review_lock = asyncio.Lock()
        self._validation_session: Optional[aiohttp.ClientSession] = None
        self._validation_session_timeout: int = 0
        self._validation_attempts: dict[tuple[int, int], list[int]] = {}
        self._validation_provider_failures: dict[str, list[int]] = {}

        guild_ids = [self.allowed_guild_id] if self.allowed_guild_id else None

        @bot.slash_command(name="refresh-request-button", description="Refresh or recreate the request button embed", guild_ids=guild_ids)
        async def refresh_request_button(ctx: discord.ApplicationContext):
            await self.refresh_request_button(ctx)

        @bot.slash_command(name="open-requests", description="Open level requests now or schedule them", guild_ids=guild_ids)
        async def open_requests(
            ctx: discord.ApplicationContext,
            number: discord.Option(int, "Maximum successful requests to accept; leave 0 for no limit", required=False, default=0),
            time: discord.Option(int, "Minutes requests stay open after opening; leave 0 for no timer", required=False, default=0),
            when: discord.Option(str, "Optional scheduled opening time in Madrid time, like 18:30", required=False, default=""),
            day: discord.Option(int, "Optional day of the month for the scheduled opening; leave 0 for next matching time", required=False, default=0),
            type: discord.Option(str, "Optional wave type: needs showcase, only demons, only plats, only classic, classic non-demons, plats non-demons, or long level", required=False, default=""),
        ):
            await self.open_requests(ctx, number, time, when, day, type)

        @bot.slash_command(name="close-requests", description="Close level requests", guild_ids=guild_ids)
        async def close_requests(ctx: discord.ApplicationContext):
            await self.close_requests(ctx)

        @bot.slash_command(name="requests-are", description="Check whether level requests are open", guild_ids=guild_ids)
        async def requests_are(ctx: discord.ApplicationContext):
            await self.requests_are(ctx)

        @bot.slash_command(name="edit-request", description="Edit your current pending level request", guild_ids=guild_ids)
        async def edit_request(ctx: discord.ApplicationContext):
            await self.edit_request(ctx)

        @bot.slash_command(name="pending-openings", description="List, edit, or delete scheduled request openings", guild_ids=guild_ids)
        async def pending_openings(
            ctx: discord.ApplicationContext,
            action: discord.Option(str, "Action to run: list shows the interactive panel", required=False, default="list"),
            opening_id: discord.Option(int, "Scheduled opening ID to edit or delete", required=False, default=0),
            number: discord.Option(int, "New request limit; use 0 for no limit and -1 to keep current value", required=False, default=-1),
            time: discord.Option(int, "New close timer in minutes; use 0 for no timer and -1 to keep current value", required=False, default=-1),
            when: discord.Option(str, "New opening time in Madrid time, like 18:30", required=False, default=""),
            day: discord.Option(int, "Optional day of the month for the new opening time", required=False, default=0),
            type: discord.Option(str, "New request type; leave blank to keep current value, use any to clear", required=False, default=""),
        ):
            await self.pending_openings(ctx, action, opening_id, number, time, when, day, type)

    def cog_unload(self) -> None:
        if self._close_task:
            self._close_task.cancel()
        if self._scheduled_open_task:
            self._scheduled_open_task.cancel()
        session = self._validation_session
        self._validation_session = None
        if session and not session.closed:
            try:
                asyncio.create_task(session.close())
            except Exception:
                pass

    async def start_background(self):
        if self._started:
            return
        self._started = True
        await self.bot.db.connect()
        self._close_task = asyncio.create_task(self._auto_close_loop())
        self._scheduled_open_task = asyncio.create_task(self._scheduled_open_loop())

    def on_config_reload(self) -> None:
        pass

    def _cfg(self, *path: str, default: Any = None) -> Any:
        return self.bot.config.get("level_requests", *path, default=default)

    def _cfg_int(self, *path: str, default: int = 0) -> int:
        return self.bot.config.get_int("level_requests", *path, default=default)

    def _cfg_int_list(self, *path: str) -> list[int]:
        return self.bot.config.get_int_list("level_requests", *path)

    def _reviewer_role_ids(self) -> list[int]:
        configured = self._cfg_int_list("reviewer_role_ids")
        return configured or list(DEFAULT_REVIEWER_ROLE_IDS)

    def _post_close_edit_seconds(self) -> int:
        minutes = self._cfg_int("request_post_close_edit_minutes", default=5)
        return max(0, int(minutes) * 60)

    def _edit_deadline_ts_for_state(self, state_row) -> Any:
        if not state_row:
            return ""
        grace = self._post_close_edit_seconds()
        if str(state_row["state"]) == STATE_OPEN:
            close_ts = self._row_value(state_row, "close_ts", None)
            if close_ts is not None:
                try:
                    return int(close_ts) + grace
                except Exception:
                    return ""
            return ""
        closed_ts = self._row_value(state_row, "closed_ts", None)
        if closed_ts is None:
            return ""
        try:
            return int(closed_ts) + grace
        except Exception:
            return ""

    def _edit_window_text(self, state_row) -> str:
        grace_minutes = max(0, self._post_close_edit_seconds() // 60)
        deadline_ts = self._edit_deadline_ts_for_state(state_row)
        if deadline_ts:
            return f"until <t:{int(deadline_ts)}:R>"
        return f"until requests close, plus {grace_minutes} minutes afterward"

    def _can_edit_submission(self, state_row, submission_row) -> bool:
        if not state_row or not submission_row:
            return False
        if str(submission_row["status"]) != "pending":
            return False
        try:
            if int(submission_row["wave_id"]) != int(state_row["wave_id"]):
                return False
        except Exception:
            return False
        if str(state_row["state"]) == STATE_OPEN:
            return True
        deadline_ts = self._edit_deadline_ts_for_state(state_row)
        if not deadline_ts:
            return False
        try:
            return int(time_module.time()) <= int(deadline_ts)
        except Exception:
            return False

    async def _current_user_submission(self, guild_id: int, wave_id: int, user_id: int):
        return await self.bot.db.fetchone(
            "SELECT * FROM level_request_submissions WHERE guild_id=? AND wave_id=? AND user_id=?",
            (guild_id, wave_id, user_id),
        )

    async def _state_after_timed_close_check(self, guild: discord.Guild, state_row):
        if not state_row:
            return state_row
        if str(state_row["state"]) != STATE_OPEN or state_row["close_ts"] is None:
            return state_row
        try:
            if int(state_row["close_ts"]) > int(time_module.time()):
                return state_row
        except Exception:
            return state_row
        await self._set_state_closed(guild, reason="time limit")
        return await self._get_state(guild.id)

    def _request_initial_values(self, submission_row) -> Dict[str, Any]:
        try:
            return json.loads(submission_row["data_json"] or "{}")
        except Exception:
            return {}

    def _message(self, key: str, default: str) -> str:
        return str(self._cfg("messages", key, default=default) or default)

    def _message_formatted(self, key: str, default: str, variables: Dict[str, Any]) -> str:
        return self._format(self._message(key, default), variables)

    def _request_button_label(self) -> str:
        return str(self._cfg("request_button_label", default="Request your level!") or "Request your level!")

    def _request_type_normalize_text(self, value: Any) -> str:
        text = str(value or "").strip().casefold()
        text = re.sub(r"[_/\\-]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _normalize_request_type(self, value: Any) -> Optional[str]:
        text = self._request_type_normalize_text(value)
        if text in REQUEST_TYPE_LABELS:
            return text
        return REQUEST_TYPE_ALIASES.get(text)

    def _request_type_label(self, value: Any) -> str:
        key = self._normalize_request_type(value)
        if key is None:
            key = ""
        return REQUEST_TYPE_LABELS.get(key, REQUEST_TYPE_LABELS[""])

    def _request_type_help(self) -> str:
        return ", ".join(label for key, label in REQUEST_TYPE_LABELS.items() if key)

    def _request_type_from_row(self, row) -> str:
        if not row:
            return ""
        value = self._row_value(row, "request_type", "")
        normalized = self._normalize_request_type(value)
        return normalized or ""

    def _color_name(self, key: str, default: str = "blurple") -> str:
        return str(self._cfg("colors", key, default=default) or default)

    def _format(self, text: Any, variables: Dict[str, Any]) -> str:
        try:
            return str(text or "").format_map(_SafeDict({k: str(v) for k, v in variables.items()}))
        except Exception:
            return str(text or "")

    def _submitted_ago(self, created_ts: Any) -> str:
        try:
            ts = int(created_ts)
        except Exception:
            return "Unknown"
        return f"<t:{ts}:R>"

    def _clean_level_id(self, value: Any) -> str:
        return str(value or "").strip()

    def _normalize_level_id(self, value: Any) -> str:
        return self._clean_level_id(value).casefold()

    def _valid_url(self, value: str) -> bool:
        try:
            parsed = urlparse(str(value).strip())
            return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
        except Exception:
            return False

    def _validate_request_data(self, data: Dict[str, str]) -> list[str]:
        errors = []
        level_id = self._clean_level_id(data.get("level_id"))
        if not re.fullmatch(r"\d{7,9}", level_id):
            errors.append("Level ID must be 7 to 9 numbers, like `111111111`.")

        showcase = str(data.get("level_showcase") or "").strip()
        if showcase and not self._valid_url(showcase):
            errors.append("Level showcase must be a valid URL, usually YouTube or Streamable.")
        return errors

    def _level_validation_cfg(self) -> Dict[str, Any]:
        cfg = self._cfg("level_validation", default={}) or {}
        return cfg if isinstance(cfg, dict) else {}

    def _level_validation_enabled(self) -> bool:
        cfg = self._level_validation_cfg()
        return bool(cfg.get("enabled", True))

    def _level_validation_cache_seconds(self) -> int:
        try:
            return max(60, int(self._level_validation_cfg().get("cache_seconds", 1800)))
        except Exception:
            return 1800

    def _level_validation_timeout_seconds(self) -> int:
        try:
            return max(2, min(20, int(self._level_validation_cfg().get("request_timeout_seconds", 8))))
        except Exception:
            return 8

    def _level_validation_message(self, key: str, default: str) -> str:
        cfg = self._level_validation_cfg().get("messages", {})
        if not isinstance(cfg, dict):
            return default
        return str(cfg.get(key) or default)

    def _level_validation_providers(self) -> dict[str, bool]:
        providers = self._level_validation_cfg().get("providers", {})
        if not isinstance(providers, dict):
            providers = {}
        return {
            "gdbrowser": bool(providers.get("gdbrowser", True)),
            "boomlings": bool(providers.get("boomlings", True)),
        }

    def _level_validation_rate_limit_message(self, guild_id: int, user_id: int) -> str:
        cfg = self._level_validation_cfg()
        try:
            window = max(10, int(cfg.get("per_user_window_seconds", 60)))
        except Exception:
            window = 60
        try:
            max_checks = max(1, int(cfg.get("per_user_max_checks", 6)))
        except Exception:
            max_checks = 6
        try:
            cooldown = max(0, int(cfg.get("per_user_cooldown_seconds", 20)))
        except Exception:
            cooldown = 20

        now_ts = int(time_module.time())
        key = (int(guild_id or 0), int(user_id or 0))
        if len(self._validation_attempts) > 5000:
            stale_keys = [
                attempt_key
                for attempt_key, timestamps in self._validation_attempts.items()
                if not timestamps or now_ts - int(timestamps[-1]) >= window
            ]
            for attempt_key in stale_keys[:1000]:
                self._validation_attempts.pop(attempt_key, None)
        attempts = [ts for ts in self._validation_attempts.get(key, []) if now_ts - int(ts) < window]
        if cooldown and attempts and now_ts - int(attempts[-1]) < cooldown:
            wait = cooldown - (now_ts - int(attempts[-1]))
            self._validation_attempts[key] = attempts
            return f"Please wait {wait}s before validating another level ID."
        if len(attempts) >= max_checks:
            self._validation_attempts[key] = attempts
            return "You are trying too many level IDs too quickly. Please wait a bit and try again."
        attempts.append(now_ts)
        self._validation_attempts[key] = attempts
        return ""

    def _provider_failure_cfg(self) -> tuple[int, int]:
        cfg = self._level_validation_cfg()
        try:
            threshold = max(1, int(cfg.get("provider_failure_threshold", 5)))
        except Exception:
            threshold = 5
        try:
            seconds = max(30, int(cfg.get("provider_circuit_breaker_seconds", 300)))
        except Exception:
            seconds = 300
        return threshold, seconds

    def _provider_circuit_open(self, provider: str) -> bool:
        threshold, seconds = self._provider_failure_cfg()
        now_ts = int(time_module.time())
        failures = [ts for ts in self._validation_provider_failures.get(provider, []) if now_ts - int(ts) < seconds]
        self._validation_provider_failures[provider] = failures
        return len(failures) >= threshold

    def _record_provider_validation_result(self, provider: str, result: Dict[str, Any]) -> None:
        now_ts = int(time_module.time())
        if result.get("ok"):
            self._validation_provider_failures[provider] = []
            return
        failures = self._validation_provider_failures.get(provider, [])
        failures.append(now_ts)
        _, seconds = self._provider_failure_cfg()
        self._validation_provider_failures[provider] = [ts for ts in failures if now_ts - int(ts) < seconds]

    async def _get_level_validation_session(self) -> aiohttp.ClientSession:
        timeout_seconds = self._level_validation_timeout_seconds()
        if (
            self._validation_session is None
            or self._validation_session.closed
            or self._validation_session_timeout != timeout_seconds
        ):
            old_session = self._validation_session
            if old_session and not old_session.closed:
                try:
                    await old_session.close()
                except Exception:
                    pass
            timeout = aiohttp.ClientTimeout(total=timeout_seconds)
            self._validation_session = aiohttp.ClientSession(timeout=timeout)
            self._validation_session_timeout = timeout_seconds
        return self._validation_session

    def _safe_json_loads(self, raw: Any, default: Any = None) -> Any:
        try:
            return json.loads(raw or "{}")
        except Exception:
            return {} if default is None else default

    async def _lookup_level_validation(self, level_id: str, force: bool = False) -> Dict[str, Any]:
        level_id = self._clean_level_id(level_id)
        if not self._level_validation_enabled() or not level_id:
            return {}

        now_ts = int(time_module.time())
        if not force:
            row = await self.bot.db.fetchone(
                "SELECT data_json, expires_ts FROM gd_level_validation_cache WHERE level_id=?",
                (level_id,),
            )
            if row:
                try:
                    if int(row["expires_ts"]) > now_ts:
                        cached = self._safe_json_loads(row["data_json"], {})
                        if isinstance(cached, dict):
                            cached["cache_hit"] = True
                            return cached
                except Exception:
                    pass

        providers = self._level_validation_providers()
        results: dict[str, dict[str, Any]] = {}
        session = await self._get_level_validation_session()
        tasks = []
        if providers.get("gdbrowser"):
            if self._provider_circuit_open("gdbrowser"):
                results["gdbrowser"] = {"provider": "gdbrowser", "ok": False, "exists": None, "error": "Circuit breaker open"}
            else:
                tasks.append(("gdbrowser", fetch_gdbrowser_level(session, level_id)))
        if providers.get("boomlings"):
            if self._provider_circuit_open("boomlings"):
                results["boomlings"] = {"provider": "boomlings", "ok": False, "exists": None, "error": "Circuit breaker open"}
            else:
                tasks.append(("boomlings", fetch_boomlings_level(session, level_id)))

        if not tasks and not results:
            return {}

        fetched = await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
        for (provider, _), result in zip(tasks, fetched):
            if isinstance(result, Exception):
                results[provider] = {"provider": provider, "ok": False, "exists": None, "error": type(result).__name__}
            elif isinstance(result, dict):
                results[provider] = result
            else:
                results[provider] = {"provider": provider, "ok": False, "exists": None, "error": "Unexpected result"}
            self._record_provider_validation_result(provider, results[provider])

        expires_ts = now_ts + self._level_validation_cache_seconds()
        combined = combine_level_validation(level_id, results, checked_ts=now_ts, expires_ts=expires_ts)
        try:
            await self.bot.db.execute(
                "INSERT INTO gd_level_validation_cache(level_id,checked_ts,expires_ts,data_json) VALUES(?,?,?,?) "
                "ON CONFLICT(level_id) DO UPDATE SET checked_ts=excluded.checked_ts, expires_ts=excluded.expires_ts, data_json=excluded.data_json",
                (level_id, now_ts, expires_ts, json.dumps(combined, separators=(",", ":"))),
            )
        except Exception as e:
            await log_error(self.bot, f"Could not cache GD level validation for {level_id}: {repr(e)}")
        return combined

    def _apply_level_validation_vars(self, data: Dict[str, Any], validation: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(data)
        if not validation:
            data.setdefault("level_validation_warning", "")
            data.setdefault("level_validation_json", "{}")
            data.setdefault("level_validation_sources", "")
            data.setdefault("level_validation_checked", "")
            data.setdefault("level_validation_refresh", "")
            data.setdefault("level_exists", "unknown")
            data.setdefault("level_rated", "unknown")
            data.setdefault("level_requires_showcase", "unknown")
            data.setdefault("gd_level_name", "Unknown")
            data.setdefault("gd_creator", "Unknown")
            data.setdefault("gd_difficulty", "Unknown")
            data.setdefault("gd_length", "Unknown")
            data.setdefault("gd_stars", "Unknown")
            data.setdefault("gd_rated", "Unknown")
            data.setdefault("gd_demon", "Unknown")
            data.setdefault("gd_platformer", "Unknown")
            data.setdefault("gd_featured", "Unknown")
            data.setdefault("gd_epic", "Unknown")
            data.setdefault("gd_flags", "Unknown")
            data.setdefault("gd_info", "GD info is not available yet.")
            return data

        checked_ts = validation.get("checked_ts") or ""
        expires_ts = validation.get("expires_ts") or ""
        exists = validation.get("exists")
        rated = bool(validation.get("rated"))
        demon = bool(validation.get("demon"))
        platformer = bool(validation.get("platformer"))
        featured = bool(validation.get("featured"))
        epic = bool(validation.get("epic"))
        stars_raw = validation.get("stars")
        try:
            stars_text = f"{int(stars_raw)} stars" if stars_raw is not None and rated else "Unrated"
        except Exception:
            stars_text = "Unrated" if not rated else str(stars_raw or "Unknown")
        gd_flags = ", ".join(
            flag
            for flag, active in (
                ("Demon", demon),
                ("Platformer", platformer),
                ("Featured", featured),
                ("Epic", epic),
            )
            if active
        ) or "None detected"

        data["level_validation_json"] = json.dumps(validation, separators=(",", ":"))
        data["level_validation_warning"] = validation_notice(validation)
        data["level_validation_sources"] = str(validation.get("source_summary") or "")
        data["level_validation_checked_ts"] = checked_ts
        data["level_validation_refresh_ts"] = expires_ts
        data["level_validation_checked"] = f"<t:{int(checked_ts)}:R>" if checked_ts else ""
        data["level_validation_refresh"] = f"<t:{int(expires_ts)}:R>" if expires_ts else ""
        data["level_exists"] = "yes" if exists is True else "no" if exists is False else "unknown"
        data["level_rated"] = "yes" if rated else "no"
        data["level_requires_showcase"] = "yes" if validation.get("requires_showcase") else "no"
        data["gd_level_name"] = str(validation.get("level_name") or "Unknown")
        data["gd_creator"] = str(validation.get("creator") or "Unknown")
        data["gd_difficulty"] = str(validation.get("difficulty") or "Unknown")
        data["gd_length"] = str(validation.get("length") or "Unknown")
        data["gd_stars"] = stars_text
        data["gd_rated"] = "Rated" if rated else "Unrated"
        data["gd_demon"] = "Yes" if demon else "No"
        data["gd_platformer"] = "Yes" if platformer else "No"
        data["gd_featured"] = "Yes" if featured else "No"
        data["gd_epic"] = "Yes" if epic else "No"
        data["gd_flags"] = gd_flags
        if exists is True:
            data["gd_info"] = (
                f"Difficulty: **{data['gd_difficulty']}** | Length: **{data['gd_length']}**\n"
                f"Stars: **{data['gd_stars']}** | Status: **{data['gd_rated']}**\n"
                f"Flags: **{gd_flags}**"
            )
        elif exists is False:
            data["gd_info"] = "This level was not found by the enabled validation sources."
        else:
            data["gd_info"] = "GD info could not be confirmed right now."
        return data

    async def _validate_level_external(self, data: Dict[str, str], guild_id: int = 0, user_id: int = 0) -> tuple[list[str], Dict[str, Any]]:
        if not self._level_validation_enabled():
            return [], {}
        level_id = self._clean_level_id(data.get("level_id"))
        if not re.fullmatch(r"\d{7,9}", level_id):
            return [], {}
        if guild_id and user_id:
            rate_limited = self._level_validation_rate_limit_message(guild_id, user_id)
            if rate_limited:
                return [rate_limited], {}

        validation = await self._lookup_level_validation(level_id)
        errors: list[str] = []
        auto_reject = bool(self._level_validation_cfg().get("auto_reject_missing", True))
        if validation.get("missing_confident") and auto_reject:
            errors.append(self._level_validation_message("missing", "That level ID does not seem to exist. Please check the ID and try again."))

        showcase = str(data.get("level_showcase") or "").strip()
        if validation.get("requires_showcase") and not self._valid_url(showcase):
            errors.append(
                self._level_validation_message(
                    "showcase_required",
                    "This level appears to be a demon or platformer, so a showcase URL is required.",
                )
            )

        return errors, validation

    def _request_type_validation_error(self, request_type: str, data: Dict[str, str], validation: Dict[str, Any]) -> str:
        request_type = self._normalize_request_type(request_type) or ""
        if not request_type:
            return ""

        label = self._request_type_label(request_type)
        showcase = str(data.get("level_showcase") or "").strip()
        if request_type == "needs_showcase":
            if not self._valid_url(showcase):
                return "This wave needs a showcase video, so the showcase field must be a valid URL."
            return ""

        if not validation or validation.get("exists") is not True:
            return f"I couldn't confirm that this level matches the current request type: **{label}**."

        demon = bool(validation.get("demon"))
        platformer = bool(validation.get("platformer"))
        length = str(validation.get("length") or "").strip().casefold()

        if request_type == "only_demons" and not demon:
            return "This wave only accepts demons."
        if request_type == "only_plats" and not platformer:
            return "This wave only accepts platformer levels."
        if request_type == "only_classic" and platformer:
            return "This wave only accepts classic levels."
        if request_type == "only_classic_non_demons" and (platformer or demon):
            return "This wave only accepts classic non-demon levels."
        if request_type == "only_plats_non_demons" and (not platformer or demon):
            return "This wave only accepts platformer non-demon levels."
        if request_type == "long_level" and length not in {"long", "xl"}:
            return "This wave only accepts Long or XL levels."
        return ""

    def _has_reviewer_role(self, member: discord.Member) -> bool:
        role_ids = self._reviewer_role_ids()
        return member_has_any_role(member, role_ids) or self._is_admin(member)

    async def _is_reviewer_interaction(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            return False
        member = await self._resolve_member(interaction.guild, interaction.user)
        return member is not None and self._has_reviewer_role(member)

    def _embed_from_template(self, template: Dict[str, Any], variables: Dict[str, Any], default_color: str = "blurple") -> discord.Embed:
        if not isinstance(template, dict):
            template = {}

        color_text = self._format(template.get("color", default_color), variables) or default_color
        embed = discord.Embed(
            title=self._format(template.get("title", ""), variables) or None,
            description=self._format(template.get("description", ""), variables) or None,
            color=basic_color(color_text),
        )

        for field in template.get("fields", []) or []:
            if not isinstance(field, dict):
                continue
            name = self._format(field.get("name", ""), variables)
            value = self._format(field.get("value", ""), variables)
            if not name or not value:
                continue
            embed.add_field(name=name[:256], value=value[:1024], inline=bool(field.get("inline", False)))

        footer = self._format(template.get("footer", ""), variables)
        if footer:
            embed.set_footer(text=footer[:2048])
        thumbnail_url = self._format(template.get("thumbnail_url", ""), variables)
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        image_url = self._format(template.get("image_url", ""), variables)
        if image_url:
            embed.set_image(url=image_url)
        author_name = self._format(template.get("author_name", ""), variables)
        if author_name:
            author_icon = self._format(template.get("author_icon_url", ""), variables)
            embed.set_author(name=author_name[:256], icon_url=author_icon or None)
        return embed

    async def _reply_ephemeral(self, interaction: discord.Interaction, message: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    async def _log_request_admin_action(self, guild: discord.Guild, user_id: int, action: str, detail: str = "") -> None:
        channel_id = self.bot.config.get_int("channels", "general_logging_channel_id", default=0)
        channel = guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            return
        embed = discord.Embed(
            title="Request Admin Action",
            description=str(action).replace("_", " ").title(),
            color=discord.Color.blurple(),
            timestamp=now_madrid(),
        )
        if int(user_id or 0):
            admin_value = f"<@{int(user_id)}>\n`{int(user_id)}`"
        else:
            admin_value = "System"
        embed.add_field(name="Admin", value=admin_value, inline=True)
        embed.add_field(name="Action", value=f"`{str(action)[:120]}`", inline=True)
        if detail:
            embed.add_field(name="Details", value=str(detail)[:1024], inline=False)
        try:
            await channel.send(embed=embed, allowed_mentions=no_mentions())
        except Exception as e:
            await log_error(self.bot, f"Could not log request admin action {action}: {repr(e)}")

    def _state_label(self, state: str) -> str:
        return "Opened" if state == STATE_OPEN else "Closed"

    def _request_button_embed(self, row) -> discord.Embed:
        cfg = self._cfg("request_button_embed", default={}) or {}
        state = str(row["state"]) if row else STATE_CLOSED
        desc_key = "description_open" if state == STATE_OPEN else "description_closed"
        color_key = "opened" if state == STATE_OPEN else "closed"
        variables = self._base_state_vars(row)
        embed = discord.Embed(
            title=self._format(cfg.get("title", "Level Requests"), variables) or "Level Requests",
            description=self._format(cfg.get(desc_key, ""), variables),
            color=basic_color(self._color_name(color_key, "green" if state == STATE_OPEN else "red")),
        )
        footer = self._format(cfg.get("footer", ""), variables)
        if footer:
            embed.set_footer(text=footer)
        return embed

    def _pct(self, amount: int, total: int) -> str:
        if total <= 0:
            return "0%"
        return f"{(amount / total) * 100:.1f}%"

    async def _wave_summary_vars(self, guild_id: int, wave_id: int) -> Dict[str, Any]:
        rows = await self.bot.db.fetchall(
            "SELECT status, result, reviewed_by, data_json FROM level_request_submissions WHERE guild_id=? AND wave_id=?",
            (guild_id, wave_id),
        )
        total = len(rows)
        reviewed = sum(1 for row in rows if str(row["status"]) == "reviewed")
        sent = sum(1 for row in rows if str(row["status"]) == "reviewed" and str(row["result"]) == "sent")
        rejected = sum(1 for row in rows if str(row["status"]) == "reviewed" and str(row["result"]) == "rejected")
        level_doesnt_exist = sum(1 for row in rows if str(row["result"]) == "level_doesnt_exist")
        stolen_level = sum(1 for row in rows if str(row["result"]) == "stolen_level")
        already_rated = sum(1 for row in rows if str(row["result"]) == "already_rated")
        other = level_doesnt_exist + stolen_level + already_rated
        not_sent = rejected + other
        pending = max(total - reviewed, 0)
        reviewer_stats = await self._reviewer_stats_lines(rows)
        request_type = ""
        for row in rows:
            data = self._safe_json_loads(row["data_json"], {})
            if isinstance(data, dict):
                request_type = str(data.get("request_type") or "")
                if request_type:
                    break
        return {
            "wave_id": wave_id,
            "request_type": request_type,
            "request_type_label": self._request_type_label(request_type),
            "total_requests": total,
            "reviewed_count": reviewed,
            "sent_count": sent,
            "not_sent_count": not_sent,
            "rejected_count": rejected,
            "other_count": other,
            "level_doesnt_exist_count": level_doesnt_exist,
            "stolen_level_count": stolen_level,
            "already_rated_count": already_rated,
            "pending_count": pending,
            "left_to_review": pending,
            "reviewed_percent": self._pct(reviewed, total),
            "pending_percent": self._pct(pending, total),
            "sent_percent": self._pct(sent, total),
            "not_sent_percent": self._pct(not_sent, total),
            "sent_percent_reviewed": self._pct(sent, reviewed),
            "not_sent_percent_reviewed": self._pct(not_sent, reviewed),
            "reviewer_stats": reviewer_stats,
            "summary_color": self._color_name("sent" if pending == 0 else "pending", "blurple"),
        }

    async def _reviewer_stats_lines(self, rows) -> str:
        stats: dict[int, dict[str, int]] = {}
        for row in rows:
            if str(row["status"]) != "reviewed" or row["reviewed_by"] is None:
                continue
            reviewer_id = int(row["reviewed_by"])
            bucket = stats.setdefault(reviewer_id, {"total": 0, "sent": 0, "not_sent": 0})
            bucket["total"] += 1
            if str(row["result"]) == "sent":
                bucket["sent"] += 1
            else:
                bucket["not_sent"] += 1
        if not stats:
            return "No reviews yet."
        lines = []
        for reviewer_id, bucket in sorted(stats.items(), key=lambda item: item[1]["total"], reverse=True)[:8]:
            lines.append(
                f"<@{reviewer_id}> - **{bucket['total']}** reviewed "
                f"({bucket['sent']} sent / {bucket['not_sent']} not sent)"
            )
        return "\n".join(lines)

    def _wave_summary_embed(self, variables: Dict[str, Any]) -> discord.Embed:
        template = self._cfg("wave_summary_embed", default={}) or {}
        if isinstance(template, dict) and template:
            return self._embed_from_template(template, variables, default_color=str(variables.get("summary_color") or "blurple"))

        embed = discord.Embed(
            title=f"Wave {variables['wave_id']} Summary",
            description="Live review progress for this request wave.",
            color=basic_color(str(variables.get("summary_color") or "blurple")),
        )
        embed.add_field(name="Requested", value=str(variables["total_requests"]), inline=True)
        embed.add_field(
            name="Reviewed",
            value=f"{variables['reviewed_count']} / {variables['total_requests']} ({variables['reviewed_percent']})",
            inline=True,
        )
        embed.add_field(name="Left to review", value=f"{variables['pending_count']} ({variables['pending_percent']})", inline=True)
        embed.add_field(name="Sent", value=f"{variables['sent_count']} ({variables['sent_percent_reviewed']} of reviewed)", inline=True)
        embed.add_field(name="Not sent", value=f"{variables['not_sent_count']} ({variables['not_sent_percent_reviewed']} of reviewed)", inline=True)
        embed.add_field(name="Reviewer stats", value=str(variables.get("reviewer_stats") or "No reviews yet.")[:1024], inline=False)
        embed.add_field(
            name="Not sent breakdown",
            value=(
                f"Rejected: **{variables['rejected_count']}**\n"
                f"Level doesn't exist: **{variables['level_doesnt_exist_count']}**\n"
                f"Stolen level: **{variables['stolen_level_count']}**\n"
                f"Already rated: **{variables['already_rated_count']}**"
            ),
            inline=False,
        )
        embed.set_footer(text="This updates whenever a request in the wave is reviewed.")
        return embed

    async def update_wave_summary(self, guild: discord.Guild, wave_id: int, create_if_missing: bool = True) -> Optional[discord.Message]:
        channel = await self._configured_channel(guild, "level_requested")
        if channel is None:
            return None

        variables = await self._wave_summary_vars(guild.id, wave_id)
        embed = self._wave_summary_embed(variables)
        now_ts = int(time_module.time())

        row = await self.bot.db.fetchone(
            "SELECT channel_id, message_id FROM level_request_wave_summaries WHERE guild_id=? AND wave_id=?",
            (guild.id, wave_id),
        )
        if row:
            old_channel = guild.get_channel(int(row["channel_id"]))
            if old_channel is None:
                try:
                    old_channel = await guild.fetch_channel(int(row["channel_id"]))
                except Exception:
                    old_channel = None
            if isinstance(old_channel, discord.TextChannel):
                try:
                    msg = await old_channel.fetch_message(int(row["message_id"]))
                    await msg.edit(embed=embed)
                    await self.bot.db.execute(
                        "UPDATE level_request_wave_summaries SET channel_id=?, message_id=?, updated_ts=? WHERE guild_id=? AND wave_id=?",
                        (old_channel.id, msg.id, now_ts, guild.id, wave_id),
                    )
                    return msg
                except Exception:
                    pass

        if not create_if_missing:
            return None

        msg = await channel.send(embed=embed)
        await self.bot.db.execute(
            "INSERT INTO level_request_wave_summaries(guild_id,wave_id,channel_id,message_id,created_ts,updated_ts) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(guild_id,wave_id) DO UPDATE SET channel_id=excluded.channel_id, message_id=excluded.message_id, updated_ts=excluded.updated_ts",
            (guild.id, wave_id, channel.id, msg.id, now_ts, now_ts),
        )
        return msg

    def _base_state_vars(self, row) -> Dict[str, Any]:
        if not row:
            return {
                "state": "Closed",
                "wave_id": 0,
                "submitted_count": 0,
                "request_limit": "",
                "close_ts": "",
                "request_type": "",
                "request_type_label": self._request_type_label(""),
                "request_type_line": "",
            }
        close_ts = row["close_ts"]
        request_type = self._request_type_from_row(row) if str(row["state"]) == STATE_OPEN else ""
        request_type_label = self._request_type_label(request_type)
        return {
            "state": self._state_label(str(row["state"])),
            "wave_id": int(row["wave_id"]),
            "submitted_count": int(row["submitted_count"]),
            "request_limit": "" if row["request_limit"] is None else int(row["request_limit"]),
            "close_ts": "" if close_ts is None else int(close_ts),
            "request_type": request_type,
            "request_type_label": request_type_label,
            "request_type_line": "" if not request_type else f"Type: **{request_type_label}**",
        }

    def _row_value(self, row, key: str, default: Any = "") -> Any:
        if isinstance(row, dict):
            return row.get(key, default)
        try:
            return row[key]
        except Exception:
            return default

    async def _duplicate_history_warning(
        self,
        guild_id: int,
        normalized_level_id: str,
        current_wave_id: int = 0,
        current_user_id: int = 0,
    ) -> str:
        if not normalized_level_id:
            return ""
        rows = await self.bot.db.fetchall(
            "SELECT wave_id, user_id, status, result, created_ts FROM level_request_submissions "
            "WHERE guild_id=? AND level_id=? AND NOT (wave_id=? AND user_id=?) "
            "ORDER BY created_ts DESC LIMIT 5",
            (guild_id, normalized_level_id, current_wave_id, current_user_id),
        )
        if not rows:
            return ""
        lines = ["This level was requested before:"]
        for row in rows[:4]:
            result = str(row["result"] or row["status"] or "pending").replace("_", " ")
            lines.append(
                f"- Wave **{int(row['wave_id'])}** by <@{int(row['user_id'])}> "
                f"{self._submitted_ago(row['created_ts'])} ({result})"
            )
        return "\n".join(lines)[:1024]

    def _days_in_month(self, year: int, month: int) -> int:
        return calendar.monthrange(year, month)[1]

    def _add_month(self, year: int, month: int) -> tuple[int, int]:
        month += 1
        if month > 12:
            return year + 1, 1
        return year, month

    def _parse_scheduled_open_ts(self, when: str, day: int = 0) -> tuple[Optional[int], str]:
        when_text = str(when or "").strip()
        if not when_text:
            return None, ""
        match = re.fullmatch(r"\s*(\d{1,2})(?::?(\d{2}))?\s*", when_text)
        if not match:
            return None, "Use `HH:MM` in Madrid time, for example `18:30`."

        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        if hour > 23 or minute > 59:
            return None, "Hour must be 0-23 and minute must be 0-59."

        now = now_madrid()
        if day and int(day) > 0:
            target_day = int(day)
            if target_day > 31:
                return None, "Day must be between 1 and 31."
            year, month = now.year, now.month
            for _ in range(2):
                if target_day <= self._days_in_month(year, month):
                    candidate = datetime(year, month, target_day, hour, minute, tzinfo=TZ)
                    if candidate > now:
                        return int(candidate.timestamp()), ""
                year, month = self._add_month(year, month)
            return None, "That day does not exist in this or next month."

        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate = candidate + timedelta(days=1)
        return int(candidate.timestamp()), ""

    async def _scheduled_opening_rows(self, guild_id: int, limit: int = 15):
        return await self.bot.db.fetchall(
            "SELECT id, request_limit, close_minutes, open_ts, created_by, created_ts, request_type FROM level_request_scheduled_openings "
            "WHERE guild_id=? AND status='pending' ORDER BY open_ts ASC LIMIT ?",
            (guild_id, limit),
        )

    async def get_scheduled_opening(self, guild_id: int, opening_id: int):
        return await self.bot.db.fetchone(
            "SELECT * FROM level_request_scheduled_openings WHERE guild_id=? AND id=? AND status='pending'",
            (guild_id, opening_id),
        )

    def _scheduled_openings_embed(self, rows) -> discord.Embed:
        embed = discord.Embed(
            title="Pending Request Openings",
            description="Scheduled openings use Madrid time and show Discord timestamps so staff can read them locally.",
            color=discord.Color.blurple(),
        )
        if not rows:
            embed.add_field(name="Openings", value="No request openings are currently scheduled.", inline=False)
            return embed

        lines = []
        for row in rows:
            limit = int(row["request_limit"]) if row["request_limit"] is not None else "none"
            close = f"{int(row['close_minutes'])} minutes" if row["close_minutes"] is not None else "none"
            request_type = self._request_type_label(row["request_type"] if "request_type" in row.keys() else "")
            open_ts = int(row["open_ts"])
            lines.append(
                f"**#{int(row['id'])}** - <t:{open_ts}:F> (<t:{open_ts}:R>)\n"
                f"Limit: **{limit}** | Close timer: **{close}** | Type: **{request_type}** | Created by <@{int(row['created_by'])}>"
            )
        embed.add_field(name=f"Openings ({len(rows)} shown)", value="\n\n".join(lines)[:4096], inline=False)
        embed.set_footer(text="Use the selector and buttons below to manage pending openings.")
        return embed

    async def refresh_pending_openings_panel(self, interaction: discord.Interaction, user_id: int, content: str = ""):
        rows = await self._scheduled_opening_rows(interaction.guild.id)
        embed = self._scheduled_openings_embed(rows)
        view = ScheduledOpeningsView(self, user_id, rows)
        await interaction.response.edit_message(content=content or None, embed=embed, view=view)

    async def delete_scheduled_opening(self, interaction: discord.Interaction, opening_id: int):
        await self.bot.db.execute(
            "UPDATE level_request_scheduled_openings SET status='deleted' WHERE guild_id=? AND id=? AND status='pending'",
            (interaction.guild.id, opening_id),
        )
        await self._log_request_admin_action(interaction.guild, interaction.user.id, "scheduled_opening_deleted", f"opening_id={opening_id}")
        rows = await self._scheduled_opening_rows(interaction.guild.id)
        await interaction.response.edit_message(
            content=f"Deleted scheduled opening **#{opening_id}** if it was still pending.",
            embed=self._scheduled_openings_embed(rows),
            view=ScheduledOpeningsView(self, interaction.user.id, rows),
        )

    async def open_scheduled_opening_now(self, interaction: discord.Interaction, opening_id: int, force: bool = False):
        row = await self.get_scheduled_opening(interaction.guild.id, opening_id)
        if not row:
            return await interaction.response.send_message("That opening is no longer pending.", ephemeral=True)
        state_row = await self._get_state(interaction.guild.id)
        if not force and state_row and str(state_row["state"]) == STATE_OPEN:
            return await interaction.response.send_message(
                "Requests are already open. Opening this now will start a new wave and replace the active opening. Confirm?",
                view=ScheduledOpenNowConfirmView(self, interaction.user.id, opening_id),
                ephemeral=True,
            )
        request_limit = int(row["request_limit"]) if row["request_limit"] is not None else None
        close_minutes = int(row["close_minutes"]) if row["close_minutes"] is not None else None
        request_type = self._request_type_from_row(row)
        wave_id, close_ts = await self._open_requests_now(interaction.guild, request_limit, close_minutes, request_type)
        await self.bot.db.execute(
            "UPDATE level_request_scheduled_openings SET status='opened', opened_wave_id=? WHERE guild_id=? AND id=? AND status='pending'",
            (wave_id, interaction.guild.id, opening_id),
        )
        rows = await self._scheduled_opening_rows(interaction.guild.id)
        note = f"Opened scheduled opening **#{opening_id}** as wave **{wave_id}**."
        if request_type:
            note += f" Type: **{self._request_type_label(request_type)}**."
        if close_ts:
            note += f" Closes <t:{close_ts}:R> unless the limit is reached first."
        await interaction.response.edit_message(
            content=note,
            embed=self._scheduled_openings_embed(rows),
            view=ScheduledOpeningsView(self, interaction.user.id, rows),
        )
        await self._log_request_admin_action(
            interaction.guild,
            interaction.user.id,
            "scheduled_opening_opened_now",
            f"opening_id={opening_id} wave_id={wave_id} force={force} request_type={request_type or 'any'}",
        )

    async def handle_scheduled_opening_edit_modal(
        self,
        interaction: discord.Interaction,
        opening_id: int,
        when: str,
        day: str,
        number: str,
        close_minutes: str,
        request_type: str,
    ):
        if interaction.guild is None:
            return await interaction.response.send_message("Wrong server.", ephemeral=True)
        member = await self._resolve_member(interaction.guild, interaction.user)
        if member is None or not self._is_admin(member):
            return await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)

        try:
            day_int = int(day) if str(day or "").strip() else 0
        except Exception:
            return await interaction.response.send_message("Day must be a number between 1 and 31, or blank.", ephemeral=True)
        open_ts, error = self._parse_scheduled_open_ts(when, day_int)
        if open_ts is None:
            return await interaction.response.send_message(error or "I couldn't parse that opening time.", ephemeral=True)

        def optional_positive(value: str, label: str) -> tuple[Optional[int], str]:
            text = str(value or "").strip()
            if not text:
                return None, ""
            try:
                parsed = int(text)
            except Exception:
                return None, f"{label} must be a number, or blank."
            return (parsed if parsed > 0 else None), ""

        request_limit, limit_error = optional_positive(number, "Request limit")
        if limit_error:
            return await interaction.response.send_message(limit_error, ephemeral=True)
        close_value, close_error = optional_positive(close_minutes, "Close timer")
        if close_error:
            return await interaction.response.send_message(close_error, ephemeral=True)
        normalized_type = self._normalize_request_type(request_type)
        if normalized_type is None:
            return await interaction.response.send_message(
                f"Unknown request type. Use one of: {self._request_type_help()}, or leave it blank.",
                ephemeral=True,
            )

        await self.bot.db.execute(
            "UPDATE level_request_scheduled_openings SET request_limit=?, close_minutes=?, open_ts=?, request_type=? WHERE guild_id=? AND id=? AND status='pending'",
            (request_limit, close_value, open_ts, normalized_type or None, interaction.guild.id, opening_id),
        )
        await self._log_request_admin_action(
            interaction.guild,
            interaction.user.id,
            "scheduled_opening_edited",
            f"opening_id={opening_id} open_ts={open_ts} limit={request_limit} close_minutes={close_value} request_type={normalized_type or 'any'}",
        )
        await interaction.response.send_message(
            f"Updated scheduled opening **#{opening_id}** for <t:{open_ts}:F> (<t:{open_ts}:R>).",
            ephemeral=True,
        )

    def _data_vars(self, row, data: Dict[str, Any], result_key: str = "", review: str = "", reviewer_id: int = 0) -> Dict[str, Any]:
        level_showcase = str(data.get("level_showcase") or "").strip() or "Not provided"
        notes = str(data.get("notes") or "").strip() or "No notes provided"
        result_label = self._result_label(result_key)
        result_color = self._color_name(result_key, self._color_name("pending", "blurple"))
        requester_id = int(self._row_value(row, "user_id", 0) or 0)
        wave_raw = self._row_value(row, "wave_id", "")
        try:
            wave_id = int(wave_raw)
        except Exception:
            wave_id = str(wave_raw or "")
        created_ts = self._row_value(row, "created_ts", "")
        edit_deadline_ts = data.get("edit_deadline_ts") or self._row_value(row, "edit_deadline_ts", "")
        variables = {
            **data,
            "level_id": data.get("level_id", ""),
            "level_name": data.get("level_name", ""),
            "creators": data.get("creators", ""),
            "request_type": str(data.get("request_type") or ""),
            "request_type_label": str(data.get("request_type_label") or self._request_type_label(data.get("request_type"))),
            "level_showcase": level_showcase,
            "showcase": level_showcase,
            "notes": notes,
            "requester_id": requester_id,
            "requester_mention": f"<@{requester_id}>",
            "wave_id": wave_id,
            "submitted_ts": created_ts,
            "submitted_ago": self._submitted_ago(created_ts),
            "edit_deadline_ts": edit_deadline_ts,
            "edit_deadline": f"<t:{edit_deadline_ts}:R>" if edit_deadline_ts else "",
            "edit_count": data.get("edit_count", 0),
            "duplicate_history_warning": str(data.get("duplicate_history_warning") or ""),
            "level_validation_warning": str(data.get("level_validation_warning") or ""),
            "level_validation_sources": str(data.get("level_validation_sources") or ""),
            "level_validation_checked": str(data.get("level_validation_checked") or ""),
            "level_validation_refresh": str(data.get("level_validation_refresh") or ""),
            "level_exists": str(data.get("level_exists") or "unknown"),
            "level_rated": str(data.get("level_rated") or "unknown"),
            "level_requires_showcase": str(data.get("level_requires_showcase") or "unknown"),
            "gd_level_name": str(data.get("gd_level_name") or "Unknown"),
            "gd_creator": str(data.get("gd_creator") or "Unknown"),
            "gd_difficulty": str(data.get("gd_difficulty") or "Unknown"),
            "gd_length": str(data.get("gd_length") or "Unknown"),
            "gd_stars": str(data.get("gd_stars") or "Unknown"),
            "gd_rated": str(data.get("gd_rated") or "Unknown"),
            "gd_demon": str(data.get("gd_demon") or "Unknown"),
            "gd_platformer": str(data.get("gd_platformer") or "Unknown"),
            "gd_featured": str(data.get("gd_featured") or "Unknown"),
            "gd_epic": str(data.get("gd_epic") or "Unknown"),
            "gd_flags": str(data.get("gd_flags") or "Unknown"),
            "gd_info": str(data.get("gd_info") or "GD info is not available yet."),
            "result": result_label,
            "result_key": result_key,
            "review": review or "No review provided.",
            "reviewer_id": reviewer_id or "",
            "reviewer_mention": f"<@{reviewer_id}>" if reviewer_id else "Unknown",
            "pending_color": self._color_name("pending", "blurple"),
            "result_color": result_color,
        }
        return variables

    def _weekly_data_vars(self, row, data: Dict[str, Any], result_key: str = "", review: str = "", reviewer_id: int = 0) -> Dict[str, Any]:
        rank = self._row_value(row, "rank", None)
        try:
            rank_text = f"#{int(rank)}"
        except Exception:
            rank_text = "Unknown"
        variables = self._data_vars(
            {
                "user_id": int(self._row_value(row, "user_id", 0) or 0),
                "wave_id": "Weekly",
                "created_ts": self._row_value(row, "created_ts", ""),
            },
            data,
            result_key=result_key,
            review=review,
            reviewer_id=reviewer_id,
        )
        variables.update(
            {
                "review_kind": "weekly",
                "week_start": self._row_value(row, "week_start", ""),
                "rank": rank_text,
                "weekly_rank": rank_text,
                "request_content": str(data.get("request_content") or ""),
            }
        )
        return variables

    def _result_label(self, result_key: str) -> str:
        if result_key == "sent":
            return "Sent"
        if result_key == "rejected":
            return "Rejected"
        return OTHER_REASONS.get(result_key, result_key or "Pending")

    def _status_channel_id(self, result_key: str) -> int:
        if result_key == "sent":
            return self._cfg_int("sent_channel")
        return self._cfg_int("rejected_channel")

    def _result_template_key(self, result_key: str) -> str:
        if result_key == "sent":
            return "sent_result_embed"
        if result_key == "rejected":
            return "rejected_result_embed"
        return "other_result_embed"

    async def _get_state(self, guild_id: int):
        await self.bot.db.execute(
            "INSERT OR IGNORE INTO level_request_state(guild_id,state,wave_id,submitted_count) VALUES(?,?,?,?)",
            (guild_id, STATE_CLOSED, 0, 0),
        )
        return await self.bot.db.fetchone("SELECT * FROM level_request_state WHERE guild_id=?", (guild_id,))

    async def _set_state_closed(self, guild: discord.Guild, reason: str = "manual") -> None:
        row = await self._get_state(guild.id)
        wave_id = int(row["wave_id"]) if row else 0
        if row and str(row["state"]) == STATE_CLOSED:
            await self.refresh_or_create_request_button(guild)
            if wave_id:
                try:
                    await self.update_wave_summary(guild, wave_id)
                except Exception as e:
                    await log_error(self.bot, f"Could not update wave {wave_id} summary: {repr(e)}")
            return
        await self.bot.db.execute(
            "UPDATE level_request_state SET state=?, close_ts=NULL, closed_ts=? WHERE guild_id=?",
            (STATE_CLOSED, int(time_module.time()), guild.id),
        )
        await self.refresh_or_create_request_button(guild)
        if wave_id:
            try:
                await self.update_wave_summary(guild, wave_id)
            except Exception as e:
                await log_error(self.bot, f"Could not update wave {wave_id} summary: {repr(e)}")

    async def _open_requests_now(
        self,
        guild: discord.Guild,
        request_limit: Optional[int],
        close_minutes: Optional[int],
        request_type: str = "",
    ) -> tuple[int, Optional[int]]:
        current = await self._get_state(guild.id)
        wave_id = int(current["wave_id"]) + 1
        close_ts = int(time_module.time()) + int(close_minutes) * 60 if close_minutes and int(close_minutes) > 0 else None
        normalized_type = self._normalize_request_type(request_type) or ""
        await self.bot.db.execute(
            "UPDATE level_request_state SET state=?, wave_id=?, request_limit=?, close_ts=?, submitted_count=0, opened_ts=?, closed_ts=NULL, request_type=? WHERE guild_id=?",
            (STATE_OPEN, wave_id, request_limit, close_ts, int(time_module.time()), normalized_type or None, guild.id),
        )
        await self.refresh_or_create_request_button(guild)
        return wave_id, close_ts

    async def _auto_close_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                await asyncio.sleep(20)
                guild = self.bot.get_guild(self.allowed_guild_id) if self.allowed_guild_id else None
                if guild is None:
                    continue
                row = await self._get_state(guild.id)
                if str(row["state"]) != STATE_OPEN or row["close_ts"] is None:
                    continue
                if int(row["close_ts"]) <= int(time_module.time()):
                    await self._set_state_closed(guild, reason="time limit")
            except asyncio.CancelledError:
                return
            except Exception as e:
                await log_error(self.bot, f"Level request auto-close loop error: {repr(e)}")

    async def _scheduled_open_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                await asyncio.sleep(30)
                guild = self.bot.get_guild(self.allowed_guild_id) if self.allowed_guild_id else None
                if guild is None:
                    continue
                now_ts = int(time_module.time())
                rows = await self.bot.db.fetchall(
                    "SELECT id, request_limit, close_minutes, created_by, request_type FROM level_request_scheduled_openings "
                    "WHERE guild_id=? AND status='pending' AND open_ts<=? ORDER BY open_ts ASC LIMIT 5",
                    (guild.id, now_ts),
                )
                for row in rows:
                    opening_id = int(row["id"])
                    try:
                        state_row = await self._get_state(guild.id)
                        if state_row and str(state_row["state"]) == STATE_OPEN:
                            await self.bot.db.execute(
                                "UPDATE level_request_scheduled_openings SET status='skipped_active' WHERE id=? AND guild_id=? AND status='pending'",
                                (opening_id, guild.id),
                            )
                            await self._log_request_admin_action(
                                guild,
                                int(row["created_by"] or 0),
                                "scheduled_opening_skipped",
                                f"opening_id={opening_id} reason=requests_already_open",
                            )
                            continue
                        request_limit = int(row["request_limit"]) if row["request_limit"] is not None else None
                        close_minutes = int(row["close_minutes"]) if row["close_minutes"] is not None else None
                        request_type = self._request_type_from_row(row)
                        wave_id, _ = await self._open_requests_now(guild, request_limit, close_minutes, request_type)
                        await self.bot.db.execute(
                            "UPDATE level_request_scheduled_openings SET status='opened', opened_wave_id=? WHERE id=? AND guild_id=?",
                            (wave_id, opening_id, guild.id),
                        )
                    except Exception as e:
                        await log_error(self.bot, f"Scheduled request opening {opening_id} failed: {repr(e)}")
            except asyncio.CancelledError:
                return
            except Exception as e:
                await log_error(self.bot, f"Scheduled request opening loop error: {repr(e)}")

    def _in_allowed_guild(self, ctx: discord.ApplicationContext) -> bool:
        return ctx.guild is not None and ctx.guild.id == self.allowed_guild_id

    async def _resolve_member(self, guild: discord.Guild, user) -> Optional[discord.Member]:
        if isinstance(user, discord.Member):
            return user

        user_id = getattr(user, "id", user)
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

    def _is_admin(self, member: discord.Member) -> bool:
        return is_admin_or_owner(member, self.bot.config.get_int_list("roles", "admin_owner_role_ids"))

    def _is_mod(self, member: discord.Member) -> bool:
        allow_manage_guild = bool(self.bot.config.get("permissions", "manage_guild_counts_as_mod", default=True))
        return is_mod(
            member,
            self.bot.config.get_int("roles", "MOD_ROLE_ID") or 0,
            allow_manage_guild=allow_manage_guild,
        )

    async def _configured_channel(self, guild: discord.Guild, key: str) -> Optional[discord.TextChannel]:
        channel_id = self._cfg_int(key)
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None and channel_id:
            try:
                channel = await guild.fetch_channel(channel_id)
            except Exception:
                channel = None
        return channel if isinstance(channel, discord.TextChannel) else None

    async def refresh_or_create_request_button(self, guild: discord.Guild) -> Optional[discord.Message]:
        row = await self._get_state(guild.id)
        channel = await self._configured_channel(guild, "request_channel")
        if channel is None:
            return None

        embed = self._request_button_embed(row)
        view = LevelRequestButtonView(label=self._request_button_label(), disabled=False)
        message_id = row["request_message_id"]
        current_channel_id = row["request_channel_id"]

        if message_id and current_channel_id:
            old_channel = guild.get_channel(int(current_channel_id))
            if isinstance(old_channel, discord.TextChannel):
                try:
                    msg = await old_channel.fetch_message(int(message_id))
                    await msg.edit(embed=embed, view=view)
                    if old_channel.id != channel.id:
                        try:
                            await msg.delete()
                        except Exception:
                            pass
                        sent = await channel.send(embed=embed, view=view)
                        await self.bot.db.execute(
                            "UPDATE level_request_state SET request_channel_id=?, request_message_id=? WHERE guild_id=?",
                            (channel.id, sent.id, guild.id),
                        )
                        return sent
                    return msg
                except Exception:
                    pass

        sent = await channel.send(embed=embed, view=view)
        await self.bot.db.execute(
            "UPDATE level_request_state SET request_channel_id=?, request_message_id=? WHERE guild_id=?",
            (channel.id, sent.id, guild.id),
        )
        return sent

    async def refresh_request_button(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        member = await self._resolve_member(ctx.guild, ctx.user)
        if member is None or not self._is_mod(member):
            return await ctx.respond("Only mods can use this.", ephemeral=True)

        msg = await self.refresh_or_create_request_button(ctx.guild)
        if msg is None:
            return await ctx.respond(self._message("not_configured", "The request system is not fully configured yet."), ephemeral=True)
        await self._log_request_admin_action(ctx.guild, ctx.user.id, "refresh_request_button", f"message_id={msg.id}")
        await ctx.respond(f"Request button refreshed: {msg.jump_url}", ephemeral=True)

    async def open_requests(
        self,
        ctx: discord.ApplicationContext,
        number: int = 0,
        time: int = 0,
        when: str = "",
        day: int = 0,
        request_type: str = "",
    ):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        member = await self._resolve_member(ctx.guild, ctx.user)
        if member is None or not self._is_admin(member):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

        request_limit = int(number) if number and int(number) > 0 else None
        close_minutes = int(time) if time and int(time) > 0 else None
        normalized_type = self._normalize_request_type(request_type)
        if normalized_type is None:
            return await ctx.respond(f"Unknown request type. Use one of: {self._request_type_help()}, or leave it blank.", ephemeral=True)
        type_label = self._request_type_label(normalized_type)

        if str(when or "").strip():
            open_ts, error = self._parse_scheduled_open_ts(when, day)
            if open_ts is None:
                return await ctx.respond(error or "I couldn't parse that opening time.", ephemeral=True)
            await self.bot.db.execute(
                "INSERT INTO level_request_scheduled_openings(guild_id,request_limit,close_minutes,open_ts,created_by,created_ts,status,request_type) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (ctx.guild.id, request_limit, close_minutes, open_ts, ctx.user.id, int(time_module.time()), "pending", normalized_type or None),
            )
            details = [f"Request opening scheduled for <t:{open_ts}:F> (<t:{open_ts}:R>)."]
            if normalized_type:
                details.append(f"Type: **{type_label}**.")
            if request_limit:
                details.append(f"Limit: **{request_limit}** successful requests.")
            if close_minutes:
                details.append(f"Requests will close after **{close_minutes}** minutes unless the limit is reached first.")
            details.append("Use `/pending-openings` to edit, delete, or open it early.")
            await self._log_request_admin_action(
                ctx.guild,
                ctx.user.id,
                "scheduled_opening_created",
                f"open_ts={open_ts} limit={request_limit} close_minutes={close_minutes} request_type={normalized_type or 'any'}",
            )
            return await ctx.respond(" ".join(details), ephemeral=True)

        wave_id, close_ts = await self._open_requests_now(ctx.guild, request_limit, close_minutes, normalized_type or "")
        await self._log_request_admin_action(
            ctx.guild,
            ctx.user.id,
            "requests_opened",
            f"wave_id={wave_id} limit={request_limit} close_ts={close_ts} request_type={normalized_type or 'any'}",
        )

        details = [f"Wave **{wave_id}** opened."]
        if normalized_type:
            details.append(f"Type: **{type_label}**.")
        if request_limit:
            details.append(f"Limit: **{request_limit}** successful requests.")
        if close_ts:
            details.append(f"Closes at <t:{close_ts}:R>.")
        if not request_limit and not close_ts:
            details.append("No limit or timer set.")
        await ctx.respond(" ".join(details), ephemeral=True)

    async def pending_openings(
        self,
        ctx: discord.ApplicationContext,
        action: str = "list",
        opening_id: int = 0,
        number: int = -1,
        time: int = -1,
        when: str = "",
        day: int = 0,
        request_type: str = "",
    ):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        member = await self._resolve_member(ctx.guild, ctx.user)
        if member is None or not self._is_admin(member):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

        action = str(action or "list").strip().casefold()
        if action in {"delete", "remove", "cancel"}:
            if not opening_id:
                return await ctx.respond("Please provide the scheduled opening ID to delete.", ephemeral=True)
            await self.bot.db.execute(
                "UPDATE level_request_scheduled_openings SET status='deleted' WHERE guild_id=? AND id=? AND status='pending'",
                (ctx.guild.id, opening_id),
            )
            await self._log_request_admin_action(ctx.guild, ctx.user.id, "scheduled_opening_deleted", f"opening_id={opening_id}")
            return await ctx.respond(f"Deleted scheduled opening **#{opening_id}** if it was still pending.", ephemeral=True)

        if action in {"edit", "update"}:
            if not opening_id:
                return await ctx.respond("Please provide the scheduled opening ID to edit.", ephemeral=True)
            row = await self.bot.db.fetchone(
                "SELECT * FROM level_request_scheduled_openings WHERE guild_id=? AND id=? AND status='pending'",
                (ctx.guild.id, opening_id),
            )
            if not row:
                return await ctx.respond("I couldn't find a pending opening with that ID.", ephemeral=True)

            new_limit = row["request_limit"]
            new_close = row["close_minutes"]
            new_open_ts = int(row["open_ts"])
            new_type = row["request_type"] if "request_type" in row.keys() else None
            if number >= 0:
                new_limit = int(number) if int(number) > 0 else None
            if time >= 0:
                new_close = int(time) if int(time) > 0 else None
            if str(request_type or "").strip():
                parsed_type = self._normalize_request_type(request_type)
                if parsed_type is None:
                    return await ctx.respond(
                        f"Unknown request type. Use one of: {self._request_type_help()}, or use `any` to clear it.",
                        ephemeral=True,
                    )
                new_type = parsed_type or None
            if str(when or "").strip():
                parsed_ts, error = self._parse_scheduled_open_ts(when, day)
                if parsed_ts is None:
                    return await ctx.respond(error or "I couldn't parse that opening time.", ephemeral=True)
                new_open_ts = parsed_ts

            await self.bot.db.execute(
                "UPDATE level_request_scheduled_openings SET request_limit=?, close_minutes=?, open_ts=?, request_type=? WHERE guild_id=? AND id=? AND status='pending'",
                (new_limit, new_close, new_open_ts, new_type, ctx.guild.id, opening_id),
            )
            await self._log_request_admin_action(
                ctx.guild,
                ctx.user.id,
                "scheduled_opening_edited",
                f"opening_id={opening_id} open_ts={new_open_ts} limit={new_limit} close_minutes={new_close} request_type={new_type or 'any'}",
            )
            return await ctx.respond(
                f"Updated scheduled opening **#{opening_id}** for <t:{new_open_ts}:F> (<t:{new_open_ts}:R>). Type: **{self._request_type_label(new_type)}**.",
                ephemeral=True,
            )

        rows = await self._scheduled_opening_rows(ctx.guild.id)
        await ctx.respond(embed=self._scheduled_openings_embed(rows), view=ScheduledOpeningsView(self, ctx.user.id, rows), ephemeral=True)

    async def close_requests(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        member = await self._resolve_member(ctx.guild, ctx.user)
        if member is None or not self._is_admin(member):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

        row = await self._get_state(ctx.guild.id)
        if str(row["state"]) == STATE_CLOSED:
            await self.refresh_or_create_request_button(ctx.guild)
            return await ctx.respond("Requests are already closed.", ephemeral=True)

        await self._set_state_closed(ctx.guild, reason=f"manual by {ctx.user.id}")
        await self._log_request_admin_action(ctx.guild, ctx.user.id, "requests_closed", f"wave_id={int(row['wave_id'])}")
        await ctx.respond("Requests closed.", ephemeral=True)

    async def requests_are(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        row = await self._get_state(ctx.guild.id)
        state_label = self._state_label(str(row["state"]))
        parts = [f"Requests are **{state_label}**.", f"Wave: **{int(row['wave_id'])}**.", f"Submitted: **{int(row['submitted_count'])}**."]
        if row["request_limit"] is not None:
            parts.append(f"Limit: **{int(row['request_limit'])}**.")
        request_type = self._request_type_from_row(row) if str(row["state"]) == STATE_OPEN else ""
        if request_type:
            parts.append(f"Type: **{self._request_type_label(request_type)}**.")
        if row["close_ts"] is not None and str(row["state"]) == STATE_OPEN:
            parts.append(f"Closes <t:{int(row['close_ts'])}:R>.")
        await ctx.respond(" ".join(parts), ephemeral=True)

    async def _requirements_ok(self, member: discord.Member) -> bool:
        banned_role_id = self._cfg_int("request_banned_role_id")
        if banned_role_id and any(role.id == banned_role_id for role in member.roles):
            return False
        required_roles = self._cfg_int_list("required_role_ids")
        if not required_roles:
            return True
        return member_has_any_role(member, required_roles)

    async def handle_request_button(self, interaction: discord.Interaction):
        if interaction.guild is None:
            return await interaction.response.send_message("Wrong server.", ephemeral=True)

        row = await self._get_state(interaction.guild.id)
        try:
            row = await self._state_after_timed_close_check(interaction.guild, row)
        except Exception as e:
            await log_error(self.bot, f"Could not refresh request button after timed close: {repr(e)}")

        request_row = await self._current_user_submission(interaction.guild.id, int(row["wave_id"]), interaction.user.id)
        if request_row:
            if self._can_edit_submission(row, request_row):
                return await interaction.response.send_modal(
                    LevelRequestModal(
                        self,
                        interaction.user.id,
                        edit=True,
                        initial=self._request_initial_values(request_row),
                    )
                )
            if str(request_row["status"]) != "pending":
                return await interaction.response.send_message("That request has already been reviewed.", ephemeral=True)
            if str(row["state"]) != STATE_OPEN:
                return await interaction.response.send_message(self._message("edit_window_expired", "Your request can no longer be edited."), ephemeral=True)
            return await interaction.response.send_message(
                self._message("already_submitted", "You already submitted a level during this request wave."),
                ephemeral=True,
            )

        if str(row["state"]) != STATE_OPEN:
            return await interaction.response.send_message(self._message("closed", "Requests are closed :/"), ephemeral=True)

        member = await self._resolve_member(interaction.guild, interaction.user)
        if member is None or not await self._requirements_ok(member):
            return await interaction.response.send_message(
                self._message("no_requirements", "You don't meet the requirements, please read the requesting rules"),
                ephemeral=True,
            )

        has_requested_role_id = self._cfg_int("has_requested_role_id")
        has_requested = has_requested_role_id and any(role.id == has_requested_role_id for role in member.roles)
        if has_requested or not has_requested_role_id:
            return await interaction.response.send_modal(LevelRequestModal(self, interaction.user.id))

        await interaction.response.send_message(
            self._message("first_time_prompt", "Please choose one option below."),
            view=FirstRequestChoiceView(self, interaction.user.id),
            ephemeral=True,
        )

    async def edit_request(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        row = await self._get_state(ctx.guild.id)
        try:
            row = await self._state_after_timed_close_check(ctx.guild, row)
        except Exception as e:
            await log_error(self.bot, f"Could not close expired request wave before edit command: {repr(e)}")
        request_row = await self._current_user_submission(ctx.guild.id, int(row["wave_id"]), ctx.user.id)
        if not request_row:
            return await ctx.respond("You do not have a request in the current wave.", ephemeral=True)
        if str(request_row["status"]) != "pending":
            return await ctx.respond("That request has already been reviewed.", ephemeral=True)
        if not self._can_edit_submission(row, request_row):
            return await ctx.respond(self._message("edit_window_expired", "Your request can no longer be edited."), ephemeral=True)
        initial = self._request_initial_values(request_row)
        await ctx.send_modal(LevelRequestModal(self, ctx.user.id, edit=True, initial=initial))

    async def handle_first_choice(self, interaction: discord.Interaction, will_request_again: bool):
        if interaction.guild is None:
            return await interaction.response.send_message("Wrong server.", ephemeral=True)
        member = await self._resolve_member(interaction.guild, interaction.user)
        if member is None:
            return await interaction.response.send_message("Member not found.", ephemeral=True)

        if will_request_again:
            role_id = self._cfg_int("request_banned_role_id")
            role = interaction.guild.get_role(role_id) if role_id else None
            if role is None:
                return await interaction.response.send_message(self._message("not_configured", "The request system is not fully configured yet."), ephemeral=True)
            try:
                if role not in member.roles:
                    await member.add_roles(role, reason="Level request first-time choice")
            except Exception:
                return await interaction.response.send_message("I couldn't give you the configured role.", ephemeral=True)
            return await interaction.response.send_message("Done.", ephemeral=True)

        role_id = self._cfg_int("has_requested_role_id")
        role = interaction.guild.get_role(role_id) if role_id else None
        if role is not None:
            try:
                if role not in member.roles:
                    await member.add_roles(role, reason="Level request started")
            except Exception:
                return await interaction.response.send_message("I couldn't give you the configured role.", ephemeral=True)

        await interaction.response.send_modal(LevelRequestModal(self, interaction.user.id))

    async def handle_request_form(self, interaction: discord.Interaction, data: Dict[str, str]):
        if interaction.guild is None:
            return await self._reply_ephemeral(interaction, "Wrong server.")
        if not data.get("level_id") or not data.get("level_name") or not data.get("creators"):
            return await self._reply_ephemeral(interaction, "Missing required fields.")
        validation_errors = self._validate_request_data(data)
        if validation_errors:
            return await self._reply_ephemeral(
                interaction,
                self._message_formatted(
                    "validation_error",
                    "Please fix your request before submitting: {errors}",
                    {"errors": " ".join(validation_errors)},
                ),
            )

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        external_errors, level_validation = await self._validate_level_external(data, interaction.guild.id, interaction.user.id)
        if external_errors:
            return await self._reply_ephemeral(
                interaction,
                self._message_formatted(
                    "validation_error",
                    "Please fix your request before submitting: {errors}",
                    {"errors": " ".join(external_errors)},
                ),
            )

        refresh_after_close = False
        closed_before_submit = False
        closed_by_timer = False
        async with self._submit_lock:
            row = await self._get_state(interaction.guild.id)
            if str(row["state"]) != STATE_OPEN:
                closed_before_submit = True
            elif row["close_ts"] is not None and int(row["close_ts"]) <= int(time_module.time()):
                await self.bot.db.execute(
                    "UPDATE level_request_state SET state=?, close_ts=NULL, closed_ts=? WHERE guild_id=?",
                    (STATE_CLOSED, int(time_module.time()), interaction.guild.id),
                )
                refresh_after_close = True
                closed_by_timer = True

            if not closed_before_submit and not refresh_after_close:
                wave_id = int(row["wave_id"])
                user_id = interaction.user.id
                normalized_level_id = self._normalize_level_id(data["level_id"])
                request_type = self._request_type_from_row(row)
                type_error = self._request_type_validation_error(request_type, data, level_validation)
                if type_error:
                    return await self._reply_ephemeral(interaction, type_error)

                existing_user = await self.bot.db.fetchone(
                    "SELECT 1 FROM level_request_submissions WHERE guild_id=? AND wave_id=? AND user_id=?",
                    (interaction.guild.id, wave_id, user_id),
                )
                if existing_user:
                    return await self._reply_ephemeral(interaction, self._message("already_submitted", "You already submitted a level during this request wave."))

                existing_level = await self.bot.db.fetchone(
                    "SELECT 1 FROM level_request_submissions WHERE guild_id=? AND wave_id=? AND level_id=?",
                    (interaction.guild.id, wave_id, normalized_level_id),
                )
                if existing_level:
                    return await self._reply_ephemeral(interaction, self._message("duplicate_level", "That level ID has already been submitted during this request wave."))

                target_channel = await self._configured_channel(interaction.guild, "level_requested")
                if target_channel is None:
                    return await self._reply_ephemeral(interaction, self._message("not_configured", "The request system is not fully configured yet."))

                data = dict(data)
                data["level_id"] = str(data["level_id"]).strip()
                data["level_id_normalized"] = normalized_level_id
                data["request_type"] = request_type
                data["request_type_label"] = self._request_type_label(request_type)
                data["edit_deadline_ts"] = self._edit_deadline_ts_for_state(row)
                data["edit_count"] = 0
                data["duplicate_history_warning"] = await self._duplicate_history_warning(
                    interaction.guild.id,
                    normalized_level_id,
                    current_wave_id=wave_id,
                    current_user_id=user_id,
                )
                data = self._apply_level_validation_vars(data, level_validation)
                data_json = json.dumps(data, separators=(",", ":"))
                created_ts = int(time_module.time())

                try:
                    await self.bot.db.execute(
                        "INSERT INTO level_request_submissions(guild_id,wave_id,user_id,level_id,status,created_ts,data_json) VALUES(?,?,?,?,?,?,?)",
                        (interaction.guild.id, wave_id, user_id, normalized_level_id, "pending", created_ts, data_json),
                    )
                except Exception as e:
                    await log_error(self.bot, f"Could not save level request submission before sending embed: {repr(e)}")
                    return await self._reply_ephemeral(interaction, "I couldn't submit your request right now.")

                try:
                    temp_row = {"guild_id": interaction.guild.id, "wave_id": wave_id, "user_id": user_id, "created_ts": created_ts}
                    embed = self._embed_from_template(
                        self._cfg("level_requested_embed", default={}) or {},
                        self._data_vars(temp_row, data),
                        default_color=self._color_name("pending", "blurple"),
                    )
                    msg = await target_channel.send(embed=embed, view=LevelRequestReviewView())
                except Exception as e:
                    try:
                        await self.bot.db.execute(
                            "DELETE FROM level_request_submissions WHERE guild_id=? AND wave_id=? AND user_id=?",
                            (interaction.guild.id, wave_id, user_id),
                        )
                    except Exception as cleanup_error:
                        await log_error(self.bot, f"Could not clean up unsent level request submission: {repr(cleanup_error)}")
                    await log_error(self.bot, f"Could not send level request embed: {repr(e)}")
                    return await self._reply_ephemeral(interaction, "I couldn't submit your request right now.")

                new_count = int(row["submitted_count"]) + 1
                try:
                    await self.bot.db.execute(
                        "UPDATE level_request_submissions SET request_message_id=? WHERE guild_id=? AND wave_id=? AND user_id=?",
                        (msg.id, interaction.guild.id, wave_id, user_id),
                    )

                    if row["request_limit"] is not None and new_count >= int(row["request_limit"]):
                        await self.bot.db.execute(
                            "UPDATE level_request_state SET submitted_count=?, state=?, close_ts=NULL, closed_ts=? WHERE guild_id=?",
                            (new_count, STATE_CLOSED, int(time_module.time()), interaction.guild.id),
                        )
                        refresh_after_close = True
                    else:
                        await self.bot.db.execute(
                            "UPDATE level_request_state SET submitted_count=? WHERE guild_id=?",
                            (new_count, interaction.guild.id),
                        )
                except Exception as e:
                    try:
                        await msg.delete()
                    except Exception:
                        try:
                            await msg.edit(view=LevelRequestReviewView(disabled=True))
                        except Exception:
                            pass
                    try:
                        await self.bot.db.execute(
                            "DELETE FROM level_request_submissions WHERE guild_id=? AND wave_id=? AND user_id=?",
                            (interaction.guild.id, wave_id, user_id),
                        )
                    except Exception as cleanup_error:
                        await log_error(self.bot, f"Could not clean up failed level request submission: {repr(cleanup_error)}")
                    await log_error(self.bot, f"Could not finish level request submission after sending embed: {repr(e)}")
                    return await self._reply_ephemeral(interaction, "I couldn't finish submitting your request right now.")

        if refresh_after_close:
            try:
                await self.refresh_or_create_request_button(interaction.guild)
                await self.update_wave_summary(interaction.guild, int(row["wave_id"]))
            except Exception as e:
                await log_error(self.bot, f"Could not refresh request button after request close: {repr(e)}")
        if closed_before_submit or closed_by_timer:
            return await self._reply_ephemeral(interaction, self._message("closed", "Requests are closed :/"))

        success_text = self._message("success", "Your request has been submitted!")
        final_state = await self._get_state(interaction.guild.id)
        success_text += (
            " You can edit it with `/edit-request` or by pressing the request button "
            f"{self._edit_window_text(final_state)}."
        )
        await self._reply_ephemeral(interaction, success_text)

    async def handle_request_edit_form(self, interaction: discord.Interaction, data: Dict[str, str]):
        if interaction.guild is None:
            return await self._reply_ephemeral(interaction, "Wrong server.")
        if not data.get("level_id") or not data.get("level_name") or not data.get("creators"):
            return await self._reply_ephemeral(interaction, "Missing required fields.")
        validation_errors = self._validate_request_data(data)
        if validation_errors:
            return await self._reply_ephemeral(
                interaction,
                self._message_formatted(
                    "validation_error",
                    "Please fix your request before submitting: {errors}",
                    {"errors": " ".join(validation_errors)},
                ),
            )
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        external_errors, level_validation = await self._validate_level_external(data, interaction.guild.id, interaction.user.id)
        if external_errors:
            return await self._reply_ephemeral(
                interaction,
                self._message_formatted(
                    "validation_error",
                    "Please fix your request before submitting: {errors}",
                    {"errors": " ".join(external_errors)},
                ),
            )

        async with self._submit_lock:
            state_row = await self._get_state(interaction.guild.id)
            try:
                state_row = await self._state_after_timed_close_check(interaction.guild, state_row)
            except Exception as e:
                await log_error(self.bot, f"Could not close expired request wave before edit submit: {repr(e)}")
            wave_id = int(state_row["wave_id"])
            row = await self.bot.db.fetchone(
                "SELECT * FROM level_request_submissions WHERE guild_id=? AND wave_id=? AND user_id=?",
                (interaction.guild.id, wave_id, interaction.user.id),
            )
            if not row:
                return await self._reply_ephemeral(interaction, "You do not have a request in the current wave.")
            if str(row["status"]) != "pending":
                return await self._reply_ephemeral(interaction, "That request has already been reviewed.")
            if not self._can_edit_submission(state_row, row):
                return await self._reply_ephemeral(interaction, self._message("edit_window_expired", "Your request can no longer be edited."))

            normalized_level_id = self._normalize_level_id(data["level_id"])
            request_type = self._request_type_from_row(state_row)
            type_error = self._request_type_validation_error(request_type, data, level_validation)
            if type_error:
                return await self._reply_ephemeral(interaction, type_error)

            existing_level = await self.bot.db.fetchone(
                "SELECT 1 FROM level_request_submissions WHERE guild_id=? AND wave_id=? AND level_id=? AND user_id<>?",
                (interaction.guild.id, wave_id, normalized_level_id, interaction.user.id),
            )
            if existing_level:
                return await self._reply_ephemeral(interaction, self._message("duplicate_level", "That level ID has already been submitted during this request wave."))

            message_id = int(row["request_message_id"] or 0)
            target_channel = await self._configured_channel(interaction.guild, "level_requested")
            if target_channel is None or not message_id:
                return await self._reply_ephemeral(interaction, "I couldn't find the original request message.")
            try:
                msg = await target_channel.fetch_message(message_id)
            except Exception as e:
                await log_error(self.bot, f"Could not fetch request for edit message_id={message_id}: {repr(e)}")
                return await self._reply_ephemeral(interaction, "I couldn't find the original request message.")

            old_data_json = row["data_json"] or "{}"
            old_data = self._safe_json_loads(old_data_json, {})
            try:
                edit_count = int(old_data.get("edit_count") or 0) + 1
            except Exception:
                edit_count = 1

            data = dict(data)
            data["level_id"] = self._clean_level_id(data["level_id"])
            data["level_id_normalized"] = normalized_level_id
            data["request_type"] = request_type
            data["request_type_label"] = self._request_type_label(request_type)
            data["edit_deadline_ts"] = self._edit_deadline_ts_for_state(state_row)
            data["edit_count"] = edit_count
            data["duplicate_history_warning"] = await self._duplicate_history_warning(
                interaction.guild.id,
                normalized_level_id,
                current_wave_id=wave_id,
                current_user_id=interaction.user.id,
            )
            data = self._apply_level_validation_vars(data, level_validation)
            data_json = json.dumps(data, separators=(",", ":"))
            embed = self._embed_from_template(
                self._cfg("level_requested_embed", default={}) or {},
                self._data_vars(row, data),
                default_color=self._color_name("pending", "blurple"),
            )

            try:
                await self.bot.db.execute(
                    "UPDATE level_request_submissions SET level_id=?, data_json=? WHERE guild_id=? AND wave_id=? AND user_id=? AND status='pending'",
                    (normalized_level_id, data_json, interaction.guild.id, wave_id, interaction.user.id),
                )
                try:
                    await self.bot.db.execute(
                        "INSERT INTO level_request_edit_audit(guild_id,wave_id,user_id,request_message_id,old_level_id,new_level_id,old_data_json,new_data_json,edited_ts) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            interaction.guild.id,
                            wave_id,
                            interaction.user.id,
                            message_id,
                            str(row["level_id"] or ""),
                            normalized_level_id,
                            old_data_json,
                            data_json,
                            int(time_module.time()),
                        ),
                    )
                except Exception as audit_error:
                    await log_error(self.bot, f"Could not write request edit audit for message_id={message_id}: {repr(audit_error)}")
                await msg.edit(embed=embed, view=LevelRequestReviewView())
            except Exception as e:
                await log_error(self.bot, f"Could not edit level request message_id={message_id}: {repr(e)}")
                return await self._reply_ephemeral(interaction, "I couldn't update your request right now.")

        await self._reply_ephemeral(interaction, self._message("edit_success", "Your request has been updated!"))

    async def _submission_by_message(self, guild_id: int, message_id: int):
        return await self.bot.db.fetchone(
            "SELECT * FROM level_request_submissions WHERE guild_id=? AND request_message_id=?",
            (guild_id, message_id),
        )

    async def _weekly_submission_by_message(self, guild_id: int, message_id: int):
        return await self.bot.db.fetchone(
            "SELECT * FROM weekly_request_reviews WHERE guild_id=? AND request_message_id=?",
            (guild_id, message_id),
        )

    async def _review_target_by_message(self, guild_id: int, message_id: int):
        row = await self._submission_by_message(guild_id, message_id)
        if row:
            return "wave", row
        row = await self._weekly_submission_by_message(guild_id, message_id)
        if row:
            return "weekly", row
        return "", None

    async def _channel_by_id(self, guild: discord.Guild, channel_id: int) -> Optional[discord.TextChannel]:
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None and channel_id:
            try:
                channel = await guild.fetch_channel(channel_id)
            except Exception:
                channel = None
        return channel if isinstance(channel, discord.TextChannel) else None

    async def _review_target_channel(self, guild: discord.Guild, target_kind: str, row) -> Optional[discord.TextChannel]:
        if target_kind == "weekly":
            return await self._channel_by_id(guild, int(self._row_value(row, "channel_id", 0) or 0))
        return await self._configured_channel(guild, "level_requested")

    async def handle_review_button(self, interaction: discord.Interaction, action: str):
        if interaction.guild is None or interaction.message is None:
            return await interaction.response.send_message("Request not found.", ephemeral=True)
        if not await self._is_reviewer_interaction(interaction):
            return await interaction.response.send_message("Only reviewers can use these controls.", ephemeral=True)
        _, row = await self._review_target_by_message(interaction.guild.id, interaction.message.id)
        if not row:
            return await interaction.response.send_message("Request not found.", ephemeral=True)
        if str(row["status"]) != "pending":
            return await interaction.response.send_message("This request has already been reviewed.", ephemeral=True)

        if action == "other":
            return await interaction.response.send_message("Choose a result:", view=OtherReasonView(self, interaction.message.id), ephemeral=True)

        await interaction.response.send_modal(ReviewModal(self, interaction.message.id, action))

    async def handle_review_submission(self, interaction: discord.Interaction, message_id: int, result_key: str, review: str):
        await self._finalize_review(interaction, message_id, result_key, review)

    async def handle_other_reason(self, interaction: discord.Interaction, message_id: int, reason_key: str):
        if not await self._is_reviewer_interaction(interaction):
            return await interaction.response.send_message("Only reviewers can use these controls.", ephemeral=True)
        await self._finalize_review(interaction, message_id, reason_key, "")

    async def _finalize_review(self, interaction: discord.Interaction, message_id: int, result_key: str, review: str):
        if interaction.guild is None:
            return await self._reply_ephemeral(interaction, "Wrong server.")
        if not await self._is_reviewer_interaction(interaction):
            return await self._reply_ephemeral(interaction, "Only reviewers can use these controls.")

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        async with self._review_lock:
            target_kind, row = await self._review_target_by_message(interaction.guild.id, message_id)
            if not row:
                return await self._reply_ephemeral(interaction, "Request not found.")
            if str(row["status"]) != "pending":
                return await self._reply_ephemeral(interaction, "This request has already been reviewed.")

            data = json.loads(row["data_json"] or "{}")
            reviewer_id = interaction.user.id
            result_label = self._result_label(result_key)
            if target_kind == "weekly":
                variables = self._weekly_data_vars(row, data, result_key=result_key, review=review, reviewer_id=reviewer_id)
            else:
                variables = self._data_vars(row, data, result_key=result_key, review=review, reviewer_id=reviewer_id)

            request_channel = await self._review_target_channel(interaction.guild, target_kind, row)
            if request_channel is None:
                return await self._reply_ephemeral(interaction, "I couldn't find the original request channel, so I did not mark it reviewed.")
            try:
                msg = await request_channel.fetch_message(message_id)
            except Exception as e:
                await log_error(self.bot, f"Could not fetch reviewed level request {message_id}: {repr(e)}")
                return await self._reply_ephemeral(interaction, "I couldn't find the original request message, so I did not mark it reviewed.")

            result_channel_id = self._status_channel_id(result_key)
            result_channel = await self._channel_by_id(interaction.guild, result_channel_id)
            if not isinstance(result_channel, discord.TextChannel):
                return await self._reply_ephemeral(interaction, "I couldn't find the result channel, so I did not mark it reviewed.")

            reviewed_ts = int(time_module.time())
            try:
                if target_kind == "weekly":
                    await self.bot.db.execute(
                        "UPDATE weekly_request_reviews SET status='reviewed', result=?, review_text=?, reviewed_by=?, reviewed_ts=? WHERE guild_id=? AND request_message_id=? AND status='pending'",
                        (result_key, review, reviewer_id, reviewed_ts, interaction.guild.id, message_id),
                    )
                else:
                    await self.bot.db.execute(
                        "UPDATE level_request_submissions SET status='reviewed', result=?, review_text=?, reviewed_by=?, reviewed_ts=? WHERE guild_id=? AND request_message_id=? AND status='pending'",
                        (result_key, review, reviewer_id, reviewed_ts, interaction.guild.id, message_id),
                    )
            except Exception as e:
                await log_error(self.bot, f"Could not save reviewed level request {message_id}: {repr(e)}")
                return await self._reply_ephemeral(interaction, "I couldn't save the review, so I did not update the request.")

            result_warning = ""
            try:
                final_embed = self._embed_from_template(
                    self._cfg("level_reviewed_embed", default={}) or {},
                    variables,
                    default_color=self._color_name(result_key, "red"),
                )
                await msg.edit(embed=final_embed, view=LevelRequestReviewView(disabled=True))
            except Exception as e:
                result_warning += " I saved the review, but couldn't update the original request embed."
                await log_error(self.bot, f"Could not edit reviewed level request {message_id}: {repr(e)}")

            try:
                result_embed = self._embed_from_template(
                    self._cfg(self._result_template_key(result_key), default={}) or {},
                    variables,
                    default_color=self._color_name(result_key, "red"),
                )
                await result_channel.send(
                    content=f"<@{int(self._row_value(row, 'user_id', 0) or 0)}>",
                    embed=result_embed,
                    allowed_mentions=user_mentions(),
                )
            except Exception as e:
                result_warning += " I couldn't send the result notification, so please check the configured result channel permissions."
                await log_error(self.bot, f"Could not send level request result {message_id}: {repr(e)}")

            if target_kind == "wave":
                try:
                    state_row = await self._get_state(interaction.guild.id)
                    request_wave_id = int(row["wave_id"])
                    create_summary = int(state_row["wave_id"]) != request_wave_id or str(state_row["state"]) == STATE_CLOSED
                    await self.update_wave_summary(interaction.guild, request_wave_id, create_if_missing=create_summary)
                except Exception as e:
                    await log_error(self.bot, f"Could not update wave summary for reviewed request {message_id}: {repr(e)}")

        await self._reply_ephemeral(interaction, f"Request marked as {result_label}.{result_warning}")

    async def repair_request_system(self, guild: discord.Guild) -> Dict[str, Any]:
        result = {
            "request_button_refreshed": False,
            "wave_summary_refreshed": False,
            "pending_messages_recreated": 0,
            "pending_messages_refreshed": 0,
            "reviewed_messages_locked": 0,
            "stale_validations_refreshed": 0,
            "validation_cache_pruned": False,
            "errors": [],
        }

        try:
            result["request_button_refreshed"] = await self.refresh_or_create_request_button(guild) is not None
        except Exception as e:
            result["errors"].append(f"request button: {type(e).__name__}")
            await log_error(self.bot, f"Request repair could not refresh button: {repr(e)}")

        try:
            state_row = await self._get_state(guild.id)
            wave_id = int(state_row["wave_id"]) if state_row else 0
            if wave_id:
                result["wave_summary_refreshed"] = await self.update_wave_summary(guild, wave_id, create_if_missing=True) is not None
        except Exception as e:
            result["errors"].append(f"wave summary: {type(e).__name__}")
            await log_error(self.bot, f"Request repair could not refresh wave summary: {repr(e)}")

        target_channel = await self._configured_channel(guild, "level_requested")
        if target_channel is None:
            result["errors"].append("level_requested channel missing")
            return result

        now_ts = int(time_module.time())
        rows = await self.bot.db.fetchall(
            "SELECT * FROM level_request_submissions WHERE guild_id=? AND status='pending' ORDER BY created_ts DESC LIMIT 75",
            (guild.id,),
        )
        for row in rows:
            data = self._safe_json_loads(row["data_json"], {})
            if not isinstance(data, dict):
                data = {}
            validation = self._safe_json_loads(data.get("level_validation_json"), {})
            try:
                expires_ts = int(validation.get("expires_ts") or 0) if isinstance(validation, dict) else 0
            except Exception:
                expires_ts = 0

            refreshed_validation = False
            level_id = str(data.get("level_id") or row["level_id"] or "").strip()
            if self._level_validation_enabled() and level_id and (not validation or expires_ts <= now_ts):
                try:
                    validation = await self._lookup_level_validation(level_id, force=True)
                    data = self._apply_level_validation_vars(data, validation)
                    data_json = json.dumps(data, separators=(",", ":"))
                    await self.bot.db.execute(
                        "UPDATE level_request_submissions SET data_json=? WHERE guild_id=? AND wave_id=? AND user_id=? AND status='pending'",
                        (data_json, guild.id, int(row["wave_id"]), int(row["user_id"])),
                    )
                    result["stale_validations_refreshed"] += 1
                    refreshed_validation = True
                except Exception as e:
                    result["errors"].append(f"validation {level_id}: {type(e).__name__}")
                    await log_error(self.bot, f"Request repair could not refresh validation for {level_id}: {repr(e)}")

            embed = self._embed_from_template(
                self._cfg("level_requested_embed", default={}) or {},
                self._data_vars(row, data),
                default_color=self._color_name("pending", "blurple"),
            )
            msg = None
            message_id = int(row["request_message_id"] or 0)
            if message_id:
                try:
                    msg = await target_channel.fetch_message(message_id)
                except Exception:
                    msg = None

            try:
                if msg is None:
                    new_msg = await target_channel.send(embed=embed, view=LevelRequestReviewView())
                    await self.bot.db.execute(
                        "UPDATE level_request_submissions SET request_message_id=? WHERE guild_id=? AND wave_id=? AND user_id=?",
                        (new_msg.id, guild.id, int(row["wave_id"]), int(row["user_id"])),
                    )
                    result["pending_messages_recreated"] += 1
                elif refreshed_validation:
                    await msg.edit(embed=embed, view=LevelRequestReviewView())
                    result["pending_messages_refreshed"] += 1
            except Exception as e:
                result["errors"].append(f"pending message {message_id or 'new'}: {type(e).__name__}")
                await log_error(self.bot, f"Request repair could not refresh pending request {message_id}: {repr(e)}")

        reviewed_rows = await self.bot.db.fetchall(
            "SELECT * FROM level_request_submissions WHERE guild_id=? AND status='reviewed' AND request_message_id IS NOT NULL ORDER BY reviewed_ts DESC LIMIT 75",
            (guild.id,),
        )
        for row in reviewed_rows:
            try:
                msg = await target_channel.fetch_message(int(row["request_message_id"]))
                data = self._safe_json_loads(row["data_json"], {})
                embed = self._embed_from_template(
                    self._cfg("level_reviewed_embed", default={}) or {},
                    self._data_vars(
                        row,
                        data if isinstance(data, dict) else {},
                        result_key=str(row["result"] or ""),
                        review=str(row["review_text"] or ""),
                        reviewer_id=int(row["reviewed_by"] or 0),
                    ),
                    default_color=self._color_name(str(row["result"] or "rejected"), "red"),
                )
                await msg.edit(embed=embed, view=LevelRequestReviewView(disabled=True))
                result["reviewed_messages_locked"] += 1
            except Exception:
                continue

        try:
            stale_cutoff = now_ts - max(self._level_validation_cache_seconds() * 4, 3600)
            await self.bot.db.execute("DELETE FROM gd_level_validation_cache WHERE expires_ts<?", (stale_cutoff,))
            result["validation_cache_pruned"] = True
        except Exception as e:
            result["errors"].append(f"validation cache: {type(e).__name__}")
            await log_error(self.bot, f"Request repair could not prune validation cache: {repr(e)}")

        return result


def setup(bot: discord.Bot):
    bot.add_cog(RequestLevelsCog(bot))
