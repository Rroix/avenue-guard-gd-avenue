from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional

import discord


class PersistedReferenceAmbiguous(LookupError):
    """More than one live Discord object matches a rounded stored ID."""


def legacy_rounded_snowflake(value: int) -> int:
    """Return the value produced by the legacy libsql Python float binding."""
    return int(float(int(value)))


def is_legacy_rounded_snowflake(value: int) -> bool:
    value = int(value)
    # Discord snowflakes are above the largest integer a binary64 float can
    # represent exactly. A value produced by the old binding is itself exactly
    # float-representable; its spacing is commonly 128 or 256 at this scale.
    return value > 2**53 and int(float(value)) == value


def snowflake_matches_legacy(stored_id: int, exact_id: int) -> bool:
    """Match an exact Discord ID to the rounded value stored by old releases."""
    stored_id = int(stored_id)
    exact_id = int(exact_id)
    return stored_id == exact_id or legacy_rounded_snowflake(exact_id) == stored_id


async def fetch_persisted_message(
    channel,
    message_id: int,
    *,
    author_id: int = 0,
    predicate: Optional[Callable[[discord.Message], bool]] = None,
    scan_limit: int = 25,
) -> tuple[Optional[discord.Message], bool]:
    """Fetch a stored message ID and recover IDs rounded by old libsql writes.

    The second return value is true when the exact message was recovered from
    nearby channel history and the caller should persist ``message.id``.
    Non-404 Discord errors are deliberately propagated so a network or
    permission failure cannot be mistaken for a deleted message.
    """
    message_id = int(message_id or 0)
    if not message_id:
        return None, False

    def _matches(candidate: discord.Message) -> bool:
        if author_id and int(getattr(getattr(candidate, "author", None), "id", 0) or 0) != int(author_id):
            return False
        return predicate is None or predicate(candidate)

    try:
        direct = await channel.fetch_message(message_id)
    except discord.NotFound:
        direct = None

    if direct is not None and _matches(direct):
        return direct, False

    if not is_legacy_rounded_snowflake(message_id):
        return None, False

    history = getattr(channel, "history", None)
    if not callable(history):
        return None, False

    candidates: list[discord.Message] = []
    async for candidate in history(
        limit=max(5, min(100, int(scan_limit))),
        around=discord.Object(id=message_id),
    ):
        candidate_id = int(getattr(candidate, "id", 0) or 0)
        if not candidate_id or legacy_rounded_snowflake(candidate_id) != message_id:
            continue
        if not _matches(candidate):
            continue
        candidates.append(candidate)

    if not candidates:
        return None, False
    if len(candidates) != 1:
        raise PersistedReferenceAmbiguous(
            f"stored message ID {message_id} matches {len(candidates)} messages"
        )
    return candidates[0], int(candidates[0].id) != message_id


async def fetch_persisted_channel(
    guild,
    channel_id: int,
) -> tuple[Optional[Any], bool]:
    """Resolve a channel ID that may have been rounded by old libsql builds.

    The second value is true when a different exact ID was recovered and the
    caller should persist ``channel.id``. Network and permission failures are
    propagated so callers cannot mistake a temporary Discord failure for a
    deleted channel.
    """
    channel_id = int(channel_id or 0)
    if not channel_id:
        return None, False

    direct = guild.get_channel(channel_id)
    if direct is not None:
        return direct, False

    def _matching_channels(channels) -> dict[int, Any]:
        return {
            int(candidate.id): candidate
            for candidate in channels or ()
            if getattr(candidate, "id", None)
            and snowflake_matches_legacy(channel_id, int(candidate.id))
        }

    cached = _matching_channels(
        (*tuple(getattr(guild, "channels", ()) or ()), *tuple(getattr(guild, "threads", ()) or ()))
    )
    if len(cached) == 1:
        candidate = next(iter(cached.values()))
        return candidate, int(candidate.id) != channel_id
    if len(cached) > 1:
        raise PersistedReferenceAmbiguous(
            f"stored channel ID {channel_id} matches {len(cached)} cached channels"
        )

    try:
        return await guild.fetch_channel(channel_id), False
    except discord.NotFound:
        pass

    if not is_legacy_rounded_snowflake(channel_id):
        return None, False

    fetch_channels = getattr(guild, "fetch_channels", None)
    if not callable(fetch_channels):
        return None, False
    fetched = _matching_channels(await fetch_channels())
    if len(fetched) == 1:
        candidate = next(iter(fetched.values()))
        return candidate, int(candidate.id) != channel_id
    if len(fetched) > 1:
        raise PersistedReferenceAmbiguous(
            f"stored channel ID {channel_id} matches {len(fetched)} fetched channels"
        )
    return None, False
