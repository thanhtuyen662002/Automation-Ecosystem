-- Migration 003: Account risk scoring + persistent browser profile path
-- Tracks ban signals per account to enable automatic risk assessment and pausing.

ALTER TABLE accounts ADD COLUMN browser_data_dir TEXT;
ALTER TABLE accounts ADD COLUMN risk_score REAL NOT NULL DEFAULT 0.0;
ALTER TABLE accounts ADD COLUMN failed_publish_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE accounts ADD COLUMN captcha_hit_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE accounts ADD COLUMN login_redirect_count INTEGER NOT NULL DEFAULT 0;
