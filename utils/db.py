from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager, closing
import json
import os
import re
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence

from utils.config_schema import (
    CONFIG_SCHEMA_VERSION,
    DATABASE_SCHEMA_VERSION,
    EMBED_SCHEMA_VERSION,
    RUNTIME_SCHEMA_VERSION,
)
from utils.libsql_worker import IsolatedConnection


class DatabaseBusyError(RuntimeError):
    """Storage is busy; no operation was started for this caller."""

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
        "turso worker",
        "file is not a database",
        "sqlite_notadb",
        "database disk image is malformed",
    )
    return any(marker in text for marker in markers)


def _is_replica_corruption_error(exc: Exception) -> bool:
    text = repr(exc).casefold()
    markers = (
        "file is not a database",
        "sqlite_notadb",
        "database disk image is malformed",
        "sqlite_corrupt",
        "malformed database schema",
    )
    return any(marker in text for marker in markers)


def _requires_libsql_integer_workaround(value: Any) -> bool:
    # libsql-python 0.1.x extracts i32 first and falls back to f64. Binding
    # larger Python ints directly therefore rounds Discord's 64-bit IDs.
    return type(value) is int and not (-(2**31) <= value <= 2**31 - 1)


def _exact_libsql_params(params: Sequence[Any]) -> tuple[Any, ...]:
    return tuple(str(value) if _requires_libsql_integer_workaround(value) else value for value in params)


def _legacy_libsql_value(value: Any) -> Any:
    if _requires_libsql_integer_workaround(value):
        return float(value)
    return value


def _legacy_where_params(sql: str, params: Sequence[Any]) -> Optional[tuple[Any, ...]]:
    """Build a compatibility parameter set for rows written by old releases.

    For UPDATE statements, values assigned before WHERE stay exact while only
    lookup parameters are rounded. This lets a successful compatibility write
    repair the row instead of storing another imprecise snowflake.
    """
    values = tuple(params)
    if not any(_requires_libsql_integer_workaround(value) for value in values):
        return None

    statement = str(sql or "").lstrip()
    match = re.search(r"\bWHERE\b", statement, flags=re.I)
    if statement.upper().startswith("UPDATE"):
        if match is None:
            return None
        where_param_start = statement[: match.start()].count("?")
        return tuple(
            value if index < where_param_start else _legacy_libsql_value(value)
            for index, value in enumerate(values)
        )
    if statement.upper().startswith(("SELECT", "DELETE", "WITH")):
        return tuple(_legacy_libsql_value(value) for value in values)
    return None


_USER_SNOWFLAKE_COLUMNS = {
    "actor_id",
    "applicant_id",
    "assignee_id",
    "author_id",
    "claimed_by",
    "completed_by",
    "created_by",
    "creator_id",
    "decided_by",
    "disabled_by",
    "ended_by",
    "handled_by",
    "new_owner_id",
    "previous_owner_id",
    "qa_by",
    "requested_by",
    "requester_id",
    "released_by",
    "responded_by",
    "reviewed_by",
    "reviewer_id",
    "started_by",
    "updated_by",
    "satisfaction_user_id",
    "uploaded_by",
    "user_id",
}
_CHANNEL_SNOWFLAKE_COLUMNS = {
    "channel_id",
    "log_channel_id",
    "offer_channel_id",
    "pre_restore_backup_channel_id",
    "report_channel_id",
    "request_channel_id",
    "ticket_channel_id",
}
_SNOWFLAKE_REPAIR_TABLES = {
    "activity_counts",
    "activity_last_counted",
    "anti_farm_events",
    "ban_info_requests",
    "bot_releases",
    "daily_stats",
    "daily_summary_reports",
    "database_backups",
    "database_restore_log",
    "discord_outbox",
    "health_metrics",
    "help_cooldowns",
    "help_sessions",
    "help_submissions",
    "impact_snapshots",
    "level_request_edit_audit",
    "level_request_scheduled_openings",
    "level_request_state",
    "level_request_submissions",
    "level_request_wave_summaries",
    "rps_streaks",
    "sticky_state",
    "staff_application_events",
    "staff_application_notes",
    "staff_application_cooldowns",
    "staff_applications",
    "staff_members",
    "staff_milestones",
    "staff_notes",
    "staff_outreach_episodes",
    "staff_portal_nickname_history",
    "staff_portal_profiles",
    "staff_queue_claim_events",
    "staff_queue_claims",
    "staff_review_qa",
    "staff_tasks",
    "staff_web_sessions",
    "level_outreach_attempts",
    "level_outreach_cp_snapshots",
    "level_outreach_cycles",
    "ticket_cooldowns",
    "ticket_sequences",
    "ticket_transcripts",
    "tickets",
    "transcript_requests",
    "user_notification_preferences",
    "weekly_claims",
    "weekly_dm_log",
    "weekly_recaps",
    "weekly_reminders",
    "weekly_request_reviews",
    "weekly_reward_disabled",
    "weekly_runs",
    "weekly_sessions",
    "weekly_streaks",
    "workflow_events",
}


