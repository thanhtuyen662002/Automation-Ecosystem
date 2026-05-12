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
    # Per-task statuses: {task_key -> status_string}
    # Populated by list endpoint for real-time step indicator in the UI.
    task_statuses: dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_record(
        cls, job: "JobRecord", task_statuses: dict[str, str] | None = None
    ) -> "JobSummaryResponse":
        obj = cls.model_validate(job)
        if task_statuses:
            obj.task_statuses = task_statuses
        return obj



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


# ── Accounts Schemas ──────────────────────────────────────────────────────────

class AccountCreateRequest(BaseModel):
    platform: str = Field(min_length=1)
    account_handle: str = Field(min_length=1)
    proxy_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AccountHealthRequest(BaseModel):
    status: str = Field(description="One of: healthy, limited, banned")

    @model_validator(mode="after")
    def _validate_status(self) -> "AccountHealthRequest":
        if self.status not in {"healthy", "limited", "banned"}:
            raise ValueError("status must be one of: healthy, limited, banned")
        return self


class AccountResponse(BaseModel):
    id: str
    platform: str
    account_handle: str
    status: str
    proxy_url: str | None
    metadata: dict[str, Any]
    # Session fields (None for accounts that have never connected)
    session_valid: bool
    last_login_at: str | None
    user_agent: str | None
    created_at: str | None
    updated_at: str | None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_row(cls, row: dict) -> "AccountResponse":
        return cls(
            id=row["id"],
            platform=row["platform"],
            account_handle=row["account_handle"],
            status=row["status"],
            proxy_url=row.get("proxy_url"),
            metadata=row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
            session_valid=bool(row.get("session_valid", 0)),
            last_login_at=str(row["last_login_at"]) if row.get("last_login_at") else None,
            user_agent=row.get("user_agent"),
            created_at=str(row["created_at"]) if row.get("created_at") else None,
            updated_at=str(row["updated_at"]) if row.get("updated_at") else None,
        )


class SessionStatusResponse(BaseModel):
    account_id: str
    session_valid: bool
    has_cookies: bool
    last_login_at: str | None
    user_agent: str | None



# ── Artifacts Schemas ─────────────────────────────────────────────────────────

class ArtifactStatusUpdateRequest(BaseModel):
    status: str = Field(description="One of: approved, rejected")

    @model_validator(mode="after")
    def _validate_status(self) -> "ArtifactStatusUpdateRequest":
        if self.status not in {"approved", "rejected"}:
            raise ValueError("status must be one of: approved, rejected")
        return self


class ArtifactResponse(BaseModel):
    id: str
    job_id: str | None
    task_id: str | None
    artifact_type: str
    status: str
    storage_uri: str
    mime_type: str | None
    size_bytes: int | None
    checksum: str | None
    metadata: dict[str, Any]
    created_at: str | None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_row(cls, row: dict) -> "ArtifactResponse":
        return cls(
            id=row["id"],
            job_id=row.get("job_id"),
            task_id=row.get("task_id"),
            artifact_type=row["artifact_type"],
            status=row["status"],
            storage_uri=row["storage_uri"],
            mime_type=row.get("mime_type"),
            size_bytes=row.get("size_bytes"),
            checksum=row.get("checksum"),
            metadata=row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
            created_at=str(row["created_at"]) if row.get("created_at") else None,
        )


# ── Policy Rules Schemas ──────────────────────────────────────────────────────

_VALID_ACTION_TYPES = {
    "publish_tiktok",
    "publish_youtube",
    "publish_facebook",
    "publish",
}


class PolicyRuleCreateRequest(BaseModel):
    action_type: str = Field(min_length=1)
    rule_name: str = Field(min_length=1)
    max_actions: int = Field(ge=1)
    window_seconds: int = Field(ge=60)
    account_id: str | None = None
    platform: str | None = None
    cooldown_seconds: int = Field(default=0, ge=0)


class PolicyRuleResponse(BaseModel):
    id: str
    account_id: str | None
    platform: str | None
    action_type: str
    rule_name: str
    enabled: bool
    cooldown_seconds: int
    max_actions: int | None
    window_seconds: int | None
    created_at: str | None
    updated_at: str | None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_row(cls, row: dict) -> "PolicyRuleResponse":
        return cls(
            id=row["id"],
            account_id=row.get("account_id"),
            platform=row.get("platform"),
            action_type=row["action_type"],
            rule_name=row["rule_name"],
            enabled=bool(row["enabled"]),
            cooldown_seconds=row["cooldown_seconds"],
            max_actions=row.get("max_actions"),
            window_seconds=row.get("window_seconds"),
            created_at=str(row["created_at"]) if row.get("created_at") else None,
            updated_at=str(row["updated_at"]) if row.get("updated_at") else None,
        )

