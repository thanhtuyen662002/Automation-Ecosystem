from __future__ import annotations

import os
import sqlite3
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from cryptography.fernet import Fernet


_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_providers (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    display_name TEXT NOT NULL,
    base_url TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    priority INTEGER NOT NULL DEFAULT 100,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_api_keys (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    label TEXT NOT NULL,
    encrypted_key TEXT NOT NULL,
    key_preview TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    priority INTEGER NOT NULL DEFAULT 100,
    last_used_at REAL,
    last_error TEXT,
    failure_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(provider_id) REFERENCES ai_providers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ai_models (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    is_default INTEGER NOT NULL DEFAULT 0,
    max_tokens INTEGER,
    temperature_default REAL,
    priority INTEGER NOT NULL DEFAULT 100,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(provider_id) REFERENCES ai_providers(id) ON DELETE CASCADE
);
"""

_SEEDED_PROVIDERS = [
    ("openai", "OpenAI", None, True, 10),
    ("gemini", "Google Gemini", None, True, 20),
    ("huggingface", "HuggingFace", None, True, 30),
    ("pollinations", "Pollinations", None, False, 999),
]

_SEEDED_MODELS = {
    "openai": [("gpt-4o-mini", "gpt-4o-mini", True), ("gpt-4o", "gpt-4o", False)],
    "gemini": [
        ("gemini-1.5-flash", "gemini-1.5-flash", True),
        ("gemini-1.5-pro", "gemini-1.5-pro", False),
    ],
    "huggingface": [
        ("mistralai/Mistral-7B-Instruct-v0.3", "Mistral 7B Instruct", True),
    ],
    "pollinations": [("openai", "openai", True)],
}

_PROVIDERS_REQUIRING_KEYS = {"openai", "gemini", "huggingface"}


@dataclass(frozen=True)
class AICandidate:
    provider_id: str
    provider: str
    display_name: str
    base_url: str | None
    model_id: str
    model_name: str
    model_display_name: str
    max_tokens: int | None
    temperature_default: float | None
    key_id: str | None = None
    key_preview: str | None = None
    raw_key: str | None = None


def _get_appdata_dir() -> Path:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Automation-Ecosystem"
    return Path.home() / ".automation-ecosystem"


def _resolve_database_path() -> Path:
    override = os.environ.get("AI_KEY_STORE_DB", "").strip()
    raw = override or os.environ.get("DATABASE_URL", "").strip()
    if "{APP_DATA_DIR}" in raw:
        raw = raw.replace("{APP_DATA_DIR}", str(_get_appdata_dir()).replace("\\", "/"))
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    if raw.startswith("sqlite+aiosqlite://") or raw.startswith("sqlite://"):
        raw = raw.split("://", 1)[1]
    if not raw or raw.startswith("postgres"):
        raw = "data/app.db"
    return Path(raw).expanduser()


def _master_key_path() -> Path:
    override = os.environ.get("AI_KEYS_MASTER_KEY_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    db_path = _resolve_database_path()
    return db_path.parent / "secrets" / "ai_keys_master.key"


def _load_or_create_master_key() -> bytes:
    path = _master_key_path()
    if path.exists():
        return path.read_bytes().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    path.write_bytes(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def _fernet() -> Fernet:
    return Fernet(_load_or_create_master_key())


def encrypt_api_key(raw_key: str) -> str:
    value = raw_key.strip()
    if not value:
        raise ValueError("API key cannot be empty")
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_api_key(encrypted_key: str) -> str:
    return _fernet().decrypt(encrypted_key.encode("utf-8")).decode("utf-8")


def make_key_preview(raw_key: str) -> str:
    value = raw_key.strip()
    if len(value) <= 8:
        return f"{value[:2]}...{value[-2:]}" if len(value) > 4 else "****"
    return f"{value[:3]}...{value[-4:]}"


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    path = _resolve_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _ensure_schema() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        count = conn.execute("SELECT COUNT(*) AS c FROM ai_providers").fetchone()["c"]
        if count:
            return
        now = time.time()
        provider_ids: dict[str, str] = {}
        for provider, display_name, base_url, enabled, priority in _SEEDED_PROVIDERS:
            provider_id = str(uuid4())
            provider_ids[provider] = provider_id
            conn.execute(
                """
                INSERT INTO ai_providers
                    (id, provider, display_name, base_url, enabled, priority, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (provider_id, provider, display_name, base_url, int(enabled), priority, now, now),
            )
        for provider, models in _SEEDED_MODELS.items():
            provider_id = provider_ids[provider]
            for idx, (model_name, display_name, is_default) in enumerate(models):
                conn.execute(
                    """
                    INSERT INTO ai_models
                        (id, provider_id, model_name, display_name, enabled, is_default,
                         max_tokens, temperature_default, priority, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 1, ?, NULL, NULL, ?, ?, ?)
                    """,
                    (str(uuid4()), provider_id, model_name, display_name, int(is_default), (idx + 1) * 10, now, now),
                )


def list_providers() -> list[dict[str, Any]]:
    _ensure_schema()
    with _connect() as conn:
        providers = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM ai_providers ORDER BY priority ASC, created_at ASC"
            ).fetchall()
        ]
        for provider in providers:
            provider_id = provider["id"]
            provider["enabled"] = bool(provider["enabled"])
            keys = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id, provider_id, label, key_preview, enabled, priority,
                           last_used_at, last_error, failure_count, created_at, updated_at
                    FROM ai_api_keys
                    WHERE provider_id = ?
                    ORDER BY priority ASC, created_at ASC
                    """,
                    (provider_id,),
                ).fetchall()
            ]
            for key in keys:
                key["enabled"] = bool(key["enabled"])
            models = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM ai_models
                    WHERE provider_id = ?
                    ORDER BY is_default DESC, priority ASC, created_at ASC
                    """,
                    (provider_id,),
                ).fetchall()
            ]
            for model in models:
                model["enabled"] = bool(model["enabled"])
                model["is_default"] = bool(model["is_default"])
            provider["keys"] = keys
            provider["models"] = models
        return providers


