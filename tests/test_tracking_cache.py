from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio
import time

import pytest

from cogs.Tracking import TrackingCog
from utils.db import Database
from utils.timeutils import now_madrid, week_start_sunday


class FakeConfig:
    def get_int_list(self, *path, default=None):
        if path == ("roles", "excluded_tracking_role_id"):
            return [999]
        return list(default or [])


class FakeDatabase:
    def __init__(self, rows):
        self.rows = rows

    async def fetchone(self, sql, params=()):
        if "user_id=?" in sql:
            user_id = int(params[-1])
            for row in self.rows:
                if int(row["user_id"]) == user_id:
                    return {"count": row["count"]}
        return None

    async def fetchall(self, sql, params=()):
        return list(self.rows)


class FakeGuild:
    def __init__(self, target_member):
        self.id = 717
        self._target = target_member

    def get_member(self, user_id):
        return self._target if int(user_id) == int(self._target.id) else None

    async def fetch_member(self, user_id):
        return self._target if int(user_id) == int(self._target.id) else None


@pytest.mark.asyncio
async def test_member_rank_keeps_valid_activity_rows_when_member_cache_is_cold():
    rows = [
        {"user_id": 10, "count": 30},
        {"user_id": 20, "count": 20},
        {"user_id": 30, "count": 10},
    ]
    target = SimpleNamespace(id=30, bot=False, roles=[], guild_permissions=SimpleNamespace())
    cog = object.__new__(TrackingCog)
    cog.bot = SimpleNamespace(config=FakeConfig(), db=FakeDatabase(rows))
    cog.flush_activity_counts = AsyncMock()

    count, rank, eligible_total = await cog.get_member_stats(FakeGuild(target), "2026-07-12T00:00:00+02:00", 30)

    assert count == 10
    assert rank == 3
    assert eligible_total == 3


@pytest.mark.asyncio
async def test_known_excluded_members_are_still_removed_from_rank():
    rows = [{"user_id": 30, "count": 10}]
    target = SimpleNamespace(
        id=30,
        bot=False,
        roles=[SimpleNamespace(id=999)],
        guild_permissions=SimpleNamespace(),
    )
    cog = object.__new__(TrackingCog)
    cog.bot = SimpleNamespace(config=FakeConfig(), db=FakeDatabase(rows))
    cog.flush_activity_counts = AsyncMock()

    count, rank, eligible_total = await cog.get_member_stats(FakeGuild(target), "week", 30)

    assert count == 10
    assert rank is None
    assert eligible_total == 0


@pytest.mark.asyncio
async def test_tracking_normalizes_and_merges_legacy_rounded_member_rows():
    exact_id = 1115678273079349288
    rounded_id = int(float(exact_id))
    target = SimpleNamespace(
        id=exact_id,
        bot=False,
        roles=[],
        guild_permissions=SimpleNamespace(),
    )
    other = SimpleNamespace(
        id=42,
        bot=False,
        roles=[],
        guild_permissions=SimpleNamespace(),
    )
    rows = [
        {"user_id": rounded_id, "count": 5},
        {"user_id": exact_id, "count": 3},
        {"user_id": other.id, "count": 2},
    ]

    class LegacyGuild:
        id = 717
        members = [target, other]

        def get_member(self, user_id):
            return next(
                (member for member in self.members if member.id == int(user_id)),
                None,
            )

        async def fetch_member(self, user_id):
            member = self.get_member(user_id)
            if member is None:
                raise AssertionError("cached legacy alias should resolve")
            return member

    guild = LegacyGuild()
    cog = object.__new__(TrackingCog)
    cog.bot = SimpleNamespace(
        config=FakeConfig(),
        db=FakeDatabase(rows),
        get_guild=lambda guild_id: guild if guild_id == guild.id else None,
    )
    cog.flush_activity_counts = AsyncMock()

    top = await cog.get_top(guild.id, "week", limit=20)
    count, rank, eligible_total = await cog.get_member_stats(
        guild,
        "week",
        exact_id,
    )

    assert top == [(exact_id, 8), (other.id, 2)]
    assert (count, rank, eligible_total) == (8, 1, 2)


