import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from services.backups import run_restore_drill
from services.diagnostics import scan_permission_drift
from services.impact import forecast_engagement
from services.request_reviews import (
    compare_waves,
    normalize_notification_mode,
    rejection_breakdown,
    request_age,
)
from services.request_scheduling import ScheduledOpening, opening_is_due
from services.request_validation import validate_level_id_shape, validate_showcase_url
from utils.config_schema import operations_settings, validate_config
from utils.db import Database
from utils.outbox import DiscordOutbox
from utils.workflows import (
    InvalidWorkflowTransition,
    WorkflowStateMachine,
    begin_workflow_context,
    clear_workflow_context,
    current_correlation_id,
    record_workflow_event,
)


def test_typed_config_validation_and_operation_bounds():
    config = json.loads(Path("config.json").read_text(encoding="utf-8"))
    assert not [issue for issue in validate_config(config) if issue.severity == "error"]

    config["level_requests"]["sent_result_embed"]["description"] = "{unknown_variable}"
    issues = validate_config(config)
    assert any(issue.path == "level_requests.sent_result_embed" for issue in issues)

    config["staff_portal"]["application_forms"]["judge"]["questions"][0]["options"] = []
    config["staff_portal"]["application_review_levels"][0]["level_id"] = "123"
    config["staff_portal"]["application_review_levels"][1]["youtube_url"] = (
        "https://youtu.be/too-short"
    )
    issues = validate_config(config)
    assert any(
        issue.path == "staff_portal.application_forms.judge.questions[0].options"
        for issue in issues
    )
    assert any(
        issue.path == "staff_portal.application_review_levels[0].level_id"
        for issue in issues
    )
    assert any(
        issue.path == "staff_portal.application_review_levels[1].youtube_url"
        for issue in issues
    )

    settings = operations_settings(
        {
            "operations": {
                "supervisor_interval_seconds": 1,
                "outbox_poll_seconds": 999,
                "monthly_report_day": 31,
                "retention_days": {"health_metrics": 0, "workflow_events": 99999},
            }
        }
    )
    assert settings.supervisor_interval_seconds == 15
    assert settings.outbox_poll_seconds == 300
    assert settings.monthly_report_day == 28
    assert settings.retention_days == {"health_metrics": 1, "workflow_events": 3650}


def test_request_service_helpers_cover_sla_filters_and_comparison():
    assert validate_level_id_shape("1234567") == ""
    assert validate_level_id_shape("123456")
    assert validate_showcase_url("https://youtu.be/example") == ""
    assert validate_showcase_url("not a URL")
    assert normalize_notification_mode("BOTH") == "both"
    assert normalize_notification_mode("invalid") == "channel"

    age = request_age(1_000, now_ts=1_000 + 49 * 3600, thresholds=(12, 24, 48))
    assert age.status == "Overdue"
    assert age.label == "2d ago"
    assert rejection_breakdown(
        [{"result": "rejected"}, {"result": "already_rated"}, {"result": "sent"}]
    ) == {"rejected": 1, "already_rated": 1}

    comparison = compare_waves(
        {"wave_id": 4, "total_requests": 12, "reviewed_count": 10, "sent_count": 7},
        {"wave_id": 3, "total_requests": 8, "reviewed_count": 8, "sent_count": 4},
    )
    assert comparison["request_delta"] == "+4"
    assert comparison["sent_rate_delta"] == "+20.0 pp"


def test_scheduling_and_state_machine_are_explicit():
    opening = ScheduledOpening.from_row(
        {
            "id": 9,
            "open_ts": 1_700_000_000,
            "request_limit": 20,
            "close_minutes": 15,
            "request_type": "only_demons",
            "open_message": "Requests are ready",
        }
    )
    assert opening.discord_time() == "<t:1700000000:F> (<t:1700000000:R>)"
    assert opening_is_due(opening.open_ts, now_ts=opening.open_ts)

    machine = WorkflowStateMachine(
        {"pending": ("processing",), "processing": ("done",)}
    )
    machine.require("pending", "processing")
    with pytest.raises(InvalidWorkflowTransition):
        machine.require("pending", "done")

    correlation = begin_workflow_context(prefix="test-command")
    assert correlation.startswith("test-command-")
    assert current_correlation_id() == correlation
    clear_workflow_context()


