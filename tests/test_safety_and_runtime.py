from copy import deepcopy
from types import SimpleNamespace

import pytest

from cogs.Mod import _review_access_text, _within_one_edit
from cogs.Sticky import StickyCog
from utils.errors import _compact_error_message, _redact_secrets
from utils.runtime_config import (
    FORUM_RULES_SETTING,
    SERVER_ICON_SETTING,
    collect_forum_required_rules,
    load_runtime_config_overrides,
    persist_forum_required_rules,
    persist_server_icon_config,
)
from utils.server_icons import (
    clean_icon_urls,
    is_expiring_discord_attachment_url,
    is_valid_icon_url,
    normalize_server_icon_mode,
    server_icon_url_warning,
)


class MemoryDatabase:
    def __init__(self):
        self.values = {}

    async def set_runtime_setting(self, key, value):
        self.values[key] = deepcopy(value)

    async def get_runtime_setting(self, key, default=None):
        return deepcopy(self.values.get(key, default))


class ConfigObject:
    def __init__(self, data):
        self.data = data


def test_review_access_phrase_is_case_insensitive_with_one_character_variation():
    expected = _review_access_text("I have read and understood the review access conditions")
    assert _within_one_edit(_review_access_text("i HAVE read and understood the review access conditions"), expected)
    assert _within_one_edit(_review_access_text("I have read and understood the review access conditions."), expected)
    assert not _within_one_edit(_review_access_text("I have read and understood the review access conditions?!"), expected)
    assert not _within_one_edit(_review_access_text("I have read and understood the review access conditions, not really"), expected)


def test_icon_url_validation_blocks_local_targets_credentials_and_expiring_links():
    assert is_valid_icon_url("https://i.ibb.co/example/icon.png")
    assert not is_valid_icon_url("file:///tmp/icon.png")
    assert not is_valid_icon_url("http://127.0.0.1/icon.png")
    assert not is_valid_icon_url("http://[::1]/icon.png")
    assert not is_valid_icon_url("https://user:password@example.com/icon.png")

    expiring = "https://media.discordapp.net/attachments/1/2/icon.png?ex=a&is=b&hm=c"
    assert is_expiring_discord_attachment_url(expiring)
    assert "expire" in server_icon_url_warning(expiring).casefold()
    assert clean_icon_urls(["https://i.ibb.co/a.png", "https://i.ibb.co/a.png", "bad"] ) == [
        "https://i.ibb.co/a.png"
    ]
    assert normalize_server_icon_mode("LINEAR") == "linear"
    assert normalize_server_icon_mode("unknown") == "disabled"


def test_forum_regex_guard_rejects_high_risk_patterns():
    cog = object.__new__(StickyCog)
    assert cog._required_regex_is_safe(r"^cubical[.!?]?$")
    assert not cog._required_regex_is_safe(r"(a+)+$")
    assert not cog._required_regex_is_safe(r".*foo.*bar.*")
    assert not cog._required_regex_is_safe(r"(foo|bar)")
    assert not cog._required_regex_is_safe("[")


@pytest.mark.asyncio
async def test_mutable_runtime_config_round_trips_through_database():
    config = ConfigObject(
        {
            "background": {"server_icon_rotation": {"mode": "disabled", "urls": []}},
            "forum_first_message": {
                "entries": [
                    {
                        "forum_channel_id": "123",
                        "required_word": "cubical",
                        "required_word_match_mode": "contains",
                    }
                ]
            },
        }
    )
    db = MemoryDatabase()
    bot = SimpleNamespace(config=config, db=db)

    await persist_server_icon_config(bot, {"mode": "linear", "urls": ["https://example.com/icon.png"]})
    await persist_forum_required_rules(bot)
    assert SERVER_ICON_SETTING in db.values
    assert db.values[FORUM_RULES_SETTING] == collect_forum_required_rules(config)

    config.data["background"]["server_icon_rotation"] = {"mode": "disabled", "urls": []}
    config.data["forum_first_message"]["entries"][0]["required_word"] = "changed locally"
    await load_runtime_config_overrides(bot)

    assert config.data["background"]["server_icon_rotation"]["mode"] == "linear"
    assert config.data["forum_first_message"]["entries"][0]["required_word"] == "cubical"


def test_error_logging_redacts_tokens_and_compacts_remote_html():
    jwt = "eyJhbGciOiJFZERTQSJ9.eyJzY29wZXMiOlsicmVhZCJdfQ.abcdefghijklmnopqrstuvwxyz123456"
    redacted = _redact_secrets(f"TURSO_AUTH_TOKEN={jwt} DISCORD_TOKEN=secret-token")
    assert jwt not in redacted
    assert "secret-token" not in redacted
    assert "[REDACTED]" in redacted

    compact = _compact_error_message("<html>Cloudflare Ray ID: abc123</html>")
    assert "full HTML omitted" in compact
    assert "abc123" in compact
