from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest


def test_normalize_top_n_clamps_request_values() -> None:
    from api.routes.tiktok import _normalize_top_n

    assert _normalize_top_n(0) == 1
    assert _normalize_top_n(1) == 1
    assert _normalize_top_n(10) == 10
    assert _normalize_top_n(20) == 10


def test_normalize_top_n_clamps_env_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.routes.tiktok import _normalize_top_n

    monkeypatch.setenv("TIKTOK_TOP_N", "20")

    assert _normalize_top_n(None) == 10


def test_publish_wait_approval_max_retries_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.routes.tiktok import _publish_wait_approval_max_retries

    monkeypatch.delenv("PUBLISH_WAIT_APPROVAL_MAX_RETRIES", raising=False)
    assert _publish_wait_approval_max_retries() == 288

    monkeypatch.setenv("PUBLISH_WAIT_APPROVAL_MAX_RETRIES", "24")
    assert _publish_wait_approval_max_retries() == 24

    monkeypatch.setenv("PUBLISH_WAIT_APPROVAL_MAX_RETRIES", "0")
    assert _publish_wait_approval_max_retries() == 1


@pytest.mark.asyncio
async def test_create_tiktok_pipeline_payload_includes_search_and_download_account(
    monkeypatch: pytest.MonkeyPatch,
):
    from api.routes.tiktok import create_tiktok_pipeline
    from api.schemas import TikTokPipelineRequest
    from database.database import JobDetailRecord, JobRecord, TaskRecord, TaskStatus

    account_id = uuid4()
    monkeypatch.setenv("TIKTOK_TOP_N", "20")
    monkeypatch.setenv("PUBLISH_WAIT_APPROVAL_MAX_RETRIES", "24")

    class FakeDatabase:
        def __init__(self) -> None:
            self.tasks: list[dict] = []

        async def get_account(self, requested_account_id: str) -> dict:
            assert requested_account_id == str(account_id)
            return {
                "id": str(account_id),
                "platform": "tiktok",
                "account_handle": "@searcher",
                "profile_url": None,
                "external_user_id": None,
                "status": "healthy",
                "proxy_url": None,
                "proxy_country": None,
                "metadata": {
                    "browser_provider": "adspower_manual",
                    "adspower_profile_id": "profile-1",
                    "manual_login_state": "connected_by_confirmation",
                },
                "session_valid": 1,
                "cookies": None,
                "soft_ban_detected": 0,
                "risk_score": 0.0,
                "warmup_sessions_completed": 0,
                "failed_publish_count": 0,
                "captcha_hit_count": 0,
                "avatar_url": None,
                "display_name": None,
                "last_login_at": None,
                "created_at": None,
                "updated_at": None,
            }

        async def create_job(self, **kwargs):
            self.tasks = kwargs["tasks"]
            now = datetime.now(UTC)
            job_id = uuid4()
            job = JobRecord(
                id=job_id,
                job_key=kwargs.get("job_key"),
                workflow_name=kwargs["workflow_name"],
                status="pending",
                priority=kwargs["priority"],
                input=kwargs["input_data"],
                metadata=kwargs["metadata"],
                error_type=None,
                error_message=None,
                started_at=None,
                completed_at=None,
                created_at=now,
                updated_at=now,
            )
            tasks = [
                TaskRecord(
                    id=uuid4(),
                    job_id=job_id,
                    task_key=task["task_key"],
                    task_type=task["task_type"],
                    status=TaskStatus.PENDING,
                    priority=0,
                    payload=task["payload"],
                    metadata=task["metadata"],
                    retry_count=0,
                    max_retries=task["max_retries"],
                    next_run_at=now,
                    next_retry_at=None,
                    account_id=UUID(task["account_id"]) if task.get("account_id") else None,
                    action_type=task.get("action_type"),
                    idempotency_key=None,
                    result=None,
                    error_type=None,
                    error_message=None,
                )
                for task in self.tasks
            ]
            return JobDetailRecord(job=job, tasks=tasks)

    database = FakeDatabase()
    request = TikTokPipelineRequest(
        product_url="https://example.com/product",
        account_id=account_id,
        min_views=12345,
        auto_publish=True,
    )

    response = await create_tiktok_pipeline(request, database)  # type: ignore[arg-type]

    search_task = next(task for task in database.tasks if task["task_type"] == "tiktok.search_tiktok")
    select_task = next(task for task in database.tasks if task["task_type"] == "tiktok.select_videos")
    download_task = next(task for task in database.tasks if task["task_type"] == "tiktok.download_videos")
    remake_task = next(task for task in database.tasks if task["task_type"] == "tiktok.remake_video")
    publish_task = next(task for task in database.tasks if task["task_type"] == "publish_tiktok")

    assert response.workflow_name == "tiktok_content_pipeline"
    assert response.metadata["top_n"] == 10
    assert search_task["account_id"] == str(account_id)
    assert search_task["payload"]["account_id"] == str(account_id)
    assert search_task["payload"]["min_views"] == 12345
    assert select_task["payload"]["top_n"] == 10
    assert download_task["payload"]["account_id"] == str(account_id)
    assert download_task["depends_on"] == ["tiktok_select"]
    assert download_task["max_retries"] == 2
    assert remake_task["depends_on"] == ["tiktok_download", "tiktok_extract_product_info"]
    assert remake_task["payload"]["video_paths"] == {"from_task": "tiktok_download", "field": "video_paths"}
    assert remake_task["max_retries"] == 1
    assert publish_task["max_retries"] == 24
