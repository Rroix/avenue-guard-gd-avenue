from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
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
        self.timed_out_until = None


class FakeGuild:
    def __init__(self, members):
        self.members = list(members)

    def get_member(self, user_id):
        return next((member for member in self.members if member.id == user_id), None)

    async def fetch_member(self, user_id):
        return self.get_member(user_id)

    def get_role(self, role_id):
        return SimpleNamespace(id=role_id, name=f"Role {role_id}")


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


def completed_form_answers(form):
    return {
        question["key"]: (
            question["options"][0]
            if question["type"] == "single_choice"
            else "A specific answer with enough context for staff review."
        )
        for question in form["questions"]
        if question["required"]
    }


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
        FakeMember(1000, []),
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

    outside_application = await service.create_session(
        {
            "user_id": 998,
            "purpose": "apply",
            "username": "outside-user",
            "global_name": "Outside User",
            "avatar_url": "https://cdn.discordapp.com/avatars/998/avatar.webp",
        }
    )
    assert outside_application["user"]["display_name"] == "Outside User"
    assert outside_application["user"]["role"] == "applicant"


@pytest.mark.asyncio
async def test_outside_guild_session_can_appeal_but_cannot_submit_staff_application(
    portal,
):
    service, guild = portal
    guild.fetch_ban = AsyncMock(return_value=SimpleNamespace(reason="Original ban"))

    async def audit_logs(**_kwargs):
        if False:
            yield None

    guild.audit_logs = audit_logs
    session = await service.create_session(
        {
            "user_id": 998,
            "purpose": "apply",
            "username": "outside-user",
            "global_name": "Outside User",
        }
    )
    headers = {
        "x-avenue-portal-key": "test-service-token",
        "x-staff-session": session["session_token"],
    }
    status, options = await service.handle_request(
        "GET", "/api/apply/options", headers, b""
    )
    appeal = next(
        item for item in options["items"] if item["application_type"] == "appeal"
    )
    reviewer = next(
        item for item in options["items"] if item["application_type"] == "judge"
    )
    assert status == 200
    assert options["guild_member"] is False
    assert appeal["enabled"] is True
    assert reviewer["enabled"] is False

    with pytest.raises(PortalError) as recruitment:
        await service.application_form(principal(998, "applicant"), "judge")
    assert recruitment.value.code == "membership_required"

    appeal_form = await service.appeals.form(principal(998, "applicant"))
    assert appeal_form["eligibility"]["eligible"] is True


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
        "UPDATE level_outreach_queue SET waiting_cycles=3,waiting_component_h=7.79,"
        "outcome_window_completed_ts=200,rated_within_window=0 WHERE id=?",
        (queue_id,),
    )
    await service.db.execute(
        "INSERT INTO staff_outreach_episodes(guild_id,queue_id,episode_number,status,started_by,started_ts) "
        "VALUES(?,?,1,'awaiting_outcome',?,100)",
        (GUILD_ID, queue_id, JUDGE_ID),
    )
    await service.db.execute(
        "UPDATE staff_outreach_episodes SET outcome='not_rated_within_window',rated_within_window=0,outcome_completed_ts=200 WHERE queue_id=?",
        (queue_id,),
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
        "SELECT episode_number,status,reason,outcome FROM staff_outreach_episodes "
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
    assert episodes[0]["outcome"] == "not_rated_within_window"
    assert "previous observation window" in event["payload_json"]
    service.priority.refresh_queue_entry.assert_awaited_once_with(queue_id, force_cp=True)


@pytest.mark.asyncio
async def test_requeue_rejects_an_open_outcome_window(portal):
    service, _guild = portal
    queue_id = await insert_queue(
        service,
        level_id="454545454",
        message_id=45,
        priority=8,
        state="awaiting_outcome",
    )
    with pytest.raises(PortalError, match="not ready"):
        await service._requeue(
            principal(HEAD_ID, "head_judge"),
            queue_id,
            {"reason": "Too early", "confirmed": True},
        )
    assert not await service.db.fetchone(
        "SELECT 1 FROM staff_outreach_episodes WHERE queue_id=?",
        (queue_id,),
    )


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
    form = await service.application_form(applicant, "judge")
    payload = {
        "application_type": "judge",
        "answers": completed_form_answers(form),
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
async def test_application_filters_cover_type_status_and_claim_state(portal):
    service, _guild = portal
    claimed = await service.db.execute_insert(
        "INSERT INTO staff_applications(guild_id,applicant_id,application_type,status,"
        "answers_json,claimed_by,created_ts,updated_ts,submitted_ts) "
        "VALUES(?,?,'mod','interview','{}',?,100,100,100)",
        (GUILD_ID, 997, ADMIN_ID),
    )
    unclaimed = await service.db.execute_insert(
        "INSERT INTO staff_applications(guild_id,applicant_id,application_type,status,"
        "answers_json,created_ts,updated_ts,submitted_ts) "
        "VALUES(?,?,'mod','interview','{}',100,100,100)",
        (GUILD_ID, 998),
    )

    result = await service.applications(
        principal(ADMIN_ID, "admin"),
        {"type": "mod", "status": "interview", "claim": "unclaimed"},
    )
    assert {int(item["id"]) for item in result["items"]} == {unclaimed}
    assert claimed not in {int(item["id"]) for item in result["items"]}
    assert result["application_types"] == ["judge", "mod"]


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
    assert payload["api"]["version"] >= 5
    assert "staff_manual_management" in payload["api"]["features"]
    assert "staff_assignee_directory" in payload["api"]["features"]
    assert "task_recipient_dm" in payload["api"]["features"]
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
async def test_application_data_reset_preserves_one_day_type_cooldown(portal):
    service, _guild = portal
    now = int(time.time())
    application_id = await service.db.execute_insert(
        "INSERT INTO staff_applications(guild_id,applicant_id,application_type,status,"
        "answers_json,created_ts,updated_ts,submitted_ts) "
        "VALUES(?,999,'judge','rejected','{}',?,?,?)",
        (GUILD_ID, now, now, now),
    )

    result = await service.reset_own_application_data(
        principal(999, "applicant"), {"confirmation": "DELETE"}
    )

    assert result["cooldown_types_preserved"] == ["judge"]
    assert await service.db.fetchone(
        "SELECT 1 FROM staff_applications WHERE id=?", (application_id,)
    ) is None
    receipt = await service.db.fetchone(
        "SELECT cooldown_until_ts,source FROM staff_application_cooldowns "
        "WHERE guild_id=? AND applicant_id=999 AND application_type='judge'",
        (GUILD_ID,),
    )
    assert receipt["source"] == "application_data_reset"
    assert now + 86399 <= int(receipt["cooldown_until_ts"]) <= now + 86401

    options = await service.application_options(principal(999, "applicant"))
    assert options["cooldowns"]["judge"]["active"] is True
    assert options["cooldowns"]["judge"]["source"] == "application_data_reset"
    assert options["cooldowns"]["mod"]["active"] is False
    with pytest.raises(PortalError) as caught:
        await service.application_form(principal(999, "applicant"), "judge")
    assert caught.value.code == "application_cooldown"


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
        {
            "task_type": "assigned",
            "assignee_id": JUDGE_ID,
            "title": "Review calibration",
            "description": "Compare the two sample reviews.",
            "due_ts": 2_000_000_000,
        },
    )
    kind, call = service.bot.outbox.calls[-1]
    assert kind == "send_dm"
    assert call["user_id"] == JUDGE_ID
    assert call["idempotency_key"].endswith(f":created:{JUDGE_ID}")
    embed = call["payload"]["embed"]
    assert embed["title"] == "New assigned staff task"
    assert embed["description"] == "Compare the two sample reviews."
    fields = {field["name"]: field["value"] for field in embed["fields"]}
    assert fields["Title"] == "Review calibration"
    assert "<t:2000000000:F>" in fields["Due date"]


@pytest.mark.asyncio
async def test_task_notifications_cover_personal_assigned_and_whole_team(portal):
    service, _guild = portal
    personal = await service.create_task(
        principal(JUDGE_ID, "reviewer"),
        {
            "task_type": "personal",
            "title": "Check my draft",
            "description": "Read the saved notes.",
        },
    )
    assert personal["notification_recipient_count"] == 1
    assert service.bot.outbox.calls[-1][1]["user_id"] == JUDGE_ID

    service.bot.outbox.calls.clear()
    team = await service.create_task(
        principal(HEAD_ID, "head_reviewer"),
        {
            "task_type": "team",
            "title": "Attend calibration",
            "description": "Bring one review example.",
        },
    )
    expected_recipients = {JUDGE_ID, HEAD_ID, ADMIN_ID, OWNER_ID, DEV_ID}
    assert team["notification_recipient_count"] == len(expected_recipients)
    assert {call[1]["user_id"] for call in service.bot.outbox.calls} == expected_recipients
    assert all(call[0] == "send_dm" for call in service.bot.outbox.calls)
    assert all(
        call[1]["payload"]["embed"]["description"] == "Bring one review example."
        for call in service.bot.outbox.calls
    )
    assert all(
        any(
            field["name"] == "Due date" and field["value"] == "No due date"
            for field in call[1]["payload"]["embed"]["fields"]
        )
        for call in service.bot.outbox.calls
    )


@pytest.mark.asyncio
async def test_staff_assignee_directory_is_active_sanitized_and_enforced(portal):
    service, _guild = portal
    head = principal(HEAD_ID, "head_reviewer")
    directory = await service.staff_assignees(head)
    assert {item["id"] for item in directory["items"]} == {
        str(JUDGE_ID),
        str(HEAD_ID),
        str(ADMIN_ID),
        str(OWNER_ID),
        str(DEV_ID),
    }
    assert set(directory["items"][0]) == {
        "id",
        "display_name",
        "role",
        "role_label",
        "avatar_url",
    }
    with pytest.raises(PermissionError):
        await service.staff_assignees(principal(JUDGE_ID, "reviewer"))
    with pytest.raises(PortalError) as invalid:
        await service.create_task(
            head,
            {"task_type": "assigned", "assignee_id": 999, "title": "Invalid"},
        )
    assert invalid.value.code == "invalid_staff_assignee"
    with pytest.raises(PortalError) as missing:
        await service.create_task(
            head,
            {"task_type": "assigned", "title": "Missing"},
        )
    assert missing.value.code == "assignee_required"


@pytest.mark.asyncio
async def test_task_linked_record_requires_a_supported_complete_pair(portal):
    service, _guild = portal
    reviewer = principal(JUDGE_ID, "reviewer")
    with pytest.raises(PortalError) as incomplete:
        await service.create_task(
            reviewer,
            {
                "task_type": "personal",
                "title": "Incomplete link",
                "linked_entity_type": "level",
            },
        )
    assert incomplete.value.code == "incomplete_linked_entity"
    with pytest.raises(PortalError) as unsupported:
        await service.create_task(
            reviewer,
            {
                "task_type": "personal",
                "title": "Unsupported link",
                "linked_entity_type": "mystery",
                "linked_entity_id": "1",
            },
        )
    assert unsupported.value.code == "invalid_linked_entity_type"


@pytest.mark.asyncio
async def test_application_form_persists_prompt_and_submission_enqueues_thread(portal):
    service, _guild = portal
    applicant = principal(999, "applicant")
    first = await service.application_form(applicant)
    second = await service.application_form(applicant)
    assert first["application"]["id"] == second["application"]["id"]
    review_question = next(item for item in first["questions"] if item["key"] == "work_works_well")
    selected_prompt = review_question["review_prompt"]
    assert selected_prompt["level_id"] in {
        "101935961",
        "139439179",
        "94859569",
        "100117857",
        "107166460",
        "82599323",
        "144535118",
        "88350599",
        "110359686",
        "91281165",
    }
    assert selected_prompt["youtube_embed_url"].startswith(
        "https://www.youtube-nocookie.com/embed/"
    )
    assert next(
        item for item in second["questions"] if item["key"] == "work_works_well"
    )["review_prompt"] == selected_prompt
    answers = completed_form_answers(first)
    result = await service.save_application(
        applicant, {"application_type": "judge", "answers": answers}, submit=True
    )
    stored = await service.db.fetchone(
        "SELECT review_prompt_key FROM staff_applications WHERE id=?",
        (int(result["application"]["id"]),),
    )
    assert stored["review_prompt_key"] == selected_prompt["key"]
    assert service.bot.outbox.calls[-1][0] == "create_application_thread"


@pytest.mark.asyncio
async def test_application_catalog_and_mod_form_are_server_defined(portal):
    service, _guild = portal
    applicant = principal(999, "applicant")

    options = await service.application_options(applicant)
    by_type = {item["application_type"]: item for item in options["items"]}
    assert by_type["judge"]["enabled"] is True
    assert by_type["mod"]["label"] == "Mod application"
    assert by_type["appeal"]["enabled"] is True
    assert by_type["appeal"]["label"] == "Punishment appeal"
    assert options["cooldowns"]["judge"]["days"] == 5
    assert options["cooldowns"]["mod"]["active"] is False
    assert options["application_open_by_type"] == {"judge": True, "mod": True}
    assert by_type["judge"]["open"] is True
    assert by_type["mod"]["open"] is True
    assert by_type["appeal"]["open"] is True

    form = await service.application_form(applicant, "mod")
    questions = {item["key"]: item for item in form["questions"]}
    assert form["form"]["label"] == "Mod application"
    assert questions["motivation"]["label"] == "Why do you want to be a mod?"
    assert questions["timezone"]["type"] == "short_text"
    assert "harassment_scenario" in questions
    assert questions["triage_action"]["type"] == "single_choice"
    assert all(item.get("review_prompt") is None for item in form["questions"])


@pytest.mark.asyncio
async def test_application_type_can_close_without_closing_other_forms(portal):
    service, _guild = portal
    applicant = principal(999, "applicant")
    existing_mod = await service.application_form(applicant, "mod")

    updated = await service.update_safe_configuration(
        principal(OWNER_ID, "owner"),
        {"application_open_by_type": {"mod": False, "judge": True}},
    )
    assert updated["configuration"]["application_open_by_type"] == {
        "judge": True,
        "mod": False,
    }

    options = await service.application_options(applicant)
    by_type = {item["application_type"]: item for item in options["items"]}
    assert by_type["judge"]["open"] is True
    assert by_type["mod"]["open"] is False

    resumed = await service.application_form(applicant, "mod")
    assert resumed["application"]["id"] == existing_mod["application"]["id"]
    await service.application_form(principal(1000, "applicant"), "judge")
    with pytest.raises(PortalError) as closed_form:
        await service.application_form(principal(1000, "applicant"), "mod")
    assert closed_form.value.code == "applications_closed"

    with pytest.raises(PortalError) as closed_submit:
        await service.save_application(
            applicant,
            {"application_type": "mod", "answers": {}},
            submit=True,
        )
    assert closed_submit.value.code == "applications_closed"


@pytest.mark.asyncio
async def test_application_cooldown_is_exclusive_to_application_type(portal):
    service, _guild = portal
    applicant = principal(999, "applicant")
    mod_form = await service.application_form(applicant, "mod")
    answers = completed_form_answers(mod_form)
    result = await service.save_application(
        applicant, {"application_type": "mod", "answers": answers}, submit=True
    )
    options = await service.application_options(applicant)
    assert options["cooldowns"]["mod"]["active"] is True
    assert options["cooldowns"]["judge"]["active"] is False

    judge_form = await service.application_form(applicant, "judge")
    assert judge_form["application"]["application_type"] == "judge"

    concurrent = await service.application_options(applicant)
    assert {item["application_type"] for item in concurrent["active_applications"]} == {
        "judge",
        "mod",
    }

    await service.db.execute(
        "UPDATE staff_applications SET status='rejected' WHERE id=?",
        (int(result["application"]["id"]),),
    )

    with pytest.raises(PortalError) as caught:
        await service.application_form(applicant, "mod")
    assert caught.value.status == 429
    assert caught.value.code == "application_cooldown"


@pytest.mark.asyncio
async def test_mod_application_scope_and_acceptance_never_grant_reviewer_role(portal):
    service, _guild = portal
    application_id = await service.db.execute_insert(
        "INSERT INTO staff_applications(guild_id,applicant_id,application_type,status,"
        "answers_json,created_ts,updated_ts,submitted_ts) "
        "VALUES(?,?,'mod','submitted','{}',100,100,100)",
        (GUILD_ID, 999),
    )

    head_view = await service.applications(principal(HEAD_ID, "head_reviewer"), {})
    admin_view = await service.applications(principal(ADMIN_ID, "admin"), {})
    assert application_id not in {int(item["id"]) for item in head_view["items"]}
    assert application_id in {int(item["id"]) for item in admin_view["items"]}

    result = await service.application_action(
        principal(ADMIN_ID, "admin"),
        application_id,
        {"action": "accept", "reason": "Strong application", "confirmed": True},
    )
    assert result["status"] == "accepted"
    assert result["role_delivery"] == "manual"
    assert all(kind != "add_role" for kind, _payload in service.bot.outbox.calls)
    assert any(kind == "send_dm" for kind, _payload in service.bot.outbox.calls)
    dm_content = next(
        payload["payload"]["content"]
        for kind, payload in service.bot.outbox.calls
        if kind == "send_dm"
    )
    assert f"#{application_id}" not in dm_content


@pytest.mark.asyncio
async def test_application_actions_follow_status_and_staff_dm_preserves_state(portal):
    service, _guild = portal
    application_id = await service.db.execute_insert(
        "INSERT INTO staff_applications(guild_id,applicant_id,application_type,status,"
        "answers_json,created_ts,updated_ts,submitted_ts) "
        "VALUES(?,?,'judge','accepted','{}',100,100,100)",
        (GUILD_ID, 999),
    )

    listing = await service.applications(principal(HEAD_ID, "head_reviewer"), {"status": "all"})
    application = next(item for item in listing["items"] if int(item["id"]) == application_id)
    assert application["available_actions"] == ["message"]

    result = await service.application_action(
        principal(HEAD_ID, "head_reviewer"),
        application_id,
        {"action": "message", "message": "Please check your Discord roles."},
    )
    assert result["status"] == "accepted"
    assert result["message_delivery"] == "pending"
    row = await service.db.fetchone(
        "SELECT status FROM staff_applications WHERE id=?", (application_id,)
    )
    assert row["status"] == "accepted"
    kind, call = service.bot.outbox.calls[-1]
    assert kind == "send_dm"
    assert call["user_id"] == 999
    assert "Please check your Discord roles." in call["payload"]["content"]
    assert f"#{application_id}" not in call["payload"]["content"]

    event = await service.db.fetchone(
        "SELECT event,from_status,to_status,detail_json FROM staff_application_events "
        "WHERE application_id=? ORDER BY id DESC LIMIT 1",
        (application_id,),
    )
    assert (event["event"], event["from_status"], event["to_status"]) == (
        "message",
        "accepted",
        "accepted",
    )
    assert json.loads(event["detail_json"])["message"] == "Please check your Discord roles."

    with pytest.raises(PortalError) as caught:
        await service.application_action(
            principal(HEAD_ID, "head_reviewer"),
            application_id,
            {"action": "reject", "reason": "Too late", "confirmed": True},
        )
    assert caught.value.status == 409
    assert caught.value.code == "application_action_unavailable"


@pytest.mark.asyncio
async def test_repeat_interview_queues_a_distinct_interview_run(portal):
    service, _guild = portal
    application_id = await service.db.execute_insert(
        "INSERT INTO staff_applications(guild_id,applicant_id,application_type,status,"
        "answers_json,created_ts,updated_ts,submitted_ts,interview_ticket_channel_id) "
        "VALUES(?,?,'judge','interview','{}',100,100,100,777)",
        (GUILD_ID, 999),
    )

    for attempt in range(2):
        if attempt:
            await service.db.execute(
                "UPDATE staff_applications SET status='hold' WHERE id=?",
                (application_id,),
            )
        result = await service.application_action(
            principal(HEAD_ID, "head_reviewer"),
            application_id,
            {"action": "interview", "confirmed": True},
        )
        assert result["status"] == "interview"

    calls = [call for kind, call in service.bot.outbox.calls if kind == "create_interview_ticket"]
    assert len(calls) == 2
    assert all(call["payload"]["repeat_interview"] is True for call in calls)
    assert all(call["payload"]["interview_run_id"] for call in calls)
    assert calls[0]["idempotency_key"] != calls[1]["idempotency_key"]
    assert all(
        call["idempotency_key"].endswith(":interview-ticket") for call in calls
    )


@pytest.mark.asyncio
async def test_pending_interview_delivery_cannot_spawn_a_duplicate(portal):
    service, _guild = portal
    application_id = await service.db.execute_insert(
        "INSERT INTO staff_applications(guild_id,applicant_id,application_type,status,"
        "answers_json,created_ts,updated_ts,submitted_ts) "
        "VALUES(?,?,'judge','interview','{}',100,100,100)",
        (GUILD_ID, 999),
    )
    outbox_id = await service.db.execute_insert(
        "INSERT INTO discord_outbox(correlation_id,idempotency_key,action_type,guild_id,"
        "channel_id,user_id,message_id,payload_json,status,attempts,next_attempt_ts,created_ts,updated_ts) "
        "VALUES('pending-interview','pending-interview:key','create_interview_ticket',?,"
        "0,999,0,'{}','pending',0,100,100,100)",
        (GUILD_ID,),
    )
    await service.db.execute(
        "UPDATE staff_applications SET interview_ticket_outbox_id=? WHERE id=?",
        (outbox_id, application_id),
    )

    listing = await service.applications(principal(HEAD_ID, "head_reviewer"), {})
    application = next(item for item in listing["items"] if int(item["id"]) == application_id)
    assert application["interview_delivery_status"] == "pending"
    assert "interview" not in application["available_actions"]
    assert "message" in application["available_actions"]

    with pytest.raises(PortalError) as caught:
        await service.application_action(
            principal(HEAD_ID, "head_reviewer"),
            application_id,
            {"action": "interview", "confirmed": True},
        )
    assert caught.value.status == 409
    assert caught.value.code == "interview_delivery_pending"


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
        ("Foggy Morning", "82599323", 1),
        ("Somewhere", "144535118", 1),
        ("VOICE", "88350599", 1),
        ("Pillows", "110359686", 1),
        ("Not My Style", "91281165", 1),
    ]
    assert _youtube_embed_url("https://youtu.be/v5tr0Tg9-9c?si=test") == (
        "https://www.youtube-nocookie.com/embed/v5tr0Tg9-9c"
    )
    assert _youtube_embed_url("https://www.youtube.com/watch?v=bfQj4ZU2nQM") == (
        "https://www.youtube-nocookie.com/embed/bfQj4ZU2nQM"
    )
    assert _youtube_embed_url(
        "https://www.youtube.com/watch?v=OU8v8D_CNw8&list=RDOU8v8D_CNw8&start_radio=1"
    ) == "https://www.youtube-nocookie.com/embed/OU8v8D_CNw8"
    assert _youtube_embed_url("https://example.com/watch?v=bfQj4ZU2nQM") == ""


