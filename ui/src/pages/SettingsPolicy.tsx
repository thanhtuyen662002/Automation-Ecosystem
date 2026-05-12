// ── Settings — Policy Rules ───────────────────────────────────────────────────
import React, { useState } from 'react';
import { PageHeader, Badge, SlideOver, EmptyState, ConfirmDialog } from '@/components/ui';
import { GlassIcon } from '@/components/Icons';
import { useI18n } from '@/lib/i18n';
import { usePolicyRules, useCreatePolicyRule, useTogglePolicyRule, useDeletePolicyRule } from '@/lib/hooks';
import { PlatformBadge, PlatformSelect } from '@/lib/platforms';

interface Rule {
  id: string; account_id: string | null; platform: string | null;
  action_type: string; rule_name: string; enabled: boolean;
  max_actions: number | null; window_seconds: number | null;
  cooldown_seconds: number; created_at: string | null; updated_at: string | null;
}

function fmtWindow(s: number | null) {
  if (!s) return '—';
  if (s >= 86400) return `${s / 86400}d`;
  if (s >= 3600)  return `${s / 3600}h`;
  return `${s}s`;
}

export function SettingsPolicy() {
  const { t } = useI18n();
  const { data: rules = [], isLoading, error } = usePolicyRules();
  const createRule = useCreatePolicyRule();
  const toggleRule = useTogglePolicyRule();
  const deleteRule = useDeletePolicyRule();

  const [showAdd, setShowAdd]             = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<Rule | null>(null);
  const [form, setForm] = useState({
    platform: 'tiktok', action_type: 'publish_tiktok', rule_name: '',
    max_actions: 2, window_seconds: 86400, cooldown_seconds: 0,
  });

  function handleToggle(id: string, currentEnabled: boolean) {
    toggleRule.mutate({ id, enabled: !currentEnabled });
  }
  function handleDelete(id: string) {
    deleteRule.mutate(id, { onSuccess: () => setConfirmDelete(null) });
  }
  function handleAddRule() {
    if (!form.rule_name) return;
    createRule.mutate({ ...form }, {
      onSuccess: () => {
        setForm({ platform: 'tiktok', action_type: 'publish_tiktok', rule_name: '', max_actions: 2, window_seconds: 86400, cooldown_seconds: 0 });
        setShowAdd(false);
      },
    });
  }

  return (
    <div>
      <PageHeader title={t('policy.title')} subtitle={t('policy.sub')}
        action={
          <button className="btn btn-primary btn-sm" onClick={() => setShowAdd(true)} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
            <GlassIcon name="add-circle" size={15} style={{ filter: 'brightness(0) invert(1)' }} />
            {t('policy.act_add')}
          </button>
        }
      />

      {isLoading && (
        <div style={{ textAlign: 'center', padding: '2rem' }}>
          <GlassIcon name="shield" size={36} style={{ opacity: 0.3, marginBottom: '0.5rem' }} />
          <div style={{ color: 'var(--text-muted)' }}>{t('ceo.loading')}</div>
        </div>
      )}
      {error && (
        <div style={{ padding: '1rem', color: 'var(--danger)', fontSize: '0.875rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          <GlassIcon name="warning" size={16} style={{ filter: 'brightness(0) saturate(100%) invert(26%) sepia(90%) saturate(3000%) hue-rotate(345deg)' }} />
          {(error as Error).message}
        </div>
      )}

      {!isLoading && !error && (
        (rules as Rule[]).length === 0
          ? <EmptyState icon="shield" message={t('policy.no_data')} />
          : (
            <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('policy.col_name')}</th>
                    <th>{t('policy.col_platform')}</th>
                    <th>{t('policy.col_action')}</th>
                    <th>{t('policy.col_max')}</th>
                    <th>{t('policy.col_window')}</th>
                    <th>{t('policy.col_cooldown')}</th>
                    <th>{t('policy.col_enabled')}</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {(rules as Rule[]).map(r => (
                    <tr key={r.id}>
                      <td>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                          <GlassIcon name="shield" size={14} style={{ opacity: 0.5, flexShrink: 0 }} />
                          <div>
                            <div style={{ fontWeight: 500 }}>{r.rule_name}</div>
                            <div className="mono" style={{ fontSize: '0.65rem', color: 'var(--text-muted)' }}>{r.id}</div>
                          </div>
                        </div>
                      </td>
                      <td>{r.platform ? <PlatformBadge platform={r.platform} /> : <span style={{ color: 'var(--text-muted)' }}>—</span>}</td>
                      <td><span className="mono" style={{ fontSize: '0.75rem' }}>{r.action_type}</span></td>
                      <td style={{ textAlign: 'center', fontWeight: 600 }}>{r.max_actions ?? '—'}</td>
                      <td style={{ color: 'var(--text-secondary)' }}>{fmtWindow(r.window_seconds)}</td>
                      <td style={{ color: 'var(--text-secondary)' }}>{r.cooldown_seconds > 0 ? fmtWindow(r.cooldown_seconds) : '—'}</td>
                      <td>
                        <label className="toggle">
                          <input type="checkbox" checked={r.enabled} onChange={() => handleToggle(r.id, r.enabled)} disabled={toggleRule.isPending} />
                          <div className="toggle-track" />
                          <div className="toggle-thumb" />
                        </label>
                      </td>
                      <td>
                        <button className="btn btn-ghost btn-icon btn-sm" onClick={() => setConfirmDelete(r)} disabled={deleteRule.isPending}>
                          <GlassIcon name="trash" size={14} style={{ filter: 'brightness(0) saturate(100%) invert(26%) sepia(90%) saturate(3000%) hue-rotate(345deg)', opacity: 0.8 }} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
      )}

      <SlideOver open={showAdd} onClose={() => setShowAdd(false)} title={t('policy.act_add')}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>{t('policy.col_platform')}</label>
            <PlatformSelect value={form.platform} onChange={v => setForm(f => ({ ...f, platform: v }))} />
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>{t('policy.col_action')}</label>
            <select className="select" value={form.action_type} onChange={e => setForm(f => ({ ...f, action_type: e.target.value }))}>
              <option value="publish_tiktok">publish_tiktok</option>
              <option value="publish_facebook">publish_facebook</option>
              <option value="publish_youtube">publish_youtube</option>
            </select>
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>{t('policy.col_name')}</label>
            <input className="input" placeholder={t('policy.ph_name')} value={form.rule_name} onChange={e => setForm(f => ({ ...f, rule_name: e.target.value }))} />
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>{t('policy.col_max')}: {form.max_actions}</label>
            <input type="range" min={1} max={50} step={1} value={form.max_actions} onChange={e => setForm(f => ({ ...f, max_actions: +e.target.value }))} style={{ width: '100%', accentColor: 'var(--primary)' }} />
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>{t('policy.col_window')}: {fmtWindow(form.window_seconds)}</label>
            <select className="select" value={form.window_seconds} onChange={e => setForm(f => ({ ...f, window_seconds: +e.target.value }))}>
              <option value={600}>{t('policy.win_10m')}</option>
              <option value={3600}>{t('policy.win_1h')}</option>
              <option value={86400}>{t('policy.win_1d')}</option>
              <option value={604800}>{t('policy.win_7d')}</option>
            </select>
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>{t('policy.col_cooldown')}: {fmtWindow(form.cooldown_seconds)}</label>
            <select className="select" value={form.cooldown_seconds} onChange={e => setForm(f => ({ ...f, cooldown_seconds: +e.target.value }))}>
              <option value={0}>{t('policy.win_none')}</option>
              <option value={1800}>{t('policy.win_30m')}</option>
              <option value={3600}>{t('policy.win_1h')}</option>
              <option value={21600}>{t('policy.win_6h')}</option>
            </select>
          </div>
          {createRule.isError && (
            <div style={{ color: 'var(--danger)', fontSize: '0.8rem', display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
              <GlassIcon name="warning" size={13} style={{ filter: 'brightness(0) saturate(100%) invert(26%) sepia(90%) saturate(3000%) hue-rotate(345deg)' }} />
              {(createRule.error as Error)?.message}
            </div>
          )}
          <button className="btn btn-primary" disabled={!form.rule_name || createRule.isPending} onClick={handleAddRule}>
            {createRule.isPending ? t('policy.adding') : t('policy.act_add')}
          </button>
        </div>
      </SlideOver>

      <ConfirmDialog
        open={!!confirmDelete} onClose={() => setConfirmDelete(null)}
        onConfirm={() => confirmDelete && handleDelete(confirmDelete.id)}
        title={t('policy.confirm_del_title')}
        message={t('policy.confirm_del_msg').replace('{name}', confirmDelete?.rule_name ?? '')}
        danger
      />
    </div>
  );
}
