-- Migration 007: Security Hardening
-- Adds: sessions, login_attempts, license_events tables
-- Extends: licenses table with IP tracking + flagging

-- ── Active Sessions ────────────────────────────────────────────────────────────
-- One row per issued token. On new login → old session revoked.
-- License revoke → all sessions for that key set revoked=1.
CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,                 -- session UUID
    license_key  TEXT NOT NULL,
    machine_fp   TEXT NOT NULL,                    -- server-computed fingerprint
    ip           TEXT,
    token_hash   TEXT NOT NULL,                    -- SHA-256 of the issued token
    issued_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at   DATETIME NOT NULL,
    revoked      INTEGER NOT NULL DEFAULT 0,       -- 1 = invalidated
    revoke_reason TEXT,
    FOREIGN KEY (license_key) REFERENCES licenses (license_key) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS sessions_key_idx      ON sessions (license_key, revoked);
CREATE INDEX IF NOT EXISTS sessions_token_idx    ON sessions (token_hash);
CREATE INDEX IF NOT EXISTS sessions_expires_idx  ON sessions (expires_at) WHERE revoked = 0;

-- ── Login Rate Limiting ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS login_attempts (
    ip           TEXT NOT NULL,
    attempted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS login_attempts_ip_time_idx ON login_attempts (ip, attempted_at);

-- ── License Audit Events ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS license_events (
    id           TEXT PRIMARY KEY,
    license_key  TEXT,
    event_type   TEXT NOT NULL,  -- login_ok, login_fail, revoked, reset_machine, ip_anomaly, admin_create
    ip           TEXT,
    machine_fp   TEXT,
    detail       TEXT,           -- JSON blob with extra context
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS license_events_key_idx  ON license_events (license_key, created_at DESC);
CREATE INDEX IF NOT EXISTS license_events_type_idx ON license_events (event_type, created_at DESC);

-- ── Extend licenses table ──────────────────────────────────────────────────────
ALTER TABLE licenses ADD COLUMN last_ip       TEXT;
ALTER TABLE licenses ADD COLUMN last_seen_at  DATETIME;
ALTER TABLE licenses ADD COLUMN flagged       INTEGER NOT NULL DEFAULT 0;
ALTER TABLE licenses ADD COLUMN flagged_reason TEXT;
