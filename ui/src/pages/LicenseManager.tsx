// ── License Manager — Admin UI ─────────────────────────────────────────────
// Protected by X-Admin-Secret. Shows all license keys, allows create/revoke/reset.
import React, { useState, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useI18n } from '@/lib/i18n';
import { api } from '@/lib/api';
import { GlassIcon } from '@/components/Icons';

// ── Types ──────────────────────────────────────────────────────────────────
interface License {
  id: string;
  license_key: string;
  label: string | null;
  machine_id: string | null;
  activated_at: string | null;
  expires_at: string | null;
  is_active: boolean;
  notes: string | null;
  created_at: string;
}

// ── Helpers ────────────────────────────────────────────────────────────────
function fmtDate(s: string | null): string {
  if (!s) return '—';
  return new Date(s).toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric' });
}
function truncate(s: string | null, n = 14): string {
  if (!s) return '—';
  return s.length > n ? s.slice(0, n) + '…' : s;
}

// ── Sub-components ─────────────────────────────────────────────────────────
function Badge({ active, expired }: { active: boolean; expired: boolean }) {
  const { t } = useI18n();
  if (!active) return <span style={styles.badgeRevoked}>{t('lic.badge_revoked')}</span>;
  if (expired)  return <span style={styles.badgeExpired}>{t('lic.badge_expired')}</span>;
  return <span style={styles.badgeActive}>{t('lic.badge_active')}</span>;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  const { t } = useI18n();
  return (
    <button onClick={copy} style={styles.copyBtn} title={t('lic.tooltip_copy')}>
      <GlassIcon name={copied ? 'check-circle' : 'document'} size={13} style={{ opacity: 0.7 }} />
    </button>
  );
}

