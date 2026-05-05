from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from redis import Redis
from redis.exceptions import ResponseError


@dataclass(frozen=True)
class QueueConfig:
    redis_url: str
    worker_id: str
    stream_name: str = "worker:tasks"
    consumer_group: str = "worker-runtime"
    read_block_ms: int = 5000


class RedisTaskQueue:
    def __init__(self, config: QueueConfig) -> None:
        self._config = config
        self._redis = Redis.from_url(config.redis_url, decode_responses=True, socket_timeout=15)

    def close(self) -> None:
        self._redis.close()

    def ensure_group(self) -> None:
        try:
            self._redis.xgroup_create(
                self._config.stream_name,
                self._config.consumer_group,
                id="0",
                mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def enqueue_ready_task(self, task_id: UUID, task_type: str) -> str:
        return self.enqueue(task_id, task_type)

    def enqueue(self, task_id: UUID, task_type: str) -> str:
        return str(
            self._redis.xadd(
                self._config.stream_name,
                {"task_id": str(task_id), "task_type": task_type},
                maxlen=100000,
                approximate=True,
            )
        )

    def read(self) -> list[tuple[str, UUID, str]]:
        raw = self._redis.xreadgroup(
            self._config.consumer_group,
            self._config.worker_id,
            {self._config.stream_name: ">"},
            count=1,
            block=self._config.read_block_ms,
        )
        messages: list[tuple[str, UUID, str]] = []
        for _, entries in raw or []:
            for message_id, fields in entries:
                task_id = UUID(fields["task_id"])
                task_type = str(fields["task_type"])
                messages.append((str(message_id), task_id, task_type))
        return messages

    def ack(self, message_id: str) -> None:
        self._redis.xack(self._config.stream_name, self._config.consumer_group, message_id)
