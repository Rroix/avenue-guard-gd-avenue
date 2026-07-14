from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cogs.Tracking import TrackingCog


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
