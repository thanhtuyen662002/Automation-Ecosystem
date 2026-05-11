// ── Executive Dashboard — Real Analytics Data ──────────────────────────────────
import React from 'react';
import {
  AreaChart, Area, BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts';
import { RefreshCw } from 'lucide-react';
import {
  PageHeader, SectionHeader, Badge, ModeBadge, ScoreBar, StatRow, Skeleton, EmptyState,
  GlassKpiCard, ChartCard,
} from '@/components/ui';
import { GlassIcon } from '@/components/Icons';
import {
  useStrategy, useNiches, useRecommendations, useQueue, useAnalyticsOverview,
} from '@/lib/hooks';
import { fmt, fmtCurrency, fmtPct, fmtScore, CHART_COLORS } from '@/lib/utils';
import { useI18n } from '@/lib/i18n';

export function ExecutiveDashboard() {
  const strategy        = useStrategy();
  const niches          = useNiches();
  const recommendations = useRecommendations();
  const queue           = useQueue();
  const analytics       = useAnalyticsOverview();
  const { t } = useI18n();

  const s             = strategy.data ?? null;
  const nicheList     = niches.data ?? [];
  const recs          = recommendations.data ?? [];
  const queueItems    = queue.data ?? [];
  const analyticsData = analytics.data;

  const isLoading = strategy.isLoading || niches.isLoading || analytics.isLoading;
  const hasError  = strategy.error || niches.error || analytics.error;

  function refetchAll() {
    strategy.refetch(); niches.refetch();
    recommendations.refetch(); queue.refetch(); analytics.refetch();
  }

  if (isLoading) return (
    <div>
      <PageHeader title={t('exec.title')} subtitle={t('exec.sub')} />
      <div className="grid-kpi" style={{ marginBottom: '1.25rem' }}>
        {[1,2,3,4,5].map(i => <div key={i} className="card"><Skeleton height={72} /></div>)}
      </div>
      <div className="grid-2" style={{ marginBottom: '1.25rem' }}>
        <div className="card"><Skeleton height={220} /></div>
        <div className="card"><Skeleton height={220} /></div>
      </div>
    </div>
  );

  if (hasError) return (
    <div>
      <PageHeader title={t('exec.title')} subtitle={t('exec.sub')}
        action={<button className="btn btn-secondary btn-sm" onClick={refetchAll}><RefreshCw size={13} /> {t('exec.refresh')}</button>}
      />
      <div style={{ textAlign: 'center', padding: '4rem 1rem' }}>
        <GlassIcon name="warning" size={48} style={{ marginBottom: '1rem', filter: 'brightness(0) saturate(100%) invert(26%) sepia(90%) saturate(3000%) hue-rotate(345deg)' }} />
        <div style={{ fontSize: '0.9375rem', fontWeight: 600, color: 'var(--danger)', marginBottom: '0.5rem' }}>{t('exec.error_load')}</div>
        <div style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
          {((strategy.error ?? niches.error ?? analytics.error) as Error)?.message ?? t('exec.err_back')}
        </div>
      </div>
    </div>
  );

  const ratio         = s?.performance_ratio ?? 0;
  const viewsTrend    = analyticsData?.views_trend ?? [];
  const funnelData    = analyticsData?.funnel ?? [];
  const topContent    = analyticsData?.top_content ?? [];
  const approvedCount = queueItems.filter((q: any) => q.status === 'approved').length;
  const pendingCount  = queueItems.filter((q: any) => q.status === 'pending').length;

  return (
    <div>
      <PageHeader title={t('exec.title')} subtitle={t('exec.sub')}
        action={
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            {s && <ModeBadge mode={s.growth_mode} />}
            <button className="btn btn-ghost btn-sm btn-icon" onClick={refetchAll} title={t('exec.refresh')}>
              <RefreshCw size={13} />
            </button>
          </div>
        }
      />

      {/* ── KPI Row ─────────────────────────────────────────────────────────── */}
      <div className="grid-kpi" style={{ marginBottom: '1.25rem' }}>
        <GlassKpiCard
          label={t('exec.daily_views')}
          value={s ? fmt(s.actual_daily_views) : '—'}
          sub={s ? `${t('exec.target')}: ${fmt(s.target_daily_views)}` : t('exec.no_data')}
          icon={<GlassIcon name="eye" size={28} />}
          iconBg="rgba(59,130,246,0.12)" iconColor="#3b82f6"
          delta={s ? fmtPct(ratio) : undefined}
          deltaUp={ratio >= 1}
        />
        <GlassKpiCard
          label={t('exec.daily_rev')}
          value={s ? fmtCurrency(s.actual_daily_revenue) : '—'}
          sub={s ? `${t('exec.target')}: ${fmtCurrency(s.target_daily_revenue)}` : t('exec.no_data')}
          icon={<GlassIcon name="currency" size={28} />}
          iconBg="rgba(245,158,11,0.12)" iconColor="#f59e0b"
          delta={s ? (s.actual_daily_revenue >= s.target_daily_revenue ? 'On target' : 'Below') : undefined}
          deltaUp={s ? s.actual_daily_revenue >= s.target_daily_revenue : false}
        />
        <GlassKpiCard
          label={t('exec.perf_ratio')}
          value={s ? fmtPct(ratio) : '—'}
          sub={ratio >= 1 ? t('exec.on_target') : ratio >= 0.7 ? t('exec.near_target') : t('exec.below_target')}
          icon={<GlassIcon name="chart" size={28} />}
          iconBg={ratio >= 1 ? 'rgba(16,185,129,0.12)' : ratio >= 0.7 ? 'rgba(245,158,11,0.12)' : 'rgba(239,68,68,0.12)'}
          iconColor={ratio >= 1 ? '#10b981' : ratio >= 0.7 ? '#f59e0b' : '#ef4444'}
        />
        <GlassKpiCard
          label={t('exec.cont_app')}
          value={`${approvedCount} / ${queueItems.length}`}
          sub={`${pendingCount} ${t('exec.pend_rev')}`}
          icon={<GlassIcon name="check-circle" size={28} />}
          iconBg="rgba(16,185,129,0.12)" iconColor="#10b981"
        />
        <GlassKpiCard
          label={t('exec.growth_mode')}
          value={s ? s.growth_mode.toUpperCase() : '—'}
          sub={s ? `${t('exec.explore')}: ${fmtPct(s.exploration_rate)} · ×${s.threshold_modifier?.toFixed(2)}` : ''}
          icon={<GlassIcon name="rocket" size={28} />}
          iconBg="rgba(124,58,237,0.10)" iconColor="#7c3aed"
        />
      </div>

      {/* ── Chart Row ───────────────────────────────────────────────────────── */}
      <div className="grid-2" style={{ marginBottom: '1.25rem' }}>
        <ChartCard title={t('exec.chart_views')} blobLeft="rgba(139,92,246,0.38)" blobRight="rgba(20,184,166,0.28)" minHeight={220}>
          {viewsTrend.length === 0 ? (
            <EmptyState icon="chart" message={t('exec.no_trend')} />
          ) : (
            <>
              {(() => {
                const today    = viewsTrend[viewsTrend.length - 1];
                const yesterday = viewsTrend[viewsTrend.length - 2];
                const velocity  = today && yesterday && yesterday.views > 0 ? (today.views / yesterday.views) : 1;
                const eodViews  = today ? Math.round(today.views * velocity) : 0;
                const onTrack   = s ? eodViews >= s.target_daily_views : false;
                return (
                  <div style={{ fontSize: '0.75rem', marginBottom: '0.5rem', color: onTrack ? 'var(--success)' : 'var(--warning)', display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                    <GlassIcon name={onTrack ? 'check-circle' : 'warning'} size={13} style={{ filter: `brightness(0) saturate(100%) ${onTrack ? 'invert(52%) sepia(70%) saturate(600%) hue-rotate(120deg)' : 'invert(70%) sepia(60%) saturate(900%) hue-rotate(15deg)'}` }} />
                    {t('exec.projected')}: <strong>{(eodViews / 1000).toFixed(0)}K {t('exec.views')}</strong>
                    {s && <span> ({onTrack ? t('exec.on_track') : `${((eodViews / s.target_daily_views) * 100).toFixed(0)}${t('exec.pct_target')}`})</span>}
                  </div>
                );
              })()}
              <ResponsiveContainer width="100%" height={190}>
                <AreaChart data={viewsTrend} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
                  <defs>
                    <linearGradient id="gViews" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%"  stopColor="#8b5cf6" stopOpacity={0.45} />
                      <stop offset="95%" stopColor="#8b5cf6" stopOpacity={0.02} />
                    </linearGradient>
                    <linearGradient id="gRev" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%"  stopColor="#14b8a6" stopOpacity={0.35} />
                      <stop offset="95%" stopColor="#14b8a6" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="day" tick={{ fill: 'var(--text-muted)', fontSize: 11 }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: 'var(--text-muted)', fontSize: 11 }} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12, boxShadow: 'var(--shadow-card)' }} />
                  {s && <ReferenceLine y={s.target_daily_views} stroke="#8b5cf6" strokeDasharray="5 3" strokeOpacity={0.5} />}
                  <Area type="monotoneX" dataKey="views"   stroke="#8b5cf6" fill="url(#gViews)" strokeWidth={2.5} name={t('exec.lbl_views')} dot={false} />
                  <Area type="monotoneX" dataKey="revenue" stroke="#14b8a6" fill="url(#gRev)"   strokeWidth={2}   name={t('exec.lbl_rev')}   dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            </>
          )}
        </ChartCard>

        <ChartCard title={t('exec.niche_alloc')} blobLeft="rgba(236,72,153,0.28)" blobRight="rgba(245,158,11,0.25)" minHeight={220}>
          {nicheList.length === 0 ? (
            <EmptyState icon="compass" message={t('exec.no_niche')} />
          ) : (
            <div style={{ display: 'flex', gap: '1rem', alignItems: 'center' }}>
              <ResponsiveContainer width={160} height={160}>
                <PieChart>
                  <Pie data={nicheList} dataKey="budget_share" nameKey="niche" cx="50%" cy="50%" outerRadius={70} innerRadius={42} strokeWidth={0}>
                    {nicheList.map((_: any, i: number) => (
                      <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip formatter={(v: unknown) => fmtPct(Number(v))} contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }} />
                </PieChart>
              </ResponsiveContainer>
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
                {nicheList.map((n: any, i: number) => (
                  <div key={n.niche}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.2rem' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                        <div style={{ width: 8, height: 8, borderRadius: '50%', background: CHART_COLORS[i % CHART_COLORS.length], flexShrink: 0 }} />
                        <span style={{ fontSize: '0.75rem', textTransform: 'capitalize' }}>{n.niche}</span>
                      </div>
                      <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{fmtPct(n.budget_share ?? 0)}</span>
                    </div>
                    <div className="score-bar" style={{ height: 4 }}>
                      <div className="score-bar-fill" style={{ width: `${(n.budget_share ?? 0) * 100}%`, background: CHART_COLORS[i % CHART_COLORS.length] }} />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </ChartCard>
      </div>

      {/* ── Win Rates + Funnel ──────────────────────────────────────────────── */}
      <div className="grid-2" style={{ marginBottom: '1.25rem' }}>
        <ChartCard title={t('exec.niche_win')} blobLeft="rgba(139,92,246,0.30)" minHeight={180}>
          {nicheList.length === 0 ? (
            <EmptyState icon="chart" message={t('exec.no_data')} />
          ) : (
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={nicheList} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
                <XAxis dataKey="niche" tick={{ fill: 'var(--text-muted)', fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis domain={[0, 1]} tickFormatter={v => `${Math.round(v * 100)}%`} tick={{ fill: 'var(--text-muted)', fontSize: 11 }} axisLine={false} tickLine={false} />
                <Tooltip formatter={(v: unknown) => fmtPct(Number(v))} contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }} />
                <Bar dataKey="win_rate" radius={[6, 6, 0, 0]} name={t('exec.win_rate')}>
                  {nicheList.map((n: any, i: number) => (
                    <Cell key={i} fill={n.win_rate >= 0.6 ? '#10b981' : n.win_rate >= 0.45 ? '#f59e0b' : '#ef4444'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </ChartCard>

        <div className="card">
          <SectionHeader title={t('exec.funnel')} />
          {funnelData.length === 0 ? (
            <EmptyState icon="filter" message={t('exec.no_funnel')} />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', padding: '0.5rem 0' }}>
              {funnelData.map((stage: any, i: number) => {
                const pct = funnelData[0].value > 0 ? stage.value / funnelData[0].value : 0;
                return (
                  <div key={stage.stage}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.3rem' }}>
                      <span style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>{stage.stage}</span>
                      <span style={{ fontSize: '0.8125rem', fontWeight: 600 }}>{fmt(stage.value)}</span>
                    </div>
                    <div className="score-bar" style={{ height: 8, borderRadius: 4 }}>
                      <div className="score-bar-fill" style={{ width: `${pct * 100}%`, background: CHART_COLORS[i % CHART_COLORS.length], borderRadius: 4 }} />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* ── Top Content + CEO Recommendations ─────────────────────────────── */}
      <div className="grid-2">
        <div className="card">
          <SectionHeader title={t('exec.top_cont')} />
          {topContent.length === 0 ? (
            <EmptyState icon="video" message={t('exec.no_metrics')} />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.625rem' }}>
              {topContent.map((v: any) => (
                <div key={v.video_id} style={{ padding: '0.75rem', background: 'var(--bg-soft)', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)' }}>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.5rem', marginBottom: '0.4rem' }}>
                    <GlassIcon name="play-circle" size={14} style={{ marginTop: '0.1rem', opacity: 0.6, flexShrink: 0 }} />
                    <div style={{ fontSize: '0.8125rem', fontWeight: 500, color: 'var(--text-primary)' }}>
                      "{v.hook_text ?? v.video_id}"
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', paddingLeft: '1.25rem' }}>
                    <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'flex', gap: '0.25rem', alignItems: 'center' }}>
                      <GlassIcon name="eye" size={11} style={{ opacity: 0.5 }} /> {fmt(v.views ?? 0)}
                    </span>
                    <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'flex', gap: '0.25rem', alignItems: 'center' }}>
                      <GlassIcon name="heart" size={11} style={{ opacity: 0.5 }} /> {fmt(v.likes ?? 0)}
                    </span>
                    {v.watch_time && <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>⏱ {v.watch_time}s</span>}
                    <span style={{ fontSize: '0.75rem', color: 'var(--success)' }}>{t('exec.score')} {fmtScore(v.performance_score ?? 0)}</span>
                  </div>
                  <div style={{ marginTop: '0.375rem' }}>
                    <ScoreBar value={v.performance_score ?? 0} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="card">
          <SectionHeader title={t('exec.ceo_recs')} />
          {recs.length === 0 ? (
            <EmptyState icon="planet" message={t('exec.no_recs')} />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.625rem' }}>
              {recs.map((r: any, i: number) => (
                <div key={i} style={{
                  padding: '0.75rem',
                  background: 'var(--bg-soft)',
                  borderRadius: 'var(--radius-sm)',
                  border: '1px solid var(--border)',
                  borderLeft: `3px solid ${r.priority === 'high' ? 'var(--danger)' : r.priority === 'medium' ? 'var(--warning)' : 'var(--border)'}`,
                }}>
                  <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'flex-start' }}>
                    <GlassIcon name={r.priority === 'high' ? 'warning' : r.priority === 'medium' ? 'info' : 'check-circle'} size={14} style={{ marginTop: '0.1rem', flexShrink: 0, opacity: 0.7 }} />
                    <div>
                      <Badge status={r.priority === 'high' ? 'danger' : r.priority === 'medium' ? 'warning' : 'muted'}>{r.priority}</Badge>
                      <span style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', marginLeft: '0.4rem' }}>{r.message}</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {queueItems.length > 0 && (
            <div style={{ marginTop: '1rem' }}>
              <SectionHeader title={t('exec.queue_status')} />
              {(['pending', 'approved', 'rejected'] as const).map(status => {
                const items = queueItems.filter((q: any) => q.status === status);
                if (items.length === 0) return null;
                const avgScore = items.reduce((s: number, q: any) => s + (q.final_score ?? 0), 0) / items.length;
                return (
                  <StatRow key={status}
                    label={status.charAt(0).toUpperCase() + status.slice(1)}
                    value={<span>{items.length} {t('exec.items_avg')} {fmtScore(avgScore)}</span>}
                  />
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
