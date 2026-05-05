from __future__ import annotations

from typing import Any
from uuid import UUID

from automation_engine.config import EngineSettings
from automation_engine.database import DatabaseStore
from automation_engine.models import EnqueueRequest, JobRecord
from automation_engine.queue import RedisJobQueue
from automation_engine.registry import TaskHandler, TaskRegistry


class ExecutionEngine:
    def __init__(
        self,
        settings: EngineSettings,
        store: DatabaseStore | None = None,
        queue: RedisJobQueue | None = None,
        registry: TaskRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.store = store or DatabaseStore(settings)
        self.queue = queue or RedisJobQueue(settings)
        self.registry = registry or TaskRegistry()

    def open(self) -> None:
        self.store.open()
        self.queue.ensure_group()

    def close(self) -> None:
        self.queue.close()
        self.store.close()

    def enqueue_job(
        self,
        task_name: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
        priority: int = 0,
        timeout_seconds: int | None = None,
    ) -> JobRecord:
        request = EnqueueRequest(
            task_name=task_name,
            payload=payload,
            idempotency_key=idempotency_key,
            priority=priority,
            timeout_seconds=timeout_seconds,
        )
        job = self.store.enqueue_job(
            task_name=request.task_name,
            payload=request.payload,
            idempotency_key=request.idempotency_key,
            priority=request.priority,
            timeout_seconds=request.timeout_seconds or self.settings.default_job_timeout_seconds,
            max_attempts=self.settings.max_attempts,
        )
        if job.status.value == "pending":
            self.queue.publish_job(job.id)
        return job

    def get_job(self, job_id: UUID) -> JobRecord:
        return self.store.get_job(job_id)

    def cancel_job(self, job_id: UUID) -> bool:
        return self.store.cancel_job(job_id)

    def register_task(self, task_name: str, handler: TaskHandler) -> None:
        self.registry.register_task(task_name, handler)
