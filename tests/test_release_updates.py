import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import cogs.Release as release_module
import utils.keepalive as keepalive_module
from cogs.Release import ReleaseCog
from utils.db import Database
from utils.keepalive import (
    _response_for_path,
    get_public_bot_payload,
    get_public_level_payload,
    get_public_team_payload,
    set_keepalive_status,
    set_public_bot_metrics,
    set_public_health_history,
    set_public_level_data,
    set_public_release_data,
    set_public_team_data,
    set_runtime_heartbeat,
)
from utils.releases import (
    ReleaseValidationError,
    compare_versions,
    load_release_manifest,
    newest_version,
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
                "version_floor": "3.18.7",
                "bot_avatar_url": (
                    "https://cdn.discordapp.com/avatars/1454985687177887866/"
                    "d268221fd7a7a5529897730d18edd5a0.webp?size=2048"
                ),
                "website_url": "https://gdavenue.netlify.app/bot",
            },
            "guild": {"allowed_guild_id": 717003826288394271},
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
            delete=AsyncMock(),
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

    def get_guild(self, guild_id):
        return next(
            (
                guild
                for guild in self.guilds
                if int(getattr(guild, "id", 0)) == int(guild_id)
            ),
            None,
        )

    def get_user(self, user_id):
        return self.owner if int(user_id) == OWNER_ID else None

    async def fetch_guild(self, guild_id, *, with_counts=True):
        return self.get_guild(guild_id)

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
    assert normalize_release_changes(
        "Fixed ticket replies | Added service status | Cleaner update cards"
    ) == [
        "Fixed ticket replies",
        "Added service status",
        "Cleaner update cards",
    ]
    assert compare_versions("3.20.1", "3.18.7") > 0
    assert compare_versions("3.20.1-beta.2", "3.20.1") < 0
    assert compare_versions("3.20.1+build.2", "3.20.1+build.1") == 0
    assert newest_version(["3.18.7", "3.20.0", "3.19.4"]) == "3.20.0"
    with pytest.raises(ReleaseValidationError):
        normalize_version("release-three")
    with pytest.raises(ReleaseValidationError):
        normalize_release_changes("")

    blank = tmp_path / "release.json"
    blank.write_text('{"version":"","changes":[]}', encoding="utf-8")
    assert load_release_manifest(blank) is None


