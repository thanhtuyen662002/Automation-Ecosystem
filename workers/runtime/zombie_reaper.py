from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

from pythonjsonlogger import jsonlogger

from workers.runtime.config import load_config
from workers.runtime.database import TaskStore
from workers.runtime.heartbeat_manager import (
    DEFAULT_ZOMBIE_TIMEOUT_SECONDS,
    mark_zombie_tasks_failed,
)
from workers.runtime.queue import QueueConfig, RedisTaskQueue
from workers.runtime.retry_handler import RetryHandler
from workers.runtime.worker_runtime import RuntimeConfig


LOGGER = logging.getLogger("workers.runtime.zombie_reaper")


@dataclass(frozen=True)
class ZombieReaperResult:
    recovered_count: int


def run_zombie_reaper(
    store: TaskStore,
    queue: RedisTaskQueue,
    retry_handler: RetryHandler,
    zombie_timeout_seconds: int = DEFAULT_ZOMBIE_TIMEOUT_SECONDS,
    batch_size: int = 100,
) -> ZombieReaperResult:
    recovered = mark_zombie_tasks_failed(
        store=store,
        retry_handler=retry_handler,
        retry_trigger=lambda task: queue.enqueue_ready_task(task.task_id, task.task_type),
        zombie_timeout_seconds=zombie_timeout_seconds,
        batch_size=batch_size,
    )
    LOGGER.info(
        "zombie reaper completed",
        extra={"event": "zombie_reaper_completed", "recovered_count": recovered},
    )
    return ZombieReaperResult(recovered_count=recovered)


def main() -> None:
    configure_json_logging()
    loaded = load_config()
    config = RuntimeConfig.from_env()
    store = TaskStore(config)
    queue = RedisTaskQueue(
        QueueConfig(
            redis_url=config.redis_url,
            worker_id=config.worker_id,
            stream_name=config.stream_name,
            consumer_group=config.consumer_group,
            read_block_ms=config.read_block_ms,
        )
    )
    retry_handler = RetryHandler(
        max_retries=loaded.max_retries,
        base_delay_seconds=loaded.retry_base_delay_seconds,
        max_delay_seconds=loaded.retry_max_delay_seconds,
    )
    store.open()
    try:
        queue.ensure_group()
        run_zombie_reaper(store, queue, retry_handler)
    finally:
        queue.close()
        store.close()


def configure_json_logging() -> None:
    logging.getLogger().handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        jsonlogger.JsonFormatter(
            "%(asctime)s %(levelname)s %(message)s %(event)s %(task_id)s "
            "%(worker_id)s %(task_type)s %(error)s %(retry_count)s %(retry_scheduled)s "
            "%(recovered_count)s"
        )
    )
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)


if __name__ == "__main__":
    main()
