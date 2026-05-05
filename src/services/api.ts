import axios from "axios";
import type { CreateJobPayload, DeepHealth, Job, JobDetail, PolicyRuleDraft, SystemStats, Task, TaskStatus } from "@/types/api";

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
  async getStats() {
    const { data } = await apiClient.get<SystemStats>("/system/stats");
    return data;
  },
  async getDeepHealth() {
    const { data } = await apiClient.get<DeepHealth>("/system/health/deep");
    return data;
  },
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