@pytest.mark.asyncio
async def test_weekly_reward_disable_and_enable_persist_and_restore_workflow_state(tmp_path):
    db = Database(str(tmp_path / "tracking.db"))
    await db.connect()
    cog = object.__new__(TrackingCog)
    cog.bot = SimpleNamespace(db=db)
    cog._log_weekly = AsyncMock()
    cog._notify_reenabled_weekly_claims = AsyncMock()
    cog._cfg_int = lambda *args, **kwargs: 48
    guild = SimpleNamespace(id=717)

    week_start = week_start_sunday(now_madrid()).isoformat()
    await db.execute(
        "INSERT INTO weekly_claims(guild_id,week_start,user_id,rank,status,contacted_ts) VALUES(?,?,?,?,?,?)",
        (guild.id, week_start, 99, 1, "pending", 1),
    )
    await db.execute(
        "INSERT INTO weekly_sessions(guild_id,week_start,user_id,stage,expires_ts,active) VALUES(?,?,?,?,?,?)",
        (guild.id, week_start, 99, "awaiting_request", 1, 1),
    )
    await db.execute(
        "INSERT INTO weekly_claims(guild_id,week_start,user_id,rank,status,contacted_ts) VALUES(?,?,?,?,?,?)",
        (guild.id, week_start, 100, 2, "contacting", 1),
    )

    disabled_week = await cog.disable_weekly_reward_for_current_week(guild, 42)
    disabled = await db.fetchone(
        "SELECT disabled_by FROM weekly_reward_disabled WHERE guild_id=? AND week_start=?",
        (guild.id, week_start),
    )
    disabled_claim = await db.fetchone(
        "SELECT status FROM weekly_claims WHERE guild_id=? AND week_start=? AND user_id=?",
        (guild.id, week_start, 99),
    )
    disabled_session = await db.fetchone(
        "SELECT active FROM weekly_sessions WHERE guild_id=? AND week_start=? AND user_id=?",
        (guild.id, week_start, 99),
    )
    assert disabled_week == week_start
    assert int(disabled["disabled_by"]) == 42
    assert disabled_claim["status"] == "disabled"
    assert int(disabled_session["active"]) == 0

    enabled_week, was_disabled = await cog.enable_weekly_reward_for_current_week(guild, 42)
    claim = await db.fetchone(
        "SELECT status FROM weekly_claims WHERE guild_id=? AND week_start=? AND user_id=?",
        (guild.id, week_start, 99),
    )
    session = await db.fetchone(
        "SELECT active,stage,expires_ts FROM weekly_sessions WHERE guild_id=? AND week_start=? AND user_id=?",
        (guild.id, week_start, 99),
    )
    recovered_claim = await db.fetchone(
        "SELECT status FROM weekly_claims WHERE guild_id=? AND week_start=? AND user_id=?",
        (guild.id, week_start, 100),
    )
    recovered_session = await db.fetchone(
        "SELECT active,stage,expires_ts FROM weekly_sessions WHERE guild_id=? AND week_start=? AND user_id=?",
        (guild.id, week_start, 100),
    )

    assert enabled_week == week_start
    assert was_disabled is True
    assert await cog.weekly_reward_disabled(guild.id, week_start) is False
    assert claim["status"] == "pending"
    assert int(session["active"]) == 1
    assert session["stage"] == "awaiting_request"
    assert int(session["expires_ts"]) > int(time.time())
    assert recovered_claim["status"] == "pending"
    assert int(recovered_session["active"]) == 1
    assert recovered_session["stage"] == "awaiting_request"
    assert int(recovered_session["expires_ts"]) > int(time.time())
    cog._notify_reenabled_weekly_claims.assert_awaited_once()
    assert cog._log_weekly.await_count == 2
    await db.close()


