// ── App Sidebar ───────────────────────────────────────────────────────────────
import React from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { useUIStore, useWSStore } from '@/lib/store';
import {
  LayoutDashboard, Terminal, Shield, Cpu, Settings,
  ChevronLeft, ChevronRight, Zap, Users, FileVideo,
  BarChart3, Layers, AlertTriangle, KeyRound, Scale,
  ListTodo, Activity,
} from 'lucide-react';

interface NavItem {
  to: string;
  icon: React.ReactNode;
  label: string;
  badge?: number;
}

interface NavSection {
  title: string;
  items: NavItem[];
}

export function Sidebar({ pendingCount = 0 }: { pendingCount?: number }) {
  const { sidebarCollapsed, toggleSidebar } = useUIStore();
  const { connected } = useWSStore();
  const location = useLocation();

  const sections: NavSection[] = [
    {
      title: 'Dashboard',
      items: [
        { to: '/dashboard/executive', icon: <BarChart3 size={16} />, label: 'Executive View' },
        { to: '/dashboard/command', icon: <Terminal size={16} />, label: 'Command Center' },
      ],
    },
    {
      title: 'Operations',
      items: [
        { to: '/operations/queue', icon: <ListTodo size={16} />, label: 'Content Queue', badge: pendingCount || undefined },
        { to: '/operations/jobs', icon: <Layers size={16} />, label: 'Pipeline Jobs' },
        { to: '/operations/artifacts', icon: <FileVideo size={16} />, label: 'Artifacts' },
      ],
    },
    {
      title: 'Fleet',
      items: [
        { to: '/fleet/health', icon: <Activity size={16} />, label: 'Fleet Health' },
        { to: '/fleet/accounts', icon: <Users size={16} />, label: 'Accounts' },
        { to: '/fleet/identities', icon: <KeyRound size={16} />, label: 'Identities' },
      ],
    },
    {
      title: 'Strategy',
      items: [
        { to: '/strategy/ceo', icon: <Cpu size={16} />, label: 'CEO Brain' },
        { to: '/strategy/niches', icon: <LayoutDashboard size={16} />, label: 'Niche Performance' },
        { to: '/strategy/overrides', icon: <AlertTriangle size={16} />, label: 'Overrides' },
      ],
    },
    {
      title: 'Settings',
      items: [
        { to: '/settings/general', icon: <Settings size={16} />, label: 'General' },
        { to: '/settings/advanced', icon: <Zap size={16} />, label: 'Advanced' },
        { to: '/settings/policy', icon: <Scale size={16} />, label: 'Policy Rules' },
      ],
    },
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
      <div style={{ padding: '1rem 0.75rem', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: '0.625rem', minHeight: '56px' }}>
        <div style={{ width: 28, height: 28, background: 'var(--primary)', borderRadius: '0.5rem', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
          <Shield size={14} color="#fff" />
        </div>
        {!sidebarCollapsed && (
          <div>
            <div style={{ fontWeight: 700, fontSize: '0.8125rem', lineHeight: 1 }}>AutoEcosystem</div>
            <div style={{ color: 'var(--text-muted)', fontSize: '0.65rem', marginTop: '0.125rem' }}>Command Center</div>
          </div>
        )}
      </div>

      {/* Nav */}
      <nav style={{ flex: 1, overflowY: 'auto', padding: '0.5rem 0.5rem', display: 'flex', flexDirection: 'column', gap: '0.125rem' }}>
        {sections.map((section) => (
          <React.Fragment key={section.title}>
            {!sidebarCollapsed && (
              <div className="sidebar-section">{section.title}</div>
            )}
            {sidebarCollapsed && <div style={{ height: '0.5rem' }} />}
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
                        background: 'var(--danger)', color: '#fff', borderRadius: '9999px',
                        fontSize: '0.65rem', fontWeight: 700, padding: '0.1rem 0.4rem',
                        minWidth: '18px', textAlign: 'center',
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

      {/* Footer: WS status + collapse toggle */}
      <div style={{ padding: '0.625rem 0.75rem', borderTop: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: '0.5rem', justifyContent: sidebarCollapsed ? 'center' : 'space-between' }}>
        {!sidebarCollapsed && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
            <span className={`dot ${connected ? 'dot-success pulse' : 'dot-danger'}`} />
            <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{connected ? 'Live' : 'Offline'}</span>
          </div>
        )}
        <button
          className="btn btn-ghost btn-icon"
          onClick={toggleSidebar}
          title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          style={{ color: 'var(--text-muted)' }}
        >
          {sidebarCollapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
        </button>
      </div>
    </aside>
  );
}
