// ── Command Center — Real Data from GET /api/v1/system/decisions ──────────────
import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { ShieldOff, Power, RefreshCw } from 'lucide-react';
import { DecisionBlock, ConfirmDialog, StatusDot, GlassKpiCard } from '@/components/ui';
import { GlassIcon } from '@/components/Icons';
import { useDecisions, useSystemStats, useFleetAccounts, useApproveContent, useRejectContent, useFreezeAccount, useSetBrainConfig } from '@/lib/hooks';
import { useUIStore, useWSStore } from '@/lib/store';
import { useI18n } from '@/lib/i18n';

type Decision = {
  id: string; type: 'system' | 'content' | 'account';
  title: string; reason: string; expected_value: number;
  confidence: number; risk_flags: string[]; action: string;
  priority_score: number; metadata: Record<string, any>;
};

// ── Section Label with asset icon ─────────────────────────────────────────────
function SectionLabel({ icon, label, color }: { icon: string; label: string; color: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.7rem', fontWeight: 700, color, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '0.625rem' }}>
      <GlassIcon name={icon as any} size={16} style={{ filter: `brightness(0) saturate(100%) ${color === 'var(--danger)' ? 'invert(26%) sepia(90%) saturate(3000%) hue-rotate(345deg)' : color === 'var(--primary)' ? 'invert(22%) sepia(88%) saturate(2000%) hue-rotate(257deg)' : 'invert(55%) sepia(60%) saturate(600%) hue-rotate(80deg)'}` }} />
      {label}
    </div>
  );
}

