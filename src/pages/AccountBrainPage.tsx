import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Brain,
  CheckCircle2,
  ChevronDown,
  Clock,
  Eye,
  Fingerprint,
  Globe,
  Lock,
  Monitor,
  RefreshCw,
  RotateCcw,
  Shield,
  ShieldAlert,
  ShieldOff,
  Smartphone,
  TestTube,
  Upload,
  Zap,
  ZapOff,
} from "lucide-react";
import { api } from "@/services/api";
import type { AccountBrainState, BrainIntent, BrainOperatingMode, BrainRiskLevel } from "@/types/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toast";
import { PageHeader } from "@/components/PageHeader";
import { cn } from "@/lib/utils";

// ── Config maps ────────────────────────────────────────────────────────────────

const INTENT_CFG: Record<BrainIntent, { label: string; icon: React.ReactNode; bg: string; text: string; ring: string; pulse: boolean }> = {
  BROWSE: { label: "Browse", icon: <Eye className="h-3.5 w-3.5" />, bg: "bg-blue-500/15", text: "text-blue-500", ring: "ring-blue-500/30", pulse: false },
  UPLOAD: { label: "Upload", icon: <Upload className="h-3.5 w-3.5" />, bg: "bg-emerald-500/15", text: "text-emerald-500", ring: "ring-emerald-500/40", pulse: true },
  IDLE:   { label: "Idle",   icon: <ZapOff className="h-3.5 w-3.5" />, bg: "bg-amber-500/15",   text: "text-amber-500",   ring: "ring-amber-500/30",   pulse: false },
};

const MODE_CFG: Record<BrainOperatingMode, { label: string; icon: React.ReactNode; bg: string; text: string; ring: string }> = {
  SAFE:       { label: "SAFE",       icon: <ShieldAlert className="h-3 w-3" />, bg: "bg-red-500/15",    text: "text-red-500",    ring: "ring-red-500/30" },
  NORMAL:     { label: "NORMAL",     icon: <Shield className="h-3 w-3" />,      bg: "bg-blue-500/15",   text: "text-blue-500",   ring: "ring-blue-500/30" },
  AGGRESSIVE: { label: "AGGRESSIVE", icon: <Zap className="h-3 w-3" />,         bg: "bg-purple-500/15", text: "text-purple-500", ring: "ring-purple-500/30" },
};

const RISK_CFG: Record<BrainRiskLevel, { dot: string; text: string; label: string }> = {
  low:    { dot: "bg-emerald-500", text: "text-emerald-600 dark:text-emerald-400", label: "Low Risk" },
  medium: { dot: "bg-amber-500",   text: "text-amber-600 dark:text-amber-400",     label: "Med Risk" },
  high:   { dot: "bg-red-500",     text: "text-red-600 dark:text-red-400",         label: "High Risk" },
};

// ── Helpers ────────────────────────────────────────────────────────────────────

