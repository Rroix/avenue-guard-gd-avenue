import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cogs.MessageResponses import MessageResponsesCog
from utils.config import Config


ROOT = Path(__file__).resolve().parents[1]


def test_checked_in_config_contains_recovery_and_rate_limit_defaults():
    config = Config(str(ROOT / "config.json"))

    assert config.get("background", "server_icon_rotation", "mode") == "disabled"
    assert config.get_int("background", "server_icon_rotation", "interval_seconds") == 300
    assert config.get_int("help", "session_timeout_seconds") == 3600
    assert config.get("level_requests", "level_validation", "provider_min_interval_seconds") == {
        "gdbrowser": 0.1,
        "boomlings": 0.55,
    }
    assert config.get_int("channels", "dm_fail_log_channel_id") == 1445502925081284729
    assert config.get_int("channels", "transcript_requests_channel_id") == 1455042313855307939


def test_all_checked_in_embed_templates_fit_discord_structural_limits():
    payload = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

    def walk(value):
        if isinstance(value, dict):
            fields = value.get("fields")
            if fields is not None:
                assert isinstance(fields, list)
                assert len(fields) <= 25
                for field in fields:
                    assert isinstance(field, dict)
                    assert 0 < len(str(field.get("name") or "")) <= 256
                    assert 0 < len(str(field.get("value") or "")) <= 1024
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(payload)


def test_checked_in_response_rules_have_valid_outputs():
    cog = object.__new__(MessageResponsesCog)
    cog._load_error = ""
    cog._rules = json.loads((ROOT / "responses.json").read_text(encoding="utf-8"))
    assert cog.validate_rules() == []


def test_response_rule_validator_rejects_a_matching_rule_with_no_output():
    cog = object.__new__(MessageResponsesCog)
    cog._load_error = ""
    cog._rules = [{"Content": "hello", "Embed": False, "Message": False}]
    assert "neither Embed nor Message output is enabled" in cog.validate_rules()[0]


@pytest.mark.asyncio
async def test_response_rules_can_send_every_match_after_one_cooldown_claim():
    class ResponseConfig:
        def get_int(self, *path, default=0):
            return 717 if path == ("guild", "allowed_guild_id") else default

        def get(self, *path, default=None):
            values = {
                ("responses", "first_match_only"): False,
                ("responses", "cooldown_seconds"): 15,
                ("responses", "max_response_chars"): 1600,
            }
            return values.get(path, default)

    channel = SimpleNamespace(id=99, send=AsyncMock())
    message = SimpleNamespace(
        author=SimpleNamespace(id=42, bot=False),
        guild=SimpleNamespace(id=717),
        channel=channel,
        content="hello there",
        reply=AsyncMock(),
    )
    cog = object.__new__(MessageResponsesCog)
    cog.bot = SimpleNamespace(config=ResponseConfig())
    cog._rules = [
        {"Content": "hello", "Message": True, "Message_text": "First"},
        {"Content": "there", "Message": True, "Message_text": "Second"},
    ]
    cog._cooldown = {}
    cog._last_rule_error_log = {}

    await cog.on_message(message)

    assert [call.args[0] for call in channel.send.await_args_list] == [
        "First",
        "Second",
    ]
