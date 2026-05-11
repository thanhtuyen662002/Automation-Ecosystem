-- Migration 006: License Key Management
-- Adds the licenses table for admin-issued, machine-bound license keys.
-- Run on existing databases that already have migrations 001–005 applied.

CREATE TABLE IF NOT EXISTS licenses (
    id           TEXT PRIMARY KEY,
    license_key  TEXT UNIQUE NOT NULL,        -- AE-XXXX-XXXX-XXXX
    label        TEXT,                         -- Human-readable name, e.g. "Khách hàng A"
    machine_id   TEXT,                         -- Hardware fingerprint (NULL = not yet activated)
    activated_at DATETIME,                     -- First activation timestamp
    expires_at   DATETIME,                     -- NULL = never expires
    is_active    INTEGER NOT NULL DEFAULT 1,   -- 1 = valid, 0 = revoked
    notes        TEXT,
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS licenses_key_uidx ON licenses (license_key);
CREATE INDEX IF NOT EXISTS licenses_machine_idx    ON licenses (machine_id) WHERE machine_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS licenses_active_idx     ON licenses (is_active, expires_at);
