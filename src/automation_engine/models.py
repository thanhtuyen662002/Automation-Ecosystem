from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class ExecutionStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class JobEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: UUID


class EnqueueRequest(BaseModel):
    task_name: str = Field(min_length=1, max_length=200)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=300)
    priority: int = 0
    timeout_seconds: int | None = Field(default=None, ge=1)
    max_attempts: int | None = Field(default=None, ge=1)

    @field_validator("task_name", "idempotency_key")
    @classmethod
    def strip_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if stripped == "":
            raise ValueError("value cannot be empty")
        return stripped


class JobRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_name: str
    payload: dict[str, Any]
    idempotency_key: str | None
    status: JobStatus
    priority: int
    attempts: int
    max_attempts: int
    timeout_seconds: int
    next_run_at: datetime
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    last_error_type: str | None = None
    last_error_message: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELED}


class ExecutionRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_id: UUID
    worker_id: str
    attempt: int
    status: ExecutionStatus
    started_at: datetime
    heartbeat_at: datetime
    completed_at: datetime | None = None
    error_type: str | None = None
    error_message: str | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)

