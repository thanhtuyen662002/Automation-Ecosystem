// ── Identities Page ───────────────────────────────────────────────────────────
import React, { useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { PageHeader, Badge, SlideOver, StatRow, EmptyState } from '@/components/ui';
import { GlassIcon } from '@/components/Icons';
import { useI18n } from '@/lib/i18n';

const mockIdentities = [
  { account_id: 'acc-001', fingerprint_hash: 'sha256:a1b2c3d4e5f6...', proxy_url: 'http://proxy-vn-01:8080', proxy_country: 'VN', locked: true,  validation_issues: [],                                            force_safe_mode: false },
  { account_id: 'acc-002', fingerprint_hash: 'sha256:f6e5d4c3b2a1...', proxy_url: 'http://proxy-vn-02:8080', proxy_country: 'VN', locked: false, validation_issues: ['timezone_mismatch'],                       force_safe_mode: false },
  { account_id: 'acc-003', fingerprint_hash: 'sha256:1a2b3c4d5e6f...', proxy_url: 'http://proxy-sg-01:8080', proxy_country: 'SG', locked: true,  validation_issues: [],                                            force_safe_mode: false },
  { account_id: 'acc-006', fingerprint_hash: 'sha256:6f5e4d3c2b1a...', proxy_url: 'http://proxy-us-01:8080', proxy_country: 'US', locked: false, validation_issues: ['canvas_fingerprint_mismatch', 'timezone_mismatch'], force_safe_mode: true },
];

type Identity = typeof mockIdentities[0];

export function Identities() {
  const { t } = useI18n();
  const [identities, setIdentities] = useState(mockIdentities);
  const [selected, setSelected] = useState<Identity | null>(null);

  function toggleLock(id: string) {
    setIdentities(prev => prev.map(i => i.account_id === id ? { ...i, locked: !i.locked } : i));
  }
  function regen(id: string) {
    setIdentities(prev => prev.map(i => i.account_id === id
      ? { ...i, fingerprint_hash: `sha256:${Math.random().toString(36).slice(2)}...`, validation_issues: [] }
      : i));
    alert(`${t('id.btn_regen')} - ${id}`);
  }
  function validate(id: string) { alert(`${t('id.btn_val')} - ${id}`); }

  return (
    <div>
      <PageHeader title={t('id.title')} subtitle={t('id.sub')} />

      <div className="card" style={{ padding: 0, overflow: 'hidden', marginBottom: '1.25rem' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>{t('id.col_acc')}</th>
              <th>{t('id.col_fp')}</th>
              <th>{t('id.col_proxy')}</th>
              <th>{t('id.col_country')}</th>
              <th>{t('id.col_locked')}</th>
              <th>{t('id.col_issues')}</th>
              <th>{t('id.col_safe')}</th>
              <th>{t('id.col_actions')}</th>
            </tr>
          </thead>
          <tbody>
            {identities.map(id => (
              <tr key={id.account_id} style={{ cursor: 'pointer' }} onClick={() => setSelected(id)}>
                <td>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <GlassIcon name="key" size={14} style={{ opacity: 0.5, flexShrink: 0 }} />
                    <span className="mono" style={{ fontSize: '0.75rem' }}>{id.account_id}</span>
                  </div>
                </td>
                <td>
                  <span className="mono" style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                    {id.fingerprint_hash.slice(0, 24)}…
                  </span>
                </td>
                <td style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{id.proxy_url?.split('://')[1] ?? '—'}</td>
                <td><Badge status="muted">{id.proxy_country}</Badge></td>
                <td>
                  {id.locked
                    ? <Badge status="success"><span style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}><GlassIcon name="lock" size={10} style={{ filter: 'brightness(0) saturate(100%) invert(52%) sepia(70%) saturate(600%) hue-rotate(120deg)' }} />{t('id.locked')}</span></Badge>
                    : <Badge status="muted">{t('id.unlocked')}</Badge>
                  }
                </td>
                <td>
                  {id.validation_issues.length > 0
                    ? <Badge status="danger"><span style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}><GlassIcon name="warning" size={10} style={{ filter: 'brightness(0) saturate(100%) invert(26%) sepia(90%) saturate(3000%) hue-rotate(345deg)' }} />{id.validation_issues.length} {t('id.issues')}</span></Badge>
                    : <Badge status="success">{t('id.clean')}</Badge>
                  }
                </td>
                <td>
                  {id.force_safe_mode
                    ? <Badge status="danger"><span style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}><GlassIcon name="shield" size={10} style={{ filter: 'brightness(0) saturate(100%) invert(26%) sepia(90%) saturate(3000%) hue-rotate(345deg)' }} />{t('id.forced')}</span></Badge>
                    : <span style={{ color: 'var(--text-muted)', fontSize: '0.75rem' }}>—</span>
                  }
                </td>
                <td onClick={e => e.stopPropagation()}>
                  <div style={{ display: 'flex', gap: '0.375rem' }}>
                    <button className="btn btn-ghost btn-icon btn-sm" title={id.locked ? t('id.act_unlock') : t('id.act_lock')} onClick={() => toggleLock(id.account_id)}>
                      <GlassIcon name={id.locked ? 'arrow-circle-up' : 'lock'} size={13} style={{ opacity: 0.7 }} />
                    </button>
                    <button className="btn btn-ghost btn-icon btn-sm" title={t('id.act_regen')} onClick={() => regen(id.account_id)} disabled={id.locked}>
                      <RefreshCw size={12} />
                    </button>
                    <button className="btn btn-ghost btn-icon btn-sm" title={t('id.act_val')} onClick={() => validate(id.account_id)}>
                      <GlassIcon name="check-circle" size={13} style={{ opacity: 0.7 }} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <SlideOver open={!!selected} onClose={() => setSelected(null)} title={t('id.detail_title')}>
        {selected && (
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.75rem' }}>
              <GlassIcon name="key" size={32} />
              <div style={{ fontWeight: 700 }}>{selected.account_id}</div>
            </div>
            <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
              {selected.locked && <Badge status="success"><span style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}><GlassIcon name="lock" size={10} />{t('id.locked')}</span></Badge>}
              {selected.force_safe_mode && <Badge status="danger">{t('id.force_safe')}</Badge>}
              {selected.validation_issues.length === 0 && <Badge status="success">{t('id.no_issues')}</Badge>}
            </div>

            <div className="card-elevated" style={{ marginBottom: '1rem' }}>
              <StatRow label={t('id.lbl_acc_id')} value={<span className="mono">{selected.account_id}</span>} mono />
              <StatRow label={t('id.lbl_fp')} value={<span className="mono" style={{ fontSize: '0.7rem' }}>{selected.fingerprint_hash}</span>} mono />
              <StatRow label={t('id.lbl_proxy_url')} value={<span className="mono">{selected.proxy_url ?? '—'}</span>} mono />
              <StatRow label={t('id.lbl_proxy_country')} value={selected.proxy_country} />
              <StatRow label={t('id.lbl_locked')} value={selected.locked ? t('id.val_yes_lock') : t('id.val_no_lock')} />
              <StatRow label={t('id.lbl_force_safe')} value={selected.force_safe_mode ? t('id.val_yes_warn') : '—'} />
            </div>

            {selected.validation_issues.length > 0 && (
              <div style={{ padding: '0.625rem 0.875rem', background: 'var(--danger-muted)', borderRadius: 'var(--radius-sm)', border: '1px solid var(--danger)', marginBottom: '1rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontWeight: 600, color: 'var(--danger)', fontSize: '0.75rem', marginBottom: '0.25rem' }}>
                  <GlassIcon name="warning" size={13} style={{ filter: 'brightness(0) saturate(100%) invert(26%) sepia(90%) saturate(3000%) hue-rotate(345deg)' }} />
                  {t('id.val_issues')}
                </div>
                {selected.validation_issues.map(issue => (
                  <div key={issue} style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>• {issue.replace(/_/g, ' ')}</div>
                ))}
              </div>
            )}

            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.625rem' }}>
              <button className="btn btn-secondary" onClick={() => { toggleLock(selected.account_id); setSelected(null); }}>
                <GlassIcon name={selected.locked ? 'arrow-circle-up' : 'lock'} size={14} />
                {selected.locked ? t('id.btn_unlock') : t('id.btn_lock')}
              </button>
              <button className="btn btn-danger" disabled={selected.locked} onClick={() => { regen(selected.account_id); setSelected(null); }}>
                <RefreshCw size={14} /> {t('id.btn_regen')}
              </button>
              <button className="btn btn-secondary" onClick={() => validate(selected.account_id)}>
                <GlassIcon name="check-circle" size={14} /> {t('id.btn_val')}
              </button>
            </div>
          </div>
        )}
      </SlideOver>
    </div>
  );
}
