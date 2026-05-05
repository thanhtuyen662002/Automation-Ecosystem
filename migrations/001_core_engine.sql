CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS engine_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_name text NOT NULL CHECK (length(task_name) > 0),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key text NULL,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'canceled')),
    priority integer NOT NULL DEFAULT 0,
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts integer NOT NULL DEFAULT 5 CHECK (max_attempts >= 1),
    timeout_seconds integer NOT NULL DEFAULT 300 CHECK (timeout_seconds >= 1),
    next_run_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz NULL,
    last_error_type text NULL,
    last_error_message text NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS engine_jobs_idempotency_key_uidx
    ON engine_jobs (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS engine_jobs_ready_idx
    ON engine_jobs (status, next_run_at, priority DESC, created_at ASC)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS engine_jobs_status_updated_idx
    ON engine_jobs (status, updated_at);

CREATE TABLE IF NOT EXISTS engine_executions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL REFERENCES engine_jobs(id) ON DELETE CASCADE,
    worker_id text NOT NULL CHECK (length(worker_id) > 0),
    attempt integer NOT NULL CHECK (attempt >= 1),
    status text NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'succeeded', 'failed', 'timed_out')),
    started_at timestamptz NOT NULL DEFAULT now(),
    heartbeat_at timestamptz NOT NULL DEFAULT now(),
    lease_expires_at timestamptz NOT NULL,
    completed_at timestamptz NULL,
    result jsonb NULL,
    error_type text NULL,
    error_message text NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS engine_executions_job_attempt_uidx
    ON engine_executions (job_id, attempt);

CREATE INDEX IF NOT EXISTS engine_executions_running_lease_idx
    ON engine_executions (lease_expires_at)
    WHERE status = 'running';

