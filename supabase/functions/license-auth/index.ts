import { createClient } from "npm:@supabase/supabase-js@2";

type Json = Record<string, unknown>;

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

const OFFLINE_GRACE_DAYS = Number(Deno.env.get("LICENSE_OFFLINE_GRACE_DAYS") ?? "7");
const REFRESH_TOKEN_DAYS = Number(Deno.env.get("LICENSE_REFRESH_TOKEN_DAYS") ?? "365");

function json(status: number, body: Json): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}

function serviceKey(): string {
  const secretKeys = Deno.env.get("SUPABASE_SECRET_KEYS");
  if (secretKeys) {
    const parsed = JSON.parse(secretKeys) as Record<string, string>;
    const first = Object.values(parsed)[0];
    if (first) return first;
  }
  const legacy = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (legacy) return legacy;
  throw new Error("Missing Supabase secret/service-role key for Edge Function");
}

const supabase = createClient(Deno.env.get("SUPABASE_URL")!, serviceKey(), {
  auth: { persistSession: false },
});

function normalizeLicenseKey(value: unknown): string {
  return String(value ?? "").trim().toUpperCase();
}

function futureIso(days: number): string {
  const ms = Date.now() + days * 24 * 60 * 60 * 1000;
  return new Date(ms).toISOString();
}

