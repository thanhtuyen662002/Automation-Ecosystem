// ── Accounts Page ─────────────────────────────────────────────────────────────
import React, { useState } from 'react';
import { Plus, Link, ShieldOff, ShieldCheck, Trash2 } from 'lucide-react';
import {
  PageHeader, Badge, SectionHeader, SlideOver, StatRow,
  ConfirmDialog, EmptyState,
} from '@/components/ui';
import { mockAccounts } from '@/lib/mock';
import { fmtRelative } from '@/lib/utils';

interface Account {
  id: string; platform: string; account_handle: string;
  status: string; proxy_url: string | null; session_valid: boolean;
  last_login_at: string | null; risk_score: number; soft_ban_detected: boolean;
  warmup_sessions_completed: number; failed_publish_count: number;
  captcha_hit_count: number; created_at: string; updated_at: string;
}

export function Accounts() {
  const [accounts, setAccounts] = useState<Account[]>(mockAccounts as Account[]);
  const [selected, setSelected] = useState<Account | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<Account | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [newHandle, setNewHandle] = useState('');
  const [newPlatform, setNewPlatform] = useState('tiktok');
  const [newProxy, setNewProxy] = useState('');

  const platforms = [...new Set(accounts.map(a => a.platform))];

  function markSoftBan(id: string) {
    setAccounts(prev => prev.map(a => a.id === id ? { ...a, soft_ban_detected: true, status: 'limited' } : a));
  }
  function clearSoftBan(id: string) {
    setAccounts(prev => prev.map(a => a.id === id ? { ...a, soft_ban_detected: false, status: 'healthy' } : a));
  }
  function deleteAccount(id: string) {
    setAccounts(prev => prev.filter(a => a.id !== id));
    setSelected(null);
  }
  function addAccount() {
    if (!newHandle) return;
    const id = `acc-${Date.now()}`;
    setAccounts(prev => [...prev, {
      id, platform: newPlatform, account_handle: newHandle,
      status: 'healthy', proxy_url: newProxy || null, session_valid: false,
      last_login_at: null, risk_score: 0, soft_ban_detected: false,
      warmup_sessions_completed: 0, failed_publish_count: 0, captcha_hit_count: 0,
      created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    } as Account]);
    setNewHandle(''); setNewProxy('');
    setShowAdd(false);
  }

  return (
    <div>
      <PageHeader
        title="Accounts"
        subtitle="Social accounts, sessions & health status"
        action={
          <button className="btn btn-primary btn-sm" onClick={() => setShowAdd(true)}>
            <Plus size={13} /> Add Account
          </button>
        }
      />

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <div style={{ overflowX: 'auto' }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Handle</th>
                <th>Platform</th>
                <th>Status</th>
                <th>Session</th>
                <th>Risk</th>
                <th>Soft Ban</th>
                <th>Warmup</th>
                <th>Failed Pub.</th>
                <th>Last Login</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map(a => (
                <tr key={a.id} style={{ cursor: 'pointer' }} onClick={() => setSelected(a)}>
                  <td>
                    <div style={{ fontWeight: 600, fontSize: '0.8125rem' }}>{a.account_handle}</div>
                    <div className="mono" style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{a.id}</div>
                  </td>
                  <td><Badge status="info">{a.platform}</Badge></td>
                  <td><Badge status={a.status}>{a.status}</Badge></td>
                  <td>
                    {a.session_valid
                      ? <span className="badge badge-success">✓ Valid</span>
                      : <span className="badge badge-muted">No session</span>}
                  </td>
                  <td>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.375rem' }}>
                      <div style={{ width: 50, background: 'var(--border)', borderRadius: 3, height: 5 }}>
                        <div style={{ width: `${a.risk_score * 100}%`, height: '100%', borderRadius: 3, background: a.risk_score >= 0.7 ? 'var(--danger)' : a.risk_score >= 0.4 ? 'var(--warning)' : 'var(--success)' }} />
                      </div>
                      <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{a.risk_score.toFixed(2)}</span>
                    </div>
                  </td>
                  <td>
                    {a.soft_ban_detected
                      ? <span className="badge badge-danger">⚠ Shadow-ban</span>
                      : <span className="badge badge-muted">—</span>}
                  </td>
                  <td style={{ textAlign: 'center' }}>{a.warmup_sessions_completed}</td>
                  <td style={{ textAlign: 'center', color: a.failed_publish_count > 0 ? 'var(--danger)' : 'var(--text-muted)' }}>
                    {a.failed_publish_count}
                  </td>
                  <td style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                    {a.last_login_at ? fmtRelative(new Date(a.last_login_at).getTime() / 1000) : '—'}
                  </td>
                  <td onClick={e => e.stopPropagation()}>
                    <div style={{ display: 'flex', gap: '0.375rem' }}>
                      <button className="btn btn-ghost btn-icon btn-sm" title="Connect session" onClick={() => alert(`Launching browser for ${a.id}`)}>
                        <Link size={12} />
                      </button>
                      {a.soft_ban_detected
                        ? <button className="btn btn-ghost btn-icon btn-sm" title="Clear soft ban" onClick={() => clearSoftBan(a.id)}>
                            <ShieldCheck size={12} />
                          </button>
                        : <button className="btn btn-ghost btn-icon btn-sm" title="Mark soft ban" onClick={() => markSoftBan(a.id)}>
                            <ShieldOff size={12} />
                          </button>
                      }
                      <button className="btn btn-ghost btn-icon btn-sm" title="Delete" onClick={() => setConfirmDelete(a)} style={{ color: 'var(--danger)' }}>
                        <Trash2 size={12} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Detail */}
      <SlideOver open={!!selected} onClose={() => setSelected(null)} title="Account Detail">
        {selected && (
          <div>
            <div style={{ fontWeight: 700, fontSize: '1rem', marginBottom: '0.75rem' }}>{selected.account_handle}</div>
            <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
              <Badge status={selected.platform}>{selected.platform}</Badge>
              <Badge status={selected.status}>{selected.status}</Badge>
              {selected.soft_ban_detected && <Badge status="danger">Shadow-ban detected</Badge>}
              {selected.session_valid && <Badge status="success">Session valid</Badge>}
            </div>
            <div className="card-elevated" style={{ marginBottom: '1rem' }}>
              <StatRow label="ID" value={<span className="mono">{selected.id}</span>} mono />
              <StatRow label="Proxy" value={selected.proxy_url ?? '—'} />
              <StatRow label="Risk Score" value={selected.risk_score.toFixed(2)} />
              <StatRow label="Warmup Sessions" value={selected.warmup_sessions_completed} />
              <StatRow label="Failed Publishes" value={<span style={{ color: selected.failed_publish_count > 0 ? 'var(--danger)' : 'inherit' }}>{selected.failed_publish_count}</span>} />
              <StatRow label="Captcha Hits" value={selected.captcha_hit_count} />
              <StatRow label="Last Login" value={selected.last_login_at ? fmtRelative(new Date(selected.last_login_at).getTime() / 1000) : '—'} />
              <StatRow label="Created" value={selected.created_at ? new Date(selected.created_at).toLocaleDateString() : '—'} />
            </div>
            <div style={{ display: 'flex', gap: '0.625rem', flexDirection: 'column' }}>
              <button className="btn btn-primary" onClick={() => alert(`Opening browser for ${selected.id}`)}>
                <Link size={14} /> Connect / Re-login
              </button>
              {selected.soft_ban_detected
                ? <button className="btn btn-secondary" onClick={() => { clearSoftBan(selected.id); setSelected(null); }}>
                    <ShieldCheck size={14} /> Clear Soft Ban
                  </button>
                : <button className="btn btn-secondary" onClick={() => { markSoftBan(selected.id); setSelected(null); }}>
                    <ShieldOff size={14} /> Mark Shadow-ban
                  </button>
              }
              <button className="btn btn-danger" onClick={() => setConfirmDelete(selected)}>
                <Trash2 size={14} /> Delete Account
              </button>
            </div>
          </div>
        )}
      </SlideOver>

      {/* Add Account */}
      <SlideOver open={showAdd} onClose={() => setShowAdd(false)} title="Add Account">
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>Platform</label>
            <select className="select" value={newPlatform} onChange={e => setNewPlatform(e.target.value)}>
              <option value="tiktok">TikTok</option>
              <option value="facebook">Facebook</option>
              <option value="youtube">YouTube</option>
            </select>
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>Account Handle</label>
            <input className="input" placeholder="@handle or page name" value={newHandle} onChange={e => setNewHandle(e.target.value)} />
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>Proxy URL (optional)</label>
            <input className="input" placeholder="http://proxy:port" value={newProxy} onChange={e => setNewProxy(e.target.value)} />
          </div>
          <button className="btn btn-primary" onClick={addAccount} disabled={!newHandle}>Add Account</button>
        </div>
      </SlideOver>

      <ConfirmDialog
        open={!!confirmDelete} onClose={() => setConfirmDelete(null)}
        onConfirm={() => confirmDelete && deleteAccount(confirmDelete.id)}
        title="Delete Account"
        message={`Permanently delete ${confirmDelete?.account_handle}? This cannot be undone.`}
        danger
      />
    </div>
  );
}
