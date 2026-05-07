CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    job_key TEXT UNIQUE,
    workflow_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    priority INTEGER NOT NULL DEFAULT 0,
    input TEXT NOT NULL DEFAULT '{}',
    metadata TEXT NOT NULL DEFAULT '{}',
    error_type TEXT,
    error_message TEXT,
    started_at DATETIME,
    completed_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE accounts (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    account_handle TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'healthy'
        CHECK (status IN ('healthy', 'limited', 'banned', 'disabled')),
    proxy_url TEXT,
    rate_limit_config TEXT NOT NULL DEFAULT '{}',
    metadata TEXT NOT NULL DEFAULT '{}',
    last_used_at DATETIME,
    -- Session storage (added in migration 001)
    cookies TEXT,               -- Fernet-encrypted JSON list of cookie dicts
    user_agent TEXT,            -- Browser user-agent string used during login
    last_login_at DATETIME,     -- When the session was last captured
    session_valid INTEGER NOT NULL DEFAULT 0,  -- 1 = valid, 0 = expired/not connected
    -- Browser fingerprint (added in migration 002) — stable identity per account
    viewport_width INTEGER NOT NULL DEFAULT 1280,
    viewport_height INTEGER NOT NULL DEFAULT 720,
    timezone TEXT NOT NULL DEFAULT 'America/New_York',
    locale TEXT NOT NULL DEFAULT 'en-US',
    -- Risk tracking (added in migration 003)
    browser_data_dir TEXT,                        -- Path to persistent Chromium profile dir
    risk_score REAL NOT NULL DEFAULT 0.0,         -- 0.0–1.0; >= 0.7 = auto-pause
    failed_publish_count INTEGER NOT NULL DEFAULT 0,
    captcha_hit_count INTEGER NOT NULL DEFAULT 0,
    login_redirect_count INTEGER NOT NULL DEFAULT 0,
    -- Proxy health + warmup + soft-ban (added in migration 004)
    proxy_country TEXT,                           -- ISO-3166-1 alpha-2, e.g. "VN"
    proxy_latency_ms INTEGER,                     -- Last TCP latency check in ms
    proxy_validated_at DATETIME,                  -- When proxy was last validated
    warmup_sessions_completed INTEGER NOT NULL DEFAULT 0,  -- View sessions before first publish
    soft_ban_detected INTEGER NOT NULL DEFAULT 0,          -- 1 = shadow-ban signals detected

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (platform, account_handle)
);

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    task_key TEXT NOT NULL,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    parent_task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    account_id TEXT REFERENCES accounts(id) ON DELETE SET NULL,
    task_type TEXT NOT NULL,
    action_type TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'READY', 'RUNNING', 'RETRY', 'SUCCESS', 'FAILED', 'CANCELED')),
    priority INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL DEFAULT '{}',
    result TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    max_retries INTEGER NOT NULL DEFAULT 3 CHECK (max_retries >= 1),
    next_run_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    next_retry_at DATETIME,
    idempotency_key TEXT,
    error_type TEXT,
    error_message TEXT,
    completed_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (parent_task_id IS NULL OR parent_task_id <> id),
    UNIQUE (job_id, task_key)
);

CREATE TABLE task_executions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    worker_id TEXT NOT NULL CHECK (length(worker_id) > 0),
    attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1),
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'succeeded', 'failed', 'timed_out')),
    heartbeat_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lease_expires_at DATETIME NOT NULL,
    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME,
    result TEXT,
    error_type TEXT,
    error_message TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (task_id, attempt_number)
);

CREATE TABLE task_dependencies (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    depends_on_task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (task_id, depends_on_task_id),
    CHECK (task_id <> depends_on_task_id)
);