export function CommandDashboard() {
  const { executionEnabled, setExecutionEnabled, autoApprove } = useUIStore();
  const { connected } = useWSStore();
  const { t } = useI18n();
  const [confirmSafe, setConfirmSafe] = useState(false);

  const { data: decisions = [], isLoading: decisionsLoading, error: decisionsError, refetch } = useDecisions(5);
  const { data: stats }          = useSystemStats();
  const { data: accounts = [] }  = useFleetAccounts();

  const approveM    = useApproveContent();
  const rejectM     = useRejectContent();
  const freezeM     = useFreezeAccount();
  const brainConfig = useSetBrainConfig();

  function handleAction(d: Decision) {
    switch (d.action) {
      case 'approve':          approveM.mutate(d.metadata.content_id ?? d.id); break;
      case 'freeze':           freezeM.mutate(d.metadata.account_id ?? d.id); break;
      case 'enable_execution': brainConfig.mutate({ EXECUTION_ENABLED: true }); setExecutionEnabled(true); break;
      default:                 console.info('unhandled action', d.action);
    }
  }

  function handlePassive(d: Decision) {
    if (d.action === 'approve') rejectM.mutate({ id: d.metadata.content_id ?? d.id });
  }

  const systemDecisions  = (decisions as Decision[]).filter(d => d.type === 'system');
  const contentDecisions = (decisions as Decision[]).filter(d => d.type === 'content');
  const fleetDecisions   = (decisions as Decision[]).filter(d => d.type === 'account');

  const activeAccounts = (accounts as any[]).filter(a => !a.uploads_suspended && a.risk_level !== 'high').length;
  const highRiskCount  = (accounts as any[]).filter(a => a.risk_level === 'high').length;
  const pendingCount   = contentDecisions.length;

  function actionLabel(d: Decision) {
    const map: Record<string, string> = { approve: t('act.approve'), freeze: t('act.freeze'), enable_execution: t('act.enable_exec'), pause: t('act.pause'), monitor: t('act.monitor') };
    return map[d.action] ?? d.action;
  }
  function passiveLabel(d: Decision) {
    const map: Record<string, string> = { approve: t('pass.reject'), freeze: t('pass.monitor'), enable_execution: t('pass.later'), pause: t('pass.continue'), monitor: t('pass.ignore') };
    return map[d.action] ?? t('pass.ignore');
  }
  function blockRisk(d: Decision): 'low' | 'medium' | 'high' {
    if (d.type === 'system' || d.risk_flags.length > 0) return 'high';
    if (d.type === 'account') return 'medium';
    return d.expected_value < 10 ? 'medium' : 'low';
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.75rem' }}>

      {/* ── SECTION 1: SYSTEM ─────────────────────────────────────────────── */}
      {systemDecisions.length > 0 && (
        <div>
          <SectionLabel icon="warning" label={t('cmd.system_alert')} color="var(--danger)" />
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            {systemDecisions.map(d => (
              <DecisionBlock key={d.id}
                badge={t('cmd.badge_system')} badgeColor="var(--danger)"
                title={d.title} reason={d.reason} risk="high"
                riskFlags={d.risk_flags.length > 0 ? d.risk_flags : undefined}
                ifSkip={t('cmd.system_skip')}
                action={{ label: actionLabel(d), onClick: () => handleAction(d) }}
                passive={{ label: passiveLabel(d), onClick: undefined }}
              />
            ))}
          </div>
        </div>
      )}

      {/* ── SECTION 2: CONTENT ───────────────────────────────────────────── */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.625rem' }}>
          <SectionLabel icon="document" label={`${t('cmd.content_title')} ${contentDecisions.length} ${t('cmd.content_pending')}`} color="var(--primary)" />
          <button className="btn btn-ghost btn-sm btn-icon" onClick={() => refetch()} style={{ color: 'var(--text-muted)' }}>
            <RefreshCw size={12} />
          </button>
        </div>

        {decisionsLoading ? (
          <div style={{ padding: '1.5rem', textAlign: 'center', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
            <GlassIcon name="arrows-square-up-down" size={28} style={{ opacity: 0.5, marginBottom: '0.5rem' }} />
            <div>{t('cmd.loading')}</div>
          </div>
        ) : decisionsError ? (
          <div style={{ padding: '1rem', background: 'var(--surface)', border: '1px solid var(--danger)', borderRadius: 'var(--radius)', fontSize: '0.8rem', color: 'var(--danger)', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <GlassIcon name="warning" size={18} style={{ filter: 'brightness(0) saturate(100%) invert(26%) sepia(90%) saturate(3000%) hue-rotate(345deg)' }} />
            {t('cmd.error_load')} {(decisionsError as Error).message}
            <button className="btn btn-ghost btn-sm" onClick={() => refetch()} style={{ marginLeft: '0.5rem' }}>{t('cmd.retry')}</button>
          </div>
        ) : contentDecisions.length === 0 ? (
          <div style={{ padding: '1.5rem', textAlign: 'center', background: 'var(--surface)', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}>
            <GlassIcon name="check-circle" size={36} style={{ marginBottom: '0.5rem', filter: 'brightness(0) saturate(100%) invert(52%) sepia(70%) saturate(600%) hue-rotate(120deg)' }} />
            <div style={{ color: 'var(--success)', fontWeight: 600 }}>{t('cmd.no_content')}</div>
            <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem', marginTop: '0.25rem' }}>
              <Link to="/operations/queue" style={{ color: 'var(--primary)' }}>{t('cmd.view_all_queue')}</Link>
            </div>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            {contentDecisions.map(d => (
              <DecisionBlock key={d.id}
                badge={t('cmd.badge_content')} badgeColor="var(--primary)"
                title={d.title} reason={d.reason}
                ev={`$${d.expected_value.toFixed(2)}`}
                confidence={`${Math.round(d.confidence * 100)}%`}
                risk={blockRisk(d)}
                riskFlags={d.risk_flags.length > 0 ? d.risk_flags : undefined}
                ifSkip={t('cmd.miss_revenue')}
                action={{ label: actionLabel(d), onClick: () => handleAction(d) }}
                passive={{ label: passiveLabel(d), onClick: () => handlePassive(d) }}
              />
            ))}
            <Link to="/operations/queue" style={{ fontSize: '0.75rem', color: 'var(--primary)', textAlign: 'right', paddingRight: '0.25rem' }}>
              {t('cmd.view_all_queue')}
            </Link>
          </div>
        )}
      </div>

      {/* ── SECTION 3: FLEET ─────────────────────────────────────────────── */}
      {fleetDecisions.length > 0 && (
        <div>
          <SectionLabel icon="shield" label={`${t('cmd.fleet_alert')} ${fleetDecisions.length} ${t('cmd.fleet_attn')}`} color="var(--danger)" />
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            {fleetDecisions.map(d => (
              <DecisionBlock key={d.id}
                badge={t('cmd.badge_fleet')} badgeColor={d.action === 'freeze' ? 'var(--danger)' : 'var(--warning)'}
                title={d.title} reason={d.reason}
                risk={blockRisk(d)}
                riskFlags={d.risk_flags.length > 0 ? d.risk_flags : undefined}
                ifSkip={d.action === 'freeze' ? t('cmd.account_ban') : t('cmd.risk_detect')}
                action={{ label: actionLabel(d), onClick: () => handleAction(d), danger: d.action === 'freeze' }}
                passive={{ label: passiveLabel(d), onClick: undefined }}
              />
            ))}
            <Link to="/fleet/health" style={{ fontSize: '0.75rem', color: 'var(--primary)', textAlign: 'right', paddingRight: '0.25rem' }}>
              {t('cmd.view_all_fleet')}
            </Link>
          </div>
        </div>
      )}

      {/* ── STATUS BAR ──────────────────────────────────────────────────── */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', color: 'var(--text-muted)', marginBottom: '0.75rem' }}>
          <GlassIcon name="cloud-sun" size={14} style={{ opacity: 0.6 }} />
          {t('cmd.status_title')}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '1.25rem' }}>
          <GlassKpiCard
            label={t('cmd.active_accs')} value={activeAccounts}
            icon={<GlassIcon name="user" size={28} />}
            iconBg="rgba(16,185,129,0.12)" iconColor="#10b981"
            sub={highRiskCount > 0 ? `${highRiskCount} ${t('cmd.high_risk')}` : undefined}
          />
          <GlassKpiCard
            label={t('cmd.content_queue')} value={pendingCount}
            icon={<GlassIcon name="clipboard" size={28} />}
            iconBg={pendingCount > 0 ? 'rgba(245,158,11,0.12)' : 'rgba(16,185,129,0.10)'}
            iconColor={pendingCount > 0 ? '#f59e0b' : '#10b981'}
          />
          {/* Engine card */}
          <div className="card" style={{ padding: '1.125rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
              <GlassIcon name="arrows-square-up-down" size={18} style={{ opacity: 0.7 }} />
              <div className="stat-label">{t('cmd.engine_on')}</div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.625rem' }}>
              <StatusDot status={executionEnabled ? 'healthy' : 'failed'} />
              <span style={{ fontSize: '0.8rem', fontWeight: 600, color: executionEnabled ? 'var(--success)' : 'var(--danger)' }}>
                {executionEnabled ? t('cmd.engine_on') : t('cmd.engine_off')}
              </span>
            </div>
            <button className={`btn btn-sm ${executionEnabled ? 'btn-secondary' : 'btn-primary'}`}
              onClick={() => executionEnabled ? setConfirmSafe(true) : (brainConfig.mutate({ EXECUTION_ENABLED: true }), setExecutionEnabled(true))}>
              {executionEnabled
                ? <><ShieldOff size={11} /> {t('cmd.emer_stop')}</>
                : <><Power size={11} /> {t('cmd.emer_start')}</>}
            </button>
          </div>
          {/* WebSocket card */}
          <div className="card" style={{ padding: '1.125rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
              <GlassIcon name="cloud" size={18} style={{ opacity: 0.7 }} />
              <div className="stat-label">WebSocket</div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.375rem' }}>
              <StatusDot status={connected ? 'healthy' : 'failed'} />
              <span style={{ fontSize: '0.8rem', fontWeight: 600, color: connected ? 'var(--success)' : 'var(--text-muted)' }}>
                {connected ? t('cmd.live') : t('cmd.offline')}
              </span>
            </div>
            {autoApprove && <div style={{ fontSize: '0.65rem', color: 'var(--warning)', marginTop: '0.375rem' }}>{t('cmd.auto_app_on')}</div>}
          </div>
        </div>
      </div>

      {/* ── BACKGROUND STATS ─────────────────────────────────────────────── */}
      <div style={{ display: 'flex', gap: '2rem', flexWrap: 'wrap', padding: '0.875rem 1.25rem', background: 'var(--surface)', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}>
        {[
          { label: t('cmd.tasks_run'),  val: stats?.running ?? '—', icon: 'play-circle' },
          { label: t('cmd.tasks_pend'), val: stats?.pending ?? '—', icon: 'clock' },
          { label: t('cmd.tasks_fail'), val: stats?.failed  ?? '—', warn: (stats?.failed ?? 0) > 5, icon: 'cross-circle' },
          { label: t('cmd.operator'),   val: 'Licensed device', icon: 'user' },
        ].map(({ label, val, warn, icon }) => (
          <div key={label} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <GlassIcon name={icon as any} size={14} style={{ opacity: 0.5 }} />
            <div>
              <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: '0.1rem' }}>{label}</div>
              <div style={{ fontWeight: 700, fontSize: '0.875rem', color: (warn as boolean) ? 'var(--danger)' : 'var(--text-secondary)' }}>{val}</div>
            </div>
          </div>
        ))}
      </div>

      <ConfirmDialog
        open={confirmSafe}
        onClose={() => setConfirmSafe(false)}
        onConfirm={() => { brainConfig.mutate({ EXECUTION_ENABLED: false }); setExecutionEnabled(false); setConfirmSafe(false); }}
        title={t('cmd.emer_stop')}
        message={t('cmd.stop_msg')}
        danger
      />
    </div>
  );
}
