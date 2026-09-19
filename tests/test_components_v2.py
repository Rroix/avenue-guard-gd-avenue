from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord
from discord.components import _component_factory

from utils.components_v2 import (
    AvenueDesignerView,
    _legacy_message_cleanup,
    _modernize_call,
    _prepare_existing_v2_edit,
    _wrap_method,
    build_components_v2,
    install_components_v2_adapter,
    message_component_text,
)


def _payload(view: discord.ui.DesignerView) -> list[dict]:
    return view.to_components()


def _walk_payload(values):
    for value in values:
        yield value
        yield from _walk_payload(value.get("components", []))


def test_embed_becomes_accented_container_with_structured_text():
    async def run():
        embed = discord.Embed(
            title="System Health",
            description="Everything is operational",
            color=discord.Color.green(),
        )
        embed.add_field(name="Database", value="Connected", inline=True)
        embed.add_field(name="Requests", value="Open", inline=True)
        embed.set_footer(text="Avenue Guard")
        view = build_components_v2([embed], content="Staff only")
        payload = _payload(view)
        assert payload[0]["type"] == 17
        assert payload[0]["accent_color"] == discord.Color.green().value
        rendered = "\n".join(
            component.get("content", "") for component in _walk_payload(payload)
        )
        assert "## System Health" in rendered
        assert "Everything is operational" in rendered
        assert "**Database**\nConnected" in rendered
        assert "**Requests**\nOpen" in rendered
        assert "-# Avenue Guard" in rendered
        assert "Staff only" in rendered

    asyncio.run(run())


def test_thumbnail_gallery_and_attachment_file_are_exposed():
    async def run():
        embed = discord.Embed(title="Evidence")
        embed.set_thumbnail(url="https://example.com/icon.png")
        embed.set_image(url="attachment://preview.png")
        attached = SimpleNamespace(filename="report.txt")
        payload = _payload(build_components_v2([embed], files=[attached]))
        flattened = list(_walk_payload(payload))
        assert any(item.get("type") == 9 for item in flattened)
        assert any(item.get("type") == 12 for item in flattened)
        assert any(
            item.get("type") == 13
            and item.get("file", {}).get("url") == "attachment://report.txt"
            for item in flattened
        )

    asyncio.run(run())


def test_legacy_buttons_keep_custom_ids_callbacks_and_persistence():
    class Controls(discord.ui.View):
        @discord.ui.button(
            label="Approve",
            style=discord.ButtonStyle.success,
            custom_id="approve_test",
        )
        async def approve(self, button, interaction):
            return None

    async def run():
        legacy = Controls(timeout=None)
        original_callback = legacy.children[0].callback
        view = build_components_v2(
            [discord.Embed(title="Approval")],
            view=legacy,
        )
        payload = _payload(view)
        buttons = [item for item in _walk_payload(payload) if item.get("type") == 2]
        assert isinstance(view, AvenueDesignerView)
        assert buttons[0]["custom_id"] == "approve_test"
        assert legacy.children[0].callback is original_callback
        assert view.is_persistent()

    asyncio.run(run())


def test_explicit_legacy_rows_are_normalized_for_v2_action_rows():
    class MultiRowControls(discord.ui.View):
        @discord.ui.button(label="First", custom_id="first", row=0)
        async def first(self, button, interaction):
            return None

        @discord.ui.button(label="Second", custom_id="second", row=1)
        async def second(self, button, interaction):
            return None

    async def run():
        legacy = MultiRowControls()
        callbacks = [item.callback for item in legacy.children]
        view = build_components_v2(
            [discord.Embed(title="Multi-row controls")],
            view=legacy,
        )
        payload = _payload(view)
        action_rows = [
            item for item in _walk_payload(payload) if item.get("type") == 1
        ]
        assert len(action_rows) == 2
        assert [
            row["components"][0]["custom_id"] for row in action_rows
        ] == ["first", "second"]
        assert [item.callback for item in legacy.children] == callbacks
        assert all(item.row is None for item in legacy.children)

    asyncio.run(run())


def test_help_ticket_topic_view_converts_with_all_controls():
    from cogs.Help import HelpTicketTopicView

    async def run():
        legacy = HelpTicketTopicView(object(), user_id=1, guild_id=2)
        view = build_components_v2(
            [discord.Embed(title="Contact Staff")],
            view=legacy,
        )
        payload = _payload(view)
        buttons = [item for item in _walk_payload(payload) if item.get("type") == 2]
        assert [item["label"] for item in buttons] == [
            "Moderation",
            "Level requests",
            "Server help",
            "Back",
            "Start over",
            "Other",
            "Cancel",
        ]
        assert len(
            [item for item in _walk_payload(payload) if item.get("type") == 1]
        ) == 2

    asyncio.run(run())


def test_call_adapter_removes_classic_content_and_embed_fields():
    async def run():
        embed = discord.Embed(title="Modern")
        args, kwargs = _modernize_call(
            ("Original content",),
            {"embed": embed, "view": None},
            content_position=0,
        )
        assert args == (None,)
        assert "embed" not in kwargs
        assert "embeds" not in kwargs
        assert isinstance(kwargs["view"], discord.ui.DesignerView)
        rendered = str(kwargs["view"].to_components())
        assert "Original content" in rendered
        assert "Modern" in rendered

    asyncio.run(run())


