import asyncio
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cogs.PrioritySystem import PrioritySystemCog
from cogs.RequestLevels import RequestLevelsCog
from cogs.RequestLevels import ReviewModal
from services.priority_system import PrioritySystemService
from utils.config import Config
from utils.db import Database
from utils.keepalive import get_public_level_payload, set_public_level_data
from utils.priority_system import (
    PPS_SEND_TYPES,
    creator_opportunity_component,
    prestige_component,
    public_lifecycle_state,
    public_priority_band,
    priority_settings,
    score_components,
    waiting_component,
)
from utils.views import (
    CID_LEVEL_REQUEST_OTHER,
    CID_LEVEL_REQUEST_PPS_SEND_TYPE,
    CID_LEVEL_REQUEST_RECHECK,
    CID_LEVEL_REQUEST_REJECT,
    CID_LEVEL_REQUEST_SEND,
    LevelRequestPPSReviewView,
    LevelRequestReviewView,
    configure_pps_send_type_emojis,
    request_review_view,
)


ROOT = Path(__file__).resolve().parents[1]
GUILD_ID = 717
OWNER_ID = 11


@pytest.mark.parametrize(
    ("position", "total", "expected"),
    [
        (1, 1, "top_priority"),
        (1, 2, "top_priority"),
        (2, 2, "lower_priority"),
        (1, 3, "top_priority"),
        (2, 3, "standard_priority"),
        (3, 3, "lower_priority"),
        (1, 5, "top_priority"),
        (2, 5, "standard_priority"),
        (4, 5, "lower_priority"),
        (5, 5, "lower_priority"),
        (1, 10, "top_priority"),
        (3, 10, "high_priority"),
        (7, 10, "standard_priority"),
        (8, 10, "lower_priority"),
        (10, 100, "top_priority"),
        (30, 100, "high_priority"),
        (70, 100, "standard_priority"),
        (71, 100, "lower_priority"),
    ],
)
def test_public_priority_band_boundaries(position, total, expected):
    assert public_priority_band(position, total) == expected


@pytest.mark.parametrize(
    ("position", "total"),
    [(None, 10), (1, None), (0, 10), (-1, 10), (11, 10), (1, 0), (True, 1)],
)
def test_public_priority_band_rejects_invalid_or_inactive_ranks(position, total):
    assert public_priority_band(position, total) is None


@pytest.mark.parametrize(
    ("state", "submitted", "rated", "window_result", "completed", "outreach", "outcome"),
    [
        ("queued", None, None, None, None, "queued_for_outreach", "unknown"),
        ("in_cycle", None, None, None, None, "outreach_in_progress", "unknown"),
        ("awaiting_outcome", 100, None, None, None, "reached_moderator", "awaiting_outcome"),
        ("awaiting_outcome", 100, None, 0, 200, "reached_moderator", "not_observed_rated_within_window"),
        ("rated", 100, 150, 1, 200, "outreach_complete", "rated"),
        ("withdrawn", None, None, None, None, "withdrawn", "unknown"),
        ("invalid", None, None, None, None, "level_unavailable", "unknown"),
        ("unexpected", None, None, None, None, "unknown", "unknown"),
    ],
)
def test_public_lifecycle_state_keeps_outreach_and_outcome_separate(
    state,
    submitted,
    rated,
    window_result,
    completed,
    outreach,
    outcome,
):
    result = public_lifecycle_state(
        state,
        submitted_to_mod_at=submitted,
        rated_observed_at=rated,
        rated_within_window=window_result,
        outcome_window_completed_at=completed,
    )

    assert result["public_outreach_state"] == outreach
    assert result["public_outcome_state"] == outcome


class PriorityConfig:
    def __init__(self):
        self.data = {
            "guild": {"allowed_guild_id": GUILD_ID},
            "impact": {"allowed_user_ids": [OWNER_ID]},
            "priority_system": {
                "new_wave_version": "pps_v1",
                "model_version": "pps_v1",
                "prestige_base": 1.8,
                "prestige_x": {
                    "rate": 0,
                    "feature": 1.25,
                    "epic": 2.5,
                    "legendary": 3.75,
                    "mythic": 5,
                },
                "creator_opportunity": {
                    "numerator": 0.25,
                    "slope": 0.06,
                    "offset": 0.01,
                    "zero_from_cp": 4,
                },
                "waiting": {
                    "multiplier": 1.5,
                    "exponent": 1.5,
                    "score_cap_cycles": 4,
                },
                "cp_refresh_hours": 24,
                "level_refresh_hours": 6,
                "outcome_window_days": 30,
                "maintenance_interval_seconds": 300,
                "maintenance_batch_size": 5,
                "failure_retry_minutes": 15,
            },
        }

    def get_int(self, *path, default=0):
        current = self.data
        for key in path:
            if not isinstance(current, dict) or key not in current:
                return default
            current = current[key]
        return int(current)

    def get_int_list(self, *path, default=None):
        current = self.data
        for key in path:
            if not isinstance(current, dict) or key not in current:
                return list(default or [])
            current = current[key]
        return [int(value) for value in current]


def make_bot(db, *, config=None, cogs=None):
    cogs = cogs or {}
    return SimpleNamespace(
        db=db,
        config=config or PriorityConfig(),
        get_cog=lambda name: cogs.get(name),
    )


