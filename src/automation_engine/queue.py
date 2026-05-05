from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from redis import Redis
from redis.exceptions import ResponseError

from automation_engine.config import EngineSettings
from automation_engine.models import JobEnvelope


@dataclass(frozen=True)
class QueueMessage:
    message_id: str
    envelope: JobEnvelope


class RedisJobQueue:
    def __init__(self, settings: EngineSettings) -> None:
        self._settings = settings
        self._client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=max(settings.read_block_ms / 1000.0 + 5, 10),
            socket_connect_timeout=10,
            health_check_interval=30,
        )

    def close(self) -> None:
        self._client.close()

    def ensure_group(self) -> None:
        try:
            self._client.xgroup_create(
                self._settings.stream_name,
                self._settings.consumer_group,
                id="0",
                mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def publish_job(self, job_id: UUID) -> str:
        return str(
            self._client.xadd(
                self._settings.stream_name,
                {"job_id": str(job_id)},
                maxlen=100_000,
                approximate=True,
            )
        )

    def read(self) -> list[QueueMessage]:
        raw = self._client.xreadgroup(
            self._settings.consumer_group,
            self._settings.worker_id,
            {self._settings.stream_name: ">"},
            count=self._settings.read_count,
            block=self._settings.read_block_ms,
        )
        return self._parse_messages(raw)

    def claim_stale(self) -> list[QueueMessage]:
        result = self._client.xautoclaim(
            self._settings.stream_name,
            self._settings.consumer_group,
            self._settings.worker_id,
            min_idle_time=self._settings.lease_timeout_seconds * 1000,
            start_id="0-0",
            count=self._settings.read_count,
        )
        messages = result[1] if isinstance(result, tuple) and len(result) >= 2 else []
        parsed: list[QueueMessage] = []
        for message_id, fields in messages:
            parsed.append(self._parse_message(str(message_id), fields))
        return parsed

    def ack(self, message_id: str) -> None:
        self._client.xack(
            self._settings.stream_name,
            self._settings.consumer_group,
            message_id,
        )

    def ping(self) -> bool:
        return bool(self._client.ping())

    def _parse_messages(self, raw: object) -> list[QueueMessage]:
        parsed: list[QueueMessage] = []
        for stream_name, messages in raw or []:
            if stream_name != self._settings.stream_name:
                continue
            for message_id, fields in messages:
                parsed.append(self._parse_message(str(message_id), fields))
        return parsed

    @staticmethod
    def _parse_message(message_id: str, fields: dict[str, str]) -> QueueMessage:
        job_id = fields.get("job_id")
        if job_id is None:
            raise ValueError(f"Queue message {message_id} is missing job_id")
        return QueueMessage(message_id=message_id, envelope=JobEnvelope(job_id=UUID(job_id)))

