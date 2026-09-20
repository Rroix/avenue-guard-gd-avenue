from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from services.staff_portal import (
    PortalError,
    StaffPortalService,
    _json_safe,
    _youtube_embed_url,
)
from utils.db import Database
from utils.keepalive import get_public_levels_payload, set_public_level_data
from utils.priority_system import priority_settings, score_components
from utils.staff_auth import (
    StaffPrincipal,
    canonical_role,
    capability_set,
    resolve_staff_role,
)

GUILD_ID = 717003826288394271
JUDGE_ID = 101
HEAD_ID = 102
OWNER_ID = 1102884420207255653
DEV_ID = 1102884420207255552
ADMIN_ID = 103
JUDGE_ROLE = 785212232786640966
HEAD_ROLE = 1430214323720163498
ADMIN_ROLE = 1524000000000000001


def test_production_portal_admin_role_is_configured_as_string():
    with open("config.json", encoding="utf-8") as config_file:
        config = json.load(config_file)
    assert "901431567719731230" in config["staff_portal"]["admin_role_ids"]


class PortalConfig:
    def __init__(self):
        with open("config.json", encoding="utf-8") as config_file:
            self.data = json.load(config_file)
        self.data["guild"]["allowed_guild_id"] = GUILD_ID
        self.data["staff_portal"].update(
            {
                "enabled": True,
                "judge_role_ids": [JUDGE_ROLE],
                "head_judge_role_ids": [HEAD_ROLE],
                "admin_role_ids": [ADMIN_ROLE],
                "owner_role_ids": [],
                "owner_user_ids": [OWNER_ID],
                "dev_user_ids": [DEV_ID],
            }
        )

    def get(self, *path, default=None):
        current = self.data
        for key in path:
            if not isinstance(current, dict) or key not in current:
                return default
            current = current[key]
        return current if current is not None else default

    def get_int(self, *path, default=0):
        try:
            return int(self.get(*path, default=default))
        except (TypeError, ValueError):
            return default

    def get_int_list(self, *path, default=None):
        value = self.get(*path, default=default or [])
        return [int(item) for item in value]


class FakeMember:
    def __init__(self, user_id, roles):
        self.id = user_id
        self.roles = [SimpleNamespace(id=role_id) for role_id in roles]
        self.display_name = f"Member {user_id}"
        self.name = self.display_name
        self.display_avatar = SimpleNamespace(url=f"https://cdn.example/{user_id}.png")


class FakeGuild:
    def __init__(self, members):
        self.members = list(members)

    def get_member(self, user_id):
        return next((member for member in self.members if member.id == user_id), None)

    async def fetch_member(self, user_id):
        return self.get_member(user_id)


class FakeOutbox:
    def __init__(self):
        self.calls = []

    async def enqueue(self, kind, **kwargs):
        self.calls.append((kind, kwargs))
        return len(self.calls)


def principal(user_id, role):
    role = canonical_role(role)
    role_ids = (
        (HEAD_ROLE,)
        if role == "head_reviewer"
        else (ADMIN_ROLE,)
        if role == "admin"
        else (JUDGE_ROLE,)
        if role == "reviewer"
        else ()
    )
    return StaffPrincipal(
        user_id=user_id,
        guild_id=GUILD_ID,
        display_name=f"Member {user_id}",
        avatar_url="",
        role=role,
        role_ids=role_ids,
        capabilities=capability_set(role),
    )


@pytest_asyncio.fixture
async def portal(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "staff-portal.db"))
    await db.connect()
    members = [
        FakeMember(JUDGE_ID, [JUDGE_ROLE]),
        FakeMember(HEAD_ID, [HEAD_ROLE]),
        FakeMember(ADMIN_ID, [ADMIN_ROLE]),
        FakeMember(OWNER_ID, []),
        FakeMember(DEV_ID, []),
        FakeMember(999, []),
    ]
    guild = FakeGuild(members)
    config = PortalConfig()
    bot = SimpleNamespace(
        db=db,
        config=config,
        outbox=FakeOutbox(),
        get_cog=lambda _name: None,
        get_guild=lambda guild_id: guild if guild_id == GUILD_ID else None,
    )
    monkeypatch.setenv("STAFF_API_TOKEN", "test-service-token")
    service = StaffPortalService(bot)
    yield service, guild
    await db.close()


async def insert_queue(service, *, level_id, message_id, priority, cp=0, state="queued"):
    settings = priority_settings(service.bot.config.data)
    components = score_components("epic", cp, 0, settings)
    return await service.db.execute_insert(
        "INSERT INTO level_outreach_queue(guild_id,wave_id,requester_id,request_message_id,level_id,send_type,"
        "queued_ts,prestige_t,prestige_component_f,current_creator_points,creator_component_g,waiting_cycles,"
        "waiting_component_h,priority_points,priority_complete,model_version,queue_state,correlation_id,updated_ts) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            GUILD_ID,
            1,
            500 + message_id,
            message_id,
            level_id,
            "epic",
            100,
            components["prestige_t"],
            components["prestige_component_f"],
            cp,
            components["creator_component_g"],
            0,
            components["waiting_component_h"],
            priority,
            1 if cp is not None else 0,
            settings.model_version,
            state,
            f"queue-{message_id}",
            100,
        ),
    )


