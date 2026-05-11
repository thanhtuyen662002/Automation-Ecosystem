// ── Settings > General ─────────────────────────────────────────────────────────
import React from 'react';
import { PageHeader, ToggleRow, Divider } from '@/components/ui';
import { GlassIcon } from '@/components/Icons';
import { useUIStore } from '@/lib/store';
import { useI18n } from '@/lib/i18n';

// Section card with GlassIcon header
function SettingsSection({ icon, title, children }: { icon: string; title: string; children: React.ReactNode }) {
  return (
    <div className="card" style={{ marginBottom: '1.25rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', marginBottom: '0.875rem' }}>
        <GlassIcon name={icon as any} size={22} style={{ opacity: 0.85 }} />
        <div style={{ fontWeight: 600, fontSize: '0.8125rem', color: 'var(--text-primary)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          {title}
        </div>
      </div>
      {children}
    </div>
  );
}

export function SettingsGeneral() {
  const { executionEnabled, setExecutionEnabled, autoApprove, setAutoApprove, theme, setTheme } = useUIStore();
  const { t, lang, setLang, setLanguage } = useI18n() as any;

  return (
    <div>
      <PageHeader title="Cài Đặt Chung" subtitle="Tất cả tùy chọn hành vi hệ thống" />

      {/* System Controls */}
      <SettingsSection icon="arrows-square-up-down" title="Điều Khiển Hệ Thống">
        <ToggleRow
          label="Bật Máy Thực Thi"
          description="Khi TẮT: không có nội dung nào được đăng, mọi lịch trình dừng lại."
          checked={executionEnabled}
          onChange={setExecutionEnabled}
        />
        <Divider />
        <ToggleRow
          label="Tự Động Duyệt Nội Dung"
          description="Tự động đăng nội dung đủ điểm mà không cần xét duyệt thủ công."
          checked={autoApprove}
          onChange={setAutoApprove}
        />
      </SettingsSection>

      {/* Language */}
      <SettingsSection icon="planet" title="Ngôn Ngữ / Language">
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          {(['vi', 'en'] as const).map(l => (
            <button key={l} id={`lang-${l}`}
              className={`btn btn-sm ${lang === l ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => { setLang(l); if (setLanguage) setLanguage(l); }}
            >
              {l === 'vi' ? '🇻🇳 Tiếng Việt' : '🇬🇧 English'}
            </button>
          ))}
        </div>
      </SettingsSection>

      {/* Theme */}
      <div className="card">
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', marginBottom: '0.875rem' }}>
          <GlassIcon name="paint-brush" size={22} style={{ opacity: 0.85 }} />
          <div style={{ fontWeight: 600, fontSize: '0.8125rem', color: 'var(--text-primary)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            Giao Diện / Theme
          </div>
        </div>
        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
          {([
            { key: 'dark',  label: 'Dark Command',  desc: 'Tối chuẩn — cho môi trường kiểm soát', icon: 'cloud' },
            { key: 'light', label: 'Light SaaS',    desc: 'Sáng sạch — cho văn phòng',            icon: 'cloud-sun' },
            { key: 'neon',  label: 'Neon Tech',     desc: 'Neon — nổi bật, năng động',            icon: 'rocket' },
          ] as const).map(({ key, label, desc, icon }) => (
            <button key={key} id={`theme-${key}`} onClick={() => setTheme(key)}
              style={{
                flex: 1, minWidth: 140, padding: '0.875rem', textAlign: 'left',
                background: theme === key ? 'var(--primary-muted)' : 'var(--surface-2)',
                border: `2px solid ${theme === key ? 'var(--primary)' : 'var(--border)'}`,
                borderRadius: 'var(--radius)', cursor: 'pointer', transition: 'border-color 0.2s',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.375rem' }}>
                <GlassIcon name={icon} size={18} style={{ opacity: 0.8 }} />
                <div style={{ fontWeight: 600, fontSize: '0.875rem', color: 'var(--text-primary)' }}>{label}</div>
              </div>
              <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{desc}</div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
