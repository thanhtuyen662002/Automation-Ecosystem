"""
Migration 009: Auth/Authorization hardening.

Changes:
  1. licenses  ← add `role` (operator|admin|viewer), `max_accounts`
  2. sessions  ← add `account` (username tied to session)
  3. login_attempts ← add `id` PK + `license_key` for persistence

Run: python scripts/migrate_009.py
Idempotent — safe to run multiple times.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import aiosqlite

DB_PATH = os.environ.get(
    "DATABASE_URL",
    "sqlite+aiosqlite:///C:/Users/Admin/AppData/Roaming/Automation-Ecosystem/data/app.db",
)


def _resolve_path(url: str) -> str:
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if url.startswith(prefix):
            return url[len(prefix):]
    return url


# -- Column additions (table_name -> [(col_name, col_definition)]) ---------------

COLUMN_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "licenses": [
        ("role",         "TEXT NOT NULL DEFAULT 'operator' CHECK (role IN ('operator', 'admin', 'viewer'))"),
        ("max_accounts", "INTEGER NOT NULL DEFAULT 10 CHECK (max_accounts > 0)"),
    ],
    "sessions": [
        ("account", "TEXT"),
    ],
}

# ── New table DDLs (skipped if already exist) ─────────────────────────────────

NEW_TABLES: list[str] = [
    # Rebuild login_attempts with PK + license_key for DB-backed rate limiting
    # We can't alter without dropping; we rename the old one if it exists.
    # Handled separately below.
]


async def _add_columns(conn: aiosqlite.Connection, table: str, cols: list[tuple[str, str]]) -> list[str]:
    """Add missing columns to a table. Returns list of added column names."""
    cur = await conn.execute(f"PRAGMA table_info({table})")
    rows = await cur.fetchall()
    existing = {row[1] for row in rows}  # row[1] = column name

    added: list[str] = []
    for col_name, col_def in cols:
        if col_name not in existing:
            sql = f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"
            print(f"  [ADD] ALTER TABLE {table} ADD COLUMN {col_name}")
            await conn.execute(sql)
            added.append(col_name)
        else:
            print(f"  [OK]  {table}.{col_name} already exists - skipped")
    return added


async def _migrate_login_attempts(conn: aiosqlite.Connection) -> None:
    """
    Rebuild login_attempts table with id PK + license_key column.
    Old table had only (ip, attempted_at) — incompatible with ALTER ADD PRIMARY KEY.
    Strategy: rename old → _old, create new, drop old.
    """
    cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='login_attempts'")
    exists = await cur.fetchone()

    # Check if already migrated (has 'id' column)
    if exists:
        cur2 = await conn.execute("PRAGMA table_info(login_attempts)")
        cols = {row[1] for row in await cur2.fetchall()}
        if "id" in cols:
            print("  [OK]  login_attempts already migrated - skipped")
            return

        print("  [MIG] Migrating login_attempts table (rename -> recreate)")
        await conn.execute("ALTER TABLE login_attempts RENAME TO login_attempts_old")

    # Create new table
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS login_attempts (
            id           TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
            ip           TEXT NOT NULL,
            license_key  TEXT,
            attempted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS login_attempts_ip_time_idx ON login_attempts (ip, attempted_at)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS login_attempts_key_time_idx ON login_attempts (license_key, attempted_at)"
        " WHERE license_key IS NOT NULL"
    )

    # Migrate old data if existed
    cur3 = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='login_attempts_old'"
    )
    if await cur3.fetchone():
        await conn.execute(
            "INSERT INTO login_attempts (ip, attempted_at) SELECT ip, attempted_at FROM login_attempts_old"
        )
        await conn.execute("DROP TABLE login_attempts_old")
        print("  [ADD] login_attempts old data migrated and old table dropped")
    else:
        print("  [ADD] login_attempts created fresh")


async def _ensure_auth_tables(conn: aiosqlite.Connection) -> None:
    """Ensure sessions, license_events, login_attempts exist (idempotent)."""
    # sessions
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id            TEXT PRIMARY KEY,
            license_key   TEXT NOT NULL,
            machine_fp    TEXT NOT NULL,
            ip            TEXT,
            account       TEXT,
            token_hash    TEXT NOT NULL,
            issued_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at    DATETIME NOT NULL,
            revoked       INTEGER NOT NULL DEFAULT 0,
            revoke_reason TEXT,
            FOREIGN KEY (license_key) REFERENCES licenses (license_key) ON DELETE CASCADE
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS sessions_key_idx     ON sessions (license_key, revoked)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS sessions_token_idx   ON sessions (token_hash)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS sessions_expires_idx ON sessions (expires_at) WHERE revoked = 0"
    )

    # license_events
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS license_events (
            id          TEXT PRIMARY KEY,
            license_key TEXT,
            event_type  TEXT NOT NULL,
            ip          TEXT,
            machine_fp  TEXT,
            detail      TEXT,
            created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS license_events_key_idx  ON license_events (license_key, created_at DESC)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS license_events_type_idx ON license_events (event_type, created_at DESC)"
    )

    print("  [OK]  sessions / license_events ensured")


async def _ensure_license_flags(conn: aiosqlite.Connection) -> None:
    """Ensure migration 007 columns on licenses exist."""
    migration_007_cols = [
        ("last_ip",       "TEXT"),
        ("last_seen_at",  "DATETIME"),
        ("flagged",       "INTEGER NOT NULL DEFAULT 0"),
        ("flagged_reason","TEXT"),
    ]
    await _add_columns(conn, "licenses", migration_007_cols)


async def run() -> None:
    db_path = _resolve_path(DB_PATH)
    print(f"\n[migrate_009] Connecting to: {db_path}\n")

    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row

        print("-- Step 1: Ensure auth tables exist (idempotent) --")
        await _ensure_auth_tables(conn)

        print("\n-- Step 2: Ensure migration 007 columns on licenses --")
        await _ensure_license_flags(conn)

        print("\n-- Step 3: Add new columns (licenses + sessions) --")
        for table, cols in COLUMN_MIGRATIONS.items():
            print(f"  Table: {table}")
            await _add_columns(conn, table, cols)

        print("\n-- Step 4: Rebuild login_attempts with PK + license_key --")
        await _migrate_login_attempts(conn)

        await conn.commit()

    print("\n[migrate_009] DONE: Migration complete.\n")

    # -- Verification ---------------------------------------------------------
    print("-- Verification --")
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        checks = {
            "licenses":         ["role", "max_accounts", "last_ip", "last_seen_at", "flagged", "flagged_reason"],
            "sessions":         ["account", "ip", "machine_fp", "token_hash", "revoked"],
            "login_attempts":   ["id", "ip", "license_key", "attempted_at"],
            "license_events":   ["id", "license_key", "event_type", "ip", "machine_fp"],
        }
        all_ok = True
        for table, expected_cols in checks.items():
            cur = await conn.execute(f"PRAGMA table_info({table})")
            rows = await cur.fetchall()
            existing = {row["name"] for row in rows}
            missing = [c for c in expected_cols if c not in existing]
            if missing:
                print(f"  [FAIL] {table}: missing {missing}")
                all_ok = False
            else:
                print(f"  [PASS] {table}: all required columns present")

        if all_ok:
            print("\n[PASS] All checks passed.")
        else:
            print("\n[FAIL] Some columns still missing - check errors above.")
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
