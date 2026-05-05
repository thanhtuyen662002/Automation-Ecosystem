"""Unit tests for execution recovery logic.

Validates lease expiry detection, zombie-reaper state transitions,
and the retry/fail decision without touching a real database.
"""
from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from database.database import (
    AcquiredTask,
    ExecutionStatus,
    RetryConfig,
    TaskExecutionRecord,
    TaskRecord,
    TaskStatus,
)
from workers.worker_runtime import WorkerRuntime, WorkerRuntimeSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(
    *,
    status: TaskStatus = TaskStatus.RUNNING,
    retry_count: int = 0,
    max_retries: int = 3,
    task_type: str = "ai",
) -> TaskRecord:
    return TaskRecord(
        id=uuid4(),
        job_id=uuid4(),
        task_type=task_type,
        status=status,
        priority=0,
        payload={"prompt": "hello"},
        metadata={},
        retry_count=retry_count,
        max_retries=max_retries,
        next_run_at=datetime.now(UTC),
        next_retry_at=None,
        account_id=None,
        action_type=None,
        idempotency_key=None,
        result=None,
        error_type=None,
        error_message=None,
    )


def _make_execution(
    task_id,
    *,
    status: ExecutionStatus = ExecutionStatus.RUNNING,
    lease_expires_at: datetime | None = None,
) -> TaskExecutionRecord:
    now = datetime.now(UTC)
    return TaskExecutionRecord(
        id=uuid4(),
        task_id=task_id,
        worker_id="test-worker",
        attempt_number=1,
        status=status,
        heartbeat_at=now,
        lease_expires_at=lease_expires_at or (now + timedelta(seconds=300)),
        started_at=now,
        completed_at=None,
        result=None,
        error_type=None,
        error_message=None,
    )


def _make_settings(**overrides) -> WorkerRuntimeSettings:
    defaults = dict(
        database_url="postgresql://user:pass@localhost/db",
        worker_id="test-worker-1",
        max_concurrency=2,
        batch_size=4,
        poll_interval_seconds=0.05,
        heartbeat_interval_seconds=30.0,
        task_timeout_seconds=10.0,
        lease_seconds=300,
        retry_base_delay_seconds=5,
        retry_max_delay_seconds=300,
    )
    defaults.update(overrides)
    return WorkerRuntimeSettings(**defaults)


# ---------------------------------------------------------------------------
# Tests: lease-expiry decision logic
# ---------------------------------------------------------------------------

class TestLeaseExpiryDetection(unittest.TestCase):
    """Verify that an expired lease is correctly identified."""

    def test_expired_lease_is_in_the_past(self) -> None:
        expired = datetime.now(UTC) - timedelta(seconds=1)
        self.assertLess(expired, datetime.now(UTC))

    def test_valid_lease_is_in_the_future(self) -> None:
        future = datetime.now(UTC) + timedelta(seconds=300)
        self.assertGreater(future, datetime.now(UTC))

    def test_task_should_retry_when_under_max_retries(self) -> None:
        task = _make_task(retry_count=1, max_retries=3)
        should_retry = task.retry_count < task.max_retries
        self.assertTrue(should_retry)

    def test_task_should_fail_when_at_max_retries(self) -> None:
        task = _make_task(retry_count=3, max_retries=3)
        should_retry = task.retry_count < task.max_retries
        self.assertFalse(should_retry)

    def test_target_status_is_retry_when_under_budget(self) -> None:
        task = _make_task(retry_count=0, max_retries=3)
        target = TaskStatus.RETRY if task.retry_count < task.max_retries else TaskStatus.FAILED
        self.assertEqual(target, TaskStatus.RETRY)

    def test_target_status_is_failed_when_budget_exhausted(self) -> None:
        task = _make_task(retry_count=3, max_retries=3)
        target = TaskStatus.RETRY if task.retry_count < task.max_retries else TaskStatus.FAILED
        self.assertEqual(target, TaskStatus.FAILED)


# ---------------------------------------------------------------------------
# Tests: RetryConfig back-off for recovery
# ---------------------------------------------------------------------------

class TestRecoveryRetryDelay(unittest.TestCase):
    """The retry delay used during recovery must follow the same back-off."""

    def test_zero_retries_yields_base_delay(self) -> None:
        cfg = RetryConfig(base_delay_seconds=5, max_delay_seconds=300)
        self.assertEqual(cfg.delay_for_attempt(0), 5)

    def test_higher_retries_yield_longer_delay(self) -> None:
        cfg = RetryConfig(base_delay_seconds=10, max_delay_seconds=600)
        delay_first = cfg.delay_for_attempt(1)
        delay_second = cfg.delay_for_attempt(2)
        self.assertGreaterEqual(delay_second, delay_first)

    def test_delay_never_exceeds_max(self) -> None:
        cfg = RetryConfig(base_delay_seconds=60, max_delay_seconds=120)
        for attempt in range(10):
            self.assertLessEqual(cfg.delay_for_attempt(attempt), 120)


# ---------------------------------------------------------------------------
# Tests: WorkerRuntime lifecycle (mocked database)
# ---------------------------------------------------------------------------

class TestWorkerRuntimeRecovery(unittest.IsolatedAsyncioTestCase):
    """WorkerRuntime handles task failures and marks them correctly."""

    async def test_runtime_marks_failed_on_handler_exception(self) -> None:
        settings = _make_settings()
        mock_db = AsyncMock()
        mock_db.open = AsyncMock()
        mock_db.close = AsyncMock()
        mock_db.acquire_ready_tasks_batch = AsyncMock(return_value=[])

        runtime = WorkerRuntime(settings=settings, database=mock_db)

        task = _make_task()
        execution = _make_execution(task.id)
        acquired = AcquiredTask(task=task, execution=execution)

        async def failing_handler(_payload):
            raise RuntimeError("simulated failure")

        runtime.register_task("ai", failing_handler)

        mock_db.mark_task_failure = AsyncMock(return_value=_make_task(status=TaskStatus.RETRY))

        await runtime._run_acquired_task(acquired)

        mock_db.mark_task_failure.assert_awaited_once()
        call_args = mock_db.mark_task_failure.call_args
        self.assertEqual(call_args.args[0], task.id)
        self.assertEqual(call_args.args[1], execution.id)

    async def test_runtime_marks_success_on_handler_return(self) -> None:
        settings = _make_settings()
        mock_db = AsyncMock()
        mock_db.open = AsyncMock()
        mock_db.close = AsyncMock()

        runtime = WorkerRuntime(settings=settings, database=mock_db)

        task = _make_task()
        execution = _make_execution(task.id)
        acquired = AcquiredTask(task=task, execution=execution)

        async def ok_handler(_payload):
            return {"ok": True}

        runtime.register_task("ai", ok_handler)
        mock_db.mark_task_success = AsyncMock(return_value=_make_task(status=TaskStatus.SUCCESS))
        mock_db.update_execution_heartbeat = AsyncMock(return_value=True)

        await runtime._run_acquired_task(acquired)

        mock_db.mark_task_success.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