def test_permission_drift_reports_the_exact_missing_capability():
    class Config:
        def get(self, *path, default=None):
            return 123 if path == ("level_requests", "request_channel") else default

        def get_int(self, *path, default=0):
            return default

        def get_int_list(self, *path, default=None):
            return list(default or [])

    class Channel:
        id = 123
        name = "requests"

        def permissions_for(self, _member):
            return SimpleNamespace(
                view_channel=True,
                send_messages=False,
                embed_links=True,
            )

    channel = Channel()
    guild = SimpleNamespace(
        me=SimpleNamespace(top_role=SimpleNamespace()),
        get_channel=lambda channel_id: channel if channel_id == 123 else None,
        get_thread=lambda _channel_id: None,
        get_role=lambda _role_id: None,
    )

    findings = scan_permission_drift(SimpleNamespace(config=Config()), guild)

    assert len(findings) == 1
    assert findings[0].resource_id == 123
    assert findings[0].missing == ("send_messages",)


def test_forecast_reports_signal_quality_and_anomalies():
    series = [(f"2026-07-{day:02d}", 10 + (day % 4)) for day in range(1, 29)]
    forecast = forecast_engagement(series)
    assert forecast.next_7_days > 0
    assert forecast.daily_average_7d > 0
    assert forecast.confidence in {"low", "medium", "high"}


@pytest.mark.asyncio
async def test_outbox_is_idempotent_and_records_delivery(tmp_path):
    database = Database(str(tmp_path / "outbox.db"))
    await database.connect()

    class Channel:
        def __init__(self):
            self.messages = []

        async def send(self, **kwargs):
            self.messages.append(kwargs)
            return SimpleNamespace(id=321)

    channel = Channel()
    bot = SimpleNamespace(
        db=database,
        get_channel=lambda channel_id: channel if channel_id == 123 else None,
        fetch_channel=None,
    )
    outbox = DiscordOutbox(bot)
    first = await outbox.enqueue(
        "send_channel",
        guild_id=1,
        channel_id=123,
        payload={"content": "durable delivery"},
        idempotency_key="test:delivery",
    )
    second = await outbox.enqueue(
        "send_channel",
        guild_id=1,
        channel_id=123,
        payload={"content": "duplicate should not send"},
        idempotency_key="test:delivery",
    )

    assert first == second
    assert await outbox.process_once() == {"delivered": 1, "retried": 0, "dead": 0}
    assert outbox.last_dead_letter_ids() == ()
    assert await outbox.process_once() == {"delivered": 0, "retried": 0, "dead": 0}
    assert [message["content"] for message in channel.messages] == ["durable delivery"]
    row = await database.fetchone(
        "SELECT status,attempts,delivered_message_id FROM discord_outbox WHERE id=?",
        (first,),
    )
    assert row["status"] == "delivered"
    assert int(row["attempts"]) == 1
    assert int(row["delivered_message_id"]) == 321

    timeline = await database.fetchall(
        "SELECT event FROM workflow_events WHERE workflow_type='discord_outbox' ORDER BY id"
    )
    assert [row["event"] for row in timeline] == ["delivered"]
    await database.close()


@pytest.mark.asyncio
async def test_application_thread_delivery_persists_thread_and_review_link(
    tmp_path, monkeypatch
):
    database = Database(str(tmp_path / "application-thread.db"))
    await database.connect()
    application_id = await database.execute_insert(
        "INSERT INTO staff_applications("
        "guild_id,applicant_id,application_type,status,answers_json,created_ts,updated_ts) "
        "VALUES(1,99,'judge','submitted','{}',1,1)"
    )

    class Thread:
        id = 654

        def __init__(self):
            self.messages = []

        async def send(self, **kwargs):
            self.messages.append(kwargs)
            return SimpleNamespace(id=700 + len(self.messages))

    class Forum:
        def __init__(self):
            self.threads = []
            self.created = 0
            self.create_kwargs = []

        async def create_thread(self, **kwargs):
            self.created += 1
            self.create_kwargs.append(kwargs)
            thread = Thread()
            self.threads.append(thread)
            return SimpleNamespace(thread=thread)

    monkeypatch.setattr(discord, "ForumChannel", Forum)
    forum = Forum()

    async def fetch_channel(_channel_id):
        return forum

    bot = SimpleNamespace(
        db=database,
        get_channel=lambda channel_id: forum if channel_id == 123 else None,
        fetch_channel=fetch_channel,
        get_cog=lambda _name: None,
    )
    outbox = DiscordOutbox(bot)
    bot.outbox = outbox
    await outbox.enqueue(
        "create_application_thread",
        guild_id=1,
        channel_id=123,
        payload={
            "application_id": application_id,
            "submitted_ts": 100,
            "review_url": "https://gdavenue.netlify.app/staff/#team/applications",
            "responses": [
                {
                    "question": "Review Synergy - https://youtu.be/example",
                    "answer": "Detailed feedback",
                }
            ],
        },
        idempotency_key=f"application:{application_id}:thread",
    )

    assert await outbox.process_once() == {"delivered": 1, "retried": 0, "dead": 0}
    saved = await database.fetchone(
        "SELECT review_thread_id FROM staff_applications WHERE id=?", (application_id,)
    )
    assert int(saved["review_thread_id"]) == 654
    assert forum.created == 1
    assert "New reviewer application by <@99>" in forum.create_kwargs[0]["content"]
    assert f"#{application_id}" not in forum.create_kwargs[0]["content"]
    assert "Open this application in the Staff Portal" in forum.create_kwargs[0]["content"]
    assert "https://youtu.be/example" in forum.threads[0].messages[0]["content"]
    await database.close()


