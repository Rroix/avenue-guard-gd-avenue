import asyncio
import csv
import io
import json
import time
from types import SimpleNamespace

import pytest
import pytest_asyncio

import services.historical_audit as service_module
from cogs.HistoricalAudit import HistoricalAuditCog, attachment_groups, split_exports
from services.historical_audit import HistoricalAuditService, normalize_level_snapshot
from utils.db import Database, DatabaseBusyError
from utils.gd_profile import fetch_creator_profile, parse_creator_profile
from utils.gd_validation import combine_level_validation, parse_gdbrowser_level, parse_boomlings_level
from utils.historical_audit import (
    audit_settings, build_dataset, cp_bin, exact_review_prestige, normalize_level_id,
    normalize_prestige, parse_sheet_csv, render_exports, summarize,
)
from utils.historical_audit_schema import AUDIT_TABLES


class Config:
    def __init__(self):
        self.data = {"historical_audit": {"enabled": True}, "guild": {"allowed_guild_id": 717},
                     "impact": {"allowed_user_ids": [11]}}

    def get_int_list(self, section, key):
        return self.data.get(section, {}).get(key, [])

    def get_int(self, section, key):
        return int(self.data.get(section, {}).get(key, 0))


def request(level="111111111", wave=1, result="sent", review="", requester=11, created=100, reviewed=110):
    return {"guild_id": 717, "wave_id": wave, "requester_id": str(requester), "level_id": level,
            "created_ts": created, "reviewed_ts": reviewed, "historical_result": result,
            "review_text": review, "reviewed_by": "22", "status": "reviewed" if result else "pending"}


def make_dataset(rows, labels=None, levels=None, creators=None, config=None):
    return build_dataset(rows, levels or {}, creators or {}, labels or [], audit_settings(config or {}), 10000)


class Providers:
    def __init__(self):
        self.calls = []
        self._validation_provider_locks = {}
        self._validation_provider_last_call = {}
        self.circuit = False
        self.fail = set()

    def _level_validation_providers(self):
        return {"boomlings": True, "gdbrowser": True}

    async def _get_level_validation_session(self):
        return SimpleNamespace()

    def _provider_min_interval(self, _provider):
        return 0

    def _provider_circuit_open(self, _provider):
        return self.circuit

    def _provider_circuit_result(self, _provider):
        return {"ok": False, "error": "HTTP 403 circuit open", "failure_kind": "access_denied"}

    def _record_provider_validation_result(self, _provider, _result):
        pass

    async def _fetch_validation_provider(self, provider, _session, level):
        self.calls.append((provider, level))
        if level in self.fail:
            raise TimeoutError("upstream timeout")
        return parse_gdbrowser_level({"id": level, "name": "Current", "author": "Creator", "playerID": "44",
                                      "accountID": "55", "stars": 0, "featured": False, "epic": False}, level)


@pytest_asyncio.fixture
async def environment(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "audit.db"))
    await db.connect()
    providers = Providers()
    bot = SimpleNamespace(db=db, config=Config(), get_cog=lambda name: providers if name == "RequestLevelsCog" else None)
    sleep = asyncio.sleep

    async def fast_sleep(_seconds):
        await sleep(0)

    monkeypatch.setattr(service_module.asyncio, "sleep", fast_sleep)
    try:
        yield db, bot, HistoricalAuditService(bot), providers
    finally:
        await db.close()


