import asyncio
import json
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
import pytest_asyncio

import cogs.RequestLevels as request_module
import cogs.Help as help_module
import utils.db as database_module
from cogs.Help import HelpCog
from cogs.Operations import OperationsCog
from cogs.RequestLevels import RequestLevelsCog
from cogs.Tracking import TrackingCog
from utils.db import Database, DatabaseBusyError
from utils.libsql_worker import IsolatedConnection
from utils.outbox import DiscordOutbox
from utils.keepalive import get_runtime_health, get_public_bot_payload, set_keepalive_status, set_runtime_heartbeat
from utils.timeutils import now_madrid, week_start_sunday


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "contention.db"))
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


def tracking_cog(database):
    cog = object.__new__(TrackingCog)
    cog.bot = SimpleNamespace(db=database)
    cog._activity_lock = asyncio.Lock()
    cog._activity_flush_lock = asyncio.Lock()
    cog._activity_retry_batch = None
    cog._pending_activity_counts = {}
    cog._pending_last_counted = {}
    cog._last_counted_cache = {}
    cog._log_background_error = AsyncMock()
    return cog


@pytest.mark.asyncio
async def test_remote_reads_and_atomic_batches_work_with_native_driver_behind_busy_writer(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "native.db"), remote_url="libsql://test.invalid")
    monkeypatch.setattr(database, "_open_connection_sync", lambda: IsolatedConnection(str(database.path)))
    monkeypatch.setattr(database, "_sync_remote_sync", lambda: None)
    try:
        await database.connect()
        user_id = 1102884420207255653
        await database.apply_activity_batch("native-batch", [(717, user_id, "week", 4)], [(717, user_id, 100)])
        async with database._guard():
            row = await asyncio.wait_for(database.fetchone("SELECT user_id,count FROM activity_counts WHERE user_id=?", (user_id,)), 0.5)
            rows = await asyncio.wait_for(database.fetchall("SELECT * FROM activity_counts"), 0.5)
            assert int(row["user_id"]) == user_id and row["count"] == 4
            assert len(rows) == 1
        await database.apply_activity_batch("native-batch", [(717, user_id, "week", 4)], [(717, user_id, 100)])
        assert (await database.fetchone("SELECT count FROM activity_counts"))["count"] == 4
        assert database.health_snapshot()["queue_timeouts"] == 0
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_executemany_rolls_back_whole_batch(db):
    await db.execute("CREATE TABLE batch_test(value INTEGER CHECK(value>0))")
    with pytest.raises(sqlite3.IntegrityError):
        await db.executemany("INSERT INTO batch_test VALUES(?)", [(1,), (-1,)])
    assert await db.fetchall("SELECT * FROM batch_test") == []


@pytest.mark.asyncio
async def test_activity_receipt_counters_and_cooldowns_restore_from_backup(db, tmp_path):
    counts = [(717, 88, "week", 3), (717, 99, "week", 5)]
    await db.apply_activity_batch("receipt-a", counts, [(717, 88, 200)])
    await db.apply_activity_batch("receipt-a", counts, [(717, 88, 200)])
    await db.apply_activity_batch("receipt-b", [(717, 88, "week", 2)], [(717, 88, 100)])
    assert (await db.fetchone("SELECT count FROM activity_counts WHERE user_id=88"))["count"] == 5
    assert (await db.fetchone("SELECT last_counted_ts FROM activity_last_counted"))["last_counted_ts"] == 200
    backup = tmp_path / "restore.sqlite3"
    await db.backup_to(backup)
    restored = Database(str(backup))
    try:
        await restored.connect()
        await restored.apply_activity_batch("receipt-a", counts, [(717, 88, 200)])
        assert sum(row["count"] for row in await restored.fetchall("SELECT count FROM activity_counts")) == 10
        assert len(await restored.fetchall("SELECT * FROM activity_flush_batches")) == 2
    finally:
        await restored.close()


