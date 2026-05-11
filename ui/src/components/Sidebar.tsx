// ── Glassmorp Sidebar — glass, clean, violet pill active ──────────────────────
import React from 'react';
import { NavLink } from 'react-router-dom';
import { useUIStore, useWSStore, useAuthStore } from '@/lib/store';
import { useI18n } from '@/lib/i18n';
import {
  LayoutDashboard, Terminal, Shield, Cpu, Settings,
  ChevronLeft, ChevronRight, Zap, Users, FileVideo,
  BarChart3, Layers, AlertTriangle, KeyRound, Scale,
  ListTodo, Activity, Lock,
} from 'lucide-react';

interface NavItem { to: string; icon: React.ReactNode; label: string; badge?: number; }
interface NavSection { title: string; items: NavItem[]; }

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
        { to: '/dashboard/executive', icon: <BarChart3 size={15} />, label: t('nav.executive') },
        { to: '/dashboard/command',   icon: <Terminal size={15} />,  label: t('nav.command') },
      ],
    },
    {
      title: t('section.operations'),
      items: [
        { to: '/operations/queue',     icon: <ListTodo size={15} />,  label: t('nav.queue'), badge: pendingCount || undefined },
        { to: '/operations/jobs',      icon: <Layers size={15} />,    label: t('nav.jobs') },
        { to: '/operations/artifacts', icon: <FileVideo size={15} />, label: t('nav.artifacts') },
      ],
    },
    {
      title: t('section.fleet'),
      items: [
        { to: '/fleet/health',     icon: <Activity size={15} />, label: t('nav.fleet') },
        { to: '/fleet/accounts',   icon: <Users size={15} />,    label: t('nav.accounts') },
        { to: '/fleet/identities', icon: <KeyRound size={15} />, label: t('nav.identities') },
      ],
    },
    {
      title: t('section.strategy'),
      items: [
        { to: '/strategy/ceo',       icon: <Cpu size={15} />,            label: t('nav.brain') },
        { to: '/strategy/niches',    icon: <LayoutDashboard size={15} />, label: t('nav.niches') },
        { to: '/strategy/overrides', icon: <AlertTriangle size={15} />,   label: t('nav.overrides') },
      ],
    },
    {
      title: t('section.settings'),
      items: [
        { to: '/settings/general',  icon: <Settings size={15} />, label: t('nav.settings_gen') },
        { to: '/settings/advanced', icon: <Zap size={15} />,      label: t('nav.settings_adv') },
        { to: '/settings/policy',   icon: <Scale size={15} />,    label: t('nav.settings_pol') },
      ],
    },
    ...(isAdmin ? [{
      title: t('section.admin'),
      items: [{ to: '/admin/licenses', icon: <Lock size={15} />, label: t('nav.licenses') }],
    }] : []),
  ];

  return (
    <aside
      className={`sidebar ${sidebarCollapsed ? 'collapsed' : ''}`}
      style={{
        background: 'rgba(255, 255, 255, 0.78)',
        backdropFilter: 'blur(14px)',
        WebkitBackdropFilter: 'blur(14px)',
        borderRight: '1px solid rgba(255,255,255,0.55)',
        boxShadow: '2px 0 20px rgba(124,58,237,0.06)',
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        position: 'sticky',
        top: 0,
        overflow: 'hidden',
        flexShrink: 0,
      }}
    >
      {/* ── Logo ── */}
      <div style={{
        padding: sidebarCollapsed ? '0.875rem 0.5rem' : '0.875rem 1rem',
        borderBottom: '1px solid rgba(237,233,248,0.6)',
        display: 'flex',
        alignItems: 'center',
        gap: '0.625rem',
        minHeight: '56px',
      }}>
        <div style={{
          width: 32, height: 32,
          background: 'linear-gradient(135deg, #7c3aed 0%, #ec4899 100%)',
          borderRadius: '0.625rem',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexShrink: 0,
          boxShadow: '0 4px 12px rgba(124,58,237,0.38)',
        }}>
          <Shield size={14} color="#fff" />
        </div>
        {!sidebarCollapsed && (
          <div>
            <div style={{ fontWeight: 700, fontSize: '0.9rem', color: 'var(--text-primary)', lineHeight: 1.1 }}>
              AutoEcosystem
            </div>
            <div style={{ color: 'var(--text-muted)', fontSize: '0.6rem', marginTop: '0.1rem', letterSpacing: '0.03em' }}>
              Command Center
            </div>
          </div>
        )}
      </div>

      {/* ── Nav ── */}
      <nav style={{
        flex: 1,
        overflowY: 'auto',
        padding: '0.5rem 0.625rem',
        display: 'flex',
        flexDirection: 'column',
        gap: 0,
      }}>
        {sections.map((section) => (
          <React.Fragment key={section.title}>
            {/* Section label */}
            {!sidebarCollapsed && (
              <div style={{
                fontSize: '0.62rem',
                fontWeight: 700,
                textTransform: 'uppercase',
                letterSpacing: '0.08em',
                color: 'var(--text-muted)',
                padding: '0.75rem 0.5rem 0.3rem',
                opacity: 0.7,
              }}>
                {section.title}
              </div>
            )}
            {sidebarCollapsed && <div style={{ height: '0.5rem' }} />}

            {/* Nav items */}
            {section.items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                title={sidebarCollapsed ? item.label : undefined}
                style={{ textDecoration: 'none' }}
              >
                {({ isActive }) => (
                  <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.5rem',
                    padding: sidebarCollapsed ? '0.5rem' : '0.44rem 0.625rem',
                    borderRadius: '0.5rem',
                    margin: '0.08rem 0',
                    cursor: 'pointer',
                    justifyContent: sidebarCollapsed ? 'center' : 'flex-start',
                    transition: 'all 0.15s ease',
                    background: isActive
                      ? 'linear-gradient(135deg, #7c3aed 0%, #a855f7 100%)'
                      : 'transparent',
                    color: isActive ? '#fff' : 'var(--text-secondary)',
                    boxShadow: isActive ? '0 4px 12px rgba(124,58,237,0.30)' : 'none',
                    fontWeight: isActive ? 600 : 400,
                    fontSize: '0.8125rem',
                  }}
                    onMouseEnter={e => {
                      if (!isActive) {
                        (e.currentTarget as HTMLElement).style.background = 'rgba(124,58,237,0.08)';
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
                    <span style={{ flexShrink: 0, display: 'flex', alignItems: 'center' }}>{item.icon}</span>
                    {!sidebarCollapsed && (
                      <>
                        <span style={{ flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {item.label}
                        </span>
                        {item.badge != null && item.badge > 0 && (
                          <span style={{
                            background: isActive ? 'rgba(255,255,255,0.3)' : 'var(--danger)',
                            color: '#fff',
                            borderRadius: '9999px',
                            fontSize: '0.6rem',
                            fontWeight: 700,
                            padding: '0.08rem 0.38rem',
                            minWidth: '16px',
                            textAlign: 'center',
                            flexShrink: 0,
                          }}>
                            {item.badge}
                          </span>
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

      {/* ── Footer ── */}
      <div style={{
        padding: '0.625rem 0.75rem',
        borderTop: '1px solid rgba(237,233,248,0.6)',
        display: 'flex',
        alignItems: 'center',
        gap: '0.5rem',
        justifyContent: sidebarCollapsed ? 'center' : 'space-between',
      }}>
        {!sidebarCollapsed && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
            <span
              style={{
                width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
                background: connected ? 'var(--success)' : 'var(--danger)',
                boxShadow: connected ? '0 0 6px var(--success)' : 'none',
                display: 'inline-block',
              }}
            />
            <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
              {connected ? 'Live' : 'Offline'}
            </span>
          </div>
        )}
        <button
          onClick={toggleSidebar}
          title={sidebarCollapsed ? 'Expand' : 'Collapse'}
          style={{
            width: 28, height: 28,
            borderRadius: '0.4rem',
            background: 'rgba(124,58,237,0.08)',
            border: 'none',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            cursor: 'pointer',
            color: 'var(--text-muted)',
            transition: 'all 0.15s',
          }}
          onMouseEnter={e => {
            (e.currentTarget as HTMLElement).style.background = 'rgba(124,58,237,0.15)';
            (e.currentTarget as HTMLElement).style.color = 'var(--primary)';
          }}
          onMouseLeave={e => {
            (e.currentTarget as HTMLElement).style.background = 'rgba(124,58,237,0.08)';
            (e.currentTarget as HTMLElement).style.color = 'var(--text-muted)';
          }}
        >
          {sidebarCollapsed ? <ChevronRight size={13} /> : <ChevronLeft size={13} />}
        </button>
      </div>
    </aside>
  );
}
