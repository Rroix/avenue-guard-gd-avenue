from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cogs.Help import BanInfoModal, HelpCog
from utils.db import Database
from utils.views import FormerMemberHelpView, HelpMenuView


class FakeConfig:
    def __init__(self):
        self.data = {
            "help": {
                "faq": {
                    "title": "FAQ",
                    "entries": [
                        f"Question {index}?\n> Answer {index}"
                        for index in range(1, 7)
                    ],
                }
            },
            "tickets": {
                "partnership_ping_role_id": "1106906460362911835",
            },
            "level_requests": {"level_requested": "1120741230570127371"},
        }

    def get(self, *path, default=None):
        value = self.data
        for key in path:
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value

    def get_int(self, *path, default=0):
        try:
            return int(self.get(*path, default=default))
        except (TypeError, ValueError):
            return default


def make_cog(db=None):
    cog = object.__new__(HelpCog)
    cog.bot = SimpleNamespace(config=FakeConfig(), db=db)
    return cog


@pytest.mark.asyncio
async def test_help_menus_are_simple_and_context_specific():
    member_options = HelpMenuView().children[0].options
    member_values = {option.value for option in member_options}
    assert "faq_search" not in member_values
    assert "partnership" in member_values

    former_options = FormerMemberHelpView().children[0].options
    assert [option.value for option in former_options] == ["ban_appeal", "ban_info"]


def test_faq_is_paginated_and_uses_questions_as_field_titles():
    embed, pages = make_cog()._faq_page_embed(0)
    assert pages == 2
    assert len(embed.fields) == 4
    assert embed.fields[0].name == "Question 1?"
    assert embed.fields[0].value == "Answer 1"


@pytest.mark.asyncio
async def test_ban_information_modal_serializes_file_upload():
    payload = BanInfoModal(make_cog(), 12).to_dict()
    assert len(payload["components"]) == 5
    assert payload["components"][-1]["component"]["type"] == 19
    assert payload["components"][-1]["component"]["required"] is False


@pytest.mark.asyncio
async def test_request_panel_only_shows_current_wave_submission(tmp_path):
    db = Database(str(tmp_path / "help.db"))
    await db.connect()
    await db.execute(
        "INSERT INTO level_request_state(guild_id,state,wave_id,submitted_count) VALUES(?,?,?,?)",
        (717, "closed", 4, 1),
    )
    await db.execute(
        "INSERT INTO level_request_submissions("
        "guild_id,wave_id,user_id,level_id,request_message_id,status,result,created_ts,data_json"
        ") VALUES(?,?,?,?,?,?,?,?,?)",
        (
            717,
            4,
            42,
            "111111111",
            987,
            "reviewed",
            "stolen_level",
            1,
            '{"level_name":"Test Level"}',
        ),
    )
    cog = make_cog(db)

    text = await cog._request_state_text(717, 42)
    assert "Rejected: Stolen level" in text
    assert "Test Level (`111111111`)" in text
    assert "/717/1120741230570127371/987" in text
    assert await cog._request_state_text(717, 99) == ""

    await db.execute("UPDATE level_request_state SET wave_id=5 WHERE guild_id=717")
    assert await cog._request_state_text(717, 42) == ""
    await db.close()


def test_ban_delivery_omits_empty_fields_and_has_fallback():
    cog = make_cog()
    user = SimpleNamespace(__str__=lambda self: "Example")
    detailed = cog._ban_info_delivery_embed(
        user,
        {"reason": "Rule violation", "ban_date": "", "evidence_text": "", "notes": ""},
        [],
    )
    assert [field.name for field in detailed.fields] == ["Reason"]

    fallback = cog._ban_info_delivery_embed(
        user,
        {"reason": "", "ban_date": "", "evidence_text": "", "notes": ""},
        [],
    )
    assert not fallback.fields
    assert "weren't able to retrieve" in fallback.description


