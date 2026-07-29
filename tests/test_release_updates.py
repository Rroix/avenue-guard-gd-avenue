import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cogs.Release import ReleaseCog
from utils.db import Database
from utils.keepalive import (
    _response_for_path,
    get_public_bot_payload,
    set_keepalive_status,
    set_public_bot_metrics,
    set_public_release_data,
)
from utils.releases import (
    ReleaseValidationError,
    load_release_manifest,
    normalize_release_changes,
    normalize_version,
)


OWNER_ID = 1102884420207255653


class _Config:
    def __init__(self, manifest_path):
        self.data = {
            "release_updates": {
                "enabled": True,
                "owner_user_ids": [str(OWNER_ID)],
                "manifest_path": str(manifest_path),
                "public_release_limit": 20,
                "website_url": "https://gdavenue.netlify.app/bot",
            },
            "impact": {"allowed_user_ids": [str(OWNER_ID)]},
        }

    def get(self, *path, default=None):
        current = self.data
        for key in path:
            if not isinstance(current, dict) or key not in current:
                return default
            current = current[key]
        return current

    def get_str(self, *path, default=""):
        value = self.get(*path, default=default)
        return str(value)

    def get_int(self, *path, default=0):
        try:
            return int(self.get(*path, default=default))
        except (TypeError, ValueError):
            return default

    def get_int_list(self, *path, default=None):
        value = self.get(*path, default=default or [])
        return [int(item) for item in value]


class _Owner:
    def __init__(self):
        self.id = OWNER_ID
        self.messages = []

    async def send(self, **kwargs):
        message = SimpleNamespace(
            id=9000 + len(self.messages),
            embeds=[kwargs["embed"]],
            edit=AsyncMock(),
        )
        self.messages.append((message, kwargs))
        return message


class _Bot:
    def __init__(self, db, config, owner):
        self.db = db
        self.config = config
        self.owner = owner
        self.guilds = []
        self.user = None
        self.latency = 0.05

    def get_user(self, user_id):
        return self.owner if int(user_id) == OWNER_ID else None

    async def fetch_user(self, user_id):
        return self.get_user(user_id)

    def is_closed(self):
        return False


def test_release_validation_normalizes_versions_and_changes(tmp_path):
    assert normalize_version("V3.4.1-beta.2") == "3.4.1-beta.2"
    assert normalize_release_changes("- Fixed tickets\n2. Added status page") == [
        "Fixed tickets",
        "Added status page",
    ]
    with pytest.raises(ReleaseValidationError):
        normalize_version("release-three")
    with pytest.raises(ReleaseValidationError):
        normalize_release_changes("")

    blank = tmp_path / "release.json"
    blank.write_text('{"version":"","changes":[]}', encoding="utf-8")
    assert load_release_manifest(blank) is None


def test_public_status_payload_is_sanitized_and_versioned():
    set_keepalive_status("online", "Logged in with an internal account name")
    set_public_bot_metrics(
        bot_name="Avenue Guard",
        avatar_url="https://cdn.example/avatar.png",
        latency_ms=42,
        guild_count=1,
        member_count=2500,
    )
    set_public_release_data(
        [
            {
                "version": "3.4.1",
                "title": "Status page",
                "summary": "Public operational visibility",
                "changes": ["Added release approvals"],
                "published_ts": 1234,
            }
        ]
    )

    payload = get_public_bot_payload()
    assert payload["online"] is True
    assert payload["status"] == "Operational"
    assert payload["version"] == "3.4.1"
    assert payload["member_count"] == 2500
    assert "detail" not in payload

    body, content_type, cache_control, public_api = _response_for_path(
        "/api/bot?cache=no"
    )
    decoded = json.loads(body)
    assert decoded["current_release"]["version"] == "3.4.1"
    assert content_type.startswith("application/json")
    assert cache_control == "no-store"
    assert public_api is True


@pytest.mark.asyncio
async def test_release_only_enters_public_feed_after_owner_approval(tmp_path):
    db = Database(str(tmp_path / "bot.db"))
    await db.connect()
    owner = _Owner()
    bot = _Bot(db, _Config(tmp_path / "release.json"), owner)
    cog = ReleaseCog(bot)
    set_public_release_data([])

    ok, message = await cog.propose_release(
        version="4.0.0",
        title="Public bot page",
        summary="A clearer view of Avenue Guard",
        changes="- Added live status\n- Added approved release notes",
        created_by=OWNER_ID,
    )
    assert ok is True
    assert "pending" in message.casefold()
    assert len(owner.messages) == 1
    assert get_public_bot_payload()["current_release"] is None

    approval_message = owner.messages[0][0]
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=OWNER_ID),
        message=approval_message,
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    await cog.handle_release_decision(interaction, approved=True)

    row = await db.fetchone(
        "SELECT status,decided_by FROM bot_releases WHERE version='4.0.0'"
    )
    assert row["status"] == "approved"
    assert row["decided_by"] == OWNER_ID
    assert get_public_bot_payload()["version"] == "4.0.0"
    approval_message.edit.assert_awaited_once()
    await db.close()


@pytest.mark.asyncio
async def test_non_owner_cannot_approve_release(tmp_path):
    db = Database(str(tmp_path / "bot.db"))
    await db.connect()
    owner = _Owner()
    bot = _Bot(db, _Config(tmp_path / "release.json"), owner)
    cog = ReleaseCog(bot)

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=999),
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    await cog.handle_release_decision(interaction, approved=True)

    interaction.response.send_message.assert_awaited_once()
    await db.close()