@pytest.mark.asyncio
async def test_application_delivery_supports_text_channel_notification_thread(
    tmp_path, monkeypatch
):
    database = Database(str(tmp_path / "application-text-thread.db"))
    await database.connect()
    application_id = await database.execute_insert(
        "INSERT INTO staff_applications("
        "guild_id,applicant_id,application_type,status,answers_json,created_ts,updated_ts) "
        "VALUES(1,99,'judge','submitted','{}',1,1)"
    )

    class Thread:
        id = 987

        def __init__(self):
            self.messages = []

        async def send(self, **kwargs):
            self.messages.append(kwargs)
            return SimpleNamespace(id=1000 + len(self.messages))

    class TextChannel:
        def __init__(self):
            self.threads = []
            self.notifications = []
            self.thread_kwargs = []

        async def send(self, **kwargs):
            self.notifications.append(kwargs)
            return SimpleNamespace(id=800)

        async def create_thread(self, **kwargs):
            self.thread_kwargs.append(kwargs)
            thread = Thread()
            self.threads.append(thread)
            return thread

    monkeypatch.setattr(discord, "TextChannel", TextChannel)
    channel = TextChannel()
    bot = SimpleNamespace(
        db=database,
        get_channel=lambda channel_id: channel if channel_id == 123 else None,
        fetch_channel=AsyncMock(return_value=channel),
        get_cog=lambda _name: None,
    )
    outbox = DiscordOutbox(bot)
    bot.outbox = outbox
    await outbox.enqueue(
        "create_application_thread",
        guild_id=1,
        channel_id=123,
        payload={
            "application_id": application_id,
            "responses": [{"question": "Why?", "answer": "Because"}],
        },
        idempotency_key=f"application:{application_id}:text-thread",
    )

    assert await outbox.process_once() == {
        "delivered": 1,
        "retried": 0,
        "dead": 0,
    }
    assert "New reviewer application by <@99>" in channel.notifications[0]["content"]
    assert f"#{application_id}" not in channel.notifications[0]["content"]
    assert channel.thread_kwargs[0]["message"].id == 800
    assert channel.threads[0].messages[0]["content"].endswith("Because")
    saved = await database.fetchone(
        "SELECT review_thread_id FROM staff_applications WHERE id=?",
        (application_id,),
    )
    assert int(saved["review_thread_id"]) == 987
    await database.close()


