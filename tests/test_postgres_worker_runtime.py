from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest

from workers.worker_runtime import (
    TaskRegistry,
    UnknownTaskTypeError,
    WorkerRuntimeSettings,
    configure_json_logging,
)
from workers.handlers import register_default_handlers


def test_worker_runtime_settings_load_from_database_only_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("WORKER_ID", raising=False)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "DATABASE_URL=postgresql://user:pass@localhost/db",
                "WORKER_ID=worker-1",
                "WORKER_MAX_CONCURRENCY=3",
                "WORKER_BATCH_SIZE=7",
                "HEARTBEAT_INTERVAL=11",
                "TASK_TIMEOUT=301",
            ]
        ),
        encoding="utf-8",
    )

    settings = WorkerRuntimeSettings.from_env()

    assert settings.database_url == "postgresql://user:pass@localhost/db"
    assert settings.worker_id == "worker-1"
    assert settings.max_concurrency == 3
    assert settings.batch_size == 7
    assert settings.heartbeat_interval_seconds == 11
    assert settings.task_timeout_seconds == 301


def test_task_registry_runs_registered_default_handler() -> None:
    registry = TaskRegistry()
    register_default_handlers(registry)

    handler = registry.get("ai")
    result = asyncio.run(handler({"prompt": "hello"}))

    assert result["handler"] == "ai"
    assert result["ok"] is True
    assert "text" in result


def test_task_registry_rejects_unknown_task_type() -> None:
    registry = TaskRegistry()

    with pytest.raises(UnknownTaskTypeError):
        registry.get("missing")


def test_worker_json_logging_includes_execution_fields(capsys: pytest.CaptureFixture[str]) -> None:
    configure_json_logging("INFO")
    logging.getLogger("workers.worker_runtime").info(
        "task_succeeded",
        extra={
            "event": "task_succeeded",
            "task_id": "task-1",
            "execution_id": "execution-1",
            "worker_id": "worker-1",
            "task_type": "ai",
            "status": "success",
            "duration_ms": 12,
            "error": None,
        },
    )

    record = json.loads(capsys.readouterr().out)

    assert record["task_id"] == "task-1"
    assert record["execution_id"] == "execution-1"
    assert record["worker_id"] == "worker-1"
    assert record["status"] == "success"