async def insert_queue(
    db,
    *,
    user_id,
    message_id,
    level_id,
    send_type="epic",
    priority=3.346916,
    complete=1,
    waiting=0,
    state="queued",
    queued_ts=100,
):
    settings = priority_settings(PriorityConfig().data)
    score = score_components(
        send_type,
        4 if complete else None,
        waiting,
        settings,
    )
    return await db.execute_insert(
        "INSERT INTO level_outreach_queue("
        "guild_id,wave_id,requester_id,request_message_id,level_id,send_type,queued_ts,"
        "prestige_t,prestige_component_f,current_creator_points,creator_component_g,"
        "waiting_cycles,waiting_component_h,priority_points,priority_complete,model_version,"
        "queue_state,correlation_id,updated_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            GUILD_ID,
            2,
            user_id,
            message_id,
            level_id,
            send_type,
            queued_ts,
            score["prestige_t"],
            score["prestige_component_f"],
            4 if complete else None,
            score["creator_component_g"],
            waiting,
            score["waiting_component_h"],
            priority if complete else None,
            complete,
            "pps_v1",
            state,
            f"queue-{message_id}",
            queued_ts,
        ),
    )


def test_pps_formula_matches_v1_and_unknown_cp_stays_unknown():
    settings = priority_settings(PriorityConfig().data)
    expected_f = {
        "rate": 0.00,
        "feature": 1.09,
        "epic": 3.35,
        "legendary": 8.06,
        "mythic": 17.90,
    }
    for send_type, expected in expected_f.items():
        _, value = prestige_component(send_type, settings)
        assert value == pytest.approx(expected, abs=0.01)

    expected_g = {0: 3.22, 1: 1.27, 2: 0.65, 3: 0.27, 4: 0, 20: 0}
    for cp, expected in expected_g.items():
        assert creator_opportunity_component(cp, settings) == pytest.approx(
            expected, abs=0.01
        )
    assert creator_opportunity_component(None, settings) is None

    expected_h = {0: 0, 1: 1.5, 2: 4.24, 3: 7.79, 4: 12, 8: 12}
    for waiting, expected in expected_h.items():
        assert waiting_component(waiting, settings) == pytest.approx(expected, abs=0.01)

    incomplete = score_components("epic", None, 2, settings)
    assert incomplete["creator_component_g"] is None
    assert incomplete["priority_points"] is None
    assert incomplete["priority_complete"] == 0


@pytest.mark.asyncio
async def test_legacy_and_pps_views_have_stable_distinct_send_controls():
    legacy = LevelRequestReviewView()
    pps = LevelRequestPPSReviewView()
    legacy_ids = {child.custom_id for child in legacy.children}
    pps_ids = {child.custom_id for child in pps.children}
    assert CID_LEVEL_REQUEST_SEND in legacy_ids
    assert CID_LEVEL_REQUEST_SEND not in pps_ids
    assert CID_LEVEL_REQUEST_PPS_SEND_TYPE in pps_ids
    assert {
        CID_LEVEL_REQUEST_REJECT,
        CID_LEVEL_REQUEST_OTHER,
        CID_LEVEL_REQUEST_RECHECK,
    } <= pps_ids
    send_select = next(
        child for child in pps.children if child.custom_id == CID_LEVEL_REQUEST_PPS_SEND_TYPE
    )
    assert tuple(option.value for option in send_select.options) == PPS_SEND_TYPES
    assert type(request_review_view("legacy")) is LevelRequestReviewView
    assert type(request_review_view("pps_v1")) is LevelRequestPPSReviewView
    assert all(child.disabled for child in request_review_view("pps_v1", disabled=True).children)


@pytest.mark.asyncio
async def test_pps_send_menu_uses_configured_application_emoji_ids():
    configured = {
        "rate": {"name": "pps_rate", "id": "1550265958688489472"},
        "feature": {"name": "pps_feature", "id": "1550265953588224102"},
        "epic": {"name": "pps_epic", "id": "1550265952333996052"},
        "legendary": {"name": "pps_legendary", "id": "1550265955026993283"},
        "mythic": {"name": "pps_mythic", "id": "1550265957463621773"},
    }
    configure_pps_send_type_emojis(configured)
    try:
        view = LevelRequestPPSReviewView()
        send_select = next(
            child
            for child in view.children
            if child.custom_id == CID_LEVEL_REQUEST_PPS_SEND_TYPE
        )
        by_value = {option.value: option for option in send_select.options}
        for send_type, expected in configured.items():
            assert by_value[send_type].emoji.id == int(expected["id"])
            assert by_value[send_type].emoji.name == expected["name"]

        disabled = LevelRequestPPSReviewView(disabled=True)
        disabled_select = next(
            child
            for child in disabled.children
            if child.custom_id == CID_LEVEL_REQUEST_PPS_SEND_TYPE
        )
        assert disabled_select.disabled is True
        assert all(option.emoji is not None for option in disabled_select.options)

        configure_pps_send_type_emojis(
            {"rate": {"name": "pps_rate", "id": "invalid"}}
        )
        fallback = LevelRequestPPSReviewView()
        fallback_select = next(
            child
            for child in fallback.children
            if child.custom_id == CID_LEVEL_REQUEST_PPS_SEND_TYPE
        )
        fallback_by_value = {option.value: option for option in fallback_select.options}
        assert fallback_by_value["rate"].emoji is None
    finally:
        configure_pps_send_type_emojis({})


