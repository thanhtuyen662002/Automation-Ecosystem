-- Transaction-safe license device activation for the trusted license-api Edge Function.
-- Existing tables are intentionally reused:
--   public.licenses
--   public.license_devices
--   public.license_audit_logs
--   public.app_license_config

CREATE OR REPLACE FUNCTION public.activate_license_device(
    p_license_key_hash text,
    p_machine_id_hash text,
    p_device_name text DEFAULT NULL,
    p_platform text DEFAULT NULL,
    p_app_version text DEFAULT NULL,
    p_metadata jsonb DEFAULT '{}'::jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_now timestamptz := now();
    v_license public.licenses%ROWTYPE;
    v_device public.license_devices%ROWTYPE;
    v_active_device_count integer := 0;
BEGIN
    IF p_license_key_hash IS NULL OR btrim(p_license_key_hash) = '' THEN
        RETURN jsonb_build_object(
            'ok', false,
            'status', 'invalid_key',
            'reason', 'Missing license key hash',
            'license_id', NULL,
            'device_id', NULL,
            'expires_at', NULL,
            'is_lifetime', false,
            'plan', NULL,
            'label', NULL,
            'max_devices', NULL
        );
    END IF;

    IF p_machine_id_hash IS NULL OR btrim(p_machine_id_hash) = '' THEN
        RETURN jsonb_build_object(
            'ok', false,
            'status', 'machine_mismatch',
            'reason', 'Missing machine id hash',
            'license_id', NULL,
            'device_id', NULL,
            'expires_at', NULL,
            'is_lifetime', false,
            'plan', NULL,
            'label', NULL,
            'max_devices', NULL
        );
    END IF;

    -- 64-bit transaction-scoped lock. This serializes activation decisions for
    -- a single license hash and prevents count/insert races across machines.
    PERFORM pg_advisory_xact_lock(hashtextextended(p_license_key_hash, 0));

    SELECT *
      INTO v_license
      FROM public.licenses
     WHERE license_key_hash = p_license_key_hash
     LIMIT 1;

    IF NOT FOUND THEN
        RETURN jsonb_build_object(
            'ok', false,
            'status', 'invalid_key',
            'reason', 'License key was not found',
            'license_id', NULL,
            'device_id', NULL,
            'expires_at', NULL,
            'is_lifetime', false,
            'plan', NULL,
            'label', NULL,
            'max_devices', NULL
        );
    END IF;

    IF v_license.status <> 'active' THEN
        RETURN jsonb_build_object(
            'ok', false,
            'status', v_license.status,
            'reason', 'License is not active',
            'license_id', v_license.id,
            'device_id', NULL,
            'expires_at', v_license.expires_at,
            'is_lifetime', v_license.expires_at IS NULL,
            'plan', v_license.plan,
            'label', v_license.label,
            'max_devices', v_license.max_devices
        );
    END IF;

    IF v_license.revoked_at IS NOT NULL THEN
        RETURN jsonb_build_object(
            'ok', false,
            'status', 'revoked',
            'reason', 'License has been revoked',
            'license_id', v_license.id,
            'device_id', NULL,
            'expires_at', v_license.expires_at,
            'is_lifetime', v_license.expires_at IS NULL,
            'plan', v_license.plan,
            'label', v_license.label,
            'max_devices', v_license.max_devices
        );
    END IF;

    IF v_license.expires_at IS NOT NULL AND v_license.expires_at <= v_now THEN
        RETURN jsonb_build_object(
            'ok', false,
            'status', 'expired',
            'reason', 'License expired',
            'license_id', v_license.id,
            'device_id', NULL,
            'expires_at', v_license.expires_at,
            'is_lifetime', false,
            'plan', v_license.plan,
            'label', v_license.label,
            'max_devices', v_license.max_devices
        );
    END IF;

    IF v_license.max_devices < 1 THEN
        RETURN jsonb_build_object(
            'ok', false,
            'status', 'server_error',
            'reason', 'License max_devices is invalid',
            'license_id', v_license.id,
            'device_id', NULL,
            'expires_at', v_license.expires_at,
            'is_lifetime', v_license.expires_at IS NULL,
            'plan', v_license.plan,
            'label', v_license.label,
            'max_devices', v_license.max_devices
        );
    END IF;

    SELECT *
      INTO v_device
      FROM public.license_devices
     WHERE license_id = v_license.id
       AND machine_id_hash = p_machine_id_hash
     LIMIT 1;

    IF FOUND THEN
        IF v_device.status = 'active' THEN
            UPDATE public.license_devices
               SET last_seen_at = v_now,
                   device_name = COALESCE(NULLIF(p_device_name, ''), device_name),
                   platform = COALESCE(NULLIF(p_platform, ''), platform),
                   app_version = COALESCE(NULLIF(p_app_version, ''), app_version),
                   metadata = COALESCE(metadata, '{}'::jsonb) || COALESCE(p_metadata, '{}'::jsonb),
                   updated_at = v_now
             WHERE id = v_device.id
             RETURNING * INTO v_device;

            UPDATE public.licenses
               SET activated_at = COALESCE(activated_at, v_now),
                   last_seen_at = v_now,
                   updated_at = v_now
             WHERE id = v_license.id
             RETURNING * INTO v_license;

            INSERT INTO public.license_audit_logs (license_id, device_id, event_type, severity, detail)
            VALUES (
                v_license.id,
                v_device.id,
                'activation_success',
                'info',
                jsonb_build_object('reused_device', true, 'platform', p_platform, 'app_version', p_app_version)
            );

            RETURN jsonb_build_object(
                'ok', true,
                'status', 'active',
                'reason', 'reused_device',
                'license_id', v_license.id,
                'device_id', v_device.id,
                'expires_at', v_license.expires_at,
                'is_lifetime', v_license.expires_at IS NULL,
                'plan', v_license.plan,
                'label', v_license.label,
                'max_devices', v_license.max_devices
            );
        END IF;

        RETURN jsonb_build_object(
            'ok', false,
            'status', 'device_revoked',
            'reason', 'This device has been revoked',
            'license_id', v_license.id,
            'device_id', v_device.id,
            'expires_at', v_license.expires_at,
            'is_lifetime', v_license.expires_at IS NULL,
            'plan', v_license.plan,
            'label', v_license.label,
            'max_devices', v_license.max_devices
        );
    END IF;

    SELECT COUNT(*)
      INTO v_active_device_count
      FROM public.license_devices
     WHERE license_id = v_license.id
       AND status = 'active';

    IF v_active_device_count >= v_license.max_devices THEN
        INSERT INTO public.license_audit_logs (license_id, event_type, severity, detail)
        VALUES (
            v_license.id,
            'activation_failed_device_limit',
            'warning',
            jsonb_build_object('max_devices', v_license.max_devices, 'active_device_count', v_active_device_count)
        );

        RETURN jsonb_build_object(
            'ok', false,
            'status', 'already_activated_on_another_device',
            'reason', 'License is already activated on another device',
            'license_id', v_license.id,
            'device_id', NULL,
            'expires_at', v_license.expires_at,
            'is_lifetime', v_license.expires_at IS NULL,
            'plan', v_license.plan,
            'label', v_license.label,
            'max_devices', v_license.max_devices
        );
    END IF;

    INSERT INTO public.license_devices (
        license_id,
        machine_id_hash,
        device_name,
        platform,
        app_version,
        status,
        activated_at,
        last_seen_at,
        metadata
    )
    VALUES (
        v_license.id,
        p_machine_id_hash,
        NULLIF(p_device_name, ''),
        NULLIF(p_platform, ''),
        NULLIF(p_app_version, ''),
        'active',
        v_now,
        v_now,
        COALESCE(p_metadata, '{}'::jsonb)
    )
    RETURNING * INTO v_device;

    UPDATE public.licenses
       SET activated_at = COALESCE(activated_at, v_now),
           last_seen_at = v_now,
           updated_at = v_now
     WHERE id = v_license.id
     RETURNING * INTO v_license;

    INSERT INTO public.license_audit_logs (license_id, device_id, event_type, severity, detail)
    VALUES (
        v_license.id,
        v_device.id,
        'activation_success',
        'info',
        jsonb_build_object('reused_device', false, 'platform', p_platform, 'app_version', p_app_version)
    );

    RETURN jsonb_build_object(
        'ok', true,
        'status', 'active',
        'reason', 'activated',
        'license_id', v_license.id,
        'device_id', v_device.id,
        'expires_at', v_license.expires_at,
        'is_lifetime', v_license.expires_at IS NULL,
        'plan', v_license.plan,
        'label', v_license.label,
        'max_devices', v_license.max_devices
    );
END;
$$;

REVOKE ALL ON FUNCTION public.activate_license_device(text, text, text, text, text, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.activate_license_device(text, text, text, text, text, jsonb) FROM anon;
REVOKE ALL ON FUNCTION public.activate_license_device(text, text, text, text, text, jsonb) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.activate_license_device(text, text, text, text, text, jsonb) TO service_role;
