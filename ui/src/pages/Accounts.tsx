// ── Accounts Page ────────────────────────────────────────────────────────────
import React, { useEffect, useState } from 'react';
import { PageHeader, Badge, SlideOver, StatRow, ConfirmDialog, EmptyState } from '@/components/ui';
import { GlassIcon } from '@/components/Icons';
import { useAccounts, useCreateAccount, useDeleteAccount, useMarkSoftBan, useClearSoftBan, useConnectAccount, useUpdateAccount, useConfirmManualLogin } from '@/lib/hooks';
import { fmtRelative } from '@/lib/utils';
import { useI18n } from '@/lib/i18n';
import { PLATFORMS, PlatformBadge, PlatformSelect } from '@/lib/platforms';

interface Account {
  id: string; platform: string; account_handle: string;
  profile_url?: string | null; external_user_id?: string | null;
  status: string; proxy_url: string | null; proxy_country?: string | null; session_valid: boolean; session_status?: string;
  has_cookies?: boolean;
  last_login_at: string | null; avatar_url: string | null; display_name: string | null;
  metadata?: Record<string, unknown>;
  browser_provider?: string;
  real_chrome_user_data_dir?: string | null;
  adspower_profile_id?: string | null;
  last_login_diagnostic?: Record<string, unknown> | null;
  browser_data_dir?: string | null; timezone?: string | null; locale?: string | null;
  viewport_width?: number | null; viewport_height?: number | null;
  risk_score?: number; soft_ban_detected?: boolean;
  warmup_sessions_completed?: number; failed_publish_count?: number;
  captcha_hit_count?: number; can_publish?: boolean; readiness_errors?: string[]; created_at?: string;
}


// ── Platform SVG badge ────────────────────────────────────────────────────────
function PlatformIcon({ platform, size = 24 }: { platform: string; size?: number }) {
  const cfg = PLATFORMS[platform];
  if (!cfg) return <span style={{ fontSize: size * 0.5 }}>?</span>;
  return (
    <img src={cfg.svg} alt={cfg.label} width={size} height={size}
      style={{ borderRadius: size * 0.25, display: 'block', objectFit: 'cover' }} />
  );
}

// ── Avatar ────────────────────────────────────────────────────────────────────
function AccountAvatar({ account, size = 48 }: { account: Account; size?: number }) {
  const [imgErr, setImgErr] = useState(false);
  const initials = (account.display_name || account.account_handle || '?')
    .replace('@', '').slice(0, 2).toUpperCase();
  return (
    <div style={{ position: 'relative', flexShrink: 0 }}>
      <div style={{
        width: size, height: size, borderRadius: '50%', overflow: 'hidden',
        border: '2px solid var(--border)', background: 'var(--surface-2)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: size * 0.35, fontWeight: 700, color: 'var(--text-secondary)',
      }}>
        {account.avatar_url && !imgErr
          ? <img src={account.avatar_url} alt={account.account_handle}
              onError={() => setImgErr(true)}
              style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
          : initials}
      </div>
      <div style={{
        position: 'absolute', bottom: -2, right: -2,
        width: size * 0.44, height: size * 0.44, borderRadius: '50%',
        border: '2px solid var(--bg)', overflow: 'hidden', background: '#fff',
      }}>
        <PlatformIcon platform={account.platform} size={size * 0.44} />
      </div>
    </div>
  );
}

function sessionStatus(account: Account) {
  if (browserProvider(account) === 'adspower_manual' && account.metadata?.manual_login_state === 'connected_by_confirmation' && account.session_valid) {
    const diagnostic = account.last_login_diagnostic ?? (
      typeof account.metadata?.last_login_diagnostic === 'object' && account.metadata?.last_login_diagnostic !== null
        ? account.metadata.last_login_diagnostic as Record<string, unknown>
        : null
    );
    const diagnosticStatus = String(diagnostic?.status ?? '').toUpperCase();
    if (['RATE_LIMITED', 'CAPTCHA_REQUIRED', 'CHECKPOINT_REQUIRED'].includes(diagnosticStatus)) {
      return 'limited';
    }
    return 'connected';
  }
  if (browserProvider(account) === 'adspower_manual' && account.metadata?.manual_login_state === 'browser_opened' && !account.session_valid) {
    return 'browser_opened';
  }
  if (account.session_status) return account.session_status;
  if (account.status === 'limited') return 'limited';
  if (account.session_valid) return 'connected';
  if (account.last_login_at) return 'expired';
  return 'not_connected';
}