def test_public_status_payload_is_sanitized_and_versioned():
    set_keepalive_status("online", "Logged in with an internal account name")
    set_runtime_heartbeat(lag_ms=0, tasks={}, database={"connected": True}, incidents=[])
    set_public_bot_metrics(
        bot_name="Avenue Guard",
        avatar_url="https://cdn.example/avatar.png",
        latency_ms=42,
        guild_count=1,
        member_count=2500,
        uptime_percentage=99.875,
        uptime_tracking_since_ts=1200,
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
    set_public_health_history(
        [
            {
                "sample_ts": 1200,
                "healthy": True,
                "database_ok": True,
                "gateway_latency_ms": 42.125,
                "database_latency_ms": "invalid",
                "provider_available": 1,
                "provider_total": 2,
            },
            {
                "sample_ts": "invalid",
                "gateway_latency_ms": "invalid",
                "provider_available": "invalid",
                "provider_total": object(),
            },
        ]
    )
    set_public_team_data(
        [
            {
                "id": "1102884420207255653",
                "display_name": "Average",
                "avatar_url": "https://cdn.example/avatar.png",
            },
            {
                "id": "not-a-snowflake",
                "display_name": "Invalid",
                "avatar_url": "http://insecure.example/avatar.png",
            },
        ]
    )

    payload = get_public_bot_payload()
    assert payload["online"] is True
    assert payload["status"] == "Operational"
    assert payload["version"] == "3.4.1"
    assert payload["member_count"] == 2500
    assert payload["uptime_percentage"] == 99.875
    assert payload["uptime_tracking_since_ts"] == 1200
    assert payload["schema_version"] == 4
    assert payload["health_history"][0]["gateway_latency_ms"] == 42.12
    assert payload["health_history"][0]["database_latency_ms"] is None
    assert len(payload["health_history"]) == 1
    assert any(item["name"] == "Database" for item in payload["systems"])
    provider = next(
        item for item in payload["systems"] if item["name"] == "GD validation providers"
    )
    assert provider["status"] == "degraded"
    assert payload["service_started_ts"] == payload["process_started_ts"]
    assert payload["service_uptime_seconds"] == payload["process_uptime_seconds"]
    assert payload["discord_connected_since_ts"] == payload["online_since_ts"]
    assert (
        payload["discord_connection_uptime_seconds"]
        == payload["online_uptime_seconds"]
    )
    assert "detail" not in payload

    body, content_type, cache_control, public_api = _response_for_path(
        "/api/bot?cache=no"
    )
    decoded = json.loads(body)
    assert decoded["current_release"]["version"] == "3.4.1"
    assert content_type.startswith("application/json")
    assert cache_control == "no-store"
    assert public_api is True

    team = get_public_team_payload()
    assert team == {
        "schema_version": 1,
        "count": 1,
        "members": [
            {
                "id": "1102884420207255653",
                "display_name": "Average",
                "avatar_url": "https://cdn.example/avatar.png",
            }
        ],
    }
    team_body, team_type, team_cache, team_public = _response_for_path("/api/team")
    assert json.loads(team_body)["count"] == 1
    assert team_type.startswith("application/json")
    assert team_cache == "public, max-age=300"
    assert team_public is True


def test_public_level_payload_is_privacy_filtered_and_versioned():
    set_public_level_data(
        [
            {
                "level_id": "111111111",
                "level_name": "Example",
                "uploader_name": "Creator",
                "recommended_at": 1234,
                "recommendation_type": "legendary",
                "public_queue_state": "queued",
                "public_priority_band": "high_priority",
                "priority_complete": True,
                "public_priority_status": "ranked",
                "public_outreach_state": "queued_for_outreach",
                "public_outcome_state": "unknown",
                "last_updated_at": 1300,
                "requester_id": "private",
                "reviewer_id": "private",
                "review_text": "private",
                "priority_points": 99,
                "prestige_component_f": 10,
                "creator_component_g": 3,
                "waiting_component_h": 2,
                "current_creator_points": 0,
                "private_target_label": "private",
                "route_type": "direct",
                "private_notes": "private",
            }
        ]
    )

    payload = get_public_level_payload("111111111")
    assert payload == {
        "schema_version": 2,
        "level_id": "111111111",
        "level_name": "Example",
        "uploader_name": "Creator",
        "recommendation_type": "legendary",
        "public_queue_state": "queued",
        "public_priority_band": "high_priority",
        "priority_complete": True,
        "public_priority_status": "ranked",
        "public_outreach_state": "queued_for_outreach",
        "public_outcome_state": "unknown",
        "recommended_at": 1234,
        "last_updated_at": 1300,
    }
    assert not {
        "queue_position",
        "active_queue_total",
        "priority_points",
        "prestige_component_f",
        "creator_component_g",
        "waiting_component_h",
        "current_creator_points",
        "requester_id",
        "reviewer_id",
        "review_text",
        "private_target_label",
        "route_type",
        "private_notes",
    } & payload.keys()

    body, content_type, cache_control, public_api = _response_for_path(
        "/api/level/111111111?cache=no"
    )
    assert json.loads(body)["recommendation_type"] == "legendary"
    assert content_type.startswith("application/json")
    assert cache_control == "public, max-age=30"
    assert public_api is True
    alias_body, *_ = _response_for_path("/api/levels/111111111")
    assert json.loads(alias_body) == json.loads(body)

    missing, *_ = _response_for_path("/api/level/222222222")
    assert json.loads(missing)["error"] == "level_not_found"
    set_public_level_data([])


def test_public_level_payload_removes_priority_when_not_actively_ranked():
    set_public_level_data(
        [
            {
                "level_id": "111111112",
                "level_name": "Rated Example",
                "recommendation_type": "mythic",
                "public_queue_state": "rated",
                "public_priority_band": "top_priority",
                "public_outreach_state": "outreach_complete",
                "public_outcome_state": "rated",
            }
        ]
    )

    payload = get_public_level_payload("111111112")

    assert payload["public_priority_band"] is None
    assert payload["public_outreach_state"] == "outreach_complete"
    assert payload["public_outcome_state"] == "rated"
    set_public_level_data([])


def test_public_level_payload_preserves_unknown_states():
    set_public_level_data(
        [
            {
                "level_id": "111111113",
                "level_name": "Unknown Example",
                "recommendation_type": "rate",
                "public_queue_state": "unexpected-private-state",
                "public_priority_band": "not-a-band",
                "public_outreach_state": "assume-success",
                "public_outcome_state": "assume-rated",
            }
        ]
    )

    payload = get_public_level_payload("111111113")

    assert payload["public_queue_state"] == "unknown"
    assert payload["public_priority_band"] is None
    assert payload["public_outreach_state"] == "unknown"
    assert payload["public_outcome_state"] == "unknown"
    assert "uploader_name" not in payload
    set_public_level_data([])


def test_public_probability_projection_allows_active_components_without_shadow_leakage():
    set_public_level_data(
        [
            {
                "level_id": "111111114",
                "level_name": "Access Only",
                "recommendation_type": "epic",
                "public_queue_state": "queued",
                "probability": {
                    "status": "active",
                    "access_probability_percent": 63,
                    "access_credible_interval_90_percent": [48, 76],
                    "access_evidence_strength": "moderate",
                    "raw_alpha": 12,
                    "private_target_label": "never public",
                },
            },
            {
                "level_id": "111111115",
                "level_name": "Shadow",
                "recommendation_type": "rate",
                "public_queue_state": "queued",
                "probability": {
                    "status": "provisional",
                    "access_probability_percent": 88,
                    "access_credible_interval_90_percent": [80, 95],
                },
            },
        ]
    )
    active = get_public_level_payload("111111114")
    assert active["probability"] == {
        "status": "active",
        "generated_at": 0,
        "data_cutoff": 0,
        "access_probability_percent": 63,
        "access_credible_interval_90_percent": [48, 76],
        "access_evidence_strength": "moderate",
    }
    assert "raw_alpha" not in json.dumps(active)
    assert "private_target_label" not in json.dumps(active)
    assert "probability" not in get_public_level_payload("111111115")
    set_public_level_data([])


def test_service_uptime_survives_discord_gateway_reconnect(monkeypatch):
    clock = [2_000]
    monkeypatch.setattr(keepalive_module, "_process_started_ts", 1_000)
    monkeypatch.setattr(keepalive_module.time, "time", lambda: clock[0])

    set_keepalive_status("reconnecting")
    set_keepalive_status("online")
    clock[0] = 2_060
    first = get_public_bot_payload()

    set_keepalive_status("reconnecting")
    clock[0] = 2_075
    set_keepalive_status("online")
    reconnected = get_public_bot_payload()

    assert first["service_uptime_seconds"] == 1_060
    assert first["discord_connection_uptime_seconds"] == 60
    assert reconnected["service_uptime_seconds"] == 1_075
    assert reconnected["discord_connection_uptime_seconds"] == 0
    assert reconnected["service_started_ts"] == first["service_started_ts"]
    set_keepalive_status("starting")


@pytest.mark.asyncio
async def test_release_only_enters_public_feed_after_owner_approval(tmp_path):
    db = Database(str(tmp_path / "bot.db"))
    await db.connect()
    owner = _Owner()
    bot = _Bot(db, _Config(tmp_path / "release.json"), owner)
    cog = ReleaseCog(bot)
    set_public_release_data([])

    ok, message = await cog.propose_release(
        version="3.20.1",
        title="Public bot page",
        summary="A clearer view of Avenue Guard",
        changes="- Added live status\n- Added recent update notes",
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
        "SELECT status,decided_by,approval_delivery_status "
        "FROM bot_releases WHERE version='3.20.1'"
    )
    assert row["status"] == "approved"
    assert row["decided_by"] == OWNER_ID
    assert row["approval_delivery_status"] == "completed"
    assert get_public_bot_payload()["version"] == "3.20.1"
    approval_message.edit.assert_awaited_once()
    await db.close()


@pytest.mark.asyncio
async def test_manifest_startup_does_not_repeat_ambiguous_approval_delivery(tmp_path):
    manifest = tmp_path / "release.json"
    manifest.write_text(
        json.dumps(
            {
                "version": "3.20.1",
                "title": "No duplicate approval",
                "summary": "",
                "changes": ["Durable delivery claim"],
            }
        ),
        encoding="utf-8",
    )
    db = Database(str(tmp_path / "bot.db"))
    await db.connect()
    await db.execute(
        "INSERT INTO bot_releases("
        "version,title,summary,changes_json,status,source,created_by,created_ts,"
        "approval_delivery_status,approval_attempted_ts"
        ") VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            "3.20.1",
            "No duplicate approval",
            "",
            '["Durable delivery claim"]',
            "pending",
            "deployment_manifest",
            OWNER_ID,
            1,
            "sending",
            1,
        ),
    )
    owner = _Owner()
    cog = ReleaseCog(_Bot(db, _Config(manifest), owner))

    await cog._ensure_manifest_proposal()

    assert owner.messages == []
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