def test_role_capabilities_do_not_trust_browser_labels():
    config = PortalConfig()
    assert resolve_staff_role(JUDGE_ID, [JUDGE_ROLE], config) == "reviewer"
    assert resolve_staff_role(HEAD_ID, [HEAD_ROLE], config) == "head_reviewer"
    assert resolve_staff_role(ADMIN_ID, [ADMIN_ROLE], config) == "admin"
    assert resolve_staff_role(OWNER_ID, [], config) == "owner"
    assert resolve_staff_role(DEV_ID, [], config) == "dev"
    assert "queue.reassign" not in capability_set("judge")
    assert "queue.reassign" in capability_set("head_judge")
    assert "staff.manage" in capability_set("owner")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_id", "expected_role"),
    (
        (JUDGE_ID, "reviewer"),
        (HEAD_ID, "head_reviewer"),
        (ADMIN_ID, "admin"),
        (OWNER_ID, "owner"),
        (DEV_ID, "dev"),
    ),
)
async def test_staff_session_maps_live_discord_roles(portal, user_id, expected_role):
    service, _guild = portal
    created = await service.create_session({"user_id": str(user_id), "purpose": "staff"})
    assert created["user"]["role"] == expected_role
    assert created["user"]["staff_access"] is True
    assert set(created["user"]) == {
        "id",
        "display_name",
        "portal_nickname",
        "discord_display_name",
        "global_display_name",
        "username",
        "avatar_url",
        "role",
        "role_label",
        "staff_access",
        "capabilities",
    }


@pytest.mark.asyncio
async def test_staff_session_denies_nonstaff_and_outside_guild(portal):
    service, _guild = portal
    with pytest.raises(PortalError) as nonstaff:
        await service.create_session({"user_id": 999, "purpose": "staff"})
    assert nonstaff.value.status == 403
    assert nonstaff.value.code == "staff_role_required"

    with pytest.raises(PortalError) as outsider:
        await service.create_session({"user_id": 998, "purpose": "staff"})
    assert outsider.value.status == 403
    assert outsider.value.code == "not_a_member"

    application = await service.create_session({"user_id": 999, "purpose": "apply"})
    assert application["user"]["role"] == "applicant"
    assert application["user"]["staff_access"] is False


@pytest.mark.asyncio
async def test_expired_staff_session_is_rejected(portal):
    service, _guild = portal
    created = await service.create_session({"user_id": JUDGE_ID, "purpose": "staff"})
    await service.db.execute("UPDATE staff_web_sessions SET expires_ts=0")
    with pytest.raises(PortalError) as expired:
        await service.handle_request(
            "GET",
            "/api/staff/session",
            {
                "x-avenue-portal-key": "test-service-token",
                "x-staff-session": created["session_token"],
            },
            b"",
        )
    assert expired.value.status == 401
    assert expired.value.code == "session_expired"


@pytest.mark.asyncio
async def test_session_rechecks_discord_role_and_revokes_staff_access(portal):
    service, guild = portal
    created = await service.create_session({"user_id": JUDGE_ID})
    headers = {
        "x-avenue-portal-key": "test-service-token",
        "x-staff-session": created["session_token"],
    }
    status, payload = await service.handle_request("GET", "/api/staff/session", headers, b"")
    assert status == 200
    assert payload["user"]["role"] == "reviewer"

    guild.get_member(JUDGE_ID).roles = []
    with pytest.raises(PermissionError):
        await service.handle_request("GET", "/api/staff/session", headers, b"")


@pytest.mark.asyncio
async def test_browser_role_claim_is_ignored_and_logout_revokes_session(portal):
    service, _guild = portal
    created = await service.create_session({"user_id": JUDGE_ID, "role": "owner"})
    assert created["user"]["role"] == "reviewer"
    headers = {
        "x-avenue-portal-key": "test-service-token",
        "x-staff-session": created["session_token"],
        "x-csrf-token": created["csrf_token"],
    }
    status, payload = await service.handle_request(
        "DELETE", "/api/staff/session", headers, b""
    )
    assert status == 200
    assert payload == {"ok": True}
    with pytest.raises(PortalError, match="session expired"):
        await service.handle_request("GET", "/api/staff/session", headers, b"")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_id", "role"),
    (
        (JUDGE_ID, "reviewer"),
        (HEAD_ID, "head_reviewer"),
        (ADMIN_ID, "admin"),
        (OWNER_ID, "owner"),
        (DEV_ID, "dev"),
    ),
)
async def test_overview_empty_account_works_for_every_staff_role(portal, user_id, role):
    service, _guild = portal
    payload = await service.overview(principal(user_id, role))
    assert payload["summary"] == {
        "active_claims": 0,
        "stale_claims": 0,
        "tasks_remaining": 0,
        "tasks_due": 0,
        "followups_due": 0,
    }
    assert payload["progress"]["outreach_attempts"] == 0
    assert payload["warnings"] == []


@pytest.mark.asyncio
async def test_overview_returns_partial_data_for_null_actor_and_failed_section(
    portal, monkeypatch
):
    service, _guild = portal
    await service.db.execute(
        "INSERT INTO workflow_events("
        "correlation_id,workflow_type,entity_id,event,guild_id,actor_id,"
        "payload_json,created_ts) VALUES(?,?,?,?,?,NULL,'{}',?)",
        ("overview-null-actor", "test", "overview", "system_event", GUILD_ID, 1),
    )
    original_fetchall = service.db.fetchall

    async def fail_pipeline(sql, params=()):
        if "GROUP BY queue_state" in sql:
            raise RuntimeError("synthetic pipeline failure")
        return await original_fetchall(sql, params)

    error_log = AsyncMock()
    monkeypatch.setattr(service.db, "fetchall", fail_pipeline)
    monkeypatch.setattr("services.staff_portal.log_error", error_log)

    payload = await service.overview(principal(JUDGE_ID, "reviewer"))

    assert payload["pipeline"] == {}
    assert payload["recent_activity"][0]["actor"] is None
    assert payload["warnings"][0]["section"] == "pipeline"
    assert payload["warnings"][0]["correlation_id"].startswith("staff-overvi-")
    error_log.assert_awaited_once()
    audit = await service.audit(principal(ADMIN_ID, "admin"), {})
    assert audit["items"][0]["actor"] is None