@pytest.mark.asyncio
async def test_v2_application_form_sections_wording_and_immutable_snapshot(portal):
    service, _guild = portal
    applicant = principal(999, "applicant")
    form = await service.application_form(applicant, "judge")
    assert form["form"]["version"] == "applications-v2"
    assert form["form"]["estimated_minutes"] == 18
    assert form["form"]["sections"] == [
        "Basics",
        "Experience",
        "Scenarios",
        "Work sample",
        "Confirmation",
    ]
    tier_question = next(
        question for question in form["questions"] if question["key"] == "rate_type"
    )
    assert tier_question["label"] == (
        "What type of rate would you recommend this level for? (Rate, feature...)"
    )
    answers = {
        question["key"]: (
            question["options"][0]
            if question["type"] == "single_choice"
            else "A specific answer with enough context for staff review."
        )
        for question in form["questions"]
        if question["required"]
    }
    submitted = await service.save_application(
        applicant,
        {"application_type": "judge", "answers": answers},
        submit=True,
    )
    application_id = int(submitted["application"]["id"])
    stored = await service.db.fetchone(
        "SELECT form_version,answers_json,submitted_answers_json,"
        "submitted_questions_json FROM staff_applications WHERE id=?",
        (application_id,),
    )
    assert stored["form_version"] == "applications-v2"
    submitted_answers = json.loads(stored["submitted_answers_json"])
    assert {key: submitted_answers[key] for key in answers} == answers
    assert submitted_answers["additional_context"] == ""
    assert any(
        question["key"] == "rate_type"
        for question in json.loads(stored["submitted_questions_json"])
    )
    with pytest.raises(PortalError) as caught:
        await service.save_application(
            applicant,
            {
                "application_type": "judge",
                "answers": {**answers, "motivation": "Changed after submission"},
            },
            submit=False,
        )
    assert caught.value.code == "application_active"
    unchanged = await service.db.fetchone(
        "SELECT submitted_answers_json FROM staff_applications WHERE id=?",
        (application_id,),
    )
    assert json.loads(unchanged["submitted_answers_json"])["motivation"] == answers[
        "motivation"
    ]


