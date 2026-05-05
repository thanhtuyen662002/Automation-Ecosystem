from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

from core.workflow_manager import WorkflowManager


LOGGER = logging.getLogger("core.scheduler")


@dataclass(frozen=True)
class SchedulerSettings:
    interval_seconds: float = 5.0
    dispatch_limit: int = 100
    max_concurrent_per_worker: int | None = None
    max_per_task_type: int | None = None
    max_per_account: int | None = None

    @classmethod
    def from_env(cls) -> "SchedulerSettings":
        return cls(
            interval_seconds=_float_env("SCHEDULER_INTERVAL_SECONDS", 5.0, 0.1),
            dispatch_limit=_int_env("SCHEDULER_DISPATCH_LIMIT", 100, 1),
            max_concurrent_per_worker=_optional_int_env("SCHEDULER_MAX_CONCURRENT_PER_WORKER", 1),
            max_per_task_type=_optional_int_env("SCHEDULER_MAX_PER_TASK_TYPE", 1),
            max_per_account=_optional_int_env("SCHEDULER_MAX_PER_ACCOUNT", 1),
        )


class AutoDispatchScheduler:
    def __init__(
        self,
        workflow_manager: WorkflowManager,
        settings: SchedulerSettings | None = None,
    ) -> None:
        self._workflow_manager = workflow_manager
        self._settings = settings or SchedulerSettings()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self.run(), name="auto-dispatch-scheduler")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is None:
            return
        try:
            await asyncio.wait_for(self._task, timeout=max(self._settings.interval_seconds + 1, 2))
        except asyncio.TimeoutError:
            self._task.cancel()
            await _await_cancelled(self._task)

    async def run(self) -> None:
        self._running = True
        LOGGER.info(
            "scheduler started",
            extra={"event": "scheduler_started", "interval_seconds": self._settings.interval_seconds},
        )
        try:
            while not self._stop_event.is_set():
                await self.run_once()
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._settings.interval_seconds,
                    )
                except asyncio.TimeoutError:
                    continue
        finally:
            self._running = False
            LOGGER.info("scheduler stopped", extra={"event": "scheduler_stopped"})

    async def run_once(self) -> None:
        try:
            promoted = await self._workflow_manager.promote_tasks_to_ready(limit=self._settings.dispatch_limit)
            dispatched = await self._workflow_manager.dispatch_tasks(
                limit=self._settings.dispatch_limit,
                max_concurrent_per_worker=self._settings.max_concurrent_per_worker,
                max_per_task_type=self._settings.max_per_task_type,
                max_per_account=self._settings.max_per_account,
                acquire_without_queue=False,
            )
            LOGGER.info(
                "scheduler tick completed",
                extra={
                    "event": "scheduler_tick_completed",
                    "promoted": len(promoted),
                    "dispatched": len(dispatched.acquired),
                    "throttled": len(dispatched.throttled_task_ids),
                    "skipped": len(dispatched.skipped_task_ids),
                },
            )
        except Exception as exc:
            LOGGER.exception(
                "scheduler tick failed",
                extra={
                    "event": "scheduler_tick_failed",
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )


async def _await_cancelled(task: asyncio.Task[None]) -> None:
    try:
        await task
    except asyncio.CancelledError:
        return


def _int_env(key: str, default: int, minimum: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{key} must be an integer") from exc
    if value < minimum:
        raise RuntimeError(f"{key} must be >= {minimum}")
    return value


def _optional_int_env(key: str, minimum: int) -> int | None:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{key} must be an integer") from exc
    if value < minimum:
        raise RuntimeError(f"{key} must be >= {minimum}")
    return value


def _float_env(key: str, default: float, minimum: float) -> float:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{key} must be a number") from exc
    if value < minimum:
        raise RuntimeError(f"{key} must be >= {minimum}")
    return value
