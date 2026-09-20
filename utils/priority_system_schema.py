from __future__ import annotations

PRIORITY_TABLES = {
    "level_outreach_queue",
    "level_outreach_cycles",
    "level_outreach_cycle_entries",
    "level_outreach_attempts",
    "level_outreach_cp_snapshots",
    "level_outreach_level_snapshots",
}


PRIORITY_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS level_outreach_queue(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        wave_id INTEGER NOT NULL,
        requester_id INTEGER NOT NULL,
        request_message_id INTEGER NOT NULL,
        level_id TEXT NOT NULL,
        send_type TEXT NOT NULL,
        queued_ts INTEGER NOT NULL,
        prestige_t REAL NOT NULL,
        prestige_component_f REAL NOT NULL,
        uploader_name TEXT,
        uploader_user_id INTEGER,
        uploader_account_id INTEGER,
        creator_points_at_recommendation INTEGER,
        creator_points_checked_ts INTEGER,
        current_creator_points INTEGER,
        current_creator_points_checked_ts INTEGER,
        creator_points_refresh_after_ts INTEGER,
        creator_component_g REAL,
        waiting_cycles INTEGER NOT NULL DEFAULT 0,
        waiting_component_h REAL NOT NULL DEFAULT 0,
        priority_points REAL,
        priority_complete INTEGER NOT NULL DEFAULT 0,
        model_version TEXT NOT NULL,
        queue_state TEXT NOT NULL DEFAULT 'queued',
        hidden_from_state TEXT,
        submitted_to_mod_ts INTEGER,
        rated_observed_ts INTEGER,
        outcome_window_due_ts INTEGER,
        rated_within_window INTEGER,
        outcome_window_completed_ts INTEGER,
        current_exists INTEGER,
        current_rated INTEGER,
        current_stars INTEGER,
        current_level_name TEXT,
        level_checked_ts INTEGER,
        level_refresh_after_ts INTEGER,
        correlation_id TEXT NOT NULL,
        updated_ts INTEGER NOT NULL,
        UNIQUE(guild_id, wave_id, requester_id),
        UNIQUE(guild_id, request_message_id)
    );""",
    """CREATE TABLE IF NOT EXISTS level_outreach_cycles(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        started_by INTEGER NOT NULL,
        started_ts INTEGER NOT NULL,
        completed_by INTEGER,
        completed_ts INTEGER,
        successful INTEGER,
        notes TEXT NOT NULL DEFAULT '',
        correlation_id TEXT NOT NULL UNIQUE
    );""",
    """CREATE TABLE IF NOT EXISTS level_outreach_cycle_entries(
        cycle_id INTEGER NOT NULL,
        queue_id INTEGER NOT NULL,
        priority_points_snapshot REAL,
        priority_complete_snapshot INTEGER NOT NULL,
        prestige_component_f_snapshot REAL NOT NULL,
        creator_component_g_snapshot REAL,
        waiting_component_h_snapshot REAL NOT NULL,
        creator_points_snapshot INTEGER,
        waiting_cycles_snapshot INTEGER NOT NULL,
        model_version_snapshot TEXT NOT NULL,
        selected INTEGER NOT NULL DEFAULT 0,
        submitted_to_mod INTEGER NOT NULL DEFAULT 0,
        waiting_incremented INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(cycle_id, queue_id)
    );""",
    """CREATE TABLE IF NOT EXISTS level_outreach_attempts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cycle_id INTEGER NOT NULL,
        queue_id INTEGER NOT NULL,
        actor_id INTEGER NOT NULL,
        status TEXT NOT NULL,
        route_type TEXT NOT NULL,
        private_notes TEXT NOT NULL DEFAULT '',
        private_target_label TEXT NOT NULL DEFAULT '',
        created_ts INTEGER NOT NULL,
        correlation_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL UNIQUE
    );""",
    """CREATE TABLE IF NOT EXISTS level_outreach_cp_snapshots(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        queue_id INTEGER NOT NULL,
        account_id INTEGER,
        checked_ts INTEGER NOT NULL,
        creator_points INTEGER,
        lookup_status TEXT NOT NULL,
        error_text TEXT,
        source TEXT NOT NULL DEFAULT 'boomlings',
        actor_id INTEGER,
        reason TEXT
    );""",
    """CREATE TABLE IF NOT EXISTS level_outreach_level_snapshots(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        queue_id INTEGER NOT NULL,
        checked_ts INTEGER NOT NULL,
        current_exists INTEGER,
        current_rated INTEGER,
        stars INTEGER,
        uploader_name TEXT,
        uploader_user_id INTEGER,
        uploader_account_id INTEGER,
        lookup_status TEXT NOT NULL,
        error_text TEXT
    );""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_outreach_cycle_active
        ON level_outreach_cycles(guild_id) WHERE status='active';""",
    """CREATE INDEX IF NOT EXISTS idx_outreach_queue_rank
        ON level_outreach_queue(guild_id, queue_state, priority_complete, priority_points DESC, waiting_cycles DESC, queued_ts, id);""",
    """CREATE INDEX IF NOT EXISTS idx_outreach_queue_level
        ON level_outreach_queue(guild_id, level_id, queued_ts DESC);""",
    """CREATE INDEX IF NOT EXISTS idx_outreach_queue_account
        ON level_outreach_queue(guild_id, uploader_account_id, current_creator_points_checked_ts);""",
    """CREATE INDEX IF NOT EXISTS idx_outreach_cycle_entries_queue
        ON level_outreach_cycle_entries(queue_id, cycle_id);""",
    """CREATE INDEX IF NOT EXISTS idx_outreach_attempts_cycle_queue
        ON level_outreach_attempts(cycle_id, queue_id, created_ts);""",
    """CREATE INDEX IF NOT EXISTS idx_outreach_cp_snapshots_queue
        ON level_outreach_cp_snapshots(queue_id, checked_ts DESC);""",
    """CREATE INDEX IF NOT EXISTS idx_outreach_level_snapshots_queue
        ON level_outreach_level_snapshots(queue_id, checked_ts DESC);""",
)