@pytest.mark.asyncio
async def test_v2_assessments_calibration_and_probation_gate_final_decision(portal):
    service, _guild = portal
    now = int(time.time())
    application_id = await service.db.execute_insert(
        "INSERT INTO staff_applications("
        "guild_id,applicant_id,application_type,status,answers_json,form_version,"
        "submitted_answers_json,submitted_questions_json,created_ts,updated_ts,submitted_ts"
        ") VALUES(?,?,'judge','submitted','{}','applications-v2','{}','[]',?,?,?)",
        (GUILD_ID, 999, now, now, now),
    )
    rubric = service._application_rubric("judge")

    def assessment(score, recommendation):
        return {
            "scores": {dimension["key"]: score for dimension in rubric["dimensions"]},
            "evidence": {
                dimension["key"]: f"Evidence for {dimension['label']}"
                for dimension in rubric["dimensions"]
            },
            "recommendation": recommendation,
        }

    await service.application_assessment(
        principal(HEAD_ID, "head_reviewer"), application_id, assessment(3, "hold")
    )
    with pytest.raises(PortalError) as incomplete:
        await service.application_action(
            principal(OWNER_ID, "owner"),
            application_id,
            {
                "action": "accept",
                "category": "standard_probation",
                "reason": "Private decision rationale",
                "confirmed": True,
            },
        )
    assert incomplete.value.code == "assessments_incomplete"

    await service.application_assessment(
        principal(ADMIN_ID, "admin"),
        application_id,
        assessment(5, "accept"),
    )
    with pytest.raises(PortalError) as disagreement:
        await service.application_action(
            principal(OWNER_ID, "owner"),
            application_id,
            {
                "action": "accept",
                "category": "standard_probation",
                "reason": "Private decision rationale",
                "confirmed": True,
            },
        )
    assert disagreement.value.code == "calibration_required"

    await service.application_action(
        principal(OWNER_ID, "owner"),
        application_id,
        {
            "action": "calibrate",
            "reason": "The reviewers discussed the evidence and aligned on expectations.",
            "confirmed": True,
        },
    )
    accepted = await service.application_action(
        principal(OWNER_ID, "owner"),
        application_id,
        {
            "action": "accept",
            "category": "standard_probation",
            "reason": "Private decision rationale",
            "applicant_message": "Thank you for the thoughtful application.",
            "confirmed": True,
        },
    )
    assert accepted["status"] == "accepted_pending_role"
    probation = await service.db.fetchone(
        "SELECT status,due_ts,started_ts FROM staff_application_probations "
        "WHERE application_id=?",
        (application_id,),
    )
    assert probation["status"] == "active"
    assert int(probation["due_ts"]) - int(probation["started_ts"]) == 30 * 86400

    _status, mine = await service._handle_apply(
        "GET", "/api/apply/mine", principal(999, "applicant"), {}
    )
    applicant_record = next(
        item for item in mine["items"] if int(item["id"]) == application_id
    )
    assert "decision_reason" not in applicant_record
    assert applicant_record["applicant_message"] == (
        "Thank you for the thoughtful application."
    )