async function sha256Hex(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function randomToken(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function readBody(req: Request): Promise<Json> {
  try {
    return (await req.json()) as Json;
  } catch {
    return {};
  }
}

async function findLicense(licenseKey: string) {
  // Query by plain license_key (always present, UNIQUE indexed).
  // license_key_hash lookup is skipped because the column may not exist
  // in all DB deployments; license_key is sufficient and equally safe.
  const { data, error } = await supabase
    .from("licenses")
    .select("*")
    .eq("license_key", licenseKey)
    .maybeSingle();
  if (error) throw error;
  return data;
}

function assertLicenseActive(license: any): Response | null {
  if (!license) return json(401, { error: "invalid_license", message: "License key không hợp lệ." });
  if (license.is_active === false || license.status === "revoked") {
    return json(403, { error: "license_revoked", message: "License key đã bị thu hồi." });
  }
  if (license.flagged) {
    return json(403, { error: "license_flagged", message: license.flagged_reason ?? "License bị khóa." });
  }
  if (license.expires_at && new Date(license.expires_at).getTime() < Date.now()) {
    return json(403, { error: "license_expired", message: "License key đã hết hạn." });
  }
  return null;
}

async function ensureDevice(license: any, machineId: string, account: string, appVersion?: string) {
  const machineHash = await sha256Hex(machineId);
  const { data: devices, error } = await supabase
    .from("license_devices")
    .select("*")
    .eq("license_id", license.id)
    .eq("status", "active");
  if (error) throw error;

  const existing = (devices ?? []).find((device: any) => device.machine_id_hash === machineHash);
  if (existing) return existing;
  if ((devices ?? []).length > 0) {
    return null;
  }

  const { data: inserted, error: insertError } = await supabase
    .from("license_devices")
    .insert({
      license_id: license.id,
      machine_id_hash: machineHash,
      account,
      app_version: appVersion ?? null,
      status: "active",
      activated_at: new Date().toISOString(),
      last_seen_at: new Date().toISOString(),
    })
    .select("*")
    .single();
  if (insertError) throw insertError;

  await supabase
    .from("licenses")
    .update({
      machine_id_hash: machineHash,
      machine_id: machineHash,
      activated_at: license.activated_at ?? new Date().toISOString(),
      last_seen_at: new Date().toISOString(),
    })
    .eq("id", license.id);

  return inserted;
}

async function createSession(license: any, device: any) {
  const refreshToken = randomToken();
  const tokenHash = await sha256Hex(refreshToken);
  const refreshExpiresAt = futureIso(REFRESH_TOKEN_DAYS);
  const { error } = await supabase
    .from("license_sessions")
    .insert({
      license_id: license.id,
      device_id: device.id,
      refresh_token_hash: tokenHash,
      expires_at: refreshExpiresAt,
      last_used_at: new Date().toISOString(),
    });
  if (error) throw error;
  return { refreshToken, refreshExpiresAt };
}

async function responsePayload(license: any, device: any, refreshToken: string, refreshExpiresAt: string): Promise<Json> {
  const { data: configs } = await supabase
    .from("app_config")
    .select("key,value")
    .eq("is_public", true);
  const appConfig: Record<string, unknown> = {};
  for (const row of configs ?? []) appConfig[row.key] = row.value;

  return {
    license_key: license.license_key ?? `license:${license.id}`,
    license_key_preview: license.license_key_preview ?? null,
    activation_id: device.id,
    account: device.account ?? license.account ?? "",
    role: license.role ?? "operator",
    max_accounts: license.max_accounts ?? 10,
    expires_at: license.expires_at,
    refresh_token: refreshToken,
    refresh_expires_at: refreshExpiresAt,
    offline_grace_until: futureIso(OFFLINE_GRACE_DAYS),
    app_config: appConfig,
  };
}

async function activate(req: Request) {
  const body = await readBody(req);
  const account = String(body.account ?? "").trim().replace(/^@/, "");
  const licenseKey = normalizeLicenseKey(body.license_key);
  const machineId = String(body.machine_id ?? "").trim();
  if (!account || !licenseKey || !machineId) {
    return json(400, { error: "bad_request", message: "Missing account, license_key, or machine_id." });
  }

  const license = await findLicense(licenseKey);
  const licenseError = assertLicenseActive(license);
  if (licenseError) return licenseError;

  const device = await ensureDevice(license, machineId, account, String(body.app_version ?? ""));
  if (!device) {
    await supabase.from("license_events").insert({
      license_id: license.id,
      event_type: "machine_mismatch",
      detail: { account },
    });
    return json(401, {
      error: "machine_mismatch",
      message: "License key này đã được kích hoạt trên thiết bị khác.",
    });
  }

  const session = await createSession(license, device);
  await supabase.from("license_events").insert({
    license_id: license.id,
    device_id: device.id,
    event_type: "activate",
    detail: { account, app_version: body.app_version ?? null },
  });
  return json(200, await responsePayload(license, device, session.refreshToken, session.refreshExpiresAt));
}

async function refreshSession(req: Request) {
  const body = await readBody(req);
  const refreshToken = String(body.refresh_token ?? "");
  const machineId = String(body.machine_id ?? "").trim();
  if (!refreshToken || !machineId) {
    return json(400, { error: "bad_request", message: "Missing refresh_token or machine_id." });
  }

  const tokenHash = await sha256Hex(refreshToken);
  const { data: session, error } = await supabase
    .from("license_sessions")
    .select("*")
    .eq("refresh_token_hash", tokenHash)
    .is("revoked_at", null)
    .maybeSingle();
  if (error) throw error;
  if (!session || new Date(session.expires_at).getTime() < Date.now()) {
    return json(401, { error: "invalid_refresh", message: "Phiên license đã hết hạn." });
  }

  const [{ data: license }, { data: device }] = await Promise.all([
    supabase.from("licenses").select("*").eq("id", session.license_id).maybeSingle(),
    supabase.from("license_devices").select("*").eq("id", session.device_id).maybeSingle(),
  ]);
  const licenseError = assertLicenseActive(license);
  if (licenseError) return licenseError;

  const machineHash = await sha256Hex(machineId);
  if (!device || device.status !== "active" || device.machine_id_hash !== machineHash) {
    return json(401, { error: "machine_mismatch", message: "Thiết bị không khớp với license." });
  }

  await supabase
    .from("license_sessions")
    .update({ revoked_at: new Date().toISOString(), revoke_reason: "rotated" })
    .eq("id", session.id);
  await supabase
    .from("license_devices")
    .update({ last_seen_at: new Date().toISOString(), app_version: body.app_version ?? device.app_version })
    .eq("id", device.id);
  await supabase
    .from("licenses")
    .update({ last_seen_at: new Date().toISOString() })
    .eq("id", license.id);

  const next = await createSession(license, device);
  return json(200, await responsePayload(license, device, next.refreshToken, next.refreshExpiresAt));
}

async function heartbeat(req: Request) {
  const body = await readBody(req);
  const refreshToken = String(body.refresh_token ?? "");
  const machineId = String(body.machine_id ?? "").trim();
  if (!refreshToken || !machineId) {
    return json(400, { error: "bad_request", message: "Missing refresh_token or machine_id." });
  }
  const tokenHash = await sha256Hex(refreshToken);
  const { data: session, error } = await supabase
    .from("license_sessions")
    .select("*")
    .eq("refresh_token_hash", tokenHash)
    .is("revoked_at", null)
    .maybeSingle();
  if (error) throw error;
  if (!session || new Date(session.expires_at).getTime() < Date.now()) {
    return json(401, { error: "invalid_refresh", message: "Phiên license đã hết hạn." });
  }

  const [{ data: license }, { data: device }] = await Promise.all([
    supabase.from("licenses").select("*").eq("id", session.license_id).maybeSingle(),
    supabase.from("license_devices").select("*").eq("id", session.device_id).maybeSingle(),
  ]);
  const licenseError = assertLicenseActive(license);
  if (licenseError) return licenseError;

  if (!device || device.machine_id_hash !== await sha256Hex(machineId)) {
    return json(401, { error: "machine_mismatch", message: "Thiết bị không khớp với license." });
  }
  await supabase
    .from("license_devices")
    .update({ last_seen_at: new Date().toISOString(), app_version: body.app_version ?? device.app_version })
    .eq("id", device.id);
  await supabase.from("licenses").update({ last_seen_at: new Date().toISOString() }).eq("id", license.id);
  return json(200, { ok: true, offline_grace_until: futureIso(OFFLINE_GRACE_DAYS) });
}

async function logout(req: Request) {
  const body = await readBody(req);
  const refreshToken = String(body.refresh_token ?? "");
  if (refreshToken) {
    await supabase
      .from("license_sessions")
      .update({ revoked_at: new Date().toISOString(), revoke_reason: "logout" })
      .eq("refresh_token_hash", await sha256Hex(refreshToken));
  }
  return json(200, { logged_out: true });
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS_HEADERS });
  if (req.method !== "POST") return json(405, { error: "method_not_allowed" });

  try {
    const path = new URL(req.url).pathname;
    if (path.endsWith("/activate")) return await activate(req);
    if (path.endsWith("/refresh")) return await refreshSession(req);
    if (path.endsWith("/heartbeat")) return await heartbeat(req);
    if (path.endsWith("/logout")) return await logout(req);
    return json(404, { error: "not_found" });
  } catch (error) {
    console.error(error);
    return json(500, { error: "internal_error", message: "License authority failed." });
  }
});
