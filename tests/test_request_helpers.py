from datetime import datetime
from types import SimpleNamespace

import pytest

import cogs.RequestLevels as request_module
from cogs.RequestLevels import RequestLevelsCog
from utils.timeutils import TZ


class FakeConfig:
    def __init__(self):
        self.data = {
            "level_requests": {
                "request_post_close_edit_minutes": 5,
                "level_validation": {},
            }
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

    def get_int_list(self, *path, default=None):
        value = self.get(*path, default=default or [])
        return [int(item) for item in value] if isinstance(value, list) else []


def make_cog():
    cog = object.__new__(RequestLevelsCog)
    cog.bot = SimpleNamespace(config=FakeConfig())
    return cog


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("only demons", "only_demons"),
        ("ONLY-PLATS", "only_plats"),
        ("classic non demons", "only_classic_non_demons"),
        ("long or XL", "long_level"),
        ("any", ""),
        ("not a request type", None),
    ],
)
def test_request_type_normalization(value, expected):
    assert make_cog()._normalize_request_type(value) == expected


def test_request_form_validation_rejects_bad_ids_and_non_urls():
    cog = make_cog()
    errors = cog._validate_request_data(
        {"level_id": "abc", "level_showcase": "youtube.com/watch?v=no-scheme"}
    )
    assert len(errors) == 2
    assert cog._validate_request_data(
        {"level_id": "111111111", "level_showcase": "https://youtu.be/example"}
    ) == []
    assert cog._valid_url("https://user:secret@example.com/video") is False


def test_edit_window_uses_persisted_deadline_for_a_closed_or_old_wave(monkeypatch):
    cog = make_cog()
    monkeypatch.setattr(request_module.time_module, "time", lambda: 1_000)

    open_state = {"wave_id": 3, "state": "open", "close_ts": None, "closed_ts": None}
    current = {"wave_id": 3, "status": "pending", "edit_deadline_ts": None}
    old_valid = {"wave_id": 2, "status": "pending", "edit_deadline_ts": 1_001}
    old_expired = {"wave_id": 2, "status": "pending", "edit_deadline_ts": 999}

    assert cog._can_edit_submission(open_state, current) is True
    assert cog._can_edit_submission(open_state, old_valid) is True
    assert cog._can_edit_submission(open_state, old_expired) is False
    assert cog._can_edit_submission(open_state, {**current, "status": "reviewed"}) is False


def test_scheduled_opening_parses_local_time_and_future_month(monkeypatch):
    cog = make_cog()
    fixed_now = datetime(2026, 1, 31, 20, 0, tzinfo=TZ)
    monkeypatch.setattr(request_module, "now_madrid", lambda: fixed_now)

    next_day_ts, error = cog._parse_scheduled_open_ts("18:30")
    assert error == ""
    assert datetime.fromtimestamp(next_day_ts, TZ) == datetime(2026, 2, 1, 18, 30, tzinfo=TZ)

    next_31st_ts, error = cog._parse_scheduled_open_ts("19:15", 31)
    assert error == ""
    assert datetime.fromtimestamp(next_31st_ts, TZ) == datetime(2026, 3, 31, 19, 15, tzinfo=TZ)

    assert cog._parse_scheduled_open_ts("25:00")[0] is None
    assert cog._parse_scheduled_open_ts("18:99")[0] is None