@pytest.mark.asyncio
async def test_contacting_weekly_offer_recovers_saved_dm_without_resending(tmp_path):
    now_ts = int(time.time())
    week_start = "2026-07-12T00:00:00+02:00"
    user_id = 1102884420207255653
    message_id = 1548671314074673201
    channel_id = 1548671314074673301
    bot_id = 1454985687177887866

    class OfferConfig:
        def get_int(self, *path, default=0):
            values = {
                ("guild", "allowed_guild_id"): 717,
                ("tracking", "dm_timeout_hours"): 48,
            }
            return values.get(path, default)

        def get(self, *path, default=None):
            return default

    db = Database(str(tmp_path / "tracking-offer-recovery.db"))
    await db.connect()
    await db.execute(
        "INSERT INTO weekly_claims("
        "guild_id,week_start,user_id,rank,status,contacted_ts,offer_channel_id,"
        "offer_message_id,offer_expires_ts"
        ") VALUES(?,?,?,?,?,?,?,?,?)",
        (
            717,
            week_start,
            user_id,
            1,
            "contacting",
            now_ts,
            channel_id,
            message_id,
            now_ts + 48 * 3600,
        ),
    )

    cog = object.__new__(TrackingCog)
    cog.bot = SimpleNamespace(config=OfferConfig(), db=db, user=SimpleNamespace(id=bot_id))
    expected_content, _embed = cog._build_request_dm_message(48, now_ts + 48 * 3600)
    saved_message = SimpleNamespace(
        id=message_id,
        author=SimpleNamespace(id=bot_id),
        content=expected_content,
        embeds=[],
    )
    dm_channel = SimpleNamespace(
        id=channel_id,
        fetch_message=AsyncMock(return_value=saved_message),
        send=AsyncMock(side_effect=AssertionError("the existing offer must not be resent")),
    )
    member = SimpleNamespace(
        id=user_id,
        bot=False,
        create_dm=AsyncMock(return_value=dm_channel),
        send=AsyncMock(side_effect=AssertionError("the existing offer must not be resent")),
    )

    class OfferGuild:
        id = 717
        members = [member]

        def get_member(self, candidate_id):
            return member if int(candidate_id) == user_id else None

    guild = OfferGuild()
    cog.bot.get_guild = lambda guild_id: guild if guild_id == guild.id else None
    cog._log_weekly = AsyncMock()
    cog._log_background_error = AsyncMock()

    await cog._recover_contacting_claims()

    claim = await db.fetchone(
        "SELECT status,offer_channel_id,offer_message_id FROM weekly_claims "
        "WHERE guild_id=? AND week_start=? AND user_id=?",
        (guild.id, week_start, user_id),
    )
    session = await db.fetchone(
        "SELECT stage,active FROM weekly_sessions "
        "WHERE guild_id=? AND week_start=? AND user_id=?",
        (guild.id, week_start, user_id),
    )
    assert claim["status"] == "pending"
    assert int(claim["offer_channel_id"]) == channel_id
    assert int(claim["offer_message_id"]) == message_id
    assert session["stage"] == "awaiting_request"
    assert int(session["active"]) == 1
    dm_channel.send.assert_not_awaited()
    member.send.assert_not_awaited()
    await db.close()


