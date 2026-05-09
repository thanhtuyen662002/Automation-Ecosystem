-- Migration 005: Global Memory tables
-- Adds cross-account environmental awareness (ban waves, fleet-wide risk signals).
-- All tables are append-only with TTL enforcement; they never overwrite local decisions.

-- Global banned fingerprints (hard filter — checked by stealth_brain before local classify).
-- TTL: 14 days. Purged on every read. source_count tracks how many accounts hit this hash.
CREATE TABLE IF NOT EXISTS global_banned_fingerprints (
    id             TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    fingerprint_hash TEXT NOT NULL,
    reason         TEXT NOT NULL DEFAULT '',
    source_count   INTEGER NOT NULL DEFAULT 1,
    created_at     REAL NOT NULL DEFAULT (unixepoch()),   -- Unix timestamp (seconds)
    expires_at     REAL NOT NULL                          -- Unix timestamp (seconds)
);

CREATE UNIQUE INDEX IF NOT EXISTS global_banned_fingerprints_hash_uidx
    ON global_banned_fingerprints (fingerprint_hash);

CREATE INDEX IF NOT EXISTS global_banned_fingerprints_expires_idx
    ON global_banned_fingerprints (expires_at);

-- Global risk events (soft signal — used only to compute recent_ban_rate).
-- event_type: 'ban' | 'captcha' | 'soft_block'
-- Retained for 14 days, queried over a rolling 24-hour window.
CREATE TABLE IF NOT EXISTS global_risk_events (
    id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    event_type  TEXT NOT NULL CHECK (event_type IN ('ban', 'captcha', 'soft_block', 'high_risk')),
    account_id  TEXT NOT NULL,
    risk_score  REAL NOT NULL CHECK (risk_score BETWEEN 0.0 AND 1.0),
    created_at  REAL NOT NULL DEFAULT (unixepoch()),
    expires_at  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS global_risk_events_type_time_idx
    ON global_risk_events (event_type, created_at DESC);

CREATE INDEX IF NOT EXISTS global_risk_events_expires_idx
    ON global_risk_events (expires_at);

-- Global KV stats store (pre-aggregated metrics for quick reads).
-- key examples: 'recent_ban_rate_24h', 'fleet_event_count_24h'
CREATE TABLE IF NOT EXISTS global_stats (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '{}',    -- JSON
    updated_at REAL NOT NULL DEFAULT (unixepoch())
);
