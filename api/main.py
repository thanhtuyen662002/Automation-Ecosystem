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
    scheduler = None
    if settings.scheduler_enabled:
        scheduler = AutoDispatchScheduler(
            WorkflowManager(database=database, worker_id=settings.dispatcher_worker_id),
            SchedulerSettings.from_env(),
        )
        scheduler.start()
    app.state.settings = settings
    app.state.database = database
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

# Allow Vite dev-server and Electron renderer to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_origin_regex=r"https?://localhost:\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(jobs.router)
app.include_router(tasks.router)
app.include_router(system.router)
app.include_router(tiktok.router)
app.include_router(analytics.router)
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
    return _error_response(404, exc)


@app.exception_handler(ConflictError)
async def conflict_handler(_request: Request, exc: ConflictError) -> JSONResponse:
    return _error_response(409, exc)


@app.exception_handler(InvalidStateTransition)
async def invalid_transition_handler(_request: Request, exc: InvalidStateTransition) -> JSONResponse:
    return _error_response(400, exc)


@app.exception_handler(ValidationError)
async def validation_error_handler(_request: Request, exc: ValidationError) -> JSONResponse:
    return _error_response(400, exc)


@app.exception_handler(sqlite3.IntegrityError)
async def unique_violation_handler(_request: Request, exc: sqlite3.IntegrityError) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "error": "UniqueViolation",
            "message": "A record with the same unique key already exists",
        },
    )


@app.exception_handler(DatabaseError)
async def database_error_handler(_request: Request, exc: DatabaseError) -> JSONResponse:
    return _error_response(400, exc)


def _error_response(status_code: int, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": type(exc).__name__,
            "message": str(exc),
        },
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok"}