@pytest.mark.asyncio
async def test_newer_release_panel_reconciles_stale_saved_message_id(
    tmp_path,
):
    db = Database(str(tmp_path / "bot.db"))
    await db.connect()
    owner = _Owner()
    bot = _Bot(db, _Config(tmp_path / "release.json"), owner)
    cog = ReleaseCog(bot)

    ok, _ = await cog.propose_release(
        version="3.20.1",
        title="Status refinements",
        summary="",
        changes="Accurate members | Persistent uptime",
        created_by=OWNER_ID,
    )
    assert ok is True
    approval_message = owner.messages[0][0]
    await db.execute(
        "UPDATE bot_releases SET approval_message_id=? WHERE version=?",
        (approval_message.id - 1, "3.20.1"),
    )
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=OWNER_ID),
        message=approval_message,
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await cog.handle_release_decision(interaction, approved=True)

    row = await db.fetchone(
        "SELECT status,approval_message_id FROM bot_releases WHERE version=?",
        ("3.20.1",),
    )
    assert row["status"] == "approved"
    assert row["approval_message_id"] == approval_message.id
    assert bot._last_release_panel_reconciliation["proposal_id"] > 0
    assert (
        bot._last_release_panel_reconciliation["clicked_message_id"]
        == approval_message.id
    )
    await db.close()


