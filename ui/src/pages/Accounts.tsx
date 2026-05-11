// ── Accounts Page ─────────────────────────────────────────────────────────────
// Data source: GET /api/v1/accounts → { items: AccountResponse[] }
// NO mock data. All actions call real API.
import React, { useState } from 'react';
import { Plus, Link, ShieldOff, ShieldCheck, Trash2 } from 'lucide-react';
import {
  PageHeader, Badge, SectionHeader, SlideOver, StatRow,
  ConfirmDialog, EmptyState,
} from '@/components/ui';
import {
  useAccounts, useCreateAccount, useDeleteAccount,
  useMarkSoftBan, useClearSoftBan,
} from '@/lib/hooks';
import { fmtRelative } from '@/lib/utils';
import { useI18n } from '@/lib/i18n';

interface Account {
  id: string; platform: string; account_handle: string;
  status: string; proxy_url: string | null; session_valid: boolean;
  last_login_at: string | null;
  // optional fields — may not exist in all backend responses
  risk_score?: number; soft_ban_detected?: boolean;
  warmup_sessions_completed?: number; failed_publish_count?: number;
  captcha_hit_count?: number; created_at?: string; updated_at?: string;
}

function LoadingState() {
  const { t } = useI18n();
  return (
    <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-muted)' }}>
      {t('acc.loading')}
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--danger)' }}>
      {message}
    </div>
  );
}

