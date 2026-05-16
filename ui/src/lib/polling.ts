export const ACTIVE_JOB_STATUSES = new Set([
  'pending',
  'ready',
  'running',
  'retry',
  'queued',
  'in_progress',
]);

export const ACTIVE_TASK_STATUSES = new Set([
  'PENDING',
  'READY',
  'RUNNING',
  'RETRY',
]);

const PENDING_ARTIFACT_STATUSES = new Set([
  'pending',
  'review',
  'awaiting_review',
  'waiting_for_approval',
]);

const ACTIVE_QUEUE_STATUSES = new Set([
  'pending',
  'queued',
  'running',
  'waiting',
  'review',
  'awaiting_review',
]);

export const LIVE_STATS_REFETCH_MS = 5_000;
export const IDLE_STATS_REFETCH_MS = 10_000;
export const DASHBOARD_ANALYTICS_REFETCH_MS = 30_000;

function asObject(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

export function hasActiveJob(jobs: unknown): boolean {
  if (!Array.isArray(jobs)) return false;

  return jobs.some((job) => {
    const record = asObject(job);
    if (!record) return false;

    const jobStatus = String(record.status ?? '').toLowerCase();
    if (ACTIVE_JOB_STATUSES.has(jobStatus)) return true;

    const taskStatuses = asObject(record.task_statuses);
    if (!taskStatuses) return false;

    return Object.values(taskStatuses).some((status) =>
      ACTIVE_TASK_STATUSES.has(String(status ?? '').toUpperCase())
    );
  });
}

export function hasPendingArtifact(artifacts: unknown): boolean {
  if (!Array.isArray(artifacts)) return false;

  return artifacts.some((artifact) => {
    const record = asObject(artifact);
    if (!record) return false;

    const status = String(record.status ?? '').toLowerCase();
    return PENDING_ARTIFACT_STATUSES.has(status);
  });
}

export function hasActiveQueueItems(queue: unknown): boolean {
  const items = Array.isArray(queue) ? queue : asObject(queue)?.items;
  if (!Array.isArray(items)) return false;

  return items.some((item) => {
    const record = asObject(item);
    if (!record) return false;

    const status = String(record.status ?? '').toLowerCase();
    return ACTIVE_QUEUE_STATUSES.has(status);
  });
}

export function jobsRefetchInterval(data: unknown): number {
  return hasActiveJob(data) ? 3_000 : 20_000;
}

export function artifactsRefetchInterval(data: unknown): number {
  return hasPendingArtifact(data) ? 5_000 : 30_000;
}

export function queueRefetchInterval(data: unknown): number {
  return hasActiveQueueItems(data) ? 5_000 : 15_000;
}