@pytest.mark.asyncio
async def test_pps_select_opens_review_modal_before_database_work():
    cog = object.__new__(RequestLevelsCog)
    cog._cached_interaction_member = lambda _interaction: SimpleNamespace()
    cog._has_reviewer_role = lambda _member: True
    response = SimpleNamespace(send_modal=AsyncMock(), send_message=AsyncMock())
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=GUILD_ID),
        message=SimpleNamespace(id=456),
        user=SimpleNamespace(id=88),
        response=response,
    )
    await cog.handle_pps_send_type(interaction, "legendary")
    response.send_modal.assert_awaited_once()
    modal = response.send_modal.call_args.args[0]
    assert isinstance(modal, ReviewModal)
    assert modal.message_id == 456
    assert modal.result_key == "sent"
    assert modal.send_type == "legendary"


@pytest.mark.asyncio
async def test_schema_v7_upgrade_marks_existing_state_and_submissions_legacy(tmp_path):
    path = tmp_path / "legacy-v7.db"
    db = Database(str(path))
    await db.connect()
    await db.execute(
        "INSERT INTO level_request_state(guild_id,state,wave_id,submitted_count,review_system_version) "
        "VALUES(?,?,?,?,?)",
        (GUILD_ID, "open", 9, 1, "legacy"),
    )
    await db.execute(
        "INSERT INTO level_request_submissions(guild_id,wave_id,user_id,level_id,status,created_ts,review_system_version) "
        "VALUES(?,?,?,?,?,?,?)",
        (GUILD_ID, 9, 22, "111111111", "pending", 1, "legacy"),
    )
    await db.close()

    with closing(sqlite3.connect(path)) as connection:
        connection.execute("DROP TABLE level_outreach_level_snapshots")
        connection.execute("DROP TABLE level_outreach_cp_snapshots")
        connection.execute("DROP TABLE level_outreach_attempts")
        connection.execute("DROP TABLE level_outreach_cycle_entries")
        connection.execute("DROP TABLE level_outreach_cycles")
        connection.execute("DROP TABLE level_outreach_queue")
        connection.execute(
            "ALTER TABLE level_request_submissions DROP COLUMN send_type"
        )
        connection.execute(
            "ALTER TABLE level_request_submissions DROP COLUMN review_system_version"
        )
        connection.execute(
            "ALTER TABLE level_request_state DROP COLUMN review_system_version"
        )
        connection.execute(
            "UPDATE schema_metadata SET schema_version=7 WHERE component='database'"
        )
        connection.commit()

    upgraded = Database(str(path))
    await upgraded.connect()
    state = await upgraded.fetchone(
        "SELECT wave_id,review_system_version FROM level_request_state WHERE guild_id=?",
        (GUILD_ID,),
    )
    request = await upgraded.fetchone(
        "SELECT wave_id,review_system_version,send_type FROM level_request_submissions WHERE guild_id=?",
        (GUILD_ID,),
    )
    assert dict(state) == {"wave_id": 9, "review_system_version": "legacy"}
    assert dict(request) == {
        "wave_id": 9,
        "review_system_version": "legacy",
        "send_type": None,
    }
    cog = object.__new__(RequestLevelsCog)
    cog.bot = make_bot(upgraded)
    assert cog._review_system_version(state) == "legacy"
    assert int(
        (
            await upgraded.fetchone(
                "SELECT schema_version FROM schema_metadata WHERE component='database'"
            )
        )["schema_version"]
    ) == 11
    assert not await upgraded.fetchall("SELECT * FROM level_outreach_queue")
    await upgraded.close()


@pytest.mark.asyncio
async def test_new_wave_is_pps_but_existing_wave_rows_remain_legacy(tmp_path):
    db = Database(str(tmp_path / "rollout.db"))
    await db.connect()
    await db.execute(
        "INSERT INTO level_request_state(guild_id,state,wave_id,submitted_count,review_system_version) "
        "VALUES(?,?,?,?,?)",
        (GUILD_ID, "closed", 4, 1, "legacy"),
    )
    await db.execute(
        "INSERT INTO level_request_submissions(guild_id,wave_id,user_id,level_id,status,created_ts,review_system_version) "
        "VALUES(?,?,?,?,?,?,?)",
        (GUILD_ID, 4, 22, "111111111", "pending", 1, "legacy"),
    )
    bot = make_bot(db)
    cog = object.__new__(RequestLevelsCog)
    cog.bot = bot
    cog._state_lock = asyncio.Lock()
    cog._submit_lock = asyncio.Lock()
    cog._configured_channel = AsyncMock(return_value=SimpleNamespace(id=123))
    cog.refresh_or_create_request_button = AsyncMock(return_value=SimpleNamespace(id=456))
    cog.update_wave_summary = AsyncMock()
    cog._send_open_announcement = AsyncMock()

    wave_id, _ = await cog._open_requests_now(
        SimpleNamespace(id=GUILD_ID), None, None, "", None
    )
    state = await db.fetchone(
        "SELECT wave_id,review_system_version FROM level_request_state WHERE guild_id=?",
        (GUILD_ID,),
    )
    old = await db.fetchone(
        "SELECT review_system_version FROM level_request_submissions WHERE wave_id=4"
    )
    assert wave_id == 5
    assert dict(state) == {"wave_id": 5, "review_system_version": "pps_v1"}
    assert old["review_system_version"] == "legacy"
    assert not await db.fetchall("SELECT * FROM level_outreach_queue")
    await db.close()


