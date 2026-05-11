// ── Glassmorp Sidebar — white, clean, violet accents ─────────────────────────
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
        background: 'var(--surface)',
        borderRight: '1px solid var(--border)',
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        position: 'sticky',
        top: 0,
        overflow: 'hidden',
        flexShrink: 0,
      }}
    >
      {/* Logo */}
      <div style={{
        padding: '0.875rem 0.875rem',
        borderBottom: '1px solid var(--border)',
        display: 'flex',
        alignItems: 'center',
        gap: '0.625rem',
        minHeight: '56px',
      }}>
        <div style={{
          width: 30, height: 30,
          background: 'linear-gradient(135deg, #7c3aed 0%, #ec4899 100%)',
          borderRadius: '0.5rem',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexShrink: 0,
          boxShadow: '0 4px 10px rgba(124,58,237,0.35)',
        }}>
          <Shield size={14} color="#fff" />
        </div>
        {!sidebarCollapsed && (
          <div>
            <div style={{ fontWeight: 700, fontSize: '0.875rem', color: 'var(--text-primary)', lineHeight: 1 }}>
              AutoEcosystem
            </div>
            <div style={{ color: 'var(--text-muted)', fontSize: '0.62rem', marginTop: '0.15rem' }}>
              Command Center
            </div>
          </div>
        )}
      </div>

      {/* Nav */}
      <nav style={{ flex: 1, overflowY: 'auto', padding: '0.375rem 0.5rem', display: 'flex', flexDirection: 'column', gap: '0.05rem' }}>
        {sections.map((section) => (
          <React.Fragment key={section.title}>
            {!sidebarCollapsed && (
              <div className="sidebar-section">{section.title}</div>
            )}
            {sidebarCollapsed && <div style={{ height: '0.375rem' }} />}
            {section.items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) => `sidebar-item ${isActive ? 'active' : ''}`}
                title={sidebarCollapsed ? item.label : undefined}
              >
                <span style={{ flexShrink: 0 }}>{item.icon}</span>
                {!sidebarCollapsed && (
                  <>
                    <span style={{ flex: 1 }}>{item.label}</span>
                    {item.badge != null && item.badge > 0 && (
                      <span style={{
                        background: 'var(--danger)',
                        color: '#fff',
                        borderRadius: '9999px',
                        fontSize: '0.6rem',
                        fontWeight: 700,
                        padding: '0.1rem 0.4rem',
                        minWidth: '16px',
                        textAlign: 'center',
                      }}>
                        {item.badge}
                      </span>
                    )}
                  </>
                )}
              </NavLink>
            ))}
          </React.Fragment>
        ))}
      </nav>

      {/* Footer */}
      <div style={{
        padding: '0.625rem 0.75rem',
        borderTop: '1px solid var(--border)',
        display: 'flex',
        alignItems: 'center',
        gap: '0.5rem',
        justifyContent: sidebarCollapsed ? 'center' : 'space-between',
      }}>
        {!sidebarCollapsed && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
            <span className={`dot ${connected ? 'dot-success pulse' : 'dot-danger'}`} />
            <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
              {connected ? 'Live' : 'Offline'}
            </span>
          </div>
        )}
        <button
          className="btn btn-ghost btn-icon"
          onClick={toggleSidebar}
          title={sidebarCollapsed ? 'Expand' : 'Collapse'}
          style={{ color: 'var(--text-muted)' }}
        >
          {sidebarCollapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
        </button>
      </div>
    </aside>
  );
}
