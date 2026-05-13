-- Migration 012: prevent duplicate artifact rows for the same storage object.

CREATE UNIQUE INDEX IF NOT EXISTS artifacts_storage_uri_uidx
    ON artifacts (storage_uri);