def create_provider(
    provider: str,
    display_name: str,
    base_url: str | None = None,
    enabled: bool = True,
    priority: int = 100,
) -> dict[str, Any]:
    _ensure_schema()
    now = time.time()
    provider_id = str(uuid4())
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO ai_providers
                (id, provider, display_name, base_url, enabled, priority, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (provider_id, provider.strip().lower(), display_name.strip(), base_url, int(enabled), priority, now, now),
        )
    return get_provider(provider_id) or {}


def get_provider(provider_id: str) -> dict[str, Any] | None:
    return next((p for p in list_providers() if p["id"] == provider_id), None)


def update_provider(provider_id: str, **updates: Any) -> dict[str, Any] | None:
    _ensure_schema()
    allowed = {"provider", "display_name", "base_url", "enabled", "priority"}
    values = {k: v for k, v in updates.items() if k in allowed}
    if values.get("provider") is not None:
        values["provider"] = str(values["provider"]).strip().lower()
    else:
        values.pop("provider", None)
    if values.get("display_name") is not None:
        values["display_name"] = str(values["display_name"]).strip()
    else:
        values.pop("display_name", None)
    if "base_url" in values:
        raw_base_url = values["base_url"]
        values["base_url"] = str(raw_base_url).strip() if raw_base_url else None
    if "enabled" in values:
        values["enabled"] = int(bool(values["enabled"]))
    if values.get("priority") is not None:
        values["priority"] = int(values["priority"])
    else:
        values.pop("priority", None)
    if not values:
        return get_provider(provider_id)
    values["updated_at"] = time.time()
    assignments = ", ".join(f"{key} = ?" for key in values)
    params = [*values.values(), provider_id]
    with _connect() as conn:
        cur = conn.execute(f"UPDATE ai_providers SET {assignments} WHERE id = ?", params)
        if cur.rowcount == 0:
            return None
    return get_provider(provider_id)


def delete_provider(provider_id: str) -> bool:
    _ensure_schema()
    with _connect() as conn:
        cur = conn.execute("DELETE FROM ai_providers WHERE id = ?", (provider_id,))
        return cur.rowcount > 0


