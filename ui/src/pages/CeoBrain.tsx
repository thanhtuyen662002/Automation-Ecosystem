// ── CEO Brain — Control Interface (Ranks 5 & 6) ──────────────────────────────
import React, { useState } from 'react';
import { Brain, ChevronDown, ChevronUp } from 'lucide-react';
import {
  PageHeader, SectionHeader, Badge, ModeBadge,
  StatRow, SlideOver, ScoreBar, Divider, ActionImpactPreview,
} from '@/components/ui';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, ReferenceLine } from 'recharts';
import { mockStrategyState, mockNichePerformance, mockRecommendations, mockStrategyLog } from '@/lib/mock';
import { GROWTH_MODES } from '@/lib/modes';
import { fmtCurrency, fmtPct, fmtScore, fmtRelative, scoreColor } from '@/lib/utils';

const MODES = Object.keys(GROWTH_MODES);

export function CeoBrain() {
  const [state, setState]               = useState(mockStrategyState);
  const [showStateEditor, setShowStateEditor] = useState(false);
  const [editValues, setEditValues]     = useState({ ...mockStrategyState });
  const [proposedMode, setProposedMode] = useState(state.growth_mode);
  const [showLog, setShowLog]           = useState(false);

  const niches = mockNichePerformance;
  const gap    = state.target_daily_revenue - state.actual_daily_revenue;

  function applyState() {
    setState({ ...editValues, growth_mode: proposedMode });
    setShowStateEditor(false);
  }

  const actionableRecs = mockRecommendations.map((r, i) => ({
    ...r,
    actionLabel: ['Spawn Account', 'Adjust Budget', 'View Niches'][i] ?? null,
  }));

  return (
    <div>
      <PageHeader
        title="CEO Brain"
        subtitle="Strategic direction & closed-loop control"
        action={
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <ModeBadge mode={state.growth_mode} />
            <button className="btn btn-primary btn-sm" onClick={() => setShowStateEditor(true)}>
              <Brain size={13} /> Adjust Strategy
            </button>
          </div>
        }
      />

      {/* Primary signal: Performance gap */}
      <div className="card" style={{ marginBottom: '1.25rem', borderLeft: `4px solid ${state.performance_ratio >= 1 ? 'var(--success)' : state.performance_ratio >= 0.7 ? 'var(--warning)' : 'var(--danger)'}` }}>
        <div style={{ display: 'flex', gap: '2rem', alignItems: 'center', flexWrap: 'wrap' }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '0.25rem' }}>Today's Performance Gap</div>
            <div style={{ display: 'flex', gap: '1rem', alignItems: 'baseline', marginBottom: '0.5rem' }}>
              <span style={{ fontSize: '2rem', fontWeight: 800, color: state.performance_ratio >= 1 ? 'var(--success)' : state.performance_ratio >= 0.7 ? 'var(--warning)' : 'var(--danger)', fontVariantNumeric: 'tabular-nums' }}>
                {fmtPct(state.performance_ratio)}
              </span>
              <span style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
                {gap > 0 ? `${fmtCurrency(gap)} short of target` : `${fmtCurrency(Math.abs(gap))} above target`}
              </span>
            </div>
            <div style={{ position: 'relative', height: 8, background: 'var(--border)', borderRadius: 4 }}>
              <div style={{ height: '100%', width: `${Math.min(100, state.performance_ratio * 100)}%`, background: state.performance_ratio >= 1 ? 'var(--success)' : state.performance_ratio >= 0.7 ? 'var(--warning)' : 'var(--danger)', borderRadius: 4 }} />
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '0.25rem', fontSize: '0.7rem', color: 'var(--text-muted)' }}>
              <span>{fmtCurrency(state.actual_daily_revenue)} actual</span>
              <span>{fmtCurrency(state.target_daily_revenue)} target</span>
            </div>
          </div>
          <div style={{ display: 'flex', gap: '1.5rem' }}>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontWeight: 700, color: 'var(--info)', fontVariantNumeric: 'tabular-nums' }}>{state.consecutive_low_cycles}</div>
              <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Low Cycles</div>
            </div>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontWeight: 700, color: 'var(--primary)' }}>×{state.threshold_modifier.toFixed(2)}</div>
              <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Modifier</div>
            </div>
          </div>
          {state.performance_ratio < 0.70 && (
            <button className="btn btn-primary btn-sm" onClick={() => setShowStateEditor(true)}>→ Adjust Strategy</button>
          )}
        </div>
      </div>

      {/* Rank 5: Recommendations as Action Cards */}
      <div className="card" style={{ marginBottom: '1.25rem' }}>
        <SectionHeader title="Strategic Actions Required" />
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          {actionableRecs.map((r, i) => (
            <div key={i} className="card-elevated" style={{ padding: '0.875rem', borderLeft: `3px solid ${r.priority === 'high' ? 'var(--danger)' : r.priority === 'medium' ? 'var(--warning)' : 'var(--border)'}`, display: 'flex', alignItems: 'center', gap: '1rem' }}>
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
      </div>

      <div className="grid-2" style={{ marginBottom: '1.25rem' }}>
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          <div style={{ padding: '1rem 1.25rem', borderBottom: '1px solid var(--border)' }}>
            <SectionHeader title="Niche Budget vs Returns" />
          </div>
          <table className="data-table">
            <thead><tr><th>Niche</th><th>Budget</th><th>Win Rate</th><th>Avg Rev</th><th>Signal</th></tr></thead>
            <tbody>
              {niches.map(n => {
                const roi = n.win_rate / n.budget_share;
                const signal = roi > 2.2 ? 'boost' : roi < 1.2 ? 'cut' : 'hold';
                return (
                  <tr key={n.niche}>
                    <td style={{ textTransform: 'capitalize', fontWeight: 500 }}>{n.niche}</td>
                    <td><span className="badge badge-primary">{fmtPct(n.budget_share)}</span></td>
                    <td>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.375rem' }}>
                        <ScoreBar value={n.win_rate} />
                        <span style={{ color: scoreColor(n.win_rate), fontWeight: 600, fontSize: '0.75rem' }}>{fmtPct(n.win_rate)}</span>
                      </div>
                    </td>
                    <td style={{ color: 'var(--success)' }}>{fmtCurrency(n.avg_revenue)}</td>
                    <td>
                      <span className={`badge ${signal === 'boost' ? 'badge-success' : signal === 'cut' ? 'badge-danger' : 'badge-muted'}`}>
                        {signal === 'boost' ? '↑ BOOST' : signal === 'cut' ? '↓ CUT' : '— HOLD'}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="card">
          <SectionHeader title="Win Rate by Niche" />
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={niches} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
              <XAxis dataKey="niche" tick={{ fill: 'var(--text-muted)', fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis domain={[0, 1]} tickFormatter={v => `${Math.round(v * 100)}%`} tick={{ fill: 'var(--text-muted)', fontSize: 11 }} axisLine={false} tickLine={false} />
              <Tooltip formatter={(v: unknown) => fmtPct(Number(v))} contentStyle={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }} />
              <ReferenceLine y={0.6} stroke="var(--success)" strokeDasharray="4 2" />
              <Bar dataKey="win_rate" radius={[4, 4, 0, 0]}>
                {niches.map((n, i) => <Cell key={i} fill={n.win_rate >= 0.6 ? 'var(--success)' : n.win_rate >= 0.45 ? 'var(--warning)' : 'var(--danger)'} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Rank 10: Strategy log collapsed */}
      <button
        style={{ fontSize: '0.75rem', color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '0.25rem', marginBottom: '0.5rem' }}
        onClick={() => setShowLog(v => !v)}
      >
        {showLog ? <ChevronUp size={12} /> : <ChevronDown size={12} />} Strategy event log ({mockStrategyLog.length} entries)
      </button>
      {showLog && (
        <div className="card">
          <div style={{ maxHeight: 260, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            {mockStrategyLog.map(entry => (
              <div key={entry.id} style={{ display: 'flex', gap: '0.75rem', padding: '0.5rem 0', borderBottom: '1px solid var(--border-subtle)' }}>
                <div style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--primary)', marginTop: '0.35rem', flexShrink: 0 }} />
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: '0.8125rem', fontWeight: 500 }}>{entry.event.replace(/_/g, ' ')}</div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{Object.entries(entry.data).map(([k, v]) => `${k}: ${v}`).join(' · ')}</div>
                </div>
                <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', flexShrink: 0 }}>{fmtRelative(entry.created_at)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Rank 6: Strategy slide-over with ActionImpactPreview */}
      <SlideOver open={showStateEditor} onClose={() => setShowStateEditor(false)} title="Adjust Strategy" width={460}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.5rem', textTransform: 'uppercase' }}>Growth Mode</label>
            <div style={{ display: 'flex', gap: '0.375rem', flexWrap: 'wrap' }}>
              {MODES.map(m => (
                <button key={m} onClick={() => setProposedMode(m)}
                  className={`btn btn-sm ${proposedMode === m ? 'btn-primary' : 'btn-secondary'}`}>
                  {GROWTH_MODES[m].label}
                </button>
              ))}
            </div>
            <ActionImpactPreview currentMode={state.growth_mode} proposedMode={proposedMode} />
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>Target Daily Views: {(editValues.target_daily_views / 1000).toFixed(0)}K</label>
            <input type="range" min={10000} max={500000} step={5000} value={editValues.target_daily_views}
              onChange={e => setEditValues(v => ({ ...v, target_daily_views: +e.target.value }))}
              style={{ width: '100%', accentColor: 'var(--primary)' }} />
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>Target Daily Revenue: {fmtCurrency(editValues.target_daily_revenue)}</label>
            <input type="range" min={5} max={500} step={5} value={editValues.target_daily_revenue}
              onChange={e => setEditValues(v => ({ ...v, target_daily_revenue: +e.target.value }))}
              style={{ width: '100%', accentColor: 'var(--primary)' }} />
          </div>
          <Divider />
          <button className="btn btn-primary" onClick={applyState}>Apply Changes</button>
        </div>
      </SlideOver>
    </div>
  );
}
