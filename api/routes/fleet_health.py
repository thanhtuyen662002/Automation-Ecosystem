"""
Fleet Health API — safety metrics dashboard endpoint.
Prefix: /api/v1/fleet-health  (register in api/main.py)

Exposes a single read-only snapshot of all safety-relevant fleet metrics:
  - Per-lifecycle-phase account distribution
  - Upload rate (10-min, hourly)
  - Safe-mode count
  - High-risk count
  - Anomaly rate
  - Coordinator state
  - Per-account lifecycle snapshot (for table view)

All data is read from in-memory singletons (no DB round-trip).
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.account_brain import get_brain_registry
from core.cross_account_coordinator import get_coordinator
from core.lifecycle_manager import get_lifecycle_manager

router = APIRouter(prefix="/fleet-health", tags=["fleet-health"])


# ── Response schemas ──────────────────────────────────────────────────────────

class LifecyclePhaseDistribution(BaseModel):
    WARM_UP:  int = 0
    RAMP_UP:  int = 0
    NORMAL:   int = 0
    COOLDOWN: int = 0


class UploadRateSnapshot(BaseModel):
    uploads_10min:     int
    uploads_1h:        int
    cap_10min:         int
    cap_1h:            int
    burst_utilisation: float   # 0–1
    hourly_utilisation: float  # 0–1


class FleetSafetyMetrics(BaseModel):
    # Counts
    total_accounts_tracked: int
    safe_mode_count:        int
    high_risk_count:        int
    cooldown_count:         int
    warm_up_count:          int
    suspended_upload_count: int
    # Rates
    avg_trust_score:        float
    avg_fatigue_level:      float
    anomaly_rate:           float        # fraction of accounts with consecutive_anomalies > 0
    # Upload health
    upload_rate:            UploadRateSnapshot
    # Phase breakdown
    lifecycle_phases:       LifecyclePhaseDistribution
    # Coordinator
    active_sessions:        int
    active_proxies:         int
    skip_rate_30min:        float
    # Hard caps (informational)
    hard_caps: dict[str, int | float]


class AccountLifecycleSummary(BaseModel):
    account_id:       str
    phase:            str
    sessions_today:   int
    uploads_today:    int
    cooldown_remaining_hours: float
    anomaly_count:    int
    account_age_days: float
    # Brain overlay
    trust_score:      float
    fatigue_level:    float
    operating_mode:   str
    risk_level:       str
    uploads_suspended: bool


class FleetHealthResponse(BaseModel):
    metrics:  FleetSafetyMetrics
    accounts: list[AccountLifecycleSummary]
    snapshot_ts: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _upload_rate_snapshot(coord_snap: dict) -> UploadRateSnapshot:
    """Build upload rate metrics from coordinator snapshot."""
    from core.cross_account_coordinator import (
        _UPLOAD_MAX_IN_WINDOW, _UPLOAD_MAX_PER_HOUR,
        _UPLOAD_WINDOW_SECS, _UPLOAD_HOURLY_WINDOW,
    )
    coord = get_coordinator()
    now   = time.monotonic()
    # Access private upload history directly (we own the singleton)
    u10   = sum(1 for r in coord._upload_history if now - r.ts <= _UPLOAD_WINDOW_SECS)
    u1h   = sum(1 for r in coord._upload_history if now - r.ts <= _UPLOAD_HOURLY_WINDOW)
    return UploadRateSnapshot(
        uploads_10min=u10,
        uploads_1h=u1h,
        cap_10min=_UPLOAD_MAX_IN_WINDOW,
        cap_1h=_UPLOAD_MAX_PER_HOUR,
        burst_utilisation=round(u10 / max(_UPLOAD_MAX_IN_WINDOW, 1), 3),
        hourly_utilisation=round(u1h / max(_UPLOAD_MAX_PER_HOUR, 1), 3),
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=FleetHealthResponse)
async def get_fleet_health() -> dict[str, Any]:
    """
    Return a full fleet safety snapshot.

    Combines LifecycleManager + AccountBrain + CrossAccountCoordinator state
    into a single response suited for the safety dashboard.
    """
    registry  = get_brain_registry()
    lifecycle = get_lifecycle_manager()
    coord     = get_coordinator()

    brain_snaps = registry.snapshot_all()     # list of brain state dicts
    lc_snaps    = {s["account_id"]: s for s in lifecycle.snapshot_all()}
    coord_snap  = coord.snapshot()

    # ── Per-account merged summaries ──────────────────────────────────────────
    account_summaries: list[AccountLifecycleSummary] = []
    phase_dist = {"WARM_UP": 0, "RAMP_UP": 0, "NORMAL": 0, "COOLDOWN": 0}

    for brain in brain_snaps:
        aid  = brain["account_id"]
        lc   = lc_snaps.get(aid, {})
        phase = lc.get("phase", "NORMAL")
        if phase in phase_dist:
            phase_dist[phase] += 1

        account_summaries.append(AccountLifecycleSummary(
            account_id=aid,
            phase=phase,
            sessions_today=lc.get("sessions_today", 0),
            uploads_today=lc.get("uploads_today", 0),
            cooldown_remaining_hours=lc.get("cooldown_remaining_hours", 0.0),
            anomaly_count=lc.get("anomaly_count", 0),
            account_age_days=lc.get("account_age_days", 0.0),
            trust_score=brain.get("trust_score", 0.0),
            fatigue_level=brain.get("fatigue_level", 0.0),
            operating_mode=brain.get("operating_mode", "NORMAL"),
            risk_level=brain.get("risk_level", "low"),
            uploads_suspended=brain.get("uploads_suspended", False),
        ))

    total = len(account_summaries)

    # ── Aggregate metrics ─────────────────────────────────────────────────────
    safe_mode_count     = sum(1 for a in account_summaries if a.operating_mode == "SAFE")
    high_risk_count     = sum(1 for a in account_summaries if a.risk_level == "high")
    cooldown_count      = sum(1 for a in account_summaries if a.phase == "COOLDOWN")
    warm_up_count       = sum(1 for a in account_summaries if a.phase == "WARM_UP")
    suspended_count     = sum(1 for a in account_summaries if a.uploads_suspended)
    anomaly_count       = sum(1 for b in brain_snaps if b.get("consecutive_anomalies", 0) > 0)

    avg_trust   = (sum(a.trust_score   for a in account_summaries) / total) if total else 0.0
    avg_fatigue = (sum(a.fatigue_level for a in account_summaries) / total) if total else 0.0
    anomaly_rate = anomaly_count / total if total else 0.0

    from core.cross_account_coordinator import (
        _MAX_SESSIONS_PER_ACCOUNT_PER_DAY, _MAX_UPLOADS_PER_ACCOUNT_PER_DAY,
        _UPLOAD_MAX_IN_WINDOW, _UPLOAD_MAX_PER_HOUR,
    )
    from core.lifecycle_manager import (
        _WARM_UP_DAYS, _RAMP_UP_DAYS, _MIN_SESSION_GAP_HOURS,
        _UPLOAD_MIN_TRUST, _UPLOAD_MAX_FATIGUE,
        _COOLDOWN_DURATION_HOURS, _COOLDOWN_SEVERE_HOURS,
    )

    metrics = FleetSafetyMetrics(
        total_accounts_tracked=total,
        safe_mode_count=safe_mode_count,
        high_risk_count=high_risk_count,
        cooldown_count=cooldown_count,
        warm_up_count=warm_up_count,
        suspended_upload_count=suspended_count,
        avg_trust_score=round(avg_trust, 3),
        avg_fatigue_level=round(avg_fatigue, 3),
        anomaly_rate=round(anomaly_rate, 3),
        upload_rate=_upload_rate_snapshot(coord_snap),
        lifecycle_phases=LifecyclePhaseDistribution(**phase_dist),
        active_sessions=coord_snap.get("active_jobs", 0),
        active_proxies=coord_snap.get("active_proxies", 0),
        skip_rate_30min=coord_snap.get("skip_rate_30min", 0.0),
        hard_caps={
            "max_sessions_per_account_per_day": _MAX_SESSIONS_PER_ACCOUNT_PER_DAY,
            "max_uploads_per_account_per_day":  _MAX_UPLOADS_PER_ACCOUNT_PER_DAY,
            "fleet_upload_cap_10min":           _UPLOAD_MAX_IN_WINDOW,
            "fleet_upload_cap_1h":              _UPLOAD_MAX_PER_HOUR,
            "min_session_gap_hours":            _MIN_SESSION_GAP_HOURS,
            "warm_up_days":                     _WARM_UP_DAYS,
            "ramp_up_days":                     _RAMP_UP_DAYS,
            "upload_min_trust":                 _UPLOAD_MIN_TRUST,
            "upload_max_fatigue":               _UPLOAD_MAX_FATIGUE,
            "cooldown_hours":                   _COOLDOWN_DURATION_HOURS,
            "cooldown_severe_hours":            _COOLDOWN_SEVERE_HOURS,
        },
    )

    from datetime import datetime, timezone
    return {
        "metrics":     metrics,
        "accounts":    account_summaries,
        "snapshot_ts": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/{account_id}/trigger-cooldown")
async def trigger_cooldown(account_id: str, severe: bool = False) -> dict[str, Any]:
    """Operator: manually put an account into cooldown."""
    lifecycle = get_lifecycle_manager()
    lifecycle.trigger_cooldown(account_id, reason="operator_manual", severe=severe)
    snap = lifecycle.snapshot(account_id) or {}
    return {
        "account_id":              account_id,
        "phase":                   snap.get("phase"),
        "cooldown_remaining_hours": snap.get("cooldown_remaining_hours"),
        "severe":                  severe,
    }


@router.post("/{account_id}/clear-cooldown")
async def clear_cooldown(account_id: str) -> dict[str, Any]:
    """Operator: clear cooldown and reset anomaly count for an account."""
    lifecycle = get_lifecycle_manager()
    lifecycle.clear_cooldown(account_id)
    snap = lifecycle.snapshot(account_id) or {}
    return {"account_id": account_id, "phase": snap.get("phase"), "anomaly_count": snap.get("anomaly_count")}


@router.get("/{account_id}/lifecycle")
async def get_account_lifecycle(account_id: str) -> dict[str, Any]:
    """Return full lifecycle state for a single account."""
    lifecycle = get_lifecycle_manager()
    snap = lifecycle.snapshot(account_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="Account not found in lifecycle manager")
    return snap
