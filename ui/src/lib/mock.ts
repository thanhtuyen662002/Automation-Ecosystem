// ── Mock Data Layer ──────────────────────────────────────────────────────────
// Mirrors exact backend contracts from Phase 1 analysis.
// Replace individual exports with real React Query hooks as API is ready.

export const mockSystemStats = {
  total_tasks: 1248,
  running: 12,
  pending: 47,
  failed: 8,
  success: 1181,
};

export const mockStrategyState = {
  target_daily_views: 50000,
  target_daily_revenue: 50.0,
  actual_daily_views: 38200,
  actual_daily_revenue: 41.5,
  performance_ratio: 0.81,
  growth_mode: "balanced",
  threshold_modifier: 1.0,
  exploration_rate: 0.10,
  consecutive_low_cycles: 0,
  max_risk_level: 0.5,
  last_updated: Date.now() / 1000 - 3600,
};

export const mockNichePerformance = [
  { niche: "finance", platform: "tiktok", win_rate: 0.72, avg_views: 42000, avg_revenue: 18.5, posts_count: 142, growth_potential: 0.8, budget_share: 0.38 },
  { niche: "entertainment", platform: "tiktok", win_rate: 0.58, avg_views: 65000, avg_revenue: 8.2, posts_count: 287, growth_potential: 0.6, budget_share: 0.28 },
  { niche: "tech", platform: "tiktok", win_rate: 0.65, avg_views: 28000, avg_revenue: 22.0, posts_count: 98, growth_potential: 0.75, budget_share: 0.22 },
  { niche: "fitness", platform: "tiktok", win_rate: 0.44, avg_views: 15000, avg_revenue: 6.0, posts_count: 54, growth_potential: 0.5, budget_share: 0.12 },
];

export const mockFleetHealth = {
  metrics: {
    total_accounts_tracked: 8,
    safe_mode_count: 1,
    high_risk_count: 1,
    cooldown_count: 2,
    warm_up_count: 1,
    suspended_upload_count: 1,
    avg_trust_score: 0.74,
    avg_fatigue_level: 0.31,
    anomaly_rate: 0.125,
    upload_rate: { uploads_10min: 3, uploads_1h: 14, cap_10min: 10, cap_1h: 30, burst_utilisation: 0.30, hourly_utilisation: 0.47 },
    lifecycle_phases: { WARM_UP: 1, RAMP_UP: 2, NORMAL: 3, COOLDOWN: 2 },
    active_sessions: 5,
    active_proxies: 7,
    skip_rate_30min: 0.18,
    hard_caps: { max_sessions_per_account_per_day: 3, max_uploads_per_account_per_day: 2, fleet_upload_cap_10min: 10, fleet_upload_cap_1h: 30 },
  },
  accounts: [
    { account_id: "acc-001", phase: "NORMAL",   sessions_today: 2, uploads_today: 1, cooldown_remaining_hours: 0,    anomaly_count: 0, account_age_days: 45.2, trust_score: 0.88, fatigue_level: 0.22, operating_mode: "NORMAL", risk_level: "low",    uploads_suspended: false, current_intent: "UPLOAD" },
    { account_id: "acc-002", phase: "COOLDOWN",  sessions_today: 3, uploads_today: 2, cooldown_remaining_hours: 6.5,  anomaly_count: 2, account_age_days: 31.0, trust_score: 0.61, fatigue_level: 0.58, operating_mode: "SAFE",   risk_level: "medium", uploads_suspended: true,  current_intent: "IDLE"   },
    { account_id: "acc-003", phase: "NORMAL",   sessions_today: 1, uploads_today: 1, cooldown_remaining_hours: 0,    anomaly_count: 0, account_age_days: 72.1, trust_score: 0.92, fatigue_level: 0.15, operating_mode: "NORMAL", risk_level: "low",    uploads_suspended: false, current_intent: "BROWSE" },
    { account_id: "acc-004", phase: "WARM_UP",  sessions_today: 3, uploads_today: 0, cooldown_remaining_hours: 0,    anomaly_count: 0, account_age_days: 5.3,  trust_score: 0.45, fatigue_level: 0.10, operating_mode: "NORMAL", risk_level: "low",    uploads_suspended: true,  current_intent: "BROWSE" },
    { account_id: "acc-005", phase: "RAMP_UP",  sessions_today: 2, uploads_today: 1, cooldown_remaining_hours: 0,    anomaly_count: 1, account_age_days: 18.7, trust_score: 0.67, fatigue_level: 0.35, operating_mode: "NORMAL", risk_level: "medium", uploads_suspended: false, current_intent: "UPLOAD" },
    { account_id: "acc-006", phase: "COOLDOWN",  sessions_today: 3, uploads_today: 2, cooldown_remaining_hours: 12.0, anomaly_count: 3, account_age_days: 22.4, trust_score: 0.38, fatigue_level: 0.82, operating_mode: "SAFE",   risk_level: "high",   uploads_suspended: true,  current_intent: "SLEEP"  },
    { account_id: "acc-007", phase: "RAMP_UP",  sessions_today: 1, uploads_today: 1, cooldown_remaining_hours: 0,    anomaly_count: 0, account_age_days: 14.2, trust_score: 0.71, fatigue_level: 0.28, operating_mode: "NORMAL", risk_level: "low",    uploads_suspended: false, current_intent: "BROWSE" },
    { account_id: "acc-008", phase: "NORMAL",   sessions_today: 2, uploads_today: 2, cooldown_remaining_hours: 0,    anomaly_count: 1, account_age_days: 58.9, trust_score: 0.79, fatigue_level: 0.44, operating_mode: "NORMAL", risk_level: "low",    uploads_suspended: false, current_intent: "UPLOAD" },
  ],
  snapshot_ts: new Date().toISOString(),
};

