import asyncio
from contextlib import closing
import sqlite3

import pytest

import utils.db as db_module
from utils.db import Database


@pytest.mark.asyncio
async def test_empty_database_migrates_all_critical_tables_and_columns(tmp_path):
    db = Database(str(tmp_path / "bot.db"))
    await db.connect()

    rows = await db.fetchall("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {str(row["name"]) for row in rows}
    assert {
        "activity_counts",
        "tickets",
        "weekly_sessions",
        "level_request_state",
        "level_request_submissions",
        "weekly_request_reviews",
        "ban_info_requests",
        "daily_stats",
        "impact_snapshots",
        "runtime_settings",
        "bot_releases",
        "bot_uptime_tracker",
        "schema_metadata",
        "discord_outbox",
        "workflow_events",
        "error_incidents",
        "health_metrics",
        "permission_drift_events",
        "user_notification_preferences",
        "restore_drills",
        "monthly_impact_reports",
    } <= tables

    ticket_columns = {str(row["name"]) for row in await db.fetchall("PRAGMA table_info(tickets)")}
    assert {
        "opening_message_id",
        "closing_prompt_message_id",
        "satisfaction_message_id",
        "satisfaction_delivery_status",
        "satisfaction_delivery_error",
        "satisfaction_attempted_ts",
        "satisfaction_resolution_version",
    } <= ticket_columns

    transcript_request_columns = {
        str(row["name"])
        for row in await db.fetchall("PRAGMA table_info(transcript_requests)")
    }
    assert {"updated_ts", "reviewed_by", "reviewed_ts", "error_text"} <= transcript_request_columns

    request_columns = {
        str(row["name"]) for row in await db.fetchall("PRAGMA table_info(level_request_submissions)")
    }
    assert {"edit_deadline_ts", "correlation_id"} <= request_columns

    for table in ("tickets", "help_submissions", "weekly_request_reviews", "level_request_scheduled_openings"):
        columns = {str(row["name"]) for row in await db.fetchall(f"PRAGMA table_info({table})")}
        assert "correlation_id" in columns

    schema_rows = await db.fetchall("SELECT component,schema_version FROM schema_metadata")
    schema_versions = {str(row["component"]): int(row["schema_version"]) for row in schema_rows}
    assert schema_versions == {
        "database": 5,
        "config": 2,
        "runtime_settings": 2,
        "embed_templates": 2,
    }

    weekly_claim_columns = {
        str(row["name"]) for row in await db.fetchall("PRAGMA table_info(weekly_claims)")
    }
    assert {"offer_channel_id", "offer_message_id", "offer_expires_ts"} <= weekly_claim_columns

    weekly_reminder_columns = {
        str(row["name"])
        for row in await db.fetchall("PRAGMA table_info(weekly_reminders)")
    }
    assert {
        "delivery_status",
        "channel_id",
        "message_id",
        "attempted_ts",
        "error_text",
    } <= weekly_reminder_columns

    release_columns = {
        str(row["name"]) for row in await db.fetchall("PRAGMA table_info(bot_releases)")
    }
    assert {"approval_delivery_status", "approval_attempted_ts"} <= release_columns
    await db.close()


@pytest.mark.asyncio
async def test_bot_releases_persist_approval_and_public_notes(tmp_path):
    path = tmp_path / "releases.db"
    db = Database(str(path))
    await db.connect()
    release_id = await db.execute_insert(
        """
        INSERT INTO bot_releases(
            version,title,summary,changes_json,status,source,
            created_by,created_ts,decided_by,decided_ts
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "3.2.1",
            "Reliable support",
            "Support delivery hardening",
            '["Fixed retry handling"]',
            "approved",
            "test",
            110,
            1000,
            110,
            1100,
        ),
    )
    await db.close()

    reopened = Database(str(path))
    await reopened.connect()
    row = await reopened.fetchone(
        "SELECT * FROM bot_releases WHERE id=?",
        (release_id,),
    )
    assert row["version"] == "3.2.1"
    assert row["status"] == "approved"
    assert row["changes_json"] == '["Fixed retry handling"]'
    assert row["decided_by"] == 110
    await reopened.close()


@pytest.mark.asyncio
async def test_existing_transcript_requests_gain_audit_columns_without_data_loss(tmp_path):
    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            "CREATE TABLE transcript_requests("
            "guild_id INTEGER NOT NULL, request_message_id INTEGER PRIMARY KEY, "
            "ticket_channel_id INTEGER NOT NULL, requester_id INTEGER NOT NULL, "
            "status TEXT NOT NULL, created_ts INTEGER NOT NULL, ticket_id INTEGER"
            ")"
        )
        conn.execute(
            "INSERT INTO transcript_requests VALUES(?,?,?,?,?,?,?)",
            (717, 555, 999, 42, "pending", 1234, 3),
        )
        conn.commit()

    db = Database(str(path))
    await db.connect()
    row = await db.fetchone(
        "SELECT status, created_ts, updated_ts, reviewed_by, reviewed_ts, error_text "
        "FROM transcript_requests WHERE request_message_id=555"
    )

    assert row["status"] == "pending"
    assert row["created_ts"] == 1234
    assert row["updated_ts"] == 1234
    assert row["reviewed_by"] is None
    assert row["reviewed_ts"] is None
    assert row["error_text"] is None
    await db.close()


@pytest.mark.asyncio
async def test_transaction_rolls_back_every_statement_on_failure(tmp_path):
    db = Database(str(tmp_path / "bot.db"))
    await db.connect()

    with pytest.raises(sqlite3.OperationalError):
        await db.execute_transaction(
            (
                (
                    "INSERT INTO runtime_settings(setting_key,value_json,updated_ts) VALUES(?,?,?)",
                    ("must_rollback", '"value"', 1),
                ),
                ("INSERT INTO table_that_does_not_exist(value) VALUES(?)", (1,)),
            )
        )

    assert await db.fetchone(
        "SELECT 1 FROM runtime_settings WHERE setting_key=?",
        ("must_rollback",),
    ) is None
    await db.close()


@pytest.mark.asyncio
async def test_execute_affected_returns_the_atomic_row_count(tmp_path):
    db = Database(str(tmp_path / "affected.db"))
    await db.connect()
    await db.set_runtime_setting("one", {"value": 1})
    await db.set_runtime_setting("two", {"value": 2})

    changed = await db.execute_affected(
        "DELETE FROM runtime_settings WHERE setting_key IN (?,?)",
        ("one", "two"),
    )

    assert changed == 2
    assert await db.fetchone("SELECT 1 FROM runtime_settings LIMIT 1") is None
    await db.close()


@pytest.mark.asyncio
async def test_ticket_ids_are_unique_under_concurrency(tmp_path):
    db = Database(str(tmp_path / "bot.db"))
    await db.connect()

    ids = await asyncio.gather(*(db.next_ticket_id(717) for _ in range(25)))

    assert sorted(ids) == list(range(1, 26))
    assert len(set(ids)) == 25
    await db.close()


@pytest.mark.asyncio
async def test_backup_is_transactionally_valid_and_contains_latest_write(tmp_path):
    db = Database(str(tmp_path / "bot.db"))
    await db.connect()
    await db.set_runtime_setting("audit.test", {"ok": True})

    backup = tmp_path / "backup.sqlite3"
    size = await db.backup_to(backup)

    assert size > 0
    with closing(sqlite3.connect(backup)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        payload = conn.execute(
            "SELECT value_json FROM runtime_settings WHERE setting_key='audit.test'"
        ).fetchone()[0]
    assert payload == '{"ok":true}'
    await db.close()


@pytest.mark.asyncio
async def test_local_interaction_read_skips_pending_remote_sync(tmp_path):
    db = Database(str(tmp_path / "bot.db"))
    await db.connect()
    await db.set_runtime_setting("modal.test", {"ready": True})

    def remote_sync_must_not_run():
        raise AssertionError("modal-critical local reads must not wait on remote sync")

    db._try_pending_remote_sync_sync = remote_sync_must_not_run
    row = await db.fetchone_local(
        "SELECT value_json FROM runtime_settings WHERE setting_key=?",
        ("modal.test",),
    )

    assert row["value_json"] == '{"ready":true}'
    await db.close()


def test_remote_commit_does_not_force_a_replica_pull(tmp_path):
    class Connection:
        sync_calls = 0

        def commit(self):
            return None

        def sync(self):
            self.sync_calls += 1
            raise AssertionError("a normal commit must not perform a replica pull")

    db = Database(str(tmp_path / "replica.db"))
    db.uses_remote = True
    db._conn = Connection()
    db._commit_and_sync_sync()

    assert db._conn.sync_calls == 0
    assert db._remote_dirty is False


def test_pending_sync_reopens_invalid_connection_before_backoff(tmp_path):
    db = Database(str(tmp_path / "replica.db"))
    db.uses_remote = True
    db._remote_dirty = True
    db._remote_reconnect_required = True
    db._remote_sync_retry_after = float("inf")
    reopened = []

    def reopen():
        reopened.append(True)
        db._remote_reconnect_required = False

    db._reopen_connection_sync = reopen
    db._try_pending_remote_sync_sync()

    assert reopened == [True]
    assert db._remote_dirty is True


@pytest.mark.asyncio
async def test_remote_parameter_adapter_preserves_64_bit_discord_ids(tmp_path):
    path = tmp_path / "snowflakes.db"
    db = Database(str(path))
    db.uses_remote = True
    db._conn = db_module.libsql.connect(str(path))
    db._ready = True
    await db.execute(
        "CREATE TABLE sticky_state("
        "guild_id INTEGER NOT NULL,channel_id INTEGER NOT NULL,"
        "last_sticky_message_id INTEGER,PRIMARY KEY(guild_id,channel_id))"
    )

    guild_id = 717003826288394271
    channel_id = 1480304268346130552
    message_id = 1548671314074673201
    await db.execute(
        "INSERT INTO sticky_state(guild_id,channel_id,last_sticky_message_id) VALUES(?,?,?)",
        (guild_id, channel_id, message_id),
    )

    row = await db.fetchone(
        "SELECT guild_id,channel_id,last_sticky_message_id FROM sticky_state "
        "WHERE guild_id=? AND channel_id=?",
        (guild_id, channel_id),
    )
    assert int(row["guild_id"]) == guild_id
    assert int(row["channel_id"]) == channel_id
    assert int(row["last_sticky_message_id"]) == message_id
    await db.close()


@pytest.mark.asyncio
async def test_remote_compatibility_lookup_repairs_legacy_rounded_ids(tmp_path):
    db = Database(str(tmp_path / "legacy-snowflakes.db"))
    await db.connect()

    guild_id = 717003826288394271
    channel_id = 1480304268346130553
    message_id = 1548671314074673201
    rounded_guild = int(float(guild_id))
    rounded_channel = int(float(channel_id))
    rounded_message = int(float(message_id))
    assert (rounded_guild, rounded_channel, rounded_message) != (
        guild_id,
        channel_id,
        message_id,
    )
    await db.execute(
        "INSERT INTO sticky_state(guild_id,channel_id,last_sticky_message_id) VALUES(?,?,?)",
        (rounded_guild, rounded_channel, rounded_message),
    )
    db.uses_remote = True

    legacy_row = await db.fetchone(
        "SELECT guild_id,channel_id,last_sticky_message_id FROM sticky_state "
        "WHERE guild_id=? AND channel_id=?",
        (guild_id, channel_id),
    )
    assert int(legacy_row["guild_id"]) == rounded_guild

    await db.execute(
        "UPDATE sticky_state SET guild_id=?,channel_id=?,last_sticky_message_id=? "
        "WHERE guild_id=? AND channel_id=?",
        (guild_id, channel_id, message_id, guild_id, channel_id),
    )
    repaired = await db.fetchone(
        "SELECT guild_id,channel_id,last_sticky_message_id FROM sticky_state "
        "WHERE guild_id=? AND channel_id=?",
        (guild_id, channel_id),
    )
    assert int(repaired["guild_id"]) == guild_id
    assert int(repaired["channel_id"]) == channel_id
    assert int(repaired["last_sticky_message_id"]) == message_id
    await db.close()


@pytest.mark.asyncio
async def test_startup_repair_recovers_known_users_channels_and_feedback(tmp_path):
    db = Database(str(tmp_path / "startup-repair.db"))
    await db.connect()
    guild_id = 717003826288394271
    channel_id = 1480304268346130553
    user_id = 1115678273079349288
    await db.execute(
        "INSERT INTO tickets("
        "guild_id,channel_id,creator_id,created_ts,last_user_activity_ts,status,"
        "ticket_id,closed_ts,satisfaction_delivery_status,satisfaction_delivery_error"
        ") VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            int(float(guild_id)),
            int(float(channel_id)),
            int(float(user_id)),
            1,
            1,
            "closed",
            6,
            2,
            "recipient_unavailable",
            "legacy rounded identity",
        ),
    )
    db.uses_remote = True

    result = await db.repair_legacy_snowflake_precision(
        guild_ids=(guild_id,),
        user_ids=(user_id,),
        channel_ids=(channel_id,),
    )
    row = await db.fetchone(
        "SELECT guild_id,channel_id,creator_id,satisfaction_delivery_status,"
        "satisfaction_delivery_error FROM tickets WHERE guild_id=? AND ticket_id=6",
        (guild_id,),
    )

    assert result["updated"] >= 3
    assert result["feedback_requeued"] == 1
    assert int(row["guild_id"]) == guild_id
    assert int(row["channel_id"]) == channel_id
    assert int(row["creator_id"]) == user_id
    assert row["satisfaction_delivery_status"] == "pending"
    assert row["satisfaction_delivery_error"] is None
    await db.close()


@pytest.mark.asyncio
async def test_startup_repair_skips_float_bucket_with_two_real_discord_ids(tmp_path):
    db = Database(str(tmp_path / "ambiguous-snowflakes.db"))
    await db.connect()
    guild_id = 717003826288394271
    rounded_user_id = 1129311352628989952
    neighboring_user_id = rounded_user_id + 31
    assert int(float(neighboring_user_id)) == rounded_user_id
    await db.execute(
        "INSERT INTO tickets("
        "guild_id,channel_id,creator_id,created_ts,last_user_activity_ts,status,ticket_id"
        ") VALUES(?,?,?,?,?,?,?)",
        (guild_id, 999, rounded_user_id, 1, 1, "closed", 6),
    )
    db.uses_remote = True

    result = await db.repair_legacy_snowflake_precision(
        guild_ids=(guild_id,),
        user_ids=(rounded_user_id, neighboring_user_id),
    )
    row = await db.fetchone(
        "SELECT creator_id FROM tickets WHERE guild_id=? AND ticket_id=6",
        (guild_id,),
    )

    assert result["ambiguous"] >= 1
    assert int(row["creator_id"]) == rounded_user_id
    await db.close()


@pytest.mark.asyncio
async def test_corrupt_remote_replica_is_quarantined_and_rebuilt(tmp_path, monkeypatch):
    path = tmp_path / "replica.db"
    path.write_bytes(b"this is not a SQLite database")

    def connect(database, **_kwargs):
        conn = sqlite3.connect(database, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    class TestConnection:
        def __new__(cls, database, **kwargs):
            return connect(database, **kwargs)

    monkeypatch.setattr(db_module, "IsolatedConnection", TestConnection)
    db = Database(
        str(path),
        remote_url="libsql://example.invalid",
        auth_token="database-token",
    )
    await db.connect()

    assert db.health_snapshot()["replica_rebuild_count"] == 1
    assert list(tmp_path.glob(".replica.db.corrupt-*"))
    assert await db.fetchone(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tickets'"
    ) is not None
    await db.close()
