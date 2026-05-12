-- Seed license key into Supabase DB
-- Run this in: Supabase Dashboard → SQL Editor
-- Project: twkqwtpgahjusofcpivw

INSERT INTO public.licenses (
    license_key,
    license_key_preview,
    label,
    role,
    max_accounts,
    status,
    is_active,
    expires_at,
    notes
)
VALUES (
    'AE-290FE371F2639BBCD1747F4ECAB7DAAA',
    'AE-290F...DAAA',
    'Admin License',
    'admin',
    100,
    'active',
    true,
    NULL,   -- NULL = no expiry
    'Primary admin license'
)
ON CONFLICT (license_key) DO UPDATE SET
    is_active  = true,
    status     = 'active',
    role       = EXCLUDED.role,
    updated_at = now();
