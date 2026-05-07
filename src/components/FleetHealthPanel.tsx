/**
 * FleetHealthPanel — real-time safety dashboard for the fleet.
 *
 * Shows:
 *   - Safety KPI cards (safe mode, cooldown, high-risk, anomaly rate)
 *   - Upload rate gauges (10-min + hourly utilisation)
 *   - Lifecycle phase distribution
 *   - Trust & fatigue fleet averages
 *   - Per-account lifecycle table with operator actions
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/services/api";
import type { AccountLifecycleSummary, FleetSafetyMetrics, LifecyclePhase } from "@/types/api";

// ── Palette helpers ───────────────────────────────────────────────────────────

const PHASE_COLORS: Record<LifecyclePhase, string> = {
  WARM_UP:  "var(--clr-blue)",
  RAMP_UP:  "var(--clr-yellow)",
  NORMAL:   "var(--clr-green)",
  COOLDOWN: "var(--clr-red)",
};

const PHASE_LABELS: Record<LifecyclePhase, string> = {
  WARM_UP:  "Warm-Up",
  RAMP_UP:  "Ramp-Up",
  NORMAL:   "Normal",
  COOLDOWN: "Cooldown",
};

const MODE_BADGE: Record<string, string> = {
  SAFE:       "badge-safe",
  NORMAL:     "badge-normal",
  AGGRESSIVE: "badge-aggressive",
};

const RISK_BADGE: Record<string, string> = {
  low:    "badge-risk-low",
  medium: "badge-risk-medium",
  high:   "badge-risk-high",
};

// ── Sub-components ────────────────────────────────────────────────────────────

function KpiCard({
  label,
  value,
  sub,
  accent,
  warn = false,
}: {
  label: string;
  value: string | number;
  sub?: string;
  accent?: string;
  warn?: boolean;
}) {
  return (
    <div className={`fh-kpi-card ${warn ? "fh-kpi-card--warn" : ""}`} style={accent ? { "--kpi-accent": accent } as React.CSSProperties : undefined}>
      <span className="fh-kpi-label">{label}</span>
      <span className="fh-kpi-value">{value}</span>
      {sub && <span className="fh-kpi-sub">{sub}</span>}
    </div>
  );
}

function UtilBar({ label, used, cap }: { label: string; used: number; cap: number }) {
  const pct = Math.min(1, used / Math.max(cap, 1));
  const color = pct > 0.85 ? "var(--clr-red)" : pct > 0.6 ? "var(--clr-yellow)" : "var(--clr-green)";
  return (
    <div className="fh-util-row">
      <span className="fh-util-label">{label}</span>
      <div className="fh-util-bar-bg">
        <div className="fh-util-bar-fill" style={{ width: `${pct * 100}%`, background: color }} />
      </div>
      <span className="fh-util-count">{used}/{cap}</span>
    </div>
  );
}

function PhaseDistribution({ phases }: { phases: FleetSafetyMetrics["lifecycle_phases"] }) {
  const total = Object.values(phases).reduce((a, b) => a + b, 0) || 1;
  const entries = (Object.entries(phases) as [LifecyclePhase, number][]).filter(([, v]) => v > 0);
  return (
    <div className="fh-phase-bar-wrap">
      <div className="fh-phase-bar">
        {entries.map(([phase, count]) => (
          <div
            key={phase}
            className="fh-phase-segment"
            style={{ width: `${(count / total) * 100}%`, background: PHASE_COLORS[phase] }}
            title={`${PHASE_LABELS[phase]}: ${count}`}
          />
        ))}
      </div>
      <div className="fh-phase-legend">
        {(Object.entries(phases) as [LifecyclePhase, number][]).map(([phase, count]) => (
          <span key={phase} className="fh-phase-legend-item">
            <span className="fh-phase-dot" style={{ background: PHASE_COLORS[phase] }} />
            {PHASE_LABELS[phase]} ({count})
          </span>
        ))}
      </div>
    </div>
  );
}

function AccountRow({
  acct,
  onCooldown,
  onClear,
}: {
  acct: AccountLifecycleSummary;
  onCooldown: (id: string, severe: boolean) => void;
  onClear: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const trustColor = acct.trust_score >= 0.7 ? "var(--clr-green)" : acct.trust_score >= 0.5 ? "var(--clr-yellow)" : "var(--clr-red)";
  const fatigueColor = acct.fatigue_level < 0.5 ? "var(--clr-green)" : acct.fatigue_level < 0.7 ? "var(--clr-yellow)" : "var(--clr-red)";

  return (
    <div className={`fh-acct-row ${acct.phase === "COOLDOWN" ? "fh-acct-row--cooldown" : ""}`}>
      <div className="fh-acct-main" onClick={() => setExpanded(e => !e)}>
        {/* Phase badge */}
        <span className="fh-phase-badge" style={{ background: PHASE_COLORS[acct.phase] }}>
          {PHASE_LABELS[acct.phase]}
        </span>

        {/* Account ID (truncated) */}
        <span className="fh-acct-id" title={acct.account_id}>
          {acct.account_id.slice(0, 8)}…
        </span>

        {/* Session/upload usage */}
        <span className="fh-acct-usage">
          <span className="fh-usage-label">Sessions</span>
          <span className={`fh-usage-val ${acct.sessions_today >= 3 ? "fh-usage-val--max" : ""}`}>
            {acct.sessions_today}/3
          </span>
          <span className="fh-usage-label">Uploads</span>
          <span className={`fh-usage-val ${acct.uploads_today >= 1 ? "fh-usage-val--max" : ""}`}>
            {acct.uploads_today}/1
          </span>
        </span>

        {/* Trust bar */}
        <span className="fh-mini-stat">
          <span className="fh-mini-label">Trust</span>
          <span className="fh-mini-val" style={{ color: trustColor }}>
            {(acct.trust_score * 100).toFixed(0)}%
          </span>
        </span>

        {/* Fatigue bar */}
        <span className="fh-mini-stat">
          <span className="fh-mini-label">Fatigue</span>
          <span className="fh-mini-val" style={{ color: fatigueColor }}>
            {(acct.fatigue_level * 100).toFixed(0)}%
          </span>
        </span>

        {/* Mode / Risk */}
        <span className={`badge ${MODE_BADGE[acct.operating_mode] || "badge-normal"}`}>{acct.operating_mode}</span>
        <span className={`badge ${RISK_BADGE[acct.risk_level] || "badge-risk-low"}`}>{acct.risk_level}</span>

        {/* Cooldown indicator */}
        {acct.phase === "COOLDOWN" && (
          <span className="fh-cooldown-timer">🕐 {acct.cooldown_remaining_hours.toFixed(1)}h</span>
        )}

        <span className="fh-expand-icon">{expanded ? "▲" : "▼"}</span>
      </div>

      {expanded && (
        <div className="fh-acct-detail">
          <div className="fh-detail-grid">
            <div className="fh-detail-item">
              <span className="fh-detail-label">Account Age</span>
              <span className="fh-detail-val">{acct.account_age_days.toFixed(1)} days</span>
            </div>
            <div className="fh-detail-item">
              <span className="fh-detail-label">Anomaly Count</span>
              <span className="fh-detail-val" style={{ color: acct.anomaly_count > 0 ? "var(--clr-red)" : "var(--clr-green)" }}>
                {acct.anomaly_count}
              </span>
            </div>
            <div className="fh-detail-item">
              <span className="fh-detail-label">Uploads Suspended</span>
              <span className="fh-detail-val">{acct.uploads_suspended ? "⚠️ Yes" : "✓ No"}</span>
            </div>
            <div className="fh-detail-item">
              <span className="fh-detail-label">Full ID</span>
              <span className="fh-detail-val fh-mono">{acct.account_id}</span>
            </div>
          </div>
          <div className="fh-acct-actions">
            <button
              className="btn btn-sm btn-warn"
              onClick={() => onCooldown(acct.account_id, false)}
              disabled={acct.phase === "COOLDOWN"}
            >
              ⏸ Cooldown (48h)
            </button>
            <button
              className="btn btn-sm btn-danger"
              onClick={() => onCooldown(acct.account_id, true)}
              disabled={acct.phase === "COOLDOWN"}
            >
              🚫 Severe Cooldown (72h)
            </button>
            <button
              className="btn btn-sm btn-ghost"
              onClick={() => onClear(acct.account_id)}
              disabled={acct.phase !== "COOLDOWN"}
            >
              ✓ Clear Cooldown
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function FleetHealthPanel() {
  const queryClient = useQueryClient();

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["fleet-health"],
    queryFn:  () => api.getFleetHealth(),
    refetchInterval: 30_000,  // auto-refresh every 30s
  });

  const cooldownMutation = useMutation({
    mutationFn: ({ id, severe }: { id: string; severe: boolean }) => api.triggerCooldown(id, severe),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["fleet-health"] }),
  });

  const clearMutation = useMutation({
    mutationFn: (id: string) => api.clearCooldown(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["fleet-health"] }),
  });

  if (isLoading) return <div className="fh-loading">Loading fleet health…</div>;
  if (isError || !data) return <div className="fh-error">Failed to load fleet health data.</div>;

  const { metrics, accounts, snapshot_ts } = data;

  return (
    <div className="fleet-health-panel">
      {/* ── Header ── */}
      <div className="fh-header">
        <div className="fh-header-left">
          <h2 className="fh-title">Fleet Safety Dashboard</h2>
          <span className="fh-subtitle">
            Real-time risk monitoring · {accounts.length} accounts tracked
          </span>
        </div>
        <div className="fh-header-right">
          <span className="fh-ts">Updated: {new Date(snapshot_ts).toLocaleTimeString()}</span>
          <button className="btn btn-sm btn-ghost" onClick={() => refetch()}>↺ Refresh</button>
        </div>
      </div>

      {/* ── KPI Cards ── */}
      <div className="fh-kpi-grid">
        <KpiCard
          label="Safe Mode"
          value={metrics.safe_mode_count}
          sub={`of ${metrics.total_accounts_tracked} accounts`}
          warn={metrics.safe_mode_count > 0}
          accent="var(--clr-yellow)"
        />
        <KpiCard
          label="In Cooldown"
          value={metrics.cooldown_count}
          sub="automatic + manual"
          warn={metrics.cooldown_count > 0}
          accent="var(--clr-red)"
        />
        <KpiCard
          label="High Risk"
          value={metrics.high_risk_count}
          sub="trust < 0.30 or 3+ anomalies"
          warn={metrics.high_risk_count > 0}
          accent="var(--clr-red)"
        />
        <KpiCard
          label="Anomaly Rate"
          value={`${(metrics.anomaly_rate * 100).toFixed(1)}%`}
          sub="accounts with active anomalies"
          warn={metrics.anomaly_rate > 0.2}
          accent="var(--clr-orange)"
        />
        <KpiCard
          label="Avg Trust"
          value={`${(metrics.avg_trust_score * 100).toFixed(0)}%`}
          sub="fleet average"
          warn={metrics.avg_trust_score < 0.6}
          accent={metrics.avg_trust_score >= 0.7 ? "var(--clr-green)" : "var(--clr-yellow)"}
        />
        <KpiCard
          label="Avg Fatigue"
          value={`${(metrics.avg_fatigue_level * 100).toFixed(0)}%`}
          sub="fleet average"
          warn={metrics.avg_fatigue_level > 0.65}
          accent={metrics.avg_fatigue_level < 0.5 ? "var(--clr-green)" : "var(--clr-yellow)"}
        />
        <KpiCard
          label="Active Sessions"
          value={metrics.active_sessions}
          sub={`${metrics.active_proxies} proxies`}
          accent="var(--clr-blue)"
        />
        <KpiCard
          label="Skip Rate (30m)"
          value={`${(metrics.skip_rate_30min * 100).toFixed(1)}%`}
          sub="session skip ceiling: 15%"
          warn={metrics.skip_rate_30min > 0.12}
          accent="var(--clr-purple)"
        />
      </div>

      {/* ── Upload Rate Gauges ── */}
      <div className="fh-section">
        <h3 className="fh-section-title">Fleet Upload Rate</h3>
        <div className="fh-util-stack">
          <UtilBar
            label="10-min burst"
            used={metrics.upload_rate.uploads_10min}
            cap={metrics.upload_rate.cap_10min}
          />
          <UtilBar
            label="Hourly cap"
            used={metrics.upload_rate.uploads_1h}
            cap={metrics.upload_rate.cap_1h}
          />
        </div>
      </div>

      {/* ── Lifecycle Phase Distribution ── */}
      <div className="fh-section">
        <h3 className="fh-section-title">Lifecycle Phase Distribution</h3>
        <PhaseDistribution phases={metrics.lifecycle_phases} />
      </div>

      {/* ── Hard Caps Reference ── */}
      <details className="fh-caps-accordion">
        <summary className="fh-caps-summary">Hard Safety Caps (read-only)</summary>
        <div className="fh-caps-grid">
          {Object.entries(metrics.hard_caps).map(([key, val]) => (
            <div key={key} className="fh-cap-item">
              <span className="fh-cap-key">{key.replace(/_/g, " ")}</span>
              <span className="fh-cap-val">{val}</span>
            </div>
          ))}
        </div>
      </details>

      {/* ── Per-Account Table ── */}
      <div className="fh-section">
        <div className="fh-section-header">
          <h3 className="fh-section-title">Account Lifecycle Status</h3>
          <span className="fh-section-count">{accounts.length} accounts</span>
        </div>
        <div className="fh-acct-list">
          {accounts.length === 0 && (
            <div className="fh-empty">No accounts tracked yet. Start a session to populate.</div>
          )}
          {accounts.map(acct => (
            <AccountRow
              key={acct.account_id}
              acct={acct}
              onCooldown={(id, severe) => cooldownMutation.mutate({ id, severe })}
              onClear={(id) => clearMutation.mutate(id)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