function displayAccountStatus(account: Account) {
  if (browserProvider(account) === 'adspower_manual' && sessionStatus(account) === 'connected') {
    return 'healthy';
  }
  return account.status;
}

function SessionPill({ account, t }: { account: Account; t: (k: string) => string }) {
  const status = sessionStatus(account);
  const connected = status === 'connected';
  const limited = status === 'limited';
  const expired = status === 'expired';
  const browserOpened = status === 'browser_opened';
  const label = connected ? t('acc.connected') : limited ? t('acc.limited') : expired ? t('acc.expired') : browserOpened ? t('acc.browser_opened_waiting') : t('acc.not_connected');
  const color = connected ? 'var(--success)' : limited || browserOpened ? 'var(--warning)' : expired ? 'var(--danger)' : 'var(--text-muted)';
  const bg = connected ? 'var(--success-muted)' : limited || browserOpened ? 'var(--warning-muted, var(--surface-2))' : expired ? 'var(--danger-muted)' : 'var(--surface-2)';
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: '0.3rem',
      padding: '0.15rem 0.55rem', borderRadius: '9999px', fontSize: '0.68rem', fontWeight: 600,
      background: bg,
      color,
      border: `1px solid ${connected || limited || expired ? color : 'var(--border)'}`,
    }}>
      <span style={{ width: 6, height: 6, borderRadius: '50%', background: connected || limited || expired ? color : 'var(--border)' }} />
      {label}
    </span>
  );
}

function browserProvider(account: Account) {
  const metadataProvider = typeof account.metadata?.browser_provider === 'string' ? account.metadata.browser_provider : undefined;
  const provider = account.browser_provider ?? metadataProvider ?? 'playwright';
  return provider === 'adspower' ? 'adspower_manual' : provider;
}

function providerLabel(provider: string, t: (k: string) => string) {
  if (provider === 'real_chrome') return t('acc.provider_real_chrome');
  if (provider === 'adspower' || provider === 'adspower_manual') return t('acc.provider_adspower_manual');
  return t('acc.provider_playwright');
}

function sessionSource(account: Account) {
  const diagnostic = account.last_login_diagnostic ?? (
    typeof account.metadata?.last_login_diagnostic === 'object' && account.metadata?.last_login_diagnostic !== null
      ? account.metadata.last_login_diagnostic as Record<string, unknown>
      : null
  );
  return typeof diagnostic?.session_source === 'string' ? diagnostic.session_source : null;
}

function cookiesCaptured(account: Account) {
  const diagnostic = account.last_login_diagnostic ?? (
    typeof account.metadata?.last_login_diagnostic === 'object' && account.metadata?.last_login_diagnostic !== null
      ? account.metadata.last_login_diagnostic as Record<string, unknown>
      : null
  );
  if (typeof diagnostic?.cookies_captured === 'boolean') return diagnostic.cookies_captured;
  return Boolean(account.has_cookies);
}

function BrowserProviderSelect({ value, onChange, t }: { value: string; onChange: (value: string) => void; t: (k: string) => string }) {
  return (
    <select className="input" value={value} onChange={e => onChange(e.target.value)}>
      <option value="playwright">{t('acc.provider_playwright')}</option>
      <option value="real_chrome">{t('acc.provider_real_chrome')}</option>
      <option value="adspower_manual">{t('acc.provider_adspower_manual')}</option>
    </select>
  );
}

