import React, { useState } from 'react';
import { Shield, RefreshCw, KeyRound, AlertTriangle } from 'lucide-react';
import { api } from '@/lib/api';
import type { LicenseResponse, LicenseStatus } from '@/types/license';

type Props = {
  status?: LicenseResponse | null;
  onLicenseChanged?: () => Promise<void> | void;
};

const STATUS_MESSAGES: Record<LicenseStatus, string> = {
  active: 'License đang hoạt động.',
  active_offline: 'Đang dùng thời gian offline grace.',
  not_activated: 'Chưa có kích hoạt license trên máy này.',
  invalid_key: 'License key không hợp lệ.',
  expired: 'License đã hết hạn. Vui lòng gia hạn hoặc nhập key mới.',
  suspended: 'License đang bị tạm khoá.',
  revoked: 'License đã bị thu hồi.',
  device_revoked: 'Thiết bị này đã bị thu hồi quyền dùng license.',
  already_activated_on_another_device: 'License key này đã được kích hoạt trên máy khác.',
  machine_mismatch: 'License local không khớp với máy hiện tại. Vui lòng kích hoạt lại bằng license key hợp lệ.',
  verification_required: 'Cần kết nối internet để xác minh license.',
  network_error: 'Không thể kết nối máy chủ license.',
  server_error: 'Máy chủ license gặp lỗi. Vui lòng thử lại.',
};