@pytest.mark.asyncio
async def test_weekly_reminder_is_not_resent_when_pointer_finalize_is_uncertain(tmp_path):
    now_ts = int(time.time())
    week_start = "2026-07-12T00:00:00+02:00"
    user_id = 1102884420207255653
    db = Database(str(tmp_path / "tracking-reminder-delivery.db"))
    await db.connect()
    await db.execute(
        "INSERT INTO weekly_claims(guild_id,week_start,user_id,rank,status,contacted_ts) "
        "VALUES(?,?,?,?,?,?)",
        (717, week_start, user_id, 1, "pending", now_ts - 7200),
    )
    await db.execute(
        "INSERT INTO weekly_sessions(guild_id,week_start,user_id,stage,expires_ts,active) "
        "VALUES(?,?,?,?,?,1)",
        (717, week_start, user_id, "awaiting_request", now_ts + 7200),
    )

    class ReminderConfig:
        def get(self, *path, default=None):
            return default

    reminder = SimpleNamespace(
        id=1548671314074673201,
        channel=SimpleNamespace(id=1548671314074673301),
    )
    user = SimpleNamespace(id=user_id, send=AsyncMock(return_value=reminder))
    guild = SimpleNamespace(id=717)
    cog = object.__new__(TrackingCog)
    cog.bot = SimpleNamespace(
        config=ReminderConfig(),
        db=db,
        get_guild=lambda guild_id: guild if guild_id == guild.id else None,
    )
    cog._cfg_int = lambda section, key, default=0: {
        ("guild", "allowed_guild_id"): 717,
        ("tracking", "reminder_after_hours"): 1,
        ("tracking", "reminder_repeat_hours"): 0,
    }.get((section, key), default)
    cog._resolve_dm_user = AsyncMock(return_value=user)
    cog._log_weekly = AsyncMock()
    cog._log_background_error = AsyncMock()

    original_execute = db.execute

    async def fail_final_pointer(sql, params=()):
        if "SET delivery_status='sent'" in sql:
            raise RuntimeError("uncertain Turso finalize")
        return await original_execute(sql, params)

    db.execute = fail_final_pointer
    await cog._process_reminders()
    db.execute = original_execute
    await cog._process_reminders()

    row = await db.fetchone(
        "SELECT delivery_status FROM weekly_reminders "
        "WHERE guild_id=? AND week_start=? AND user_id=?",
        (guild.id, week_start, user_id),
    )
    assert row["delivery_status"] == "sending"
    user.send.assert_awaited_once()
    cog._log_background_error.assert_awaited_once()
    await db.close()


@pytest.mark.asyncio
async def test_decline_confirmation_defers_before_database_work():
    events = []

    class Response:
        def __init__(self):
            self.done = False

        def is_done(self):
            return self.done

        async def defer(self, **kwargs):
            events.append("defer")
            self.done = True

        async def send_message(self, *args, **kwargs):
            events.append("initial_response")
            self.done = True

    class Followup:
        async def send(self, *args, **kwargs):
            events.append("followup")

    class EmptyDatabase:
        async def fetchone(self, *args, **kwargs):
            events.append("database")
            return None

    guild = SimpleNamespace(id=717)
    cog = object.__new__(TrackingCog)
    cog.bot = SimpleNamespace(
        db=EmptyDatabase(),
        get_guild=lambda guild_id: guild if guild_id == 717 else None,
    )
    cog._weekly_submit_lock = asyncio.Lock()
    cog._cfg_int = lambda *args, **kwargs: 717
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=42),
        message=SimpleNamespace(id=9_001),
        response=Response(),
        followup=Followup(),
    )

    await cog.handle_decline_confirm(interaction, confirmed=False)

    assert events == ["defer", "database", "followup"]


@pytest.mark.asyncio
async def test_weekly_dm_handler_yields_to_an_active_support_session():
    guild = SimpleNamespace(id=717)
    support = SimpleNamespace(should_yield_weekly_dm=AsyncMock(return_value=True))
    db = SimpleNamespace(fetchall=AsyncMock(side_effect=AssertionError("weekly state must not be read")))
    cog = object.__new__(TrackingCog)
    cog.bot = SimpleNamespace(
        db=db,
        get_guild=lambda guild_id: guild if guild_id == guild.id else None,
        get_cog=lambda name: support if name == "HelpCog" else None,
    )
    cog._cfg_int = lambda *args, **kwargs: 717
    cog._resolve_member = AsyncMock(return_value=SimpleNamespace(id=42))
    cog._log_background_error = AsyncMock()

    await cog._handle_dm(
        SimpleNamespace(
            id=555,
            author=SimpleNamespace(id=42),
            channel=SimpleNamespace(),
            content="This answer belongs to support",
        )
    )

    support.should_yield_weekly_dm.assert_awaited_once_with(guild.id, 42, 555)
    db.fetchall.assert_not_awaited()
