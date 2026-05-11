// ── Pipeline Jobs ─────────────────────────────────────────────────────────────
import React, { useState } from 'react';
import { Play, RefreshCw, ChevronDown, ChevronRight, AlertCircle } from 'lucide-react';
import { PageHeader, Badge, SlideOver, EmptyState, Skeleton } from '@/components/ui';
import { useJobs, useLaunchPipeline } from '@/lib/hooks';
import { fmtRelative } from '@/lib/utils';
import { useI18n } from '@/lib/i18n';

const DAG_STEPS = [
  { key: 'extract_product_info', label: 'Extract Product', step: 1 },
  { key: 'search_tiktok',        label: 'Search TikTok',  step: 2 },
  { key: 'select_videos',        label: 'Select Videos',  step: 3 },
  { key: 'download_videos',      label: 'Download',       step: 4 },
  { key: 'remake_video',         label: 'Remake Video',   step: 5 },
  { key: 'generate_content',     label: 'Gen Caption',    step: 6 },
  { key: 'generate_comment',     label: 'Gen Comment',    step: 7 },
];

function statusForStep(jobStatus: string, step: number): string {
  if (jobStatus === 'completed') return 'SUCCESS';
  if (jobStatus === 'failed')    return step <= 3 ? 'SUCCESS' : step === 4 ? 'FAILED' : 'CANCELED';
  if (jobStatus === 'running')   return step <= 2 ? 'SUCCESS' : step === 3 ? 'RUNNING' : 'PENDING';
  return 'PENDING';
}

export function PipelineJobs() {
  const { t } = useI18n();
  const { data: jobs = [], isLoading, error, refetch } = useJobs();
  const launch = useLaunchPipeline();
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [showLaunch, setShowLaunch] = useState(false);
  const [productUrl, setProductUrl] = useState('');
  const [topN, setTopN] = useState(5);
  const [launchError, setLaunchError] = useState('');

  function toggle(id: string) {
    setExpanded(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function handleLaunch() {
    setLaunchError('');
    try {
      await launch.mutateAsync({ product_url: productUrl, top_n: topN });
      setShowLaunch(false);
      setProductUrl('');
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
            <button className="btn btn-primary btn-sm" onClick={() => setShowLaunch(true)}>
              <Play size={13} /> {t('job.launch')}
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
          <AlertCircle size={32} style={{ marginBottom: '0.75rem', opacity: 0.7 }} />
          <div style={{ fontSize: '0.875rem', marginBottom: '1rem' }}>{(error as Error).message}</div>
          <button className="btn btn-secondary btn-sm" onClick={() => refetch()}>
            <RefreshCw size={13} /> {t('job.retry')}
          </button>
        </div>
      ) : jobs.length === 0 ? (
        <EmptyState icon="🚀" message={t('job.no_data')} />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {jobs.map((job: any) => {
            const isExpanded = expanded.has(job.id);
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
                      {job.created_at ? fmtRelative(new Date(job.created_at).getTime() / 1000) : (t('common.dash') ?? '—')}
                    </div>
                    {job.error_type && <div style={{ fontSize: '0.7rem', color: 'var(--danger)' }}>{job.error_type}</div>}
                  </div>
                  {job.status === 'failed' && (
                    <button className="btn btn-ghost btn-icon btn-sm" title={t('job.retry_tooltip')} onClick={e => {
                      e.stopPropagation();
                      if (job.input?.product_url) {
                        setProductUrl(job.input.product_url);
                        setShowLaunch(true);
                      }
                    }}>
                      <RefreshCw size={12} />
                    </button>
                  )}
                </div>

                {/* DAG Steps */}
                {isExpanded && (
                  <div style={{ borderTop: '1px solid var(--border)', padding: '0.875rem 1.25rem', background: 'var(--surface-2)' }}>
                    {job.error_message && (
                      <div style={{ padding: '0.5rem 0.75rem', background: 'var(--danger-muted)', border: '1px solid var(--danger)', borderRadius: 'var(--radius-sm)', marginBottom: '0.75rem', fontSize: '0.8125rem', color: 'var(--danger)' }}>
                        {job.error_message}
                      </div>
                    )}
                    <div style={{ display: 'flex', gap: '0.375rem', alignItems: 'center', flexWrap: 'wrap' }}>
                      {DAG_STEPS.map((s, i) => {
                        const stepStatus = statusForStep(job.status, s.step);
                        const color = stepStatus === 'SUCCESS'  ? 'var(--success)'
                          : stepStatus === 'RUNNING'  ? 'var(--info)'
                          : stepStatus === 'FAILED'   ? 'var(--danger)'
                          : stepStatus === 'CANCELED' ? 'var(--text-muted)'
                          : 'var(--border)';
                        return (
                          <React.Fragment key={s.key}>
                            <div style={{ textAlign: 'center' }}>
                              <div style={{ width: 32, height: 32, borderRadius: '50%', background: color, display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto', fontSize: '0.7rem', color: stepStatus === 'PENDING' || stepStatus === 'CANCELED' ? 'var(--text-muted)' : '#fff', border: `2px solid ${color}`, fontWeight: 700 }}>
                                {s.step}
                              </div>
                              <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', marginTop: '0.2rem', maxWidth: 50 }}>{s.label}</div>
                            </div>
                            {i < DAG_STEPS.length - 1 && (
                              <div style={{ flex: 1, height: 2, background: stepStatus === 'SUCCESS' ? 'var(--success)' : 'var(--border)', minWidth: 12, maxWidth: 40, marginBottom: '1rem' }} />
                            )}
                          </React.Fragment>
                        );
                      })}
                    </div>
                    <div style={{ marginTop: '0.75rem', display: 'flex', gap: '1rem', flexWrap: 'wrap', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                      {job.input?.product_url && <span>{t('job.lbl_url')} {job.input.product_url}</span>}
                      {job.metadata?.top_n !== undefined && <span>{t('job.lbl_topn')} {job.metadata.top_n}</span>}
                      {job.metadata?.min_views !== undefined && <span>{t('job.lbl_min_views')} {(job.metadata.min_views / 1000).toFixed(0)}K</span>}
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
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>{t('job.lbl_prod_url')}</label>
            <input
              className="input"
              placeholder="https://shopee.vn/product/..."
              value={productUrl}
              onChange={e => setProductUrl(e.target.value)}
            />
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>
              {t('job.lbl_topn_val')} {topN}
            </label>
            <input
              type="range" min={1} max={20} step={1} value={topN}
              onChange={e => setTopN(+e.target.value)}
              style={{ width: '100%', accentColor: 'var(--primary)' }}
            />
          </div>
          <div style={{ padding: '0.75rem', background: 'var(--surface-2)', borderRadius: 'var(--radius-sm)', fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>
            {t('job.dag_desc')}
          </div>
          {launchError && (
            <div style={{ padding: '0.5rem 0.75rem', background: 'var(--danger-muted)', border: '1px solid var(--danger)', borderRadius: 'var(--radius-sm)', fontSize: '0.8125rem', color: 'var(--danger)' }}>
              {launchError}
            </div>
          )}
          <button
            className="btn btn-primary"
            disabled={!productUrl.trim() || launch.isPending}
            onClick={handleLaunch}
          >
            <Play size={14} /> {launch.isPending ? t('job.launching') : t('job.launch')}
          </button>
        </div>
      </SlideOver>
    </div>
  );
}