@pytest.mark.asyncio
async def test_api_preserves_real_snowflakes_as_strings(portal):
    service, _guild = portal
    created = await service.create_session(
        {"user_id": str(DEV_ID), "purpose": "staff"}
    )
    headers = {
        "x-avenue-portal-key": "test-service-token",
        "x-staff-session": created["session_token"],
    }
    status, payload = await service.handle_request(
        "GET", "/api/staff/session", headers, b""
    )
    assert status == 200
    assert payload["user"]["id"] == "1102884420207255552"
    assert isinstance(payload["user"]["id"], str)
    with pytest.raises(PortalError) as unsafe:
        await service.create_session({"user_id": DEV_ID, "purpose": "staff"})
    assert unsafe.value.code == "unsafe_discord_id"


def test_nested_portal_payload_preserves_every_staff_snowflake_as_text():
    exact = "1102884420207255653"
    payload = _json_safe(
        {
            "profile": {"user_id": int(exact), "role_ids": [901431567719731230]},
            "claim": {"claimed_by": int(exact)},
            "outreach": {"actor_id": int(exact), "released_by": int(exact)},
            "qa": {
                "reviewer_id": int(exact),
                "qa_by": int(exact),
                "request_message_id": int(exact),
            },
            "audit": {"actor_id": int(exact)},
            "task": {"assignee_id": int(exact), "created_by": int(exact)},
            "application": {"applicant_id": int(exact), "decided_by": int(exact)},
            "discord": {
                "guild_id": int(exact),
                "channel_id": int(exact),
                "forum_channel_id": int(exact),
                "message_id": int(exact),
            },
        }
    )
    assert payload["profile"]["user_id"] == exact
    assert payload["profile"]["role_ids"] == ["901431567719731230"]
    for section in (
        "claim",
        "outreach",
        "qa",
        "audit",
        "task",
        "application",
        "discord",
    ):
        for key, value in payload[section].items():
            if key.endswith("_id") or key in {
                "claimed_by",
                "created_by",
                "decided_by",
                "qa_by",
                "released_by",
            }:
                assert value == exact


@pytest.mark.asyncio
async def test_portal_nickname_permissions_fallback_and_audit(portal):
    service, _guild = portal
    reviewer = principal(JUDGE_ID, "reviewer")
    admin = principal(ADMIN_ID, "admin")
    owner = principal(OWNER_ID, "owner")
    changed = await service.update_portal_nickname(
        reviewer, JUDGE_ID, {"portal_nickname": "  Average  "}
    )
    assert changed["profile"]["display_name"] == "Average"
    with pytest.raises(PermissionError):
        await service.update_portal_nickname(
            reviewer,
            HEAD_ID,
            {"portal_nickname": "Nope", "reason": "Escalation"},
        )
    reset = await service.update_portal_nickname(
        admin,
        JUDGE_ID,
        {"portal_nickname": "", "reason": "Requested reset"},
    )
    assert reset["profile"]["display_name"] == f"Member {JUDGE_ID}"
    history = await service.nickname_history(owner, JUDGE_ID)
    assert [item["new_nickname"] for item in history["items"]] == ["", "Average"]
    with pytest.raises(PortalError) as unsafe:
        await service.update_portal_nickname(
            reviewer, JUDGE_ID, {"portal_nickname": "@everyone"}
        )
    assert unsafe.value.code == "nickname_unsafe"


@pytest.mark.asyncio
async def test_admin_cannot_grant_owner_or_dev(portal):
    service, _guild = portal
    with pytest.raises(PermissionError):
        await service.staff_action(
            principal(ADMIN_ID, "admin"),
            str(JUDGE_ID),
            {
                "action": "set_owner",
                "reason": "Attempted browser escalation",
                "confirmed": True,
            },
        )
    with pytest.raises(PortalError) as protected:
        await service.staff_action(
            principal(DEV_ID, "dev"),
            str(DEV_ID),
            {
                "action": "deactivate",
                "reason": "Cannot mutate Dev membership",
                "confirmed": True,
            },
        )
    assert protected.value.code == "protected_staff_account"


@pytest.mark.asyncio
async def test_admin_can_edit_pending_request_opening(portal):
    service, _guild = portal
    opening_id = await service.db.execute_insert(
        "INSERT INTO level_request_scheduled_openings("
        "guild_id,request_limit,close_minutes,open_ts,created_by,created_ts,"
        "status,request_type,open_message,correlation_id) "
        "VALUES(?,?,?,?,?,?,'pending',?,?,?)",
        (GUILD_ID, 10, 30, 2_000_000_000, ADMIN_ID, 1, "any", "Old", "edit-test"),
    )
    request_cog = SimpleNamespace(
        _normalize_request_type=lambda value: value if value == "only_demons" else None,
        _clean_open_message=lambda value: str(value or "").strip() or None,
    )
    service.bot.get_cog = (
        lambda name: request_cog if name == "RequestLevelsCog" else None
    )

    result = await service.requests_action(
        principal(ADMIN_ID, "admin"),
        {
            "action": "edit_scheduled",
            "opening_id": opening_id,
            "open_ts": 2_000_000_100,
            "request_limit": 25,
            "close_minutes": 45,
            "request_type": "only_demons",
            "open_message": "Updated opening",
            "reason": "Schedule changed",
            "confirmed": True,
        },
    )

    saved = await service.db.fetchone(
        "SELECT request_limit,close_minutes,open_ts,request_type,open_message "
        "FROM level_request_scheduled_openings WHERE id=?",
        (opening_id,),
    )
    assert result["result"]["opening_id"] == opening_id
    assert dict(saved) == {
        "request_limit": 25,
        "close_minutes": 45,
        "open_ts": 2_000_000_100,
        "request_type": "only_demons",
        "open_message": "Updated opening",
    }