// ── Main Component ─────────────────────────────────────────────────────────
export function LicenseManager() {
  const { t } = useI18n();
  const qc = useQueryClient();

  // Admin auth state (stored only in component memory, never persisted)
  const [secret, setSecret]       = useState('');
  const [secretInput, setSecretInput] = useState('');
  const [authError, setAuthError]  = useState('');

  // Create dialog
  const [showCreate, setShowCreate] = useState(false);
  const [newLabel, setNewLabel]     = useState('');
  const [newExpires, setNewExpires] = useState('');
  const [newNotes, setNewNotes]     = useState('');

  // Confirm dialog
  const [confirm, setConfirm] = useState<{ action: string; key: string } | null>(null);

  const isAuthed = Boolean(secret);

  // ── Queries ───────────────────────────────────────────────────────────────
  const { data: licenses = [], isLoading, error, refetch } = useQuery<License[]>({
    queryKey: ['admin-licenses', secret],
    queryFn:  () => api.adminListLicenses(secret),
    enabled:  isAuthed,
    staleTime: 0,
    retry: false,
  });

  // ── Mutations ─────────────────────────────────────────────────────────────
  const createMut = useMutation({
    mutationFn: () => api.adminCreateLicense(secret, {
      label:       newLabel.trim() || undefined,
      expires_days: newExpires ? parseInt(newExpires) : undefined,
      notes:        newNotes.trim() || undefined,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-licenses'] });
      setShowCreate(false);
      setNewLabel(''); setNewExpires(''); setNewNotes('');
    },
  });

  const revokeMut = useMutation({
    mutationFn: (key: string) => api.adminRevokeLicense(secret, key),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['admin-licenses'] }); setConfirm(null); },
  });

  const reactivateMut = useMutation({
    mutationFn: (key: string) => api.adminReactivateLicense(secret, key),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['admin-licenses'] }); setConfirm(null); },
  });

  const resetMut = useMutation({
    mutationFn: (key: string) => api.adminResetMachine(secret, key),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['admin-licenses'] }); setConfirm(null); },
  });

  // ── Auth submit ───────────────────────────────────────────────────────────
  const handleAuth = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    const s = secretInput.trim();
    if (!s) return;
    setSecret(s);
    setAuthError('');
    // Verify it works — error will surface via query
  }, [secretInput]);

  // Detect 403 from query error
  React.useEffect(() => {
    if (error) {
      const msg = (error as Error).message ?? '';
      if (msg.includes('403') || msg.includes('Invalid admin')) {
        setAuthError(t('lic.auth_err'));
        setSecret('');
      }
    }
  }, [error]);

  // ── Confirm dispatch ──────────────────────────────────────────────────────
  function handleConfirm() {
    if (!confirm) return;
    if (confirm.action === 'revoke')   revokeMut.mutate(confirm.key);
    if (confirm.action === 'activate') reactivateMut.mutate(confirm.key);
    if (confirm.action === 'reset')    resetMut.mutate(confirm.key);
  }

  // ── Stats ─────────────────────────────────────────────────────────────────
  const totalActive  = licenses.filter(l => l.is_active).length;
  const totalBound   = licenses.filter(l => l.machine_id).length;
  const totalRevoked = licenses.filter(l => !l.is_active).length;

  // ── Render: Login wall ─────────────────────────────────────────────────────
  if (!isAuthed) {
    return (
      <div style={styles.page}>
        <div style={styles.authCard}>
          <div style={styles.authIcon}><GlassIcon name="key" size={48} /></div>
          <h2 style={styles.authTitle}>{t('lic.auth_title')}</h2>
          <p style={styles.authSub}>{t('lic.auth_sub')}</p>
          <form onSubmit={handleAuth} style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            <input
              id="admin-secret-input"
              type="password"
              className="input"
              placeholder="ADMIN_SECRET"
              value={secretInput}
              onChange={e => setSecretInput(e.target.value)}
              autoFocus
              style={{ width: '100%', boxSizing: 'border-box', letterSpacing: '0.1em' }}
            />
            {authError && <div style={styles.errBox}>{authError}</div>}
            <button id="admin-auth-btn" type="submit" className="btn btn-primary" style={{ height: 42, fontWeight: 700 }}>
              {t('lic.auth_btn')}
            </button>
          </form>
        </div>
      </div>
    );
  }

  // ── Render: Main dashboard ─────────────────────────────────────────────────
  return (
    <div style={styles.page}>
      {/* Header */}
      <div style={styles.header}>
        <div>
          <h1 style={styles.title}>{t('lic.title')}</h1>
          <p style={styles.subtitle}>{t('lic.sub')}</p>
        </div>
        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
          <button
            id="license-refresh-btn"
            onClick={() => refetch()}
            className="btn"
            style={styles.secondaryBtn}
          >
            {t('lic.act_refresh')}
          </button>
          <button
            id="license-create-btn"
            onClick={() => setShowCreate(true)}
            className="btn btn-primary"
            style={{ height: 40, fontWeight: 700, paddingInline: '1.25rem' }}
          >
            {t('lic.act_create')}
          </button>
          <button
            onClick={() => setSecret('')}
            className="btn"
            style={{ ...styles.secondaryBtn, color: 'var(--danger)' }}
            title={t('lic.act_logout')}
          >
            <GlassIcon name="cross-circle" size={15} style={{ filter: 'brightness(0) saturate(100%) invert(26%) sepia(90%) saturate(3000%) hue-rotate(345deg)', opacity: 0.85 }} />
          </button>
        </div>
      </div>

      {/* Stats */}
      <div style={styles.statsRow}>
        {[
          { label: t('lic.stat_total'), value: licenses.length, icon: 'key',          color: 'var(--primary)' },
          { label: t('lic.stat_active'), value: totalActive,  icon: 'check-circle',  color: '#22c55e' },
          { label: t('lic.stat_bound'), value: totalBound,   icon: 'cloud',          color: '#f59e0b' },
          { label: t('lic.stat_revoked'), value: totalRevoked, icon: 'cross-circle', color: 'var(--danger)' },
        ].map(s => (
          <div key={s.label} style={styles.statCard}>
            <GlassIcon name={s.icon as any} size={26} style={{ opacity: 0.8, marginBottom: '0.25rem' }} />
            <div style={{ fontSize: '1.75rem', fontWeight: 800, color: s.color }}>{s.value}</div>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{s.label}</div>
          </div>
        ))}
      </div>

      {/* Table */}
      {isLoading ? (
        <div style={styles.loadingBox}>{t('lic.loading')}</div>
      ) : (
        <div style={styles.tableWrap}>
          <table style={styles.table}>
            <thead>
              <tr>
                {[t('lic.col_key'), t('lic.col_label'), t('lic.col_status'), t('lic.col_machine'), t('lic.col_act'), t('lic.col_exp'), t('lic.col_actions')].map(h => (
                  <th key={h} style={styles.th}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {licenses.length === 0 ? (
                <tr>
                  <td colSpan={7} style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-muted)' }} dangerouslySetInnerHTML={{ __html: t('lic.no_data') }}>
                  </td>
                </tr>
              ) : licenses.map(lic => {
                const isExpired = lic.expires_at
                  ? new Date(lic.expires_at) < new Date()
                  : false;
                return (
                  <tr key={lic.id} style={styles.tr}>
                    <td style={styles.td}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                        <code style={styles.keyCode}>{lic.license_key}</code>
                        <CopyButton text={lic.license_key} />
                      </div>
                    </td>
                    <td style={styles.td}>
                      <span style={{ fontSize: '0.875rem' }}>{lic.label || '—'}</span>
                    </td>
                    <td style={styles.td}>
                      <Badge active={lic.is_active} expired={isExpired} />
                    </td>
                    <td style={styles.td}>
                      {lic.machine_id ? (
                        <span style={{ ...styles.machineChip, display: 'inline-flex', alignItems: 'center', gap: '0.25rem' }} title={lic.machine_id}>
                          <GlassIcon name="cloud" size={11} style={{ opacity: 0.8 }} />
                          {truncate(lic.machine_id, 12)}
                        </span>
                      ) : (
                        <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{t('lic.val_unbound')}</span>
                      )}
                    </td>
                    <td style={{ ...styles.td, fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                      {fmtDate(lic.activated_at)}
                    </td>
                    <td style={{ ...styles.td, fontSize: '0.8rem', color: isExpired ? 'var(--danger)' : 'var(--text-muted)' }}>
                      {lic.expires_at ? fmtDate(lic.expires_at) : t('lic.val_forever')}
                    </td>
                    <td style={styles.td}>
                      <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
                        {lic.machine_id && (
                          <button
                            id={`reset-machine-${lic.id}`}
                            className="btn"
                            style={styles.actionBtn}
                            onClick={() => setConfirm({ action: 'reset', key: lic.license_key })}
                            title="Reset machine binding"
                          >
                            {t('lic.btn_reset')}
                          </button>
                        )}
                        {lic.is_active ? (
                          <button
                            id={`revoke-${lic.id}`}
                            className="btn"
                            style={{ ...styles.actionBtn, color: 'var(--danger)', borderColor: 'var(--danger)' }}
                            onClick={() => setConfirm({ action: 'revoke', key: lic.license_key })}
                          >
                            {t('lic.btn_revoke')}
                          </button>
                        ) : (
                          <button
                            id={`activate-${lic.id}`}
                            className="btn"
                            style={{ ...styles.actionBtn, color: '#22c55e', borderColor: '#22c55e' }}
                            onClick={() => setConfirm({ action: 'activate', key: lic.license_key })}
                          >
                            {t('lic.btn_reactivate')}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Create Dialog */}
      {showCreate && (
        <div style={styles.overlay}>
          <div style={styles.dialog}>
            <h3 style={{ margin: '0 0 1.25rem', fontWeight: 800, fontSize: '1.1rem' }}>
              {t('lic.create_title')}
            </h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.875rem' }}>
              <div>
                <label style={styles.label}>{t('lic.lbl_label')}</label>
                <input
                  id="new-license-label"
                  className="input"
                  style={styles.dialogInput}
                  placeholder={t('lic.ph_label')}
                  value={newLabel}
                  onChange={e => setNewLabel(e.target.value)}
                  autoFocus
                />
              </div>
              <div>
                <label style={styles.label}>{t('lic.lbl_exp')}</label>
                <input
                  id="new-license-expires"
                  className="input"
                  style={styles.dialogInput}
                  type="number"
                  placeholder="VD: 365"
                  value={newExpires}
                  onChange={e => setNewExpires(e.target.value)}
                  min={1}
                />
              </div>
              <div>
                <label style={styles.label}>{t('lic.lbl_notes')}</label>
                <input
                  id="new-license-notes"
                  className="input"
                  style={styles.dialogInput}
                  placeholder={t('lic.ph_notes')}
                  value={newNotes}
                  onChange={e => setNewNotes(e.target.value)}
                />
              </div>
              {createMut.error && (
                <div style={styles.errBox}>{(createMut.error as Error).message}</div>
              )}
            </div>
            <div style={{ display: 'flex', gap: '0.75rem', marginTop: '1.5rem', justifyContent: 'flex-end' }}>
              <button
                id="create-cancel-btn"
                className="btn"
                style={styles.secondaryBtn}
                onClick={() => setShowCreate(false)}
              >
                {t('lic.btn_cancel')}
              </button>
              <button
                id="create-confirm-btn"
                className="btn btn-primary"
                style={{ fontWeight: 700, height: 40, paddingInline: '1.25rem' }}
                onClick={() => createMut.mutate()}
                disabled={createMut.isPending}
              >
                {createMut.isPending ? t('lic.creating') : t('lic.btn_confirm_create')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Confirm Dialog */}
      {confirm && (
        <div style={styles.overlay}>
          <div style={{ ...styles.dialog, maxWidth: 420 }}>
            <h3 style={{ margin: '0 0 0.75rem', fontWeight: 800 }}>
              {confirm.action === 'revoke'   ? t('lic.confirm_rev_title') : ''}
              {confirm.action === 'activate' ? t('lic.confirm_act_title') : ''}
              {confirm.action === 'reset'    ? t('lic.confirm_res_title') : ''}
            </h3>
            <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem', margin: '0 0 0.5rem' }}>
              Key: <code style={styles.keyCode}>{confirm.key}</code>
            </p>
            {confirm.action === 'revoke' && (
              <p style={{ color: 'var(--danger)', fontSize: '0.8125rem' }}>
                {t('lic.confirm_rev_msg')}
              </p>
            )}
            {confirm.action === 'reset' && (
              <p style={{ color: '#f59e0b', fontSize: '0.8125rem' }}>
                {t('lic.confirm_res_msg')}
              </p>
            )}
            <div style={{ display: 'flex', gap: '0.75rem', marginTop: '1.25rem', justifyContent: 'flex-end' }}>
              <button className="btn" style={styles.secondaryBtn} onClick={() => setConfirm(null)}>{t('lic.btn_cancel')}</button>
              <button
                id="confirm-action-btn"
                className="btn btn-primary"
                style={{
                  fontWeight: 700, height: 40, paddingInline: '1.25rem',
                  background: confirm.action === 'revoke' ? 'var(--danger)' : undefined,
                }}
                onClick={handleConfirm}
                disabled={revokeMut.isPending || resetMut.isPending || reactivateMut.isPending}
              >
                {t('lic.btn_confirm')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Styles ─────────────────────────────────────────────────────────────────
const styles: Record<string, React.CSSProperties> = {
  page: { padding: '2rem', minHeight: '100vh', background: 'var(--bg)', maxWidth: 1300, margin: '0 auto' },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1.75rem', flexWrap: 'wrap', gap: '1rem' },
  title: { margin: 0, fontSize: '1.5rem', fontWeight: 800 },
  subtitle: { margin: '0.25rem 0 0', color: 'var(--text-muted)', fontSize: '0.875rem' },
  statsRow: { display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '1rem', marginBottom: '1.5rem' },
  statCard: { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1.25rem', display: 'flex', flexDirection: 'column', gap: '0.25rem', alignItems: 'flex-start' },
  tableWrap: { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', overflow: 'hidden' },
  table: { width: '100%', borderCollapse: 'collapse' },
  th: { padding: '0.75rem 1rem', textAlign: 'left', fontSize: '0.6875rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)', background: 'rgba(0,0,0,0.15)', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' },
  td: { padding: '0.75rem 1rem', borderBottom: '1px solid var(--border)', verticalAlign: 'middle' },
  tr: { transition: 'background 0.15s' },
  keyCode: { fontFamily: 'monospace', fontSize: '0.85rem', background: 'rgba(99,102,241,0.12)', color: 'var(--primary)', padding: '0.2em 0.5em', borderRadius: 4 },
  copyBtn: { background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', fontSize: '1rem', padding: '0.1em 0.3em', borderRadius: 4, transition: 'color 0.15s' },
  machineChip: { fontSize: '0.75rem', background: 'rgba(245,158,11,0.12)', color: '#f59e0b', padding: '0.2em 0.6em', borderRadius: 99, fontFamily: 'monospace' },
  badgeActive:  { fontSize: '0.7rem', fontWeight: 700, background: 'rgba(34,197,94,0.15)', color: '#22c55e', padding: '0.2em 0.6em', borderRadius: 99, textTransform: 'uppercase', letterSpacing: '0.05em' },
  badgeRevoked: { fontSize: '0.7rem', fontWeight: 700, background: 'rgba(239,68,68,0.15)', color: 'var(--danger)', padding: '0.2em 0.6em', borderRadius: 99, textTransform: 'uppercase', letterSpacing: '0.05em' },
  badgeExpired: { fontSize: '0.7rem', fontWeight: 700, background: 'rgba(245,158,11,0.15)', color: '#f59e0b', padding: '0.2em 0.6em', borderRadius: 99, textTransform: 'uppercase', letterSpacing: '0.05em' },
  actionBtn: { fontSize: '0.75rem', padding: '0.3em 0.7em', height: 'auto', borderRadius: 6, border: '1px solid var(--border)', background: 'transparent', cursor: 'pointer', color: 'var(--text-muted)', fontWeight: 600 },
  secondaryBtn: { height: 40, paddingInline: '1rem', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', cursor: 'pointer', color: 'var(--text)', fontWeight: 600, fontSize: '0.875rem' },
  loadingBox: { textAlign: 'center', padding: '3rem', color: 'var(--text-muted)', background: 'var(--surface)', borderRadius: 'var(--radius)', border: '1px solid var(--border)' },
  overlay: { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 },
  dialog: { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '2rem', width: '100%', maxWidth: 520, boxShadow: '0 24px 64px rgba(0,0,0,0.5)' },
  label: { display: 'block', fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '0.375rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' },
  dialogInput: { width: '100%', boxSizing: 'border-box' },
  errBox: { padding: '0.625rem 0.875rem', background: 'rgba(239,68,68,0.1)', border: '1px solid var(--danger)', borderRadius: 'var(--radius-sm)', fontSize: '0.8125rem', color: 'var(--danger)' },
  authCard: { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '2.5rem', width: '100%', maxWidth: 400, margin: '8rem auto', boxShadow: '0 8px 40px rgba(0,0,0,0.35)', display: 'flex', flexDirection: 'column', gap: '0' },
  authIcon: { fontSize: '2.5rem', marginBottom: '1rem', textAlign: 'center' as const },
  authTitle: { margin: '0 0 0.375rem', textAlign: 'center' as const, fontWeight: 800, fontSize: '1.25rem' },
  authSub: { margin: '0 0 1.5rem', textAlign: 'center' as const, color: 'var(--text-muted)', fontSize: '0.8125rem' },
};