export const mockBrainQueue = [
  {
    content_id: "c-8821", platform: "tiktok", niche: "finance", mode: "reup",
    status: "pending", decision: "publish",
    reason: "trend_surge+high_hook",
    signals: { trend_score: 0.94, match_score: 0.88, novelty_score: 0.82, historical_perf: 0.80, production_cost: 0.15 },
    final_score: 0.87, raw_score: 0.84, expected_value: 22.5,
    ev_range: [18, 27] as [number, number], confidence: 0.78, priority_score: 0.68,
    publish_account: "acc-001",
    hook: "This investing hack made me $500 in a week",
    caption: "The 1% secret that banks don't want you to know 🏦💰 #finance #investing #moneytips",
    risk_flags: [], created_at: Date.now() / 1000 - 120,
  },
  {
    content_id: "c-8820", platform: "tiktok", niche: "tech", mode: "remark",
    status: "pending", decision: "publish",
    reason: "novelty_high+trend_match+duplicate_angle",
    signals: { trend_score: 0.81, match_score: 0.74, novelty_score: 0.42, historical_perf: 0.68, production_cost: 0.22 },
    final_score: 0.79, raw_score: 0.75, expected_value: 18.2,
    ev_range: [12, 24] as [number, number], confidence: 0.71, priority_score: 0.56,
    publish_account: "acc-003",
    hook: "AI just replaced this entire job category",
    caption: "No one is talking about this AI shift 🤖 #tech #ai #futureofwork",
    risk_flags: ["duplicate_angle"], created_at: Date.now() / 1000 - 340,
  },
  {
    content_id: "c-8819", platform: "tiktok", niche: "entertainment", mode: "reup",
    status: "pending", decision: "publish",
    reason: "trend_match+high_views",
    signals: { trend_score: 0.70, match_score: 0.65, novelty_score: 0.75, historical_perf: 0.72, production_cost: 0.10 },
    final_score: 0.71, raw_score: 0.68, expected_value: 9.8,
    ev_range: [7, 13] as [number, number], confidence: 0.65, priority_score: 0.46,
    publish_account: "acc-007",
    hook: "Wait for the ending... you won't believe it",
    caption: "POV: reality hits different at 2am 😂 #viral #fyp #entertainment",
    risk_flags: [], created_at: Date.now() / 1000 - 600,
  },
  {
    content_id: "c-8818", platform: "facebook", niche: "fitness", mode: "generate",
    status: "approved", decision: "publish",
    reason: "manual_override",
    signals: { trend_score: 0.55, match_score: 0.62, novelty_score: 0.68, historical_perf: 0.55, production_cost: 0.45 },
    final_score: 0.63, raw_score: 0.59, expected_value: 7.4,
    ev_range: [5, 10] as [number, number], confidence: 0.60, priority_score: 0.38,
    publish_account: "acc-006",
    hook: "30-day transformation: no gym needed",
    caption: "Home workout that actually works 💪 #fitness #workout #transformation",
    risk_flags: [], approved_by: "human", created_at: Date.now() / 1000 - 1800,
  },
];


