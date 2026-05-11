// ── Settings > General — ALL toggles live here ───────────────────────────────
// Rule: EVERY behavioral toggle → this page. Nothing else anywhere.
import React from 'react';
import { PageHeader, ToggleRow, Divider } from '@/components/ui';
import { useUIStore } from '@/lib/store';
import { useI18n } from '@/lib/i18n';

export function SettingsGeneral() {
  const { executionEnabled, setExecutionEnabled, autoApprove, setAutoApprove, theme, setTheme, language, setLanguage } = useUIStore();
  const { t, lang, setLang } = useI18n();

  return (
    <div style={{ maxWidth: 600 }}>
      <PageHeader title="Cài Đặt Chung" subtitle="Tất cả tùy chọn hành vi hệ thống" />

      {/* ── System Controls ─────────────────────────────────────────────────── */}
      <div className="card" style={{ marginBottom: '1.25rem' }}>
        <div style={{ fontWeight: 600, fontSize: '0.8125rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '0.75rem' }}>
          Điều Khiển Hệ Thống
        </div>
        <ToggleRow
          label="Bật Máy Thực Thi"
          description="Khi TẮT: không có nội dung nào được đăng, mọi lịch trình dừng lại."
          checked={executionEnabled}
          onChange={setExecutionEnabled}
        />
        <ToggleRow
          label="Tự Động Duyệt Nội Dung"
          description="Tự động đăng nội dung đủ điểm mà không cần xét duyệt thủ công."
          checked={autoApprove}
          onChange={setAutoApprove}
        />
      </div>

      {/* ── Language ────────────────────────────────────────────────────────── */}
      <div className="card" style={{ marginBottom: '1.25rem' }}>
        <div style={{ fontWeight: 600, fontSize: '0.8125rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '0.875rem' }}>
          Ngôn Ngữ / Language
        </div>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          {(['vi', 'en'] as const).map(l => (
            <button
              key={l}
              id={`lang-${l}`}
              className={`btn btn-sm ${lang === l ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => { setLang(l); setLanguage(l); }}
            >
              {l === 'vi' ? '🇻🇳 Tiếng Việt' : '🇬🇧 English'}
            </button>
          ))}
        </div>
      </div>

      {/* ── Theme ───────────────────────────────────────────────────────────── */}
      <div className="card">
        <div style={{ fontWeight: 600, fontSize: '0.8125rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '0.875rem' }}>
          Giao Diện / Theme
        </div>
        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
          {([
            { key: 'dark',  label: '🌑 Dark Command',  desc: 'Tối chuẩn — cho môi trường kiểm soát' },
            { key: 'light', label: '☀️ Light SaaS',    desc: 'Sáng sạch — cho văn phòng' },
            { key: 'neon',  label: '⚡ Neon Tech',      desc: 'Neon — nổi bật, năng động' },
          ] as const).map(({ key, label, desc }) => (
            <button
              key={key}
              id={`theme-${key}`}
              onClick={() => setTheme(key)}
              style={{
                flex: 1, minWidth: 140, padding: '0.875rem', textAlign: 'left',
                background: theme === key ? 'var(--primary-muted)' : 'var(--surface-2)',
                border: `2px solid ${theme === key ? 'var(--primary)' : 'var(--border)'}`,
                borderRadius: 'var(--radius)', cursor: 'pointer', transition: 'border-color 0.2s',
              }}
            >
              <div style={{ fontWeight: 600, fontSize: '0.875rem', color: 'var(--text-primary)', marginBottom: '0.25rem' }}>{label}</div>
              <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{desc}</div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
