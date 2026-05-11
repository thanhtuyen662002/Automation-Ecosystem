// ── Overrides — Control Interface (Rank 7) ────────────────────────────────────
// Data source: GET /api/v1/strategy/overrides → array (unwrapped from .overrides)
// NO mock data. All actions call real API.
import React, { useState } from 'react';
import { Trash2, AlertTriangle, Zap } from 'lucide-react';
import { PageHeader, SectionHeader, Badge, SlideOver, EmptyState, ConfirmDialog } from '@/components/ui';
import { useI18n } from '@/lib/i18n';
import { useOverrides, useAddOverride, useRemoveOverride } from '@/lib/hooks';
import { fmtRelative } from '@/lib/utils';

const OVERRIDE_TYPES = ['freeze', 'boost', 'kill', 'force_publish', 'restrict'];
const TARGET_TYPES   = ['account', 'niche', 'content'];
const overrideColor: Record<string, string> = {
  freeze: 'var(--warning)', boost: 'var(--success)', kill: 'var(--danger)',
  force_publish: 'var(--primary)', restrict: 'var(--info)',
};

// Rank 7: Preset quick interventions — most common emergency actions
interface Preset { labelKey: string; override: string; target_type: string; descKey: string; severity: 'danger' | 'warning' | 'info'; ttl_hours: number; needsTarget: boolean }
const PRESETS: Preset[] = [
  { labelKey: 'override.ps_freeze',    override: 'freeze',        target_type: 'account', descKey: 'override.ps_freeze_desc', severity: 'danger',  ttl_hours: 24,  needsTarget: true },
  { labelKey: 'override.ps_pause',     override: 'freeze',        target_type: 'niche',   descKey: 'override.ps_pause_desc',  severity: 'danger',  ttl_hours: 2,   needsTarget: false },
  { labelKey: 'override.ps_boost',     override: 'boost',         target_type: 'niche',   descKey: 'override.ps_boost_desc',  severity: 'info',    ttl_hours: 48,  needsTarget: true },
  { labelKey: 'override.ps_kill',      override: 'kill',          target_type: 'content', descKey: 'override.ps_kill_desc',   severity: 'warning', ttl_hours: 72,  needsTarget: true },
  { labelKey: 'override.ps_force',     override: 'force_publish', target_type: 'content', descKey: 'override.ps_force_desc',  severity: 'info',    ttl_hours: 1,   needsTarget: true },
];

