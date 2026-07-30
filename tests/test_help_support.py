import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

import cogs.Help as help_module
from cogs.Help import BanInfoModal, HelpCog, HelpSessionControlView
from utils.db import Database
from utils.views import FormerMemberHelpView, HelpMenuView


class FakeConfig:
    def __init__(self):
        self.data = {
            "guild": {"allowed_guild_id": "717"},
            "roles": {"MOD_ROLE_ID": "123"},
            "permissions": {"manage_guild_counts_as_mod": True},
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
                "ticket_inactivity_hours": 24,
                "ticket_creation_cooldown_hours": 24,
                "satisfaction_prompt": "How was your staff ticket experience?",
            },
            "channels": {},
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
    cog.bot = SimpleNamespace(
        config=FakeConfig(),
        db=db,
        get_channel=lambda _channel_id: None,
        fetch_channel=AsyncMock(return_value=None),
    )
    cog._last_error_log = {}
    cog._active_ticket_channels = set()
    return cog


@pytest.mark.asyncio
async def test_ticket_close_lock_serializes_and_cleans_up_without_private_asyncio_state():
    cog = make_cog()
    cog._ticket_close_locks = {}
    cog._ticket_close_lock_users = {}
    active = 0
    max_active = 0

    async def close_once(_guild, _channel_id):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        active -= 1
        return True

    cog._close_ticket_channel_locked = close_once
    results = await asyncio.gather(
        cog.close_ticket_channel(SimpleNamespace(), 999),
        cog.close_ticket_channel(SimpleNamespace(), 999),
    )

    assert results == [True, True]
    assert max_active == 1
    assert cog._ticket_close_locks == {}
    assert cog._ticket_close_lock_users == {}


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
async def test_initial_help_flow_has_start_and_no_dead_navigation_buttons():
    initial = HelpSessionControlView(make_cog(), 42, 717, allow_back=False, show_start=True)
    assert [item.label for item in initial.children] == ["Start", "Cancel"]

    active = HelpSessionControlView(make_cog(), 42, 717, allow_back=False)
    assert [item.label for item in active.children] == ["Cancel", "Start over"]


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
async def test_live_session_cache_survives_stale_turso_read():
    class StaleReplica:
        execute = AsyncMock()
        fetchone_local = AsyncMock(return_value=None)
        fetchone = AsyncMock(return_value=None)

    db = StaleReplica()
    cog = make_cog(db)
    guild = SimpleNamespace(id=717)
    channel = SimpleNamespace(send=AsyncMock())
    author = SimpleNamespace(id=42)

    await cog._start_help_session(author.id, guild.id, "report_details", {})
    handled = await cog._handle_help_session_message(
        guild,
        SimpleNamespace(
            author=author,
            content="User 123 repeatedly posted abusive messages",
            attachments=[],
            channel=channel,
        ),
    )

    assert handled is True
    assert db.fetchone_local.await_count == 0
    assert cog._help_session_cache[(guild.id, author.id)]["stage"] == "preview_report"
    assert channel.send.await_args.kwargs["embed"].title == "Review Report"


@pytest.mark.asyncio
async def test_live_session_continues_during_transient_storage_failure():
    class FailingReplica:
        execute = AsyncMock(side_effect=RuntimeError("temporary Turso outage"))
        fetchone_local = AsyncMock(side_effect=RuntimeError("temporary Turso outage"))
        fetchone = AsyncMock(side_effect=RuntimeError("temporary Turso outage"))

    cog = make_cog(FailingReplica())
    cog._log_background_error = AsyncMock()
    guild = SimpleNamespace(id=717)
    channel = SimpleNamespace(send=AsyncMock())
    author = SimpleNamespace(id=42)

    await cog._start_help_session(author.id, guild.id, "bot_issue_details", {})
    handled = await cog._handle_help_session_message(
        guild,
        SimpleNamespace(
            author=author,
            content="The request button stopped responding",
            attachments=[],
            channel=channel,
        ),
    )

    assert handled is True
    assert cog._help_session_cache[(guild.id, author.id)]["stage"] == "preview_bot_issue"
    assert channel.send.await_args.kwargs["embed"].title == "Review Bot issue"


