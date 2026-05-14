from __future__ import annotations

import logging
import sys
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import sqlite3
from pythonjsonlogger import jsonlogger

from api.dependencies import ApiSettings
from api.routes import jobs, system, tasks, tiktok, analytics, accounts, artifacts, policy_rules, account_brain, identity, fleet_health, content_brain, ws, strategy, auth, decisions
from api.routes import license as license_routes
from api.middleware.license_guard import LicenseGuard
from api.services.license_service import LicenseService
from core.scheduler import AutoDispatchScheduler, SchedulerSettings
from core.workflow_manager import WorkflowManager
from database.database import (
    AutomationDatabase,
    ConflictError,
    DatabaseError,
    InvalidStateTransition,
    NotFoundError,
    ValidationError,
)


LOGGER = logging.getLogger("api")


# ── Idempotent auto-migrations ────────────────────────────────────────────────
# Each entry: (table, column_name, column_definition)
# Only ALTER TABLE is issued if the column is missing — safe on every startup.
_PENDING_MIGRATIONS: list[tuple[str, str, str]] = [
    ("tasks", "task_key", "TEXT NOT NULL DEFAULT ''"),
    ("accounts", "avatar_url", "TEXT"),
    ("accounts", "display_name", "TEXT"),
    ("accounts", "profile_url", "TEXT"),
    ("accounts", "external_user_id", "TEXT"),
]


async def _run_auto_migrations(database: "AutomationDatabase") -> None:
    """Apply any missing column migrations at startup (idempotent)."""
    import aiosqlite as _aiosqlite

    db_path = database._db_path
    async with _aiosqlite.connect(db_path) as conn:
        conn.row_factory = _aiosqlite.Row
        for table, column, col_def in _PENDING_MIGRATIONS:
            cur = await conn.execute(f"PRAGMA table_info({table})")
            rows = await cur.fetchall()
            existing = {r["name"] for r in rows}
            if column not in existing:
                LOGGER.warning(
                    "auto_migration_apply",
                    extra={
                        "event": "auto_migration_apply",
                        "table": table,
                        "column": column,
                    },
                )
                await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
                await conn.commit()
                LOGGER.info(
                    "auto_migration_done",
                    extra={"event": "auto_migration_done", "table": table, "column": column},
                )
        for stmt in (
            "CREATE INDEX IF NOT EXISTS accounts_profile_url_idx ON accounts (profile_url) WHERE profile_url IS NOT NULL",
            "CREATE UNIQUE INDEX IF NOT EXISTS artifacts_storage_uri_uidx ON artifacts (storage_uri)",
        ):
            try:
                await conn.execute(stmt)
                await conn.commit()
            except Exception as exc:
                LOGGER.warning(
                    "auto_migration_index_failed",
                    extra={"event": "auto_migration_index_failed", "statement": stmt, "error": str(exc)},
                )


def configure_json_logging(level: str = "INFO") -> None:
    logging.getLogger().handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(message)s %(event)s %(method)s %(path)s %(status_code)s "
        "%(duration_ms)s %(error)s %(error_type)s"
    )
    handler.setFormatter(formatter)
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_json_logging()
    settings = ApiSettings.from_env()
    database = AutomationDatabase(settings.database_url)
    await database.open()
    # ── Auto-migration: ensure schema is up-to-date on every start ───────────
    await _run_auto_migrations(database)
    scheduler = None
    if settings.scheduler_enabled:
        scheduler = AutoDispatchScheduler(
            WorkflowManager(database=database, worker_id=settings.dispatcher_worker_id),
            SchedulerSettings.from_env(),
        )
        scheduler.start()
    app.state.settings = settings
    app.state.database = database
    app.state.license_service = LicenseService.from_env()
    if scheduler is not None:
        app.state.scheduler = scheduler
    LOGGER.info("api_started", extra={"event": "api_started"})
    try:
        yield
    finally:
        if scheduler is not None:
            await scheduler.stop()
        await database.close()
        LOGGER.info("api_stopped", extra={"event": "api_stopped"})


app = FastAPI(title="Automation Ecosystem API", version="0.1.0", lifespan=lifespan)

# License guard: validates local activation state through trusted license-api.
app.add_middleware(LicenseGuard)

# CORS: restrict to known local origins only.
# In production (Electron), renderer talks to 127.0.0.1 on a fixed port.
# Wildcard regex removed — it allowed ANY localhost port.
_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Admin-Secret"],
    expose_headers=["Retry-After"],
)
app.include_router(jobs.router)
app.include_router(tasks.router)
app.include_router(system.router)
app.include_router(tiktok.router)
app.include_router(analytics.router, prefix="/api/v1")
app.include_router(accounts.router, prefix="/api/v1")
app.include_router(artifacts.router, prefix="/api/v1")
app.include_router(policy_rules.router, prefix="/api/v1")
app.include_router(account_brain.router, prefix="/api/v1")
app.include_router(identity.router, prefix="/api/v1")
app.include_router(fleet_health.router, prefix="/api/v1")
app.include_router(content_brain.router, prefix="/api/v1")
app.include_router(ws.router, prefix="/api/v1")
app.include_router(strategy.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(decisions.router, prefix="/api/v1")
app.include_router(license_routes.router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    started_at = time.monotonic()
    try:
        response = await call_next(request)
    except Exception as exc:
        LOGGER.exception(
            "request_failed",
            extra={
                "event": "request_failed",
                "method": request.method,
                "path": request.url.path,
                "duration_ms": int((time.monotonic() - started_at) * 1000),
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
        )
        raise
    LOGGER.info(
        "request_completed",
        extra={
            "event": "request_completed",
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": int((time.monotonic() - started_at) * 1000),
        },
    )
    return response


@app.exception_handler(NotFoundError)
async def not_found_handler(_request: Request, exc: NotFoundError) -> JSONResponse:
    return _safe_error(404, "NotFound", "Resource not found.")


@app.exception_handler(ConflictError)
async def conflict_handler(_request: Request, exc: ConflictError) -> JSONResponse:
    return _safe_error(409, "Conflict", "A conflict occurred with existing data.")


@app.exception_handler(InvalidStateTransition)
async def invalid_transition_handler(_request: Request, exc: InvalidStateTransition) -> JSONResponse:
    return _safe_error(400, "InvalidStateTransition", "Invalid state transition.")


@app.exception_handler(ValidationError)
async def validation_error_handler(_request: Request, exc: ValidationError) -> JSONResponse:
    return _safe_error(400, "ValidationError", str(exc))


@app.exception_handler(sqlite3.IntegrityError)
async def unique_violation_handler(_request: Request, exc: sqlite3.IntegrityError) -> JSONResponse:
    return _safe_error(409, "UniqueViolation", "A record with the same unique key already exists.")


@app.exception_handler(DatabaseError)
async def database_error_handler(_request: Request, exc: DatabaseError) -> JSONResponse:
    return _safe_error(400, "DatabaseError", "A database error occurred.")


@app.exception_handler(Exception)
async def generic_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    # Never expose internal stacktraces to clients
    LOGGER.exception("unhandled_exception", extra={"event": "unhandled_exception", "error": str(exc)})
    return _safe_error(500, "InternalServerError", "An internal error occurred.")


def _safe_error(status_code: int, error: str, message: str) -> JSONResponse:
    """Return a sanitized error response — no stacktraces, no internal paths."""
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "message": message},
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok"}
