-- Migration 010: account profile identity fields.
-- The startup migration runner skips already-applied duplicate columns.

ALTER TABLE accounts ADD COLUMN avatar_url TEXT;
ALTER TABLE accounts ADD COLUMN display_name TEXT;
ALTER TABLE accounts ADD COLUMN profile_url TEXT;
ALTER TABLE accounts ADD COLUMN external_user_id TEXT;

CREATE INDEX IF NOT EXISTS accounts_profile_url_idx
    ON accounts (profile_url)
    WHERE profile_url IS NOT NULL;