@pytest.mark.asyncio
async def test_staff_appeal_reply_uses_known_guild_member_before_global_fetch(tmp_path):
    requester_id = 1102884420207255653
    staff_id = 123456789012345678
    db = Database(str(tmp_path / "staff-appeal-reply.db"))
    await db.connect()
    await db.execute(
        "INSERT INTO help_submissions("
        "guild_id,kind,user_id,status,created_ts,updated_ts,log_channel_id,log_message_id,data_json"
        ") VALUES(?,?,?,?,?,?,?,?,?)",
        (717, "appeal", requester_id, "pending", 1, 1, 888, 999, "{}"),
    )

    requester = SimpleNamespace(id=requester_id, send=AsyncMock())
    staff = SimpleNamespace(
        id=staff_id,
        bot=False,
        roles=[],
        guild_permissions=SimpleNamespace(manage_guild=True),
    )
    cog = make_cog(db)
    cog.bot.fetch_user = AsyncMock(side_effect=AssertionError("global fetch should not be used"))
    cog.bot.get_user = lambda user_id: None
    guild = SimpleNamespace(
        id=717,
        icon=None,
        get_member=lambda user_id: {
            requester_id: requester,
        }.get(user_id),
        fetch_member=AsyncMock(side_effect=AssertionError("guild fetch should not be used")),
        get_channel=lambda channel_id: None,
    )
    original = SimpleNamespace(
        embeds=[cog._submission_staff_embed(guild, requester_id, "appeal", 1, {})],
        edit=AsyncMock(),
    )
    channel = SimpleNamespace(
        id=888,
        fetch_message=AsyncMock(return_value=original),
    )
    message = SimpleNamespace(
        author=staff,
        guild=guild,
        channel=channel,
        content="Your appeal has been accepted.",
        attachments=[],
        reply=AsyncMock(),
    )

    handled = await cog._handle_staff_help_reply_locked(message, 999)
    row = await db.fetchone("SELECT status, user_id FROM help_submissions WHERE id=1")

    assert handled is True
    requester.send.assert_awaited_once()
    cog.bot.fetch_user.assert_not_awaited()
    assert row["status"] == "responded"
    assert int(row["user_id"]) == requester_id
    original.edit.assert_awaited_once()
    await db.close()


def test_staff_embed_contains_recoverable_requester_id():
    requester_id = 1102884420207255653
    embed = SimpleNamespace(
        description=f"<@{requester_id}> submitted an appeal",
        fields=[],
    )
    message = SimpleNamespace(embeds=[embed])

    assert make_cog()._requester_id_from_help_log_message(message) == requester_id

    ban_info_embed = SimpleNamespace(
        description="A former member requested ban information",
        fields=[SimpleNamespace(name="User", value=f"<@{requester_id}>\n`{requester_id}`")],
    )
    assert make_cog()._requester_id_from_help_log_message(
        SimpleNamespace(embeds=[ban_info_embed])
    ) == requester_id


@pytest.mark.asyncio
async def test_transcript_session_accepts_short_ticket_code(tmp_path):
    db = Database(str(tmp_path / "transcript-flow.db"))
    await db.connect()
    await db.execute(
        "INSERT INTO tickets(guild_id,channel_id,creator_id,created_ts,last_user_activity_ts,status,ticket_id) "
        "VALUES(?,?,?,?,?,?,?)",
        (717, 999, 42, 1, 1, "closed", 3),
    )
    cog = make_cog(db)
    cog._create_transcript_request = AsyncMock(return_value=(True, ""))
    cog._touch_help_cooldown = AsyncMock()
    guild = SimpleNamespace(id=717)
    channel = SimpleNamespace(send=AsyncMock())
    author = SimpleNamespace(id=42)

    await cog._start_help_session(author.id, guild.id, "transcript_ticket", {})
    handled = await cog._handle_help_session_message(
        guild,
        SimpleNamespace(
            author=author,
            content="T3",
            attachments=[],
            channel=channel,
        ),
    )

    assert handled is True
    cog._create_transcript_request.assert_awaited_once_with(
        guild,
        requester_id=author.id,
        ticket_channel_id=999,
        ticket_id=3,
    )
    assert "sent to staff" in channel.send.await_args.args[0]
    assert await cog._get_help_session(author.id, guild.id) is None
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
    cog._help_session_lock = lambda guild_id, user_id: OrderedLock()

    async def locked_handler(*args):
        events.append("handler")

    cog._handle_help_submission_preview_locked = AsyncMock(side_effect=locked_handler)
    interaction = SimpleNamespace(response=Response(), user=SimpleNamespace(id=42))

    await cog.handle_help_submission_preview(interaction, 717, "appeal", "submit")

    assert events == ["defer", "lock", "handler"]


