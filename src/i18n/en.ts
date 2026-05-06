/** English translation strings. */
const en = {
  // ── Navigation ─────────────────────────────────────────────────────────────
  nav: {
    dashboard: "Dashboard",
    jobs: "Jobs",
    tasks: "Tasks",
    create: "Create",
    system: "System",
    settings: "Settings",
  },

  // ── App layout ──────────────────────────────────────────────────────────────
  layout: {
    appName: "Automation",
    appSubtitle: "Ecosystem",
    tagline: "Postgres-driven orchestration dashboard",
    healthy: "Healthy",
    needsAttention: "Needs attention",
  },

  // ── Dashboard ───────────────────────────────────────────────────────────────
  dashboard: {
    title: "Dashboard",
    description: "A calm overview of workflows, tasks, and system health.",
    totalJobs: "Total jobs",
    runningTasks: "Running tasks",
    failedTasks: "Failed tasks",
    successRate: "Success rate",
    tasksOverTime: "Tasks over time",
    tasksOverTimeDesc: "Recent task volume from the current API window.",
    successVsFail: "Success vs Fail",
    successVsFailDesc: "Outcome mix across known tasks.",
    recentActivity: "Recent activity",
    recentActivityDesc: "Latest tasks reported by the API.",
    noTaskActivity: "No task activity yet",
    noTaskActivityDesc: "Create a job to see task movement here.",
    noRecentActivity: "No recent activity",
    noRecentActivityDesc: "Tasks will appear here as workers process them.",
  },

  // ── Jobs ────────────────────────────────────────────────────────────────────
  jobs: {
    title: "Jobs",
    description: "Workflow runs created by the orchestration API.",
    createJob: "Create job",
    searchPlaceholder: "Search jobs",
    allStatuses: "All statuses",
    pending: "Pending",
    running: "Running",
    completed: "Completed",
    failed: "Failed",
    newestFirst: "Newest first",
    oldestFirst: "Oldest first",
    sortByName: "Name",
    colJobId: "job_id",
    colWorkflow: "workflow",
    colStatus: "status",
    colCreatedAt: "created_at",
    noJobsFound: "No jobs found",
    noJobsFoundDesc: "Try a different search, filter, or create a new workflow.",
    errorLoad: "Could not load jobs",
  },

  // ── Job Detail ──────────────────────────────────────────────────────────────
  jobDetail: {
    notFound: "Job not found",
    notFoundDesc: "The selected workflow could not be loaded.",
    labelStatus: "Status",
    labelPriority: "Priority",
    labelTasks: "Tasks",
    inputPreview: "Workflow input preview",
    metadata: "Workflow metadata",
    tasksTitle: "Tasks",
    colTaskId: "task_id",
    colTaskType: "task_type",
    colStatus: "status",
    colRetryCount: "retry_count",
    noTasksInJob: "No tasks in this job",
    noTasksInJobDesc: "This workflow does not have task records yet.",
    errorLoad: "Could not load job",
    jobCreated: "Job {{id}} created {{date}}",
  },

  // ── Tasks ───────────────────────────────────────────────────────────────────
  tasks: {
    title: "Tasks",
    description: "Atomic units of work handled by workers.",
    filterByType: "Filter by task_type",
    filterByAccount: "Filter by account_id",
    autoRefresh: "Auto-refresh",
    colTaskId: "task_id",
    colTaskType: "task_type",
    colStatus: "status",
    colRetryCount: "retry_count",
    colCreatedAt: "created_at",
    colAction: "action",
    retry: "Retry",
    retryDialogTitle: "Retry failed task?",
    retryDialogDesc: "This moves the task back to pending if retry budget is available.",
    retryConfirm: "Retry task",
    noTasksMatch: "No tasks match",
    noTasksMatchDesc: "Try changing filters or creating a new job.",
    retryScheduled: "Task retry scheduled",
    retryScheduledDesc: "The task was moved back into the pending queue.",
    errorLoad: "Could not load tasks",
  },

  // ── Task Detail ─────────────────────────────────────────────────────────────
  taskDetail: {
    title: "Task {{id}}",
    description: "Payload, result, retry state, and execution notes.",
    notFound: "Task not found",
    notFoundDesc: "The selected task could not be loaded.",
    labelStatus: "Status",
    labelTaskType: "Task type",
    labelRetryCount: "Retry count",
    labelJob: "Job",
    tabPayload: "Payload",
    tabResult: "Result",
    tabError: "Error",
    tabExecutions: "Executions",
    executionHistory: "Execution history",
    executionHistoryNote:
      "Execution history is stored in Postgres under task_executions. The current API does not expose a read endpoint for it yet.",
    errorLoad: "Could not load task",
  },

  // ── Create Job ──────────────────────────────────────────────────────────────
  createJob: {
    title: "Create job",
    description: "Build a workflow without writing JSON. Advanced mode is available when needed.",
    builderTitle: "Workflow builder",
    builderDesc: "Choose a task type and fill in the fields. The system will create the task payload for you.",
    labelWorkflowName: "Workflow name",
    workflowNamePlaceholder: "Daily content workflow",
    labelTaskType: "Task type",
    taskTypeAi: "AI text",
    taskTypeBrowser: "Open webpage",
    taskTypeMedia: "Process media file",
    fieldAiPrimary: "Prompt",
    fieldAiPlaceholder: "Write a short launch caption",
    fieldBrowserPrimary: "URL",
    fieldBrowserPlaceholder: "https://example.com",
    fieldMediaPrimary: "Input path",
    fieldMediaPlaceholder: "D:\\Media\\clip.mp4",
    modeSimple: "Simple",
    modeAdvanced: "Advanced JSON",
    payloadPreview: "Payload preview:",
    createJobBtn: "Create job",
    jobCreated: "Job created",
    jobCreatedDesc: "Workers can now pick up this workflow.",
    errorCreate: "Could not create job",
  },

  // ── System Health ───────────────────────────────────────────────────────────
  system: {
    title: "System health",
    description: "A quick read on database connectivity, workers, and scheduler state.",
    database: "Database",
    worker: "Worker",
    scheduler: "Scheduler",
    statusOk: "OK",
    statusCheck: "Check",
    dbConnectionOk: "Connection check passed",
    workerActive: "Worker runtime is active",
    workerInactive: "No worker reported by this API process",
    schedulerActive: "Auto dispatch loop is active",
    schedulerInactive: "Scheduler is not running",
    heartbeatDb: "Checked every 5 seconds",
    heartbeatWorker: "Worker heartbeat is stored per execution",
    heartbeatScheduler: "Scheduler loop is supervised by the API process",
    errorLoad: "Could not load system health",
  },

  // ── Settings ────────────────────────────────────────────────────────────────
  settings: {
    title: "Settings",
    description: "Policy Rules for safer, more predictable account behavior.",
    apiKeysTitle: "AI Provider API Keys",
    apiKeysDesc: "Keys are stored locally in your browser and never sent to any server.",
    keyGemini: "Gemini API Key (Primary)",
    keyHuggingFace: "HuggingFace API Key (Fallback)",
    keyOpenAI: "OpenAI API Key (extract_product_info)",
    saveApiKeys: "Save API Keys",
    apiKeysSaved: "API Keys saved",
    apiKeysSavedDesc: "Your keys are stored locally and will be used by the AI router.",
    apiKeysNote: "🔒 Keys are saved in localStorage — they never leave this device.",
    showKey: "Show key",
    hideKey: "Hide key",
    postingPolicy: "Posting policy",
    postingPolicyDesc: "Choose a preset, then fine-tune limits.",
    presetSafe: "Safe",
    presetMedium: "Medium",
    presetAggressive: "Aggressive",
    perDay: "{{n}}/day",
    minDelay: "{{n}}m delay",
    labelActionType: "Action type",
    actionPost: "Post",
    actionComment: "Comment",
    actionFollow: "Follow",
    actionMessage: "Message",
    labelPostsPerDay: "Posts per day",
    labelDelay: "Delay between actions",
    suffixMin: "min",
    savePolicy: "Save policy draft",
    policySaved: "Policy draft saved",
    policySavedDesc: "The policy_rules payload is ready for backend persistence.",
    languageLabel: "Language",
  },

  // ── Language Switcher ───────────────────────────────────────────────────────
  language: {
    vi: "Tiếng Việt",
    en: "English",
  },
} as const;

/** Recursive type: same key structure as `en`, but values can be any string. */
export type TranslationShape = typeof en;
export type TranslationKeys = {
  [K in keyof TranslationShape]: {
    [P in keyof TranslationShape[K]]: string;
  };
};

export default en;
