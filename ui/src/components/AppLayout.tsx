// ── App Shell Layout — Glassmorp Floating Rounded Sidebar + Topbar + Breadcrumb ─
// Matches reference image exactly:
//   • Sidebar  = floating glass card, rounded, left column
//   • Topbar   = floating glass card, rounded, top-right
//   • BreadcrumbBar = floating glass card below topbar (page title + breadcrumb path)
//   • Collapse button in Topbar = toggles Sidebar (not browser back)
import React, { useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { Sidebar } from '@/components/Sidebar';
import { useWebSocket } from '@/lib/useWebSocket';
import { useUIStore } from '@/lib/store';
import { Search, Bell, Sun, MessageSquare, User, ChevronRight, Home } from 'lucide-react';
import { useI18n } from '@/lib/i18n';

// ── Route → { title, section, icon } map ─────────────────────────────────────
const ROUTE_META: Record<string, { title: string; section: string }> = {
  '/dashboard/command': { title: 'Trung Tâm Lệnh', section: 'Dashboard' },
  '/dashboard/executive': { title: 'Dashboard CEO', section: 'Dashboard' },
  '/operations/queue': { title: 'Hàng Chờ Nội Dung', section: 'Vận Hành' },
  '/operations/jobs': { title: 'Pipeline Jobs', section: 'Vận Hành' },
  '/operations/artifacts': { title: 'Artifacts', section: 'Vận Hành' },
  '/fleet/health': { title: 'Sức Khỏe Đội', section: 'Đội Hình' },
  '/fleet/accounts': { title: 'Tài Khoản', section: 'Đội Hình' },
  '/fleet/identities': { title: 'Danh Tính', section: 'Đội Hình' },
  '/strategy/ceo': { title: 'Bộ Não CEO', section: 'Chiến Lược' },
  '/strategy/niches': { title: 'Hiệu Suất Ngách', section: 'Chiến Lược' },
  '/strategy/overrides': { title: 'Ghi Đè Chiến Lược', section: 'Chiến Lược' },
  '/settings/general': { title: 'Cài Đặt Chung', section: 'Hệ Thống' },
  '/settings/advanced': { title: 'Nâng Cao', section: 'Hệ Thống' },
  '/settings/policy': { title: 'Chính Sách', section: 'Hệ Thống' },
  '/admin/licenses': { title: 'Quản Lý License', section: 'Quản Trị' },
};

// ── Breadcrumb Bar ────────────────────────────────────────────────────────────
function BreadcrumbBar() {
  const location = useLocation();
  const meta = ROUTE_META[location.pathname] ?? { title: 'Dashboard', section: 'Home' };

  return (
    <div style={{
      padding: '0 1.25rem',
      height: 48,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      background: 'var(--surface-glass)',
      backdropFilter: 'blur(16px)',
      WebkitBackdropFilter: 'blur(16px)',
      border: '1px solid var(--surface-glass-border)',
      borderRadius: 'var(--radius)',
      boxShadow: '0 2px 16px var(--surface-glass-shadow)',
      flexShrink: 0,
      transition: 'background 0.25s ease, border-color 0.25s ease, box-shadow 0.25s ease',
    }}>
      {/* Page title */}
      <span style={{
        fontWeight: 700,
        fontSize: '0.9375rem',
        color: 'var(--text-primary)',
        letterSpacing: '-0.01em',
      }}>
        {meta.title}
      </span>

      {/* Breadcrumb path */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
        <Home size={12} />
        <ChevronRight size={11} style={{ opacity: 0.5 }} />
        <span>{meta.section}</span>
        <ChevronRight size={11} style={{ opacity: 0.5 }} />
        <span style={{ color: 'var(--primary)', fontWeight: 600 }}>{meta.title}</span>
      </div>
    </div>
  );
}

// ── Topbar ────────────────────────────────────────────────────────────────────
function Topbar() {
  const { toggleSidebar, sidebarCollapsed } = useUIStore();
  const [search, setSearch] = useState('');

  const circleBtn: React.CSSProperties = {
    width: 36, height: 36,
    borderRadius: 'var(--radius)',
    background: 'var(--surface-glass)',
    border: '1px solid var(--surface-glass-border)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    cursor: 'pointer',
    color: 'var(--text-secondary)',
    transition: 'all 0.18s ease',
    backdropFilter: 'blur(8px)',
    WebkitBackdropFilter: 'blur(8px)',
    flexShrink: 0,
    position: 'relative',
  };

  function hIn(e: React.MouseEvent<HTMLButtonElement>) {
    const el = e.currentTarget as HTMLElement;
    el.style.background = 'var(--surface-glass-hover)';
    el.style.color = 'var(--primary)';
    el.style.borderColor = 'var(--primary-border)';
    el.style.transform = 'scale(1.06)';
  }
  function hOut(e: React.MouseEvent<HTMLButtonElement>) {
    const el = e.currentTarget as HTMLElement;
    el.style.background = 'var(--surface-glass)';
    el.style.color = 'var(--text-secondary)';
    el.style.borderColor = 'var(--surface-glass-border)';
    el.style.transform = 'scale(1)';
  }

  const badgeDot: React.CSSProperties = {
    position: 'absolute', top: 0, right: 0,
    minWidth: 16, height: 16,
    background: '#ef4444',
    borderRadius: '9999px',
    fontSize: '0.58rem', fontWeight: 700, color: '#fff',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    padding: '0 3px', border: '2px solid var(--badge-border, rgba(255,255,255,0.9))',
    lineHeight: 1, transform: 'translate(4px,-4px)',
  };

  return (
    <div style={{
      height: 56,
      background: 'var(--surface-glass)',
      backdropFilter: 'blur(20px)',
      WebkitBackdropFilter: 'blur(20px)',
      border: '1px solid var(--surface-glass-border)',
      borderRadius: 'var(--radius)',
      boxShadow: '0 4px 24px var(--surface-glass-shadow)',
      display: 'flex',
      alignItems: 'center',
      padding: '0 1rem',
      gap: '0.625rem',
      flexShrink: 0,
      transition: 'background 0.25s ease, border-color 0.25s ease, box-shadow 0.25s ease',
    }}>

      {/* Collapse Sidebar button — arrow rotates with sidebar state */}
      <button
        onClick={toggleSidebar}
        title={sidebarCollapsed ? 'Expand Sidebar' : 'Collapse Sidebar'}
        style={{ ...circleBtn, borderRadius: '0.5rem', width: 32, height: 32 }}
        onMouseEnter={hIn}
        onMouseLeave={hOut}
      >
        <svg
          width="14" height="14" viewBox="0 0 24 24"
          fill="none" stroke="currentColor" strokeWidth="2.2"
          strokeLinecap="round" strokeLinejoin="round"
          style={{
            transform: sidebarCollapsed ? 'rotate(180deg)' : 'rotate(0deg)',
            transition: 'transform 0.28s cubic-bezier(0.4,0,0.2,1)',
            display: 'block',
          }}
        >
          {/* ←| icon: arrow left + vertical bar */}
          <line x1="3" y1="12" x2="15" y2="12" />
          <polyline points="8 7 3 12 8 17" />
          <line x1="21" y1="5" x2="21" y2="19" />
        </svg>
      </button>

      {/* Search pill */}
      <div style={{ flex: 1, maxWidth: 280, position: 'relative' }}>
        <span style={{ position: 'absolute', left: '0.75rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)', pointerEvents: 'none', display: 'flex' }}>
          <Search size={13} />
        </span>
        <input
          type="text"
          placeholder="Search..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={{
            width: '100%',
            background: 'var(--surface-glass)',
            border: '1px solid var(--surface-glass-border)',
            borderRadius: '9999px',
            padding: '0.36rem 1rem 0.36rem 2rem',
            fontSize: '0.8rem',
            color: 'var(--text-primary)',
            outline: 'none',
            fontFamily: 'inherit',
            transition: 'all 0.18s ease',
          }}
          onFocus={e => { e.target.style.borderColor = 'var(--primary)'; e.target.style.boxShadow = '0 0 0 3px var(--primary-muted)'; e.target.style.background = 'var(--surface-glass-active)'; }}
          onBlur={e => { e.target.style.borderColor = 'var(--surface-glass-border)'; e.target.style.boxShadow = 'none'; e.target.style.background = 'var(--surface-glass)'; }}
        />
      </div>

      <div style={{ flex: 1 }} />

      <div style={{ display: 'flex', alignItems: 'center', gap: '0.375rem' }}>
        {/* Sun / theme */}
        <button style={circleBtn} title="Theme" onMouseEnter={hIn} onMouseLeave={hOut}><Sun size={15} /></button>

        {/* Messages badge 2 */}
        <button style={circleBtn} title="Messages" onMouseEnter={hIn} onMouseLeave={hOut}>
          <MessageSquare size={15} />
          <span style={badgeDot}>2</span>
        </button>

        {/* Notifications badge 1 */}
        <button style={circleBtn} title="Notifications" onMouseEnter={hIn} onMouseLeave={hOut}>
          <Bell size={15} />
          <span style={badgeDot}>1</span>
        </button>

        {/* Avatar */}
        <div
          title="Licensed device"
          style={{
            width: 36, height: 36, borderRadius: '50%',
            background: 'linear-gradient(135deg, #7c3aed 0%, #ec4899 100%)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            color: '#fff', fontWeight: 700, fontSize: '0.75rem',
            cursor: 'pointer', letterSpacing: '0.02em',
            boxShadow: '0 3px 10px rgba(124,58,237,0.38)',
            border: '2px solid rgba(255,255,255,0.85)',
            transition: 'transform 0.18s, box-shadow 0.18s',
            flexShrink: 0,
          }}
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.transform = 'scale(1.07)'; (e.currentTarget as HTMLElement).style.boxShadow = '0 5px 18px rgba(124,58,237,0.50)'; }}
          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.transform = 'scale(1)'; (e.currentTarget as HTMLElement).style.boxShadow = '0 3px 10px rgba(124,58,237,0.38)'; }}
        >
          <User size={14} />
        </div>
      </div>
    </div>
  );
}

