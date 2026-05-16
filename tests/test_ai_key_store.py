from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture()
def isolated_ai_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_KEY_STORE_DB", str(tmp_path / "ai_keys.db"))
    monkeypatch.setenv("AI_KEYS_MASTER_KEY_PATH", str(tmp_path / "secrets" / "ai_keys_master.key"))
    from core import ai_key_store

    ai_key_store.reset_store_for_tests()
    yield ai_key_store
    ai_key_store.reset_store_for_tests()


def test_seed_create_key_model_and_list_never_returns_raw_key(isolated_ai_store):
    store = isolated_ai_store
    providers = store.list_providers()
    assert [provider["provider"] for provider in providers] == ["openai", "gemini", "huggingface", "pollinations"]

    custom = store.create_provider("custom", "Custom AI", enabled=True, priority=5)
    key = store.create_key(custom["id"], "Main key", "secret-raw-key", enabled=True, priority=10)
    model = store.create_model(custom["id"], "custom-model", "Custom Model", enabled=True, is_default=True)

    listed = next(provider for provider in store.list_providers() if provider["id"] == custom["id"])
    assert listed["keys"][0]["id"] == key["id"]
    assert listed["keys"][0]["key_preview"] == "sec...-key"
    assert "raw_key" not in listed["keys"][0]
    assert "encrypted_key" not in listed["keys"][0]
    assert listed["models"][0]["id"] == model["id"]


def test_api_key_is_encrypted_at_rest(isolated_ai_store):
    store = isolated_ai_store
    openai = next(provider for provider in store.list_providers() if provider["provider"] == "openai")
    raw_key = "sk-test-1234567890"
    key = store.create_key(openai["id"], "Encrypted", raw_key, enabled=True, priority=10)

    conn = sqlite3.connect(store._resolve_database_path())
    try:
        encrypted = conn.execute(
            "SELECT encrypted_key FROM ai_api_keys WHERE id = ?",
            (key["id"],),
        ).fetchone()[0]
    finally:
        conn.close()

    assert encrypted != raw_key
    assert raw_key not in encrypted
    assert store.decrypt_api_key(encrypted) == raw_key


def test_get_enabled_candidates_orders_by_provider_model_and_key_priority(isolated_ai_store):
    store = isolated_ai_store
    providers = store.list_providers()
    openai = next(provider for provider in providers if provider["provider"] == "openai")
    gemini = next(provider for provider in providers if provider["provider"] == "gemini")
    openai_default = next(model for model in openai["models"] if model["is_default"])
    gemini_default = next(model for model in gemini["models"] if model["is_default"])

    key_slow = store.create_key(openai["id"], "Slow", "openai-slow", enabled=True, priority=30)
    key_fast = store.create_key(openai["id"], "Fast", "openai-fast", enabled=True, priority=10)
    gemini_key = store.create_key(gemini["id"], "Gemini", "gemini-key", enabled=True, priority=10)

    candidates = store.get_enabled_candidates()

    assert candidates[0].provider == "openai"
    assert candidates[0].model_id == openai_default["id"]
    assert candidates[0].key_id == key_fast["id"]
    assert candidates[1].provider == "openai"
    assert candidates[1].model_id == openai_default["id"]
    assert candidates[1].key_id == key_slow["id"]
    assert any(candidate.provider == "gemini" and candidate.model_id == gemini_default["id"] and candidate.key_id == gemini_key["id"] for candidate in candidates)


def test_mark_key_success_and_failure(isolated_ai_store):
    store = isolated_ai_store
    openai = next(provider for provider in store.list_providers() if provider["provider"] == "openai")
    key = store.create_key(openai["id"], "Main", "raw-key", enabled=True, priority=10)

    store.mark_key_failure(key["id"], RuntimeError("temporary failure"))
    failed = next(item for item in store.get_provider(openai["id"])["keys"] if item["id"] == key["id"])
    assert failed["failure_count"] == 1
    assert failed["last_error"] == "temporary failure"

    store.mark_key_success(key["id"])
    succeeded = next(item for item in store.get_provider(openai["id"])["keys"] if item["id"] == key["id"])
    assert succeeded["failure_count"] == 0
    assert succeeded["last_error"] is None
    assert succeeded["last_used_at"] is not None
