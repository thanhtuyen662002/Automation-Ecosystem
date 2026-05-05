from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pythonjsonlogger import jsonlogger
from redis.exceptions import RedisError

from workers.runtime.heartbeat_manager import HeartbeatManager
from workers.runtime.heartbeat_manager import mark_zombie_tasks_failed
from workers.runtime.config import load_config
from workers.runtime.database import TaskStore
from workers.runtime.queue import QueueConfig, RedisTaskQueue
from workers.runtime.resource_manager import ResourceConfig, ResourceManager
from workers.runtime.retry_handler import RetryHandler
from workers.runtime.task_executor import TaskExecutor, TaskRegistry


LOGGER = logging.getLogger("workers.runtime")


@dataclass(frozen=True)
class RuntimeConfig:
    database_url: str
    redis_url: str
    worker_id: str
    stream_name: str = "worker:tasks"
    consumer_group: str = "worker-runtime"
    read_block_ms: int = 5000
    heartbeat_interval_seconds: int = 10
    heartbeat_timeout_seconds: int = 300
    max_retries: int = 3
    retry_base_delay_seconds: int = 5
    retry_max_delay_seconds: int = 300
    total_concurrency: int = 4
    browser_concurrency: int = 1
    media_concurrency: int = 2
    ai_concurrency: int = 4
    default_pool: str = "ai"
    task_type_pools: dict[str, str] | None = None
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        config = load_config()
        return cls(
            database_url=config.database_url,
            redis_url=config.redis_url,
            worker_id=config.worker_id,
            stream_name=config.stream_name,
            consumer_group=config.consumer_group,
            read_block_ms=config.read_block_ms,
            heartbeat_interval_seconds=config.heartbeat_interval,
            heartbeat_timeout_seconds=config.task_timeout,
            max_retries=config.max_retries,
            retry_base_delay_seconds=config.retry_base_delay_seconds,
            retry_max_delay_seconds=config.retry_max_delay_seconds,
            total_concurrency=config.total_concurrency,
            browser_concurrency=config.max_browser_workers,
            media_concurrency=config.max_media_workers,
            ai_concurrency=config.max_ai_workers,
            default_pool=config.default_pool,
            task_type_pools=config.task_type_pools,
            log_level=config.log_level,
        )