@pytest.mark.asyncio
async def test_older_release_panel_is_still_rejected(tmp_path):
    db = Database(str(tmp_path / "bot.db"))
    await db.connect()
    owner = _Owner()
    bot = _Bot(db, _Config(tmp_path / "release.json"), owner)
    cog = ReleaseCog(bot)

    ok, _ = await cog.propose_release(
        version="3.20.1",
        title="Status refinements",
        summary="",
        changes="Accurate members",
        created_by=OWNER_ID,
    )
    assert ok is True
    old_message = owner.messages[0][0]
    await db.execute(
        "UPDATE bot_releases SET approval_message_id=? WHERE version=?",
        (old_message.id + (1 << 22), "3.20.1"),
    )
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=OWNER_ID),
        message=old_message,
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await cog.handle_release_decision(interaction, approved=True)

    row = await db.fetchone(
        "SELECT status FROM bot_releases WHERE version=?",
        ("3.20.1",),
    )
    assert row["status"] == "pending"
    sent_text = interaction.followup.send.await_args.args[0]
    assert "replaced by a newer one" in sent_text
    await db.close()


@pytest.mark.asyncio
async def test_rejecting_old_placeholder_version_proposes_manifest_version(
    tmp_path,
):
    manifest = tmp_path / "release.json"
    manifest.write_text(
        json.dumps(
            {
                "version": "3.20.1",
                "title": "Correct version",
                "summary": "",
                "changes": ["Uses the agreed version sequence"],
            }
        ),
        encoding="utf-8",
    )
    db = Database(str(tmp_path / "bot.db"))
    await db.connect()
    owner = _Owner()
    bot = _Bot(db, _Config(manifest), owner)
    cog = ReleaseCog(bot)

    ok, _ = await cog.propose_release(
        version="4.2.0",
        title="Old placeholder",
        summary="",
        changes=["Incorrect placeholder version"],
        created_by=OWNER_ID,
    )
    assert ok is True
    old_message = owner.messages[0][0]
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=OWNER_ID),
        message=old_message,
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await cog.handle_release_decision(interaction, approved=False)

    rows = await db.fetchall(
        "SELECT version,status FROM bot_releases ORDER BY id"
    )
    assert [(row["version"], row["status"]) for row in rows] == [
        ("4.2.0", "rejected"),
        ("3.20.1", "pending"),
    ]
    assert len(owner.messages) == 2
    await db.close()