@pytest.mark.asyncio
async def test_private_api_requires_service_auth_csrf_and_idempotency(portal):
    service, _guild = portal
    created = await service.create_session({"user_id": JUDGE_ID})
    base_headers = {
        "x-avenue-portal-key": "test-service-token",
        "x-staff-session": created["session_token"],
    }
    with pytest.raises(PortalError, match="could not authenticate"):
        await service.handle_request(
            "GET",
            "/api/staff/overview",
            {**base_headers, "x-avenue-portal-key": "wrong"},
            b"",
        )
    body = json.dumps({"scope": "private", "body": "One durable note"}).encode()
    with pytest.raises(PortalError, match="could not be verified"):
        await service.handle_request("POST", "/api/staff/notes", base_headers, body)
    headers = {
        **base_headers,
        "x-csrf-token": created["csrf_token"],
        "idempotency-key": "note-action-0001",
    }
    first_status, first = await service.handle_request(
        "POST", "/api/staff/notes", headers, body
    )
    retry_status, retry = await service.handle_request(
        "POST", "/api/staff/notes", headers, body
    )
    assert first_status == 201
    assert retry_status == 200
    assert first == retry
    count = await service.db.fetchone("SELECT COUNT(*) AS c FROM staff_notes")
    assert int(count["c"]) == 1


@pytest.mark.asyncio
async def test_judge_cannot_open_owner_operations(portal):
    service, _guild = portal
    created = await service.create_session({"user_id": JUDGE_ID})
    with pytest.raises(PermissionError):
        await service.handle_request(
            "GET",
            "/api/staff/operations",
            {
                "x-avenue-portal-key": "test-service-token",
                "x-staff-session": created["session_token"],
            },
            b"",
        )


@pytest.mark.asyncio
async def test_queue_rank_filters_and_unknown_cp_are_preserved(portal):
    service, _guild = portal
    lower = await insert_queue(service, level_id="111111111", message_id=1, priority=2, cp=0)
    higher = await insert_queue(service, level_id="222222222", message_id=2, priority=9, cp=None)
    data = await service.queue(principal(JUDGE_ID, "judge"), {"filter": "cp_unknown"})
    assert data["total"] == 1
    assert data["items"][0]["id"] == higher
    assert data["items"][0]["rank"] == 2  # Incomplete values follow complete PPS rows.
    assert data["items"][0]["cp"] is None
    assert lower != higher


@pytest.mark.asyncio
async def test_claim_has_one_owner_and_head_can_reassign(portal):
    service, _guild = portal
    queue_id = await insert_queue(service, level_id="333333333", message_id=3, priority=4)
    judge = principal(JUDGE_ID, "judge")
    head = principal(HEAD_ID, "head_judge")
    await service._claim(judge, queue_id, JUDGE_ID, "")
    with pytest.raises(PortalError, match="already claimed"):
        await service._claim(head, queue_id, HEAD_ID, "takeover")
    result = await service._claim(head, queue_id, HEAD_ID, "stale handoff", reassign=True)
    assert int(result["claim"]["claimed_by"]) == HEAD_ID
    with pytest.raises(PermissionError):
        await service._release_claim(judge, queue_id, "")
    await service._release_claim(head, queue_id, "")
    assert await service.db.fetchone(
        "SELECT 1 FROM staff_queue_claims WHERE queue_id=? AND claim_state='active'", (queue_id,)
    ) is None


@pytest.mark.asyncio
async def test_outreach_submission_and_followup_keep_distinct_semantics(portal):
    service, _guild = portal
    queue_id = await insert_queue(service, level_id="444444444", message_id=4, priority=4)
    judge = principal(JUDGE_ID, "judge")
    await service.priority.start_cycle(GUILD_ID, OWNER_ID)
    await service.record_outreach(
        judge,
        {"queue_id": queue_id, "event": "submitted_to_mod", "route": "direct", "target": "@Mod One", "confirmed": True},
        "portal-submit-0001",
    )
    await service.record_outreach(
        judge,
        {"queue_id": queue_id, "event": "follow_up", "route": "direct", "target": "mod-one"},
        "portal-followup-001",
    )
    await service.record_outreach(
        judge,
        {
            "queue_id": queue_id,
            "event": "submitted_to_mod",
            "route": "network",
            "target": "Moderator Two",
            "confirmed": True,
        },
        "portal-submit-0002",
    )
    with pytest.raises(PortalError, match="follow-up instead"):
        await service.record_outreach(
            judge,
            {"queue_id": queue_id, "event": "submitted_to_mod", "route": "direct", "target": "MOD ONE", "confirmed": True},
            "portal-submit-0003",
        )
    rows = await service.db.fetchall(
        "SELECT status FROM level_outreach_attempts WHERE queue_id=? ORDER BY id", (queue_id,)
    )
    assert [row["status"] for row in rows] == [
        "submitted_to_mod",
        "follow_up",
        "submitted_to_mod",
    ]


