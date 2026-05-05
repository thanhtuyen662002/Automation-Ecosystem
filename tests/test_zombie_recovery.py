from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from workers.runtime.heartbeat_manager import (
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    HeartbeatManager,
    mark_zombie_tasks_failed,
)
from workers.runtime.retry_handler import RetryHandler
from workers.runtime.task_executor import TaskRecord, TaskStatus
from workers.runtime.zombie_reaper import ZombieReaperResult


class FakeZombieStore:
    def __init__(self, tasks: list[TaskRecord]) -> None:
        self.tasks = tasks
        self.marked: list[tuple[UUID, int, datetime]] = []

    def stale_running_tasks(self, stale_before: datetime, limit: int = 100) -> list[TaskRecord]:
        return self.tasks[:limit]

    def mark_zombie_failed(self, task_id: UUID, retry_count: int, next_run_at: datetime) -> bool:
        self.marked.append((task_id, retry_count, next_run_at))
        return True


def make_task(retry_count: int = 0, max_retries: int = 3) -> TaskRecord:
    return TaskRecord(
        task_id=uuid4(),
        task_type="ai_summary",
        status=TaskStatus.RUNNING,
        data={},
        retry_count=retry_count,
        max_retries=max_retries,
        last_heartbeat=datetime.now(UTC),
        next_run_at=datetime.now(UTC),
        worker_id="worker-1",
    )


def test_heartbeat_manager_defaults_to_thirty_seconds() -> None:
    manager = HeartbeatManager()

    assert manager.interval_seconds == DEFAULT_HEARTBEAT_INTERVAL_SECONDS == 30


def test_mark_zombie_tasks_failed_marks_and_triggers_retry() -> None:
    task = make_task(retry_count=0)
    store = FakeZombieStore([task])
    triggered: list[TaskRecord] = []

    recovered = mark_zombie_tasks_failed(
        store=store,
        retry_handler=RetryHandler(max_retries=3, base_delay_seconds=0, max_delay_seconds=0),
        retry_trigger=triggered.append,
    )

    assert recovered == 1
    assert store.marked[0][0] == task.task_id
    assert store.marked[0][1] == 1
    assert triggered == [task]


def test_mark_zombie_tasks_failed_does_not_trigger_when_retries_exhausted() -> None:
    task = make_task(retry_count=3)
    store = FakeZombieStore([task])
    triggered: list[TaskRecord] = []

    recovered = mark_zombie_tasks_failed(
        store=store,
        retry_handler=RetryHandler(max_retries=3),
        retry_trigger=triggered.append,
    )

    assert recovered == 1
    assert store.marked[0][1] == 4
    assert triggered == []


def test_zombie_reaper_result_shape() -> None:
    result = ZombieReaperResult(recovered_count=2)

    assert result.recovered_count == 2
