from __future__ import annotations

import time

import discord
from discord.ext import commands

from utils.checks import ensure_allowed_guild_id, member_has_any_role
from utils.errors import log_error


def _review_access_text(value: str) -> str:
    cleaned = " ".join(str(value or "").casefold().strip().split())
    if cleaned.endswith((".", ",", "!", "?")):
        cleaned = cleaned[:-1].rstrip()
    return cleaned


def _within_one_edit(actual: str, expected: str) -> bool:
    if actual == expected:
        return True
    if abs(len(actual) - len(expected)) > 1:
        return False

    if len(actual) == len(expected):
        differences = sum(1 for a, b in zip(actual, expected) if a != b)
        return differences <= 1

    shorter, longer = (actual, expected) if len(actual) < len(expected) else (expected, actual)
    i = j = edits = 0
    while i < len(shorter) and j < len(longer):
        if shorter[i] == longer[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        j += 1
    return True


class ModCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self._recent_role_dms: dict[tuple[int, int], float] = {}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        cfg = self.bot.config
        allowed_guild_id = cfg.get_int("guild", "allowed_guild_id")
        if message.guild is None or not ensure_allowed_guild_id(message.guild, allowed_guild_id):
            return

        if await self._handle_review_access_message(message):
            return

        target_channel_id = cfg.get_int("channels", "autodelete_channel_id")
        if not target_channel_id:
            return
        if message.channel.id != target_channel_id:
            return

        whitelist_roles = cfg.get_int_list("roles", "whitelisted_deletion_ID_roles")
        restriction_role_id = cfg.get_int("roles", "restriction_role_ID")
        if not restriction_role_id:
            return

        member = message.guild.get_member(message.author.id)
        if member is None:
            return

        if member_has_any_role(member, whitelist_roles):
            return

        # delete the message and apply restriction role
        try:
            await message.delete()
        except Exception:
            pass

        try:
            role = message.guild.get_role(restriction_role_id)
            if role and role not in member.roles:
                await member.add_roles(role, reason="Autodeletion restriction")
        except Exception:
            pass

    async def _handle_review_access_message(self, message: discord.Message) -> bool:
        cfg = self.bot.config
        channel_id = cfg.get_int("channels", "review_access_channel_id")
        if not channel_id or message.channel.id != channel_id:
            return False

        member = message.guild.get_member(message.author.id) if message.guild else None
        if member is None:
            return True

        whitelist_roles = set(cfg.get_int_list("roles", "admin_owner_role_ids"))
        if member.guild_permissions.administrator or member_has_any_role(member, list(whitelist_roles)):
            return True

        expected = str(
            cfg.get(
                "review_access",
                "agreement_phrase",
                default="I have read and understood the review access conditions",
            )
            or "I have read and understood the review access conditions"
        )
        content = str(message.content or "").strip()
        if not _within_one_edit(_review_access_text(content), _review_access_text(expected)):
            try:
                await message.delete()
            except Exception as e:
                await log_error(
                    self.bot,
                    f"Review access message delete failed in channel_id={message.channel.id} "
                    f"message_id={message.id} user_id={message.author.id}: {repr(e)}",
                )
            return True

        role_id = cfg.get_int("roles", "review_access_role_id")
        role = message.guild.get_role(role_id) if role_id else None
        if role is None:
            await log_error(self.bot, f"Review access role is missing or invalid: role_id={role_id}")
            return True
        if role is not None and role not in member.roles:
            try:
                await member.add_roles(role, reason="Review access agreement")
            except Exception as e:
                await log_error(
                    self.bot,
                    f"Review access role grant failed for user_id={member.id} role_id={role.id}: {repr(e)}",
                )
                return True
            await self._send_role_dm(member, role, source="review_access")
        return True

    def _dm_templates_for_role(self, role_id: int) -> list[str]:
        cfg = self.bot.config
        templates: list[str] = []
        entries = cfg.get("autoDM", "entries", default=None)
        if isinstance(entries, list):
            for ent in entries:
                if not isinstance(ent, dict):
                    continue
                try:
                    entry_role_id = int(ent.get("role_id"))
                except Exception:
                    continue
                if entry_role_id != int(role_id):
                    continue
                msg = str(ent.get("message", "") or "").strip()
                if msg:
                    templates.append(msg)

        legacy_role_id = cfg.get_int("roles", "autoDM_watched_role_id")
        if legacy_role_id and int(legacy_role_id) == int(role_id):
            legacy_msg = cfg.get_str("autoDM", "message", default="")
            if legacy_msg:
                templates.append(legacy_msg)
        return templates

    async def _send_role_dm(self, member: discord.Member, role: discord.Role, *, source: str) -> None:
        now = time.monotonic()
        key = (member.id, role.id)
        if now - self._recent_role_dms.get(key, 0) < 30:
            return
        for msg_template in self._dm_templates_for_role(role.id):
            txt = (
                str(msg_template)
                .replace("{user}", member.mention)
                .replace("{role}", role.name)
                .replace("{guild}", member.guild.name)
            )
            try:
                await member.send(txt)
                self._recent_role_dms[key] = now
            except Exception as e:
                await log_error(
                    self.bot,
                    f"Role DM failed for source={source} user_id={member.id} role_id={role.id}: {repr(e)}",
                )
            return

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None:
            return
        cfg = self.bot.config
        allowed_guild_id = cfg.get_int("guild", "allowed_guild_id")
        if payload.guild_id != allowed_guild_id:
            return

        target_channel_id = cfg.get_int("channels", "autodelete_channel_id")
        if not target_channel_id:
            return
        if payload.channel_id != target_channel_id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        member = payload.member
        if member is None or member.bot:
            return

        whitelist_roles = cfg.get_int_list("roles", "whitelisted_deletion_ID_roles")
        restriction_role_id = cfg.get_int("roles", "restriction_role_ID")
        if not restriction_role_id:
            return

        if member_has_any_role(member, whitelist_roles):
            return

        # remove reaction and apply restriction role
        try:
            channel = guild.get_channel(payload.channel_id)
            if isinstance(channel, discord.TextChannel):
                msg = await channel.fetch_message(payload.message_id)
                await msg.remove_reaction(payload.emoji, member)
        except Exception:
            pass

        try:
            role = guild.get_role(restriction_role_id)
            if role and role not in member.roles:
                await member.add_roles(role, reason="Autodeletion reaction restriction")
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        cfg = self.bot.config
        allowed_guild_id = cfg.get_int("guild", "allowed_guild_id")
        if after.guild.id != allowed_guild_id:
            return        # DM-on-role supports one legacy role or multiple entries.
        rules = []
        entries = cfg.get("autoDM", "entries", default=None)
        if isinstance(entries, list):
            for ent in entries:
                if not isinstance(ent, dict):
                    continue
                rid = ent.get("role_id")
                try:
                    rid_int = int(rid)
                except Exception:
                    continue
                msg = str(ent.get("message", "") or "")
                if msg:
                    rules.append((rid_int, msg))

        legacy_role_id = cfg.get_int("roles", "autoDM_watched_role_id")
        if legacy_role_id:
            legacy_msg = cfg.get_str("autoDM", "message", default="Hello {user}!")
            rules.append((legacy_role_id, legacy_msg))

        # no rules configured
        if not rules:
            return

        before_ids = {r.id for r in before.roles}
        after_ids = {r.id for r in after.roles}

        for role_id, msg_template in rules:
            if role_id in after_ids and role_id not in before_ids:
                role = after.guild.get_role(role_id)
                if role is not None:
                    await self._send_role_dm(after, role, source="member_update")
                elif msg_template:
                    try:
                        await after.send(
                            str(msg_template)
                            .replace("{user}", after.mention)
                            .replace("{role}", str(role_id))
                            .replace("{guild}", after.guild.name)
                        )
                    except Exception as e:
                        await log_error(self.bot, f"Role DM failed for missing role_id={role_id} user_id={after.id}: {repr(e)}")

def setup(bot: discord.Bot):
    bot.add_cog(ModCog(bot))
