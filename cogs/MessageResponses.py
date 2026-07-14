from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List

import discord
from discord.ext import commands

from utils.checks import ensure_allowed_guild_id, basic_color
from utils.errors import log_error
from utils.mentions import no_mentions


class MessageResponsesCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self._rules: List[Dict[str, Any]] = []
        self._cooldown: Dict[int, float] = {}
        self._last_rule_error_log: Dict[str, float] = {}
        self._load_error = ""
        self.load_rules()

    def load_rules(self) -> None:
        cfg = self.bot.config
        path = cfg.get_str("responses", "rules_path", default="responses.json")
        p = Path(path)
        if not p.exists():
            self._rules = []
            self._load_error = f"{path} does not exist"
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                # strip pseudo-comments at rule-level
                self._rules = [{k: v for k, v in r.items() if not str(k).startswith("_")} for r in data if isinstance(r, dict)]
                self._load_error = ""
            else:
                self._rules = []
                self._load_error = f"{path} must contain a list of rules"
        except Exception as e:
            self._rules = []
            self._load_error = f"{path} could not be parsed: {type(e).__name__}"

    def on_config_reload(self) -> None:
        self.load_rules()
        self._cooldown.clear()

    def _max_response_chars(self) -> int:
        try:
            return max(100, min(2000, int(self.bot.config.get("responses", "max_response_chars", default=1600) or 1600)))
        except Exception:
            return 1600

    async def _log_rule_error(self, key: str, message: str) -> None:
        now = time.monotonic()
        if now - self._last_rule_error_log.get(key, 0.0) < 300:
            return
        self._last_rule_error_log[key] = now
        await log_error(self.bot, message)

    def validate_rules(self) -> list[str]:
        issues: list[str] = []
        if self._load_error:
            issues.append(self._load_error)
        for idx, rule in enumerate(self._rules, start=1):
            trigger = str(rule.get("Content", "") or "").strip()
            if not trigger:
                issues.append(f"Rule #{idx}: missing trigger content")
            if bool(rule.get("Embed", False)):
                embed_text = rule.get("Embed_text", {})
                if not isinstance(embed_text, dict):
                    issues.append(f"Rule #{idx}: Embed_text must be an object")
                elif not str(embed_text.get("title", "") or "").strip() and not str(
                    embed_text.get("description", "") or ""
                ).strip():
                    issues.append(f"Rule #{idx}: Embed enabled but title and description are empty")
            if bool(rule.get("Message", False)) and not str(rule.get("Message_text", "") or "").strip():
                issues.append(f"Rule #{idx}: Message enabled but Message_text is empty")
            if not bool(rule.get("Embed", False)) and not bool(rule.get("Message", False)):
                issues.append(f"Rule #{idx}: neither Embed nor Message output is enabled")
            channels = rule.get("Channels", [])
            if channels and not isinstance(channels, list):
                issues.append(f"Rule #{idx}: Channels must be a list")
        return issues

    def _cooldown_ok(self, user_id: int) -> bool:
        cfg = self.bot.config
        try:
            cd = max(0, min(3600, int(cfg.get("responses", "cooldown_seconds", default=15) or 15)))
        except (TypeError, ValueError):
            cd = 15
        now = time.monotonic()
        if len(self._cooldown) > 5000:
            cutoff = now - max(cd, 60) * 2
            self._cooldown = {uid: seen for uid, seen in self._cooldown.items() if seen >= cutoff}
        last = self._cooldown.get(user_id, 0.0)
        if now - last < cd:
            return False
        self._cooldown[user_id] = now
        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return

        cfg = self.bot.config
        allowed_guild_id = cfg.get_int("guild", "allowed_guild_id")
        if not ensure_allowed_guild_id(message.guild, allowed_guild_id):
            return

        review_access_channel_id = cfg.get_int("channels", "review_access_channel_id")
        if review_access_channel_id and message.channel.id == review_access_channel_id:
            return

        if not self._rules:
            return

        content = (message.content or "").strip()
        lowered = content.casefold()
        first_match_only = bool(cfg.get("responses", "first_match_only", default=True))

        for rule in self._rules:
            try:
                trigger = str(rule.get("Content", "")).strip()
                if not trigger:
                    continue
                whole = bool(rule.get("Whole_message", False))
                trigger_l = trigger.strip().casefold()

                match = (lowered == trigger_l) if whole else (trigger_l in lowered)
                if not match:
                    continue

                # channel filter
                chans = rule.get("Channels", [])
                if isinstance(chans, list) and chans:
                    chan_ids = set()
                    has_non_empty_filter = False
                    for raw_channel_id in chans:
                        try:
                            text = str(raw_channel_id).strip()
                            if text:
                                has_non_empty_filter = True
                                chan_ids.add(int(text))
                        except Exception:
                            continue
                    if has_non_empty_filter and (not chan_ids or message.channel.id not in chan_ids):
                        continue

                respond = bool(rule.get("Respond", False))
                use_embed = bool(rule.get("Embed", False))
                use_msg = bool(rule.get("Message", False))
                sent_response = False

                if use_embed:
                    et = rule.get("Embed_text", {}) or {}
                    title = str(et.get("title", "") or "")[:256]
                    desc = str(et.get("description", "") or "")[:4096]
                    if not title and not desc:
                        await self._log_rule_error(trigger[:80], f"Message response rule {trigger!r} has an empty embed")
                        continue
                    if not self._cooldown_ok(message.author.id):
                        return
                    color = basic_color(str(et.get("color", "") or ""))
                    embed = discord.Embed(title=title or None, description=desc or None, color=color)
                    if respond:
                        await message.reply(embed=embed, mention_author=False, allowed_mentions=no_mentions())
                    else:
                        await message.channel.send(embed=embed, allowed_mentions=no_mentions())
                    sent_response = True
                elif use_msg:
                    text = str(rule.get("Message_text", "") or "")[: self._max_response_chars()]
                    if not text:
                        await self._log_rule_error(trigger[:80], f"Message response rule {trigger!r} has empty text")
                        continue
                    if not self._cooldown_ok(message.author.id):
                        return
                    if respond:
                        await message.reply(text, mention_author=False, allowed_mentions=no_mentions())
                    else:
                        await message.channel.send(text, allowed_mentions=no_mentions())
                    sent_response = True

                if sent_response and first_match_only:
                    break
            except Exception as e:
                await self._log_rule_error(str(rule.get("Content", ""))[:80], f"Message response rule failed: {repr(e)}")
                continue


def setup(bot: discord.Bot):
    bot.add_cog(MessageResponsesCog(bot))
