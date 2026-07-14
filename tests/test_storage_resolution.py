import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main import PersistenceConfigurationError, _install_storage_close_hook, resolve_db_path
from utils.config import Config


def write_config(tmp_path, *, require_remote=True):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "database": {
                    "turso_url": "libsql://example.turso.io",
                    "turso_replica_path": str(tmp_path / "replica.db"),
                    "require_remote_when_configured": require_remote,
                    "path": "",
                }
            }
        ),
        encoding="utf-8",
    )
    return Config(str(path))


def clear_database_environment(monkeypatch):
    for name in (
        "TURSO_DATABASE_URL",
        "LIBSQL_URL",
        "TURSO_AUTH_TOKEN",
        "LIBSQL_AUTH_TOKEN",
        "TURSO_REPLICA_PATH",
        "AVENUE_GUARD_DB_PATH",
        "ALLOW_LOCAL_DATABASE_FALLBACK",
    ):
        monkeypatch.delenv(name, raising=False)


def test_configured_remote_refuses_silent_local_fallback(tmp_path, monkeypatch):
    clear_database_environment(monkeypatch)
    config = write_config(tmp_path)

    with pytest.raises(PersistenceConfigurationError, match="TURSO_AUTH_TOKEN is missing"):
        resolve_db_path(config)


def test_local_development_can_explicitly_allow_fallback(tmp_path, monkeypatch):
    clear_database_environment(monkeypatch)
    config = write_config(tmp_path)
    local_path = tmp_path / "local.db"
    monkeypatch.setenv("ALLOW_LOCAL_DATABASE_FALLBACK", "1")
    monkeypatch.setenv("AVENUE_GUARD_DB_PATH", str(local_path))

    path, source, warning, remote_url, token = resolve_db_path(config)

    assert path == str(local_path)
    assert source == "AVENUE_GUARD_DB_PATH"
    assert "TURSO_AUTH_TOKEN is missing" in warning
    assert remote_url == ""
    assert token == ""


def test_valid_remote_configuration_selects_embedded_replica(tmp_path, monkeypatch):
    clear_database_environment(monkeypatch)
    config = write_config(tmp_path)
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "database-token")

    path, source, warning, remote_url, token = resolve_db_path(config)

    assert path == str(tmp_path / "replica.db")
    assert source == "Turso/libSQL embedded replica"
    assert warning == ""
    assert remote_url == "libsql://example.turso.io"
    assert token == "database-token"


@pytest.mark.asyncio
async def test_shutdown_flushes_once_on_the_discord_event_loop():
    tracking = SimpleNamespace(flush_activity_counts=AsyncMock())
    background = SimpleNamespace(_persist_current_day=AsyncMock())
    original_close = AsyncMock()
    database = SimpleNamespace(sync_remote=AsyncMock(), close=AsyncMock())
    cogs = {"TrackingCog": tracking, "BackgroundCog": background}
    bot = SimpleNamespace(
        close=original_close,
        db=database,
        get_cog=lambda name: cogs.get(name),
    )

    _install_storage_close_hook(bot)
    await bot.close()
    await bot.close()

    tracking.flush_activity_counts.assert_awaited_once()
    background._persist_current_day.assert_awaited_once()
    database.sync_remote.assert_awaited_once()
    database.close.assert_awaited_once()
    assert original_close.await_count == 2