@pytest.mark.asyncio
async def test_v2_interview_must_be_completed_before_it_resolves_calibration(portal):
    service, _guild = portal
    now = int(time.time())
    application_id = await service.db.execute_insert(
        "INSERT INTO staff_applications("
        "guild_id,applicant_id,application_type,status,answers_json,form_version,"
        "submitted_answers_json,submitted_questions_json,created_ts,updated_ts,submitted_ts"
        ") VALUES(?,?,'judge','submitted','{}','applications-v2','{}','[]',?,?,?)",
        (GUILD_ID, 998, now, now, now),
    )
    rubric = service._application_rubric("judge")

    def assessment(score, recommendation):
        return {
            "scores": {dimension["key"]: score for dimension in rubric["dimensions"]},
            "evidence": {
                dimension["key"]: f"Evidence for {dimension['label']}"
                for dimension in rubric["dimensions"]
            },
            "recommendation": recommendation,
        }

    await service.application_assessment(
        principal(HEAD_ID, "head_reviewer"), application_id, assessment(2, "reject")
    )
    await service.application_assessment(
        principal(ADMIN_ID, "admin"), application_id, assessment(5, "accept")
    )
    await service.application_action(
        principal(OWNER_ID, "owner"),
        application_id,
        {
            "action": "interview",
            "reason": "Clarify the contradictory work-sample evidence.",
            "clarification_questions": "Explain the recommendation.\nHow would you phrase the feedback?",
            "recommendation": "hold",
            "confirmed": True,
        },
    )
    interview = await service.db.fetchone(
        "SELECT id,status FROM staff_application_interviews WHERE application_id=?",
        (application_id,),
    )
    assert interview["status"] == "requested"
    await service.db.execute(
        "UPDATE staff_application_interviews SET status='open',ticket_channel_id=777 "
        "WHERE id=?",
        (interview["id"],),
    )
    await service.db.execute(
        "UPDATE staff_applications SET interview_ticket_channel_id=777 WHERE id=?",
        (application_id,),
    )

    with pytest.raises(PortalError) as unresolved:
        await service.application_action(
            principal(OWNER_ID, "owner"),
            application_id,
            {
                "action": "accept",
                "category": "standard_probation",
                "reason": "Private decision rationale",
                "confirmed": True,
            },
        )
    assert unresolved.value.code == "calibration_required"

    completed = await service.application_interview_outcome(
        principal(HEAD_ID, "head_reviewer"),
        application_id,
        {
            "interview_id": interview["id"],
            "notes": "The applicant explained the evidence and produced clear feedback.",
            "recommendation": "accept",
            "confirmed": True,
        },
    )
    assert completed["calibration_resolved"] is True
    stored = await service.db.fetchone(
        "SELECT status,notes,recommendation,completed_by,completed_ts "
        "FROM staff_application_interviews WHERE id=?",
        (interview["id"],),
    )
    assert stored["status"] == "completed"
    assert stored["recommendation"] == "accept"
    assert stored["completed_by"] == HEAD_ID
    assert stored["completed_ts"] is not None

    accepted = await service.application_action(
        principal(OWNER_ID, "owner"),
        application_id,
        {
            "action": "accept",
            "category": "standard_probation",
            "reason": "Private decision rationale",
            "confirmed": True,
        },
    )
    assert accepted["status"] == "accepted_pending_role"


