// ── Niche Performance ─────────────────────────────────────────────────────────
import React, { useState } from 'react';
import { Plus, RefreshCw, AlertCircle } from 'lucide-react';
import { PageHeader, Badge, SectionHeader, ScoreBar, SlideOver, EmptyState, Skeleton } from '@/components/ui';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts';
import { useI18n } from '@/lib/i18n';
import { useNiches, useUpsertNiche } from '@/lib/hooks';
import { fmtCurrency, fmtPct, fmtScore, scoreColor, CHART_COLORS } from '@/lib/utils';

const DEFAULT_FORM = { niche: '', platform: 'tiktok', win_rate: 0.5, avg_views: 10000, avg_revenue: 10, posts_count: 0, growth_potential: 0.5 };

export function NichePerformance() {
  const { t } = useI18n();
  const { data: niches = [], isLoading, error, refetch } = useNiches();
  const upsert = useUpsertNiche();
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState(DEFAULT_FORM);
  const [saveError, setSaveError] = useState('');

  async function handleSave() {
    setSaveError('');
    try {
      await upsert.mutateAsync(form);
      setShowAdd(false);
      setForm(DEFAULT_FORM);
    } catch (e: any) {
      setSaveError(e.message ?? 'Failed to save niche');
    }
  }

  return (
    <div>
      <PageHeader
        title={t('niche.title')}
        subtitle={t('niche.sub')}
        action={
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button className="btn btn-ghost btn-sm btn-icon" onClick={() => refetch()} title={t('cmd.retry')}>
              <RefreshCw size={13} />
            </button>
            <button className="btn btn-primary btn-sm" onClick={() => setShowAdd(true)}>
              <Plus size={13} /> {t('niche.act_add')}
            </button>
          </div>
        }
      />

      {isLoading ? (
        <div className="grid-2" style={{ marginBottom: '1.25rem' }}>
          <div className="card"><Skeleton height={200} /></div>
          <div className="card"><Skeleton height={200} /></div>
        </div>
      ) : error ? (
        <div style={{ textAlign: 'center', padding: '3rem 1rem', color: 'var(--danger)' }}>
          <AlertCircle size={32} style={{ marginBottom: '0.75rem', opacity: 0.7 }} />
          <div style={{ fontSize: '0.875rem', marginBottom: '1rem' }}>{(error as Error).message}</div>
          <button className="btn btn-secondary btn-sm" onClick={() => refetch()}>
            <RefreshCw size={13} /> {t('cmd.retry')}
          </button>
        </div>
      ) : niches.length === 0 ? (
        <EmptyState icon="📊" message={t('niche.no_data')} />
      ) : (
        <>
          <div className="grid-2" style={{ marginBottom: '1.25rem' }}>
            <div className="card">
              <SectionHeader title={t('niche.title_win')} />
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={niches} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
                  <XAxis dataKey="niche" tick={{ fill: 'var(--text-muted)', fontSize: 11 }} axisLine={false} tickLine={false} />
                  <YAxis domain={[0, 1]} tickFormatter={v => `${Math.round(v * 100)}%`} tick={{ fill: 'var(--text-muted)', fontSize: 11 }} axisLine={false} tickLine={false} />
                  <Tooltip formatter={(v: unknown) => fmtPct(Number(v))} contentStyle={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }} />
                  <Bar dataKey="win_rate" radius={[4, 4, 0, 0]} name={t('niche.lbl_win')}>
                    {niches.map((n: any, i: number) => (
                      <Cell key={i} fill={n.win_rate >= 0.65 ? 'var(--success)' : n.win_rate >= 0.45 ? 'var(--warning)' : 'var(--danger)'} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>

            <div className="card">
              <SectionHeader title={t('niche.title_rev')} />
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={niches} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
                  <XAxis dataKey="niche" tick={{ fill: 'var(--text-muted)', fontSize: 11 }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: 'var(--text-muted)', fontSize: 11 }} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }} />
                  <Bar dataKey="avg_revenue" radius={[4, 4, 0, 0]} name={t('niche.lbl_avg_rev_usd')} fill="var(--success)" opacity={0.85} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Detail Cards */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '1rem', marginBottom: '1.25rem' }}>
            {niches.map((n: any, i: number) => (
              <div key={n.niche} className="card">
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', marginBottom: '0.875rem' }}>
                  <div style={{ width: 10, height: 10, borderRadius: '50%', background: CHART_COLORS[i % CHART_COLORS.length], flexShrink: 0 }} />
                  <div style={{ fontWeight: 700, fontSize: '0.9375rem', textTransform: 'capitalize' }}>{n.niche}</div>
                  <Badge status="info">{n.platform}</Badge>
                  <div style={{ marginLeft: 'auto' }}>
                    <span className="badge badge-primary">{fmtPct(n.budget_share ?? 0)}</span>
                  </div>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.2rem' }}>
                      <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{t('niche.lbl_win')}</span>
                      <span style={{ fontSize: '0.75rem', color: scoreColor(n.win_rate), fontWeight: 600 }}>{fmtPct(n.win_rate)}</span>
                    </div>
                    <ScoreBar value={n.win_rate} />
                  </div>
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.2rem' }}>
                      <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{t('niche.lbl_growth')}</span>
                      <span style={{ fontSize: '0.75rem', color: scoreColor(n.growth_potential), fontWeight: 600 }}>{fmtPct(n.growth_potential)}</span>
                    </div>
                    <ScoreBar value={n.growth_potential} />
                  </div>
                </div>
                <div style={{ marginTop: '0.75rem', display: 'flex', gap: '1rem' }}>
                  <div><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{t('niche.lbl_views')}</div><div style={{ fontWeight: 600, fontSize: '0.875rem' }}>{((n.avg_views ?? 0) / 1000).toFixed(0)}K</div></div>
                  <div><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{t('niche.lbl_rev')}</div><div style={{ fontWeight: 600, fontSize: '0.875rem', color: 'var(--success)' }}>{fmtCurrency(n.avg_revenue ?? 0)}</div></div>
                  <div><div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{t('niche.lbl_posts')}</div><div style={{ fontWeight: 600, fontSize: '0.875rem' }}>{n.posts_count ?? 0}</div></div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      <SlideOver open={showAdd} onClose={() => { setShowAdd(false); setSaveError(''); setForm(DEFAULT_FORM); }} title={t('niche.act_add')}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {(['niche', 'platform'] as const).map(field => (
            <div key={field}>
              <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>
                {field === 'platform' ? t('niche.lbl_platform') : t('niche.lbl_niche')}
              </label>
              {field === 'platform'
                ? <select className="select" value={form.platform} onChange={e => setForm(f => ({ ...f, platform: e.target.value }))}><option value="tiktok">TikTok</option><option value="facebook">Facebook</option></select>
                : <input className="input" value={form[field]} onChange={e => setForm(f => ({ ...f, [field]: e.target.value }))} />
              }
            </div>
          ))}
          {[
            ['win_rate', t('niche.lbl_win'), 0, 1, 0.01],
            ['growth_potential', t('niche.lbl_growth'), 0, 1, 0.01],
            ['avg_views', t('niche.lbl_views'), 1000, 500000, 1000],
            ['avg_revenue', t('niche.lbl_rev'), 0, 100, 0.5],
          ].map(([key, label, min, max, step]) => (
            <div key={String(key)}>
              <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>
                {String(label)}: {key === 'avg_views' ? `${((form as any)[key] / 1000).toFixed(0)}K` : key === 'avg_revenue' ? fmtCurrency((form as any)[key]) : fmtScore((form as any)[key])}
              </label>
              <input type="range" min={Number(min)} max={Number(max)} step={Number(step)} value={(form as any)[key]}
                onChange={e => setForm(f => ({ ...f, [key as string]: +e.target.value }))}
                style={{ width: '100%', accentColor: 'var(--primary)' }} />
            </div>
          ))}
          {saveError && (
            <div style={{ padding: '0.5rem 0.75rem', background: 'var(--danger-muted)', border: '1px solid var(--danger)', borderRadius: 'var(--radius-sm)', fontSize: '0.8125rem', color: 'var(--danger)' }}>
              {saveError}
            </div>
          )}
          <button className="btn btn-primary" disabled={!form.niche.trim() || upsert.isPending} onClick={handleSave}>
            {upsert.isPending ? t('niche.saving') : t('niche.btn_save')}
          </button>
        </div>
      </SlideOver>
    </div>
  );
}
