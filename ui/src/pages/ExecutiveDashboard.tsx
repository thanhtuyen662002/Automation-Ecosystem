// ── Executive Dashboard (CEO Strategic View) ──────────────────────────────────
import React from 'react';
import {
  AreaChart, Area, BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts';
import { TrendingUp, DollarSign, Eye, Target, Brain, CheckCircle } from 'lucide-react';
import {
  KpiCard, PageHeader, SectionHeader, Badge, ModeBadge, ScoreBar, StatRow,
} from '@/components/ui';
import {
  mockStrategyState, mockNichePerformance, mockInsights,
  mockBrainQueue, mockVideoMetrics, mockRecommendations,
  mockViewsTrend, mockFunnelData,   // Phase C: from mock.ts, not local constants
} from '@/lib/mock';
import { fmt, fmtCurrency, fmtPct, fmtScore, CHART_COLORS } from '@/lib/utils'; // Phase D: shared palette

export function ExecutiveDashboard() {
  const s = mockStrategyState;
  const ratio = s.performance_ratio;

  // Phase C: compute CEO-relevant KPI — content approved today
  const approvedToday = mockBrainQueue.filter(q => q.status === 'approved').length;
  const totalQueue    = mockBrainQueue.length;

  return (
    <div>
      <PageHeader
        title="Executive Dashboard"
        subtitle="Performance overview · updated live"
        action={<ModeBadge mode={s.growth_mode} />}
      />

      {/* Phase C: 5 CEO-relevant KPI cards — Tasks Success removed, Content Approved added */}
      <div className="grid-kpi" style={{ marginBottom: '1.25rem' }}>
        <KpiCard
          label="Daily Views"
          value={fmt(s.actual_daily_views)}
          sub={`Target: ${fmt(s.target_daily_views)}`}
          icon={<Eye size={20} />}
          color="var(--primary)"
          trend={ratio >= 1 ? 'up' : ratio >= 0.7 ? 'neutral' : 'down'}
        />
        <KpiCard
          label="Daily Revenue"
          value={fmtCurrency(s.actual_daily_revenue)}
          sub={`Target: ${fmtCurrency(s.target_daily_revenue)}`}
          icon={<DollarSign size={20} />}
          color="var(--success)"
          trend={s.actual_daily_revenue >= s.target_daily_revenue ? 'up' : 'down'}
        />
        <KpiCard
          label="Performance Ratio"
          value={fmtPct(ratio)}
          sub={ratio >= 1 ? 'On target' : ratio >= 0.7 ? 'Near target' : 'Below target'}
          icon={<TrendingUp size={20} />}
          color={ratio >= 1 ? 'var(--success)' : ratio >= 0.7 ? 'var(--warning)' : 'var(--danger)'}
        />
        {/* Phase C: replaced "Tasks Success" (operational) with "Content Approved" (strategic) */}
        <KpiCard
          label="Content Approved"
          value={`${approvedToday} / ${totalQueue}`}
          sub={`${mockBrainQueue.filter(q => q.status === 'pending').length} pending review`}
          icon={<CheckCircle size={20} />}
          color="var(--info)"
        />
        <KpiCard
          label="Growth Mode"
          value={s.growth_mode.toUpperCase()}
          sub={`Explore: ${fmtPct(s.exploration_rate)} · ×${s.threshold_modifier.toFixed(2)}`}
          icon={<Brain size={20} />}
          color="var(--primary)"
        />
      </div>

      {/* Charts Row */}
      <div className="grid-2" style={{ marginBottom: '1.25rem' }}>
        {/* Rank 9: target reference line + EOD projection note */}
        <div className="card">
          <SectionHeader title="7-Day Views & Revenue" />
          {/* EOD velocity note */}
          {(() => {
            const today = mockViewsTrend[mockViewsTrend.length - 1];
            const yesterday = mockViewsTrend[mockViewsTrend.length - 2];
            const velocity = today && yesterday ? (today.views / yesterday.views) : 1;
            const eodViews = today ? Math.round(today.views * velocity) : 0;
            const onTrack = eodViews >= s.target_daily_views;
            return (
              <div style={{ fontSize: '0.75rem', marginBottom: '0.5rem', color: onTrack ? 'var(--success)' : 'var(--warning)' }}>
                {onTrack ? '✓' : '⚠'} At current pace — projected today: <strong>{(eodViews / 1000).toFixed(0)}K views</strong>
                {' '}({onTrack ? 'on track' : `${((eodViews / s.target_daily_views) * 100).toFixed(0)}% of ${(s.target_daily_views / 1000).toFixed(0)}K target`})
              </div>
            );
          })()}
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={mockViewsTrend} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="gViews" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#6366F1" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#6366F1" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="gRev" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#10B981" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#10B981" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="day" tick={{ fill: 'var(--text-muted)', fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: 'var(--text-muted)', fontSize: 11 }} axisLine={false} tickLine={false} />
              <Tooltip contentStyle={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }} />
              {/* Rank 9: target reference lines — the gap is now visible on the chart */}
              <ReferenceLine y={s.target_daily_views} stroke="#6366F1" strokeDasharray="5 3" strokeOpacity={0.5} label={{ value: 'target', fill: '#6366F1', fontSize: 9, opacity: 0.7 }} />
              <ReferenceLine y={s.target_daily_revenue} stroke="#10B981" strokeDasharray="5 3" strokeOpacity={0.5} />
              <Area type="monotone" dataKey="views"   stroke="#6366F1" fill="url(#gViews)" strokeWidth={2} name="Views" />
              <Area type="monotone" dataKey="revenue" stroke="#10B981" fill="url(#gRev)"   strokeWidth={2} name="Revenue $" />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        <div className="card">
          <SectionHeader title="Niche Budget Allocation" />
          <div style={{ display: 'flex', gap: '1rem', alignItems: 'center' }}>
            <ResponsiveContainer width={160} height={160}>
              <PieChart>
                {/* Phase D: CHART_COLORS from utils.ts */}
                <Pie data={mockNichePerformance} dataKey="budget_share" nameKey="niche" cx="50%" cy="50%" outerRadius={70} innerRadius={40} strokeWidth={0}>
                  {mockNichePerformance.map((_, i) => (
                    <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip formatter={(v: unknown) => fmtPct(Number(v))} contentStyle={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }} />
              </PieChart>
            </ResponsiveContainer>
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
              {mockNichePerformance.map((n, i) => (
                <div key={n.niche}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.2rem' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                      <div style={{ width: 8, height: 8, borderRadius: '50%', background: CHART_COLORS[i % CHART_COLORS.length], flexShrink: 0 }} />
                      <span style={{ fontSize: '0.75rem', textTransform: 'capitalize' }}>{n.niche}</span>
                    </div>
                    <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{fmtPct(n.budget_share)}</span>
                  </div>
                  <div className="score-bar" style={{ height: 4 }}>
                    <div className="score-bar-fill" style={{ width: `${n.budget_share * 100}%`, background: CHART_COLORS[i % CHART_COLORS.length] }} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Niche Win Rates + Conversion Funnel */}
      <div className="grid-2" style={{ marginBottom: '1.25rem' }}>
        <div className="card">
          <SectionHeader title="Niche Win Rates" />
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={mockNichePerformance} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
              <XAxis dataKey="niche" tick={{ fill: 'var(--text-muted)', fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis domain={[0, 1]} tickFormatter={v => `${Math.round(v * 100)}%`} tick={{ fill: 'var(--text-muted)', fontSize: 11 }} axisLine={false} tickLine={false} />
              <Tooltip formatter={(v: unknown) => fmtPct(Number(v))} contentStyle={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }} />
              <Bar dataKey="win_rate" radius={[4, 4, 0, 0]} name="Win Rate">
                {mockNichePerformance.map((n, i) => (
                  <Cell key={i} fill={n.win_rate >= 0.6 ? 'var(--success)' : n.win_rate >= 0.45 ? 'var(--warning)' : 'var(--danger)'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="card">
          <SectionHeader title="Conversion Funnel" />
          {/* Phase C: data from mockFunnelData (mock.ts) */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.625rem', padding: '0.5rem 0' }}>
            {mockFunnelData.map((stage, i) => {
              const pct = stage.value / mockFunnelData[0].value;
              return (
                <div key={stage.stage}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.25rem' }}>
                    <span style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>{stage.stage}</span>
                    <span style={{ fontSize: '0.8125rem', fontWeight: 600 }}>{fmt(stage.value)}</span>
                  </div>
                  <div className="score-bar" style={{ height: 8 }}>
                    <div className="score-bar-fill" style={{ width: `${pct * 100}%`, background: CHART_COLORS[i % CHART_COLORS.length] }} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Top Content + CEO Recommendations */}
      <div className="grid-2">
        <div className="card">
          <SectionHeader title="Top Performing Content" />
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.625rem' }}>
            {mockVideoMetrics.map((v) => (
              <div key={v.video_id} className="card-elevated" style={{ padding: '0.75rem' }}>
                <div style={{ fontSize: '0.8125rem', fontWeight: 500, marginBottom: '0.4rem', color: 'var(--text-primary)' }}>
                  "{v.hook_text}"
                </div>
                <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>👁 {fmt(v.views)}</span>
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>❤ {fmt(v.likes)}</span>
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>⏱ {v.watch_time}s</span>
                  <span style={{ fontSize: '0.75rem', color: 'var(--success)' }}>Score {fmtScore(v.performance_score)}</span>
                </div>
                <ScoreBar value={v.performance_score} label="" />
              </div>
            ))}
          </div>
        </div>

        <div className="card">
          <SectionHeader title="CEO Recommendations" />
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.625rem' }}>
            {mockRecommendations.map((r, i) => (
              <div key={i} className="card-elevated" style={{ padding: '0.75rem', borderLeft: `3px solid ${r.priority === 'high' ? 'var(--danger)' : r.priority === 'medium' ? 'var(--warning)' : 'var(--border)'}` }}>
                <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'flex-start' }}>
                  <Badge status={r.priority === 'high' ? 'danger' : r.priority === 'medium' ? 'warning' : 'muted'}>{r.priority}</Badge>
                  <span style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>{r.message}</span>
                </div>
              </div>
            ))}
          </div>

          <div style={{ marginTop: '1rem' }}>
            <SectionHeader title="Queue Status" />
            {mockInsights.queue_summary.map(q => (
              <StatRow key={q.status}
                label={q.status.charAt(0).toUpperCase() + q.status.slice(1)}
                value={<span>{q.cnt} items · avg {fmtScore(q.avg_score)}</span>}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
