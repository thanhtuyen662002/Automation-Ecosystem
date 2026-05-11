// ── Niche Performance ─────────────────────────────────────────────────────────
import React, { useState } from 'react';
import { Plus } from 'lucide-react';
import { PageHeader, Badge, SectionHeader, ScoreBar, SlideOver, StatRow } from '@/components/ui';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts';
import { mockNichePerformance } from '@/lib/mock';
import { fmtCurrency, fmtPct, fmtScore, scoreColor, CHART_COLORS } from '@/lib/utils';

// Phase D: no longer needed — use shared CHART_COLORS from utils.ts

export function NichePerformance() {
  const [niches, setNiches] = useState(mockNichePerformance);
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState({ niche: '', platform: 'tiktok', win_rate: 0.5, avg_views: 10000, avg_revenue: 10, posts_count: 0, growth_potential: 0.5 });

  function upsert() {
    const existing = niches.find(n => n.niche === form.niche && n.platform === form.platform);
    if (existing) {
      setNiches(prev => prev.map(n => n.niche === form.niche ? { ...n, ...form, budget_share: form.win_rate * form.growth_potential } : n));
    } else {
      setNiches(prev => [...prev, { ...form, budget_share: form.win_rate * form.growth_potential }]);
    }
    setShowAdd(false);
  }

  const radarData = niches.map(n => ({
    niche: n.niche,
    'Win Rate': Math.round(n.win_rate * 100),
    'Growth': Math.round(n.growth_potential * 100),
    'Budget': Math.round(n.budget_share * 100),
  }));

  return (
    <div>
      <PageHeader
        title="Niche Performance"
        subtitle="Budget allocation, win rates, and growth potential per niche"
        action={<button className="btn btn-primary btn-sm" onClick={() => setShowAdd(true)}><Plus size={13} /> Add / Update Niche</button>}
      />

      <div className="grid-2" style={{ marginBottom: '1.25rem' }}>
        <div className="card">
          <SectionHeader title="Win Rate by Niche" />
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={niches} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
              <XAxis dataKey="niche" tick={{ fill: 'var(--text-muted)', fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis domain={[0, 1]} tickFormatter={v => `${Math.round(v * 100)}%`} tick={{ fill: 'var(--text-muted)', fontSize: 11 }} axisLine={false} tickLine={false} />
              <Tooltip formatter={(v: unknown) => fmtPct(Number(v))} contentStyle={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }} />
              <Bar dataKey="win_rate" radius={[4, 4, 0, 0]} name="Win Rate">
                {niches.map((n, i) => <Cell key={i} fill={n.win_rate >= 0.65 ? 'var(--success)' : n.win_rate >= 0.45 ? 'var(--warning)' : 'var(--danger)'} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="card">
          <SectionHeader title="Revenue vs Views" />
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={niches} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
              <XAxis dataKey="niche" tick={{ fill: 'var(--text-muted)', fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: 'var(--text-muted)', fontSize: 11 }} axisLine={false} tickLine={false} />
              <Tooltip contentStyle={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }} />
              <Bar dataKey="avg_revenue" radius={[4, 4, 0, 0]} name="Avg Revenue $" fill="var(--success)" opacity={0.85} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Detail Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '1rem', marginBottom: '1.25rem' }}>
        {niches.map((n, i) => (
          <div key={n.niche} className="card">
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', marginBottom: '0.875rem' }}>
              <div style={{ width: 10, height: 10, borderRadius: '50%', background: CHART_COLORS[i % CHART_COLORS.length], flexShrink: 0 }} />
              <div style={{ fontWeight: 700, fontSize: '0.9375rem', textTransform: 'capitalize' }}>{n.niche}</div>
              <Badge status="info">{n.platform}</Badge>
              <div style={{ marginLeft: 'auto' }}>
                <span className="badge badge-primary">{fmtPct(n.budget_share)}</span>
              </div>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.2rem' }}>
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Win Rate</span>
                  <span style={{ fontSize: '0.75rem', color: scoreColor(n.win_rate), fontWeight: 600 }}>{fmtPct(n.win_rate)}</span>
                </div>
                <ScoreBar value={n.win_rate} />
              </div>
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.2rem' }}>
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Growth Potential</span>
                  <span style={{ fontSize: '0.75rem', color: scoreColor(n.growth_potential), fontWeight: 600 }}>{fmtPct(n.growth_potential)}</span>
                </div>
                <ScoreBar value={n.growth_potential} />
              </div>
            </div>
            <div style={{ marginTop: '0.75rem', display: 'flex', gap: '1rem' }}>
              <div><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Avg Views</div><div style={{ fontWeight: 600, fontSize: '0.875rem' }}>{(n.avg_views / 1000).toFixed(0)}K</div></div>
              <div><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Avg Revenue</div><div style={{ fontWeight: 600, fontSize: '0.875rem', color: 'var(--success)' }}>{fmtCurrency(n.avg_revenue)}</div></div>
              <div><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Posts</div><div style={{ fontWeight: 600, fontSize: '0.875rem' }}>{n.posts_count}</div></div>
            </div>
          </div>
        ))}
      </div>

      <SlideOver open={showAdd} onClose={() => setShowAdd(false)} title="Add / Update Niche">
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {(['niche', 'platform'] as const).map(field => (
            <div key={field}>
              <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>{field.charAt(0).toUpperCase() + field.slice(1)}</label>
              {field === 'platform'
                ? <select className="select" value={form.platform} onChange={e => setForm(f => ({ ...f, platform: e.target.value }))}><option value="tiktok">TikTok</option><option value="facebook">Facebook</option></select>
                : <input className="input" value={form[field]} onChange={e => setForm(f => ({ ...f, [field]: e.target.value }))} />
              }
            </div>
          ))}
          {[['win_rate', 'Win Rate', 0, 1, 0.01], ['growth_potential', 'Growth Potential', 0, 1, 0.01], ['avg_views', 'Avg Views', 1000, 500000, 1000], ['avg_revenue', 'Avg Revenue', 0, 100, 0.5]].map(([key, label, min, max, step]) => (
            <div key={String(key)}>
              <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>
                {String(label)}: {key === 'avg_views' ? `${((form as any)[key] / 1000).toFixed(0)}K` : key === 'avg_revenue' ? fmtCurrency((form as any)[key]) : fmtScore((form as any)[key])}
              </label>
              <input type="range" min={Number(min)} max={Number(max)} step={Number(step)} value={(form as any)[key]}
                onChange={e => setForm(f => ({ ...f, [key as string]: +e.target.value }))}
                style={{ width: '100%', accentColor: 'var(--primary)' }} />
            </div>
          ))}
          <button className="btn btn-primary" disabled={!form.niche} onClick={upsert}>Save Niche</button>
        </div>
      </SlideOver>
    </div>
  );
}
