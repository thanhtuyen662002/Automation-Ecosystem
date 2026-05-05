from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, AsyncIterator
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    RETRY = "RETRY"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class ExecutionStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


VALID_TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.READY, TaskStatus.FAILED, TaskStatus.CANCELED}),
    TaskStatus.READY: frozenset({TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELED}),
    TaskStatus.RUNNING: frozenset({TaskStatus.SUCCESS, TaskStatus.RETRY, TaskStatus.FAILED}),
    TaskStatus.RETRY: frozenset({TaskStatus.READY, TaskStatus.FAILED, TaskStatus.CANCELED}),
    TaskStatus.SUCCESS: frozenset(),
    TaskStatus.FAILED: frozenset({TaskStatus.PENDING}),
    TaskStatus.CANCELED: frozenset(),
}


class DatabaseError(RuntimeError):
    pass


class InvalidStateTransition(DatabaseError):
    pass


class NotFoundError(DatabaseError):
    pass


class ConflictError(DatabaseError):
    pass


class ValidationError(DatabaseError):
    pass


@dataclass(frozen=True)
class RetryConfig:
    base_delay_seconds: int = 5
    max_delay_seconds: int = 300

    def delay_for_attempt(self, retry_count: int) -> int:
        if retry_count < 0:
            raise ValueError("retry_count must be >= 0")
        if self.base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must be >= 0")
        if self.max_delay_seconds < 0:
            raise ValueError("max_delay_seconds must be >= 0")
        if self.base_delay_seconds == 0 or self.max_delay_seconds == 0:
            return 0
        return min(self.base_delay_seconds * (2**max(retry_count - 1, 0)), self.max_delay_seconds)


@dataclass(frozen=True)
class TaskRecord:
    id: UUID
    job_id: UUID
    task_type: str
    status: TaskStatus
    priority: int
    payload: dict[str, Any]
    metadata: dict[str, Any]
    retry_count: int
    max_retries: int
    next_run_at: datetime
    next_retry_at: datetime | None
    account_id: UUID | None
    action_type: str | None
    idempotency_key: str | None
    result: dict[str, Any] | None
    error_type: str | None
    error_message: str | None


@dataclass(frozen=True)
class JobRecord:
    id: UUID
    job_key: str | None
    workflow_name: str
    status: str
    priority: int
    input: dict[str, Any]
    metadata: dict[str, Any]
    error_type: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class JobDetailRecord:
    job: JobRecord
    tasks: list[TaskRecord]


@dataclass(frozen=True)
class SystemStatsRecord:
    total_tasks: int
    running: int
    pending: int
    failed: int
    success: int


@dataclass(frozen=True)
class TaskExecutionRecord:
    id: UUID
    task_id: UUID
    worker_id: str
    attempt_number: int
    status: ExecutionStatus
    heartbeat_at: datetime
    lease_expires_at: datetime
    started_at: datetime
    completed_at: datetime | None
    result: dict[str, Any] | None
    error_type: str | None
    error_message: str | None


@dataclass(frozen=True)
class AcquiredTask:
    task: TaskRecord
    execution: TaskExecutionRecord


@dataclass(frozen=True)
class DependencyRecord:
    task_id: UUID
    depends_on_task_id: UUID
    dependency_status: TaskStatus


@dataclass(frozen=True)
class PolicyRuleRecord:
    id: UUID
    account_id: UUID | None
    platform: str | None
    action_type: str
    rule_name: str
    enabled: bool
    config: dict[str, Any]
    cooldown_seconds: int
    max_actions: int | None
    window_seconds: int | None


@dataclass(frozen=True)
class AccountUsage:
    account_id: UUID
    total_actions: int
    successful_actions: int
    failed_actions: int
    blocked_actions: int
    last_action_at: datetime | None


@dataclass(frozen=True)
class ActionLogRecord:
    id: UUID
    task_id: UUID | None
    action_type: str
    status: str
    metadata: dict[str, Any]
    created_at: datetime