export function Overrides() {
  const { t } = useI18n();
  const { data: overrides = [], isLoading, error } = useOverrides();
  const addOverride    = useAddOverride();
  const removeOverride = useRemoveOverride();

  const [showAdd, setShowAdd]             = useState(false);
  const [showCustom, setShowCustom]       = useState(false);
  const [selectedPreset, setSelectedPreset] = useState<Preset | null>(null);
  const [presetTarget, setPresetTarget]   = useState('');
  const [confirmRemove, setConfirmRemove] = useState<{ target_id: string; override: string } | null>(null);
  const [form, setForm] = useState({ target_id: '', target_type: 'account', override: 'freeze', reason: '', ttl_hours: 24 });

  const now = Date.now() / 1000;
  const active = (overrides as any[]).filter((o: any) => o.active && (o.expires_at ?? 0) > now);

  function handleRemove(target_id: string) {
    removeOverride.mutate(target_id, {
      onSuccess: () => setConfirmRemove(null),
    });
  }

  function handleAddPreset() {
    if (!selectedPreset) return;
    const targetId = selectedPreset.needsTarget ? presetTarget : 'ALL';
    if (selectedPreset.needsTarget && !targetId) return;
    addOverride.mutate(
      {
        target_id: targetId,
        target_type: selectedPreset.target_type,
        override: selectedPreset.override,
        reason: t(selectedPreset.descKey),
        ttl_hours: selectedPreset.ttl_hours,
      },
      {
        onSuccess: () => {
          setSelectedPreset(null);
          setPresetTarget('');
          setShowAdd(false);
        },
      },
    );
  }

  function handleAddCustom() {
    if (!form.target_id) return;
    addOverride.mutate(
      { ...form },
      {
        onSuccess: () => {
          setForm({ target_id: '', target_type: 'account', override: 'freeze', reason: '', ttl_hours: 24 });
          setShowCustom(false);
        },
      },
    );
  }

  return (
    <div>
      <PageHeader
        title={t('override.title')}
        subtitle={t('override.sub')}
        action={
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button className="btn btn-primary btn-sm" onClick={() => setShowAdd(true)}>
              <Zap size={13} /> {t('override.act_quick')}
            </button>
            <button className="btn btn-secondary btn-sm" onClick={() => setShowCustom(true)}>
              {t('override.act_custom')}
            </button>
          </div>
        }
      />

      {isLoading && (
        <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-muted)' }}>Loading overrides…</div>
      )}
      {error && (
        <div style={{ padding: '1rem', color: 'var(--danger)', fontSize: '0.875rem' }}>
          {(error as Error).message}
        </div>
      )}

      {/* Active overrides */}
      {!isLoading && active.length === 0
        ? <EmptyState icon="✅" message={t('override.no_data')} />
        : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            {active.map((o: any, i: number) => {
              const ttlLeft = Math.max(0, ((o.expires_at ?? 0) - now) / 3600);
              const pct = Math.min(100, (ttlLeft / (o.ttl_hours ?? 24)) * 100);
              return (
                <div key={o.id ?? i} className="card" style={{ borderLeft: `4px solid ${overrideColor[o.override] ?? 'var(--border)'}` }}>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: '1rem' }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginBottom: '0.375rem', flexWrap: 'wrap' }}>
                        <span style={{ background: overrideColor[o.override], color: '#fff', borderRadius: '9999px', padding: '0.15rem 0.6rem', fontSize: '0.75rem', fontWeight: 700 }}>
                          {String(o.override ?? '').toUpperCase().replace(/_/g, ' ')}
                        </span>
                        <Badge status="muted">{o.target_type}</Badge>
                        <code style={{ fontSize: '0.8125rem', color: 'var(--primary)', background: 'var(--primary-muted)', padding: '0.125rem 0.5rem', borderRadius: 4 }}>
                          {o.target_id}
                        </code>
                      </div>
                      {o.reason && <div style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>{o.reason}</div>}
                      <div style={{ display: 'flex', gap: '1rem', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                        <span>{t('override.lbl_created')} {fmtRelative(o.created_at)}</span>
                        <span style={{ color: ttlLeft < 2 ? 'var(--danger)' : 'var(--text-muted)' }}>
                          {ttlLeft.toFixed(1)}{t('override.lbl_remain')}
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

      {/* Quick override presets panel */}
      <SlideOver open={showAdd} onClose={() => { setShowAdd(false); setSelectedPreset(null); setPresetTarget(''); }} title={t('override.act_quick')}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.875rem' }}>
          <div style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>
            {t('override.desc_quick')}
          </div>
          {PRESETS.map(p => (
            <div
              key={p.labelKey}
              className="card-elevated"
              style={{
                padding: '0.875rem', cursor: 'pointer',
                border: `1px solid ${selectedPreset?.labelKey === p.labelKey ? overrideColor[p.override] : 'var(--border)'}`,
                borderRadius: 'var(--radius)',
                background: selectedPreset?.labelKey === p.labelKey ? 'var(--surface-2)' : undefined,
              }}
              onClick={() => { setSelectedPreset(p); setPresetTarget(''); }}
            >
              <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginBottom: '0.25rem' }}>
                <span style={{ background: overrideColor[p.override], color: '#fff', borderRadius: '9999px', padding: '0.1rem 0.5rem', fontSize: '0.7rem', fontWeight: 700 }}>
                  {p.override.toUpperCase()}
                </span>
                <span style={{ fontWeight: 600, fontSize: '0.875rem' }}>{t(p.labelKey)}</span>
                <span style={{ marginLeft: 'auto', fontSize: '0.7rem', color: 'var(--text-muted)' }}>{t('override.lbl_ttl')} {p.ttl_hours}h</span>
              </div>
              <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{t(p.descKey)}</div>
            </div>
          ))}

          {selectedPreset && (
            <div style={{ borderTop: '1px solid var(--border)', paddingTop: '1rem' }}>
              {selectedPreset.needsTarget && (
                <div style={{ marginBottom: '0.75rem' }}>
                  <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>
                    {selectedPreset.target_type === 'account' ? t('override.lbl_acc_id') : selectedPreset.target_type === 'niche' ? t('override.lbl_niche_id') : t('override.lbl_content_id')}
                  </label>
                  <input className="input" placeholder={t('override.ph_target_id').replace('{target}', selectedPreset.target_type)}
                    value={presetTarget} onChange={e => setPresetTarget(e.target.value)} />
                </div>
              )}
              <div style={{ padding: '0.5rem 0.75rem', background: 'var(--warning-muted)', borderRadius: 'var(--radius-sm)', display: 'flex', gap: '0.5rem', alignItems: 'center', marginBottom: '0.75rem' }}>
                <AlertTriangle size={14} color="var(--warning)" />
                <span style={{ fontSize: '0.8rem', color: 'var(--warning)' }}>{t('override.warn_effect')}</span>
              </div>
              {addOverride.isError && (
                <div style={{ color: 'var(--danger)', fontSize: '0.8rem', marginBottom: '0.5rem' }}>
                  {(addOverride.error as Error)?.message}
                </div>
              )}
              <button className="btn btn-primary" style={{ width: '100%' }}
                disabled={(selectedPreset.needsTarget && !presetTarget) || addOverride.isPending}
                onClick={handleAddPreset}>
                {addOverride.isPending ? t('override.deploying') : `${t('override.btn_deploy')} ${t(selectedPreset.labelKey)}`}
              </button>
            </div>
          )}
        </div>
      </SlideOver>

      {/* Custom override form */}
      <SlideOver open={showCustom} onClose={() => setShowCustom(false)} title={t('override.act_custom')}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>{t('override.lbl_type')}</label>
            <select className="select" value={form.target_type} onChange={e => setForm(f => ({ ...f, target_type: e.target.value }))}>
              {TARGET_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>{t('override.lbl_id')}</label>
            <input className="input" placeholder="acc-001 or finance" value={form.target_id} onChange={e => setForm(f => ({ ...f, target_id: e.target.value }))} />
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>{t('override.lbl_action')}</label>
            <div style={{ display: 'flex', gap: '0.375rem', flexWrap: 'wrap' }}>
              {OVERRIDE_TYPES.map(t => (
                <button key={t} onClick={() => setForm(f => ({ ...f, override: t }))}
                  className={`btn btn-sm ${form.override === t ? 'btn-primary' : 'btn-secondary'}`}>{t}</button>
              ))}
            </div>
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>{t('override.lbl_reason')}</label>
            <input className="input" placeholder={t('override.ph_reason')} value={form.reason} onChange={e => setForm(f => ({ ...f, reason: e.target.value }))} />
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>{t('override.lbl_ttl')}: {form.ttl_hours}h</label>
            <input type="range" min={1} max={168} step={1} value={form.ttl_hours}
              onChange={e => setForm(f => ({ ...f, ttl_hours: +e.target.value }))}
              style={{ width: '100%', accentColor: 'var(--primary)' }} />
          </div>
          {addOverride.isError && (
            <div style={{ color: 'var(--danger)', fontSize: '0.8rem' }}>
              {(addOverride.error as Error)?.message}
            </div>
          )}
          <button className="btn btn-primary" disabled={!form.target_id || addOverride.isPending} onClick={handleAddCustom}>
            {addOverride.isPending ? t('override.adding') : t('override.btn_add')}
          </button>
        </div>
      </SlideOver>

      <ConfirmDialog
        open={!!confirmRemove}
        onClose={() => setConfirmRemove(null)}
        onConfirm={() => confirmRemove && handleRemove(confirmRemove.target_id)}
        title={t('override.confirm_rem_title')}
        message={t('override.confirm_rem_msg').replace('{override}', String(confirmRemove?.override).toUpperCase()).replace('{id}', confirmRemove?.target_id ?? '')}
        danger
      />
    </div>
  );
}
