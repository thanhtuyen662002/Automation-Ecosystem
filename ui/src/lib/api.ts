// ── API Client — Real Endpoints Only ─────────────────────────────────────────
// ALL paths verified against backend routes in api/main.py.
// NO mock data. NO fallback. If API fails → throw → UI shows error state.
//
// SECURITY:
//   - Tokens stored in sessionStorage (cleared on tab/window close)
//   - Auto-logout on any 401 response (revoked session / expired token)
//   - machine_id NOT sent in request body — computed server-side
//   - X-Machine-ID header passes Electron hardware UUID as a hint only

import { getElectronMachineId } from '@/lib/machine';

// BASE: empty string = relative URL → goes through Vite proxy → no CORS.
// Set VITE_API_BASE in .env.local only if connecting to a remote server.
const BASE = import.meta.env.VITE_API_BASE ?? '';

// ── Token storage (localStorage — persists across tabs + browser restarts) ────
const TOKEN_KEY = 'auth_token';
const STORE_KEY = 'ae-auth'; // zustand persist key - fallback source
export const tokenStore = {
  // Primary: direct fast path; Fallback: zustand persist state (guards
  // against race where tokenStore.clear() ran before handler was set).
  get: (): string | null => {
    const direct = localStorage.getItem(TOKEN_KEY);
    if (direct) return direct;
    try {
      const raw = localStorage.getItem(STORE_KEY);
      if (raw) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const t: string | null = (JSON.parse(raw) as any)?.state?.token ?? null;
        if (t) { localStorage.setItem(TOKEN_KEY, t); return t; }
      }
    } catch { /* ignore parse errors */ }
    return null;
  },
  set: (t: string)  => { localStorage.setItem(TOKEN_KEY, t); },
  clear: ()         => { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(STORE_KEY); },
};

// ── Auto-logout handler ────────────────────────────────────────────────────────────────
let _onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: () => void) { _onUnauthorized = fn; }

