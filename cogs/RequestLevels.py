import asyncio
import json
import time as time_module
from typing import Any, Dict, Optional

import discord
from discord.ext import commands

from utils.checks import basic_color, is_admin_or_owner, is_mod, member_has_any_role
from utils.errors import log_error
from utils.views import LevelRequestButtonView, LevelRequestReviewView


STATE_OPEN = "open"
STATE_CLOSED = "closed"

OTHER_REASONS = {
    "level_doesnt_exist": "Level doesn't exist",
    "stolen_level": "Stolen level",
    "already_rated": "Already rated",
}


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


class LevelRequestModal(discord.ui.Modal):
    def __init__(self, cog, user_id: int):
        super().__init__(title="Request your level")
        self.cog = cog
        self.user_id = user_id

        self.level_id = discord.ui.InputText(label="Level ID", required=True, max_length=100)
        self.level_name = discord.ui.InputText(label="Level name", required=True, max_length=150)
        self.creators = discord.ui.InputText(label="Creator(s)", required=True, max_length=200)
        self.showcase = discord.ui.InputText(
            label="Level showcase",
            required=False,
            style=discord.InputTextStyle.long,
            max_length=1000,
            placeholder="Optional, but demons and platformers need a showcase.",
        )
        self.notes = discord.ui.InputText(
            label="Notes",
            required=False,
            style=discord.InputTextStyle.long,
            max_length=1000,
        )

        self.add_item(self.level_id)
        self.add_item(self.level_name)
        self.add_item(self.creators)
        self.add_item(self.showcase)
        self.add_item(self.notes)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This form is not for you.", ephemeral=True)

        await self.cog.handle_request_form(
            interaction,
            {
                "level_id": str(self.level_id.value or "").strip(),
                "level_name": str(self.level_name.value or "").strip(),
                "creators": str(self.creators.value or "").strip(),
                "level_showcase": str(self.showcase.value or "").strip(),
                "notes": str(self.notes.value or "").strip(),
            },
        )


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

        will = discord.ui.Button(label="I will", style=discord.ButtonStyle.danger)
        wont = discord.ui.Button(label="I won't", style=discord.ButtonStyle.success)
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


class RequestLevelsCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.allowed_guild_id = bot.config.get_int("guild", "allowed_guild_id") or 0
        self._started = False
        self._close_task: Optional[asyncio.Task] = None
        self._submit_lock = asyncio.Lock()
        self._review_lock = asyncio.Lock()

        guild_ids = [self.allowed_guild_id] if self.allowed_guild_id else None

        @bot.slash_command(name="refresh-request-button", description="Refresh or recreate the request button embed.", guild_ids=guild_ids)
        async def refresh_request_button(ctx: discord.ApplicationContext):
            await self.refresh_request_button(ctx)

        @bot.slash_command(name="open-requests", description="Open level requests.", guild_ids=guild_ids)
        async def open_requests(ctx: discord.ApplicationContext, number: int = 0, time: int = 0):
            await self.open_requests(ctx, number, time)

        @bot.slash_command(name="close-requests", description="Close level requests.", guild_ids=guild_ids)
        async def close_requests(ctx: discord.ApplicationContext):
            await self.close_requests(ctx)

        @bot.slash_command(name="requests-are", description="Check whether level requests are open.", guild_ids=guild_ids)
        async def requests_are(ctx: discord.ApplicationContext):
            await self.requests_are(ctx)

    async def start_background(self):
        if self._started:
            return
        self._started = True
        await self.bot.db.connect()
        self._close_task = asyncio.create_task(self._auto_close_loop())

    def on_config_reload(self) -> None:
        pass

    def _cfg(self, *path: str, default: Any = None) -> Any:
        return self.bot.config.get("level_requests", *path, default=default)

    def _cfg_int(self, *path: str, default: int = 0) -> int:
        return self.bot.config.get_int("level_requests", *path, default=default)

    def _cfg_int_list(self, *path: str) -> list[int]:
        return self.bot.config.get_int_list("level_requests", *path)

    def _message(self, key: str, default: str) -> str:
        return str(self._cfg("messages", key, default=default) or default)

    def _request_button_label(self) -> str:
        return str(self._cfg("request_button_label", default="Request your level!") or "Request your level!")

    def _color_name(self, key: str, default: str = "blurple") -> str:
        return str(self._cfg("colors", key, default=default) or default)

    def _format(self, text: Any, variables: Dict[str, Any]) -> str:
        try:
            return str(text or "").format_map(_SafeDict({k: str(v) for k, v in variables.items()}))
        except Exception:
            return str(text or "")

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

    def _base_state_vars(self, row) -> Dict[str, Any]:
        if not row:
            return {"state": "Closed", "wave_id": 0, "submitted_count": 0, "request_limit": "", "close_ts": ""}
        close_ts = row["close_ts"]
        return {
            "state": self._state_label(str(row["state"])),
            "wave_id": int(row["wave_id"]),
            "submitted_count": int(row["submitted_count"]),
            "request_limit": "" if row["request_limit"] is None else int(row["request_limit"]),
            "close_ts": "" if close_ts is None else int(close_ts),
        }

    def _data_vars(self, row, data: Dict[str, Any], result_key: str = "", review: str = "", reviewer_id: int = 0) -> Dict[str, Any]:
        level_showcase = str(data.get("level_showcase") or "").strip() or "Not provided"
        notes = str(data.get("notes") or "").strip() or "No notes provided"
        result_label = self._result_label(result_key)
        result_color = self._color_name(result_key, self._color_name("pending", "blurple"))
        variables = {
            **data,
            "level_id": data.get("level_id", ""),
            "level_name": data.get("level_name", ""),
            "creators": data.get("creators", ""),
            "level_showcase": level_showcase,
            "showcase": level_showcase,
            "notes": notes,
            "requester_id": int(row["user_id"]),
            "requester_mention": f"<@{int(row['user_id'])}>",
            "wave_id": int(row["wave_id"]),
            "result": result_label,
            "result_key": result_key,
            "review": review or "No review provided.",
            "reviewer_id": reviewer_id or "",
            "reviewer_mention": f"<@{reviewer_id}>" if reviewer_id else "Unknown",
            "pending_color": self._color_name("pending", "blurple"),
            "result_color": result_color,
        }
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
        if row and str(row["state"]) == STATE_CLOSED:
            await self.refresh_or_create_request_button(guild)
            return
        await self.bot.db.execute(
            "UPDATE level_request_state SET state=?, close_ts=NULL, closed_ts=? WHERE guild_id=?",
            (STATE_CLOSED, int(time_module.time()), guild.id),
        )
        await self.refresh_or_create_request_button(guild)

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

    def _in_allowed_guild(self, ctx: discord.ApplicationContext) -> bool:
        return ctx.guild is not None and ctx.guild.id == self.allowed_guild_id

    def _is_admin(self, member: discord.Member) -> bool:
        return is_admin_or_owner(member, self.bot.config.get_int_list("roles", "admin_owner_role_ids"))

    def _is_mod(self, member: discord.Member) -> bool:
        return is_mod(member, self.bot.config.get_int("roles", "MOD_ROLE_ID") or 0)

    async def _configured_channel(self, guild: discord.Guild, key: str) -> Optional[discord.TextChannel]:
        channel_id = self._cfg_int(key)
        channel = guild.get_channel(channel_id) if channel_id else None
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
        member = ctx.guild.get_member(ctx.user.id)
        if member is None or not self._is_mod(member):
            return await ctx.respond("Only mods can use this.", ephemeral=True)

        msg = await self.refresh_or_create_request_button(ctx.guild)
        if msg is None:
            return await ctx.respond(self._message("not_configured", "The request system is not fully configured yet."), ephemeral=True)
        await ctx.respond(f"Request button refreshed: {msg.jump_url}", ephemeral=True)

    async def open_requests(self, ctx: discord.ApplicationContext, number: int = 0, time: int = 0):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        member = ctx.guild.get_member(ctx.user.id)
        if member is None or not self._is_admin(member):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

        current = await self._get_state(ctx.guild.id)
        wave_id = int(current["wave_id"]) + 1
        request_limit = int(number) if number and int(number) > 0 else None
        close_ts = int(time_module.time()) + int(time) * 60 if time and int(time) > 0 else None

        await self.bot.db.execute(
            "UPDATE level_request_state SET state=?, wave_id=?, request_limit=?, close_ts=?, submitted_count=0, opened_ts=?, closed_ts=NULL WHERE guild_id=?",
            (STATE_OPEN, wave_id, request_limit, close_ts, int(time_module.time()), ctx.guild.id),
        )
        await self.refresh_or_create_request_button(ctx.guild)

        details = [f"Wave **{wave_id}** opened."]
        if request_limit:
            details.append(f"Limit: **{request_limit}** successful requests.")
        if close_ts:
            details.append(f"Closes at <t:{close_ts}:R>.")
        if not request_limit and not close_ts:
            details.append("No limit or timer set.")
        await ctx.respond(" ".join(details), ephemeral=True)

    async def close_requests(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        member = ctx.guild.get_member(ctx.user.id)
        if member is None or not self._is_admin(member):
            return await ctx.respond("You don't have permission to use this.", ephemeral=True)

        row = await self._get_state(ctx.guild.id)
        if str(row["state"]) == STATE_CLOSED:
            await self.refresh_or_create_request_button(ctx.guild)
            return await ctx.respond("Requests are already closed.", ephemeral=True)

        await self._set_state_closed(ctx.guild, reason=f"manual by {ctx.user.id}")
        await ctx.respond("Requests closed.", ephemeral=True)

    async def requests_are(self, ctx: discord.ApplicationContext):
        if not self._in_allowed_guild(ctx):
            return await ctx.respond("Wrong server.", ephemeral=True)
        row = await self._get_state(ctx.guild.id)
        state_label = self._state_label(str(row["state"]))
        parts = [f"Requests are **{state_label}**.", f"Wave: **{int(row['wave_id'])}**.", f"Submitted: **{int(row['submitted_count'])}**."]
        if row["request_limit"] is not None:
            parts.append(f"Limit: **{int(row['request_limit'])}**.")
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
        if str(row["state"]) != STATE_OPEN:
            return await interaction.response.send_message(self._message("closed", "Requests are closed :/"), ephemeral=True)
        if row["close_ts"] is not None and int(row["close_ts"]) <= int(time_module.time()):
            await interaction.response.send_message(self._message("closed", "Requests are closed :/"), ephemeral=True)
            try:
                await self._set_state_closed(interaction.guild, reason="time limit")
            except Exception as e:
                await log_error(self.bot, f"Could not refresh request button after timed close: {repr(e)}")
            return

        member = interaction.guild.get_member(interaction.user.id)
        if member is None or not await self._requirements_ok(member):
            return await interaction.response.send_message(
                self._message("no_requirements", "You don't meet the requirements, please read the requesting rules"),
                ephemeral=True,
            )

        existing_user = await self.bot.db.fetchone(
            "SELECT 1 FROM level_request_submissions WHERE guild_id=? AND wave_id=? AND user_id=?",
            (interaction.guild.id, int(row["wave_id"]), interaction.user.id),
        )
        if existing_user:
            return await interaction.response.send_message(
                self._message("already_submitted", "You already submitted a level during this request wave."),
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

    async def handle_first_choice(self, interaction: discord.Interaction, will_request_again: bool):
        if interaction.guild is None:
            return await interaction.response.send_message("Wrong server.", ephemeral=True)
        member = interaction.guild.get_member(interaction.user.id)
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

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        async with self._submit_lock:
            row = await self._get_state(interaction.guild.id)
            if str(row["state"]) != STATE_OPEN:
                return await self._reply_ephemeral(interaction, self._message("closed", "Requests are closed :/"))

            wave_id = int(row["wave_id"])
            user_id = interaction.user.id
            normalized_level_id = str(data["level_id"]).strip().casefold()

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
            data_json = json.dumps(data, separators=(",", ":"))

            await self.bot.db.execute(
                "INSERT INTO level_request_submissions(guild_id,wave_id,user_id,level_id,status,created_ts,data_json) VALUES(?,?,?,?,?,?,?)",
                (interaction.guild.id, wave_id, user_id, normalized_level_id, "pending", int(time_module.time()), data_json),
            )

            try:
                temp_row = {"guild_id": interaction.guild.id, "wave_id": wave_id, "user_id": user_id}
                embed = self._embed_from_template(
                    self._cfg("level_requested_embed", default={}) or {},
                    self._data_vars(temp_row, data),
                    default_color=self._color_name("pending", "blurple"),
                )
                msg = await target_channel.send(embed=embed, view=LevelRequestReviewView())
            except Exception as e:
                await self.bot.db.execute(
                    "DELETE FROM level_request_submissions WHERE guild_id=? AND wave_id=? AND user_id=?",
                    (interaction.guild.id, wave_id, user_id),
                )
                await log_error(self.bot, f"Could not send level request embed: {repr(e)}")
                return await self._reply_ephemeral(interaction, "I couldn't submit your request right now.")

            new_count = int(row["submitted_count"]) + 1
            await self.bot.db.execute(
                "UPDATE level_request_submissions SET request_message_id=? WHERE guild_id=? AND wave_id=? AND user_id=?",
                (msg.id, interaction.guild.id, wave_id, user_id),
            )
            await self.bot.db.execute(
                "UPDATE level_request_state SET submitted_count=? WHERE guild_id=?",
                (new_count, interaction.guild.id),
            )

            limit_reached = row["request_limit"] is not None and new_count >= int(row["request_limit"])

        if limit_reached:
            try:
                await self._set_state_closed(interaction.guild, reason="request limit")
            except Exception as e:
                await log_error(self.bot, f"Could not refresh request button after limit close: {repr(e)}")

        await self._reply_ephemeral(interaction, self._message("success", "Your request has been submitted!"))

    async def _submission_by_message(self, guild_id: int, message_id: int):
        return await self.bot.db.fetchone(
            "SELECT * FROM level_request_submissions WHERE guild_id=? AND request_message_id=?",
            (guild_id, message_id),
        )

    async def handle_review_button(self, interaction: discord.Interaction, action: str):
        if interaction.guild is None or interaction.message is None:
            return await interaction.response.send_message("Request not found.", ephemeral=True)
        row = await self._submission_by_message(interaction.guild.id, interaction.message.id)
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
        await self._finalize_review(interaction, message_id, reason_key, "")

    async def _finalize_review(self, interaction: discord.Interaction, message_id: int, result_key: str, review: str):
        if interaction.guild is None:
            return await self._reply_ephemeral(interaction, "Wrong server.")

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        async with self._review_lock:
            row = await self._submission_by_message(interaction.guild.id, message_id)
            if not row:
                return await self._reply_ephemeral(interaction, "Request not found.")
            if str(row["status"]) != "pending":
                return await self._reply_ephemeral(interaction, "This request has already been reviewed.")

            data = json.loads(row["data_json"] or "{}")
            reviewer_id = interaction.user.id
            result_label = self._result_label(result_key)
            variables = self._data_vars(row, data, result_key=result_key, review=review, reviewer_id=reviewer_id)

            await self.bot.db.execute(
                "UPDATE level_request_submissions SET status='reviewed', result=?, review_text=?, reviewed_by=?, reviewed_ts=? WHERE guild_id=? AND request_message_id=?",
                (result_key, review, reviewer_id, int(time_module.time()), interaction.guild.id, message_id),
            )

            request_channel = await self._configured_channel(interaction.guild, "level_requested")
            if request_channel is not None:
                try:
                    msg = await request_channel.fetch_message(message_id)
                    final_embed = self._embed_from_template(
                        self._cfg("level_reviewed_embed", default={}) or {},
                        variables,
                        default_color=self._color_name(result_key, "red"),
                    )
                    await msg.edit(embed=final_embed, view=LevelRequestReviewView(disabled=True))
                except Exception as e:
                    await log_error(self.bot, f"Could not edit reviewed level request {message_id}: {repr(e)}")

            result_channel_id = self._status_channel_id(result_key)
            result_channel = interaction.guild.get_channel(result_channel_id) if result_channel_id else None
            if isinstance(result_channel, discord.TextChannel):
                try:
                    result_embed = self._embed_from_template(
                        self._cfg(self._result_template_key(result_key), default={}) or {},
                        variables,
                        default_color=self._color_name(result_key, "red"),
                    )
                    await result_channel.send(content=f"<@{int(row['user_id'])}>", embed=result_embed)
                except Exception as e:
                    await log_error(self.bot, f"Could not send level request result {message_id}: {repr(e)}")

        await self._reply_ephemeral(interaction, f"Request marked as {result_label}.")


def setup(bot: discord.Bot):
    bot.add_cog(RequestLevelsCog(bot))