@pytest.mark.asyncio
async def test_activity_batch_failure_does_not_persist_partial_counts_or_receipt(db):
    await db.execute("DROP TABLE activity_last_counted")
    with pytest.raises(sqlite3.OperationalError):
        await db.apply_activity_batch("failed", [(717, 88, "week", 3)], [(717, 88, 100)])
    assert await db.fetchall("SELECT * FROM activity_counts") == []
    assert await db.fetchall("SELECT * FROM activity_flush_batches") == []


@pytest.mark.asyncio
async def test_activity_batch_short_queue_deadline_does_not_start_write(db):
    async with db._guard():
        with pytest.raises(DatabaseBusyError, match="not started"):
            await db.apply_activity_batch("busy", [(717, 88, "week", 3)], [])
    assert await db.fetchall("SELECT * FROM activity_counts") == []


@pytest.mark.asyncio
async def test_replica_query_timeout_is_bounded_and_returns_reader_slot(db):
    with pytest.raises(DatabaseBusyError, match="Replica read deadline"):
        await db._read_replica("WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<100000000) SELECT sum(x) FROM n", (), many=False, timeout=0.03)
    await asyncio.sleep(0.05)
    assert (await db.fetchone_local("SELECT 1 AS ok"))["ok"] == 1
    assert db._read_waiting == 0
    assert db._read_slots._value == 4


@pytest.mark.asyncio
async def test_replica_reader_cannot_mutate_state(db):
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        await db.fetchone_local("DELETE FROM runtime_settings RETURNING setting_key")


@pytest.mark.asyncio
async def test_cancelled_replica_read_keeps_slot_until_thread_exits(db, monkeypatch):
    original = database_module.sqlite3.connect
    import threading
    entered = threading.Event()
    released = threading.Event()
    def slow_open(*args, **kwargs):
        entered.set()
        released.wait(2)
        return original(*args, **kwargs)
    monkeypatch.setattr(database_module.sqlite3, "connect", slow_open)
    task = asyncio.create_task(db.fetchone_local("SELECT 1"))
    while not entered.is_set():
        await asyncio.sleep(0.001)
    task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await task
        assert db._read_slots._value == 3
    finally:
        released.set()
        async with asyncio.timeout(1):
            while db._read_slots._value != 4:
                await asyncio.sleep(0.001)


@pytest.mark.asyncio
async def test_schema_five_upgrade_is_additive_and_preserves_counts(db, tmp_path):
    await db.execute("INSERT INTO activity_counts VALUES(717,88,'week',9)")
    await db.execute("DROP TABLE activity_flush_batches")
    await db.execute("UPDATE schema_metadata SET schema_version=5 WHERE component='database'")
    backup = tmp_path / "v5.sqlite3"
    await db.backup_to(backup)
    restored = Database(str(backup))
    try:
        await restored.connect()
        assert (await restored.fetchone("SELECT count FROM activity_counts"))["count"] == 9
        assert (await restored.fetchone("SELECT schema_version FROM schema_metadata WHERE component='database'"))["schema_version"] == 11
        assert await restored.fetchall("SELECT * FROM activity_flush_batches") == []
    finally:
        await restored.close()


@pytest.mark.asyncio
async def test_schema_probe_failure_is_not_misread_as_empty_database(db):
    class FailingConnection:
        def __init__(self):
            self.statements = []

        def execute(self, sql):
            self.statements.append(sql)
            raise ValueError("Hrana: S3 error, primary unavailable")
    original = db._conn
    connection = FailingConnection()
    db._conn = connection
    try:
        with pytest.raises(ValueError, match="S3 error"):
            db._migrate_sync()
        assert len(connection.statements) == 1
    finally:
        db._conn = original


@pytest.mark.asyncio
async def test_newer_schema_cannot_be_silently_downgraded(db):
    await db.execute("UPDATE schema_metadata SET schema_version=999 WHERE component='database'")
    with pytest.raises(RuntimeError, match="newer than this bot"):
        db._migrate_sync()
    assert (await db.fetchone("SELECT schema_version FROM schema_metadata WHERE component='database'"))["schema_version"] == 999


