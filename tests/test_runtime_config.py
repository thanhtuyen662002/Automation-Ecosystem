from __future__ import annotations

from pathlib import Path

import pytest

from workers.runtime.config import (
    DEFAULT_HEARTBEAT_INTERVAL,
    DEFAULT_MAX_AI_WORKERS,
    DEFAULT_MAX_BROWSER_WORKERS,
    DEFAULT_MAX_MEDIA_WORKERS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TASK_TIMEOUT,
    ConfigError,
    load_config,
)
from workers.runtime.worker_runtime import RuntimeConfig


def write_env(path: Path, content: str) -> Path:
    path.write_text(content.strip() + "\n", encoding="utf-8")
    return path


def test_load_config_uses_requested_env_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("WORKER_ID", raising=False)
    env_file = write_env(
        tmp_path / ".env",
        """
        DATABASE_URL=postgresql://user:pass@localhost/db
        REDIS_URL=redis://localhost:6379/0
        WORKER_ID=worker-1
        MAX_BROWSER_WORKERS=3
        MAX_MEDIA_WORKERS=4
        MAX_AI_WORKERS=5
        HEARTBEAT_INTERVAL=11
        TASK_TIMEOUT=301
        MAX_RETRIES=6
        """,
    )

    config = load_config(env_file)

    assert config.max_browser_workers == 3
    assert config.max_media_workers == 4
    assert config.max_ai_workers == 5
    assert config.heartbeat_interval == 11
    assert config.task_timeout == 301
    assert config.max_retries == 6
    assert config.total_concurrency == 12


def test_load_config_provides_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAX_BROWSER_WORKERS", raising=False)
    monkeypatch.delenv("MAX_MEDIA_WORKERS", raising=False)
    monkeypatch.delenv("MAX_AI_WORKERS", raising=False)
    monkeypatch.delenv("HEARTBEAT_INTERVAL", raising=False)
    monkeypatch.delenv("TASK_TIMEOUT", raising=False)
    monkeypatch.delenv("MAX_RETRIES", raising=False)
    env_file = write_env(
        tmp_path / ".env",
        """
        DATABASE_URL=postgresql://user:pass@localhost/db
        REDIS_URL=redis://localhost:6379/0
        WORKER_ID=worker-1
        """,
    )

    config = load_config(env_file)

    assert config.max_browser_workers == DEFAULT_MAX_BROWSER_WORKERS
    assert config.max_media_workers == DEFAULT_MAX_MEDIA_WORKERS
    assert config.max_ai_workers == DEFAULT_MAX_AI_WORKERS
    assert config.heartbeat_interval == DEFAULT_HEARTBEAT_INTERVAL
    assert config.task_timeout == DEFAULT_TASK_TIMEOUT
    assert config.max_retries == DEFAULT_MAX_RETRIES


def test_load_config_rejects_invalid_integer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAX_BROWSER_WORKERS", raising=False)
    env_file = write_env(
        tmp_path / ".env",
        """
        DATABASE_URL=postgresql://user:pass@localhost/db
        REDIS_URL=redis://localhost:6379/0
        WORKER_ID=worker-1
        MAX_BROWSER_WORKERS=zero
        """,
    )

    with pytest.raises(ConfigError, match="MAX_BROWSER_WORKERS"):
        load_config(env_file)


def test_runtime_config_from_env_uses_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("WORKER_ID", raising=False)
    write_env(
        tmp_path / ".env",
        """
        DATABASE_URL=postgresql://user:pass@localhost/db
        REDIS_URL=redis://localhost:6379/0
        WORKER_ID=worker-1
        MAX_BROWSER_WORKERS=2
        MAX_MEDIA_WORKERS=3
        MAX_AI_WORKERS=4
        HEARTBEAT_INTERVAL=10
        TASK_TIMEOUT=300
        MAX_RETRIES=3
        """,
    )

    config = RuntimeConfig.from_env()

    assert config.browser_concurrency == 2
    assert config.media_concurrency == 3
    assert config.ai_concurrency == 4
    assert config.total_concurrency == 9

