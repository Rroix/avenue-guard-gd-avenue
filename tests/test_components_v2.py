from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

from utils.components_v2 import (
    AvenueDesignerView,
    _modernize_call,
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
