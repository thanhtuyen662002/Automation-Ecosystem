from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from api.dependencies import DatabaseDependency, WorkflowManagerDependency
from api.schemas import DispatchRequest, DispatchResponse, SystemStatsResponse
from workers.handlers.tiktok.download_videos import get_ytdlp_impersonation_dependency_warning


LOGGER = logging.getLogger("api.system")
router = APIRouter(prefix="/system", tags=["system"])


def _deep_health_payload(
    *,
    db_ok: bool,
    db_error: str | None,
    scheduler_running: bool,
    worker_running: bool,
) -> dict[str, object]:
    can_execute_tasks = worker_running
    healthy = db_ok and scheduler_running and can_execute_tasks
    warnings = []
    if warning := get_ytdlp_impersonation_dependency_warning():
        warnings.append(warning)
    return {
        "status": "ok" if healthy else "degraded",
        "database": {"ok": db_ok, "error": db_error},
        "scheduler": {"running": scheduler_running},
        "worker": {"running": worker_running},
        "execution": {
            "can_execute_tasks": can_execute_tasks,
            "worker_required": True,
            "mode": "all_in_one" if worker_running else "api_only_or_worker_missing",
        },
        "warnings": warnings,
    }


@router.post("/dispatch", response_model=DispatchResponse)
async def dispatch_tasks(
    request: DispatchRequest,
    workflow_manager: WorkflowManagerDependency,
) -> DispatchResponse:
    promoted = await workflow_manager.promote_tasks_to_ready(limit=request.limit)
    dispatched = await workflow_manager.dispatch_tasks(
        limit=request.limit,
        max_concurrent_per_worker=request.max_concurrent_per_worker,
        max_per_task_type=request.max_per_task_type,
        max_per_account=request.max_per_account,
        acquire_without_queue=False,
    )
    LOGGER.info(
        "system_dispatch_triggered",
        extra={
            "event": "system_dispatch_triggered",
            "promoted": len(promoted),
            "dispatched": len(dispatched.acquired),
            "throttled": len(dispatched.throttled_task_ids),
            "skipped": len(dispatched.skipped_task_ids),
        },
    )
    return DispatchResponse(
        promoted=len(promoted),
        dispatched=len(dispatched.acquired),
        throttled=len(dispatched.throttled_task_ids),
        skipped=len(dispatched.skipped_task_ids),
    )


@router.get("/stats", response_model=SystemStatsResponse)
async def get_system_stats(database: DatabaseDependency) -> SystemStatsResponse:
    stats = await database.get_system_stats()
    LOGGER.info("system_stats_read", extra={"event": "system_stats_read"})
    return SystemStatsResponse.from_record(stats)


@router.get("/health/deep")
async def get_deep_health(request: Request, database: DatabaseDependency) -> dict[str, object]:
    db_ok = False
    db_error = None
    try:
        db_ok = await database.ping()
    except Exception as exc:
        db_error = str(exc)

    scheduler = getattr(request.app.state, "scheduler", None)
    worker_runtime = getattr(request.app.state, "worker_runtime", None)
    scheduler_running = bool(getattr(scheduler, "is_running", False))
    worker_running = bool(getattr(worker_runtime, "is_running", False))
    payload = _deep_health_payload(
        db_ok=db_ok,
        db_error=db_error,
        scheduler_running=scheduler_running,
        worker_running=worker_running,
    )
    can_execute_tasks = worker_running

    LOGGER.info(
        "system_deep_health_read",
        extra={
            "event": "system_deep_health_read",
            "db_ok": db_ok,
            "scheduler_running": scheduler_running,
            "worker_running": worker_running,
            "can_execute_tasks": can_execute_tasks,
            "warning_count": len(payload.get("warnings", [])),
        },
    )
    return payload
