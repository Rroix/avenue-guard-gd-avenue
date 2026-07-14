from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence

try:
    import libsql
except Exception:
    libsql = None


class DictRow(dict):
    """sqlite3.Row-like fallback for drivers that return tuples."""

    def __init__(self, keys: Sequence[str], values: Sequence[Any]):
        super().__init__((str(key), values[index] if index < len(values) else None) for index, key in enumerate(keys))
        self._values = tuple(values)

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


def _row_get(row: Any, key: str, *, index: int = 0, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        return row[key]
    except Exception:
        pass
    try:
        return row[index]
    except Exception:
        return default


def _normalize_row(cursor: Any, row: Any) -> Any:
    if row is None:
        return None
    try:
        _ = row["__avenue_guard_missing_column__"]
    except KeyError:
        return row
    except Exception:
        pass
    description = getattr(cursor, "description", None) or []
    keys = [str(col[0]) for col in description if col]
    if keys:
        return DictRow(keys, tuple(row))
    return row


def _normalize_rows(cursor: Any, rows: Iterable[Any] | None) -> list[Any]:
    return [_normalize_row(cursor, row) for row in (rows or [])]


def _fetchall(cursor: Any) -> list[Any]:
    return list(cursor.fetchall() or [])


def _jwt_payload(token: str) -> dict[str, Any]:
    parts = str(token or "").strip().split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode((payload + padding).encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _token_scope_names(payload: dict[str, Any]) -> set[str]:
    scopes = payload.get("scopes")
    if isinstance(scopes, dict):
        scopes = scopes.get("scopes")
    if isinstance(scopes, str):
        return {scopes}
    if isinstance(scopes, list):
        return {str(scope) for scope in scopes}
    return set()


def _looks_like_turso_platform_token(token: str) -> bool:
    payload = _jwt_payload(token)
    scopes = _token_scope_names(payload)
    platform_scopes = {
        "db:create",
        "db:delete",
        "db:configure",
        "db:mint-token",
        "group:configure",
        "group:mint-token",
    }
    return bool(scopes & platform_scopes)


def _is_recoverable_remote_error(exc: Exception) -> bool:
    text = repr(exc).casefold()
    markers = (
        "connection has reached an invalid state",
        "started with txn",
        "stream error",
        "s3 error",
        "internalservererror",
        "sqlite_unknown",
        "failed to list objects in s3 storage",
        "hrana",
        "status=500",
        "http 500",
        "temporarily unavailable",
        "service unavailable",
        "connection reset",
        "timed out",
    )
    return any(marker in text for marker in markers)


class Database:
    """Small SQLite wrapper safe to use from an async bot.

    - Uses a single connection opened with check_same_thread=False
    - Serializes all operations with an asyncio.Lock
    - Executes each query fully inside one to_thread call to avoid cursor/thread mismatches
    """

    def __init__(self, path: str, *, remote_url: str = "", auth_token: str = ""):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.remote_url = str(remote_url or "").strip()
        self.auth_token = str(auth_token or "").strip()
        self.uses_remote = bool(self.remote_url)
        self._lock = asyncio.Lock()
        self._conn: Optional[Any] = None
        self._ready = False
        self._remote_dirty = False
        self._remote_sync_retry_after = 0.0
        self._last_remote_sync_error = ""
        self._last_remote_sync_error_ts = 0

    def _close_connection_sync(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.close()
        except Exception:
            pass
        self._conn = None
        self._ready = False

    def _reopen_connection_sync(self) -> None:
        self._close_connection_sync()
        self._conn = self._open_connection_sync()
        self._ready = True

    def _open_connection_sync(self) -> Any:
        if self.uses_remote:
            if libsql is None:
                raise RuntimeError("TURSO_DATABASE_URL is configured, but the libsql Python package is not installed.")
            if _looks_like_turso_platform_token(self.auth_token):
                raise RuntimeError(
                    "TURSO_AUTH_TOKEN looks like a Turso platform/API token, not a database auth token. "
                    "Create a database token with `turso db tokens create <database-name>` and use that value instead."
                )
            conn = libsql.connect(str(self.path), sync_url=self.remote_url, auth_token=self.auth_token)
        else:
            conn = sqlite3.connect(str(self.path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
        try:
            conn.execute("PRAGMA foreign_keys=ON;")
        except Exception:
            pass
        for pragma in ("PRAGMA busy_timeout=5000;", "PRAGMA synchronous=NORMAL;"):
            try:
                conn.execute(pragma)
            except Exception:
                pass
        conn.commit()
        return conn

    def _sync_remote_sync(self) -> None:
        if not self.uses_remote or self._conn is None:
            return
        sync = getattr(self._conn, "sync", None)
        if callable(sync):
            sync()

    def _sync_remote_with_retry_sync(self) -> None:
        if not self.uses_remote:
            return

        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                self._sync_remote_sync()
                return
            except Exception as exc:
                last_error = exc
                if not _is_recoverable_remote_error(exc) or attempt >= 2:
                    raise
                time.sleep(0.25 * (attempt + 1))
        if last_error is not None:
            raise last_error

    def _try_pending_remote_sync_sync(self) -> None:
        """Retry a previously deferred remote sync without blocking every query."""
        if not self.uses_remote or not self._remote_dirty:
            return
        if time.monotonic() < self._remote_sync_retry_after:
            return
        try:
            self._sync_remote_with_retry_sync()
        except Exception as exc:
            self._last_remote_sync_error = f"{type(exc).__name__}: {exc}"[:1000]
            self._last_remote_sync_error_ts = int(time.time())
            retry_delay = 5.0 if _is_recoverable_remote_error(exc) else 60.0
            self._remote_sync_retry_after = time.monotonic() + retry_delay
            return
        self._remote_dirty = False
        self._remote_sync_retry_after = 0.0
        self._last_remote_sync_error = ""
        self._last_remote_sync_error_ts = 0

    def _commit_and_sync_sync(self) -> None:
        assert self._conn is not None
        self._conn.commit()
        try:
            self._sync_remote_with_retry_sync()
        except Exception as exc:
            # The local commit already succeeded. A replication failure must
            # not make callers re-run a non-idempotent write such as an
            # activity increment. Initial connection still performs a strict
            # sync, so invalid credentials prevent startup rather than silently
            # running as local-only storage.
            if self.uses_remote:
                self._remote_dirty = True
                self._last_remote_sync_error = f"{type(exc).__name__}: {exc}"[:1000]
                self._last_remote_sync_error_ts = int(time.time())
                retry_delay = 5.0 if _is_recoverable_remote_error(exc) else 60.0
                self._remote_sync_retry_after = time.monotonic() + retry_delay
                return
            raise
        self._remote_dirty = False
        self._remote_sync_retry_after = 0.0
        self._last_remote_sync_error = ""
        self._last_remote_sync_error_ts = 0

    async def _run_locked_with_retry(self, operation, *, retry_operation: bool = True) -> Any:
        await self.connect()
        attempts = 3 if self.uses_remote and retry_operation else 1
        last_error: Optional[Exception] = None

        for attempt in range(attempts):
            async with self._lock:
                assert self._conn is not None

                try:
                    await asyncio.to_thread(self._try_pending_remote_sync_sync)
                    return await asyncio.to_thread(operation)
                except Exception as exc:
                    last_error = exc
                    should_recover = self.uses_remote and _is_recoverable_remote_error(exc)
                    if should_recover:
                        await asyncio.to_thread(self._reopen_connection_sync)
                    if not should_recover or attempt >= attempts - 1:
                        raise

            await asyncio.sleep(0.35 * (attempt + 1))

        if last_error is not None:
            raise last_error
        return None

    async def connect(self) -> None:
        async with self._lock:
            if self._conn is not None and self._ready:
                return

            def _connect_and_migrate():
                if self._conn is None:
                    self._conn = self._open_connection_sync()

                assert self._conn is not None
                self._migrate_sync()
                self._sync_remote_with_retry_sync()

            await asyncio.to_thread(_connect_and_migrate)
            self._ready = True

    async def close(self) -> None:
        async with self._lock:
            if self._conn is None:
                return

            await asyncio.to_thread(self._close_connection_sync)

    async def backup_to(self, target_path: str | Path) -> int:
        await self.connect()
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        async with self._lock:
            assert self._conn is not None

            def _backup() -> int:
                assert self._conn is not None
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass
                if self.uses_remote:
                    # A backup of the transactionally consistent local replica
                    # is still valuable during a temporary Turso outage. Try
                    # replication first, but do not make remote availability a
                    # prerequisite for producing a recovery file.
                    self._remote_dirty = True
                    self._try_pending_remote_sync_sync()
                    if not self.path.exists():
                        raise RuntimeError("The local Turso replica file does not exist yet, so no backup file can be created.")
                    # The embedded replica can have live WAL state. SQLite's
                    # backup API produces a transactionally consistent file;
                    # copying only the main file can omit committed pages.
                    source_uri = f"file:{self.path}?mode=ro"
                    with sqlite3.connect(source_uri, uri=True) as source, sqlite3.connect(str(target)) as dest:
                        source.backup(dest)
                else:
                    self._conn.execute("PRAGMA wal_checkpoint(FULL);")
                    with sqlite3.connect(str(target)) as dest:
                        self._conn.backup(dest)

                with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as check:
                    row = check.execute("PRAGMA integrity_check;").fetchone()
                    result = str(row[0] if row else "")
                    if result.casefold() != "ok":
                        raise sqlite3.DatabaseError(f"backup integrity_check returned {result!r}")
                return int(target.stat().st_size)

            return await asyncio.to_thread(_backup)

    async def restore_from(self, source_path: str | Path) -> int:
        """Replace the live SQLite file with a validated backup and migrate it.

        The uploaded database is copied to a temporary file in the target DB
        directory, migrated there first, and only then atomically swapped into
        place. That keeps the current database intact if migration fails.
        """
        source = Path(source_path)
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(str(source))
        if self.uses_remote:
            raise RuntimeError(
                "SQLite upload restore is only supported for local SQLite storage. "
                "For Turso/libSQL, restore through Turso backups/import tooling so the remote primary stays consistent."
            )

        async with self._lock:
            def _unlink_sidecars(base: Path) -> None:
                for suffix in ("-wal", "-shm", "-journal"):
                    try:
                        (base.parent / f"{base.name}{suffix}").unlink()
                    except FileNotFoundError:
                        pass

            def _connect_current() -> None:
                conn = sqlite3.connect(str(self.path), check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA foreign_keys=ON;")
                conn.commit()
                self._conn = conn
                self._migrate_sync()
                self._ready = True

            def _restore() -> int:
                if self._conn is not None:
                    try:
                        self._conn.execute("PRAGMA wal_checkpoint(FULL);")
                        self._conn.commit()
                    finally:
                        self._conn.close()
                self._conn = None
                self._ready = False

                self.path.parent.mkdir(parents=True, exist_ok=True)
                stamp = f"{int(time.time())}-{os.getpid()}"
                tmp_target = self.path.parent / f".{self.path.name}.restore-{stamp}.tmp"
                _unlink_sidecars(tmp_target)
                shutil.copy2(source, tmp_target)

                try:
                    temp_conn = sqlite3.connect(str(tmp_target), check_same_thread=False)
                    temp_conn.row_factory = sqlite3.Row
                    temp_conn.execute("PRAGMA foreign_keys=ON;")
                    temp_conn.execute("PRAGMA journal_mode=WAL;")
                    temp_conn.commit()
                    self._conn = temp_conn
                    self._migrate_sync()
                    integrity_row = temp_conn.execute("PRAGMA integrity_check;").fetchone()
                    integrity = str(integrity_row[0] if integrity_row else "")
                    if integrity.casefold() != "ok":
                        raise sqlite3.DatabaseError(f"integrity_check returned {integrity!r}")
                    temp_conn.execute("PRAGMA wal_checkpoint(FULL);")
                    temp_conn.commit()
                    temp_conn.close()
                    self._conn = None
                    self._ready = False

                    _unlink_sidecars(self.path)
                    os.replace(tmp_target, self.path)
                    _unlink_sidecars(tmp_target)
                    _connect_current()
                    return int(self.path.stat().st_size)
                except Exception:
                    try:
                        if self._conn is not None:
                            self._conn.close()
                    except Exception:
                        pass
                    self._conn = None
                    self._ready = False
                    try:
                        tmp_target.unlink()
                    except FileNotFoundError:
                        pass
                    _unlink_sidecars(tmp_target)
                    if self.path.exists():
                        _connect_current()
                    raise

            return await asyncio.to_thread(_restore)

    def _migrate_sync(self) -> None:
        assert self._conn is not None
        stmts = [
            """CREATE TABLE IF NOT EXISTS activity_counts(
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                week_start TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, week_start)
            );""",
            """CREATE TABLE IF NOT EXISTS activity_last_counted(
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                last_counted_ts INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );""",
            """CREATE TABLE IF NOT EXISTS weekly_claims(
                guild_id INTEGER NOT NULL,
                week_start TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                rank INTEGER NOT NULL,
                status TEXT NOT NULL,
                contacted_ts INTEGER NOT NULL,
                PRIMARY KEY (guild_id, week_start, user_id)
            );""",
            """CREATE TABLE IF NOT EXISTS weekly_sessions(
                guild_id INTEGER NOT NULL,
                week_start TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                stage TEXT NOT NULL,
                expires_ts INTEGER NOT NULL,
                decline_prompt_message_id INTEGER,
                active INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (guild_id, week_start, user_id)
            );""",
            """CREATE TABLE IF NOT EXISTS weekly_dm_log(
                guild_id INTEGER NOT NULL,
                week_start TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                event TEXT NOT NULL,
                detail TEXT NOT NULL,
                ts INTEGER NOT NULL
            );""",
            """CREATE TABLE IF NOT EXISTS weekly_reminders(
                guild_id INTEGER NOT NULL,
                week_start TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                reminded_ts INTEGER NOT NULL,
                PRIMARY KEY (guild_id, week_start, user_id)
            );""",
            """CREATE TABLE IF NOT EXISTS weekly_runs(
                guild_id INTEGER NOT NULL,
                week_start TEXT NOT NULL,
                ran_ts INTEGER NOT NULL,
                PRIMARY KEY (guild_id, week_start)
            );""",
            """CREATE TABLE IF NOT EXISTS weekly_reward_disabled(
                guild_id INTEGER NOT NULL,
                week_start TEXT NOT NULL,
                disabled_ts INTEGER NOT NULL,
                disabled_by INTEGER NOT NULL,
                PRIMARY KEY (guild_id, week_start)
            );""",
            """CREATE TABLE IF NOT EXISTS weekly_streaks(
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                streak INTEGER NOT NULL DEFAULT 0,
                best_streak INTEGER NOT NULL DEFAULT 0,
                last_week_start TEXT,
                updated_ts INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );""",
            """CREATE TABLE IF NOT EXISTS weekly_recaps(
                guild_id INTEGER NOT NULL,
                week_start TEXT NOT NULL,
                message_id INTEGER,
                channel_id INTEGER,
                created_ts INTEGER NOT NULL,
                PRIMARY KEY (guild_id, week_start)
            );""",
            """CREATE TABLE IF NOT EXISTS anti_farm_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                channel_id INTEGER,
                reason TEXT NOT NULL,
                sample TEXT NOT NULL DEFAULT '',
                ts INTEGER NOT NULL
            );""",
            """CREATE TABLE IF NOT EXISTS weekly_request_reviews(
                guild_id INTEGER NOT NULL,
                request_message_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                week_start TEXT NOT NULL,
                rank INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                result TEXT,
                review_text TEXT,
                reviewed_by INTEGER,
                reviewed_ts INTEGER,
                edit_deadline_ts INTEGER,
                created_ts INTEGER NOT NULL,
                data_json TEXT NOT NULL DEFAULT '{}'
            );""",
            """CREATE TABLE IF NOT EXISTS tickets(
                guild_id INTEGER NOT NULL,
                channel_id INTEGER PRIMARY KEY,
                creator_id INTEGER NOT NULL,
                created_ts INTEGER NOT NULL,
                last_user_activity_ts INTEGER NOT NULL,
                status TEXT NOT NULL,
                ticket_id INTEGER,
                status_tag TEXT NOT NULL DEFAULT 'waiting_staff',
                closed_ts INTEGER,
                satisfaction_score INTEGER,
                satisfaction_comment TEXT,
                satisfaction_user_id INTEGER,
                satisfaction_ts INTEGER,
                satisfaction_message_id INTEGER,
                closing_prompt_message_id INTEGER,
                opening_message_id INTEGER
            );""",
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_ticket_id
                ON tickets(guild_id, ticket_id) WHERE ticket_id IS NOT NULL;""",
            """CREATE TABLE IF NOT EXISTS ticket_sequences(
                guild_id INTEGER PRIMARY KEY,
                next_ticket_id INTEGER NOT NULL
            );""",
            """CREATE TABLE IF NOT EXISTS ticket_transcripts(
                guild_id INTEGER NOT NULL,
                ticket_id INTEGER NOT NULL,
                log_channel_id INTEGER NOT NULL,
                log_message_id INTEGER NOT NULL,
                created_ts INTEGER NOT NULL,
                PRIMARY KEY (guild_id, ticket_id)
            );""",
            """CREATE TABLE IF NOT EXISTS ticket_cooldowns(
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                last_created_ts INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );""",
            """CREATE TABLE IF NOT EXISTS sticky_state(
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                last_sticky_message_id INTEGER,
                PRIMARY KEY (guild_id, channel_id)
            );""",
            """CREATE TABLE IF NOT EXISTS help_sessions(
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                stage TEXT NOT NULL,
                created_ts INTEGER NOT NULL,
                data_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (guild_id, user_id)
            );""",
            """CREATE TABLE IF NOT EXISTS help_cooldowns(
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                last_used_ts INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id, action)
            );""",
            """CREATE TABLE IF NOT EXISTS help_submissions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_ts INTEGER NOT NULL,
                updated_ts INTEGER NOT NULL,
                log_channel_id INTEGER,
                log_message_id INTEGER,
                data_json TEXT NOT NULL DEFAULT '{}',
                response_text TEXT,
                responded_by INTEGER,
                responded_ts INTEGER
            );""",
            """CREATE TABLE IF NOT EXISTS transcript_requests(
                guild_id INTEGER NOT NULL,
                request_message_id INTEGER PRIMARY KEY,
                ticket_channel_id INTEGER NOT NULL,
                requester_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_ts INTEGER NOT NULL,
                ticket_id INTEGER
            );""",
            """CREATE TABLE IF NOT EXISTS rps_streaks(
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                streak INTEGER NOT NULL,
                updated_ts INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );""",
            """CREATE TABLE IF NOT EXISTS level_request_state(
                guild_id INTEGER PRIMARY KEY,
                state TEXT NOT NULL DEFAULT 'closed',
                wave_id INTEGER NOT NULL DEFAULT 0,
                request_limit INTEGER,
                close_ts INTEGER,
                submitted_count INTEGER NOT NULL DEFAULT 0,
                opened_ts INTEGER,
                closed_ts INTEGER,
                request_channel_id INTEGER,
                request_message_id INTEGER,
                request_type TEXT
            );""",
            """CREATE TABLE IF NOT EXISTS level_request_submissions(
                guild_id INTEGER NOT NULL,
                wave_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                level_id TEXT NOT NULL,
                request_message_id INTEGER UNIQUE,
                status TEXT NOT NULL,
                result TEXT,
                review_text TEXT,
                reviewed_by INTEGER,
                reviewed_ts INTEGER,
                created_ts INTEGER NOT NULL,
                data_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (guild_id, wave_id, user_id),
                UNIQUE (guild_id, wave_id, level_id)
            );""",
            """CREATE TABLE IF NOT EXISTS level_request_wave_summaries(
                guild_id INTEGER NOT NULL,
                wave_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                created_ts INTEGER NOT NULL,
                updated_ts INTEGER NOT NULL,
                PRIMARY KEY (guild_id, wave_id)
            );""",
            """CREATE TABLE IF NOT EXISTS level_request_scheduled_openings(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                request_limit INTEGER,
                close_minutes INTEGER,
                open_ts INTEGER NOT NULL,
                request_type TEXT,
                open_message TEXT,
                created_by INTEGER NOT NULL,
                created_ts INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                opened_wave_id INTEGER
            );""",
            """CREATE TABLE IF NOT EXISTS level_request_edit_audit(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                wave_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                request_message_id INTEGER,
                old_level_id TEXT,
                new_level_id TEXT,
                old_data_json TEXT NOT NULL DEFAULT '{}',
                new_data_json TEXT NOT NULL DEFAULT '{}',
                edited_ts INTEGER NOT NULL
            );""",
            """CREATE TABLE IF NOT EXISTS gd_level_validation_cache(
                level_id TEXT PRIMARY KEY,
                checked_ts INTEGER NOT NULL,
                expires_ts INTEGER NOT NULL,
                data_json TEXT NOT NULL DEFAULT '{}'
            );""",
            """CREATE TABLE IF NOT EXISTS daily_stats(
                guild_id INTEGER NOT NULL,
                day_key TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_ts INTEGER NOT NULL,
                PRIMARY KEY (guild_id, day_key)
            );""",
            """CREATE TABLE IF NOT EXISTS daily_summary_reports(
                guild_id INTEGER NOT NULL,
                day_key TEXT NOT NULL,
                channel_id INTEGER,
                message_id INTEGER,
                sent_ts INTEGER NOT NULL,
                PRIMARY KEY (guild_id, day_key)
            );""",
            """CREATE TABLE IF NOT EXISTS impact_snapshots(
                guild_id INTEGER NOT NULL,
                snapshot_ts INTEGER NOT NULL,
                report_channel_id INTEGER,
                report_message_id INTEGER,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (guild_id, snapshot_ts)
            );""",
            """CREATE TABLE IF NOT EXISTS database_backups(
                guild_id INTEGER NOT NULL,
                backup_ts INTEGER NOT NULL,
                channel_id INTEGER,
                message_id INTEGER,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT '',
                requested_by INTEGER,
                filename TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (guild_id, backup_ts)
            );""",
            """CREATE TABLE IF NOT EXISTS database_restore_log(
                guild_id INTEGER NOT NULL,
                restore_ts INTEGER NOT NULL,
                uploaded_by INTEGER NOT NULL,
                source_filename TEXT NOT NULL DEFAULT '',
                size_bytes INTEGER NOT NULL DEFAULT 0,
                pre_restore_backup_channel_id INTEGER,
                pre_restore_backup_message_id INTEGER,
                pre_restore_backup_filename TEXT NOT NULL DEFAULT '',
                tables_count INTEGER NOT NULL DEFAULT 0,
                known_tables_json TEXT NOT NULL DEFAULT '[]',
                PRIMARY KEY (guild_id, restore_ts)
            );""",
            """CREATE TABLE IF NOT EXISTS runtime_settings(
                setting_key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_ts INTEGER NOT NULL
            );""",
            """CREATE INDEX IF NOT EXISTS idx_activity_counts_week_count
                ON activity_counts(guild_id, week_start, count DESC);""",
            """CREATE INDEX IF NOT EXISTS idx_weekly_sessions_active_expiry
                ON weekly_sessions(guild_id, active, expires_ts);""",
            """CREATE INDEX IF NOT EXISTS idx_weekly_claims_status
                ON weekly_claims(guild_id, week_start, status);""",
            """CREATE INDEX IF NOT EXISTS idx_tickets_status_activity
                ON tickets(guild_id, status, last_user_activity_ts);""",
            """CREATE INDEX IF NOT EXISTS idx_level_request_submissions_status
                ON level_request_submissions(guild_id, status, wave_id);""",
            """CREATE INDEX IF NOT EXISTS idx_level_request_scheduled_openings_pending
                ON level_request_scheduled_openings(guild_id, status, open_ts);""",
            """CREATE INDEX IF NOT EXISTS idx_level_request_edit_audit_lookup
                ON level_request_edit_audit(guild_id, wave_id, user_id, edited_ts DESC);""",
            """CREATE INDEX IF NOT EXISTS idx_level_request_edit_audit_message
                ON level_request_edit_audit(guild_id, request_message_id);""",
            """CREATE INDEX IF NOT EXISTS idx_gd_level_validation_cache_expiry
                ON gd_level_validation_cache(expires_ts);""",
            """CREATE INDEX IF NOT EXISTS idx_weekly_request_reviews_status
                ON weekly_request_reviews(guild_id, status, week_start);""",
            """CREATE INDEX IF NOT EXISTS idx_weekly_streaks
                ON weekly_streaks(guild_id, streak DESC, best_streak DESC);""",
            """CREATE INDEX IF NOT EXISTS idx_anti_farm_events_lookup
                ON anti_farm_events(guild_id, user_id, ts DESC);""",
            """CREATE INDEX IF NOT EXISTS idx_transcript_requests_ticket_status
                ON transcript_requests(guild_id, ticket_id, status);""",
            """CREATE INDEX IF NOT EXISTS idx_help_submissions_user_status
                ON help_submissions(guild_id, user_id, status, created_ts DESC);""",
            """CREATE INDEX IF NOT EXISTS idx_help_submissions_log_message
                ON help_submissions(guild_id, log_channel_id, log_message_id);""",
            """CREATE INDEX IF NOT EXISTS idx_impact_snapshots_guild
                ON impact_snapshots(guild_id, snapshot_ts DESC);""",
            """CREATE INDEX IF NOT EXISTS idx_database_backups_guild
                ON database_backups(guild_id, backup_ts DESC);""",
            """CREATE INDEX IF NOT EXISTS idx_database_restore_log_guild
                ON database_restore_log(guild_id, restore_ts DESC);""",
        ]
        index_stmts = [
            stmt
            for stmt in stmts
            if stmt.lstrip().upper().startswith(("CREATE INDEX", "CREATE UNIQUE INDEX"))
        ]
        table_stmts = [stmt for stmt in stmts if stmt not in index_stmts]
        for stmt in table_stmts:
            self._conn.execute(stmt)

        self._ensure_column_sync("tickets", "ticket_id", "INTEGER")
        self._ensure_column_sync("tickets", "status_tag", "TEXT NOT NULL DEFAULT 'waiting_staff'")
        self._ensure_column_sync("tickets", "closed_ts", "INTEGER")
        self._ensure_column_sync("tickets", "satisfaction_score", "INTEGER")
        self._ensure_column_sync("tickets", "satisfaction_comment", "TEXT")
        self._ensure_column_sync("tickets", "satisfaction_user_id", "INTEGER")
        self._ensure_column_sync("tickets", "satisfaction_ts", "INTEGER")
        self._ensure_column_sync("tickets", "satisfaction_message_id", "INTEGER")
        self._ensure_column_sync("tickets", "closing_prompt_message_id", "INTEGER")
        self._ensure_column_sync("tickets", "opening_message_id", "INTEGER")
        self._ensure_column_sync("weekly_sessions", "decline_prompt_message_id", "INTEGER")
        self._ensure_column_sync("transcript_requests", "ticket_id", "INTEGER")
        self._ensure_column_sync("level_request_state", "request_channel_id", "INTEGER")
        self._ensure_column_sync("level_request_state", "request_message_id", "INTEGER")
        self._ensure_column_sync("level_request_state", "request_type", "TEXT")
        self._ensure_column_sync("level_request_submissions", "request_message_id", "INTEGER")
        self._ensure_column_sync("level_request_submissions", "result", "TEXT")
        self._ensure_column_sync("level_request_submissions", "review_text", "TEXT")
        self._ensure_column_sync("level_request_submissions", "reviewed_by", "INTEGER")
        self._ensure_column_sync("level_request_submissions", "reviewed_ts", "INTEGER")
        self._ensure_column_sync("level_request_submissions", "edit_deadline_ts", "INTEGER")
        self._ensure_column_sync("weekly_request_reviews", "channel_id", "INTEGER")
        self._ensure_column_sync("weekly_request_reviews", "rank", "INTEGER")
        self._ensure_column_sync("weekly_request_reviews", "result", "TEXT")
        self._ensure_column_sync("weekly_request_reviews", "review_text", "TEXT")
        self._ensure_column_sync("weekly_request_reviews", "reviewed_by", "INTEGER")
        self._ensure_column_sync("weekly_request_reviews", "reviewed_ts", "INTEGER")
        self._ensure_column_sync("weekly_request_reviews", "data_json", "TEXT NOT NULL DEFAULT '{}'")
        self._ensure_column_sync("help_submissions", "response_text", "TEXT")
        self._ensure_column_sync("help_submissions", "responded_by", "INTEGER")
        self._ensure_column_sync("help_submissions", "responded_ts", "INTEGER")
        self._ensure_column_sync("level_request_wave_summaries", "channel_id", "INTEGER")
        self._ensure_column_sync("level_request_wave_summaries", "message_id", "INTEGER")
        self._ensure_column_sync("level_request_wave_summaries", "created_ts", "INTEGER")
        self._ensure_column_sync("level_request_wave_summaries", "updated_ts", "INTEGER")
        self._ensure_column_sync("level_request_scheduled_openings", "opened_wave_id", "INTEGER")
        self._ensure_column_sync("level_request_scheduled_openings", "request_type", "TEXT")
        self._ensure_column_sync("level_request_scheduled_openings", "open_message", "TEXT")
        self._normalize_weekly_dm_log_sync()
        self._init_ticket_sequences_sync()
        for stmt in index_stmts:
            self._conn.execute(stmt)
        self._commit_and_sync_sync()

    def _ensure_column_sync(self, table: str, column: str, coltype: str) -> None:
        assert self._conn is not None
        info = _fetchall(self._conn.execute(f"PRAGMA table_info({table})"))
        cols = {_row_get(r, "name", index=1) for r in info}
        if column in cols:
            return
        self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")

    def _normalize_weekly_dm_log_sync(self) -> None:
        assert self._conn is not None
        info = _fetchall(self._conn.execute("PRAGMA table_info(weekly_dm_log)"))
        cols = {_row_get(r, "name", index=1) for r in info}
        if "event" in cols and "action" not in cols:
            return

        event_expr = "''"
        if "event" in cols and "action" in cols:
            event_expr = "COALESCE(event, action, '')"
        elif "event" in cols:
            event_expr = "COALESCE(event, '')"
        elif "action" in cols:
            event_expr = "COALESCE(action, '')"

        self._conn.execute("DROP TABLE IF EXISTS weekly_dm_log_new")
        self._conn.execute(
            """CREATE TABLE weekly_dm_log_new(
                guild_id INTEGER NOT NULL,
                week_start TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                event TEXT NOT NULL,
                detail TEXT NOT NULL,
                ts INTEGER NOT NULL
            );"""
        )
        # event_expr is chosen solely from the fixed migration expressions above.
        self._conn.execute(
            "INSERT INTO weekly_dm_log_new(guild_id, week_start, user_id, event, detail, ts) "  # nosec
            f"SELECT guild_id, week_start, user_id, {event_expr}, COALESCE(detail, ''), ts FROM weekly_dm_log"
        )
        self._conn.execute("DROP TABLE weekly_dm_log")
        self._conn.execute("ALTER TABLE weekly_dm_log_new RENAME TO weekly_dm_log")

    def _init_ticket_sequences_sync(self) -> None:
        assert self._conn is not None
        for gid_row in _fetchall(self._conn.execute("SELECT DISTINCT guild_id FROM tickets")):
            gid = int(_row_get(gid_row, "guild_id", index=0, default=0) or 0)
            max_row = self._conn.execute(
                "SELECT MAX(ticket_id) AS m FROM tickets WHERE guild_id=?",
                (gid,),
            ).fetchone()
            max_value = _row_get(max_row, "m", index=0)
            max_id = int(max_value) if max_value is not None else 0
            cur2 = self._conn.execute("SELECT next_ticket_id FROM ticket_sequences WHERE guild_id=?", (gid,))
            sequence_row = cur2.fetchone()
            if sequence_row is None:
                self._conn.execute(
                    "INSERT INTO ticket_sequences(guild_id, next_ticket_id) VALUES(?,?)",
                    (gid, max_id + 1 if max_id > 0 else 1),
                )
            else:
                current_next = int(_row_get(sequence_row, "next_ticket_id", index=0, default=1) or 1)
                if current_next <= max_id:
                    self._conn.execute(
                        "UPDATE ticket_sequences SET next_ticket_id=? WHERE guild_id=?",
                        (max_id + 1, gid),
                    )

    async def next_ticket_id(self, guild_id: int) -> int:
        def _run():
            assert self._conn is not None
            cur = self._conn.execute("SELECT next_ticket_id FROM ticket_sequences WHERE guild_id=?", (guild_id,))
            row = cur.fetchone()
            if row is None:
                next_id = 1
                self._conn.execute("INSERT INTO ticket_sequences(guild_id, next_ticket_id) VALUES(?,?)", (guild_id, 2))
                self._commit_and_sync_sync()
                return next_id
            next_id = int(_row_get(row, "next_ticket_id", index=0, default=1) or 1)
            self._conn.execute("UPDATE ticket_sequences SET next_ticket_id=? WHERE guild_id=?", (next_id + 1, guild_id))
            self._commit_and_sync_sync()
            return next_id

        return await self._run_locked_with_retry(_run, retry_operation=False)

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        def _run():
            assert self._conn is not None
            self._conn.execute(sql, params)
            self._commit_and_sync_sync()

        await self._run_locked_with_retry(_run, retry_operation=False)

    async def execute_insert(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Execute one INSERT and return its generated integer row ID."""

        def _run() -> int:
            assert self._conn is not None
            cursor = self._conn.execute(sql, params)
            row_id = getattr(cursor, "lastrowid", None)
            if row_id is None:
                row = self._conn.execute("SELECT last_insert_rowid()").fetchone()
                row_id = _row_get(row, "last_insert_rowid()", index=0, default=0)
            self._commit_and_sync_sync()
            return int(row_id or 0)

        return await self._run_locked_with_retry(_run, retry_operation=False)

    async def execute_transaction(
        self,
        statements: Iterable[tuple[str, Sequence[Any]]],
        *,
        retry_safe: bool = False,
    ) -> None:
        """Commit several statements atomically on the local/remote replica."""
        items = [(sql, tuple(params)) for sql, params in statements]
        if not items:
            return

        def _run() -> None:
            assert self._conn is not None
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                for sql, params in items:
                    self._conn.execute(sql, params)
                self._commit_and_sync_sync()
            except Exception:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
                raise

        await self._run_locked_with_retry(_run, retry_operation=retry_safe)

    async def set_runtime_setting(self, key: str, value: Any) -> None:
        payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        await self.execute(
            "INSERT INTO runtime_settings(setting_key,value_json,updated_ts) VALUES(?,?,?) "
            "ON CONFLICT(setting_key) DO UPDATE SET value_json=excluded.value_json, updated_ts=excluded.updated_ts",
            (str(key), payload, int(time.time())),
        )

    async def get_runtime_setting(self, key: str, default: Any = None) -> Any:
        row = await self.fetchone(
            "SELECT value_json FROM runtime_settings WHERE setting_key=?",
            (str(key),),
        )
        if not row:
            return default
        try:
            return json.loads(str(row["value_json"] or "null"))
        except Exception:
            return default

    async def sync_remote(self) -> bool:
        """Force any pending embedded-replica changes toward the remote primary."""
        if not self.uses_remote:
            return True

        def _run() -> bool:
            self._sync_remote_with_retry_sync()
            self._remote_dirty = False
            self._remote_sync_retry_after = 0.0
            self._last_remote_sync_error = ""
            self._last_remote_sync_error_ts = 0
            return True

        return bool(await self._run_locked_with_retry(_run))

    async def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        items = list(seq)

        def _run():
            assert self._conn is not None
            self._conn.executemany(sql, items)
            self._commit_and_sync_sync()

        await self._run_locked_with_retry(_run, retry_operation=False)

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> Optional[Any]:
        def _run():
            assert self._conn is not None
            cur = self._conn.execute(sql, params)
            return _normalize_row(cur, cur.fetchone())

        return await self._run_locked_with_retry(_run)

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> List[Any]:
        def _run():
            assert self._conn is not None
            cur = self._conn.execute(sql, params)
            return _normalize_rows(cur, cur.fetchall())

        return await self._run_locked_with_retry(_run)
