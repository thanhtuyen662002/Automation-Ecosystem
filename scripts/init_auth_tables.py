"""Create missing auth tables: licenses, sessions, login_attempts, license_events."""
from __future__ import annotations
import asyncio, os, sys
from pathlib import Path

for raw in Path(".env").read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

sys.path.insert(0, ".")
from database.database import AutomationDatabase

MISSING_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS licenses (
    id             TEXT PRIMARY KEY,
    license_key    TEXT UNIQUE NOT NULL,
    label          TEXT,
    machine_id     TEXT,
    activated_at   DATETIME,
    expires_at     DATETIME,
    is_active      INTEGER NOT NULL DEFAULT 1,
    role           TEXT NOT NULL DEFAULT 'operator'
        CHECK (role IN ('operator', 'admin', 'viewer')),
    max_accounts   INTEGER NOT NULL DEFAULT 10
        CHECK (max_accounts > 0),
    last_ip        TEXT,
    last_seen_at   DATETIME,
    flagged        INTEGER NOT NULL DEFAULT 0,
    flagged_reason TEXT,
    notes          TEXT,
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

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
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id           TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    ip           TEXT NOT NULL,
    license_key  TEXT,
    attempted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS license_events (
    id          TEXT PRIMARY KEY,
    license_key TEXT,
    event_type  TEXT NOT NULL,
    ip          TEXT,
    machine_fp  TEXT,
    detail      TEXT,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS licenses_key_uidx       ON licenses (license_key);
CREATE INDEX IF NOT EXISTS licenses_machine_idx    ON licenses (machine_id) WHERE machine_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS licenses_active_idx     ON licenses (is_active, expires_at);
CREATE INDEX IF NOT EXISTS sessions_key_idx        ON sessions (license_key, revoked);
CREATE INDEX IF NOT EXISTS sessions_token_idx      ON sessions (token_hash);
CREATE INDEX IF NOT EXISTS sessions_expires_idx    ON sessions (expires_at) WHERE revoked = 0;
CREATE INDEX IF NOT EXISTS login_attempts_ip_idx   ON login_attempts (ip, attempted_at);
CREATE INDEX IF NOT EXISTS license_events_key_idx  ON license_events (license_key, created_at DESC);
"""

async def main() -> None:
    db_url = os.environ.get("DATABASE_URL", "")
    print(f"[init_auth] DB: {db_url}")
    db = AutomationDatabase(db_url)
    await db.open()
    async with db.connection() as conn:
        await conn.executescript(MISSING_TABLES_SQL)
        await conn.commit()
    await db.close()
    print("[init_auth] Auth tables created OK")

if __name__ == "__main__":
    asyncio.run(main())
