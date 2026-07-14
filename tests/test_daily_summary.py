from types import SimpleNamespace

import pytest

import cogs.Background as background_module
from cogs.Background import BackgroundCog, DailyStats


class FakeTextChannel:
    def __init__(self, events):
        self.id = 55
        self.events = events

    async def send(self, **kwargs):
        self.events.append("send")
        return SimpleNamespace(id=77, delete=self._delete)

    async def _delete(self):
        self.events.append("delete")


class FakeGuild:
    def __init__(self, channel):
        self.id = 717
        self.icon = None
        self._channel = channel

    def get_channel(self, channel_id):
        return self._channel if int(channel_id) == self._channel.id else None

    async def fetch_channel(self, channel_id):
        return self.get_channel(channel_id)


@pytest.mark.asyncio
async def test_daily_snapshot_is_persisted_before_discord_message(monkeypatch):
    events = []
    channel = FakeTextChannel(events)
    guild = FakeGuild(channel)
    cog = object.__new__(BackgroundCog)
    cog.bot = SimpleNamespace(config=SimpleNamespace())
    cog._current_day = "2026-07-12"
    cog.stats = DailyStats(messages=12, commands=2, by_channel={55: 12}, by_user={1: 12})
    cog._completed_day_stats = {}
    cog.voice_sessions = {}
    cog._daily_summary_already_sent = lambda guild_id, day: _async_value(False)
    cog._load_daily_stats = lambda guild_id, day: _async_value(None)

    async def persist(guild_id, day, snapshot):
        events.append("persist")

    async def record(guild_id, day, channel_id, message_id):
        events.append("record")

    cog._persist_daily_stats = persist
    cog._record_daily_summary_sent = record
    cog._daily_summary_channel_id = lambda: 55
    cog._daily_reset_after_report = lambda: False
    cog._rollover_boundary_ts = lambda day: 0
    cog._add_voice_until = lambda snapshot, ts: None
    cog._voice_sessions_from_guild = lambda guild, ts: {}
    monkeypatch.setattr(background_module.discord, "TextChannel", FakeTextChannel)

    assert await cog._send_daily_summary_for_day(guild, "2026-07-12") is True
    assert events == ["persist", "send", "record"]


@pytest.mark.asyncio
async def test_daily_summary_is_not_sent_when_snapshot_persistence_fails(monkeypatch):
    events = []
    channel = FakeTextChannel(events)
    guild = FakeGuild(channel)
    cog = object.__new__(BackgroundCog)
    cog.bot = SimpleNamespace(config=SimpleNamespace())
    cog._current_day = "2026-07-12"
    cog.stats = DailyStats()
    cog._completed_day_stats = {}
    cog.voice_sessions = {}
    cog._daily_summary_already_sent = lambda guild_id, day: _async_value(False)
    cog._load_daily_stats = lambda guild_id, day: _async_value(None)

    async def fail_persist(guild_id, day, snapshot):
        raise RuntimeError("temporary database failure")

    cog._persist_daily_stats = fail_persist
    cog._daily_summary_channel_id = lambda: 55
    cog._daily_reset_after_report = lambda: False
    cog._rollover_boundary_ts = lambda day: 0
    cog._add_voice_until = lambda snapshot, ts: None
    cog._voice_sessions_from_guild = lambda guild, ts: {}
    monkeypatch.setattr(background_module.discord, "TextChannel", FakeTextChannel)

    async def ignore_log_error(*args, **kwargs):
        return None

    monkeypatch.setattr(background_module, "log_error", ignore_log_error)
    assert await cog._send_daily_summary_for_day(guild, "2026-07-12") is False
    assert events == []


async def _async_value(value):
    return value