export function Accounts() {
  const { t } = useI18n();
  const { data: accounts = [], isLoading, error } = useAccounts();
  const createAccount = useCreateAccount();
  const deleteAccount = useDeleteAccount();
  const markSoftBan   = useMarkSoftBan();
  const clearSoftBan  = useClearSoftBan();

  const [selected, setSelected]       = useState<Account | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<Account | null>(null);
  const [showAdd, setShowAdd]         = useState(false);
  const [newHandle, setNewHandle]     = useState('');
  const [newPlatform, setNewPlatform] = useState('tiktok');
  const [newProxy, setNewProxy]       = useState('');

  function handleAddAccount() {
    if (!newHandle) return;
    createAccount.mutate(
      { platform: newPlatform, account_handle: newHandle, proxy_url: newProxy || undefined },
      {
        onSuccess: () => {
          setNewHandle(''); setNewProxy('');
          setShowAdd(false);
        },
      },
    );
  }

  function handleDelete(id: string) {
    deleteAccount.mutate(id, {
      onSuccess: () => {
        setSelected(null);
        setConfirmDelete(null);
      },
    });
  }

  function handleMarkSoftBan(id: string) {
    markSoftBan.mutate(id, { onSuccess: () => setSelected(null) });
  }

  function handleClearSoftBan(id: string) {
    clearSoftBan.mutate(id, { onSuccess: () => setSelected(null) });
  }

  return (
    <div>
      <PageHeader
        title={t('acc.title')}
        subtitle={t('acc.sub')}
        action={
          <button className="btn btn-primary btn-sm" onClick={() => setShowAdd(true)}>
            <Plus size={13} /> {t('acc.add')}
          </button>
        }
      />

      {isLoading && <LoadingState />}
      {error && <ErrorState message={(error as Error).message} />}

      {!isLoading && !error && (
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          {accounts.length === 0
            ? <EmptyState icon="👤" message={t('acc.no_data')} />
            : (
              <div style={{ overflowX: 'auto' }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>{t('acc.col_handle')}</th>
                      <th>{t('acc.col_platform')}</th>
                      <th>{t('acc.col_status')}</th>
                      <th>{t('acc.col_session')}</th>
                      <th>{t('acc.col_risk')}</th>
                      <th>{t('acc.col_soft_ban')}</th>
                      <th>{t('acc.col_warmup')}</th>
                      <th>{t('acc.col_failed')}</th>
                      <th>{t('acc.col_last_login')}</th>
                      <th>{t('acc.col_actions')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(accounts as Account[]).map(a => {
                      const riskScore = a.risk_score ?? 0;
                      const softBan   = a.soft_ban_detected ?? false;
                      return (
                        <tr key={a.id} style={{ cursor: 'pointer' }} onClick={() => setSelected(a)}>
                          <td>
                            <div style={{ fontWeight: 600, fontSize: '0.8125rem' }}>{a.account_handle}</div>
                            <div className="mono" style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{a.id}</div>
                          </td>
                          <td><Badge status="info">{a.platform}</Badge></td>
                          <td><Badge status={a.status}>{a.status}</Badge></td>
                          <td>
                            {a.session_valid
                              ? <span className="badge badge-success">{t('acc.valid')}</span>
                              : <span className="badge badge-muted">{t('acc.no_session')}</span>}
                          </td>
                          <td>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.375rem' }}>
                              <div style={{ width: 50, background: 'var(--border)', borderRadius: 3, height: 5 }}>
                                <div style={{ width: `${riskScore * 100}%`, height: '100%', borderRadius: 3, background: riskScore >= 0.7 ? 'var(--danger)' : riskScore >= 0.4 ? 'var(--warning)' : 'var(--success)' }} />
                              </div>
                              <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{riskScore.toFixed(2)}</span>
                            </div>
                          </td>
                          <td>
                            {softBan
                              ? <span className="badge badge-danger">{t('acc.shadow_ban')}</span>
                              : <span className="badge badge-muted">{t('common.dash') ?? '—'}</span>}
                          </td>
                          <td style={{ textAlign: 'center' }}>{a.warmup_sessions_completed ?? 0}</td>
                          <td style={{ textAlign: 'center', color: (a.failed_publish_count ?? 0) > 0 ? 'var(--danger)' : 'var(--text-muted)' }}>
                            {a.failed_publish_count ?? 0}
                          </td>
                          <td style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                            {a.last_login_at ? fmtRelative(new Date(a.last_login_at).getTime() / 1000) : (t('common.dash') ?? '—')}
                          </td>
                          <td onClick={e => e.stopPropagation()}>
                            <div style={{ display: 'flex', gap: '0.375rem' }}>
                              {softBan
                                ? <button className="btn btn-ghost btn-icon btn-sm" title={t('acc.clear_ban')} onClick={() => handleClearSoftBan(a.id)}>
                                    <ShieldCheck size={12} />
                                  </button>
                                : <button className="btn btn-ghost btn-icon btn-sm" title={t('acc.mark_ban')} onClick={() => handleMarkSoftBan(a.id)}>
                                    <ShieldOff size={12} />
                                  </button>
                              }
                              <button className="btn btn-ghost btn-icon btn-sm" title={t('acc.delete')} onClick={() => setConfirmDelete(a)} style={{ color: 'var(--danger)' }}>
                                <Trash2 size={12} />
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )
          }
        </div>
      )}

      {/* Detail */}
      <SlideOver open={!!selected} onClose={() => setSelected(null)} title={t('acc.detail_title')}>
        {selected && (
          <div>
            <div style={{ fontWeight: 700, fontSize: '1rem', marginBottom: '0.75rem' }}>{selected.account_handle}</div>
            <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
              <Badge status={selected.platform}>{selected.platform}</Badge>
              <Badge status={selected.status}>{selected.status}</Badge>
              {(selected.soft_ban_detected ?? false) && <Badge status="danger">{t('acc.ban_detect')}</Badge>}
              {selected.session_valid && <Badge status="success">{t('acc.session_valid')}</Badge>}
            </div>
            <div className="card-elevated" style={{ marginBottom: '1rem' }}>
              <StatRow label={t('acc.detail_id')} value={<span className="mono">{selected.id}</span>} mono />
              <StatRow label={t('acc.detail_proxy')} value={selected.proxy_url ?? (t('common.dash') ?? '—')} />
              <StatRow label={t('acc.detail_risk')} value={(selected.risk_score ?? 0).toFixed(2)} />
              <StatRow label={t('acc.detail_warmup')} value={selected.warmup_sessions_completed ?? 0} />
              <StatRow label={t('acc.detail_failed')} value={<span style={{ color: (selected.failed_publish_count ?? 0) > 0 ? 'var(--danger)' : 'inherit' }}>{selected.failed_publish_count ?? 0}</span>} />
              <StatRow label={t('acc.detail_captcha')} value={selected.captcha_hit_count ?? 0} />
              <StatRow label={t('acc.detail_last_login')} value={selected.last_login_at ? fmtRelative(new Date(selected.last_login_at).getTime() / 1000) : (t('common.dash') ?? '—')} />
              <StatRow label={t('acc.detail_created')} value={selected.created_at ? new Date(selected.created_at).toLocaleDateString() : (t('common.dash') ?? '—')} />
            </div>
            <div style={{ display: 'flex', gap: '0.625rem', flexDirection: 'column' }}>
              {(selected.soft_ban_detected ?? false)
                ? <button className="btn btn-secondary" onClick={() => handleClearSoftBan(selected.id)}>
                    <ShieldCheck size={14} /> {t('acc.btn_clear')}
                  </button>
                : <button className="btn btn-secondary" onClick={() => handleMarkSoftBan(selected.id)}>
                    <ShieldOff size={14} /> {t('acc.btn_mark')}
                  </button>
              }
              <button className="btn btn-danger" onClick={() => setConfirmDelete(selected)}>
                <Trash2 size={14} /> {t('acc.btn_delete')}
              </button>
            </div>
          </div>
        )}
      </SlideOver>

      {/* Add Account */}
      <SlideOver open={showAdd} onClose={() => setShowAdd(false)} title={t('acc.add')}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>{t('acc.lbl_platform')}</label>
            <select className="select" value={newPlatform} onChange={e => setNewPlatform(e.target.value)}>
              <option value="tiktok">TikTok</option>
              <option value="facebook">Facebook</option>
              <option value="youtube">YouTube</option>
            </select>
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>{t('acc.lbl_handle')}</label>
            <input className="input" placeholder="@handle or page name" value={newHandle} onChange={e => setNewHandle(e.target.value)} />
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem' }}>{t('acc.lbl_proxy')}</label>
            <input className="input" placeholder="http://proxy:port" value={newProxy} onChange={e => setNewProxy(e.target.value)} />
          </div>
          {createAccount.isError && (
            <div style={{ color: 'var(--danger)', fontSize: '0.8rem' }}>
              {(createAccount.error as Error)?.message}
            </div>
          )}
          <button
            className="btn btn-primary"
            onClick={handleAddAccount}
            disabled={!newHandle || createAccount.isPending}
          >
            {createAccount.isPending ? t('acc.adding') : t('acc.add')}
          </button>
        </div>
      </SlideOver>

      <ConfirmDialog
        open={!!confirmDelete} onClose={() => setConfirmDelete(null)}
        onConfirm={() => confirmDelete && handleDelete(confirmDelete.id)}
        title={t('acc.btn_delete')}
        message={`${t('acc.delete_msg')} ${confirmDelete?.account_handle}?`}
        danger
      />
    </div>
  );
}