@pytest.mark.asyncio
async def test_requeue_preserves_episode_history_and_resets_waiting(portal):
    service, _guild = portal
    queue_id = await insert_queue(
        service,
        level_id="434343434",
        message_id=43,
        priority=8,
        state="awaiting_outcome",
    )
    await service.db.execute(
        "UPDATE level_outreach_queue SET waiting_cycles=3,waiting_component_h=7.79 WHERE id=?",
        (queue_id,),
    )
    await service.db.execute(
        "INSERT INTO staff_outreach_episodes(guild_id,queue_id,episode_number,status,started_by,started_ts) "
        "VALUES(?,?,1,'awaiting_outcome',?,100)",
        (GUILD_ID, queue_id, JUDGE_ID),
    )
    service.priority.refresh_queue_entry = AsyncMock()

    result = await service._requeue(
        principal(HEAD_ID, "head_judge"),
        queue_id,
        {"reason": "The previous observation window ended", "confirmed": True},
    )

    queue = await service.db.fetchone(
        "SELECT queue_state,waiting_cycles,submitted_to_mod_ts FROM level_outreach_queue WHERE id=?",
        (queue_id,),
    )
    episodes = await service.db.fetchall(
        "SELECT episode_number,status,reason FROM staff_outreach_episodes "
        "WHERE queue_id=? ORDER BY episode_number",
        (queue_id,),
    )
    event = await service.db.fetchone(
        "SELECT payload_json FROM workflow_events WHERE entity_id=? AND event='outreach_episode_requeued'",
        (f"queue:{queue_id}",),
    )
    assert result["episode_number"] == 2
    assert dict(queue) == {
        "queue_state": "queued",
        "waiting_cycles": 0,
        "submitted_to_mod_ts": None,
    }
    assert [(row["episode_number"], row["status"]) for row in episodes] == [
        (1, "completed"),
        (2, "active"),
    ]
    assert "previous observation window" in event["payload_json"]
    service.priority.refresh_queue_entry.assert_awaited_once_with(queue_id, force_cp=True)


@pytest.mark.asyncio
async def test_ineligible_outreach_does_not_create_orphan_episode(portal):
    service, _guild = portal
    await service.priority.start_cycle(GUILD_ID, OWNER_ID)
    queue_id = await insert_queue(
        service, level_id="454545454", message_id=45, priority=4
    )
    with pytest.raises(PortalError, match="not part of the active outreach cycle"):
        await service.record_outreach(
            principal(JUDGE_ID, "judge"),
            {
                "queue_id": queue_id,
                "event": "attempted",
                "route": "direct",
            },
            "portal-ineligible-001",
        )
    count = await service.db.fetchone(
        "SELECT COUNT(*) AS c FROM staff_outreach_episodes WHERE queue_id=?",
        (queue_id,),
    )
    assert int(count["c"]) == 0


@pytest.mark.asyncio
async def test_tier_adjustment_locks_after_submission_except_for_owner(portal):
    service, _guild = portal
    queue_id = await insert_queue(
        service, level_id="464646464", message_id=46, priority=4
    )
    await service.priority.start_cycle(GUILD_ID, OWNER_ID)
    await service.record_outreach(
        principal(JUDGE_ID, "judge"),
        {
            "queue_id": queue_id,
            "event": "submitted_to_mod",
            "route": "direct",
            "target": "Moderator Two",
            "confirmed": True,
        },
        "portal-tier-lock-001",
    )
    payload = {"tier": "mythic", "reason": "Correction", "confirmed": True}
    with pytest.raises(PortalError, match="Only the owner"):
        await service._adjust_tier(
            principal(HEAD_ID, "head_judge"), queue_id, payload
        )
    result = await service._adjust_tier(principal(OWNER_ID, "owner"), queue_id, payload)
    assert result["tier"] == "mythic"


@pytest.mark.asyncio
async def test_qa_tier_adjustment_preserves_original_tier(portal):
    service, _guild = portal
    message_id = 47
    queue_id = await insert_queue(
        service, level_id="474747474", message_id=message_id, priority=4
    )
    await service.db.execute(
        "INSERT INTO level_request_submissions(guild_id,wave_id,user_id,level_id,request_message_id,status,"
        "result,reviewed_by,reviewed_ts,created_ts,send_type) VALUES(?,?,?,?,?,'reviewed','sent',?,?,?,?)",
        (GUILD_ID, 1, 547, "474747474", message_id, JUDGE_ID, 200, 100, "epic"),
    )
    await service.qa_action(
        principal(HEAD_ID, "head_judge"),
        message_id,
        {"action": "adjust", "tier": "legendary", "reason": "Calibration", "confirmed": True},
    )
    qa = await service.db.fetchone(
        "SELECT * FROM staff_review_qa WHERE guild_id=? AND request_message_id=?",
        (GUILD_ID, message_id),
    )
    queue = await service.db.fetchone(
        "SELECT send_type FROM level_outreach_queue WHERE id=?", (queue_id,)
    )
    assert qa["qa_status"] == "adjusted"
    assert qa["original_send_type"] == "epic"
    assert qa["adjusted_send_type"] == "legendary"
    assert int(qa["reviewer_id"]) == JUDGE_ID
    assert queue["send_type"] == "legendary"


@pytest.mark.asyncio
async def test_application_submit_retry_returns_same_active_application(portal):
    service, _guild = portal
    applicant = principal(999, "applicant")
    payload = {
        "application_type": "judge",
        "answers": {
            "age": "16 to 18",
            "motivation": "Motivation",
            "experience": "Experience",
            "improvements": "More review calibration",
            "weekly_capacity": "3-6",
            "timezone": "UTC+1",
            "level_review": "A structured level review",
        },
    }
    first = await service.save_application(applicant, payload, submit=True)
    second = await service.save_application(applicant, payload, submit=True)
    assert first["application"]["id"] == second["application"]["id"]
    count = await service.db.fetchone("SELECT COUNT(*) AS c FROM staff_applications")
    assert int(count["c"]) == 1


