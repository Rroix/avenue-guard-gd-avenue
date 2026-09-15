import asyncio
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from cogs.Commands import CommandsCog
from cogs.Operations import OperationsCog
from cogs.RequestLevels import RequestLevelsCog
from main import AvenueBot, _gateway_startup_watchdog, create_bot
from utils.config import Config
from utils.db import Database, DatabaseBusyError
from utils.errors import ErrorReporter, log_error, setup_global_error_handlers
from utils.keepalive import get_public_bot_payload, get_runtime_health, set_keepalive_status, set_runtime_heartbeat
from utils.libsql_worker import DatabaseWorkerTimeout, IsolatedConnection
from utils.outbox import DiscordOutbox
from utils.supervision import start_cog_background


async def wait_for_condition(condition, timeout=3):
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_isolated_native_query_does_not_block_gateway_heartbeat(tmp_path):
    connection = await asyncio.to_thread(IsolatedConnection, str(tmp_path / "isolated.db"))
    gaps = []
    running = True

    async def heartbeat():
        previous = time.monotonic()
        while running:
            await asyncio.sleep(0.01)
            now = time.monotonic()
            gaps.append(now - previous)
            previous = now

    task = asyncio.create_task(heartbeat())
    try:
        result = await asyncio.to_thread(
            lambda: connection.execute(
                "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<3000000) SELECT sum(x) FROM n"
            ).fetchone()
        )
        assert result[0] == 4_500_001_500_000
        assert len(gaps) >= 5
        assert max(gaps) < 0.2
    finally:
        running = False
        await task
        await asyncio.to_thread(connection.close)


@pytest.mark.asyncio
async def test_native_worker_deadline_terminates_only_the_database_process(tmp_path):
    connection = await asyncio.to_thread(IsolatedConnection, str(tmp_path / "deadline.db"))
    connection.set_deadline(0.04)
    try:
        with pytest.raises(DatabaseWorkerTimeout, match="completion is unknown"):
            await asyncio.to_thread(connection.execute,
                "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<100000000) SELECT sum(x) FROM n")
        assert not connection.alive
        assert not connection._process.is_alive()
        assert await asyncio.to_thread(lambda: 42) == 42
    finally:
        await asyncio.to_thread(connection.close)


@pytest.mark.asyncio
async def test_native_worker_preserves_database_api_transactions_ids_and_backups(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "native.db"), remote_url="libsql://test.invalid")
    monkeypatch.setattr(database, "_open_connection_sync", lambda: IsolatedConnection(str(database.path)))
    monkeypatch.setattr(database, "_sync_remote_sync", lambda: None)
    try:
        await database.connect()
        user_id = 1102884420207255653
        await database.execute_transaction([
            ("INSERT INTO user_notification_preferences(guild_id,user_id,request_result_mode,updated_ts) VALUES(?,?,?,?)", (717, user_id, "both", 100)),
            ("INSERT INTO runtime_settings(setting_key,value_json,updated_ts) VALUES(?,?,?)", ("native", '{"ok":true}', 100)),
        ])
        row = await database.fetchone("SELECT user_id FROM user_notification_preferences WHERE user_id=?", (user_id,))
        assert int(row["user_id"]) == user_id
        assert database.health_snapshot()["isolated_worker"] is True
        assert await database.execute_affected("UPDATE user_notification_preferences SET request_result_mode=? WHERE user_id=?", ("dm", user_id)) == 1
        with pytest.raises(ValueError, match="no such table"):
            await database.execute_transaction([
                ("UPDATE user_notification_preferences SET request_result_mode='none'", ()),
                ("INSERT INTO table_that_does_not_exist VALUES(1)", ()),
            ])
        assert (await database.fetchone("SELECT request_result_mode FROM user_notification_preferences"))["request_result_mode"] == "dm"
        assert await database.backup_to(tmp_path / "native.sqlite3") > 0
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_cancelled_database_operation_retains_connection_lock_until_thread_finishes(tmp_path):
    database = Database(str(tmp_path / "cancel.db"))
    await database.connect()
    started = threading.Event()
    finished = threading.Event()

    def operation():
        started.set()
        time.sleep(0.12)
        finished.set()

    first = asyncio.create_task(database._run_locked_with_retry(operation))
    await wait_for_condition(started.is_set)
    first.cancel()

    def second_operation():
        assert finished.is_set(), "a cancelled thread must not race another DB caller"
        return True

    second = asyncio.create_task(database._run_locked_with_retry(second_operation))
    with pytest.raises(asyncio.CancelledError):
        await first
    assert await second is True
    await database.close()


