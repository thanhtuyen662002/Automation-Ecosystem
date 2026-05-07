import axios from "axios";
import type {
  Account,
  Artifact,
  ArtifactStatus,
  CreateJobPayload,
  DeepHealth,
  Job,
  JobDetail,
  PaginatedResponse,
  PolicyRule,
  PolicyRuleDraft,
  SystemStats,
  Task,
  TaskStatus,
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
};