@pytest.mark.asyncio
async def test_scheduled_opening_creates_a_pps_wave(tmp_path):
    db = Database(str(tmp_path / "scheduled.db"))
    await db.connect()
    await db.execute(
        "INSERT INTO level_request_state(guild_id,state,wave_id,submitted_count,review_system_version) "
        "VALUES(?,?,?,?,?)",
        (GUILD_ID, "closed", 7, 0, "legacy"),
    )
    opening_id = await db.execute_insert(
        "INSERT INTO level_request_scheduled_openings(guild_id,open_ts,created_by,created_ts,status) "
        "VALUES(?,?,?,?,?)",
        (GUILD_ID, 1, OWNER_ID, 1, "pending"),
    )
    cog = object.__new__(RequestLevelsCog)
    cog.bot = make_bot(db)
    cog._state_lock = asyncio.Lock()
    cog._submit_lock = asyncio.Lock()
    cog._configured_channel = AsyncMock(return_value=SimpleNamespace(id=123))
    cog.refresh_or_create_request_button = AsyncMock(return_value=SimpleNamespace(id=456))
    cog.update_wave_summary = AsyncMock()
    cog._send_open_announcement = AsyncMock()

    wave_id, _ = await cog._open_requests_now(
        SimpleNamespace(id=GUILD_ID),
        None,
        None,
        "",
        None,
        scheduled_opening_id=opening_id,
    )
    opening = await db.fetchone(
        "SELECT status,opened_wave_id FROM level_request_scheduled_openings WHERE id=?",
        (opening_id,),
    )
    state = await db.fetchone(
        "SELECT review_system_version FROM level_request_state WHERE guild_id=?",
        (GUILD_ID,),
    )
    assert wave_id == 8
    assert dict(opening) == {"status": "opened", "opened_wave_id": 8}
    assert state["review_system_version"] == "pps_v1"
    await db.close()


@pytest.mark.asyncio
async def test_pps_review_finalization_creates_exactly_one_queue_entry(tmp_path):
    db = Database(str(tmp_path / "review.db"))
    await db.connect()
    config = Config(str(ROOT / "config.json"))
    message = SimpleNamespace(id=555, edit=AsyncMock())
    channel = SimpleNamespace(id=321, fetch_message=AsyncMock(return_value=message))
    bot = SimpleNamespace(
        db=db,
        config=config,
        get_channel=lambda _channel_id: channel,
        get_cog=lambda _name: None,
    )
    cog = object.__new__(RequestLevelsCog)
    cog.bot = bot
    cog._review_lock = asyncio.Lock()
    cog._cached_interaction_member = lambda _interaction: SimpleNamespace()
    cog._has_reviewer_role = lambda _member: True
    cog._review_target_channel = AsyncMock(return_value=channel)
    cog._get_state = AsyncMock(return_value={"wave_id": 2, "state": "open"})
    cog.update_wave_summary = AsyncMock()
    cog._reply_ephemeral = AsyncMock()
    await db.execute(
        "INSERT INTO level_request_submissions("
        "guild_id,wave_id,user_id,level_id,request_message_id,status,created_ts,data_json,"
        "review_system_version,correlation_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            GUILD_ID,
            2,
            33,
            "123456789",
            555,
            "pending",
            1,
            json.dumps({"level_id": "123456789", "level_name": "Example"}),
            "pps_v1",
            "request-555",
        ),
    )
    response = SimpleNamespace(is_done=lambda: False, defer=AsyncMock())
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=GUILD_ID),
        message=message,
        user=SimpleNamespace(id=88),
        response=response,
    )

    await cog._finalize_review(
        interaction, 555, "sent", "Strong candidate", send_type="epic"
    )
    saved = await db.fetchone(
        "SELECT status,result,send_type,reviewed_by FROM level_request_submissions WHERE request_message_id=555"
    )
    queue = await db.fetchall("SELECT * FROM level_outreach_queue")
    assert dict(saved) == {
        "status": "reviewed",
        "result": "sent",
        "send_type": "epic",
        "reviewed_by": 88,
    }
    assert len(queue) == 1
    assert queue[0]["priority_points"] is None
    assert queue[0]["priority_complete"] == 0
    assert queue[0]["prestige_component_f"] == pytest.approx(3.35, abs=0.01)
    outbox_payloads = [
        json.loads(row["payload_json"])
        for row in await db.fetchall("SELECT payload_json FROM discord_outbox")
    ]
    serialized_payloads = json.dumps(outbox_payloads)
    assert "Your level was recommended for Epic" in serialized_payloads
    assert "epic-worthy" in serialized_payloads
    assert "https://gdavenue.netlify.app/level/123456789" in serialized_payloads
    reviewed_embed = message.edit.call_args.kwargs["embed"]
    outreach_field = next(
        field for field in reviewed_embed.fields if field.name == "Outreach queue"
    )
    assert "Queued for outreach" in outreach_field.value
    assert "no moderator contact" not in outreach_field.value

    await cog._finalize_review(
        interaction, 555, "sent", "Duplicate", send_type="mythic"
    )
    assert int((await db.fetchone("SELECT COUNT(*) AS c FROM level_outreach_queue"))["c"]) == 1
    assert int((await db.fetchone("SELECT COUNT(*) AS c FROM discord_outbox"))["c"]) == 2
    await db.close()


