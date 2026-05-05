from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from database.database import JobDetailRecord, JobRecord, SystemStatsRecord, TaskRecord, TaskStatus


class TaskCreateRequest(BaseModel):
    task_type: str = Field(min_length=1)
    task_key: str | None = Field(default=None, min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    priority: int = 0
    max_retries: int = Field(default=3, ge=1)
    next_run_at: datetime | None = None
    action_type: str | None = Field(default=None, min_length=1)
    account_id: UUID | None = None
    parent_task_id: UUID | None = None
    idempotency_key: str | None = Field(default=None, min_length=1)
    depends_on: list[str] = Field(default_factory=list)


class JobCreateRequest(BaseModel):
    workflow_name: str = Field(min_length=1)
    tasks: list[TaskCreateRequest] = Field(min_length=1)
    job_key: str | None = Field(default=None, min_length=1)
    priority: int = 0
    input: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskResponse(BaseModel):
    id: UUID
    job_id: UUID
    task_type: str
    status: TaskStatus
    priority: int
    payload: dict[str, Any]
    metadata: dict[str, Any]
    retry_count: int
    max_retries: int
    next_run_at: datetime
    next_retry_at: datetime | None
    account_id: UUID | None
    action_type: str | None
    idempotency_key: str | None
    result: dict[str, Any] | None
    error_type: str | None
    error_message: str | None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_record(cls, task: TaskRecord) -> "TaskResponse":
        return cls.model_validate(task)


class JobSummaryResponse(BaseModel):
    id: UUID
    job_key: str | None
    workflow_name: str
    status: str
    priority: int
    input: dict[str, Any]
    metadata: dict[str, Any]
    error_type: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_record(cls, job: JobRecord) -> "JobSummaryResponse":
        return cls.model_validate(job)


class JobResponse(JobSummaryResponse):
    tasks: list[TaskResponse] = Field(default_factory=list)

    @classmethod
    def from_detail(cls, detail: JobDetailRecord) -> "JobResponse":
        base = JobSummaryResponse.from_record(detail.job).model_dump()
        base["tasks"] = [TaskResponse.from_record(task) for task in detail.tasks]
        return cls.model_validate(base)


class DispatchRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=1000)
    max_concurrent_per_worker: int | None = Field(default=None, ge=1)
    max_per_task_type: int | None = Field(default=None, ge=1)
    max_per_account: int | None = Field(default=None, ge=1)


class DispatchResponse(BaseModel):
    promoted: int
    dispatched: int
    throttled: int
    skipped: int


class SystemStatsResponse(BaseModel):
    total_tasks: int
    running: int
    pending: int
    failed: int
    success: int

    @classmethod
    def from_record(cls, stats: SystemStatsRecord) -> "SystemStatsResponse":
        return cls.model_validate(stats)