export const mockInsights = {
  top_hooks: {
    finance: ["This investing hack made me $500", "The 1% secret bankers hide", "Why your savings account is losing money"],
    tech: ["AI just replaced this job", "The tool replacing programmers", "ChatGPT can't do this yet"],
    entertainment: ["Wait for the ending", "POV: nobody tells you this", "The plot twist we didn't see coming"],
    fitness: ["30-day no-gym transformation", "The exercise doctors do daily", "Why you're not losing weight"],
  },
  queue_summary: [
    { status: "pending", cnt: 3, avg_score: 0.79, total_ev: 50.5 },
    { status: "approved", cnt: 1, avg_score: 0.63, total_ev: 7.4 },
    { status: "rejected", cnt: 12, avg_score: 0.24, total_ev: 0 },
  ],
  timing: {
    tiktok_finance: { best_hours: [9, 12, 19], avg_engagement: 0.048 },
    tiktok_entertainment: { best_hours: [20, 21, 22], avg_engagement: 0.062 },
    tiktok_tech: { best_hours: [10, 14, 18], avg_engagement: 0.041 },
  },
};

export const mockAccounts = [
  { id: "acc-001", platform: "tiktok", account_handle: "@finance_alpha", status: "healthy", proxy_url: "http://proxy-vn-01:8080", session_valid: true, last_login_at: "2026-05-10T08:00:00Z", risk_score: 0.12, soft_ban_detected: false, warmup_sessions_completed: 12, failed_publish_count: 0, captcha_hit_count: 0, created_at: "2026-03-26T00:00:00Z", updated_at: "2026-05-10T08:00:00Z" },
  { id: "acc-002", platform: "tiktok", account_handle: "@money_moves_vn", status: "limited", proxy_url: "http://proxy-vn-02:8080", session_valid: true, last_login_at: "2026-05-09T14:22:00Z", risk_score: 0.51, soft_ban_detected: false, warmup_sessions_completed: 8, failed_publish_count: 2, captcha_hit_count: 1, created_at: "2026-04-09T00:00:00Z", updated_at: "2026-05-09T14:22:00Z" },
  { id: "acc-003", platform: "tiktok", account_handle: "@techtalk_pro", status: "healthy", proxy_url: "http://proxy-sg-01:8080", session_valid: true, last_login_at: "2026-05-10T09:15:00Z", risk_score: 0.08, soft_ban_detected: false, warmup_sessions_completed: 18, failed_publish_count: 0, captcha_hit_count: 0, created_at: "2026-03-01T00:00:00Z", updated_at: "2026-05-10T09:15:00Z" },
  { id: "acc-004", platform: "tiktok", account_handle: "@newaccount_2026", status: "healthy", proxy_url: null, session_valid: false, last_login_at: null, risk_score: 0.0, soft_ban_detected: false, warmup_sessions_completed: 0, failed_publish_count: 0, captcha_hit_count: 0, created_at: "2026-05-05T00:00:00Z", updated_at: "2026-05-05T00:00:00Z" },
  { id: "acc-006", platform: "facebook", account_handle: "fb.finance.page", status: "limited", proxy_url: "http://proxy-us-01:8080", session_valid: true, last_login_at: "2026-05-08T11:00:00Z", risk_score: 0.78, soft_ban_detected: true, warmup_sessions_completed: 5, failed_publish_count: 4, captcha_hit_count: 3, created_at: "2026-04-09T00:00:00Z", updated_at: "2026-05-08T11:00:00Z" },
];