@pytest.mark.asyncio
async def test_tasks_and_notes_respect_assignee_and_scope(portal):
    service, _guild = portal
    judge = principal(JUDGE_ID, "judge")
    other_judge = principal(201, "judge")
    head = principal(HEAD_ID, "head_judge")
    assigned = await service.create_task(
        head,
        {
            "task_type": "assigned",
            "assignee_id": JUDGE_ID,
            "title": "Review outreach evidence",
        },
    )
    await service.create_task(
        head,
        {"task_type": "team", "title": "Prepare the next cycle"},
    )
    judge_tasks = await service.tasks(judge, {})
    other_tasks = await service.tasks(other_judge, {})
    assert int(assigned["task"]["id"]) in {int(item["id"]) for item in judge_tasks["items"]}
    assert int(assigned["task"]["id"]) not in {
        int(item["id"]) for item in other_tasks["items"]
    }
    assert any(item["task_type"] == "team" for item in other_tasks["items"])

    await service.create_note(judge, {"scope": "private", "body": "Judge private"})
    await service.create_note(head, {"scope": "head_judges", "body": "Head private"})
    judge_notes = await service.notes(judge, {})
    head_notes = await service.notes(head, {})
    assert {item["body"] for item in judge_notes["items"]} == {"Judge private"}
    assert {item["body"] for item in head_notes["items"]} == {"Head private"}
    with pytest.raises(PortalError, match="cannot use that note scope"):
        await service.create_note(
            judge, {"scope": "head_judges", "body": "Not permitted"}
        )


@pytest.mark.asyncio
async def test_application_scope_and_applicant_payload_hide_internal_notes(portal):
    service, _guild = portal
    now = 100
    judge_application = await service.db.execute_insert(
        "INSERT INTO staff_applications(guild_id,applicant_id,application_type,status,answers_json,created_ts,updated_ts,submitted_ts) "
        "VALUES(?,?,'judge','submitted','{}',?,?,?)",
        (GUILD_ID, 999, now, now, now),
    )
    private_application = await service.db.execute_insert(
        "INSERT INTO staff_applications(guild_id,applicant_id,application_type,status,answers_json,created_ts,updated_ts,submitted_ts) "
        "VALUES(?,?,'management','submitted','{}',?,?,?)",
        (GUILD_ID, 998, now, now, now),
    )
    await service.db.execute(
        "INSERT INTO staff_application_notes(application_id,author_id,body,created_ts,updated_ts) "
        "VALUES(?,?,?,?,?)",
        (judge_application, HEAD_ID, "Internal only", now, now),
    )

    head_view = await service.applications(principal(HEAD_ID, "head_judge"), {})
    owner_view = await service.applications(principal(OWNER_ID, "owner"), {})
    assert {int(item["id"]) for item in head_view["items"]} == {judge_application}
    assert {int(item["id"]) for item in owner_view["items"]} == {
        judge_application,
        private_application,
    }
    status, applicant_view = await service._handle_apply(
        "GET", "/api/apply/mine", principal(999, "applicant"), {}
    )
    assert status == 200
    assert applicant_view["items"][0]["id"] == judge_application
    assert "internal_notes" not in applicant_view["items"][0]


@pytest.mark.asyncio
async def test_inactive_staff_remains_visible_for_restore(portal):
    service, _guild = portal
    await service.db.execute(
        "INSERT INTO staff_members(guild_id,user_id,desired_role,status,updated_by,updated_ts,reason) "
        "VALUES(?,?,'inactive','inactive',?,?,?)",
        (GUILD_ID, 999, OWNER_ID, 100, "Access removed"),
    )
    data = await service.staff_list(principal(OWNER_ID, "owner"))
    former = next(item for item in data["items"] if item["id"] == "999")
    assert former["active"] is False
    assert former["desired_status"] == "inactive"


@pytest.mark.asyncio
async def test_team_and_statistics_use_real_empty_aggregates(portal):
    service, _guild = portal
    team = await service.team(principal(JUDGE_ID, "judge"))
    personal = await service.statistics(principal(JUDGE_ID, "judge"))
    whole_team = await service.statistics(principal(HEAD_ID, "head_judge"))

    assert team["review_progress"] == {"done": 0, "total": 0}
    assert team["outreach_week"] == {"attempts": 0, "submissions": 0}
    assert personal["scope"] == "self"
    assert whole_team["scope"] == "team"
    assert personal["outreach"]["attempts"] == 0
    assert whole_team["tasks_completed"] == 0


def test_public_level_search_exposes_only_allowlisted_cache_fields():
    set_public_level_data(
        [
            {
                "level_id": "555555555",
                "level_name": "Synergy",
                "uploader_name": "CreatorName",
                "recommendation_type": "mythic",
                "public_queue_state": "queued",
                "public_priority_band": "top_priority",
                "public_outreach_state": "queued_for_outreach",
                "public_outcome_state": "unknown",
                "requester_id": 123,
                "private_target_label": "Secret Mod",
                "priority_points": 99.5,
            }
        ]
    )
    payload = get_public_levels_payload("creator")
    assert payload["count"] == 1
    item = payload["levels"][0]
    assert item["level_id"] == "555555555"
    assert "requester_id" not in item
    assert "private_target_label" not in item
    assert "priority_points" not in item


