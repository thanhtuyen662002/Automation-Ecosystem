// ── Pipeline Jobs ─────────────────────────────────────────────────────────────
import React, { useState } from 'react';
import { Camera, ExternalLink, RefreshCw, ChevronDown, ChevronRight, Upload, X, Trash2 } from 'lucide-react';
import { PageHeader, Badge, SlideOver, EmptyState, Skeleton, ConfirmDialog } from '@/components/ui';
import { GlassIcon } from '@/components/Icons';
import {
  useAccounts,
  useDeepHealth,
  useDeleteJob,
  useJobs,
  useLaunchPipeline,
  useMobileTikTokStatus,
  useOpenMobileTikTok,
  useScreenshotMobileTikTok,
  useUploadProductImage,
} from '@/lib/hooks';
import { apiUrl } from '@/lib/api';
import { fmtRelative } from '@/lib/utils';
import { useI18n } from '@/lib/i18n';

const DAG_STEPS = [
  { key: 'tiktok_extract_product_info', label: 'Extract Product', step: 1 },
  { key: 'tiktok_search',               label: 'Search TikTok',   step: 2 },
  { key: 'tiktok_select',               label: 'Select Videos',   step: 3 },
  { key: 'tiktok_download',             label: 'Download',        step: 4 },
  { key: 'tiktok_remake',               label: 'Remake Video',    step: 5 },
  { key: 'tiktok_content',              label: 'Gen Caption',     step: 6 },
  { key: 'tiktok_comment',              label: 'Gen Comment',     step: 7 },
  { key: 'tiktok_publish',              label: 'Publish',         step: 8 },
];

const TOP_N_MIN = 1;
const TOP_N_MAX = 10;
const DEFAULT_TOP_N = 5;

function clampTopN(value: number) {
  if (!Number.isFinite(value)) return TOP_N_MIN;
  return Math.max(TOP_N_MIN, Math.min(TOP_N_MAX, Math.trunc(value)));
}

type PipelineJobMetadata = {
  pipeline?: string;
  top_n?: number;
  min_views?: number;
  auto_publish?: boolean;
  account_id?: string;
  [key: string]: unknown;
};

type PipelineJob = {
  id: string;
  workflow_name?: string;
  status?: string;
  task_statuses?: Record<string, string>;
  task_results?: Record<string, unknown>;
  metadata?: PipelineJobMetadata;
  input?: { product_url?: string; product_image_path?: string; [key: string]: unknown };
  error_type?: string;
  error_message?: string;
  created_at?: string;
  started_at?: string;
  completed_at?: string;
};

type AccountSummary = {
  id: string;
  platform?: string;
  browser_provider?: string | null;
  metadata?: Record<string, unknown> | null;
  session_valid?: unknown;
  can_publish?: boolean;
  display_name?: string | null;
  account_handle?: string | null;
};

type DownloadAttemptSummary = {
  mode?: string;
  ok?: boolean;
  failure_kind?: string;
  message?: string;
  requires_mobile_app?: boolean;
};

type DownloadFailure = {
  url?: string;
  failure_kind?: string;
  message?: string;
  requires_mobile_app?: boolean;
  provider_attempts?: {
    browser_capture?: DownloadAttemptSummary | null;
    yt_dlp?: DownloadAttemptSummary[];
    mobile?: DownloadAttemptSummary | null;
  };
};

type DownloadResult = {
  failed_downloads?: DownloadFailure[];
  download_stats?: {
    downloaded_count?: number;
    app_only_count?: number;
    http_403_count?: number;
    audio_only_count?: number;
    failed_count?: number;
  };
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null ? value as Record<string, unknown> : null;
}

function downloadResultForJob(job: PipelineJob): DownloadResult | null {
  const task = asRecord(job.task_results?.tiktok_download);
  const result = asRecord(task?.result) ?? asRecord(job.task_results?.tiktok_download);
  return result as DownloadResult | null;
}

function attemptLabel(attempt: DownloadAttemptSummary | null | undefined) {
  if (!attempt) return 'not attempted';
  if (attempt.ok) return 'ok';
  return attempt.failure_kind || attempt.message || 'failed';
}

function mobileStatusText(value: unknown) {
  return value ? 'ok' : 'missing';
}

function mobileFlagText(value: unknown) {
  return value ? 'enabled' : 'disabled';
}