@pytest.mark.asyncio
async def test_older_pending_release_cannot_regress_published_version(tmp_path):
    db = Database(str(tmp_path / "bot.db"))
    await db.connect()
    owner = _Owner()
    bot = _Bot(db, _Config(tmp_path / "release.json"), owner)
    cog = ReleaseCog(bot)

    for version in ("3.20.1", "3.20.2"):
        ok, _ = await cog.propose_release(
            version=version,
            title=f"Version {version}",
            summary="",
            changes=["Versioned change"],
            created_by=OWNER_ID,
        )
        assert ok is True

    newer_interaction = SimpleNamespace(
        user=SimpleNamespace(id=OWNER_ID),
        message=owner.messages[1][0],
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    await cog.handle_release_decision(newer_interaction, approved=True)

    older_interaction = SimpleNamespace(
        user=SimpleNamespace(id=OWNER_ID),
        message=owner.messages[0][0],
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    await cog.handle_release_decision(older_interaction, approved=True)

    rows = await db.fetchall(
        "SELECT version,status,error_text FROM bot_releases ORDER BY version"
    )
    assert [(row["version"], row["status"]) for row in rows] == [
        ("3.20.1", "rejected"),
        ("3.20.2", "approved"),
    ]
    assert "Superseded by published version 3.20.2" == rows[0]["error_text"]
    sent_text = older_interaction.followup.send.await_args.args[0]
    assert "marked as superseded" in sent_text
    await db.close()


@pytest.mark.asyncio
async def test_release_rejects_versions_at_or_below_agreed_baseline(tmp_path):
    db = Database(str(tmp_path / "bot.db"))
    await db.connect()
    owner = _Owner()
    bot = _Bot(db, _Config(tmp_path / "release.json"), owner)
    cog = ReleaseCog(bot)

    ok, message = await cog.propose_release(
        version="3.18.7",
        title="Old release",
        summary="",
        changes=["Would reuse the agreed baseline"],
        created_by=OWNER_ID,
    )

    assert ok is False
    assert "newer than" in message
    assert owner.messages == []
    await db.close()


@pytest.mark.asyncio
async def test_public_member_count_only_uses_configured_gd_avenue_guild(tmp_path):
    db = Database(str(tmp_path / "bot.db"))
    await db.connect()
    owner = _Owner()
    bot = _Bot(db, _Config(tmp_path / "release.json"), owner)
    target_guild = SimpleNamespace(
        id=717003826288394271,
        member_count=2431,
        members=[],
    )
    unrelated_guild = SimpleNamespace(
        id=999,
        member_count=8000,
        members=[],
    )
    bot.guilds = [target_guild, unrelated_guild]
    bot.user = SimpleNamespace(
        display_avatar=SimpleNamespace(
            url="https://cdn.discordapp.com/avatars/1454985687177887866/live.webp"
        )
    )
    set_keepalive_status("online")

    cog = ReleaseCog(bot)
    await cog.refresh_public_metrics()

    payload = get_public_bot_payload()
    assert payload["member_count"] == 2431
    assert payload["guild_count"] == 1
    assert payload["avatar_url"].endswith("/live.webp")
    await db.close()


@pytest.mark.asyncio
async def test_public_health_and_team_caches_use_sanitized_bot_data(tmp_path):
    db = Database(str(tmp_path / "bot.db"))
    await db.connect()
    owner = _Owner()
    owner.display_name = "Average"
    owner.name = "average"
    owner.display_avatar = SimpleNamespace(
        url="https://cdn.discordapp.com/avatars/1102884420207255653/current.webp"
    )
    config = _Config(tmp_path / "release.json")
    config.data["release_updates"]["public_team_user_ids"] = [str(OWNER_ID)]
    bot = _Bot(db, config, owner)
    await db.execute(
        "INSERT INTO health_metrics(guild_id,sample_ts,metric_type,value,payload_json) "
        "VALUES(?,?,'runtime',?,?)",
        (
            717003826288394271,
            1234,
            1.0,
            json.dumps(
                {
                    "db_ok": True,
                    "db_probe_ms": 3.5,
                    "gateway_latency_ms": 37.25,
                    "tasks": {"priority.maintenance": "running"},
                    "providers": {
                        "boomlings": {"enabled": True, "available": True},
                        "gdhistory": {"enabled": True, "available": False},
                    },
                }
            ),
        ),
    )

    cog = ReleaseCog(bot)
    await cog.refresh_public_health_cache()
    await cog.refresh_public_team_cache()

    health = get_public_bot_payload()["health_history"]
    assert health == [
        {
            "sample_ts": 1234,
            "healthy": True,
            "database_ok": True,
            "gateway_latency_ms": 37.25,
            "database_latency_ms": 3.5,
            "provider_available": 1,
            "provider_total": 2,
        }
    ]
    assert get_public_team_payload()["members"] == [
        {
            "id": str(OWNER_ID),
            "display_name": "Average",
            "avatar_url": (
                "https://cdn.discordapp.com/avatars/"
                "1102884420207255653/current.webp"
            ),
        }
    ]
    await db.close()


@pytest.mark.asyncio
async def test_auxiliary_public_cache_failure_does_not_block_other_cache(tmp_path):
    db = Database(str(tmp_path / "bot.db"))
    await db.connect()
    cog = ReleaseCog(_Bot(db, _Config(tmp_path / "release.json"), _Owner()))
    cog.refresh_public_health_cache = AsyncMock(
        side_effect=release_module.DatabaseBusyError("database busy")
    )
    cog.refresh_public_team_cache = AsyncMock()

    await cog.refresh_public_auxiliary_caches()

    cog.refresh_public_health_cache.assert_awaited_once()
    cog.refresh_public_team_cache.assert_awaited_once()
    assert cog._last_public_auxiliary_refresh > 0
    await db.close()


@pytest.mark.asyncio
async def test_uptime_percentage_persists_and_counts_restart_gap_as_offline(
    tmp_path,
    monkeypatch,
):
    db = Database(str(tmp_path / "bot.db"))
    await db.connect()
    owner = _Owner()
    bot = _Bot(db, _Config(tmp_path / "release.json"), owner)
    clock = [1000]
    monkeypatch.setattr(release_module.time, "time", lambda: clock[0])

    first_process = ReleaseCog(bot)
    await first_process._initialize_uptime_tracker()
    clock[0] = 1060
    await first_process.record_uptime_sample(online=True)

    clock[0] = 1120
    restarted_process = ReleaseCog(bot)
    await restarted_process._initialize_uptime_tracker()
    snapshot = await restarted_process.uptime_snapshot(online=False)
    row = await db.fetchone("SELECT * FROM bot_uptime_tracker WHERE id=1")

    assert row["tracking_started_ts"] == 1000
    assert row["observed_seconds"] == 120
    assert row["online_seconds"] == 60
    assert snapshot["percentage"] == 50.0
    await db.close()