@pytest.mark.asyncio
async def test_concurrent_pps_reviewers_cannot_finalize_or_notify_twice(tmp_path):
    db = Database(str(tmp_path / "concurrent-review.db"))
    await db.connect()
    config = Config(str(ROOT / "config.json"))
    message = SimpleNamespace(id=556, edit=AsyncMock())
    channel = SimpleNamespace(id=321, fetch_message=AsyncMock(return_value=message))
    bot = SimpleNamespace(
        db=db,
        config=config,
        get_channel=lambda _channel_id: channel,
        get_cog=lambda _name: None,
    )

    def make_cog():
        cog = object.__new__(RequestLevelsCog)
        cog.bot = bot
        cog._review_lock = asyncio.Lock()
        cog._cached_interaction_member = lambda _interaction: SimpleNamespace()
        cog._has_reviewer_role = lambda _member: True
        cog._review_target_channel = AsyncMock(return_value=channel)
        cog._get_state = AsyncMock(return_value={"wave_id": 2, "state": "open"})
        cog.update_wave_summary = AsyncMock()
        cog._reply_ephemeral = AsyncMock()
        return cog

    await db.execute(
        "INSERT INTO level_request_submissions("
        "guild_id,wave_id,user_id,level_id,request_message_id,status,created_ts,data_json,"
        "review_system_version,correlation_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            GUILD_ID,
            2,
            34,
            "223456789",
            556,
            "pending",
            1,
            json.dumps({"level_id": "223456789", "level_name": "Race"}),
            "pps_v1",
            "request-556",
        ),
    )

    def interaction(user_id):
        return SimpleNamespace(
            guild=SimpleNamespace(id=GUILD_ID),
            message=message,
            user=SimpleNamespace(id=user_id),
            response=SimpleNamespace(is_done=lambda: False, defer=AsyncMock()),
        )

    first_cog = make_cog()
    second_cog = make_cog()
    await asyncio.gather(
        first_cog._finalize_review(
            interaction(88), 556, "sent", "First", send_type="epic"
        ),
        second_cog._finalize_review(
            interaction(99), 556, "sent", "Second", send_type="mythic"
        ),
    )
    saved = await db.fetchone(
        "SELECT result,send_type,reviewed_by FROM level_request_submissions "
        "WHERE request_message_id=556"
    )
    assert saved["result"] == "sent"
    assert (saved["send_type"], saved["reviewed_by"]) in {
        ("epic", 88),
        ("mythic", 99),
    }
    assert int((await db.fetchone("SELECT COUNT(*) AS c FROM level_outreach_queue"))["c"]) == 1
    assert int((await db.fetchone("SELECT COUNT(*) AS c FROM discord_outbox"))["c"]) == 2
    assert int(
        (
            await db.fetchone(
                "SELECT COUNT(*) AS c FROM workflow_events WHERE event='reviewed'"
            )
        )["c"]
    ) == 1
    await db.close()


@pytest.mark.asyncio
async def test_legacy_and_rejected_requests_never_enter_pps_queue(tmp_path):
    db = Database(str(tmp_path / "no-retroactive.db"))
    await db.connect()
    service = PrioritySystemService(make_bot(db))
    rows = [
        {
            "guild_id": GUILD_ID,
            "wave_id": 1,
            "user_id": 10,
            "request_message_id": 100,
            "level_id": "111111111",
            "review_system_version": "legacy",
            "status": "reviewed",
            "result": "sent",
            "send_type": None,
            "reviewed_ts": 1,
            "correlation_id": "legacy",
        },
        {
            "guild_id": GUILD_ID,
            "wave_id": 2,
            "user_id": 11,
            "request_message_id": 101,
            "level_id": "222222222",
            "review_system_version": "pps_v1",
            "status": "reviewed",
            "result": "rejected",
            "send_type": None,
            "reviewed_ts": 2,
            "correlation_id": "rejected",
        },
    ]
    assert not await service.ensure_queue_for_submission(rows[0])
    assert not await service.ensure_queue_for_submission(rows[1])
    assert not await db.fetchall("SELECT * FROM level_outreach_queue")
    await db.close()


