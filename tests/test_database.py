import asyncio
import sqlite3

import pytest

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
        "daily_stats",
        "impact_snapshots",
        "runtime_settings",
    } <= tables

    ticket_columns = {str(row["name"]) for row in await db.fetchall("PRAGMA table_info(tickets)")}
    assert {"opening_message_id", "closing_prompt_message_id", "satisfaction_message_id"} <= ticket_columns

    request_columns = {
        str(row["name"]) for row in await db.fetchall("PRAGMA table_info(level_request_submissions)")
    }
    assert "edit_deadline_ts" in request_columns
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
    with sqlite3.connect(backup) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        payload = conn.execute(
            "SELECT value_json FROM runtime_settings WHERE setting_key='audit.test'"
        ).fetchone()[0]
    assert payload == '{"ok":true}'
    await db.close()