@pytest.mark.asyncio
async def test_application_analytics_are_aggregate_and_private(portal):
    service, _guild = portal
    now = int(time.time())
    await service.db.execute(
        "INSERT INTO staff_applications("
        "guild_id,applicant_id,application_type,status,answers_json,created_ts,"
        "updated_ts,submitted_ts,first_review_ts,decided_ts,decision_category"
        ") VALUES(?,?,'mod','rejected','{}',?,?,?,?,?,'availability')",
        (GUILD_ID, 999, now - 7200, now, now - 7200, now - 3600, now),
    )
    result = await service.statistics(principal(HEAD_ID, "head_reviewer"))
    process = result["application_process"]
    assert process["submitted"] == 1
    assert process["average_first_review_seconds"] == 3600
    assert process["reason_breakdown"] == {"availability": 1}
    assert "applicant_id" not in json.dumps(process)
    assert _youtube_embed_url("https://youtu.be/too-short") == ""


@pytest.mark.asyncio
async def test_appeal_form_uses_discord_ban_and_audit_provenance_for_outsider(portal):
    service, guild = portal
    outsider = principal(998, "applicant")
    guild.fetch_ban = AsyncMock(
        return_value=SimpleNamespace(reason="Sapphire ban reason")
    )

    async def audit_logs(**_kwargs):
        yield SimpleNamespace(
            id=123456789012345678,
            target=SimpleNamespace(id=998),
            user=SimpleNamespace(id=555),
            reason="Sapphire ban reason",
            created_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
        )

    guild.audit_logs = audit_logs
    form = await service.appeals.form(outsider)

    assert form["eligibility"]["eligible"] is True
    assert form["punishment"]["reason"] == "Sapphire ban reason"
    assert form["punishment"]["reason_source"] == "discord_ban"
    assert form["punishment"]["issued_ts"] == 1788264000
    assert "issued_by_id" not in form["punishment"]
    assert any(question.get("show_for") for question in form["questions"])


