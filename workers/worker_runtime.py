from __future__ import annotations

import asyncio
import inspect
import logging
import os
import signal
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pythonjsonlogger import jsonlogger

from database.database import AcquiredTask, AutomationDatabase, DatabaseError, RetryConfig


LOGGER = logging.getLogger("workers.worker_runtime")
TaskHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]]


@dataclass(frozen=True)
class WorkerRuntimeSettings:
    database_url: str
    worker_id: str
    max_concurrency: int = 4
    batch_size: int = 10
    poll_interval_seconds: float = 2.0
    heartbeat_interval_seconds: float = 30.0
    task_timeout_seconds: float = 300.0
    lease_seconds: int = 300
    retry_base_delay_seconds: int = 5
    retry_max_delay_seconds: int = 300
    max_per_task_type: int | None = None
    max_per_account: int | None = None
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env_file: str | Path = ".env") -> "WorkerRuntimeSettings":
        env = _load_env(env_file)
        return cls(
            database_url=_required(env, "DATABASE_URL"),
            worker_id=_first_present(env, ("WORKER_ID", "ENGINE_WORKER_ID"), required=True),
            max_concurrency=_int_value(env, "WORKER_MAX_CONCURRENCY", 4, 1),
            batch_size=_int_value(env, "WORKER_BATCH_SIZE", 10, 1),
            poll_interval_seconds=_float_value(env, "WORKER_POLL_INTERVAL_SECONDS", 2.0, 0.1),
            heartbeat_interval_seconds=_float_value(env, "HEARTBEAT_INTERVAL", 30.0, 1.0),
            task_timeout_seconds=_float_value(env, "TASK_TIMEOUT", 300.0, 1.0),
            lease_seconds=_int_value(env, "WORKER_LEASE_SECONDS", 300, 1),
            retry_base_delay_seconds=_int_value(env, "WORKER_RETRY_BASE_DELAY_SECONDS", 5, 0),
            retry_max_delay_seconds=_int_value(env, "WORKER_RETRY_MAX_DELAY_SECONDS", 300, 0),
            max_per_task_type=_optional_int_value(env, "WORKER_MAX_PER_TASK_TYPE", 1),
            max_per_account=_optional_int_value(env, "WORKER_MAX_PER_ACCOUNT", 1),
            log_level=_first_present(env, ("WORKER_LOG_LEVEL",), default="INFO").upper(),
        )


class TaskRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, TaskHandler] = {}

    def register(self, task_type: str, handler: TaskHandler) -> None:
        if not task_type.strip():
            raise ValueError("task_type cannot be empty")
        if not callable(handler):
            raise TypeError("handler must be callable")
        self._handlers[task_type] = handler

    def get(self, task_type: str) -> TaskHandler:
        try:
            return self._handlers[task_type]
        except KeyError as exc:
            raise UnknownTaskTypeError(f"No handler registered for task_type={task_type}") from exc


class UnknownTaskTypeError(RuntimeError):
    pass

class RetryableDependencyError(Exception):
    pass

class FatalDependencyError(Exception):
    pass