CREATE TABLE policy_rules (
    id TEXT PRIMARY KEY,
    account_id TEXT REFERENCES accounts(id) ON DELETE CASCADE,
    platform TEXT,
    action_type TEXT NOT NULL,
    rule_name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    config TEXT NOT NULL DEFAULT '{}',
    cooldown_seconds INTEGER NOT NULL DEFAULT 0 CHECK (cooldown_seconds >= 0),
    max_actions INTEGER CHECK (max_actions IS NULL OR max_actions >= 0),
    window_seconds INTEGER CHECK (window_seconds IS NULL OR window_seconds > 0),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (account_id, platform, action_type, rule_name)
);

CREATE TABLE action_logs (
    id TEXT PRIMARY KEY,
    job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
    task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    execution_id TEXT REFERENCES task_executions(id) ON DELETE SET NULL,
    account_id TEXT REFERENCES accounts(id) ON DELETE SET NULL,
    platform TEXT,
    action_type TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('attempted', 'succeeded', 'failed', 'blocked', 'skipped')),
    request TEXT NOT NULL DEFAULT '{}',
    response TEXT,
    error_type TEXT,
    error_message TEXT,
    duration_ms INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Migration (existing databases): ALTER TABLE artifacts ADD COLUMN status TEXT NOT NULL DEFAULT 'pending';
CREATE TABLE artifacts (
    id TEXT PRIMARY KEY,
    job_id TEXT REFERENCES jobs(id) ON DELETE CASCADE,
    task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE,
    execution_id TEXT REFERENCES task_executions(id) ON DELETE SET NULL,
    artifact_type TEXT NOT NULL
        CHECK (artifact_type IN ('video', 'image', 'audio', 'metadata', 'file', 'log')),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    storage_uri TEXT NOT NULL,
    mime_type TEXT,
    size_bytes INTEGER CHECK (size_bytes IS NULL OR size_bytes >= 0),
    checksum TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX tasks_idempotency_key_uidx
    ON tasks (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE UNIQUE INDEX task_executions_one_running_per_task_uidx
    ON task_executions (task_id)
    WHERE status = 'running';

CREATE INDEX jobs_status_priority_idx
    ON jobs (status, priority DESC, created_at ASC);

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

CREATE INDEX task_executions_running_lease_idx
    ON task_executions (lease_expires_at)
    WHERE status = 'running';

CREATE INDEX task_executions_task_attempt_idx
    ON task_executions (task_id, attempt_number DESC);

CREATE INDEX task_executions_worker_status_idx
    ON task_executions (worker_id, status, heartbeat_at DESC);

CREATE INDEX task_dependencies_task_idx
    ON task_dependencies (task_id);

CREATE INDEX task_dependencies_depends_on_idx
    ON task_dependencies (depends_on_task_id);

CREATE INDEX accounts_platform_status_idx
    ON accounts (platform, status);

CREATE INDEX accounts_last_used_idx
    ON accounts (platform, last_used_at);

CREATE INDEX policy_rules_lookup_idx
    ON policy_rules (account_id, platform, action_type, enabled);

CREATE INDEX action_logs_task_idx
    ON action_logs (task_id, created_at DESC);

CREATE INDEX action_logs_execution_idx
    ON action_logs (execution_id, created_at DESC);

CREATE INDEX action_logs_account_action_time_idx
    ON action_logs (account_id, action_type, created_at DESC);

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

CREATE TABLE video_metrics (
    id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL,
    views INTEGER NOT NULL DEFAULT 0,
    likes INTEGER NOT NULL DEFAULT 0,
    comments INTEGER NOT NULL DEFAULT 0,
    shares INTEGER NOT NULL DEFAULT 0,
    watch_time REAL,
    retention_rate REAL,
    hook_text TEXT,
    template_type TEXT,
    video_length REAL,
    effect_types TEXT,
    keyword TEXT,
    product_type TEXT,
    posted_at DATETIME NOT NULL,
    collected_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    view_velocity REAL NOT NULL DEFAULT 0.0,
    performance_score REAL NOT NULL DEFAULT 0.0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX video_metrics_video_id_idx ON video_metrics (video_id);
CREATE INDEX video_metrics_collected_at_idx ON video_metrics (collected_at DESC);
CREATE INDEX video_metrics_keyword_idx ON video_metrics (keyword);
