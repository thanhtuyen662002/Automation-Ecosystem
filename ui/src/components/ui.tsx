// ── Shared UI Components ──────────────────────────────────────────────────────
import React from 'react';
import { scoreGradient, scoreColor, statusBadgeClass, modeBadgeClass, fmtRelative, fmtScore } from '@/lib/utils';

// ── KPI Card ─────────────────────────────────────────────────────────────────
interface KpiCardProps {
  label: string;
  value: string | number;
  sub?: string;
  icon?: React.ReactNode;
  trend?: 'up' | 'down' | 'neutral';
  color?: string;
}
export function KpiCard({ label, value, sub, icon, trend, color }: KpiCardProps) {
  return (
    <div className="card" style={{ position: 'relative', overflow: 'hidden' }}>
      {color && (
        <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: '3px', background: color, borderRadius: 'var(--radius) var(--radius) 0 0' }} />
      )}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '0.5rem' }}>
        <div style={{ flex: 1 }}>
          <div className="stat-label" style={{ marginBottom: '0.5rem' }}>{label}</div>
          <div className="stat-value">{value}</div>
          {sub && <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem', marginTop: '0.375rem' }}>{sub}</div>}
        </div>
        {icon && (
          <div style={{ color: color ?? 'var(--primary)', opacity: 0.8, marginTop: '0.125rem' }}>{icon}</div>
        )}
      </div>
      {trend && (
        <div className="stat-change" style={{ marginTop: '0.5rem', color: trend === 'up' ? 'var(--success)' : trend === 'down' ? 'var(--danger)' : 'var(--text-muted)' }}>
          {trend === 'up' ? '↑' : trend === 'down' ? '↓' : '→'}
        </div>
      )}
    </div>
  );
}

// ── Score Bar ─────────────────────────────────────────────────────────────────
export function ScoreBar({ value, max = 1, label }: { value: number; max?: number; label?: string }) {
  const pct = Math.min(1, value / max);
  return (
    <div>
      {label && <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginBottom: '0.2rem' }}>{label}</div>}
      <div className="score-bar">
        <div className="score-bar-fill" style={{ width: `${pct * 100}%`, background: scoreGradient(pct) }} />
      </div>
    </div>
  );
}

// ── Badge ─────────────────────────────────────────────────────────────────────
export function Badge({ status, children }: { status?: string; children: React.ReactNode }) {
  const cls = status ? statusBadgeClass(status) : 'badge-muted';
  return <span className={`badge ${cls}`}>{children}</span>;
}

export function ModeBadge({ mode }: { mode: string }) {
  return <span className={`badge ${modeBadgeClass(mode)}`}>{mode}</span>;
}

// ── Status Dot ────────────────────────────────────────────────────────────────
export function StatusDot({ status }: { status: string }) {
  const cls = ['healthy', 'active', 'normal', 'success', 'completed'].includes(status?.toLowerCase())
    ? 'dot-success'
    : ['limited', 'pending', 'warning'].includes(status?.toLowerCase())
    ? 'dot-warning'
    : ['banned', 'failed', 'high', 'danger'].includes(status?.toLowerCase())
    ? 'dot-danger'
    : 'dot-muted';
  return <span className={`dot ${cls}`} />;
}

