// ── Content Queue ─────────────────────────────────────────────────────────────
import React, { useEffect } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { DecisionBlock, EmptyState } from '@/components/ui';
import { GlassIcon } from '@/components/Icons';
import { useQueue, useApproveContent, useRejectContent } from '@/lib/hooks';
import { useUIStore } from '@/lib/store';
import { fmtCurrency } from '@/lib/utils';
import { useI18n } from '@/lib/i18n';

type QueueItem = {
  content_id: string; platform: string; niche: string; mode: string;
  status: 'pending' | 'approved' | 'rejected' | 'force_published';
  final_score: number; expected_value: number; confidence: number;
  hook: string; risk_flags: string[]; reason: string; priority_score: number;
  created_at: number; approved_by?: string;
};

function StatusChip({ status }: { status: string }) {
  const { t } = useI18n();
  type ChipCfg = { color: string; bg: string; icon: string; label: string };
  const cfg: Record<string, ChipCfg> = {
    approved:        { color: 'var(--success)', bg: 'var(--success-muted)', icon: 'check-circle', label: t('q.approved') },
    force_published: { color: 'var(--success)', bg: 'var(--success-muted)', icon: 'check-circle', label: t('q.force_pub') },
    rejected:        { color: 'var(--danger)',  bg: 'var(--danger-muted)',  icon: 'cross-circle', label: t('q.rejected') },
    pending:         { color: 'var(--warning)', bg: 'var(--warning-muted)', icon: 'calendar',     label: t('q.pending') },
  };
  const c = cfg[status] ?? { color: 'var(--text-muted)', bg: 'var(--surface-2)', icon: 'info', label: status };
  return (
    <span style={{ display: 'inline-flex', gap: '0.3rem', alignItems: 'center', padding: '0.2rem 0.6rem', borderRadius: '9999px', background: c.bg, color: c.color, fontSize: '0.7rem', fontWeight: 700 }}>
      <GlassIcon name={c.icon as any} size={11} style={{ filter: `brightness(0) saturate(100%) ${c.color === 'var(--success)' ? 'invert(52%) sepia(70%) saturate(600%) hue-rotate(120deg)' : c.color === 'var(--danger)' ? 'invert(26%) sepia(90%) saturate(3000%) hue-rotate(345deg)' : 'invert(70%) sepia(60%) saturate(900%) hue-rotate(15deg)'}` }} />
      {c.label}
    </span>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  const { t } = useI18n();
  return (
    <div style={{ padding: '2rem', textAlign: 'center', background: 'var(--surface)', border: '1px solid var(--danger)', borderRadius: 'var(--radius)' }}>
      <GlassIcon name="warning" size={36} style={{ marginBottom: '0.5rem', filter: 'brightness(0) saturate(100%) invert(26%) sepia(90%) saturate(3000%) hue-rotate(345deg)' }} />
      <div style={{ color: 'var(--danger)', fontWeight: 600, marginBottom: '0.5rem' }}>{t('q.error_load')}</div>
      <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem', marginBottom: '1rem' }}>{message}</div>
      <button className="btn btn-secondary btn-sm" onClick={onRetry}><RefreshCw size={13} /> {t('q.retry')}</button>
    </div>
  );
}

export function ContentQueue() {
  const { autoApprove, setPendingCount } = useUIStore();
  const { data: rawQueue, isLoading, error, refetch } = useQueue();
  const { t } = useI18n();
  const approveM = useApproveContent();
  const rejectM  = useRejectContent();

  const queue    = (rawQueue ?? []) as QueueItem[];
  const pending  = queue.filter(i => i.status === 'pending').sort((a, b) => b.priority_score - a.priority_score);
  const approved = queue.filter(i => i.status === 'approved' || i.status === 'force_published');
  const rejected = queue.filter(i => i.status === 'rejected');
  const avgEv    = pending.reduce((s, i) => s + i.expected_value, 0) / (pending.length || 1);
  const highValue = pending.filter(i => i.expected_value >= avgEv);
  const totalEv  = highValue.reduce((s, i) => s + i.expected_value, 0);

  useEffect(() => { setPendingCount(pending.length); }, [pending.length, setPendingCount]);

  function approve(id: string) { approveM.mutate(id); }
  function reject(id: string)  { rejectM.mutate({ id }); }
  function approveAll()        { highValue.forEach(i => approve(i.content_id)); }

  if (isLoading) return (
    <div style={{ textAlign: 'center', padding: '4rem' }}>
      <GlassIcon name="clipboard" size={40} style={{ opacity: 0.4, marginBottom: '0.75rem' }} />
      <div style={{ color: 'var(--text-muted)' }}>{t('q.loading')}</div>
    </div>
  );
  if (error) return <ErrorState message={(error as Error).message} onRetry={() => refetch()} />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>

      {/* Header actions */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.75rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-muted)', fontSize: '0.8rem' }}>
          <GlassIcon name="clipboard" size={14} style={{ opacity: 0.6 }} />
          {pending.length} {t('q.stat_pend')} · {approved.length} {t('q.stat_app')} · {rejected.length} {t('q.stat_rej')}
          {pending.length > 0 && ` · ${t('q.tot_ev')} ${fmtCurrency(pending.reduce((s, i) => s + i.expected_value, 0))}`}
        </div>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          {highValue.length > 0 && (
            <button className="btn btn-primary" onClick={approveAll} style={{ display: 'flex', gap: '0.375rem', alignItems: 'center' }}>
              <GlassIcon name="check-circle" size={15} style={{ filter: 'brightness(0) invert(1)' }} />
              {t('q.app_high')} ({highValue.length}) ({fmtCurrency(totalEv)})
            </button>
          )}
          <button className="btn btn-secondary btn-sm" onClick={() => refetch()}><RefreshCw size={13} /></button>
        </div>
      </div>

      {/* Auto-approve banner */}
      {autoApprove && (
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', padding: '0.625rem 1rem', background: 'var(--warning-muted)', border: '1px solid var(--warning)', borderRadius: 'var(--radius-sm)', fontSize: '0.8125rem' }}>
          <AlertTriangle size={14} color="var(--warning)" />
          <span style={{ color: 'var(--warning)', fontWeight: 600 }}>{t('q.auto_on')}</span>
          <span style={{ color: 'var(--text-secondary)' }}>{t('q.auto_desc')}</span>
        </div>
      )}

      {/* PENDING */}
      {pending.length === 0
        ? <EmptyState icon="check-circle" message={t('q.no_content')} />
        : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--warning)' }}>
              <GlassIcon name="calendar" size={13} style={{ filter: 'brightness(0) saturate(100%) invert(70%) sepia(60%) saturate(900%) hue-rotate(15deg)' }} />
              {t('q.wait_dec')} ({pending.length})
            </div>
            {pending.map(item => {
              const risk: 'low' | 'medium' | 'high' = item.risk_flags.length > 0 ? 'medium' : item.final_score < 0.65 ? 'medium' : 'low';
              const isHV = item.expected_value >= avgEv;
              return (
                <DecisionBlock key={item.content_id}
                  badge={isHV ? t('q.badge_high') : t('q.badge_norm')}
                  badgeColor={isHV ? 'var(--success)' : 'var(--text-muted)'}
                  title={`"${item.hook.slice(0, 65)}${item.hook.length > 65 ? '...' : ''}"`}
                  reason={`${item.niche} · ${item.platform} · ${item.mode} · Score ${Math.round(item.final_score * 100)}/100`}
                  ev={`$${item.expected_value.toFixed(2)}`}
                  confidence={`${Math.round(item.confidence * 100)}%`}
                  risk={risk}
                  riskFlags={item.risk_flags.length > 0 ? item.risk_flags : undefined}
                  ifSkip={item.risk_flags.length > 0 ? t('q.risk_flag') : t('cmd.miss_revenue')}
                  action={{ label: t('act.approve'), onClick: () => approve(item.content_id) }}
                  passive={{ label: t('pass.reject'), onClick: () => reject(item.content_id) }}
                />
              );
            })}
          </div>
        )
      }

      {/* APPROVED */}
      {approved.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--success)' }}>
            <GlassIcon name="check-circle" size={13} style={{ filter: 'brightness(0) saturate(100%) invert(52%) sepia(70%) saturate(600%) hue-rotate(120deg)' }} />
            {t('q.approved')} ({approved.length})
          </div>
          {approved.map(item => (
            <div key={item.content_id} style={{ padding: '0.75rem 1rem', background: 'var(--surface)', border: '1px solid var(--border)', borderLeft: '4px solid var(--success)', borderRadius: 'var(--radius)', display: 'flex', gap: '0.75rem', alignItems: 'center', flexWrap: 'wrap' }}>
              <StatusChip status={item.status} />
              <span style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', flex: 1 }}>{item.hook.slice(0, 70)}</span>
              <span style={{ fontSize: '0.75rem', color: 'var(--success)', fontWeight: 600 }}>${item.expected_value.toFixed(2)}</span>
              <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{item.platform} · {item.niche}</span>
            </div>
          ))}
        </div>
      )}

      {/* REJECTED */}
      {rejected.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--danger)' }}>
            <GlassIcon name="cross-circle" size={13} style={{ filter: 'brightness(0) saturate(100%) invert(26%) sepia(90%) saturate(3000%) hue-rotate(345deg)' }} />
            {t('q.rejected')} ({rejected.length})
          </div>
          {rejected.map(item => (
            <div key={item.content_id} style={{ padding: '0.75rem 1rem', background: 'var(--surface)', border: '1px solid var(--border)', borderLeft: '4px solid var(--danger)', borderRadius: 'var(--radius)', display: 'flex', gap: '0.75rem', alignItems: 'center', flexWrap: 'wrap', opacity: 0.7 }}>
              <StatusChip status="rejected" />
              <span style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', flex: 1 }}>{item.hook.slice(0, 70)}</span>
              <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{item.platform} · {item.niche}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
