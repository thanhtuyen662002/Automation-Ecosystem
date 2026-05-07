-- Migration 002: Add browser fingerprint fields per account
-- These fields store stable identity values captured during login,
-- ensuring the same browser "looks" are used consistently on every run.
-- Randomizing them per-run would increase detection risk.

ALTER TABLE accounts ADD COLUMN viewport_width INTEGER NOT NULL DEFAULT 1280;
ALTER TABLE accounts ADD COLUMN viewport_height INTEGER NOT NULL DEFAULT 720;
ALTER TABLE accounts ADD COLUMN timezone TEXT NOT NULL DEFAULT 'America/New_York';
ALTER TABLE accounts ADD COLUMN locale TEXT NOT NULL DEFAULT 'en-US';