@pytest.mark.asyncio
async def test_public_level_cache_rebuilds_from_durable_queue_without_private_data(tmp_path):
    db = Database(str(tmp_path / "public-level-cache.db"))
    await db.connect()
    try:
        await db.execute(
            "INSERT INTO level_request_submissions("
            "guild_id,wave_id,user_id,level_id,request_message_id,status,created_ts,data_json,"
            "review_system_version,result,reviewed_by,reviewed_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                GUILD_ID,
                2,
                33,
                "111111111",
                700,
                "reviewed",
                100,
                json.dumps({"level_name": "Durable Example", "notes": "private"}),
                "pps_v1",
                "sent",
                88,
                200,
            ),
        )
        queue_id = await insert_queue(
            db,
            user_id=33,
            message_id=700,
            level_id="111111111",
            send_type="mythic",
            queued_ts=200,
        )
        cog = PrioritySystemCog(make_bot(db))

        assert await cog.refresh_public_level_cache() == 1
        payload = get_public_level_payload("111111111")
        assert payload["level_name"] == "Durable Example"
        assert payload["recommendation_type"] == "mythic"
        assert payload["public_priority_band"] == "top_priority"
        assert payload["public_queue_state"] == "queued"
        assert payload["public_outreach_state"] == "queued_for_outreach"
        assert payload["public_outcome_state"] == "unknown"
        assert "queue_position" not in payload
        assert "active_queue_total" not in payload
        assert "requester_id" not in payload
        assert "reviewed_by" not in payload
        assert "notes" not in payload
        assert queue_id > 0
    finally:
        set_public_level_data([])
        await db.close()


@pytest.mark.asyncio
async def test_cp_zero_completes_score_but_failed_cp_stays_null(tmp_path):
    db = Database(str(tmp_path / "cp.db"))
    await db.connect()
    queue_id = await insert_queue(
        db,
        user_id=21,
        message_id=201,
        level_id="111111111",
        complete=0,
    )
    service = PrioritySystemService(make_bot(db))
    row = await db.fetchone("SELECT * FROM level_outreach_queue WHERE id=?", (queue_id,))
    await service._save_creator_snapshot(
        row,
        {
            "checked_ts": 1000,
            "creator_points": None,
            "status": "network_error",
            "error": "timeout",
        },
    )
    failed = await db.fetchone(
        "SELECT current_creator_points,creator_component_g,priority_points,priority_complete "
        "FROM level_outreach_queue WHERE id=?",
        (queue_id,),
    )
    assert dict(failed) == {
        "current_creator_points": None,
        "creator_component_g": None,
        "priority_points": None,
        "priority_complete": 0,
    }

    row = await db.fetchone("SELECT * FROM level_outreach_queue WHERE id=?", (queue_id,))
    await service._save_creator_snapshot(
        row,
        {"checked_ts": 2000, "creator_points": 0, "status": "ok", "name": "Creator"},
    )
    complete = await db.fetchone(
        "SELECT creator_points_at_recommendation,current_creator_points,creator_component_g,"
        "priority_points,priority_complete FROM level_outreach_queue WHERE id=?",
        (queue_id,),
    )
    assert complete["creator_points_at_recommendation"] == 0
    assert complete["current_creator_points"] == 0
    assert complete["creator_component_g"] == pytest.approx(3.22, abs=0.01)
    assert complete["priority_points"] == pytest.approx(6.57, abs=0.02)
    assert complete["priority_complete"] == 1

    row = await db.fetchone("SELECT * FROM level_outreach_queue WHERE id=?", (queue_id,))
    await service._save_creator_snapshot(
        row,
        {"checked_ts": 3000, "creator_points": 3, "status": "ok", "name": "Creator"},
    )
    refreshed = await db.fetchone(
        "SELECT creator_points_at_recommendation,current_creator_points,creator_component_g,"
        "priority_points FROM level_outreach_queue WHERE id=?",
        (queue_id,),
    )
    assert refreshed["creator_points_at_recommendation"] == 0
    assert refreshed["current_creator_points"] == 3
    assert refreshed["creator_component_g"] == pytest.approx(0.27, abs=0.01)
    assert refreshed["priority_points"] == pytest.approx(3.62, abs=0.02)
    await db.close()


@pytest.mark.asyncio
async def test_outreach_idempotency_key_cannot_mutate_a_different_entry(tmp_path):
    db = Database(str(tmp_path / "attempt-idempotency.db"))
    await db.connect()
    service = PrioritySystemService(make_bot(db))
    first = await insert_queue(
        db, user_id=1, message_id=301, level_id="111111111", priority=4
    )
    second = await insert_queue(
        db, user_id=2, message_id=302, level_id="222222222", priority=3
    )
    cycle = await service.start_cycle(GUILD_ID, OWNER_ID)
    await service.record_attempt(
        GUILD_ID,
        int(cycle["id"]),
        first,
        OWNER_ID,
        status="attempted",
        route_type="direct",
        idempotency_key="same-interaction",
    )
    with pytest.raises(ValueError, match="already used"):
        await service.record_attempt(
            GUILD_ID,
            int(cycle["id"]),
            second,
            OWNER_ID,
            status="submitted_to_mod",
            route_type="stream",
            idempotency_key="same-interaction",
        )
    second_entry = await db.fetchone(
        "SELECT selected,submitted_to_mod FROM level_outreach_cycle_entries "
        "WHERE cycle_id=? AND queue_id=?",
        (int(cycle["id"]), second),
    )
    second_queue = await db.fetchone(
        "SELECT queue_state,submitted_to_mod_ts FROM level_outreach_queue WHERE id=?",
        (second,),
    )
    assert dict(second_entry) == {"selected": 0, "submitted_to_mod": 0}
    assert dict(second_queue) == {
        "queue_state": "in_cycle",
        "submitted_to_mod_ts": None,
    }
    await db.close()


