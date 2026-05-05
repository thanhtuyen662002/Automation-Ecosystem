from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn
from pythonjsonlogger import jsonlogger

from api.main import app
from core.scheduler import AutoDispatchScheduler, SchedulerSettings
from core.workflow_manager import WorkflowManager
from database.database import AutomationDatabase, RetryConfig
from workers.handlers import register_default_handlers
from workers.worker_runtime import WorkerRuntime, WorkerRuntimeSettings


LOGGER = logging.getLogger("automation.backend")


@dataclass(frozen=True)
class BackendSettings:
    host: str
    port: int
    env_path: Path
    log_path: Path

    @classmethod
    def load(cls) -> "BackendSettings":
        base_dir = _base_dir()
        env_path = Path(os.getenv("AE_ENV_FILE", base_dir / ".env.production"))
        _ensure_env_file(env_path)
        _load_env(env_path)
        log_path = Path(os.getenv("AE_LOG_FILE", base_dir / "logs" / "app.log"))
        return cls(
            host=os.getenv("API_HOST", "127.0.0.1"),
            port=_int_env("APP_PORT", _int_env("API_PORT", 8000, 1), 1),
            env_path=env_path,
            log_path=log_path,
        )


async def main() -> None:
    settings = BackendSettings.load()
    configure_logging(settings.log_path)
    os.environ["API_HOST"] = settings.host
    os.environ["API_PORT"] = str(settings.port)
    os.environ["API_SCHEDULER_ENABLED"] = "false"
    worker_settings = WorkerRuntimeSettings.from_env(settings.env_path)
    scheduler_settings = SchedulerSettings.from_env()

    LOGGER.info(
        "backend_starting",
        extra={"event": "backend_starting", "host": settings.host, "port": settings.port},
    )

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
        WorkflowManager(scheduler_database, worker_id=os.getenv("SCHEDULER_WORKER_ID", "desktop-scheduler")),
        scheduler_settings,
    )
    app.state.scheduler = scheduler

    uvicorn_config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        loop="asyncio",
        log_config=None,
        access_log=False,
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
        LOGGER.info("backend_stopping", extra={"event": "backend_stopping"})
        server.should_exit = True
        worker_runtime.request_stop()
        await scheduler.stop()
        await _await_safely(worker_task)
        await _await_safely(server_task)
        await scheduler_database.close()
        LOGGER.info("backend_stopped", extra={"event": "backend_stopped"})


def configure_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(message)s %(event)s %(error)s %(error_type)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logging.getLogger().handlers.clear()
    logging.getLogger().addHandler(file_handler)
    logging.getLogger().addHandler(stream_handler)
    logging.getLogger().setLevel(os.getenv("WORKER_LOG_LEVEL", "INFO").upper())


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _ensure_env_file(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/automation",
                "WORKER_ID=desktop-worker-1",
                "WORKER_MAX_CONCURRENCY=4",
                "WORKER_BATCH_SIZE=10",
                "WORKER_POLL_INTERVAL_SECONDS=2",
                "HEARTBEAT_INTERVAL=30",
                "TASK_TIMEOUT=300",
                "WORKER_LEASE_SECONDS=300",
                "WORKER_RETRY_BASE_DELAY_SECONDS=5",
                "WORKER_RETRY_MAX_DELAY_SECONDS=300",
                "WORKER_LOG_LEVEL=INFO",
                "SCHEDULER_INTERVAL_SECONDS=5",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _load_env(path: Path) -> None:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), _strip_quotes(value.strip()))


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


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
    except Exception as exc:
        LOGGER.exception(
            "background_task_failed",
            extra={"event": "background_task_failed", "error": str(exc), "error_type": type(exc).__name__},
        )


def _int_env(key: str, default: int, minimum: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{key} must be an integer") from exc
    if parsed < minimum:
        raise RuntimeError(f"{key} must be >= {minimum}")
    return parsed


if __name__ == "__main__":
    asyncio.run(main())