@pytest.mark.asyncio
async def test_appeal_flow_continues_through_all_three_questions(tmp_path):
    db = Database(str(tmp_path / "appeal-flow.db"))
    await db.connect()
    cog = make_cog(db)
    guild = SimpleNamespace(id=717)
    channel = SimpleNamespace(send=AsyncMock())
    author = SimpleNamespace(id=42)

    await cog._start_help_session(
        author.id,
        guild.id,
        "appeal_punishment",
        {"appeal_type": "ban", "former_member": True},
    )
    for content, expected_stage in (
        ("I was banned after an argument", "appeal_reason"),
        ("I understand the rule and want another chance", "appeal_behavior"),
        ("I will step away instead of escalating", "preview_appeal"),
    ):
        handled = await cog._handle_help_session_message(
            guild,
            SimpleNamespace(
                author=author,
                content=content,
                attachments=[],
                channel=channel,
            ),
        )
        row = await db.fetchone(
            "SELECT stage FROM help_sessions WHERE guild_id=? AND user_id=?",
            (guild.id, author.id),
        )
        assert handled is True
        assert row["stage"] == expected_stage

    sent_embeds = [
        call.kwargs.get("embed")
        for call in channel.send.await_args_list
        if call.kwargs.get("embed") is not None
    ]
    assert any("Why should staff revoke" in str(embed.description or "") for embed in sent_embeds)
    assert any("What will change" in str(embed.description or "") for embed in sent_embeds)
    assert sent_embeds[-1].title == "Review Appeal"
    await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stage", "kind", "payload"),
    [
        ("report_details", "report", "User 123 posted abusive messages"),
        ("bot_issue_details", "bot_issue", "The request button returned an error"),
    ],
)
async def test_single_answer_support_flows_continue_to_preview(tmp_path, stage, kind, payload):
    db = Database(str(tmp_path / f"{kind}.db"))
    await db.connect()
    cog = make_cog(db)
    guild = SimpleNamespace(id=717)
    channel = SimpleNamespace(send=AsyncMock())
    author = SimpleNamespace(id=42)
    await cog._start_help_session(author.id, guild.id, stage, {})

    handled = await cog._handle_help_session_message(
        guild,
        SimpleNamespace(
            author=author,
            content=payload,
            attachments=[],
            channel=channel,
        ),
    )
    row = await db.fetchone(
        "SELECT stage FROM help_sessions WHERE guild_id=? AND user_id=?",
        (guild.id, author.id),
    )

    assert handled is True
    assert row["stage"] == f"preview_{kind}"
    assert channel.send.await_args.kwargs["embed"].title.startswith("Review ")
    await db.close()


@pytest.mark.asyncio
async def test_partnership_confirmation_uses_only_partnership_ping_override():
    class Response:
        def __init__(self):
            self.done = False

        def is_done(self):
            return self.done

        async def defer(self):
            self.done = True

    guild = SimpleNamespace(id=717)
    cog = make_cog()
    cog.bot.get_guild = lambda guild_id: guild if guild_id == guild.id else None
    cog._create_staff_ticket = AsyncMock()
    interaction = SimpleNamespace(
        response=Response(),
        message=SimpleNamespace(delete=AsyncMock()),
        user=SimpleNamespace(id=42),
        channel=SimpleNamespace(),
    )

    await cog.handle_partnership_confirmation(
        interaction,
        guild.id,
        confirmed=True,
    )

    assert cog._create_staff_ticket.await_args.args[2:4] == (
        "partnership",
        "Partnership request",
    )
    assert cog._create_staff_ticket.await_args.kwargs == {
        "ping_role_id": 1106906460362911835,
    }


@pytest.mark.asyncio
async def test_submission_preview_defers_before_waiting_for_lock():
    events = []

    class Response:
        def __init__(self):
            self.done = False

        def is_done(self):
            return self.done

        async def defer(self):
            self.done = True
            events.append("defer")

    class OrderedLock:
        async def __aenter__(self):
            events.append("lock")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    cog = make_cog()
    cog._submission_preview_lock = OrderedLock()

    async def locked_handler(*args):
        events.append("handler")

    cog._handle_help_submission_preview_locked = AsyncMock(side_effect=locked_handler)
    interaction = SimpleNamespace(response=Response())

    await cog.handle_help_submission_preview(interaction, 717, "appeal", "submit")

    assert events == ["defer", "lock", "handler"]