@pytest.mark.asyncio
async def test_failed_transcript_request_keeps_session_for_retry(tmp_path):
    db = Database(str(tmp_path / "transcript-retry.db"))
    await db.connect()
    await db.execute(
        "INSERT INTO tickets(guild_id,channel_id,creator_id,created_ts,last_user_activity_ts,status,ticket_id) "
        "VALUES(?,?,?,?,?,?,?)",
        (717, 999, 42, 1, 1, "closed", 3),
    )
    cog = make_cog(db)
    cog._create_transcript_request = AsyncMock(
        return_value=(False, "The staff channel is temporarily unavailable.")
    )
    cog._touch_help_cooldown = AsyncMock()
    channel = SimpleNamespace(send=AsyncMock())
    author = SimpleNamespace(id=42)
    guild = SimpleNamespace(id=717)
    await cog._start_help_session(author.id, guild.id, "transcript_ticket", {})

    handled = await cog._handle_help_session_message(
        guild,
        SimpleNamespace(
            author=author,
            content="T3",
            attachments=[],
            channel=channel,
        ),
    )

    assert handled is True
    assert (await cog._get_help_session(author.id, guild.id))["stage"] == "transcript_ticket"
    assert isinstance(channel.send.await_args.kwargs["view"], HelpSessionControlView)
    cog._touch_help_cooldown.assert_not_awaited()
    await db.close()


def test_editing_a_submission_removes_old_answers_and_attachments():
    cog = make_cog()
    original = {
        "appeal_type": "ban",
        "former_member": True,
        "punishment": "Old answer",
        "reason": "Old reason",
        "behavior_change": "Old promise",
        "attachments": [{"filename": "private.png", "url": "https://example.test/private"}],
    }

    assert cog._fresh_edit_data("appeal", original) == {
        "appeal_type": "ban",
        "former_member": True,
    }
    assert cog._fresh_edit_data("report", original) == {}


@pytest.mark.asyncio
async def test_recent_support_status_sorts_all_workflow_types_together(tmp_path):
    db = Database(str(tmp_path / "support-status.db"))
    await db.connect()
    await db.execute(
        "INSERT INTO help_submissions("
        "guild_id,kind,user_id,status,created_ts,updated_ts,data_json"
        ") VALUES(?,?,?,?,?,?,?)",
        (717, "appeal", 42, "pending", 10, 10, "{}"),
    )
    await db.execute(
        "INSERT INTO transcript_requests("
        "guild_id,request_message_id,ticket_channel_id,requester_id,status,"
        "created_ts,updated_ts,ticket_id"
        ") VALUES(?,?,?,?,?,?,?,?)",
        (717, 555, 999, 42, "delivery_failed", 20, 30, 3),
    )

    text = await make_cog(db)._recent_help_status_text(717, 42)

    assert text.splitlines()[0].startswith("`TR-T3`")
    assert "Delivery Failed" in text
    assert "`A-1`" in text
    await db.close()


