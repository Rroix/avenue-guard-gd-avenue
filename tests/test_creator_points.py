from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.priority_system import PrioritySystemService
from utils.config import Config
from utils.creator_points import (
    canonical_identity,
    creator_points_settings,
    parse_gdbrowser_level_html,
    parse_gdbrowser_profile_api,
    parse_gdbrowser_profile_html,
    select_creator_points,
)
from utils.db import Database
from utils.priority_system import priority_settings, score_components


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
GUILD_ID = 717003826288394271


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def observation(
    provider: str,
    cp: int | None,
    *,
    method: str = "profile",
    account_id: int = 1234,
    username: str = "Creator",
    observed_at: int = 100,
    archival: bool = False,
) -> dict:
    return {
        "provider": provider,
        "method": method,
        "username": username,
        "account_id": account_id,
        "player_id": 5678,
        "creator_points": cp,
        "observed_at": observed_at,
        "success": cp is not None,
        "archival": archival,
        "response_fingerprint": f"{provider}-{method}-{cp}-{observed_at}",
    }


def make_bot(db: Database):
    return SimpleNamespace(
        db=db,
        config=Config(str(ROOT / "config.json")),
        get_cog=lambda _name: None,
    )


async def insert_pending(db: Database, *, level_id: str, message_id: int, send_type: str = "epic") -> int:
    settings = priority_settings(Config(str(ROOT / "config.json")).data)
    score = score_components(send_type, None, 0, settings)
    return await db.execute_insert(
        "INSERT INTO level_outreach_queue(guild_id,wave_id,requester_id,request_message_id,level_id,send_type,"
        "queued_ts,prestige_t,prestige_component_f,waiting_cycles,waiting_component_h,priority_complete,model_version,"
        "queue_state,correlation_id,updated_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            GUILD_ID,
            1,
            100 + message_id,
            message_id,
            level_id,
            send_type,
            100,
            score["prestige_t"],
            score["prestige_component_f"],
            0,
            score["waiting_component_h"],
            0,
            settings.model_version,
            "queued",
            f"cp-test-{message_id}",
            100,
        ),
    )


def test_gdbrowser_level_parser_uses_creator_profile_link_not_other_names():
    parsed = parse_gdbrowser_level_html(fixture("gdbrowser_level_normal.html"), "101935961")
    assert parsed["ok"] is True
    assert parsed["username"] == "iIZEKIi"
    assert parsed["account_id"] == 23644534
    assert parsed["profile_path"] == "/u/23644534."


def test_gdbrowser_collab_parser_keeps_actual_online_uploader():
    parsed = parse_gdbrowser_level_html(fixture("gdbrowser_level_collab.html"), "123456789")
    assert parsed["ok"] is True
    assert parsed["username"] == "ActualUploader"
    assert parsed["profile_path"] == "/profile/ActualUploader"


def test_gdbrowser_profile_parser_distinguishes_zero_positive_and_missing_player_id():
    zero = parse_gdbrowser_profile_html(
        fixture("gdbrowser_profile_zero.html"),
        expected_username="zerocreator",
        expected_account_id=1234,
    )
    positive = parse_gdbrowser_profile_html(
        fixture("gdbrowser_profile_positive.html"),
        expected_username="iIZEKIi",
        expected_account_id=23644534,
    )
    account_only = parse_gdbrowser_profile_html(
        fixture("gdbrowser_profile_no_player.html"),
        expected_account_id=4321,
    )
    assert zero["ok"] is True and zero["creator_points"] == 0
    assert positive["ok"] is True and positive["creator_points"] == 2
    assert positive["player_id"] == 210263169
    assert account_only["ok"] is True and account_only["player_id"] is None
    assert account_only["creator_points"] == 7


def test_gdbrowser_parsers_reject_malformed_error_and_identity_mismatch():
    assert parse_gdbrowser_level_html(fixture("gdbrowser_malformed.html"), "101935961")["ok"] is False
    assert parse_gdbrowser_level_html(fixture("gdbrowser_error.html"), "101935961")["ok"] is False
    mismatch = parse_gdbrowser_profile_html(
        fixture("gdbrowser_profile_zero.html"), expected_account_id=9999
    )
    missing_expected_account = parse_gdbrowser_profile_api(
        {"username": "Creator", "playerID": "12", "cp": 0},
        expected_account_id=1234,
    )
    assert mismatch["error_category"] == "identity_mismatch"
    assert missing_expected_account["error_category"] == "identity_mismatch"


