from __future__ import annotations

STAFF_PORTAL_TABLES = {
    "staff_web_sessions",
    "staff_queue_claims",
    "staff_queue_claim_events",
    "staff_outreach_episodes",
    "staff_tasks",
    "staff_notes",
    "staff_review_qa",
    "staff_applications",
    "staff_application_cooldowns",
    "staff_application_events",
    "staff_application_notes",
    "staff_members",
    "staff_portal_profiles",
    "staff_portal_nickname_history",
    "staff_milestones",
    "staff_idempotency",
    "staff_snowflake_repairs",
}


STAFF_PORTAL_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS staff_web_sessions(
        token_hash TEXT PRIMARY KEY,
        guild_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        display_name TEXT NOT NULL,
        avatar_url TEXT NOT NULL DEFAULT '',
        role_key TEXT NOT NULL,
        csrf_hash TEXT NOT NULL,
        created_ts INTEGER NOT NULL,
        last_seen_ts INTEGER NOT NULL,
        expires_ts INTEGER NOT NULL,
        revoked_ts INTEGER
    );""",
    """CREATE TABLE IF NOT EXISTS staff_queue_claims(
        queue_id INTEGER PRIMARY KEY,
        guild_id INTEGER NOT NULL,
        claimed_by INTEGER NOT NULL,
        claimed_ts INTEGER NOT NULL,
        claim_state TEXT NOT NULL DEFAULT 'active',
        released_by INTEGER,
        released_ts INTEGER,
        updated_ts INTEGER NOT NULL
    );""",
    """CREATE TABLE IF NOT EXISTS staff_queue_claim_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        queue_id INTEGER NOT NULL,
        actor_id INTEGER NOT NULL,
        event TEXT NOT NULL,
        previous_owner_id INTEGER,
        new_owner_id INTEGER,
        reason TEXT NOT NULL DEFAULT '',
        created_ts INTEGER NOT NULL,
        correlation_id TEXT NOT NULL
    );""",
    """CREATE TABLE IF NOT EXISTS staff_outreach_episodes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        queue_id INTEGER NOT NULL,
        episode_number INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        started_by INTEGER NOT NULL,
        started_ts INTEGER NOT NULL,
        ended_by INTEGER,
        ended_ts INTEGER,
        reason TEXT NOT NULL DEFAULT '',
        outcome TEXT,
        UNIQUE(queue_id, episode_number)
    );""",
    """CREATE TABLE IF NOT EXISTS staff_tasks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        task_type TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        priority TEXT NOT NULL DEFAULT 'normal',
        status TEXT NOT NULL DEFAULT 'todo',
        created_by INTEGER NOT NULL,
        assignee_id INTEGER,
        created_ts INTEGER NOT NULL,
        updated_ts INTEGER NOT NULL,
        due_ts INTEGER,
        completed_ts INTEGER,
        linked_entity_type TEXT,
        linked_entity_id TEXT,
        system_key TEXT
    );""",
    """CREATE TABLE IF NOT EXISTS staff_notes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        author_id INTEGER NOT NULL,
        scope TEXT NOT NULL,
        body TEXT NOT NULL,
        entity_type TEXT,
        entity_id TEXT,
        created_ts INTEGER NOT NULL,
        updated_ts INTEGER NOT NULL,
        archived_ts INTEGER
    );""",
    """CREATE TABLE IF NOT EXISTS staff_review_qa(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        request_message_id INTEGER NOT NULL,
        reviewer_id INTEGER,
        qa_status TEXT NOT NULL DEFAULT 'unreviewed',
        original_send_type TEXT,
        adjusted_send_type TEXT,
        qa_by INTEGER,
        reason TEXT NOT NULL DEFAULT '',
        created_ts INTEGER NOT NULL,
        updated_ts INTEGER NOT NULL,
        UNIQUE(guild_id, request_message_id)
    );""",
    """CREATE TABLE IF NOT EXISTS staff_applications(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        applicant_id INTEGER NOT NULL,
        application_type TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft',
        answers_json TEXT NOT NULL DEFAULT '{}',
        claimed_by INTEGER,
        created_ts INTEGER NOT NULL,
        updated_ts INTEGER NOT NULL,
        submitted_ts INTEGER,
        decided_by INTEGER,
        decided_ts INTEGER,
        decision_reason TEXT NOT NULL DEFAULT '',
        role_outbox_id INTEGER,
        review_prompt_key TEXT,
        review_thread_outbox_id INTEGER,
        review_thread_id INTEGER,
        interview_ticket_outbox_id INTEGER,
        interview_ticket_channel_id INTEGER
    );""",
    """CREATE TABLE IF NOT EXISTS staff_application_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id INTEGER NOT NULL,
        actor_id INTEGER NOT NULL,
        event TEXT NOT NULL,
        from_status TEXT,
        to_status TEXT,
        detail_json TEXT NOT NULL DEFAULT '{}',
        created_ts INTEGER NOT NULL,
        correlation_id TEXT NOT NULL
    );""",
    """CREATE TABLE IF NOT EXISTS staff_application_cooldowns(
        guild_id INTEGER NOT NULL,
        applicant_id INTEGER NOT NULL,
        application_type TEXT NOT NULL,
        cooldown_until_ts INTEGER NOT NULL,
        source TEXT NOT NULL DEFAULT 'application_data_reset',
        created_ts INTEGER NOT NULL,
        updated_ts INTEGER NOT NULL,
        PRIMARY KEY(guild_id,applicant_id,application_type)
    );""",
    """CREATE TABLE IF NOT EXISTS staff_application_notes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id INTEGER NOT NULL,
        author_id INTEGER NOT NULL,
        body TEXT NOT NULL,
        created_ts INTEGER NOT NULL,
        updated_ts INTEGER NOT NULL
    );""",
    """CREATE TABLE IF NOT EXISTS staff_members(
        guild_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        desired_role TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        updated_by INTEGER NOT NULL,
        updated_ts INTEGER NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        role_outbox_id INTEGER,
        PRIMARY KEY(guild_id, user_id)
    );""",
    """CREATE TABLE IF NOT EXISTS staff_portal_profiles(
        guild_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        portal_nickname TEXT NOT NULL DEFAULT '',
        updated_ts INTEGER NOT NULL,
        updated_by INTEGER NOT NULL,
        PRIMARY KEY(guild_id, user_id)
    );""",
    """CREATE TABLE IF NOT EXISTS staff_portal_nickname_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        actor_id INTEGER NOT NULL,
        old_nickname TEXT NOT NULL DEFAULT '',
        new_nickname TEXT NOT NULL DEFAULT '',
        reason TEXT NOT NULL DEFAULT '',
        created_ts INTEGER NOT NULL,
        correlation_id TEXT NOT NULL
    );""",
    """CREATE TABLE IF NOT EXISTS staff_milestones(
        guild_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        milestone_key TEXT NOT NULL,
        achieved_ts INTEGER NOT NULL,
        PRIMARY KEY(guild_id, user_id, milestone_key)
    );""",
    """CREATE TABLE IF NOT EXISTS staff_idempotency(
        idempotency_key TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        operation TEXT NOT NULL,
        response_json TEXT NOT NULL,
        created_ts INTEGER NOT NULL,
        expires_ts INTEGER NOT NULL
    );""",
    """CREATE TABLE IF NOT EXISTS staff_snowflake_repairs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        table_name TEXT NOT NULL,
        column_name TEXT NOT NULL,
        old_id TEXT NOT NULL,
        repaired_id TEXT,
        status TEXT NOT NULL,
        source TEXT NOT NULL,
        rows_changed INTEGER NOT NULL DEFAULT 0,
        checked_ts INTEGER NOT NULL,
        UNIQUE(table_name,column_name,old_id)
    );""",
    """CREATE INDEX IF NOT EXISTS idx_staff_sessions_user
        ON staff_web_sessions(guild_id,user_id,expires_ts DESC);""",
    """CREATE INDEX IF NOT EXISTS idx_staff_sessions_expiry
        ON staff_web_sessions(expires_ts,revoked_ts);""",
    """CREATE INDEX IF NOT EXISTS idx_staff_claims_owner_state
        ON staff_queue_claims(guild_id,claimed_by,claim_state,claimed_ts);""",
    """CREATE INDEX IF NOT EXISTS idx_staff_claim_events_queue
        ON staff_queue_claim_events(guild_id,queue_id,created_ts DESC);""",
    """CREATE INDEX IF NOT EXISTS idx_staff_episodes_queue
        ON staff_outreach_episodes(guild_id,queue_id,episode_number DESC);""",
    """CREATE INDEX IF NOT EXISTS idx_staff_tasks_assignee
        ON staff_tasks(guild_id,assignee_id,status,due_ts);""",
    """CREATE INDEX IF NOT EXISTS idx_staff_tasks_status
        ON staff_tasks(guild_id,status,priority,due_ts);""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_staff_tasks_system_key
        ON staff_tasks(guild_id,system_key) WHERE system_key IS NOT NULL;""",
    """CREATE INDEX IF NOT EXISTS idx_staff_notes_scope
        ON staff_notes(guild_id,scope,author_id,updated_ts DESC);""",
    """CREATE INDEX IF NOT EXISTS idx_staff_notes_entity
        ON staff_notes(guild_id,entity_type,entity_id,updated_ts DESC);""",
    """CREATE INDEX IF NOT EXISTS idx_staff_qa_status
        ON staff_review_qa(guild_id,qa_status,updated_ts DESC);""",
    """CREATE INDEX IF NOT EXISTS idx_staff_applicant_status
        ON staff_applications(guild_id,applicant_id,status,updated_ts DESC);""",
    """CREATE INDEX IF NOT EXISTS idx_staff_applications_review
        ON staff_applications(guild_id,application_type,status,submitted_ts);""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_staff_applications_one_active
        ON staff_applications(guild_id,applicant_id,application_type)
        WHERE status IN('draft','submitted','under_review','interview','hold','accepted_pending_role');""",
    """CREATE INDEX IF NOT EXISTS idx_staff_application_cooldowns_expiry
        ON staff_application_cooldowns(cooldown_until_ts);""",
    """CREATE INDEX IF NOT EXISTS idx_staff_application_events
        ON staff_application_events(application_id,created_ts);""",
    """CREATE INDEX IF NOT EXISTS idx_staff_idempotency_expiry
        ON staff_idempotency(expires_ts);""",
    """CREATE INDEX IF NOT EXISTS idx_staff_nickname_history_user
        ON staff_portal_nickname_history(guild_id,user_id,created_ts DESC);""",
    """CREATE INDEX IF NOT EXISTS idx_staff_snowflake_repairs_status
        ON staff_snowflake_repairs(status,checked_ts DESC);""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_outreach_submission_target
        ON level_outreach_attempts(episode_id,queue_id,private_target_key)
        WHERE status='submitted_to_mod' AND episode_id IS NOT NULL AND private_target_key!='';""",
)
