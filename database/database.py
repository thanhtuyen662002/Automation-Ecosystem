from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, AsyncIterator
from uuid import UUID
import uuid

import aiosqlite


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
    task_key: str
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


def _to_json(data: dict | None) -> str:
    return json.dumps(data) if data is not None else "{}"

def _from_json(data: str | bytes | None) -> dict:
    if not data:
        return {}
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return json.loads(data)

def _uuid() -> str:
    return str(uuid.uuid4())


_ACCOUNT_SELECT_COLUMNS = (
    "id, platform, account_handle, profile_url, external_user_id, status, proxy_url, "
    "rate_limit_config, metadata, last_used_at, cookies, user_agent, last_login_at, "
    "session_valid, viewport_width, viewport_height, timezone, locale, browser_data_dir, "
    "risk_score, failed_publish_count, captcha_hit_count, login_redirect_count, "
    "proxy_country, proxy_latency_ms, proxy_validated_at, warmup_sessions_completed, "
    "soft_ban_detected, avatar_url, display_name, created_at, updated_at"
)


class AutomationDatabase:
    def __init__(
        self,
        database_url: str,
        lease_seconds: int = 300,
        retry_config: RetryConfig | None = None,
    ) -> None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be >= 1")
        if database_url.startswith("sqlite+aiosqlite:///"):
            self._db_path = database_url[len("sqlite+aiosqlite:///"):]
        elif database_url.startswith("sqlite:///"):
            self._db_path = database_url[len("sqlite:///"):]
        else:
            self._db_path = database_url
        self._lease_seconds = lease_seconds
        self._retry_config = retry_config or RetryConfig()

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def init_schema(self, schema_path: str) -> None:
        async with self.connection() as conn:
            with open(schema_path, "r", encoding="utf-8") as f:
                schema = f.read()
            await conn.executescript(schema)
            await conn.commit()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[aiosqlite.Connection]:
        async with aiosqlite.connect(self._db_path, isolation_level=None) as conn:
            conn.row_factory = aiosqlite.Row
            # Enable WAL mode for better concurrency
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA synchronous=NORMAL")
            # We use isolation_level=None (autocommit mode) and handle transactions manually
            yield conn

    async def ping(self) -> bool:
        try:
            async with self.connection() as conn:
                result = await conn.execute("SELECT 1 AS ok")
                row = await result.fetchone()
                return bool(row and row["ok"] == 1)
        except Exception:
            return False

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

        # Validation phase: check dependencies
        all_keys = {str(t.get("task_key", f"task_{i}")) for i, t in enumerate(tasks)}
        
        # Build schema registry check
        from core.task_schemas import TASK_SCHEMAS

        def get_all_from_task_refs(obj: Any) -> list[tuple[str, str]]:
            refs = []
            if isinstance(obj, dict):
                if "from_task" in obj and "field" in obj:
                    refs.append((obj["from_task"], obj["field"]))
                for v in obj.values():
                    refs.extend(get_all_from_task_refs(v))
            elif isinstance(obj, list):
                for item in obj:
                    refs.extend(get_all_from_task_refs(item))
            return refs

        task_types = {str(t.get("task_key", f"task_{i}")): t.get("task_type") for i, t in enumerate(tasks)}

        for i, task in enumerate(tasks):
            task_key = str(task.get("task_key", f"task_{i}"))
            task_type = task.get("task_type")
            payload = task.get("payload", {})
            
            # Validate schema
            schema = TASK_SCHEMAS.get(task_type)
            if schema:
                for req in schema.get("required", []):
                    if req not in payload:
                        raise ValidationError(f"Task '{task_key}' missing required field: {req}")

            # Validate explicit depends_on
            dependencies = task.get("depends_on") or []
            for dep in dependencies:
                if str(dep) not in all_keys:
                    raise ValidationError(f"Invalid dependency: task_key '{dep}' not found in job definition")

            # Validate dynamic payload refs
            dynamic_refs = get_all_from_task_refs(payload)
            for ref_task, ref_field in dynamic_refs:
                if ref_task not in all_keys:
                    raise ValidationError(f"Task '{task_key}' references unknown task '{ref_task}'")
                ref_task_type = task_types.get(ref_task)
                ref_schema = TASK_SCHEMAS.get(ref_task_type)
                if ref_schema and ref_field not in ref_schema.get("output", []):
                    raise ValidationError(f"Task '{task_key}' references invalid field '{ref_field}' from task '{ref_task}'")

        account_validations = []
        for i, task in enumerate(tasks):
            task_type = task.get("task_type", "")
            if task_type.startswith("publish_"):
                account_id = task.get("payload", {}).get("account_id")
                if not account_id:
                    raise ValidationError(f"Task '{task.get('task_key')}' of type '{task_type}' MUST contain 'account_id' in payload")
                expected_platform = task_type.replace("publish_", "")
                account_validations.append((str(account_id), expected_platform, str(task.get("task_key"))))

        job_id = _uuid()
        async with self.connection() as conn:
            if account_validations:
                for acc_id, expected_platform, tk_key in account_validations:
                    cursor = await conn.execute("SELECT platform, status FROM accounts WHERE id = ?", (acc_id,))
                    row = await cursor.fetchone()
                    if not row:
                        raise ValidationError(f"Task '{tk_key}': account_id '{acc_id}' does not exist")
                    if row["platform"] != expected_platform:
                        raise ValidationError(f"Task '{tk_key}': account_id '{acc_id}' belongs to platform '{row['platform']}', but task requires '{expected_platform}'")

            await conn.execute("BEGIN IMMEDIATE")
            try:
                await conn.execute(
                    """
                    INSERT INTO jobs (id, job_key, workflow_name, priority, input, metadata)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (job_id, job_key, workflow_name, priority, _to_json(input_data), _to_json(metadata)),
                )
                
                job_result = await conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
                job_row = await job_result.fetchone()

                task_key_to_id: dict[str, str] = {}
                inserted_tasks: list[TaskRecord] = []
                pending_dependencies: list[tuple[str, list[str]]] = []

                for index, task in enumerate(tasks):
                    task_type = str(task.get("task_type") or "").strip()
                    if not task_type:
                        raise ValidationError(f"tasks[{index}].task_type cannot be empty")
                    
                    task_key = str(task.get("task_key", f"task_{index}")).strip()
                    if task_key in task_key_to_id:
                        raise ValidationError(f"duplicate task_key: {task_key}")

                    task_id = _uuid()
                    parent_task_id = str(task.get("parent_task_id")) if task.get("parent_task_id") else None
                    account_id = str(task.get("account_id")) if task.get("account_id") else None
                    if account_id is None and task_type.startswith("publish_"):
                        payload_account_id = (task.get("payload") or {}).get("account_id")
                        account_id = str(payload_account_id) if payload_account_id else None
                    action_type = str(task.get("action_type")) if task.get("action_type") else None
                    idempotency_key = str(task.get("idempotency_key")) if task.get("idempotency_key") else None
                    next_run_at = task.get("next_run_at") or datetime.now(UTC).isoformat()

                    await conn.execute(
                        """
                        INSERT INTO tasks (
                            id, task_key, job_id, parent_task_id, account_id, task_type, action_type,
                            status, priority, payload, metadata, max_retries,
                            next_run_at, idempotency_key
                        )
                        VALUES (
                            ?, ?, ?, ?, ?, ?, ?,
                            'PENDING', ?, ?, ?, ?,
                            ?, ?
                        )
                        """,
                        (
                            task_id, task_key, job_id, parent_task_id, account_id, task_type, action_type,
                            int(task.get("priority", 0)), _to_json(task.get("payload")),
                            _to_json(task.get("metadata")), int(task.get("max_retries", 3)),
                            next_run_at, idempotency_key
                        ),
                    )
                    task_result = await conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
                    task_row = await task_result.fetchone()
                    task_key_to_id[task_key] = task_id
                    dependency_keys = [str(value) for value in task.get("depends_on") or []]
                    pending_dependencies.append((task_id, dependency_keys))
                    inserted_tasks.append(_task(task_row))

                for tid, dependency_keys in pending_dependencies:
                    for dependency_key in dependency_keys:
                        depends_on_task_id = task_key_to_id.get(dependency_key)
                        if depends_on_task_id is None:
                            raise ValidationError(f"unknown dependency task_key: {dependency_key}")
                        await conn.execute(
                            """
                            INSERT OR IGNORE INTO task_dependencies (task_id, depends_on_task_id)
                            VALUES (?, ?)
                            """,
                            (tid, depends_on_task_id),
                        )
                await conn.execute("COMMIT")
                return JobDetailRecord(job=_job(job_row), tasks=inserted_tasks)
            except Exception as e:
                await conn.execute("ROLLBACK")
                raise e

    async def get_job_detail(self, job_id: UUID) -> JobDetailRecord | None:
        async with self.connection() as conn:
            job_result = await conn.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),))
            job_row = await job_result.fetchone()
            if job_row is None:
                return None
            task_result = await conn.execute(
                """
                SELECT * FROM tasks WHERE job_id = ? ORDER BY priority DESC, created_at ASC
                """,
                (str(job_id),),
            )
            return JobDetailRecord(job=_job(job_row), tasks=[_task(row) for row in await task_result.fetchall()])

    async def list_jobs(self, limit: int, offset: int) -> list[JobRecord]:
        async with self.connection() as conn:
            result = await conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
            return [_job(row) for row in await result.fetchall()]

    async def get_task_statuses_for_jobs(
        self, job_ids: list[str]
    ) -> dict[str, dict[str, str]]:
        """Batch-fetch per-task statuses for multiple jobs.

        Returns: {job_id: {task_key: status_string}}
        task_key falls back to the suffix of task_type when task_key is NULL.
        """
        if not job_ids:
            return {}
        placeholders = ",".join("?" * len(job_ids))
        async with self.connection() as conn:
            result = await conn.execute(
                f"SELECT job_id, task_key, task_type, status FROM tasks"
                f" WHERE job_id IN ({placeholders})",
                job_ids,
            )
            rows = await result.fetchall()
        by_job: dict[str, dict[str, str]] = {}
        for row in rows:
            jid = str(row["job_id"])
            # task_key may be NULL for ad-hoc tasks; fallback to last segment of task_type
            key: str = row["task_key"] or str(row["task_type"]).rsplit(".", 1)[-1]
            by_job.setdefault(jid, {})[key] = str(row["status"])
        return by_job


    async def get_task(self, task_id: UUID) -> TaskRecord | None:
        async with self.connection() as conn:
            result = await conn.execute("SELECT * FROM tasks WHERE id = ?", (str(task_id),))
            row = await result.fetchone()
            return _task(row) if row else None

    async def get_task_result(self, task_id: str) -> dict:
        async with self.connection() as conn:
            result = await conn.execute("SELECT status, result FROM tasks WHERE id = ?", (task_id,))
            row = await result.fetchone()
            if not row:
                raise NotFoundError(f"Task {task_id} not found")
            if row["status"] != TaskStatus.SUCCESS.value:
                raise DatabaseError(f"Dependency task {task_id} is not in SUCCESS state (current: {row['status']})")
            return _from_json(row["result"]) if row["result"] else {}

    async def mark_task_for_retry(self, task_id: str, execution_id: str, error: Exception, delay_seconds: int) -> None:
        async with self.connection() as conn:
            next_run = datetime.now(UTC) + timedelta(seconds=delay_seconds)

            await conn.execute(
                """
                UPDATE tasks
                SET status = 'RETRY',
                    retry_count = retry_count + 1,
                    next_retry_at = ?,
                    next_run_at = ?,
                    error_type = ?,
                    error_message = ?
                WHERE id = ?
                """,
                (
                    next_run.isoformat(),
                    next_run.isoformat(),
                    type(error).__name__,
                    str(error)[:4000],
                    str(task_id),
                )
            )

            await conn.execute(
                """
                UPDATE task_executions
                SET status = 'failed',
                    completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (str(execution_id),)
            )

    async def get_task_result_by_key(self, job_id: str, task_key: str) -> dict:
        async with self.connection() as conn:
            result = await conn.execute(
                "SELECT status, result FROM tasks WHERE job_id = ? AND task_key = ?",
                (job_id, task_key),
            )
            row = await result.fetchone()
            if not row:
                raise NotFoundError(f"Task '{task_key}' not found.")
                
            return {
                "status": row["status"],
                "result": _from_json(row["result"]) if row["result"] else None
            }

    async def get_task_results_bulk(self, job_id: str, task_keys: list[str]) -> dict[str, dict]:
        if not task_keys:
            return {}
            
        placeholders = ",".join("?" * len(task_keys))
        query = f"SELECT task_key, status, result FROM tasks WHERE job_id = ? AND task_key IN ({placeholders})"
        
        async with self.connection() as conn:
            result = await conn.execute(query, [job_id] + task_keys)
            rows = await result.fetchall()
            
            return {
                row["task_key"]: {
                    "status": row["status"],
                    "result": _from_json(row["result"]) if row["result"] else None
                }
                for row in rows
            }

    async def list_tasks(
        self,
        limit: int,
        offset: int,
        status: TaskStatus | None = None,
        task_type: str | None = None,
        job_id: UUID | None = None,
    ) -> list[TaskRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if task_type is not None:
            clauses.append("task_type = ?")
            params.append(task_type)
        if job_id is not None:
            clauses.append("job_id = ?")
            params.append(str(job_id))

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        async with self.connection() as conn:
            result = await conn.execute(
                f"SELECT * FROM tasks {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                tuple(params),
            )
            return [_task(row) for row in await result.fetchall()]

    async def reset_failed_task_for_retry(self, task_id: UUID) -> TaskRecord:
        async with self.connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                task_result = await conn.execute("SELECT * FROM tasks WHERE id = ?", (str(task_id),))
                task_row = await task_result.fetchone()
                if task_row is None:
                    raise NotFoundError(f"Task not found: {task_id}")
                status = TaskStatus(task_row["status"])
                if status != TaskStatus.FAILED:
                    raise InvalidStateTransition(f"Only FAILED tasks can be retried: {status.value}")
                if int(task_row["retry_count"]) >= int(task_row["max_retries"]):
                    raise ConflictError(f"Task retry budget exhausted: {task_id}")
                _ensure_task_transition(status, TaskStatus.PENDING)
                
                await conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'PENDING', next_run_at = CURRENT_TIMESTAMP,
                        next_retry_at = NULL, error_type = NULL, error_message = NULL,
                        completed_at = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (str(task_id),),
                )
                updated_result = await conn.execute("SELECT * FROM tasks WHERE id = ?", (str(task_id),))
                updated = await updated_result.fetchone()
                await conn.execute("COMMIT")
                return _task(updated)
            except Exception as e:
                await conn.execute("ROLLBACK")
                raise e

    async def get_system_stats(self) -> SystemStatsRecord:
        async with self.connection() as conn:
            result = await conn.execute(
                """
                SELECT count(*) AS total_tasks,
                       SUM(CASE WHEN status = 'RUNNING' THEN 1 ELSE 0 END) AS running,
                       SUM(CASE WHEN status IN ('PENDING', 'READY', 'RETRY') THEN 1 ELSE 0 END) AS pending,
                       SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) AS failed,
                       SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END) AS success
                FROM tasks
                """
            )
            row = await result.fetchone()
            if row is None:
                return SystemStatsRecord(0, 0, 0, 0, 0)
            return SystemStatsRecord(
                total_tasks=row["total_tasks"] or 0,
                running=row["running"] or 0,
                pending=row["pending"] or 0,
                failed=row["failed"] or 0,
                success=row["success"] or 0,
            )

    async def acquire_task_with_execution(self, task_id: UUID, worker_id: str) -> AcquiredTask | None:
        async with self.connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                task_result = await conn.execute(
                    """
                    SELECT * FROM tasks
                    WHERE id = ? AND status = 'READY'
                      AND datetime(next_run_at) <= datetime('now')
                      AND retry_count < max_retries
                      AND NOT EXISTS (
                          SELECT 1 FROM task_dependencies dep
                          JOIN tasks parent ON parent.id = dep.depends_on_task_id
                          WHERE dep.task_id = tasks.id AND parent.status <> 'SUCCESS'
                      )
                    """,
                    (str(task_id),),
                )
                task_row = await task_result.fetchone()
                if task_row is None:
                    await conn.execute("ROLLBACK")
                    return None

                running_result = await conn.execute(
                    "SELECT id FROM task_executions WHERE task_id = ? AND status = 'running'",
                    (str(task_id),),
                )
                if await running_result.fetchone() is not None:
                    await conn.execute("ROLLBACK")
                    return None

                _ensure_task_transition(TaskStatus(task_row["status"]), TaskStatus.RUNNING)
                # Use total execution count + 1 to guarantee uniqueness
                # (retry_count+1 can collide when tasks are re-acquired after crashes)
                exec_count_cur = await conn.execute(
                    "SELECT COUNT(*) as cnt FROM task_executions WHERE task_id = ?",
                    (str(task_id),)
                )
                exec_count_row = await exec_count_cur.fetchone()
                attempt_number = (exec_count_row["cnt"] if exec_count_row else 0) + 1
                lease_expires_at = (datetime.now(UTC) + timedelta(seconds=self._lease_seconds)).isoformat()

                await conn.execute(
                    "UPDATE tasks SET status = 'RUNNING', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (str(task_id),),
                )
                
                execution_id = _uuid()
                await conn.execute(
                    """
                    INSERT INTO task_executions (
                        id, task_id, worker_id, attempt_number, status,
                        heartbeat_at, lease_expires_at, started_at
                    )
                    VALUES (?, ?, ?, ?, 'running', CURRENT_TIMESTAMP, ?, CURRENT_TIMESTAMP)
                    """,
                    (execution_id, str(task_id), worker_id, attempt_number, lease_expires_at),
                )
                
                updated_task = await (await conn.execute("SELECT * FROM tasks WHERE id = ?", (str(task_id),))).fetchone()
                execution = await (await conn.execute("SELECT * FROM task_executions WHERE id = ?", (execution_id,))).fetchone()
                await conn.execute("COMMIT")
                return AcquiredTask(task=_task(updated_task), execution=_execution(execution))
            except Exception as e:
                await conn.execute("ROLLBACK")
                raise e

    async def acquire_ready_tasks_batch(
        self,
        limit: int,
        worker_id: str,
        max_per_task_type: int | None = None,
        max_per_account: int | None = None,
    ) -> list[AcquiredTask]:
        acquired: list[AcquiredTask] = []
        async with self.connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                # Basic query (SQLite compatible window functions supported in 3.25+)
                candidate_result = await conn.execute(
                    """
                    SELECT id FROM tasks t
                    WHERE t.status = 'READY'
                      AND datetime(t.next_run_at) <= datetime('now')
                      AND t.retry_count < t.max_retries
                      AND NOT EXISTS (
                          SELECT 1 FROM task_dependencies dep
                          JOIN tasks parent ON parent.id = dep.depends_on_task_id
                          WHERE dep.task_id = t.id AND parent.status <> 'SUCCESS'
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM task_executions ex
                          WHERE ex.task_id = t.id AND ex.status = 'running'
                      )
                    ORDER BY t.priority DESC, t.next_run_at ASC, t.created_at ASC
                    LIMIT ?
                    """,
                    (limit,),
                )
                task_rows = await candidate_result.fetchall()
                for task_row in task_rows:
                    task_id = task_row["id"]
                    t_result = await conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
                    t_full = await t_result.fetchone()
                    
                    _ensure_task_transition(TaskStatus(t_full["status"]), TaskStatus.RUNNING)
                    # Use total execution count + 1 to guarantee uniqueness
                    exec_count_cur = await conn.execute(
                        "SELECT COUNT(*) as cnt FROM task_executions WHERE task_id = ?",
                        (task_id,)
                    )
                    exec_count_row = await exec_count_cur.fetchone()
                    attempt_number = (exec_count_row["cnt"] if exec_count_row else 0) + 1
                    lease_expires_at = (datetime.now(UTC) + timedelta(seconds=self._lease_seconds)).isoformat()

                    await conn.execute(
                        "UPDATE tasks SET status = 'RUNNING', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (task_id,),
                    )
                    
                    execution_id = _uuid()
                    await conn.execute(
                        """
                        INSERT INTO task_executions (
                            id, task_id, worker_id, attempt_number, status,
                            heartbeat_at, lease_expires_at, started_at
                        )
                        VALUES (?, ?, ?, ?, 'running', CURRENT_TIMESTAMP, ?, CURRENT_TIMESTAMP)
                        """,
                        (execution_id, task_id, worker_id, attempt_number, lease_expires_at),
                    )
                    
                    updated_task = await (await conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))).fetchone()
                    execution = await (await conn.execute("SELECT * FROM task_executions WHERE id = ?", (execution_id,))).fetchone()
                    acquired.append(AcquiredTask(task=_task(updated_task), execution=_execution(execution)))
                await conn.execute("COMMIT")
            except Exception as e:
                await conn.execute("ROLLBACK")
                raise e
        return acquired

    async def get_task_and_lock(self, task_id: UUID, worker_id: str) -> AcquiredTask | None:
        return await self.acquire_task_with_execution(task_id, worker_id)

    async def get_ready_tasks(self, limit: int) -> list[TaskRecord]:
        async with self.connection() as conn:
            rows = await conn.execute(
                """
                SELECT t.* FROM tasks t
                WHERE t.status = 'READY'
                  AND datetime(t.next_run_at) <= datetime('now')
                  AND t.retry_count < t.max_retries
                  AND NOT EXISTS (
                      SELECT 1 FROM task_dependencies dep
                      JOIN tasks parent ON parent.id = dep.depends_on_task_id
                      WHERE dep.task_id = t.id AND parent.status <> 'SUCCESS'
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM task_executions ex
                      WHERE ex.task_id = t.id AND ex.status = 'running'
                  )
                ORDER BY t.priority DESC, t.next_run_at ASC, t.created_at ASC
                LIMIT ?
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
        async with self.connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                task_row = await self._lock_task(conn, str(task_id))
                execution_row = await self._lock_execution(conn, str(execution_id))
                self._validate_execution_belongs_to_task(execution_row, str(task_id))
                self._validate_running_execution(execution_row)
                _ensure_task_transition(TaskStatus(task_row["status"]), TaskStatus.SUCCESS)

                await conn.execute(
                    "UPDATE task_executions SET status = 'succeeded', result = ?, completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (_to_json(result), str(execution_id)),
                )
                await conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'SUCCESS', result = ?, completed_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP, error_type = NULL, error_message = NULL
                    WHERE id = ?
                    """,
                    (_to_json(result), str(task_id)),
                )
                
                updated_task = await (await conn.execute("SELECT * FROM tasks WHERE id = ?", (str(task_id),))).fetchone()
                await self._maybe_complete_job(conn, updated_task["job_id"])
                await conn.execute("COMMIT")
                return _task(updated_task)
            except Exception as e:
                await conn.execute("ROLLBACK")
                raise e

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
        async with self.connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                task_row = await self._lock_task(conn, str(task_id))
                execution_row = await self._lock_execution(conn, str(execution_id))
                self._validate_execution_belongs_to_task(execution_row, str(task_id))
                self._validate_running_execution(execution_row)

                current_status = TaskStatus(task_row["status"])
                if current_status != TaskStatus.RUNNING:
                    raise InvalidStateTransition(f"Task must be RUNNING before failure: {current_status.value}")

                retry_count = int(task_row["retry_count"])
                should_retry = retry_count < int(task_row["max_retries"])
                target_status = TaskStatus.RETRY if should_retry else TaskStatus.FAILED
                _ensure_task_transition(current_status, target_status)
                next_run_at = (
                    (datetime.now(UTC) + timedelta(seconds=retry_config.delay_for_attempt(retry_count))).isoformat()
                    if should_retry
                    else task_row["next_run_at"]
                )
                execution_status = ExecutionStatus.TIMED_OUT if timed_out else ExecutionStatus.FAILED

                await conn.execute(
                    """
                    UPDATE task_executions
                    SET status = ?, error_type = ?, error_message = ?, completed_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (execution_status.value, error_type, error_message[:4000], str(execution_id)),
                )
                await conn.execute(
                    """
                    UPDATE tasks
                    SET status = ?, next_run_at = ?, next_retry_at = ?,
                        completed_at = CASE WHEN ? = 'FAILED' THEN CURRENT_TIMESTAMP ELSE completed_at END,
                        updated_at = CURRENT_TIMESTAMP, error_type = ?, error_message = ?
                    WHERE id = ?
                    """,
                    (
                        target_status.value, next_run_at, next_run_at if should_retry else None,
                        target_status.value, error_type, error_message[:4000], str(task_id)
                    ),
                )
                
                updated_task = await (await conn.execute("SELECT * FROM tasks WHERE id = ?", (str(task_id),))).fetchone()
                if target_status == TaskStatus.FAILED:
                    await self._maybe_fail_job(conn, updated_task["job_id"])
                await conn.execute("COMMIT")
                return _task(updated_task)
            except Exception as e:
                await conn.execute("ROLLBACK")
                raise e

    async def update_execution_heartbeat(self, execution_id: UUID) -> bool:
        async with self.connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                lease_expires_at = (datetime.now(UTC) + timedelta(seconds=self._lease_seconds)).isoformat()
                await conn.execute(
                    "UPDATE task_executions SET heartbeat_at = CURRENT_TIMESTAMP, lease_expires_at = ? WHERE id = ? AND status = 'running'",
                    (lease_expires_at, str(execution_id)),
                )
                result = await conn.execute("SELECT id FROM task_executions WHERE id = ?", (str(execution_id),))
                found = await result.fetchone() is not None
                await conn.execute("COMMIT")
                return found
            except Exception as e:
                await conn.execute("ROLLBACK")
                raise e

    async def update_heartbeat(self, task_id: UUID, worker_id: str) -> bool:
        async with self.connection() as conn:
            lease_expires_at = (datetime.now(UTC) + timedelta(seconds=self._lease_seconds)).isoformat()
            await conn.execute(
                "UPDATE task_executions SET heartbeat_at = CURRENT_TIMESTAMP, lease_expires_at = ? WHERE task_id = ? AND worker_id = ? AND status = 'running'",
                (lease_expires_at, str(task_id), worker_id),
            )
            result = await conn.execute("SELECT id FROM task_executions WHERE task_id = ? AND worker_id = ? AND status = 'running'", (str(task_id), worker_id))
            return await result.fetchone() is not None

    async def recover_expired_executions(self, limit: int = 100) -> list[TaskRecord]:
        recovered: list[TaskRecord] = []
        async with self.connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                expired_result = await conn.execute(
                    """
                    SELECT ex.id, ex.task_id FROM task_executions ex
                    JOIN tasks t ON t.id = ex.task_id
                    WHERE ex.status = 'running' AND ex.lease_expires_at < CURRENT_TIMESTAMP AND t.status = 'RUNNING'
                    ORDER BY ex.lease_expires_at ASC LIMIT ?
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
                    # Parse lease_expires_at
                    # (simplification for python logic datetime parse)
                    if execution_row["lease_expires_at"] >= datetime.now(UTC).isoformat():
                        continue
                        
                    retry_count = int(task_row["retry_count"])
                    should_retry = retry_count < int(task_row["max_retries"])
                    target_status = TaskStatus.RETRY if should_retry else TaskStatus.FAILED
                    next_run_at = (
                        (datetime.now(UTC) + timedelta(seconds=self._retry_config.delay_for_attempt(retry_count))).isoformat()
                        if should_retry else task_row["next_run_at"]
                    )
                    await conn.execute(
                        """
                        UPDATE task_executions
                        SET status = 'failed', error_type = 'LeaseExpired',
                            error_message = 'Execution heartbeat lease expired', completed_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND status = 'running'
                        """,
                        (execution_row["id"],),
                    )
                    await conn.execute(
                        """
                        UPDATE tasks
                        SET status = ?, next_run_at = ?, next_retry_at = ?,
                            completed_at = CASE WHEN ? = 'FAILED' THEN CURRENT_TIMESTAMP ELSE completed_at END,
                            error_type = 'LeaseExpired', error_message = 'Execution heartbeat lease expired',
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (target_status.value, next_run_at, next_run_at if should_retry else None, target_status.value, task_row["id"]),
                    )
                    updated_task = await (await conn.execute("SELECT * FROM tasks WHERE id = ?", (task_row["id"],))).fetchone()
                    if updated_task:
                        recovered.append(_task(updated_task))
                        if target_status == TaskStatus.FAILED:
                            await self._maybe_fail_job(conn, updated_task["job_id"])
                await conn.execute("COMMIT")
            except Exception as e:
                await conn.execute("ROLLBACK")
                raise e
        return recovered

    async def get_task_dependencies(self, task_id: UUID) -> list[DependencyRecord]:
        async with self.connection() as conn:
            rows = await conn.execute(
                """
                SELECT dep.task_id, dep.depends_on_task_id, parent.status AS dependency_status
                FROM task_dependencies dep
                JOIN tasks parent ON parent.id = dep.depends_on_task_id
                WHERE dep.task_id = ? ORDER BY dep.created_at ASC
                """,
                (str(task_id),),
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
        async with self.connection() as conn:
            result = await conn.execute(
                """
                SELECT NOT EXISTS (
                    SELECT 1 FROM task_dependencies dep
                    JOIN tasks parent ON parent.id = dep.depends_on_task_id
                    WHERE dep.task_id = ? AND parent.status <> 'SUCCESS'
                ) AS ready
                """,
                (str(task_id),),
            )
            row = await result.fetchone()
            return bool(row and row["ready"])

    async def log_action(self, task_id: UUID, action_type: str, metadata: dict[str, Any]) -> ActionLogRecord:
        async with self.connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                task_result = await conn.execute("SELECT job_id, account_id FROM tasks WHERE id = ?", (str(task_id),))
                task = await task_result.fetchone()
                if task is None:
                    raise DatabaseError(f"Task not found for action log: {task_id}")
                
                log_id = _uuid()
                await conn.execute(
                    """
                    INSERT INTO action_logs (
                        id, job_id, task_id, account_id, action_type, status, request
                    )
                    VALUES (?, ?, ?, ?, ?, 'attempted', ?)
                    """,
                    (log_id, task["job_id"], str(task_id), task["account_id"], action_type, _to_json(metadata)),
                )
                row = await (await conn.execute("SELECT * FROM action_logs WHERE id = ?", (log_id,))).fetchone()
                await conn.execute("COMMIT")
                return _action_log(row)
            except Exception as e:
                await conn.execute("ROLLBACK")
                raise e

    async def get_account_usage(self, account_id: UUID) -> AccountUsage:
        async with self.connection() as conn:
            result = await conn.execute(
                """
                SELECT ? AS account_id,
                       count(*) AS total_actions,
                       SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS successful_actions,
                       SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_actions,
                       SUM(CASE WHEN status = 'blocked' THEN 1 ELSE 0 END) AS blocked_actions,
                       max(created_at) AS last_action_at
                FROM action_logs WHERE account_id = ?
                """,
                (str(account_id), str(account_id)),
            )
            row = await result.fetchone()
            if row is None:
                return AccountUsage(account_id, 0, 0, 0, 0, None)
            return AccountUsage(
                account_id=row["account_id"],
                total_actions=row["total_actions"] or 0,
                successful_actions=row["successful_actions"] or 0,
                failed_actions=row["failed_actions"] or 0,
                blocked_actions=row["blocked_actions"] or 0,
                last_action_at=row["last_action_at"],
            )

    # ── Accounts CRUD ────────────────────────────────────────────────────────────

    async def list_accounts(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        async with self.connection() as conn:
            result = await conn.execute(
                f"SELECT {_ACCOUNT_SELECT_COLUMNS} "
                "FROM accounts ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
            return [dict(row) for row in await result.fetchall()]

    async def create_account(
        self,
        platform: str,
        account_handle: str,
        proxy_url: str | None = None,
        profile_url: str | None = None,
        external_user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        account_id = _uuid()
        async with self.connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                await conn.execute(
                    """
                    INSERT INTO accounts (
                        id, platform, account_handle, profile_url, external_user_id,
                        status, proxy_url, metadata
                    )
                    VALUES (?, ?, ?, ?, ?, 'healthy', ?, ?)
                    """,
                    (
                        account_id,
                        platform,
                        account_handle,
                        profile_url,
                        external_user_id,
                        proxy_url,
                        _to_json(metadata or {}),
                    ),
                )
                row = await (await conn.execute(
                    f"SELECT {_ACCOUNT_SELECT_COLUMNS} FROM accounts WHERE id = ?",
                    (account_id,),
                )).fetchone()
                await conn.execute("COMMIT")
                return dict(row)
            except Exception as e:
                await conn.execute("ROLLBACK")
                raise e

    async def get_account(self, account_id: str) -> dict[str, Any] | None:
        async with self.connection() as conn:
            result = await conn.execute(
                f"SELECT {_ACCOUNT_SELECT_COLUMNS} FROM accounts WHERE id = ?",
                (account_id,),
            )
            row = await result.fetchone()
            return dict(row) if row else None

    async def update_account_status(self, account_id: str, status: str) -> dict[str, Any] | None:
        async with self.connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                await conn.execute(
                    "UPDATE accounts SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (status, account_id),
                )
                row = await (await conn.execute(
                    f"SELECT {_ACCOUNT_SELECT_COLUMNS} FROM accounts WHERE id = ?",
                    (account_id,),
                )).fetchone()
                await conn.execute("COMMIT")
                return dict(row) if row else None
            except Exception as e:
                await conn.execute("ROLLBACK")
                raise e

    async def update_account_fields(self, account_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        allowed_fields = {
            "platform",
            "account_handle",
            "profile_url",
            "external_user_id",
            "proxy_url",
            "metadata",
            "status",
        }
        unknown = set(fields) - allowed_fields
        if unknown:
            raise ValidationError(f"Unsupported account fields: {sorted(unknown)}")
        if not fields:
            return await self.get_account(account_id)

        assignments: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            params.append(_to_json(value) if key == "metadata" else value)
        params.append(account_id)

        async with self.connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                result = await conn.execute(
                    f"""
                    UPDATE accounts
                    SET {', '.join(assignments)}, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    tuple(params),
                )
                if result.rowcount == 0:
                    await conn.execute("ROLLBACK")
                    return None
                row = await (await conn.execute(
                    f"SELECT {_ACCOUNT_SELECT_COLUMNS} FROM accounts WHERE id = ?",
                    (account_id,),
                )).fetchone()
                await conn.execute("COMMIT")
                return dict(row) if row else None
            except Exception as e:
                await conn.execute("ROLLBACK")
                raise e

    async def delete_account(self, account_id: str) -> bool:
        async with self.connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                result = await conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
                deleted = result.rowcount > 0
                await conn.execute("COMMIT")
                return deleted
            except Exception as e:
                await conn.execute("ROLLBACK")
                raise e

    # ── Account Session Management ───────────────────────────────────────────────

    async def save_account_session(
        self,
        account_id: str,
        cookies_encrypted: str,
        user_agent: str,
        viewport_width: int | None = None,
        viewport_height: int | None = None,
        timezone: str | None = None,
        locale: str | None = None,
    ) -> dict[str, Any] | None:
        """Save encrypted session cookies and mark session as valid.

        Optionally also persists browser fingerprint values captured during login.
        Fingerprint values are stored stably — do NOT change them on reconnect
        unless the user explicitly resets their account identity.
        """
        async with self.connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                if viewport_width and viewport_height and timezone and locale:
                    await conn.execute(
                        """
                        UPDATE accounts
                        SET cookies = ?, user_agent = ?, last_login_at = CURRENT_TIMESTAMP,
                            session_valid = 1, updated_at = CURRENT_TIMESTAMP,
                            viewport_width = ?, viewport_height = ?, timezone = ?, locale = ?
                        WHERE id = ?
                        """,
                        (cookies_encrypted, user_agent,
                         viewport_width, viewport_height, timezone, locale,
                         account_id),
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE accounts
                        SET cookies = ?, user_agent = ?, last_login_at = CURRENT_TIMESTAMP,
                            session_valid = 1, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (cookies_encrypted, user_agent, account_id),
                    )
                row = await (await conn.execute(
                    f"SELECT {_ACCOUNT_SELECT_COLUMNS} FROM accounts WHERE id = ?",
                    (account_id,),
                )).fetchone()
                await conn.execute("COMMIT")
                return dict(row) if row else None
            except Exception as e:
                await conn.execute("ROLLBACK")
                raise e

    async def update_account_profile(
        self,
        account_id: str,
        avatar_url: str | None = None,
        display_name: str | None = None,
        profile_url: str | None = None,
        external_user_id: str | None = None,
        account_handle: str | None = None,
    ) -> dict[str, Any] | None:
        """Save avatar URL and display name extracted after successful browser login."""
        async with self.connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                await conn.execute(
                    """
                    UPDATE accounts
                    SET avatar_url = COALESCE(?, avatar_url),
                        display_name = COALESCE(?, display_name),
                        profile_url = COALESCE(?, profile_url),
                        external_user_id = COALESCE(?, external_user_id),
                        account_handle = COALESCE(?, account_handle),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        avatar_url or None,
                        display_name or None,
                        profile_url or None,
                        external_user_id or None,
                        account_handle or None,
                        account_id,
                    ),
                )
                row = await (await conn.execute(
                    f"SELECT {_ACCOUNT_SELECT_COLUMNS} FROM accounts WHERE id = ?",
                    (account_id,),
                )).fetchone()
                await conn.execute("COMMIT")
                return dict(row) if row else None
            except Exception as e:
                await conn.execute("ROLLBACK")
                raise e


    async def get_account_session(self, account_id: str) -> dict[str, Any] | None:
        """Return session data + proxy + fingerprint for an account.

        Returns None if account not found.
        Caller decrypts cookies using session_crypto.
        """
        async with self.connection() as conn:
            result = await conn.execute(
                "SELECT id, platform, account_handle, profile_url, external_user_id, status, "
                "proxy_url, proxy_country, cookies, user_agent, session_valid, "
                "last_login_at, last_used_at, browser_data_dir, "
                "viewport_width, viewport_height, timezone, locale, soft_ban_detected "
                "FROM accounts WHERE id = ?",
                (account_id,),
            )
            row = await result.fetchone()
            if not row:
                return None
            return dict(row)

    async def get_account_identity_profile(self, account_id: str) -> dict[str, Any] | None:
        """Return the persisted IdentityProfile dict from account metadata."""
        async with self.connection() as conn:
            result = await conn.execute(
                "SELECT metadata FROM accounts WHERE id = ?",
                (account_id,),
            )
            row = await result.fetchone()
            if not row:
                return None
            metadata = _from_json(row["metadata"])
            profile = metadata.get("identity_profile")
            return profile if isinstance(profile, dict) else None

    async def save_account_identity_profile(self, account_id: str, profile: dict[str, Any]) -> None:
        """Persist account IdentityProfile in metadata without overwriting other keys."""
        async with self.connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                row = await (await conn.execute(
                    "SELECT metadata FROM accounts WHERE id = ?",
                    (account_id,),
                )).fetchone()
                if not row:
                    await conn.execute("ROLLBACK")
                    return
                metadata = _from_json(row["metadata"])
                metadata["identity_profile"] = profile
                if profile.get("identity_id"):
                    metadata["identity_profile_id"] = profile.get("identity_id")
                await conn.execute(
                    "UPDATE accounts SET metadata = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (_to_json(metadata), account_id),
                )
                await conn.execute("COMMIT")
            except Exception as e:
                await conn.execute("ROLLBACK")
                raise e

    async def record_login_diagnostic(
        self,
        account_id: str,
        diagnostic: str,
        *,
        platform: str | None = None,
        status: str | None = None,
    ) -> None:
        """Record a non-secret login diagnostic in metadata.

        This deliberately does not clear existing cookies. Callers decide
        separately whether the session should be invalidated.
        """
        async with self.connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                row = await (await conn.execute(
                    "SELECT metadata FROM accounts WHERE id = ?",
                    (account_id,),
                )).fetchone()
                if not row:
                    await conn.execute("ROLLBACK")
                    return
                metadata = _from_json(row["metadata"])
                metadata["login_diagnostic"] = {
                    "diagnostic": diagnostic,
                    "platform": platform,
                    "recorded_at": datetime.now(UTC).isoformat(),
                }
                if status:
                    await conn.execute(
                        "UPDATE accounts SET metadata = ?, status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (_to_json(metadata), status, account_id),
                    )
                else:
                    await conn.execute(
                        "UPDATE accounts SET metadata = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (_to_json(metadata), account_id),
                    )
                await conn.execute("COMMIT")
            except Exception as e:
                await conn.execute("ROLLBACK")
                raise e

    async def invalidate_account_session(self, account_id: str) -> None:
        """Mark account session as invalid (e.g. after detecting logout)."""
        async with self.connection() as conn:
            await conn.execute(
                "UPDATE accounts SET session_valid = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (account_id,),
            )

    async def has_running_task_for_account(self, account_id: str) -> bool:
        """Return True if there is already a RUNNING publish task for this account.
        Used to enforce the no-parallel-publish-per-account safety rule.
        """
        async with self.connection() as conn:
            result = await conn.execute(
                "SELECT COUNT(*) as cnt FROM tasks "
                "WHERE account_id = ? AND status = 'RUNNING' "
                "AND task_type LIKE 'publish_%'",
                (account_id,),
            )
            row = await result.fetchone()
            return row["cnt"] > 0 if row else False

    async def save_account_fingerprint(
        self,
        account_id: str,
        viewport_width: int,
        viewport_height: int,
        timezone: str,
        locale: str,
    ) -> None:
        """Persist stable browser fingerprint values captured during login.
        These values are reused on every subsequent run for identity consistency.
        """
        async with self.connection() as conn:
            await conn.execute(
                """
                UPDATE accounts
                SET viewport_width = ?, viewport_height = ?, timezone = ?, locale = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (viewport_width, viewport_height, timezone, locale, account_id),
            )

    async def update_account_last_used(self, account_id: str) -> None:
        """Stamp last_used_at = now() when a publish task completes.
        Used by rate-limiting logic to enforce minimum intervals between posts.
        """
        async with self.connection() as conn:
            await conn.execute(
                "UPDATE accounts SET last_used_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (account_id,),
            )

    async def get_seconds_since_last_publish(self, account_id: str) -> float | None:
        """Return seconds elapsed since last successful publish for this account.

        Returns None if the account has never published.
        Used to enforce minimum cooldown between posts.
        """
        async with self.connection() as conn:
            result = await conn.execute(
                "SELECT last_used_at FROM accounts WHERE id = ?",
                (account_id,),
            )
            row = await result.fetchone()
            if not row or not row["last_used_at"]:
                return None
            # SQLite returns CURRENT_TIMESTAMP as a string; compute difference
            result2 = await conn.execute(
                "SELECT (strftime('%s', 'now') - strftime('%s', last_used_at)) AS elapsed "
                "FROM accounts WHERE id = ?",
                (account_id,),
            )
            row2 = await result2.fetchone()
            return float(row2["elapsed"]) if row2 and row2["elapsed"] is not None else None

    # ── Account Risk Tracking ────────────────────────────────────────────────────
    # Risk score formula: score = min(1.0, failed*0.20 + captcha*0.30 + redirects*0.25)
    # >= 0.70 → auto-mark limited; >= 1.0 → auto-mark banned

    _RISK_WEIGHTS = {"failed": 0.20, "captcha": 0.30, "redirect": 0.25}
    _RISK_AUTO_PAUSE = 0.70
    _RISK_AUTO_BAN   = 1.00

    async def _recalculate_risk(self, conn: Any, account_id: str) -> float:
        """Recalculate risk score from current counters and persist it.
        Also auto-pauses (limited) or auto-bans account if thresholds exceeded.
        Returns the new risk_score.
        """
        row = await (await conn.execute(
            "SELECT failed_publish_count, captcha_hit_count, login_redirect_count, status "
            "FROM accounts WHERE id = ?",
            (account_id,),
        )).fetchone()
        if not row:
            return 0.0
        score = min(
            1.0,
            row["failed_publish_count"] * 0.20
            + row["captcha_hit_count"] * 0.30
            + row["login_redirect_count"] * 0.25,
        )
        # Auto-escalate account status on high risk
        new_status = row["status"]
        if score >= self._RISK_AUTO_BAN and row["status"] not in ("banned",):
            new_status = "banned"
        elif score >= self._RISK_AUTO_PAUSE and row["status"] == "healthy":
            new_status = "limited"
        await conn.execute(
            "UPDATE accounts SET risk_score = ?, status = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (score, new_status, account_id),
        )
        return score

    async def record_publish_success(self, account_id: str) -> None:
        """Stamp last_used_at and gently reduce risk score on successful publish.
        Resets consecutive failure pressure but does not wipe historical counts.
        """
        async with self.connection() as conn:
            # Reduce raw counters slightly on success (reward good behaviour)
            await conn.execute(
                """UPDATE accounts
                   SET last_used_at = CURRENT_TIMESTAMP,
                       failed_publish_count = MAX(0, failed_publish_count - 1),
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (account_id,),
            )
            await self._recalculate_risk(conn, account_id)

    async def record_publish_failure(self, account_id: str) -> float:
        """Increment failed_publish_count and recalculate risk score.
        Returns new risk_score.
        """
        async with self.connection() as conn:
            await conn.execute(
                "UPDATE accounts SET failed_publish_count = failed_publish_count + 1, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (account_id,),
            )
            return await self._recalculate_risk(conn, account_id)

    async def record_captcha_hit(self, account_id: str) -> float:
        """Increment captcha_hit_count, invalidate session, recalculate risk.
        Returns new risk_score.
        """
        async with self.connection() as conn:
            await conn.execute(
                "UPDATE accounts "
                "SET captcha_hit_count = captcha_hit_count + 1, "
                "    session_valid = 0, "
                "    updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (account_id,),
            )
            return await self._recalculate_risk(conn, account_id)

    async def record_login_redirect(self, account_id: str) -> float:
        """Increment login_redirect_count, invalidate session, recalculate risk.
        Returns new risk_score.
        """
        async with self.connection() as conn:
            await conn.execute(
                "UPDATE accounts "
                "SET login_redirect_count = login_redirect_count + 1, "
                "    session_valid = 0, "
                "    updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (account_id,),
            )
            return await self._recalculate_risk(conn, account_id)

    async def get_account_risk(self, account_id: str) -> dict[str, Any] | None:
        """Return risk score and counters for an account."""
        async with self.connection() as conn:
            row = await (await conn.execute(
                "SELECT id, account_handle, status, risk_score, "
                "failed_publish_count, captcha_hit_count, login_redirect_count, "
                "browser_data_dir "
                "FROM accounts WHERE id = ?",
                (account_id,),
            )).fetchone()
            return dict(row) if row else None

    async def set_browser_data_dir(self, account_id: str, data_dir: str) -> None:
        """Persist the persistent Chromium profile path for this account."""
        async with self.connection() as conn:
            await conn.execute(
                "UPDATE accounts SET browser_data_dir = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (data_dir, account_id),
            )

    # ── Proxy Health Tracking ────────────────────────────────────────────────────

    async def update_proxy_health(
        self,
        account_id: str,
        latency_ms: int,
        country: str | None = None,
    ) -> None:
        """Store proxy health check results (TCP latency + geo) for this account.
        Called before every publish run to keep proxy metadata fresh.
        """
        async with self.connection() as conn:
            if country:
                await conn.execute(
                    "UPDATE accounts "
                    "SET proxy_latency_ms = ?, proxy_country = ?, "
                    "    proxy_validated_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ?",
                    (latency_ms, country, account_id),
                )
            else:
                await conn.execute(
                    "UPDATE accounts "
                    "SET proxy_latency_ms = ?, proxy_validated_at = CURRENT_TIMESTAMP, "
                    "    updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ?",
                    (latency_ms, account_id),
                )

    async def get_proxy_account_count(self, proxy_url: str) -> int:
        """Count how many active accounts share the same proxy_url.
        Used to enforce the 1-account-per-proxy best practice.
        """
        async with self.connection() as conn:
            result = await conn.execute(
                "SELECT COUNT(*) as cnt FROM accounts "
                "WHERE proxy_url = ? AND status NOT IN ('banned', 'disabled')",
                (proxy_url,),
            )
            row = await result.fetchone()
            return row["cnt"] if row else 0

    # ── Warm-up Session Tracking ─────────────────────────────────────────────────

    async def increment_warmup_session(self, account_id: str) -> int:
        """Increment warmup_sessions_completed and return the new count.
        Called after each successful homepage warm-up to track account conditioning.
        """
        async with self.connection() as conn:
            await conn.execute(
                "UPDATE accounts "
                "SET warmup_sessions_completed = warmup_sessions_completed + 1, "
                "    updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (account_id,),
            )
            result = await conn.execute(
                "SELECT warmup_sessions_completed FROM accounts WHERE id = ?",
                (account_id,),
            )
            row = await result.fetchone()
            return row["warmup_sessions_completed"] if row else 0

    # ── Soft-Ban Detection ───────────────────────────────────────────────────────

    async def mark_soft_ban(self, account_id: str) -> None:
        """Mark account as shadow-banned (0-view posts, upload success but no reach).
        Sets soft_ban_detected=1 and status='limited' to pause publishing.
        """
        async with self.connection() as conn:
            await conn.execute(
                "UPDATE accounts "
                "SET soft_ban_detected = 1, status = 'limited', "
                "    updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (account_id,),
            )

    async def clear_soft_ban(self, account_id: str) -> None:
        """Clear soft-ban flag after manual review confirms account is healthy."""
        async with self.connection() as conn:
            await conn.execute(
                "UPDATE accounts "
                "SET soft_ban_detected = 0, status = 'healthy', "
                "    updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (account_id,),
            )

    # ── Artifacts CRUD ───────────────────────────────────────────────────────────


    async def create_artifact(
        self,
        artifact_type: str,
        storage_uri: str,
        job_id: str | None = None,
        task_id: str | None = None,
        execution_id: str | None = None,
        mime_type: str | None = None,
        size_bytes: int | None = None,
        checksum: str | None = None,
        metadata: dict[str, Any] | None = None,
        status: str = "pending",
    ) -> dict[str, Any]:
        if not storage_uri.strip():
            raise ValidationError("storage_uri cannot be empty")
        if artifact_type not in {"video", "image", "audio", "metadata", "file", "log"}:
            raise ValidationError(f"Unsupported artifact_type: {artifact_type}")
        if status not in {"pending", "approved", "rejected"}:
            raise ValidationError(f"Unsupported artifact status: {status}")
        artifact_id = _uuid()
        async with self.connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                await conn.execute(
                    """
                    INSERT OR IGNORE INTO artifacts (
                        id, job_id, task_id, execution_id, artifact_type, status,
                        storage_uri, mime_type, size_bytes, checksum, metadata
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact_id,
                        job_id,
                        task_id,
                        execution_id,
                        artifact_type,
                        status,
                        storage_uri,
                        mime_type,
                        size_bytes,
                        checksum,
                        _to_json(metadata or {}),
                    ),
                )
                row = await (await conn.execute(
                    "SELECT id, job_id, task_id, artifact_type, status, storage_uri, "
                    "mime_type, size_bytes, checksum, metadata, created_at "
                    "FROM artifacts WHERE storage_uri = ?",
                    (storage_uri,),
                )).fetchone()
                await conn.execute("COMMIT")
                if row is None:
                    raise DatabaseError(f"Artifact was not persisted: {storage_uri}")
                return dict(row)
            except Exception as e:
                await conn.execute("ROLLBACK")
                raise e

    async def list_artifacts(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        async with self.connection() as conn:
            result = await conn.execute(
                "SELECT id, job_id, task_id, artifact_type, status, storage_uri, mime_type, size_bytes, checksum, metadata, created_at FROM artifacts ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
            return [dict(row) for row in await result.fetchall()]

    async def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        async with self.connection() as conn:
            result = await conn.execute(
                "SELECT id, job_id, task_id, artifact_type, status, storage_uri, mime_type, size_bytes, checksum, metadata, created_at FROM artifacts WHERE id = ?",
                (artifact_id,),
            )
            row = await result.fetchone()
            return dict(row) if row else None

    async def update_artifact_status(self, artifact_id: str, status: str) -> dict[str, Any] | None:
        async with self.connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                await conn.execute(
                    "UPDATE artifacts SET status = ? WHERE id = ?",
                    (status, artifact_id),
                )
                row = await (await conn.execute(
                    "SELECT id, job_id, task_id, artifact_type, status, storage_uri, mime_type, size_bytes, checksum, metadata, created_at FROM artifacts WHERE id = ?",
                    (artifact_id,),
                )).fetchone()
                await conn.execute("COMMIT")
                return dict(row) if row else None
            except Exception as e:
                await conn.execute("ROLLBACK")
                raise e

    # ── Policy Rules CRUD ────────────────────────────────────────────────────────

    async def list_policy_rules_all(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        async with self.connection() as conn:
            result = await conn.execute(
                "SELECT id, account_id, platform, action_type, rule_name, enabled, config, cooldown_seconds, max_actions, window_seconds, created_at, updated_at FROM policy_rules ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
            return [dict(row) for row in await result.fetchall()]

    async def create_policy_rule(
        self,
        action_type: str,
        rule_name: str,
        max_actions: int | None = None,
        window_seconds: int | None = None,
        account_id: str | None = None,
        platform: str | None = None,
        cooldown_seconds: int = 0,
    ) -> dict[str, Any]:
        rule_id = _uuid()
        async with self.connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                await conn.execute(
                    """
                    INSERT INTO policy_rules (id, account_id, platform, action_type, rule_name, enabled, config, cooldown_seconds, max_actions, window_seconds)
                    VALUES (?, ?, ?, ?, ?, 1, '{}', ?, ?, ?)
                    """,
                    (rule_id, account_id, platform, action_type, rule_name, cooldown_seconds, max_actions, window_seconds),
                )
                row = await (await conn.execute(
                    "SELECT id, account_id, platform, action_type, rule_name, enabled, config, cooldown_seconds, max_actions, window_seconds, created_at, updated_at FROM policy_rules WHERE id = ?",
                    (rule_id,),
                )).fetchone()
                await conn.execute("COMMIT")
                return dict(row)
            except Exception as e:
                await conn.execute("ROLLBACK")
                raise e

    async def delete_policy_rule(self, rule_id: str) -> bool:
        async with self.connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                result = await conn.execute("DELETE FROM policy_rules WHERE id = ?", (rule_id,))
                deleted = result.rowcount > 0
                await conn.execute("COMMIT")
                return deleted
            except Exception as e:
                await conn.execute("ROLLBACK")
                raise e

    async def update_policy_rule_enabled(self, rule_id: str, enabled: bool) -> dict[str, Any] | None:
        """Toggle the enabled flag on a policy rule. Returns updated row or None if not found."""
        async with self.connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                await conn.execute(
                    "UPDATE policy_rules SET enabled = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (1 if enabled else 0, rule_id),
                )
                row = await (await conn.execute(
                    "SELECT id, account_id, platform, action_type, rule_name, enabled, config, "
                    "cooldown_seconds, max_actions, window_seconds, created_at, updated_at "
                    "FROM policy_rules WHERE id = ?",
                    (rule_id,),
                )).fetchone()
                await conn.execute("COMMIT")
                return dict(row) if row else None
            except Exception as e:
                await conn.execute("ROLLBACK")
                raise e

    async def get_account_publish_count(self, account_id: str, last_hours: int = 24) -> int:

        """Return the number of publish_* actions logged for an account in the last `last_hours` hours."""
        async with self.connection() as conn:
            result = await conn.execute(
                """
                SELECT count(*) AS cnt FROM action_logs
                WHERE account_id = ?
                  AND action_type LIKE 'publish_%'
                  AND created_at >= datetime('now', ? || ' hours')
                """,
                (str(account_id), f"-{last_hours}"),
            )
            row = await result.fetchone()
            return int(row["cnt"]) if row else 0

    async def get_artifact_by_storage_uri(self, storage_uri: str) -> dict[str, Any] | None:
        """Return the artifact row dict matching a storage_uri, or None if not found."""
        async with self.connection() as conn:
            result = await conn.execute(
                "SELECT id, status, artifact_type, metadata FROM artifacts WHERE storage_uri = ? LIMIT 1",
                (storage_uri,),
            )
            row = await result.fetchone()
            if row is None:
                return None
            return {
                "id": row["id"],
                "status": row["status"],
                "artifact_type": row["artifact_type"],
                "metadata": _from_json(row["metadata"]),
            }

    async def get_policy_rules(self, account_id: UUID, action_type: str) -> list[PolicyRuleRecord]:
        async with self.connection() as conn:
            account_result = await conn.execute("SELECT platform FROM accounts WHERE id = ?", (str(account_id),))
            account = await account_result.fetchone()
            if account is None:
                raise DatabaseError(f"Account not found: {account_id}")
            rows = await conn.execute(
                """
                SELECT * FROM policy_rules
                WHERE enabled = 1 AND action_type = ?
                  AND (account_id = ? OR (account_id IS NULL AND platform = ?) OR (account_id IS NULL AND platform IS NULL))
                ORDER BY account_id, platform, rule_name ASC
                """,
                (action_type, str(account_id), account["platform"]),
            )
            return [_policy_rule(row) for row in await rows.fetchall()]

    async def _lock_task(self, conn: aiosqlite.Connection, task_id: str) -> dict[str, Any]:
        result = await conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = await result.fetchone()
        if row is None:
            raise DatabaseError(f"Task not found: {task_id}")
        return row

    async def _lock_execution(self, conn: aiosqlite.Connection, execution_id: str) -> dict[str, Any]:
        result = await conn.execute("SELECT * FROM task_executions WHERE id = ?", (execution_id,))
        row = await result.fetchone()
        if row is None:
            raise DatabaseError(f"Task execution not found: {execution_id}")
        return row

    @staticmethod
    def _validate_execution_belongs_to_task(execution: dict[str, Any], task_id: str) -> None:
        if execution["task_id"] != task_id:
            raise DatabaseError(f"Execution {execution['id']} does not belong to task {task_id}")

    @staticmethod
    def _validate_running_execution(execution: dict[str, Any]) -> None:
        if ExecutionStatus(execution["status"]) != ExecutionStatus.RUNNING:
            raise InvalidStateTransition(f"Execution must be running before completion: {execution['status']}")

    async def _maybe_complete_job(self, conn: aiosqlite.Connection, job_id: str) -> None:
        result = await conn.execute(
            """
            SELECT SUM(CASE WHEN status NOT IN ('SUCCESS', 'CANCELED') THEN 1 ELSE 0 END) AS unfinished,
                   SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) AS failed
            FROM tasks WHERE job_id = ?
            """,
            (job_id,),
        )
        row = await result.fetchone()
        if row and (row["unfinished"] or 0) == 0 and (row["failed"] or 0) == 0:
            await conn.execute(
                "UPDATE jobs SET status = 'completed', completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status IN ('pending', 'running')",
                (job_id,),
            )

    async def _maybe_fail_job(self, conn: aiosqlite.Connection, job_id: str) -> None:
        await conn.execute(
            """
            UPDATE jobs SET status = 'failed', completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ('pending', 'running')
              AND EXISTS (SELECT 1 FROM tasks WHERE job_id = ? AND status = 'FAILED')
            """,
            (job_id, job_id),
        )


def _ensure_task_transition(current: TaskStatus, target: TaskStatus) -> None:
    if target not in VALID_TASK_TRANSITIONS[current]:
        raise InvalidStateTransition(f"Illegal task transition: {current.value} -> {target.value}")

def _job(row: dict[str, Any]) -> JobRecord:
    return JobRecord(
        id=UUID(row["id"]) if isinstance(row["id"], str) else row["id"],
        job_key=row["job_key"],
        workflow_name=row["workflow_name"],
        status=row["status"],
        priority=row["priority"],
        input=_from_json(row["input"]),
        metadata=_from_json(row["metadata"]),
        error_type=row["error_type"],
        error_message=row["error_message"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )

def _task(row: dict[str, Any]) -> TaskRecord:
    return TaskRecord(
        id=UUID(row["id"]) if isinstance(row["id"], str) else row["id"],
        job_id=UUID(row["job_id"]) if isinstance(row["job_id"], str) else row["job_id"],
        task_key=row["task_key"],
        task_type=row["task_type"],
        status=TaskStatus(row["status"]),
        priority=row["priority"],
        payload=_from_json(row["payload"]),
        metadata=_from_json(row["metadata"]),
        retry_count=row["retry_count"],
        max_retries=row["max_retries"],
        next_run_at=row["next_run_at"],
        next_retry_at=row["next_retry_at"],
        account_id=UUID(row["account_id"]) if row["account_id"] and isinstance(row["account_id"], str) else row["account_id"],
        action_type=row["action_type"],
        idempotency_key=row["idempotency_key"],
        result=_from_json(row["result"]),
        error_type=row["error_type"],
        error_message=row["error_message"],
    )

def _execution(row: dict[str, Any]) -> TaskExecutionRecord:
    return TaskExecutionRecord(
        id=UUID(row["id"]) if isinstance(row["id"], str) else row["id"],
        task_id=UUID(row["task_id"]) if isinstance(row["task_id"], str) else row["task_id"],
        worker_id=row["worker_id"],
        attempt_number=row["attempt_number"],
        status=ExecutionStatus(row["status"]),
        heartbeat_at=row["heartbeat_at"],
        lease_expires_at=row["lease_expires_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        result=_from_json(row["result"]),
        error_type=row["error_type"],
        error_message=row["error_message"],
    )

def _policy_rule(row: dict[str, Any]) -> PolicyRuleRecord:
    return PolicyRuleRecord(
        id=UUID(row["id"]) if isinstance(row["id"], str) else row["id"],
        account_id=UUID(row["account_id"]) if row["account_id"] and isinstance(row["account_id"], str) else row["account_id"],
        platform=row["platform"],
        action_type=row["action_type"],
        rule_name=row["rule_name"],
        enabled=bool(row["enabled"]),
        config=_from_json(row["config"]),
        cooldown_seconds=row["cooldown_seconds"],
        max_actions=row["max_actions"],
        window_seconds=row["window_seconds"],
    )

def _action_log(row: dict[str, Any]) -> ActionLogRecord:
    return ActionLogRecord(
        id=UUID(row["id"]) if isinstance(row["id"], str) else row["id"],
        task_id=UUID(row["task_id"]) if row["task_id"] and isinstance(row["task_id"], str) else row["task_id"],
        action_type=row["action_type"],
        status=row["status"],
        metadata=_from_json(row["request"]),
        created_at=row["created_at"],
    )