class AutomationDatabase:
    def __init__(
        self,
        database_url: str,
        min_pool_size: int = 1,
        max_pool_size: int = 10,
        lease_seconds: int = 300,
        retry_config: RetryConfig | None = None,
    ) -> None:
        if min_pool_size < 1:
            raise ValueError("min_pool_size must be >= 1")
        if max_pool_size < min_pool_size:
            raise ValueError("max_pool_size must be >= min_pool_size")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be >= 1")
        self._pool = AsyncConnectionPool(
            conninfo=database_url,
            min_size=min_pool_size,
            max_size=max_pool_size,
            kwargs={"row_factory": dict_row},
            open=False,
        )
        self._lease_seconds = lease_seconds
        self._retry_config = retry_config or RetryConfig()

    async def open(self) -> None:
        await self._pool.open(wait=True)

    async def close(self) -> None:
        await self._pool.close()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection[Any]]:
        async with self._pool.connection() as conn:
            yield conn

    async def ping(self) -> bool:
        async with self._pool.connection() as conn:
            result = await conn.execute("SELECT 1 AS ok")
            row = await result.fetchone()
            return bool(row and row["ok"] == 1)

    async def create_job(
        self,
        workflow_name: str,
        tasks: list[dict[str, Any]],
        job_key: str | None = None,
        priority: int = 0,
        input_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> JobDetailRecord:
        if not workflow_name.strip():
            raise ValidationError("workflow_name cannot be empty")
        if not tasks:
            raise ValidationError("job must include at least one task")

        async with self._pool.connection() as conn:
            async with conn.transaction():
                job_result = await conn.execute(
                    """
                    INSERT INTO jobs (job_key, workflow_name, priority, input, metadata)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        job_key,
                        workflow_name,
                        priority,
                        Jsonb(input_data or {}),
                        Jsonb(metadata or {}),
                    ),
                )
                job_row = await job_result.fetchone()
                if job_row is None:
                    raise DatabaseError("Failed to create job")

                task_key_to_id: dict[str, UUID] = {}
                inserted_tasks: list[TaskRecord] = []
                pending_dependencies: list[tuple[UUID, list[str]]] = []

                for index, task in enumerate(tasks):
                    task_type = str(task.get("task_type") or "").strip()
                    if not task_type:
                        raise ValidationError(f"tasks[{index}].task_type cannot be empty")
                    task_key = task.get("task_key")
                    if task_key is not None:
                        task_key = str(task_key).strip()
                        if not task_key:
                            raise ValidationError(f"tasks[{index}].task_key cannot be empty")
                        if task_key in task_key_to_id:
                            raise ValidationError(f"duplicate task_key: {task_key}")

                    task_result = await conn.execute(
                        """
                        INSERT INTO tasks (
                            job_id, parent_task_id, account_id, task_type, action_type,
                            status, priority, payload, metadata, max_retries,
                            next_run_at, idempotency_key
                        )
                        VALUES (
                            %s, %s, %s, %s, %s,
                            'PENDING', %s, %s, %s, %s,
                            COALESCE(%s, now()), %s
                        )
                        RETURNING *
                        """,
                        (
                            job_row["id"],
                            task.get("parent_task_id"),
                            task.get("account_id"),
                            task_type,
                            task.get("action_type"),
                            int(task.get("priority", 0)),
                            Jsonb(task.get("payload") or {}),
                            Jsonb(task.get("metadata") or {}),
                            int(task.get("max_retries", 3)),
                            task.get("next_run_at"),
                            task.get("idempotency_key"),
                        ),
                    )
                    task_row = await task_result.fetchone()
                    if task_row is None:
                        raise DatabaseError(f"Failed to create task at index {index}")
                    if task_key is not None:
                        task_key_to_id[task_key] = task_row["id"]
                    dependency_keys = [str(value) for value in task.get("depends_on") or []]
                    pending_dependencies.append((task_row["id"], dependency_keys))
                    inserted_tasks.append(_task(task_row))

                for task_id, dependency_keys in pending_dependencies:
                    for dependency_key in dependency_keys:
                        depends_on_task_id = task_key_to_id.get(dependency_key)
                        if depends_on_task_id is None:
                            raise ValidationError(f"unknown dependency task_key: {dependency_key}")
                        await conn.execute(
                            """
                            INSERT INTO task_dependencies (task_id, depends_on_task_id)
                            VALUES (%s, %s)
                            ON CONFLICT DO NOTHING
                            """,
                            (task_id, depends_on_task_id),
                        )
                return JobDetailRecord(job=_job(job_row), tasks=inserted_tasks)

    async def get_job_detail(self, job_id: UUID) -> JobDetailRecord | None:
        async with self._pool.connection() as conn:
            job_result = await conn.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
            job_row = await job_result.fetchone()
            if job_row is None:
                return None
            task_result = await conn.execute(
                """
                SELECT *
                FROM tasks
                WHERE job_id = %s
                ORDER BY priority DESC, created_at ASC
                """,
                (job_id,),
            )
            return JobDetailRecord(job=_job(job_row), tasks=[_task(row) for row in await task_result.fetchall()])

    async def list_jobs(self, limit: int, offset: int) -> list[JobRecord]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if offset < 0:
            raise ValueError("offset must be >= 0")
        async with self._pool.connection() as conn:
            result = await conn.execute(
                """
                SELECT *
                FROM jobs
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            )
            return [_job(row) for row in await result.fetchall()]

    async def get_task(self, task_id: UUID) -> TaskRecord | None:
        async with self._pool.connection() as conn:
            result = await conn.execute("SELECT * FROM tasks WHERE id = %s", (task_id,))
            row = await result.fetchone()
            return _task(row) if row else None

    async def list_tasks(
        self,
        limit: int,
        offset: int,
        status: TaskStatus | None = None,
        task_type: str | None = None,
        job_id: UUID | None = None,
    ) -> list[TaskRecord]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if offset < 0:
            raise ValueError("offset must be >= 0")
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = %s")
            params.append(status.value)
        if task_type is not None:
            if not task_type.strip():
                raise ValidationError("task_type cannot be empty")
            clauses.append("task_type = %s")
            params.append(task_type)
        if job_id is not None:
            clauses.append("job_id = %s")
            params.append(job_id)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        async with self._pool.connection() as conn:
            result = await conn.execute(
                f"""
                SELECT *
                FROM tasks
                {where_sql}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                tuple(params),
            )
            return [_task(row) for row in await result.fetchall()]

    async def reset_failed_task_for_retry(self, task_id: UUID) -> TaskRecord:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                task_result = await conn.execute("SELECT * FROM tasks WHERE id = %s FOR UPDATE", (task_id,))
                task_row = await task_result.fetchone()
                if task_row is None:
                    raise NotFoundError(f"Task not found: {task_id}")
                status = TaskStatus(task_row["status"])
                if status != TaskStatus.FAILED:
                    raise InvalidStateTransition(f"Only FAILED tasks can be retried: {status.value}")
                if int(task_row["retry_count"]) >= int(task_row["max_retries"]):
                    raise ConflictError(f"Task retry budget exhausted: {task_id}")
                _ensure_task_transition(status, TaskStatus.PENDING)
                result = await conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'PENDING',
                        next_run_at = now(),
                        next_retry_at = NULL,
                        error_type = NULL,
                        error_message = NULL,
                        completed_at = NULL,
                        updated_at = now()
                    WHERE id = %s
                      AND status = 'FAILED'
                      AND retry_count < max_retries
                    RETURNING *
                    """,
                    (task_id,),
                )
                updated = await result.fetchone()
                if updated is None:
                    raise ConflictError(f"Task could not be reset for retry: {task_id}")
                return _task(updated)

    async def get_system_stats(self) -> SystemStatsRecord:
        async with self._pool.connection() as conn:
            result = await conn.execute(
                """
                SELECT count(*)::int AS total_tasks,
                       count(*) FILTER (WHERE status = 'RUNNING')::int AS running,
                       count(*) FILTER (WHERE status IN ('PENDING', 'READY', 'RETRY'))::int AS pending,
                       count(*) FILTER (WHERE status = 'FAILED')::int AS failed,
                       count(*) FILTER (WHERE status = 'SUCCESS')::int AS success
                FROM tasks
                """
            )
            row = await result.fetchone()
            if row is None:
                return SystemStatsRecord(0, 0, 0, 0, 0)
            return SystemStatsRecord(
                total_tasks=row["total_tasks"],
                running=row["running"],
                pending=row["pending"],
                failed=row["failed"],
                success=row["success"],
            )

    async def acquire_task_with_execution(self, task_id: UUID, worker_id: str) -> AcquiredTask | None:
        if not worker_id.strip():
            raise ValueError("worker_id cannot be empty")
        async with self._pool.connection() as conn:
            async with conn.transaction():
                task_result = await conn.execute(
                    """
                    SELECT *
                    FROM tasks
                    WHERE id = %s
                      AND status = 'READY'
                      AND next_run_at <= now()
                      AND retry_count < max_retries
                      AND NOT EXISTS (
                          SELECT 1
                          FROM task_dependencies dep
                          JOIN tasks parent ON parent.id = dep.depends_on_task_id
                          WHERE dep.task_id = tasks.id
                            AND parent.status <> 'SUCCESS'
                      )
                    FOR UPDATE SKIP LOCKED
                    """,
                    (task_id,),
                )
                task_row = await task_result.fetchone()
                if task_row is None:
                    return None

                running_result = await conn.execute(
                    """
                    SELECT id
                    FROM task_executions
                    WHERE task_id = %s
                      AND status = 'running'
                    FOR UPDATE
                    """,
                    (task_id,),
                )
                if await running_result.fetchone() is not None:
                    return None

                _ensure_task_transition(TaskStatus(task_row["status"]), TaskStatus.RUNNING)
                attempt_number = int(task_row["retry_count"]) + 1
                lease_expires_at = datetime.now(UTC) + timedelta(seconds=self._lease_seconds)

                updated_task_result = await conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'RUNNING',
                        retry_count = %s,
                        updated_at = now()
                    WHERE id = %s
                      AND status = 'READY'
                    RETURNING *
                    """,
                    (attempt_number, task_id),
                )
                updated_task = await updated_task_result.fetchone()
                if updated_task is None:
                    return None

                execution_result = await conn.execute(
                    """
                    INSERT INTO task_executions (
                        task_id, worker_id, attempt_number, status,
                        heartbeat_at, lease_expires_at, started_at
                    )
                    VALUES (%s, %s, %s, 'running', now(), %s, now())
                    RETURNING *
                    """,
                    (task_id, worker_id, attempt_number, lease_expires_at),
                )
                execution = await execution_result.fetchone()
                if execution is None:
                    raise DatabaseError(f"Failed to create execution for task: {task_id}")
                return AcquiredTask(task=_task(updated_task), execution=_execution(execution))

    async def acquire_ready_tasks_batch(
        self,
        limit: int,
        worker_id: str,
        max_per_task_type: int | None = None,
        max_per_account: int | None = None,
    ) -> list[AcquiredTask]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if not worker_id.strip():
            raise ValueError("worker_id cannot be empty")
        if max_per_task_type is not None and max_per_task_type < 1:
            raise ValueError("max_per_task_type must be >= 1")
        if max_per_account is not None and max_per_account < 1:
            raise ValueError("max_per_account must be >= 1")

        acquired: list[AcquiredTask] = []
        async with self._pool.connection() as conn:
            async with conn.transaction():
                candidate_result = await conn.execute(
                    """
                    WITH ranked AS (
                        SELECT t.id,
                               t.account_id,
                               t.priority,
                               t.next_run_at,
                               t.created_at,
                               row_number() OVER (
                                   PARTITION BY t.task_type
                                   ORDER BY t.priority DESC, t.next_run_at ASC, t.created_at ASC
                               ) AS task_type_rank,
                               row_number() OVER (
                                   PARTITION BY t.account_id
                                   ORDER BY t.priority DESC, t.next_run_at ASC, t.created_at ASC
                               ) AS account_rank
                        FROM tasks t
                        WHERE t.status = 'READY'
                          AND t.next_run_at <= now()
                          AND t.retry_count < t.max_retries
                          AND NOT EXISTS (
                              SELECT 1
                              FROM task_dependencies dep
                              JOIN tasks parent ON parent.id = dep.depends_on_task_id
                              WHERE dep.task_id = t.id
                                AND parent.status <> 'SUCCESS'
                          )
                          AND NOT EXISTS (
                              SELECT 1
                              FROM task_executions ex
                              WHERE ex.task_id = t.id
                                AND ex.status = 'running'
                          )
                    ),
                    limited AS (
                        SELECT id, priority, next_run_at, created_at
                        FROM ranked
                        WHERE (%s::int IS NULL OR task_type_rank <= %s::int)
                          AND (%s::int IS NULL OR account_id IS NULL OR account_rank <= %s::int)
                        ORDER BY priority DESC, next_run_at ASC, created_at ASC
                        LIMIT %s
                    )
                    SELECT t.*
                    FROM tasks t
                    JOIN limited ON limited.id = t.id
                    ORDER BY t.priority DESC, t.next_run_at ASC, t.created_at ASC
                    FOR UPDATE OF t SKIP LOCKED
                    """,
                    (max_per_task_type, max_per_task_type, max_per_account, max_per_account, limit),
                )
                task_rows = await candidate_result.fetchall()
                for task_row in task_rows:
                    _ensure_task_transition(TaskStatus(task_row["status"]), TaskStatus.RUNNING)
                    attempt_number = int(task_row["retry_count"]) + 1
                    lease_expires_at = datetime.now(UTC) + timedelta(seconds=self._lease_seconds)

                    updated_task_result = await conn.execute(
                        """
                        UPDATE tasks
                        SET status = 'RUNNING',
                            retry_count = %s,
                            updated_at = now()
                        WHERE id = %s
                          AND status = 'READY'
                        RETURNING *
                        """,
                        (attempt_number, task_row["id"]),
                    )
                    updated_task = await updated_task_result.fetchone()
                    if updated_task is None:
                        continue

                    execution_result = await conn.execute(
                        """
                        INSERT INTO task_executions (
                            task_id, worker_id, attempt_number, status,
                            heartbeat_at, lease_expires_at, started_at
                        )
                        VALUES (%s, %s, %s, 'running', now(), %s, now())
                        RETURNING *
                        """,
                        (task_row["id"], worker_id, attempt_number, lease_expires_at),
                    )
                    execution = await execution_result.fetchone()
                    if execution is None:
                        raise DatabaseError(f"Failed to create execution for task: {task_row['id']}")
                    acquired.append(AcquiredTask(task=_task(updated_task), execution=_execution(execution)))
        return acquired

    async def get_task_and_lock(self, task_id: UUID, worker_id: str) -> AcquiredTask | None:
        return await self.acquire_task_with_execution(task_id, worker_id)

    async def get_ready_tasks(self, limit: int) -> list[TaskRecord]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        async with self._pool.connection() as conn:
            rows = await conn.execute(
                """
                SELECT t.*
                FROM tasks t
                WHERE t.status = 'READY'
                  AND t.next_run_at <= now()
                  AND t.retry_count < t.max_retries
                  AND NOT EXISTS (
                      SELECT 1
                      FROM task_dependencies dep
                      JOIN tasks parent ON parent.id = dep.depends_on_task_id
                      WHERE dep.task_id = t.id
                        AND parent.status <> 'SUCCESS'
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM task_executions ex
                      WHERE ex.task_id = t.id
                        AND ex.status = 'running'
                  )
                ORDER BY t.priority DESC, t.next_run_at ASC, t.created_at ASC
                LIMIT %s
                """,
                (limit,),
            )
            return [_task(row) for row in await rows.fetchall()]

    async def mark_task_running(self, task_id: UUID, worker_id: str) -> AcquiredTask | None:
        return await self.acquire_task_with_execution(task_id, worker_id)

    async def mark_task_success(
        self,
        task_id: UUID,
        execution_id: UUID,
        result: dict[str, Any],
    ) -> TaskRecord:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                task_row = await self._lock_task(conn, task_id)
                execution_row = await self._lock_execution(conn, execution_id)
                self._validate_execution_belongs_to_task(execution_row, task_id)
                self._validate_running_execution(execution_row)
                _ensure_task_transition(TaskStatus(task_row["status"]), TaskStatus.SUCCESS)

                await conn.execute(
                    """
                    UPDATE task_executions
                    SET status = 'succeeded',
                        result = %s,
                        completed_at = now()
                    WHERE id = %s
                      AND status = 'running'
                    """,
                    (Jsonb(result), execution_id),
                )
                updated_task_result = await conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'SUCCESS',
                        result = %s,
                        completed_at = now(),
                        updated_at = now(),
                        error_type = NULL,
                        error_message = NULL
                    WHERE id = %s
                      AND status = 'RUNNING'
                    RETURNING *
                    """,
                    (Jsonb(result), task_id),
                )
                updated_task = await updated_task_result.fetchone()
                if updated_task is None:
                    raise DatabaseError(f"Task was not RUNNING when marking success: {task_id}")
                await self._maybe_complete_job(conn, updated_task["job_id"])
                return _task(updated_task)

    async def mark_task_failure(
        self,
        task_id: UUID,
        execution_id: UUID,
        error: Exception | str,
        retry_logic: RetryConfig | None = None,
        timed_out: bool = False,
    ) -> TaskRecord:
        error_type = type(error).__name__ if isinstance(error, Exception) else "TaskError"
        error_message = str(error)
        retry_config = retry_logic or self._retry_config
        async with self._pool.connection() as conn:
            async with conn.transaction():
                task_row = await self._lock_task(conn, task_id)
                execution_row = await self._lock_execution(conn, execution_id)
                self._validate_execution_belongs_to_task(execution_row, task_id)
                self._validate_running_execution(execution_row)

                current_status = TaskStatus(task_row["status"])
                if current_status != TaskStatus.RUNNING:
                    raise InvalidStateTransition(f"Task must be RUNNING before failure: {current_status.value}")

                retry_count = int(task_row["retry_count"])
                should_retry = retry_count < int(task_row["max_retries"])
                target_status = TaskStatus.RETRY if should_retry else TaskStatus.FAILED
                _ensure_task_transition(current_status, target_status)
                next_run_at = (
                    datetime.now(UTC) + timedelta(seconds=retry_config.delay_for_attempt(retry_count))
                    if should_retry
                    else task_row["next_run_at"]
                )
                execution_status = ExecutionStatus.TIMED_OUT if timed_out else ExecutionStatus.FAILED

                await conn.execute(
                    """
                    UPDATE task_executions
                    SET status = %s,
                        error_type = %s,
                        error_message = %s,
                        completed_at = now()
                    WHERE id = %s
                      AND status = 'running'
                    """,
                    (execution_status.value, error_type, error_message[:4000], execution_id),
                )
                updated_task_result = await conn.execute(
                    """
                    UPDATE tasks
                    SET status = %s,
                        next_run_at = %s,
                        next_retry_at = %s,
                        completed_at = CASE WHEN %s = 'FAILED' THEN now() ELSE completed_at END,
                        updated_at = now(),
                        error_type = %s,
                        error_message = %s
                    WHERE id = %s
                      AND status = 'RUNNING'
                    RETURNING *
                    """,
                    (
                        target_status.value,
                        next_run_at,
                        next_run_at if should_retry else None,
                        target_status.value,
                        error_type,
                        error_message[:4000],
                        task_id,
                    ),
                )
                updated_task = await updated_task_result.fetchone()
                if updated_task is None:
                    raise DatabaseError(f"Task was not RUNNING when marking failure: {task_id}")
                if target_status == TaskStatus.FAILED:
                    await self._maybe_fail_job(conn, updated_task["job_id"])
                return _task(updated_task)

    async def update_execution_heartbeat(self, execution_id: UUID) -> bool:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                result = await conn.execute(
                    """
                    UPDATE task_executions
                    SET heartbeat_at = now(),
                        lease_expires_at = now() + make_interval(secs => %s)
                    WHERE id = %s
                      AND status = 'running'
                    RETURNING id
                    """,
                    (self._lease_seconds, execution_id),
                )
                return await result.fetchone() is not None

    async def update_heartbeat(self, task_id: UUID, worker_id: str) -> bool:
        async with self._pool.connection() as conn:
            result = await conn.execute(
                """
                UPDATE task_executions
                SET heartbeat_at = now(),
                    lease_expires_at = now() + make_interval(secs => %s)
                WHERE task_id = %s
                  AND worker_id = %s
                  AND status = 'running'
                RETURNING id
                """,
                (self._lease_seconds, task_id, worker_id),
            )
            return await result.fetchone() is not None

    async def recover_expired_executions(self, limit: int = 100) -> list[TaskRecord]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        recovered: list[TaskRecord] = []
        async with self._pool.connection() as conn:
            async with conn.transaction():
                expired_result = await conn.execute(
                    """
                    SELECT ex.id, ex.task_id
                    FROM task_executions ex
                    JOIN tasks t ON t.id = ex.task_id
                    WHERE ex.status = 'running'
                      AND ex.lease_expires_at < now()
                      AND t.status = 'RUNNING'
                    ORDER BY ex.lease_expires_at ASC
                    LIMIT %s
                    """,
                    (limit,),
                )
                expired = await expired_result.fetchall()
                for expired_row in expired:
                    task_row = await self._lock_task(conn, expired_row["task_id"])
                    execution_row = await self._lock_execution(conn, expired_row["id"])
                    if TaskStatus(task_row["status"]) != TaskStatus.RUNNING:
                        continue
                    if ExecutionStatus(execution_row["status"]) != ExecutionStatus.RUNNING:
                        continue
                    if execution_row["lease_expires_at"] >= datetime.now(UTC):
                        continue
                    retry_count = int(task_row["retry_count"])
                    should_retry = retry_count < int(task_row["max_retries"])
                    target_status = TaskStatus.RETRY if should_retry else TaskStatus.FAILED
                    next_run_at = (
                        datetime.now(UTC) + timedelta(seconds=self._retry_config.delay_for_attempt(retry_count))
                        if should_retry
                        else task_row["next_run_at"]
                    )
                    await conn.execute(
                        """
                        UPDATE task_executions
                        SET status = 'failed',
                            error_type = 'LeaseExpired',
                            error_message = 'Execution heartbeat lease expired',
                            completed_at = now()
                        WHERE id = %s
                          AND status = 'running'
                        """,
                        (execution_row["id"],),
                    )
                    updated_task_result = await conn.execute(
                        """
                        UPDATE tasks
                        SET status = %s,
                            next_run_at = %s,
                            next_retry_at = %s,
                            completed_at = CASE WHEN %s = 'FAILED' THEN now() ELSE completed_at END,
                            error_type = 'LeaseExpired',
                            error_message = 'Execution heartbeat lease expired',
                            updated_at = now()
                        WHERE id = %s
                          AND status = 'RUNNING'
                        RETURNING *
                        """,
                        (
                            target_status.value,
                            next_run_at,
                            next_run_at if should_retry else None,
                            target_status.value,
                            task_row["id"],
                        ),
                    )
                    updated_task = await updated_task_result.fetchone()
                    if updated_task:
                        recovered.append(_task(updated_task))
                        if target_status == TaskStatus.FAILED:
                            await self._maybe_fail_job(conn, updated_task["job_id"])
        return recovered

    async def get_task_dependencies(self, task_id: UUID) -> list[DependencyRecord]:
        async with self._pool.connection() as conn:
            rows = await conn.execute(
                """
                SELECT dep.task_id,
                       dep.depends_on_task_id,
                       parent.status AS dependency_status
                FROM task_dependencies dep
                JOIN tasks parent ON parent.id = dep.depends_on_task_id
                WHERE dep.task_id = %s
                ORDER BY dep.created_at ASC
                """,
                (task_id,),
            )
            return [
                DependencyRecord(
                    task_id=row["task_id"],
                    depends_on_task_id=row["depends_on_task_id"],
                    dependency_status=TaskStatus(row["dependency_status"]),
                )
                for row in await rows.fetchall()
            ]

    async def dependencies_satisfied(self, task_id: UUID) -> bool:
        async with self._pool.connection() as conn:
            result = await conn.execute(
                """
                SELECT NOT EXISTS (
                    SELECT 1
                    FROM task_dependencies dep
                    JOIN tasks parent ON parent.id = dep.depends_on_task_id
                    WHERE dep.task_id = %s
                      AND parent.status <> 'SUCCESS'
                ) AS ready
                """,
                (task_id,),
            )
            row = await result.fetchone()
            return bool(row and row["ready"])

    async def log_action(
        self,
        task_id: UUID,
        action_type: str,
        metadata: dict[str, Any],
    ) -> ActionLogRecord:
        if not action_type.strip():
            raise ValueError("action_type cannot be empty")
        async with self._pool.connection() as conn:
            async with conn.transaction():
                task_result = await conn.execute("SELECT job_id, account_id FROM tasks WHERE id = %s", (task_id,))
                task = await task_result.fetchone()
                if task is None:
                    raise DatabaseError(f"Task not found for action log: {task_id}")
                row_result = await conn.execute(
                    """
                    INSERT INTO action_logs (
                        job_id, task_id, account_id, action_type, status, request
                    )
                    VALUES (%s, %s, %s, %s, 'attempted', %s)
                    RETURNING *
                    """,
                    (task["job_id"], task_id, task["account_id"], action_type, Jsonb(metadata)),
                )
                row = await row_result.fetchone()
                if row is None:
                    raise DatabaseError("Failed to insert action log")
                return _action_log(row)

    async def get_account_usage(self, account_id: UUID) -> AccountUsage:
        async with self._pool.connection() as conn:
            result = await conn.execute(
                """
                SELECT %s::uuid AS account_id,
                       count(*)::int AS total_actions,
                       count(*) FILTER (WHERE status = 'succeeded')::int AS successful_actions,
                       count(*) FILTER (WHERE status = 'failed')::int AS failed_actions,
                       count(*) FILTER (WHERE status = 'blocked')::int AS blocked_actions,
                       max(created_at) AS last_action_at
                FROM action_logs
                WHERE account_id = %s
                """,
                (account_id, account_id),
            )
            row = await result.fetchone()
            if row is None:
                return AccountUsage(account_id, 0, 0, 0, 0, None)
            return AccountUsage(
                account_id=row["account_id"],
                total_actions=row["total_actions"],
                successful_actions=row["successful_actions"],
                failed_actions=row["failed_actions"],
                blocked_actions=row["blocked_actions"],
                last_action_at=row["last_action_at"],
            )

    async def get_policy_rules(self, account_id: UUID, action_type: str) -> list[PolicyRuleRecord]:
        if not action_type.strip():
            raise ValueError("action_type cannot be empty")
        async with self._pool.connection() as conn:
            account_result = await conn.execute("SELECT platform FROM accounts WHERE id = %s", (account_id,))
            account = await account_result.fetchone()
            if account is None:
                raise DatabaseError(f"Account not found: {account_id}")
            rows = await conn.execute(
                """
                SELECT *
                FROM policy_rules
                WHERE enabled = true
                  AND action_type = %s
                  AND (
                      account_id = %s
                      OR (account_id IS NULL AND platform = %s)
                      OR (account_id IS NULL AND platform IS NULL)
                  )
                ORDER BY account_id NULLS LAST, platform NULLS LAST, rule_name ASC
                """,
                (action_type, account_id, account["platform"]),
            )
            return [_policy_rule(row) for row in await rows.fetchall()]

    async def _lock_task(self, conn: AsyncConnection[Any], task_id: UUID) -> dict[str, Any]:
        result = await conn.execute("SELECT * FROM tasks WHERE id = %s FOR UPDATE", (task_id,))
        row = await result.fetchone()
        if row is None:
            raise DatabaseError(f"Task not found: {task_id}")
        return row

    async def _lock_execution(self, conn: AsyncConnection[Any], execution_id: UUID) -> dict[str, Any]:
        result = await conn.execute("SELECT * FROM task_executions WHERE id = %s FOR UPDATE", (execution_id,))
        row = await result.fetchone()
        if row is None:
            raise DatabaseError(f"Task execution not found: {execution_id}")
        return row

    @staticmethod
    def _validate_execution_belongs_to_task(execution: dict[str, Any], task_id: UUID) -> None:
        if execution["task_id"] != task_id:
            raise DatabaseError(f"Execution {execution['id']} does not belong to task {task_id}")

    @staticmethod
    def _validate_running_execution(execution: dict[str, Any]) -> None:
        if ExecutionStatus(execution["status"]) != ExecutionStatus.RUNNING:
            raise InvalidStateTransition(
                f"Execution must be running before completion: {execution['status']}"
            )

    async def _maybe_complete_job(self, conn: AsyncConnection[Any], job_id: UUID) -> None:
        result = await conn.execute(
            """
            SELECT
                count(*) FILTER (WHERE status NOT IN ('SUCCESS', 'CANCELED')) AS unfinished,
                count(*) FILTER (WHERE status = 'FAILED') AS failed
            FROM tasks
            WHERE job_id = %s
            """,
            (job_id,),
        )
        row = await result.fetchone()
        if row and row["unfinished"] == 0 and row["failed"] == 0:
            await conn.execute(
                """
                UPDATE jobs
                SET status = 'completed', completed_at = now(), updated_at = now()
                WHERE id = %s AND status IN ('pending', 'running')
                """,
                (job_id,),
            )

    async def _maybe_fail_job(self, conn: AsyncConnection[Any], job_id: UUID) -> None:
        await conn.execute(
            """
            UPDATE jobs
            SET status = 'failed', completed_at = now(), updated_at = now()
            WHERE id = %s
              AND status IN ('pending', 'running')
              AND EXISTS (
                  SELECT 1 FROM tasks WHERE job_id = %s AND status = 'FAILED'
              )
            """,
            (job_id, job_id),
        )


