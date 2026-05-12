// ── Glassmorp Sidebar — Asset icons + frosted glass card ──────────────────────
import React from 'react';
import ReactDOM from 'react-dom';
import { NavLink } from 'react-router-dom';
import { useUIStore, useWSStore, useAuthStore } from '@/lib/store';
import { useI18n } from '@/lib/i18n';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { GlassIcon } from '@/components/Icons';

// ── Nav structure — uses asset icon names ─────────────────────────────────────
interface NavItem { to: string; iconAsset: string; label: string; badge?: number; }
interface NavSection { title: string; items: NavItem[]; }

// ── Custom Tooltip — portal-based to escape overflow:hidden clipping ──────────
function NavTooltip({ label, active, children }: { label: string; active: boolean; children: React.ReactNode }) {
  const ref = React.useRef<HTMLDivElement>(null);
  const [pos, setPos] = React.useState<{ top: number; left: number } | null>(null);

  if (!active) return <>{children}</>;

  const handleEnter = () => {
    if (ref.current) {
      const r = ref.current.getBoundingClientRect();
      setPos({ top: r.top + r.height / 2, left: r.right + 10 });
    }
  };

  const tooltip = pos ? ReactDOM.createPortal(
    <div style={{
      position: 'fixed',
      top: pos.top,
      left: pos.left,
      transform: 'translateY(-50%)',
      background: 'rgba(255,255,255,0.95)',
      backdropFilter: 'blur(16px)',
      WebkitBackdropFilter: 'blur(16px)',
      border: '1px solid rgba(124,58,237,0.20)',
      borderRadius: '0.5rem',
      padding: '0.28rem 0.7rem',
      fontSize: '0.78rem',
      fontWeight: 600,
      color: 'var(--text-primary)',
      whiteSpace: 'nowrap',
      boxShadow: '0 4px 18px rgba(124,58,237,0.18)',
      pointerEvents: 'none',
      zIndex: 9999,
      animation: 'tooltipFadeIn 0.12s ease forwards',
    }}>
      {/* Arrow */}
      <span style={{
        position: 'absolute',
        left: -5, top: '50%', transform: 'translateY(-50%)',
        width: 0, height: 0,
        borderTop: '5px solid transparent',
        borderBottom: '5px solid transparent',
        borderRight: '5px solid rgba(255,255,255,0.95)',
      }} />
      {label}
    </div>,
    document.body
  ) : null;

  return (
    <div ref={ref} style={{ position: 'relative', display: 'block' }} onMouseEnter={handleEnter} onMouseLeave={() => setPos(null)}>
      {children}
      {tooltip}
    </div>
  );
}


// ── Glassmorp Polygon Logo ─────────────────────────────────────────────────────
function GlassmorphLogo() {
  return (
    <svg width="28" height="28" viewBox="0 0 32 32" fill="none">
      <defs>
        <linearGradient id="lgLogo" x1="0" y1="0" x2="32" y2="32" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#7c3aed" /><stop offset="100%" stopColor="#a855f7" />
        </linearGradient>
      </defs>
      <polygon points="16,2 30,9 30,23 16,30 2,23 2,9" fill="url(#lgLogo)" opacity="0.95" />
      <polygon points="16,7 26,12 26,22 16,27 6,22 6,12" fill="none" stroke="rgba(255,255,255,0.35)" strokeWidth="1" />
      <circle cx="16" cy="16" r="4" fill="rgba(255,255,255,0.92)" />
    </svg>
  );
}

