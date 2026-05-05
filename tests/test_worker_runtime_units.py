from __future__ import annotations

import json
import logging
import threading
import time
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from workers.runtime.resource_manager import ResourceConfig, ResourceManager
from workers.runtime.retry_handler import RetryHandler
from workers.runtime.retry_handler import ErrorClassification, RetryStrategy, classify_error
from workers.runtime.task_executor import (
    InvalidTaskStateTransition,
    TaskExecutor,
    TaskRecord,
    TaskRegistry,
    TaskStatus,
    build_execution_hash,
    can_transition,
    validate_task_transition,
)
from workers.runtime.worker_runtime import RuntimeConfig, configure_json_logging


def test_retry_handler_uses_max_three_retries_by_default() -> None:
    handler = RetryHandler()

    assert handler.decide(0).should_retry is True
    assert handler.decide(0).delay_seconds == 5
    assert handler.decide(1).delay_seconds == 10
    assert handler.decide(2).delay_seconds == 20
    assert handler.decide(3).should_retry is False


def test_retry_handler_classifies_fatal_errors() -> None:
    handler = RetryHandler()

    decision = handler.decide(0, error_type="InvalidInputError", error_message="invalid input")

    assert decision.should_retry is False
    assert decision.classification == ErrorClassification.FATAL
    assert decision.next_retry_at is None


def test_retry_handler_classifies_retryable_errors() -> None:
    handler = RetryHandler(base_delay_seconds=7, max_delay_seconds=100)

    decision = handler.decide(2, error_type="NetworkTimeout", error_message="connection timeout")

    assert decision.should_retry is True
    assert decision.classification == ErrorClassification.RETRYABLE
    assert decision.retry_strategy == RetryStrategy.EXPONENTIAL_BACKOFF
    assert decision.delay_seconds == 28
    assert decision.next_retry_at is not None


def test_classify_error_defaults_unknown_to_retryable() -> None:
    assert classify_error("UnexpectedRemoteError") == ErrorClassification.RETRYABLE


def test_resource_manager_maps_task_type_to_separate_pool() -> None:
    manager = ResourceManager(
        ResourceConfig(
            total_concurrency=3,
            browser_concurrency=1,
            media_concurrency=1,
            ai_concurrency=1,
            task_type_pools={"render_page": "browser", "transcode": "media"},
        )
    )

    assert manager.pool_for("render_page") == "browser"
    assert manager.pool_for("transcode") == "media"
    assert manager.pool_for("summarize") == "ai"


def test_resource_manager_acquire_release_browser_slot_blocks_until_available() -> None:
    manager = ResourceManager(ResourceConfig(total_concurrency=2, browser_concurrency=1))
    acquired_second = threading.Event()

    manager.acquire_slot("browser")

    def acquire_second_slot() -> None:
        manager.acquire_slot("browser")
        acquired_second.set()
        manager.release_slot("browser")

    thread = threading.Thread(target=acquire_second_slot)
    thread.start()
    time.sleep(0.05)

    assert acquired_second.is_set() is False

    manager.release_slot("browser")
    thread.join(timeout=1)

    assert acquired_second.is_set() is True


def test_resource_manager_rejects_unknown_slot_type() -> None:
    manager = ResourceManager(ResourceConfig())

    with pytest.raises(ValueError):
        manager.acquire_slot("ai")  # type: ignore[arg-type]


def test_task_executor_runs_registered_handler() -> None:
    registry = TaskRegistry()
    registry.register("ai_summary", lambda task: {"seen": task.data["value"]})
    executor = TaskExecutor(registry)
    task = TaskRecord(
        task_id=uuid4(),
        task_type="ai_summary",
        status=TaskStatus.RUNNING,
        data={"value": 7},
        retry_count=0,
        max_retries=3,
        last_heartbeat=None,
        next_run_at=datetime.now(UTC),
        worker_id="worker-1",
    )

    result = executor.execute(task)

    assert result.success is True
    assert result.output == {"seen": 7}


