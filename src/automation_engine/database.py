from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from automation_engine.config import EngineSettings
from automation_engine.models import ExecutionRecord, ExecutionStatus, JobRecord, JobStatus, utc_now


class JobNotFoundError(LookupError):
    pass


class DatabaseStore:
    def __init__(self, settings: EngineSettings) -> None:
        self._settings = settings
        self._pool = ConnectionPool(
            conninfo=settings.database_url,
            min_size=settings.postgres_pool_min_size,
            max_size=settings.postgres_pool_max_size,
            kwargs={"row_factory": dict_row},
            open=False,
        )

    def open(self) -> None:
        self._pool.open(wait=True)

    def close(self) -> None:
        self._pool.close()

    @contextmanager
    def connection(self) -> Iterator[Connection[Any]]:
        with self._pool.connection() as conn:
            yield conn

    def run_migrations(self, migrations_dir: Path | None = None) -> None:
        directory = migrations_dir or Path(__file__).resolve().parents[2] / "migrations"
        for migration in sorted(directory.glob("*.sql")):
            sql = migration.read_text(encoding="utf-8")
            with self.connection() as conn:
                with conn.transaction():
                    conn.execute(sql)

    def enqueue_job(
        self,
        task_name: str,
        payload: dict[str, Any],
        idempotency_key: str | None,
        priority: int,
        timeout_seconds: int,
        max_attempts: int,
    ) -> JobRecord:
        with self.connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    INSERT INTO engine_jobs (
                        task_name, payload, idempotency_key, priority, timeout_seconds,
                        max_attempts, next_run_at
                    )
                    VALUES (%s, %s::jsonb, %s, %s, %s, %s, now())
                    ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL
                    DO UPDATE SET updated_at = engine_jobs.updated_at
                    RETURNING *
                    """,
                    (task_name, Jsonb(payload), idempotency_key, priority, timeout_seconds, max_attempts),
                ).fetchone()
                return self._job(row)

    def get_job(self, job_id: UUID) -> JobRecord:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM engine_jobs WHERE id = %s", (job_id,)).fetchone()
            if row is None:
                raise JobNotFoundError(f"Job not found: {job_id}")
            return self._job(row)

    def cancel_job(self, job_id: UUID) -> bool:
        with self.connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    UPDATE engine_jobs
                    SET status = 'canceled', completed_at = now(), updated_at = now()
                    WHERE id = %s AND status IN ('pending', 'running')
                    RETURNING *
                    """,
                    (job_id,),
                ).fetchone()
                return row is not None

    def acquire_job(
        self, job_id: UUID, worker_id: str, lease_timeout_seconds: int
    ) -> tuple[JobRecord, ExecutionRecord] | None:
        with self.connection() as conn:
            with conn.transaction():
                job = conn.execute(
                    """
                    SELECT *
                    FROM engine_jobs
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (job_id,),
                ).fetchone()
                if job is None:
                    return None
                record = self._job(job)
                if record.status != JobStatus.PENDING or record.next_run_at > utc_now():
                    return None
                attempt = record.attempts + 1
                updated_job = conn.execute(
                    """
                    UPDATE engine_jobs
                    SET status = 'running', attempts = %s, updated_at = now()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (attempt, job_id),
                ).fetchone()
                execution = conn.execute(
                    """
                    INSERT INTO engine_executions (
                        job_id, worker_id, attempt, status, lease_expires_at
                    )
                    VALUES (%s, %s, %s, 'running', now() + make_interval(secs => %s))
                    RETURNING *
                    """,
                    (job_id, worker_id, attempt, lease_timeout_seconds),
                ).fetchone()
                return self._job(updated_job), self._execution(execution)

    def heartbeat(self, execution_id: UUID, lease_timeout_seconds: int) -> bool:
        with self.connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    UPDATE engine_executions
                    SET heartbeat_at = now(),
                        lease_expires_at = now() + make_interval(secs => %s)
                    WHERE id = %s AND status = 'running'
                    RETURNING id
                    """,
                    (lease_timeout_seconds, execution_id),
                ).fetchone()
                return row is not None

    def mark_success(self, job_id: UUID, execution_id: UUID, result: Any | None) -> JobRecord:
        with self.connection() as conn:
            with conn.transaction():
                conn.execute(
                    """
                    UPDATE engine_executions
                    SET status = 'succeeded', completed_at = now(), result = %s::jsonb
                    WHERE id = %s AND status = 'running'
                    """,
                    (Jsonb(result), execution_id),
                )
                row = conn.execute(
                    """
                    UPDATE engine_jobs
                    SET status = 'succeeded', completed_at = now(), updated_at = now(),
                        last_error_type = NULL, last_error_message = NULL
                    WHERE id = %s
                    RETURNING *
                    """,
                    (job_id,),
                ).fetchone()
                return self._job(row)

    def mark_failure(
        self,
        job_id: UUID,
        execution_id: UUID,
        error_type: str,
        error_message: str,
        retry_delay_seconds: int,
        max_attempts: int,
        timed_out: bool = False,
    ) -> JobRecord:
        with self.connection() as conn:
            with conn.transaction():
                job = conn.execute(
                    "SELECT * FROM engine_jobs WHERE id = %s FOR UPDATE",
                    (job_id,),
                ).fetchone()
                if job is None:
                    raise JobNotFoundError(f"Job not found: {job_id}")
                record = self._job(job)
                execution_status = (
                    ExecutionStatus.TIMED_OUT if timed_out else ExecutionStatus.FAILED
                ).value
                conn.execute(
                    """
                    UPDATE engine_executions
                    SET status = %s, completed_at = now(),
                        error_type = %s, error_message = %s
                    WHERE id = %s AND status = 'running'
                    """,
                    (execution_status, error_type, error_message[:2_000], execution_id),
                )
                if record.attempts < max_attempts:
                    next_run_at = utc_now() + timedelta(seconds=retry_delay_seconds)
                    status = JobStatus.PENDING.value
                    completed_at = None
                else:
                    next_run_at = record.next_run_at
                    status = JobStatus.FAILED.value
                    completed_at = utc_now()
                row = conn.execute(
                    """
                    UPDATE engine_jobs
                    SET status = %s, next_run_at = %s, completed_at = %s, updated_at = now(),
                        last_error_type = %s, last_error_message = %s
                    WHERE id = %s
                    RETURNING *
                    """,
                    (status, next_run_at, completed_at, error_type, error_message[:2_000], job_id),
                ).fetchone()
                return self._job(row)

    def ready_jobs_due_for_wakeup(self, limit: int = 100) -> list[JobRecord]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM engine_jobs
                WHERE status = 'pending' AND next_run_at <= now()
                ORDER BY priority DESC, created_at ASC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
            return [self._job(row) for row in rows]

    def reset_expired_running_jobs(self, limit: int = 100) -> list[JobRecord]:
        with self.connection() as conn:
            with conn.transaction():
                rows = conn.execute(
                    """
                    WITH expired AS (
                        SELECT j.id
                        FROM engine_jobs j
                        JOIN engine_executions e ON e.job_id = j.id
                        WHERE j.status = 'running'
                          AND e.status = 'running'
                          AND e.lease_expires_at < now()
                        ORDER BY e.lease_expires_at ASC
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED
                    )
                    UPDATE engine_jobs j
                    SET status = 'pending', updated_at = now()
                    FROM expired
                    WHERE j.id = expired.id
                    RETURNING j.*
                    """,
                    (limit,),
                ).fetchall()
                conn.execute(
                    """
                    UPDATE engine_executions
                    SET status = 'failed', completed_at = now(),
                        error_type = 'WorkerLeaseExpired',
                        error_message = 'Worker heartbeat lease expired before completion'
                    WHERE status = 'running' AND lease_expires_at < now()
                    """
                )
                return [self._job(row) for row in rows]

    @staticmethod
    def _job(row: Any) -> JobRecord:
        if row is None:
            raise JobNotFoundError("Expected job row but query returned no rows")
        return JobRecord.model_validate(row)

    @staticmethod
    def _execution(row: Any) -> ExecutionRecord:
        if row is None:
            raise JobNotFoundError("Expected execution row but query returned no rows")
        return ExecutionRecord.model_validate(row)
