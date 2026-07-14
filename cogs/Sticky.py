from __future__ import annotations

import asyncio
import re
from typing import Dict, Optional, Any, List

import discord
from discord.ext import commands

from utils.checks import ensure_allowed_guild_id, basic_color
from utils.errors import log_error
from utils.mentions import no_mentions


class StickyCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self._debounce_tasks: Dict[int, asyncio.Task] = {}
        self._sticky_channel_locks: Dict[int, asyncio.Lock] = {}
        self._background_tasks: set[asyncio.Task] = set()
        self._sticky_recovery_scanned: set[int] = set()
        self._sticky_entries: List[Dict[str, Any]] = []

        # forum_channel_id -> templates dict (keys: "default" and tag_id strings)
        self._forum_rules: Dict[int, Dict[str, Dict[str, Any]]] = {}
        self._forum_required_rules: Dict[int, Dict[str, Any]] = {}

        # Thread IDs we've already handled this runtime
        self._forum_sent_threads: set[int] = set()
        self._forum_required_checked_threads: set[int] = set()

        # Per-thread locks so only one send attempt runs at a time for a thread.
        self._forum_thread_locks: Dict[int, asyncio.Lock] = {}

        self.reload_from_config()

    def cog_unload(self) -> None:
        for task in (*self._debounce_tasks.values(), *self._background_tasks):
            if not task.done():
                task.cancel()
        self._debounce_tasks.clear()
        self._background_tasks.clear()

    def _start_background_task(self, coroutine, *, label: str) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)

        def _done(completed: asyncio.Task) -> None:
            self._background_tasks.discard(completed)
            if completed.cancelled():
                return
            try:
                error = completed.exception()
            except Exception:
                return
            if error is not None:
                log_task = asyncio.create_task(log_error(self.bot, f"{label} failed: {repr(error)}"))
                self._background_tasks.add(log_task)
                log_task.add_done_callback(self._background_tasks.discard)

        task.add_done_callback(_done)
        return task

    def reload_from_config(self) -> None:
        cfg = self.bot.config
        self._sticky_entries = cfg.get("sticky", "entries", default=[]) or []

        # Forum first-message supports either a single config (legacy) or multiple entries.
        self._forum_rules = {}
        self._forum_required_rules = {}
        global_required_rule = self._required_rule_from_config(cfg.get("forum_first_message", default={}) or {})
        entries = cfg.get("forum_first_message", "entries", default=None)
        if isinstance(entries, list) and entries:
            for ent in entries:
                if not isinstance(ent, dict):
                    continue
                ch = ent.get("forum_channel_id")
                try:
                    ch_id = int(ch)
                except Exception:
                    continue
                templates = ent.get("templates", {}) or {}
                if isinstance(templates, dict):
                    self._forum_rules[ch_id] = templates
                    required_rule = self._required_rule_from_config(ent, fallback=global_required_rule)
                    if required_rule:
                        self._forum_required_rules[ch_id] = required_rule
        else:
            ch_id = cfg.get_int("forum_first_message", "forum_channel_id")
            templates = cfg.get("forum_first_message", "templates", default={}) or {}
            if ch_id and isinstance(templates, dict):
                self._forum_rules[int(ch_id)] = templates
                required_rule = global_required_rule
                if required_rule:
                    self._forum_required_rules[int(ch_id)] = required_rule

    def on_config_reload(self) -> None:
        self.reload_from_config()

    def _required_rule_from_config(self, source: Dict[str, Any], fallback: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        fallback = fallback or {}
        word = str(source.get("required_word", fallback.get("word", "")) or "").strip()[:256]
        if not word:
            return None

        dm_message = str(
            source.get("missing_required_word_dm")
            or source.get("required_word_dm_message")
            or fallback.get("dm_message")
            or "Your thread was removed because it did not include the required word: {required_word}."
        )
        try:
            delay = float(source.get("required_word_delete_delay_seconds", fallback.get("delete_delay_seconds", 10)) or 10)
        except Exception:
            delay = 10.0
        match_mode = str(
            source.get("required_word_match_mode")
            or fallback.get("match_mode")
            or fallback.get("required_word_match_mode")
            or "contains"
        ).strip().casefold()
        if match_mode not in {"contains", "whole_word", "regex"}:
            match_mode = "contains"

        return {
            "word": word,
            "dm_message": dm_message,
            "delete_delay_seconds": max(0.0, min(delay, 3600.0)),
            "match_mode": match_mode,
        }

    def _get_sticky_for_channel(self, channel_id: int) -> Optional[Dict[str, Any]]:
        for e in self._sticky_entries:
            try:
                if int(e.get("channel_id")) == channel_id:
                    return e
            except Exception:
                continue
        return None

    # ---------------------------
    # Sticky message feature
    # ---------------------------
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

        # Forum-first-message fallback:
        # Normal path (on_thread_create) should run first. This fallback:
        # - checks if the bot already posted in the thread (manual check)
        # - if yes, does nothing
        # - if no, sends
        try:
            if isinstance(message.channel, discord.Thread) and message.channel.parent_id in self._forum_rules:
                self._start_background_task(
                    self._forum_first_message_flow(message.channel, prefer_normal=False),
                    label=f"Forum fallback for thread {message.channel.id}",
                )
        except Exception as e:
            await log_error(self.bot, f"Could not schedule forum fallback for channel_id={message.channel.id}: {repr(e)}")

        entry = self._get_sticky_for_channel(message.channel.id)
        if not entry:
            return

        # debounce per channel
        task = self._debounce_tasks.get(message.channel.id)
        if task and not task.done():
            task.cancel()

        try:
            delay = max(0.0, min(300.0, float(entry.get("delay_seconds", 5) or 5)))
        except (TypeError, ValueError):
            delay = 5.0
        sticky_task = asyncio.create_task(
            self._do_sticky(message.channel, message.guild, entry, delay)
        )
        self._debounce_tasks[message.channel.id] = sticky_task

        def _remove(completed: asyncio.Task) -> None:
            if self._debounce_tasks.get(message.channel.id) is completed:
                self._debounce_tasks.pop(message.channel.id, None)

        sticky_task.add_done_callback(_remove)

    async def _do_sticky(self, channel: discord.TextChannel, guild: discord.Guild, entry: Dict[str, Any], delay: float):
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

        text = str(entry.get("message", "") or "")
        if not text:
            return

        # Once replacement begins, let it finish even if another message
        # resets the debounce timer. Cancelling between send and DB save would
        # leave an untracked sticky that the next refresh could duplicate.
        critical = self._start_background_task(
            self._replace_sticky(channel, guild, text),
            label=f"Sticky replacement for channel {channel.id}",
        )
        try:
            await asyncio.shield(critical)
        except asyncio.CancelledError:
            return

    async def _replace_sticky(self, channel: discord.TextChannel, guild: discord.Guild, text: str) -> None:
        lock = self._sticky_channel_locks.setdefault(channel.id, asyncio.Lock())
        async with lock:
            await self._replace_sticky_locked(channel, guild, text)

    async def _replace_sticky_locked(self, channel: discord.TextChannel, guild: discord.Guild, text: str) -> None:

        # delete previous sticky
        db = self.bot.db
        try:
            row = await db.fetchone(
                "SELECT last_sticky_message_id FROM sticky_state WHERE guild_id=? AND channel_id=?",
                (guild.id, channel.id),
            )
        except Exception as e:
            await log_error(self.bot, f"Sticky state lookup failed for channel_id={channel.id}: {repr(e)}")
            return
        last_id = int(row["last_sticky_message_id"]) if row and row["last_sticky_message_id"] else None
        previous_missing = last_id is None
        if last_id:
            try:
                msg = await channel.fetch_message(last_id)
                await msg.delete()
            except discord.NotFound:
                previous_missing = True
            except Exception as e:
                await log_error(self.bot, f"Sticky cleanup could not delete saved sticky message_id={last_id} channel_id={channel.id}: {repr(e)}")
                return

        # Recovery cleanup for deployments whose database state was reset or migrated.
        # Only deletes exact matching bot-authored sticky copies.
        if previous_missing or channel.id not in self._sticky_recovery_scanned:
            self._sticky_recovery_scanned.add(channel.id)
            try:
                me_id = int(getattr(self.bot.user, "id", 0) or 0)
                async for old in channel.history(limit=50):
                    if old.id == last_id:
                        continue
                    if me_id and getattr(old.author, "id", 0) != me_id:
                        continue
                    if (old.content or "") != text:
                        continue
                    try:
                        await old.delete()
                    except discord.NotFound:
                        pass
                    except Exception as e:
                        await log_error(self.bot, f"Sticky cleanup could not delete duplicate sticky message_id={old.id} channel_id={channel.id}: {repr(e)}")
            except Exception as e:
                await log_error(self.bot, f"Sticky cleanup history scan failed for channel_id={channel.id}: {repr(e)}")

        sent = None
        try:
            sent = await channel.send(text, allowed_mentions=no_mentions())
            await db.execute(
                "INSERT INTO sticky_state(guild_id, channel_id, last_sticky_message_id) VALUES(?,?,?) "
                "ON CONFLICT(guild_id, channel_id) DO UPDATE SET last_sticky_message_id=excluded.last_sticky_message_id",
                (guild.id, channel.id, sent.id),
            )
        except Exception as e:
            if sent is not None:
                try:
                    await sent.delete()
                except discord.NotFound:
                    pass
                except Exception as cleanup_error:
                    await log_error(
                        self.bot,
                        f"Untracked sticky cleanup failed message_id={sent.id} channel_id={channel.id}: {repr(cleanup_error)}",
                    )
            await log_error(self.bot, f"Sticky send/state update failed for channel_id={channel.id}: {repr(e)}")

    # ---------------------------
    # Forum first-message feature
    # ---------------------------
    def _get_thread_lock(self, thread_id: int) -> asyncio.Lock:
        self._trim_forum_runtime_state()
        lock = self._forum_thread_locks.get(thread_id)
        if lock is None:
            lock = asyncio.Lock()
            self._forum_thread_locks[thread_id] = lock
        return lock

    def _trim_forum_runtime_state(self) -> None:
        max_items = 5000
        for runtime_set in (self._forum_sent_threads, self._forum_required_checked_threads):
            while len(runtime_set) > max_items:
                runtime_set.pop()
        if len(self._forum_thread_locks) > max_items:
            removable = [thread_id for thread_id, lock in self._forum_thread_locks.items() if not lock.locked()]
            for thread_id in removable[: len(self._forum_thread_locks) - max_items]:
                self._forum_thread_locks.pop(thread_id, None)

    def _forum_template_for_thread(self, thread: discord.Thread) -> Dict[str, Any]:
        templates = self._forum_rules.get(thread.parent_id, {})
        template = templates.get("default", {}) if isinstance(templates, dict) else {}
        try:
            for tag in getattr(thread, "applied_tags", []) or []:
                candidate = templates.get(str(tag.id))
                if isinstance(candidate, dict):
                    return candidate
        except Exception:
            pass
        return template if isinstance(template, dict) else {}

    async def _thread_has_bot_message(self, thread: discord.Thread, limit: int = 25) -> bool:
        """Return whether this thread already contains its configured reminder."""
        me = self.bot.user
        if me is None:
            return False
        template = self._forum_template_for_thread(thread)
        expected_title = str(template.get("title", "") or "")[:256]
        expected_description = str(template.get("description", "") or "")[:4096]
        try:
            async for msg in thread.history(limit=limit, oldest_first=True):
                if not msg.author or msg.author.id != me.id:
                    continue
                for embed in msg.embeds:
                    if (embed.title or "") == expected_title and (embed.description or "") == expected_description:
                        return True
        except Exception:
            # If we can't read history, play safe and avoid double posting.
            return True
        return False

    async def _send_forum_first_message(self, thread: discord.Thread) -> bool:
        """Send the configured first-message embed once. Returns True if sent."""
        if thread.guild is None:
            return False

        if thread.parent_id not in self._forum_rules:
            return False
        template = self._forum_template_for_thread(thread)

        title = str(template.get("title", "") or "")[:256]
        desc = str(template.get("description", "") or "")[:4096]
        color = basic_color(str(template.get("color", "") or "blurple"))
        embed = discord.Embed(title=title or None, description=desc or None, color=color)

        await thread.send(embed=embed, allowed_mentions=no_mentions())
        return True

    def _schedule_required_word_check(self, thread: discord.Thread) -> None:
        if thread.parent_id not in self._forum_required_rules:
            return
        if thread.id in self._forum_required_checked_threads:
            return
        self._forum_required_checked_threads.add(thread.id)
        self._start_background_task(
            self._enforce_required_word(thread),
            label=f"Required-word check for thread {thread.id}",
        )

    def _normalize_required_word_text(self, value: Any) -> str:
        text = str(value or "")
        text = re.sub(r"[\u200b-\u200f\ufeff]", "", text)
        return text.casefold()

    def _required_regex_is_safe(self, pattern: str) -> bool:
        """Allow useful search regexes while rejecting high-risk constructs."""
        if not pattern or len(pattern) > 128:
            return False
        # Group nesting, lookarounds, backreferences, and repeated wildcard
        # quantifiers are unnecessary for this feature and are common sources
        # of catastrophic backtracking in Python's standard regex engine.
        if any(token in pattern for token in ("(", ")", "(?", "\\1", "\\2", "\\3")):
            return False
        if re.search(r"(?:\.\*|\.\+).*(?:\.\*|\.\+)", pattern):
            return False
        if re.search(r"(?:[*+?]|\{\d+(?:,\d*)?\})\s*(?:[*+?]|\{)", pattern):
            return False
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error:
            return False
        return True

    async def _thread_contains_required_word(self, thread: discord.Thread, required_word: str, match_mode: str = "contains") -> bool:
        needle = self._normalize_required_word_text(required_word).strip()
        text_parts = [thread.name or ""]
        try:
            async for msg in thread.history(limit=10, oldest_first=True):
                if msg.author and msg.author.bot:
                    continue
                if msg.content:
                    text_parts.append(msg.content)
                for embed in msg.embeds:
                    if embed.title:
                        text_parts.append(embed.title)
                    if embed.description:
                        text_parts.append(embed.description)
        except Exception:
            # If history cannot be read, avoid deleting a valid thread by mistake.
            return True

        haystack = self._normalize_required_word_text("\n".join(text_parts))[:20000]
        if not needle:
            return True
        if match_mode == "whole_word":
            pattern = rf"(?<![0-9A-Za-z_]){re.escape(needle)}(?![0-9A-Za-z_])"
            return re.search(pattern, haystack) is not None
        if match_mode == "regex":
            if not self._required_regex_is_safe(required_word):
                return needle in haystack
            return re.search(required_word, haystack[:4000], re.IGNORECASE) is not None
        return needle in haystack

    async def _find_thread_owner(self, thread: discord.Thread) -> Optional[discord.abc.User]:
        owner_id = getattr(thread, "owner_id", None)
        if owner_id:
            member = thread.guild.get_member(owner_id) if thread.guild else None
            if member:
                return member
            try:
                return await self.bot.fetch_user(owner_id)
            except Exception:
                return None

        try:
            async for msg in thread.history(limit=5, oldest_first=True):
                if msg.author and not msg.author.bot:
                    return msg.author
        except Exception:
            return None
        return None

    async def _log_required_word_deletion(self, thread: discord.Thread, owner, required_word: str, match_mode: str) -> None:
        guild = thread.guild
        if guild is None:
            return
        channel_id = self.bot.config.get_int("channels", "general_logging_channel_id", default=0)
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None and channel_id:
            try:
                channel = await guild.fetch_channel(channel_id)
            except Exception:
                channel = None
        if not isinstance(channel, discord.TextChannel):
            return

        embed = discord.Embed(
            title="Forum Post Removed",
            description="A forum post was deleted because it did not include the required word.",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Thread", value=f"{thread.name}\n`{thread.id}`", inline=False)
        embed.add_field(name="Forum", value=f"<#{thread.parent_id}>\n`{thread.parent_id}`", inline=True)
        if owner:
            embed.add_field(name="Author", value=f"{owner.mention if hasattr(owner, 'mention') else owner}\n`{owner.id}`", inline=True)
        else:
            owner_id = getattr(thread, "owner_id", None)
            embed.add_field(name="Author", value=(f"`{owner_id}`" if owner_id else "Unknown"), inline=True)
        embed.add_field(name="Required Word", value=f"`{required_word}`", inline=True)
        embed.add_field(name="Match Mode", value=f"`{match_mode}`", inline=True)
        try:
            await channel.send(embed=embed, allowed_mentions=no_mentions())
        except Exception as e:
            await log_error(self.bot, f"Could not log required-word deletion for thread {thread.id}: {repr(e)}")

    async def _enforce_required_word(self, thread: discord.Thread) -> None:
        rule = self._forum_required_rules.get(thread.parent_id)
        if not rule:
            return

        delay = float(rule.get("delete_delay_seconds", 10.0) or 10.0)
        if delay:
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return

        required_word = str(rule.get("word", "") or "").strip()
        if not required_word:
            return

        match_mode = str(rule.get("match_mode") or "contains")
        if await self._thread_contains_required_word(thread, required_word, match_mode):
            return

        owner = await self._find_thread_owner(thread)
        try:
            try:
                if getattr(thread, "archived", False) or getattr(thread, "locked", False):
                    await thread.edit(archived=False, locked=False)
            except Exception:
                pass
            await thread.delete()
        except Exception as e:
            await log_error(self.bot, f"Could not delete thread {thread.id} missing required word {required_word!r}: {repr(e)}")
            return

        dm_template = str(rule.get("dm_message", "") or "")
        if owner and dm_template:
            try:
                dm_text = dm_template.format(
                    required_word=required_word,
                    thread_name=thread.name,
                    guild=thread.guild.name if thread.guild else "",
                )
            except Exception:
                dm_text = dm_template
            try:
                await owner.send(dm_text, allowed_mentions=no_mentions())
            except Exception as e:
                await log_error(
                    self.bot,
                    f"Required-word infraction DM failed thread_id={thread.id} "
                    f"owner_id={getattr(owner, 'id', 0)}: {repr(e)}",
                )

        await self._log_required_word_deletion(thread, owner, required_word, match_mode)

    async def _forum_first_message_flow(self, thread: discord.Thread, prefer_normal: bool) -> None:
        """One task at a time per thread.

        - Normal path (on_thread_create) runs first.
        - Fallback (on_message) waits, then checks if the bot already posted; if not, sends.
        """
        if thread.guild is None:
            return
        if thread.parent_id not in self._forum_rules:
            return

        # Fast skip if already handled in this runtime.
        if thread.id in self._forum_sent_threads:
            self._schedule_required_word_check(thread)
            return

        lock = self._get_thread_lock(thread.id)
        try:
            async with lock:
                # Re-check inside lock.
                if thread.id in self._forum_sent_threads:
                    self._schedule_required_word_check(thread)
                    return

                # If fallback, give normal path time to send first.
                if not prefer_normal:
                    try:
                        await asyncio.sleep(2.0)
                    except Exception:
                        pass

                # Manual check: if bot already posted in the thread, don't send again.
                if await self._thread_has_bot_message(thread):
                    self._forum_sent_threads.add(thread.id)
                    self._schedule_required_word_check(thread)
                    return

                # Try to send with retries (attachment posts can race thread readiness)
                last_error: Optional[Exception] = None
                for attempt in range(6):
                    try:
                        if attempt == 0:
                            await asyncio.sleep(1.0)
                        sent = await self._send_forum_first_message(thread)
                        if sent:
                            self._forum_sent_threads.add(thread.id)
                            self._schedule_required_word_check(thread)
                        return
                    except Exception as e:
                        last_error = e
                        try:
                            await asyncio.sleep(1.0 + attempt * 0.5)
                        except Exception:
                            return
                if last_error is not None:
                    await log_error(
                        self.bot,
                        f"Forum first message exhausted retries thread_id={thread.id}: {repr(last_error)}",
                    )
        finally:
            waiters = getattr(lock, "_waiters", None)
            has_waiters = bool(waiters)
            if not lock.locked() and not has_waiters:
                self._forum_thread_locks.pop(thread.id, None)

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        cfg = self.bot.config
        allowed_guild_id = cfg.get_int("guild", "allowed_guild_id")
        if thread.guild is None or thread.guild.id != allowed_guild_id:
            return

        if thread.parent_id not in self._forum_rules:
            return

        # Normal path: prefer_normal=True so it doesn't delay.
        self._start_background_task(
            self._forum_first_message_flow(thread, prefer_normal=True),
            label=f"Forum first message for thread {thread.id}",
        )


def setup(bot: discord.Bot):
    bot.add_cog(StickyCog(bot))