@pytest.mark.asyncio
async def test_transcript_denial_is_not_final_when_requester_dm_fails(
    tmp_path,
    monkeypatch,
):
    requester_id = 1102884420207255653
    db = Database(str(tmp_path / "transcript-denial.db"))
    await db.connect()
    await db.execute(
        "INSERT INTO transcript_requests("
        "guild_id,request_message_id,ticket_channel_id,requester_id,status,"
        "created_ts,updated_ts,ticket_id"
        ") VALUES(?,?,?,?,?,?,?,?)",
        (717, 555, 999, requester_id, "pending", 1, 1, 3),
    )
    cog = make_cog(db)
    cog._resolve_dm_recipient = AsyncMock(side_effect=RuntimeError("temporary DM failure"))
    monkeypatch.setattr(help_module, "log_error", AsyncMock())

    staff = SimpleNamespace(
        id=77,
        roles=[SimpleNamespace(id=123)],
        guild_permissions=SimpleNamespace(manage_guild=False),
    )
    guild = SimpleNamespace(id=717, get_member=lambda _user_id: None)
    embed = discord.Embed()
    embed.add_field(name="Requester", value=f"<@{requester_id}>\n`{requester_id}`")
    request_message = SimpleNamespace(id=555, embeds=[embed], edit=AsyncMock())
    interaction = SimpleNamespace(
        guild=guild,
        user=staff,
        message=request_message,
        response=SimpleNamespace(is_done=lambda: True),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await cog._handle_transcript_request_decision_locked(interaction, approved=False)
    row = await db.fetchone(
        "SELECT status, error_text, reviewed_by FROM transcript_requests "
        "WHERE request_message_id=555"
    )

    assert row["status"] == "pending"
    assert "temporary DM failure" in row["error_text"]
    assert row["reviewed_by"] is None
    assert isinstance(request_message.edit.await_args.kwargs["view"], help_module.TranscriptRequestView)
    assert "not finalized" in interaction.followup.send.await_args.args[0]
    await db.close()


@pytest.mark.asyncio
async def test_successful_transcript_denial_records_reviewer_and_repairs_requester_id(
    tmp_path,
    monkeypatch,
):
    stored_requester_id = 1102884420207255652
    requester_id = 1102884420207255653
    db = Database(str(tmp_path / "transcript-denied.db"))
    await db.connect()
    await db.execute(
        "INSERT INTO transcript_requests("
        "guild_id,request_message_id,ticket_channel_id,requester_id,status,"
        "created_ts,updated_ts,ticket_id"
        ") VALUES(?,?,?,?,?,?,?,?)",
        (717, 555, 999, stored_requester_id, "pending", 1, 1, 3),
    )
    cog = make_cog(db)
    recipient = SimpleNamespace(send=AsyncMock())
    cog._resolve_dm_recipient = AsyncMock(return_value=recipient)
    cog._log_help_action = AsyncMock()
    monkeypatch.setattr(help_module, "log_error", AsyncMock())

    staff = SimpleNamespace(
        id=77,
        roles=[SimpleNamespace(id=123)],
        guild_permissions=SimpleNamespace(manage_guild=False),
    )
    guild = SimpleNamespace(id=717, get_member=lambda _user_id: None)
    embed = discord.Embed()
    embed.add_field(name="Requester", value=f"<@{requester_id}>\n`{requester_id}`")
    request_message = SimpleNamespace(id=555, embeds=[embed], edit=AsyncMock())
    interaction = SimpleNamespace(
        guild=guild,
        user=staff,
        message=request_message,
        response=SimpleNamespace(is_done=lambda: True),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await cog._handle_transcript_request_decision_locked(interaction, approved=False)
    row = await db.fetchone(
        "SELECT status, requester_id, reviewed_by, reviewed_ts, error_text "
        "FROM transcript_requests WHERE request_message_id=555"
    )

    recipient.send.assert_awaited_once()
    assert row["status"] == "denied"
    assert row["requester_id"] == requester_id
    assert row["reviewed_by"] == 77
    assert row["reviewed_ts"] is not None
    assert row["error_text"] is None
    assert request_message.edit.await_args.kwargs["view"] is None
    await db.close()


@pytest.mark.asyncio
async def test_inactivity_scan_discards_prompt_when_activity_resumes(
    tmp_path,
    monkeypatch,
):
    db = Database(str(tmp_path / "ticket-scan.db"))
    await db.connect()
    await db.execute(
        "INSERT INTO tickets("
        "guild_id,channel_id,creator_id,created_ts,last_user_activity_ts,status,ticket_id"
        ") VALUES(?,?,?,?,?,?,?)",
        (717, 999, 42, 1, 1, "open", 3),
    )
    prompt = SimpleNamespace(id=1234, delete=AsyncMock())

    class FakeTextChannel:
        id = 999

        async def send(self, *args, **kwargs):
            await db.execute(
                "UPDATE tickets SET last_user_activity_ts=? WHERE channel_id=?",
                (int(time.time()), self.id),
            )
            return prompt

    monkeypatch.setattr(help_module.discord, "TextChannel", FakeTextChannel)
    channel = FakeTextChannel()
    guild = SimpleNamespace(id=717, get_channel=lambda _channel_id: channel)
    cog = make_cog(db)
    cog.bot.config.data["tickets"]["ticket_inactivity_hours"] = 0.01
    cog.bot.get_guild = lambda guild_id: guild if guild_id == guild.id else None

    await cog._scan_tickets()
    row = await db.fetchone(
        "SELECT status, closing_prompt_message_id FROM tickets WHERE channel_id=999"
    )

    assert row["status"] == "open"
    assert row["closing_prompt_message_id"] is None
    prompt.delete.assert_awaited_once()
    await db.close()


@pytest.mark.asyncio
async def test_inactivity_scan_restores_ticket_if_prompt_verification_fails(
    tmp_path,
    monkeypatch,
):
    db = Database(str(tmp_path / "ticket-scan-verify.db"))
    await db.connect()
    await db.execute(
        "INSERT INTO tickets("
        "guild_id,channel_id,creator_id,created_ts,last_user_activity_ts,status,ticket_id"
        ") VALUES(?,?,?,?,?,?,?)",
        (717, 999, 42, 1, 1, "open", 3),
    )
    prompt = SimpleNamespace(id=1234, delete=AsyncMock())

    class FakeTextChannel:
        id = 999
        send = AsyncMock(return_value=prompt)

    class VerifyFailureDatabase:
        fetchall = db.fetchall
        execute = db.execute

        async def fetchone(self, *args, **kwargs):
            raise RuntimeError("temporary verification read failure")

    monkeypatch.setattr(help_module.discord, "TextChannel", FakeTextChannel)
    channel = FakeTextChannel()
    guild = SimpleNamespace(id=717, get_channel=lambda _channel_id: channel)
    cog = make_cog(VerifyFailureDatabase())
    cog.bot.get_guild = lambda guild_id: guild if guild_id == guild.id else None
    cog._log_background_error = AsyncMock()

    await cog._scan_tickets()
    row = await db.fetchone(
        "SELECT status, closing_prompt_message_id FROM tickets WHERE channel_id=999"
    )

    assert row["status"] == "open"
    assert row["closing_prompt_message_id"] is None
    prompt.delete.assert_awaited_once()
    await db.close()


@pytest.mark.asyncio
async def test_missing_ticket_opening_message_is_recreated(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "ticket-status-repair.db"))
    await db.connect()
    await db.execute(
        "INSERT INTO tickets("
        "guild_id,channel_id,creator_id,created_ts,last_user_activity_ts,status,"
        "ticket_id,status_tag"
        ") VALUES(?,?,?,?,?,?,?,?)",
        (717, 999, 42, 1, 1, "open", 3, "waiting_staff"),
    )
    replacement = SimpleNamespace(id=1234)

    class FakeTextChannel:
        id = 999
        send = AsyncMock(return_value=replacement)

    monkeypatch.setattr(help_module.discord, "TextChannel", FakeTextChannel)
    channel = FakeTextChannel()
    guild = SimpleNamespace(id=717, get_channel=lambda _channel_id: channel)
    cog = make_cog(db)

    await cog.update_ticket_opening_status(guild, channel.id, "waiting_user")
    row = await db.fetchone(
        "SELECT opening_message_id FROM tickets WHERE channel_id=999"
    )

    assert row["opening_message_id"] == replacement.id
    assert "Waiting for user" in channel.send.await_args.args[0]
    await db.close()


@pytest.mark.asyncio
async def test_ticket_close_prompt_never_becomes_public_when_mod_role_is_missing():
    class Response:
        def __init__(self):
            self.done = False

        def is_done(self):
            return self.done

        async def defer(self, **kwargs):
            self.done = True

        send_message = AsyncMock()

    cog = make_cog()
    cog.bot.config.data["roles"]["MOD_ROLE_ID"] = 0
    guild = SimpleNamespace(id=717, get_member=lambda _user_id: None)
    interaction = SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(
            id=42,
            roles=[],
            guild_permissions=SimpleNamespace(manage_guild=False),
        ),
        response=Response(),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await cog.handle_ticket_close_prompt(interaction, confirmed=True)

    assert "Only staff" in interaction.followup.send.await_args.args[0]


@pytest.mark.asyncio
async def test_keeping_ticket_open_resets_inactivity_clock(tmp_path):
    db = Database(str(tmp_path / "ticket-keep-open.db"))
    await db.connect()
    await db.execute(
        "INSERT INTO tickets("
        "guild_id,channel_id,creator_id,created_ts,last_user_activity_ts,status,"
        "ticket_id,closing_prompt_message_id"
        ") VALUES(?,?,?,?,?,?,?,?)",
        (717, 999, 42, 1, 1, "closing_prompted", 3, 1234),
    )

    class Response:
        def __init__(self):
            self.done = False

        def is_done(self):
            return self.done

        async def defer(self, **kwargs):
            self.done = True

    cog = make_cog(db)
    staff = SimpleNamespace(
        id=77,
        roles=[SimpleNamespace(id=123)],
        guild_permissions=SimpleNamespace(manage_guild=False),
    )
    prompt = SimpleNamespace(id=1234, edit=AsyncMock())
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=717, get_member=lambda _user_id: None),
        user=staff,
        response=Response(),
        followup=SimpleNamespace(send=AsyncMock()),
        channel_id=999,
        message=prompt,
    )

    before = int(time.time())
    await cog.handle_ticket_close_prompt(interaction, confirmed=False)
    row = await db.fetchone(
        "SELECT status, last_user_activity_ts, closing_prompt_message_id "
        "FROM tickets WHERE channel_id=999"
    )

    assert row["status"] == "open"
    assert row["last_user_activity_ts"] >= before
    assert row["closing_prompt_message_id"] is None
    assert prompt.edit.await_args.kwargs["view"] is None
    await db.close()