export const mockJobs = [
  { id: "job-001", job_key: "tiktok_finance_001", workflow_name: "tiktok_content_pipeline", status: "completed", priority: 1, started_at: "2026-05-10T08:05:00Z", completed_at: "2026-05-10T08:12:00Z", created_at: "2026-05-10T08:05:00Z", updated_at: "2026-05-10T08:12:00Z", metadata: { pipeline: "tiktok", top_n: 5, min_views: 10000 }, input: { product_url: "https://shopee.vn/product/123" } },
  { id: "job-002", job_key: "tiktok_tech_002", workflow_name: "tiktok_content_pipeline", status: "running", priority: 0, started_at: "2026-05-10T09:30:00Z", completed_at: null, created_at: "2026-05-10T09:30:00Z", updated_at: "2026-05-10T09:35:00Z", metadata: { pipeline: "tiktok", top_n: 3, min_views: 5000 }, input: { product_url: "https://amazon.com/dp/B09X" } },
  { id: "job-003", job_key: "tiktok_finance_003", workflow_name: "tiktok_content_pipeline", status: "failed", priority: 2, started_at: "2026-05-10T07:00:00Z", completed_at: "2026-05-10T07:08:00Z", created_at: "2026-05-10T07:00:00Z", updated_at: "2026-05-10T07:08:00Z", error_type: "VideoDownloadError", error_message: "No qualifying videos found", metadata: { pipeline: "tiktok", top_n: 5, min_views: 15000 }, input: { product_url: "https://shopee.vn/product/456" } },
];

export const mockArtifacts = [
  { id: "art-001", job_id: "job-001", task_id: "task-005", artifact_type: "video", status: "pending", storage_uri: "/media_output/job-001/remixed_final.mp4", mime_type: "video/mp4", size_bytes: 18450123, checksum: "sha256:abc123", metadata: { duration_sec: 45, resolution: "1080x1920" }, created_at: "2026-05-10T08:11:00Z" },
  { id: "art-002", job_id: "job-001", task_id: "task-006", artifact_type: "metadata", status: "approved", storage_uri: "/media_output/job-001/content.json", mime_type: "application/json", size_bytes: 2048, checksum: null, metadata: { niche: "finance" }, created_at: "2026-05-10T08:11:30Z" },
];

export const mockOverrides = [
  { target_id: "acc-006", target_type: "account", override: "freeze", reason: "high risk score + soft ban", ttl_hours: 24, created_at: Date.now() / 1000 - 3600, expires_at: Date.now() / 1000 + 82800, active: 1 },
  { target_id: "finance", target_type: "niche",   override: "boost",  reason: "win_rate > 0.70, capital shift",   ttl_hours: 48, created_at: Date.now() / 1000 - 7200, expires_at: Date.now() / 1000 + 36000, active: 1 },
];

export const mockRecommendations = [
  { type: "spawn", message: "Consider adding 2 more accounts to finance niche (win_rate=0.72, utilization high)", priority: "high" },
  { type: "niche_action", message: "fitness niche underperforming (win_rate=0.44) — consider reducing budget share", priority: "medium" },
  { type: "exploration", message: "Top-3 niches hold 88% of budget — diversity boost recommended", priority: "low" },
];

export const mockBrainConfig = {
  EXECUTION_ENABLED: true,
  AUTO_APPROVE: false,
  MAX_POSTS_PER_DAY: 5,
  EXPLORATION_RATE: 0.10,
  COST_LIMIT: 1.00,
  MIN_SCORE: 0.26,
};

