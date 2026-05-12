from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from api.dependencies import DatabaseDependency
from api.schemas import JobCreateRequest, JobResponse, JobSummaryResponse


LOGGER = logging.getLogger("api.jobs")
router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(request: JobCreateRequest, database: DatabaseDependency) -> JobResponse:
    detail = await database.create_job(
        workflow_name=request.workflow_name,
        tasks=[task.model_dump() for task in request.tasks],
        job_key=request.job_key,
        priority=request.priority,
        input_data=request.input,
        metadata=request.metadata,
    )
    LOGGER.info(
        "job_created",
        extra={"event": "job_created", "job_id": str(detail.job.id), "workflow_name": detail.job.workflow_name},
    )
    return JobResponse.from_detail(detail)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: UUID, database: DatabaseDependency) -> JobResponse:
    detail = await database.get_job_detail(job_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Job not found")
    LOGGER.info("job_read", extra={"event": "job_read", "job_id": str(job_id)})
    return JobResponse.from_detail(detail)


@router.get("", response_model=list[JobSummaryResponse])
async def list_jobs(
    database: DatabaseDependency,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[JobSummaryResponse]:
    jobs = await database.list_jobs(limit=limit, offset=offset)
    job_ids = [str(j.id) for j in jobs]
    task_statuses_by_job = await database.get_task_statuses_for_jobs(job_ids)
    LOGGER.info("jobs_listed", extra={"event": "jobs_listed", "limit": limit, "offset": offset})
    return [
        JobSummaryResponse.from_record(job, task_statuses_by_job.get(str(job.id), {}))
        for job in jobs
    ]
