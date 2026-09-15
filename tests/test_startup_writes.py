import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

import cogs.Release as release_module
import cogs.RequestLevels as request_module
from cogs.Release import ReleaseCog
from cogs.RequestLevels import RequestLevelsCog
from utils.db import Database, DatabaseBusyError
from utils.libsql_worker import IsolatedConnection
from utils.supervision import start_cog_background


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "startup.db"))
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


def release_cog(database):
    return ReleaseCog(SimpleNamespace(db=database, is_closed=lambda: False))


def cache_cog(database):
    cog = object.__new__(RequestLevelsCog)
    cog.bot = SimpleNamespace(db=database)
    cog._next_validation_cleanup = 0
    return cog


@pytest.mark.asyncio
async def test_deferred_initialization_preserves_startup_boundary_and_online_observations(db, monkeypatch):
    await db.execute("INSERT INTO bot_uptime_tracker VALUES(1,1000,1060,60,60)")
    clock = [1120]
    monkeypatch.setattr(release_module.time, "time", lambda: clock[0])
    cog = release_cog(db)
    async with db._guard():
        preview = await cog.record_uptime_sample(online=False, force=True)
        assert preview["percentage"] == 50.0
        assert cog._uptime_start_ts == 1120 and not cog._uptime_initialized
        clock[0] = 1180
        await cog.record_uptime_sample(online=True, force=True)
    clock[0] = 1240
    snapshot = await cog.record_uptime_sample(online=True, force=True)
    row = await db.fetchone("SELECT * FROM bot_uptime_tracker")
    assert row["tracking_started_ts"] == 1000
    assert row["observed_seconds"] == 240 and row["online_seconds"] == 180
    assert snapshot["percentage"] == 75.0
    assert not cog._uptime_pending


@pytest.mark.asyncio
async def test_busy_reconnect_checkpoints_keep_online_and_offline_intervals(db, monkeypatch):
    clock = [1000]
    monkeypatch.setattr(release_module.time, "time", lambda: clock[0])
    cog = release_cog(db)
    await cog._initialize_uptime_tracker()
    async with db._guard():
        clock[0] = 1080
        await cog.record_uptime_transition(was_online=True)
        clock[0] = 1140
        await cog.record_uptime_transition(was_online=False)
    clock[0] = 1200
    snapshot = await cog.record_uptime_sample(online=True, force=True)
    assert snapshot["observed_seconds"] == 200 and snapshot["online_seconds"] == 140
    assert snapshot["percentage"] == 70.0
    assert cog.uptime_persistence_snapshot()["pending_intervals"] == 0


@pytest.mark.asyncio
async def test_native_replica_uptime_checkpoints_are_idempotent(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "native-uptime.db"), remote_url="libsql://test.invalid")
    monkeypatch.setattr(database, "_open_connection_sync", lambda: IsolatedConnection(str(database.path)))
    monkeypatch.setattr(database, "_sync_remote_sync", lambda: None)
    clock = [1000]
    monkeypatch.setattr(release_module.time, "time", lambda: clock[0])
    try:
        await database.connect()
        cog = release_cog(database)
        await cog._initialize_uptime_tracker()
        clock[0] = 1060
        await cog.record_uptime_sample(online=True, force=True)
        await cog.record_uptime_sample(online=True, force=True)
        row = await database.fetchone("SELECT * FROM bot_uptime_tracker")
        assert row["observed_seconds"] == 60 and row["online_seconds"] == 60
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_cancelled_uptime_write_retains_pending_interval(db, monkeypatch):
    clock = [1000]
    monkeypatch.setattr(release_module.time, "time", lambda: clock[0])
    cog = release_cog(db)
    await cog._initialize_uptime_tracker()
    original = db.execute_transaction
    db.execute_transaction = AsyncMock(side_effect=asyncio.CancelledError)
    clock[0] = 1060
    with pytest.raises(asyncio.CancelledError):
        await cog.record_uptime_sample(online=True, force=True)
    assert cog._uptime_pending == [(1060, True)] and not cog._uptime_lock.locked()
    db.execute_transaction = original
    snapshot = await cog.record_uptime_sample(online=True, force=True)
    assert snapshot["observed_seconds"] == snapshot["online_seconds"] == 60


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["uptime.initialize", "uptime.checkpoint"])
async def test_unknown_uptime_commit_is_not_replayed_or_reclassified(db, monkeypatch, operation):
    await db.execute("INSERT INTO bot_uptime_tracker VALUES(1,1000,1060,60,60)")
    clock = [1120]
    monkeypatch.setattr(release_module.time, "time", lambda: clock[0])
    cog = release_cog(db)
    if operation == "uptime.checkpoint":
        await cog._initialize_uptime_tracker()
    original = db.execute_transaction
    async def uncertain(*args, **kwargs):
        await original(*args, **kwargs)
        if kwargs.get("operation_label") == operation:
            raise RuntimeError("completion unknown after commit")
    db.execute_transaction = uncertain
    clock[0] = 1180 if operation == "uptime.checkpoint" else 1120
    with pytest.raises(RuntimeError, match="completion unknown"):
        await cog.record_uptime_sample(online=True, force=True)
    db.execute_transaction = original
    clock[0] = 1240
    snapshot = await cog.record_uptime_sample(online=True, force=True)
    assert snapshot["observed_seconds"] == 240 and snapshot["online_seconds"] == 180
    assert cog._uptime_start_ts == 1120


