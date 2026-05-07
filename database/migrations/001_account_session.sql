-- Migration 001: Add account session storage fields
-- Safe to run multiple times (uses IF NOT EXISTS pattern via ALTER TABLE)
-- SQLite does not support IF NOT EXISTS for ALTER TABLE, so the startup runner
-- checks column existence before applying.

ALTER TABLE accounts ADD COLUMN cookies TEXT;
ALTER TABLE accounts ADD COLUMN user_agent TEXT;
ALTER TABLE accounts ADD COLUMN last_login_at DATETIME;
ALTER TABLE accounts ADD COLUMN session_valid INTEGER NOT NULL DEFAULT 0;
