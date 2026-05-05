import pytest

from automation_engine.config import ConfigError, EngineSettings


def base_env() -> dict[str, str]:
    return {
        "DATABASE_URL": "postgresql://user:pass@localhost/db",
        "REDIS_URL": "redis://localhost:6379/0",
        "ENGINE_WORKER_ID": "worker-1",
    }


def test_settings_require_core_environment() -> None:
    env = base_env()
    del env["DATABASE_URL"]

    with pytest.raises(ConfigError, match="DATABASE_URL"):
        EngineSettings.from_env(env)


def test_settings_parse_optional_values() -> None:
    env = base_env()
    env["ENGINE_MAX_ATTEMPTS"] = "7"
    env["ENGINE_LOG_LEVEL"] = "debug"

    settings = EngineSettings.from_env(env)

    assert settings.max_attempts == 7
    assert settings.log_level == "DEBUG"


def test_settings_reject_invalid_integer() -> None:
    env = base_env()
    env["ENGINE_READ_COUNT"] = "0"

    with pytest.raises(ConfigError, match="ENGINE_READ_COUNT"):
        EngineSettings.from_env(env)

