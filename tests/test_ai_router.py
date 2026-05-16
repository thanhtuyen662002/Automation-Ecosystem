from __future__ import annotations

import inspect

import pytest


@pytest.fixture()
def isolated_ai_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_KEY_STORE_DB", str(tmp_path / "ai_keys.db"))
    monkeypatch.setenv("AI_KEYS_MASTER_KEY_PATH", str(tmp_path / "secrets" / "ai_keys_master.key"))
    from core import ai_key_store

    ai_key_store.reset_store_for_tests()
    yield ai_key_store
    ai_key_store.reset_store_for_tests()


@pytest.mark.asyncio
async def test_generate_text_ignores_env_keys_when_store_empty(isolated_ai_store, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-openai-key")
    monkeypatch.setenv("GEMINI_API_KEY", "env-gemini-key")
    monkeypatch.setenv("HF_API_KEY", "env-hf-key")

    from core.ai_router import generate_text

    with pytest.raises(RuntimeError, match="No usable AI provider key configured"):
        await generate_text("hello")


def test_ai_router_source_does_not_read_provider_key_envs():
    import core.ai_router as router

    source = inspect.getsource(router)
    assert "OPENAI_API_KEY" not in source
    assert "GEMINI_API_KEY" not in source
    assert "HF_API_KEY" not in source
    assert "_get_env" not in source


@pytest.mark.asyncio
async def test_generate_text_falls_back_to_next_key_and_marks_failure(isolated_ai_store, monkeypatch):
    store = isolated_ai_store
    openai = next(provider for provider in store.list_providers() if provider["provider"] == "openai")
    default_model = next(model for model in openai["models"] if model["is_default"])
    bad_key = store.create_key(openai["id"], "Bad key", "bad-key", enabled=True, priority=10)
    good_key = store.create_key(openai["id"], "Good key", "good-key", enabled=True, priority=20)

    async def fake_openai(candidate, *_args):
        if candidate.raw_key == "bad-key":
            raise RuntimeError("rejected key")
        return "ok from fallback"

    import core.ai_router as router

    monkeypatch.setattr(router, "_PROVIDER_RETRIES", 0)
    monkeypatch.setitem(router._PROVIDER_CALLS, "openai", fake_openai)

    result = await router.generate_text(
        "test",
        preferred_provider=openai["id"],
        preferred_model=default_model["id"],
    )

    assert result == "ok from fallback"
    keys = {key["id"]: key for key in store.list_providers()[0]["keys"]}
    assert keys[bad_key["id"]]["failure_count"] == 1
    assert keys[bad_key["id"]]["last_error"] == "rejected key"
    assert keys[good_key["id"]]["failure_count"] == 0
    assert keys[good_key["id"]]["last_used_at"] is not None


@pytest.mark.asyncio
async def test_generate_text_raises_clear_error_when_every_key_fails(isolated_ai_store, monkeypatch):
    store = isolated_ai_store
    openai = next(provider for provider in store.list_providers() if provider["provider"] == "openai")
    default_model = next(model for model in openai["models"] if model["is_default"])
    store.create_key(openai["id"], "Only key", "bad-key", enabled=True, priority=10)

    async def fake_openai(*_args):
        raise RuntimeError("provider down")

    import core.ai_router as router

    monkeypatch.setattr(router, "_PROVIDER_RETRIES", 0)
    monkeypatch.setitem(router._PROVIDER_CALLS, "openai", fake_openai)

    with pytest.raises(RuntimeError, match="All configured AI providers failed"):
        await router.generate_text(
            "test",
            preferred_provider=openai["id"],
            preferred_model=default_model["id"],
        )
