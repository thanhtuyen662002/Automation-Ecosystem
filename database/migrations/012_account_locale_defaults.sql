-- Align unconnected account fingerprint defaults with the packaged desktop locale.
-- Existing connected sessions are left untouched to avoid changing a trusted profile.

UPDATE accounts
SET timezone = 'Asia/Ho_Chi_Minh',
    updated_at = CURRENT_TIMESTAMP
WHERE timezone = 'America/New_York'
  AND session_valid = 0
  AND last_login_at IS NULL;

UPDATE accounts
SET locale = 'vi-VN',
    updated_at = CURRENT_TIMESTAMP
WHERE locale = 'en-US'
  AND session_valid = 0
  AND last_login_at IS NULL;