function relTime(min: number | null) {
  if (min === null) return "Never";
  if (min < 1) return "Just now";
  if (min < 60) return `${Math.round(min)}m ago`;
  return `${Math.round(min / 60)}h ago`;
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function ScoreBar({ value, label, colorFn }: { value: number; label: string; colorFn: (v: number) => string }) {
  const pct = Math.round(value * 100);
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className={cn("font-semibold tabular-nums", colorFn(value))}>{pct}%</span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
        <div className={cn("h-full rounded-full transition-all duration-700", colorFn(value).replace("text-", "bg-"))} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function Badge({ icon, label, bg, text, ring, pulse = false }: { icon?: React.ReactNode; label: string; bg: string; text: string; ring: string; pulse?: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold ring-1", bg, text, ring, pulse && "animate-pulse")}>
      {icon}{label}
    </span>
  );
}

// ── Identity Panel (Fingerprint Layer) ────────────────────────────────────────

// WebGL vendor lookup matches fingerprint_engine.py _WEBGL_PROFILES
const WEBGL_VENDOR: Record<string, string> = {
  iOS:     "Apple Inc.",
  macOS:   "Apple Inc.",
  Android: "Qualcomm",
  Windows: "Intel Inc.",
  Linux:   "Mesa/X.org",
};

function getOsFamily(os: string): string {
  for (const key of ["iOS", "macOS", "Android", "Windows", "Linux"]) {
    if (os.toLowerCase().includes(key.toLowerCase())) return key;
  }
  return "Windows";
}

function IdentityPanel({ accountId }: { accountId: string }) {
  const qc = useQueryClient();
  const [showRegen, setShowRegen] = useState(false);

  const { data: identity, isLoading } = useQuery({
    queryKey: ["identity", accountId],
    queryFn: () => api.getIdentity(accountId),
    retry: false,
  });

  const generate    = useMutation({ mutationFn: () => api.generateIdentity(accountId),  onSuccess: () => qc.invalidateQueries({ queryKey: ["identity", accountId] }) });
  const lock        = useMutation({ mutationFn: () => api.lockIdentity(accountId),      onSuccess: () => qc.invalidateQueries({ queryKey: ["identity", accountId] }) });
  const unlock      = useMutation({ mutationFn: () => api.unlockIdentity(accountId),    onSuccess: () => qc.invalidateQueries({ queryKey: ["identity", accountId] }) });
  const validate    = useMutation({ mutationFn: () => api.validateIdentity(accountId),  onSuccess: () => { toast({ title: "Identity validated" }); qc.invalidateQueries({ queryKey: ["identity", accountId] }); } });
  const regen       = useMutation({ mutationFn: () => api.regenerateIdentity(accountId), onSuccess: () => { toast({ title: "⚠️ Identity regenerated — trust score degraded" }); setShowRegen(false); qc.invalidateQueries({ queryKey: ["identity", accountId] }); }, onError: (e: Error) => toast({ title: e.message, variant: "destructive" }) });

  if (isLoading) {
    return (
      <div className="space-y-2 rounded-lg bg-muted/40 p-3">
        <div className="h-3 w-32 animate-pulse rounded bg-muted" />
        <div className="h-3 w-48 animate-pulse rounded bg-muted" />
      </div>
    );
  }

  if (!identity) {
    return (
      <div className="rounded-lg border border-dashed p-3 text-center">
        <p className="text-xs text-muted-foreground">No identity profile</p>
        <button onClick={() => generate.mutate()} disabled={generate.isPending}
          className="mt-1.5 text-xs text-primary hover:underline disabled:opacity-40">
          Initialize Identity
        </button>
      </div>
    );
  }

  const hasCritical   = identity.has_critical_issues;
  const riskScore     = identity.identity_risk_score;    // 0–1
  const isStable      = riskScore < 0.1;
  const isDrift       = hasCritical;
  const isMobile      = identity.device_type === "mobile";
  const osFamily      = getOsFamily(identity.os);
  const webglVendor   = WEBGL_VENDOR[osFamily] ?? "Intel Inc.";
  const fpPreview     = identity.fingerprint_hash.slice(0, 16);
  const fpStatus      = isDrift ? "Drift" : isStable ? "Stable" : "Warning";
  const fpStatusColor = isDrift ? "text-red-500" : isStable ? "text-emerald-500" : "text-amber-500";
  const fpStatusBg    = isDrift ? "bg-red-500/15" : isStable ? "bg-emerald-500/15" : "bg-amber-500/15";

  return (
    <div className={cn("space-y-2.5 rounded-lg border p-3 transition-colors", isDrift && "border-red-500/40 bg-red-500/5")}>
      {/* Header row */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <Fingerprint className={cn("h-3.5 w-3.5", fpStatusColor)} />
          <span className="text-xs font-semibold">Fingerprint</span>
          {identity.is_locked && <Lock className="h-3 w-3 text-muted-foreground" />}
        </div>
        <div className="flex items-center gap-1.5">
          {isStable && <CheckCircle2 className="h-3 w-3 text-emerald-500" />}
          {isDrift  && <AlertTriangle className="h-3 w-3 text-red-500" />}
          <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-semibold", fpStatusBg, fpStatusColor)}>
            {fpStatus}
          </span>
        </div>
      </div>

      {/* Detection risk score bar */}
      <div className="space-y-0.5">
        <div className="flex justify-between text-[10px]">
          <span className="text-muted-foreground">Detection Risk</span>
          <span className={cn("font-semibold", riskScore >= 0.6 ? "text-red-500" : riskScore >= 0.3 ? "text-amber-500" : "text-emerald-500")}>
            {Math.round(riskScore * 100)}%
          </span>
        </div>
        <div className="h-1 w-full overflow-hidden rounded-full bg-muted">
          <div
            className={cn("h-full rounded-full transition-all duration-700",
              riskScore >= 0.6 ? "bg-red-500" : riskScore >= 0.3 ? "bg-amber-500" : "bg-emerald-500")}
            style={{ width: `${Math.round(riskScore * 100)}%` }}
          />
        </div>
      </div>

      {/* Identity details grid */}
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
        <div className="flex items-center gap-1 text-muted-foreground">
          {isMobile ? <Smartphone className="h-3 w-3" /> : <Monitor className="h-3 w-3" />}
          <span className="truncate font-medium text-foreground/80">{identity.os}</span>
        </div>
        <div className="flex items-center gap-1 text-muted-foreground">
          <Globe className="h-3 w-3" />
          <span className="font-medium text-foreground/80">{identity.proxy_country ?? "No proxy"}</span>
        </div>
        <div className="text-muted-foreground">
          <span className="mr-1">TZ</span>
          <span className="font-medium text-foreground/80">
            {identity.timezone.split("/")[1]?.replace("_", " ") ?? identity.timezone}
          </span>
        </div>
        <div className="text-muted-foreground">
          <span className="mr-1">Lang</span>
          <span className="font-medium text-foreground/80">{identity.locale}</span>
        </div>
      </div>

      {/* Fingerprint detail chips */}
      <div className="flex flex-wrap gap-1">
        <span className="rounded bg-muted/70 px-1.5 py-0.5 text-[9px] font-mono text-muted-foreground">
          GPU: {webglVendor}
        </span>
        <span className="rounded bg-muted/70 px-1.5 py-0.5 text-[9px] font-mono text-muted-foreground">
          fp: {fpPreview}…
        </span>
        <span className={cn("rounded px-1.5 py-0.5 text-[9px] font-medium",
          isStable ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400" : "bg-amber-500/10 text-amber-500")}>
          canvas: {isStable ? "✓ stable" : "⚠ warn"}
        </span>
        <span className={cn("rounded px-1.5 py-0.5 text-[9px] font-medium",
          isStable ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400" : "bg-amber-500/10 text-amber-500")}>
          audio: {isStable ? "✓ stable" : "⚠ warn"}
        </span>
      </div>

      {/* Consistency issues */}
      {identity.consistency_issues.length > 0 && (
        <div className="space-y-1">
          {identity.consistency_issues.map((issue, i) => (
            <div key={i} className={cn("flex items-start gap-1.5 rounded px-2 py-1 text-[10px]",
              issue.severity === "CRITICAL" ? "bg-red-500/10 text-red-600 dark:text-red-400" : "bg-amber-500/10 text-amber-600 dark:text-amber-400")}>
              <AlertTriangle className="mt-px h-3 w-3 shrink-0" />
              <span>{issue.severity === "CRITICAL" ? "🚨" : "⚠️"} {issue.code.replace(/_/g, " ")}: {issue.message}</span>
            </div>
          ))}
        </div>
      )}

      {/* Controls */}
      <div className="flex flex-wrap gap-1.5">
        <button onClick={() => validate.mutate()} disabled={validate.isPending}
          className="inline-flex items-center gap-1 rounded bg-muted px-2 py-1 text-[10px] font-medium text-muted-foreground ring-1 ring-border hover:text-foreground disabled:opacity-40 transition-all">
          <TestTube className="h-3 w-3" />Test FP
        </button>
        <button onClick={() => (identity.is_locked ? unlock : lock).mutate()}
          className="inline-flex items-center gap-1 rounded bg-muted px-2 py-1 text-[10px] font-medium text-muted-foreground ring-1 ring-border hover:text-foreground disabled:opacity-40 transition-all">
          <Lock className="h-3 w-3" />{identity.is_locked ? "Unlock" : "Lock"}
        </button>
        {!identity.is_locked && (
          <button onClick={() => setShowRegen(v => !v)}
            className="inline-flex items-center gap-1 rounded bg-red-500/10 px-2 py-1 text-[10px] font-medium text-red-500 ring-1 ring-red-500/20 hover:opacity-80 transition-all">
            <RefreshCw className="h-3 w-3" />Regen…
          </button>
        )}
      </div>

      {/* Regen confirmation */}
      {showRegen && (
        <div className="rounded border border-red-500/30 bg-red-500/5 p-2 text-[10px]">
          <p className="text-red-600 dark:text-red-400 font-medium">⚠️ This changes the fingerprint hash. Trust score will drop.</p>
          <div className="mt-1.5 flex gap-1.5">
            <button onClick={() => regen.mutate()} disabled={regen.isPending}
              className="rounded bg-red-600 px-2 py-0.5 text-white hover:bg-red-700 disabled:opacity-40 transition-all">
              Confirm Regenerate
            </button>
            <button onClick={() => setShowRegen(false)}
              className="rounded bg-muted px-2 py-0.5 text-muted-foreground ring-1 ring-border hover:text-foreground transition-all">
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Account Brain Card ─────────────────────────────────────────────────────────

function BrainCard({ state }: { state: AccountBrainState }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const id = state.account_id;
  const onOk = () => qc.invalidateQueries({ queryKey: ["account-brain"] });
  const onErr = (msg: string) => () => toast({ title: msg, variant: "destructive" });

  const forceIntent = useMutation({ mutationFn: (intent: BrainIntent) => api.forceIntent(id, intent), onSuccess: onOk, onError: onErr("Failed") });
  const resetFat    = useMutation({ mutationFn: () => api.resetFatigue(id), onSuccess: onOk, onError: onErr("Failed") });
  const cReady      = useMutation({ mutationFn: (r: boolean) => api.setContentReady(id, r), onSuccess: onOk, onError: onErr("Failed") });
  const setMode     = useMutation({ mutationFn: (m: BrainOperatingMode | null) => api.setMode(id, m), onSuccess: onOk, onError: onErr("Failed") });
  const simAnomaly  = useMutation({ mutationFn: (k: string) => api.updateStrategy(id, { [k]: true, engagement_score: 0.1, intent: state.current_intent }), onSuccess: () => { toast({ title: "Anomaly simulated" }); onOk(); }, onError: onErr("Failed") });

  const busy = forceIntent.isPending || resetFat.isPending || cReady.isPending || setMode.isPending;
  const ic = INTENT_CFG[state.current_intent];
  const mc = MODE_CFG[state.operating_mode];
  const rc = RISK_CFG[state.risk_level];

  const accentColor = state.operating_mode === "SAFE" ? "from-red-400 to-red-600"
    : state.operating_mode === "AGGRESSIVE" ? "from-purple-400 to-purple-600"
    : state.current_intent === "UPLOAD" ? "from-emerald-400 to-emerald-600"
    : state.current_intent === "IDLE" ? "from-amber-400 to-amber-600"
    : "from-blue-400 to-blue-600";

  return (
    <Card className="overflow-hidden border transition-all hover:shadow-md hover:shadow-primary/5">
      <div className={cn("h-1 w-full bg-gradient-to-r transition-colors duration-500", accentColor)} />
      <CardContent className="p-4 space-y-3">
        {/* Header */}
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge icon={ic.icon} label={ic.label} bg={ic.bg} text={ic.text} ring={ic.ring} pulse={ic.pulse} />
          <Badge icon={mc.icon} label={mc.label} bg={mc.bg} text={mc.text} ring={mc.ring} />
          <span className={cn("inline-flex items-center gap-1 text-xs font-medium", rc.text)}>
            <span className={cn("h-1.5 w-1.5 rounded-full", rc.dot)} />
            {rc.label}
          </span>
          {state.uploads_suspended && (
            <span className="inline-flex items-center gap-1 rounded-full bg-red-500/15 px-2 py-0.5 text-xs font-medium text-red-500 ring-1 ring-red-500/30">
              <ShieldOff className="h-3 w-3" />Uploads Suspended
            </span>
          )}
        </div>

        <p className="font-mono text-[10px] text-muted-foreground/60 truncate">{id}</p>

        {/* Score bars */}
        <div className="space-y-2">
          <ScoreBar
            label={`Fatigue ${state.consecutive_anomalies > 0 ? `(${state.consecutive_anomalies} anomalies)` : ""}`}
            value={state.fatigue_level}
            colorFn={(v) => v >= 0.75 ? "text-red-500" : v >= 0.5 ? "text-amber-500" : "text-emerald-500"}
          />
          <ScoreBar
            label="Trust Score"
            value={state.trust_score}
            colorFn={(v) => v >= 0.7 ? "text-emerald-500" : v >= 0.4 ? "text-amber-500" : "text-red-500"}
          />
        </div>

        {/* Stats */}
        <div className="grid grid-cols-2 gap-2 text-xs">
          <div>
            <p className="text-muted-foreground/60">Window</p>
            <p className="flex items-center gap-1 font-semibold"><Clock className="h-3 w-3 text-muted-foreground" />{state.active_window}</p>
          </div>
          <div>
            <p className="text-muted-foreground/60">Last Active</p>
            <p className="font-semibold">{relTime(state.minutes_since_active)}</p>
          </div>
          <div>
            <p className="text-muted-foreground/60">Last Upload</p>
            <p className="font-semibold">{relTime(state.minutes_since_upload)}</p>
          </div>
          <div>
            <p className="text-muted-foreground/60">Streak</p>
            <p className="font-semibold">{state.activity_streak_days}d</p>
          </div>
        </div>

        {/* Reason */}
        <div className="rounded bg-muted/50 px-2.5 py-1.5 text-[11px] text-muted-foreground">
          <span className="font-medium text-foreground/80">Reason: </span>
          <span className="font-mono">{state.intent_reason}</span>
        </div>

        {/* Allowed actions */}
        <div className="flex flex-wrap gap-1">
          {state.allowed_actions.map((a) => (
            <span key={a} className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">{a}</span>
          ))}
          <span className="rounded bg-muted/50 px-1.5 py-0.5 text-[10px] text-muted-foreground/60">×{state.delay_multiplier.toFixed(1)} delay</span>
        </div>

        {/* Identity Panel */}
        <IdentityPanel accountId={id} />

        {/* Controls toggle */}
        <button onClick={() => setOpen(v => !v)} className="flex w-full items-center justify-between rounded border border-dashed px-2.5 py-1.5 text-xs text-muted-foreground hover:bg-muted/50 transition-colors">
          Controls
          <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-180")} />
        </button>

        {open && (
          <div className="space-y-3 border-t pt-3">
            {/* Force intent */}
            <div>
              <p className="mb-1 text-xs font-medium text-muted-foreground">Force Intent</p>
              <div className="flex flex-wrap gap-1.5">
                {(["BROWSE", "UPLOAD", "IDLE"] as BrainIntent[]).map((intent) => {
                  const c = INTENT_CFG[intent];
                  return (
                    <button key={intent} disabled={busy} onClick={() => forceIntent.mutate(intent)}
                      className={cn("inline-flex items-center gap-1 rounded px-2 py-1 text-xs font-medium ring-1 hover:opacity-80 disabled:opacity-40 transition-all", c.bg, c.text, c.ring)}>
                      {c.icon}{c.label}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Force mode */}
            <div>
              <p className="mb-1 text-xs font-medium text-muted-foreground">Operating Mode</p>
              <div className="flex flex-wrap gap-1.5">
                {(["SAFE", "NORMAL", "AGGRESSIVE"] as BrainOperatingMode[]).map((mode) => {
                  const c = MODE_CFG[mode];
                  const active = state.mode_override === mode;
                  return (
                    <button key={mode} disabled={busy}
                      onClick={() => setMode.mutate(active ? null : mode)}
                      className={cn("inline-flex items-center gap-1 rounded px-2 py-1 text-xs font-semibold ring-1 transition-all hover:opacity-80 disabled:opacity-40", c.bg, c.text, c.ring, active && "ring-2")}>
                      {c.icon}{c.label}{active && " ✓"}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Other controls */}
            <div className="flex flex-wrap gap-1.5">
              <button disabled={busy} onClick={() => resetFat.mutate()}
                className="inline-flex items-center gap-1 rounded bg-muted px-2 py-1 text-xs font-medium text-muted-foreground ring-1 ring-border hover:text-foreground disabled:opacity-40 transition-all">
                <RotateCcw className="h-3 w-3" />Reset Fatigue
              </button>
              <button disabled={busy} onClick={() => cReady.mutate(!state.content_ready)}
                className={cn("inline-flex items-center gap-1 rounded px-2 py-1 text-xs font-medium ring-1 transition-all hover:opacity-80 disabled:opacity-40",
                  state.content_ready ? "bg-purple-500/15 text-purple-500 ring-purple-500/30" : "bg-muted text-muted-foreground ring-border")}>
                <Upload className="h-3 w-3" />Content: {state.content_ready ? "Ready" : "Not Ready"}
              </button>
            </div>

            {/* Simulate anomaly (developer tool) */}
            <div>
              <p className="mb-1 text-xs font-medium text-muted-foreground">Simulate Anomaly</p>
              <div className="flex flex-wrap gap-1">
                {["captcha_hit", "action_blocked", "soft_ban_detected", "low_engagement"].map((k) => (
                  <button key={k} disabled={busy} onClick={() => simAnomaly.mutate(k)}
                    className="rounded bg-red-500/10 px-1.5 py-0.5 text-[10px] font-medium text-red-500 ring-1 ring-red-500/20 hover:opacity-80 disabled:opacity-40 transition-all">
                    {k.replace(/_/g, " ")}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Global Stats Panel ─────────────────────────────────────────────────────────

function GlobalStatsPanel({ states }: { states: AccountBrainState[] }) {
  const byMode = (m: BrainOperatingMode) => states.filter(s => s.operating_mode === m).length;
  const byRisk = (r: BrainRiskLevel) => states.filter(s => s.risk_level === r).length;
  const suspendedCount = states.filter(s => s.uploads_suspended).length;
  const avgTrust = states.length ? (states.reduce((a, s) => a + s.trust_score, 0) / states.length) : 0;

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
      {[
        { label: "SAFE Mode",   value: byMode("SAFE"),   color: "text-red-500",    bg: "bg-red-500/10" },
        { label: "NORMAL",      value: byMode("NORMAL"), color: "text-blue-500",   bg: "bg-blue-500/10" },
        { label: "AGGRESSIVE",  value: byMode("AGGRESSIVE"), color: "text-purple-500", bg: "bg-purple-500/10" },
        { label: "High Risk",   value: byRisk("high"),   color: "text-red-500",    bg: "bg-red-500/10" },
        { label: "Suspended",   value: suspendedCount,   color: "text-orange-500", bg: "bg-orange-500/10" },
        { label: "Avg Trust",   value: `${Math.round(avgTrust * 100)}%`, color: avgTrust > 0.6 ? "text-emerald-500" : "text-amber-500", bg: "bg-muted" },
      ].map(({ label, value, color, bg }) => (
        <div key={label} className={cn("rounded-lg px-3 py-2.5 text-center", bg)}>
          <p className={cn("text-lg font-bold tabular-nums", color)}>{value}</p>
          <p className="text-xs text-muted-foreground">{label}</p>
        </div>
      ))}
    </div>
  );
}

// ── Decision Log ───────────────────────────────────────────────────────────────

function DecisionLog() {
  const { data: entries = [] } = useQuery({
    queryKey: ["brain-log"],
    queryFn: () => api.getBrainDecisionLog(30),
    refetchInterval: 5000,
  });
  return (
    <div className="rounded-xl border bg-card">
      <div className="flex items-center justify-between border-b px-4 py-3">
        <div className="flex items-center gap-2"><Brain className="h-4 w-4 text-primary" /><span className="text-sm font-semibold">Decision Log</span></div>
        <span className="text-xs text-muted-foreground">Live · 5s</span>
      </div>
      <div className="max-h-64 divide-y overflow-y-auto">
        {entries.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">No decisions yet.</p>
        ) : entries.map((e, i) => {
          const intent = e.intent as BrainIntent;
          const ic = INTENT_CFG[intent];
          const mode = e.operating_mode as BrainOperatingMode | undefined;
          const mc = mode ? MODE_CFG[mode] : null;
          return (
            <div key={i} className="flex items-start gap-2.5 px-4 py-2.5">
              <div className={cn("mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs", ic?.bg, ic?.text)}>{ic?.icon}</div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5 flex-wrap">
                  <span className="font-mono text-xs font-semibold">{String(e.account_id ?? "").slice(0, 8)}…</span>
                  {ic && <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-medium", ic.bg, ic.text)}>{intent}</span>}
                  {mc && <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-medium", mc.bg, mc.text)}>{mode}</span>}
                  <span className="text-[10px] text-muted-foreground">trust={String(e.trust_score ?? "?")} fat={String(e.fatigue ?? "?")}</span>
                </div>
                <p className="truncate font-mono text-[10px] text-muted-foreground/70">{String(e.intent_reason ?? "—")}</p>
              </div>
              <span className="shrink-0 text-[10px] text-muted-foreground/50">{e.ts ? new Date(String(e.ts)).toLocaleTimeString() : ""}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────────────────────────

export function AccountBrainPage() {
  const qc = useQueryClient();
  const [autoRefresh, setAutoRefresh] = useState(true);

  const brainQ = useQuery({
    queryKey: ["account-brain"],
    queryFn: () => api.getAccountBrainAll(),
    refetchInterval: autoRefresh ? 6000 : false,
  });
  const accountsQ = useQuery({
    queryKey: ["accounts"],
    queryFn: () => api.getAccounts({ limit: 200 }),
  });

  const states = brainQ.data ?? [];
  const accounts = accountsQ.data?.items ?? [];
  const unseeded = accounts.filter(a => !states.find(s => s.account_id === a.id));

  const seedMut = useMutation({
    mutationFn: (id: string) => api.getAccountBrain(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["account-brain"] }),
  });

  const emergencyMut = useMutation({
    mutationFn: () => api.emergencySafeMode(),
    onSuccess: (d) => { toast({ title: `🚨 Emergency SAFE MODE: ${d.count} accounts locked` }); qc.invalidateQueries({ queryKey: ["account-brain"] }); },
    onError: () => toast({ title: "Failed", variant: "destructive" }),
  });

  const clearMut = useMutation({
    mutationFn: () => api.clearSafeMode(),
    onSuccess: (d) => { toast({ title: `SAFE mode cleared for ${d.count} accounts` }); qc.invalidateQueries({ queryKey: ["account-brain"] }); },
    onError: () => toast({ title: "Failed", variant: "destructive" }),
  });

  const safeModeCount = states.filter(s => s.mode_override === "SAFE").length;

  return (
    <div className="space-y-5">
      <PageHeader title="Account Brain" description="Intent + Memory + Adaptation — long-term survival, not randomness." />

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <label className="inline-flex h-8 cursor-pointer select-none items-center gap-2 rounded border bg-card px-3 text-xs">
          <input type="checkbox" checked={autoRefresh} onChange={e => setAutoRefresh(e.target.checked)} className="accent-primary" />
          Auto-refresh
        </label>
        <Button variant="outline" size="sm" onClick={() => qc.invalidateQueries({ queryKey: ["account-brain"] })} className="h-8 gap-1.5 text-xs">
          <RefreshCw className="h-3.5 w-3.5" />Refresh
        </Button>
        {unseeded.length > 0 && (
          <Button variant="outline" size="sm" onClick={() => unseeded.forEach(a => seedMut.mutate(a.id))} disabled={seedMut.isPending} className="h-8 gap-1.5 text-xs">
            <Brain className="h-3.5 w-3.5" />Load {unseeded.length} untracked
          </Button>
        )}

        <div className="ml-auto flex items-center gap-2">
          {/* Emergency controls */}
          {safeModeCount > 0 && (
            <Button variant="outline" size="sm" onClick={() => clearMut.mutate()} disabled={clearMut.isPending} className="h-8 gap-1.5 text-xs text-emerald-600 border-emerald-500/30 hover:bg-emerald-500/10">
              <Shield className="h-3.5 w-3.5" />Clear SAFE ({safeModeCount})
            </Button>
          )}
          <Button size="sm" onClick={() => emergencyMut.mutate()} disabled={emergencyMut.isPending}
            className="h-8 gap-1.5 bg-red-600 text-xs text-white hover:bg-red-700">
            <AlertTriangle className="h-3.5 w-3.5" />🚨 Emergency SAFE MODE
          </Button>
        </div>
      </div>

      {/* Fleet stats */}
      {states.length > 0 && <GlobalStatsPanel states={states} />}

      {/* Intent summary pills */}
      {states.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {(["BROWSE", "UPLOAD", "IDLE"] as BrainIntent[]).map((intent) => {
            const c = INTENT_CFG[intent];
            const count = states.filter(s => s.current_intent === intent).length;
            return (
              <div key={intent} className={cn("inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold ring-1", c.bg, c.text, c.ring)}>
                {c.icon}{count} {c.label}
              </div>
            );
          })}
          <span className="text-xs text-muted-foreground self-center ml-1">• {states.length} accounts tracked</span>
        </div>
      )}

      {/* Loading */}
      {brainQ.isLoading && (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {[1, 2, 3].map(i => <Skeleton key={i} className="h-72 rounded-xl" />)}
        </div>
      )}

      {/* Empty */}
      {!brainQ.isLoading && states.length === 0 && (
        <div className="flex flex-col items-center gap-4 rounded-xl border border-dashed py-16">
          <div className="flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
            <Brain className="h-7 w-7 text-primary" />
          </div>
          <div className="text-center">
            <p className="font-semibold">No accounts tracked</p>
            <p className="mt-1 text-sm text-muted-foreground">
              {accounts.length > 0 ? "Click below to initialize the brain." : "Add accounts first."}
            </p>
          </div>
          {accounts.length > 0 && (
            <Button onClick={() => accounts.forEach(a => seedMut.mutate(a.id))} disabled={seedMut.isPending} className="gap-2">
              <Brain className="h-4 w-4" />Initialize {accounts.length} Account{accounts.length !== 1 ? "s" : ""}
            </Button>
          )}
        </div>
      )}

      {/* Cards */}
      {states.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {states.map(s => <BrainCard key={s.account_id} state={s} />)}
        </div>
      )}

      <DecisionLog />
    </div>
  );
}
