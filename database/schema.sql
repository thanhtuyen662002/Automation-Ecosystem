-- ═══════════════════════════════════════════════════════════════════════════════
-- schema.sql — Canonical DB schema (fresh install)
-- Includes all migrations 001–009. Run this on a NEW database only.
-- For existing databases, run scripts/migrate_009.py instead.
-- ═══════════════════════════════════════════════════════════════════════════════

-- ── Jobs ──────────────────────────────────────────────────────────────────────
CREATE TABLE jobs (
    id            TEXT PRIMARY KEY,
    job_key       TEXT UNIQUE,
    workflow_name TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    priority      INTEGER NOT NULL DEFAULT 0,
    input         TEXT NOT NULL DEFAULT '{}',
    metadata      TEXT NOT NULL DEFAULT '{}',
    error_type    TEXT,
    error_message TEXT,
    started_at    DATETIME,
    completed_at  DATETIME,
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ── Accounts ──────────────────────────────────────────────────────────────────
CREATE TABLE accounts (
    id          TEXT PRIMARY KEY,
    platform    TEXT NOT NULL,
    account_handle TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'healthy'
        CHECK (status IN ('healthy', 'limited', 'banned', 'disabled')),
    proxy_url   TEXT,
    rate_limit_config TEXT NOT NULL DEFAULT '{}',
    metadata    TEXT NOT NULL DEFAULT '{}',
    last_used_at DATETIME,

    -- Session storage (migration 001)
    cookies         TEXT,               -- Fernet-encrypted JSON list of cookie dicts
    user_agent      TEXT,               -- Browser user-agent string used during login
    last_login_at   DATETIME,           -- When the session was last captured
    session_valid   INTEGER NOT NULL DEFAULT 0,  -- 1 = valid, 0 = expired/not connected

    -- Browser fingerprint (migration 002) — stable identity per account
    viewport_width  INTEGER NOT NULL DEFAULT 1280,
    viewport_height INTEGER NOT NULL DEFAULT 720,
    timezone        TEXT NOT NULL DEFAULT 'America/New_York',
    locale          TEXT NOT NULL DEFAULT 'en-US',

    -- Risk tracking (migration 003)
    browser_data_dir       TEXT,                        -- Path to persistent Chromium profile dir
    risk_score             REAL NOT NULL DEFAULT 0.0,   -- 0.0–1.0; >= 0.7 = auto-pause
    failed_publish_count   INTEGER NOT NULL DEFAULT 0,
    captcha_hit_count      INTEGER NOT NULL DEFAULT 0,
    login_redirect_count   INTEGER NOT NULL DEFAULT 0,

    -- Proxy health + warmup + soft-ban (migration 004)
    proxy_country              TEXT,                    -- ISO-3166-1 alpha-2, e.g. "VN"
    proxy_latency_ms           INTEGER,                 -- Last TCP latency check in ms
    proxy_validated_at         DATETIME,                -- When proxy was last validated
    warmup_sessions_completed  INTEGER NOT NULL DEFAULT 0,  -- View sessions before first publish
    soft_ban_detected          INTEGER NOT NULL DEFAULT 0,  -- 1 = shadow-ban signals detected

    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (platform, account_handle)
);

-- ── Tasks ─────────────────────────────────────────────────────────────────────
CREATE TABLE tasks (
    id             TEXT PRIMARY KEY,
    task_key       TEXT NOT NULL,
    job_id         TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    parent_task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    account_id     TEXT REFERENCES accounts(id) ON DELETE SET NULL,
    task_type      TEXT NOT NULL,
    action_type    TEXT,
    status         TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'READY', 'RUNNING', 'RETRY', 'SUCCESS', 'FAILED', 'CANCELED')),
    priority       INTEGER NOT NULL DEFAULT 0,
    payload        TEXT NOT NULL DEFAULT '{}',
    result         TEXT,
    metadata       TEXT NOT NULL DEFAULT '{}',
    retry_count    INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    max_retries    INTEGER NOT NULL DEFAULT 3 CHECK (max_retries >= 1),
    next_run_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    next_retry_at  DATETIME,
    idempotency_key TEXT,
    error_type     TEXT,
    error_message  TEXT,
    completed_at   DATETIME,
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (parent_task_id IS NULL OR parent_task_id <> id),
    UNIQUE (job_id, task_key)
);