class WorkerRuntime:
    def __init__(
        self,
        settings: WorkerRuntimeSettings,
        database: AutomationDatabase | None = None,
        registry: TaskRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.database = database or AutomationDatabase(
            settings.database_url,
            lease_seconds=settings.lease_seconds,
            retry_config=RetryConfig(
                base_delay_seconds=settings.retry_base_delay_seconds,
                max_delay_seconds=settings.retry_max_delay_seconds,
            ),
        )
        self.registry = registry or TaskRegistry()
        self._stop_event = asyncio.Event()
        self._active: set[asyncio.Task[None]] = set()
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running and not self._stop_event.is_set()

    def register_task(self, task_type: str, handler: TaskHandler) -> None:
        self.registry.register(task_type, handler)

    def request_stop(self) -> None:
        self._stop_event.set()

    def install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, self.request_stop)
            except NotImplementedError:
                signal.signal(signum, lambda _signum, _frame: self.request_stop())

    async def run(self, install_signal_handlers: bool = True) -> None:
        configure_json_logging(self.settings.log_level)
        await self.database.open()
        if install_signal_handlers:
            self.install_signal_handlers()
        self._running = True
        log_event("worker_started", self.settings.worker_id, status="running")
        try:
            while not self._stop_event.is_set():
                self._reap_finished()
                capacity = self.settings.max_concurrency - len(self._active)
                if capacity <= 0:
                    await self._wait_for_capacity()
                    continue

                batch_limit = min(self.settings.batch_size, capacity)
                try:
                    acquired = await self.database.acquire_ready_tasks_batch(
                        batch_limit,
                        self.settings.worker_id,
                        max_per_task_type=self.settings.max_per_task_type,
                        max_per_account=self.settings.max_per_account,
                    )
                except Exception as exc:
                    log_event(
                        "task_acquire_error",
                        self.settings.worker_id,
                        status="error",
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
                    await asyncio.sleep(self.settings.poll_interval_seconds)
                    continue

                if not acquired:
                    await asyncio.sleep(self.settings.poll_interval_seconds)
                    continue

                for acquired_task in acquired:
                    task = asyncio.create_task(self._run_acquired_task(acquired_task))
                    self._active.add(task)
            await self._drain_active_tasks()
        finally:
            self._running = False
            await self.database.close()
            log_event("worker_stopped", self.settings.worker_id, status="stopped")

    async def _run_acquired_task(self, acquired_task: AcquiredTask) -> None:
        started_at = time.monotonic()
        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(acquired_task, heartbeat_stop))
        log_event(
            "task_started",
            self.settings.worker_id,
            task_id=acquired_task.task.id,
            execution_id=acquired_task.execution.id,
            task_type=acquired_task.task.task_type,
            status="running",
        )
        try:
            handler = self.registry.get(acquired_task.task.task_type)
            async with asyncio.timeout(self.settings.task_timeout_seconds):
                cache: dict[tuple[str, str], dict] = {}
                resolved_payload = await self._resolve_payload(
                    acquired_task.task.payload,
                    str(acquired_task.task.job_id),
                    cache=cache
                )
                if acquired_task.task.task_type.startswith("publish_"):
                    account_id = resolved_payload.get("account_id")
                    if account_id:
                        async with self.database.connection() as conn:
                            cursor = await conn.execute("SELECT platform, account_handle, proxy_url FROM accounts WHERE id = ?", (str(account_id),))
                            row = await cursor.fetchone()
                            if not row:
                                raise FatalDependencyError(f"Account '{account_id}' not found at runtime")
                            resolved_payload["_account"] = {
                                "platform": row["platform"],
                                "account_handle": row["account_handle"],
                                "proxy": row["proxy_url"],
                            }
                result = await _call_handler(handler, resolved_payload)
            safe_result = _ensure_json_object(result)
            await self.database.mark_task_success(
                acquired_task.task.id,
                acquired_task.execution.id,
                safe_result,
            )
            log_event(
                "task_succeeded",
                self.settings.worker_id,
                task_id=acquired_task.task.id,
                execution_id=acquired_task.execution.id,
                task_type=acquired_task.task.task_type,
                status="success",
                duration_ms=_duration_ms(started_at),
            )
        except RetryableDependencyError as exc:
            retry_count = acquired_task.task.retry_count
            delay = min(
                self.settings.retry_base_delay_seconds * (2 ** retry_count),
                self.settings.retry_max_delay_seconds
            )
            await self.database.mark_task_for_retry(
                task_id=str(acquired_task.task.id),
                execution_id=str(acquired_task.execution.id),
                error=exc,
                delay_seconds=delay
            )
        except FatalDependencyError as exc:
            await self.database.mark_task_failure(
                acquired_task.task.id,
                acquired_task.execution.id,
                exc,
                timed_out=False
            )
        except asyncio.TimeoutError as exc:
            await self._mark_failed_safely(acquired_task, exc, timed_out=True, started_at=started_at)
        except Exception as exc:
            await self._mark_failed_safely(acquired_task, exc, timed_out=False, started_at=started_at)
        finally:
            heartbeat_stop.set()
            await _await_safely(heartbeat_task)

    async def _resolve_payload(self, payload: Any, job_id: str, cache: dict) -> dict:
        needed_keys = set()
        def _scan(obj: Any) -> None:
            if isinstance(obj, dict):
                if "from_task" in obj and "field" in obj:
                    needed_keys.add(obj["from_task"])
                for v in obj.values():
                    _scan(v)
            elif isinstance(obj, list):
                for item in obj:
                    _scan(item)
                    
        _scan(payload)
        
        keys_to_fetch = [k for k in needed_keys if (job_id, k) not in cache]
        if keys_to_fetch:
            results = await self.database.get_task_results_bulk(job_id, keys_to_fetch)
            missing_keys = set(keys_to_fetch) - set(results.keys())
            if missing_keys:
                raise FatalDependencyError(f"Missing dependency tasks: {missing_keys}")
            for k, state in results.items():
                cache[(job_id, k)] = state
                
        async def _resolve(obj: Any) -> Any:
            if isinstance(obj, dict):
                if "from_task" in obj and "field" in obj:
                    task_key = obj["from_task"]
                    field = obj["field"]
                    
                    state = cache.get((job_id, task_key))
                    if not state:
                        raise ValueError(f"Task '{task_key}' missing from bulk fetch results")
                        
                    status, result = state["status"], state["result"]
                    
                    if status == "SUCCESS":
                        if not result or field not in result:
                            import json
                            raise FatalDependencyError(json.dumps({
                                "error_type": "INVALID_DEPENDENCY_FIELD",
                                "task_key": task_key,
                                "field": field
                            }))
                        return result[field]
                    elif status == "FAILED":
                        raise FatalDependencyError(f"Dependency task '{task_key}' failed permanently.")
                    else:
                        raise RetryableDependencyError(f"Dependency task '{task_key}' is not ready (status: {status}).")

                return {k: await _resolve(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [await _resolve(item) for item in obj]
            return obj
            
        resolved = await _resolve(payload)
        if not isinstance(resolved, dict):
            raise TypeError("Resolved payload must be a JSON object")
        return resolved

    async def _mark_failed_safely(
        self,
        acquired_task: AcquiredTask,
        exc: Exception,
        timed_out: bool,
        started_at: float,
    ) -> None:
        try:
            updated_task = await self.database.mark_task_failure(
                acquired_task.task.id,
                acquired_task.execution.id,
                exc,
                timed_out=timed_out,
            )
            log_event(
                "task_failed",
                self.settings.worker_id,
                task_id=acquired_task.task.id,
                execution_id=acquired_task.execution.id,
                task_type=acquired_task.task.task_type,
                status=updated_task.status.value,
                duration_ms=_duration_ms(started_at),
                error=str(exc),
                error_type=type(exc).__name__,
                retry_count=updated_task.retry_count,
            )
        except Exception as mark_exc:
            log_event(
                "task_failure_update_error",
                self.settings.worker_id,
                task_id=acquired_task.task.id,
                execution_id=acquired_task.execution.id,
                task_type=acquired_task.task.task_type,
                status="error",
                duration_ms=_duration_ms(started_at),
                error=str(mark_exc),
                error_type=type(mark_exc).__name__,
            )

    async def _heartbeat_loop(self, acquired_task: AcquiredTask, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                updated = await self.database.update_execution_heartbeat(acquired_task.execution.id)
                log_event(
                    "task_heartbeat",
                    self.settings.worker_id,
                    task_id=acquired_task.task.id,
                    execution_id=acquired_task.execution.id,
                    task_type=acquired_task.task.task_type,
                    status="running" if updated else "not_running",
                )
                if not updated:
                    return
            except Exception as exc:
                log_event(
                    "task_heartbeat_error",
                    self.settings.worker_id,
                    task_id=acquired_task.task.id,
                    execution_id=acquired_task.execution.id,
                    task_type=acquired_task.task.task_type,
                    status="error",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.settings.heartbeat_interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _wait_for_capacity(self) -> None:
        if not self._active:
            await asyncio.sleep(self.settings.poll_interval_seconds)
            return
        done, pending = await asyncio.wait(self._active, return_when=asyncio.FIRST_COMPLETED)
        self._active = set(pending)
        for task in done:
            await _await_safely(task)

    async def _drain_active_tasks(self) -> None:
        if not self._active:
            return
        log_event(
            "worker_shutdown_waiting",
            self.settings.worker_id,
            status="draining",
            active_tasks=len(self._active),
        )
        done, _pending = await asyncio.wait(self._active)
        self._active.clear()
        for task in done:
            await _await_safely(task)

    def _reap_finished(self) -> None:
        done = {task for task in self._active if task.done()}
        self._active.difference_update(done)
        for task in done:
            if task.cancelled():
                continue
            exc = task.exception()
            if exc is not None:
                log_event(
                    "task_runtime_error",
                    self.settings.worker_id,
                    status="error",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )


async def _call_handler(handler: TaskHandler, payload: dict[str, Any]) -> dict[str, Any]:
    result = handler(payload)
    if inspect.isawaitable(result):
        result = await result
    return _ensure_json_object(result)


def _ensure_json_object(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError("task handler must return a JSON object")
    return value


async def _await_safely(task: asyncio.Task[Any]) -> None:
    try:
        await task
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log_event("background_task_error", "unknown", status="error", error=str(exc), error_type=type(exc).__name__)


def configure_json_logging(level: str) -> None:
    logging.getLogger().handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(message)s %(event)s %(task_id)s %(execution_id)s "
        "%(worker_id)s %(task_type)s %(status)s %(duration_ms)s %(error)s %(error_type)s"
    )
    handler.setFormatter(formatter)
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(level)


def log_event(event: str, worker_id: str, **fields: Any) -> None:
    extra = {"event": event, "worker_id": worker_id}
    extra.update({key: _log_value(value) for key, value in fields.items()})
    LOGGER.info(event, extra=extra)


def _duration_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)


def _log_value(value: Any) -> Any:
    if value is None:
        return None
    return str(value) if hasattr(value, "hex") else value


def _load_env(env_file: str | Path) -> dict[str, str]:
    merged = dict(os.environ)
    path = Path(env_file)
    if not path.exists():
        return merged
    if not path.is_file():
        raise RuntimeError(f".env path is not a file: {path}")
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            raise RuntimeError(f"Invalid .env line {line_number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise RuntimeError(f"Invalid .env line {line_number}: empty key")
        merged[key] = _strip_quotes(value.strip())
    return merged


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _required(env: Mapping[str, str], key: str) -> str:
    value = env.get(key)
    if value is None or value.strip() == "":
        raise RuntimeError(f"Missing required environment variable: {key}")
    return value.strip()


def _first_present(
    env: Mapping[str, str],
    keys: tuple[str, ...],
    default: str | None = None,
    required: bool = False,
) -> str:
    for key in keys:
        value = env.get(key)
        if value is not None and value.strip():
            return value.strip()
    if required:
        raise RuntimeError(f"Missing required environment variable: {' or '.join(keys)}")
    if default is None:
        raise RuntimeError(f"Missing required environment variable: {' or '.join(keys)}")
    return default


def _int_value(env: Mapping[str, str], key: str, default: int, minimum: int) -> int:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{key} must be an integer") from exc
    if parsed < minimum:
        raise RuntimeError(f"{key} must be >= {minimum}")
    return parsed


def _optional_int_value(env: Mapping[str, str], key: str, minimum: int) -> int | None:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return None
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{key} must be an integer") from exc
    if parsed < minimum:
        raise RuntimeError(f"{key} must be >= {minimum}")
    return parsed


def _float_value(env: Mapping[str, str], key: str, default: float, minimum: float) -> float:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        parsed = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{key} must be a number") from exc
    if parsed < minimum:
        raise RuntimeError(f"{key} must be >= {minimum}")
    return parsed


async def main() -> None:
    from workers.handlers import register_default_handlers

    settings = WorkerRuntimeSettings.from_env()
    runtime = WorkerRuntime(settings)
    register_default_handlers(runtime.registry)
    await runtime.run()


if __name__ == "__main__":
    asyncio.run(main())