// ── Radial Gauge ──────────────────────────────────────────────────────────────
export function RadialGauge({ value, label, size = 80 }: { value: number; label: string; size?: number }) {
  const r = size * 0.38;
  const circ = 2 * Math.PI * r;
  const fill = circ * (1 - Math.min(1, value));
  const col = scoreColor(value);
  return (
    <div style={{ textAlign: 'center', display: 'inline-flex', flexDirection: 'column', alignItems: 'center', gap: '0.25rem' }}>
      <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--border)" strokeWidth={size * 0.1} />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={col} strokeWidth={size * 0.1}
          strokeDasharray={circ} strokeDashoffset={fill} strokeLinecap="round"
          style={{ transition: 'stroke-dashoffset 0.5s ease' }} />
        <text x="50%" y="50%" textAnchor="middle" dominantBaseline="central"
          style={{ transform: 'rotate(90deg)', transformOrigin: '50% 50%', fill: col, fontSize: size * 0.2, fontWeight: 700, fontVariantNumeric: 'tabular-nums', fontFamily: 'Inter' }}>
          {Math.round(value * 100)}%
        </text>
      </svg>
      <span style={{ fontSize: '0.65rem', color: 'var(--text-muted)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</span>
    </div>
  );
}

// ── Section Header ────────────────────────────────────────────────────────────
export function SectionHeader({ title, action }: { title: string; action?: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem' }}>
      <div className="section-title">{title}</div>
      {action}
    </div>
  );
}

// ── Empty State ───────────────────────────────────────────────────────────────
export function EmptyState({ icon, message }: { icon?: string; message: string }) {
  return (
    <div style={{ textAlign: 'center', padding: '3rem 1rem', color: 'var(--text-muted)' }}>
      {icon && <div style={{ fontSize: '2rem', marginBottom: '0.75rem' }}>{icon}</div>}
      <div style={{ fontSize: '0.875rem' }}>{message}</div>
    </div>
  );
}

// ── Loading Skeleton ──────────────────────────────────────────────────────────
export function Skeleton({ height = 16, width = '100%', borderRadius = 6 }: { height?: number; width?: string | number; borderRadius?: number }) {
  return (
    <div style={{ height, width, borderRadius, background: 'var(--surface-2)', animation: 'pulse 1.5s ease infinite' }} />
  );
}

// ── Tag ───────────────────────────────────────────────────────────────────────
export function Tag({ children }: { children: React.ReactNode }) {
  return <span className="tag">{children}</span>;
}

// ── Divider ───────────────────────────────────────────────────────────────────
export function Divider() {
  return <hr className="divider" />;
}

// ── Live Event Item ───────────────────────────────────────────────────────────
export function LiveEventItem({ event }: { event: { event: string; data: Record<string, unknown>; ts: number } }) {
  const colorMap: Record<string, string> = {
    decision_made: 'var(--primary)',
    publish_event: 'var(--success)',
    metric_update: 'var(--info)',
    connected: 'var(--success)',
  };
  const color = colorMap[event.event] ?? 'var(--border)';
  const d = (event.data ?? {}) as Record<string, unknown>;
  return (
    <div className="live-feed-item" style={{ borderLeftColor: color, marginBottom: '0.375rem', background: 'var(--surface-2)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '0.5rem' }}>
        <div>
          <span style={{ color, fontWeight: 600, fontSize: '0.7rem', textTransform: 'uppercase' }}>{event.event.replace('_', ' ')}</span>
          {!!d.content_id && <span style={{ color: 'var(--text-secondary)', marginLeft: '0.4rem', fontFamily: 'monospace' }}>{String(d.content_id)}</span>}
          {!!d.decision && <Badge status={String(d.decision)}>{String(d.decision)}</Badge>}
          {!!d.action && <Badge status={String(d.action)}>{String(d.action)}</Badge>}
          {d.final_score !== undefined && (
            <span style={{ color: scoreColor(Number(d.final_score)), marginLeft: '0.4rem', fontFamily: 'monospace', fontSize: '0.7rem' }}>
              {fmtScore(Number(d.final_score))}
            </span>
          )}
        </div>
        <span style={{ color: 'var(--text-muted)', fontSize: '0.65rem', flexShrink: 0 }}>{fmtRelative(event.ts)}</span>
      </div>
    </div>
  );
}

// ── Modal / Slide-over ────────────────────────────────────────────────────────
interface SlideOverProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  width?: number;
}
export function SlideOver({ open, onClose, title, children, width = 480 }: SlideOverProps) {
  if (!open) return null;
  return (
    <>
      <div className="slide-over-backdrop" onClick={onClose} />
      <div className="slide-over" style={{ width }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '1.25rem', borderBottom: '1px solid var(--border)', position: 'sticky', top: 0, background: 'var(--surface)', zIndex: 1 }}>
          <div style={{ fontWeight: 700, fontSize: '0.9375rem' }}>{title}</div>
          <button className="btn btn-ghost btn-icon" onClick={onClose} style={{ fontSize: '1.1rem', lineHeight: 1 }}>✕</button>
        </div>
        <div style={{ padding: '1.25rem' }}>{children}</div>
      </div>
    </>
  );
}

// ── Confirm Dialog ────────────────────────────────────────────────────────────
export function ConfirmDialog({ open, onClose, onConfirm, title, message, danger = false }: {
  open: boolean; onClose: () => void; onConfirm: () => void; title: string; message: string; danger?: boolean;
}) {
  if (!open) return null;
  return (
    <>
      <div className="slide-over-backdrop" onClick={onClose} />
      <div style={{ position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%, -50%)', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1.5rem', width: '380px', zIndex: 60, animation: 'fadeIn 0.2s ease' }}>
        <div style={{ fontWeight: 700, marginBottom: '0.75rem' }}>{title}</div>
        <div style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', marginBottom: '1.25rem' }}>{message}</div>
        <div style={{ display: 'flex', gap: '0.625rem', justifyContent: 'flex-end' }}>
          <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button className={`btn ${danger ? 'btn-danger' : 'btn-primary'}`} onClick={() => { onConfirm(); onClose(); }}>Confirm</button>
        </div>
      </div>
    </>
  );
}