class WorkerRuntime:
    def __init__(
        self,
        config: RuntimeConfig,
        registry: TaskRegistry | None = None,
        store: TaskStore | None = None,
        queue: RedisTaskQueue | None = None,
    ) -> None:
        self.config = config
        self.registry = registry or TaskRegistry()
        self.store = store or TaskStore(config)
        self.queue = queue or RedisTaskQueue(
            QueueConfig(
                redis_url=config.redis_url,
                worker_id=config.worker_id,
                stream_name=config.stream_name,
                consumer_group=config.consumer_group,
                read_block_ms=config.read_block_ms,
            )
        )
        self.executor = TaskExecutor(
            self.registry,
            lock_provider=lambda task_id: self.store.get_task_and_lock(task_id, self.config.worker_id),
            fetch_provider=self.store.fetch_task,
        )
        self.retry_handler = RetryHandler(
            max_retries=config.max_retries,
            base_delay_seconds=config.retry_base_delay_seconds,
            max_delay_seconds=config.retry_max_delay_seconds,
        )
        self.resource_manager = ResourceManager(
            ResourceConfig(
                total_concurrency=config.total_concurrency,
                browser_concurrency=config.browser_concurrency,
                media_concurrency=config.media_concurrency,
                ai_concurrency=config.ai_concurrency,
                default_pool=config.default_pool,
                task_type_pools=config.task_type_pools,
            )
        )
        self.heartbeat_manager = HeartbeatManager(config.heartbeat_interval_seconds)
        self._shutdown = threading.Event()
        self._futures: set[Future[None]] = set()
        self._pool = ThreadPoolExecutor(max_workers=config.total_concurrency)

    def register_task(self, task_type: str, handler) -> None:
        self.registry.register(task_type, handler)

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self._signal_shutdown)
        signal.signal(signal.SIGTERM, self._signal_shutdown)

    def run(self) -> None:
        configure_json_logging(self.config.log_level)
        self.store.open()
        self.queue.ensure_group()
        log_event("runtime_started", self.config.worker_id)
        try:
            while not self._shutdown.is_set():
                self._reap_finished_tasks(block=False)
                self._mark_and_requeue_zombies()
                self._requeue_due_retries()
                if len(self._futures) >= self.config.total_concurrency:
                    self._reap_finished_tasks(block=True)
                    continue
                try:
                    messages = self.queue.read()
                except (RedisError, ValueError, KeyError) as exc:
                    log_event("queue_read_error", self.config.worker_id, error=str(exc), error_type=type(exc).__name__)
                    time.sleep(1)
                    continue
                for message_id, task_id, task_type in messages:
                    future = self._pool.submit(self._run_one, message_id, task_id, task_type)
                    self._futures.add(future)
        finally:
            log_event("shutdown_waiting", self.config.worker_id)
            self._pool.shutdown(wait=True, cancel_futures=False)
            self.queue.close()
            self.store.close()
            log_event("runtime_stopped", self.config.worker_id)

    def stop(self) -> None:
        self._shutdown.set()

    def _run_one(self, message_id: str, task_id: UUID, task_type: str) -> None:
        started_at = time.monotonic()
        heartbeat = None
        try:
            task = self.executor.acquire_for_execution(task_id)
            if task is None:
                self.queue.ack(message_id)
                log_event("task_skipped", self.config.worker_id, task_id=task_id, task_type=task_type)
                return
            if task.task_type != task_type:
                retry = self.retry_handler.decide(
                    task.retry_count,
                    error_type="TaskTypeMismatch",
                    error_message=f"queue task_type={task_type} db task_type={task.task_type}",
                )
                self.store.mark_failed(
                    task.task_id,
                    self.config.worker_id,
                    "TaskTypeMismatch",
                    f"queue task_type={task_type} db task_type={task.task_type}",
                )
                self.queue.ack(message_id)
                log_event(
                    "task_type_mismatch",
                    self.config.worker_id,
                    task_id=task.task_id,
                    task_type=task_type,
                    error=f"queue task_type={task_type} db task_type={task.task_type}",
                )
                return
            with self.resource_manager.acquire(task.task_type) as pool_name:
                log_event(
                    "task_running",
                    self.config.worker_id,
                    task_id=task.task_id,
                    task_type=task.task_type,
                    resource_pool=pool_name,
                )
                heartbeat = self.heartbeat_manager.start(
                    task.task_id,
                    lambda current_task_id: self.store.heartbeat(current_task_id, self.config.worker_id),
                )
                result = self.executor.execute(task)
                duration_ms = int((time.monotonic() - started_at) * 1000)
                if result.success:
                    marked = self.store.mark_success(task.task_id, self.config.worker_id, result.output or {})
                    if not marked:
                        log_event(
                            "task_completion_lost",
                            self.config.worker_id,
                            task_id=task.task_id,
                            task_type=task.task_type,
                            duration=duration_ms,
                            error="task was no longer locked by this worker",
                        )
                        return
                    self.queue.ack(message_id)
                    log_event(
                        "task_success",
                        self.config.worker_id,
                        task_id=task.task_id,
                        task_type=task.task_type,
                        duration=duration_ms,
                    )
                    return
                retry = self.retry_handler.decide(
                    task.retry_count,
                    error_type=result.error_type,
                    error_message=result.error,
                )
                next_retry_count = task.retry_count + 1
                next_run_at = retry.next_retry_at or _now()
                if retry.should_retry:
                    marked = self.store.mark_retry(
                        task.task_id,
                        self.config.worker_id,
                        result.error_type or "TaskError",
                        result.error or "Task failed",
                        next_retry_count,
                        next_run_at,
                    )
                else:
                    marked = self.store.mark_failed(
                        task.task_id,
                        self.config.worker_id,
                        result.error_type or "TaskError",
                        result.error or "Task failed",
                    )
                if not marked:
                    log_event(
                        "task_completion_lost",
                        self.config.worker_id,
                        task_id=task.task_id,
                        task_type=task.task_type,
                        duration=duration_ms,
                        error="task was no longer locked by this worker",
                    )
                    return
                self.queue.ack(message_id)
                if retry.should_retry and retry.delay_seconds == 0:
                    self.queue.enqueue_ready_task(task.task_id, task.task_type)
                log_event(
                    "task_failed",
                    self.config.worker_id,
                    task_id=task.task_id,
                    task_type=task.task_type,
                    duration=duration_ms,
                    error=result.error,
                    error_type=result.error_type,
                    retry_count=next_retry_count,
                    retry_scheduled=retry.should_retry,
                )
        except Exception as exc:
            log_event(
                "task_runtime_error",
                self.config.worker_id,
                task_id=task_id,
                task_type=task_type,
                error=str(exc),
                error_type=type(exc).__name__,
            )
        finally:
            if heartbeat is not None:
                heartbeat.stop()

    def _mark_and_requeue_zombies(self) -> None:
        stale_before = _now() - timedelta(seconds=self.config.heartbeat_timeout_seconds)
        try:
            mark_zombie_tasks_failed(
                store=self.store,
                retry_handler=self.retry_handler,
                retry_trigger=lambda task: self.queue.enqueue_ready_task(task.task_id, task.task_type),
                zombie_timeout_seconds=self.config.heartbeat_timeout_seconds,
            )
        except Exception as exc:
            log_event("zombie_scan_error", self.config.worker_id, error=str(exc), error_type=type(exc).__name__)

    def _requeue_due_retries(self) -> None:
        try:
            for task in self.store.promote_due_retries_to_ready():
                self.queue.enqueue_ready_task(task.task_id, task.task_type)
        except Exception as exc:
            log_event("retry_scan_error", self.config.worker_id, error=str(exc), error_type=type(exc).__name__)

    def _reap_finished_tasks(self, block: bool) -> None:
        if not self._futures:
            return
        timeout = None if block else 0
        done, pending = wait(self._futures, timeout=timeout, return_when=FIRST_COMPLETED)
        self._futures = set(pending)
        for future in done:
            future.result()

    def _signal_shutdown(self, signum: int, _frame: object) -> None:
        log_event("shutdown_signal", self.config.worker_id, error=str(signum))
        self.stop()


def configure_json_logging(level: str) -> None:
    logging.getLogger().handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(message)s %(event)s %(task_id)s "
        "%(worker_id)s %(task_type)s %(duration)s %(error)s %(error_type)s"
    )
    handler.setFormatter(formatter)
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(level)


def log_event(event: str, worker_id: str, **fields: Any) -> None:
    extra = {"event": event, "worker_id": worker_id}
    extra.update({key: _log_value(value) for key, value in fields.items()})
    LOGGER.info(event, extra=extra)


def _now() -> datetime:
    return datetime.now(UTC)


def _log_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    return value


if __name__ == "__main__":
    runtime = WorkerRuntime(RuntimeConfig.from_env())
    runtime.install_signal_handlers()
    runtime.run()
