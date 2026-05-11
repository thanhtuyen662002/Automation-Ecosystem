// ── Utility helpers ───────────────────────────────────────────────────────────
import { type ClassValue, clsx } from 'clsx';

export function cn(...inputs: ClassValue[]) {
  return clsx(inputs);
}

// Shared chart color palette — single source of truth
export const CHART_COLORS = ['#6366F1', '#10B981', '#F59E0B', '#EF4444', '#3B82F6', '#8B5CF6'];

export function fmt(n: number, decimals = 1): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return n.toFixed(decimals);
}

export function fmtPct(n: number): string {
  return (n * 100).toFixed(1) + '%';
}

export function fmtScore(n: number): string {
  return n.toFixed(2);
}

export function fmtCurrency(n: number): string {
  return '$' + n.toFixed(2);
}

export function fmtTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export function fmtRelative(ts: number): string {
  const diff = Math.floor(Date.now() / 1000 - ts);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export function scoreColor(score: number): string {
  if (score >= 0.7) return 'var(--success)';
  if (score >= 0.4) return 'var(--warning)';
  return 'var(--danger)';
}

export function scoreGradient(score: number): string {
  if (score >= 0.7) return 'linear-gradient(90deg, var(--success), #34d399)';
  if (score >= 0.4) return 'linear-gradient(90deg, var(--warning), #fbbf24)';
  return 'linear-gradient(90deg, var(--danger), #f87171)';
}

export function statusBadgeClass(status: string): string {
  const map: Record<string, string> = {
    healthy: 'badge-success', active: 'badge-success', approved: 'badge-success',
    success: 'badge-success', completed: 'badge-success', normal: 'badge-success',
    limited: 'badge-warning', pending: 'badge-warning', running: 'badge-info',
    cooldown: 'badge-warning', warm_up: 'badge-info', ramp_up: 'badge-info',
    banned: 'badge-danger', failed: 'badge-danger', rejected: 'badge-danger',
    high: 'badge-danger', safe: 'badge-warning',
    disabled: 'badge-muted', low: 'badge-success', medium: 'badge-warning',
  };
  return map[status?.toLowerCase()] ?? 'badge-muted';
}

export function modeBadgeClass(mode: string): string {
  const map: Record<string, string> = {
    SAFE: 'badge-warning', NORMAL: 'badge-success', AGGRESSIVE: 'badge-danger',
    balanced: 'badge-info', aggressive: 'badge-danger',
    conservative: 'badge-muted', recovery: 'badge-warning', domination: 'badge-primary',
  };
  return map[mode] ?? 'badge-muted';
}

export function truncate(s: string, max = 60): string {
  return s.length > max ? s.slice(0, max) + '…' : s;
}
