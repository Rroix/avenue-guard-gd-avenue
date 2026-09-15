"""Additive, isolated audit storage; no foreign-key writes to live workflows."""

AUDIT_TABLES = {
    "historical_audit_runs", "historical_audit_requests", "historical_audit_levels",
    "historical_audit_creators", "historical_level_audit_snapshots",
    "historical_creator_audit_snapshots", "historical_prestige_labels",
}

AUDIT_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS historical_audit_runs(
        run_id TEXT PRIMARY KEY, guild_id INTEGER NOT NULL, owner_id TEXT NOT NULL,
        started_ts INTEGER NOT NULL, completed_ts INTEGER, status TEXT NOT NULL,
        config_json TEXT NOT NULL, summary_json TEXT, error_text TEXT,
        delivery_status TEXT NOT NULL DEFAULT 'pending',
        lease_owner TEXT, lease_until_ts INTEGER NOT NULL DEFAULT 0)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_historical_audit_active
        ON historical_audit_runs((1)) WHERE status IN ('queued','running')""",
    """CREATE TABLE IF NOT EXISTS historical_audit_requests(
        run_id TEXT NOT NULL, guild_id INTEGER NOT NULL, wave_id INTEGER NOT NULL,
        requester_id TEXT NOT NULL, level_id TEXT NOT NULL, request_message_id TEXT,
        created_ts INTEGER NOT NULL, reviewed_ts INTEGER, reviewed_by TEXT,
        status TEXT NOT NULL, historical_result TEXT, review_text TEXT,
        PRIMARY KEY(run_id,guild_id,wave_id,requester_id))""",
    """CREATE TABLE IF NOT EXISTS historical_audit_levels(
        run_id TEXT NOT NULL, level_id TEXT NOT NULL, snapshot_json TEXT,
        PRIMARY KEY(run_id,level_id))""",
    """CREATE TABLE IF NOT EXISTS historical_audit_creators(
        run_id TEXT NOT NULL, account_id TEXT NOT NULL, snapshot_json TEXT,
        PRIMARY KEY(run_id,account_id))""",
    """CREATE TABLE IF NOT EXISTS historical_level_audit_snapshots(
        level_id TEXT PRIMARY KEY, checked_ts INTEGER NOT NULL,
        expires_ts INTEGER NOT NULL, data_json TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS historical_creator_audit_snapshots(
        account_id TEXT PRIMARY KEY, checked_ts INTEGER NOT NULL,
        expires_ts INTEGER NOT NULL, data_json TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS historical_prestige_labels(
        evidence_id TEXT PRIMARY KEY, imported_ts INTEGER NOT NULL,
        level_id TEXT NOT NULL, wave_id INTEGER, request_date TEXT,
        prestige TEXT NOT NULL, source TEXT NOT NULL, source_detail TEXT NOT NULL)""",
]