@pytest.mark.asyncio
async def test_active_help_session_owns_dm_before_weekly_workflow(tmp_path):
    db = Database(str(tmp_path / "help-priority.db"))
    await db.connect()
    cog = make_cog(db)
    tracking = SimpleNamespace(user_in_weekly_process=AsyncMock(return_value=True))
    guild = SimpleNamespace(id=717)
    cog.bot.get_guild = lambda guild_id: guild if guild_id == guild.id else None
    cog.bot.get_cog = lambda name: tracking if name == "TrackingCog" else None
    author = SimpleNamespace(id=42, bot=False)
    channel = SimpleNamespace(send=AsyncMock())
    await cog._start_help_session(author.id, guild.id, "report_details", {})

    await cog.on_message(
        SimpleNamespace(
            id=555,
            author=author,
            guild=None,
            channel=channel,
            content="A complete report for staff",
            attachments=[],
        )
    )

    assert (await cog._get_help_session(author.id, guild.id))["stage"] == "preview_report"
    tracking.user_in_weekly_process.assert_not_awaited()
    await db.close()


@pytest.mark.asyncio
async def test_final_support_dm_remains_claimed_after_session_is_cleared(tmp_path):
    db = Database(str(tmp_path / "help-final-message-claim.db"))
    await db.connect()
    cog = make_cog(db)
    guild = SimpleNamespace(id=717)
    cog.bot.get_guild = lambda guild_id: guild if guild_id == guild.id else None
    cog.bot.get_cog = lambda _name: None
    cog._send_dm_dashboard = AsyncMock()
    author = SimpleNamespace(id=42, bot=False)
    channel = SimpleNamespace(send=AsyncMock())
    await cog._start_help_session(author.id, guild.id, "report_details", {})

    await cog.on_message(
        SimpleNamespace(
            id=777,
            author=author,
            guild=None,
            channel=channel,
            content="cancel",
            attachments=[],
        )
    )

    assert await cog._get_help_session(author.id, guild.id) is None
    assert await cog.should_yield_weekly_dm(guild.id, author.id, 777) is True
    await db.close()