def test_embed_edit_without_content_does_not_emit_legacy_content_field():
    async def run():
        _, kwargs = _modernize_call(
            (),
            {"embed": discord.Embed(title="Refresh")},
            content_position=0,
            content_replacement=discord.utils.MISSING,
        )
        assert "content" not in kwargs
        assert "embed" not in kwargs
        assert "embeds" not in kwargs
        assert isinstance(kwargs["view"], discord.ui.DesignerView)

    asyncio.run(run())


def test_legacy_message_cleanup_keeps_unknown_values_unknown():
    assert _legacy_message_cleanup(
        SimpleNamespace(content="old text", embeds=[object()])
    ) == {"content": None, "embed": None}
    assert _legacy_message_cleanup(
        SimpleNamespace(content="", embeds=[])
    ) == {}


def test_existing_v2_edit_drops_forbidden_empty_embed_fields():
    async def run():
        message = SimpleNamespace(
            flags=SimpleNamespace(is_components_v2=True),
            components=[],
        )
        _, kwargs = _prepare_existing_v2_edit(
            message,
            (),
            {"content": "Updated", "embed": None, "view": None},
            content_position=0,
        )
        assert "content" not in kwargs
        assert "embed" not in kwargs
        assert "embeds" not in kwargs
        assert isinstance(kwargs["view"], discord.ui.DesignerView)
        assert "Updated" in str(kwargs["view"].to_components())

    asyncio.run(run())


def test_legacy_message_is_cleared_before_v2_conversion():
    class FakeMessage:
        def __init__(self):
            self.flags = SimpleNamespace(is_components_v2=False)
            self.content = "legacy content"
            self.embeds = [object()]
            self.calls = []

        async def edit(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return self

    _wrap_method(
        FakeMessage,
        "edit",
        content_position=0,
        edit_target="self",
    )

    async def run():
        message = FakeMessage()
        await message.edit(embed=discord.Embed(title="Modern"))
        assert len(message.calls) == 2
        assert message.calls[0] == ((), {"content": None, "embed": None})
        final_args, final_kwargs = message.calls[1]
        assert final_args == ()
        assert "content" not in final_kwargs
        assert "embed" not in final_kwargs
        assert "embeds" not in final_kwargs
        assert isinstance(final_kwargs["view"], discord.ui.DesignerView)

    asyncio.run(run())


def test_view_only_v2_edit_preserves_card_and_replaces_controls():
    class EnabledControls(discord.ui.View):
        @discord.ui.button(label="Review", custom_id="review", disabled=False)
        async def review(self, button, interaction):
            return None

    class DisabledControls(discord.ui.View):
        @discord.ui.button(label="Review", custom_id="review", disabled=True)
        async def review(self, button, interaction):
            return None

    async def run():
        initial = build_components_v2(
            [discord.Embed(title="Level Request", description="Keep this card")],
            view=EnabledControls(),
        )
        message = SimpleNamespace(
            flags=SimpleNamespace(is_components_v2=True),
            components=[
                _component_factory(component) for component in initial.to_components()
            ],
        )
        _, kwargs = _prepare_existing_v2_edit(
            message,
            (),
            {"view": DisabledControls()},
            content_position=0,
        )
        payload = kwargs["view"].to_components()
        rendered = str(payload)
        buttons = [item for item in _walk_payload(payload) if item.get("type") == 2]
        assert "Level Request" in rendered
        assert "Keep this card" in rendered
        assert len(buttons) == 1
        assert buttons[0]["custom_id"] == "review"
        assert buttons[0]["disabled"] is True

    asyncio.run(run())


def test_adapter_is_installed_on_every_discord_delivery_boundary():
    install_components_v2_adapter()
    for owner, method in (
        (discord.abc.Messageable, "send"),
        (discord.Message, "edit"),
        (discord.InteractionResponse, "send_message"),
        (discord.InteractionResponse, "edit_message"),
        (discord.Webhook, "send"),
        (discord.Webhook, "edit_message"),
        (discord.WebhookMessage, "edit"),
    ):
        assert getattr(getattr(owner, method), "__avenue_components_v2__", False)


def test_component_text_supports_nested_discord_component_shapes():
    message = SimpleNamespace(
        components=[
            SimpleNamespace(
                content="",
                children=[
                    SimpleNamespace(content="## Ticket Transcript", children=[]),
                    SimpleNamespace(content="**Ticket**\n`T42`", children=[]),
                ],
            )
        ]
    )
    assert message_component_text(message) == (
        "## Ticket Transcript\n**Ticket**\n`T42`"
    )


def test_plain_messages_are_not_changed_by_embed_adapter():
    args = ("Plain response",)
    kwargs = {"ephemeral": True}
    assert _modernize_call(args, kwargs, content_position=0) == (args, kwargs)
