// ── Overrides — Control Interface (Rank 7) ────────────────────────────────────
import React, { useState } from 'react';
import { Trash2, AlertTriangle, Zap } from 'lucide-react';
import { PageHeader, SectionHeader, Badge, SlideOver, EmptyState, ConfirmDialog } from '@/components/ui';
import { mockOverrides } from '@/lib/mock';
import { fmtRelative } from '@/lib/utils';

const OVERRIDE_TYPES = ['freeze', 'boost', 'kill', 'force_publish', 'restrict'];
const TARGET_TYPES   = ['account', 'niche', 'content'];
const overrideColor: Record<string, string> = {
  freeze: 'var(--warning)', boost: 'var(--success)', kill: 'var(--danger)',
  force_publish: 'var(--primary)', restrict: 'var(--info)',
};

// Rank 7: Preset quick interventions — most common emergency actions
interface Preset { label: string; override: string; target_type: string; description: string; severity: 'danger' | 'warning' | 'info'; ttl_hours: number; needsTarget: boolean }
const PRESETS: Preset[] = [
  { label: 'Freeze Account',    override: 'freeze',        target_type: 'account', description: 'Suspend all uploads from a specific account immediately.', severity: 'danger',  ttl_hours: 24,  needsTarget: true },
  { label: 'Pause All Uploads', override: 'freeze',        target_type: 'niche',   description: 'Fleet-wide publish halt across all niches for 2 hours.', severity: 'danger',  ttl_hours: 2,   needsTarget: false },
  { label: 'Boost Niche',       override: 'boost',         target_type: 'niche',   description: 'Increase budget priority for a niche — more content pushed.', severity: 'info', ttl_hours: 48, needsTarget: true },
  { label: 'Kill Content',      override: 'kill',          target_type: 'content', description: 'Immediately stop a specific content item from publishing.', severity: 'warning', ttl_hours: 72, needsTarget: true },
  { label: 'Force Publish',     override: 'force_publish', target_type: 'content', description: 'Bypass score gate and publish a specific content item now.', severity: 'info', ttl_hours: 1,  needsTarget: true },
];