def create_key(
    provider_id: str,
    label: str,
    raw_key: str,
    enabled: bool = True,
    priority: int = 100,
) -> dict[str, Any]:
    _ensure_schema()
    now = time.time()
    key_id = str(uuid4())
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO ai_api_keys
                (id, provider_id, label, encrypted_key, key_preview, enabled, priority,
                 last_used_at, last_error, failure_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, 0, ?, ?)
            """,
            (
                key_id,
                provider_id,
                label.strip(),
                encrypt_api_key(raw_key),
                make_key_preview(raw_key),
                int(enabled),
                priority,
                now,
                now,
            ),
        )
    return get_key(key_id) or {}


def get_key(key_id: str) -> dict[str, Any] | None:
    _ensure_schema()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id, provider_id, label, key_preview, enabled, priority,
                   last_used_at, last_error, failure_count, created_at, updated_at
            FROM ai_api_keys WHERE id = ?
            """,
            (key_id,),
        ).fetchone()
        item = _row_to_dict(row)
        if item:
            item["enabled"] = bool(item["enabled"])
        return item


def update_key(key_id: str, **updates: Any) -> dict[str, Any] | None:
    _ensure_schema()
    values: dict[str, Any] = {}
    if updates.get("label") is not None:
        values["label"] = str(updates["label"]).strip()
    if updates.get("enabled") is not None:
        values["enabled"] = int(bool(updates["enabled"]))
    if updates.get("priority") is not None:
        values["priority"] = int(updates["priority"])
    raw_key = updates.get("raw_key")
    if raw_key:
        values["encrypted_key"] = encrypt_api_key(str(raw_key))
        values["key_preview"] = make_key_preview(str(raw_key))
        values["last_error"] = None
        values["failure_count"] = 0
    if not values:
        return get_key(key_id)
    values["updated_at"] = time.time()
    assignments = ", ".join(f"{key} = ?" for key in values)
    params = [*values.values(), key_id]
    with _connect() as conn:
        cur = conn.execute(f"UPDATE ai_api_keys SET {assignments} WHERE id = ?", params)
        if cur.rowcount == 0:
            return None
    return get_key(key_id)


def delete_key(key_id: str) -> bool:
    _ensure_schema()
    with _connect() as conn:
        cur = conn.execute("DELETE FROM ai_api_keys WHERE id = ?", (key_id,))
        return cur.rowcount > 0