class Database:
    """Small SQLite wrapper safe to use from an async bot.

    - Uses a single connection opened with check_same_thread=False
    - Serializes writes and connection recovery with an asyncio.Lock
    - Remote reads use bounded, independent read-only replica snapshots
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
        self._remote_reconnect_required = False
        self._replica_rebuild_required = False
        self._remote_sync_retry_after = 0.0
        self._last_remote_sync_error = ""
        self._last_remote_sync_error_ts = 0
        self._replica_rebuild_count = 0
        self._last_replica_rebuild_ts = 0
        self._last_replica_rebuild_reason = ""
        self._query_metrics: dict[str, dict[str, float | int]] = {}
        self._waiting_operations = 0
        self._active_operation = ""
        self._active_operation_since = 0.0
        self._active_operation_task = ""
        self._queue_timeouts = 0
        self._queue_timeout_seconds = 10.0
        self._primary_write_degraded = False
        self._last_operation_error_ts = 0
        self._read_slots = asyncio.Semaphore(4)
        self._read_waiting = 0
        self._last_queue_timeout_ts = 0
        self._last_queue_timeout_operation = ""
        self._background_queue_deferrals = 0

    @asynccontextmanager
    async def _guard(self, timeout: float | None = None, operation: str = "database"):
        self._waiting_operations += 1
        try:
            try:
                await asyncio.wait_for(self._lock.acquire(), self._queue_timeout_seconds if timeout is None else timeout)
            except asyncio.TimeoutError as exc:
                self._queue_timeouts += 1
                if timeout is None or timeout >= self._queue_timeout_seconds:
                    self._last_queue_timeout_ts = int(time.time())
                    self._last_queue_timeout_operation = operation
                else:
                    self._background_queue_deferrals += 1
                holder = self._active_operation or "connection/maintenance"
                owner = self._active_operation_task or "unknown"
                raise DatabaseBusyError(f"Database busy; {operation} was not started, please retry (holder={holder}, task={owner}, waiting={self._waiting_operations})") from exc
        finally:
            self._waiting_operations -= 1
        try:
            yield
        finally:
            self._lock.release()

    async def _thread_call(self, function, *args, budget: float = 30):
        def invoke():
            if isinstance(self._conn, IsolatedConnection):
                self._conn.set_deadline(budget)
            return function(*args)

        task = asyncio.create_task(asyncio.to_thread(invoke))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Cancelling to_thread does not stop its thread. Retain the lock
            # until it has exited so a second caller cannot race the connection.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if task.done() and not task.cancelled():
                task.exception()
            raise

    def _record_query_timing(self, operation: str, elapsed_ms: float, *, failed: bool) -> None:
        key = str(operation or "database")[:40]
        metric = self._query_metrics.setdefault(
            key,
            {"count": 0, "errors": 0, "total_ms": 0.0, "max_ms": 0.0},
        )
        metric["count"] = int(metric["count"]) + 1
        metric["errors"] = int(metric["errors"]) + int(failed)
        metric["total_ms"] = float(metric["total_ms"]) + max(0.0, float(elapsed_ms))
        metric["max_ms"] = max(float(metric["max_ms"]), max(0.0, float(elapsed_ms)))

    def query_timing_snapshot(self, *, reset: bool = False) -> dict[str, dict[str, float | int]]:
        snapshot: dict[str, dict[str, float | int]] = {}
        for key, raw in self._query_metrics.items():
            count = int(raw.get("count", 0) or 0)
            total_ms = float(raw.get("total_ms", 0.0) or 0.0)
            snapshot[key] = {
                "count": count,
                "errors": int(raw.get("errors", 0) or 0),
                "average_ms": round(total_ms / count, 2) if count else 0.0,
                "max_ms": round(float(raw.get("max_ms", 0.0) or 0.0), 2),
            }
        if reset:
            self._query_metrics.clear()
        return snapshot

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
        self._remote_reconnect_required = False

    def _adapt_params(self, params: Sequence[Any]) -> tuple[Any, ...]:
        values = tuple(params)
        return _exact_libsql_params(values) if self.uses_remote else values

    def _execute_sync(self, sql: str, params: Sequence[Any] = ()) -> Any:
        assert self._conn is not None
        return self._conn.execute(sql, self._adapt_params(params))

    def _execute_write_compat_sync(self, sql: str, params: Sequence[Any] = ()) -> Any:
        cursor = self._execute_sync(sql, params)
        if not self.uses_remote or int(getattr(cursor, "rowcount", -1) or 0) != 0:
            return cursor
        legacy_params = _legacy_where_params(sql, params)
        if legacy_params is None or legacy_params == tuple(params):
            return cursor
        assert self._conn is not None
        return self._conn.execute(sql, self._adapt_params(legacy_params))

    def _quarantine_replica_files_sync(self) -> list[Path]:
        stamp = f"{int(time.time())}-{os.getpid()}"
        quarantined: list[Path] = []
        for suffix in ("", "-wal", "-shm", "-journal"):
            source = self.path.parent / f"{self.path.name}{suffix}"
            if not source.exists():
                continue
            target = self.path.parent / f".{self.path.name}{suffix}.corrupt-{stamp}"
            try:
                os.replace(source, target)
                quarantined.append(target)
            except FileNotFoundError:
                continue

        old_files = sorted(
            self.path.parent.glob(f".{self.path.name}*.corrupt-*"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for old_file in old_files[12:]:
            try:
                old_file.unlink()
            except OSError:
                pass
        return quarantined

    def _rebuild_remote_replica_sync(self, reason: Exception | str) -> None:
        if not self.uses_remote:
            raise RuntimeError("A local SQLite database cannot be rebuilt from a remote primary.")
        self._close_connection_sync()
        self._quarantine_replica_files_sync()
        self._conn = self._open_connection_sync()
        self._sync_remote_with_retry_sync()
        self._ready = True
        self._remote_dirty = False
        self._remote_reconnect_required = False
        self._replica_rebuild_required = False
        self._remote_sync_retry_after = 0.0
        self._last_remote_sync_error = ""
        self._last_remote_sync_error_ts = 0
        self._replica_rebuild_count += 1
        self._last_replica_rebuild_ts = int(time.time())
        self._last_replica_rebuild_reason = f"{type(reason).__name__}: {reason}"[:500]
        print(
            "[Avenue Guard database] Rebuilt the local Turso replica from the remote primary "
            f"after {self._last_replica_rebuild_reason}",
            flush=True,
        )

    def _open_connection_sync(self) -> Any:
        if self.uses_remote:
            if libsql is None:
                raise RuntimeError("TURSO_DATABASE_URL is configured, but the libsql Python package is not installed.")
            if _looks_like_turso_platform_token(self.auth_token):
                raise RuntimeError(
                    "TURSO_AUTH_TOKEN looks like a Turso platform/API token, not a database auth token. "
                    "Create a database token with `turso db tokens create <database-name>` and use that value instead."
                )
            conn = IsolatedConnection(str(self.path), sync_url=self.remote_url, auth_token=self.auth_token)
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
        if self._replica_rebuild_required:
            self._rebuild_remote_replica_sync(
                self._last_remote_sync_error or "local replica corruption"
            )
            return
        if self._remote_reconnect_required:
            # A failed Hrana transaction can leave the local connection unable
            # to serve even local work. Reopen it before honoring sync backoff.
            self._reopen_connection_sync()
        if time.monotonic() < self._remote_sync_retry_after:
            return
        try:
            self._sync_remote_with_retry_sync()
        except Exception as exc:
            self._replica_rebuild_required = _is_replica_corruption_error(exc)
            self._remote_reconnect_required = _is_recoverable_remote_error(exc)
            self._last_remote_sync_error = f"{type(exc).__name__}: {exc}"[:1000]
            self._last_remote_sync_error_ts = int(time.time())
            retry_delay = 5.0 if _is_recoverable_remote_error(exc) else 60.0
            self._remote_sync_retry_after = time.monotonic() + retry_delay
            return
        self._remote_dirty = False
        self._remote_reconnect_required = False
        self._replica_rebuild_required = False
        self._remote_sync_retry_after = 0.0
        self._last_remote_sync_error = ""
        self._last_remote_sync_error_ts = 0

    def _commit_and_sync_sync(self) -> None:
        """Commit a transaction without forcing a full replica pull.

        Embedded-replica writes are forwarded to Turso's primary and reflected
        into the local file as part of the write path. Calling ``sync()`` after
        every transaction only performs an extra pull, amplifies S3 traffic,
        and exposes unrelated commands to transient replica-sync failures.
        Explicit pulls remain at startup, backup, shutdown, and ``sync_remote``.
        """
        assert self._conn is not None
        self._conn.commit()

    async def _run_locked_with_retry(
        self,
        operation,
        *,
        retry_operation: bool = True,
        attempt_pending_sync: bool = True,
        operation_name: str = "database",
        queue_timeout: float | None = None,
        operation_label: str | None = None,
    ) -> Any:
        await self.connect()
        attempts = 3 if self.uses_remote and retry_operation else 1
        last_error: Optional[Exception] = None

        for attempt in range(attempts):
            started = time.perf_counter()
            async with self._guard(queue_timeout, operation_label or operation_name):
                assert self._conn is not None
                self._active_operation = operation_label or operation_name
                self._active_operation_since = time.monotonic()
                task = asyncio.current_task()
                self._active_operation_task = task.get_name() if task is not None else "unknown"
                try:
                    def run():
                        if attempt_pending_sync:
                            self._try_pending_remote_sync_sync()
                        return operation()
                    result = await self._thread_call(run)
                    if operation_name in {"execute", "execute_affected", "insert", "transaction", "executemany", "next_ticket_id"}:
                        self._primary_write_degraded = False
                    self._record_query_timing(
                        operation_name,
                        (time.perf_counter() - started) * 1000,
                        failed=False,
                    )
                    return result
                except Exception as exc:
                    self._record_query_timing(
                        operation_name,
                        (time.perf_counter() - started) * 1000,
                        failed=True,
                    )
                    last_error = exc
                    self._last_operation_error_ts = int(time.time())
                    should_recover = self.uses_remote and _is_recoverable_remote_error(exc)
                    if self.uses_remote and operation_name in {"execute", "execute_affected", "insert", "transaction", "executemany", "next_ticket_id"}:
                        if should_recover or any(marker in str(exc).casefold() for marker in ("unauthorized", "forbidden", "401", "403", "read-only", "readonly")):
                            self._primary_write_degraded = True
                    if should_recover:
                        try:
                            if _is_replica_corruption_error(exc):
                                await self._thread_call(self._rebuild_remote_replica_sync, exc)
                            else:
                                await self._thread_call(self._reopen_connection_sync)
                        except Exception as recovery_error:
                            self._ready = False
                            self._last_remote_sync_error = f"Reconnect failed: {recovery_error}"[:1000]
                            self._last_remote_sync_error_ts = int(time.time())
                            raise exc from recovery_error
                    else:
                        try:
                            await self._thread_call(self._conn.rollback)
                        except Exception:
                            pass
                    if not should_recover or attempt >= attempts - 1:
                        raise
                finally:
                    self._active_operation = ""
                    self._active_operation_since = 0.0
                    self._active_operation_task = ""

            await asyncio.sleep(0.35 * (attempt + 1))

        if last_error is not None:
            raise last_error
        return None

    async def connect(self) -> None:
        if self._conn is not None and self._ready:
            return
        async with self._guard():
            if self._conn is not None and self._ready:
                return

            def _connect_and_migrate():
                try:
                    if self._conn is None:
                        self._conn = self._open_connection_sync()

                    assert self._conn is not None
                    if self.uses_remote:
                        # Pull the durable primary before any local migration.
                        # A fresh or discarded replica must never publish an
                        # empty local view over existing cloud state.
                        self._sync_remote_with_retry_sync()
                    self._migrate_sync()
                except Exception as exc:
                    if not self.uses_remote or not _is_replica_corruption_error(exc):
                        raise
                    self._rebuild_remote_replica_sync(exc)
                    self._ready = False
                    self._migrate_sync()
                self._remote_reconnect_required = False
                self._replica_rebuild_required = False

            await self._thread_call(_connect_and_migrate, budget=90)
            self._ready = True

    async def close(self) -> None:
        async with self._guard():
            if self._conn is None:
                return

            await self._thread_call(self._close_connection_sync)

    async def backup_to(self, target_path: str | Path) -> int:
        await self.connect()
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        async with self._guard():
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
                    with closing(sqlite3.connect(source_uri, uri=True)) as source, closing(
                        sqlite3.connect(str(target))
                    ) as dest:
                        source.backup(dest)
                else:
                    self._conn.execute("PRAGMA wal_checkpoint(FULL);")
                    with closing(sqlite3.connect(str(target))) as dest:
                        self._conn.backup(dest)

                with closing(
                    sqlite3.connect(f"file:{target}?mode=ro", uri=True)
                ) as check:
                    row = check.execute("PRAGMA integrity_check;").fetchone()
                    result = str(row[0] if row else "")
                    if result.casefold() != "ok":
                        raise sqlite3.DatabaseError(f"backup integrity_check returned {result!r}")
                return int(target.stat().st_size)

            try:
                return await self._thread_call(_backup)
            except Exception as exc:
                if not self.uses_remote or not _is_replica_corruption_error(exc):
                    raise
                await self._thread_call(self._rebuild_remote_replica_sync, exc)
                return await self._thread_call(_backup)

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

        async with self._guard():
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

            return await self._thread_call(_restore)

    def _migrate_sync(self) -> None:
        assert self._conn is not None
        try:
            versions = dict(self._conn.execute("SELECT component,schema_version FROM schema_metadata").fetchall())
        except Exception as exc:
            if "no such table: schema_metadata" not in str(exc).casefold():
                raise
            versions = {}
        expected = {
            "database": DATABASE_SCHEMA_VERSION,
            "config": CONFIG_SCHEMA_VERSION,
            "runtime_settings": RUNTIME_SCHEMA_VERSION,
            "embed_templates": EMBED_SCHEMA_VERSION,
        }
        if versions.get("database", 0) > DATABASE_SCHEMA_VERSION:
            raise RuntimeError("Database schema is newer than this bot; deploy matching code instead of downgrading it")
        from utils.historical_audit_schema import AUDIT_SCHEMA, AUDIT_TABLES
        from utils.priority_system_schema import PRIORITY_SCHEMA, PRIORITY_TABLES
        from utils.staff_portal_schema import STAFF_PORTAL_SCHEMA, STAFF_PORTAL_TABLES

        if versions in (
            {**expected, "database": 5},
            {**expected, "database": 6},
            {**expected, "database": 7},
            {**expected, "database": 8},
            {**expected, "database": 9},
            {**expected, "database": 10},
            {**expected, "database": 11},
        ):
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute("CREATE TABLE IF NOT EXISTS activity_flush_batches(batch_id TEXT PRIMARY KEY,created_ts INTEGER NOT NULL)")
                for stmt in AUDIT_SCHEMA:
                    self._conn.execute(stmt)
                self._ensure_column_sync(
                    "level_request_state",
                    "review_system_version",
                    "TEXT NOT NULL DEFAULT 'legacy'",
                )
                self._ensure_column_sync(
                    "level_request_submissions",
                    "review_system_version",
                    "TEXT NOT NULL DEFAULT 'legacy'",
                )
                self._ensure_column_sync("level_request_submissions", "send_type", "TEXT")
                self._conn.execute(
                    "UPDATE level_request_state SET review_system_version='legacy' "
                    "WHERE review_system_version IS NULL OR review_system_version=''"
                )
                self._conn.execute(
                    "UPDATE level_request_submissions SET review_system_version='legacy' "
                    "WHERE review_system_version IS NULL OR review_system_version=''"
                )
                for stmt in PRIORITY_SCHEMA:
                    self._conn.execute(stmt)
                self._ensure_column_sync("level_outreach_queue", "hidden_from_state", "TEXT")
                self._ensure_column_sync("level_outreach_attempts", "episode_id", "INTEGER")
                self._ensure_column_sync(
                    "level_outreach_attempts", "private_target_key", "TEXT NOT NULL DEFAULT ''"
                )
                self._ensure_column_sync("level_outreach_attempts", "event_ts", "INTEGER")
                for stmt in STAFF_PORTAL_SCHEMA:
                    self._conn.execute(stmt)
                self._ensure_column_sync("staff_applications", "review_prompt_key", "TEXT")
                self._ensure_column_sync("staff_applications", "review_thread_outbox_id", "INTEGER")
                self._ensure_column_sync("staff_applications", "review_thread_id", "INTEGER")
                self._ensure_column_sync("staff_applications", "interview_ticket_outbox_id", "INTEGER")
                self._ensure_column_sync("staff_applications", "interview_ticket_channel_id", "INTEGER")
                self._ensure_column_sync("staff_applications", "form_version", "TEXT NOT NULL DEFAULT 'legacy'")
                self._ensure_column_sync("staff_applications", "submitted_answers_json", "TEXT")
                self._ensure_column_sync("staff_applications", "submitted_questions_json", "TEXT")
                self._ensure_column_sync("staff_applications", "decision_category", "TEXT NOT NULL DEFAULT ''")
                self._ensure_column_sync("staff_applications", "applicant_message", "TEXT NOT NULL DEFAULT ''")
                self._ensure_column_sync("staff_applications", "first_review_ts", "INTEGER")
                self._ensure_column_sync("staff_applications", "calibration_resolved_ts", "INTEGER")
                self._ensure_column_sync("staff_applications", "calibration_resolved_by", "INTEGER")
                self._ensure_column_sync("staff_applications", "calibration_note", "TEXT NOT NULL DEFAULT ''")
                self._ensure_column_sync("staff_application_interviews", "notes", "TEXT NOT NULL DEFAULT ''")
                self._ensure_column_sync("staff_application_interviews", "completed_by", "INTEGER")
                self._execute_sync("UPDATE schema_metadata SET schema_version=?,updated_ts=? WHERE component='database'", (DATABASE_SCHEMA_VERSION, int(time.time())))
                self._commit_and_sync_sync()
            except Exception:
                self._conn.rollback()
                raise
            versions["database"] = DATABASE_SCHEMA_VERSION
        if versions == expected:
            tables = {row[0] for row in self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if AUDIT_TABLES | PRIORITY_TABLES | STAFF_PORTAL_TABLES | {"tickets", "ticket_transcripts", "activity_counts", "activity_flush_batches", "weekly_claims", "weekly_sessions", "daily_stats", "level_request_state", "level_request_submissions", "weekly_request_reviews", "gd_level_validation_cache", "discord_outbox", "workflow_events", "error_incidents", "error_incident_batches", "health_metrics", "runtime_settings", "bot_releases", "impact_snapshots", "restore_drills", "monthly_impact_reports"} <= tables:
                return
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
            """CREATE TABLE IF NOT EXISTS activity_flush_batches(
                batch_id TEXT PRIMARY KEY,
                created_ts INTEGER NOT NULL
            );""",
            """CREATE TABLE IF NOT EXISTS weekly_claims(
                guild_id INTEGER NOT NULL,
                week_start TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                rank INTEGER NOT NULL,
                status TEXT NOT NULL,
                contacted_ts INTEGER NOT NULL,
                offer_channel_id INTEGER,
                offer_message_id INTEGER,
                offer_expires_ts INTEGER,
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
                delivery_status TEXT NOT NULL DEFAULT 'sent',
                channel_id INTEGER,
                message_id INTEGER,
                attempted_ts INTEGER,
                error_text TEXT,
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
                satisfaction_delivery_status TEXT NOT NULL DEFAULT 'pending',
                satisfaction_delivery_error TEXT,
                satisfaction_attempted_ts INTEGER,
                satisfaction_resolution_version INTEGER NOT NULL DEFAULT 0,
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
            """CREATE TABLE IF NOT EXISTS ban_info_requests(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_ts INTEGER NOT NULL,
                updated_ts INTEGER NOT NULL,
                log_channel_id INTEGER,
                log_message_id INTEGER,
                handled_by INTEGER,
                reason TEXT,
                ban_date TEXT,
                evidence_text TEXT,
                evidence_files_json TEXT NOT NULL DEFAULT '[]',
                notes TEXT,
                history_json TEXT NOT NULL DEFAULT '{}',
                delivered_ts INTEGER,
                error_text TEXT
            );""",
            """CREATE TABLE IF NOT EXISTS transcript_requests(
                guild_id INTEGER NOT NULL,
                request_message_id INTEGER PRIMARY KEY,
                ticket_channel_id INTEGER NOT NULL,
                requester_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_ts INTEGER NOT NULL,
                updated_ts INTEGER,
                ticket_id INTEGER,
                reviewed_by INTEGER,
                reviewed_ts INTEGER,
                error_text TEXT
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
                request_type TEXT,
                review_system_version TEXT NOT NULL DEFAULT 'legacy'
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
                review_system_version TEXT NOT NULL DEFAULT 'legacy',
                send_type TEXT,
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
            """CREATE TABLE IF NOT EXISTS bot_releases(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                changes_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'pending',
                source TEXT NOT NULL DEFAULT 'command',
                created_by INTEGER,
                created_ts INTEGER NOT NULL,
                approval_message_id INTEGER,
                approval_delivery_status TEXT NOT NULL DEFAULT 'pending',
                approval_attempted_ts INTEGER,
                decided_by INTEGER,
                decided_ts INTEGER,
                error_text TEXT
            );""",
            """CREATE TABLE IF NOT EXISTS bot_uptime_tracker(
                id INTEGER PRIMARY KEY CHECK(id = 1),
                tracking_started_ts INTEGER NOT NULL,
                last_heartbeat_ts INTEGER NOT NULL,
                observed_seconds INTEGER NOT NULL DEFAULT 0,
                online_seconds INTEGER NOT NULL DEFAULT 0
            );""",
            """CREATE TABLE IF NOT EXISTS schema_metadata(
                component TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                updated_ts INTEGER NOT NULL
            );""",
            """CREATE TABLE IF NOT EXISTS discord_outbox(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                correlation_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                action_type TEXT NOT NULL,
                guild_id INTEGER,
                channel_id INTEGER,
                user_id INTEGER,
                message_id INTEGER,
                payload_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_ts INTEGER NOT NULL,
                created_ts INTEGER NOT NULL,
                updated_ts INTEGER NOT NULL,
                delivered_ts INTEGER,
                delivered_message_id INTEGER,
                last_error TEXT
            );""",
            """CREATE TABLE IF NOT EXISTS workflow_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                correlation_id TEXT NOT NULL,
                workflow_type TEXT NOT NULL,
                entity_id TEXT NOT NULL DEFAULT '',
                event TEXT NOT NULL,
                guild_id INTEGER,
                actor_id INTEGER,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_ts INTEGER NOT NULL
            );""",
            """CREATE TABLE IF NOT EXISTS error_incidents(
                fingerprint TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                first_seen_ts INTEGER NOT NULL,
                last_seen_ts INTEGER NOT NULL,
                occurrence_count INTEGER NOT NULL DEFAULT 1,
                last_message TEXT NOT NULL,
                last_correlation_id TEXT,
                log_message_id INTEGER,
                resolved_ts INTEGER
            );""",
            """CREATE TABLE IF NOT EXISTS error_incident_batches(
                batch_id TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                created_ts INTEGER NOT NULL
            );""",
            """CREATE TABLE IF NOT EXISTS health_metrics(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER,
                sample_ts INTEGER NOT NULL,
                metric_type TEXT NOT NULL,
                value REAL,
                payload_json TEXT NOT NULL DEFAULT '{}'
            );""",
            """CREATE TABLE IF NOT EXISTS permission_drift_events(
                guild_id INTEGER NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                detail_json TEXT NOT NULL DEFAULT '{}',
                first_seen_ts INTEGER NOT NULL,
                last_seen_ts INTEGER NOT NULL,
                PRIMARY KEY(guild_id, resource_type, resource_id)
            );""",
            """CREATE TABLE IF NOT EXISTS user_notification_preferences(
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                request_result_mode TEXT NOT NULL DEFAULT 'channel',
                updated_ts INTEGER NOT NULL,
                PRIMARY KEY(guild_id, user_id)
            );""",
            """CREATE TABLE IF NOT EXISTS restore_drills(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER,
                drill_ts INTEGER NOT NULL,
                status TEXT NOT NULL,
                duration_ms INTEGER NOT NULL,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                table_count INTEGER NOT NULL DEFAULT 0,
                missing_tables_json TEXT NOT NULL DEFAULT '[]',
                error_text TEXT,
                trigger TEXT NOT NULL DEFAULT 'scheduled'
            );""",
            """CREATE TABLE IF NOT EXISTS monthly_impact_reports(
                guild_id INTEGER NOT NULL,
                month_key TEXT NOT NULL,
                generated_ts INTEGER NOT NULL,
                channel_id INTEGER,
                message_id INTEGER,
                payload_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'generated',
                PRIMARY KEY(guild_id, month_key)
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
            """CREATE INDEX IF NOT EXISTS idx_ban_info_requests_user_status
                ON ban_info_requests(guild_id, user_id, status, created_ts DESC);""",
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_ban_info_requests_log_message
                ON ban_info_requests(guild_id, log_channel_id, log_message_id)
                WHERE log_message_id IS NOT NULL;""",
            """CREATE INDEX IF NOT EXISTS idx_impact_snapshots_guild
                ON impact_snapshots(guild_id, snapshot_ts DESC);""",
            """CREATE INDEX IF NOT EXISTS idx_database_backups_guild
                ON database_backups(guild_id, backup_ts DESC);""",
            """CREATE INDEX IF NOT EXISTS idx_bot_releases_status_time
                ON bot_releases(status, decided_ts DESC, id DESC);""",
            """CREATE INDEX IF NOT EXISTS idx_bot_releases_version
                ON bot_releases(version, id DESC);""",
            """CREATE INDEX IF NOT EXISTS idx_database_restore_log_guild
                ON database_restore_log(guild_id, restore_ts DESC);""",
            """CREATE INDEX IF NOT EXISTS idx_discord_outbox_ready
                ON discord_outbox(status, next_attempt_ts, created_ts);""",
            """CREATE INDEX IF NOT EXISTS idx_workflow_events_correlation
                ON workflow_events(correlation_id, created_ts);""",
            """CREATE INDEX IF NOT EXISTS idx_workflow_events_entity
                ON workflow_events(workflow_type, entity_id, created_ts);""",
            """CREATE INDEX IF NOT EXISTS idx_health_metrics_time
                ON health_metrics(guild_id, sample_ts DESC, metric_type);""",
            """CREATE INDEX IF NOT EXISTS idx_restore_drills_time
                ON restore_drills(guild_id, drill_ts DESC);""",
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
        self._ensure_column_sync(
            "tickets",
            "satisfaction_delivery_status",
            "TEXT NOT NULL DEFAULT 'pending'",
        )
        self._ensure_column_sync("tickets", "satisfaction_delivery_error", "TEXT")
        self._ensure_column_sync("tickets", "satisfaction_attempted_ts", "INTEGER")
        self._ensure_column_sync(
            "tickets",
            "satisfaction_resolution_version",
            "INTEGER NOT NULL DEFAULT 0",
        )
        self._conn.execute(
            "UPDATE tickets SET satisfaction_delivery_status='sent' "
            "WHERE satisfaction_message_id IS NOT NULL AND satisfaction_score IS NULL "
            "AND satisfaction_delivery_status='pending'"
        )
        self._conn.execute(
            "UPDATE tickets SET satisfaction_delivery_status='completed' "
            "WHERE satisfaction_score IS NOT NULL"
        )
        self._ensure_column_sync("tickets", "closing_prompt_message_id", "INTEGER")
        self._ensure_column_sync("tickets", "opening_message_id", "INTEGER")
        self._ensure_column_sync("weekly_claims", "offer_channel_id", "INTEGER")
        self._ensure_column_sync("weekly_claims", "offer_message_id", "INTEGER")
        self._ensure_column_sync("weekly_claims", "offer_expires_ts", "INTEGER")
        self._ensure_column_sync(
            "weekly_reminders",
            "delivery_status",
            "TEXT NOT NULL DEFAULT 'sent'",
        )
        self._ensure_column_sync("weekly_reminders", "channel_id", "INTEGER")
        self._ensure_column_sync("weekly_reminders", "message_id", "INTEGER")
        self._ensure_column_sync("weekly_reminders", "attempted_ts", "INTEGER")
        self._ensure_column_sync("weekly_reminders", "error_text", "TEXT")
        self._ensure_column_sync("weekly_sessions", "decline_prompt_message_id", "INTEGER")
        self._ensure_column_sync("transcript_requests", "ticket_id", "INTEGER")
        self._ensure_column_sync("transcript_requests", "updated_ts", "INTEGER")
        self._ensure_column_sync("transcript_requests", "reviewed_by", "INTEGER")
        self._ensure_column_sync("transcript_requests", "reviewed_ts", "INTEGER")
        self._ensure_column_sync("transcript_requests", "error_text", "TEXT")
        self._conn.execute(
            "UPDATE transcript_requests SET updated_ts=created_ts WHERE updated_ts IS NULL"
        )
        self._ensure_column_sync("level_request_state", "request_channel_id", "INTEGER")
        self._ensure_column_sync("level_request_state", "request_message_id", "INTEGER")
        self._ensure_column_sync("level_request_state", "request_type", "TEXT")
        self._ensure_column_sync(
            "level_request_state",
            "review_system_version",
            "TEXT NOT NULL DEFAULT 'legacy'",
        )
        self._ensure_column_sync("level_request_submissions", "request_message_id", "INTEGER")
        self._ensure_column_sync("level_request_submissions", "result", "TEXT")
        self._ensure_column_sync("level_request_submissions", "review_text", "TEXT")
        self._ensure_column_sync("level_request_submissions", "reviewed_by", "INTEGER")
        self._ensure_column_sync("level_request_submissions", "reviewed_ts", "INTEGER")
        self._ensure_column_sync("level_request_submissions", "edit_deadline_ts", "INTEGER")
        self._ensure_column_sync("level_request_submissions", "correlation_id", "TEXT")
        self._ensure_column_sync(
            "level_request_submissions",
            "review_system_version",
            "TEXT NOT NULL DEFAULT 'legacy'",
        )
        self._ensure_column_sync("level_request_submissions", "send_type", "TEXT")
        self._conn.execute(
            "UPDATE level_request_state SET review_system_version='legacy' "
            "WHERE review_system_version IS NULL OR review_system_version=''"
        )
        self._conn.execute(
            "UPDATE level_request_submissions SET review_system_version='legacy' "
            "WHERE review_system_version IS NULL OR review_system_version=''"
        )
        self._ensure_column_sync("weekly_request_reviews", "channel_id", "INTEGER")
        self._ensure_column_sync("weekly_request_reviews", "rank", "INTEGER")
        self._ensure_column_sync("weekly_request_reviews", "result", "TEXT")
        self._ensure_column_sync("weekly_request_reviews", "review_text", "TEXT")
        self._ensure_column_sync("weekly_request_reviews", "reviewed_by", "INTEGER")
        self._ensure_column_sync("weekly_request_reviews", "reviewed_ts", "INTEGER")
        self._ensure_column_sync("weekly_request_reviews", "data_json", "TEXT NOT NULL DEFAULT '{}'")
        self._ensure_column_sync("weekly_request_reviews", "correlation_id", "TEXT")
        self._ensure_column_sync("tickets", "correlation_id", "TEXT")
        self._ensure_column_sync("help_submissions", "correlation_id", "TEXT")
        self._ensure_column_sync("level_request_scheduled_openings", "correlation_id", "TEXT")
        self._ensure_column_sync("discord_outbox", "delivered_message_id", "INTEGER")
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
        self._ensure_column_sync(
            "bot_releases",
            "approval_delivery_status",
            "TEXT NOT NULL DEFAULT 'pending'",
        )
        self._ensure_column_sync("bot_releases", "approval_attempted_ts", "INTEGER")
        self._conn.execute(
            "UPDATE bot_releases SET approval_delivery_status='sent' "
            "WHERE approval_message_id IS NOT NULL "
            "AND approval_delivery_status='pending'"
        )
        self._conn.execute(
            "UPDATE bot_releases SET approval_delivery_status='completed' "
            "WHERE status IN ('approved','rejected')"
        )
        self._normalize_weekly_dm_log_sync()
        self._init_ticket_sequences_sync()
        now_ts = int(time.time())
        for component, version in (
            ("database", DATABASE_SCHEMA_VERSION),
            ("config", CONFIG_SCHEMA_VERSION),
            ("runtime_settings", RUNTIME_SCHEMA_VERSION),
            ("embed_templates", EMBED_SCHEMA_VERSION),
        ):
            self._conn.execute(
                "INSERT INTO schema_metadata(component,schema_version,updated_ts) VALUES(?,?,?) "
                "ON CONFLICT(component) DO UPDATE SET schema_version=excluded.schema_version,updated_ts=excluded.updated_ts",
                (component, version, now_ts),
            )
        for stmt in AUDIT_SCHEMA:
            self._conn.execute(stmt)
        for stmt in PRIORITY_SCHEMA:
            self._conn.execute(stmt)
        self._ensure_column_sync("level_outreach_queue", "hidden_from_state", "TEXT")
        self._ensure_column_sync("level_outreach_attempts", "episode_id", "INTEGER")
        self._ensure_column_sync(
            "level_outreach_attempts", "private_target_key", "TEXT NOT NULL DEFAULT ''"
        )
        self._ensure_column_sync("level_outreach_attempts", "event_ts", "INTEGER")
        for stmt in STAFF_PORTAL_SCHEMA:
            self._conn.execute(stmt)
        self._ensure_column_sync("staff_applications", "review_prompt_key", "TEXT")
        self._ensure_column_sync("staff_applications", "review_thread_outbox_id", "INTEGER")
        self._ensure_column_sync("staff_applications", "review_thread_id", "INTEGER")
        self._ensure_column_sync("staff_applications", "interview_ticket_outbox_id", "INTEGER")
        self._ensure_column_sync("staff_applications", "interview_ticket_channel_id", "INTEGER")
        self._ensure_column_sync("staff_applications", "form_version", "TEXT NOT NULL DEFAULT 'legacy'")
        self._ensure_column_sync("staff_applications", "submitted_answers_json", "TEXT")
        self._ensure_column_sync("staff_applications", "submitted_questions_json", "TEXT")
        self._ensure_column_sync("staff_applications", "decision_category", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column_sync("staff_applications", "applicant_message", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column_sync("staff_applications", "first_review_ts", "INTEGER")
        self._ensure_column_sync("staff_applications", "calibration_resolved_ts", "INTEGER")
        self._ensure_column_sync("staff_applications", "calibration_resolved_by", "INTEGER")
        self._ensure_column_sync("staff_applications", "calibration_note", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column_sync("staff_application_interviews", "notes", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column_sync("staff_application_interviews", "completed_by", "INTEGER")
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
            max_row = self._execute_sync(
                "SELECT MAX(ticket_id) AS m FROM tickets WHERE guild_id=?",
                (gid,),
            ).fetchone()
            max_value = _row_get(max_row, "m", index=0)
            max_id = int(max_value) if max_value is not None else 0
            cur2 = self._execute_sync(
                "SELECT next_ticket_id FROM ticket_sequences WHERE guild_id=?",
                (gid,),
            )
            sequence_row = cur2.fetchone()
            if sequence_row is None:
                self._execute_sync(
                    "INSERT INTO ticket_sequences(guild_id, next_ticket_id) VALUES(?,?)",
                    (gid, max_id + 1 if max_id > 0 else 1),
                )
            else:
                current_next = int(_row_get(sequence_row, "next_ticket_id", index=0, default=1) or 1)
                if current_next <= max_id:
                    self._execute_sync(
                        "UPDATE ticket_sequences SET next_ticket_id=? WHERE guild_id=?",
                        (max_id + 1, gid),
                    )

    async def next_ticket_id(self, guild_id: int) -> int:
        def _run():
            assert self._conn is not None
            cur = self._execute_sync(
                "SELECT next_ticket_id FROM ticket_sequences WHERE guild_id=?",
                (guild_id,),
            )
            row = cur.fetchone()
            if row is None:
                next_id = 1
                self._execute_sync(
                    "INSERT INTO ticket_sequences(guild_id, next_ticket_id) VALUES(?,?)",
                    (guild_id, 2),
                )
                self._commit_and_sync_sync()
                return next_id
            next_id = int(_row_get(row, "next_ticket_id", index=0, default=1) or 1)
            self._execute_write_compat_sync(
                "UPDATE ticket_sequences SET next_ticket_id=? WHERE guild_id=?",
                (next_id + 1, guild_id),
            )
            self._commit_and_sync_sync()
            return next_id

        return await self._run_locked_with_retry(_run, retry_operation=False, operation_name="next_ticket_id")

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        def _run():
            assert self._conn is not None
            self._execute_write_compat_sync(sql, params)
            self._commit_and_sync_sync()

        await self._run_locked_with_retry(_run, retry_operation=False, operation_name="execute")

    async def execute_affected(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Execute one write and return its affected-row count under the same lock."""

        def _run() -> int:
            assert self._conn is not None
            self._execute_write_compat_sync(sql, params)
            row = self._conn.execute("SELECT changes()").fetchone()
            changed = int(_row_get(row, "changes()", index=0, default=0) or 0)
            self._commit_and_sync_sync()
            return changed

        return await self._run_locked_with_retry(
            _run,
            retry_operation=False,
            operation_name="execute_affected",
        )

    async def execute_insert(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Execute one INSERT and return its generated integer row ID."""

        def _run() -> int:
            assert self._conn is not None
            cursor = self._execute_sync(sql, params)
            row_id = getattr(cursor, "lastrowid", None)
            if row_id is None:
                row = self._conn.execute("SELECT last_insert_rowid()").fetchone()
                row_id = _row_get(row, "last_insert_rowid()", index=0, default=0)
            self._commit_and_sync_sync()
            return int(row_id or 0)

        return await self._run_locked_with_retry(_run, retry_operation=False, operation_name="insert")

    async def execute_transaction(
        self,
        statements: Iterable[tuple[str, Sequence[Any]]],
        *,
        retry_safe: bool = False,
        queue_timeout: float | None = None,
        operation_label: str | None = None,
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
                    self._execute_write_compat_sync(sql, params)
                self._commit_and_sync_sync()
            except Exception:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
                raise

        await self._run_locked_with_retry(_run, retry_operation=retry_safe, operation_name="transaction", queue_timeout=queue_timeout, operation_label=operation_label)

    async def apply_activity_batch(self, batch_id: str, counts: Sequence[tuple], last_seen: Sequence[tuple]) -> None:
        """Commit counters, cooldowns and a retry receipt together in few RPCs."""
        if not batch_id or len(counts) > 50 or len(last_seen) > 50:
            raise ValueError("activity batches need an ID and at most 50 rows per table")
        statements = []
        if counts:
            placeholders = ",".join("(?,?,?,?)" for _ in counts)
            statements.append((
                "INSERT INTO activity_counts(guild_id,user_id,week_start,count) "
                f"SELECT column1,column2,column3,column4 FROM (VALUES {placeholders}) "  # nosec B608
                "WHERE NOT EXISTS(SELECT 1 FROM activity_flush_batches WHERE batch_id=?) "
                "ON CONFLICT(guild_id,user_id,week_start) DO UPDATE SET count=count+excluded.count",
                tuple(value for row in counts for value in row) + (batch_id,),
            ))
        if last_seen:
            placeholders = ",".join("(?,?,?)" for _ in last_seen)
            statements.append((
                "INSERT INTO activity_last_counted(guild_id,user_id,last_counted_ts) "
                f"SELECT column1,column2,column3 FROM (VALUES {placeholders}) "  # nosec B608
                "WHERE NOT EXISTS(SELECT 1 FROM activity_flush_batches WHERE batch_id=?) "
                "ON CONFLICT(guild_id,user_id) DO UPDATE SET last_counted_ts=MAX(last_counted_ts,excluded.last_counted_ts)",
                tuple(value for row in last_seen for value in row) + (batch_id,),
            ))
        statements.append(("INSERT OR IGNORE INTO activity_flush_batches(batch_id,created_ts) VALUES(?,?)", (batch_id, int(time.time()))))
        await self.execute_transaction(statements, retry_safe=True, queue_timeout=0.5, operation_label="tracking.activity")

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

    async def repair_legacy_snowflake_precision(
        self,
        *,
        guild_ids: Sequence[int] = (),
        user_ids: Sequence[int] = (),
        channel_ids: Sequence[int] = (),
    ) -> dict[str, int]:
        """Repair IDs rounded by libsql-python's historical f64 fallback.

        Only IDs supplied by Discord itself are trusted. Ambiguous 256-value
        buckets are skipped, and conflicting rows are retained for manual
        inspection instead of being deleted.
        """
        if not self.uses_remote:
            return {
                "updated": 0,
                "conflicts": 0,
                "ambiguous": 0,
                "feedback_requeued": 0,
                "audited": 0,
            }

        def _mapping(values: Sequence[int]) -> tuple[dict[int, int], int]:
            candidates: dict[int, set[int]] = {}
            for raw_value in values:
                try:
                    exact = int(raw_value)
                except (TypeError, ValueError):
                    continue
                if not _requires_libsql_integer_workaround(exact):
                    continue
                legacy = int(float(exact))
                candidates.setdefault(legacy, set()).add(exact)
            mapping = {
                legacy: next(iter(exact_values))
                for legacy, exact_values in candidates.items()
                if len(exact_values) == 1 and next(iter(exact_values)) != legacy
            }
            ambiguous = sum(1 for exact_values in candidates.values() if len(exact_values) > 1)
            return mapping, ambiguous

        guild_map, guild_ambiguous = _mapping(guild_ids)
        user_map, user_ambiguous = _mapping(user_ids)
        channel_map, channel_ambiguous = _mapping(channel_ids)

        result = {
            "updated": 0,
            "conflicts": 0,
            "ambiguous": guild_ambiguous + user_ambiguous + channel_ambiguous,
            "feedback_requeued": 0,
            "audited": 0,
        }

        # Plan from the read-only local replica. The old implementation scanned
        # every table through the isolated writer process while holding one large
        # transaction, which could exhaust the worker deadline before any live
        # command had a chance to use the database.
        table_rows = await self.fetchall_local(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        available_tables = {
            str(_row_get(row, "name", index=0, default="")) for row in table_rows
        }
        audit_enabled = "staff_snowflake_repairs" in available_tables
        tables = sorted(
            table for table in available_tables if table in _SNOWFLAKE_REPAIR_TABLES
        )
        repairs: list[tuple[str, str, int, int]] = []
        for table in tables:
            info = await self.fetchall_local(f"PRAGMA table_info({table})")
            columns = {
                str(_row_get(row, "name", index=1, default=""))
                for row in info
            }
            for column in sorted(columns):
                mapping: dict[int, int]
                if column == "guild_id":
                    mapping = guild_map
                elif column in _USER_SNOWFLAKE_COLUMNS:
                    mapping = user_map
                elif column in _CHANNEL_SNOWFLAKE_COLUMNS:
                    mapping = channel_map
                else:
                    continue
                if not mapping:
                    continue
                values = await self.fetchall_local(
                    f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL"  # nosec B608
                )
                for value_row in values:
                    try:
                        stored = int(_row_get(value_row, column, index=0))
                    except (TypeError, ValueError):
                        continue
                    exact = mapping.get(stored)
                    if exact is not None and exact != stored:
                        repairs.append((table, column, stored, exact))

        if not repairs:
            return result

        # Each batch is independently idempotent and releases the writer between
        # chunks. If Turso reports unknown completion, replay can only update rows
        # that still contain the legacy rounded value.
        for offset in range(0, len(repairs), 12):
            batch = tuple(repairs[offset : offset + 12])

            def _run_batch(batch=batch) -> dict[str, int]:
                assert self._conn is not None
                batch_result = {
                    "updated": 0,
                    "conflicts": 0,
                    "feedback_requeued": 0,
                    "audited": 0,
                }
                try:
                    self._conn.execute("BEGIN IMMEDIATE")
                    for table, column, stored, exact in batch:
                        cursor = self._conn.execute(
                            f"UPDATE OR IGNORE {table} SET {column}=? WHERE {column}=?",  # nosec B608
                            (str(exact), str(stored)),
                        )
                        changed = max(0, int(getattr(cursor, "rowcount", 0) or 0))
                        batch_result["updated"] += changed
                        if table == "tickets" and column == "creator_id" and changed:
                            retry_cursor = self._conn.execute(
                                "UPDATE tickets SET satisfaction_delivery_status='pending', "
                                "satisfaction_delivery_error=NULL "
                                "WHERE creator_id=? AND satisfaction_score IS NULL "
                                "AND satisfaction_message_id IS NULL "
                                "AND satisfaction_delivery_status='recipient_unavailable'",
                                (str(exact),),
                            )
                            batch_result["feedback_requeued"] += max(
                                0,
                                int(getattr(retry_cursor, "rowcount", 0) or 0),
                            )
                        remaining = self._conn.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE {column}=?",  # nosec B608
                            (str(stored),),
                        ).fetchone()
                        remaining_count = int(
                            _row_get(remaining, "COUNT(*)", index=0, default=0) or 0
                        )
                        batch_result["conflicts"] += remaining_count
                        if audit_enabled:
                            self._conn.execute(
                                "INSERT INTO staff_snowflake_repairs("
                                "table_name,column_name,old_id,repaired_id,status,source,"
                                "rows_changed,checked_ts) VALUES(?,?,?,?,?,?,?,?) "
                                "ON CONFLICT(table_name,column_name,old_id) DO UPDATE SET "
                                "repaired_id=excluded.repaired_id,status=excluded.status,"
                                "source=excluded.source,rows_changed=excluded.rows_changed,"
                                "checked_ts=excluded.checked_ts",
                                (
                                    table,
                                    column,
                                    str(stored),
                                    str(exact),
                                    "conflict" if remaining_count else "repaired",
                                    "turso_libsql_f64_backfill",
                                    changed,
                                    int(time.time()),
                                ),
                            )
                            batch_result["audited"] += 1
                    self._commit_and_sync_sync()
                except Exception:
                    try:
                        self._conn.rollback()
                    except Exception:
                        pass
                    raise
                return batch_result

            batch_result = await self._run_locked_with_retry(
                _run_batch,
                retry_operation=True,
                operation_name="snowflake_repair",
                queue_timeout=2.0,
                operation_label="maintenance.snowflake_repair",
            )
            for key in ("updated", "conflicts", "feedback_requeued", "audited"):
                result[key] += int(batch_result.get(key, 0) or 0)
            await asyncio.sleep(0)
        return result

    def health_snapshot(self) -> dict[str, Any]:
        return {
            "connected": self._conn is not None and self._ready,
            "uses_remote": self.uses_remote,
            "remote_dirty": self._remote_dirty,
            "replica_refresh_pending": self._remote_dirty,
            "remote_reconnect_required": self._remote_reconnect_required,
            "replica_rebuild_required": self._replica_rebuild_required,
            "last_remote_sync_error": self._last_remote_sync_error,
            "last_remote_sync_error_ts": self._last_remote_sync_error_ts,
            "replica_rebuild_count": self._replica_rebuild_count,
            "last_replica_rebuild_ts": self._last_replica_rebuild_ts,
            "last_replica_rebuild_reason": self._last_replica_rebuild_reason,
            "query_timing": self.query_timing_snapshot(),
            "isolated_worker": isinstance(self._conn, IsolatedConnection),
            "worker_alive": self._conn.alive if isinstance(self._conn, IsolatedConnection) else None,
            "waiting_operations": self._waiting_operations,
            "active_operation": self._active_operation,
            "active_operation_task": self._active_operation_task,
            "active_operation_seconds": round(time.monotonic() - self._active_operation_since, 2) if self._active_operation_since else 0,
            "queue_timeouts": self._queue_timeouts,
            "primary_write_degraded": self._primary_write_degraded,
            "last_operation_error_ts": self._last_operation_error_ts,
            "read_waiting": self._read_waiting,
            "last_queue_timeout_ts": self._last_queue_timeout_ts,
            "last_queue_timeout_operation": self._last_queue_timeout_operation,
            "background_queue_deferrals": self._background_queue_deferrals,
            "write_queue_stalled": bool(self._active_operation_since and time.monotonic() - self._active_operation_since >= self._queue_timeout_seconds),
        }

    async def sync_remote(self) -> bool:
        """Pull the latest remote primary state into the embedded replica."""
        if not self.uses_remote:
            return True

        def _run() -> bool:
            self._sync_remote_with_retry_sync()
            self._remote_dirty = False
            self._remote_reconnect_required = False
            self._replica_rebuild_required = False
            self._remote_sync_retry_after = 0.0
            self._last_remote_sync_error = ""
            self._last_remote_sync_error_ts = 0
            return True

        return bool(await self._run_locked_with_retry(_run, operation_name="remote_sync"))

    async def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        items = list(seq)
        await self.execute_transaction([(sql, params) for params in items])

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> Optional[Any]:
        if self.uses_remote:
            await self.connect()
            return await self._read_replica(sql, params, many=False, timeout=5)
        def _run():
            assert self._conn is not None
            cur = self._execute_sync(sql, params)
            row = cur.fetchone()
            if row is None and self.uses_remote:
                legacy_params = _legacy_where_params(sql, params)
                if legacy_params is not None and legacy_params != tuple(params):
                    cur = self._conn.execute(sql, self._adapt_params(legacy_params))
                    row = cur.fetchone()
            return _normalize_row(cur, row)

        return await self._run_locked_with_retry(_run, operation_name="fetchone")

    async def fetchone_local(self, sql: str, params: Sequence[Any] = ()) -> Optional[Any]:
        """Read current replica state without waiting for a pending remote sync.

        This short-deadline variant serves interactions and health probes.
        Standard remote reads also use snapshots, with a longer deadline.
        """

        if not self._ready:
            return None

        return await self._read_replica(sql, params, many=False, timeout=0.75)

    async def fetchall_local(self, sql: str, params: Sequence[Any] = ()) -> List[Any]:
        if not self._ready:
            raise DatabaseBusyError("Replica is not initialized; no snapshot read was started")
        return await self._read_replica(sql, params, many=True, timeout=5)

    async def _read_replica(self, sql, params, *, many: bool, timeout: float):
        deadline = time.monotonic() + timeout
        def read_snapshot():
            with closing(sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=0.2)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA query_only=ON")
                connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
                cursor = connection.execute(sql, tuple(params))
                result = cursor.fetchall() if many else cursor.fetchone()
                if (not result if many else result is None) and self.uses_remote:
                    legacy = _legacy_where_params(sql, params)
                    if legacy is not None and legacy != tuple(params):
                        cursor = connection.execute(sql, legacy)
                        result = cursor.fetchall() if many else cursor.fetchone()
                return result

        started = time.perf_counter()
        task = None
        acquired = False
        self._read_waiting += 1
        try:
            async with asyncio.timeout(timeout):
                await self._read_slots.acquire()
                acquired = True
                task = asyncio.create_task(asyncio.to_thread(read_snapshot))
                result = await asyncio.shield(task)
        except TimeoutError as exc:
            self._record_query_timing("replica_read", (time.perf_counter() - started) * 1000, failed=True)
            raise DatabaseBusyError("Replica read deadline exceeded; the write queue was not used") from exc
        except Exception as exc:
            if self.uses_remote and _is_replica_corruption_error(exc):
                self._ready = False
                self._replica_rebuild_required = True
            self._record_query_timing("replica_read", (time.perf_counter() - started) * 1000, failed=True)
            if isinstance(exc, sqlite3.OperationalError) and str(exc) == "interrupted" and time.monotonic() >= deadline:
                raise DatabaseBusyError("Replica read deadline exceeded; the write queue was not used") from exc
            raise
        finally:
            self._read_waiting -= 1
            if acquired:
                if task is not None and not task.done():
                    def release(completed):
                        if not completed.cancelled():
                            completed.exception()
                        self._read_slots.release()
                    task.add_done_callback(release)
                else:
                    self._read_slots.release()
        self._record_query_timing("replica_read", (time.perf_counter() - started) * 1000, failed=False)
        return result

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> List[Any]:
        if self.uses_remote:
            await self.connect()
            return await self._read_replica(sql, params, many=True, timeout=5)
        def _run():
            assert self._conn is not None
            cur = self._execute_sync(sql, params)
            rows = cur.fetchall()
            if not rows and self.uses_remote:
                legacy_params = _legacy_where_params(sql, params)
                if legacy_params is not None and legacy_params != tuple(params):
                    cur = self._conn.execute(sql, self._adapt_params(legacy_params))
                    rows = cur.fetchall()
            return _normalize_rows(cur, rows)

        return await self._run_locked_with_retry(_run, operation_name="fetchall")
