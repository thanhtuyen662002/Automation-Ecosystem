from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass

import uvicorn

from api.main import app
from core.scheduler import AutoDispatchScheduler, SchedulerSettings
from core.workflow_manager import WorkflowManager
from database.database import AutomationDatabase, RetryConfig
from workers.handlers import register_default_handlers
from workers.worker_runtime import WorkerRuntime, WorkerRuntimeSettings


@dataclass(frozen=True)
class RunAllSettings:
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    @classmethod
    def from_env(cls) -> "RunAllSettings":
        return cls(
            api_host=os.getenv("API_HOST", "127.0.0.1"),
            api_port=_int_env("API_PORT", 8000, 1),
        )


async def main() -> None:
    os.environ["API_SCHEDULER_ENABLED"] = "false"
    run_settings = RunAllSettings.from_env()
    worker_settings = WorkerRuntimeSettings.from_env()
    scheduler_settings = SchedulerSettings.from_env()

    worker_runtime = WorkerRuntime(worker_settings)
    register_default_handlers(worker_runtime.registry)
    app.state.worker_runtime = worker_runtime

    scheduler_database = AutomationDatabase(
        worker_settings.database_url,
        lease_seconds=worker_settings.lease_seconds,
        retry_config=RetryConfig(
            base_delay_seconds=worker_settings.retry_base_delay_seconds,
            max_delay_seconds=worker_settings.retry_max_delay_seconds,
        ),
    )
    await scheduler_database.open()
    scheduler = AutoDispatchScheduler(
        WorkflowManager(scheduler_database, worker_id="run-all-scheduler"),
        scheduler_settings,
    )
    app.state.scheduler = scheduler

    uvicorn_config = uvicorn.Config(
        app,
        host=run_settings.api_host,
        port=run_settings.api_port,
        loop="asyncio",
        log_config=None,
    )
    server = uvicorn.Server(uvicorn_config)
    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)

    scheduler.start()
    worker_task = asyncio.create_task(worker_runtime.run(install_signal_handlers=False), name="worker-runtime")
    server_task = asyncio.create_task(server.serve(), name="fastapi-server")

    try:
        await stop_event.wait()
    finally:
        server.should_exit = True
        worker_runtime.request_stop()
        await scheduler.stop()
        await _await_safely(worker_task)
        await _await_safely(server_task)
        await scheduler_database.close()


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except NotImplementedError:
            signal.signal(signum, lambda _signum, _frame: stop_event.set())


async def _await_safely(task: asyncio.Task[object]) -> None:
    try:
        await task
    except asyncio.CancelledError:
        raise


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


if __name__ == "__main__":
    asyncio.run(main())
