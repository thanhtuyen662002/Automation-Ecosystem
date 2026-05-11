// ── App Shell Layout — Glassmorp with Topbar ──────────────────────────────────
import React, { useState } from 'react';
import { Outlet } from 'react-router-dom';
import { Sidebar } from '@/components/Sidebar';
import { useWebSocket } from '@/lib/useWebSocket';
import { useUIStore, useAuthStore } from '@/lib/store';
import { Search, Bell, Settings, User } from 'lucide-react';

// ── Topbar ────────────────────────────────────────────────────────────────────
function Topbar() {
  const { user } = useAuthStore();
  const [search, setSearch] = useState('');

  return (
    <header style={{
      height: '56px',
      background: 'rgba(255, 255, 255, 0.78)',
      backdropFilter: 'blur(14px)',
      WebkitBackdropFilter: 'blur(14px)',
      borderBottom: '1px solid rgba(255,255,255,0.55)',
      boxShadow: '0 2px 16px rgba(124,58,237,0.06)',
      display: 'flex',
      alignItems: 'center',
      padding: '0 1.25rem',
      gap: '0.75rem',
      flexShrink: 0,
      position: 'sticky',
      top: 0,
      zIndex: 20,
    }}>
      {/* Back arrow */}
      <button
        onClick={() => window.history.back()}
        style={{
          width: 32, height: 32,
          borderRadius: '0.5rem',
          background: 'transparent',
          border: '1px solid var(--border)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          cursor: 'pointer',
          color: 'var(--text-muted)',
          flexShrink: 0,
          transition: 'all 0.15s',
        }}
        onMouseEnter={e => {
          (e.currentTarget as HTMLElement).style.background = 'var(--primary-soft)';
          (e.currentTarget as HTMLElement).style.color = 'var(--primary)';
          (e.currentTarget as HTMLElement).style.borderColor = 'var(--primary-border)';
        }}
        onMouseLeave={e => {
          (e.currentTarget as HTMLElement).style.background = 'transparent';
          (e.currentTarget as HTMLElement).style.color = 'var(--text-muted)';
          (e.currentTarget as HTMLElement).style.borderColor = 'var(--border)';
        }}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M19 12H5M12 5l-7 7 7 7" />
        </svg>
      </button>

      {/* Search bar */}
      <div style={{ flex: 1, maxWidth: 380, position: 'relative' }}>
        <span style={{
          position: 'absolute', left: '0.875rem', top: '50%',
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
            background: 'rgba(250,249,255,0.80)',
            border: '1px solid rgba(237,233,248,0.8)',
            borderRadius: '9999px',
            padding: '0.42rem 1rem 0.42rem 2.375rem',
            fontSize: '0.8125rem',
            color: 'var(--text-primary)',
            outline: 'none',
            fontFamily: 'inherit',
            transition: 'border-color 0.15s, box-shadow 0.15s',
          }}
          onFocus={e => {
            e.target.style.borderColor = 'var(--primary)';
            e.target.style.boxShadow = '0 0 0 3px var(--primary-muted)';
            e.target.style.background = '#fff';
          }}
          onBlur={e => {
            e.target.style.borderColor = 'rgba(237,233,248,0.8)';
            e.target.style.boxShadow = 'none';
            e.target.style.background = 'rgba(250,249,255,0.80)';
          }}
        />
      </div>

      {/* Spacer */}
      <div style={{ flex: 1 }} />

      {/* Right icons */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.375rem' }}>
        {/* Settings */}
        <button style={{
          width: 36, height: 36,
          borderRadius: '0.625rem',
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
            (e.currentTarget as HTMLElement).style.borderColor = 'var(--primary-border)';
          }}
          onMouseLeave={e => {
            (e.currentTarget as HTMLElement).style.background = 'transparent';
            (e.currentTarget as HTMLElement).style.color = 'var(--text-secondary)';
            (e.currentTarget as HTMLElement).style.borderColor = 'var(--border)';
          }}
        >
          <Settings size={15} />
        </button>

        {/* Bell with badge */}
        <button style={{
          position: 'relative',
          width: 36, height: 36,
          borderRadius: '0.625rem',
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
            (e.currentTarget as HTMLElement).style.borderColor = 'var(--primary-border)';
          }}
          onMouseLeave={e => {
            (e.currentTarget as HTMLElement).style.background = 'transparent';
            (e.currentTarget as HTMLElement).style.color = 'var(--text-secondary)';
            (e.currentTarget as HTMLElement).style.borderColor = 'var(--border)';
          }}
        >
          <Bell size={15} />
          <span style={{
            position: 'absolute', top: 4, right: 4,
            minWidth: 14, height: 14,
            background: 'var(--danger)',
            borderRadius: '9999px',
            fontSize: '0.55rem',
            fontWeight: 700,
            color: '#fff',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            padding: '0 3px',
            border: '1.5px solid rgba(255,255,255,0.9)',
            lineHeight: 1,
          }}>1</span>
        </button>

        {/* Avatar */}
        <div style={{
          width: 36, height: 36,
          borderRadius: '0.625rem',
          background: 'linear-gradient(135deg, #7c3aed 0%, #ec4899 100%)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          color: '#fff',
          fontWeight: 700,
          fontSize: '0.75rem',
          cursor: 'pointer',
          marginLeft: '0.125rem',
          boxShadow: '0 3px 10px rgba(124,58,237,0.35)',
          flexShrink: 0,
          letterSpacing: '0.02em',
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
