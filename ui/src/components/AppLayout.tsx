// ── App Shell Layout — Glassmorp with Topbar ──────────────────────────────────
import React, { useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { Sidebar } from '@/components/Sidebar';
import { useWebSocket } from '@/lib/useWebSocket';
import { useUIStore, useAuthStore } from '@/lib/store';
import { Search, Bell, Settings, User } from 'lucide-react';
import { useI18n } from '@/lib/i18n';

// ── Page title map ───────────────────────────────────────────────────────────
const PAGE_TITLES: Record<string, string> = {
  '/dashboard/executive': 'Executive Dashboard',
  '/dashboard/command':   'Command Center',
  '/operations/queue':    'Content Queue',
  '/operations/jobs':     'Pipeline Jobs',
  '/operations/artifacts':'Artifacts',
  '/fleet/health':        'Fleet Health',
  '/fleet/accounts':      'Accounts',
  '/fleet/identities':    'Identities',
  '/strategy/ceo':        'CEO Brain',
  '/strategy/niches':     'Niche Performance',
  '/strategy/overrides':  'Overrides',
  '/settings/general':    'General Settings',
  '/settings/advanced':   'Advanced Settings',
  '/settings/policy':     'Policy Rules',
  '/admin/licenses':      'License Manager',
};

// ── Topbar ────────────────────────────────────────────────────────────────────
function Topbar() {
  const location = useLocation();
  const { user } = useAuthStore();
  const [search, setSearch] = useState('');
  const title = PAGE_TITLES[location.pathname] ?? 'Dashboard';

  return (
    <header style={{
      height: '56px',
      background: 'var(--surface)',
      borderBottom: '1px solid var(--border)',
      display: 'flex',
      alignItems: 'center',
      padding: '0 1.75rem',
      gap: '1rem',
      flexShrink: 0,
      position: 'sticky',
      top: 0,
      zIndex: 20,
    }}>
      {/* Page title */}
      <div style={{
        fontWeight: 700,
        fontSize: '1rem',
        color: 'var(--text-primary)',
        letterSpacing: '-0.01em',
        minWidth: 160,
      }}>
        {title}
      </div>

      {/* Search */}
      <div style={{
        flex: 1,
        maxWidth: 360,
        position: 'relative',
      }}>
        <span style={{
          position: 'absolute', left: '0.75rem', top: '50%',
          transform: 'translateY(-50%)',
          color: 'var(--text-muted)',
          pointerEvents: 'none',
          display: 'flex',
        }}>
          <Search size={14} />
        </span>
        <input
          type="text"
          placeholder="Search..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={{
            width: '100%',
            background: 'var(--bg-soft)',
            border: '1px solid var(--border)',
            borderRadius: '9999px',
            padding: '0.375rem 1rem 0.375rem 2.25rem',
            fontSize: '0.8125rem',
            color: 'var(--text-primary)',
            outline: 'none',
            fontFamily: 'inherit',
            transition: 'border-color 0.15s',
          }}
          onFocus={e => { e.target.style.borderColor = 'var(--primary)'; }}
          onBlur={e => { e.target.style.borderColor = 'var(--border)'; }}
        />
      </div>

      {/* Spacer */}
      <div style={{ flex: 1 }} />

      {/* Right icons */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
        {/* Notifications */}
        <button style={{
          position: 'relative',
          width: 36, height: 36,
          borderRadius: '9999px',
          background: 'transparent',
          border: '1px solid var(--border)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          cursor: 'pointer',
          color: 'var(--text-secondary)',
          transition: 'all 0.15s',
        }}
          onMouseEnter={e => {
            (e.currentTarget as HTMLElement).style.background = 'var(--primary-soft)';
            (e.currentTarget as HTMLElement).style.color = 'var(--primary)';
          }}
          onMouseLeave={e => {
            (e.currentTarget as HTMLElement).style.background = 'transparent';
            (e.currentTarget as HTMLElement).style.color = 'var(--text-secondary)';
          }}
        >
          <Bell size={15} />
          <span style={{
            position: 'absolute', top: 6, right: 6,
            width: 7, height: 7,
            background: 'var(--danger)',
            borderRadius: '50%',
            border: '1.5px solid var(--surface)',
          }} />
        </button>

        {/* Settings */}
        <button style={{
          width: 36, height: 36,
          borderRadius: '9999px',
          background: 'transparent',
          border: '1px solid var(--border)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          cursor: 'pointer',
          color: 'var(--text-secondary)',
          transition: 'all 0.15s',
        }}
          onMouseEnter={e => {
            (e.currentTarget as HTMLElement).style.background = 'var(--primary-soft)';
            (e.currentTarget as HTMLElement).style.color = 'var(--primary)';
          }}
          onMouseLeave={e => {
            (e.currentTarget as HTMLElement).style.background = 'transparent';
            (e.currentTarget as HTMLElement).style.color = 'var(--text-secondary)';
          }}
        >
          <Settings size={15} />
        </button>

        {/* Avatar */}
        <div style={{
          width: 34, height: 34,
          borderRadius: '50%',
          background: 'linear-gradient(135deg, #7c3aed, #ec4899)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          color: '#fff',
          fontWeight: 700,
          fontSize: '0.75rem',
          cursor: 'pointer',
          marginLeft: '0.25rem',
          boxShadow: '0 2px 8px rgba(124,58,237,0.35)',
          flexShrink: 0,
        }}>
          {user?.account ? user.account.slice(0, 2).toUpperCase() : <User size={14} />}
        </div>
      </div>
    </header>
  );
}

// ── App Layout ────────────────────────────────────────────────────────────────
export function AppLayout() {
  useWebSocket();
  const { theme, pendingCount } = useUIStore();

  React.useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  return (
    <div style={{
      display: 'flex',
      height: '100vh',
      overflow: 'hidden',
      background: 'var(--bg)',
    }}>
      <Sidebar pendingCount={pendingCount} />

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <Topbar />

        <main style={{
          flex: 1,
          overflowY: 'auto',
          background: 'var(--bg)',
        }}>
          <div style={{
            padding: '1.75rem 2rem',
            maxWidth: '1440px',
            width: '100%',
            margin: '0 auto',
          }}>
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