-- ── Task Executions ───────────────────────────────────────────────────────────
CREATE TABLE task_executions (
    id             TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    worker_id      TEXT NOT NULL CHECK (length(worker_id) > 0),
    attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1),
    status         TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'succeeded', 'failed', 'timed_out')),
    heartbeat_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lease_expires_at DATETIME NOT NULL,
    started_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at   DATETIME,
    result         TEXT,
    error_type     TEXT,
    error_message  TEXT,
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (task_id, attempt_number)
);

-- ── Task Dependencies ─────────────────────────────────────────────────────────
CREATE TABLE task_dependencies (
    task_id          TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    depends_on_task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (task_id, depends_on_task_id),
    CHECK (task_id <> depends_on_task_id)
);

-- ── Policy Rules ──────────────────────────────────────────────────────────────
CREATE TABLE policy_rules (
    id               TEXT PRIMARY KEY,
    account_id       TEXT REFERENCES accounts(id) ON DELETE CASCADE,
    platform         TEXT,
    action_type      TEXT NOT NULL,
    rule_name        TEXT NOT NULL,
    enabled          INTEGER NOT NULL DEFAULT 1,
    config           TEXT NOT NULL DEFAULT '{}',
    cooldown_seconds INTEGER NOT NULL DEFAULT 0 CHECK (cooldown_seconds >= 0),
    max_actions      INTEGER CHECK (max_actions IS NULL OR max_actions >= 0),
    window_seconds   INTEGER CHECK (window_seconds IS NULL OR window_seconds > 0),
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (account_id, platform, action_type, rule_name)
);