@pytest.mark.asyncio
async def test_dev_view_mode_uses_effective_capabilities_and_is_read_only(portal):
    service, _guild = portal
    created = await service.create_session({"user_id": str(DEV_ID), "purpose": "staff"})
    headers = {
        "x-avenue-portal-key": "test-service-token",
        "x-staff-session": created["session_token"],
        "x-staff-view-role": "reviewer",
    }
    status, payload = await service.handle_request("GET", "/api/staff/session", headers, b"")
    assert status == 200
    assert payload["user"]["role"] == "reviewer"
    assert payload["view_mode"]["actual_role"] == "dev"
    assert payload["api"]["version"] >= 2
    assert "staff_manual_management" in payload["api"]["features"]
    assert "developer.access" not in payload["user"]["capabilities"]

    headers["x-csrf-token"] = created["csrf_token"]
    with pytest.raises(PortalError) as read_only:
        await service.handle_request("POST", "/api/staff/tasks", headers, b"{}")
    assert read_only.value.code == "view_mode_read_only"


@pytest.mark.asyncio
async def test_dev_can_reset_own_stale_application_data_atomically(portal):
    service, _guild = portal
    application_id = await service.db.execute_insert(
        "INSERT INTO staff_applications(guild_id,applicant_id,application_type,status,"
        "answers_json,created_ts,updated_ts,submitted_ts,review_thread_outbox_id) "
        "VALUES(?,?,'judge','submitted','{}',100,100,100,?)",
        (GUILD_ID, DEV_ID, 9001),
    )
    await service.db.execute(
        "INSERT INTO staff_application_events(application_id,actor_id,event,from_status,"
        "to_status,detail_json,created_ts,correlation_id) VALUES(?,?,'submitted','draft',"
        "'submitted','{}',100,'application-reset-test')",
        (application_id, DEV_ID),
    )
    await service.db.execute(
        "INSERT INTO staff_application_notes(application_id,author_id,body,created_ts,updated_ts) "
        "VALUES(?,?,?,100,100)",
        (application_id, DEV_ID, "Old application note"),
    )
    await service.db.execute(
        "INSERT INTO discord_outbox(id,correlation_id,idempotency_key,action_type,guild_id,"
        "payload_json,status,attempts,next_attempt_ts,created_ts,updated_ts) "
        "VALUES(9001,'application-reset-test','application-reset-test','create_application_thread',"
        "?,'{}','pending',0,100,100,100)",
        (GUILD_ID,),
    )
    await service.db.execute(
        "INSERT INTO staff_idempotency(idempotency_key,user_id,operation,response_json,created_ts,expires_ts) "
        "VALUES('old-application-save',?,'POST /api/apply/save','{}',100,9999999999)",
        (DEV_ID,),
    )

    status, result = await service._handle_apply(
        "DELETE",
        "/api/apply/mine",
        principal(DEV_ID, "dev"),
        {"confirmation": "DELETE"},
    )

    assert status == 200
    assert result["applications_removed"] == 1
    assert await service.db.fetchone(
        "SELECT 1 FROM staff_applications WHERE id=?", (application_id,)
    ) is None
    assert await service.db.fetchone(
        "SELECT 1 FROM staff_application_events WHERE application_id=?", (application_id,)
    ) is None
    assert await service.db.fetchone(
        "SELECT 1 FROM staff_application_notes WHERE application_id=?", (application_id,)
    ) is None
    outbox = await service.db.fetchone("SELECT status FROM discord_outbox WHERE id=9001")
    assert outbox["status"] == "dead"
    assert await service.db.fetchone(
        "SELECT 1 FROM staff_idempotency WHERE idempotency_key='old-application-save'"
    ) is None


@pytest.mark.asyncio
async def test_applicant_cannot_erase_an_active_submitted_application(portal):
    service, _guild = portal
    await service.db.execute(
        "INSERT INTO staff_applications(guild_id,applicant_id,application_type,status,"
        "answers_json,created_ts,updated_ts,submitted_ts) "
        "VALUES(?,999,'judge','submitted','{}',100,100,100)",
        (GUILD_ID,),
    )
    with pytest.raises(PortalError) as restricted:
        await service.reset_own_application_data(
            principal(999, "applicant"), {"confirmation": "DELETE"}
        )
    assert restricted.value.code == "application_reset_restricted"


@pytest.mark.asyncio
async def test_hidden_queue_is_reversible_and_excluded_from_normal_views(portal):
    service, _guild = portal
    queue_id = await insert_queue(service, level_id="565656565", message_id=56, priority=5)
    dev = principal(DEV_ID, "dev")
    hidden = await service._hide_queue(dev, queue_id, {"reason": "Duplicate test entry", "confirmed": True})
    assert hidden["state"] == "hidden"
    assert (await service.queue(principal(JUDGE_ID, "reviewer"), {}))["total"] == 0
    with pytest.raises(PortalError) as blocked:
        await service.queue_action(
            principal(JUDGE_ID, "reviewer"), queue_id, "claim", {}
        )
    assert blocked.value.code == "queue_hidden"
    assert "hidden" not in (await service.priority.dashboard(GUILD_ID))["states"]
    hidden_view = await service.queue(dev, {"filter": "hidden"})
    assert hidden_view["items"][0]["id"] == queue_id
    restored = await service._restore_hidden_queue(dev, queue_id, {"reason": "Restore test entry", "confirmed": True})
    assert restored["state"] == "queued"
    assert (await service.queue(principal(JUDGE_ID, "reviewer"), {}))["total"] == 1


