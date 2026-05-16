// ── Pipeline Jobs ─────────────────────────────────────────────────────────────
import React, { useState } from 'react';
import { RefreshCw, ChevronDown, ChevronRight } from 'lucide-react';
import { PageHeader, Badge, SlideOver, EmptyState, Skeleton } from '@/components/ui';
import { GlassIcon } from '@/components/Icons';
import { useAccounts, useJobs, useLaunchPipeline } from '@/lib/hooks';
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

// Real per-task status from API (task_key → status string from DB)
// Falls back to job-level heuristic if task_statuses is not populated.
function statusForStep(job: any, stepKey: string, stepIndex: number): string {
  const ts: Record<string, string> = job.task_statuses ?? {};
  if (Object.keys(ts).length > 0 && stepKey in ts) {
    const raw = (ts[stepKey] ?? '').toUpperCase();
    // RETRY means the step ran but is waiting to retry → show as PENDING in UX
    if (raw === 'RETRY' || raw === 'READY') return 'PENDING';
    return raw; // SUCCESS, RUNNING, FAILED, PENDING
  }
  // Fallback: derive from job-level status (pre-task_statuses compatibility)
  const step = stepIndex + 1;
  if (job.status === 'completed') return 'SUCCESS';
  if (job.status === 'failed')    return step <= 3 ? 'SUCCESS' : step === 4 ? 'FAILED' : 'CANCELED';
  if (job.status === 'running')   return step <= 2 ? 'SUCCESS' : step === 3 ? 'RUNNING' : 'PENDING';
  return 'PENDING';
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
  const { data: jobs = [], isLoading, error, refetch } = useJobs();
  const { data: accounts = [] } = useAccounts();
  const launch = useLaunchPipeline();
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [showLaunch, setShowLaunch] = useState(false);
  const [productUrl, setProductUrl] = useState('');
  const [topN, setTopN] = useState(5);
  const [autoPublish, setAutoPublish] = useState(false);
  const [accountId, setAccountId] = useState('');
  const [launchError, setLaunchError] = useState('');
  const browserProvider = (account: any) => String(account.browser_provider ?? account.metadata?.browser_provider ?? '').toLowerCase();
  const searchAccounts = accounts.filter((account: any) =>
    account.platform === 'tiktok' && ['adspower_manual', 'adspower'].includes(browserProvider(account))
  );
  const accountReady = (account: any) =>
    Boolean(account.session_valid) && account.metadata?.manual_login_state === 'connected_by_confirmation';
  const accountDisabled = (account: any) => autoPublish ? !account.can_publish : !accountReady(account);

  function toggle(id: string) {
    setExpanded(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function handleLaunch() {
    setLaunchError('');
    if (!accountId) {
      setLaunchError(t('job.select_account_error'));
      return;
    }
    try {
      await launch.mutateAsync({
        product_url: productUrl,
        top_n: topN,
        auto_publish: autoPublish,
        account_id: accountId,
      });
      setShowLaunch(false);
      setProductUrl('');
      setAutoPublish(false);
      setAccountId('');
    } catch (e: any) {
      setLaunchError(e.message ?? 'Failed to launch pipeline');
    }
  }

  return (
    <div>
      <PageHeader
        title={t('job.title')}
        subtitle={t('job.sub')}
        action={
          <div style={{ display: 'flex', gap: '0.5rem' }}>
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
      ) : jobs.length === 0 ? (
        <EmptyState icon="play-circle" message={t('job.no_data')} />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {jobs.map((job: any) => {
            const isExpanded = expanded.has(job.id);
            const taskStatuses: Record<string, string> = job.task_statuses ?? {};
            const hasTaskStatuses = Object.keys(taskStatuses).length > 0;
            const visibleSteps = hasTaskStatuses
              ? DAG_STEPS.filter(step => step.key in taskStatuses)
              : DAG_STEPS.filter(step => step.key !== 'tiktok_publish' || job.metadata?.auto_publish);
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
                    name={job.status === 'completed' ? 'check-circle' : job.status === 'failed' ? 'cross-circle' : job.status === 'running' ? 'arrows-square-up-down' : 'calendar'}
                    size={18}
                    style={{ flexShrink: 0, opacity: 0.75 }}
                  />
                  <div style={{ flex: 1 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem' }}>
                      <span style={{ fontWeight: 600, fontSize: '0.875rem' }}>
                        {String(job.workflow_name ?? '').replace(/_/g, ' ')}
                      </span>
                      <Badge status={job.status}>{job.status}</Badge>
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
                  {job.status === 'failed' && (
                    <button className="btn btn-ghost btn-icon btn-sm" title={t('job.retry_tooltip')} onClick={e => {
                      e.stopPropagation();
                      if (job.input?.product_url) { setProductUrl(job.input.product_url); setShowLaunch(true); }
                    }}>
                      <RefreshCw size={12} />
                    </button>
                  )}
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
                      {job.metadata?.top_n !== undefined && <span>{t('job.lbl_topn')} {job.metadata.top_n}</span>}
                      {job.metadata?.min_views !== undefined && <span>{t('job.lbl_min_views')} {(job.metadata.min_views / 1000).toFixed(0)}K</span>}
                      {job.metadata?.account_id && <span>{t('job.lbl_publish_account')} {job.metadata.account_id}</span>}
                      {job.started_at && <span>{t('job.lbl_started')} {fmtRelative(new Date(job.started_at).getTime() / 1000)}</span>}
                      {job.completed_at && <span>{t('job.lbl_done')} {fmtRelative(new Date(job.completed_at).getTime() / 1000)}</span>}
                    </div>
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
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>{t('job.lbl_prod_url')}</label>
            <input className="input" placeholder="https://shopee.vn/product/..." value={productUrl} onChange={e => setProductUrl(e.target.value)} />
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>
              {t('job.lbl_topn_val')} {topN}
            </label>
            <input type="range" min={1} max={20} step={1} value={topN} onChange={e => setTopN(+e.target.value)} style={{ width: '100%', accentColor: 'var(--primary)' }} />
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
              {searchAccounts.map((account: any) => (
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
          <button className="btn btn-primary" disabled={!productUrl.trim() || !accountId || launch.isPending} onClick={handleLaunch} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', justifyContent: 'center' }}>
            <GlassIcon name="play-circle" size={15} style={{ filter: 'brightness(0) invert(1)' }} />
            {launch.isPending ? t('job.launching') : t('job.launch')}
          </button>
        </div>
      </SlideOver>
    </div>
  );
}