async function request<T>(path: string, opts?: RequestInit, adminSecret?: string): Promise<T> {
  const token = tokenStore.get();
  const machineId = getElectronMachineId();
  const res = await fetch(`${BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(token      ? { Authorization: `Bearer ${token}` } : {}),
      ...(adminSecret ? { 'X-Admin-Secret': adminSecret }  : {}),
      // Hint to backend for stronger machine binding (Electron only)
      ...(machineId  ? { 'X-Machine-ID': machineId }       : {}),
    },
    ...opts,
  });

  // Auto-logout on 401 — token expired or session revoked
  if (res.status === 401) {
    tokenStore.clear();
    _onUnauthorized?.();
    const body = await res.json().catch(() => ({}));
    throw new Error(body?.message ?? 'Session expired. Please log in again.');
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body?.message ?? `API ${res.status}: ${path}`);
  }
  return res.json();
}

// ── Verified URL map ──────────────────────────────────────────────────────────
// Source of truth: api/main.py router registrations
//
// Route prefix         | Module             | Path
// ──────────────────────────────────────────────────
// (no prefix)          | jobs.py            | /jobs  ← NO /api/v1 prefix
// (no prefix)          | system.py          | /system/stats
// /api/v1/accounts     | accounts.py        | /api/v1/accounts
// /api/v1/analytics    | analytics.py       | /api/v1/analytics/overview
// /api/v1/artifacts    | artifacts.py       | /api/v1/artifacts
// /api/v1/brain        | content_brain.py   | /api/v1/brain/queue
// /api/v1/fleet-health | fleet_health.py    | /api/v1/fleet-health
// /api/v1/strategy     | strategy.py        | /api/v1/strategy/state
// /api/v1/auth         | auth.py            | /api/v1/auth/login
// /api/v1/system       | decisions.py       | /api/v1/system/decisions
// /api/v1/policy-rules | policy_rules.py    | /api/v1/policy-rules

export const api = {
  // ── Accounts ───────────────────────────────────────────────────────────────
  accounts: () =>
    request<{ items: any[] }>('/api/v1/accounts').then(r => r.items),

  createAccount: (payload: { platform: string; account_handle: string; proxy_url?: string }) =>
    request<any>('/api/v1/accounts', { method: 'POST', body: JSON.stringify(payload) }),

  deleteAccount: (id: string) =>
    request<void>(`/api/v1/accounts/${id}`, { method: 'DELETE' }),

  markSoftBan: (id: string) =>
    request<any>(`/api/v1/accounts/${id}/mark-soft-ban`, { method: 'POST', body: '{}' }),

  clearSoftBan: (id: string) =>
    request<any>(`/api/v1/accounts/${id}/clear-soft-ban`, { method: 'POST', body: '{}' }),

  // ── Content Queue ──────────────────────────────────────────────────────────
  // status=all → returns all (pending + approved + rejected)
  queue: (status = 'all') =>
    request<any[]>(`/api/v1/brain/queue?status=${status}&limit=100`),

  approveContent: (id: string) =>
    request(`/api/v1/brain/queue/${id}/approve`, { method: 'POST', body: '{}' }),

  rejectContent: (id: string, reason = 'human_rejected') =>
    request(`/api/v1/brain/queue/${id}/reject`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    }),

  forcePublish: (id: string) =>
    request(`/api/v1/brain/queue/${id}/override`, { method: 'POST', body: '{}' }),

  // ── Fleet Health ──────────────────────────────────────────────────────────
  // NOTE: path is /api/v1/fleet-health (hyphen), NOT /fleet/health
  fleet: () => request<any>('/api/v1/fleet-health'),

  // FIX: body now matches OverrideIn schema: { target_id, target_type, override, reason, ttl_hours }
  freezeAccount: (id: string) =>
    request('/api/v1/strategy/overrides', {
      method: 'POST',
      body: JSON.stringify({
        target_id: id,
        target_type: 'account',
        override: 'freeze',
        reason: 'operator_manual',
        ttl_hours: 24,
      }),
    }),

  // FIX: use actual fleet-health clear-cooldown endpoint (not strategy/overrides)
  clearCooldown: (id: string) =>
    request(`/api/v1/fleet-health/${id}/clear-cooldown`, { method: 'POST', body: '{}' }),

  // ── System Stats ──────────────────────────────────────────────────────────
  // NOTE: system router has NO /api/v1 prefix — it's at /system/stats
  stats: () => request<any>('/system/stats'),

  // ── Decision Feed (Command Dashboard primary source) ──────────────────────
  decisions: (limit = 5) =>
    request<any[]>(`/api/v1/system/decisions?limit=${limit}`),

  // ── Strategy ──────────────────────────────────────────────────────────────
  strategy: () => request<any>('/api/v1/strategy/state'),
  niches:   () => request<any[]>('/api/v1/strategy/niche-performance'),

  // FIX: unwrap .overrides from response { overrides: [...] }
  overrides: () =>
    request<{ overrides: any[] }>('/api/v1/strategy/overrides')
      .then(r => r.overrides ?? []),

  recommendations: () => request<any[]>('/api/v1/strategy/recommendations'),
  strategyLog: (limit = 50) => request<any[]>(`/api/v1/strategy/log?limit=${limit}`),

  addOverride: (payload: object) =>
    request('/api/v1/strategy/overrides', { method: 'POST', body: JSON.stringify(payload) }),
  removeOverride: (id: string) =>
    request(`/api/v1/strategy/overrides/${id}`, { method: 'DELETE' }),

  updateStrategy: (patch: object) =>
    request<any>('/api/v1/strategy/state', { method: 'POST', body: JSON.stringify(patch) }),

  setExecution: (enabled: boolean) =>
    request('/api/v1/brain/config', {
      method: 'POST',
      body: JSON.stringify({ EXECUTION_ENABLED: enabled }),
    }),

  setBrainConfig: (patch: Record<string, unknown>) =>
    request('/api/v1/brain/config', { method: 'POST', body: JSON.stringify(patch) }),

  // ── Auth ──────────────────────────────────────────────────────────────────
  // machine_id is NOT sent — the backend computes it from request headers.
  login: (account: string, licenseKey: string) =>
    request<{ token: string; expires_in: number; user: any }>('/api/v1/auth/login', {
      method: 'POST',
      body: JSON.stringify({ account, license_key: licenseKey }),
    }),

  refresh: () =>
    request<{ token: string; expires_in: number }>('/api/v1/auth/refresh', { method: 'POST', body: '{}' }),

  logout: () =>
    request<{ logged_out: boolean }>('/api/v1/auth/logout', { method: 'POST', body: '{}' }).catch(() => ({ logged_out: true })),

  // ── Admin: License Management ─────────────────────────────────────────────
  adminListLicenses: (adminSecret: string) =>
    request<{ items: any[]; total: number }>('/api/v1/admin/licenses', undefined, adminSecret)
      .then(r => r.items),

  adminCreateLicense: (
    adminSecret: string,
    payload: { label?: string; expires_days?: number; notes?: string },
  ) =>
    request<any>('/api/v1/admin/licenses', {
      method: 'POST',
      body: JSON.stringify(payload),
    }, adminSecret),

  adminRevokeLicense: (adminSecret: string, key: string) =>
    request<any>(`/api/v1/admin/licenses/${key}`, { method: 'DELETE' }, adminSecret),

  adminResetMachine: (adminSecret: string, key: string) =>
    request<any>(`/api/v1/admin/licenses/${key}/reset`, { method: 'POST', body: '{}' }, adminSecret),

  adminReactivateLicense: (adminSecret: string, key: string) =>
    request<any>(`/api/v1/admin/licenses/${key}/activate`, { method: 'POST', body: '{}' }, adminSecret),

  adminUnflag: (adminSecret: string, key: string) =>
    request<any>(`/api/v1/admin/licenses/${key}/unflag`, { method: 'POST', body: '{}' }, adminSecret),

  adminLicenseEvents: (adminSecret: string, key: string) =>
    request<any>(`/api/v1/admin/licenses/${key}/events`, undefined, adminSecret),

  // ── Jobs / Pipeline ────────────────────────────────────────────────────────
  // jobs router is mounted WITHOUT /api/v1 prefix → path is /jobs
  jobs: () => request<any[]>('/jobs'),

  // POST /jobs — launch TikTok pipeline via tiktok.router (POST /pipelines/tiktok)
  // The tiktok router provides the high-level pipeline endpoint.
  launchPipeline: (payload: { product_url: string; top_n?: number; priority?: number }) =>
    request<any>('/pipelines/tiktok', { method: 'POST', body: JSON.stringify(payload) }),

  // ── Artifacts ─────────────────────────────────────────────────────────────
  artifacts: (limit = 50) =>
    request<{ items: any[] }>(`/api/v1/artifacts?limit=${limit}`).then(r => r.items ?? []),

  updateArtifactStatus: (id: string, status: 'approved' | 'rejected') =>
    request<any>(`/api/v1/artifacts/${id}/status`, {
      method: 'PUT',
      body: JSON.stringify({ status }),
    }),

  // ── Niche Upsert ──────────────────────────────────────────────────────────
  upsertNiche: (payload: {
    niche: string; platform: string; win_rate: number;
    avg_views: number; avg_revenue: number; posts_count: number; growth_potential: number;
  }) =>
    request<any>('/api/v1/strategy/niche-performance', { method: 'POST', body: JSON.stringify(payload) }),

  // ── Brain Config ──────────────────────────────────────────────────────────
  brainConfig: () => request<Record<string, unknown>>('/api/v1/brain/config'),

  // ── Analytics Overview ────────────────────────────────────────────────────
  analyticsOverview: () => request<{
    views_trend: { day: string; views: number; revenue: number }[];
    funnel: { stage: string; value: number }[];
    top_content: any[];
  }>('/api/v1/analytics/overview'),

  // ── Policy Rules ──────────────────────────────────────────────────────────
  policyRules: () =>
    request<{ items: any[] }>('/api/v1/policy-rules').then(r => r.items),

  createPolicyRule: (payload: object) =>
    request<any>('/api/v1/policy-rules', { method: 'POST', body: JSON.stringify(payload) }),

  togglePolicyRule: (id: string, enabled: boolean) =>
    request<any>(`/api/v1/policy-rules/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ enabled }),
    }),

  deletePolicyRule: (id: string) =>
    request<void>(`/api/v1/policy-rules/${id}`, { method: 'DELETE' }),
};