@pytest.mark.asyncio
async def test_database_queue_is_bounded_but_local_modal_snapshot_bypasses_it(tmp_path):
    database = Database(str(tmp_path / "busy.db"))
    await database.connect()
    database._queue_timeout_seconds = 0.04
    await database._lock.acquire()
    try:
        row = await database.fetchone_local("SELECT 42 AS value")
        assert row["value"] == 42
        with pytest.raises(DatabaseBusyError, match="not started"):
            await database.fetchone("SELECT 1")
        assert database.health_snapshot()["queue_timeouts"] == 1
    finally:
        database._lock.release()
        await database.close()


@pytest.mark.asyncio
async def test_current_schema_restart_skips_remote_migration_work(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "restart.db"))
    await database.connect()
    await database.close()
    monkeypatch.setattr(database, "_normalize_weekly_dm_log_sync", lambda: pytest.fail("unchanged schema should not run DDL again"))
    await database.connect()
    assert await database.fetchone("SELECT 1 FROM sqlite_master WHERE name='error_incident_batches'")
    await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["sent", "rejected", "other"])
async def test_review_controls_acknowledge_without_accessing_storage(action):
    cog = object.__new__(RequestLevelsCog)
    cog.bot = SimpleNamespace(db=None)
    cog._cached_interaction_member = lambda _: SimpleNamespace()
    cog._has_reviewer_role = lambda _: True
    interaction = SimpleNamespace(guild=SimpleNamespace(id=717), message=SimpleNamespace(id=555),
                                  response=SimpleNamespace(send_modal=AsyncMock(), send_message=AsyncMock()))
    await cog.handle_review_button(interaction, action)
    if action == "other":
        interaction.response.send_message.assert_awaited_once()
    else:
        interaction.response.send_modal.assert_awaited_once()


@pytest.mark.asyncio
async def test_incident_delivery_and_occurrence_updates_work_while_database_is_stalled():
    blocked = asyncio.Event()
    database = SimpleNamespace(execute_transaction=AsyncMock(side_effect=lambda *a, **k: blocked.wait()),
                               fetchone=AsyncMock(), execute=AsyncMock())
    # A genuine async stall, not an AsyncMock returning an unawaited coroutine.
    async def stall(*_args, **_kwargs):
        await blocked.wait()
    database.execute_transaction = stall
    message = SimpleNamespace(id=999, edit=AsyncMock())
    channel = SimpleNamespace(send=AsyncMock(return_value=message), fetch_message=AsyncMock(return_value=message))
    bot = SimpleNamespace(db=database, config=SimpleNamespace(get_int=lambda *_: 123), get_channel=lambda _: channel)
    reporter = bot._error_reporter = ErrorReporter(bot)
    reporter.delivery_interval = 0.01
    try:
        await asyncio.wait_for(log_error(bot, "Repeated storage incident"), timeout=0.1)
        await log_error(bot, "Repeated storage incident")
        await wait_for_condition(lambda: channel.send.await_count == 1)
        await log_error(bot, "Repeated storage incident")
        await wait_for_condition(lambda: message.edit.await_count >= 1)
        assert channel.send.await_count == 1
        assert reporter.snapshot()[0]["count"] == 3
        assert reporter.snapshot()[0]["pending_persistence"] == 3
        fields = message.edit.call_args.kwargs["embed"].fields
        assert any(field.name == "Occurrences" and field.value == "3" for field in fields)
    finally:
        await reporter.close()


