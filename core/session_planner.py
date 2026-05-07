"""
Session Planner — unified risk-aware session planning entry point.

This module is the SINGLE integration layer that combines:
  1. LifecycleManager  — phase gates, per-day caps, trust/fatigue gates
  2. AccountBrain      — intent decision, fatigue model, anomaly memory
  3. CrossAccountCoordinator — fleet-level rate limiting, anti-sync, proxy load

Workers call plan_session() ONCE before starting any session.
It returns a SessionResult that specifies:
  - Whether to proceed or skip
  - The exact permitted intent (may be downgraded)
  - Session duration and interaction constraints
  - Full structured reason chain for audit logging

Design rules:
  - All three layers are ADDITIVE constraints (each can only restrict, never relax)
  - Every gate produces a structured log entry
  - SessionResult is immutable — workers must not deviate from it

Integration (publisher worker):
    from core.session_planner import get_session_planner, SessionOutcome

    planner = get_session_planner()
    result  = await planner.plan_session(account_id, proxy_url=proxy_url)

    if result.outcome == SessionOutcome.SKIP:
        return  # do nothing, log result.reason

    # Execute session using result.plan
    # ...

    await planner.record_session_end(
        account_id=account_id,
        proxy_url=proxy_url,
        uploaded=did_upload,
        had_anomaly=had_anomaly,
        severe_anomaly=soft_ban_detected,
        signals=session_signals,
    )
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from core.account_brain import (
    SessionPlan,
    SessionSignals,
    get_brain_registry,
)
from core.cross_account_coordinator import get_coordinator
from core.identity_manager import get_identity_registry
from core.lifecycle_manager import get_lifecycle_manager

LOGGER = logging.getLogger("core.session_planner")


# ── Types ─────────────────────────────────────────────────────────────────────

class SessionOutcome(str, Enum):
    PROCEED = "PROCEED"   # session should run with result.plan
    SKIP    = "SKIP"      # session must not start (cap hit or gap not met)
    DEGRADE = "DEGRADE"   # session starts but intent was downgraded (e.g. UPLOAD→BROWSE)


@dataclass(frozen=True)
class GateResult:
    """Single gate evaluation result — one per layer."""
    gate:       str   # "lifecycle" | "brain" | "coordinator"
    passed:     bool
    cap_hit:    str | None
    reason:     str


@dataclass(frozen=True)
class SessionResult:
    """Full planning output — the contract between planner and worker.

    Workers MUST NOT deviate from this contract:
      - If outcome == SKIP, the session must not start.
      - plan.intent is the final permitted intent (already downgraded if needed).
      - plan.session_duration_min is the hard ceiling.
      - plan.allowed_actions is the exact action whitelist.
    """
    account_id:     str
    outcome:        SessionOutcome
    plan:           SessionPlan | None          # None only when outcome == SKIP
    gates:          list[GateResult]
    reason:         str                         # summary reason string
    lifecycle_phase: str
    ts:             str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "event":           "session_planner_result",
            "account_id":      self.account_id,
            "outcome":         self.outcome.value,
            "lifecycle_phase": self.lifecycle_phase,
            "intent":          self.plan.intent if self.plan else "SKIP",
            "reason":          self.reason,
            "gates":           [
                {"gate": g.gate, "passed": g.passed, "cap_hit": g.cap_hit, "reason": g.reason}
                for g in self.gates
            ],
            "ts":              self.ts,
        }


# ── Session Planner ───────────────────────────────────────────────────────────

class SessionPlanner:
    """Process-level singleton that orchestrates all planning layers.

    Layer execution order (each layer can only restrict, never relax):
      0. IdentityManager: validate consistency → CRITICAL issues force SAFE mode
      1. Coordinator: check_session_allowed (daily session cap — safety net)
      2. Coordinator: get_start_delay (anti-sync jitter — async)
      3. LifecycleManager: evaluate (phase gate, 4h gap, daily caps, trust/fatigue)
      4. AccountBrain: decide_session_plan (intent, fatigue decay, mode)
      5. Coordinator: can_upload_now (fleet upload rate caps — if intent is UPLOAD)
    """

    async def plan_session(
        self,
        account_id: str,
        proxy_url: str | None = None,
        now: datetime | None = None,
    ) -> SessionResult:
        """Run all planning layers for one account session.

        This is the SINGLE call workers make before starting any session.
        Returns a SessionResult — the worker must not deviate from it.
        """
        if now is None:
            now = datetime.now()

        registry   = get_brain_registry()
        lifecycle  = get_lifecycle_manager()
        coord      = get_coordinator()
        identity   = get_identity_registry()
        gates: list[GateResult] = []

        # ── Layer 0: Identity consistency validation ──────────────────────────
        # Validates TZ/proxy/locale alignment and fingerprint stability.
        # CRITICAL issues force SAFE mode on AccountBrain for this session.
        id_issues  = identity.validate(account_id)
        has_critical = any(i.severity == "CRITICAL" for i in id_issues)
        if has_critical:
            registry.set_operating_mode(account_id, "SAFE")
            LOGGER.warning("session_planner_identity_critical", extra={
                "event":        "session_planner_identity_critical",
                "account_id":   account_id,
                "issues":       [{"code": i.code, "field": i.field, "severity": i.severity}
                                  for i in id_issues if i.severity == "CRITICAL"],
                "action":       "forced_safe_mode",
            })
        gates.append(GateResult(
            gate="identity",
            passed=not has_critical,
            cap_hit="IDENTITY_CRITICAL" if has_critical else None,
            reason=(
                f"identity_critical_issues={[i.code for i in id_issues if i.severity == 'CRITICAL']}"
                if has_critical else
                f"identity_ok (issues={len(id_issues)})"
            ),
        ))
        # Note: CRITICAL identity issues do NOT skip the session — they degrade it to SAFE.
        # The session still runs (browse only), ensuring the account stays active
        # without taking high-risk upload actions.

        # ── Layer 1: Coordinator daily session cap (safety net) ───────────────
        session_ok, session_reason = coord.check_session_allowed(account_id)
        gates.append(GateResult(
            gate="coordinator_session_cap",
            passed=session_ok,
            cap_hit="MAX_SESSIONS_PER_DAY" if not session_ok else None,
            reason=session_reason,
        ))
        if not session_ok:
            return self._skip(account_id, "COORDINATOR_SESSION_CAP", session_reason, gates, lifecycle)

        # ── Layer 2: Anti-sync start delay (async, must be awaited) ──────────
        start_delay = await coord.get_start_delay(account_id, proxy_url)
        if start_delay > 0:
            import asyncio
            LOGGER.info("session_planner_start_delay", extra={
                "event": "session_planner_start_delay",
                "account_id": account_id,
                "delay_secs": round(start_delay, 1),
            })
            await asyncio.sleep(start_delay)

        # ── Layer 3: LifecycleManager phase gate ──────────────────────────────
        state  = registry.get_state(account_id)
        lc_snap = lifecycle.snapshot(account_id)
        phase  = lc_snap["phase"] if lc_snap else "NORMAL"

        # Get brain's preliminary intent (before lifecycle constrains it)
        from core.account_brain import apply_fatigue_decay, decide_intent
        apply_fatigue_decay(state)
        raw_intent, _ = decide_intent(state, now)

        lc_gate = lifecycle.evaluate(
            account_id=account_id,
            requested_intent=raw_intent,
            trust_score=state.trust_score,
            fatigue_level=state.fatigue_level,
        )
        gates.append(GateResult(
            gate="lifecycle",
            passed=lc_gate.allowed and lc_gate.allowed_intent != "SKIP",
            cap_hit=lc_gate.cap_hit,
            reason=lc_gate.reason,
        ))

        if not lc_gate.allowed or lc_gate.allowed_intent == "SKIP":
            return self._skip(account_id, lc_gate.cap_hit or "LIFECYCLE_GATE", lc_gate.reason, gates, lifecycle)

        # If lifecycle downgraded intent, force it via one-shot override
        if lc_gate.allowed_intent != raw_intent:
            state.intent_override = lc_gate.allowed_intent  # type: ignore[assignment]

        # ── Layer 4: AccountBrain full session plan ───────────────────────────
        plan = registry.decide_session_plan(account_id, now=now)

        gates.append(GateResult(
            gate="brain",
            passed=True,
            cap_hit=None,
            reason=plan.intent_reason,
        ))

        # ── Layer 5: Fleet upload rate caps (if UPLOAD intent) ───────────────
        if plan.intent == "UPLOAD":
            upload_ok, upload_reason = coord.can_upload_now(account_id)
            gates.append(GateResult(
                gate="coordinator_upload_cap",
                passed=upload_ok,
                cap_hit="FLEET_UPLOAD_CAP" if not upload_ok else None,
                reason=upload_reason,
            ))
            if not upload_ok:
                # Downgrade to BROWSE — don't skip entirely
                LOGGER.warning("session_planner_upload_downgraded", extra={
                    "event":      "session_planner_upload_downgraded",
                    "account_id": account_id,
                    "reason":     upload_reason,
                })
                state.intent_override = "BROWSE"
                plan = registry.decide_session_plan(account_id, now=now)
        else:
            gates.append(GateResult(
                gate="coordinator_upload_cap",
                passed=True,
                cap_hit=None,
                reason=f"upload_cap_skipped (intent={plan.intent})",
            ))

        # ── All layers passed — register job start ────────────────────────────
        coord.register_job_start(account_id, proxy_url or "")
        lifecycle.record_start(account_id)

        outcome = (
            SessionOutcome.DEGRADE
            if lc_gate.allowed_intent != raw_intent or plan.intent != raw_intent
            else SessionOutcome.PROCEED
        )

        summary_reason = (
            f"phase={phase} intent={plan.intent} "
            f"trust={state.trust_score:.2f} fatigue={state.fatigue_level:.2f} "
            f"mode={plan.operating_mode}"
        )

        result = SessionResult(
            account_id=account_id,
            outcome=outcome,
            plan=plan,
            gates=gates,
            reason=summary_reason,
            lifecycle_phase=phase,
        )
        LOGGER.info("session_planner_proceed", extra=result.to_log_dict())
        return result

    async def record_session_end(
        self,
        account_id: str,
        proxy_url: str | None,
        uploaded: bool,
        had_anomaly: bool,
        severe_anomaly: bool = False,
        signals: SessionSignals | None = None,
        activity_level: str = "medium",
    ) -> dict[str, Any]:
        """Record session completion across all three layers.

        Always call in a finally block — even if the session errored.

        Args:
            account_id:     Account UUID.
            proxy_url:      Proxy used (empty string if none).
            uploaded:       True if an upload completed successfully.
            had_anomaly:    True if any anomaly signal fired.
            severe_anomaly: True for soft_ban/hard_fail → 72h cooldown.
            signals:        Full SessionSignals for AccountBrain feedback.
            activity_level: "low" | "medium" | "high" for personality balancer.
        """
        registry  = get_brain_registry()
        lifecycle = get_lifecycle_manager()
        coord     = get_coordinator()

        session_end_ts = time.time()

        # 1. Coordinator layer
        coord.register_job_end(
            account_id=account_id,
            proxy_url=proxy_url or "",
            uploaded=uploaded,
            activity_level=activity_level,
        )

        # 2. Lifecycle layer
        lifecycle.record_end(
            account_id=account_id,
            uploaded=uploaded,
            had_anomaly=had_anomaly,
            severe_anomaly=severe_anomaly,
        )

        # 3. AccountBrain layer
        if signals is None:
            signals = SessionSignals(
                uploaded=uploaded,
                captcha_hit=False,
                soft_ban_detected=severe_anomaly,
                upload_failed=not uploaded and had_anomaly,
            )
        brain_summary = registry.update_strategy(account_id, signals)
        registry.record_action(
            account_id=account_id,
            action_type=signals.intent,
            session_duration_min=signals.session_duration_min,
            uploaded=uploaded,
        )

        lc_snap = lifecycle.snapshot(account_id)
        summary = {
            "event":              "session_planner_end",
            "account_id":        account_id,
            "uploaded":          uploaded,
            "had_anomaly":       had_anomaly,
            "severe_anomaly":    severe_anomaly,
            "lifecycle_phase":   lc_snap["phase"] if lc_snap else "UNKNOWN",
            "sessions_today":    lc_snap["sessions_today"] if lc_snap else "?",
            "uploads_today":     lc_snap["uploads_today"] if lc_snap else "?",
            "new_trust":         round(brain_summary.get("new_trust_score", 0), 3),
            "new_mode":          brain_summary.get("new_operating_mode"),
            "anomalies":         brain_summary.get("anomalies", []),
            "ts":                datetime.now(timezone.utc).isoformat(),
        }
        LOGGER.info("session_planner_end", extra=summary)

        # Persist state after every session so daily caps survive process restarts
        from core.persistence import save_after_session
        save_after_session(account_id)

        return summary

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _skip(
        self,
        account_id: str,
        cap_hit: str,
        reason: str,
        gates: list[GateResult],
        lifecycle: Any,
    ) -> SessionResult:
        registry = get_brain_registry()
        state    = registry.get_state(account_id)
        from core.account_brain import _MODE_DELAY_MULTIPLIER
        idle_plan = SessionPlan(
            intent="IDLE",
            intent_reason=reason,
            session_duration_min=0.0,
            interaction_level="low",
            operating_mode=state.operating_mode,
            allowed_actions=["view"],
            delay_multiplier=_MODE_DELAY_MULTIPLIER.get(state.operating_mode, 1.0),
            risk_level=state.risk_level,
        )
        lc_snap = lifecycle.snapshot(account_id)
        result = SessionResult(
            account_id=account_id,
            outcome=SessionOutcome.SKIP,
            plan=idle_plan,
            gates=gates,
            reason=reason,
            lifecycle_phase=lc_snap["phase"] if lc_snap else "UNKNOWN",
        )
        LOGGER.info("session_planner_skip", extra={
            **result.to_log_dict(),
            "cap_hit": cap_hit,
        })
        return result


# ── Singleton ─────────────────────────────────────────────────────────────────

_PLANNER_INSTANCE: SessionPlanner | None = None


def get_session_planner() -> SessionPlanner:
    """Return the process-level SessionPlanner singleton."""
    global _PLANNER_INSTANCE
    if _PLANNER_INSTANCE is None:
        _PLANNER_INSTANCE = SessionPlanner()
        LOGGER.info("session_planner_initialised", extra={"event": "session_planner_initialised"})
    return _PLANNER_INSTANCE
