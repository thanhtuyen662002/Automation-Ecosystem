from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from core.workflow_manager import WorkflowManager
from database.database import AutomationDatabase


@dataclass(frozen=True)
class ApiSettings:
    database_url: str
    dispatcher_worker_id: str = "api-dispatcher"
    scheduler_enabled: bool = True

    @classmethod
    def from_env(cls) -> "ApiSettings":
        database_url = os.getenv("DATABASE_URL", "").strip()
        if not database_url:
            raise RuntimeError("Missing required environment variable: DATABASE_URL")
        dispatcher_worker_id = os.getenv("API_DISPATCHER_WORKER_ID", "api-dispatcher").strip()
        if not dispatcher_worker_id:
            raise RuntimeError("API_DISPATCHER_WORKER_ID cannot be empty")
        scheduler_enabled = os.getenv("API_SCHEDULER_ENABLED", "true").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        return cls(
            database_url=database_url,
            dispatcher_worker_id=dispatcher_worker_id,
            scheduler_enabled=scheduler_enabled,
        )


def get_database(request: Request) -> AutomationDatabase:
    database = getattr(request.app.state, "database", None)
    if not isinstance(database, AutomationDatabase):
        raise RuntimeError("Database has not been initialized")
    return database


def get_workflow_manager(
    request: Request,
    database: Annotated[AutomationDatabase, Depends(get_database)],
) -> WorkflowManager:
    settings = getattr(request.app.state, "settings", None)
    if not isinstance(settings, ApiSettings):
        raise RuntimeError("API settings have not been initialized")
    return WorkflowManager(database=database, worker_id=settings.dispatcher_worker_id)


DatabaseDependency = Annotated[AutomationDatabase, Depends(get_database)]
WorkflowManagerDependency = Annotated[WorkflowManager, Depends(get_workflow_manager)]
