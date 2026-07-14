from types import SimpleNamespace
from unittest.mock import AsyncMock

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
async def test_weekly_reward_disable_and_enable_persist_and_restore_workflow_state(tmp_path):
    db = Database(str(tmp_path / "tracking.db"))
    await db.connect()
    cog = object.__new__(TrackingCog)
    cog.bot = SimpleNamespace(db=db)
    cog._log_weekly = AsyncMock()
    guild = SimpleNamespace(id=717)

    week_start = week_start_sunday(now_madrid()).isoformat()
    await db.execute(
        "INSERT INTO weekly_claims(guild_id,week_start,user_id,rank,status,contacted_ts) VALUES(?,?,?,?,?,?)",
        (guild.id, week_start, 99, 1, "pending", 1),
    )
    await db.execute(
        "INSERT INTO weekly_sessions(guild_id,week_start,user_id,stage,expires_ts,active) VALUES(?,?,?,?,?,?)",
        (guild.id, week_start, 99, "awaiting_request", 9_999_999_999, 1),
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
        "SELECT active,stage FROM weekly_sessions WHERE guild_id=? AND week_start=? AND user_id=?",
        (guild.id, week_start, 99),
    )

    assert enabled_week == week_start
    assert was_disabled is True
    assert await cog.weekly_reward_disabled(guild.id, week_start) is False
    assert claim["status"] == "pending"
    assert int(session["active"]) == 1
    assert session["stage"] == "awaiting_request"
    assert cog._log_weekly.await_count == 2
    await db.close()