@pytest.mark.asyncio
async def test_appeal_reason_conflict_is_preserved_and_submission_is_immutable(portal):
    service, guild = portal
    applicant = principal(999, "applicant")
    guild.fetch_ban = AsyncMock(return_value=SimpleNamespace(reason="Live reason"))

    async def audit_logs(**_kwargs):
        yield SimpleNamespace(
            id=999999999999999999,
            target=SimpleNamespace(id=999),
            user=SimpleNamespace(id=555),
            reason="Older audit reason",
            created_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        )

    guild.audit_logs = audit_logs
    answers = {
        "primary_ground": "I am asking for reconsideration",
        "chronology": "I am providing a chronological account with enough detail for a fair review.",
        "disputed_detail": "",
        "reconsideration": "I understand the concern and have changed how I handle conflict.",
        "evidence_links": "",
        "requested_outcome": "Remove the punishment",
        "confirmation": "I confirm",
    }
    submitted = await service.appeals.save(
        applicant, {"answers": answers}, submit=True
    )
    appeal_id = int(submitted["application"]["id"])
    notification_kind, notification = service.bot.outbox.calls[-1]
    assert notification_kind == "send_channel"
    assert notification["channel_id"] == 1455042313855307939
    stored = await service.db.fetchone(
        "SELECT submitted_snapshot_json,answers_json FROM punishment_appeals WHERE id=?",
        (appeal_id,),
    )
    snapshot = json.loads(stored["submitted_snapshot_json"])
    assert snapshot["reason"] == "Live reason"
    assert snapshot["reason_conflict"] is True

    changed = dict(answers, chronology="A replacement answer that must not overwrite the submitted case.")
    retried = await service.appeals.save(applicant, {"answers": changed}, submit=True)
    assert retried["application"]["id"] == appeal_id
    after = await service.db.fetchone(
        "SELECT submitted_snapshot_json,answers_json FROM punishment_appeals WHERE id=?",
        (appeal_id,),
    )
    assert after["submitted_snapshot_json"] == stored["submitted_snapshot_json"]
    assert after["answers_json"] == stored["answers_json"]


