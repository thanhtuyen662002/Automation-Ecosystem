from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from workers.runtime.task_executor import (
    TaskRecord,
    TaskStatus,
    build_execution_hash,
    validate_task_transition,
)


class TaskStore:
    def __init__(self, config: Any) -> None:
        self._pool = ConnectionPool(
            conninfo=config.database_url,
            min_size=1,
            max_size=max(config.total_concurrency + 2, 4),
            kwargs={"row_factory": dict_row},
            open=False,
        )

    def open(self) -> None:
        self._pool.open(wait=True)

    def close(self) -> None:
        self._pool.close()

    def migrate(self, migration_path: Path | None = None) -> None:
        path = migration_path or Path(__file__).resolve().parents[2] / "migrations" / "002_worker_runtime_tasks.sql"
        sql = path.read_text(encoding="utf-8")
        with self._connection() as conn:
            with conn.transaction():
                conn.execute(sql)

    def fetch_task(self, task_id: UUID) -> TaskRecord | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM automation_tasks WHERE task_id = %s", (task_id,)).fetchone()
            return _task(row) if row else None

    def get_task_and_lock(self, task_id: UUID, worker_id: str) -> TaskRecord | None:
        with self._connection() as conn:
            with conn.transaction():
                current = conn.execute(
                    """
                    SELECT *
                    FROM automation_tasks
                    WHERE task_id = %s
                      AND status = 'READY'
                      AND next_run_at <= now()
                      AND execution_hash IS NULL
                    FOR UPDATE SKIP LOCKED
                    """,
                    (task_id,),
                ).fetchone()
                if current is None:
                    return None
                validate_task_transition(TaskStatus(current["status"]), TaskStatus.RUNNING)
                execution_hash = build_execution_hash(_task(current))
                row = conn.execute(
                    """
                    UPDATE automation_tasks AS task
                    SET status = 'RUNNING',
                        worker_id = %s,
                        locked_by = %s,
                        started_at = now(),
                        last_heartbeat = now(),
                        execution_hash = %s,
                        completed_at = NULL,
                        updated_at = now(),
                        error = NULL,
                        error_type = NULL
                    WHERE task.task_id = %s
                      AND execution_hash IS NULL
                    RETURNING task.*
                    """,
                    (worker_id, worker_id, execution_hash, task_id),
                ).fetchone()
                return _task(row) if row else None

    def heartbeat(self, task_id: UUID, worker_id: str) -> bool:
        with self._connection() as conn:
            row = conn.execute(
                """
                UPDATE automation_tasks
                SET last_heartbeat = now(), updated_at = now()
                WHERE task_id = %s
                  AND status = 'RUNNING'
                  AND worker_id = %s
                RETURNING task_id
                """,
                (task_id, worker_id),
            ).fetchone()
            return row is not None

    def mark_success(self, task_id: UUID, worker_id: str, result: dict[str, Any]) -> bool:
        validate_task_transition(TaskStatus.RUNNING, TaskStatus.SUCCESS)
        with self._connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    UPDATE automation_tasks
                    SET status = 'SUCCESS',
                        result = %s,
                        completed_at = now(),
                        updated_at = now(),
                        worker_id = NULL,
                        locked_by = NULL
                    WHERE task_id = %s
                      AND status = 'RUNNING'
                      AND worker_id = %s
                    RETURNING task_id
                    """,
                    (Jsonb(result), task_id, worker_id),
                ).fetchone()
                return row is not None

    def mark_retry(
        self,
        task_id: UUID,
        worker_id: str,
        error_type: str,
        error: str,
        retry_count: int,
        next_run_at: datetime,
    ) -> bool:
        validate_task_transition(TaskStatus.RUNNING, TaskStatus.RETRY)
        with self._connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    UPDATE automation_tasks
                    SET status = 'RETRY',
                        retry_count = %s,
                        next_run_at = %s,
                        next_retry_at = %s,
                        error_type = %s,
                        error = %s,
                        completed_at = now(),
                        updated_at = now(),
                        worker_id = NULL,
                        locked_by = NULL
                    WHERE task_id = %s
                      AND status = 'RUNNING'
                      AND worker_id = %s
                    RETURNING task_id
                    """,
                    (retry_count, next_run_at, next_run_at, error_type, error[:4000], task_id, worker_id),
                ).fetchone()
                return row is not None

    def mark_failed(
        self,
        task_id: UUID,
        worker_id: str,
        error_type: str,
        error: str,
    ) -> bool:
        validate_task_transition(TaskStatus.RUNNING, TaskStatus.FAILED)
        with self._connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    UPDATE automation_tasks
                    SET status = 'FAILED',
                        error_type = %s,
                        error = %s,
                        completed_at = now(),
                        updated_at = now(),
                        worker_id = NULL,
                        locked_by = NULL
                    WHERE task_id = %s
                      AND status = 'RUNNING'
                      AND worker_id = %s
                    RETURNING task_id
                    """,
                    (error_type, error[:4000], task_id, worker_id),
                ).fetchone()
                return row is not None

    def due_failed_tasks(self, limit: int = 100) -> list[TaskRecord]:
        return self.promote_due_retries_to_ready(limit)

    def promote_due_retries_to_ready(self, limit: int = 100) -> list[TaskRecord]:
        with self._connection() as conn:
            with conn.transaction():
                rows = conn.execute(
                    """
                    WITH due AS (
                        SELECT task_id
                        FROM automation_tasks
                        WHERE status = 'RETRY'
                          AND retry_count < max_retries
                          AND next_retry_at <= now()
                        ORDER BY next_retry_at ASC, created_at ASC
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED
                    )
                    UPDATE automation_tasks AS task
                    SET status = 'READY',
                        next_run_at = now(),
                        updated_at = now()
                    FROM due
                    WHERE task.task_id = due.task_id
                    RETURNING task.*
                    """,
                    (limit,),
                ).fetchall()
                return [_task(row) for row in rows]

    def stale_running_tasks(self, stale_before: datetime, limit: int = 100) -> list[TaskRecord]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM automation_tasks
                WHERE status = 'RUNNING'
                  AND COALESCE(last_heartbeat, started_at) < %s
                ORDER BY COALESCE(last_heartbeat, started_at) ASC
                LIMIT %s
                """,
                (stale_before, limit),
            ).fetchall()
            return [_task(row) for row in rows]

    def mark_zombie_failed(
        self,
        task_id: UUID,
        retry_count: int,
        next_run_at: datetime,
    ) -> bool:
        with self._connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    UPDATE automation_tasks
                    SET status = CASE
                            WHEN %s <= max_retries THEN 'RETRY'
                            ELSE 'FAILED'
                        END,
                        retry_count = %s,
                        next_run_at = %s,
                        next_retry_at = %s,
                        error_type = 'ZombieTask',
                        error = 'Task heartbeat exceeded stale timeout',
                        completed_at = now(),
                        updated_at = now(),
                        worker_id = NULL,
                        locked_by = NULL
                    WHERE task_id = %s
                      AND status = 'RUNNING'
                    RETURNING task_id
                    """,
                    (retry_count, retry_count, next_run_at, next_run_at, task_id),
                ).fetchone()
                return row is not None

    def _connection(self):
        return self._pool.connection()


def _task(row: dict[str, Any]) -> TaskRecord:
    return TaskRecord(
        task_id=row["task_id"],
        task_type=row["task_type"],
        status=TaskStatus(row["status"]),
        data=row["data"] or {},
        retry_count=row["retry_count"],
        max_retries=row["max_retries"],
        last_heartbeat=row["last_heartbeat"],
        next_run_at=row["next_run_at"],
        worker_id=row.get("worker_id") or row.get("locked_by"),
        execution_hash=row.get("execution_hash"),
    )