@pytest.mark.asyncio
async def test_tracking_chunks_large_flush_and_drains_shutdown(db):
    cog = tracking_cog(db)
    cog._pending_activity_counts = {(717, user_id, "week"): 1 for user_id in range(260)}
    cog._pending_last_counted = {(717, user_id): 100 for user_id in range(260)}
    await cog.flush_activity_counts()
    assert len(await db.fetchall("SELECT * FROM activity_counts")) == 200
    assert len(cog._pending_activity_counts) == 60
    await cog.flush_activity_counts(drain=True)
    assert len(await db.fetchall("SELECT * FROM activity_counts")) == 260
    assert len(await db.fetchall("SELECT * FROM activity_last_counted")) == 260
    assert len(await db.fetchall("SELECT * FROM activity_flush_batches")) == 6
    assert not cog._pending_activity_counts and not cog._pending_last_counted


@pytest.mark.asyncio
async def test_tracking_unknown_commit_uses_same_receipt_and_separates_new_activity(db):
    cog = tracking_cog(db)
    cog._pending_activity_counts = {(717, 88, "week"): 3}
    original = db.apply_activity_batch
    calls = []
    async def uncertain(batch_id, counts, last_seen):
        calls.append(batch_id)
        await original(batch_id, counts, last_seen)
        if len(calls) == 1:
            raise RuntimeError("confirmation lost after commit")
    db.apply_activity_batch = uncertain
    await cog.flush_activity_counts()
    assert cog._activity_retry_batch is not None
    cog._pending_activity_counts[(717, 88, "week")] = 2
    await cog.flush_activity_counts()
    assert calls[0] == calls[1] and calls[2] != calls[0]
    assert (await db.fetchone("SELECT count FROM activity_counts"))["count"] == 5
    assert cog._activity_retry_batch is None


@pytest.mark.asyncio
async def test_tracking_reset_does_not_recount_other_guild_after_unknown_commit(db):
    cog = tracking_cog(db)
    week = week_start_sunday(now_madrid()).isoformat()
    counts = [(717, 88, week, 3), (818, 99, week, 5)]
    await db.apply_activity_batch("reset-receipt", counts, [])
    cog._activity_retry_batch = ("reset-receipt", counts, [])
    await cog._reset_activity(717)
    await cog.flush_activity_counts()
    rows = await db.fetchall("SELECT guild_id,count FROM activity_counts")
    assert [(row["guild_id"], row["count"]) for row in rows] == [(818, 5)]


@pytest.mark.asyncio
async def test_tracking_cancelled_flush_retains_retry_batch(db):
    cog = tracking_cog(db)
    cog._pending_activity_counts = {(717, 88, "week"): 3}
    db.apply_activity_batch = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await cog.flush_activity_counts()
    assert cog._activity_retry_batch[1] == [(717, 88, "week", 3)]
    assert not cog._activity_flush_lock.locked()


@pytest.mark.asyncio
async def test_weekly_winners_wait_for_activity_persistence(db):
    cog = tracking_cog(db)
    cog.bot.get_guild = lambda _: SimpleNamespace(id=717)
    cog._cfg_int = lambda *args: 717
    cog._pending_activity_counts = {(717, 88, "week"): 3}
    cog._ranked_rows_for_week = AsyncMock()
    db.apply_activity_batch = AsyncMock(side_effect=DatabaseBusyError("busy"))
    with pytest.raises(DatabaseBusyError, match="no winners were selected"):
        await cog.run_weekly_job("week")
    cog._ranked_rows_for_week.assert_not_awaited()
    assert cog._activity_retry_batch is not None


@pytest.mark.asyncio
async def test_idle_outbox_recovery_does_not_write(db):
    original = db.execute_affected
    db.execute_affected = AsyncMock(wraps=original)
    outbox = DiscordOutbox(SimpleNamespace(db=db))
    assert await outbox.recover_stale() == 0
    db.execute_affected.assert_not_awaited()
    await outbox.enqueue("send_channel", idempotency_key="stale")
    await db.execute("UPDATE discord_outbox SET status='processing',updated_ts=0")
    assert await outbox.recover_stale() == 1
    assert db.execute_affected.await_count == 1