// ── Page Header ───────────────────────────────────────────────────────────────
export function PageHeader({ title, subtitle, action }: { title: string; subtitle?: string; action?: React.ReactNode }) {
  return (
    <div className="page-header" style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between' }}>
      <div>
        <h1 className="page-title">{title}</h1>
        {subtitle && <p className="page-subtitle">{subtitle}</p>}
      </div>
      {action && <div>{action}</div>}
    </div>
  );
}

// ── Inline Stat Row ────────────────────────────────────────────────────────────
export function StatRow({ label, value, mono = false }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0.4rem 0', borderBottom: '1px solid var(--border-subtle)' }}>
      <span style={{ color: 'var(--text-secondary)', fontSize: '0.8125rem' }}>{label}</span>
      <span style={{ fontWeight: 500, fontSize: '0.8125rem', fontFamily: mono ? 'JetBrains Mono, monospace' : 'inherit' }}>{value}</span>
    </div>
  );
}

// ── Toggle Row ────────────────────────────────────────────────────────────────
export function ToggleRow({ label, description, checked, onChange }: { label: string; description?: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0.75rem 0', borderBottom: '1px solid var(--border-subtle)' }}>
      <div>
        <div style={{ fontWeight: 500, fontSize: '0.875rem' }}>{label}</div>
        {description && <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem', marginTop: '0.125rem' }}>{description}</div>}
      </div>
      <label className="toggle">
        <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} />
        <div className="toggle-track" />
        <div className="toggle-thumb" />
      </label>
    </div>
  );
}

// ── Decision Card ─────────────────────────────────────────────────────────────
// Replaces the content queue item card. Shows a verdict, score decomposition,
// plain-English reasoning, EV range, and prominent action buttons.
import { SCORE_SIGNAL_LABELS, translateReason } from '@/lib/modes';

interface DecisionCardProps {
  item: {
    content_id: string; platform: string; niche: string; mode: string;
    status: string; reason: string; final_score: number; expected_value: number;
    ev_range?: [number, number]; confidence: number; hook: string; caption: string;
    risk_flags: string[]; publish_account?: string; created_at: number;
    signals?: Record<string, number>;
  };
  queueAvgEv?: number;
  onApprove?: () => void;
  onReject?: () => void;
  onForce?: () => void;
  onClick?: () => void;
}

