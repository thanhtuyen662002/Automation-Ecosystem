from __future__ import annotations

import hashlib
import json
import logging
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    RETRY = "RETRY"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


@dataclass(frozen=True)
class TaskRecord:
    task_id: UUID
    task_type: str
    status: TaskStatus
    data: dict[str, Any]
    retry_count: int
    max_retries: int
    last_heartbeat: datetime | None
    next_run_at: datetime
    worker_id: str | None
    execution_hash: str | None = None

    @property
    def locked_by(self) -> str | None:
        return self.worker_id


@dataclass(frozen=True)
class TaskResult:
    success: bool
    output: dict[str, Any] | None
    error: str | None
    error_type: str | None


TaskHandler = Callable[[TaskRecord], dict[str, Any] | None]
TaskLockProvider = Callable[[UUID], TaskRecord | None]
TaskFetchProvider = Callable[[UUID], TaskRecord | None]

LOGGER = logging.getLogger("workers.runtime")

VALID_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.READY, TaskStatus.FAILED}),
    TaskStatus.READY: frozenset({TaskStatus.RUNNING, TaskStatus.FAILED}),
    TaskStatus.RUNNING: frozenset({TaskStatus.SUCCESS, TaskStatus.RETRY, TaskStatus.FAILED}),
    TaskStatus.RETRY: frozenset({TaskStatus.READY, TaskStatus.FAILED}),
    TaskStatus.SUCCESS: frozenset(),
    TaskStatus.FAILED: frozenset(),
}


class InvalidTaskStateTransition(ValueError):
    pass


class TaskRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, TaskHandler] = {}

    def register(self, task_type: str, handler: TaskHandler) -> None:
        normalized = task_type.strip()
        if normalized == "":
            raise ValueError("task_type cannot be empty")
        if not callable(handler):
            raise TypeError("handler must be callable")
        self._handlers[normalized] = handler

    def get(self, task_type: str) -> TaskHandler:
        try:
            return self._handlers[task_type]
        except KeyError as exc:
            raise KeyError(f"No task handler registered for task_type={task_type}") from exc


class TaskExecutor:
    def __init__(
        self,
        registry: TaskRegistry,
        lock_provider: TaskLockProvider | None = None,
        fetch_provider: TaskFetchProvider | None = None,
    ) -> None:
        self._registry = registry
        self._lock_provider = lock_provider
        self._fetch_provider = fetch_provider

    def get_task_and_lock(self, task_id: UUID) -> TaskRecord | None:
        if self._lock_provider is None:
            raise RuntimeError("TaskExecutor was created without a task lock provider")
        return self._lock_provider(task_id)

    def acquire_for_execution(self, task_id: UUID) -> TaskRecord | None:
        current = self._fetch_task(task_id)
        if current is None:
            return None
        if self._is_duplicate(current):
            self._log_duplicate_skip(current)
            return None
        if current.status != TaskStatus.READY:
            LOGGER.info(
                "Skipping task that is not READY",
                extra={
                    "event": "task_not_ready_skipped",
                    "task_id": str(current.task_id),
                    "task_type": current.task_type,
                    "worker_id": current.worker_id,
                    "status": current.status.value,
                },
            )
            return None
        locked = self.get_task_and_lock(task_id)
        if locked is None:
            return None
        if self._is_duplicate(locked):
            self._log_duplicate_skip(locked)
            return None
        return locked

    def execute(self, task: TaskRecord) -> TaskResult:
        if self._is_duplicate(task):
            self._log_duplicate_skip(task)
            return TaskResult(success=True, output={}, error=None, error_type=None)
        try:
            handler = self._registry.get(task.task_type)
            output = handler(task)
            if output is not None and not isinstance(output, dict):
                raise TypeError("task handler must return a dict or None")
            return TaskResult(success=True, output=output or {}, error=None, error_type=None)
        except Exception as exc:
            return TaskResult(
                success=False,
                output=None,
                error="".join(traceback.format_exception_only(type(exc), exc)).strip(),
                error_type=type(exc).__name__,
            )

    def _fetch_task(self, task_id: UUID) -> TaskRecord | None:
        if self._fetch_provider is None:
            return None
        return self._fetch_provider(task_id)

    @staticmethod
    def _is_duplicate(task: TaskRecord) -> bool:
        if task.status == TaskStatus.SUCCESS:
            return True
        return task.status != TaskStatus.RUNNING and task.execution_hash is not None

    @staticmethod
    def _log_duplicate_skip(task: TaskRecord) -> None:
        LOGGER.info(
            "Skipping duplicate execution",
            extra={
                "event": "duplicate_execution_skipped",
                "task_id": str(task.task_id),
                "task_type": task.task_type,
                "worker_id": task.worker_id,
                "status": task.status.value,
                "execution_hash": task.execution_hash,
            },
        )


def build_execution_hash(task: TaskRecord) -> str:
    body = {
        "task_id": str(task.task_id),
        "task_type": task.task_type,
        "data": task.data,
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_task_transition(current: TaskStatus, target: TaskStatus) -> None:
    allowed = VALID_TRANSITIONS[current]
    if target not in allowed:
        raise InvalidTaskStateTransition(f"Illegal task state transition: {current.value} -> {target.value}")


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    return target in VALID_TRANSITIONS[current]