export function Login({ status, onLicenseChanged }: Props) {
  const [licenseKey, setLicenseKey] = useState('');
  const [deviceName, setDeviceName] = useState('');
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState('');
  const [mode, setMode] = useState<'activate' | 'change'>('activate');

  const currentStatus = status?.status ?? 'not_activated';
  const message = status?.reason || STATUS_MESSAGES[currentStatus];
  const isExpired = currentStatus === 'expired';

  async function afterChange() {
    await onLicenseChanged?.();
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!licenseKey.trim()) {
      setError('Vui lòng nhập license key.');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const payload = {
        license_key: licenseKey.trim(),
        device_name: deviceName.trim() || undefined,
        app_version: import.meta.env.VITE_APP_VERSION ?? '0.1.0',
      };
      const result = mode === 'change'
        ? await api.changeLicenseKey(payload)
        : await api.activateLicense(payload);
      if (!result.licensed) {
        setError(result.reason || STATUS_MESSAGES[result.status]);
      } else {
        setLicenseKey('');
        await afterChange();
      }
    } catch (err: any) {
      setError(err.message ?? 'Kích hoạt license thất bại.');
    } finally {
      setLoading(false);
    }
  }

  async function handleRefresh() {
    setChecking(true);
    setError('');
    try {
      const result = await api.refreshLicense({ app_version: import.meta.env.VITE_APP_VERSION ?? '0.1.0' });
      if (!result.licensed) setError(result.reason || STATUS_MESSAGES[result.status]);
      await afterChange();
    } catch (err: any) {
      setError(err.message ?? 'Không thể kiểm tra lại license.');
    } finally {
      setChecking(false);
    }
  }

  async function handleDeactivateLocal() {
    setChecking(true);
    setError('');
    try {
      await api.deactivateLocalLicense();
      await afterChange();
    } catch (err: any) {
      setError(err.message ?? 'Không thể xoá kích hoạt local.');
    } finally {
      setChecking(false);
    }
  }

  return (
    <div style={{ minHeight: '100vh', display: 'grid', gridTemplateColumns: 'minmax(340px, 0.9fr) minmax(420px, 1.1fr)', background: 'var(--bg)' }}>
      <section style={{ padding: '3rem', background: 'linear-gradient(145deg, #2563eb 0%, #7c3aed 55%, #ec4899 100%)', color: '#fff', display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <div style={{ width: 42, height: 42, borderRadius: 12, background: 'rgba(255,255,255,0.18)', display: 'grid', placeItems: 'center', border: '1px solid rgba(255,255,255,0.25)' }}>
            <Shield size={21} />
          </div>
          <div>
            <div style={{ fontWeight: 800, fontSize: '1.1rem' }}>Automation Ecosystem</div>
            <div style={{ opacity: 0.72, fontSize: '0.78rem' }}>Local device license</div>
          </div>
        </div>
        <div>
          <h1 style={{ fontSize: '2.25rem', lineHeight: 1.1, margin: 0, letterSpacing: '-0.02em' }}>Kích hoạt một lần cho máy này.</h1>
          <p style={{ opacity: 0.78, marginTop: '1rem', maxWidth: 440, lineHeight: 1.65 }}>
            Chỉ cần nhập license key. Chrome, Edge, Brave và Firefox trên cùng máy sẽ dùng chung kích hoạt từ backend local.
          </p>
        </div>
        <div style={{ fontSize: '0.78rem', opacity: 0.72 }}>Không lưu license key thô trong trình duyệt.</div>
      </section>

      <main style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '2rem' }}>
        <div style={{ width: '100%', maxWidth: 460 }}>
          <div style={{ marginBottom: '1.5rem' }}>
            <div style={{ display: 'inline-flex', alignItems: 'center', gap: '0.45rem', padding: '0.35rem 0.65rem', borderRadius: 999, background: 'var(--surface)', border: '1px solid var(--border)', color: isExpired ? 'var(--warning)' : 'var(--text-muted)', fontSize: '0.75rem', fontWeight: 700 }}>
              {isExpired ? <AlertTriangle size={14} /> : <KeyRound size={14} />}
              {currentStatus}
            </div>
            <h2 style={{ margin: '1rem 0 0.4rem', fontSize: '1.6rem', color: 'var(--text-primary)' }}>
              {mode === 'change' ? 'Nhập license key mới' : 'Kích hoạt license'}
            </h2>
            <p style={{ color: 'var(--text-secondary)', lineHeight: 1.55, margin: 0 }}>{message}</p>
          </div>

          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            <div>
              <label style={{ display: 'block', fontSize: '0.82rem', fontWeight: 700, color: 'var(--text-secondary)', marginBottom: '0.45rem' }}>License key</label>
              <input
                className="input"
                value={licenseKey}
                onChange={(e) => setLicenseKey(e.target.value)}
                placeholder="AECO-XXXX-XXXX-XXXX-XXXX"
                autoComplete="off"
                autoFocus
              />
            </div>
            <div>
              <label style={{ display: 'block', fontSize: '0.82rem', fontWeight: 700, color: 'var(--text-secondary)', marginBottom: '0.45rem' }}>Tên thiết bị (tuỳ chọn)</label>
              <input
                className="input"
                value={deviceName}
                onChange={(e) => setDeviceName(e.target.value)}
                placeholder="Máy làm việc chính"
                autoComplete="off"
              />
            </div>

            {error && <div style={{ padding: '0.8rem 0.9rem', borderRadius: 10, border: '1px solid var(--danger-border, rgba(239,68,68,0.35))', color: 'var(--danger)', background: 'rgba(239,68,68,0.08)', fontSize: '0.85rem' }}>{error}</div>}

            <button className="btn btn-primary" type="submit" disabled={loading} style={{ width: '100%', justifyContent: 'center' }}>
              {loading ? 'Đang xử lý…' : mode === 'change' ? 'Đổi license key' : 'Kích hoạt'}
            </button>
          </form>

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.6rem', marginTop: '1rem' }}>
            <button className="btn btn-ghost btn-sm" onClick={handleRefresh} disabled={checking}>
              <RefreshCw size={14} /> {checking ? 'Đang kiểm tra…' : 'Kiểm tra lại'}
            </button>
            <button className="btn btn-ghost btn-sm" onClick={() => setMode(mode === 'change' ? 'activate' : 'change')}>
              {mode === 'change' ? 'Quay lại kích hoạt' : 'Nhập key mới'}
            </button>
            <button className="btn btn-ghost btn-sm" onClick={handleDeactivateLocal} disabled={checking}>
              Xoá kích hoạt local
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}
