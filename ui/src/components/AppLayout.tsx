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
import { useUIStore, useAuthStore } from '@/lib/store';
import { Search, Bell, Sun, MessageSquare, User, ChevronRight, Home } from 'lucide-react';
import { useI18n } from '@/lib/i18n';

// ── Route → { title, section, icon } map ─────────────────────────────────────
const ROUTE_META: Record<string, { title: string; section: string }> = {
  '/dashboard/command':   { title: 'Trung Tâm Lệnh',      section: 'Dashboard' },
  '/dashboard/executive': { title: 'Dashboard CEO',        section: 'Dashboard' },
  '/operations/queue':    { title: 'Hàng Chờ Nội Dung',   section: 'Vận Hành' },
  '/operations/jobs':     { title: 'Pipeline Jobs',        section: 'Vận Hành' },
  '/operations/artifacts':{ title: 'Artifacts',            section: 'Vận Hành' },
  '/fleet/health':        { title: 'Sức Khỏe Đội',        section: 'Đội Hình' },
  '/fleet/accounts':      { title: 'Tài Khoản',           section: 'Đội Hình' },
  '/fleet/identities':    { title: 'Danh Tính',           section: 'Đội Hình' },
  '/strategy/ceo':        { title: 'Bộ Não CEO',          section: 'Chiến Lược' },
  '/strategy/niches':     { title: 'Hiệu Suất Ngách',     section: 'Chiến Lược' },
  '/strategy/overrides':  { title: 'Ghi Đè Chiến Lược',  section: 'Chiến Lược' },
  '/settings/general':    { title: 'Cài Đặt Chung',       section: 'Hệ Thống' },
  '/settings/advanced':   { title: 'Nâng Cao',            section: 'Hệ Thống' },
  '/settings/policy':     { title: 'Chính Sách',          section: 'Hệ Thống' },
  '/admin/licenses':      { title: 'Quản Lý License',     section: 'Quản Trị' },
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
      background: 'rgba(255,255,255,0.52)',
      backdropFilter: 'blur(16px)',
      WebkitBackdropFilter: 'blur(16px)',
      border: '1px solid rgba(255,255,255,0.55)',
      borderRadius: '1.125rem',
      boxShadow: '0 2px 16px rgba(124,58,237,0.05)',
      flexShrink: 0,
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
  const { user } = useAuthStore();
  const { toggleSidebar } = useUIStore();
  const [search, setSearch] = useState('');

  const circleBtn: React.CSSProperties = {
    width: 36, height: 36,
    borderRadius: '50%',
    background: 'rgba(255,255,255,0.55)',
    border: '1px solid rgba(255,255,255,0.65)',
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
    el.style.background = 'rgba(124,58,237,0.10)';
    el.style.color = 'var(--primary)';
    el.style.borderColor = 'rgba(124,58,237,0.25)';
    el.style.transform = 'scale(1.06)';
  }
  function hOut(e: React.MouseEvent<HTMLButtonElement>) {
    const el = e.currentTarget as HTMLElement;
    el.style.background = 'rgba(255,255,255,0.55)';
    el.style.color = 'var(--text-secondary)';
    el.style.borderColor = 'rgba(255,255,255,0.65)';
    el.style.transform = 'scale(1)';
  }

  const badgeDot: React.CSSProperties = {
    position: 'absolute', top: 0, right: 0,
    minWidth: 16, height: 16,
    background: '#ef4444',
    borderRadius: '9999px',
    fontSize: '0.58rem', fontWeight: 700, color: '#fff',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    padding: '0 3px', border: '2px solid rgba(255,255,255,0.9)',
    lineHeight: 1, transform: 'translate(4px,-4px)',
  };

  return (
    <div style={{
      height: 56,
      background: 'rgba(255,255,255,0.55)',
      backdropFilter: 'blur(20px)',
      WebkitBackdropFilter: 'blur(20px)',
      border: '1px solid rgba(255,255,255,0.65)',
      borderRadius: '1.125rem',
      boxShadow: '0 4px 24px rgba(124,58,237,0.06)',
      display: 'flex',
      alignItems: 'center',
      padding: '0 1rem',
      gap: '0.625rem',
      flexShrink: 0,
    }}>

      {/* Collapse Sidebar button (not browser back!) */}
      <button
        onClick={toggleSidebar}
        title="Toggle Sidebar"
        style={{ ...circleBtn, borderRadius: '0.5rem', width: 32, height: 32 }}
        onMouseEnter={hIn}
        onMouseLeave={hOut}
      >
        {/* ←| collapse icon */}
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
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
            background: 'rgba(255,255,255,0.50)',
            border: '1px solid rgba(255,255,255,0.65)',
            borderRadius: '9999px',
            padding: '0.36rem 1rem 0.36rem 2rem',
            fontSize: '0.8rem',
            color: 'var(--text-primary)',
            outline: 'none',
            fontFamily: 'inherit',
            transition: 'all 0.18s ease',
          }}
          onFocus={e => { e.target.style.borderColor='var(--primary)'; e.target.style.boxShadow='0 0 0 3px rgba(124,58,237,0.12)'; e.target.style.background='rgba(255,255,255,0.85)'; }}
          onBlur={e => { e.target.style.borderColor='rgba(255,255,255,0.65)'; e.target.style.boxShadow='none'; e.target.style.background='rgba(255,255,255,0.50)'; }}
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
          title={user?.account ?? 'User'}
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
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.transform='scale(1.07)'; (e.currentTarget as HTMLElement).style.boxShadow='0 5px 18px rgba(124,58,237,0.50)'; }}
          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.transform='scale(1)'; (e.currentTarget as HTMLElement).style.boxShadow='0 3px 10px rgba(124,58,237,0.38)'; }}
        >
          {user?.account ? user.account.slice(0, 2).toUpperCase() : <User size={14} />}
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
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  const GAP = 10;

  return (
    <div style={{
      height: '100vh',
      display: 'flex',
      padding: GAP,
      gap: GAP,
      background: 'var(--bg)',
      backgroundAttachment: 'fixed',
      overflow: 'hidden',
      position: 'relative',
      boxSizing: 'border-box',
    }}>

      {/* ── Mesh gradient blobs (fixed behind everything) ─────────────────── */}
      <div style={{ position: 'fixed', inset: 0, pointerEvents: 'none', zIndex: 0, overflow: 'hidden' }}>
        <div style={{ position: 'absolute', width: 600, height: 600, top: -180, left: -140, borderRadius: '50%', background: 'radial-gradient(circle, rgba(192,132,252,0.55) 0%, transparent 65%)', filter: 'blur(80px)' }} />
        <div style={{ position: 'absolute', width: 500, height: 500, top: '28%', right: -100, borderRadius: '50%', background: 'radial-gradient(circle, rgba(147,197,253,0.48) 0%, transparent 65%)', filter: 'blur(70px)' }} />
        <div style={{ position: 'absolute', width: 450, height: 450, bottom: -80, left: '22%', borderRadius: '50%', background: 'radial-gradient(circle, rgba(249,168,212,0.45) 0%, transparent 65%)', filter: 'blur(75px)' }} />
        <div style={{ position: 'absolute', width: 360, height: 360, bottom: '12%', right: '20%', borderRadius: '50%', background: 'radial-gradient(circle, rgba(167,243,208,0.35) 0%, transparent 65%)', filter: 'blur(65px)' }} />
      </div>

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
        <div style={{ flex: 1, overflowY: 'auto', borderRadius: '1.125rem' }}>
          <div style={{ padding: '1.25rem 0' }}>
            <Outlet />
          </div>
        </div>
      </div>
    </div>
  );
}