@pytest.mark.asyncio
async def test_incident_batch_retry_does_not_double_count_an_uncertain_commit(tmp_path):
    database = Database(str(tmp_path / "incidents.db"))
    await database.connect()
    original = database.execute_transaction
    calls = 0

    async def uncertain_commit(statements, **kwargs):
        nonlocal calls
        await original(statements, **kwargs)
        calls += 1
        if calls == 1:
            raise RuntimeError("transport reset after commit")

    database.execute_transaction = uncertain_commit
    bot = SimpleNamespace(db=database, config=SimpleNamespace(get_int=lambda *_: 0))
    reporter = bot._error_reporter = ErrorReporter(bot)
    reporter.persistence_retry_seconds = 0.01
    try:
        await log_error(bot, "Idempotent incident")
        await log_error(bot, "Idempotent incident")
        await wait_for_condition(lambda: calls >= 2 and reporter.snapshot()[0]["pending_persistence"] == 0)
        row = await database.fetchone("SELECT occurrence_count FROM error_incidents")
        assert row["occurrence_count"] == 2
        assert calls == 2
    finally:
        await reporter.close()
        await database.close()


@pytest.mark.asyncio
async def test_supervisor_repairs_missing_tasks_and_keeps_configured_retention(tmp_path):
    database = Database(str(tmp_path / "supervisor.db"))
    await database.connect()
    cog = SimpleNamespace(_ticket_scan_task=None, start_background=AsyncMock())
    config = SimpleNamespace(data={"operations": {"retention_days": {"health_metrics": 365}}})
    bot = SimpleNamespace(db=database, config=config, get_cog=lambda name: cog if name == "HelpCog" else None,
                          outbox=SimpleNamespace(recover_stale=AsyncMock()))
    operations = OperationsCog(bot)
    await operations._load_persisted_operations()
    assert config.data["operations"]["retention_days"] == {"health_metrics": 365}
    await operations.restart_stopped_tasks()
    cog.start_background.assert_awaited_once()
    await database.close()


@pytest.mark.asyncio
async def test_operations_pillars_start_even_when_bootstrap_storage_is_stalled():
    blocker = asyncio.Event()
    async def stall():
        await blocker.wait()
    operations = OperationsCog(SimpleNamespace())
    for name in ("_load_persisted_operations", "_outbox_loop", "_supervisor_loop", "_watchdog_loop", "_health_loop", "_maintenance_loop", "_post_deploy_smoke_test"):
        setattr(operations, name, stall)
    try:
        await asyncio.wait_for(operations.start_background(), 0.1)
        before = {name: id(task) for name, task in operations._tasks.items()}
        await operations.start_background()
        assert before == {name: id(task) for name, task in operations._tasks.items()}
        assert {"watchdog", "supervisor", "bootstrap"} <= set(before)
    finally:
        await operations.close_resources()


@pytest.mark.asyncio
async def test_startup_and_supervisor_cannot_start_the_same_cog_concurrently():
    active = 0
    peak = 0
    async def start():
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        await asyncio.sleep(0.02)
        active -= 1
    bot = SimpleNamespace(get_cog=lambda _: SimpleNamespace(start_background=start))
    await asyncio.gather(start_cog_background(bot, "HelpCog"), start_cog_background(bot, "HelpCog"))
    assert peak == 1


@pytest.mark.asyncio
async def test_cold_command_id_cache_uses_guild_command_without_network_sync(monkeypatch):
    command = SimpleNamespace(name="bot", guild_ids=[717])
    bot = object.__new__(AvenueBot)
    bot._application_commands = {}
    bot._pending_application_commands = [command]
    received = []
    async def process(self, interaction, auto_sync=None):
        received.append((self._application_commands[interaction.data["id"]], auto_sync))
    monkeypatch.setattr(discord.Bot, "process_application_commands", process)
    interaction = SimpleNamespace(type=discord.InteractionType.application_command, data={"id": "456", "name": "bot"}, guild_id=717)
    await AvenueBot.process_application_commands(bot, interaction)
    assert received == [(command, False)]