export function DecisionCard({ item, queueAvgEv = 13.5, onApprove, onReject, onForce, onClick }: DecisionCardProps) {
  const score = item.final_score;
  const verdict = score >= 0.75 ? 'PUBLISH' : score >= 0.50 ? 'REVIEW' : 'REJECT';
  const verdictColor = verdict === 'PUBLISH' ? 'var(--success)' : verdict === 'REVIEW' ? 'var(--warning)' : 'var(--danger)';
  const verdictBg = verdict === 'PUBLISH' ? 'var(--success-muted)' : verdict === 'REVIEW' ? 'var(--warning-muted)' : 'var(--danger-muted)';
  const isPending = item.status === 'pending';
  const signals = item.signals ?? {};
  const evRange = item.ev_range ?? [item.expected_value * 0.75, item.expected_value * 1.25];

  return (
    <div
      className="card"
      style={{ borderLeft: `4px solid ${verdictColor}`, cursor: onClick ? 'pointer' : 'default' }}
      onClick={onClick}
    >
      {/* Verdict + Hook */}
      <div style={{ display: 'flex', gap: '1rem', alignItems: 'flex-start', marginBottom: '0.75rem' }}>
        <div style={{ flexShrink: 0, textAlign: 'center', minWidth: 70 }}>
          <div style={{ background: verdictBg, border: `1px solid ${verdictColor}`, borderRadius: 'var(--radius-sm)', padding: '0.25rem 0.5rem', marginBottom: '0.25rem' }}>
            <div style={{ fontSize: '0.6rem', color: verdictColor, fontWeight: 700, letterSpacing: '0.08em' }}>{verdict}</div>
            <div style={{ fontSize: '1.5rem', fontWeight: 800, color: verdictColor, lineHeight: 1.1, fontVariantNumeric: 'tabular-nums' }}>
              {Math.round(score * 100)}
            </div>
          </div>
          <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)' }}>{fmtRelative(item.created_at)}</div>
        </div>

        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap', marginBottom: '0.375rem' }}>
            <span className="badge badge-muted" style={{ textTransform: 'capitalize' }}>{item.niche}</span>
            <span className="badge badge-muted">{item.platform}</span>
            <span className="badge badge-muted">{item.mode}</span>
            {item.risk_flags.map(f => (
              <span key={f} className="badge badge-danger">⚠ {f.replace(/_/g, ' ')}</span>
            ))}
          </div>
          <div style={{ fontWeight: 600, fontSize: '0.9rem', color: 'var(--text-primary)', marginBottom: '0.25rem', lineHeight: 1.4 }}>
            "{item.hook}"
          </div>
          {/* Plain-English AI reasoning */}
          <div style={{ fontSize: '0.75rem', color: 'var(--primary)', marginBottom: '0.375rem' }}>
            🧠 {translateReason(item.reason)}
          </div>
          {item.publish_account && (
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
              Will publish from <span className="mono" style={{ color: 'var(--text-secondary)' }}>{item.publish_account}</span>
            </div>
          )}
        </div>

        {/* EV + confidence */}
        <div style={{ flexShrink: 0, textAlign: 'right', minWidth: 100 }}>
          <div style={{ fontSize: '0.75rem', color: 'var(--success)', fontWeight: 700, marginBottom: '0.125rem' }}>
            EV ${evRange[0]}–${evRange[1]}
          </div>
          <div style={{ fontSize: '0.7rem', color: item.expected_value >= queueAvgEv ? 'var(--success)' : 'var(--text-muted)' }}>
            {item.expected_value >= queueAvgEv ? '↑ above avg' : '↓ below avg'}
          </div>
          <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
            conf {fmtScore(item.confidence)}
          </div>
        </div>
      </div>

      {/* 5-Signal Score Decomposition */}
      {Object.keys(signals).length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem', marginBottom: '0.75rem', padding: '0.625rem', background: 'var(--surface-2)', borderRadius: 'var(--radius-sm)' }}>
          {Object.entries(SCORE_SIGNAL_LABELS).map(([key, meta]) => {
            const val = signals[key] ?? 0;
            const isPositive = meta.weight > 0;
            return (
              <div key={key} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <div style={{ width: 72, fontSize: '0.65rem', color: 'var(--text-muted)', textAlign: 'right', flexShrink: 0 }}>{meta.label}</div>
                <div style={{ flex: 1, height: 4, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
                  <div style={{ height: '100%', width: `${val * 100}%`, background: isPositive ? meta.color : 'var(--danger)', borderRadius: 2, transition: 'width 0.4s ease' }} />
                </div>
                <div style={{ width: 28, fontSize: '0.65rem', color: isPositive ? meta.color : 'var(--danger)', fontWeight: 600, flexShrink: 0 }}>
                  {isPositive ? '' : '−'}{Math.round(val * 100)}
                </div>
                <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', opacity: 0.7, flexShrink: 0 }}>{Math.round(Math.abs(meta.weight) * 100)}%</div>
              </div>
            );
          })}
        </div>
      )}

      {/* Actions */}
      {isPending && (onApprove || onReject) && (
        <div style={{ display: 'flex', gap: '0.5rem' }} onClick={e => e.stopPropagation()}>
          <button className="btn btn-primary" style={{ flex: 2 }} onClick={onApprove}>
            ✓ Publish
          </button>
          {onForce && (
            <button className="btn btn-secondary" style={{ flex: 1 }} onClick={onForce} title="Force publish, bypasses score gate">
              ⚡ Force
            </button>
          )}
          <button className="btn btn-danger" style={{ flex: 1 }} onClick={onReject}>
            ✕ Reject
          </button>
        </div>
      )}
    </div>
  );
}