@pytest.mark.asyncio
async def test_pure_uptime_snapshot_never_initializes_or_writes(db):
    await db.execute("INSERT INTO bot_uptime_tracker VALUES(1,1000,1060,60,60)")
    cog = release_cog(db)
    db.execute_transaction = AsyncMock()
    assert (await cog.uptime_snapshot(online=False))["tracking_started_ts"] == 1000
    db.execute_transaction.assert_not_awaited()
    assert not cog._uptime_initialized


@pytest.mark.asyncio
async def test_busy_uptime_snapshot_keeps_last_known_history(db):
    cog = release_cog(db)
    cog._uptime_last_row = {"tracking_started_ts": 1000, "last_heartbeat_ts": 1060,
                            "observed_seconds": 60, "online_seconds": 60}
    db.fetchone_local = AsyncMock(side_effect=DatabaseBusyError("replica reader busy"))
    snapshot = await cog.uptime_snapshot(online=False)
    assert snapshot["tracking_started_ts"] == 1000 and snapshot["online_seconds"] == 60


@pytest.mark.asyncio
async def test_uptime_observations_coalesce_same_state_and_bound_transaction_size(db, monkeypatch):
    clock = [1000]
    monkeypatch.setattr(release_module.time, "time", lambda: clock[0])
    cog = release_cog(db)
    await cog._initialize_uptime_tracker()
    for index in range(100):
        cog._queue_uptime_observation(1010 + index, True)
    assert cog._uptime_pending == [(1109, True)]
    cog._uptime_pending = [(1100 + index * 10, bool(index % 2)) for index in range(40)]
    clock[0] = 1490
    await cog.record_uptime_sample(online=True)
    assert len(cog._uptime_pending) == 8
    await cog.record_uptime_sample(online=True)
    assert not cog._uptime_pending


@pytest.mark.asyncio
async def test_release_tasks_start_before_slow_storage_and_do_not_duplicate(db):
    cog = release_cog(db)
    blocker = asyncio.Event()
    async def stalled():
        await blocker.wait()
    cog._metrics_loop = stalled
    cog._bootstrap_loop = stalled
    try:
        await asyncio.wait_for(cog.start_background(), 0.1)
        first = (cog._metrics_task, cog._bootstrap_task)
        await cog.start_background()
        assert first == (cog._metrics_task, cog._bootstrap_task)
        assert all(task is not None and not task.done() for task in first)
    finally:
        await cog.close_resources()


@pytest.mark.asyncio
async def test_release_bootstrap_retries_unstarted_uptime_write(db, monkeypatch):
    cog = release_cog(db)
    monkeypatch.setattr(release_module, "BOOTSTRAP_RETRY_SECONDS", 0.01)
    cog.refresh_public_release_cache = AsyncMock()
    cog._initialize_uptime_tracker = AsyncMock(side_effect=[DatabaseBusyError("not started"), None])
    cog.refresh_public_metrics = AsyncMock()
    cog._ensure_manifest_proposal = AsyncMock()
    logger = AsyncMock()
    monkeypatch.setattr(release_module, "log_error", logger)
    try:
        await cog.start_background()
        async with asyncio.timeout(1):
            while not cog._bootstrap_complete:
                await asyncio.sleep(0.01)
        assert cog._initialize_uptime_tracker.await_count == 2
        cog._ensure_manifest_proposal.assert_awaited_once()
        logger.assert_not_awaited()
    finally:
        await cog.close_resources()


@pytest.mark.asyncio
async def test_release_bootstrap_reports_real_failures_and_retries(db, monkeypatch):
    cog = release_cog(db)
    monkeypatch.setattr(release_module, "BOOTSTRAP_RETRY_SECONDS", 0.01)
    cog.refresh_public_release_cache = AsyncMock(side_effect=[ValueError("invalid JWT"), None])
    cog._initialize_uptime_tracker = AsyncMock()
    cog.refresh_public_metrics = AsyncMock()
    cog._ensure_manifest_proposal = AsyncMock()
    logger = AsyncMock()
    monkeypatch.setattr(release_module, "log_error", logger)
    await cog._bootstrap_loop()
    assert cog._bootstrap_complete
    logger.assert_awaited_once()
    assert "invalid JWT" in logger.call_args.args[1]


