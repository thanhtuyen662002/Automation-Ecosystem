// ── Login Page — Glassmorp Split Layout ──────────────────────────────────────
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/lib/store';
import { tokenStore } from '@/lib/api';
import { Shield, LogIn } from 'lucide-react';

const API = import.meta.env.VITE_API_URL ?? import.meta.env.VITE_API_BASE ?? 'http://localhost:8000';

const TESTIMONIALS = [
  {
    name: 'Nguyễn Văn An',
    role: 'Content Strategist',
    quote: 'Hệ thống tự động hóa nội dung giúp chúng tôi tăng hiệu suất gấp 5x mà không cần thêm nhân lực.',
    avatar: 'NA',
  },
  {
    name: 'Trần Minh Khoa',
    role: 'Growth Manager',
    quote: 'Dashboard trực quan, dữ liệu real-time — tôi luôn nắm rõ tình hình fleet mọi lúc mọi nơi.',
    avatar: 'TK',
  },
  {
    name: 'Lê Thu Hằng',
    role: 'Operations Lead',
    quote: 'Tính năng CEO Brain tự động đề xuất chiến lược — tiết kiệm hàng giờ phân tích mỗi ngày.',
    avatar: 'LH',
  },
];

export function Login() {
  const [account,    setAccount]    = useState('');
  const [licenseKey, setLicenseKey] = useState('');
  const [loading,    setLoading]    = useState(false);
  const [error,      setError]      = useState('');
  const [activeTest, setActiveTest] = useState(0);
  const { login } = useAuthStore();
  const navigate  = useNavigate();

  // Rotate testimonials
  React.useEffect(() => {
    const t = setInterval(() => setActiveTest(p => (p + 1) % TESTIMONIALS.length), 4000);
    return () => clearInterval(t);
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!account.trim() || !licenseKey.trim()) {
      setError('Vui lòng nhập đầy đủ thông tin.');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${API}/api/v1/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          account:     account.replace(/^@/, '').trim(),
          license_key: licenseKey.trim(),
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data?.detail ?? `Lỗi ${res.status}`);
      }
      const data = await res.json();
      // Backend returns { token, user } — NOT access_token
      const token = data.token ?? data.access_token;
      if (!token) throw new Error('Server không trả về token. Kiểm tra lại thông tin đăng nhập.');
      // Synchronously write to sessionStorage BEFORE navigate so React Query
      // requests fired on mount already have the Authorization header.
      tokenStore.set(token);
      login(token, data.user);
      navigate('/dashboard/command', { replace: true });
    } catch (err: any) {
      setError(err.message ?? 'Đăng nhập thất bại.');
    } finally {
      setLoading(false);
    }
  }

  const t = TESTIMONIALS[activeTest];

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      background: 'var(--bg)',
    }}>
      {/* ── Left panel: Branding + Testimonial ──────────────────────────────── */}
      <div style={{
        width: '45%',
        minHeight: '100vh',
        background: 'linear-gradient(145deg, #7c3aed 0%, #a855f7 45%, #ec4899 100%)',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'space-between',
        padding: '2.5rem',
        position: 'relative',
        overflow: 'hidden',
        flexShrink: 0,
      }}>
        {/* BG blobs */}
        <div style={{ position: 'absolute', width: 350, height: 350, top: -80, right: -80, borderRadius: '50%', background: 'rgba(255,255,255,0.08)', pointerEvents: 'none' }} />
        <div style={{ position: 'absolute', width: 250, height: 250, bottom: 60, left: -60, borderRadius: '50%', background: 'rgba(255,255,255,0.06)', pointerEvents: 'none' }} />

        {/* Logo */}
        <div style={{ position: 'relative', zIndex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <div style={{
              width: 40, height: 40,
              background: 'rgba(255,255,255,0.20)',
              borderRadius: '0.75rem',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              backdropFilter: 'blur(12px)',
              border: '1px solid rgba(255,255,255,0.25)',
            }}>
              <Shield size={20} color="#fff" />
            </div>
            <div>
              <div style={{ fontWeight: 800, fontSize: '1.125rem', color: '#fff' }}>AutoEcosystem</div>
              <div style={{ fontSize: '0.72rem', color: 'rgba(255,255,255,0.70)' }}>Automation Dashboard</div>
            </div>
          </div>
        </div>

        {/* Testimonial card */}
        <div style={{ position: 'relative', zIndex: 1 }}>
          <div style={{
            background: 'rgba(255,255,255,0.14)',
            border: '1px solid rgba(255,255,255,0.22)',
            borderRadius: '1.25rem',
            padding: '1.75rem',
            backdropFilter: 'blur(20px)',
            transition: 'all 0.5s ease',
          }}>
            {/* Stars */}
            <div style={{ marginBottom: '1rem', color: '#fde68a', letterSpacing: '0.15em', fontSize: '1rem' }}>★★★★★</div>
            <p style={{ color: '#fff', fontSize: '0.9375rem', lineHeight: 1.65, fontStyle: 'italic', marginBottom: '1.25rem' }}>
              "{t.quote}"
            </p>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <div style={{
                width: 40, height: 40, borderRadius: '50%',
                background: 'rgba(255,255,255,0.25)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontWeight: 700, fontSize: '0.8rem', color: '#fff',
              }}>
                {t.avatar}
              </div>
              <div>
                <div style={{ fontWeight: 600, fontSize: '0.875rem', color: '#fff' }}>{t.name}</div>
                <div style={{ fontSize: '0.75rem', color: 'rgba(255,255,255,0.65)' }}>{t.role}</div>
              </div>
            </div>
          </div>
          {/* Dots */}
          <div style={{ display: 'flex', gap: '0.4rem', marginTop: '1rem', justifyContent: 'center' }}>
            {TESTIMONIALS.map((_, i) => (
              <button
                key={i}
                onClick={() => setActiveTest(i)}
                style={{
                  width: i === activeTest ? 20 : 7, height: 7, borderRadius: '9999px',
                  background: i === activeTest ? '#fff' : 'rgba(255,255,255,0.35)',
                  border: 'none', cursor: 'pointer', transition: 'all 0.3s ease', padding: 0,
                }}
              />
            ))}
          </div>
        </div>
      </div>

      {/* ── Right panel: Form ────────────────────────────────────────────────── */}
      <div style={{
        flex: 1,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '2rem',
        background: 'var(--bg)',
      }}>
        <div style={{ width: '100%', maxWidth: 420 }}>
          <div style={{ marginBottom: '2rem' }}>
            <h1 style={{ fontSize: '1.625rem', fontWeight: 800, color: 'var(--text-primary)', marginBottom: '0.5rem', letterSpacing: '-0.02em' }}>
              Đăng nhập
            </h1>
            <p style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
              Quản lý hệ thống tự động hóa của bạn.
            </p>
          </div>

          <form onSubmit={handleSubmit}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              {/* Account */}
              <div>
                <label style={{ fontSize: '0.8125rem', fontWeight: 600, color: 'var(--text-secondary)', display: 'block', marginBottom: '0.4rem' }}>
                  Tên tài khoản
                </label>
                <input
                  id="login-account"
                  type="text"
                  className="input"
                  placeholder="@your_account"
                  value={account}
                  onChange={e => setAccount(e.target.value)}
                  autoComplete="username"
                  autoFocus
                />
              </div>

              {/* License key */}
              <div>
                <label style={{ fontSize: '0.8125rem', fontWeight: 600, color: 'var(--text-secondary)', display: 'block', marginBottom: '0.4rem' }}>
                  License Key
                </label>
                <input
                  id="login-license"
                  type="password"
                  className="input"
                  placeholder="AE-XXXX-XXXX-XXXX"
                  value={licenseKey}
                  onChange={e => setLicenseKey(e.target.value)}
                  autoComplete="current-password"
                />
              </div>

              {/* Error */}
              {error && (
                <div style={{
                  padding: '0.75rem 1rem',
                  background: 'var(--danger-muted)',
                  border: '1px solid rgba(239,68,68,0.25)',
                  borderRadius: 'var(--radius-sm)',
                  color: 'var(--danger)',
                  fontSize: '0.8125rem',
                }}>
                  {error}
                </div>
              )}

              {/* Submit */}
              <button
                id="login-submit"
                type="submit"
                className="btn btn-primary btn-lg"
                disabled={loading}
                style={{ width: '100%', justifyContent: 'center', marginTop: '0.25rem' }}
              >
                {loading ? (
                  <span style={{ display: 'inline-block', width: 16, height: 16, border: '2px solid rgba(255,255,255,0.3)', borderTopColor: '#fff', borderRadius: '50%', animation: 'spin 0.7s linear infinite' }} />
                ) : (
                  <><LogIn size={16} /> Đăng Nhập</>
                )}
              </button>
            </div>
          </form>

          <p style={{ marginTop: '1.5rem', fontSize: '0.8rem', color: 'var(--text-muted)', textAlign: 'center' }}>
            Cần license key?{' '}
            <span style={{ color: 'var(--primary)', fontWeight: 600 }}>Liên hệ quản trị viên hệ thống.</span>
          </p>
        </div>
      </div>

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