@pytest.mark.asyncio
async def test_health_sampler_yields_to_writer_and_keeps_live_sample(db):
    cog = object.__new__(OperationsCog)
    cog.bot = SimpleNamespace(db=db, config=SimpleNamespace(get_int=lambda *args, **kwargs: 717),
                              get_cog=lambda name: None, latency=0.01)
    cog.task_snapshot = lambda: {}
    cog._last_health_sample = {}
    cog._deferred_health_samples = 0
    async with db._guard():
        payload = await asyncio.wait_for(cog.collect_health_sample(), 0.8)
        assert payload["db_ok"] is True
        assert payload["persisted"] is False and payload["deferred_samples"] == 1
        assert cog._last_health_sample is payload
    payload = await cog.collect_health_sample()
    assert payload["persisted"] is True
    assert len(await db.fetchall("SELECT * FROM health_metrics")) == 1
    assert db.health_snapshot()["background_queue_deferrals"] == 1
    assert db.health_snapshot()["last_queue_timeout_ts"] == 0


@pytest.mark.asyncio
async def test_uninitialized_replica_is_not_reported_healthy(db):
    cog = object.__new__(OperationsCog)
    cog.bot = SimpleNamespace(db=db, config=SimpleNamespace(get_int=lambda *args, **kwargs: 717),
                              get_cog=lambda name: None, latency=0.01)
    cog.task_snapshot = lambda: {}
    cog._deferred_health_samples = 0
    db.fetchone_local = AsyncMock(return_value=None)
    assert (await cog.collect_health_sample())["db_ok"] is False


def test_slow_write_queue_marks_public_service_degraded_without_claiming_gateway_is_offline(monkeypatch):
    import utils.keepalive as keepalive
    monkeypatch.setattr(keepalive, "_status", dict(keepalive._status))
    monkeypatch.setattr(keepalive, "_runtime_health", {})
    monkeypatch.setattr(keepalive, "_runtime_heartbeat", 0)
    set_keepalive_status("online")
    health = {"connected": True, "uses_remote": True, "worker_alive": True, "write_queue_stalled": True}
    set_runtime_heartbeat(lag_ms=0, tasks={}, database=health, incidents=[])
    assert get_runtime_health()["responsive"] is True
    assert get_runtime_health()["ready"] is False
    assert get_public_bot_payload()["status"] == "Degraded"
    health["write_queue_stalled"] = False
    set_runtime_heartbeat(lag_ms=0, tasks={}, database=health, incidents=[])
    assert get_runtime_health()["ready"] is True


@pytest.mark.asyncio
async def test_ticket_cache_single_flight_and_failure_backoff():
    cog = object.__new__(HelpCog)
    cog._ticket_cache_load_lock = asyncio.Lock()
    cog._ticket_cache_ready = False
    cog._ticket_cache_retry_after = 0.0
    started = asyncio.Event()
    release = asyncio.Event()
    async def fail_load():
        started.set()
        await release.wait()
        raise DatabaseBusyError("busy")
    cog._load_active_ticket_channels_once = AsyncMock(side_effect=fail_load)
    first = asyncio.create_task(cog._load_active_ticket_channels())
    await started.wait()
    await asyncio.gather(*(cog._load_active_ticket_channels() for _ in range(10)))
    release.set()
    with pytest.raises(DatabaseBusyError):
        await first
    await cog._load_active_ticket_channels()
    assert cog._load_active_ticket_channels_once.await_count == 1
    cog._ticket_cache_retry_after = 0
    cog._load_active_ticket_channels_once = AsyncMock()
    await cog._load_active_ticket_channels()
    cog._load_active_ticket_channels_once.assert_awaited_once()