@pytest.mark.asyncio
async def test_interview_delivery_creates_ticket_and_durable_dm(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "application-interview.db"))
    await database.connect()
    application_id = await database.execute_insert(
        "INSERT INTO staff_applications("
        "guild_id,applicant_id,application_type,status,answers_json,created_ts,updated_ts) "
        "VALUES(1,99,'judge','interview','{}',1,1)"
    )

    class Category:
        id = 456

    class Member:
        id = 99
        name = "applicant"
        mention = "<@99>"

    class Interview:
        def __init__(self, channel_id, topic):
            self.id = channel_id
            self.mention = f"<#{channel_id}>"
            self.topic = topic
            self.messages = []

        async def send(self, **kwargs):
            self.messages.append(kwargs)
            return SimpleNamespace(id=800 + len(self.messages))

    class Config:
        def get_int(self, *path, default=0):
            return 456 if path == ("tickets", "ticket_category_id") else default

        def get_int_list(self, *_path, default=None):
            return list(default or [])

    class Guild:
        id = 1
        default_role = object()

        def __init__(self):
            self.member = Member()
            self.category = Category()
            self.channels = []

        def get_member(self, user_id):
            return self.member if user_id == 99 else None

        async def fetch_member(self, user_id):
            return self.get_member(user_id)

        def get_channel(self, channel_id):
            if channel_id == 456:
                return self.category
            return next((item for item in self.channels if item.id == channel_id), None)

        def get_role(self, _role_id):
            return None

        async def create_text_channel(self, **kwargs):
            interview = Interview(789 + len(self.channels), kwargs.get("topic", ""))
            self.channels.append(interview)
            return interview

    class User:
        def __init__(self):
            self.messages = []

        async def send(self, **kwargs):
            self.messages.append(kwargs)
            return SimpleNamespace(id=900 + len(self.messages))

    monkeypatch.setattr(discord, "CategoryChannel", Category)
    guild = Guild()
    user = User()

    async def fetch_guild(_guild_id):
        return guild

    async def fetch_user(_user_id):
        return user

    async def fetch_channel(_channel_id):
        return None

    bot = SimpleNamespace(
        db=database,
        config=Config(),
        get_guild=lambda guild_id: guild if guild_id == 1 else None,
        fetch_guild=fetch_guild,
        get_channel=lambda _channel_id: None,
        fetch_channel=fetch_channel,
        get_user=lambda user_id: user if user_id == 99 else None,
        fetch_user=fetch_user,
        get_cog=lambda _name: None,
    )
    outbox = DiscordOutbox(bot)
    bot.outbox = outbox
    await outbox.enqueue(
        "create_interview_ticket",
        guild_id=1,
        user_id=99,
        payload={"application_id": application_id},
        idempotency_key=f"application:{application_id}:interview",
    )

    assert await outbox.process_once() == {"delivered": 1, "retried": 0, "dead": 0}
    assert await outbox.process_once() == {"delivered": 1, "retried": 0, "dead": 0}
    ticket = await database.fetchone("SELECT * FROM tickets WHERE channel_id=789")
    application = await database.fetchone(
        "SELECT interview_ticket_channel_id FROM staff_applications WHERE id=?",
        (application_id,),
    )
    assert ticket["status_tag"] == "waiting_staff"
    assert int(application["interview_ticket_channel_id"]) == 789
    assert "<#789>" in user.messages[0]["content"]

    await outbox.enqueue(
        "create_interview_ticket",
        guild_id=1,
        user_id=99,
        payload={
            "application_id": application_id,
            "interview_run_id": "application-repeat-test",
            "repeat_interview": True,
        },
        correlation_id="application-repeat-test",
        idempotency_key="application-repeat-test:interview-ticket",
    )
    assert await outbox.process_once() == {"delivered": 1, "retried": 0, "dead": 0}
    assert await outbox.process_once() == {"delivered": 1, "retried": 0, "dead": 0}
    repeated = await database.fetchone("SELECT * FROM tickets WHERE channel_id=790")
    application = await database.fetchone(
        "SELECT interview_ticket_channel_id FROM staff_applications WHERE id=?",
        (application_id,),
    )
    assert repeated["status_tag"] == "waiting_staff"
    assert int(application["interview_ticket_channel_id"]) == 790
    assert guild.channels[1].topic.endswith(":application-repeat-test")
    assert "another interview" in user.messages[1]["content"]
    await database.close()


@pytest.mark.asyncio
async def test_workflow_timeline_query_timing_and_restore_drill_persist(tmp_path):
    database = Database(str(tmp_path / "operations.db"))
    await database.connect()
    correlation = await record_workflow_event(
        database,
        workflow_type="request",
        entity_id="wave-2",
        event="opened",
        guild_id=717,
    )
    assert correlation.startswith("request-")
    assert "execute" in database.query_timing_snapshot()

    drill = await run_restore_drill(database, guild_id=717, trigger="test")
    assert drill["status"] == "passed"
    assert drill["size_bytes"] > 0
    row = await database.fetchone(
        "SELECT status,missing_tables_json FROM restore_drills"
    )
    assert row["status"] == "passed"
    assert json.loads(row["missing_tables_json"]) == []
    await database.close()