function failureRequiresTikTokApp(failure: DownloadFailure) {
  const attempts = failure.provider_attempts ?? {};
  return Boolean(
    failure.requires_mobile_app ||
    failure.failure_kind === 'app_only_gate' ||
    attempts.browser_capture?.requires_mobile_app ||
    attempts.mobile?.requires_mobile_app ||
    (attempts.yt_dlp ?? []).some(attempt => attempt.requires_mobile_app),
  );
}

function failureMessage(failure: DownloadFailure) {
  switch (failure.failure_kind) {
    case 'app_only_gate':
      return 'Video này chỉ xem được trong ứng dụng TikTok. Bật Mobile Fallback hoặc chọn video khác.';
    case 'mobile_fallback_disabled':
      return 'Video app-only cần Android mobile fallback. Bật TIKTOK_MOBILE_FALLBACK_ENABLED=true rồi chạy lại.';
    case 'mobile_device_unavailable':
      return 'Mobile fallback đã bật nhưng không thấy Android emulator/device qua ADB.';
    case 'mobile_verification_required':
      return 'TikTok app đang yêu cầu captcha/checkpoint. Cần xử lý thủ công trên emulator.';
    case 'mobile_login_required':
      return 'TikTok app chưa đăng nhập trên emulator/device.';
    case 'mobile_download_not_available':
      return 'TikTok app đã mở video nhưng không có nút Save/Download hợp lệ hoặc không tạo file mới.';
    case 'mobile_save_no_new_file':
      return 'Đã bấm Save video nhưng emulator không tạo file media mới.';
    case 'mobile_invalid_video_stream':
      return 'File mobile fallback tải về không có video stream hợp lệ.';
    default:
      return failure.message || 'Download failed';
  }
}

function failureHasKind(failure: DownloadFailure, kind: string) {
  const attempts = failure.provider_attempts ?? {};
  return (
    failure.failure_kind === kind ||
    attempts.browser_capture?.failure_kind === kind ||
    attempts.mobile?.failure_kind === kind ||
    (attempts.yt_dlp ?? []).some(attempt => attempt.failure_kind === kind)
  );
}

// Real per-task status from API (task_key → status string from DB)
// Falls back to job-level heuristic if task_statuses is not populated.
function statusForStep(job: PipelineJob, stepKey: string, stepIndex: number): string {
  const ts: Record<string, string> = job.task_statuses ?? {};
  if (Object.keys(ts).length > 0 && stepKey in ts) {
    const raw = (ts[stepKey] ?? '').toUpperCase();
    // RETRY means the step ran but is waiting to retry → show as PENDING in UX
    if (raw === 'RETRY' || raw === 'READY') return 'PENDING';
    return raw; // SUCCESS, RUNNING, FAILED, PENDING
  }
  // Fallback: derive from job-level status (pre-task_statuses compatibility)
  const step = stepIndex + 1;
  const status = job.status ?? '';
  if (status === 'completed') return 'SUCCESS';
  if (status === 'failed')    return step <= 3 ? 'SUCCESS' : step === 4 ? 'FAILED' : 'CANCELED';
  if (status === 'running')   return step <= 2 ? 'SUCCESS' : step === 3 ? 'RUNNING' : 'PENDING';
  return 'PENDING';
}

function hasRunningTask(job: PipelineJob): boolean {
  return Object.values(job.task_statuses ?? {}).some(status => String(status).toUpperCase() === 'RUNNING');
}


