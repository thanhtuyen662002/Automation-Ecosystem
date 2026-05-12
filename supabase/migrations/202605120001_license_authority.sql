-- Supabase-hosted license authority schema.
-- Desktop clients must not read/write these tables directly. They call the
-- license-auth Edge Function, which uses service-role access internally.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS public.licenses (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    license_key text UNIQUE,
    license_key_hash text UNIQUE,
    license_key_preview text,
    label text,
    account text,
    role text NOT NULL DEFAULT 'operator'
        CHECK (role IN ('operator', 'admin', 'viewer')),
    max_accounts integer NOT NULL DEFAULT 10 CHECK (max_accounts > 0),
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'revoked', 'expired')),
    is_active boolean NOT NULL DEFAULT true,
    expires_at timestamptz,
    machine_id text,
    machine_id_hash text,
    activated_at timestamptz,
    last_ip text,
    last_seen_at timestamptz,
    flagged boolean NOT NULL DEFAULT false,
    flagged_reason text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.license_devices (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    license_id uuid NOT NULL REFERENCES public.licenses(id) ON DELETE CASCADE,
    machine_id_hash text NOT NULL,
    account text NOT NULL,
    app_version text,
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'revoked')),
    activated_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz,
    revoked_at timestamptz,
    revoke_reason text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (license_id, machine_id_hash)
);

CREATE TABLE IF NOT EXISTS public.license_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    license_id uuid NOT NULL REFERENCES public.licenses(id) ON DELETE CASCADE,
    device_id uuid NOT NULL REFERENCES public.license_devices(id) ON DELETE CASCADE,
    refresh_token_hash text NOT NULL UNIQUE,
    issued_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    last_used_at timestamptz,
    revoked_at timestamptz,
    revoke_reason text
);

CREATE TABLE IF NOT EXISTS public.license_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    license_id uuid REFERENCES public.licenses(id) ON DELETE SET NULL,
    device_id uuid REFERENCES public.license_devices(id) ON DELETE SET NULL,
    event_type text NOT NULL,
    detail jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.app_config (
    key text PRIMARY KEY,
    value jsonb NOT NULL,
    is_public boolean NOT NULL DEFAULT false,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS licenses_active_idx
    ON public.licenses (is_active, status, expires_at);

CREATE INDEX IF NOT EXISTS license_devices_license_status_idx
    ON public.license_devices (license_id, status);

CREATE INDEX IF NOT EXISTS license_sessions_active_idx
    ON public.license_sessions (license_id, device_id, expires_at)
    WHERE revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS license_events_license_time_idx
    ON public.license_events (license_id, created_at DESC);

ALTER TABLE public.licenses ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.license_devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.license_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.license_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.app_config ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.licenses FROM anon, authenticated;
REVOKE ALL ON public.license_devices FROM anon, authenticated;
REVOKE ALL ON public.license_sessions FROM anon, authenticated;
REVOKE ALL ON public.license_events FROM anon, authenticated;
REVOKE ALL ON public.app_config FROM anon, authenticated;