@pytest.mark.asyncio
async def test_appeal_lookup_failure_keeps_prior_evidence_but_blocks_submission(portal):
    service, guild = portal
    applicant = principal(997, "applicant")
    guild.fetch_ban = AsyncMock(return_value=SimpleNamespace(reason="Verified reason"))

    async def audit_logs(**_kwargs):
        yield SimpleNamespace(
            id=777777777777777777,
            target=SimpleNamespace(id=997),
            user=SimpleNamespace(id=555),
            reason="Verified reason",
            created_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        )

    guild.audit_logs = audit_logs
    first = await service.appeals.snapshot_punishment(applicant, force=True)
    response = SimpleNamespace(status=403, reason="Forbidden", headers={})
    guild.fetch_ban = AsyncMock(
        side_effect=discord.Forbidden(
            response, {"message": "Missing Permissions", "code": 50013}
        )
    )
    failed = await service.appeals.snapshot_punishment(applicant, force=True)

    assert failed["lookup_status"] == "forbidden"
    assert failed["reason"] == "Verified reason"
    assert failed["issued_ts"] == first["issued_ts"]
    assert (
        json.loads(failed["source_detail_json"])["evidence_reused_from_snapshot_id"]
        == first["id"]
    )
    with pytest.raises(PortalError) as blocked:
        await service.appeals.save(
            applicant,
            {
                "answers": {
                    "primary_ground": "Other",
                    "chronology": "A complete chronological account with enough detail for review.",
                    "disputed_detail": "",
                    "reconsideration": "",
                    "evidence_links": "",
                    "requested_outcome": "Review the decision",
                    "confirmation": "I confirm",
                }
            },
            submit=True,
        )
    assert blocked.value.code == "manual_punishment_incomplete"


@pytest.mark.asyncio
async def test_appeal_discovers_timeout_and_configured_restriction_roles(portal):
    service, guild = portal
    response = SimpleNamespace(status=404, reason="Not Found", headers={})
    guild.fetch_ban = AsyncMock(
        side_effect=discord.NotFound(response, {"message": "Unknown Ban", "code": 10026})
    )

    async def no_audit_logs(**_kwargs):
        if False:
            yield None

    guild.audit_logs = no_audit_logs
    member = guild.get_member(999)
    member.timed_out_until = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=2)
    timeout = await service.appeals.snapshot_punishment(
        principal(999, "applicant"), force=True
    )
    assert timeout["punishment_type"] == "timeout"
    assert timeout["lookup_status"] == "found"
    assert json.loads(timeout["source_detail_json"])["timeout_until_ts"] > int(time.time())

    member.timed_out_until = None
    restriction_role = service.bot.config.get_int_list(
        "staff_portal", "appeal_restriction_role_ids"
    )[0]
    member.roles.append(SimpleNamespace(id=restriction_role))
    restriction = await service.appeals.snapshot_punishment(
        principal(999, "applicant"), force=True
    )
    assert restriction["punishment_type"] == "restriction_role"
    assert json.loads(restriction["source_detail_json"])["role_id"] == str(
        restriction_role
    )


@pytest.mark.asyncio
async def test_manual_punishment_can_open_appeal_but_cannot_auto_remove(portal):
    service, guild = portal
    response = SimpleNamespace(status=404, reason="Not Found", headers={})
    guild.fetch_ban = AsyncMock(
        side_effect=discord.NotFound(response, {"message": "Unknown Ban", "code": 10026})
    )

    async def no_audit_logs(**_kwargs):
        if False:
            yield None

    guild.audit_logs = no_audit_logs
    answers = {
        "primary_ground": "Other",
        "chronology": "This is a complete chronological account with enough detail for staff review.",
        "disputed_detail": "",
        "reconsideration": "",
        "evidence_links": "",
        "requested_outcome": "Remove the punishment",
        "confirmation": "I confirm",
    }
    result = await service.appeals.save(
        principal(999, "applicant"),
        {
            "answers": answers,
            "manual_punishment": {
                "type": "timeout",
                "reason": "The reason shown to me",
                "issued_date": "2026-09-20",
                "details": "The Discord record was unavailable.",
            },
        },
        submit=True,
    )
    appeal_id = int(result["application"]["id"])
    punishment = await service.db.fetchone(
        "SELECT p.* FROM moderation_punishments p JOIN punishment_appeals a ON a.punishment_id=p.id WHERE a.id=?",
        (appeal_id,),
    )
    assert punishment["lookup_status"] == "applicant_reported"
    assert punishment["reason_source"] == "applicant_reported"

    await service.db.execute(
        "UPDATE punishment_appeals SET status='under_review' WHERE id=?", (appeal_id,)
    )
    findings = {
        "factual_accuracy": "Reviewed",
        "rule_applicability": "Reviewed",
        "proportionality": "Reviewed",
        "consistency": "Reviewed",
        "new_evidence": "Reviewed",
        "current_risk": "Reviewed",
    }
    for reviewer in (principal(ADMIN_ID, "admin"), principal(HEAD_ID, "admin")):
        await service.appeals.assessment(
            reviewer,
            appeal_id,
            {"findings": findings, "recommendation": "removed", "rationale": "Independent review."},
        )
    with pytest.raises(PortalError) as blocked:
        await service.appeals.action(
            principal(OWNER_ID, "owner"),
            appeal_id,
            {
                "action": "decide",
                "outcome": "removed",
                "internal_rationale": "Decision",
                "applicant_explanation": "Staff approved the appeal.",
                "execute_removal": True,
            },
        )
    assert blocked.value.code == "unverified_punishment"


