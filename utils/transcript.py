from __future__ import annotations

import io

import discord


def _indented(value: object) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return text.replace("\n", "\n    ")


def _message_text(msg: discord.Message) -> str:
    parts: list[str] = []
    if msg.content:
        parts.append(_indented(msg.content))
    for attachment in getattr(msg, "attachments", []) or []:
        filename = str(getattr(attachment, "filename", "attachment") or "attachment")
        url = str(getattr(attachment, "url", "") or "")
        parts.append(f"[attachment: {filename}] {url}".strip())
    for index, embed in enumerate(getattr(msg, "embeds", []) or [], start=1):
        title = _indented(getattr(embed, "title", ""))
        description = _indented(getattr(embed, "description", ""))
        summary = " | ".join(value for value in (title, description) if value)
        if summary:
            parts.append(f"[embed {index}] {summary}")
        for field in getattr(embed, "fields", []) or []:
            name = _indented(getattr(field, "name", "")) or "Field"
            value = _indented(getattr(field, "value", ""))
            parts.append(f"[embed {index} field] {name}: {value}")
    for sticker in getattr(msg, "stickers", []) or []:
        parts.append(f"[sticker] {getattr(sticker, 'name', 'sticker')}")
    return "\n    ".join(part for part in parts if part) or "[no text content]"


async def build_text_transcript(
    channel: discord.TextChannel,
    limit: int | None = None,
) -> io.BytesIO:
    """Build a chronological transcript.

    The default includes the complete channel history. If a caller supplies a
    limit, the newest messages are retained and the transcript states that
    older messages were omitted.
    """
    messages: list[discord.Message] = []
    truncated = False
    if limit is None:
        async for msg in channel.history(limit=None, oldest_first=True):
            messages.append(msg)
    else:
        max_messages = max(1, int(limit))
        async for msg in channel.history(limit=max_messages + 1, oldest_first=False):
            messages.append(msg)
        truncated = len(messages) > max_messages
        messages = list(reversed(messages[:max_messages]))

    lines: list[str] = []
    if truncated:
        lines.append(
            f"[Avenue Guard] Older messages were omitted; showing the newest {len(messages)} messages."
        )
    for msg in messages:
        ts = msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        author = f"{msg.author} ({msg.author.id})"
        edited_at = getattr(msg, "edited_at", None)
        edited = (
            f" [edited {edited_at.strftime('%Y-%m-%d %H:%M:%S UTC')}]"
            if edited_at is not None
            else ""
        )
        reference = getattr(getattr(msg, "reference", None), "message_id", None)
        replied = f" [reply to {reference}]" if reference else ""
        lines.append(f"[{ts}] {author}{edited}{replied}: {_message_text(msg)}")
    data = "\n".join(lines).encode("utf-8", errors="replace")
    bio = io.BytesIO(data)
    bio.seek(0)
    return bio