export const mockStrategyLog = [
  { id: 1, event: "state_updated", data: { growth_mode: "balanced" }, created_at: Date.now() / 1000 - 3600 },
  { id: 2, event: "override_added", data: { target_id: "acc-006", override: "freeze" }, created_at: Date.now() / 1000 - 7200 },
  { id: 3, event: "account_auto_boosted", data: { account_id: "acc-001", score: 0.88 }, created_at: Date.now() / 1000 - 10800 },
  { id: 4, event: "account_auto_frozen", data: { account_id: "acc-006", score: 0.22 }, created_at: Date.now() / 1000 - 14400 },
];

export const mockVideoMetrics = [
  { video_id: "v-001", views: 128000, likes: 9200, comments: 412, shares: 1840, watch_time: 28.4, retention_rate: 0.63, hook_text: "This investing hack made me $500", performance_score: 0.89, keyword: "investing", product_type: "finance_app" },
  { video_id: "v-002", views: 84000, likes: 5600, comments: 218, shares: 920, watch_time: 22.1, retention_rate: 0.49, hook_text: "AI just replaced this entire job", performance_score: 0.74, keyword: "ai_tools", product_type: "tech_course" },
  { video_id: "v-003", views: 62000, likes: 4100, comments: 189, shares: 670, watch_time: 18.5, retention_rate: 0.41, hook_text: "30-day transformation", performance_score: 0.61, keyword: "fitness", product_type: "supplement" },
];

export const mockPolicyRules = [
  { id: "rule-001", account_id: null, platform: "tiktok", action_type: "publish_tiktok", rule_name: "daily_upload_cap", enabled: true, max_actions: 2, window_seconds: 86400, cooldown_seconds: 0, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" },
  { id: "rule-002", account_id: null, platform: "tiktok", action_type: "publish_tiktok", rule_name: "hourly_cap", enabled: true, max_actions: 1, window_seconds: 3600, cooldown_seconds: 1800, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" },
  { id: "rule-003", account_id: null, platform: "facebook", action_type: "publish_facebook", rule_name: "daily_upload_cap", enabled: true, max_actions: 3, window_seconds: 86400, cooldown_seconds: 0, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" },
];

export const mockAccountScores = [
  { account_id: "acc-001", engagement_score: 0.88, conversion_score: 0.74, trust_score: 0.88, total_score: 0.84 },
  { account_id: "acc-002", engagement_score: 0.61, conversion_score: 0.45, trust_score: 0.61, total_score: 0.56 },
  { account_id: "acc-003", engagement_score: 0.92, conversion_score: 0.81, trust_score: 0.92, total_score: 0.88 },
  { account_id: "acc-005", engagement_score: 0.67, conversion_score: 0.55, trust_score: 0.67, total_score: 0.63 },
  { account_id: "acc-006", engagement_score: 0.38, conversion_score: 0.22, trust_score: 0.38, total_score: 0.33 },
  { account_id: "acc-007", engagement_score: 0.71, conversion_score: 0.62, trust_score: 0.71, total_score: 0.68 },
  { account_id: "acc-008", engagement_score: 0.79, conversion_score: 0.70, trust_score: 0.79, total_score: 0.76 },
];

// ── Chart data (moved from page files for single source of truth) ─────────────
export const mockViewsTrend = [
  { day: 'Mon', views: 28000, revenue: 31 },
  { day: 'Tue', views: 34000, revenue: 38 },
  { day: 'Wed', views: 41000, revenue: 44 },
  { day: 'Thu', views: 38000, revenue: 40 },
  { day: 'Fri', views: 52000, revenue: 55 },
  { day: 'Sat', views: 47000, revenue: 50 },
  { day: 'Sun', views: 38200, revenue: 41.5 },
];

export const mockFunnelData = [
  { stage: 'Views',  value: 38200 },
  { stage: 'Clicks', value: 4100 },
  { stage: 'Leads',  value: 820 },
  { stage: 'Sales',  value: 164 },
];

// Alias — mockFleetHealth.accounts is the single source of truth
export const mockFleetHealthAccounts = mockFleetHealth.accounts;
