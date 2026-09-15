"""Process isolation for the native libSQL driver, which retains Python's GIL."""
from __future__ import annotations

import multiprocessing
import os
import time
from typing import Any


class DatabaseWorkerError(RuntimeError):
    pass


class DatabaseWorkerTimeout(DatabaseWorkerError):
    """A worker deadline expired; a write's completion must not be assumed."""


def _worker_main(pipe, path: str, remote_url: str, token: str) -> None:
    import libsql

    connection = None
    try:
        while True:
            request = pipe.recv()
            action, args = request
            try:
                if action == "open":
                    options = {"sync_url": remote_url, "auth_token": token} if remote_url else {}
                    connection = libsql.connect(path, **options)
                    result = None
                elif action == "close":
                    if connection is not None:
                        connection.close()
                    pipe.send((True, None))
                    return
                elif connection is None:
                    raise RuntimeError("database worker connection is not open")
                elif action in {"execute", "executescript"}:
                    cursor = getattr(connection, action)(*args)
                    description = getattr(cursor, "description", None)
                    result = {
                        "description": description,
                        "rows": cursor.fetchall() if description else [],
                        "rowcount": getattr(cursor, "rowcount", -1),
                        "lastrowid": getattr(cursor, "lastrowid", None),
                    }
                elif action in {"commit", "rollback", "sync"}:
                    result = getattr(connection, action)()
                else:
                    raise ValueError("unsupported database worker operation")
                pipe.send((True, result))
            except Exception as exc:
                pipe.send((False, (type(exc).__name__, str(exc))))
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        pipe.close()


class BufferedCursor:
    """Only materialized data crosses the process boundary, never driver cursors."""

    def __init__(self, result: dict[str, Any]):
        self.description = result.get("description")
        self.rowcount = result.get("rowcount", -1)
        self.lastrowid = result.get("lastrowid")
        self._rows = list(result.get("rows") or [])
        self._offset = 0

    def fetchone(self):
        if self._offset >= len(self._rows):
            return None
        row = self._rows[self._offset]
        self._offset += 1
        return row

    def fetchall(self):
        rows = self._rows[self._offset:]
        self._offset = len(self._rows)
        return rows


class IsolatedConnection:
    """A synchronous DB-API facade suitable for asyncio.to_thread.

    Pipe waits release the parent's GIL. A hung native call can be terminated
    without closing a connection still being used by another parent thread.
    """

    def __init__(self, path: str, *, sync_url: str = "", auth_token: str = "", timeout: float | None = None):
        try:
            configured = float(os.getenv("TURSO_WORKER_TIMEOUT_SECONDS", "20"))
        except ValueError:
            configured = 20.0
        self.timeout = max(0.1, min(120.0, configured if timeout is None else timeout))
        self._deadline = time.monotonic() + 90
        context = multiprocessing.get_context("spawn")
        self._pipe, child_pipe = context.Pipe()
        self._process = context.Process(
            target=_worker_main,
            args=(child_pipe, path, sync_url, auth_token),
            name="avenue-guard-turso",
            daemon=True,
        )
        self._closed = False
        self._process.start()
        child_pipe.close()
        try:
            self._call("open")
        except BaseException:
            self.terminate()
            raise

    @property
    def alive(self) -> bool:
        return not self._closed and self._process.is_alive()

    @property
    def pid(self) -> int | None:
        return self._process.pid

    def set_deadline(self, seconds: float = 30) -> None:
        self._deadline = time.monotonic() + max(0.1, float(seconds))

    def _call(self, action: str, *args):
        if not self.alive:
            raise DatabaseWorkerError("Turso worker is unavailable; reconnect required")
        remaining = min(self.timeout, self._deadline - time.monotonic())
        if remaining <= 0:
            self.terminate()
            raise DatabaseWorkerTimeout("Turso worker timed out; operation completion is unknown")
        try:
            self._pipe.send((action, args))
            if not self._pipe.poll(remaining):
                self.terminate()
                raise DatabaseWorkerTimeout("Turso worker timed out; operation completion is unknown")
            successful, result = self._pipe.recv()
        except (EOFError, BrokenPipeError, OSError) as exc:
            self.terminate()
            raise DatabaseWorkerError("Turso worker exited; operation completion is unknown") from exc
        if not successful:
            name, message = result
            if name == "ValueError":
                raise ValueError(message)
            raise DatabaseWorkerError(f"{name}: {message}")
        return result

    def execute(self, sql: str, parameters=()) -> BufferedCursor:
        return BufferedCursor(self._call("execute", sql, tuple(parameters)))

    def executescript(self, script: str) -> BufferedCursor:
        return BufferedCursor(self._call("executescript", script))

    def commit(self) -> None:
        self._call("commit")

    def rollback(self) -> None:
        self._call("rollback")

    def sync(self) -> None:
        self._call("sync")

    def close(self) -> None:
        try:
            if self.alive:
                self._call("close")
        finally:
            self.terminate()

    def terminate(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._pipe.close()
        if self._process.is_alive():
            self._process.terminate()
        self._process.join(timeout=2)
        if self._process.is_alive():
            self._process.kill()
            self._process.join(timeout=2)
