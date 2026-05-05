from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

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

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_record(cls, stats: SystemStatsRecord) -> "SystemStatsResponse":
        return cls.model_validate(stats)


# ── TikTok Pipeline Schemas ───────────────────────────────────────────────────

class TikTokPipelineRequest(BaseModel):
    """
    Request body for POST /pipelines/tiktok.

    At least one of product_url or product_image_path must be provided.
    """

    product_url: str | None = Field(
        default=None,
        description="Public URL of the product page to extract information from.",
    )
    product_image_path: str | None = Field(
        default=None,
        description="Local absolute path to a product image file.",
    )

    # Video filtering
    min_views: int | None = Field(
        default=None,
        ge=0,
        description="Minimum view count for a TikTok video to be considered. "
                    "Defaults to env TIKTOK_MIN_VIEWS.",
    )
    min_likes: int | None = Field(
        default=None,
        ge=0,
        description="Minimum like count. Defaults to env TIKTOK_MIN_LIKES.",
    )
    min_duration: float = Field(
        default=15.0,
        ge=1.0,
        description="Minimum video duration in seconds.",
    )
    max_duration: float = Field(
        default=180.0,
        le=3600.0,
        description="Maximum video duration in seconds.",
    )
    top_n: int | None = Field(
        default=None,
        ge=1,
        le=20,
        description="Number of videos to download and remix. Defaults to env TIKTOK_TOP_N.",
    )

    # Video remake
    add_grain: bool = Field(default=True, description="Add subtle film grain to the remixed video.")
    bgm_path: str | None = Field(
        default=None,
        description="Optional local path to a background music file (.mp3 / .m4a).",
    )

    # Content generation
    comment_count: int = Field(default=3, ge=2, le=5, description="Number of comments to generate.")

    # Job metadata
    job_key: str | None = Field(
        default=None,
        min_length=1,
        description="Optional idempotency key — submitting the same key twice returns the existing job.",
    )
    priority: int = Field(default=0, description="Job dispatch priority (higher = runs sooner).")

    @model_validator(mode="after")
    def _require_product_source(self) -> "TikTokPipelineRequest":
        if not self.product_url and not self.product_image_path:
            raise ValueError("At least one of 'product_url' or 'product_image_path' must be provided.")
        return self
