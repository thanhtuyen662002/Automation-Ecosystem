from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from database.database import AcquiredTask, AutomationDatabase, TaskRecord, TaskStatus


LOGGER = logging.getLogger("core.workflow_manager")


class TaskQueue(Protocol):
    async def enqueue_ready_task(self, task_id: UUID, task_type: str) -> None:
        pass


@dataclass(frozen=True)
class DispatchResult:
    acquired: list[AcquiredTask]
    skipped_task_ids: list[UUID]
    throttled_task_ids: list[UUID] = field(default_factory=list)


class WorkflowManager:
    def __init__(
        self,
        database: AutomationDatabase,
        worker_id: str,
        queue: TaskQueue | None = None,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id cannot be empty")
        self._database = database
        self._worker_id = worker_id
        self._queue = queue

    async def get_ready_tasks(self, limit: int = 100) -> list[TaskRecord]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        return await self._database.get_ready_tasks(limit)

    async def check_dependencies(self, task_id: UUID) -> bool:
        return await self._database.dependencies_satisfied(task_id)

    async def promote_tasks_to_ready(self, limit: int = 100) -> list[TaskRecord]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        async with self._database.connection() as conn:
            async with conn.transaction():
                result = await conn.execute(
                    """
                    WITH candidates AS (
                        SELECT t.id
                        FROM tasks t
                        WHERE (
                              (t.status = 'PENDING' AND t.next_run_at <= now())
                              OR (t.status = 'RETRY' AND t.next_retry_at <= now())
                          )
                          AND NOT EXISTS (
                              SELECT 1
                              FROM task_dependencies dep
                              JOIN tasks parent ON parent.id = dep.depends_on_task_id
                              WHERE dep.task_id = t.id
                                AND parent.status <> 'SUCCESS'
                          )
                        ORDER BY t.priority DESC,
                                 COALESCE(t.next_retry_at, t.next_run_at) ASC,
                                 t.created_at ASC
                        LIMIT %s
                        FOR UPDATE OF t SKIP LOCKED
                    )
                    UPDATE tasks AS task
                    SET status = 'READY',
                        updated_at = now()
                    FROM candidates
                    WHERE task.id = candidates.id
                      AND task.status IN ('PENDING', 'RETRY')
                    RETURNING task.*
                    """,
                    (limit,),
                )
                rows = await result.fetchall()
        promoted = [_task_from_row(row) for row in rows]
        for task in promoted:
            LOGGER.info(
                "promoted task to READY",
                extra={
                    "event": "task_promoted_ready",
                    "task_id": str(task.id),
                    "job_id": str(task.job_id),
                    "task_type": task.task_type,
                    "status": task.status.value,
                },
            )
        return promoted

    async def dispatch_tasks(
        self,
        limit: int = 100,
        max_concurrent_per_worker: int | None = None,
        max_per_task_type: int | None = None,
        max_per_account: int | None = None,
        acquire_without_queue: bool = True,
    ) -> DispatchResult:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        _validate_optional_limit("max_concurrent_per_worker", max_concurrent_per_worker)
        _validate_optional_limit("max_per_task_type", max_per_task_type)
        _validate_optional_limit("max_per_account", max_per_account)
        if self._queue is None and not acquire_without_queue:
            LOGGER.info(
                "dispatch skipped because no queue is configured",
                extra={
                    "event": "task_dispatch_noop",
                    "worker_id": self._worker_id,
                },
            )
            return DispatchResult(acquired=[], skipped_task_ids=[])

        acquisition_limit = min(limit, max_concurrent_per_worker) if max_concurrent_per_worker else limit
        acquired_batch = await self._database.acquire_ready_tasks_batch(
            acquisition_limit,
            self._worker_id,
            max_per_task_type=max_per_task_type,
            max_per_account=max_per_account,
        )
        dispatched: list[AcquiredTask] = []
        throttled: list[UUID] = []
        task_type_counts: dict[str, int] = defaultdict(int)
        account_counts: dict[UUID, int] = defaultdict(int)

        for acquired_task in acquired_batch:
            throttle_reason = _dispatch_throttle_reason(
                acquired_task=acquired_task,
                dispatched_count=len(dispatched),
                task_type_counts=task_type_counts,
                account_counts=account_counts,
                max_concurrent_per_worker=max_concurrent_per_worker,
                max_per_task_type=max_per_task_type,
                max_per_account=max_per_account,
            )
            if throttle_reason is not None:
                throttled.append(acquired_task.task.id)
                LOGGER.info(
                    "task dispatch throttled",
                    extra={
                        "event": "task_dispatch_throttled",
                        "task_id": str(acquired_task.task.id),
                        "execution_id": str(acquired_task.execution.id),
                        "task_type": acquired_task.task.task_type,
                        "worker_id": self._worker_id,
                        "account_id": str(acquired_task.task.account_id) if acquired_task.task.account_id else None,
                        "reason": throttle_reason,
                    },
                )
                continue

            dispatched.append(acquired_task)
            task_type_counts[acquired_task.task.task_type] += 1
            if acquired_task.task.account_id is not None:
                account_counts[acquired_task.task.account_id] += 1
            if self._queue is not None:
                await self._queue.enqueue_ready_task(
                    acquired_task.task.id,
                    acquired_task.task.task_type,
                )
            LOGGER.info(
                "dispatched task execution",
                extra={
                    "event": "task_dispatched",
                    "task_id": str(acquired_task.task.id),
                    "execution_id": str(acquired_task.execution.id),
                    "task_type": acquired_task.task.task_type,
                    "worker_id": self._worker_id,
                },
            )
        return DispatchResult(
            acquired=dispatched,
            skipped_task_ids=[],
            throttled_task_ids=throttled,
        )


def _validate_optional_limit(name: str, value: int | None) -> None:
    if value is not None and value < 1:
        raise ValueError(f"{name} must be >= 1")


def _dispatch_throttle_reason(
    acquired_task: AcquiredTask,
    dispatched_count: int,
    task_type_counts: dict[str, int],
    account_counts: dict[UUID, int],
    max_concurrent_per_worker: int | None,
    max_per_task_type: int | None,
    max_per_account: int | None,
) -> str | None:
    if max_concurrent_per_worker is not None and dispatched_count >= max_concurrent_per_worker:
        return "max_concurrent_per_worker"
    if (
        max_per_task_type is not None
        and task_type_counts[acquired_task.task.task_type] >= max_per_task_type
    ):
        return "max_per_task_type"
    if (
        max_per_account is not None
        and acquired_task.task.account_id is not None
        and account_counts[acquired_task.task.account_id] >= max_per_account
    ):
        return "max_per_account"
    return None


def _task_from_row(row: dict) -> TaskRecord:
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
