from __future__ import annotations

def test_deep_health_payload_degraded_when_worker_missing() -> None:
    from api.routes.system import _deep_health_payload

    payload = _deep_health_payload(
        db_ok=True,
        db_error=None,
        scheduler_running=True,
        worker_running=False,
    )

    assert payload["status"] == "degraded"
    assert payload["worker"] == {"running": False}
    assert payload["execution"] == {
        "can_execute_tasks": False,
        "worker_required": True,
        "mode": "api_only_or_worker_missing",
    }


def test_deep_health_payload_ok_when_worker_running() -> None:
    from api.routes.system import _deep_health_payload

    payload = _deep_health_payload(
        db_ok=True,
        db_error=None,
        scheduler_running=True,
        worker_running=True,
    )

    assert payload["status"] == "ok"
    assert payload["worker"] == {"running": True}
    assert payload["execution"] == {
        "can_execute_tasks": True,
        "worker_required": True,
        "mode": "all_in_one",
    }
