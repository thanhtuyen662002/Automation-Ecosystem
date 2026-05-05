from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


class ConfigError(ValueError):
    pass


def _require(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if value is None or value.strip() == "":
        raise ConfigError(f"Missing required environment variable: {name}")
    return value.strip()


def _int_env(env: Mapping[str, str], name: str, default: int, minimum: int) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    return value


def _float_env(env: Mapping[str, str], name: str, default: float, minimum: float) -> float:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    return value


@dataclass(frozen=True)
class EngineSettings:
    database_url: str
    redis_url: str
    worker_id: str
    stream_name: str = "automation:jobs"
    consumer_group: str = "automation-workers"
    read_block_ms: int = 5_000
    read_count: int = 1
    heartbeat_interval_seconds: float = 5.0
    lease_timeout_seconds: int = 60
    default_job_timeout_seconds: int = 300
    max_attempts: int = 5
    retry_base_delay_seconds: int = 5
    retry_max_delay_seconds: int = 300
    postgres_pool_min_size: int = 1
    postgres_pool_max_size: int = 5
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "EngineSettings":
        source = os.environ if env is None else env
        log_level = source.get("ENGINE_LOG_LEVEL", "INFO").upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigError("ENGINE_LOG_LEVEL must be a valid Python logging level")
        min_pool = _int_env(source, "ENGINE_POSTGRES_POOL_MIN_SIZE", 1, 1)
        max_pool = _int_env(source, "ENGINE_POSTGRES_POOL_MAX_SIZE", 5, min_pool)
        return cls(
            database_url=_require(source, "DATABASE_URL"),
            redis_url=_require(source, "REDIS_URL"),
            worker_id=_require(source, "ENGINE_WORKER_ID"),
            stream_name=source.get("ENGINE_STREAM_NAME", "automation:jobs"),
            consumer_group=source.get("ENGINE_CONSUMER_GROUP", "automation-workers"),
            read_block_ms=_int_env(source, "ENGINE_READ_BLOCK_MS", 5_000, 100),
            read_count=_int_env(source, "ENGINE_READ_COUNT", 1, 1),
            heartbeat_interval_seconds=_float_env(
                source, "ENGINE_HEARTBEAT_INTERVAL_SECONDS", 5.0, 0.1
            ),
            lease_timeout_seconds=_int_env(source, "ENGINE_LEASE_TIMEOUT_SECONDS", 60, 1),
            default_job_timeout_seconds=_int_env(source, "ENGINE_DEFAULT_JOB_TIMEOUT_SECONDS", 300, 1),
            max_attempts=_int_env(source, "ENGINE_MAX_ATTEMPTS", 5, 1),
            retry_base_delay_seconds=_int_env(source, "ENGINE_RETRY_BASE_DELAY_SECONDS", 5, 0),
            retry_max_delay_seconds=_int_env(source, "ENGINE_RETRY_MAX_DELAY_SECONDS", 300, 0),
            postgres_pool_min_size=min_pool,
            postgres_pool_max_size=max_pool,
            log_level=log_level,
        )

