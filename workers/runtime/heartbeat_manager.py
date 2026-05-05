from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from workers.runtime.retry_handler import RetryHandler
from workers.runtime.task_executor import TaskRecord

HeartbeatCallback = Callable[[UUID], None]
RetryTrigger = Callable[[TaskRecord], None]

DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30
DEFAULT_ZOMBIE_TIMEOUT_SECONDS = 300

LOGGER = logging.getLogger("workers.runtime")


class ZombieTaskStore(Protocol):
    def stale_running_tasks(self, stale_before: datetime, limit: int = 100) -> list[TaskRecord]:
        pass

    def mark_zombie_failed(
        self,
        task_id: UUID,
        retry_count: int,
        next_run_at: datetime,
    ) -> bool:
        pass


class HeartbeatHandle:
    def __init__(
        self,
        task_id: UUID,
        interval_seconds: int,
        callback: HeartbeatCallback,
    ) -> None:
        if interval_seconds < 1:
            raise ValueError("interval_seconds must be >= 1")
        self._task_id = task_id
        self._interval_seconds = interval_seconds
        self._callback = callback
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"heartbeat-{task_id}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=self._interval_seconds + 2)

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                self._callback(self._task_id)
            except Exception as exc:
                LOGGER.error(
                    "heartbeat update failed",
                    extra={
                        "event": "heartbeat_failed",
                        "task_id": str(self._task_id),
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                )


class HeartbeatManager:
    def __init__(self, interval_seconds: int = DEFAULT_HEARTBEAT_INTERVAL_SECONDS) -> None:
        if interval_seconds < 1:
            raise ValueError("interval_seconds must be >= 1")
        self.interval_seconds = interval_seconds

    def start(self, task_id: UUID, callback: HeartbeatCallback) -> HeartbeatHandle:
        handle = HeartbeatHandle(task_id, self.interval_seconds, callback)
        handle.start()
        return handle


def mark_zombie_tasks_failed(
    store: ZombieTaskStore,
    retry_handler: RetryHandler,
    retry_trigger: RetryTrigger | None = None,
    zombie_timeout_seconds: int = DEFAULT_ZOMBIE_TIMEOUT_SECONDS,
    batch_size: int = 100,
) -> int:
    if zombie_timeout_seconds < 1:
        raise ValueError("zombie_timeout_seconds must be >= 1")
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")

    now = datetime.now(UTC)
    stale_before = now - timedelta(seconds=zombie_timeout_seconds)
    recovered = 0
    for task in store.stale_running_tasks(stale_before, batch_size):
        retry = retry_handler.decide(
            task.retry_count,
            error_type="ZombieTask",
            error_message="Task heartbeat exceeded zombie timeout",
        )
        next_retry_count = task.retry_count + 1
        next_run_at = retry.next_retry_at or now
        marked = store.mark_zombie_failed(task.task_id, next_retry_count, next_run_at)
        if not marked:
            continue
        recovered += 1
        LOGGER.warning(
            "zombie task marked failed",
            extra={
                "event": "zombie_task_failed",
                "task_id": str(task.task_id),
                "task_type": task.task_type,
                "worker_id": task.worker_id,
                "error": "last_heartbeat exceeded zombie timeout",
                "retry_count": next_retry_count,
                "retry_scheduled": retry.should_retry,
            },
        )
        if retry.should_retry and retry_trigger is not None:
            retry_trigger(task)
    return recovered
