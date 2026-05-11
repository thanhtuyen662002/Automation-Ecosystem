// ── Settings — Advanced ───────────────────────────────────────────────────────
import React, { useEffect, useState } from 'react';
import { Save, Info, RefreshCw, AlertCircle } from 'lucide-react';
import { PageHeader, SectionHeader, Skeleton } from '@/components/ui';
import { useI18n } from '@/lib/i18n';
import { useBrainConfig, useSetBrainConfig, useStrategy, useUpdateStrategy } from '@/lib/hooks';
import { fmtCurrency, fmtPct, fmtScore } from '@/lib/utils';

const GROWTH_MODES = ['conservative', 'balanced', 'aggressive', 'recovery'];

export function SettingsAdvanced() {
  const { t } = useI18n();
  const { data: configData, isLoading: configLoading, error: configError, refetch: refetchConfig } = useBrainConfig();
  const { data: strategyData, isLoading: stratLoading, error: stratError, refetch: refetchStrat } = useStrategy();
  const setBrainConfig = useSetBrainConfig();
  const updateStrategy  = useUpdateStrategy();

  const [brain, setBrain] = useState<Record<string, any>>({});
  const [strategy, setStrategy] = useState<Record<string, any>>({});
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState('');

  useEffect(() => { if (configData)   setBrain(configData as Record<string, any>); }, [configData]);
  useEffect(() => { if (strategyData) setStrategy(strategyData as Record<string, any>); }, [strategyData]);

  const isLoading = configLoading || stratLoading;
  const hasError  = configError || stratError;

  async function save() {
    setSaveError('');
    try {
      await Promise.all([
        setBrainConfig.mutateAsync(brain),
        updateStrategy.mutateAsync({
          target_daily_views:   strategy.target_daily_views,
          target_daily_revenue: strategy.target_daily_revenue,
          growth_mode:          strategy.growth_mode,
        }),
      ]);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (e: any) {
      setSaveError(e.message ?? 'Failed to save');
    }
  }

  if (isLoading) return (
    <div style={{ maxWidth: 640 }}>
      <PageHeader title={t('adv.title')} subtitle={t('adv.sub')} />
      <div className="card" style={{ marginBottom: '1.25rem' }}>
        {[1, 2, 3].map(i => <div key={i} style={{ padding: '0.75rem 0' }}><Skeleton height={40} /></div>)}
      </div>
      <div className="card"><Skeleton height={120} /></div>
    </div>
  );

  if (hasError) return (
    <div style={{ maxWidth: 640 }}>
      <PageHeader title={t('adv.title')} subtitle={t('adv.sub')} />
      <div style={{ textAlign: 'center', padding: '3rem 1rem', color: 'var(--danger)' }}>
        <AlertCircle size={32} style={{ marginBottom: '0.75rem', opacity: 0.7 }} />
        <div style={{ fontSize: '0.875rem', marginBottom: '1rem' }}>
          {((configError ?? stratError) as Error)?.message ?? 'Failed to load settings'}
        </div>
        <button className="btn btn-secondary btn-sm" onClick={() => { refetchConfig(); refetchStrat(); }}>
          <RefreshCw size={13} /> {t('cmd.retry')}
        </button>
      </div>
    </div>
  );

  const isSaving = setBrainConfig.isPending || updateStrategy.isPending;

  return (
    <div style={{ maxWidth: 640 }}>
      <PageHeader
        title={t('adv.title')}
        subtitle={t('adv.sub')}
        action={
          <button className="btn btn-primary btn-sm" onClick={save} disabled={isSaving}>
            <Save size={13} /> {saved ? t('adv.saved') : isSaving ? t('adv.saving') : t('adv.btn_save')}
          </button>
        }
      />

      {saveError && (
        <div style={{ padding: '0.625rem 0.875rem', background: 'var(--danger-muted)', border: '1px solid var(--danger)', borderRadius: 'var(--radius-sm)', marginBottom: '1rem', fontSize: '0.8125rem', color: 'var(--danger)' }}>
          {saveError}
        </div>
      )}

      <div className="card" style={{ marginBottom: '1.25rem' }}>
        <SectionHeader title={t('adv.title_thresh')} />

        {[
          { key: 'MIN_SCORE', label: 'MIN_SCORE', desc: t('adv.desc_min_score'), min: 0, max: 1, step: 0.01, fmt: fmtScore },
          { key: 'EXPLORATION_RATE', label: 'EXPLORATION_RATE', desc: t('adv.desc_explore'), min: 0, max: 0.5, step: 0.01, fmt: fmtPct },
          { key: 'COST_LIMIT', label: 'COST_LIMIT', desc: t('adv.desc_cost'), min: 0.1, max: 10, step: 0.1, fmt: fmtCurrency },
          { key: 'MAX_POSTS_PER_DAY', label: 'MAX_POSTS_PER_DAY', desc: t('adv.desc_cap'), min: 1, max: 50, step: 1, fmt: (v: number) => String(v) },
        ].map(({ key, label, desc, min, max, step, fmt }, idx, arr) => (
          <div key={key} style={{ padding: '0.75rem 0', borderBottom: idx < arr.length - 1 ? '1px solid var(--border-subtle)' : undefined }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
              <div>
                <div style={{ fontWeight: 500, fontSize: '0.875rem' }}>{label}</div>
                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{desc}</div>
              </div>
              <span style={{ fontWeight: 700, color: 'var(--primary)', fontVariantNumeric: 'tabular-nums' }}>
                {fmt(brain[key] ?? min)}
              </span>
            </div>
            <input type="range" min={min} max={max} step={step} value={brain[key] ?? min}
              onChange={e => setBrain(b => ({ ...b, [key]: +e.target.value }))}
              style={{ width: '100%', accentColor: 'var(--primary)' }} />
          </div>
        ))}
      </div>

      <div className="card" style={{ marginBottom: '1.25rem' }}>
        <SectionHeader title={t('adv.title_ceo')} />

        <div style={{ padding: '0.75rem 0', borderBottom: '1px solid var(--border-subtle)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
            <div>
              <div style={{ fontWeight: 500, fontSize: '0.875rem' }}>{t('adv.lbl_views')}</div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{t('adv.desc_views')}</div>
            </div>
            <span style={{ fontWeight: 700, color: 'var(--primary)' }}>{((strategy.target_daily_views ?? 50000) / 1000).toFixed(0)}K</span>
          </div>
          <input type="range" min={5000} max={500000} step={5000} value={strategy.target_daily_views ?? 50000}
            onChange={e => setStrategy(s => ({ ...s, target_daily_views: +e.target.value }))}
            style={{ width: '100%', accentColor: 'var(--primary)' }} />
        </div>

        <div style={{ padding: '0.75rem 0', borderBottom: '1px solid var(--border-subtle)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
            <div><div style={{ fontWeight: 500, fontSize: '0.875rem' }}>{t('adv.lbl_rev')}</div></div>
            <span style={{ fontWeight: 700, color: 'var(--success)' }}>{fmtCurrency(strategy.target_daily_revenue ?? 50)}</span>
          </div>
          <input type="range" min={5} max={500} step={5} value={strategy.target_daily_revenue ?? 50}
            onChange={e => setStrategy(s => ({ ...s, target_daily_revenue: +e.target.value }))}
            style={{ width: '100%', accentColor: 'var(--primary)' }} />
        </div>

        <div style={{ padding: '0.75rem 0' }}>
          <div style={{ fontWeight: 500, fontSize: '0.875rem', marginBottom: '0.625rem' }}>{t('adv.lbl_mode')}</div>
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
            {GROWTH_MODES.map(m => (
              <button key={m} onClick={() => setStrategy(s => ({ ...s, growth_mode: m }))}
                className={`btn btn-sm ${strategy.growth_mode === m ? 'btn-primary' : 'btn-secondary'}`}>
                {m}
              </button>
            ))}
          </div>
          <div style={{ marginTop: '0.5rem', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
            {strategy.growth_mode === 'conservative' && t('adv.mode_cons')}
            {strategy.growth_mode === 'balanced'     && t('adv.mode_bal')}
            {strategy.growth_mode === 'aggressive'   && t('adv.mode_agg')}
            {strategy.growth_mode === 'recovery'     && t('adv.mode_rec')}
          </div>
        </div>
      </div>

      <div style={{ padding: '0.75rem 1rem', background: 'var(--info-muted)', borderRadius: 'var(--radius-sm)', display: 'flex', gap: '0.5rem', alignItems: 'flex-start' }}>
        <Info size={14} color="var(--info)" style={{ flexShrink: 0, marginTop: '0.125rem' }} />
        <span style={{ fontSize: '0.8125rem', color: 'var(--info)' }}>
          {t('adv.info_persist')}
        </span>
      </div>
    </div>
  );
}