@pytest.mark.asyncio
async def test_dev_can_add_and_remove_staff_profiles_by_discord_id(portal):
    service, _guild = portal
    dev = principal(DEV_ID, "dev")
    added = await service.add_staff(
        dev,
        {"user_id": "999", "role": "reviewer", "reason": "New reviewer", "confirmed": True},
    )
    assert added["to_role"] == "reviewer"
    assert service.bot.outbox.calls[-1][0] == "add_role"
    removed = await service.staff_action(
        dev,
        "999",
        {"action": "remove", "reason": "Left the team", "confirmed": True},
    )
    assert removed["to_role"] == "inactive"
    saved = await service.db.fetchone("SELECT status FROM staff_members WHERE guild_id=? AND user_id=?", (GUILD_ID, 999))
    assert saved["status"] == "removed"


@pytest.mark.asyncio
async def test_assigned_task_enqueues_private_notification(portal):
    service, _guild = portal
    await service.create_task(
        principal(HEAD_ID, "head_reviewer"),
        {"task_type": "assigned", "assignee_id": JUDGE_ID, "title": "Review calibration"},
    )
    kind, call = service.bot.outbox.calls[-1]
    assert kind == "send_dm"
    assert call["user_id"] == JUDGE_ID
    assert call["idempotency_key"].endswith(f":assigned:{JUDGE_ID}")


@pytest.mark.asyncio
async def test_application_form_persists_prompt_and_submission_enqueues_thread(portal):
    service, _guild = portal
    applicant = principal(999, "applicant")
    first = await service.application_form(applicant)
    second = await service.application_form(applicant)
    assert first["application"]["id"] == second["application"]["id"]
    review_question = next(item for item in first["questions"] if item["key"] == "level_review")
    selected_prompt = review_question["review_prompt"]
    assert selected_prompt["level_id"] in {
        "101935961",
        "139439179",
        "94859569",
        "100117857",
        "107166460",
    }
    assert selected_prompt["youtube_embed_url"].startswith(
        "https://www.youtube-nocookie.com/embed/"
    )
    assert next(
        item for item in second["questions"] if item["key"] == "level_review"
    )["review_prompt"] == selected_prompt
    answers = {
        "age": "16 to 18",
        "motivation": "I want to help creators",
        "experience": "I review levels regularly",
        "improvements": "More calibration sessions",
        "weekly_capacity": "3-6",
        "timezone": "UTC+1",
        "level_review": "A detailed review",
    }
    result = await service.save_application(
        applicant, {"application_type": "judge", "answers": answers}, submit=True
    )
    assert result["application"]["review_prompt_key"] == selected_prompt["key"]
    assert service.bot.outbox.calls[-1][0] == "create_application_thread"


@pytest.mark.asyncio
async def test_application_delivery_reconciliation_revives_dead_outbox(portal):
    service, _guild = portal
    now = 100
    application_id = await service.db.execute_insert(
        "INSERT INTO staff_applications("
        "guild_id,applicant_id,application_type,status,answers_json,created_ts,"
        "updated_ts,submitted_ts,review_prompt_key) VALUES(?,?,'judge','submitted',"
        "?,?,?,?,?)",
        (
            GUILD_ID,
            999,
            json.dumps({"motivation": "I want to help"}),
            now,
            now,
            now,
            "synergy-101935961",
        ),
    )
    outbox_id = await service.db.execute_insert(
        "INSERT INTO discord_outbox("
        "correlation_id,idempotency_key,action_type,guild_id,channel_id,user_id,"
        "payload_json,status,attempts,next_attempt_ts,created_ts,updated_ts,last_error) "
        "VALUES(?,?,?,?,?,?,?,'dead',8,?,?,?,'old channel type assumption')",
        (
            f"staff-application:{application_id}",
            f"staff-application:{application_id}:review-thread",
            "create_application_thread",
            GUILD_ID,
            1461483580197703832,
            999,
            "{}",
            now,
            now,
            now,
        ),
    )
    await service.db.execute(
        "UPDATE staff_applications SET review_thread_outbox_id=? WHERE id=?",
        (outbox_id, application_id),
    )

    assert await service.reconcile_application_deliveries() == 1
    revived = await service.db.fetchone(
        "SELECT status,attempts,last_error,payload_json FROM discord_outbox WHERE id=?",
        (outbox_id,),
    )
    payload = json.loads(revived["payload_json"])
    assert revived["status"] == "pending"
    assert int(revived["attempts"]) == 0
    assert revived["last_error"] is None
    assert payload["application_id"] == application_id
    assert payload["review_url"].endswith("/staff/#team/applications")
    assert any(
        item["answer"] == "I want to help" for item in payload["responses"]
    )
    assert await service.reconcile_application_deliveries() == 0


def test_application_review_pool_and_youtube_embed_urls_are_valid(portal):
    service, _guild = portal
    levels = service._application_review_levels()
    assert [(level["name"], level["level_id"], level["weight"]) for level in levels] == [
        ("Synergy", "101935961", 1),
        ("Madeline", "139439179", 1),
        ("V I B E", "94859569", 1),
        ("Speed", "100117857", 1),
        ("Distimia", "107166460", 1),
    ]
    assert _youtube_embed_url("https://youtu.be/v5tr0Tg9-9c?si=test") == (
        "https://www.youtube-nocookie.com/embed/v5tr0Tg9-9c"
    )
    assert _youtube_embed_url("https://www.youtube.com/watch?v=bfQj4ZU2nQM") == (
        "https://www.youtube-nocookie.com/embed/bfQj4ZU2nQM"
    )
    assert _youtube_embed_url("https://example.com/watch?v=bfQj4ZU2nQM") == ""
    assert _youtube_embed_url("https://youtu.be/too-short") == ""