def test_identity_matching_is_exact_unicode_casefold_without_fuzzy_merge():
    identity = canonical_identity(
        [
            {"provider": "one", "username": " Creator ", "account_id": None, "player_id": None},
            {"provider": "two", "username": "creator", "account_id": None, "player_id": None},
        ]
    )
    assert identity and identity["confidence"] == "username_consensus"
    assert canonical_identity(
        [
            {"provider": "one", "username": "Creator", "account_id": 1, "player_id": None},
            {"provider": "two", "username": "CreatorTwo", "account_id": 2, "player_id": None},
        ]
    )["account_id"] in {1, 2}


def test_cp_precedence_direct_consensus_conflict_and_fresh_html_over_archival():
    identity = {"username": "Creator", "account_id": 1234, "player_id": 5678}
    direct = select_creator_points(
        [
            observation("gdbrowser", 2, method="html_profile"),
            observation("boomlings", 3, method="direct_profile"),
            observation("gdhistory", 2, archival=True),
        ],
        identity,
    )
    assert direct["creator_points"] == 3
    assert direct["confidence"] == "direct_authoritative"

    consensus = select_creator_points(
        [observation("gdbrowser", 2), observation("gdrateplus", 2)], identity
    )
    assert consensus["creator_points"] == 2 and consensus["confidence"] == "consensus"

    conflict = select_creator_points(
        [observation("gdbrowser", 0, method="api_profile"), observation("gdrateplus", 2)],
        identity,
    )
    assert conflict == {"resolved": False, "state": "conflict", "creator_points": None}

    fresh = select_creator_points(
        [
            observation("gdbrowser", 3, method="html_profile", observed_at=500),
            observation("gdhistory", 1, observed_at=100, archival=True),
        ],
        identity,
    )
    assert fresh["creator_points"] == 3 and fresh["source"] == "gdbrowser_html"


def test_cached_observation_does_not_fake_independent_provider_consensus():
    identity = {"username": "Creator", "account_id": 1234, "player_id": 5678}
    result = select_creator_points(
        [observation("cache", 2), observation("gdrateplus", 3)], identity
    )
    assert result["resolved"] is False
    assert result["state"] == "conflict"


def test_creator_points_config_defaults_are_valid_and_include_daily_backoff():
    settings = creator_points_settings(Config(str(ROOT / "config.json")).data)
    assert settings.enabled is True
    assert settings.current_cp_ttl_seconds == 21600
    assert settings.retry_schedule_seconds == (30, 120, 600, 3600, 21600, 86400)
    assert settings.max_concurrency == 3


def test_creator_points_config_rejects_enabled_resolver_without_providers():
    data = json.loads(json.dumps(Config(str(ROOT / "config.json")).data))
    raw = data["priority_system"]["creator_points"]
    raw.update(
        {
            "resolution_enabled": True,
            "gdbrowser_html_enabled": False,
            "gdbrowser_api_enabled": False,
            "boomlings_enabled": False,
            "gdhistory_enabled": False,
            "gdrateplus_enabled": False,
        }
    )
    with pytest.raises(ValueError, match="at least one enabled provider"):
        creator_points_settings(data)


