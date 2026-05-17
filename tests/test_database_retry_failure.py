from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_mark_task_failure_increments_retry_count_and_exhausts(tmp_path):
    from core.workflow_manager import WorkflowManager
    from database.database import AutomationDatabase, RetryConfig, TaskStatus

    db = AutomationDatabase(
        f"sqlite+aiosqlite:///{tmp_path / 'app.db'}",
        retry_config=RetryConfig(base_delay_seconds=0, max_delay_seconds=0),
    )
    await db.init_schema("database/schema.sql")
    detail = await db.create_job(
        workflow_name="retry-test",
        tasks=[
            {
                "task_type": "ai",
                "task_key": "ai_task",
                "payload": {"prompt": "hello"},
                "metadata": {},
                "max_retries": 2,
                "depends_on": [],
            }
        ],
    )
    manager = WorkflowManager(db, worker_id="worker-1")

    await manager.promote_tasks_to_ready()
    first = (await db.acquire_ready_tasks_batch(1, "worker-1"))[0]
    first_failed = await db.mark_task_failure(first.task.id, first.execution.id, RuntimeError("first"))

    assert first_failed.status == TaskStatus.RETRY
    assert first_failed.retry_count == 1

    await manager.promote_tasks_to_ready()
    second = (await db.acquire_ready_tasks_batch(1, "worker-1"))[0]
    second_failed = await db.mark_task_failure(second.task.id, second.execution.id, RuntimeError("second"))

    assert second_failed.status == TaskStatus.FAILED
    assert second_failed.retry_count == 2
    assert str(second_failed.job_id) == str(detail.job.id)


@pytest.mark.asyncio
async def test_mark_task_failure_force_final_skips_retry(tmp_path):
    from core.workflow_manager import WorkflowManager
    from database.database import AutomationDatabase, TaskStatus

    db = AutomationDatabase(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    await db.init_schema("database/schema.sql")
    await db.create_job(
        workflow_name="fatal-test",
        tasks=[
            {
                "task_type": "ai",
                "task_key": "ai_task",
                "payload": {"prompt": "hello"},
                "metadata": {},
                "max_retries": 5,
                "depends_on": [],
            }
        ],
    )
    manager = WorkflowManager(db, worker_id="worker-1")

    await manager.promote_tasks_to_ready()
    acquired = (await db.acquire_ready_tasks_batch(1, "worker-1"))[0]
    failed = await db.mark_task_failure(
        acquired.task.id,
        acquired.execution.id,
        RuntimeError("fatal"),
        force_final=True,
    )

    assert failed.status == TaskStatus.FAILED
    assert failed.retry_count == 1


@pytest.mark.asyncio
async def test_dependency_task_waits_for_parent_success(tmp_path):
    from core.workflow_manager import WorkflowManager
    from database.database import AutomationDatabase

    db = AutomationDatabase(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    await db.init_schema("database/schema.sql")
    await db.create_job(
        workflow_name="dependency-test",
        tasks=[
            {
                "task_type": "tiktok.download_videos",
                "task_key": "tiktok_download",
                "payload": {"selected_videos": [{"url": "https://example.com"}]},
                "metadata": {},
                "max_retries": 2,
                "depends_on": [],
            },
            {
                "task_type": "tiktok.remake_video",
                "task_key": "tiktok_remake",
                "payload": {"video_paths": {"from_task": "tiktok_download", "field": "video_paths"}},
                "metadata": {},
                "max_retries": 1,
                "depends_on": ["tiktok_download"],
            },
        ],
    )
    manager = WorkflowManager(db, worker_id="worker-1")

    promoted = await manager.promote_tasks_to_ready(limit=10)
    assert [task.task_key for task in promoted] == ["tiktok_download"]

    acquired = (await db.acquire_ready_tasks_batch(10, "worker-1"))[0]
    assert acquired.task.task_key == "tiktok_download"
    await db.mark_task_success(
        acquired.task.id,
        acquired.execution.id,
        {"video_paths": ["media_output/job/downloads/video_00.mp4"], "ok": True},
    )

    promoted = await manager.promote_tasks_to_ready(limit=10)
    assert [task.task_key for task in promoted] == ["tiktok_remake"]
