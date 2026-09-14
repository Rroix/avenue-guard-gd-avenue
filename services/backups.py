from __future__ import annotations

import json
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

CORE_TABLES = {
    "activity_counts",
    "tickets",
    "level_request_state",
    "level_request_submissions",
    "weekly_request_reviews",
    "runtime_settings",
    "workflow_events",
    "discord_outbox",
}


async def run_restore_drill(
    db, *, guild_id: int = 0, trigger: str = "scheduled"
) -> dict[str, object]:
    started = time.monotonic()
    timestamp = int(time.time())
    outcome = "passed"
    error = ""
    table_count = 0
    missing: list[str] = []
    with tempfile.TemporaryDirectory(prefix="avenue-guard-drill-") as directory:
        target = Path(directory) / "restore-drill.sqlite3"
        try:
            size_bytes = await db.backup_to(target)

            def _inspect() -> tuple[int, list[str]]:
                with closing(
                    sqlite3.connect(f"file:{target}?mode=ro", uri=True)
                ) as connection:
                    integrity = connection.execute("PRAGMA integrity_check").fetchone()
                    if not integrity or str(integrity[0]).casefold() != "ok":
                        raise ValueError(f"integrity check returned {integrity!r}")
                    rows = connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                    tables = {str(row[0]) for row in rows}
                    return len(tables), sorted(CORE_TABLES - tables)

            import asyncio

            table_count, missing = await asyncio.to_thread(_inspect)
            if missing:
                raise ValueError(f"missing core tables: {', '.join(missing)}")
        except Exception as exc:
            outcome = "failed"
            error = f"{type(exc).__name__}: {exc}"[:1000]
            size_bytes = int(target.stat().st_size) if target.exists() else 0

    duration_ms = int((time.monotonic() - started) * 1000)
    await db.execute(
        "INSERT INTO restore_drills(guild_id,drill_ts,status,duration_ms,size_bytes,table_count,missing_tables_json,error_text,trigger) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (
            guild_id,
            timestamp,
            outcome,
            duration_ms,
            size_bytes,
            table_count,
            json.dumps(missing, separators=(",", ":")),
            error,
            trigger,
        ),
    )
    return {
        "status": outcome,
        "duration_ms": duration_ms,
        "size_bytes": size_bytes,
        "table_count": table_count,
        "missing_tables": missing,
        "error": error,
    }
