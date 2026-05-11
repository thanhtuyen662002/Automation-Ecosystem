// ── Fleet Health ──────────────────────────────────────────────────────────────
import React, { useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { DecisionBlock, ConfirmDialog } from '@/components/ui';
import { GlassIcon } from '@/components/Icons';
import { useFleetAccounts, useFreezeAccount, useClearCooldown } from '@/lib/hooks';
import { useI18n } from '@/lib/i18n';

type Account = {
  account_id: string; phase: string; risk_level: string;
  trust_score: number; fatigue_level: number; anomaly_count: number;
  cooldown_remaining_hours: number; uploads_suspended: boolean;
  operating_mode: string; current_intent?: string; account_age_days?: number;
};

function AccountStatusCard({ a }: { a: Account }) {
  const { t } = useI18n();
  const trustColor   = a.trust_score   >= 0.75 ? 'var(--success)' : a.trust_score   >= 0.50 ? 'var(--warning)' : 'var(--danger)';
  const fatigueColor = a.fatigue_level <= 0.40 ? 'var(--success)' : a.fatigue_level <= 0.65 ? 'var(--warning)' : 'var(--danger)';

  return (
    <div style={{
      padding: '0.875rem 1rem', background: 'var(--surface)',
      border: '1px solid var(--border)', borderLeft: '4px solid var(--success)',
      borderRadius: 'var(--radius)', display: 'flex', gap: '1rem',
      alignItems: 'center', flexWrap: 'wrap',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', minWidth: 100 }}>
        <GlassIcon name="user" size={18} style={{ opacity: 0.6, flexShrink: 0 }} />
        <div>
          <div style={{ fontWeight: 700, fontSize: '0.875rem' }}>{a.account_id}</div>
          <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '0.15rem' }}>{a.phase}</div>
        </div>
      </div>

      <div style={{ display: 'flex', gap: '1.25rem', flex: 1, flexWrap: 'wrap' }}>
        {[
          { label: t('fleet.lbl_risk'),    val: t('fleet.val_low'),                          color: 'var(--success)' },
          { label: t('fleet.lbl_trust'),   val: `${Math.round(a.trust_score * 100)}%`,       color: trustColor },
          { label: t('fleet.lbl_fatigue'), val: `${Math.round(a.fatigue_level * 100)}%`,     color: fatigueColor },
          { label: t('fleet.lbl_anomaly'), val: String(a.anomaly_count),                     color: a.anomaly_count > 0 ? 'var(--warning)' : 'var(--text-secondary)' },
          { label: t('fleet.lbl_status'),  val: a.current_intent ?? a.operating_mode,        color: 'var(--success)' },
        ].map(({ label, val, color }) => (
          <div key={label}>
            <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>{label}</div>
            <div style={{ fontWeight: 700, fontSize: '0.875rem', color }}>{val}</div>
          </div>
        ))}
        {a.uploads_suspended && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.7rem', color: 'var(--warning)', fontWeight: 600, alignSelf: 'center' }}>
            <GlassIcon name="warning" size={12} style={{ filter: 'brightness(0) saturate(100%) invert(70%) sepia(60%) saturate(900%) hue-rotate(15deg)' }} />
            {t('fleet.upload_paused')}
          </div>
        )}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.7rem', color: 'var(--success)', fontWeight: 600 }}>
        <GlassIcon name="check-circle" size={13} style={{ filter: 'brightness(0) saturate(100%) invert(52%) sepia(70%) saturate(600%) hue-rotate(120deg)' }} />
        {t('fleet.is_fine')}
      </div>
    </div>
  );
}

function needsAction(a: Account) {
  return a.risk_level === 'high' || a.anomaly_count >= 2 ||
    a.fatigue_level > 0.70 || (a.cooldown_remaining_hours > 0 && a.anomaly_count > 0);
}

