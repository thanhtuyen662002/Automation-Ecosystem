// ── Shared platform config — used across all pages ───────────────────────────
export interface PlatformCfg {
  label: string;
  bg: string;
  color: string;
  svg: string; // path relative to /public
}

export const PLATFORMS: Record<string, PlatformCfg> = {
  tiktok:    { label: 'TikTok',    bg: '#010101', color: '#fff', svg: '/icons/tiktok.svg' },
  facebook:  { label: 'Facebook',  bg: '#1877F2', color: '#fff', svg: '/icons/facebook.svg' },
  youtube:   { label: 'YouTube',   bg: '#FF0000', color: '#fff', svg: '/icons/youtube.svg' },
  zalo:      { label: 'Zalo',      bg: '#0068FF', color: '#fff', svg: '/icons/zalo.svg' },
  shopee:    { label: 'Shopee',    bg: '#EE4D2D', color: '#fff', svg: '/icons/shopee.svg' },
  instagram: { label: 'Instagram', bg: '#E1306C', color: '#fff', svg: '/icons/instagram.svg' },
};

// ── PlatformBadge: icon + label pill ─────────────────────────────────────────
import React from 'react';

interface PlatformBadgeProps {
  platform: string;
  /** icon-only mode — hides text label */
  iconOnly?: boolean;
  size?: number;
}

export function PlatformBadge({ platform, iconOnly = false, size = 14 }: PlatformBadgeProps) {
  const cfg = PLATFORMS[platform.toLowerCase()] ?? {
    label: platform,
    bg: 'var(--surface-2)',
    color: 'var(--text-primary)',
    svg: '',
  };

  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: '0.3rem',
      padding: iconOnly ? `${size * 0.2}px` : '0.1rem 0.5rem',
      borderRadius: iconOnly ? '50%' : '9999px',
      fontSize: '0.65rem', fontWeight: 700,
      background: cfg.bg, color: cfg.color,
      lineHeight: 1, whiteSpace: 'nowrap',
      flexShrink: 0,
    }}>
      {cfg.svg && (
        <img src={cfg.svg} alt={cfg.label} width={size} height={size}
          style={{ borderRadius: size * 0.2, display: 'block', objectFit: 'cover', flexShrink: 0 }} />
      )}
      {!iconOnly && cfg.label}
    </span>
  );
}

/** Dropdown <select> with all platforms as options */
export function PlatformSelect({
  value, onChange, className = 'select',
}: { value: string; onChange: (v: string) => void; className?: string }) {
  return (
    <select className={className} value={value} onChange={e => onChange(e.target.value)}>
      {Object.entries(PLATFORMS).map(([k, v]) => (
        <option key={k} value={k}>{v.label}</option>
      ))}
    </select>
  );
}
