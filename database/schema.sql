CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_key text UNIQUE,
    workflow_name text NOT NULL,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    priority integer NOT NULL DEFAULT 0,
    input jsonb NOT NULL DEFAULT '{}'::jsonb,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_type text,
    error_message text,
    started_at timestamptz,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE accounts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    platform text NOT NULL,
    account_handle text NOT NULL,
    status text NOT NULL DEFAULT 'healthy'
        CHECK (status IN ('healthy', 'limited', 'banned', 'disabled')),
    proxy_url text,
    rate_limit_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    last_used_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (platform, account_handle)
);

CREATE TABLE tasks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    parent_task_id uuid REFERENCES tasks(id) ON DELETE SET NULL,
    account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
    task_type text NOT NULL,
    action_type text,
    status text NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'READY', 'RUNNING', 'RETRY', 'SUCCESS', 'FAILED', 'CANCELED')),
    priority integer NOT NULL DEFAULT 0,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    result jsonb,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    retry_count integer NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    max_retries integer NOT NULL DEFAULT 3 CHECK (max_retries >= 1),
    next_run_at timestamptz NOT NULL DEFAULT now(),
    next_retry_at timestamptz,
    idempotency_key text,
    error_type text,
    error_message text,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (parent_task_id IS NULL OR parent_task_id <> id)
);

CREATE TABLE task_executions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    worker_id text NOT NULL CHECK (length(worker_id) > 0),
    attempt_number integer NOT NULL CHECK (attempt_number >= 1),
    status text NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'succeeded', 'failed', 'timed_out')),
    heartbeat_at timestamptz NOT NULL DEFAULT now(),
    lease_expires_at timestamptz NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    result jsonb,
    error_type text,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (task_id, attempt_number)
);

CREATE TABLE task_dependencies (
    task_id uuid NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    depends_on_task_id uuid NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (task_id, depends_on_task_id),
    CHECK (task_id <> depends_on_task_id)
);

CREATE TABLE policy_rules (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id uuid REFERENCES accounts(id) ON DELETE CASCADE,
    platform text,
    action_type text NOT NULL,
    rule_name text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    config jsonb NOT NULL DEFAULT '{}'::jsonb,
    cooldown_seconds integer NOT NULL DEFAULT 0 CHECK (cooldown_seconds >= 0),
    max_actions integer CHECK (max_actions IS NULL OR max_actions >= 0),
    window_seconds integer CHECK (window_seconds IS NULL OR window_seconds > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (account_id, platform, action_type, rule_name)
);

CREATE TABLE action_logs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid REFERENCES jobs(id) ON DELETE SET NULL,
    task_id uuid REFERENCES tasks(id) ON DELETE SET NULL,
    execution_id uuid REFERENCES task_executions(id) ON DELETE SET NULL,
    account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
    platform text,
    action_type text NOT NULL,
    status text NOT NULL
        CHECK (status IN ('attempted', 'succeeded', 'failed', 'blocked', 'skipped')),
    request jsonb NOT NULL DEFAULT '{}'::jsonb,
    response jsonb,
    error_type text,
    error_message text,
    duration_ms integer CHECK (duration_ms IS NULL OR duration_ms >= 0),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE artifacts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid REFERENCES jobs(id) ON DELETE CASCADE,
    task_id uuid REFERENCES tasks(id) ON DELETE CASCADE,
    execution_id uuid REFERENCES task_executions(id) ON DELETE SET NULL,
    artifact_type text NOT NULL
        CHECK (artifact_type IN ('video', 'image', 'audio', 'metadata', 'file', 'log')),
    storage_uri text NOT NULL,
    mime_type text,
    size_bytes bigint CHECK (size_bytes IS NULL OR size_bytes >= 0),
    checksum text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
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