// Mini step icon inside the DAG circle
function StepDot({ status, step }: { status: string; step: number }) {
  const color = status === 'SUCCESS'  ? 'var(--success)'
    : status === 'RUNNING'  ? 'var(--info)'
    : status === 'FAILED'   ? 'var(--danger)'
    : status === 'CANCELED' ? 'var(--text-muted)'
    : 'var(--border)';
  const textColor = status === 'PENDING' || status === 'CANCELED' ? 'var(--text-muted)' : '#fff';
  return (
    <div style={{ width: 32, height: 32, borderRadius: '50%', background: color, display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto', fontSize: '0.7rem', color: textColor, border: `2px solid ${color}`, fontWeight: 700 }}>
      {step}
    </div>
  );
}

export function PipelineJobs() {
  const { t } = useI18n();
  const { data: jobs = [], isLoading, error, refetch, dataUpdatedAt, isFetching } = useJobs();
  const { data: accounts = [] } = useAccounts();
  const { data: health } = useDeepHealth();
  const { data: mobileStatus, refetch: refetchMobileStatus, isFetching: isMobileStatusFetching } = useMobileTikTokStatus();
  const openMobileTikTok = useOpenMobileTikTok();
  const screenshotMobileTikTok = useScreenshotMobileTikTok();
  const launch = useLaunchPipeline();
  const deleteJob = useDeleteJob();
  const uploadProductImage = useUploadProductImage();
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [confirmDeleteJob, setConfirmDeleteJob] = useState<PipelineJob | null>(null);
  const [deleteError, setDeleteError] = useState('');
  const [showLaunch, setShowLaunch] = useState(false);
  const [productUrl, setProductUrl] = useState('');
  const [productImagePath, setProductImagePath] = useState('');
  const [productImagePreviewUrl, setProductImagePreviewUrl] = useState('');
  const [uploadError, setUploadError] = useState('');
  const [dragActive, setDragActive] = useState(false);
  const [topN, setTopN] = useState(DEFAULT_TOP_N);
  const [autoPublish, setAutoPublish] = useState(false);
  const [accountId, setAccountId] = useState('');
  const [launchError, setLaunchError] = useState('');
  const jobList = jobs as PipelineJob[];
  const accountList = accounts as AccountSummary[];
  const browserProvider = (account: AccountSummary) => {
    const metadataProvider = account.metadata?.browser_provider;
    return String(account.browser_provider ?? (typeof metadataProvider === 'string' ? metadataProvider : '')).toLowerCase();
  };
  const searchAccounts = accountList.filter((account) =>
    account.platform === 'tiktok' && ['adspower_manual', 'adspower'].includes(browserProvider(account))
  );
  const accountReady = (account: AccountSummary) =>
    Boolean(account.session_valid) && account.metadata?.manual_login_state === 'connected_by_confirmation';
  const accountDisabled = (account: AccountSummary) => autoPublish ? !account.can_publish : !accountReady(account);
  const workerWarning = health?.execution?.can_execute_tasks === false || health?.worker?.running === false;
  const providerStatus = mobileStatus ?? health?.mobile_tiktok;

  function toggle(id: string) {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  async function handleProductImageFile(file: File | undefined) {
    if (!file) return;
    setUploadError('');
    if (!file.type.startsWith('image/')) {
      setUploadError('File must be an image');
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      setUploadError('Image must be 10MB or smaller');
      return;
    }
    try {
      const uploaded = await uploadProductImage.mutateAsync(file);
      setProductImagePath(uploaded.path);
      setProductImagePreviewUrl(uploaded.url ? apiUrl(uploaded.url) : URL.createObjectURL(file));
    } catch (e: unknown) {
      setUploadError(e instanceof Error ? e.message : 'Failed to upload image');
    }
  }

  function clearProductImage() {
    setProductImagePath('');
    setProductImagePreviewUrl('');
    setUploadError('');
  }

  async function handleDeleteJob(job: PipelineJob | null) {
    if (!job) return;
    setDeleteError('');
    try {
      await deleteJob.mutateAsync(job.id);
      setExpanded(prev => {
        const next = new Set(prev);
        next.delete(job.id);
        return next;
      });
    } catch (e: unknown) {
      setDeleteError(e instanceof Error ? e.message : 'Failed to delete pipeline');
    }
  }

  async function handleLaunch() {
    setLaunchError('');
    const normalizedTopN = clampTopN(topN);
    const trimmedProductUrl = productUrl.trim();
    const trimmedProductImagePath = productImagePath.trim();
    setTopN(normalizedTopN);
    if (!trimmedProductUrl && !trimmedProductImagePath) {
      setLaunchError(t('job.product_source_error'));
      return;
    }
    if (!accountId) {
      setLaunchError(t('job.select_account_error'));
      return;
    }
    try {
      await launch.mutateAsync({
        ...(trimmedProductUrl ? { product_url: trimmedProductUrl } : {}),
        ...(trimmedProductImagePath ? { product_image_path: trimmedProductImagePath } : {}),
        top_n: normalizedTopN,
        auto_publish: autoPublish,
        account_id: accountId,
      });
      setShowLaunch(false);
      setProductUrl('');
      setProductImagePath('');
      setProductImagePreviewUrl('');
      setUploadError('');
      setAutoPublish(false);
      setAccountId('');
    } catch (e: unknown) {
      setLaunchError(e instanceof Error ? e.message : 'Failed to launch pipeline');
    }
  }

  return (
    <div>
      <PageHeader
        title={t('job.title')}
        subtitle={t('job.sub')}
        action={
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
              {isFetching
                ? t('job.updating')
                : dataUpdatedAt
                  ? `${t('job.updated')} ${fmtRelative(Math.floor(dataUpdatedAt / 1000))}`
                  : null}
            </span>
            <button className="btn btn-ghost btn-sm btn-icon" onClick={() => refetch()} title={t('job.refresh')}>
              <RefreshCw size={13} />
            </button>
            <button className="btn btn-primary btn-sm" onClick={() => setShowLaunch(true)} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <GlassIcon name="play-circle" size={15} style={{ filter: 'brightness(0) invert(1)' }} />
              {t('job.launch')}
            </button>
          </div>
        }
      />

      {workerWarning && (
        <div style={{
          padding: '0.75rem 1rem',
          border: '1px solid var(--warning, var(--border))',
          background: 'var(--surface-2)',
          borderRadius: 'var(--radius-sm)',
          marginBottom: '1rem',
          color: 'var(--text-secondary)',
          fontSize: '0.8125rem',
          display: 'flex',
          gap: '0.5rem',
          alignItems: 'flex-start',
        }}>
          <GlassIcon name="warning" size={14} style={{ flexShrink: 0, marginTop: '0.1rem' }} />
          <span>{t('job.worker_not_running_warning')}</span>
        </div>
      )}

      {deleteError && (
        <div style={{
          padding: '0.75rem 1rem',
          border: '1px solid var(--danger)',
          background: 'var(--danger-muted)',
          borderRadius: 'var(--radius-sm)',
          marginBottom: '1rem',
          color: 'var(--danger)',
          fontSize: '0.8125rem',
          display: 'flex',
          gap: '0.5rem',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}>
          <span>{deleteError}</span>
          <button className="btn btn-ghost btn-icon btn-sm" onClick={() => setDeleteError('')} title="Dismiss">
            <X size={13} />
          </button>
        </div>
      )}

      {isLoading ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {[1, 2, 3].map(i => <div key={i} className="card"><Skeleton height={52} /></div>)}
        </div>
      ) : error ? (
        <div style={{ textAlign: 'center', padding: '3rem 1rem', color: 'var(--danger)' }}>
          <GlassIcon name="warning" size={36} style={{ marginBottom: '0.75rem', filter: 'brightness(0) saturate(100%) invert(26%) sepia(90%) saturate(3000%) hue-rotate(345deg)' }} />
          <div style={{ fontSize: '0.875rem', marginBottom: '1rem' }}>{(error as Error).message}</div>
          <button className="btn btn-secondary btn-sm" onClick={() => refetch()}>
            <RefreshCw size={13} /> {t('job.retry')}
          </button>
        </div>
      ) : jobList.length === 0 ? (
        <EmptyState icon="play-circle" message={t('job.no_data')} />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {jobList.map((job) => {
            const isExpanded = expanded.has(job.id);
            const taskStatuses: Record<string, string> = job.task_statuses ?? {};
            const hasTaskStatuses = Object.keys(taskStatuses).length > 0;
            const visibleSteps = hasTaskStatuses
              ? DAG_STEPS.filter(step => step.key in taskStatuses)
              : DAG_STEPS.filter(step => step.key !== 'tiktok_publish' || job.metadata?.auto_publish);
            const jobStatus = job.status ?? 'pending';
            const deleteDisabled = deleteJob.isPending || hasRunningTask(job);
            const downloadResult = downloadResultForJob(job);
            const downloadStats = downloadResult?.download_stats;
            const failedDownloads = Array.isArray(downloadResult?.failed_downloads) ? downloadResult.failed_downloads : [];
            const hasDisabledAppOnlyVideo = failedDownloads.some((failure) =>
              failureHasKind(failure, 'mobile_fallback_disabled') ||
              (failureRequiresTikTokApp(failure) && providerStatus?.mobile_fallback_enabled === false)
            );
            return (
              <div key={job.id} className="card" style={{ padding: 0, overflow: 'hidden' }}>
                {/* Job Header */}
                <div
                  style={{ display: 'flex', alignItems: 'center', gap: '1rem', padding: '0.875rem 1.25rem', cursor: 'pointer' }}
                  onClick={() => toggle(job.id)}
                >
                  <span style={{ color: 'var(--text-muted)' }}>
                    {isExpanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                  </span>
                  {/* Status icon */}
                  <GlassIcon
                    name={jobStatus === 'completed' ? 'check-circle' : jobStatus === 'failed' ? 'cross-circle' : jobStatus === 'running' ? 'arrows-square-up-down' : 'calendar'}
                    size={18}
                    style={{ flexShrink: 0, opacity: 0.75 }}
                  />
                  <div style={{ flex: 1 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem' }}>
                      <span style={{ fontWeight: 600, fontSize: '0.875rem' }}>
                        {String(job.workflow_name ?? '').replace(/_/g, ' ')}
                      </span>
                      <Badge status={jobStatus}>{jobStatus}</Badge>
                      {job.metadata?.pipeline && <span className="badge badge-muted">{job.metadata.pipeline}</span>}
                    </div>
                    <div className="mono" style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '0.2rem' }}>{job.id}</div>
                  </div>
                  <div style={{ textAlign: 'right', flexShrink: 0 }}>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                      {job.created_at ? fmtRelative(new Date(job.created_at).getTime() / 1000) : '—'}
                    </div>
                    {job.error_type && <div style={{ fontSize: '0.7rem', color: 'var(--danger)' }}>{job.error_type}</div>}
                  </div>
                  {jobStatus === 'failed' && (
                    <button className="btn btn-ghost btn-icon btn-sm" title={t('job.retry_tooltip')} onClick={e => {
                      e.stopPropagation();
                      if (job.input?.product_url || job.input?.product_image_path) {
                        setProductUrl(job.input.product_url ?? '');
                        setProductImagePath(job.input.product_image_path ?? '');
                        setShowLaunch(true);
                      }
                    }}>
                      <RefreshCw size={12} />
                    </button>
                  )}
                  <button
                    className="btn btn-ghost btn-icon btn-sm"
                    title={hasRunningTask(job) ? t('job.delete_blocked_running') : t('job.delete_tooltip')}
                    disabled={deleteDisabled}
                    onClick={e => {
                      e.stopPropagation();
                      setDeleteError('');
                      setConfirmDeleteJob(job);
                    }}
                    style={{ color: deleteDisabled ? 'var(--text-muted)' : 'var(--danger)' }}
                  >
                    <Trash2 size={12} />
                  </button>
                </div>

                {/* DAG Steps */}
                {isExpanded && (
                  <div style={{ borderTop: '1px solid var(--border)', padding: '0.875rem 1.25rem', background: 'var(--surface-2)' }}>
                    {job.error_message && (
                      <div style={{ padding: '0.5rem 0.75rem', background: 'var(--danger-muted)', border: '1px solid var(--danger)', borderRadius: 'var(--radius-sm)', marginBottom: '0.75rem', fontSize: '0.8125rem', color: 'var(--danger)', display: 'flex', alignItems: 'flex-start', gap: '0.4rem' }}>
                        <GlassIcon name="warning" size={14} style={{ marginTop: '0.1rem', flexShrink: 0, filter: 'brightness(0) saturate(100%) invert(26%) sepia(90%) saturate(3000%) hue-rotate(345deg)' }} />
                        {job.error_message}
                      </div>
                    )}
                    <div style={{ display: 'flex', gap: '0.375rem', alignItems: 'center', flexWrap: 'wrap' }}>
                      {visibleSteps.map((s, i) => {
                        const stepStatus = statusForStep(job, s.key, i);
                        const lineColor = stepStatus === 'SUCCESS' ? 'var(--success)' : 'var(--border)';
                        return (
                          <React.Fragment key={s.key}>
                            <div style={{ textAlign: 'center' }}>
                              <StepDot status={stepStatus} step={s.step} />
                              <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', marginTop: '0.2rem', maxWidth: 50 }}>{s.label}</div>
                            </div>
                            {i < visibleSteps.length - 1 && (
                              <div style={{ flex: 1, height: 2, background: lineColor, minWidth: 12, maxWidth: 40, marginBottom: '1rem' }} />
                            )}
                          </React.Fragment>
                        );
                      })}
                    </div>
                    <div style={{ marginTop: '0.75rem', display: 'flex', gap: '1rem', flexWrap: 'wrap', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                      {job.input?.product_url && <span>{t('job.lbl_url')} {job.input.product_url}</span>}
                      {job.input?.product_image_path && <span>{t('job.lbl_product_image_path')} {job.input.product_image_path}</span>}
                      {job.metadata?.top_n !== undefined && <span>{t('job.lbl_topn')} {job.metadata.top_n}</span>}
                      {job.metadata?.min_views !== undefined && <span>{t('job.lbl_min_views')} {(job.metadata.min_views / 1000).toFixed(0)}K</span>}
                      {job.metadata?.account_id && <span>{t('job.lbl_publish_account')} {job.metadata.account_id}</span>}
                      {job.started_at && <span>{t('job.lbl_started')} {fmtRelative(new Date(job.started_at).getTime() / 1000)}</span>}
                      {job.completed_at && <span>{t('job.lbl_done')} {fmtRelative(new Date(job.completed_at).getTime() / 1000)}</span>}
                    </div>
                    {(downloadStats || failedDownloads.length > 0) && (
                      <div style={{ marginTop: '0.75rem', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: '0.75rem', background: 'var(--surface)' }}>
                        {downloadStats && (
                          <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', fontSize: '0.72rem', color: 'var(--text-muted)', marginBottom: failedDownloads.length ? '0.625rem' : 0 }}>
                            <span>downloaded_count: {downloadStats.downloaded_count ?? 0}</span>
                            <span>app_only_count: {downloadStats.app_only_count ?? 0}</span>
                            <span>http_403_count: {downloadStats.http_403_count ?? 0}</span>
                            <span>audio_only_count: {downloadStats.audio_only_count ?? 0}</span>
                          </div>
                        )}
                        {failedDownloads.length > 0 && (
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                            {hasDisabledAppOnlyVideo && (
                              <div style={{ border: '1px solid var(--warning, var(--border))', borderRadius: 'var(--radius-sm)', padding: '0.5rem 0.625rem', background: 'var(--surface-2)', color: 'var(--text-secondary)', fontSize: '0.72rem' }}>
                                App-only video detected. Bật <code>TIKTOK_MOBILE_FALLBACK_ENABLED=true</code> để dùng Android emulator/ADB.
                              </div>
                            )}
                            {failedDownloads.map((failure, failureIndex) => {
                              const attempts = failure.provider_attempts ?? {};
                              const ytDlpAttempts = Array.isArray(attempts.yt_dlp) ? attempts.yt_dlp : [];
                              const requiresTikTokApp = failureRequiresTikTokApp(failure);
                              return (
                                <div key={`${failure.url ?? 'download'}-${failureIndex}`} style={{ borderTop: failureIndex ? '1px solid var(--border)' : 0, paddingTop: failureIndex ? '0.5rem' : 0 }}>
                                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flexWrap: 'wrap' }}>
                                    <span style={{ fontSize: '0.74rem', color: 'var(--danger)', fontWeight: 600 }}>
                                      {failure.failure_kind || 'download_failed'}
                                    </span>
                                    {requiresTikTokApp && <span className="badge badge-muted">Requires TikTok App</span>}
                                  </div>
                                  <div style={{ fontSize: '0.72rem', color: 'var(--text-secondary)', marginTop: '0.2rem', overflowWrap: 'anywhere' }}>
                                    {failureMessage(failure)}
                                  </div>
                                  {failure.url && (
                                    <div className="mono" style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: '0.25rem', overflowWrap: 'anywhere' }}>
                                      {failure.url}
                                    </div>
                                  )}
                                  <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginTop: '0.35rem', fontSize: '0.68rem', color: 'var(--text-muted)' }}>
                                    <span>browser_capture: {attemptLabel(attempts.browser_capture)}</span>
                                    <span>yt_dlp: {ytDlpAttempts.length ? ytDlpAttempts.map(attemptLabel).join(', ') : 'not attempted'}</span>
                                    <span>mobile: {attemptLabel(attempts.mobile)}</span>
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Launch Pipeline Slide-over */}
      <SlideOver open={showLaunch} onClose={() => { setShowLaunch(false); setLaunchError(''); }} title={t('job.launch_title')}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', padding: '0.75rem', background: 'var(--surface-2)', borderRadius: 'var(--radius-sm)', marginBottom: '0.25rem' }}>
            <GlassIcon name="arrows-square-up-down" size={24} style={{ opacity: 0.7 }} />
            <div style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>{t('job.dag_desc')}</div>
          </div>
          <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: '0.75rem', background: 'var(--surface)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.75rem', marginBottom: '0.625rem' }}>
              <div>
                <div style={{ fontSize: '0.78rem', fontWeight: 700 }}>Mobile Provider</div>
                <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '0.1rem' }}>
                  {providerStatus?.mobile_provider ?? 'adb'} · {providerStatus?.device_id || providerStatus?.configured_device_id || 'no device'}
                </div>
              </div>
              <span className={`badge ${providerStatus?.ok ? 'badge-success' : 'badge-muted'}`}>
                {providerStatus?.ok ? 'ready' : 'not ready'}
              </span>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '0.4rem', fontSize: '0.7rem', color: 'var(--text-secondary)', marginBottom: '0.625rem' }}>
              <span>Fallback: {mobileFlagText(providerStatus?.mobile_fallback_enabled)}</span>
              <span>ADB: {mobileStatusText(providerStatus?.adb_available)}</span>
              <span>Device ID: {providerStatus?.device_id || providerStatus?.configured_device_id || 'none'}</span>
              <span>Device: {mobileStatusText(providerStatus?.device_available)}</span>
              <span>TikTok app: {mobileStatusText(providerStatus?.tiktok_app_installed)}</span>
              <span>App active: {providerStatus?.tiktok_app_active ? 'active' : 'inactive'}</span>
              <span>Login: {providerStatus?.login_required ? 'required' : 'clear'}</span>
              <span>Verification: {providerStatus?.verification_required ? 'required' : 'clear'}</span>
            </div>
            {providerStatus?.mobile_fallback_enabled === false && (
              <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginBottom: '0.625rem' }}>
                App-only downloads need <code>TIKTOK_MOBILE_FALLBACK_ENABLED=true</code>.
              </div>
            )}
            <div style={{ display: 'flex', gap: '0.45rem', flexWrap: 'wrap' }}>
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => void refetchMobileStatus()} disabled={isMobileStatusFetching}>
                <RefreshCw size={13} /> Test Mobile TikTok
              </button>
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => void openMobileTikTok.mutateAsync()} disabled={openMobileTikTok.isPending}>
                <ExternalLink size={13} /> Open TikTok App
              </button>
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => void screenshotMobileTikTok.mutateAsync()} disabled={screenshotMobileTikTok.isPending}>
                <Camera size={13} /> Screenshot Device
              </button>
            </div>
            {(mobileStatus?.error || openMobileTikTok.error || screenshotMobileTikTok.error) && (
              <div style={{ fontSize: '0.7rem', color: 'var(--danger)', marginTop: '0.5rem' }}>
                {mobileStatus?.error || (openMobileTikTok.error as Error | null)?.message || (screenshotMobileTikTok.error as Error | null)?.message}
              </div>
            )}
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>{t('job.lbl_prod_url')}</label>
            <input className="input" placeholder="https://shopee.vn/product/..." value={productUrl} onChange={e => setProductUrl(e.target.value)} />
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>{t('job.lbl_product_image_path')}</label>
            <label
              onDragEnter={(e) => { e.preventDefault(); setDragActive(true); }}
              onDragOver={(e) => { e.preventDefault(); setDragActive(true); }}
              onDragLeave={(e) => { e.preventDefault(); setDragActive(false); }}
              onDrop={(e) => {
                e.preventDefault();
                setDragActive(false);
                void handleProductImageFile(e.dataTransfer.files?.[0]);
              }}
              style={{
                display: 'flex',
                minHeight: 128,
                alignItems: 'center',
                justifyContent: 'center',
                flexDirection: 'column',
                gap: '0.5rem',
                border: `1px dashed ${dragActive ? 'var(--primary)' : 'var(--border)'}`,
                borderRadius: 'var(--radius-sm)',
                background: dragActive ? 'var(--surface-2)' : 'transparent',
                cursor: 'pointer',
                padding: '0.875rem',
                textAlign: 'center',
              }}
            >
              <input
                type="file"
                accept="image/jpeg,image/png,image/webp"
                style={{ display: 'none' }}
                onChange={(e) => void handleProductImageFile(e.target.files?.[0])}
              />
              {productImagePreviewUrl ? (
                <img src={productImagePreviewUrl} alt="" style={{ maxWidth: '100%', maxHeight: 180, objectFit: 'contain', borderRadius: 'var(--radius-sm)' }} />
              ) : (
                <>
                  <Upload size={20} />
                  <span style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>
                    {uploadProductImage.isPending ? 'Uploading image...' : 'Drop product image or click to upload'}
                  </span>
                </>
              )}
            </label>
            {productImagePath && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginTop: '0.5rem' }}>
                <input className="input" value={productImagePath} onChange={e => setProductImagePath(e.target.value)} style={{ fontSize: '0.72rem' }} />
                <button type="button" className="btn btn-ghost btn-icon btn-sm" onClick={clearProductImage} title="Clear image">
                  <X size={13} />
                </button>
              </div>
            )}
            {!productImagePath && (
              <details style={{ marginTop: '0.5rem' }}>
                <summary style={{ fontSize: '0.7rem', color: 'var(--text-muted)', cursor: 'pointer' }}>Advanced path</summary>
                <input className="input" placeholder="C:\\path\\to\\product.jpg" value={productImagePath} onChange={e => setProductImagePath(e.target.value)} style={{ marginTop: '0.4rem', fontSize: '0.72rem' }} />
              </details>
            )}
            {(uploadError || productImagePath) && (
              <div style={{ fontSize: '0.7rem', color: uploadError ? 'var(--danger)' : 'var(--text-muted)', marginTop: '0.25rem' }}>
                {uploadError || t('job.product_source_helper')}
              </div>
            )}
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>
              {t('job.lbl_topn_val')} {topN}
            </label>
            <input
              type="range"
              min={TOP_N_MIN}
              max={TOP_N_MAX}
              step={1}
              value={topN}
              onChange={e => setTopN(clampTopN(Number(e.target.value)))}
              style={{ width: '100%', accentColor: 'var(--primary)' }}
            />
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
              {t('job.topn_helper')}
            </div>
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>
            <input type="checkbox" checked={autoPublish} onChange={e => setAutoPublish(e.target.checked)} />
            {t('job.auto_publish')}
          </label>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>
              {t('job.lbl_account')}
            </label>
            <select className="input" value={accountId} onChange={e => setAccountId(e.target.value)}>
              <option value="">{t('job.select_account')}</option>
              {searchAccounts.map((account) => (
                <option key={account.id} value={account.id} disabled={accountDisabled(account)}>
                  {account.display_name || account.account_handle} {accountDisabled(account) ? `(${t('job.account_not_ready')})` : ''}
                </option>
              ))}
            </select>
          </div>
          {launchError && (
            <div style={{ padding: '0.5rem 0.75rem', background: 'var(--danger-muted)', border: '1px solid var(--danger)', borderRadius: 'var(--radius-sm)', fontSize: '0.8125rem', color: 'var(--danger)', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <GlassIcon name="warning" size={13} style={{ filter: 'brightness(0) saturate(100%) invert(26%) sepia(90%) saturate(3000%) hue-rotate(345deg)' }} />
              {launchError}
            </div>
          )}
          <button className="btn btn-primary" disabled={(!productUrl.trim() && !productImagePath.trim()) || !accountId || launch.isPending} onClick={handleLaunch} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', justifyContent: 'center' }}>
            <GlassIcon name="play-circle" size={15} style={{ filter: 'brightness(0) invert(1)' }} />
            {launch.isPending ? t('job.launching') : t('job.launch')}
          </button>
        </div>
      </SlideOver>

      <ConfirmDialog
        open={!!confirmDeleteJob}
        onClose={() => setConfirmDeleteJob(null)}
        onConfirm={() => void handleDeleteJob(confirmDeleteJob)}
        title={t('job.delete_title')}
        message={t('job.delete_confirm_msg')
          .replace('{workflow}', String(confirmDeleteJob?.workflow_name ?? 'pipeline'))
          .replace('{id}', confirmDeleteJob?.id ?? '')}
        danger
      />
    </div>
  );
}
