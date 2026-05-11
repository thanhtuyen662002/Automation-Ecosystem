// ── Login — Real Auth (POST /api/v1/auth/login) ───────────────────────────────
import React, { useState } from 'react';
import { useAuthStore } from '@/lib/store';
import { api } from '@/lib/api';

export function Login() {
  const { login } = useAuthStore();
  const [account, setAccount] = useState('');
  const [license, setLicense]  = useState('');
  const [error, setError]      = useState('');
  const [loading, setLoading]  = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const acc = account.trim().replace(/^@/, '');
    const key = license.trim();
    if (!acc || !key) { setError('Vui lòng nhập đầy đủ tài khoản và license key.'); return; }
    setLoading(true); setError('');
    try {
      const res = await api.login(acc, key);
      localStorage.setItem('auth_token', res.token);
      login(res.token, acc);
    } catch (err: any) {
      setError(err?.message?.includes('401') || err?.message?.includes('Invalid')
        ? 'License key không hợp lệ. Kiểm tra lại hoặc liên hệ quản trị viên.'
        : `Lỗi kết nối: ${err?.message ?? 'Không thể kết nối đến máy chủ.'}`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg)' }}>
      <div style={{ width: '100%', maxWidth: 400, padding: '2.5rem', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', boxShadow: '0 8px 40px rgba(0,0,0,0.35)' }}>
        <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
          <div style={{ width: 52, height: 52, borderRadius: 14, background: 'linear-gradient(135deg, var(--primary), #818cf8)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1.5rem', margin: '0 auto 1rem' }}>⚡</div>
          <h1 style={{ fontWeight: 800, fontSize: '1.25rem', margin: '0 0 0.25rem' }}>Automation Ecosystem</h1>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.8125rem', margin: 0 }}>Đăng nhập để vào hệ thống</p>
        </div>

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Tên Tài Khoản</label>
            <input id="login-account" className="input" style={{ width: '100%', boxSizing: 'border-box' }}
              type="text" placeholder="@your_account" value={account}
              onChange={e => setAccount(e.target.value)} autoComplete="username" autoFocus />
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '0.375rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>License Key</label>
            <input id="login-license" className="input" style={{ width: '100%', boxSizing: 'border-box' }}
              type="password" placeholder="AE-XXXX-XXXX-XXXX" value={license}
              onChange={e => setLicense(e.target.value)} autoComplete="current-password" />
          </div>

          {error && (
            <div style={{ padding: '0.625rem 0.875rem', background: 'var(--danger-muted)', border: '1px solid var(--danger)', borderRadius: 'var(--radius-sm)', fontSize: '0.8125rem', color: 'var(--danger)' }}>
              {error}
            </div>
          )}

          <button id="login-submit" className="btn btn-primary" type="submit" disabled={loading}
            style={{ marginTop: '0.25rem', height: 44, fontSize: '0.9375rem', fontWeight: 600 }}>
            {loading ? 'Đang đăng nhập...' : '⚡ Đăng Nhập'}
          </button>
        </form>

        <div style={{ marginTop: '1.5rem', fontSize: '0.7rem', color: 'var(--text-muted)', textAlign: 'center' }}>
          Cần license key? Liên hệ quản trị viên hệ thống.
        </div>
      </div>
    </div>
  );
}
