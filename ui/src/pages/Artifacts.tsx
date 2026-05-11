// ── Artifacts ─────────────────────────────────────────────────────────────────
import React, { useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { PageHeader, Badge, EmptyState, ConfirmDialog, Skeleton } from '@/components/ui';
import { GlassIcon } from '@/components/Icons';
import { useArtifacts, useUpdateArtifactStatus } from '@/lib/hooks';
import { fmtRelative } from '@/lib/utils';
import { useI18n } from '@/lib/i18n';

export function Artifacts() {
  const { t } = useI18n();
  const [filter, setFilter] = useState<'all' | 'pending' | 'approved' | 'rejected'>('all');
  const [confirmAction, setConfirmAction] = useState<{ id: string; action: 'approved' | 'rejected' } | null>(null);

  const { data: artifacts = [], isLoading, error, refetch } = useArtifacts(100);
  const updateStatus = useUpdateArtifactStatus();

  function handleConfirm() {
    if (!confirmAction) return;
    updateStatus.mutate({ id: confirmAction.id, status: confirmAction.action });
  }

  const filtered = filter === 'all' ? artifacts : artifacts.filter((a: any) => a.status === filter);

  if (isLoading) return (
    <div>
      <PageHeader title={t('art.title')} subtitle={t('art.sub')} />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '1rem' }}>
        {[1, 2, 3, 4].map(i => (
          <div key={i} className="card">
            <Skeleton height={40} borderRadius={8} />
            <div style={{ marginTop: '0.75rem' }}><Skeleton height={14} /><Skeleton height={14} width="70%" /></div>
          </div>
        ))}
      </div>
    </div>
  );

  if (error) return (
    <div>
      <PageHeader title={t('art.title')} subtitle={t('art.sub')} />
      <div style={{ textAlign: 'center', padding: '3rem 1rem', color: 'var(--danger)' }}>
        <GlassIcon name="warning" size={42} style={{ marginBottom: '0.75rem', filter: 'brightness(0) saturate(100%) invert(26%) sepia(90%) saturate(3000%) hue-rotate(345deg)' }} />
        <div style={{ fontSize: '0.875rem', marginBottom: '1rem' }}>{(error as Error).message ?? t('art.fail_load')}</div>
        <button className="btn btn-secondary btn-sm" onClick={() => refetch()}><RefreshCw size={13} /> {t('art.retry')}</button>
      </div>
    </div>
  );

  return (
    <div>
      <PageHeader title={t('art.title')} subtitle={t('art.sub')} />

      {/* Filter tabs */}
      <div style={{ display: 'flex', gap: '0.375rem', marginBottom: '1.25rem', borderBottom: '1px solid var(--border)', paddingBottom: '0.75rem', flexWrap: 'wrap' }}>
        {(['all', 'pending', 'approved', 'rejected'] as const).map(f => (
          <button key={f} onClick={() => setFilter(f)} className={`btn btn-sm ${filter === f ? 'btn-primary' : 'btn-ghost'}`}>
            <GlassIcon name={f === 'approved' ? 'check-circle' : f === 'rejected' ? 'cross-circle' : f === 'pending' ? 'calendar' : 'video'} size={12}
              style={{ filter: filter === f ? 'brightness(0) invert(1)' : 'none', opacity: filter === f ? 1 : 0.6 }}
            />
            {t(`art.filter_${f}`)}
            <span style={{ marginLeft: '0.25rem', opacity: 0.7 }}>
              ({f === 'all' ? artifacts.length : artifacts.filter((a: any) => a.status === f).length})
            </span>
          </button>
        ))}
        <button className="btn btn-ghost btn-sm btn-icon" onClick={() => refetch()} title={t('art.refresh')} style={{ marginLeft: 'auto' }}>
          <RefreshCw size={12} />
        </button>
      </div>

      {filtered.length === 0
        ? <EmptyState icon="video" message={`${t('art.no_data').replace('{filter}', t(`art.filter_${filter}`))}`} />
        : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '1rem' }}>
            {filtered.map((art: any) => (
              <div key={art.id} className="card">
                {/* Artifact header */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', marginBottom: '0.75rem' }}>
                  <div style={{
                    width: 44, height: 44,
                    background: 'rgba(255,255,255,0.60)',
                    border: '1px solid rgba(255,255,255,0.75)',
                    borderRadius: '0.625rem',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    flexShrink: 0, backdropFilter: 'blur(8px)',
                    boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
                  }}>
                    <GlassIcon name={art.artifact_type === 'video' ? 'video' : 'document'} size={22} />
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600, fontSize: '0.8125rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {art.storage_uri?.split('/').pop() ?? art.id}
                    </div>
                    <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{art.artifact_type}</div>
                  </div>
                  <Badge status={art.status}>{art.status}</Badge>
                </div>

                {/* Metadata */}
                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
                  <span>{t('art.lbl_mime')} {art.mime_type ?? '—'}</span>
                  <span>{t('art.lbl_size')} {art.size_bytes ? `${(art.size_bytes / 1024 / 1024).toFixed(1)} MB` : '—'}</span>
                  <span>{t('art.lbl_created')} {art.created_at ? fmtRelative(new Date(art.created_at).getTime() / 1000) : '—'}</span>
                  {art.metadata?.duration_sec && <span>{t('art.lbl_duration')} {art.metadata.duration_sec}s</span>}
                  {art.metadata?.resolution && <span>{t('art.lbl_resolution')} {art.metadata.resolution as string}</span>}
                </div>

                {/* Actions */}
                {art.status === 'pending' && (
                  <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <button className="btn btn-primary btn-sm" style={{ flex: 1 }}
                      disabled={updateStatus.isPending}
                      onClick={() => setConfirmAction({ id: art.id, action: 'approved' })}>
                      <GlassIcon name="check-circle" size={12} style={{ filter: 'brightness(0) invert(1)' }} /> {t('art.act_approve')}
                    </button>
                    <button className="btn btn-danger btn-sm" style={{ flex: 1 }}
                      disabled={updateStatus.isPending}
                      onClick={() => setConfirmAction({ id: art.id, action: 'rejected' })}>
                      <GlassIcon name="cross-circle" size={12} style={{ filter: 'brightness(0) invert(1)' }} /> {t('art.act_reject')}
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )
      }

      <ConfirmDialog
        open={!!confirmAction} onClose={() => setConfirmAction(null)} onConfirm={handleConfirm}
        title={confirmAction?.action === 'approved' ? t('art.confirm_app') : t('art.confirm_rej')}
        message={`${t('art.confirm_msg')} ${confirmAction?.action === 'approved' ? t('art.filter_approved').toLowerCase() : t('art.filter_rejected').toLowerCase()}?`}
        danger={confirmAction?.action === 'rejected'}
      />
    </div>
  );
}