-- ── Action Logs ───────────────────────────────────────────────────────────────
CREATE TABLE action_logs (
    id           TEXT PRIMARY KEY,
    job_id       TEXT REFERENCES jobs(id) ON DELETE SET NULL,
    task_id      TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    execution_id TEXT REFERENCES task_executions(id) ON DELETE SET NULL,
    account_id   TEXT REFERENCES accounts(id) ON DELETE SET NULL,
    platform     TEXT,
    action_type  TEXT NOT NULL,
    status       TEXT NOT NULL
        CHECK (status IN ('attempted', 'succeeded', 'failed', 'blocked', 'skipped')),
    request      TEXT NOT NULL DEFAULT '{}',
    response     TEXT,
    error_type   TEXT,
    error_message TEXT,
    duration_ms  INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ── Artifacts ─────────────────────────────────────────────────────────────────
CREATE TABLE artifacts (
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
    size_bytes    INTEGER CHECK (size_bytes IS NULL OR size_bytes >= 0),
    checksum      TEXT,
    metadata      TEXT NOT NULL DEFAULT '{}',
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ── Video Metrics ─────────────────────────────────────────────────────────────
CREATE TABLE video_metrics (
    id               TEXT PRIMARY KEY,
    video_id         TEXT NOT NULL,
    views            INTEGER NOT NULL DEFAULT 0,
    likes            INTEGER NOT NULL DEFAULT 0,
    comments         INTEGER NOT NULL DEFAULT 0,
    shares           INTEGER NOT NULL DEFAULT 0,
    watch_time       REAL,
    retention_rate   REAL,
    hook_text        TEXT,
    template_type    TEXT,
    video_length     REAL,
    effect_types     TEXT,
    keyword          TEXT,
    product_type     TEXT,
    posted_at        DATETIME NOT NULL,
    collected_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    view_velocity    REAL NOT NULL DEFAULT 0.0,
    performance_score REAL NOT NULL DEFAULT 0.0,
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ── Global Memory (migration 005) ─────────────────────────────────────────────
-- Cross-account environmental awareness (ban waves, fleet-wide risk signals).
CREATE TABLE global_banned_fingerprints (
    id               TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    fingerprint_hash TEXT NOT NULL,
    reason           TEXT NOT NULL DEFAULT '',
    source_count     INTEGER NOT NULL DEFAULT 1,
    created_at       REAL NOT NULL DEFAULT (unixepoch()),
    expires_at       REAL NOT NULL
);

CREATE TABLE global_risk_events (
    id         TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    event_type TEXT NOT NULL CHECK (event_type IN ('ban', 'captcha', 'soft_block', 'high_risk')),
    account_id TEXT NOT NULL,
    risk_score REAL NOT NULL CHECK (risk_score BETWEEN 0.0 AND 1.0),
    created_at REAL NOT NULL DEFAULT (unixepoch()),
    expires_at REAL NOT NULL
);

CREATE TABLE global_stats (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '{}',   -- JSON
    updated_at REAL NOT NULL DEFAULT (unixepoch())
);

-- ═══════════════════════════════════════════════════════════════════════════════
-- Auth & Authorization (migrations 006–009)
-- ═══════════════════════════════════════════════════════════════════════════════

-- ── Licenses (migrations 006 + 007 + 009) ────────────────────────────────────
-- Admin issues keys via `python scripts/generate_license.py` or the admin UI.
-- Each key is bound to exactly one machine_id on first login.
CREATE TABLE licenses (
    id             TEXT PRIMARY KEY,
    license_key    TEXT UNIQUE NOT NULL,        -- AE-XXXX-XXXX-XXXX
    label          TEXT,                         -- Human-readable name, e.g. "Khách hàng A"
    machine_id     TEXT,                         -- Hardware fingerprint (NULL = not yet activated)
    activated_at   DATETIME,                     -- First activation timestamp
    expires_at     DATETIME,                     -- NULL = never expires
    is_active      INTEGER NOT NULL DEFAULT 1,   -- 1 = valid, 0 = revoked

    -- Authorization (migration 009)
    role           TEXT NOT NULL DEFAULT 'operator'
        CHECK (role IN ('operator', 'admin', 'viewer')),
    max_accounts   INTEGER NOT NULL DEFAULT 10
        CHECK (max_accounts > 0),               -- Max TikTok accounts per license

    -- IP tracking + anomaly detection (migration 007)
    last_ip        TEXT,                         -- IP address of last login
    last_seen_at   DATETIME,                     -- Timestamp of last successful login
    flagged        INTEGER NOT NULL DEFAULT 0,   -- 1 = IP anomaly detected, login blocked
    flagged_reason TEXT,                         -- Human-readable flag reason

    notes          TEXT,
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ── Sessions (migrations 007 + 009) ──────────────────────────────────────────
-- One row per issued token. On new login → old session revoked.
-- License revoke → all sessions for that key set revoked = 1.
CREATE TABLE sessions (
    id            TEXT PRIMARY KEY,             -- session UUID
    license_key   TEXT NOT NULL,
    machine_fp    TEXT NOT NULL,               -- server-computed fingerprint
    ip            TEXT,
    account       TEXT,                        -- operator username (migration 009)
    token_hash    TEXT NOT NULL,               -- SHA-256 of the issued token
    issued_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at    DATETIME NOT NULL,
    revoked       INTEGER NOT NULL DEFAULT 0,  -- 1 = invalidated
    revoke_reason TEXT,                        -- logout / refreshed / new_login / license_revoked
    FOREIGN KEY (license_key) REFERENCES licenses (license_key) ON DELETE CASCADE
);

-- ── Login Attempts (migrations 007 + 009) ────────────────────────────────────
-- DB-backed rate limiting — persists across server restarts.
CREATE TABLE login_attempts (
    id           TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    ip           TEXT NOT NULL,
    license_key  TEXT,                         -- NULL until key is validated (migration 009)
    attempted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ── License Audit Events (migration 007) ─────────────────────────────────────
CREATE TABLE license_events (
    id          TEXT PRIMARY KEY,
    license_key TEXT,
    event_type  TEXT NOT NULL,  -- login_ok, login_fail, revoked, reset_machine, ip_anomaly, admin_create
    ip          TEXT,
    machine_fp  TEXT,
    detail      TEXT,           -- JSON blob with extra context
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════════════════════════════════════════
-- Indexes
-- ═══════════════════════════════════════════════════════════════════════════════

-- jobs
CREATE INDEX jobs_status_priority_idx
    ON jobs (status, priority DESC, created_at ASC);

-- tasks
CREATE UNIQUE INDEX tasks_idempotency_key_uidx
    ON tasks (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX tasks_ready_schedule_idx
    ON tasks (status, next_run_at, priority DESC, created_at ASC)
    WHERE status = 'READY';

CREATE INDEX tasks_retry_schedule_idx
    ON tasks (status, next_retry_at, priority DESC)
    WHERE status = 'RETRY';

CREATE INDEX tasks_job_status_idx
    ON tasks (job_id, status);

CREATE INDEX tasks_account_action_status_idx
    ON tasks (account_id, action_type, status);

CREATE INDEX tasks_parent_task_idx
    ON tasks (parent_task_id);

-- task_executions
CREATE UNIQUE INDEX task_executions_one_running_per_task_uidx
    ON task_executions (task_id)
    WHERE status = 'running';

CREATE INDEX task_executions_running_lease_idx
    ON task_executions (lease_expires_at)
    WHERE status = 'running';

CREATE INDEX task_executions_task_attempt_idx
    ON task_executions (task_id, attempt_number DESC);

CREATE INDEX task_executions_worker_status_idx
    ON task_executions (worker_id, status, heartbeat_at DESC);

-- task_dependencies
CREATE INDEX task_dependencies_task_idx
    ON task_dependencies (task_id);

CREATE INDEX task_dependencies_depends_on_idx
    ON task_dependencies (depends_on_task_id);

-- accounts
CREATE INDEX accounts_platform_status_idx
    ON accounts (platform, status);

CREATE INDEX accounts_last_used_idx
    ON accounts (platform, last_used_at);

-- policy_rules
CREATE INDEX policy_rules_lookup_idx
    ON policy_rules (account_id, platform, action_type, enabled);

-- action_logs
CREATE INDEX action_logs_task_idx
    ON action_logs (task_id, created_at DESC);

CREATE INDEX action_logs_execution_idx
    ON action_logs (execution_id, created_at DESC);

CREATE INDEX action_logs_account_action_time_idx
    ON action_logs (account_id, action_type, created_at DESC);

-- artifacts
CREATE INDEX artifacts_job_idx
    ON artifacts (job_id, created_at DESC);

CREATE INDEX artifacts_task_idx
    ON artifacts (task_id, created_at DESC);

CREATE INDEX artifacts_execution_idx
    ON artifacts (execution_id, created_at DESC);

CREATE INDEX artifacts_checksum_idx
    ON artifacts (checksum)
    WHERE checksum IS NOT NULL;

CREATE INDEX artifacts_status_idx
    ON artifacts (status, artifact_type);

-- video_metrics
CREATE INDEX video_metrics_video_id_idx    ON video_metrics (video_id);
CREATE INDEX video_metrics_collected_at_idx ON video_metrics (collected_at DESC);
CREATE INDEX video_metrics_keyword_idx     ON video_metrics (keyword);

-- global_memory
CREATE UNIQUE INDEX global_banned_fingerprints_hash_uidx
    ON global_banned_fingerprints (fingerprint_hash);

CREATE INDEX global_banned_fingerprints_expires_idx
    ON global_banned_fingerprints (expires_at);

CREATE INDEX global_risk_events_type_time_idx
    ON global_risk_events (event_type, created_at DESC);

CREATE INDEX global_risk_events_expires_idx
    ON global_risk_events (expires_at);

-- licenses
CREATE UNIQUE INDEX licenses_key_uidx
    ON licenses (license_key);

CREATE INDEX licenses_machine_idx
    ON licenses (machine_id)
    WHERE machine_id IS NOT NULL;

CREATE INDEX licenses_active_idx
    ON licenses (is_active, expires_at);

CREATE INDEX licenses_role_idx
    ON licenses (role, is_active);

-- sessions
CREATE INDEX sessions_key_idx
    ON sessions (license_key, revoked);

CREATE INDEX sessions_token_idx
    ON sessions (token_hash);

CREATE INDEX sessions_expires_idx
    ON sessions (expires_at)
    WHERE revoked = 0;

-- login_attempts
CREATE INDEX login_attempts_ip_time_idx
    ON login_attempts (ip, attempted_at);

CREATE INDEX login_attempts_key_time_idx
    ON login_attempts (license_key, attempted_at)
    WHERE license_key IS NOT NULL;

-- license_events
CREATE INDEX license_events_key_idx
    ON license_events (license_key, created_at DESC);

CREATE INDEX license_events_type_idx
    ON license_events (event_type, created_at DESC);
