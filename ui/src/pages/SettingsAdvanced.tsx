// ── Settings — Advanced ───────────────────────────────────────────────────────
import React, { useState } from 'react';
import { Save, Info } from 'lucide-react';
import { PageHeader, SectionHeader, StatRow, Divider } from '@/components/ui';
import { mockBrainConfig, mockStrategyState } from '@/lib/mock';
import { fmtCurrency, fmtPct, fmtScore } from '@/lib/utils';

export function SettingsAdvanced() {
  const [brain, setBrain] = useState(mockBrainConfig);
  const [strategy, setStrategy] = useState(mockStrategyState);
  const [saved, setSaved] = useState(false);

  function save() {
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  const GROWTH_MODES = ['conservative', 'balanced', 'aggressive', 'recovery'];

  return (
    <div style={{ maxWidth: 640 }}>
      <PageHeader
        title="Advanced Settings"
        subtitle="Execution thresholds, scoring config, and strategy targets"
        action={
          <button className="btn btn-primary btn-sm" onClick={save}>
            <Save size={13} /> {saved ? '✓ Saved' : 'Save Changes'}
          </button>
        }
      />

      <div className="card" style={{ marginBottom: '1.25rem' }}>
        <SectionHeader title="Execution Brain Thresholds" />

        <div style={{ padding: '0.75rem 0', borderBottom: '1px solid var(--border-subtle)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
            <div>
              <div style={{ fontWeight: 500, fontSize: '0.875rem' }}>MIN_SCORE</div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Content below this score is dropped</div>
            </div>
            <span style={{ fontWeight: 700, color: 'var(--primary)', fontVariantNumeric: 'tabular-nums' }}>{fmtScore(brain.MIN_SCORE)}</span>
          </div>
          <input type="range" min={0} max={1} step={0.01} value={brain.MIN_SCORE}
            onChange={e => setBrain(b => ({ ...b, MIN_SCORE: +e.target.value }))}
            style={{ width: '100%', accentColor: 'var(--primary)' }} />
        </div>

        <div style={{ padding: '0.75rem 0', borderBottom: '1px solid var(--border-subtle)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
            <div>
              <div style={{ fontWeight: 500, fontSize: '0.875rem' }}>EXPLORATION_RATE</div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>% of budget allocated to experimental content</div>
            </div>
            <span style={{ fontWeight: 700, color: 'var(--primary)', fontVariantNumeric: 'tabular-nums' }}>{fmtPct(brain.EXPLORATION_RATE)}</span>
          </div>
          <input type="range" min={0} max={0.5} step={0.01} value={brain.EXPLORATION_RATE}
            onChange={e => setBrain(b => ({ ...b, EXPLORATION_RATE: +e.target.value }))}
            style={{ width: '100%', accentColor: 'var(--primary)' }} />
        </div>

        <div style={{ padding: '0.75rem 0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
            <div>
              <div style={{ fontWeight: 500, fontSize: '0.875rem' }}>COST_LIMIT</div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Max API cost per decision cycle</div>
            </div>
            <span style={{ fontWeight: 700, color: 'var(--primary)', fontVariantNumeric: 'tabular-nums' }}>{fmtCurrency(brain.COST_LIMIT)}</span>
          </div>
          <input type="range" min={0.1} max={10} step={0.1} value={brain.COST_LIMIT}
            onChange={e => setBrain(b => ({ ...b, COST_LIMIT: +e.target.value }))}
            style={{ width: '100%', accentColor: 'var(--primary)' }} />
        </div>
      </div>

      <div className="card" style={{ marginBottom: '1.25rem' }}>
        <SectionHeader title="CEO Strategy Targets" />

        <div style={{ padding: '0.75rem 0', borderBottom: '1px solid var(--border-subtle)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
            <div>
              <div style={{ fontWeight: 500, fontSize: '0.875rem' }}>Target Daily Views</div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>CEO closes loop to reach this target</div>
            </div>
            <span style={{ fontWeight: 700, color: 'var(--primary)' }}>{(strategy.target_daily_views / 1000).toFixed(0)}K</span>
          </div>
          <input type="range" min={5000} max={500000} step={5000} value={strategy.target_daily_views}
            onChange={e => setStrategy(s => ({ ...s, target_daily_views: +e.target.value }))}
            style={{ width: '100%', accentColor: 'var(--primary)' }} />
        </div>

        <div style={{ padding: '0.75rem 0', borderBottom: '1px solid var(--border-subtle)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
            <div>
              <div style={{ fontWeight: 500, fontSize: '0.875rem' }}>Target Daily Revenue</div>
            </div>
            <span style={{ fontWeight: 700, color: 'var(--success)' }}>{fmtCurrency(strategy.target_daily_revenue)}</span>
          </div>
          <input type="range" min={5} max={500} step={5} value={strategy.target_daily_revenue}
            onChange={e => setStrategy(s => ({ ...s, target_daily_revenue: +e.target.value }))}
            style={{ width: '100%', accentColor: 'var(--primary)' }} />
        </div>

        <div style={{ padding: '0.75rem 0' }}>
          <div style={{ fontWeight: 500, fontSize: '0.875rem', marginBottom: '0.625rem' }}>Growth Mode</div>
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
            {GROWTH_MODES.map(m => (
              <button key={m} onClick={() => setStrategy(s => ({ ...s, growth_mode: m }))}
                className={`btn btn-sm ${strategy.growth_mode === m ? 'btn-primary' : 'btn-secondary'}`}>
                {m}
              </button>
            ))}
          </div>
          <div style={{ marginTop: '0.5rem', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
            {strategy.growth_mode === 'conservative' && 'Low exploration (5%), high threshold (×1.2). Protects capital.'}
            {strategy.growth_mode === 'balanced' && 'Moderate exploration (10%), neutral threshold (×1.0). Default mode.'}
            {strategy.growth_mode === 'aggressive' && 'High exploration (20%), low threshold (×0.8). High risk / high reward.'}
            {strategy.growth_mode === 'recovery' && 'Emergency mode — exploration 25%, threshold 0.85. For under-performance.'}
          </div>
        </div>
      </div>

      <div style={{ padding: '0.75rem 1rem', background: 'var(--info-muted)', borderRadius: 'var(--radius-sm)', display: 'flex', gap: '0.5rem', alignItems: 'flex-start' }}>
        <Info size={14} color="var(--info)" style={{ flexShrink: 0, marginTop: '0.125rem' }} />
        <span style={{ fontSize: '0.8125rem', color: 'var(--info)' }}>
          Changes are applied in-memory. Restart the backend to persist to environment variables.
        </span>
      </div>
    </div>
  );
}