export function Sidebar({ pendingCount = 0 }: { pendingCount?: number }) {
  const { sidebarCollapsed, toggleSidebar } = useUIStore();
  const { connected } = useWSStore();
  const { user } = useAuthStore();
  const { t } = useI18n();
  const isAdmin = user?.account?.toLowerCase() === 'admin';

  const sections: NavSection[] = [
    {
      title: t('section.dashboard'),
      items: [
        { to: '/dashboard/executive', iconAsset: 'chart', label: t('nav.executive') },
        { to: '/dashboard/command', iconAsset: 'rocket', label: t('nav.command') },
      ],
    },
    {
      title: t('section.operations'),
      items: [
        { to: '/operations/queue', iconAsset: 'clipboard', label: t('nav.queue'), badge: pendingCount || undefined },
        { to: '/operations/jobs', iconAsset: 'arrows-square-up-down', label: t('nav.jobs') },
        { to: '/operations/artifacts', iconAsset: 'video', label: t('nav.artifacts') },
      ],
    },
    {
      title: t('section.fleet'),
      items: [
        { to: '/fleet/health', iconAsset: 'heart', label: t('nav.fleet') },
        { to: '/fleet/accounts', iconAsset: 'user', label: t('nav.accounts') },
        { to: '/fleet/identities', iconAsset: 'key', label: t('nav.identities') },
      ],
    },
    {
      title: t('section.strategy'),
      items: [
        { to: '/strategy/ceo', iconAsset: 'planet', label: t('nav.brain') },
        { to: '/strategy/niches', iconAsset: 'compass', label: t('nav.niches') },
        { to: '/strategy/overrides', iconAsset: 'warning', label: t('nav.overrides') },
      ],
    },
    {
      title: t('section.settings'),
      items: [
        { to: '/settings/general', iconAsset: 'setting', label: t('nav.settings_gen') },
        { to: '/settings/advanced', iconAsset: 'puzzle', label: t('nav.settings_adv') },
        { to: '/settings/policy', iconAsset: 'shield', label: t('nav.settings_pol') },
      ],
    },
    ...(isAdmin ? [{
      title: t('section.admin'),
      items: [{ to: '/admin/licenses', iconAsset: 'badge', label: t('nav.licenses') }],
    }] : []),
  ];

  const W = sidebarCollapsed ? 62 : 210;

  // opacity + instant width collapse: label takes no space when collapsed so icon centers
  const fadeStyle: React.CSSProperties = {
    opacity: sidebarCollapsed ? 0 : 1,
    width: sidebarCollapsed ? 0 : undefined,   // instant — no layout transition cost
    overflow: 'hidden',
    whiteSpace: 'nowrap',
    flexShrink: 0,
    transition: 'opacity 0.2s ease',           // only opacity animates (GPU)
    pointerEvents: sidebarCollapsed ? 'none' : 'auto',
  };

  return (
    <aside style={{
      width: W, minWidth: W, height: '100%',
      display: 'flex', flexDirection: 'column',
      overflow: 'hidden',
      flexShrink: 0,
      background: 'var(--surface-glass)',
      backdropFilter: 'blur(20px)', WebkitBackdropFilter: 'blur(20px)',
      border: '1px solid var(--surface-glass-border)',
      borderRadius: '1.25rem',
      boxShadow: '0 4px 24px var(--surface-glass-shadow)',
      willChange: 'width',
      transition: [
        'width 0.3s cubic-bezier(0.4,0,0.2,1)',
        'min-width 0.3s cubic-bezier(0.4,0,0.2,1)',
        'background 0.25s ease',
        'border-color 0.25s ease',
        'box-shadow 0.25s ease',
      ].join(', '),
    }}>

      {/* ── Logo ─────────────────────────────────────────────────────────── */}
      <div style={{
        padding: '0.875rem 1rem',
        borderBottom: '1px solid var(--border)',
        display: 'flex', alignItems: 'center', gap: '0.625rem',
        minHeight: 56, flexShrink: 0,
      }}>
        <div style={{ flexShrink: 0, filter: 'drop-shadow(0 3px 8px rgba(124,58,237,0.38))' }}>
          <GlassmorphLogo />
        </div>
        {/* Opacity-only fade — no max-width layout cost */}
        <div style={fadeStyle}>
          <div style={{ fontWeight: 800, fontSize: '0.9375rem', color: 'var(--primary)', lineHeight: 1.1, letterSpacing: '-0.01em' }}>
            AutoEcosystem
          </div>
          <div style={{ color: 'var(--text-muted)', fontSize: '0.6rem', marginTop: '0.1rem', letterSpacing: '0.04em', textTransform: 'uppercase' }}>
            Command Center
          </div>
        </div>
      </div>

      {/* ── Navigation ───────────────────────────────────────────────────── */}
      <nav style={{
        flex: 1, overflowY: 'auto', overflowX: 'hidden',
        padding: sidebarCollapsed ? '0.5rem 0.375rem' : '0.375rem 0.625rem',
        // no padding transition — instant switch is fine since overflow:hidden clips it
        display: 'flex', flexDirection: 'column', gap: 0,
      }}>
        {sections.map((section) => (
          <React.Fragment key={section.title}>
            {/* Section label — opacity only, height stays reserved but clips via aside overflow */}
            <div style={{
              ...fadeStyle,
              fontSize: '0.6rem', fontWeight: 700, textTransform: 'uppercase',
              letterSpacing: '0.09em', color: 'var(--text-muted)',
              padding: '0.7rem 0.5rem 0.2rem',
              height: sidebarCollapsed ? 0 : undefined,
              paddingTop: sidebarCollapsed ? 0 : undefined,
              paddingBottom: sidebarCollapsed ? 0 : undefined,
            }}>
              {section.title}
            </div>

            {section.items.map((item) => (
              <NavLink key={item.to} to={item.to} style={{ textDecoration: 'none' }}>
                {({ isActive }) => (
                  <NavTooltip label={item.label} active={sidebarCollapsed}>
                    <div
                      style={{
                        display: 'flex', alignItems: 'center',
                        gap: sidebarCollapsed ? 0 : '0.5rem',  // gap:0 when collapsed — avoids offset from invisible label
                        padding: sidebarCollapsed ? '0.45rem' : '0.38rem 0.55rem',
                        justifyContent: sidebarCollapsed ? 'center' : 'flex-start',
                        borderRadius: '0.625rem', margin: '0.05rem 0',
                        cursor: 'pointer',
                        // only non-layout properties transition
                        transition: 'background 0.18s ease, color 0.18s ease, box-shadow 0.18s ease',
                        background: isActive ? 'var(--surface-glass-active)' : 'transparent',
                        color: isActive ? 'var(--primary)' : 'var(--text-secondary)',
                        boxShadow: isActive ? `0 2px 10px var(--surface-glass-active-shadow)` : 'none',
                        fontWeight: isActive ? 600 : 400,
                        fontSize: '0.8rem',
                        border: isActive ? '1px solid var(--surface-glass-border)' : '1px solid transparent',
                      }}
                      onMouseEnter={e => {
                        if (!isActive) {
                          (e.currentTarget as HTMLElement).style.background = 'var(--surface-glass-hover)';
                          (e.currentTarget as HTMLElement).style.color = 'var(--primary)';
                        }
                      }}
                      onMouseLeave={e => {
                        if (!isActive) {
                          (e.currentTarget as HTMLElement).style.background = 'transparent';
                          (e.currentTarget as HTMLElement).style.color = 'var(--text-secondary)';
                        }
                      }}
                    >
                      {/* Icon — always visible, GPU filter transition */}
                      <GlassIcon
                        name={item.iconAsset as any}
                        size={18}
                        style={{
                          flexShrink: 0,
                          filter: isActive
                            ? 'brightness(0) saturate(100%) invert(22%) sepia(88%) saturate(2000%) hue-rotate(257deg) brightness(90%)'
                            : 'none',
                          opacity: isActive ? 1 : 0.7,
                          transition: 'filter 0.18s ease, opacity 0.18s ease',
                        }}
                      />

                      {/* Label + badge — opacity fade only */}
                      <div style={{ ...fadeStyle, display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {item.label}
                        </span>
                        {item.badge != null && item.badge > 0 && (
                          <span style={{
                            background: isActive ? 'var(--primary)' : '#ef4444',
                            color: '#fff', borderRadius: '9999px',
                            fontSize: '0.58rem', fontWeight: 700,
                            padding: '0.1rem 0.42rem', minWidth: 17,
                            textAlign: 'center', flexShrink: 0, lineHeight: 1.4,
                          }}>{item.badge}</span>
                        )}
                      </div>
                    </div>
                  </NavTooltip>
                )}
              </NavLink>
            ))}
          </React.Fragment>
        ))}
      </nav>

      {/* ── Footer ───────────────────────────────────────────────────────── */}
      <div style={{
        padding: '0.625rem 0.75rem',        borderTop: '1px solid var(--border)',
        display: 'flex', alignItems: 'center', gap: '0.5rem',
        justifyContent: 'space-between', flexShrink: 0,
      }}>
        {/* Connection status — opacity fade */}
        <div style={{ ...fadeStyle, display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          <span style={{
            width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
            background: connected ? 'var(--success)' : 'var(--danger)',
            boxShadow: connected ? '0 0 6px var(--success)' : 'none',
            display: 'inline-block',
          }} />
          <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
            {connected ? 'Live' : 'Offline'}
          </span>
        </div>
      </div>
    </aside>
  );
}

