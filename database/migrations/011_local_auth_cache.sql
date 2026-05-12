-- Local desktop auth cache for Supabase Edge Function license authority.
-- The refresh token is stored via DPAPI/keyring, not in SQLite.

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
);

CREATE INDEX IF NOT EXISTS local_auth_cache_grace_idx
    ON local_auth_cache (offline_grace_until);