@pytest.mark.asyncio
async def test_queue_order_is_deterministic_and_excludes_awaiting_outcome(tmp_path):
    db = Database(str(tmp_path / "rank.db"))
    await db.connect()
    first = await insert_queue(
        db, user_id=1, message_id=1, level_id="111111111", priority=5, waiting=1, queued_ts=20
    )
    second = await insert_queue(
        db, user_id=2, message_id=2, level_id="222222222", priority=5, waiting=2, queued_ts=30
    )
    third = await insert_queue(
        db, user_id=3, message_id=3, level_id="333333333", priority=5, waiting=2, queued_ts=10
    )
    incomplete = await insert_queue(
        db, user_id=4, message_id=4, level_id="444444444", complete=0, queued_ts=1
    )
    await db.execute(
        "UPDATE level_outreach_queue SET queue_state='awaiting_outcome' WHERE id=?",
        (first,),
    )
    rows, total = await PrioritySystemService(make_bot(db)).queue_rows(GUILD_ID)
    assert total == 3
    assert [int(row["id"]) for row in rows] == [third, second, incomplete]
    await db.close()


@pytest.mark.asyncio
async def test_successful_cycle_ages_only_eligible_unsubmitted_start_candidates(tmp_path):
    db = Database(str(tmp_path / "cycles.db"))
    await db.connect()
    service = PrioritySystemService(make_bot(db))
    submitted = await insert_queue(
        db, user_id=1, message_id=11, level_id="111111111", priority=4
    )
    waiting = await insert_queue(
        db, user_id=2, message_id=12, level_id="222222222", priority=3
    )
    cycle = await service.start_cycle(GUILD_ID, OWNER_ID, "first round")
    late = await insert_queue(
        db, user_id=3, message_id=13, level_id="333333333", priority=2
    )
    await service.record_attempt(
        GUILD_ID,
        int(cycle["id"]),
        submitted,
        OWNER_ID,
        status="submitted_to_mod",
        route_type="direct",
        idempotency_key="attempt-submitted",
    )
    repeated = await service.record_attempt(
        GUILD_ID,
        int(cycle["id"]),
        submitted,
        OWNER_ID,
        status="submitted_to_mod",
        route_type="direct",
        idempotency_key="attempt-submitted",
    )
    assert repeated["status"] == "submitted_to_mod"
    assert int(
        (
            await db.fetchone(
                "SELECT COUNT(*) AS c FROM level_outreach_attempts "
                "WHERE idempotency_key='attempt-submitted'"
            )
        )["c"]
    ) == 1
    await service.record_attempt(
        GUILD_ID,
        int(cycle["id"]),
        waiting,
        OWNER_ID,
        status="failed",
        route_type="stream",
        idempotency_key="attempt-failed",
    )
    saved_cycle, incremented = await service.complete_cycle(
        GUILD_ID, int(cycle["id"]), OWNER_ID
    )
    rows = {
        int(row["id"]): row
        for row in await db.fetchall("SELECT * FROM level_outreach_queue")
    }
    assert saved_cycle["status"] == "completed"
    assert incremented == 1
    assert rows[submitted]["queue_state"] == "awaiting_outcome"
    assert rows[submitted]["waiting_cycles"] == 0
    assert (
        int(rows[submitted]["outcome_window_due_ts"])
        - int(rows[submitted]["submitted_to_mod_ts"])
    ) == 30 * 86400
    assert rows[waiting]["queue_state"] == "queued"
    assert rows[waiting]["waiting_cycles"] == 1
    assert rows[waiting]["waiting_component_h"] == pytest.approx(1.5)
    assert rows[late]["queue_state"] == "queued"
    assert rows[late]["waiting_cycles"] == 0

    _same_cycle, second_increment = await service.complete_cycle(
        GUILD_ID, int(cycle["id"]), OWNER_ID
    )
    assert second_increment == 0
    assert (
        await db.fetchone(
            "SELECT waiting_cycles FROM level_outreach_queue WHERE id=?", (waiting,)
        )
    )["waiting_cycles"] == 1

    no_capacity = await service.start_cycle(GUILD_ID, OWNER_ID)
    with pytest.raises(ValueError, match="no confirmed moderator submission"):
        await service.complete_cycle(GUILD_ID, int(no_capacity["id"]), OWNER_ID)
    await service.cancel_cycle(GUILD_ID, int(no_capacity["id"]), OWNER_ID)
    assert (
        await db.fetchone(
            "SELECT waiting_cycles FROM level_outreach_queue WHERE id=?", (waiting,)
        )
    )["waiting_cycles"] == 1
    await db.close()