@pytest.mark.asyncio
async def test_persistent_views_are_registered_before_database_startup_and_only_once(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOW_LOCAL_DATABASE_FALLBACK", "1")
    monkeypatch.setenv("AVENUE_GUARD_DB_PATH", str(tmp_path / "views.db"))
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("LIBSQL_AUTH_TOKEN", raising=False)
    bot = create_bot()
    original = bot.add_view
    calls = []
    def add(view):
        calls.append(type(view).__name__)
        original(view)
    monkeypatch.setattr(bot, "add_view", add)
    try:
        assert bot.db._ready is False
        await bot.register_persistent_views()
        await bot.register_persistent_views()
        assert len(calls) == 9
        assert "LevelRequestReviewView" in calls
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_gateway_startup_deadline_closes_stuck_session_for_retry():
    bot = SimpleNamespace(is_closed=lambda: False, _runtime_initialized=False, close=AsyncMock(),
                          config=SimpleNamespace(get_int=lambda *_: 0),
                          db=SimpleNamespace(execute_transaction=AsyncMock(side_effect=RuntimeError("offline"))))
    previous = get_public_bot_payload()["state"]
    try:
        await _gateway_startup_watchdog(bot, timeout=0.01)
        bot.close.assert_awaited_once()
        assert get_public_bot_payload()["state"] == "startup_error"
    finally:
        await bot._error_reporter.close()
        set_keepalive_status(previous)


@pytest.mark.asyncio
async def test_component_and_modal_failures_get_user_response_and_incident_logs():
    events = {}
    bot = SimpleNamespace(event=lambda function: events.setdefault(function.__name__, function),
                          config=SimpleNamespace(get_int=lambda *_: 0),
                          db=SimpleNamespace(execute_transaction=AsyncMock(side_effect=RuntimeError("offline"))))
    setup_global_error_handlers(bot)
    response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock())
    interaction = SimpleNamespace(response=response, data={"custom_id": "review"}, followup=SimpleNamespace(send=AsyncMock()))
    try:
        await events["on_view_error"](RuntimeError("storage stalled"), SimpleNamespace(custom_id="send"), interaction)
        await events["on_modal_error"](RuntimeError("storage stalled"), interaction)
        assert response.send_message.await_count == 2
        assert len(bot._error_reporter.snapshot()) == 2
    finally:
        await bot._error_reporter.close()


def test_readiness_is_not_just_an_open_http_port(monkeypatch):
    import utils.keepalive as keepalive
    previous = keepalive.get_keepalive_status()
    try:
        set_keepalive_status("online")
        set_runtime_heartbeat(lag_ms=0, tasks={}, database={"uses_remote": True, "worker_alive": True}, incidents=[])
        assert get_runtime_health()["ready"] is True
        monkeypatch.setattr(keepalive, "_runtime_heartbeat", time.monotonic() - 30)
        assert get_runtime_health()["ready"] is False
        assert get_public_bot_payload()["online"] is False
        assert get_public_bot_payload()["status"] == "Unavailable"
    finally:
        set_keepalive_status(previous["state"], previous["detail"])


def test_readiness_detects_failed_primary_writes_with_a_live_replica():
    import utils.keepalive as keepalive
    previous = keepalive.get_keepalive_status()
    try:
        set_keepalive_status("online")
        set_runtime_heartbeat(lag_ms=0, tasks={}, database={"uses_remote": True, "worker_alive": True, "primary_write_degraded": True}, incidents=[])
        assert get_runtime_health()["responsive"] is True
        assert get_runtime_health()["ready"] is False
        assert get_public_bot_payload()["status"] == "Degraded"
    finally:
        set_keepalive_status(previous["state"], previous["detail"])


def test_readiness_reports_missing_critical_tasks_but_allows_disabled_features():
    import utils.keepalive as keepalive
    previous = keepalive.get_keepalive_status()
    try:
        set_keepalive_status("online")
        set_runtime_heartbeat(lag_ms=0, tasks={"requests.auto_close": "missing", "background.icon": "disabled"}, database={"connected": True}, incidents=[])
        assert get_runtime_health()["ready"] is False
        assert get_public_bot_payload()["status"] == "Degraded"
        set_runtime_heartbeat(lag_ms=0, tasks={"requests.auto_close": "running", "background.icon": "disabled", "operations.smoke": "completed"}, database={"connected": True}, incidents=[])
        assert get_runtime_health()["ready"] is True
    finally:
        set_keepalive_status(previous["state"], previous["detail"])


