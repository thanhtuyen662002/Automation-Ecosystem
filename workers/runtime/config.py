from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


DEFAULT_MAX_BROWSER_WORKERS = 1
DEFAULT_MAX_MEDIA_WORKERS = 2
DEFAULT_MAX_AI_WORKERS = 4
DEFAULT_HEARTBEAT_INTERVAL = 30
DEFAULT_TASK_TIMEOUT = 300
DEFAULT_MAX_RETRIES = 3


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkerRuntimeConfig:
    database_url: str
    redis_url: str
    worker_id: str
    max_browser_workers: int = DEFAULT_MAX_BROWSER_WORKERS
    max_media_workers: int = DEFAULT_MAX_MEDIA_WORKERS
    max_ai_workers: int = DEFAULT_MAX_AI_WORKERS
    heartbeat_interval: int = DEFAULT_HEARTBEAT_INTERVAL
    task_timeout: int = DEFAULT_TASK_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    stream_name: str = "worker:tasks"
    consumer_group: str = "worker-runtime"
    read_block_ms: int = 5000
    retry_base_delay_seconds: int = 5
    retry_max_delay_seconds: int = 300
    default_pool: str = "ai"
    task_type_pools: dict[str, str] | None = None
    log_level: str = "INFO"

    @property
    def total_concurrency(self) -> int:
        return self.max_browser_workers + self.max_media_workers + self.max_ai_workers


def load_config(env_file: str | Path = ".env") -> WorkerRuntimeConfig:
    env = _load_env(env_file)
    return WorkerRuntimeConfig(
        database_url=_required(env, "DATABASE_URL"),
        redis_url=_required(env, "REDIS_URL"),
        worker_id=_first_present(env, ("WORKER_ID", "ENGINE_WORKER_ID"), required=True),
        max_browser_workers=_int_value(
            env, ("MAX_BROWSER_WORKERS", "WORKER_BROWSER_CONCURRENCY"), DEFAULT_MAX_BROWSER_WORKERS, 1
        ),
        max_media_workers=_int_value(
            env, ("MAX_MEDIA_WORKERS", "WORKER_MEDIA_CONCURRENCY"), DEFAULT_MAX_MEDIA_WORKERS, 1
        ),
        max_ai_workers=_int_value(
            env, ("MAX_AI_WORKERS", "WORKER_AI_CONCURRENCY"), DEFAULT_MAX_AI_WORKERS, 1
        ),
        heartbeat_interval=_int_value(
            env, ("HEARTBEAT_INTERVAL", "WORKER_HEARTBEAT_INTERVAL_SECONDS"), DEFAULT_HEARTBEAT_INTERVAL, 1
        ),
        task_timeout=_int_value(
            env, ("TASK_TIMEOUT", "WORKER_HEARTBEAT_TIMEOUT_SECONDS"), DEFAULT_TASK_TIMEOUT, 1
        ),
        max_retries=_int_value(env, ("MAX_RETRIES", "WORKER_MAX_RETRIES"), DEFAULT_MAX_RETRIES, 0),
        stream_name=_first_present(env, ("WORKER_REDIS_STREAM",), default="worker:tasks"),
        consumer_group=_first_present(env, ("WORKER_REDIS_GROUP",), default="worker-runtime"),
        read_block_ms=_int_value(env, ("WORKER_READ_BLOCK_MS",), 5000, 100),
        retry_base_delay_seconds=_int_value(env, ("WORKER_RETRY_BASE_DELAY_SECONDS",), 5, 0),
        retry_max_delay_seconds=_int_value(env, ("WORKER_RETRY_MAX_DELAY_SECONDS",), 300, 0),
        default_pool=_first_present(env, ("WORKER_DEFAULT_POOL",), default="ai"),
        task_type_pools=_json_map(env, "WORKER_TASK_TYPE_POOLS"),
        log_level=_first_present(env, ("WORKER_LOG_LEVEL",), default="INFO").upper(),
    )


def _load_env(env_file: str | Path) -> dict[str, str]:
    merged = dict(os.environ)
    path = Path(env_file)
    if not path.exists():
        return merged
    if not path.is_file():
        raise ConfigError(f".env path is not a file: {path}")
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if line == "" or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            raise ConfigError(f"Invalid .env line {line_number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        if key == "":
            raise ConfigError(f"Invalid .env line {line_number}: empty key")
        merged[key] = _strip_quotes(value.strip())
    return merged


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _required(env: Mapping[str, str], key: str) -> str:
    value = env.get(key)
    if value is None or value.strip() == "":
        raise ConfigError(f"Missing required environment variable: {key}")
    return value.strip()


def _first_present(
    env: Mapping[str, str],
    keys: tuple[str, ...],
    default: str | None = None,
    required: bool = False,
) -> str:
    for key in keys:
        value = env.get(key)
        if value is not None and value.strip() != "":
            return value.strip()
    if required:
        raise ConfigError(f"Missing required environment variable: {' or '.join(keys)}")
    if default is None:
        raise ConfigError(f"Missing required environment variable: {' or '.join(keys)}")
    return default


def _int_value(env: Mapping[str, str], keys: tuple[str, ...], default: int, minimum: int) -> int:
    raw = None
    used_key = keys[0]
    for key in keys:
        value = env.get(key)
        if value is not None and value.strip() != "":
            raw = value.strip()
            used_key = key
            break
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{used_key} must be an integer") from exc
    if parsed < minimum:
        raise ConfigError(f"{used_key} must be >= {minimum}")
    return parsed


def _json_map(env: Mapping[str, str], key: str) -> dict[str, str] | None:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return None
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{key} must be valid JSON") from exc
    if not isinstance(decoded, dict):
        raise ConfigError(f"{key} must be a JSON object")
    return {str(map_key): str(map_value) for map_key, map_value in decoded.items()}