// ── Risk Meter ────────────────────────────────────────────────────────────────
// A horizontal strip plotting all fleet accounts by risk score.
// Zones: SAFE | ELEVATED | HIGH | CRITICAL
interface RiskDot { id: string; risk: number; label?: string; }
export function RiskMeter({ accounts, onSelect }: { accounts: RiskDot[]; onSelect?: (id: string) => void }) {
  const zones = [
    { label: 'SAFE',     from: 0,    to: 0.30, color: 'var(--success)',  bg: 'var(--success-muted)' },
    { label: 'ELEVATED', from: 0.30, to: 0.60, color: 'var(--warning)',  bg: 'var(--warning-muted)' },
    { label: 'HIGH',     from: 0.60, to: 0.80, color: '#F97316',         bg: 'rgba(249,115,22,0.12)' },
    { label: 'CRITICAL', from: 0.80, to: 1.00, color: 'var(--danger)',   bg: 'var(--danger-muted)' },
  ];

  // Numeric risk mapping
  const riskScore = (r: string | number) => typeof r === 'number' ? r : r === 'high' ? 0.85 : r === 'medium' ? 0.50 : 0.15;

  return (
    <div style={{ marginBottom: '0.5rem' }}>
      <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginBottom: '0.375rem', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Fleet Risk Distribution</div>
      <div style={{ position: 'relative', height: 40, display: 'flex', borderRadius: 'var(--radius-sm)', overflow: 'hidden', border: '1px solid var(--border)' }}>
        {zones.map(z => (
          <div key={z.label} style={{ flex: z.to - z.from, background: z.bg, borderRight: z.to < 1 ? '1px solid var(--border)' : undefined, position: 'relative' }}>
            <div style={{ position: 'absolute', bottom: 2, left: '50%', transform: 'translateX(-50%)', fontSize: '0.55rem', color: z.color, fontWeight: 700, letterSpacing: '0.05em', whiteSpace: 'nowrap' }}>{z.label}</div>
          </div>
        ))}
        {/* Account dots */}
        {accounts.map(a => {
          const rs = riskScore(a.risk);
          const pct = rs * 100;
          const zone = zones.find(z => rs >= z.from && rs < z.to) ?? zones[zones.length - 1];
          return (
            <div key={a.id}
              onClick={() => onSelect?.(a.id)}
              title={`${a.id} — risk ${Math.round(rs * 100)}%`}
              style={{
                position: 'absolute', top: '50%', left: `${pct}%`, transform: 'translate(-50%, -50%)',
                width: 22, height: 22, borderRadius: '50%',
                background: zone.color, border: '2px solid var(--bg)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: '0.55rem', color: '#fff', fontWeight: 700, cursor: onSelect ? 'pointer' : 'default',
                zIndex: 2, transition: 'transform 0.2s',
                boxShadow: '0 1px 4px rgba(0,0,0,0.4)',
              }}
            >
              {a.label ?? a.id.slice(-3)}
            </div>
          );
        })}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '0.25rem', padding: '0 2px', fontSize: '0.6rem', color: 'var(--text-muted)' }}>
        <span>0%</span><span>30%</span><span>60%</span><span>80%</span><span>100%</span>
      </div>
    </div>
  );
}