@pytest.mark.asyncio
async def test_level_singleflight_deduplicates_concurrent_resolution(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "singleflight.db"))
    await db.connect()
    try:
        resolver = PrioritySystemService(make_bot(db)).creator_points
        calls = 0

        async def fake_resolve(level_id, *, force):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.02)
            return {"identity": {"username": "Creator"}, "accepted": {}, "observations": []}

        monkeypatch.setattr(resolver, "_resolve_level", fake_resolve)
        results = await asyncio.gather(
            *(resolver._singleflight_level("101935961", force=False) for _ in range(5))
        )
        assert calls == 1
        assert len(results) == 5
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_same_creator_profile_singleflight_updates_five_levels_atomically(tmp_path):
    db = Database(str(tmp_path / "shared-creator.db"))
    await db.connect()
    try:
        resolver = PrioritySystemService(make_bot(db)).creator_points
        queue_ids = [
            await insert_pending(db, level_id=str(200000000 + index), message_id=50 + index)
            for index in range(5)
        ]
        for queue_id in queue_ids:
            await db.execute(
                "UPDATE level_outreach_queue SET uploader_name='Creator',uploader_account_id=1234 WHERE id=?",
                (queue_id,),
            )

        calls = 0

        async def fetch_profile():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.02)
            return observation("gdbrowser", 0, method="html_profile", observed_at=1000)

        profiles = await asyncio.gather(
            *(resolver._singleflight_profile("gdbrowser:account:1234", fetch_profile) for _ in range(5))
        )
        assert calls == 1
        assert [item["creator_points"] for item in profiles] == [0, 0, 0, 0, 0]

        identity = {
            "username": "Creator",
            "account_id": 1234,
            "player_id": 5678,
            "confidence": "account_id",
            "creator_key": "account:1234",
        }
        accepted = {
            "resolved": True,
            "state": "resolved",
            "creator_points": 0,
            "source": "gdbrowser_html",
            "confidence": "verified_single_source",
            "observation": profiles[0],
        }
        first = await db.fetchone(
            "SELECT * FROM level_outreach_queue WHERE id=?", (queue_ids[0],)
        )
        await resolver._persist_resolution(first, identity, accepted, profiles[:1])

        rows = await db.fetchall(
            "SELECT current_creator_points,creator_component_g,priority_points,priority_complete "
            "FROM level_outreach_queue WHERE id IN (?,?,?,?,?) ORDER BY id",
            tuple(queue_ids),
        )
        assert len(rows) == 5
        assert all(row["current_creator_points"] == 0 for row in rows)
        assert all(row["creator_component_g"] == pytest.approx(3.2188758249) for row in rows)
        assert all(row["priority_points"] is not None for row in rows)
        assert all(row["priority_complete"] == 1 for row in rows)
        snapshots = await db.fetchone(
            "SELECT COUNT(*) AS c FROM level_outreach_cp_snapshots WHERE queue_id IN (?,?,?,?,?)",
            tuple(queue_ids),
        )
        assert int(snapshots["c"]) == 5
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_provider_observation_dedupes_one_window_but_preserves_later_verification(tmp_path):
    db = Database(str(tmp_path / "observation-window.db"))
    await db.connect()
    try:
        resolver = PrioritySystemService(make_bot(db)).creator_points
        queue_id = await insert_pending(db, level_id="202020202", message_id=70)
        first = observation("gdbrowser", 0, method="html_profile", observed_at=900)
        first["response_fingerprint"] = "same-response"
        duplicate = {**first, "observed_at": 901}
        later = {**first, "observed_at": 1201}
        await resolver._persist_observations(
            queue_id, "202020202", [first, duplicate, later]
        )
        count = await db.fetchone(
            "SELECT COUNT(*) AS c FROM creator_points_provider_observations WHERE queue_id=?",
            (queue_id,),
        )
        assert int(count["c"]) == 2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_zero_cp_resolves_atomically_and_cp_change_keeps_old_snapshot(tmp_path):
    db = Database(str(tmp_path / "zero-change.db"))
    await db.connect()
    try:
        service = PrioritySystemService(make_bot(db))
        resolver = service.creator_points
        queue_id = await insert_pending(db, level_id="101935961", message_id=1)
        await resolver.enqueue(queue_id)
        identity = {
            "username": "ZeroCreator",
            "account_id": 1234,
            "player_id": 5678,
            "confidence": "account_id",
            "creator_key": "account:1234",
        }
        zero_observation = observation("gdbrowser", 0, method="html_profile", observed_at=1000)
        row = await db.fetchone("SELECT * FROM level_outreach_queue WHERE id=?", (queue_id,))
        await resolver._persist_resolution(
            row,
            identity,
            {
                "creator_points": 0,
                "source": "gdbrowser_html",
                "confidence": "verified_single_source",
                "observation": zero_observation,
            },
            [zero_observation],
        )
        resolved = await db.fetchone("SELECT * FROM level_outreach_queue WHERE id=?", (queue_id,))
        assert resolved["current_creator_points"] == 0
        assert resolved["creator_component_g"] == pytest.approx(3.2188758249)
        assert resolved["priority_complete"] == 1
        assert resolved["priority_points"] is not None

        one_observation = observation("gdbrowser", 1, method="html_profile", observed_at=2000)
        await resolver._persist_resolution(
            resolved,
            identity,
            {
                "creator_points": 1,
                "source": "gdbrowser_html",
                "confidence": "verified_single_source",
                "observation": one_observation,
            },
            [one_observation],
        )
        changed = await db.fetchone("SELECT * FROM level_outreach_queue WHERE id=?", (queue_id,))
        snapshots = await db.fetchall(
            "SELECT creator_points FROM level_outreach_cp_snapshots WHERE queue_id=? ORDER BY checked_ts",
            (queue_id,),
        )
        assert changed["creator_points_at_recommendation"] == 0
        assert changed["current_creator_points"] == 1
        assert [item["creator_points"] for item in snapshots] == [0, 1]
        events = {row["event"] for row in await db.fetchall("SELECT event FROM workflow_events")}
        assert {"cp_resolved", "cp_changed", "cp_identity_resolved"} <= events
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_provider_failure_keeps_unknown_null_and_schedules_retry(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "failure.db"))
    await db.connect()
    try:
        resolver = PrioritySystemService(make_bot(db)).creator_points
        queue_id = await insert_pending(db, level_id="123456789", message_id=2)
        await resolver.enqueue(queue_id)
        job = await db.fetchone("SELECT * FROM creator_points_resolution_jobs WHERE queue_id=?", (queue_id,))
        monkeypatch.setattr("services.creator_points.random.randint", lambda _a, _b: 0)
        before = int(job["next_attempt_ts"])
        await resolver._schedule_failure(job, "timeout", "all providers timed out", minimum_delay=120)
        row = await db.fetchone("SELECT * FROM level_outreach_queue WHERE id=?", (queue_id,))
        retried = await db.fetchone("SELECT * FROM creator_points_resolution_jobs WHERE queue_id=?", (queue_id,))
        assert row["current_creator_points"] is None
        assert row["creator_component_g"] is None
        assert row["priority_points"] is None
        assert row["priority_complete"] == 0
        assert retried["state"] == "providers_unavailable"
        assert int(retried["next_attempt_ts"]) >= before + 120
        assert await db.fetchone(
            "SELECT 1 FROM workflow_events WHERE event='cp_resolution_retry_scheduled'"
        )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_bootstrap_repairs_false_priority_and_enqueues_active_pending_rows(tmp_path):
    db = Database(str(tmp_path / "bootstrap.db"))
    await db.connect()
    try:
        resolver = PrioritySystemService(make_bot(db)).creator_points
        queue_id = await insert_pending(db, level_id="222222222", message_id=3, send_type="rate")
        await db.execute(
            "UPDATE level_outreach_queue SET priority_points=999,creator_component_g=999,priority_complete=0 WHERE id=?",
            (queue_id,),
        )
        assert await resolver.bootstrap(GUILD_ID) == 1
        row = await db.fetchone("SELECT * FROM level_outreach_queue WHERE id=?", (queue_id,))
        job = await db.fetchone("SELECT * FROM creator_points_resolution_jobs WHERE queue_id=?", (queue_id,))
        assert row["priority_points"] is None
        assert row["creator_component_g"] is None
        assert row["priority_complete"] == 0
        assert row["creator_points_status"] == "pending"
        assert job and job["state"] == "pending"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_level_identity_cache_has_separate_long_ttl(tmp_path):
    db = Database(str(tmp_path / "identity-cache.db"))
    await db.connect()
    try:
        resolver = PrioritySystemService(make_bot(db)).creator_points
        parsed = parse_gdbrowser_level_html(fixture("gdbrowser_level_normal.html"), "101935961")
        await resolver._save_level_identity("101935961", parsed)
        cached = await resolver._cached_level_identity("101935961")
        assert cached and cached["username"] == "iIZEKIi"
        assert cached["account_id"] == 23644534
        row = await db.fetchone("SELECT expires_ts-observed_at AS ttl FROM creator_level_identities")
        assert int(row["ttl"]) == resolver.settings.level_identity_ttl_seconds
    finally:
        await db.close()