def create_model(
    provider_id: str,
    model_name: str,
    display_name: str,
    enabled: bool = True,
    is_default: bool = False,
    max_tokens: int | None = None,
    temperature_default: float | None = None,
    priority: int = 100,
) -> dict[str, Any]:
    _ensure_schema()
    now = time.time()
    model_id = str(uuid4())
    with _connect() as conn:
        if is_default:
            conn.execute("UPDATE ai_models SET is_default = 0 WHERE provider_id = ?", (provider_id,))
        conn.execute(
            """
            INSERT INTO ai_models
                (id, provider_id, model_name, display_name, enabled, is_default,
                 max_tokens, temperature_default, priority, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                model_id,
                provider_id,
                model_name.strip(),
                display_name.strip(),
                int(enabled),
                int(is_default),
                max_tokens,
                temperature_default,
                priority,
                now,
                now,
            ),
        )
    return get_model(model_id) or {}


def get_model(model_id: str) -> dict[str, Any] | None:
    _ensure_schema()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM ai_models WHERE id = ?", (model_id,)).fetchone()
        item = _row_to_dict(row)
        if item:
            item["enabled"] = bool(item["enabled"])
            item["is_default"] = bool(item["is_default"])
        return item


def update_model(model_id: str, **updates: Any) -> dict[str, Any] | None:
    _ensure_schema()
    current = get_model(model_id)
    if current is None:
        return None
    values: dict[str, Any] = {}
    for key in ("model_name", "display_name"):
        if updates.get(key) is not None:
            values[key] = str(updates[key]).strip()
    if "max_tokens" in updates:
        values["max_tokens"] = int(updates["max_tokens"]) if updates["max_tokens"] is not None else None
    if "priority" in updates and updates["priority"] is not None:
        values["priority"] = int(updates["priority"])
    if "temperature_default" in updates:
        values["temperature_default"] = (
            float(updates["temperature_default"]) if updates["temperature_default"] is not None else None
        )
    for key in ("enabled", "is_default"):
        if updates.get(key) is not None:
            values[key] = int(bool(updates[key]))
    if not values:
        return current
    values["updated_at"] = time.time()
    with _connect() as conn:
        if values.get("is_default") == 1:
            conn.execute("UPDATE ai_models SET is_default = 0 WHERE provider_id = ?", (current["provider_id"],))
        assignments = ", ".join(f"{key} = ?" for key in values)
        params = [*values.values(), model_id]
        cur = conn.execute(f"UPDATE ai_models SET {assignments} WHERE id = ?", params)
        if cur.rowcount == 0:
            return None
    return get_model(model_id)


def delete_model(model_id: str) -> bool:
    _ensure_schema()
    with _connect() as conn:
        cur = conn.execute("DELETE FROM ai_models WHERE id = ?", (model_id,))
        return cur.rowcount > 0


def get_enabled_candidates(
    preferred_provider: str | None = None,
    preferred_model: str | None = None,
    preferred_key: str | None = None,
) -> list[AICandidate]:
    _ensure_schema()
    preferred_provider = preferred_provider.strip().lower() if preferred_provider else None
    preferred_model = preferred_model.strip() if preferred_model else None
    preferred_key = preferred_key.strip() if preferred_key else None
    candidates: list[AICandidate] = []
    with _connect() as conn:
        providers = conn.execute(
            """
            SELECT * FROM ai_providers
            WHERE enabled = 1
              AND (? IS NULL OR provider = ? OR id = ?)
            ORDER BY priority ASC, created_at ASC
            """,
            (preferred_provider, preferred_provider, preferred_provider),
        ).fetchall()
        for provider in providers:
            models = conn.execute(
                """
                SELECT * FROM ai_models
                WHERE provider_id = ? AND enabled = 1
                  AND (? IS NULL OR model_name = ? OR id = ?)
                ORDER BY is_default DESC, priority ASC, created_at ASC
                """,
                (provider["id"], preferred_model, preferred_model, preferred_model),
            ).fetchall()
            if not models:
                continue
            provider_name = str(provider["provider"])
            if provider_name in _PROVIDERS_REQUIRING_KEYS:
                keys = conn.execute(
                    """
                    SELECT * FROM ai_api_keys
                    WHERE provider_id = ? AND enabled = 1
                      AND (? IS NULL OR id = ?)
                    ORDER BY priority ASC, failure_count ASC, created_at ASC
                    """,
                    (provider["id"], preferred_key, preferred_key),
                ).fetchall()
                for model in models:
                    for key in keys:
                        raw_key = decrypt_api_key(str(key["encrypted_key"]))
                        candidates.append(_candidate_from_rows(provider, model, key, raw_key))
            else:
                if preferred_key is not None:
                    continue
                for model in models:
                    candidates.append(_candidate_from_rows(provider, model, None, None))
    return candidates


def _candidate_from_rows(
    provider: sqlite3.Row,
    model: sqlite3.Row,
    key: sqlite3.Row | None,
    raw_key: str | None,
) -> AICandidate:
    return AICandidate(
        provider_id=str(provider["id"]),
        provider=str(provider["provider"]),
        display_name=str(provider["display_name"]),
        base_url=provider["base_url"],
        model_id=str(model["id"]),
        model_name=str(model["model_name"]),
        model_display_name=str(model["display_name"]),
        max_tokens=model["max_tokens"],
        temperature_default=model["temperature_default"],
        key_id=str(key["id"]) if key is not None else None,
        key_preview=str(key["key_preview"]) if key is not None else None,
        raw_key=raw_key,
    )


def mark_key_success(key_id: str) -> None:
    _ensure_schema()
    now = time.time()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE ai_api_keys
            SET last_used_at = ?, last_error = NULL, failure_count = 0, updated_at = ?
            WHERE id = ?
            """,
            (now, now, key_id),
        )


def mark_key_failure(key_id: str, error: Exception | str) -> None:
    _ensure_schema()
    now = time.time()
    message = str(error)[:500]
    with _connect() as conn:
        conn.execute(
            """
            UPDATE ai_api_keys
            SET last_error = ?, failure_count = failure_count + 1, updated_at = ?
            WHERE id = ?
            """,
            (message, now, key_id),
        )


def reset_store_for_tests() -> None:
    path = _resolve_database_path()
    if path.exists():
        path.unlink()
