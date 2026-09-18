from __future__ import annotations

import inspect
from collections import defaultdict
from datetime import datetime
from functools import wraps
from typing import Any, Iterable, Sequence

import discord


TEXT_DISPLAY_LIMIT = 3900
MAX_COMPONENTS = 40


def _component_count(components: Sequence[dict[str, Any]]) -> int:
    return sum(
        1 + _component_count(component.get("components", []))
        for component in components
    )


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _split_markdown(text: str, limit: int = TEXT_DISPLAY_LIMIT) -> list[str]:
    """Split markdown on natural boundaries while respecting Discord limits."""
    remaining = _clean(text)
    if not remaining:
        return []
    chunks: list[str] = []
    while len(remaining) > limit:
        boundary = max(
            remaining.rfind("\n\n", 0, limit + 1),
            remaining.rfind("\n", 0, limit + 1),
            remaining.rfind(" ", 0, limit + 1),
        )
        if boundary < limit // 2:
            boundary = limit
        chunk = remaining[:boundary].rstrip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[boundary:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _embed_header(embed: discord.Embed) -> str:
    parts: list[str] = []
    author = _clean(getattr(getattr(embed, "author", None), "name", ""))
    title = _clean(getattr(embed, "title", ""))
    url = _clean(getattr(embed, "url", ""))
    description = _clean(getattr(embed, "description", ""))
    if author:
        parts.append(f"-# **{author}**")
    if title:
        parts.append(f"## [{title}]({url})" if url else f"## {title}")
    if description:
        parts.append(description)
    return "\n".join(parts)


def _embed_fields(embed: discord.Embed) -> str:
    parts: list[str] = []
    for field in getattr(embed, "fields", ()) or ():
        name = _clean(getattr(field, "name", "")) or "Details"
        value = _clean(getattr(field, "value", "")) or "Not provided"
        parts.append(f"**{name}**\n{value}")
    return "\n\n".join(parts)


def _embed_footer(embed: discord.Embed) -> str:
    parts: list[str] = []
    footer = _clean(getattr(getattr(embed, "footer", None), "text", ""))
    if footer:
        parts.append(footer)
    timestamp = getattr(embed, "timestamp", None)
    if isinstance(timestamp, datetime):
        parts.append(f"<t:{int(timestamp.timestamp())}:f>")
    return " • ".join(parts)


def _embed_colour(embed: discord.Embed) -> discord.Colour | None:
    colour = getattr(embed, "colour", None)
    if colour is None:
        return None
    try:
        return colour if int(colour.value) else None
    except (AttributeError, TypeError, ValueError):
        return None


def _thumbnail_url(embed: discord.Embed) -> str:
    thumbnail = _clean(getattr(getattr(embed, "thumbnail", None), "url", ""))
    if thumbnail:
        return thumbnail
    return _clean(getattr(getattr(embed, "author", None), "icon_url", ""))


def _image_url(embed: discord.Embed) -> str:
    return _clean(getattr(getattr(embed, "image", None), "url", ""))


def _file_names(file: Any = None, files: Any = None) -> list[str]:
    values: list[Any] = []
    if file not in (None, discord.utils.MISSING):
        values.append(file)
    if files not in (None, discord.utils.MISSING):
        values.extend(files or ())
    names: list[str] = []
    for item in values:
        name = _clean(getattr(item, "filename", ""))
        if name and name not in names:
            names.append(name)
    return names


def _legacy_action_rows(view: discord.ui.View | None) -> list[discord.ui.ActionRow]:
    if view is None or isinstance(view, discord.ui.DesignerView):
        return []
    grouped: dict[int, list[discord.ui.Item]] = defaultdict(list)
    for index, item in enumerate(list(getattr(view, "children", ()) or ())):
        rendered_row = getattr(item, "_rendered_row", None)
        declared_row = getattr(item, "row", None)
        row = rendered_row if rendered_row is not None else declared_row
        grouped[int(row if row is not None else index)].append(item)
    rows: list[discord.ui.ActionRow] = []
    for row_number in sorted(grouped):
        items = grouped[row_number]
        if items:
            rows.append(discord.ui.ActionRow(*items))
    return rows


class AvenueDesignerView(discord.ui.DesignerView):
    """Designer view that preserves checks and callbacks from a legacy View."""

    def __init__(
        self,
        *items: discord.ui.ViewItem,
        legacy_view: discord.ui.View | None = None,
    ) -> None:
        timeout = getattr(legacy_view, "timeout", None) if legacy_view else None
        disable_on_timeout = bool(
            getattr(legacy_view, "disable_on_timeout", False)
        )
        super().__init__(
            *items,
            timeout=timeout,
            disable_on_timeout=disable_on_timeout,
        )
        self.legacy_view = legacy_view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.legacy_view is None:
            return True
        result = self.legacy_view.interaction_check(interaction)
        return bool(await result) if inspect.isawaitable(result) else bool(result)

    async def on_timeout(self) -> None:
        if self.legacy_view is None:
            return
        result = self.legacy_view.on_timeout()
        if inspect.isawaitable(result):
            await result

    async def on_error(
        self,
        error: Exception,
        item: discord.ui.Item,
        interaction: discord.Interaction,
    ) -> None:
        if self.legacy_view is None:
            return await super().on_error(error, item, interaction)
        result = self.legacy_view.on_error(error, item, interaction)
        if inspect.isawaitable(result):
            await result


def _container_for_embed(
    embed: discord.Embed,
    *,
    leading_content: str = "",
    action_rows: Sequence[discord.ui.ActionRow] = (),
    attachment_names: Sequence[str] = (),
) -> discord.ui.Container:
    items: list[discord.ui.ViewItem] = []
    header = _embed_header(embed)
    if leading_content:
        header = f"{leading_content}\n\n{header}" if header else leading_content

    header_chunks = _split_markdown(header)
    thumbnail_url = _thumbnail_url(embed)
    if thumbnail_url and header_chunks:
        section_chunks = header_chunks[:3]
        items.append(
            discord.ui.Section(
                *(discord.ui.TextDisplay(chunk) for chunk in section_chunks),
                accessory=discord.ui.Thumbnail(
                    thumbnail_url,
                    description=_clean(getattr(embed, "title", "")) or "Avenue Guard",
                ),
            )
        )
        header_chunks = header_chunks[3:]
    for chunk in header_chunks:
        items.append(discord.ui.TextDisplay(chunk))

    fields = _embed_fields(embed)
    if fields:
        if items:
            items.append(discord.ui.Separator(spacing=discord.SeparatorSpacingSize.small))
        items.extend(discord.ui.TextDisplay(chunk) for chunk in _split_markdown(fields))

    image_url = _image_url(embed)
    if image_url:
        gallery = discord.ui.MediaGallery()
        gallery.add_item(
            image_url,
            description=_clean(getattr(embed, "title", "")) or "Avenue Guard image",
        )
        items.append(gallery)

    referenced_attachments = {
        value.removeprefix("attachment://")
        for value in (image_url, thumbnail_url)
        if value.startswith("attachment://")
    }
    for filename in attachment_names:
        if filename not in referenced_attachments:
            items.append(discord.ui.File(f"attachment://{filename}"))

    footer = _embed_footer(embed)
    if footer:
        items.append(discord.ui.Separator(spacing=discord.SeparatorSpacingSize.small))
        items.append(discord.ui.TextDisplay(f"-# {footer}"))

    if action_rows:
        if items:
            items.append(discord.ui.Separator(spacing=discord.SeparatorSpacingSize.small))
        items.extend(action_rows)

    if not items:
        items.append(discord.ui.TextDisplay("Avenue Guard"))
    return discord.ui.Container(*items, colour=_embed_colour(embed))


def build_components_v2(
    embeds: Iterable[discord.Embed],
    *,
    content: Any = None,
    view: discord.ui.BaseView | None = None,
    file: Any = None,
    files: Any = None,
) -> discord.ui.DesignerView:
    """Render classic embeds and controls as one Components V2 message."""
    embed_list = [item for item in embeds if isinstance(item, discord.Embed)]
    if not embed_list:
        raise ValueError("at least one embed is required")
    content_text = "" if content in (None, discord.utils.MISSING) else str(content)
    attachment_names = _file_names(file=file, files=files)

    if isinstance(view, discord.ui.DesignerView):
        top_level = list(view.children)
        first = _container_for_embed(
            embed_list[0],
            leading_content=content_text,
            attachment_names=attachment_names,
        )
        result = AvenueDesignerView(
            first,
            *(_container_for_embed(item) for item in embed_list[1:]),
            *top_level,
            legacy_view=None,
        )
        if _component_count(result.to_components()) > MAX_COMPONENTS:
            raise ValueError("Components V2 layout exceeds Discord's 40-component limit")
        return result

    legacy_view = view if isinstance(view, discord.ui.View) else None
    action_rows = _legacy_action_rows(legacy_view)
    containers: list[discord.ui.Container] = []
    for index, embed in enumerate(embed_list):
        containers.append(
            _container_for_embed(
                embed,
                leading_content=content_text if index == 0 else "",
                action_rows=action_rows if index == len(embed_list) - 1 else (),
                attachment_names=attachment_names if index == len(embed_list) - 1 else (),
            )
        )
    result = AvenueDesignerView(*containers, legacy_view=legacy_view)
    if _component_count(result.to_components()) > MAX_COMPONENTS:
        raise ValueError("Components V2 layout exceeds Discord's 40-component limit")
    return result


def _provided(value: Any) -> bool:
    return value not in (None, discord.utils.MISSING)


def _modernize_call(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    content_position: int | None,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    embed = kwargs.get("embed", discord.utils.MISSING)
    embeds = kwargs.get("embeds", discord.utils.MISSING)
    if _provided(embed) and _provided(embeds):
        raise TypeError("cannot mix embed and embeds")
    if _provided(embed):
        embed_list = [embed]
    elif _provided(embeds) and embeds:
        embed_list = list(embeds)
    else:
        return args, kwargs

    mutable_args = list(args)
    if content_position is not None and len(mutable_args) > content_position:
        content = mutable_args[content_position]
        mutable_args[content_position] = None
    else:
        content = kwargs.get("content", None)
        kwargs["content"] = None

    kwargs["view"] = build_components_v2(
        embed_list,
        content=content,
        view=kwargs.get("view"),
        file=kwargs.get("file"),
        files=kwargs.get("files"),
    )
    kwargs.pop("embed", None)
    kwargs.pop("embeds", None)
    return tuple(mutable_args), kwargs


def _wrap_method(owner: type, name: str, *, content_position: int | None) -> None:
    original = getattr(owner, name)
    if getattr(original, "__avenue_components_v2__", False):
        return

    @wraps(original)
    async def wrapped(self, *args, **kwargs):
        new_args, new_kwargs = _modernize_call(
            args,
            dict(kwargs),
            content_position=content_position,
        )
        return await original(self, *new_args, **new_kwargs)

    wrapped.__avenue_components_v2__ = True
    wrapped.__avenue_original__ = original
    setattr(owner, name, wrapped)


def install_components_v2_adapter() -> None:
    """Install one idempotent boundary adapter for every Discord send path."""
    _wrap_method(discord.abc.Messageable, "send", content_position=0)
    _wrap_method(discord.Message, "edit", content_position=0)
    _wrap_method(discord.InteractionResponse, "send_message", content_position=0)
    _wrap_method(discord.InteractionResponse, "edit_message", content_position=None)
    _wrap_method(discord.Webhook, "send", content_position=0)
    _wrap_method(discord.Webhook, "edit_message", content_position=None)
    _wrap_method(discord.WebhookMessage, "edit", content_position=0)


def message_component_text(message: Any) -> str:
    """Return user-visible V2 text for compatibility and message identification."""
    output: list[str] = []

    def walk(component: Any) -> None:
        content = _clean(getattr(component, "content", ""))
        if content:
            output.append(content)
        for child in getattr(component, "children", ()) or ():
            walk(child)
        for child in getattr(component, "components", ()) or ():
            walk(child)

    for component in getattr(message, "components", ()) or ():
        walk(component)
    return "\n".join(output)