@pytest.mark.asyncio
async def test_ticket_cache_partial_load_retries_transient_channel_failure(monkeypatch):
    class Channel:
        def __init__(self, channel_id):
            self.id = channel_id
    monkeypatch.setattr(help_module.discord, "TextChannel", Channel)
    cog = object.__new__(HelpCog)
    cog.bot = SimpleNamespace(config=SimpleNamespace(get_int=lambda *args: 717),
                              db=SimpleNamespace(fetchall=AsyncMock(return_value=[{"channel_id": 1}, {"channel_id": 2}])),
                              get_guild=lambda _: SimpleNamespace(id=717))
    cog._active_ticket_channels = set()
    cog._ticket_cache_ready = False
    cog._ticket_cache_load_lock = asyncio.Lock()
    cog._ticket_cache_retry_after = 0
    cog._log_background_error = AsyncMock()
    cog._ticket_channel_from_stored_id = AsyncMock(side_effect=[Channel(1), RuntimeError("temporary network failure")])
    await cog._load_active_ticket_channels()
    assert cog._active_ticket_channels == {1}
    assert not cog._ticket_cache_ready and cog._ticket_cache_retry_after > time.monotonic()
    cog._ticket_cache_retry_after = 0
    cog._ticket_channel_from_stored_id = AsyncMock(side_effect=[Channel(1), Channel(2)])
    await cog._load_active_ticket_channels()
    assert cog._active_ticket_channels == {1, 2} and cog._ticket_cache_ready


@pytest.mark.asyncio
async def test_ticket_cache_does_not_overwrite_concurrent_new_ticket(monkeypatch):
    class Channel:
        id = 1
    monkeypatch.setattr(help_module.discord, "TextChannel", Channel)
    cog = object.__new__(HelpCog)
    cog.bot = SimpleNamespace(config=SimpleNamespace(get_int=lambda *args: 717),
                              db=SimpleNamespace(fetchall=AsyncMock(return_value=[{"channel_id": 1}])),
                              get_guild=lambda _: SimpleNamespace(id=717))
    cog._active_ticket_channels = set()
    async def resolve(*args):
        cog._active_ticket_channels.add(999)
        return Channel()
    cog._ticket_channel_from_stored_id = resolve
    await cog._load_active_ticket_channels_once()
    assert cog._active_ticket_channels == {1, 999}


async def request_cog(database):
    await database.execute("INSERT INTO level_request_submissions(guild_id,wave_id,user_id,level_id,request_message_id,data_json,status,created_ts) VALUES(?,?,?,?,?,?,?,?)",
                           (717, 1, 88, "123456789", 555, '{"level_id":"123456789"}', "pending", 1))
    cog = object.__new__(RequestLevelsCog)
    channel = SimpleNamespace(id=333, send=AsyncMock(), fetch_message=AsyncMock())
    message = SimpleNamespace(id=666, channel=channel, edit=AsyncMock(), delete=AsyncMock())
    channel.send.return_value = message
    channel.fetch_message.return_value = message
    cog.bot = SimpleNamespace(db=database, user=SimpleNamespace(id=42), get_guild=lambda _: SimpleNamespace(id=717))
    cog._review_lock = asyncio.Lock()
    cog._validation_card_lock = asyncio.Lock()
    cog._validation_refresh_receipts = {}
    cog._review_target_channel = AsyncMock(return_value=channel)
    cog._lookup_level_validation = AsyncMock(return_value={"exists": True, "checked_ts": 10, "expires_ts": 20})
    cog._cfg = lambda *args, **kwargs: {}
    cog._color_name = lambda *args: discord.Color.blurple()
    cog._data_vars = lambda row, data: {**data, "message_id": row["request_message_id"]}
    cog._weekly_data_vars = cog._data_vars
    cog._embed_from_template = lambda template, variables, **kwargs: discord.Embed(title=str(variables["message_id"]))
    return cog, channel, message


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [True, False])
async def test_validation_missing_or_legacy_card_commits_exact_id_and_guarded_edit(db, monkeypatch, missing):
    cog, channel, message = await request_cog(db)
    resolver = AsyncMock(return_value=(None, False) if missing else (message, True))
    monkeypatch.setattr(request_module, "fetch_persisted_message", resolver)
    row = await db.fetchone("SELECT * FROM level_request_submissions")
    assert await cog._refresh_review_validation(SimpleNamespace(id=717), "wave", row)
    assert channel.send.await_count == int(missing)
    assert (await db.fetchone("SELECT request_message_id FROM level_request_submissions"))["request_message_id"] == 666
    job = await db.fetchone("SELECT * FROM discord_outbox")
    assert job["channel_id"] == 333 and job["message_id"] == 666
    assert json.loads(job["payload_json"])["embed"]["title"] == "666"
    if missing:
        assert channel.send.call_args.kwargs["enforce_nonce"] is True
    message.delete.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [True, False])
