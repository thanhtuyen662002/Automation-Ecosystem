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

// ── Brain primitive types ──────────────────────────────────────────────────────

export type BrainIntent         = "BROWSE" | "UPLOAD" | "IDLE";
export type BrainInteractionLevel = "low" | "medium" | "high";
export type BrainRiskLevel      = "low" | "medium" | "high";
export type BrainOperatingMode  = "SAFE" | "NORMAL" | "AGGRESSIVE";

export type PolicyRuleDraft = {
  preset: PolicyPreset;
  action_type: string;
  posts_per_day: number;
  delay_minutes: number;
};

// ── New resource types ────────────────────────────────────────────────────────

export type AccountStatus = "healthy" | "limited" | "banned";

export type Account = {
  id: string;
  platform: string;
  account_handle: string;
  status: AccountStatus;
  proxy_url: string | null;
  metadata: Record<string, unknown>;
  // Session fields
  session_valid: boolean;
  last_login_at: string | null;
  user_agent: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type SessionStatus = {
  account_id: string;
  session_valid: boolean;
  has_cookies: boolean;
  last_login_at: string | null;
  user_agent: string | null;
};

export type ArtifactStatus = "pending" | "approved" | "rejected";

export type Artifact = {
  id: string;
  job_id: string | null;
  task_id: string | null;
  artifact_type: string;
  status: ArtifactStatus;
  storage_uri: string;
  mime_type: string | null;
  size_bytes: number | null;
  checksum: string | null;
  metadata: Record<string, unknown>;
  created_at: string | null;
};

export type PolicyRule = {
  id: string;
  account_id: string | null;
  platform: string | null;
  action_type: string;
  rule_name: string;
  enabled: boolean;
  cooldown_seconds: number;
  max_actions: number | null;
  window_seconds: number | null;
  created_at: string | null;
  updated_at: string | null;
};

export type PaginatedResponse<T> = { items: T[] };

export type ValidActionType = "publish_tiktok" | "publish_youtube" | "publish_facebook";

// ── Account Brain ─────────────────────────────────────────────────────────────

export type SessionHistoryEntry = {
  ts: number;
  intent: string;
  duration_min: number;
  uploaded: boolean;
  anomalies: string[];
  engagement_score: number;
  trust_after: number;
  mode: BrainOperatingMode;
};

export type AccountBrainState = {
  account_id: string;
  fatigue_level: number;
  trust_score: number;
  activity_streak_days: number;
  content_ready: boolean;
  intent_override: BrainIntent | null;
  mode_override: BrainOperatingMode | null;
  preferred_hour_start: number;
  preferred_hour_end: number;
  active_window: string;
  last_active_at: number | null;
  last_upload_at: number | null;
  minutes_since_active: number | null;
  minutes_since_upload: number | null;
  recent_actions: string[];
  session_history: SessionHistoryEntry[];
  consecutive_anomalies: number;
  uploads_suspended: boolean;
  uploads_suspended_until: number | null;
  // Derived
  risk_level: BrainRiskLevel;
  operating_mode: BrainOperatingMode;
  current_intent: BrainIntent;
  intent_reason: string;
  session_duration_min: number;
  interaction_level: BrainInteractionLevel;
  allowed_actions: string[];
  delay_multiplier: number;
};

export type UpdateStrategyPayload = {
  captcha_hit?: boolean;
  action_blocked?: boolean;
  soft_ban_detected?: boolean;
  low_engagement?: boolean;
  upload_failed?: boolean;
  engagement_score?: number;
  session_duration_min?: number;
  uploaded?: boolean;
  intent?: string;
  // Identity fields
  ip_changed?: boolean;
  fingerprint_changed?: boolean;
  geo_mismatch?: boolean;
  device_mismatch?: boolean;
  identity_risk_score?: number;
};

export type ConsistencyIssue = {
  code: string;
  severity: "WARNING" | "CRITICAL";
  message: string;
  field: string;
};

export type IdentityProfile = {
  account_id: string;
  device_type: "mobile" | "desktop";
  os: string;
  browser: string;
  browser_version: string;
  screen_resolution: string;
  user_agent: string;
  timezone: string;
  locale: string;
  proxy_url: string | null;
  proxy_country: string | null;
  fingerprint_hash: string;
  canvas_noise_seed: number;
  webgl_noise_seed: number;
  created_at: number;
  last_seen_at: number | null;
  is_locked: boolean;
  identity_risk_score: number;
  consistency_issues: ConsistencyIssue[];
  has_critical_issues: boolean;
};

// ── Fleet Health types ────────────────────────────────────────────────────────

export type LifecyclePhase = "WARM_UP" | "RAMP_UP" | "NORMAL" | "COOLDOWN";

export type UploadRateSnapshot = {
  uploads_10min:      number;
  uploads_1h:         number;
  cap_10min:          number;
  cap_1h:             number;
  burst_utilisation:  number;  // 0–1
  hourly_utilisation: number;  // 0–1
};

export type LifecyclePhaseDistribution = {
  WARM_UP:  number;
  RAMP_UP:  number;
  NORMAL:   number;
  COOLDOWN: number;
};

export type FleetSafetyMetrics = {
  total_accounts_tracked: number;
  safe_mode_count:        number;
  high_risk_count:        number;
  cooldown_count:         number;
  warm_up_count:          number;
  suspended_upload_count: number;
  avg_trust_score:        number;
  avg_fatigue_level:      number;
  anomaly_rate:           number;
  upload_rate:            UploadRateSnapshot;
  lifecycle_phases:       LifecyclePhaseDistribution;
  active_sessions:        number;
  active_proxies:         number;
  skip_rate_30min:        number;
  hard_caps:              Record<string, number>;
};

export type AccountLifecycleSummary = {
  account_id:               string;
  phase:                    LifecyclePhase;
  sessions_today:           number;
  uploads_today:            number;
  cooldown_remaining_hours: number;
  anomaly_count:            number;
  account_age_days:         number;
  trust_score:              number;
  fatigue_level:            number;
  operating_mode:           BrainOperatingMode;
  risk_level:               BrainRiskLevel;
  uploads_suspended:        boolean;
};

export type FleetHealthResponse = {
  metrics:     FleetSafetyMetrics;
  accounts:    AccountLifecycleSummary[];
  snapshot_ts: string;
};
