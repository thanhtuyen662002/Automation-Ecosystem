from __future__ import annotations

import pickle
from collections.abc import Callable
from typing import Any


TaskHandler = Callable[[dict[str, Any]], Any]


def _validate_json_value(value: Any) -> None:
    if value is None or isinstance(value, str | int | float | bool):
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("task result dictionaries must use string keys")
            _validate_json_value(item)
        return
    raise TypeError(f"task result is not JSON serializable: {type(value).__name__}")


class UnknownTaskError(LookupError):
    pass


class TaskRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, TaskHandler] = {}

    def register_task(self, task_name: str, handler: TaskHandler) -> None:
        normalized = task_name.strip()
        if normalized == "":
            raise ValueError("task_name cannot be empty")
        if not callable(handler):
            raise TypeError("handler must be callable")
        try:
            pickle.dumps(handler)
        except Exception as exc:
            raise TypeError("handler must be pickleable for process-enforced timeouts") from exc
        self._handlers[normalized] = handler

    def get(self, task_name: str) -> TaskHandler:
        try:
            return self._handlers[task_name]
        except KeyError as exc:
            raise UnknownTaskError(f"No handler registered for task: {task_name}") from exc

    def contains(self, task_name: str) -> bool:
        return task_name in self._handlers


def run_registered_task(registry: TaskRegistry, task_name: str, payload: dict[str, Any]) -> Any:
    handler = registry.get(task_name)
    result = handler(payload)
    _validate_json_value(result)
    return result