@pytest.mark.asyncio
async def test_outcome_window_is_completed_from_observed_evidence_without_probability(tmp_path):
    db = Database(str(tmp_path / "outcome.db"))
    await db.connect()
    queue_id = await insert_queue(
        db,
        user_id=1,
        message_id=31,
        level_id="888888888",
        priority=2,
        state="awaiting_outcome",
    )
    now = int(time.time())
    due = now - 10
    await db.execute(
        "UPDATE level_outreach_queue SET submitted_to_mod_ts=?,outcome_window_due_ts=?,"
        "level_refresh_after_ts=?,rated_observed_ts=? WHERE id=?",
        (now - 30 * 86400 - 10, due, 0, due - 5, queue_id),
    )
    service = PrioritySystemService(make_bot(db))
    refresh_calls = 0

    async def refresh(identity, **_kwargs):
        nonlocal refresh_calls
        refresh_calls += 1
        await db.execute(
            "INSERT INTO level_outreach_level_snapshots("
            "queue_id,checked_ts,current_exists,current_rated,lookup_status) VALUES(?,?,?,?,?)",
            (identity, now, 1, 1, "ok"),
        )
        return await db.fetchone(
            "SELECT * FROM level_outreach_queue WHERE id=?", (identity,)
        )

    service.refresh_queue_entry = refresh
    result = await service.maintenance_once(GUILD_ID)
    saved = await db.fetchone(
        "SELECT rated_within_window,outcome_window_completed_ts FROM level_outreach_queue WHERE id=?",
        (queue_id,),
    )
    assert result["outcomes_completed"] == 1
    assert refresh_calls == 1
    assert saved["rated_within_window"] == 1
    assert saved["outcome_window_completed_ts"] is not None
    await db.close()


@pytest.mark.asyncio
async def test_queue_and_active_cycle_survive_database_restart(tmp_path):
    path = tmp_path / "restart.db"
    db = Database(str(path))
    await db.connect()
    queue_id = await insert_queue(
        db, user_id=1, message_id=41, level_id="777777777", priority=2
    )
    cycle = await PrioritySystemService(make_bot(db)).start_cycle(
        GUILD_ID, OWNER_ID
    )
    await db.close()

    reopened = Database(str(path))
    await reopened.connect()
    service = PrioritySystemService(make_bot(reopened))
    assert int((await service.queue_entry(GUILD_ID, queue_id))["id"]) == queue_id
    assert int((await service.active_cycle(GUILD_ID))["id"]) == int(cycle["id"])
    entries = await service.cycle_entries(int(cycle["id"]))
    assert [int(row["queue_id"]) for row in entries] == [queue_id]
    await reopened.close()


@pytest.mark.asyncio
async def test_rated_refresh_preserves_evidence_and_removes_level_from_queue(tmp_path):
    db = Database(str(tmp_path / "rated.db"))
    await db.connect()
    queue_id = await insert_queue(
        db, user_id=1, message_id=20, level_id="999999999", priority=2
    )
    service = PrioritySystemService(make_bot(db))
    row = await db.fetchone("SELECT * FROM level_outreach_queue WHERE id=?", (queue_id,))
    await service._save_level_snapshot(
        row,
        {
            "gd_checked_ts": 500,
            "gd_lookup_status": "ok",
            "current_exists": True,
            "current_rated": True,
            "current_stars": 10,
            "current_level_name": "Rated Level",
            "current_uploader_name": "Creator",
            "current_uploader_user_id": "44",
            "current_uploader_account_id": "55",
        },
    )
    saved = await db.fetchone("SELECT * FROM level_outreach_queue WHERE id=?", (queue_id,))
    assert saved["queue_state"] == "rated"
    assert saved["rated_observed_ts"] == 500
    assert saved["current_rated"] == 1
    assert int((await db.fetchone("SELECT COUNT(*) AS c FROM level_outreach_level_snapshots"))["c"]) == 1
    await db.close()


@pytest.mark.asyncio
async def test_maintenance_batch_is_bounded_and_failures_do_not_abort(tmp_path):
    db = Database(str(tmp_path / "maintenance.db"))
    await db.connect()
    for index in range(8):
        await insert_queue(
            db,
            user_id=index + 1,
            message_id=index + 100,
            level_id=str(100000000 + index),
            priority=float(index),
        )
    service = PrioritySystemService(make_bot(db))
    calls = []

    async def refresh(queue_id, **_kwargs):
        calls.append(queue_id)
        if len(calls) == 2:
            raise TimeoutError("provider unavailable")
        return await db.fetchone("SELECT * FROM level_outreach_queue WHERE id=?", (queue_id,))

    service.refresh_queue_entry = refresh
    result = await service.maintenance_once(GUILD_ID)
    assert len(calls) == 5
    assert result["refreshed"] == 4
    assert result["failed"] == 1
    await db.close()


@pytest.mark.asyncio
async def test_owner_access_is_fail_closed_for_pps_management():
    cog = object.__new__(PrioritySystemCog)
    cog.bot = SimpleNamespace(config=PriorityConfig())
    denied = SimpleNamespace(
        guild=SimpleNamespace(id=GUILD_ID),
        user=SimpleNamespace(id=999),
        defer=AsyncMock(),
        respond=AsyncMock(),
    )
    allowed = SimpleNamespace(
        guild=SimpleNamespace(id=GUILD_ID),
        user=SimpleNamespace(id=OWNER_ID),
        defer=AsyncMock(),
        respond=AsyncMock(),
    )
    assert await cog._prepare(denied) is None
    denied.respond.assert_awaited_once()
    assert denied.respond.call_args.kwargs["ephemeral"] is True
    assert await cog._prepare(allowed) == GUILD_ID


def test_priority_config_is_validated_and_does_not_accept_arbitrary_model_code():
    settings = priority_settings(PriorityConfig().data)
    assert settings.model_version == "pps_v1"
    invalid = PriorityConfig().data
    invalid["priority_system"]["model_version"] = "eval(user_formula)"
    with pytest.raises(ValueError, match="model_version"):
        priority_settings(invalid)