// ── App Layout ────────────────────────────────────────────────────────────────
export function AppLayout() {
  useWebSocket();
  const { theme, pendingCount } = useUIStore();

  React.useEffect(() => {
    const root = document.documentElement;
    // Cross-fade: fade out → apply new theme → fade in
    root.style.transition = 'opacity 0.16s ease';
    root.style.opacity = '0';

    const t = window.setTimeout(() => {
      root.setAttribute('data-theme', theme);
      root.style.opacity = '1';
    }, 160);

    return () => {
      window.clearTimeout(t);
      root.style.opacity = '1'; // restore if unmounted mid-transition
    };
  }, [theme]);

  const GAP = "1.25rem";

  return (
    <div style={{
      height: '100vh',
      display: 'flex',
      padding: GAP,
      gap: GAP,
      background: 'transparent',
      overflow: 'hidden',
      position: 'relative',
      boxSizing: 'border-box',
    }}>


      {/* ── Sidebar ───────────────────────────────────────────────────────── */}
      <div style={{ position: 'relative', zIndex: 1, flexShrink: 0 }}>
        <Sidebar pendingCount={pendingCount} />
      </div>

      {/* ── Right column ──────────────────────────────────────────────────── */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: GAP, overflow: 'hidden', position: 'relative', zIndex: 1, minWidth: 0 }}>
        {/* Topbar */}
        <Topbar />

        {/* Breadcrumb bar (page title + path) */}
        <BreadcrumbBar />

        {/* Main scrollable content */}
        <div style={{ flex: 1, overflowY: 'auto', borderRadius: 'var(--radius)' }}>
          <div style={{ padding: '0' }}>
            <Outlet />
          </div>
        </div>
      </div>
    </div>
  );
}
