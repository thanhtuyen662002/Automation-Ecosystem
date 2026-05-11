// ── Identities Page ───────────────────────────────────────────────────────────
import React, { useState } from 'react';
import { KeyRound, Lock, Unlock, RefreshCw, ShieldAlert } from 'lucide-react';
import { PageHeader, Badge, SectionHeader, SlideOver, StatRow, EmptyState } from '@/components/ui';

const mockIdentities = [
  { account_id: 'acc-001', fingerprint_hash: 'sha256:a1b2c3d4e5f6...', proxy_url: 'http://proxy-vn-01:8080', proxy_country: 'VN', locked: true, validation_issues: [], force_safe_mode: false },
  { account_id: 'acc-002', fingerprint_hash: 'sha256:f6e5d4c3b2a1...', proxy_url: 'http://proxy-vn-02:8080', proxy_country: 'VN', locked: false, validation_issues: ['timezone_mismatch'], force_safe_mode: false },
  { account_id: 'acc-003', fingerprint_hash: 'sha256:1a2b3c4d5e6f...', proxy_url: 'http://proxy-sg-01:8080', proxy_country: 'SG', locked: true, validation_issues: [], force_safe_mode: false },
  { account_id: 'acc-006', fingerprint_hash: 'sha256:6f5e4d3c2b1a...', proxy_url: 'http://proxy-us-01:8080', proxy_country: 'US', locked: false, validation_issues: ['canvas_fingerprint_mismatch', 'timezone_mismatch'], force_safe_mode: true },
];

type Identity = typeof mockIdentities[0];

export function Identities() {
  const [identities, setIdentities] = useState(mockIdentities);
  const [selected, setSelected] = useState<Identity | null>(null);

  function toggleLock(id: string) {
    setIdentities(prev => prev.map(i => i.account_id === id ? { ...i, locked: !i.locked } : i));
  }
  function regen(id: string) {
    setIdentities(prev => prev.map(i => i.account_id === id ? { ...i, fingerprint_hash: `sha256:${Math.random().toString(36).slice(2)}...`, validation_issues: [] } : i));
    alert(`Fingerprint regenerated for ${id}`);
  }
  function validate(id: string) {
    alert(`Validation triggered for ${id} (mock)`);
  }

  return (
    <div>
      <PageHeader
        title="Identities"
        subtitle="Device fingerprints, proxy assignments & validation"
      />

      <div className="card" style={{ padding: 0, overflow: 'hidden', marginBottom: '1.25rem' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>Account</th>
              <th>Fingerprint</th>
              <th>Proxy</th>
              <th>Country</th>
              <th>Locked</th>
              <th>Issues</th>
              <th>Safe Mode</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {identities.map(id => (
              <tr key={id.account_id} style={{ cursor: 'pointer' }} onClick={() => setSelected(id)}>
                <td><span className="mono" style={{ fontSize: '0.75rem' }}>{id.account_id}</span></td>
                <td>
                  <span className="mono" style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                    {id.fingerprint_hash.slice(0, 24)}…
                  </span>
                </td>
                <td style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{id.proxy_url?.split('://')[1] ?? '—'}</td>
                <td><Badge status="muted">{id.proxy_country}</Badge></td>
                <td>
                  {id.locked
                    ? <Badge status="success"><Lock size={10} /> Locked</Badge>
                    : <Badge status="muted"><Unlock size={10} /> Unlocked</Badge>
                  }
                </td>
                <td>
                  {id.validation_issues.length > 0
                    ? <Badge status="danger">{id.validation_issues.length} issues</Badge>
                    : <Badge status="success">Clean</Badge>
                  }
                </td>
                <td>
                  {id.force_safe_mode
                    ? <Badge status="danger"><ShieldAlert size={10} /> Forced</Badge>
                    : <span style={{ color: 'var(--text-muted)', fontSize: '0.75rem' }}>—</span>
                  }
                </td>
                <td onClick={e => e.stopPropagation()}>
                  <div style={{ display: 'flex', gap: '0.375rem' }}>
                    <button className="btn btn-ghost btn-icon btn-sm" title={id.locked ? 'Unlock' : 'Lock'}
                      onClick={() => toggleLock(id.account_id)}>
                      {id.locked ? <Unlock size={12} /> : <Lock size={12} />}
                    </button>
                    <button className="btn btn-ghost btn-icon btn-sm" title="Regenerate fingerprint"
                      onClick={() => regen(id.account_id)} disabled={id.locked}>
                      <RefreshCw size={12} />
                    </button>
                    <button className="btn btn-ghost btn-icon btn-sm btn-sm" title="Validate consistency"
                      onClick={() => validate(id.account_id)}>
                      <KeyRound size={12} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <SlideOver open={!!selected} onClose={() => setSelected(null)} title="Identity Detail">
        {selected && (
          <div>
            <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
              {selected.locked && <Badge status="success"><Lock size={10} /> Locked</Badge>}
              {selected.force_safe_mode && <Badge status="danger">Force Safe Mode</Badge>}
              {selected.validation_issues.length === 0 && <Badge status="success">No Issues</Badge>}
            </div>

            <div className="card-elevated" style={{ marginBottom: '1rem' }}>
              <StatRow label="Account ID" value={<span className="mono">{selected.account_id}</span>} mono />
              <StatRow label="Fingerprint" value={<span className="mono" style={{ fontSize: '0.7rem' }}>{selected.fingerprint_hash}</span>} mono />
              <StatRow label="Proxy URL" value={<span className="mono">{selected.proxy_url ?? '—'}</span>} mono />
              <StatRow label="Proxy Country" value={selected.proxy_country} />
              <StatRow label="Locked" value={selected.locked ? '🔒 Yes' : '🔓 No'} />
              <StatRow label="Force Safe Mode" value={selected.force_safe_mode ? '⚠ YES' : '—'} />
            </div>

            {selected.validation_issues.length > 0 && (
              <div style={{ padding: '0.625rem 0.875rem', background: 'var(--danger-muted)', borderRadius: 'var(--radius-sm)', border: '1px solid var(--danger)', marginBottom: '1rem' }}>
                <div style={{ fontWeight: 600, color: 'var(--danger)', fontSize: '0.75rem', marginBottom: '0.25rem' }}>Validation Issues</div>
                {selected.validation_issues.map(issue => (
                  <div key={issue} style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>• {issue.replace(/_/g, ' ')}</div>
                ))}
              </div>
            )}

            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.625rem' }}>
              <button className="btn btn-secondary" onClick={() => { toggleLock(selected.account_id); setSelected(null); }}>
                {selected.locked ? <><Unlock size={14} /> Unlock Fingerprint</> : <><Lock size={14} /> Lock Fingerprint</>}
              </button>
              <button className="btn btn-danger" disabled={selected.locked} onClick={() => { regen(selected.account_id); setSelected(null); }}>
                <RefreshCw size={14} /> Regenerate Fingerprint
              </button>
              <button className="btn btn-secondary" onClick={() => validate(selected.account_id)}>
                <KeyRound size={14} /> Validate Consistency
              </button>
            </div>
          </div>
        )}
      </SlideOver>
    </div>
  );
}
