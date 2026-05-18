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


def test_runtime_env_bootstrap_preserves_os_env_and_normalizes_lists(tmp_path, monkeypatch) -> None:
    from core import runtime_env

    for key in runtime_env._DEFAULT_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "ADSPOWER_API_BASE=http://from-dotenv.example\n"
        "TIKTOK_MOBILE_PULL_EXTENSIONS=mp4,.WEBM\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ADSPOWER_API_BASE", "http://from-os.example")

    effective = runtime_env.bootstrap_runtime_env()

    assert effective["ADSPOWER_API_BASE"] == "http://from-os.example"
    assert effective["TIKTOK_MOBILE_PULL_EXTENSIONS"] == [".mp4", ".webm"]
    assert effective["TIKTOK_MOBILE_SAVE_SCAN_DIRS"] == [
        "/sdcard/DCIM",
        "/sdcard/Movies",
        "/sdcard/Download",
        "/sdcard/Pictures",
    ]


def test_runtime_env_warns_for_unavailable_impersonate_target(monkeypatch) -> None:
    from core import runtime_env

    monkeypatch.setenv("TIKTOK_YTDLP_IMPERSONATE", "chrome")
    monkeypatch.setattr(runtime_env, "_YTDLP_IMPERSONATE_TARGETS_CACHE", set())

    warnings = runtime_env.runtime_dependency_warnings({})

    assert any(warning["code"] == "download_ytdlp_impersonate_target_unavailable" for warning in warnings)
