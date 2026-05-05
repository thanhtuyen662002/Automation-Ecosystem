CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS automation_tasks (
    task_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_type text NOT NULL CHECK (length(task_type) > 0),
    status text NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'READY', 'RUNNING', 'RETRY', 'SUCCESS', 'FAILED')),
    data jsonb NOT NULL DEFAULT '{}'::jsonb,
    result jsonb NULL,
    retry_count integer NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    max_retries integer NOT NULL DEFAULT 3 CHECK (max_retries >= 0),
    next_run_at timestamptz NOT NULL DEFAULT now(),
    next_retry_at timestamptz NULL,
    worker_id text NULL,
    locked_by text NULL,
    last_heartbeat timestamptz NULL,
    started_at timestamptz NULL,
    completed_at timestamptz NULL,
    error_type text NULL,
    error text NULL,
    execution_hash text NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE automation_tasks
    ADD COLUMN IF NOT EXISTS worker_id text NULL;

ALTER TABLE automation_tasks
    ADD COLUMN IF NOT EXISTS last_heartbeat timestamptz NULL;

ALTER TABLE automation_tasks
    ADD COLUMN IF NOT EXISTS next_retry_at timestamptz NULL;

ALTER TABLE automation_tasks
    ADD COLUMN IF NOT EXISTS execution_hash text NULL;

ALTER TABLE automation_tasks
    DROP CONSTRAINT IF EXISTS automation_tasks_status_check;

ALTER TABLE automation_tasks
    ADD CONSTRAINT automation_tasks_status_check
    CHECK (status IN ('PENDING', 'READY', 'RUNNING', 'RETRY', 'SUCCESS', 'FAILED'));

CREATE UNIQUE INDEX IF NOT EXISTS automation_tasks_execution_hash_uidx
    ON automation_tasks (execution_hash)
    WHERE execution_hash IS NOT NULL;

CREATE INDEX IF NOT EXISTS automation_tasks_status_next_run_idx
    ON automation_tasks (status, next_run_at, created_at);

CREATE INDEX IF NOT EXISTS automation_tasks_running_heartbeat_idx
    ON automation_tasks (last_heartbeat)
    WHERE status = 'RUNNING';

CREATE INDEX IF NOT EXISTS automation_tasks_type_status_idx
    ON automation_tasks (task_type, status);