@pytest.mark.asyncio
async def test_cancelled_bootstrap_can_be_restarted_and_shutdown_joins_tasks(db):
    cog = release_cog(db)
    async def stalled():
        await asyncio.Event().wait()
    cog._bootstrap_loop = stalled
    cog._metrics_loop = stalled
    await cog.start_background()
    old_bootstrap = cog._bootstrap_task
    old_bootstrap.cancel()
    await asyncio.gather(old_bootstrap, return_exceptions=True)
    await cog.start_background()
    assert cog._bootstrap_task is not old_bootstrap
    tasks = [cog._bootstrap_task, cog._metrics_task]
    await cog.close_resources()
    assert all(task.done() for task in tasks)


@pytest.mark.asyncio
async def test_request_tasks_start_without_any_validation_cleanup_write(db):
    cog = cache_cog(db)
    cog._started = False
    cog._close_task = cog._scheduled_open_task = cog._validation_refresh_task = None
    async def stalled():
        await asyncio.Event().wait()
    cog._auto_close_loop = cog._scheduled_open_loop = cog._pending_validation_refresh_loop = stalled
    db.execute = AsyncMock()
    db.execute_transaction = AsyncMock()
    try:
        await asyncio.wait_for(cog.start_background(), 0.1)
        assert all(not task.done() for task in (cog._close_task, cog._scheduled_open_task, cog._validation_refresh_task))
        db.execute.assert_not_awaited()
        db.execute_transaction.assert_not_awaited()
    finally:
        tasks = [cog._close_task, cog._scheduled_open_task, cog._validation_refresh_task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_validation_cleanup_is_read_first_and_bounded(db):
    cog = cache_cog(db)
    await db.executemany("INSERT INTO gd_level_validation_cache VALUES(?,?,?,?)", [(str(index), 1, 2, '{}') for index in range(201)])
    await db.execute("INSERT INTO gd_level_validation_cache VALUES('fresh',1,9999999999,'{}')")
    await cog._cleanup_expired_validation_cache()
    assert len(await db.fetchall("SELECT * FROM gd_level_validation_cache")) == 2
    cog._next_validation_cleanup = 0
    await cog._cleanup_expired_validation_cache()
    assert [row["level_id"] for row in await db.fetchall("SELECT * FROM gd_level_validation_cache")] == ["fresh"]
    cog._next_validation_cleanup = 0
    db.execute_transaction = AsyncMock()
    await cog._cleanup_expired_validation_cache()
    db.execute_transaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_busy_validation_cleanup_defers_and_recovers_without_error_flood(db, monkeypatch):
    cog = cache_cog(db)
    logger = AsyncMock()
    monkeypatch.setattr(request_module, "log_error", logger)
    await db.execute("INSERT INTO gd_level_validation_cache VALUES('old',1,2,'{}')")
    async with db._guard():
        await asyncio.wait_for(cog._cleanup_expired_validation_cache(), 0.7)
    logger.assert_not_awaited()
    assert cog._next_validation_cleanup > time.monotonic()
    assert len(await db.fetchall("SELECT * FROM gd_level_validation_cache")) == 1
    cog._next_validation_cleanup = 0
    await cog._cleanup_expired_validation_cache()
    assert await db.fetchall("SELECT * FROM gd_level_validation_cache") == []


@pytest.mark.asyncio
async def test_validation_cleanup_reports_real_failure(db, monkeypatch):
    cog = cache_cog(db)
    logger = AsyncMock()
    monkeypatch.setattr(request_module, "log_error", logger)
    db.fetchone_local = AsyncMock(side_effect=ValueError("Hrana: S3 error"))
    await cog._cleanup_expired_validation_cache()
    logger.assert_awaited_once()
    assert "S3 error" in logger.call_args.args[1]


@pytest.mark.asyncio
async def test_named_transaction_still_updates_write_health_and_names_busy_holder(db):
    entered = threading.Event()
    released = threading.Event()
    def hold_connection():
        entered.set()
        released.wait(2)
    task = asyncio.create_task(db._run_locked_with_retry(hold_connection, operation_name="transaction", operation_label="test.checkpoint"), name="test-owner")
    try:
        while not entered.is_set():
            await asyncio.sleep(0.001)
        with pytest.raises(DatabaseBusyError, match="holder=test.checkpoint, task=test-owner"):
            async with db._guard(0.03, "test.contender"):
                pass
        assert db.health_snapshot()["active_operation_task"] == "test-owner"
    finally:
        released.set()
        await task
    db._primary_write_degraded = True
    await db.execute_transaction([("INSERT INTO runtime_settings VALUES('label','1',1)", ())], operation_label="test.write")
    assert not db.health_snapshot()["primary_write_degraded"]
    assert db.health_snapshot()["active_operation_task"] == ""


@pytest.mark.asyncio
async def test_startup_task_name_is_scoped_and_restored_on_failure():
    task = asyncio.current_task()
    name = task.get_name()
    async def start():
        assert asyncio.current_task().get_name() == "avenue-guard:startup:TestCog"
        raise RuntimeError("startup failure")
    bot = SimpleNamespace(get_cog=lambda _: SimpleNamespace(start_background=start))
    with pytest.raises(RuntimeError):
        await start_cog_background(bot, "TestCog")
    assert task.get_name() == name
