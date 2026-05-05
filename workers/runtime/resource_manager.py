from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Literal


ResourceType = Literal["browser", "media"]
BLOCKED_LOG_INTERVAL_SECONDS = 30.0


@dataclass(frozen=True)
class ResourceConfig:
    total_concurrency: int = 4
    browser_concurrency: int = 1
    media_concurrency: int = 2
    ai_concurrency: int = 4
    default_pool: str = "ai"
    task_type_pools: Mapping[str, str] | None = None


class ResourceManager:
    def __init__(self, config: ResourceConfig) -> None:
        if config.total_concurrency < 1:
            raise ValueError("total_concurrency must be >= 1")
        self._default_pool = config.default_pool
        self._task_type_pools = dict(config.task_type_pools or {})
        self._total = threading.BoundedSemaphore(config.total_concurrency)
        self._pools = {
            "browser": threading.BoundedSemaphore(_positive(config.browser_concurrency, "browser_concurrency")),
            "media": threading.BoundedSemaphore(_positive(config.media_concurrency, "media_concurrency")),
            "ai": threading.BoundedSemaphore(_positive(config.ai_concurrency, "ai_concurrency")),
        }
        if self._default_pool not in self._pools:
            raise ValueError("default_pool must be one of: browser, media, ai")
        for task_type, pool_name in self._task_type_pools.items():
            if task_type.strip() == "":
                raise ValueError("task_type_pools cannot contain an empty task type")
            if pool_name not in self._pools:
                raise ValueError(f"unknown resource pool for {task_type}: {pool_name}")

    def pool_for(self, task_type: str) -> str:
        return self._task_type_pools.get(task_type, self._default_pool)

    def acquire_slot(self, resource_type: ResourceType) -> None:
        pool = self._slot_pool(resource_type)
        next_log_at = time.monotonic() + BLOCKED_LOG_INTERVAL_SECONDS
        while True:
            acquired = pool.acquire(timeout=1.0)
            if acquired:
                return
            if time.monotonic() >= next_log_at:
                next_log_at = time.monotonic() + BLOCKED_LOG_INTERVAL_SECONDS

    def release_slot(self, resource_type: ResourceType) -> None:
        pool = self._slot_pool(resource_type)
        try:
            pool.release()
        except ValueError as exc:
            raise RuntimeError(f"release_slot called without matching acquire for {resource_type}") from exc

    @contextmanager
    def acquire(self, task_type: str) -> Iterator[str]:
        pool_name = self.pool_for(task_type)
        self._total.acquire()
        slot_acquired = False
        try:
            if pool_name in {"browser", "media"}:
                self.acquire_slot(pool_name)
                slot_acquired = True
            else:
                self._pools[pool_name].acquire()
                slot_acquired = True
            yield pool_name
        finally:
            if slot_acquired:
                if pool_name in {"browser", "media"}:
                    self.release_slot(pool_name)
                else:
                    self._pools[pool_name].release()
            self._total.release()

    def _slot_pool(self, resource_type: str) -> threading.BoundedSemaphore:
        if resource_type not in {"browser", "media"}:
            raise ValueError("resource_type must be 'browser' or 'media'")
        return self._pools[resource_type]


def _positive(value: int, name: str) -> int:
    if value < 1:
        raise ValueError(f"{name} must be >= 1")
    return value