@pytest.mark.asyncio
async def test_smoke_test_keeps_in_memory_failure_even_when_storage_is_down():
    bot = SimpleNamespace(
        db=SimpleNamespace(fetchone=AsyncMock(side_effect=RuntimeError("offline")),
                           fetchall=AsyncMock(side_effect=RuntimeError("offline")),
                           execute=AsyncMock(side_effect=RuntimeError("offline")),
                           execute_transaction=AsyncMock(side_effect=RuntimeError("offline"))),
        config=Config("config.json"), get_guild=lambda _: SimpleNamespace(),
        get_cog=lambda _: SimpleNamespace(), user=SimpleNamespace(id=1),
        walk_application_commands=lambda: range(100),
    )
    bot.config.data.setdefault("operations", {})["smoke_test_delay_seconds"] = 0
    cog = OperationsCog(bot)
    try:
        await cog._post_deploy_smoke_test()
        assert cog._last_smoke_result["status"] == "failed"
        assert cog._last_smoke_result["checks"]["database"] is False
        assert cog._last_smoke_result["checks"]["schema_versions"] is False
        assert cog._last_smoke_result["checks"]["outbox"] is False
        assert bot._error_reporter.snapshot()
    finally:
        await bot._error_reporter.close()


@pytest.mark.asyncio
async def test_dashboard_memory_fallback_still_shows_real_failures(tmp_path):
    database = Database(str(tmp_path / "dashboard.db"))
    cog = object.__new__(CommandsCog)
    cog.bot = SimpleNamespace(db=database, get_cog=lambda _: None, config=SimpleNamespace(get_int=lambda *_: 0))
    cog._admin_dashboard_embed = AsyncMock(side_effect=RuntimeError("Turso unavailable"))
    try:
        embed = await cog._safe_admin_dashboard_embed(SimpleNamespace(id=717))
        assert embed.title == "Admin Dashboard - Recovery"
        assert any(field.name == "Storage" for field in embed.fields)
    finally:
        await cog.bot._error_reporter.close()


@pytest.mark.asyncio
async def test_request_review_is_saved_with_durable_disabled_button_edit(tmp_path):
    database = Database(str(tmp_path / "reviews.db"))
    await database.connect()
    guild = SimpleNamespace(id=717)
    message = SimpleNamespace(id=555, edit=AsyncMock(side_effect=RuntimeError("Discord edit failed")))
    channel = SimpleNamespace(id=321, fetch_message=AsyncMock(return_value=message))
    bot = SimpleNamespace(db=database, config=Config("config.json"), get_channel=lambda _: channel)
    cog = object.__new__(RequestLevelsCog)
    cog.bot = bot
    cog._review_lock = asyncio.Lock()
    cog._cached_interaction_member = lambda _: SimpleNamespace()
    cog._has_reviewer_role = lambda _: True
    cog._review_target_channel = AsyncMock(return_value=channel)
    cog._status_channel_id = lambda _: 999
    cog._get_state = AsyncMock(return_value={"wave_id": 1, "state": "open"})
    cog.update_wave_summary = AsyncMock()
    cog._reply_ephemeral = AsyncMock()
    await database.execute(
        "INSERT INTO level_request_submissions(guild_id,wave_id,user_id,level_id,request_message_id,data_json,status,created_ts) VALUES(?,?,?,?,?,?,?,?)",
        (717, 1, 1102884420207255653, "123456789", 555, json.dumps({"level_id": "123456789", "level_name": "Example"}), "pending", int(time.time())),
    )
    interaction = SimpleNamespace(guild=guild, message=message, user=SimpleNamespace(id=88),
                                  response=SimpleNamespace(is_done=lambda: False, defer=AsyncMock()))
    try:
        await cog._finalize_review(interaction, 555, "sent", "Reviewed")
        row = await database.fetchone("SELECT status,result FROM level_request_submissions")
        assert dict(row) == {"status": "reviewed", "result": "sent"}
        actions = await database.fetchall("SELECT action_type,payload_json FROM discord_outbox")
        assert {row["action_type"] for row in actions} == {"send_channel", "edit_message"}
        assert any(json.loads(row["payload_json"]).get("request_review_disabled") for row in actions)
        message.edit = AsyncMock()
        outbox = DiscordOutbox(bot)
        receipt = await database.fetchone("SELECT * FROM discord_outbox WHERE action_type='edit_message'")
        await outbox._deliver(receipt)
        assert all(button.disabled for button in message.edit.call_args.kwargs["view"].children)
        await cog._finalize_review(interaction, 555, "rejected", "second attempt")
        assert (await database.fetchone("SELECT COUNT(*) AS c FROM discord_outbox"))["c"] == 2
    finally:
        reporter = getattr(bot, "_error_reporter", None)
        if reporter:
            await reporter.close()
        await database.close()


