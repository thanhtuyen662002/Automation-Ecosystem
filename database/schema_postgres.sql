-- ============================================================
-- Automation Ecosystem — PostgreSQL Schema
-- Converted from schema.sql (SQLite → PostgreSQL)
--
-- Key differences vs SQLite version:
--   DATETIME       → TIMESTAMPTZ
--   TEXT (JSON)    → JSONB
--   INTEGER (bool) → BOOLEAN
--   REAL           → DOUBLE PRECISION
--   length()       → char_length()
--   CURRENT_TIMESTAMP → NOW()
-- ============================================================

CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    job_key         TEXT UNIQUE,
    workflow_name   TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    priority        INTEGER NOT NULL DEFAULT 0,
    input           JSONB NOT NULL DEFAULT '{}',
    metadata        JSONB NOT NULL DEFAULT '{}',
    error_type      TEXT,
    error_message   TEXT,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS accounts (
    id              TEXT PRIMARY KEY,
    platform        TEXT NOT NULL,
    account_handle  TEXT NOT NULL,
    profile_url     TEXT,
    external_user_id TEXT,
    status          TEXT NOT NULL DEFAULT 'healthy'
                        CHECK (status IN ('healthy', 'limited', 'banned', 'disabled')),
    proxy_url       TEXT,
    rate_limit_config JSONB NOT NULL DEFAULT '{}',
    metadata        JSONB NOT NULL DEFAULT '{}',
    last_used_at    TIMESTAMPTZ,
    avatar_url      TEXT,
    display_name    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (platform, account_handle)
);

CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    task_key        TEXT NOT NULL,
    job_id          TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    parent_task_id  TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    account_id      TEXT REFERENCES accounts(id) ON DELETE SET NULL,
    task_type       TEXT NOT NULL,
    action_type     TEXT,
    status          TEXT NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN ('PENDING', 'READY', 'RUNNING', 'RETRY', 'SUCCESS', 'FAILED', 'CANCELED')),
    priority        INTEGER NOT NULL DEFAULT 0,
    payload         JSONB NOT NULL DEFAULT '{}',
    result          JSONB,
    metadata        JSONB NOT NULL DEFAULT '{}',
    retry_count     INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    max_retries     INTEGER NOT NULL DEFAULT 3 CHECK (max_retries >= 1),
    next_run_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    next_retry_at   TIMESTAMPTZ,
    idempotency_key TEXT,
    error_type      TEXT,
    error_message   TEXT,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (parent_task_id IS NULL OR parent_task_id <> id),
    UNIQUE (job_id, task_key)
);

CREATE TABLE IF NOT EXISTS task_executions (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    worker_id       TEXT NOT NULL CHECK (char_length(worker_id) > 0),
    attempt_number  INTEGER NOT NULL CHECK (attempt_number >= 1),
    status          TEXT NOT NULL DEFAULT 'running'
                        CHECK (status IN ('running', 'succeeded', 'failed', 'timed_out')),
    heartbeat_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_expires_at TIMESTAMPTZ NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    result          JSONB,
    error_type      TEXT,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (task_id, attempt_number)
);

CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id             TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    depends_on_task_id  TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (task_id, depends_on_task_id),
    CHECK (task_id <> depends_on_task_id)
);

CREATE TABLE IF NOT EXISTS policy_rules (
    id               TEXT PRIMARY KEY,
    account_id       TEXT REFERENCES accounts(id) ON DELETE CASCADE,
    platform         TEXT,
    action_type      TEXT NOT NULL,
    rule_name        TEXT NOT NULL,
    enabled          BOOLEAN NOT NULL DEFAULT TRUE,
    config           JSONB NOT NULL DEFAULT '{}',
    cooldown_seconds INTEGER NOT NULL DEFAULT 0 CHECK (cooldown_seconds >= 0),
    max_actions      INTEGER CHECK (max_actions IS NULL OR max_actions >= 0),
    window_seconds   INTEGER CHECK (window_seconds IS NULL OR window_seconds > 0),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (account_id, platform, action_type, rule_name)
);