async def insert_request(db, level="111111111", wave=1, user=11, result="sent", review="Rate"):
    await db.execute("INSERT INTO level_request_submissions(guild_id,wave_id,user_id,level_id,status,result,review_text,reviewed_by,reviewed_ts,created_ts) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (717, wave, user, level, "reviewed", result, review, 22, 110 + wave, 100 + wave))


@pytest.mark.parametrize("value,expected", [(0, "0"), (1, "1"), (2, "2"), (3, "3"), (4, "4+"), (100, "4+"), (None, None), (-1, None), (True, None), (0.0, None)])
def test_CP_bins_zero_is_not_unknown(value, expected):
    assert cp_bin(value) == expected


@pytest.mark.parametrize("value,expected", [(" Rate ", "rate"), ("FEATURE", "feature"), ("featured", "feature"), ("EpIc", "epic"), ("Legendary", "legendary"), ("MYTHIC", "mythic"), ("rate/epic", None), ("", None)])
def test_exact_prestige_normalization(value, expected):
    assert normalize_prestige(value) == expected


@pytest.mark.parametrize("text,expected", [("Sent for Epic", "epic"), ("Rate", "rate"), ("Prestige: Mythic.", "mythic"), ("Not epic", None), ("feature or epic", None), ("epic epic", None), ("Maybe rate", None), ("Rejected", None), ("SentforEpic", None)])
def test_conservative_review_text(text, expected):
    assert exact_review_prestige(text) == expected


def test_sheet_missing_rows_remain_null_and_ambiguous_repeated_ID_is_not_guessed():
    labels, errors = parse_sheet_csv(b"level_id,prestige\n111111111,Feature\n333333333,Mythic\n")
    assert not errors
    rows = [request(), request(wave=2, requester=12, created=200, reviewed=210), request("222222222", requester=13)]
    dataset, enrichment = make_dataset(rows, labels)
    assert all(row["prestige"] is None and row["prestige_source"] is None for row in dataset)
    assert {row["match_confidence"] for row in enrichment} == {"ambiguous", "unmatched"}


def test_sheet_wave_qualifier_and_conflicting_sources():
    labels, _ = parse_sheet_csv(b"level_id,prestige,wave_id\n111111111,feature,1\n111111111,mythic,2\n")
    dataset, enrichment = make_dataset([request(review="Epic"), request(wave=2, requester=12, created=200)], labels)
    assert dataset[0]["prestige"] is None and dataset[0]["prestige_conflict"] is True
    assert dataset[1]["prestige"] == "mythic" and dataset[1]["prestige_source"] == "google_sheet"
    assert any(row.get("prestige_conflict") for row in enrichment)


def test_two_sheet_labels_disagree_without_silent_choice():
    labels, _ = parse_sheet_csv(b"id,category\n111111111,rate\n111111111,epic\n")
    dataset, _ = make_dataset([request()], labels)
    assert dataset[0]["prestige_conflict"] and dataset[0]["prestige_source"] is None


def test_csv_invalid_qualifiers_and_missing_label_are_recorded():
    labels, errors = parse_sheet_csv(b"level_id,prestige,wave,date\n111111111,,1,\n222222222,epic,no,\n333333333,rate,3,not-a-date\n")
    assert labels == [] and len(errors) == 3


def test_profile_key_semantics_and_identity_are_verified():
    profile = parse_creator_profile("1:Creator:2:44:8:0:16:55", "55")
    assert profile["current_creator_points"] == 0
    for raw in ("1:Creator:2:44:16:55", "1:Creator:2:44:8:no:16:55", "1:Creator:2:44:8:0:16:56", "1:Creator:2:44:8:0:8:2:16:55", "-1", "<html>403</html>"):
        failure = parse_creator_profile(raw, "55")
        assert not failure["ok"] and failure.get("current_creator_points") is None


@pytest.mark.asyncio
async def test_direct_profile_http_failure_never_becomes_zero():
    class Response:
        status = 403
        headers = {}
        charset = "utf-8"
        content = SimpleNamespace(read=lambda _limit: asyncio.sleep(0, result=b"denied"))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    session = SimpleNamespace(post=lambda _url, **_kwargs: Response())
    result = await fetch_creator_profile(session, "55")
    assert result["failure_kind"] == "access_denied" and result.get("current_creator_points") is None


def test_current_metadata_uses_nullable_flags_and_does_not_invent_Mythic():
    direct = parse_boomlings_level("1:111111111:2:Name:6:44:18:10:19:1:42:3#44:Creator:55", "111111111")
    snapshot = normalize_level_snapshot("111111111", {"boomlings": direct})
    assert snapshot["current_epic_tier_raw"] == 3
    assert snapshot["current_mythic"] is None and snapshot["current_legendary"] is None
    assert snapshot["current_uploader_account_id"] == "55"
    incomplete = parse_gdbrowser_level({"id": "111111111"}, "111111111")
    assert normalize_level_snapshot("111111111", {"gdbrowser": incomplete})["current_rated"] is None


def test_provider_conflict_withholds_CP_identity_and_unknown_missing_is_not_unrated():
    a = parse_gdbrowser_level({"id": "111111111", "accountID": "55", "playerID": "44"}, "111111111")
    b = parse_gdbrowser_level({"id": "111111111", "accountID": "66", "playerID": "44"}, "111111111")
    snapshot = normalize_level_snapshot("111111111", {"a": a, "b": b})
    assert snapshot["current_uploader_account_id"] is None
    missing = normalize_level_snapshot("111111111", {"a": {"ok": True, "exists": False}})
    assert missing["current_exists"] is False and missing["current_rated"] is None


def test_candidate_components_remain_null_for_unknown_prestige_and_default_g():
    levels = {"111111111": {"current_uploader_account_id": "55"}}
    creators = {"55": {"current_creator_points": 0}}
    config = {"historical_audit": {"cp_formula": {"name": "example-table-v1", "points": {"0": 4}}}}
    dataset, _ = make_dataset([request()], levels=levels, creators=creators, config=config)
    assert dataset[0]["prestige_component_f"] is None and dataset[0]["candidate_priority_total"] is None
    assert dataset[0]["known_components_only_g_plus_h_not_full_priority"] == 4
    labelled, _ = make_dataset([request(review="Rate")], levels=levels, creators=creators)
    assert labelled[0]["prestige_component_f"] == 0 and labelled[0]["candidate_priority_total"] is None
    complete, _ = make_dataset([request(review="Rate")], levels=levels, creators=creators, config=config)
    assert complete[0]["candidate_priority_total"] == 4


def test_repeated_history_has_no_lookahead_and_waiting_is_explicit_proxy():
    rows = [request(reviewed=300), request(wave=2, requester=12, created=200, reviewed=210)]
    dataset, _ = make_dataset(rows)
    assert dataset[1]["previous_request_count_for_level"] == 1 and dataset[1]["previous_sent_count"] == 0
    assert dataset[0]["approved_age_observed_waves_proxy"] == 0
    assert "not_actual_queue_wait" in dataset[0]["waiting_basis"]


def test_report_cross_tabs_and_noncausal_wording_and_private_CSV_safety():
    rows = [request(review="Rate"), request("222222222", result="rejected", requester=12, review="=HYPERLINK(\"bad\")"), request("333333333", result="stolen", requester=13)]
    levels = {"111111111": {"current_rated": True, "gd_lookup_status": "ok"}, "222222222": {"current_rated": False, "gd_lookup_status": "ok"}}
    dataset, enrichment = make_dataset(rows, levels=levels)
    summary = summarize(dataset, levels, {}, enrichment, "test")
    assert summary["current_rating_outcomes"]["sent"]["currently_rated"]["count"] == 1
    assert summary["current_rating_outcomes"]["other"]["unknown"]["count"] == 1
    assert summary["coverage"]["sent_prestige_labels"]["percentage"] == 100
    assert summary["prestige_subset"]["counts"] == {"rate": 1, "feature": 0, "epic": 0, "legendary": 0, "mythic": 0}
    files = render_exports(dataset, levels, {}, enrichment, summary)
    report = files["avenue_historical_audit.md"].decode()
    assert "Currently rated among historically sent levels" in report
    assert "Avenue success rate" not in report
    exported = list(csv.DictReader(io.StringIO(files["avenue_historical_requests.csv"].decode("utf-8-sig"))))
    assert exported[1]["review_text"].startswith("'=")
    assert exported[0]["current_creator_points"] == ""


@pytest.mark.asyncio
async def test_history_capture_excludes_weekly_and_never_mutates_live_rows(environment):
    db, _bot, service, _providers = environment
    await insert_request(db)
    await insert_request(db, wave=2, user=12, result="rejected")
    await db.execute("INSERT INTO weekly_request_reviews(guild_id,request_message_id,channel_id,user_id,week_start,result,created_ts) VALUES(?,?,?,?,?,?,?)", (717, 123, 456, 13, "week", "sent", 100))
    before = [dict(row) for row in await db.fetchall("SELECT * FROM level_request_submissions")]
    run = await service.start(717, 11, refresh_external=False)
    await service.process(await service.get_run(run))
    after = [dict(row) for row in await db.fetchall("SELECT * FROM level_request_submissions")]
    assert before == after
    assert (await service.progress(run))["requests"] == 2
    assert await db.fetchall("SELECT * FROM level_request_state") == []
    assert await db.fetchall("SELECT * FROM gd_level_validation_cache") == []
    assert await db.fetchall("SELECT * FROM discord_outbox") == []
    assert (await service.get_run(run))["status"] == "completed"


@pytest.mark.asyncio
async def test_deduplicated_lookups_profile_zero_and_incremental_cache(environment, monkeypatch):
    db, _bot, service, providers = environment
    await insert_request(db)
    await insert_request(db, wave=2, user=12)
    cp_calls = []

    async def profile(_session, account):
        cp_calls.append(account)
        return parse_creator_profile("1:Creator:2:44:8:0:16:55", account)

    monkeypatch.setattr(service_module, "fetch_creator_profile", profile)
    first = await service.start(717, 11)
    await service.process(await service.get_run(first))
    assert len(providers.calls) == 2 and cp_calls == ["55"]
    files, summary = await service.report(first)
    assert summary["coverage"]["known_current_CP_requests"]["count"] == 2
    assert "0" in files["avenue_creator_snapshot.csv"].decode()
    second = await service.start(717, 11)
    await service.process(await service.get_run(second))
    assert len(providers.calls) == 2 and cp_calls == ["55"]
    forced = await service.start(717, 11, force=True)
    await service.process(await service.get_run(forced))
    assert len(providers.calls) == 4 and cp_calls == ["55", "55"]


@pytest.mark.asyncio
async def test_failed_level_does_not_abort_other_rows_and_failure_cache_expires(environment):
    db, _bot, service, providers = environment
    await insert_request(db)
    await insert_request(db, level="222222222", user=12)
    providers.fail.add("111111111")
    providers.circuit = True
    first = await service.start(717, 11)
    await service.process(await service.get_run(first))
    _, summary = await service.report(first)
    assert summary["coverage"]["gd_success_requests"]["count"] == 1
    assert summary["coverage"]["known_current_CP_requests"]["count"] == 0
    calls = len(providers.calls)
    second = await service.start(717, 11)
    await service.process(await service.get_run(second))
    assert len(providers.calls) == calls
    await db.execute("UPDATE historical_level_audit_snapshots SET expires_ts=? WHERE level_id='111111111'", (int(time.time()) - 1,))
    third = await service.start(717, 11)
    await service.process(await service.get_run(third))
    assert len(providers.calls) == calls + 2


@pytest.mark.asyncio
async def test_duplicate_run_prevention_cancel_and_disabled_start(environment):
    _db, bot, service, _providers = environment
    first = await service.start(717, 11, refresh_external=False)
    with pytest.raises(ValueError, match="already"):
        await service.start(717, 11)
    await service.cancel(first)
    bot.config.data["historical_audit"]["enabled"] = False
    with pytest.raises(ValueError, match="disabled"):
        await service.start(717, 11)


@pytest.mark.asyncio
async def test_restart_resumes_frozen_input_and_finished_items_without_refetch(environment):
    db, bot, service, providers = environment
    await insert_request(db)
    first = await service.start(717, 11)
    await db.execute("INSERT INTO historical_audit_levels VALUES(?,?,?)", (first, "111111111", json.dumps({"level_id": "111111111", "gd_lookup_status": "ok", "current_exists": False})))
    await insert_request(db, level="222222222", user=12)
    resumed = HistoricalAuditService(bot)
    await resumed.process(await resumed.get_run(first))
    assert providers.calls == []
    assert (await resumed.progress(first))["requests"] == 1


@pytest.mark.asyncio
async def test_database_cache_fault_propagates_without_poisoning_external_data(environment, monkeypatch):
    _db, _bot, service, _providers = environment

    async def busy(*_args):
        raise DatabaseBusyError("busy")

    monkeypatch.setattr(service, "_cached", busy)
    with pytest.raises(DatabaseBusyError):
        await service.lookup_level("111111111", {"refresh_external": True})


@pytest.mark.asyncio
async def test_owner_only_command_defers_before_checks_and_never_starts_for_admin(environment):
    _db, bot, _service, _providers = environment
    cog = HistoricalAuditCog(bot)
    events = []

    async def defer(**kwargs):
        events.append(("defer", kwargs))

    async def respond(*args, **kwargs):
        events.append(("respond", args, kwargs))

    ctx = SimpleNamespace(guild=SimpleNamespace(id=717), user=SimpleNamespace(id=99, guild_permissions=SimpleNamespace(administrator=True)), defer=defer, respond=respond)
    await cog.command(ctx, "start", "", True, False, None, "")
    assert events[0] == ("defer", {"ephemeral": True})
    assert events[1][2]["ephemeral"] is True
    assert await cog.service.active_run() is None
    bot.config.data["impact"]["allowed_user_ids"] = []
    assert not cog.is_owner(11)


@pytest.mark.asyncio
async def test_schema_six_upgrade_and_database_backup_preserve_audit(environment, tmp_path):
    db, _bot, service, _providers = environment
    await insert_request(db)
    run = await service.start(717, 11, refresh_external=False)
    await service.process(await service.get_run(run))
    backup = tmp_path / "audit.sqlite3"
    await db.backup_to(backup)
    restored = Database(str(backup))
    try:
        await restored.connect()
        assert (await restored.fetchone("SELECT run_id FROM historical_audit_runs"))["run_id"] == run
        await restored.execute("UPDATE schema_metadata SET schema_version=6 WHERE component='database'")
    finally:
        await restored.close()
    upgraded = Database(str(backup))
    try:
        await upgraded.connect()
        tables = {row["name"] for row in await upgraded.fetchall("SELECT name FROM sqlite_master WHERE type='table'")}
        assert AUDIT_TABLES <= tables
        assert (await upgraded.fetchone("SELECT historical_result FROM historical_audit_requests"))["historical_result"] == "sent"
    finally:
        await upgraded.close()


def test_missing_components_and_raw_ID_deduplication_and_byte_parts():
    assert normalize_level_id("0111111111.0") == "111111111"
    assert normalize_level_id("1e8") is None
    assert normalize_level_id("111111111 not really") is None
    parts = split_exports({"example.csv": b"abcde\nfghijk"}, 5)
    assert b"".join(payload for _name, payload in parts) == b"abcde\nfghijk"


def test_analysis_config_rejects_undefined_anchors_and_nonfinite_CP():
    with pytest.raises(ValueError, match="finite"):
        audit_settings({"historical_audit": {"cp_formula": {"name": "bad", "points": {"0": float("nan")}}}})
    with pytest.raises(ValueError, match="anchors"):
        audit_settings({"historical_audit": {"prestige_x": {"rate": 1, "feature": None, "epic": None, "legendary": None, "mythic": 5}}})


def test_live_validation_semantics_are_identical_when_audit_metadata_is_removed():
    providers = {"gdbrowser": parse_gdbrowser_level({"id": "111111111", "stars": 10, "featured": True, "epic": True, "accountID": "55"}, "111111111")}
    without_audit = {key: {name: value for name, value in row.items() if name != "audit_metadata"} for key, row in providers.items()}
    before = combine_level_validation("111111111", without_audit, checked_ts=100, expires_ts=200)
    after = combine_level_validation("111111111", providers, checked_ts=100, expires_ts=200)
    assert before == after


def test_CP_profile_user_mismatch_is_unknown():
    dataset, _ = make_dataset([request()], levels={"111111111": {"current_uploader_account_id": "55", "current_uploader_user_id": "44"}},
                             creators={"55": {"user_id": "45", "current_creator_points": 0, "cp_lookup_status": "ok"}})
    assert dataset[0]["current_creator_points"] is None
    assert dataset[0]["cp_lookup_status"] == "identity_conflict"


def test_attachment_groups_bound_total_message_bytes():
    files = [("a", b"1234"), ("b", b"5678"), ("c", b"9")]
    groups = list(attachment_groups(files, limit=5))
    assert all(sum(len(data) for _name, data in group) <= 5 for group in groups)
    assert [item for group in groups for item in group] == files


@pytest.mark.asyncio
async def test_completed_report_can_fallback_ephemerally_and_unknown_delivery_is_not_repeated(environment):
    db, bot, service, _providers = environment
    await insert_request(db)
    run_id = await service.start(717, 11, refresh_external=False)
    await service.process(await service.get_run(run_id))
    cog = HistoricalAuditCog(bot)
    messages = []

    async def respond(**kwargs):
        messages.append(kwargs)

    bot.get_user = lambda _user: None

    async def fetch(_user):
        from discord import NotFound
        raise NotFound(SimpleNamespace(status=404, reason="missing"), {"code": 10013, "message": "unknown user"})

    bot.fetch_user = fetch
    cog._contexts[run_id] = (SimpleNamespace(respond=respond), time.monotonic())
    await cog.deliver(await service.get_run(run_id))
    assert messages and all(message["ephemeral"] for message in messages)
    assert (await service.get_run(run_id))["delivery_status"] == "sent"
    await db.execute("UPDATE historical_audit_runs SET delivery_status='sending' WHERE run_id=?", (run_id,))
    messages.clear()
    await cog.deliver(await service.get_run(run_id))
    assert not messages


@pytest.mark.asyncio
async def test_latest_run_is_insertion_order_not_random_UUID(environment):
    _db, _bot, service, _providers = environment
    first = await service.start(717, 11, refresh_external=False)
    await service.cancel(first)
    second = await service.start(717, 11, refresh_external=False)
    assert (await service.get_run())["run_id"] == second


@pytest.mark.asyncio
async def test_durable_lease_denies_second_worker_and_expires_for_restart(environment):
    db, bot, service, _providers = environment
    run = await service.start(717, 11, refresh_external=False)
    second = HistoricalAuditService(bot)
    assert await service._claim_run(run)
    assert not await second._claim_run(run)
    await db.execute("UPDATE historical_audit_runs SET lease_until_ts=0 WHERE run_id=?", (run,))
    assert await second._claim_run(run)
    assert not await service._running(run)
    await db.execute("UPDATE historical_audit_runs SET lease_until_ts=? WHERE run_id=?", (int(time.time()) + 20, run))
    assert await second._running(run)
    assert int((await second.get_run(run))["lease_until_ts"]) >= int(time.time()) + 100


@pytest.mark.asyncio
async def test_cancelled_process_releases_worker_lease_without_losing_input(environment, monkeypatch):
    _db, _bot, service, _providers = environment
    run = await service.start(717, 11, refresh_external=False)

    async def cancel(_run):
        raise asyncio.CancelledError

    monkeypatch.setattr(service, "_process_owned", cancel)
    with pytest.raises(asyncio.CancelledError):
        await service.process(await service.get_run(run))
    assert (await service.get_run(run))["lease_owner"] is None
