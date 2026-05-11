// ── CEO Brain ─────────────────────────────────────────────────────────────────
import React, { useState, useEffect } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { PageHeader, SectionHeader, Badge, ModeBadge, StatRow, SlideOver, ScoreBar, Divider, ActionImpactPreview } from '@/components/ui';
import { GlassIcon } from '@/components/Icons';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, ReferenceLine } from 'recharts';
import { useI18n } from '@/lib/i18n';
import { useStrategy, useNiches, useRecommendations, useStrategyLog, useUpdateStrategy } from '@/lib/hooks';
import { GROWTH_MODES } from '@/lib/modes';
import { fmtCurrency, fmtPct, fmtScore, fmtRelative, scoreColor } from '@/lib/utils';

const MODES = Object.keys(GROWTH_MODES);
const GROWTH_ICONS: Record<string, string> = { conservative: 'shield', balanced: 'compass', aggressive: 'rocket', recovery: 'heart' };

export function CeoBrain() {
  const { t } = useI18n();
  const { data: state, isLoading: stateLoading } = useStrategy();
  const { data: niches = [] }          = useNiches();
  const { data: recommendations = [] } = useRecommendations();
  const { data: strategyLog = [] }     = useStrategyLog();
  const updateStrategy = useUpdateStrategy();

  const [showStateEditor, setShowStateEditor] = useState(false);
  const [editValues, setEditValues] = useState<Record<string, any>>({});
  const [proposedMode, setProposedMode] = useState('balanced');
  const [showLog, setShowLog] = useState(false);

  useEffect(() => {
    if (state) { setEditValues({ ...state }); setProposedMode(state.growth_mode ?? 'balanced'); }
  }, [state]);

  function applyState() {
    updateStrategy.mutate({ ...editValues, growth_mode: proposedMode }, { onSuccess: () => setShowStateEditor(false) });
  }

  if (stateLoading || !state) return (
    <div className="card" style={{ padding: '2rem', textAlign: 'center' }}>
      <GlassIcon name="planet" size={40} style={{ opacity: 0.3, marginBottom: '0.5rem' }} />
      <div style={{ color: 'var(--text-muted)' }}>{t('ceo.loading')}</div>
    </div>
  );

  const gap = (state.target_daily_revenue ?? 0) - (state.actual_daily_revenue ?? 0);
  const actionableRecs = (recommendations as any[]).map((r: any, i: number) => ({
    ...r, actionLabel: [t('ceo.act_spawn'), t('ceo.act_budget'), t('ceo.act_niches')][i] ?? null,
  }));

  return (
    <div>
      <PageHeader title={t('ceo.title')} subtitle={t('ceo.sub')}
        action={
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <ModeBadge mode={state.growth_mode} />
            <button className="btn btn-primary btn-sm" onClick={() => setShowStateEditor(true)} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <GlassIcon name="planet" size={14} style={{ filter: 'brightness(0) invert(1)' }} />
              {t('ceo.act_adjust')}
            </button>
          </div>
        }
      />

      {/* Performance Gap */}
      <div className="card" style={{ marginBottom: '1.25rem', borderLeft: `4px solid ${state.performance_ratio >= 1 ? 'var(--success)' : state.performance_ratio >= 0.7 ? 'var(--warning)' : 'var(--danger)'}` }}>
        <div style={{ display: 'flex', gap: '2rem', alignItems: 'center', flexWrap: 'wrap' }}>
          <GlassIcon name="chart" size={32} style={{ opacity: 0.6, flexShrink: 0 }} />
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '0.25rem' }}>{t('ceo.lbl_perf_gap')}</div>
            <div style={{ display: 'flex', gap: '1rem', alignItems: 'baseline', marginBottom: '0.5rem' }}>
              <span style={{ fontSize: '2rem', fontWeight: 800, fontVariantNumeric: 'tabular-nums', color: state.performance_ratio >= 1 ? 'var(--success)' : state.performance_ratio >= 0.7 ? 'var(--warning)' : 'var(--danger)' }}>
                {fmtPct(state.performance_ratio)}
              </span>
              <span style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
                {gap > 0 ? `${fmtCurrency(gap)} ${t('ceo.msg_short')}` : `${fmtCurrency(Math.abs(gap))} ${t('ceo.msg_above')}`}
              </span>
            </div>
            <div style={{ position: 'relative', height: 8, background: 'var(--border)', borderRadius: 4 }}>
              <div style={{ height: '100%', width: `${Math.min(100, state.performance_ratio * 100)}%`, background: state.performance_ratio >= 1 ? 'var(--success)' : state.performance_ratio >= 0.7 ? 'var(--warning)' : 'var(--danger)', borderRadius: 4 }} />
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '0.25rem', fontSize: '0.7rem', color: 'var(--text-muted)' }}>
              <span>{fmtCurrency(state.actual_daily_revenue)} {t('ceo.lbl_actual')}</span>
              <span>{fmtCurrency(state.target_daily_revenue)} {t('ceo.lbl_target')}</span>
            </div>
          </div>
          <div style={{ display: 'flex', gap: '1.5rem' }}>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontWeight: 700, color: 'var(--info)' }}>{state.consecutive_low_cycles ?? 0}</div>
              <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>{t('ceo.lbl_low_cycles')}</div>
            </div>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontWeight: 700, color: 'var(--primary)' }}>×{(state.threshold_modifier ?? 1).toFixed(2)}</div>
              <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>{t('ceo.lbl_modifier')}</div>
            </div>
          </div>
          {state.performance_ratio < 0.70 && (
            <button className="btn btn-primary btn-sm" onClick={() => setShowStateEditor(true)}>{t('ceo.btn_adjust')}</button>
          )}
        </div>
      </div>

      {/* Recommendations */}
      <div className="card" style={{ marginBottom: '1.25rem' }}>
        <SectionHeader title={t('ceo.title_actions')} />
        {actionableRecs.length === 0
          ? <div style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>{t('ceo.no_recs')}</div>
          : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              {actionableRecs.map((r: any, i: number) => (
                <div key={i} className="card-elevated" style={{ padding: '0.875rem', borderLeft: `3px solid ${r.priority === 'high' ? 'var(--danger)' : r.priority === 'medium' ? 'var(--warning)' : 'var(--border)'}`, display: 'flex', alignItems: 'center', gap: '1rem' }}>
                  <GlassIcon name={r.priority === 'high' ? 'warning' : r.priority === 'medium' ? 'info' : 'check-circle'} size={15} style={{ flexShrink: 0, opacity: 0.7 }} />
                  <div style={{ flex: 1 }}>
                    <Badge status={r.priority === 'high' ? 'danger' : r.priority === 'medium' ? 'warning' : 'muted'}>{r.priority}</Badge>
                    <span style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', marginLeft: '0.5rem' }}>{r.message}</span>
                  </div>
                  {r.actionLabel && (
                    <button className={`btn btn-sm ${r.priority === 'high' ? 'btn-danger' : 'btn-secondary'}`} style={{ flexShrink: 0 }}>
                      {r.actionLabel} →
                    </button>
                  )}
                </div>
              ))}
            </div>
          )
        }
      </div>

      {/* Niche Table + Chart */}
      <div className="grid-2" style={{ marginBottom: '1.25rem' }}>
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          <div style={{ padding: '1rem 1.25rem', borderBottom: '1px solid var(--border)' }}>
            <SectionHeader title={t('ceo.title_niche')} />
          </div>
          <table className="data-table">
            <thead><tr><th>{t('ceo.col_niche')}</th><th>{t('ceo.col_budget')}</th><th>{t('ceo.col_win')}</th><th>{t('ceo.col_rev')}</th><th>{t('ceo.col_signal')}</th></tr></thead>
            <tbody>
              {(niches as any[]).map((n: any) => {
                const roi = n.win_rate / (n.budget_share || 0.01);
                const signal = roi > 2.2 ? 'boost' : roi < 1.2 ? 'cut' : 'hold';
                return (
                  <tr key={n.niche}>
                    <td style={{ textTransform: 'capitalize', fontWeight: 500 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                        <GlassIcon name="compass" size={12} style={{ opacity: 0.4 }} />{n.niche}
                      </div>
                    </td>
                    <td><span className="badge badge-primary">{fmtPct(n.budget_share ?? 0)}</span></td>
                    <td>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.375rem' }}>
                        <ScoreBar value={n.win_rate} />
                        <span style={{ color: scoreColor(n.win_rate), fontWeight: 600, fontSize: '0.75rem' }}>{fmtPct(n.win_rate)}</span>
                      </div>
                    </td>
                    <td style={{ color: 'var(--success)' }}>{fmtCurrency(n.avg_revenue ?? 0)}</td>
                    <td>
                      <span className={`badge ${signal === 'boost' ? 'badge-success' : signal === 'cut' ? 'badge-danger' : 'badge-muted'}`}>
                        {signal === 'boost' ? t('ceo.val_boost') : signal === 'cut' ? t('ceo.val_cut') : t('ceo.val_hold')}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="card">
          <SectionHeader title={t('ceo.title_win_niche')} />
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={niches as any[]} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
              <XAxis dataKey="niche" tick={{ fill: 'var(--text-muted)', fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis domain={[0, 1]} tickFormatter={v => `${Math.round(v * 100)}%`} tick={{ fill: 'var(--text-muted)', fontSize: 11 }} axisLine={false} tickLine={false} />
              <Tooltip formatter={(v: unknown) => fmtPct(Number(v))} contentStyle={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }} />
              <ReferenceLine y={0.6} stroke="var(--success)" strokeDasharray="4 2" />
              <Bar dataKey="win_rate" radius={[4, 4, 0, 0]}>
                {(niches as any[]).map((n: any, i: number) => <Cell key={i} fill={n.win_rate >= 0.6 ? 'var(--success)' : n.win_rate >= 0.45 ? 'var(--warning)' : 'var(--danger)'} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Strategy Log */}
      <button style={{ fontSize: '0.75rem', color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '0.35rem', marginBottom: '0.5rem' }} onClick={() => setShowLog(v => !v)}>
        {showLog ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        {t('ceo.log_title')} ({strategyLog.length} {t('ceo.log_entries')})
      </button>
      {showLog && (
        <div className="card">
          <div style={{ maxHeight: 260, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            {(strategyLog as any[]).length === 0
              ? <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>{t('ceo.no_events')}</div>
              : (strategyLog as any[]).map((entry: any) => (
                <div key={entry.id} style={{ display: 'flex', gap: '0.75rem', padding: '0.5rem 0', borderBottom: '1px solid var(--border-subtle)' }}>
                  <div style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--primary)', marginTop: '0.35rem', flexShrink: 0 }} />
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: '0.8125rem', fontWeight: 500 }}>{String(entry.event ?? '').replace(/_/g, ' ')}</div>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{Object.entries(entry.data ?? {}).map(([k, v]) => `${k}: ${v}`).join(' · ')}</div>
                  </div>
                  <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', flexShrink: 0 }}>{fmtRelative(entry.created_at)}</span>
                </div>
              ))
            }
          </div>
        </div>
      )}

      {/* Strategy Editor */}
      <SlideOver open={showStateEditor} onClose={() => setShowStateEditor(false)} title={t('ceo.act_adjust')} width={460}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.5rem', textTransform: 'uppercase' }}>{t('ceo.lbl_growth_mode')}</label>
            <div style={{ display: 'flex', gap: '0.375rem', flexWrap: 'wrap' }}>
              {MODES.map(m => (
                <button key={m} onClick={() => setProposedMode(m)} className={`btn btn-sm ${proposedMode === m ? 'btn-primary' : 'btn-secondary'}`} style={{ display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
                  <GlassIcon name={GROWTH_ICONS[m] as any} size={13} style={{ filter: proposedMode === m ? 'brightness(0) invert(1)' : 'none', opacity: proposedMode === m ? 1 : 0.7 }} />
                  {(GROWTH_MODES as any)[m].label}
                </button>
              ))}
            </div>
            <ActionImpactPreview currentMode={state.growth_mode} proposedMode={proposedMode} />
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>{t('ceo.lbl_target_views')} {((editValues.target_daily_views ?? 0) / 1000).toFixed(0)}K</label>
            <input type="range" min={10000} max={500000} step={5000} value={editValues.target_daily_views ?? 50000} onChange={e => setEditValues(v => ({ ...v, target_daily_views: +e.target.value }))} style={{ width: '100%', accentColor: 'var(--primary)' }} />
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>{t('ceo.lbl_target_rev')} {fmtCurrency(editValues.target_daily_revenue ?? 0)}</label>
            <input type="range" min={5} max={500} step={5} value={editValues.target_daily_revenue ?? 50} onChange={e => setEditValues(v => ({ ...v, target_daily_revenue: +e.target.value }))} style={{ width: '100%', accentColor: 'var(--primary)' }} />
          </div>
          {updateStrategy.isError && <div style={{ color: 'var(--danger)', fontSize: '0.8rem' }}>{(updateStrategy.error as Error)?.message}</div>}
          <Divider />
          <button className="btn btn-primary" onClick={applyState} disabled={updateStrategy.isPending}>
            {updateStrategy.isPending ? t('ceo.saving') : t('ceo.btn_apply')}
          </button>
        </div>
      </SlideOver>
    </div>
  );
}
