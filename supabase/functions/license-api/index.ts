import { createClient } from "@supabase/supabase-js";

type Json = Record<string, unknown>;
type LicenseStatus =
  | "active"
  | "active_offline"
  | "not_activated"
  | "invalid_key"
  | "expired"
  | "suspended"
  | "revoked"
  | "device_revoked"
  | "already_activated_on_another_device"
  | "machine_mismatch"
  | "verification_required"
  | "network_error"
  | "server_error";

type LicenseResponse = {
  ok: boolean;
  licensed: boolean;
  status: LicenseStatus;
  reason: string | null;
  license: Json | null;
  device: Json | null;
  offline_valid_until: string | null;
};

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

const RATE_LIMIT_WINDOW_MS = 60_000;
const RATE_LIMIT_MAX = 40;
const RATE_LIMITS = new Map<string, { count: number; resetAt: number }>();

const supabaseUrl = mustEnv("SUPABASE_URL");
const serviceRoleKey = mustEnv("SUPABASE_SERVICE_ROLE_KEY");
const licenseKeyPepper = mustEnv("LICENSE_KEY_PEPPER");
const machineHashPepper = mustEnv("MACHINE_HASH_PEPPER");

const supabase = createClient(supabaseUrl, serviceRoleKey, {
  auth: { persistSession: false, autoRefreshToken: false },
});

function mustEnv(name: string): string {
  const value = Deno.env.get(name)?.trim();
  if (!value) throw new Error(`Missing required environment variable: ${name}`);
  return value;
}