@pytest.mark.asyncio
async def test_outbox_receipt_prevents_resend_after_discord_success_and_database_failure(tmp_path):
    database = Database(str(tmp_path / "receipt.db"))
    await database.connect()
    channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=777)))
    bot = SimpleNamespace(db=database, get_channel=lambda _: channel)
    outbox = DiscordOutbox(bot)
    await outbox.enqueue("send_channel", channel_id=123, idempotency_key="durable:receipt")
    original = database.execute
    failed = False
    async def fail_confirmation(sql, params=()):
        nonlocal failed
        if "status='delivered'" in sql and not failed:
            failed = True
            raise RuntimeError("storage unavailable after Discord sent the message")
        await original(sql, params)
    database.execute = fail_confirmation
    try:
        with pytest.raises(RuntimeError):
            await outbox.process_once()
        await original("UPDATE discord_outbox SET updated_ts=0")
        assert await outbox.recover_stale() == 1
        assert (await outbox.process_once())["delivered"] == 1
        assert channel.send.await_count == 1
        assert channel.send.call_args.kwargs["enforce_nonce"] is True
        assert len(channel.send.call_args.kwargs["nonce"]) == 24
        assert outbox._delivery_receipts == {}
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_validation_refresh_cannot_reopen_buttons_after_review(tmp_path):
    database = Database(str(tmp_path / "validation-race.db"))
    await database.connect()
    data = json.dumps({"level_id": "123456789"})
    await database.execute(
        "INSERT INTO level_request_submissions(guild_id,wave_id,user_id,level_id,request_message_id,data_json,status,created_ts) VALUES(?,?,?,?,?,?,?,?)",
        (717, 1, 88, "123456789", 555, data, "pending", 1),
    )
    cog = object.__new__(RequestLevelsCog)
    cog.bot = SimpleNamespace(db=database)
    cog._review_lock = asyncio.Lock()
    cog._validation_card_lock = asyncio.Lock()
    cog._validation_refresh_receipts = {}
    cog._review_target_channel = AsyncMock(return_value=SimpleNamespace(id=123))
    async def lookup(*_args, **_kwargs):
        await database.execute("UPDATE level_request_submissions SET status='reviewed'")
        return {"exists": True, "checked_ts": 10, "expires_ts": 20}
    cog._lookup_level_validation = lookup
    message = SimpleNamespace(edit=AsyncMock())
    try:
        row = await database.fetchone("SELECT * FROM level_request_submissions")
        assert await cog._refresh_review_validation(SimpleNamespace(id=717), "wave", row, message=message) == {}
        message.edit.assert_not_awaited()
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_outbox_prunes_receipt_after_unknown_confirmation_actually_committed(tmp_path):
    database = Database(str(tmp_path / "confirmed-receipt.db"))
    await database.connect()
    channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=777)))
    outbox = DiscordOutbox(SimpleNamespace(db=database, get_channel=lambda _: channel))
    await outbox.enqueue("send_channel", channel_id=123, idempotency_key="durable:confirmed")
    original = database.execute
    async def lose_confirmation(sql, params=()):
        await original(sql, params)
        if "status='delivered'" in sql:
            raise RuntimeError("confirmation lost after commit")
    database.execute = lose_confirmation
    try:
        with pytest.raises(RuntimeError, match="confirmation lost"):
            await outbox.process_once()
        assert outbox._delivery_receipts
        await outbox.process_once()
        assert outbox._delivery_receipts == {}
        assert channel.send.await_count == 1
    finally:
        await database.close()
