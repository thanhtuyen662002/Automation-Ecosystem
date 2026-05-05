from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from api.dependencies import DatabaseDependency
from api.schemas import TaskResponse
from database.database import TaskStatus


LOGGER = logging.getLogger("api.tasks")
router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: UUID, database: DatabaseDependency) -> TaskResponse:
    task = await database.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    LOGGER.info("task_read", extra={"event": "task_read", "task_id": str(task_id)})
    return TaskResponse.from_record(task)


@router.post("/{task_id}/retry", response_model=TaskResponse)
async def retry_task(task_id: UUID, database: DatabaseDependency) -> TaskResponse:
    task = await database.reset_failed_task_for_retry(task_id)
    LOGGER.info("task_retry_reset", extra={"event": "task_retry_reset", "task_id": str(task_id)})
    return TaskResponse.from_record(task)


@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    database: DatabaseDependency,
    status: TaskStatus | None = None,
    task_type: str | None = Query(default=None, min_length=1),
    job_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[TaskResponse]:
    tasks = await database.list_tasks(
        limit=limit,
        offset=offset,
        status=status,
        task_type=task_type,
        job_id=job_id,
    )
    LOGGER.info(
        "tasks_listed",
        extra={
            "event": "tasks_listed",
            "status": status.value if status else None,
            "task_type": task_type,
            "job_id": str(job_id) if job_id else None,
            "limit": limit,
            "offset": offset,
        },
    )
    return [TaskResponse.from_record(task) for task in tasks]
