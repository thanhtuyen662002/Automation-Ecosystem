from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_delete_job_removes_owned_records_and_detaches_action_logs(tmp_path):
    from core.workflow_manager import WorkflowManager
    from database.database import AutomationDatabase

    db = AutomationDatabase(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    await db.init_schema("database/schema.sql")
    detail = await db.create_job(
        workflow_name="delete-test",
        tasks=[
            {
                "task_type": "ai",
                "task_key": "extract",
                "payload": {"prompt": "hello"},
                "metadata": {},
                "depends_on": [],
            },
            {
                "task_type": "ai",
                "task_key": "remake",
                "payload": {"prompt": "next"},
                "metadata": {},
                "depends_on": ["extract"],
            },
        ],
    )
    manager = WorkflowManager(db, worker_id="worker-1")
    await manager.promote_tasks_to_ready()
    acquired = (await db.acquire_ready_tasks_batch(1, "worker-1"))[0]
    await db.mark_task_success(acquired.task.id, acquired.execution.id, {"ok": True})

    artifact_id = str(uuid4())
    action_log_id = str(uuid4())
    async with db.connection() as conn:
        await conn.execute(
            """
            INSERT INTO artifacts (id, job_id, task_id, execution_id, artifact_type, storage_uri)
            VALUES (?, ?, ?, ?, 'video', ?)
            """,
            (
                artifact_id,
                str(detail.job.id),
                str(acquired.task.id),
                str(acquired.execution.id),
                "media_output/delete-test/video.mp4",
            ),
        )
        await conn.execute(
            """
            INSERT INTO action_logs (id, job_id, task_id, execution_id, action_type, status, request)
            VALUES (?, ?, ?, ?, 'test', 'attempted', '{}')
            """,
            (
                action_log_id,
                str(detail.job.id),
                str(acquired.task.id),
                str(acquired.execution.id),
            ),
        )
        await conn.commit()

    assert await db.delete_job(detail.job.id) is True
    assert await db.get_job_detail(detail.job.id) is None

    task_ids = [str(task.id) for task in detail.tasks]
    placeholders = ",".join("?" * len(task_ids))
    async with db.connection() as conn:
        tasks = await (await conn.execute("SELECT COUNT(*) AS count FROM tasks WHERE job_id = ?", (str(detail.job.id),))).fetchone()
        executions = await (
            await conn.execute(
                f"SELECT COUNT(*) AS count FROM task_executions WHERE task_id IN ({placeholders})",
                task_ids,
            )
        ).fetchone()
        dependencies = await (
            await conn.execute(
                f"""
                SELECT COUNT(*) AS count FROM task_dependencies
                WHERE task_id IN ({placeholders}) OR depends_on_task_id IN ({placeholders})
                """,
                task_ids + task_ids,
            )
        ).fetchone()
        artifacts = await (await conn.execute("SELECT COUNT(*) AS count FROM artifacts WHERE id = ?", (artifact_id,))).fetchone()
        action_log = await (
            await conn.execute(
                "SELECT job_id, task_id, execution_id FROM action_logs WHERE id = ?",
                (action_log_id,),
            )
        ).fetchone()

    assert tasks["count"] == 0
    assert executions["count"] == 0
    assert dependencies["count"] == 0
    assert artifacts["count"] == 0
    assert action_log["job_id"] is None
    assert action_log["task_id"] is None
    assert action_log["execution_id"] is None


@pytest.mark.asyncio
async def test_delete_job_blocks_running_tasks(tmp_path):
    from core.workflow_manager import WorkflowManager
    from database.database import AutomationDatabase, ConflictError

    db = AutomationDatabase(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    await db.init_schema("database/schema.sql")
    detail = await db.create_job(
        workflow_name="delete-running-test",
        tasks=[
            {
                "task_type": "ai",
                "task_key": "extract",
                "payload": {"prompt": "hello"},
                "metadata": {},
                "depends_on": [],
            }
        ],
    )
    manager = WorkflowManager(db, worker_id="worker-1")
    await manager.promote_tasks_to_ready()
    await db.acquire_ready_tasks_batch(1, "worker-1")

    with pytest.raises(ConflictError):
        await db.delete_job(detail.job.id)

    assert await db.get_job_detail(detail.job.id) is not None


@pytest.mark.asyncio
async def test_delete_job_route_maps_missing_and_conflict():
    from api.routes.jobs import delete_job
    from database.database import ConflictError

    class MissingDatabase:
        async def delete_job(self, job_id):
            return False

    class ConflictDatabase:
        async def delete_job(self, job_id):
            raise ConflictError("Cannot delete a job while one or more tasks are RUNNING")

    with pytest.raises(HTTPException) as missing:
        await delete_job(uuid4(), MissingDatabase())
    assert missing.value.status_code == 404

    with pytest.raises(HTTPException) as conflict:
        await delete_job(uuid4(), ConflictDatabase())
    assert conflict.value.status_code == 409
