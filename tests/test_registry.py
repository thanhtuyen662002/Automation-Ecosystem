import pytest

from automation_engine.registry import TaskRegistry, UnknownTaskError, run_registered_task


def echo_handler(payload: dict) -> dict:
    return payload


def bad_result_handler(payload: dict) -> object:
    return object()


def test_register_and_get_task() -> None:
    registry = TaskRegistry()

    registry.register_task("example", echo_handler)

    assert registry.get("example") is echo_handler


def test_unknown_task_raises() -> None:
    registry = TaskRegistry()

    with pytest.raises(UnknownTaskError):
        registry.get("missing")


def test_register_rejects_empty_name() -> None:
    registry = TaskRegistry()

    with pytest.raises(ValueError):
        registry.register_task(" ", echo_handler)


def test_register_rejects_unpickleable_handler() -> None:
    registry = TaskRegistry()

    with pytest.raises(TypeError, match="pickleable"):
        registry.register_task("bad", lambda payload: payload)


def test_run_registered_task_rejects_non_json_result() -> None:
    registry = TaskRegistry()
    registry.register_task("bad", bad_result_handler)

    with pytest.raises(TypeError, match="JSON serializable"):
        run_registered_task(registry, "bad", {})