// ── Urgency Row ───────────────────────────────────────────────────────────────
// An action-first account row. Sorted by urgency, shows suggested next action.
interface UrgencyRowProps {
  account: {
    account_id: string; phase: string; risk_level: string; trust_score: number;
    fatigue_level: number; anomaly_count: number; cooldown_remaining_hours: number;
    uploads_suspended: boolean; operating_mode: string;
  };
  urgencyScore: number;
  suggestedAction: { label: string; severity: 'danger' | 'warning' | 'info' | 'muted'; action?: () => void };
  onClick?: () => void;
}
export function UrgencyRow({ account: a, urgencyScore, suggestedAction, onClick }: UrgencyRowProps) {
  const borderColor = urgencyScore >= 0.7 ? 'var(--danger)' : urgencyScore >= 0.4 ? 'var(--warning)' : 'var(--success)';
  const bgColor = urgencyScore >= 0.7 ? 'rgba(239,68,68,0.04)' : urgencyScore >= 0.4 ? 'rgba(245,158,11,0.04)' : 'transparent';

  return (
    <div
      style={{ display: 'flex', alignItems: 'center', gap: '1rem', padding: '0.75rem 1rem', borderLeft: `4px solid ${borderColor}`, background: bgColor, borderBottom: '1px solid var(--border-subtle)', cursor: onClick ? 'pointer' : 'default' }}
      onClick={onClick}
    >
      {/* Account ID */}
      <div style={{ minWidth: 80, flexShrink: 0 }}>
        <div className="mono" style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>{a.account_id}</div>
        <Badge status={a.phase.toLowerCase()}>{a.phase}</Badge>
      </div>

      {/* State signals */}
      <div style={{ flex: 1, display: 'flex', gap: '0.75rem', flexWrap: 'wrap', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', fontSize: '0.75rem' }}>
          <span style={{ color: 'var(--text-muted)' }}>Trust</span>
          <span style={{ color: scoreColor(a.trust_score), fontWeight: 600 }}>{fmtScore(a.trust_score)}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', fontSize: '0.75rem' }}>
          <span style={{ color: 'var(--text-muted)' }}>Fatigue</span>
          <span style={{ color: scoreColor(1 - a.fatigue_level), fontWeight: 600 }}>{fmtScore(a.fatigue_level)}</span>
        </div>
        {a.anomaly_count > 0 && (
          <span style={{ fontSize: '0.75rem', color: 'var(--danger)', fontWeight: 600 }}>⚠ {a.anomaly_count} anomal{a.anomaly_count > 1 ? 'ies' : 'y'}</span>
        )}
        {a.cooldown_remaining_hours > 0 && (
          <span style={{ fontSize: '0.75rem', color: 'var(--warning)' }}>⏱ {a.cooldown_remaining_hours.toFixed(1)}h cooldown</span>
        )}
        {a.uploads_suspended && (
          <span className="badge badge-warning">⏸ uploads paused</span>
        )}
      </div>

      {/* Suggested action */}
      <div style={{ flexShrink: 0 }} onClick={e => e.stopPropagation()}>
        {suggestedAction.action ? (
          <button
            className={`btn btn-sm ${suggestedAction.severity === 'danger' ? 'btn-danger' : suggestedAction.severity === 'warning' ? 'btn-secondary' : 'btn-ghost'}`}
            onClick={suggestedAction.action}
          >
            {suggestedAction.label}
          </button>
        ) : (
          <span style={{ fontSize: '0.75rem', color: `var(--${suggestedAction.severity === 'muted' ? 'text-muted' : suggestedAction.severity})` }}>
            {suggestedAction.label}
          </span>
        )}
      </div>
    </div>
  );
}

// ── Action Impact Preview ─────────────────────────────────────────────────────
// Shows a before/after diff when changing growth mode.
import { GROWTH_MODES } from '@/lib/modes';

export function ActionImpactPreview({ currentMode, proposedMode }: { currentMode: string; proposedMode: string }) {
  if (currentMode === proposedMode) return null;
  const cur = GROWTH_MODES[currentMode];
  const prop = GROWTH_MODES[proposedMode];
  if (!cur || !prop) return null;

  const rows: { label: string; cur: string; prop: string; direction: 'up' | 'down' | 'neutral' }[] = [
    {
      label: 'Threshold modifier',
      cur: `×${cur.threshold_modifier.toFixed(2)}`,
      prop: `×${prop.threshold_modifier.toFixed(2)}`,
      direction: prop.threshold_modifier < cur.threshold_modifier ? 'down' : prop.threshold_modifier > cur.threshold_modifier ? 'up' : 'neutral',
    },
    {
      label: 'Exploration rate',
      cur: `${Math.round(cur.exploration_rate * 100)}%`,
      prop: `${Math.round(prop.exploration_rate * 100)}%`,
      direction: prop.exploration_rate > cur.exploration_rate ? 'up' : 'down',
    },
    {
      label: 'Max risk allowed',
      cur: fmtScore(cur.max_risk_allowed),
      prop: fmtScore(prop.max_risk_allowed),
      direction: prop.max_risk_allowed > cur.max_risk_allowed ? 'up' : 'down',
    },
    {
      label: 'Est. content/day',
      cur: `${cur.est_content_per_day[0]}–${cur.est_content_per_day[1]}`,
      prop: `${prop.est_content_per_day[0]}–${prop.est_content_per_day[1]}`,
      direction: prop.est_content_per_day[1] > cur.est_content_per_day[1] ? 'up' : 'down',
    },
    {
      label: 'Risk exposure',
      cur: cur.est_risk_exposure,
      prop: prop.est_risk_exposure,
      direction: ['very low','low','medium','high','very high'].indexOf(prop.est_risk_exposure) > ['very low','low','medium','high','very high'].indexOf(cur.est_risk_exposure) ? 'up' : 'down',
    },
  ];

  return (
    <div style={{ background: 'var(--surface-2)', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)', padding: '0.875rem', marginTop: '0.75rem' }}>
      <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginBottom: '0.625rem', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Impact Preview</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.375rem' }}>
        {rows.map(r => (
          <div key={r.label} style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', fontSize: '0.8125rem' }}>
            <span style={{ flex: 1, color: 'var(--text-muted)', fontSize: '0.75rem' }}>{r.label}</span>
            <span style={{ color: 'var(--text-secondary)', minWidth: 60, textAlign: 'right' }}>{r.cur}</span>
            <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>→</span>
            <span style={{ fontWeight: 600, minWidth: 60, color: r.direction === 'neutral' ? 'var(--text-primary)' : 'var(--text-primary)' }}>{r.prop}</span>
            <span style={{ fontSize: '0.8rem', color: r.direction === 'up' ? 'var(--danger)' : r.direction === 'down' ? 'var(--success)' : 'var(--text-muted)' }}>
              {r.direction === 'up' ? '↑' : r.direction === 'down' ? '↓' : '—'}
            </span>
          </div>
        ))}
      </div>
      {prop.description && (
        <div style={{ marginTop: '0.625rem', fontSize: '0.75rem', color: 'var(--text-secondary)', borderTop: '1px solid var(--border-subtle)', paddingTop: '0.5rem' }}>
          {prop.description}
        </div>
      )}
      {prop.warning && (
        <div style={{ marginTop: '0.375rem', fontSize: '0.75rem', color: 'var(--danger)', fontWeight: 500 }}>
          ⚠ {prop.warning}
        </div>
      )}
    </div>
  );
}

// ── Capacity Burn Bar ─────────────────────────────────────────────────────────
// Shows current upload consumption rate + projected EOD utilisation.
export function CapacityBurnBar({
  used, cap, label, projectedEod, warnAt = 0.80,
}: { used: number; cap: number; label: string; projectedEod?: number; warnAt?: number }) {
  const pct = Math.min(1, used / cap);
  const projPct = projectedEod ? Math.min(1, projectedEod / cap) : undefined;
  const color = pct >= warnAt ? 'var(--danger)' : pct >= 0.6 ? 'var(--warning)' : 'var(--success)';

  return (
    <div style={{ marginBottom: '0.75rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.25rem' }}>
        <span style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>{label}</span>
        <span style={{ fontSize: '0.8125rem', fontWeight: 600, color }}>
          {used} / {cap} <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>({Math.round(pct * 100)}%)</span>
        </span>
      </div>
      <div style={{ position: 'relative', height: 10, background: 'var(--border)', borderRadius: 5, overflow: 'visible' }}>
        {/* Actual used */}
        <div style={{ position: 'absolute', left: 0, top: 0, height: '100%', width: `${pct * 100}%`, background: color, borderRadius: 5, transition: 'width 0.5s ease' }} />
        {/* Projected EOD */}
        {projPct !== undefined && projPct > pct && (
          <div style={{ position: 'absolute', left: `${pct * 100}%`, top: 0, height: '100%', width: `${(projPct - pct) * 100}%`, background: color, opacity: 0.25, borderRadius: '0 5px 5px 0' }} />
        )}
        {/* Warn threshold line */}
        <div style={{ position: 'absolute', left: `${warnAt * 100}%`, top: -3, bottom: -3, width: 1.5, background: 'var(--danger)', opacity: 0.6 }} />
      </div>
      {projPct !== undefined && (
        <div style={{ fontSize: '0.7rem', color: projPct >= warnAt ? 'var(--danger)' : 'var(--text-muted)', marginTop: '0.25rem', textAlign: 'right' }}>
          Projected EOD: {Math.round(projPct * cap)} / {cap} ({Math.round(projPct * 100)}%)
          {projPct >= warnAt && ' — ⚠ cap risk'}
        </div>
      )}
    </div>
  );
}

// ── Decision Block ────────────────────────────────────────────────────────────
// 1 block = 1 decision. Mandatory visible signals: risk, EV, reason, confidence.
export interface DecisionBlockProps {
  title: string;           // What is happening — plain language
  reason: string;          // Why — plain language, max 2 lines
  ev?: string;             // Expected value: "$22", "+2 accounts", etc.
  confidence?: string;     // Confidence level: "78%" — MUST be visible per spec
  risk: 'low' | 'medium' | 'high';
  badge?: string;
  badgeColor?: string;
  ifSkip?: string;         // Consequence of inaction — 1 line
  riskFlags?: string[];    // Individual flags — shown explicitly, NOT compressed
  action: { label: string; onClick: () => void; danger?: boolean };
  passive?: { label: string; onClick?: () => void };
}

export function DecisionBlock({
  title, reason, ev, confidence, risk, badge, badgeColor,
  ifSkip, riskFlags, action, passive,
}: DecisionBlockProps) {
  const riskColor = risk === 'high' ? 'var(--danger)' : risk === 'medium' ? 'var(--warning)' : 'var(--success)';
  const riskLabel = risk === 'high' ? 'Rủi ro Cao' : risk === 'medium' ? 'Rủi ro Trung bình' : 'Rủi ro Thấp';

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: '0.625rem',
      padding: '1rem 1.25rem',
      background: 'var(--surface)',
      border: `1px solid ${riskColor}22`,
      borderLeft: `4px solid ${riskColor}`,
      borderRadius: 'var(--radius)',
    }}>
      {/* Header row: badge · title · risk */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', flexWrap: 'wrap' }}>
        {badge && (
          <span style={{
            background: badgeColor ?? riskColor, color: '#fff',
            fontSize: '0.6rem', fontWeight: 700, letterSpacing: '0.08em',
            padding: '0.15rem 0.5rem', borderRadius: '9999px', textTransform: 'uppercase', flexShrink: 0,
          }}>{badge}</span>
        )}
        <span style={{ fontWeight: 700, fontSize: '0.9375rem', color: 'var(--text-primary)', flex: 1 }}>{title}</span>
        <span style={{ fontSize: '0.7rem', color: riskColor, fontWeight: 600, flexShrink: 0 }}>● {riskLabel}</span>
      </div>

      {/* Reason */}
      <div style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>{reason}</div>

      {/* Risk flags — each shown individually (V5 fix) */}
      {riskFlags && riskFlags.length > 0 && (
        <div style={{ display: 'flex', gap: '0.375rem', flexWrap: 'wrap' }}>
          {riskFlags.map(flag => (
            <span key={flag} style={{
              fontSize: '0.7rem', padding: '0.2rem 0.5rem',
              background: 'var(--danger-muted)', border: '1px solid var(--danger)',
              borderRadius: '9999px', color: 'var(--danger)', fontWeight: 600,
            }}>⚠ {flag}</span>
          ))}
        </div>
      )}

      {/* Signals row: EV · Confidence · If-skip (all mandatory per spec) */}
      <div style={{ display: 'flex', gap: '1.25rem', flexWrap: 'wrap', alignItems: 'center' }}>
        {ev && (
          <div style={{ display: 'flex', gap: '0.3rem', alignItems: 'center' }}>
            <span style={{ fontSize: '0.65rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>EV</span>
            <span style={{ fontWeight: 700, color: 'var(--success)', fontSize: '0.9rem' }}>{ev}</span>
          </div>
        )}
        {confidence && (
          <div style={{ display: 'flex', gap: '0.3rem', alignItems: 'center' }}>
            <span style={{ fontSize: '0.65rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Độ Tin Cậy</span>
            <span style={{ fontWeight: 600, color: 'var(--primary)', fontSize: '0.875rem' }}>{confidence}</span>
          </div>
        )}
        {ifSkip && (
          <div style={{ fontSize: '0.75rem', color: 'var(--danger)', opacity: 0.9 }}>
            ⚠ Nếu bỏ qua: {ifSkip}
          </div>
        )}
      </div>

      {/* Actions: 1 primary + 1 passive (skip/ignore) */}
      <div style={{ display: 'flex', gap: '0.625rem', marginTop: '0.125rem' }}>
        <button
          className={`btn ${action.danger ? 'btn-danger' : 'btn-primary'}`}
          style={{ flex: 2 }}
          onClick={action.onClick}
        >
          {action.label}
        </button>
        {passive && (
          <button
            className="btn btn-ghost"
            style={{ flex: 1, color: 'var(--text-muted)' }}
            onClick={passive.onClick}
          >
            {passive.label}
          </button>
        )}
      </div>
    </div>
  );
}