@pytest.mark.asyncio
async def test_appeal_decision_requires_two_independent_reviews_and_queues_unban(portal):
    service, guild = portal
    applicant = principal(999, "applicant")
    guild.fetch_ban = AsyncMock(return_value=SimpleNamespace(reason=None))

    async def audit_logs(**_kwargs):
        if False:
            yield None

    guild.audit_logs = audit_logs
    answers = {
        "primary_ground": "Other",
        "chronology": "This is a complete chronological account of the events for staff review.",
        "disputed_detail": "",
        "reconsideration": "",
        "evidence_links": "",
        "requested_outcome": "Remove the punishment",
        "confirmation": "I confirm",
    }
    appeal = await service.appeals.save(applicant, {"answers": answers}, submit=True)
    appeal_id = int(appeal["application"]["id"])
    findings = {
        "factual_accuracy": "Reviewed",
        "rule_applicability": "Reviewed",
        "proportionality": "Reviewed",
        "consistency": "Reviewed",
        "new_evidence": "Reviewed",
        "current_risk": "Reviewed",
    }
    await service.appeals.assessment(
        principal(ADMIN_ID, "admin"),
        appeal_id,
        {"findings": findings, "recommendation": "removed", "rationale": "Independent review one."},
    )
    with pytest.raises(PortalError) as early:
        await service.appeals.action(
            principal(OWNER_ID, "owner"),
            appeal_id,
            {"action": "decide", "outcome": "removed", "internal_rationale": "Decision", "applicant_explanation": "Your ban was removed.", "execute_unban": True},
        )
    assert early.value.code == "second_review_required"
    await service.appeals.assessment(
        principal(HEAD_ID, "admin"),
        appeal_id,
        {"findings": findings, "recommendation": "removed", "rationale": "Independent review two."},
    )
    result = await service.appeals.action(
        principal(OWNER_ID, "owner"),
        appeal_id,
        {"action": "decide", "outcome": "removed", "internal_rationale": "Decision", "applicant_explanation": "Your ban was removed.", "execute_unban": True},
    )
    assert result["unban_outbox_id"]
    assert service.bot.outbox.calls[-1][0] == "unban_member"


@pytest.mark.asyncio
async def test_punishment_appeals_have_an_independent_runtime_switch(portal):
    service, _guild = portal
    await service.db.set_runtime_setting(
        "staff_portal.safe_config",
        {
            "applications_open": False,
            "application_open_by_type": {"judge": False, "mod": False},
            "appeals_open": True,
        },
    )

    options = await service.application_options(principal(999, "applicant"))
    appeal = next(
        item for item in options["items"] if item["application_type"] == "appeal"
    )

    assert options["applications_open"] is False
    assert appeal["open"] is True


@pytest.mark.asyncio
async def test_reopening_is_controlled_and_removes_case_cooldown(portal):
    service, guild = portal
    applicant = principal(999, "applicant")
    guild.fetch_ban = AsyncMock(return_value=SimpleNamespace(reason="Original reason"))

    async def audit_logs(**_kwargs):
        if False:
            yield None

    guild.audit_logs = audit_logs
    appeal = await service.appeals.save(
        applicant,
        {
            "answers": {
                "primary_ground": "Other",
                "chronology": "A complete chronological account with sufficient detail for review.",
                "disputed_detail": "",
                "reconsideration": "",
                "evidence_links": "",
                "requested_outcome": "Review the decision",
                "confirmation": "I confirm",
            }
        },
        submit=True,
    )
    appeal_id = int(appeal["application"]["id"])
    punishment = await service.db.fetchone(
        "SELECT punishment_id FROM punishment_appeals WHERE id=?", (appeal_id,)
    )
    now = int(time.time())
    await service.db.execute(
        "UPDATE punishment_appeals SET status='decided',outcome='upheld' WHERE id=?",
        (appeal_id,),
    )
    await service.db.execute(
        "INSERT INTO punishment_appeal_cooldowns("
        "guild_id,appellant_id,punishment_id,cooldown_until_ts,source,created_ts,updated_ts"
        ") VALUES(?,?,?,?,?,?,?)",
        (GUILD_ID, 999, punishment["punishment_id"], now + 86400, "upheld", now, now),
    )

    await service.appeals.action(
        principal(OWNER_ID, "owner"), appeal_id, {"action": "reopen"}
    )

    stored = await service.db.fetchone(
        "SELECT status FROM punishment_appeals WHERE id=?", (appeal_id,)
    )
    cooldown = await service.db.fetchone(
        "SELECT 1 FROM punishment_appeal_cooldowns WHERE punishment_id=?",
        (punishment["punishment_id"],),
    )
    assert stored["status"] == "second_review"
    assert cooldown is None
