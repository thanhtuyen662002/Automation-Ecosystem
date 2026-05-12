from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any


DEFAULT_AUTH_ID = "default"
DEFAULT_REFRESH_TOKEN_KEY = "license_refresh_token"
DEFAULT_OFFLINE_GRACE_DAYS = 7


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def offline_grace_iso(days: int = DEFAULT_OFFLINE_GRACE_DAYS) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


async def ensure_local_auth_table(db) -> None:
    async with db.connection() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS local_auth_cache (
                id TEXT PRIMARY KEY CHECK (id = 'default'),
                license_key TEXT NOT NULL,
                activation_id TEXT,
                account TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'operator',
                max_accounts INTEGER NOT NULL DEFAULT 10,
                expires_at DATETIME,
                last_validated_at DATETIME NOT NULL,
                offline_grace_until DATETIME NOT NULL,
                refresh_token_key TEXT NOT NULL DEFAULT 'license_refresh_token',
                app_config TEXT NOT NULL DEFAULT '{}',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS local_auth_cache_grace_idx ON local_auth_cache (offline_grace_until)"
        )
        await conn.commit()


async def upsert_local_auth_cache(
    db,
    *,
    license_key: str,
    account: str,
    role: str = "operator",
    max_accounts: int = 10,
    activation_id: str | None = None,
    expires_at: str | None = None,
    offline_grace_until: str | None = None,
    refresh_token_key: str = DEFAULT_REFRESH_TOKEN_KEY,
    app_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    await ensure_local_auth_table(db)
    now = utc_now_iso()
    grace = offline_grace_until or offline_grace_iso()
    config_json = json.dumps(app_config or {}, separators=(",", ":"))
    async with db.connection() as conn:
        await conn.execute(
            """
            INSERT INTO local_auth_cache (
                id, license_key, activation_id, account, role, max_accounts,
                expires_at, last_validated_at, offline_grace_until,
                refresh_token_key, app_config
            )
            VALUES ('default', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                license_key = excluded.license_key,
                activation_id = excluded.activation_id,
                account = excluded.account,
                role = excluded.role,
                max_accounts = excluded.max_accounts,
                expires_at = excluded.expires_at,
                last_validated_at = excluded.last_validated_at,
                offline_grace_until = excluded.offline_grace_until,
                refresh_token_key = excluded.refresh_token_key,
                app_config = excluded.app_config,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                license_key,
                activation_id,
                account,
                role,
                int(max_accounts or 10),
                expires_at,
                now,
                grace,
                refresh_token_key,
                config_json,
            ),
        )
        await conn.commit()
    cached = await get_local_auth_cache(db)
    if cached is None:
        raise RuntimeError("local_auth_cache upsert failed")
    return cached


async def get_local_auth_cache(db) -> dict[str, Any] | None:
    await ensure_local_auth_table(db)
    async with db.connection() as conn:
        cur = await conn.execute("SELECT * FROM local_auth_cache WHERE id = 'default'")
        row = await cur.fetchone()
    if not row:
        return None
    data = dict(row)
    try:
        data["app_config"] = json.loads(data.get("app_config") or "{}")
    except json.JSONDecodeError:
        data["app_config"] = {}
    return data


async def clear_local_auth_cache(db) -> None:
    await ensure_local_auth_table(db)
    async with db.connection() as conn:
        await conn.execute("DELETE FROM local_auth_cache WHERE id = 'default'")
        await conn.commit()


def cache_allows_offline_use(cache: dict[str, Any] | None) -> bool:
    if not cache:
        return False
    raw = cache.get("offline_grace_until")
    if not raw:
        return False
    try:
        grace_until = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    return datetime.now(UTC) <= grace_until
