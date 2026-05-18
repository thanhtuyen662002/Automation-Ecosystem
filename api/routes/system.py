from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter, Request

from api.dependencies import DatabaseDependency, WorkflowManagerDependency
from api.schemas import DispatchRequest, DispatchResponse, SystemStatsResponse
from core.runtime_env import env_bool, runtime_dependency_warnings
from workers.handlers.tiktok._base import get_media_output_dir


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
    warnings = runtime_dependency_warnings()
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


async def _mobile_tiktok_status_payload() -> dict[str, object]:
    payload: dict[str, object] = {
        "mobile_fallback_enabled": env_bool("TIKTOK_MOBILE_FALLBACK_ENABLED", default=False),
        "mobile_provider": os.environ.get("TIKTOK_MOBILE_PROVIDER", "adb").strip().lower(),
        "configured_device_id": os.environ.get("TIKTOK_MOBILE_DEVICE_ID", "").strip(),
        "package_name": os.environ.get("TIKTOK_ANDROID_TIKTOK_PACKAGE", "com.zhiliaoapp.musically").strip(),
        "manual_login_required": env_bool("TIKTOK_MOBILE_REQUIRE_MANUAL_LOGIN", default=True),
        "setup_guidance": [],
    }
    try:
        from core.mobile_tiktok_provider import make_mobile_tiktok_provider

        provider = make_mobile_tiktok_provider()
        state = await provider.get_current_state()
        payload.update(state)
        payload["ok"] = bool(state.get("adb_available")) and bool(state.get("device_available")) and bool(state.get("tiktok_app_installed"))
    except Exception as exc:
        payload.update({"ok": False, "error": str(exc)})
    payload["setup_guidance"] = _mobile_setup_guidance(payload)
    return payload


def _mobile_setup_guidance(status: dict[str, object]) -> list[str]:
    guidance: list[str] = []
    if not bool(status.get("adb_available")):
        guidance.append("Install Android Platform Tools and verify `adb devices` works.")
    if not bool(status.get("device_available")):
        guidance.append("Start an Android emulator/device, run `adb devices`, then set `TIKTOK_MOBILE_DEVICE_ID` if multiple devices are listed.")
    if bool(status.get("device_available")) and not bool(status.get("tiktok_app_installed")):
        guidance.append("Install the TikTok app on the emulator/device.")
    if bool(status.get("tiktok_app_installed")) and not bool(status.get("tiktok_app_active")):
        guidance.append("Open TikTok on the emulator/device with the Mobile Provider test button.")
    if bool(status.get("login_required")):
        guidance.append("Log in to TikTok manually on the emulator/device.")
    if bool(status.get("verification_required")):
        guidance.append("Complete TikTok captcha/checkpoint manually; automation will not bypass it.")
    return guidance


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
    mobile_status = await _mobile_tiktok_status_payload()
    payload["mobile_tiktok"] = mobile_status
    payload["warnings"] = runtime_dependency_warnings(mobile_status)
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


@router.get("/mobile/tiktok")
async def get_mobile_tiktok_status() -> dict[str, object]:
    payload = await _mobile_tiktok_status_payload()
    LOGGER.info(
        "system_mobile_tiktok_status_read",
        extra={
            "event": "system_mobile_tiktok_status_read",
            "ok": bool(payload.get("ok")),
            "provider": payload.get("mobile_provider"),
            "device_id": payload.get("device_id") or payload.get("configured_device_id"),
            "tiktok_app_installed": bool(payload.get("tiktok_app_installed")),
        },
    )
    return payload


@router.post("/mobile/tiktok/open")
async def open_mobile_tiktok(url: str = "https://www.tiktok.com/") -> dict[str, object]:
    from core.mobile_tiktok_provider import make_mobile_tiktok_provider

    provider = make_mobile_tiktok_provider()
    result = await provider.open_url(url)
    payload = {
        "ok": result.ok,
        "status": result.status,
        "failure_kind": result.failure_kind,
        "message": result.message,
        "current_package": result.current_package,
        "state": result.state or {},
    }
    LOGGER.info(
        "system_mobile_tiktok_open",
        extra={
            "event": "system_mobile_tiktok_open",
            "url": url,
            "ok": result.ok,
            "failure_kind": result.failure_kind,
        },
    )
    return payload


@router.post("/mobile/tiktok/screenshot")
async def screenshot_mobile_tiktok() -> dict[str, object]:
    from core.mobile_tiktok_provider import make_mobile_tiktok_provider

    output_dir = get_media_output_dir() / "diagnostics" / "mobile_tiktok"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"screenshot_{int(time.time())}.png"
    provider = make_mobile_tiktok_provider()
    screenshot_path: Path | None = await provider.screenshot(output_path)
    ok = screenshot_path is not None
    LOGGER.info(
        "system_mobile_tiktok_screenshot",
        extra={"event": "system_mobile_tiktok_screenshot", "ok": ok, "path": str(screenshot_path or "")},
    )
    return {"ok": ok, "path": str(screenshot_path) if screenshot_path else ""}