CREATE TABLE IF NOT EXISTS action_logs (
    id            TEXT PRIMARY KEY,
    job_id        TEXT REFERENCES jobs(id) ON DELETE SET NULL,
    task_id       TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    execution_id  TEXT REFERENCES task_executions(id) ON DELETE SET NULL,
    account_id    TEXT REFERENCES accounts(id) ON DELETE SET NULL,
    platform      TEXT,
    action_type   TEXT NOT NULL,
    status        TEXT NOT NULL
                      CHECK (status IN ('attempted', 'succeeded', 'failed', 'blocked', 'skipped')),
    request       JSONB NOT NULL DEFAULT '{}',
    response      JSONB,
    error_type    TEXT,
    error_message TEXT,
    duration_ms   INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS artifacts (
    id            TEXT PRIMARY KEY,
    job_id        TEXT REFERENCES jobs(id) ON DELETE CASCADE,
    task_id       TEXT REFERENCES tasks(id) ON DELETE CASCADE,
    execution_id  TEXT REFERENCES task_executions(id) ON DELETE SET NULL,
    artifact_type TEXT NOT NULL
                      CHECK (artifact_type IN ('video', 'image', 'audio', 'metadata', 'file', 'log')),
    status        TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'approved', 'rejected')),
    storage_uri   TEXT NOT NULL,
    mime_type     TEXT,
    size_bytes    BIGINT CHECK (size_bytes IS NULL OR size_bytes >= 0),
    checksum      TEXT,
    metadata      JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS video_metrics (
    id                TEXT PRIMARY KEY,
    video_id          TEXT NOT NULL,
    views             INTEGER NOT NULL DEFAULT 0,
    likes             INTEGER NOT NULL DEFAULT 0,
    comments          INTEGER NOT NULL DEFAULT 0,
    shares            INTEGER NOT NULL DEFAULT 0,
    watch_time        DOUBLE PRECISION,
    retention_rate    DOUBLE PRECISION,
    hook_text         TEXT,
    template_type     TEXT,
    video_length      DOUBLE PRECISION,
    effect_types      TEXT,
    keyword           TEXT,
    product_type      TEXT,
    posted_at         TIMESTAMPTZ NOT NULL,
    collected_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    view_velocity     DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    performance_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Indexes ──────────────────────────────────────────────────────────────────

CREATE UNIQUE INDEX IF NOT EXISTS tasks_idempotency_key_uidx
    ON tasks (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS task_executions_one_running_per_task_uidx
    ON task_executions (task_id)
    WHERE status = 'running';

CREATE INDEX IF NOT EXISTS jobs_status_priority_idx
    ON jobs (status, priority DESC, created_at ASC);

CREATE INDEX IF NOT EXISTS tasks_ready_schedule_idx
    ON tasks (status, next_run_at, priority DESC, created_at ASC)
    WHERE status = 'READY';

CREATE INDEX IF NOT EXISTS tasks_retry_schedule_idx
    ON tasks (status, next_retry_at, priority DESC)
    WHERE status = 'RETRY';

CREATE INDEX IF NOT EXISTS tasks_job_status_idx
    ON tasks (job_id, status);

CREATE INDEX IF NOT EXISTS tasks_account_action_status_idx
    ON tasks (account_id, action_type, status);

CREATE INDEX IF NOT EXISTS tasks_parent_task_idx
    ON tasks (parent_task_id);

CREATE INDEX IF NOT EXISTS task_executions_running_lease_idx
    ON task_executions (lease_expires_at)
    WHERE status = 'running';

CREATE INDEX IF NOT EXISTS task_executions_task_attempt_idx
    ON task_executions (task_id, attempt_number DESC);

CREATE INDEX IF NOT EXISTS task_executions_worker_status_idx
    ON task_executions (worker_id, status, heartbeat_at DESC);

CREATE INDEX IF NOT EXISTS task_dependencies_task_idx
    ON task_dependencies (task_id);

CREATE INDEX IF NOT EXISTS task_dependencies_depends_on_idx
    ON task_dependencies (depends_on_task_id);

CREATE INDEX IF NOT EXISTS accounts_platform_status_idx
    ON accounts (platform, status);

CREATE INDEX IF NOT EXISTS accounts_last_used_idx
    ON accounts (platform, last_used_at);

CREATE INDEX IF NOT EXISTS accounts_profile_url_idx
    ON accounts (profile_url)
    WHERE profile_url IS NOT NULL;

CREATE INDEX IF NOT EXISTS policy_rules_lookup_idx
    ON policy_rules (account_id, platform, action_type, enabled);

CREATE INDEX IF NOT EXISTS action_logs_task_idx
    ON action_logs (task_id, created_at DESC);

CREATE INDEX IF NOT EXISTS action_logs_execution_idx
    ON action_logs (execution_id, created_at DESC);

CREATE INDEX IF NOT EXISTS action_logs_account_action_time_idx
    ON action_logs (account_id, action_type, created_at DESC);

CREATE INDEX IF NOT EXISTS artifacts_job_idx
    ON artifacts (job_id, created_at DESC);

CREATE INDEX IF NOT EXISTS artifacts_task_idx
    ON artifacts (task_id, created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS artifacts_storage_uri_uidx
    ON artifacts (storage_uri);

CREATE INDEX IF NOT EXISTS artifacts_execution_idx
    ON artifacts (execution_id, created_at DESC);

CREATE INDEX IF NOT EXISTS artifacts_checksum_idx
    ON artifacts (checksum)
    WHERE checksum IS NOT NULL;

CREATE INDEX IF NOT EXISTS artifacts_status_idx
    ON artifacts (status, artifact_type);

CREATE INDEX IF NOT EXISTS video_metrics_video_id_idx   ON video_metrics (video_id);
CREATE INDEX IF NOT EXISTS video_metrics_collected_at_idx ON video_metrics (collected_at DESC);
CREATE INDEX IF NOT EXISTS video_metrics_keyword_idx    ON video_metrics (keyword);