export function Overrides() {
  const [overrides, setOverrides] = useState(mockOverrides);
  const [showAdd, setShowAdd]     = useState(false);
  const [showCustom, setShowCustom] = useState(false);
  const [selectedPreset, setSelectedPreset] = useState<Preset | null>(null);
  const [presetTarget, setPresetTarget]     = useState('');
  const [confirmRemove, setConfirmRemove]   = useState<{ target_id: string; override: string } | null>(null);
  const [form, setForm] = useState({ target_id: '', target_type: 'account', override: 'freeze', reason: '', ttl_hours: 24 });

  const active = overrides.filter(o => o.active && o.expires_at > Date.now() / 1000);

  function remove(target_id: string, override: string) {
    setOverrides(prev => prev.filter(o => !(o.target_id === target_id && o.override === override)));
    setConfirmRemove(null);
  }
  function addPreset() {
    if (!selectedPreset) return;
    const now = Date.now() / 1000;
    const targetId = selectedPreset.needsTarget ? presetTarget : 'ALL';
    if (selectedPreset.needsTarget && !targetId) return;
    setOverrides(prev => [...prev, {
      target_id: targetId, target_type: selectedPreset.target_type,
      override: selectedPreset.override, reason: selectedPreset.description,
      ttl_hours: selectedPreset.ttl_hours, created_at: now,
      expires_at: now + selectedPreset.ttl_hours * 3600, active: 1,
    }]);
    setSelectedPreset(null);
    setPresetTarget('');
    setShowAdd(false);
  }
  function addCustom() {
    const now = Date.now() / 1000;
    setOverrides(prev => [...prev, { ...form, created_at: now, expires_at: now + form.ttl_hours * 3600, active: 1 }]);
    setForm({ target_id: '', target_type: 'account', override: 'freeze', reason: '', ttl_hours: 24 });
    setShowCustom(false);
  }

  return (
    <div>
      <PageHeader
        title="Strategy Overrides"
        subtitle="Active fleet directives — freeze, boost, kill, force-publish"
        action={
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button className="btn btn-primary btn-sm" onClick={() => setShowAdd(true)}>
              <Zap size={13} /> Quick Override
            </button>
            <button className="btn btn-secondary btn-sm" onClick={() => setShowCustom(true)}>
              Custom
            </button>
          </div>
        }
      />

      {/* Active overrides */}
      {active.length === 0
        ? <EmptyState icon="✅" message="No active overrides" />
        : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            {active.map((o, i) => {
              const ttlLeft = Math.max(0, (o.expires_at - Date.now() / 1000) / 3600);
              const pct = Math.min(100, (ttlLeft / (o.ttl_hours ?? 24)) * 100);
              return (
                <div key={i} className="card" style={{ borderLeft: `4px solid ${overrideColor[o.override] ?? 'var(--border)'}` }}>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: '1rem' }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginBottom: '0.375rem', flexWrap: 'wrap' }}>
                        <span style={{ background: overrideColor[o.override], color: '#fff', borderRadius: '9999px', padding: '0.15rem 0.6rem', fontSize: '0.75rem', fontWeight: 700 }}>
                          {o.override.toUpperCase().replace(/_/g, ' ')}
                        </span>
                        <Badge status="muted">{o.target_type}</Badge>
                        <code style={{ fontSize: '0.8125rem', color: 'var(--primary)', background: 'var(--primary-muted)', padding: '0.125rem 0.5rem', borderRadius: 4 }}>
                          {o.target_id}
                        </code>
                      </div>
                      {o.reason && <div style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>{o.reason}</div>}
                      <div style={{ display: 'flex', gap: '1rem', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                        <span>Created {fmtRelative(o.created_at)}</span>
                        <span style={{ color: ttlLeft < 2 ? 'var(--danger)' : 'var(--text-muted)' }}>
                          {ttlLeft.toFixed(1)}h remaining
                        </span>
                      </div>
                    </div>
                    <button className="btn btn-ghost btn-icon btn-sm" style={{ color: 'var(--danger)', flexShrink: 0 }}
                      onClick={() => setConfirmRemove({ target_id: o.target_id, override: o.override })}>
                      <Trash2 size={14} />
                    </button>
                  </div>
                  <div style={{ marginTop: '0.625rem' }}>
                    <div className="score-bar" style={{ height: 4 }}>
                      <div className="score-bar-fill" style={{ width: `${pct}%`, background: ttlLeft < 2 ? 'var(--danger)' : ttlLeft < 6 ? 'var(--warning)' : 'var(--success)', transition: 'width 0.4s ease' }} />
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )
      }

      {/* Rank 7: Quick override presets panel */}
      <SlideOver open={showAdd} onClose={() => { setShowAdd(false); setSelectedPreset(null); setPresetTarget(''); }} title="Quick Override">
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.875rem' }}>
          <div style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>
            Select the intervention you need. The most common patterns are pre-configured.
          </div>
          {PRESETS.map(p => (
            <div
              key={p.label}
              className={`card-elevated ${selectedPreset?.label === p.label ? '' : ''}`}
              style={{
                padding: '0.875rem', cursor: 'pointer',
                border: `1px solid ${selectedPreset?.label === p.label ? overrideColor[p.override] : 'var(--border)'}`,
                borderRadius: 'var(--radius)',
                background: selectedPreset?.label === p.label ? 'var(--surface-2)' : undefined,
              }}
              onClick={() => { setSelectedPreset(p); setPresetTarget(''); }}
            >
              <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginBottom: '0.25rem' }}>
                <span style={{ background: overrideColor[p.override], color: '#fff', borderRadius: '9999px', padding: '0.1rem 0.5rem', fontSize: '0.7rem', fontWeight: 700 }}>
                  {p.override.toUpperCase()}
                </span>
                <span style={{ fontWeight: 600, fontSize: '0.875rem' }}>{p.label}</span>
                <span style={{ marginLeft: 'auto', fontSize: '0.7rem', color: 'var(--text-muted)' }}>TTL {p.ttl_hours}h</span>
              </div>
              <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{p.description}</div>
            </div>
          ))}

          {selectedPreset && (
            <div style={{ borderTop: '1px solid var(--border)', paddingTop: '1rem' }}>
              {selectedPreset.needsTarget && (
                <div style={{ marginBottom: '0.75rem' }}>
                  <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>
                    {selectedPreset.target_type === 'account' ? 'Account ID (e.g. acc-006)' : selectedPreset.target_type === 'niche' ? 'Niche (e.g. finance)' : 'Content ID (e.g. c-8820)'}
                  </label>
                  <input className="input" placeholder={`Enter ${selectedPreset.target_type} ID`}
                    value={presetTarget} onChange={e => setPresetTarget(e.target.value)} />
                </div>
              )}
              <div style={{ padding: '0.5rem 0.75rem', background: 'var(--warning-muted)', borderRadius: 'var(--radius-sm)', display: 'flex', gap: '0.5rem', alignItems: 'center', marginBottom: '0.75rem' }}>
                <AlertTriangle size={14} color="var(--warning)" />
                <span style={{ fontSize: '0.8rem', color: 'var(--warning)' }}>Override will take effect immediately on the execution brain.</span>
              </div>
              <button className="btn btn-primary" style={{ width: '100%' }}
                disabled={selectedPreset.needsTarget && !presetTarget}
                onClick={addPreset}>
                Deploy: {selectedPreset.label}
              </button>
            </div>
          )}
        </div>
      </SlideOver>

      {/* Custom override form (secondary) */}
      <SlideOver open={showCustom} onClose={() => setShowCustom(false)} title="Custom Override">
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>Target Type</label>
            <select className="select" value={form.target_type} onChange={e => setForm(f => ({ ...f, target_type: e.target.value }))}>
              {TARGET_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>Target ID</label>
            <input className="input" placeholder="acc-001 or finance" value={form.target_id} onChange={e => setForm(f => ({ ...f, target_id: e.target.value }))} />
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>Override Action</label>
            <div style={{ display: 'flex', gap: '0.375rem', flexWrap: 'wrap' }}>
              {OVERRIDE_TYPES.map(t => (
                <button key={t} onClick={() => setForm(f => ({ ...f, override: t }))}
                  className={`btn btn-sm ${form.override === t ? 'btn-primary' : 'btn-secondary'}`}>{t}</button>
              ))}
            </div>
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>Reason</label>
            <input className="input" placeholder="Why is this override needed?" value={form.reason} onChange={e => setForm(f => ({ ...f, reason: e.target.value }))} />
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>TTL: {form.ttl_hours}h</label>
            <input type="range" min={1} max={168} step={1} value={form.ttl_hours}
              onChange={e => setForm(f => ({ ...f, ttl_hours: +e.target.value }))}
              style={{ width: '100%', accentColor: 'var(--primary)' }} />
          </div>
          <button className="btn btn-primary" disabled={!form.target_id} onClick={addCustom}>Add Override</button>
        </div>
      </SlideOver>

      <ConfirmDialog
        open={!!confirmRemove}
        onClose={() => setConfirmRemove(null)}
        onConfirm={() => confirmRemove && remove(confirmRemove.target_id, confirmRemove.override)}
        title="Remove Override"
        message={`Remove the ${confirmRemove?.override.toUpperCase()} override on ${confirmRemove?.target_id}?`}
        danger
      />
    </div>
  );
}
