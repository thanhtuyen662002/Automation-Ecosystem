// ── Settings — Policy Rules ───────────────────────────────────────────────────
import React, { useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { PageHeader, Badge, SectionHeader, ToggleRow, SlideOver, EmptyState, ConfirmDialog } from '@/components/ui';
import { mockPolicyRules } from '@/lib/mock';

type Rule = typeof mockPolicyRules[0];

export function SettingsPolicy() {
  const [rules, setRules] = useState(mockPolicyRules);
  const [showAdd, setShowAdd] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<Rule | null>(null);
  const [form, setForm] = useState({
    platform: 'tiktok', action_type: 'publish_tiktok', rule_name: '',
    max_actions: 2, window_seconds: 86400, cooldown_seconds: 0, enabled: true,
  });

  function toggle(id: string) {
    setRules(prev => prev.map(r => r.id === id ? { ...r, enabled: !r.enabled } : r));
  }
  function deleteRule(id: string) {
    setRules(prev => prev.filter(r => r.id !== id));
  }
  function addRule() {
    setRules(prev => [...prev, { ...form, id: `rule-${Date.now()}`, account_id: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString() }]);
    setForm({ platform: 'tiktok', action_type: 'publish_tiktok', rule_name: '', max_actions: 2, window_seconds: 86400, cooldown_seconds: 0, enabled: true });
    setShowAdd(false);
  }

  function fmtWindow(s: number) {
    if (s >= 86400) return `${s / 86400}d`;
    if (s >= 3600) return `${s / 3600}h`;
    return `${s}s`;
  }

  return (
    <div style={{ maxWidth: 800 }}>
      <PageHeader
        title="Policy Rules"
        subtitle="Rate-limit rules per platform and action type"
        action={<button className="btn btn-primary btn-sm" onClick={() => setShowAdd(true)}><Plus size={13} /> Add Rule</button>}
      />

      {rules.length === 0
        ? <EmptyState icon="📋" message="No policy rules configured" />
        : (
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Rule Name</th>
                  <th>Platform</th>
                  <th>Action Type</th>
                  <th>Max Actions</th>
                  <th>Window</th>
                  <th>Cooldown</th>
                  <th>Enabled</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {rules.map(r => (
                  <tr key={r.id}>
                    <td>
                      <div style={{ fontWeight: 500 }}>{r.rule_name}</div>
                      <div className="mono" style={{ fontSize: '0.65rem', color: 'var(--text-muted)' }}>{r.id}</div>
                    </td>
                    <td><Badge status="info">{r.platform}</Badge></td>
                    <td><span className="mono" style={{ fontSize: '0.75rem' }}>{r.action_type}</span></td>
                    <td style={{ textAlign: 'center', fontWeight: 600 }}>{r.max_actions}</td>
                    <td style={{ color: 'var(--text-secondary)' }}>{fmtWindow(r.window_seconds)}</td>
                    <td style={{ color: 'var(--text-secondary)' }}>{r.cooldown_seconds > 0 ? fmtWindow(r.cooldown_seconds) : '—'}</td>
                    <td>
                      <label className="toggle">
                        <input type="checkbox" checked={r.enabled} onChange={() => toggle(r.id)} />
                        <div className="toggle-track" />
                        <div className="toggle-thumb" />
                      </label>
                    </td>
                    <td>
                      <button className="btn btn-ghost btn-icon btn-sm" style={{ color: 'var(--danger)' }} onClick={() => setConfirmDelete(r)}>
                        <Trash2 size={13} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      }

      <SlideOver open={showAdd} onClose={() => setShowAdd(false)} title="Add Policy Rule">
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>Platform</label>
            <select className="select" value={form.platform} onChange={e => setForm(f => ({ ...f, platform: e.target.value }))}>
              <option value="tiktok">TikTok</option>
              <option value="facebook">Facebook</option>
            </select>
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>Action Type</label>
            <select className="select" value={form.action_type} onChange={e => setForm(f => ({ ...f, action_type: e.target.value }))}>
              <option value="publish_tiktok">publish_tiktok</option>
              <option value="publish_facebook">publish_facebook</option>
              <option value="login">login</option>
              <option value="browse">browse</option>
            </select>
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>Rule Name</label>
            <input className="input" placeholder="e.g. daily_upload_cap" value={form.rule_name} onChange={e => setForm(f => ({ ...f, rule_name: e.target.value }))} />
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>Max Actions: {form.max_actions}</label>
            <input type="range" min={1} max={50} step={1} value={form.max_actions}
              onChange={e => setForm(f => ({ ...f, max_actions: +e.target.value }))}
              style={{ width: '100%', accentColor: 'var(--primary)' }} />
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>Window: {fmtWindow(form.window_seconds)}</label>
            <select className="select" value={form.window_seconds} onChange={e => setForm(f => ({ ...f, window_seconds: +e.target.value }))}>
              <option value={600}>10 minutes</option>
              <option value={3600}>1 hour</option>
              <option value={86400}>1 day</option>
              <option value={604800}>7 days</option>
            </select>
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>Cooldown (after limit hit): {fmtWindow(form.cooldown_seconds)}</label>
            <select className="select" value={form.cooldown_seconds} onChange={e => setForm(f => ({ ...f, cooldown_seconds: +e.target.value }))}>
              <option value={0}>None</option>
              <option value={1800}>30 min</option>
              <option value={3600}>1 hour</option>
              <option value={21600}>6 hours</option>
            </select>
          </div>
          <button className="btn btn-primary" disabled={!form.rule_name} onClick={addRule}>Add Rule</button>
        </div>
      </SlideOver>

      <ConfirmDialog
        open={!!confirmDelete} onClose={() => setConfirmDelete(null)}
        onConfirm={() => confirmDelete && deleteRule(confirmDelete.id)}
        title="Delete Rule" message={`Delete rule "${confirmDelete?.rule_name}"?`} danger
      />
    </div>
  );
}