function json(statusCode: number, body: LicenseResponse): Response {
  return new Response(JSON.stringify(body), {
    status: statusCode,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}

function response(
  status: LicenseStatus,
  reason: string | null,
  options: {
    ok?: boolean;
    licensed?: boolean;
    license?: Json | null;
    device?: Json | null;
    offlineValidUntil?: string | null;
  } = {},
): LicenseResponse {
  return {
    ok: options.ok ?? status === "active",
    licensed: options.licensed ?? status === "active",
    status,
    reason,
    license: options.license ?? null,
    device: options.device ?? null,
    offline_valid_until: options.offlineValidUntil ?? null,
  };
}

async function sha256Hex(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest)).map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function normalizeLicenseKey(value: unknown): string {
  return String(value ?? "").trim().replace(/\s+/g, "").toUpperCase();
}

function licensePrefix(normalized: string): string {
  if (!normalized) return "";
  const parts = normalized.split("-");
  if (parts.length >= 2 && parts[0]) return parts.slice(0, 2).join("-").slice(0, 12);
  return normalized.slice(0, 12);
}

async function licenseKeyHash(normalized: string): Promise<string> {
  return await sha256Hex(`${normalized}${licenseKeyPepper}`);
}

async function machineIdHash(machineFingerprint: string): Promise<string> {
  return await sha256Hex(`${machineFingerprint}${machineHashPepper}`);
}

async function readBody(req: Request): Promise<Json> {
  try {
    const body = await req.json();
    return body && typeof body === "object" && !Array.isArray(body) ? body as Json : {};
  } catch {
    return {};
  }
}

function actionFromRequest(req: Request, body: Json): string {
  const path = new URL(req.url).pathname.split("/").filter(Boolean);
  const last = path[path.length - 1] ?? "";
  if (["activate", "status", "refresh", "change-key"].includes(last)) return last;
  return String(body.action ?? "").trim();
}

function stringValue(body: Json, key: string): string {
  const value = body[key];
  return typeof value === "string" ? value.trim() : "";
}

function optionalString(body: Json, key: string): string | null {
  const value = stringValue(body, key);
  return value || null;
}

function metadataValue(body: Json): Json {
  const metadata = body.metadata;
  if (metadata && typeof metadata === "object" && !Array.isArray(metadata)) {
    return metadata as Json;
  }
  return {};
}

function requestIp(req: Request): string {
  return req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
    req.headers.get("cf-connecting-ip") ||
    "unknown";
}

function rateLimitAllowed(key: string): boolean {
  const now = Date.now();
  const current = RATE_LIMITS.get(key);
  if (!current || current.resetAt <= now) {
    RATE_LIMITS.set(key, { count: 1, resetAt: now + RATE_LIMIT_WINDOW_MS });
    return true;
  }
  if (current.count >= RATE_LIMIT_MAX) return false;
  current.count += 1;
  return true;
}

function statusFromLicenseRow(license: any): LicenseResponse | null {
  if (!license) return response("not_activated", "License record was not found", { ok: true, licensed: false });
  if (license.status === "suspended") return response("suspended", "License is suspended");
  if (license.status === "revoked" || license.revoked_at) return response("revoked", "License has been revoked");
  if (license.status === "expired") return response("expired", "License expired");
  if (license.status !== "active") return response("revoked", "License is not active");
  if (license.expires_at && new Date(license.expires_at).getTime() <= Date.now()) {
    return response("expired", "License expired", { license: licenseObject(license) });
  }
  return null;
}

function licenseObject(row: any): Json {
  return {
    id: row.id,
    label: row.label ?? null,
    plan: row.plan ?? "standard",
    expires_at: row.expires_at ?? null,
    is_lifetime: row.expires_at == null,
    max_devices: row.max_devices ?? null,
  };
}

function deviceObject(row: any, machineHash: string): Json {
  return {
    id: row.id,
    status: row.status,
    last_seen_at: row.last_seen_at ?? null,
    device_name: row.device_name ?? null,
    platform: row.platform ?? null,
    app_version: row.app_version ?? null,
    machine_id_hash: machineHash,
  };
}

async function logLicenseEvent(
  eventType: string,
  options: {
    licenseId?: string | null;
    deviceId?: string | null;
    severity?: "info" | "warning" | "error";
    detail?: Json;
  } = {},
): Promise<void> {
  const detail = sanitizeDetail(options.detail ?? {});
  const { error } = await supabase.from("license_audit_logs").insert({
    license_id: options.licenseId ?? null,
    device_id: options.deviceId ?? null,
    event_type: eventType,
    severity: options.severity ?? "info",
    detail,
  });
  if (error) console.error("license_audit_log_failed", error.code ?? "unknown");
}

function sanitizeDetail(detail: Json): Json {
  const blocked = ["license_key", "machine_fingerprint", "machine_id", "token", "service_role", "pepper", "salt"];
  const clean: Json = {};
  for (const [key, value] of Object.entries(detail)) {
    const lowered = key.toLowerCase();
    if (blocked.some((word) => lowered.includes(word))) {
      clean[key] = "[redacted]";
    } else if (value && typeof value === "object" && !Array.isArray(value)) {
      clean[key] = sanitizeDetail(value as Json);
    } else {
      clean[key] = value;
    }
  }
  return clean;
}

async function activate(req: Request, body: Json): Promise<Response> {
  const normalized = normalizeLicenseKey(body.license_key);
  const machineFingerprint = stringValue(body, "machine_fingerprint");
  if (!normalized || !machineFingerprint) {
    return json(400, response("invalid_key", "license_key and machine_fingerprint are required", { ok: false, licensed: false }));
  }

  const prefix = licensePrefix(normalized);
  const rateKey = `${body.action ?? "activate"}:${requestIp(req)}:${prefix}`;
  if (!rateLimitAllowed(rateKey)) {
    return json(429, response("server_error", "Too many license requests"));
  }

  const hashedLicense = await licenseKeyHash(normalized);
  const hashedMachine = await machineIdHash(machineFingerprint);
  const { data, error } = await supabase.rpc("activate_license_device", {
    p_license_key_hash: hashedLicense,
    p_machine_id_hash: hashedMachine,
    p_device_name: optionalString(body, "device_name"),
    p_platform: optionalString(body, "platform"),
    p_app_version: optionalString(body, "app_version"),
    p_metadata: { ...metadataValue(body), license_key_prefix: prefix },
  });

  if (error) {
    console.error("activate_license_rpc_failed", error.code ?? "unknown");
    return json(500, response("server_error", "License activation failed"));
  }

  const rpc = data as Json;
  const status = String(rpc.status ?? "server_error") as LicenseStatus;
  const ok = Boolean(rpc.ok) && status === "active";
  if (!ok) {
    await logLicenseEvent(activationFailureEvent(status), {
      licenseId: rpc.license_id as string | null | undefined,
      deviceId: rpc.device_id as string | null | undefined,
      severity: "warning",
      detail: { status, license_key_prefix: prefix },
    });
  }
  const license = rpc.license_id
    ? {
      id: rpc.license_id,
      label: rpc.label ?? null,
      plan: rpc.plan ?? "standard",
      expires_at: rpc.expires_at ?? null,
      is_lifetime: Boolean(rpc.is_lifetime),
      max_devices: rpc.max_devices ?? null,
      license_key_prefix: prefix,
    }
    : null;
  const device = rpc.device_id
    ? {
      id: rpc.device_id,
      status: ok ? "active" : null,
      last_seen_at: new Date().toISOString(),
      machine_id_hash: hashedMachine,
    }
    : null;

  return json(200, response(status, String(rpc.reason ?? "") || null, { ok, licensed: ok, license, device }));
}

function activationFailureEvent(status: LicenseStatus): string {
  if (status === "invalid_key") return "activation_failed_invalid_key";
  if (status === "expired") return "activation_failed_expired";
  if (status === "revoked") return "activation_failed_revoked";
  if (status === "suspended") return "activation_failed_suspended";
  if (status === "device_revoked") return "activation_failed_device_revoked";
  if (status === "already_activated_on_another_device") return "activation_failed_device_limit";
  return "activation_failed";
}

async function status(body: Json, eventScope = "status"): Promise<Response> {
  const licenseId = stringValue(body, "license_id");
  const deviceId = stringValue(body, "device_id");
  const machineFingerprint = stringValue(body, "machine_fingerprint");
  if (!licenseId || !deviceId || !machineFingerprint) {
    return json(400, response("not_activated", "license_id, device_id, and machine_fingerprint are required", {
      ok: true,
      licensed: false,
    }));
  }

  const hashedMachine = await machineIdHash(machineFingerprint);
  const [{ data: license, error: licenseError }, { data: device, error: deviceError }] = await Promise.all([
    supabase.from("licenses").select("id,label,plan,status,expires_at,revoked_at,max_devices,last_seen_at").eq("id", licenseId).maybeSingle(),
    supabase.from("license_devices").select("id,license_id,status,machine_id_hash,last_seen_at,revoked_at,device_name,platform,app_version").eq("id", deviceId).maybeSingle(),
  ]);

  if (licenseError || deviceError) {
    console.error("license_status_query_failed", licenseError?.code ?? deviceError?.code ?? "unknown");
    return json(500, response("server_error", "License verification failed"));
  }

  const licenseProblem = statusFromLicenseRow(license);
  if (licenseProblem) {
    await logLicenseEvent(statusFailureEvent(eventScope), {
      licenseId,
      deviceId,
      severity: "warning",
      detail: { status: licenseProblem.status },
    });
    licenseProblem.license = license ? licenseObject(license) : null;
    return json(200, licenseProblem);
  }

  if (!device || device.license_id && device.license_id !== licenseId) {
    await logLicenseEvent(statusFailureEvent(eventScope), {
      licenseId,
      deviceId,
      severity: "warning",
      detail: { status: "device_not_found" },
    });
    return json(200, response("device_revoked", "License device was not found", { license: licenseObject(license) }));
  }

  const currentDevice = device as any;
  if (currentDevice.machine_id_hash !== hashedMachine) {
    await logLicenseEvent("machine_mismatch", {
      licenseId,
      deviceId,
      severity: "warning",
      detail: { status: "machine_mismatch" },
    });
    return json(200, response("machine_mismatch", "Device binding does not match this machine", {
      license: licenseObject(license),
      device: deviceObject(currentDevice, hashedMachine),
    }));
  }

  if (currentDevice.status !== "active" || currentDevice.revoked_at) {
    await logLicenseEvent("device_revoked", {
      licenseId,
      deviceId,
      severity: "warning",
      detail: { status: currentDevice.status ?? "revoked" },
    });
    return json(200, response("device_revoked", "This device has been revoked", {
      license: licenseObject(license),
      device: deviceObject(currentDevice, hashedMachine),
    }));
  }

  const now = new Date().toISOString();
  await Promise.all([
    supabase.from("license_devices").update({
      last_seen_at: now,
      app_version: optionalString(body, "app_version") ?? currentDevice.app_version ?? null,
      updated_at: now,
    }).eq("id", deviceId),
    supabase.from("licenses").update({ last_seen_at: now, updated_at: now }).eq("id", licenseId),
    logLicenseEvent(eventScope === "refresh" ? "refresh_success" : "status_check_success", { licenseId, deviceId }),
  ]);

  return json(200, response("active", null, {
    ok: true,
    licensed: true,
    license: licenseObject(license),
    device: deviceObject({ ...currentDevice, last_seen_at: now }, hashedMachine),
  }));
}

function statusFailureEvent(eventScope: string): string {
  return eventScope === "refresh" ? "refresh_failed" : "status_check_failed";
}

async function changeKey(req: Request, body: Json): Promise<Response> {
  const result = await activate(req, { ...body, action: "change-key" });
  try {
    const payload = await result.clone().json() as LicenseResponse;
    if (payload.status === "active") {
      await logLicenseEvent("key_replaced", {
        licenseId: payload.license?.id as string | undefined,
        deviceId: payload.device?.id as string | undefined,
        detail: {
          previous_license_id: body.previous_license_id ?? null,
          previous_device_id: body.previous_device_id ?? null,
        },
      });
    }
  } catch {
    // The activation response remains authoritative even if audit parsing fails.
  }
  return result;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS_HEADERS });
  if (req.method !== "POST") {
    return json(405, response("server_error", "Method not allowed"));
  }

  const body = await readBody(req);
  const action = actionFromRequest(req, body);

  try {
    if (action === "activate") return await activate(req, body);
    if (action === "status") return await status(body, "status");
    if (action === "refresh") return await status(body, "refresh");
    if (action === "change-key") return await changeKey(req, body);
    return json(400, response("server_error", "Unsupported license action"));
  } catch (error) {
    console.error("license_api_internal_error", error instanceof Error ? error.name : "unknown");
    return json(500, response("server_error", "License service failed"));
  }
});