async def test_validation_unknown_confirmation_does_not_duplicate_or_delete_canonical_card(db, monkeypatch, committed):
    cog, channel, message = await request_cog(db)
    monkeypatch.setattr(request_module, "fetch_persisted_message", AsyncMock(return_value=(None, False)))
    original = db.execute_transaction
    async def fail_confirmation(*args, **kwargs):
        if committed:
            await original(*args, **kwargs)
        raise RuntimeError("storage confirmation lost")
    db.execute_transaction = fail_confirmation
    row = await db.fetchone("SELECT * FROM level_request_submissions")
    with pytest.raises(RuntimeError):
        await cog._refresh_review_validation(SimpleNamespace(id=717), "wave", row)
    assert cog._validation_refresh_receipts
    db.execute_transaction = original
    assert await cog._refresh_review_validation(SimpleNamespace(id=717), "wave", row)
    assert channel.send.await_count == 1
    message.delete.assert_not_awaited()
    assert not cog._validation_refresh_receipts


@pytest.mark.asyncio
async def test_validation_never_recreates_card_on_permission_or_transport_failure(db, monkeypatch):
    cog, channel, _message = await request_cog(db)
    monkeypatch.setattr(request_module, "fetch_persisted_message", AsyncMock(side_effect=RuntimeError("HTTP 403")))
    with pytest.raises(RuntimeError, match="HTTP 403"):
        await cog._refresh_review_validation(SimpleNamespace(id=717), "wave", await db.fetchone("SELECT * FROM level_request_submissions"))
    channel.send.assert_not_awaited()
    assert await db.fetchall("SELECT * FROM discord_outbox") == []


@pytest.mark.asyncio
async def test_validation_committed_card_reviewed_before_retry_is_not_deleted(db, monkeypatch):
    cog, _channel, message = await request_cog(db)
    row = await db.fetchone("SELECT * FROM level_request_submissions")
    await db.execute("UPDATE level_request_submissions SET request_message_id=666,status='reviewed'")
    cog._validation_refresh_receipts[("wave", 555)] = message
    monkeypatch.setattr(request_module, "fetch_persisted_message", AsyncMock(side_effect=AssertionError("receipt should be used")))
    assert await cog._refresh_review_validation(SimpleNamespace(id=717), "wave", row) == {}
    message.delete.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalidate", ["review", "edit", "none"])
async def test_queued_validation_edit_retries_without_reopening_reviewed_or_overwriting_edited_card(db, monkeypatch, invalidate):
    cog, channel, message = await request_cog(db)
    monkeypatch.setattr(request_module, "fetch_persisted_message", AsyncMock(return_value=(None, False)))
    monkeypatch.setattr(request_module, "log_error", AsyncMock())
    message.edit.side_effect = RuntimeError("temporary Discord failure")
    assert await cog._refresh_review_validation(SimpleNamespace(id=717), "wave", await db.fetchone("SELECT * FROM level_request_submissions"))
    message.edit.reset_mock(side_effect=True)
    if invalidate == "review":
        await db.execute("UPDATE level_request_submissions SET status='reviewed'")
    elif invalidate == "edit":
        await db.execute("UPDATE level_request_submissions SET data_json='{}'")
    bot = SimpleNamespace(db=db, get_cog=lambda _: cog, get_channel=lambda _: channel)
    outbox = DiscordOutbox(bot)
    assert (await outbox.process_once())["delivered"] == 1
    assert message.edit.await_count == int(invalidate == "none")
    if invalidate == "none":
        assert all(not child.disabled for child in message.edit.call_args.kwargs["view"].children)
