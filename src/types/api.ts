export type TaskStatus = "PENDING" | "READY" | "RUNNING" | "RETRY" | "SUCCESS" | "FAILED" | "CANCELED";

export type Task = {
  id: string;
  job_id: string;
  task_type: string;
  status: TaskStatus;
  priority: number;
  payload: Record<string, unknown>;
  metadata: Record<string, unknown>;
  retry_count: number;
  max_retries: number;
  next_run_at: string;
  next_retry_at: string | null;
  account_id: string | null;
  action_type: string | null;
  idempotency_key: string | null;
  result: Record<string, unknown> | null;
  error_type: string | null;
  error_message: string | null;
  created_at?: string;
};

export type Job = {
  id: string;
  job_key: string | null;
  workflow_name: string;
  status: string;
  priority: number;
  input: Record<string, unknown>;
  metadata: Record<string, unknown>;
  error_type: string | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type JobDetail = Job & {
  tasks: Task[];
};

export type SystemStats = {
  total_tasks: number;
  running: number;
  pending: number;
  failed: number;
  success: number;
};

export type DeepHealth = {
  status: "ok" | "degraded";
  database: { ok: boolean; error: string | null };
  scheduler: { running: boolean };
  worker: { running: boolean };
};

export type CreateJobPayload = {
  workflow_name: string;
  tasks: Array<{
    task_type: string;
    payload: Record<string, unknown>;
  }>;
};

export type PolicyPreset = "Safe" | "Medium" | "Aggressive";

export type PolicyRuleDraft = {
  preset: PolicyPreset;
  action_type: string;
  posts_per_day: number;
  delay_minutes: number;
};