function ConnectingOverlay({ platform, stage, t }: { platform: string; stage: string | null; t: (k: string, f?: string) => string }) {
  const plat = PLATFORMS[platform] ?? { label: platform };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.625rem', padding: '1.5rem', textAlign: 'center' }}>
      <div style={{ width: 48, height: 48, borderRadius: '50%', background: 'var(--primary)', opacity: 0.15, animation: 'pulse 1.5s ease infinite' }} />
      <div style={{ fontWeight: 600, fontSize: '0.875rem' }}>{stage ?? t('acc.browser_opening')}</div>
      <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', maxWidth: 260 }}>
        {t('acc.browser_login_hint').replace('{platform}', plat.label)}
      </div>
    </div>
  );
}

function formatConnectError(message: string, t: (k: string) => string) {
  const lowered = message.toLowerCase();
  if (
    lowered.includes('temporarily blocked login') ||
    lowered.includes('maximum number of attempts') ||
    lowered.includes('try again later') ||
    lowered.includes('too many attempts') ||
    lowered.includes('rate')
  ) {
    return t('acc.err_tiktok_runtime_blocked');
  }
  return message;
}

export function Accounts() {
  const { t } = useI18n();
  const { data: accounts = [], isLoading, error } = useAccounts();
  const createAccount  = useCreateAccount();
  const updateAccount  = useUpdateAccount();
  const deleteAccount  = useDeleteAccount();
  const markSoftBan    = useMarkSoftBan();
  const clearSoftBan   = useClearSoftBan();
  const connectAccount = useConnectAccount();
  const confirmManualLogin = useConfirmManualLogin();
  const isDev = import.meta.env.DEV;

  const [selected,      setSelected]      = useState<Account | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<Account | null>(null);
  const [showAdd,       setShowAdd]       = useState(false);
  const [newHandle,     setNewHandle]     = useState('');
  const [newPlatform,   setNewPlatform]   = useState('tiktok');
  const [newProfileUrl, setNewProfileUrl] = useState('');
  const [newProxy,      setNewProxy]      = useState('');
  const [newBrowserProvider, setNewBrowserProvider] = useState('playwright');
  const [newAdsPowerProfileId, setNewAdsPowerProfileId] = useState('');
  const [editProfileUrl, setEditProfileUrl] = useState('');
  const [editProxy, setEditProxy] = useState('');
  const [editBrowserProvider, setEditBrowserProvider] = useState('playwright');
  const [editAdsPowerProfileId, setEditAdsPowerProfileId] = useState('');
  const [connectingId,  setConnectingId]  = useState<string | null>(null);
  const [connectStage, setConnectStage] = useState<string | null>(null);
  const [connectError,  setConnectError]  = useState<string | null>(null);

  useEffect(() => {
    if (!selected) return;
    setEditProfileUrl(selected.profile_url ?? '');
    setEditProxy(selected.proxy_url ?? '');
    setEditBrowserProvider(browserProvider(selected));
    setEditAdsPowerProfileId(selected.adspower_profile_id ?? (typeof selected.metadata?.adspower_profile_id === 'string' ? selected.metadata.adspower_profile_id : ''));
  }, [selected]);

  async function handleAddAndConnect() {
    if (!newHandle.trim()) return;
    try {
      const created = await createAccount.mutateAsync({
        platform: newPlatform,
        account_handle: newHandle.trim(),
        profile_url: newProfileUrl.trim() || undefined,
        proxy_url: newProxy.trim() || undefined,
        browser_provider: newBrowserProvider,
        adspower_profile_id: newBrowserProvider === 'adspower_manual' ? newAdsPowerProfileId.trim() : undefined,
        metadata: {
          browser_provider: newBrowserProvider,
          ...(newBrowserProvider === 'adspower_manual' ? { adspower_profile_id: newAdsPowerProfileId.trim() } : {}),
        },
      });
      setShowAdd(false); setNewHandle(''); setNewProfileUrl(''); setNewProxy(''); setNewBrowserProvider('playwright'); setNewAdsPowerProfileId('');
      await handleConnect(created.id, created.platform);
    } catch { /* shown via createAccount.isError */ }
  }

  async function handleSaveAccount() {
    if (!selected) return;
    setConnectError(null);
    try {
      const updated = await updateAccount.mutateAsync({
        id: selected.id,
        payload: {
          profile_url: editProfileUrl.trim() || null,
          proxy_url: editProxy.trim() || null,
          browser_provider: editBrowserProvider,
          adspower_profile_id: editBrowserProvider === 'adspower_manual' ? editAdsPowerProfileId.trim() || null : null,
        },
      });
      setSelected(updated);
    } catch (e: unknown) {
      setConnectError((e as Error)?.message ?? t('acc.connect_failed'));
    }
  }

  async function handleConnect(id: string, platform: string) {
    setConnectingId(id); setConnectError(null); setConnectStage(t('acc.connect_stage_opening'));
    const timers = [
      window.setTimeout(() => setConnectStage(t('acc.connect_stage_waiting')), 1400),
      window.setTimeout(() => setConnectStage(t('acc.connect_stage_saving')), 260_000),
    ];
    try {
      const result = await connectAccount.mutateAsync(id);
      if (result?.status === 'browser_opened') {
        timers.forEach(timer => window.clearTimeout(timer));
        setConnectStage(t('acc.adspower_waiting_manual'));
        return;
      }
      setConnectStage(t('acc.connect_stage_connected'));
    }
    catch (e: unknown) { setConnectError(formatConnectError((e as Error)?.message ?? t('acc.connect_failed'), t)); }
    finally {
      timers.forEach(timer => window.clearTimeout(timer));
      setConnectingId(null);
      window.setTimeout(() => setConnectStage(null), 800);
    }
  }

  async function handleConfirmManualLogin(id: string) {
    setConnectingId(id); setConnectError(null); setConnectStage(t('acc.adspower_confirming'));
    try {
      const updated = await confirmManualLogin.mutateAsync(id);
      setConnectStage(t('acc.connect_stage_connected'));
      if (selected?.id === id) setSelected(updated);
    } catch (e: unknown) {
      setConnectError((e as Error)?.message ?? t('acc.connect_failed'));
    } finally {
      setConnectingId(null);
      window.setTimeout(() => setConnectStage(null), 800);
    }
  }

  function handleDelete(id: string) {
    deleteAccount.mutate(id, { onSuccess: () => { setSelected(null); setConfirmDelete(null); } });
  }

  const isConnecting = (id: string) => connectingId === id;

  return (
    <div>
      <PageHeader title={t('acc.title')} subtitle={t('acc.sub')}
        action={
          <button className="btn btn-primary btn-sm" onClick={() => setShowAdd(true)}
            style={{ display: 'flex', alignItems: 'center', gap: '0.375rem' }}>
            <GlassIcon name="add-circle" size={15} style={{ filter: 'brightness(0) invert(1)' }} />
            {t('acc.add')}
          </button>
        }
      />

      {connectError && (
        <div style={{
          marginBottom: '1rem', padding: '0.75rem 1rem', borderRadius: 'var(--radius)',
          background: 'var(--danger-muted)', border: '1px solid var(--danger)',
          color: 'var(--danger)', fontSize: '0.8125rem',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <span>⚠ {connectError}</span>
          <button className="btn btn-ghost btn-icon btn-sm" onClick={() => setConnectError(null)}>✕</button>
        </div>
      )}

      {isLoading && (
        <div style={{ textAlign: 'center', padding: '3rem' }}>
          <GlassIcon name="user" size={36} style={{ opacity: 0.3, marginBottom: '0.5rem' }} />
          <div style={{ color: 'var(--text-muted)' }}>{t('acc.loading')}</div>
        </div>
      )}
      {error && <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--danger)' }}>{(error as Error).message}</div>}

      {!isLoading && !error && (
        <>
          {(accounts as Account[]).length === 0
            ? <div className="card"><EmptyState icon="user" message={t('acc.no_data')} /></div>
            : (
              <div style={{ display: 'grid', gap: '0.875rem', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))' }}>
                {(accounts as Account[]).map(a => {
                  const connecting = isConnecting(a.id);
                  const riskScore = a.risk_score ?? 0;
                  const softBan = a.soft_ban_detected ?? false;
                  const plat = PLATFORMS[a.platform] ?? { label: a.platform, bg: 'var(--primary)', color: '#fff' };
                  const provider = browserProvider(a);
                  const isAdsPowerManual = provider === 'adspower_manual' || provider === 'adspower';

                  return (
                    <div key={a.id} className="card" style={{
                      padding: '1.125rem', cursor: 'pointer',
                      opacity: connecting ? 0.7 : 1, transition: 'opacity 0.2s',
                      borderLeft: softBan ? '3px solid var(--danger)' : a.session_valid ? '3px solid var(--success)' : '3px solid var(--border)',
                    }} onClick={() => !connecting && setSelected(a)}>
                      {connecting ? <ConnectingOverlay platform={a.platform} stage={connectStage} t={t} /> : (
                        <>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '0.875rem', marginBottom: '0.875rem' }}>
                            <AccountAvatar account={a} size={52} />
                            <div style={{ flex: 1, minWidth: 0 }}>
                              <div style={{ fontWeight: 700, fontSize: '0.9375rem', color: 'var(--text-primary)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                {a.display_name || a.account_handle}
                              </div>
                              {a.display_name && <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>@{a.account_handle}</div>}
                              <div style={{ display: 'flex', gap: '0.375rem', marginTop: '0.3rem', flexWrap: 'wrap', alignItems: 'center' }}>
                                <span style={{
                                  display: 'inline-flex', alignItems: 'center', gap: '0.3rem',
                                  padding: '0.1rem 0.5rem', borderRadius: '9999px',
                                  fontSize: '0.65rem', fontWeight: 700,
                                  background: plat.bg, color: plat.color,
                                }}>
                                  <img src={PLATFORMS[a.platform]?.svg} alt="" width={12} height={12}
                                    style={{ borderRadius: 2, opacity: 0.9 }} />
                                  {plat.label}
                                </span>
                                <Badge status={displayAccountStatus(a)}>{displayAccountStatus(a)}</Badge>
                                <span className="badge badge-muted">{providerLabel(provider, t)}</span>
                                {isAdsPowerManual && sessionSource(a) === 'adspower_profile' && <span className="badge badge-muted">{t('acc.session_source_adspower')}</span>}
                                {isAdsPowerManual && <span className="badge badge-muted">{cookiesCaptured(a) ? t('acc.cookies_yes') : t('acc.cookies_no')}</span>}
                                {softBan && <span className="badge badge-danger">⚠ shadow ban</span>}
                              </div>
                            </div>
                          </div>

                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.875rem' }}>
                            <SessionPill account={a} t={t} />
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                              <div style={{ width: 36, height: 4, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
                                <div style={{
                                  width: `${riskScore * 100}%`, height: '100%', borderRadius: 2,
                                  background: riskScore >= 0.7 ? 'var(--danger)' : riskScore >= 0.4 ? 'var(--warning)' : 'var(--success)',
                                }} />
                              </div>
                              {t('acc.risk')} {Math.round(riskScore * 100)}%
                            </div>
                          </div>

                          {a.last_login_at && (
                            <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginBottom: '0.75rem' }}>
                              {t('acc.last_login_rel')} {fmtRelative(new Date(a.last_login_at).getTime() / 1000)}
                            </div>
                          )}

                          <div style={{ display: 'flex', gap: '0.5rem' }} onClick={e => e.stopPropagation()}>
                            <button className="btn btn-primary btn-sm"
                              style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.35rem' }}
                              onClick={() => handleConnect(a.id, a.platform)} disabled={!!connectingId}>
                              <GlassIcon name="key" size={13} style={{ filter: 'brightness(0) invert(1)' }} />
                              {isAdsPowerManual ? t('acc.open_adspower_profile') : a.session_valid ? t('acc.reconnect') : t('acc.connect')}
                            </button>
                            {isAdsPowerManual && !a.session_valid && (
                              <button className="btn btn-secondary btn-sm" onClick={() => handleConfirmManualLogin(a.id)} disabled={!!connectingId}>
                                {t('acc.confirm_manual_login')}
                              </button>
                            )}
                            {softBan
                              ? <button className="btn btn-secondary btn-sm btn-icon" title={t('acc.clear_ban_title')} onClick={() => clearSoftBan.mutate(a.id)}>
                                  <GlassIcon name="shield" size={14} style={{ opacity: 0.8 }} />
                                </button>
                              : <button className="btn btn-ghost btn-sm btn-icon" title={t('acc.mark_ban_title')} onClick={() => markSoftBan.mutate(a.id)}>
                                  <GlassIcon name="warning" size={14} style={{ opacity: 0.7 }} />
                                </button>
                            }
                            <button className="btn btn-ghost btn-sm btn-icon" title={t('acc.delete')}
                              onClick={() => setConfirmDelete(a)} style={{ color: 'var(--danger)' }}>
                              <GlassIcon name="trash" size={14} style={{ filter: 'brightness(0) saturate(100%) invert(26%) sepia(90%) saturate(3000%) hue-rotate(345deg)', opacity: 0.8 }} />
                            </button>
                          </div>
                        </>
                      )}
                    </div>
                  );
                })}
              </div>
            )
          }
        </>
      )}

      {/* Detail slide-over */}
      <SlideOver open={!!selected} onClose={() => setSelected(null)} title={t('acc.slide_detail')}>
        {selected && (
          <div>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.75rem', marginBottom: '1.25rem', padding: '1rem 0' }}>
              <AccountAvatar account={selected} size={80} />
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontWeight: 700, fontSize: '1.125rem' }}>{selected.display_name || selected.account_handle}</div>
                {selected.display_name && <div style={{ color: 'var(--text-muted)', fontSize: '0.8125rem' }}>@{selected.account_handle}</div>}
              </div>
              <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', justifyContent: 'center' }}>
                <Badge status="info">{PLATFORMS[selected.platform]?.label ?? selected.platform}</Badge>
                <Badge status={displayAccountStatus(selected)}>{displayAccountStatus(selected)}</Badge>
                {selected.soft_ban_detected && <Badge status="danger">shadow ban</Badge>}
                {selected.session_valid && <Badge status="success">{t('acc.connected')}</Badge>}
                {browserProvider(selected) === 'adspower_manual' && sessionSource(selected) === 'adspower_profile' && <Badge status="info">{t('acc.session_source_adspower')}</Badge>}
                {browserProvider(selected) === 'adspower_manual' && <Badge status={cookiesCaptured(selected) ? 'success' : 'info'}>{cookiesCaptured(selected) ? t('acc.cookies_yes') : t('acc.cookies_no')}</Badge>}
              </div>
            </div>

            <div className="card-elevated" style={{ marginBottom: '1rem' }}>
              <StatRow label={t('acc.detail_id')} value={<span className="mono" style={{ fontSize: '0.7rem' }}>{selected.id}</span>} mono />
              <StatRow label={t('acc.detail_profile')} value={selected.profile_url
                ? <a href={selected.profile_url} target="_blank" rel="noreferrer" style={{ color: 'var(--primary)' }}>{selected.profile_url}</a>
                : '—'} />
              <StatRow label={t('acc.session_status')} value={<SessionPill account={selected} t={t} />} />
              <StatRow label={t('acc.detail_provider')} value={providerLabel(browserProvider(selected), t)} />
              <StatRow label={t('acc.detail_proxy')} value={selected.proxy_url ?? '—'} />
              <StatRow label={t('acc.detail_timezone')} value={selected.timezone ?? '—'} />
              <StatRow label={t('acc.detail_locale')} value={selected.locale ?? '—'} />
              {isDev && <StatRow label={t('acc.detail_browser_profile')} value={<span className="mono" style={{ fontSize: '0.68rem', wordBreak: 'break-all' }}>{browserProvider(selected) === 'real_chrome' ? selected.real_chrome_user_data_dir ?? '—' : browserProvider(selected) === 'adspower_manual' ? selected.adspower_profile_id ?? '—' : selected.browser_data_dir ?? '—'}</span>} />}
              <StatRow label={t('acc.detail_risk')} value={`${Math.round((selected.risk_score ?? 0) * 100)}%`} />
              <StatRow label={t('acc.detail_warmup')} value={selected.warmup_sessions_completed ?? 0} />
              <StatRow label={t('acc.detail_failed')} value={
                <span style={{ color: (selected.failed_publish_count ?? 0) > 0 ? 'var(--danger)' : 'inherit' }}>
                  {selected.failed_publish_count ?? 0}
                </span>
              } />
              <StatRow label={t('acc.detail_captcha')} value={selected.captcha_hit_count ?? 0} />
              <StatRow label={t('acc.detail_last_login')} value={selected.last_login_at ? fmtRelative(new Date(selected.last_login_at).getTime() / 1000) : '—'} />
              <StatRow label={t('acc.detail_created')} value={selected.created_at ? new Date(selected.created_at).toLocaleDateString() : '—'} />
            </div>

            <div className="card-elevated" style={{ marginBottom: '1rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              <div>
                <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem', fontWeight: 600 }}>
                  {t('acc.lbl_profile_url')}
                </label>
                <input className="input" value={editProfileUrl} placeholder="https://www.tiktok.com/@username" onChange={e => setEditProfileUrl(e.target.value)} />
              </div>
              <div>
                <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem', fontWeight: 600 }}>
                  {t('acc.lbl_proxy')}
                </label>
                <input className="input" value={editProxy} placeholder={t('acc.ph_proxy')} onChange={e => setEditProxy(e.target.value)} />
              </div>
              <div>
                <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem', fontWeight: 600 }}>
                  {t('acc.lbl_browser_provider')}
                </label>
                <BrowserProviderSelect value={editBrowserProvider} onChange={setEditBrowserProvider} t={t} />
              </div>
              {editBrowserProvider === 'adspower_manual' && (
                <div>
                  <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem', fontWeight: 600 }}>
                    {t('acc.lbl_adspower_profile_id')}
                  </label>
                  <input className="input" value={editAdsPowerProfileId} placeholder="AdsPower user_id / profile id" onChange={e => setEditAdsPowerProfileId(e.target.value)} />
                </div>
              )}
              <button className="btn btn-secondary" onClick={handleSaveAccount} disabled={updateAccount.isPending}>
                <GlassIcon name="save" size={14} /> {updateAccount.isPending ? t('acc.saving') : t('acc.save_account')}
              </button>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.625rem' }}>
              <button className="btn btn-primary" disabled={isConnecting(selected.id)}
                onClick={() => handleConnect(selected.id, selected.platform)}>
                <GlassIcon name="key" size={14} style={{ filter: 'brightness(0) invert(1)' }} />
                {' '}{isConnecting(selected.id) ? t('acc.browser_opening') : browserProvider(selected) === 'adspower_manual' ? t('acc.open_adspower_profile') : selected.session_valid ? t('acc.reconnect_account') : t('acc.connect_account')}
              </button>
              {browserProvider(selected) === 'adspower_manual' && (
                <button className="btn btn-secondary" disabled={isConnecting(selected.id)}
                  onClick={() => handleConfirmManualLogin(selected.id)}>
                  <GlassIcon name="shield" size={14} />
                  {' '}{t('acc.confirm_manual_login')}
                </button>
              )}
              {(selected.soft_ban_detected ?? false)
                ? <button className="btn btn-secondary" onClick={() => clearSoftBan.mutate(selected.id, { onSuccess: () => setSelected(null) })}>
                    <GlassIcon name="shield" size={14} /> {t('acc.btn_clear')}
                  </button>
                : <button className="btn btn-secondary" onClick={() => markSoftBan.mutate(selected.id, { onSuccess: () => setSelected(null) })}>
                    <GlassIcon name="warning" size={14} /> {t('acc.btn_mark')}
                  </button>
              }
              <button className="btn btn-danger" onClick={() => setConfirmDelete(selected)}>
                <GlassIcon name="trash" size={14} style={{ filter: 'brightness(0) invert(1)' }} /> {t('acc.btn_delete')}
              </button>
            </div>
          </div>
        )}
      </SlideOver>

      {/* Add Account slide-over */}
      <SlideOver open={showAdd} onClose={() => setShowAdd(false)} title={t('acc.slide_add')}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1.125rem' }}>
          <div>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '0.625rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
              {t('acc.lbl_platform_sel')}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '0.5rem' }}>
              {(Object.keys(PLATFORMS) as string[]).map(p => {
                const cfg = PLATFORMS[p];
                const active = newPlatform === p;
                return (
                  <button key={p} onClick={() => setNewPlatform(p)} style={{
                    padding: '0.75rem 0.5rem', borderRadius: 'var(--radius)',
                    border: active ? `2px solid ${cfg.bg}` : '2px solid var(--border)',
                    background: active ? `${cfg.bg}16` : 'transparent',
                    cursor: 'pointer', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.3rem', transition: 'all 0.15s',
                  }}>
                    <img src={cfg.svg} alt={cfg.label} width={32} height={32} style={{ borderRadius: 8 }} />
                    <div style={{ fontSize: '0.72rem', fontWeight: active ? 700 : 500, color: active ? cfg.bg : 'var(--text-secondary)' }}>
                      {cfg.label}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem', fontWeight: 600 }}>
              {t('acc.lbl_handle_full')} <span style={{ fontWeight: 400 }}>{t('acc.lbl_handle_hint')}</span>
            </label>
            <input className="input" placeholder={t('acc.ph_handle')} value={newHandle}
              onChange={e => setNewHandle(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleAddAndConnect()} />
          </div>

          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem', fontWeight: 600 }}>
              {t('acc.lbl_profile_url')} <span style={{ fontWeight: 400 }}>{t('acc.lbl_profile_hint')}</span>
            </label>
            <input className="input" placeholder="https://www.tiktok.com/@username" value={newProfileUrl}
              onChange={e => setNewProfileUrl(e.target.value)} />
          </div>

          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem', fontWeight: 600 }}>
              {t('acc.lbl_proxy')} <span style={{ fontWeight: 400 }}>{t('acc.lbl_proxy_hint')}</span>
            </label>
            <input className="input" placeholder={t('acc.ph_proxy')} value={newProxy}
              onChange={e => setNewProxy(e.target.value)} />
          </div>

          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem', fontWeight: 600 }}>
              {t('acc.lbl_browser_provider')}
            </label>
            <BrowserProviderSelect value={newBrowserProvider} onChange={setNewBrowserProvider} t={t} />
          </div>

          {newBrowserProvider === 'adspower_manual' && (
            <div>
              <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem', fontWeight: 600 }}>
                {t('acc.lbl_adspower_profile_id')}
              </label>
              <input className="input" placeholder="AdsPower user_id / profile id" value={newAdsPowerProfileId}
                onChange={e => setNewAdsPowerProfileId(e.target.value)} />
            </div>
          )}

          <div style={{
            padding: '0.75rem 1rem', borderRadius: 'var(--radius)',
            background: 'var(--surface-2)', border: '1px solid var(--border)',
            fontSize: '0.78rem', color: 'var(--text-muted)', lineHeight: 1.5,
          }}>
            {t('acc.security_note')}
          </div>

          {createAccount.isError && (
            <div style={{ color: 'var(--danger)', fontSize: '0.8rem' }}>
              {(createAccount.error as Error)?.message}
            </div>
          )}

          <button className="btn btn-primary" style={{ width: '100%', padding: '0.75rem' }}
            onClick={handleAddAndConnect}
            disabled={!newHandle.trim() || (newBrowserProvider === 'adspower_manual' && !newAdsPowerProfileId.trim()) || createAccount.isPending}>
            {createAccount.isPending ? t('acc.creating') : t('acc.connect_login')}
          </button>

          <p style={{ textAlign: 'center', fontSize: '0.72rem', color: 'var(--text-muted)', margin: 0 }}>
            {t('acc.login_timeout')}
          </p>
        </div>
      </SlideOver>

      <ConfirmDialog
        open={!!confirmDelete}
        onClose={() => setConfirmDelete(null)}
        onConfirm={() => confirmDelete && handleDelete(confirmDelete.id)}
        title={t('acc.btn_delete')}
        message={t('acc.delete_confirm_msg').replace('{handle}', confirmDelete?.account_handle ?? '')}
        danger
      />
    </div>
  );
}