def _ensure_task_transition(current: TaskStatus, target: TaskStatus) -> None:
    if target not in VALID_TASK_TRANSITIONS[current]:
        raise InvalidStateTransition(f"Illegal task transition: {current.value} -> {target.value}")


def _job(row: dict[str, Any]) -> JobRecord:
    return JobRecord(
        id=row["id"],
        job_key=row["job_key"],
        workflow_name=row["workflow_name"],
        status=row["status"],
        priority=row["priority"],
        input=row["input"] or {},
        metadata=row["metadata"] or {},
        error_type=row["error_type"],
        error_message=row["error_message"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _task(row: dict[str, Any]) -> TaskRecord:
    return TaskRecord(
        id=row["id"],
        job_id=row["job_id"],
        task_type=row["task_type"],
        status=TaskStatus(row["status"]),
        priority=row["priority"],
        payload=row["payload"] or {},
        metadata=row["metadata"] or {},
        retry_count=row["retry_count"],
        max_retries=row["max_retries"],
        next_run_at=row["next_run_at"],
        next_retry_at=row["next_retry_at"],
        account_id=row["account_id"],
        action_type=row["action_type"],
        idempotency_key=row["idempotency_key"],
        result=row["result"],
        error_type=row["error_type"],
        error_message=row["error_message"],
    )


def _execution(row: dict[str, Any]) -> TaskExecutionRecord:
    return TaskExecutionRecord(
        id=row["id"],
        task_id=row["task_id"],
        worker_id=row["worker_id"],
        attempt_number=row["attempt_number"],
        status=ExecutionStatus(row["status"]),
        heartbeat_at=row["heartbeat_at"],
        lease_expires_at=row["lease_expires_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        result=row["result"],
        error_type=row["error_type"],
        error_message=row["error_message"],
    )


def _policy_rule(row: dict[str, Any]) -> PolicyRuleRecord:
    return PolicyRuleRecord(
        id=row["id"],
        account_id=row["account_id"],
        platform=row["platform"],
        action_type=row["action_type"],
        rule_name=row["rule_name"],
        enabled=row["enabled"],
        config=row["config"] or {},
        cooldown_seconds=row["cooldown_seconds"],
        max_actions=row["max_actions"],
        window_seconds=row["window_seconds"],
    )


def _action_log(row: dict[str, Any]) -> ActionLogRecord:
    return ActionLogRecord(
        id=row["id"],
        task_id=row["task_id"],
        action_type=row["action_type"],
        status=row["status"],
        metadata=row["request"] or {},
        created_at=row["created_at"],
    )
