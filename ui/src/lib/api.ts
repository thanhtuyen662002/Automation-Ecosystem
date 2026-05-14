import type { LicenseResponse } from '@/types/license';

const BASE = import.meta.env.VITE_API_BASE ?? '';

function buildHeaders(adminSecret?: string): HeadersInit {
  return {
    'Content-Type': 'application/json',
    ...(adminSecret ? { 'X-Admin-Secret': adminSecret } : {}),
  };
}

async function request<T>(
  path: string,
  opts?: RequestInit,
  adminSecret?: string,
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...opts,
    headers: {
      ...buildHeaders(adminSecret),
      ...(opts?.headers ?? {}),
    },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = body?.message ?? body?.detail ?? body?.reason ?? body?.error;
    const message = typeof detail === 'string'
      ? detail
      : detail
        ? JSON.stringify(detail)
        : `API ${res.status}: ${path}`;
    throw new Error(message);
  }
  return res.json();
}

export const api = {
  licenseStatus: () =>
    request<LicenseResponse>('/api/license/status'),

  activateLicense: (payload: { license_key: string; device_name?: string; app_version?: string }) =>
    request<LicenseResponse>('/api/license/activate', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  refreshLicense: (payload: { app_version?: string } = {}) =>
    request<LicenseResponse>('/api/license/refresh', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  changeLicenseKey: (payload: { license_key: string; app_version?: string }) =>
    request<LicenseResponse>('/api/license/change-key', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  deactivateLocalLicense: () =>
    request<LicenseResponse>('/api/license/deactivate-local', {
      method: 'POST',
      body: '{}',
    }),

  accounts: () =>
    request<{ items: any[] }>('/api/v1/accounts').then(r => r.items),

  createAccount: (payload: { platform: string; account_handle: string; profile_url?: string; proxy_url?: string }) =>
    request<any>('/api/v1/accounts', { method: 'POST', body: JSON.stringify(payload) }),

  updateAccount: (id: string, payload: { account_handle?: string; profile_url?: string | null; proxy_url?: string | null }) =>
    request<any>(`/api/v1/accounts/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),

  deleteAccount: (id: string) =>
    request<void>(`/api/v1/accounts/${id}`, { method: 'DELETE' }),

  markSoftBan: (id: string) =>
    request<any>(`/api/v1/accounts/${id}/mark-soft-ban`, { method: 'POST', body: '{}' }),

  clearSoftBan: (id: string) =>
    request<any>(`/api/v1/accounts/${id}/clear-soft-ban`, { method: 'POST', body: '{}' }),

  connectAccount: (id: string) =>
    request<any>(`/api/v1/accounts/${id}/connect`, { method: 'POST', body: '{}' }),

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

  fleet: () => request<any>('/api/v1/fleet-health'),

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

  clearCooldown: (id: string) =>
    request(`/api/v1/fleet-health/${id}/clear-cooldown`, { method: 'POST', body: '{}' }),

  stats: () => request<any>('/system/stats'),

  decisions: (limit = 5) =>
    request<any[]>(`/api/v1/system/decisions?limit=${limit}`),

  strategy: () => request<any>('/api/v1/strategy/state'),
  niches: () => request<any[]>('/api/v1/strategy/niche-performance'),

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

  jobs: () => request<any[]>('/jobs'),

  launchPipeline: (payload: { product_url: string; top_n?: number; priority?: number; account_id?: string; auto_publish?: boolean }) =>
    request<any>('/pipelines/tiktok', { method: 'POST', body: JSON.stringify(payload) }),

  artifacts: (limit = 50) =>
    request<{ items: any[] }>(`/api/v1/artifacts?limit=${limit}`).then(r => r.items ?? []),

  updateArtifactStatus: (id: string, status: 'approved' | 'rejected') =>
    request<any>(`/api/v1/artifacts/${id}/status`, {
      method: 'PUT',
      body: JSON.stringify({ status }),
    }),

  upsertNiche: (payload: {
    niche: string; platform: string; win_rate: number;
    avg_views: number; avg_revenue: number; posts_count: number; growth_potential: number;
  }) =>
    request<any>('/api/v1/strategy/niche-performance', { method: 'POST', body: JSON.stringify(payload) }),

  brainConfig: () => request<Record<string, unknown>>('/api/v1/brain/config'),

  analyticsOverview: () => request<{
    views_trend: { day: string; views: number; revenue: number }[];
    funnel: { stage: string; value: number }[];
    top_content: any[];
  }>('/api/v1/analytics/overview'),

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
