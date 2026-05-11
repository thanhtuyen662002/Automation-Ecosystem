// ── Glassmorp Sidebar — Asset icons + frosted glass card ──────────────────────
import React from 'react';
import { NavLink } from 'react-router-dom';
import { useUIStore, useWSStore, useAuthStore } from '@/lib/store';
import { useI18n } from '@/lib/i18n';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { GlassIcon } from '@/components/Icons';

// ── Nav structure — uses asset icon names ─────────────────────────────────────
interface NavItem { to: string; iconAsset: string; label: string; badge?: number; }
interface NavSection { title: string; items: NavItem[]; }

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
        { to: '/dashboard/executive', iconAsset: 'chart',     label: t('nav.executive') },
        { to: '/dashboard/command',   iconAsset: 'rocket',    label: t('nav.command') },
      ],
    },
    {
      title: t('section.operations'),
      items: [
        { to: '/operations/queue',     iconAsset: 'clipboard', label: t('nav.queue'), badge: pendingCount || undefined },
        { to: '/operations/jobs',      iconAsset: 'arrows-square-up-down', label: t('nav.jobs') },
        { to: '/operations/artifacts', iconAsset: 'video',     label: t('nav.artifacts') },
      ],
    },
    {
      title: t('section.fleet'),
      items: [
        { to: '/fleet/health',     iconAsset: 'heart',   label: t('nav.fleet') },
        { to: '/fleet/accounts',   iconAsset: 'user',    label: t('nav.accounts') },
        { to: '/fleet/identities', iconAsset: 'key',     label: t('nav.identities') },
      ],
    },
    {
      title: t('section.strategy'),
      items: [
        { to: '/strategy/ceo',       iconAsset: 'planet',  label: t('nav.brain') },
        { to: '/strategy/niches',    iconAsset: 'compass', label: t('nav.niches') },
        { to: '/strategy/overrides', iconAsset: 'warning', label: t('nav.overrides') },
      ],
    },
    {
      title: t('section.settings'),
      items: [
        { to: '/settings/general',  iconAsset: 'setting', label: t('nav.settings_gen') },
        { to: '/settings/advanced', iconAsset: 'puzzle',  label: t('nav.settings_adv') },
        { to: '/settings/policy',   iconAsset: 'shield',  label: t('nav.settings_pol') },
      ],
    },
    ...(isAdmin ? [{
      title: t('section.admin'),
      items: [{ to: '/admin/licenses', iconAsset: 'badge', label: t('nav.licenses') }],
    }] : []),
  ];

  const W = sidebarCollapsed ? 62 : 210;

  return (
    <aside style={{
      width: W, minWidth: W, height: '100%',
      display: 'flex', flexDirection: 'column',
      overflow: 'hidden', flexShrink: 0,
      background: 'rgba(255,255,255,0.58)',
      backdropFilter: 'blur(20px)', WebkitBackdropFilter: 'blur(20px)',
      border: '1px solid rgba(255,255,255,0.65)',
      borderRadius: '1.25rem',
      boxShadow: '0 4px 24px rgba(124,58,237,0.07)',
      transition: 'width 0.25s ease, min-width 0.25s ease',
    }}>

      {/* ── Logo ─────────────────────────────────────────────────────────── */}
      <div style={{
        padding: sidebarCollapsed ? '0.875rem 0' : '0.875rem 1rem',
        borderBottom: '1px solid rgba(237,233,248,0.50)',
        display: 'flex', alignItems: 'center', gap: '0.625rem',
        minHeight: 56, justifyContent: sidebarCollapsed ? 'center' : 'flex-start', flexShrink: 0,
      }}>
        <div style={{ flexShrink: 0, filter: 'drop-shadow(0 3px 8px rgba(124,58,237,0.38))' }}>
          <GlassmorphLogo />
        </div>
        {!sidebarCollapsed && (
          <div>
            <div style={{ fontWeight: 800, fontSize: '0.9375rem', color: 'var(--primary)', lineHeight: 1.1, letterSpacing: '-0.01em' }}>
              AutoEcosystem
            </div>
            <div style={{ color: 'var(--text-muted)', fontSize: '0.6rem', marginTop: '0.1rem', letterSpacing: '0.04em', textTransform: 'uppercase' }}>
              Command Center
            </div>
          </div>
        )}
      </div>

      {/* ── Navigation ───────────────────────────────────────────────────── */}
      <nav style={{
        flex: 1, overflowY: 'auto',
        padding: sidebarCollapsed ? '0.5rem 0.5rem' : '0.375rem 0.625rem',
        display: 'flex', flexDirection: 'column', gap: 0,
      }}>
        {sections.map((section) => (
          <React.Fragment key={section.title}>
            {!sidebarCollapsed && (
              <div style={{
                fontSize: '0.6rem', fontWeight: 700, textTransform: 'uppercase',
                letterSpacing: '0.09em', color: 'var(--text-muted)',
                padding: '0.7rem 0.5rem 0.2rem', opacity: 0.6,
              }}>
                {section.title}
              </div>
            )}
            {sidebarCollapsed && <div style={{ height: '0.4rem' }} />}

            {section.items.map((item) => (
              <NavLink key={item.to} to={item.to} title={sidebarCollapsed ? item.label : undefined} style={{ textDecoration: 'none' }}>
                {({ isActive }) => (
                  <div
                    style={{
                      display: 'flex', alignItems: 'center', gap: '0.5rem',
                      padding: sidebarCollapsed ? '0.45rem' : '0.38rem 0.55rem',
                      borderRadius: '0.625rem', margin: '0.05rem 0',
                      cursor: 'pointer',
                      justifyContent: sidebarCollapsed ? 'center' : 'flex-start',
                      transition: 'all 0.18s ease',
                      background: isActive ? 'rgba(255,255,255,0.92)' : 'transparent',
                      color: isActive ? 'var(--primary)' : 'var(--text-secondary)',
                      boxShadow: isActive ? '0 2px 10px rgba(124,58,237,0.12)' : 'none',
                      fontWeight: isActive ? 600 : 400,
                      fontSize: '0.8rem',
                      border: isActive ? '1px solid rgba(255,255,255,0.75)' : '1px solid transparent',
                    }}
                    onMouseEnter={e => {
                      if (!isActive) {
                        (e.currentTarget as HTMLElement).style.background = 'rgba(124,58,237,0.07)';
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
                    {/* Asset icon — 18px for sidebar nav items */}
                    <GlassIcon
                      name={item.iconAsset as any}
                      size={18}
                      style={{
                        flexShrink: 0,
                        // Active state: highlight the icon with a violet tint
                        filter: isActive
                          ? 'brightness(0) saturate(100%) invert(22%) sepia(88%) saturate(2000%) hue-rotate(257deg) brightness(90%)'
                          : 'none',
                        opacity: isActive ? 1 : 0.7,
                        transition: 'all 0.18s ease',
                      }}
                    />

                    {!sidebarCollapsed && (
                      <>
                        <span style={{ flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
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
                      </>
                    )}
                  </div>
                )}
              </NavLink>
            ))}
          </React.Fragment>
        ))}
      </nav>

      {/* ── Footer ───────────────────────────────────────────────────────── */}
      <div style={{
        padding: '0.625rem 0.75rem', borderTop: '1px solid rgba(237,233,248,0.50)',
        display: 'flex', alignItems: 'center', gap: '0.5rem',
        justifyContent: sidebarCollapsed ? 'center' : 'space-between', flexShrink: 0,
      }}>
        {!sidebarCollapsed && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
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
        )}
        <button
          onClick={toggleSidebar}
          title={sidebarCollapsed ? 'Expand' : 'Collapse'}
          style={{
            width: 28, height: 28, borderRadius: '0.4rem',
            background: 'rgba(124,58,237,0.07)', border: '1px solid rgba(124,58,237,0.12)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            cursor: 'pointer', color: 'var(--text-muted)', transition: 'all 0.18s ease',
          }}
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background='rgba(124,58,237,0.13)'; (e.currentTarget as HTMLElement).style.color='var(--primary)'; }}
          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background='rgba(124,58,237,0.07)'; (e.currentTarget as HTMLElement).style.color='var(--text-muted)'; }}
        >
          {sidebarCollapsed ? <ChevronRight size={13} /> : <ChevronLeft size={13} />}
        </button>
      </div>
    </aside>
  );
}