def test_task_state_machine_allows_required_lifecycle() -> None:
    assert can_transition(TaskStatus.PENDING, TaskStatus.READY)
    assert can_transition(TaskStatus.READY, TaskStatus.RUNNING)
    assert can_transition(TaskStatus.RUNNING, TaskStatus.RETRY)
    assert can_transition(TaskStatus.RETRY, TaskStatus.READY)
    assert can_transition(TaskStatus.RUNNING, TaskStatus.SUCCESS)
    assert can_transition(TaskStatus.RUNNING, TaskStatus.FAILED)


def test_task_state_machine_rejects_invalid_transition() -> None:
    with pytest.raises(InvalidTaskStateTransition):
        validate_task_transition(TaskStatus.PENDING, TaskStatus.RUNNING)


def test_task_executor_skips_success_duplicate() -> None:
    registry = TaskRegistry()
    registry.register("ai_summary", lambda task: {"should_not": "run"})
    executor = TaskExecutor(registry)
    task = TaskRecord(
        task_id=uuid4(),
        task_type="ai_summary",
        status=TaskStatus.SUCCESS,
        data={"value": 7},
        retry_count=0,
        max_retries=3,
        last_heartbeat=None,
        next_run_at=datetime.now(UTC),
        worker_id=None,
    )

    result = executor.execute(task)

    assert result.success is True
    assert result.output == {}


def test_task_executor_skips_non_running_task_with_execution_hash() -> None:
    registry = TaskRegistry()
    registry.register("ai_summary", lambda task: {"should_not": "run"})
    executor = TaskExecutor(registry)
    task = TaskRecord(
        task_id=uuid4(),
        task_type="ai_summary",
        status=TaskStatus.FAILED,
        data={"value": 7},
        retry_count=1,
        max_retries=3,
        last_heartbeat=None,
        next_run_at=datetime.now(UTC),
        worker_id=None,
        execution_hash="already-claimed",
    )

    result = executor.execute(task)

    assert result.success is True
    assert result.output == {}


def test_task_executor_runs_locked_task_with_execution_hash() -> None:
    registry = TaskRegistry()
    registry.register("ai_summary", lambda task: {"seen": task.execution_hash})
    executor = TaskExecutor(registry)
    task = TaskRecord(
        task_id=uuid4(),
        task_type="ai_summary",
        status=TaskStatus.RUNNING,
        data={"value": 7},
        retry_count=0,
        max_retries=3,
        last_heartbeat=None,
        next_run_at=datetime.now(UTC),
        worker_id="worker-1",
        execution_hash="current-execution",
    )

    result = executor.execute(task)

    assert result.success is True
    assert result.output == {"seen": "current-execution"}


def test_execution_hash_is_deterministic() -> None:
    task_id = uuid4()
    first = TaskRecord(
        task_id=task_id,
        task_type="ai_summary",
        status=TaskStatus.PENDING,
        data={"b": 2, "a": 1},
        retry_count=0,
        max_retries=3,
        last_heartbeat=None,
        next_run_at=datetime.now(UTC),
        worker_id=None,
    )
    second = TaskRecord(
        task_id=task_id,
        task_type="ai_summary",
        status=TaskStatus.PENDING,
        data={"a": 1, "b": 2},
        retry_count=0,
        max_retries=3,
        last_heartbeat=None,
        next_run_at=datetime.now(UTC),
        worker_id=None,
    )

    assert build_execution_hash(first) == build_execution_hash(second)


def test_runtime_config_validates_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("WORKER_ID", "worker-1")
    monkeypatch.setenv("WORKER_TASK_TYPE_POOLS", json.dumps({"render": "browser"}))

    config = RuntimeConfig.from_env()

    assert config.worker_id == "worker-1"
    assert config.task_type_pools == {"render": "browser"}


def test_json_logging_includes_required_fields(capsys: pytest.CaptureFixture[str]) -> None:
    configure_json_logging("INFO")
    logging.getLogger("workers.runtime").info(
        "task_success",
        extra={
            "event": "task_success",
            "task_id": "task-1",
            "worker_id": "worker-1",
            "task_type": "ai",
            "duration": 12,
            "error": None,
        },
    )

    record = json.loads(capsys.readouterr().out)

    assert record["task_id"] == "task-1"
    assert record["worker_id"] == "worker-1"
    assert record["task_type"] == "ai"
    assert record["duration"] == 12
    assert "error" in record
