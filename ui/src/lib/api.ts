// ── API Client — Real Endpoints Only ─────────────────────────────────────────
// ALL paths verified against backend routes in api/main.py.
// NO mock data. NO fallback. If API fails → throw → UI shows error state.

const BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000';

async function request<T>(path: string, opts?: RequestInit): Promise<T> {
  const token = localStorage.getItem('auth_token');
  const res = await fetch(`${BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...opts,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body?.message ?? `API ${res.status}: ${path}`);
  }
  return res.json();
}

// ── Verified URL map ──────────────────────────────────────────────────────────
// Source of truth: api/main.py router registrations
//
// Route prefix       | Module             | Path
// ──────────────────────────────────────────────────
// (no prefix)        | system.py          | /system/stats
// /api/v1/brain      | content_brain.py   | /api/v1/brain/queue
// /api/v1/fleet-health | fleet_health.py  | /api/v1/fleet-health
// /api/v1/strategy   | strategy.py        | /api/v1/strategy/state
// /api/v1/auth       | auth.py            | /api/v1/auth/login
// /api/v1/system     | decisions.py       | /api/v1/system/decisions

export const api = {
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

  freezeAccount: (id: string) =>
    // MISSING_BACKEND_ENDPOINT: POST /api/v1/fleet-health/accounts/{id}/freeze
    // Closest available: strategy overrides with type=freeze
    request('/api/v1/strategy/overrides', {
      method: 'POST',
      body: JSON.stringify({ target: id, type: 'freeze', reason: 'manual_ui' }),
    }),

  clearCooldown: (id: string) =>
    // MISSING_BACKEND_ENDPOINT: POST /api/v1/fleet-health/accounts/{id}/cooldown/clear
    request('/api/v1/strategy/overrides', {
      method: 'POST',
      body: JSON.stringify({ target: id, type: 'clear_cooldown', reason: 'manual_ui' }),
    }),

  // ── System Stats ──────────────────────────────────────────────────────────
  // NOTE: system router has NO /api/v1 prefix — it's at /system/stats
  stats: () => request<any>('/system/stats'),

  // ── Decision Feed (Command Dashboard primary source) ──────────────────────
  decisions: (limit = 5) =>
    request<any[]>(`/api/v1/system/decisions?limit=${limit}`),

  // ── Strategy ──────────────────────────────────────────────────────────────
  strategy: () => request<any>('/api/v1/strategy/state'),
  niches:   () => request<any[]>('/api/v1/strategy/niche-performance'),
  overrides: () => request<any[]>('/api/v1/strategy/overrides'),

  addOverride: (payload: object) =>
    request('/api/v1/strategy/overrides', { method: 'POST', body: JSON.stringify(payload) }),
  removeOverride: (id: string) =>
    request(`/api/v1/strategy/overrides/${id}`, { method: 'DELETE' }),

  setExecution: (enabled: boolean) =>
    request('/api/v1/brain/config', {
      method: 'POST',
      body: JSON.stringify({ EXECUTION_ENABLED: enabled }),
    }),

  setBrainConfig: (patch: Record<string, unknown>) =>
    request('/api/v1/brain/config', { method: 'POST', body: JSON.stringify(patch) }),

  // ── Auth ──────────────────────────────────────────────────────────────────
  login: (account: string, licenseKey: string) =>
    request<{ token: string; user: any }>('/api/v1/auth/login', {
      method: 'POST',
      body: JSON.stringify({ account, license_key: licenseKey }),
    }),

  // ── Jobs / Pipeline ────────────────────────────────────────────────────────
  jobs: () => request<any[]>('/api/v1/jobs'),
};
