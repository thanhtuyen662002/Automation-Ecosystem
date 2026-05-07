-- Migration 004: Proxy health tracking + account warm-up + soft-ban detection
ALTER TABLE accounts ADD COLUMN proxy_country TEXT;
ALTER TABLE accounts ADD COLUMN proxy_latency_ms INTEGER;
ALTER TABLE accounts ADD COLUMN proxy_validated_at DATETIME;
ALTER TABLE accounts ADD COLUMN warmup_sessions_completed INTEGER NOT NULL DEFAULT 0;
ALTER TABLE accounts ADD COLUMN soft_ban_detected INTEGER NOT NULL DEFAULT 0;
