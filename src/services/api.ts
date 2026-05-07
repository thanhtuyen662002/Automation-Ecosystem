import axios from "axios";
import type {
  Account,
  AccountBrainState,
  AccountLifecycleSummary,
  Artifact,
  ArtifactStatus,
  BrainIntent,
  BrainOperatingMode,
  ConsistencyIssue,
  CreateJobPayload,
  DeepHealth,
  FleetHealthResponse,
  IdentityProfile,
  Job,
  JobDetail,
  PaginatedResponse,
  PolicyRule,
  PolicyRuleDraft,
  SystemStats,
  Task,
  TaskStatus,
  UpdateStrategyPayload,
} from "@/types/api";

const desktopApiBaseUrl = window.automationDesktop?.apiBaseUrl;
const baseURL = import.meta.env.VITE_API_URL || desktopApiBaseUrl;

if (!baseURL) {
  console.warn("VITE_API_URL is not set. API calls will use the current origin.");
}

export const apiClient = axios.create({
  baseURL: baseURL || window.location.origin,
  timeout: 15000,
  headers: {
    "Content-Type": "application/json",
  },
});

export const api = {
  // ── Jobs ───────────────────────────────────────────────────────────────────
  async getJobs() {
    const { data } = await apiClient.get<Job[]>("/jobs");
    return data;
  },
  async getJob(jobId: string) {
    const { data } = await apiClient.get<JobDetail>(`/jobs/${jobId}`);
    return data;
  },
  async createJob(payload: CreateJobPayload) {
    const { data } = await apiClient.post<JobDetail>("/jobs", payload);
    return data;
  },

  // ── Tasks ──────────────────────────────────────────────────────────────────
  async getTasks(filters?: { status?: TaskStatus | ""; task_type?: string; account_id?: string }) {
    const { data } = await apiClient.get<Task[]>("/tasks", {
      params: {
        status: filters?.status || undefined,
        task_type: filters?.task_type || undefined,
        account_id: filters?.account_id || undefined,
      },
    });
    return data;
  },
  async getTask(taskId: string) {
    const { data } = await apiClient.get<Task>(`/tasks/${taskId}`);
    return data;
  },
  async retryTask(taskId: string) {
    const { data } = await apiClient.post<Task>(`/tasks/${taskId}/retry`);
    return data;
  },

  // ── System ─────────────────────────────────────────────────────────────────
  async getStats() {
    const { data } = await apiClient.get<SystemStats>("/system/stats");
    return data;
  },
  async getDeepHealth() {
    const { data } = await apiClient.get<DeepHealth>("/system/health/deep");
    return data;
  },

  // ── Accounts ───────────────────────────────────────────────────────────────
  async getAccounts(params?: { limit?: number; offset?: number }) {
    const { data } = await apiClient.get<PaginatedResponse<Account>>("/api/v1/accounts", { params });
    return data;
  },
  async createAccount(payload: { platform: string; account_handle: string; proxy_url?: string }) {
    const { data } = await apiClient.post<Account>("/api/v1/accounts", payload);
    return data;
  },
  async deleteAccount(id: string) {
    await apiClient.delete(`/api/v1/accounts/${id}`);
  },
  async updateAccountHealth(id: string, status: string) {
    const { data } = await apiClient.post<Account>(`/api/v1/accounts/${id}/health`, { status });
    return data;
  },
  async connectAccount(id: string) {
    // Triggers Playwright browser window for manual login — long timeout needed
    const { data } = await apiClient.post<Account>(
      `/api/v1/accounts/${id}/connect`,
      {},
      { timeout: 360000 }, // 6 minutes
    );
    return data;
  },
  async getSessionStatus(id: string) {
    const { data } = await apiClient.get<import("@/types/api").SessionStatus>(
      `/api/v1/accounts/${id}/session-status`,
    );
    return data;
  },

  // ── Artifacts ──────────────────────────────────────────────────────────────
  async getArtifacts(params?: { limit?: number; offset?: number }) {
    const { data } = await apiClient.get<PaginatedResponse<Artifact>>("/api/v1/artifacts", { params });
    return data;
  },
  async updateArtifactStatus(id: string, status: ArtifactStatus) {
    const { data } = await apiClient.put<Artifact>(`/api/v1/artifacts/${id}/status`, { status });
    return data;
  },

  // ── Policy Rules ───────────────────────────────────────────────────────────
  async getPolicyRules(params?: { limit?: number; offset?: number }) {
    const { data } = await apiClient.get<PaginatedResponse<PolicyRule>>("/api/v1/policy-rules", { params });
    return data;
  },
  async createPolicyRule(payload: {
    action_type: string;
    rule_name: string;
    max_actions: number;
    window_seconds: number;
    account_id?: string | null;
    platform?: string | null;
    cooldown_seconds?: number;
  }) {
    const { data } = await apiClient.post<PolicyRule>("/api/v1/policy-rules", payload);
    return data;
  },
  async deletePolicyRule(id: string) {
    await apiClient.delete(`/api/v1/policy-rules/${id}`);
  },

  // ── Legacy helper ──────────────────────────────────────────────────────────
  buildPolicyRulePayload(draft: PolicyRuleDraft) {
    return {
      account_id: null,
      platform: null,
      action_type: draft.action_type,
      rule_name: draft.preset.toLowerCase(),
      enabled: true,
      cooldown_seconds: draft.delay_minutes * 60,
      max_actions: draft.posts_per_day,
      window_seconds: 86400,
      config: {
        preset: draft.preset,
        posts_per_day: draft.posts_per_day,
        delay_minutes: draft.delay_minutes,
      },
    };
  },

  // ── Account Brain ──────────────────────────────────────────────────────────
  async getAccountBrainAll() {
    const { data } = await apiClient.get<AccountBrainState[]>("/api/v1/account-brain");
    return data;
  },
  async getAccountBrain(id: string) {
    const { data } = await apiClient.get<AccountBrainState>(`/api/v1/account-brain/${id}`);
    return data;
  },
  async forceIntent(id: string, intent: BrainIntent) {
    const { data } = await apiClient.post<AccountBrainState>(`/api/v1/account-brain/${id}/force-intent`, { intent });
    return data;
  },
  async resetFatigue(id: string) {
    const { data } = await apiClient.post<AccountBrainState>(`/api/v1/account-brain/${id}/reset-fatigue`);
    return data;
  },
  async setContentReady(id: string, ready: boolean) {
    const { data } = await apiClient.post<AccountBrainState>(`/api/v1/account-brain/${id}/content-ready`, { ready });
    return data;
  },
  async setMode(id: string, mode: BrainOperatingMode | null) {
    const { data } = await apiClient.post<AccountBrainState>(`/api/v1/account-brain/${id}/set-mode`, { mode });
    return data;
  },
  async updateStrategy(id: string, payload: UpdateStrategyPayload) {
    const { data } = await apiClient.post<AccountBrainState>(`/api/v1/account-brain/${id}/update-strategy`, payload);
    return data;
  },
  async emergencySafeMode() {
    const { data } = await apiClient.post<{ affected_accounts: string[]; count: number }>("/api/v1/account-brain/emergency-safe-mode");
    return data;
  },
  async clearSafeMode() {
    const { data } = await apiClient.post<{ cleared_accounts: string[]; count: number }>("/api/v1/account-brain/clear-safe-mode");
    return data;
  },
  async getBrainDecisionLog(limit = 50) {
    const { data } = await apiClient.get<Record<string, unknown>[]>("/api/v1/account-brain/log", { params: { limit } });
    return data;
  },

  // ── Identity Manager ────────────────────────────────────────────────────────

  async getIdentities() {
    const { data } = await apiClient.get<IdentityProfile[]>("/api/v1/identity");
    return data;
  },
  async getIdentity(id: string) {
    const { data } = await apiClient.get<IdentityProfile>(`/api/v1/identity/${id}`);
    return data;
  },
  async generateIdentity(id: string, proxyUrl?: string, proxyCountry?: string) {
    const params: Record<string, string> = {};
    if (proxyUrl) params.proxy_url = proxyUrl;
    if (proxyCountry) params.proxy_country = proxyCountry;
    const { data } = await apiClient.post<IdentityProfile>(`/api/v1/identity/${id}/generate`, null, { params });
    return data;
  },
  async regenerateIdentity(id: string) {
    const { data } = await apiClient.post<IdentityProfile>(`/api/v1/identity/${id}/regenerate`);
    return data;
  },
  async lockIdentity(id: string) {
    const { data } = await apiClient.post<IdentityProfile>(`/api/v1/identity/${id}/lock`);
    return data;
  },
  async unlockIdentity(id: string) {
    const { data } = await apiClient.post<IdentityProfile>(`/api/v1/identity/${id}/unlock`);
    return data;
  },
  async updateIdentityProxy(id: string, proxyUrl: string, proxyCountry: string) {
    const { data } = await apiClient.post<IdentityProfile>(`/api/v1/identity/${id}/proxy`, { proxy_url: proxyUrl, proxy_country: proxyCountry });
    return data;
  },
  async validateIdentity(id: string, opts?: { ip_changed?: boolean; current_fingerprint?: string; geo_mismatch?: boolean }) {
    const { data } = await apiClient.post<IdentityProfile & { validation_issues: ConsistencyIssue[]; force_safe_mode: boolean }>(`/api/v1/identity/${id}/validate`, opts ?? {});
    return data;
  },

  // ── Fleet Health ────────────────────────────────────────────────────────────
  async getFleetHealth() {
    const { data } = await apiClient.get<FleetHealthResponse>("/api/v1/fleet-health");
    return data;
  },
  async triggerCooldown(accountId: string, severe = false) {
    const { data } = await apiClient.post<{ account_id: string; phase: string; cooldown_remaining_hours: number; severe: boolean }>(
      `/api/v1/fleet-health/${accountId}/trigger-cooldown`,
      null,
      { params: { severe } }
    );
    return data;
  },
  async clearCooldown(accountId: string) {
    const { data } = await apiClient.post<{ account_id: string; phase: string; anomaly_count: number }>(
      `/api/v1/fleet-health/${accountId}/clear-cooldown`
    );
    return data;
  },
  async getAccountLifecycle(accountId: string) {
    const { data } = await apiClient.get<AccountLifecycleSummary>(`/api/v1/fleet-health/${accountId}/lifecycle`);
    return data;
  },
};
