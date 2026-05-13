from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path

# NOTE: WindowsSelectorEventLoopPolicy intentionally NOT set here.
# Playwright on Windows requires ProactorEventLoop (the asyncio default on Windows)
# for browser subprocess IPC. Setting WindowsSelectorEventLoopPolicy causes
# Playwright's launch_persistent_context to fail silently with an empty exception.

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
        app_data_dir = _get_appdata_dir()
        env_path = Path(os.getenv("AE_ENV_FILE", app_data_dir / ".env.production"))
        _ensure_env_file(env_path)
        _load_env(env_path, app_data_dir)
        
        db_path = os.getenv("DATABASE_PATH")
        if not db_path:
            db_path = str(app_data_dir / "data" / "app.db")
        
        # Ensure directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Resolve DATABASE_URL:
        current_db_url = os.environ.get("DATABASE_URL", "")
        if not current_db_url or "postgresql" in current_db_url:
            # Standard format: sqlite+aiosqlite:///C:/path/to/db
            normalized_db_path = db_path.replace("\\", "/")
            os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{normalized_db_path}"
        elif "AppData" in current_db_url or ".automation-ecosystem" in current_db_url or "{APP_DATA_DIR}" in current_db_url:
            # Ensure it uses forward slashes for the URL part
            current_db_url = current_db_url.replace("\\", "/")
            # If the path doesn't exist and it's a default path, re-resolve to current app_data_dir
            path_part = current_db_url.replace("sqlite+aiosqlite:///", "").replace("sqlite+aiosqlite://", "")
            if not Path(path_part).exists() and ("Automation-Ecosystem" in path_part):
                normalized_db_path = db_path.replace("\\", "/")
                os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{normalized_db_path}"
            else:
                os.environ["DATABASE_URL"] = current_db_url

        log_path = Path(os.getenv("AE_LOG_FILE", app_data_dir / "logs" / "app.log"))
        return cls(
            host=os.getenv("API_HOST", "127.0.0.1"),
            port=_int_env("APP_PORT", _int_env("API_PORT", 8000, 1), 1),
            env_path=env_path,
            log_path=log_path,
        )


async def main() -> None:
    settings = BackendSettings.load()
    _configure_playwright_runtime()
    print(f"DEBUG: DATABASE_URL={os.environ.get('DATABASE_URL')}")
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
    
    schema_path = _base_dir() / "database" / "schema.sql"
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        schema_path = Path(sys._MEIPASS) / "database" / "schema.sql"
    if schema_path.exists():
        async with scheduler_database.connection() as conn:
            cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'")
            if not await cursor.fetchone():
                await scheduler_database.init_schema(str(schema_path))
    else:
        LOGGER.warning("schema_not_found", extra={"path": str(schema_path)})

    # ── Auto-apply pending migrations ────────────────────────────────────────
    await _run_migrations(scheduler_database)

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


async def _run_migrations(database: AutomationDatabase) -> None:
    """
    Apply any pending SQL migrations from database/migrations/*.sql.
    
    Uses a per-statement try/except so that already-applied ALTER TABLE
    statements are skipped silently (SQLite raises OperationalError for
    duplicate columns, not an error we want to crash on).
    """
    migrations_dir = _base_dir() / "database" / "migrations"
    if not migrations_dir.exists():
        return

    migration_files = sorted(migrations_dir.glob("*.sql"))
    for migration_file in migration_files:
        LOGGER.info(
            "migration_apply",
            extra={"event": "migration_apply", "file": migration_file.name},
        )
        sql = migration_file.read_text(encoding="utf-8")
        statements: list[str] = []
        for raw_stmt in sql.split(";"):
            lines = [
                line
                for line in raw_stmt.splitlines()
                if line.strip() and not line.strip().startswith("--")
            ]
            stmt = "\n".join(lines).strip()
            if stmt:
                statements.append(stmt)
        async with database.connection() as conn:
            for stmt in statements:
                try:
                    await conn.execute(stmt)
                except Exception as exc:
                    # "duplicate column name" is expected for already-applied migrations
                    if "duplicate column" in str(exc).lower():
                        LOGGER.debug(
                            "migration_skip_existing_column",
                            extra={"event": "migration_skip_existing_column", "stmt": stmt[:80]},
                        )
                    else:
                        LOGGER.warning(
                            "migration_stmt_error",
                            extra={
                                "event": "migration_stmt_error",
                                "file": migration_file.name,
                                "stmt": stmt[:80],
                                "error": str(exc),
                            },
                        )


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


def _configure_playwright_runtime() -> None:
    if not getattr(sys, "frozen", False) or not hasattr(sys, "_MEIPASS"):
        return
    bundled_browsers = Path(sys._MEIPASS) / "ms-playwright"
    if bundled_browsers.exists():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(bundled_browsers))


def _get_appdata_dir() -> Path:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Automation-Ecosystem"
    return Path.home() / ".automation-ecosystem"


def _ensure_env_file(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "DATABASE_URL=sqlite+aiosqlite:///{APP_DATA_DIR}/data/app.db",
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


def _load_env(path: Path, app_data_dir: Path) -> None:
    """Load env file and resolve placeholders like {APP_DATA_DIR}."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        # Resolve placeholder for portability
        resolved_value = _strip_quotes(value.strip()).replace("{APP_DATA_DIR}", str(app_data_dir).replace("\\", "/"))
        os.environ[key.strip()] = resolved_value


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