function toDecisionProps(a: Account, onFreeze: () => void, onClear: () => void, t: (k: string) => string) {
  if (a.risk_level === 'high' || a.anomaly_count >= 2) {
    return {
      badge: t('fleet.badge_danger'), badgeColor: 'var(--danger)',
      title: `${a.account_id} — ${t('fleet.msg_anomaly')} ${a.anomaly_count} ${t('fleet.msg_anomaly_2')}`,
      reason: `${t('fleet.msg_phase_mode')}: ${a.phase} · ${t('fleet.msg_mode')}: ${a.operating_mode}`,
      ev: undefined, confidence: undefined, risk: 'high' as const,
      riskFlags: a.anomaly_count >= 2 ? [`${a.anomaly_count} anomalies detected`] : undefined,
      ifSkip: t('fleet.skip_danger'),
      action: { label: t('fleet.act_freeze'), onClick: onFreeze, danger: true },
      passive: { label: t('fleet.pass_monitor'), onClick: undefined },
    };
  }
  if (a.phase === 'COOLDOWN' && a.cooldown_remaining_hours > 0) {
    return {
      badge: t('fleet.badge_cooldown'), badgeColor: 'var(--warning)',
      title: `${a.account_id} — ${t('fleet.msg_cooldown')} ${a.cooldown_remaining_hours.toFixed(1)}${t('fleet.msg_cooldown_2')}`,
      reason: `Trust: ${Math.round(a.trust_score * 100)}% · Fatigue: ${Math.round(a.fatigue_level * 100)}% · ${a.anomaly_count} anomalies`,
      ev: undefined, confidence: undefined, risk: 'medium' as const,
      ifSkip: t('fleet.skip_cooldown'),
      action: { label: t('fleet.act_clear'), onClick: onClear },
      passive: { label: t('fleet.pass_wait'), onClick: undefined },
    };
  }
  if (a.fatigue_level > 0.70) {
    return {
      badge: t('fleet.badge_fatigue'), badgeColor: 'var(--warning)',
      title: `${a.account_id} — ${t('fleet.msg_fatigue')} ${Math.round(a.fatigue_level * 100)}%`,
      reason: `Trust: ${Math.round(a.trust_score * 100)}% · Phase: ${a.phase} · Anomalies: ${a.anomaly_count}`,
      ev: undefined, confidence: undefined, risk: 'medium' as const,
      riskFlags: [`Fatigue ${Math.round(a.fatigue_level * 100)}% — ${t('fleet.flag_fatigue')}`],
      ifSkip: t('fleet.skip_fatigue'),
      action: { label: t('fleet.act_pause'), onClick: onFreeze },
      passive: { label: t('fleet.pass_continue'), onClick: undefined },
    };
  }
  return null;
}

export function FleetHealth() {
  const { t } = useI18n();
  const { data: accounts = [], isLoading, refetch } = useFleetAccounts();
  const freezeM = useFreezeAccount();
  const clearM  = useClearCooldown();
  const [confirmFreeze, setConfirmFreeze] = useState<Account | null>(null);

  const all        = accounts as Account[];
  const actionable = all.filter(needsAction);
  const healthy    = all.filter(a => !needsAction(a));

  if (isLoading) return (
    <div style={{ textAlign: 'center', padding: '4rem' }}>
      <GlassIcon name="heart" size={40} style={{ opacity: 0.4, marginBottom: '0.75rem' }} />
      <div style={{ color: 'var(--text-muted)' }}>{t('fleet.loading')}</div>
    </div>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.75rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', color: 'var(--text-muted)', fontSize: '0.8rem' }}>
          <GlassIcon name="heart" size={15} style={{ opacity: 0.6 }} />
          {all.length} {t('fleet.sub_total')} ·{' '}
          <span style={{ color: actionable.length > 0 ? 'var(--danger)' : 'var(--text-muted)' }}>{actionable.length} {t('fleet.sub_act')}</span>
          · {healthy.length} {t('fleet.sub_ok')}
        </div>
        <button className="btn btn-secondary btn-sm" onClick={() => refetch()} style={{ display: 'flex', gap: '0.375rem', alignItems: 'center' }}>
          <RefreshCw size={13} /> {t('fleet.refresh')}
        </button>
      </div>

      {/* ACTIONABLE */}
      {actionable.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--danger)' }}>
            <GlassIcon name="fire" size={14} style={{ filter: 'brightness(0) saturate(100%) invert(26%) sepia(90%) saturate(3000%) hue-rotate(345deg)' }} />
            {t('fleet.action_req')} ({actionable.length})
          </div>
          {actionable.map(a => {
            const props = toDecisionProps(a, () => setConfirmFreeze(a), () => clearM.mutate(a.account_id), t);
            return props ? <DecisionBlock key={a.account_id} {...props} /> : null;
          })}
        </div>
      )}

      {/* HEALTHY */}
      {healthy.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--success)' }}>
            <GlassIcon name="shield" size={13} style={{ filter: 'brightness(0) saturate(100%) invert(52%) sepia(70%) saturate(600%) hue-rotate(120deg)' }} />
            {t('fleet.healthy')} ({healthy.length})
          </div>
          {healthy.map(a => <AccountStatusCard key={a.account_id} a={a} />)}
        </div>
      )}

      <ConfirmDialog
        open={!!confirmFreeze}
        onClose={() => setConfirmFreeze(null)}
        onConfirm={() => { if (confirmFreeze) { freezeM.mutate(confirmFreeze.account_id); setConfirmFreeze(null); } }}
        title={t('fleet.confirm_title')}
        message={`${t('fleet.confirm_msg')} ${confirmFreeze?.account_id}?`}
        danger
      />
    </div>
  );
}
