"""
Account Lifecycle Manager — phase-aware account growth and cooldown controller.

Implements the four lifecycle phases that gate what an account is allowed to do
based on its age and health history. This is a HARD gate — not a suggestion.

Phases:
    WARM_UP   Days 0–6   Browse only, no uploads, 1 session/day max
    RAMP_UP   Days 7–20  Uploads allowed once per 3 days max
    NORMAL    Days 21+   Full behavior, still subject to all caps
    COOLDOWN  Any        Triggered by anomalies, pauses uploads for 48h+

Design contract:
    - LifecycleManager wraps AccountBrain decisions with phase-level constraints
    - Phase is derived from account_age_days (set once at account creation)
    - COOLDOWN overrides any phase based on consecutive_anomalies
    - All decisions are logged with structured reason strings

Integration:
    from core.lifecycle_manager import get_lifecycle_manager

    lm = get_lifecycle_manager()
    gate = lm.evaluate(account_id, state, plan)
    # gate.allowed_intent  — overridden if phase blocks it
    # gate.reason          — why the gate fired
    # gate.phase           — current lifecycle phase

    # After session:
    lm.record_session(account_id, uploaded=True, had_anomaly=False)
"""
from __future__ import annotations

import collections
import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

LOGGER = logging.getLogger("core.lifecycle_manager")

# ── Phase definitions ────────────────────────────────────────────────────────

LifecyclePhase = Literal["WARM_UP", "RAMP_UP", "NORMAL", "COOLDOWN"]

# Age thresholds (days since account was first registered in the system)
_WARM_UP_DAYS: int   = 7    # Days 0–6: browse only
_RAMP_UP_DAYS: int   = 21   # Days 7–20: limited uploads

# Hard activity caps per phase
_PHASE_MAX_SESSIONS_PER_DAY: dict[str, int] = {
    "WARM_UP":  1,
    "RAMP_UP":  2,
    "NORMAL":   3,   # Global hard ceiling — never exceeded regardless of trust
    "COOLDOWN": 1,
}
_PHASE_MAX_UPLOADS_PER_DAY: dict[str, int] = {
    "WARM_UP":  0,   # No uploads during warm-up
    "RAMP_UP":  1,
    "NORMAL":   1,   # Hard ceiling: 1 upload/account/day always
    "COOLDOWN": 0,
}
_PHASE_MIN_UPLOAD_GAP_DAYS: dict[str, float] = {
    "WARM_UP":  999.0,  # Effectively infinite
    "RAMP_UP":  3.0,    # Upload at most once per 3 days during ramp-up
    "NORMAL":   1.0,    # Once per day minimum gap
    "COOLDOWN": 999.0,
}

# Minimum time between sessions (all phases)
_MIN_SESSION_GAP_HOURS: float = 4.0   # Hard: never less than 4h between sessions

# Cooldown triggers
_COOLDOWN_ANOMALY_THRESHOLD: int   = 2      # anomalies before COOLDOWN
_COOLDOWN_DURATION_HOURS: float    = 48.0   # default COOLDOWN duration
_COOLDOWN_SEVERE_HOURS: float      = 72.0   # for soft_ban anomaly

# Upload trust gate (must satisfy BOTH)
_UPLOAD_MIN_TRUST: float   = 0.70   # trust_score must be ≥ this
_UPLOAD_MAX_FATIGUE: float = 0.70   # fatigue_level must be < this


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class AccountLifecycleState:
    """Persistent lifecycle metadata per account.

    Stored separately from AccountState so the lifecycle layer
    doesn't depend on brain internals — clean separation of concerns.
    """
    account_id:        str
    registered_at:     float = field(default_factory=time.time)  # Unix ts

    # Daily counters (reset at UTC midnight)
    sessions_today:    int   = 0
    uploads_today:     int   = 0
    last_counter_date: str   = ""   # ISO date string "YYYY-MM-DD"

    # Session timing
    last_session_at:   float | None = None
    last_upload_at:    float | None = None

    # Cooldown state
    cooldown_until:    float | None = None   # Unix ts; None = not in cooldown
    anomaly_count:     int          = 0

    def _today_iso(self) -> str:
        """Return today's date as ISO string, always in UTC to avoid timezone drift."""
        return datetime.now(timezone.utc).date().isoformat()

    def _refresh_daily_counters(self) -> None:
        """Reset counters if the calendar date has changed."""
        today = self._today_iso()
        if today != self.last_counter_date:
            self.sessions_today = 0
            self.uploads_today  = 0
            self.last_counter_date = today

    @property
    def account_age_days(self) -> float:
        return (time.time() - self.registered_at) / 86400.0

    @property
    def phase(self) -> LifecyclePhase:
        """Derive current lifecycle phase. COOLDOWN always wins."""
        if self.cooldown_until and time.time() < self.cooldown_until:
            return "COOLDOWN"
        age = self.account_age_days
        if age < _WARM_UP_DAYS:
            return "WARM_UP"
        if age < _RAMP_UP_DAYS:
            return "RAMP_UP"
        return "NORMAL"

    @property
    def in_cooldown(self) -> bool:
        return self.phase == "COOLDOWN"

    @property
    def cooldown_remaining_hours(self) -> float:
        if not self.cooldown_until:
            return 0.0
        return max(0.0, (self.cooldown_until - time.time()) / 3600.0)

    @property
    def hours_since_last_session(self) -> float | None:
        if self.last_session_at is None:
            return None
        return (time.time() - self.last_session_at) / 3600.0

    @property
    def days_since_last_upload(self) -> float | None:
        if self.last_upload_at is None:
            return None
        return (time.time() - self.last_upload_at) / 86400.0

    def to_dict(self) -> dict[str, Any]:
        self._refresh_daily_counters()
        return {
            "account_id":             self.account_id,
            "registered_at":          self.registered_at,
            "account_age_days":       round(self.account_age_days, 2),
            "phase":                  self.phase,
            "sessions_today":         self.sessions_today,
            "uploads_today":          self.uploads_today,
            "last_session_at":        self.last_session_at,
            "last_upload_at":         self.last_upload_at,
            "cooldown_until":         self.cooldown_until,
            "cooldown_remaining_hours": round(self.cooldown_remaining_hours, 1),
            "anomaly_count":          self.anomaly_count,
            "in_cooldown":            self.in_cooldown,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AccountLifecycleState":
        obj = cls(account_id=data["account_id"])
        obj.registered_at     = float(data.get("registered_at") or time.time())
        obj.sessions_today    = int(data.get("sessions_today") or 0)
        obj.uploads_today     = int(data.get("uploads_today") or 0)
        obj.last_counter_date = str(data.get("last_counter_date") or "")
        obj.last_session_at   = data.get("last_session_at")
        obj.last_upload_at    = data.get("last_upload_at")
        obj.cooldown_until    = data.get("cooldown_until")
        obj.anomaly_count     = int(data.get("anomaly_count") or 0)
        obj._refresh_daily_counters()
        return obj


@dataclass(frozen=True)
class LifecycleGate:
    """Result of a lifecycle evaluation.

    allowed:        False → session must not proceed (or must downgrade intent)
    allowed_intent: Potentially overridden intent (e.g. UPLOAD → BROWSE)
    phase:          Current lifecycle phase
    reason:         Structured explanation string for logging
    cap_hit:        Which cap was triggered (if any)
    """
    allowed:        bool
    allowed_intent: str                    # "UPLOAD" | "BROWSE" | "IDLE" | "SKIP"
    phase:          LifecyclePhase
    reason:         str
    cap_hit:        str | None = None


# ── Core evaluation logic ────────────────────────────────────────────────────

def evaluate_lifecycle_gate(
    lc: AccountLifecycleState,
    requested_intent: str,
    trust_score: float,
    fatigue_level: float,
) -> LifecycleGate:
    """Apply all lifecycle constraints to a requested session intent.

    Decision tree (first match wins):

    1. Min session gap (4h hard wall) → SKIP if too soon
    2. Max sessions per day (phase cap) → SKIP if daily cap hit
    3. Phase = WARM_UP and intent = UPLOAD → downgrade to BROWSE
    4. Phase = COOLDOWN → downgrade to BROWSE, no uploads
    5. Trust gate (trust < 0.70) → downgrade UPLOAD to BROWSE
    6. Fatigue gate (fatigue >= 0.70) → downgrade UPLOAD to BROWSE
    7. Upload frequency cap (phase: 1/day NORMAL, 1/3 days RAMP_UP) → downgrade
    8. Max uploads per day (hard) → downgrade UPLOAD to BROWSE
    9. Intent passes through

    Returns LifecycleGate — never raises.
    """
    lc._refresh_daily_counters()
    phase = lc.phase
    max_sessions = _PHASE_MAX_SESSIONS_PER_DAY.get(phase, 3)
    max_uploads  = _PHASE_MAX_UPLOADS_PER_DAY.get(phase, 1)
    min_gap_days = _PHASE_MIN_UPLOAD_GAP_DAYS.get(phase, 1.0)

    # ── 1. Minimum session gap (hard wall) ────────────────────────────────────
    hours_since = lc.hours_since_last_session
    if hours_since is not None and hours_since < _MIN_SESSION_GAP_HOURS:
        remaining = _MIN_SESSION_GAP_HOURS - hours_since
        return LifecycleGate(
            allowed=False,
            allowed_intent="SKIP",
            phase=phase,
            reason=(
                f"min_session_gap_not_met "
                f"(gap={hours_since:.1f}h < required={_MIN_SESSION_GAP_HOURS}h, "
                f"wait={remaining:.1f}h)"
            ),
            cap_hit="MIN_SESSION_GAP",
        )

    # ── 2. Daily session cap ──────────────────────────────────────────────────
    if lc.sessions_today >= max_sessions:
        return LifecycleGate(
            allowed=False,
            allowed_intent="SKIP",
            phase=phase,
            reason=(
                f"daily_session_cap_hit "
                f"(sessions_today={lc.sessions_today} >= max={max_sessions}, "
                f"phase={phase})"
            ),
            cap_hit="MAX_SESSIONS_PER_DAY",
        )

    # ── From here: session is allowed. Determine permitted intent ─────────────
    effective_intent = requested_intent

    # ── 3. WARM_UP blocks all uploads ────────────────────────────────────────
    if effective_intent == "UPLOAD" and phase == "WARM_UP":
        effective_intent = "BROWSE"
        return LifecycleGate(
            allowed=True,
            allowed_intent=effective_intent,
            phase=phase,
            reason=f"warm_up_no_uploads (age={lc.account_age_days:.1f} days, remaining={_WARM_UP_DAYS - lc.account_age_days:.1f} days)",
            cap_hit="PHASE_WARM_UP",
        )

    # ── 4. COOLDOWN blocks all uploads ───────────────────────────────────────
    if effective_intent == "UPLOAD" and phase == "COOLDOWN":
        effective_intent = "BROWSE"
        return LifecycleGate(
            allowed=True,
            allowed_intent=effective_intent,
            phase=phase,
            reason=f"cooldown_no_uploads (remaining={lc.cooldown_remaining_hours:.1f}h, anomalies={lc.anomaly_count})",
            cap_hit="PHASE_COOLDOWN",
        )

    # ── 5. Trust gate ─────────────────────────────────────────────────────────
    if effective_intent == "UPLOAD" and trust_score < _UPLOAD_MIN_TRUST:
        effective_intent = "BROWSE"
        return LifecycleGate(
            allowed=True,
            allowed_intent=effective_intent,
            phase=phase,
            reason=f"trust_gate_failed (trust={trust_score:.2f} < required={_UPLOAD_MIN_TRUST})",
            cap_hit="TRUST_GATE",
        )

    # ── 6. Fatigue gate ───────────────────────────────────────────────────────
    if effective_intent == "UPLOAD" and fatigue_level >= _UPLOAD_MAX_FATIGUE:
        effective_intent = "BROWSE"
        return LifecycleGate(
            allowed=True,
            allowed_intent=effective_intent,
            phase=phase,
            reason=f"fatigue_gate_failed (fatigue={fatigue_level:.2f} >= threshold={_UPLOAD_MAX_FATIGUE})",
            cap_hit="FATIGUE_GATE",
        )

    # ── 7. Upload frequency cap (phase-specific gap) ─────────────────────────
    if effective_intent == "UPLOAD":
        days_since_upload = lc.days_since_last_upload
        if days_since_upload is not None and days_since_upload < min_gap_days:
            remaining_days = min_gap_days - days_since_upload
            effective_intent = "BROWSE"
            return LifecycleGate(
                allowed=True,
                allowed_intent=effective_intent,
                phase=phase,
                reason=(
                    f"upload_frequency_cap "
                    f"(days_since={days_since_upload:.1f} < required={min_gap_days}, "
                    f"wait={remaining_days:.1f} days, phase={phase})"
                ),
                cap_hit="UPLOAD_FREQUENCY",
            )

    # ── 8. Daily upload hard cap ──────────────────────────────────────────────
    if effective_intent == "UPLOAD" and lc.uploads_today >= max_uploads:
        effective_intent = "BROWSE"
        return LifecycleGate(
            allowed=True,
            allowed_intent=effective_intent,
            phase=phase,
            reason=(
                f"daily_upload_cap_hit "
                f"(uploads_today={lc.uploads_today} >= max={max_uploads})"
            ),
            cap_hit="MAX_UPLOADS_PER_DAY",
        )

    # ── 9. Pass through ───────────────────────────────────────────────────────
    return LifecycleGate(
        allowed=True,
        allowed_intent=effective_intent,
        phase=phase,
        reason=f"lifecycle_gate_passed (phase={phase}, sessions_today={lc.sessions_today}/{max_sessions})",
        cap_hit=None,
    )


def trigger_cooldown(
    lc: AccountLifecycleState,
    reason: str,
    severe: bool = False,
    _increment_anomaly: bool = False,
) -> None:
    """Put an account into COOLDOWN. Mutates lc in-place.

    Args:
        lc:                 The lifecycle state to mutate.
        reason:             Reason string for the cooldown log.
        severe:             True → 72h cooldown (soft_ban); False → 48h.
        _increment_anomaly: Internal flag. ONLY set to True when called from
                            record_session_end(), which has NOT yet incremented
                            anomaly_count for the current event.  Operator calls
                            via LifecycleManager.trigger_cooldown() must leave
                            this False — anomaly_count is NOT an operator counter.
    """
    # NOTE: do NOT increment anomaly_count here by default. record_session_end()
    # already increments it before calling this function.  The _increment_anomaly
    # flag exists only to support the legacy direct-call path — it must remain False
    # in all production paths to avoid the double-increment bug.
    if _increment_anomaly:
        lc.anomaly_count += 1
    duration_hours = _COOLDOWN_SEVERE_HOURS if severe else _COOLDOWN_DURATION_HOURS
    lc.cooldown_until = time.time() + duration_hours * 3600
    LOGGER.warning(
        "lifecycle_cooldown_triggered",
        extra={
            "event":          "lifecycle_cooldown_triggered",
            "account_id":     lc.account_id,
            "reason":         reason,
            "duration_hours": duration_hours,
            "severe":         severe,
            "anomaly_count":  lc.anomaly_count,
            "phase":          lc.phase,
            "decision":       "COOLDOWN",
        },
    )


def record_session_start(lc: AccountLifecycleState) -> None:
    """Increment daily session counter. Call when a session begins."""
    lc._refresh_daily_counters()
    lc.sessions_today += 1
    lc.last_session_at = time.time()


def record_session_end(
    lc: AccountLifecycleState,
    uploaded: bool,
    had_anomaly: bool,
    severe_anomaly: bool = False,
) -> None:
    """Update lifecycle state after a session completes.

    Args:
        lc:             The AccountLifecycleState to mutate.
        uploaded:       True if an upload completed successfully.
        had_anomaly:    True if any anomaly signal was detected.
        severe_anomaly: True for soft_ban or repeated failures → 72h cooldown.

    Safety invariants enforced here (HARD GATE — lifecycle always wins):
        - uploads_today is incremented ONCE per uploaded=True call.
        - anomaly_count is incremented ONCE per had_anomaly=True call.
          trigger_cooldown() must NOT re-increment it.
    """
    lc._refresh_daily_counters()

    # ── Invariant guard: hard cap uploads at 1/day ────────────────────────────
    max_uploads = _PHASE_MAX_UPLOADS_PER_DAY.get(lc.phase, 1)
    if uploaded:
        if lc.uploads_today < max_uploads:
            lc.uploads_today += 1
            lc.last_upload_at = time.time()
        else:
            # Should not happen if evaluate() was called correctly; log CRITICAL.
            LOGGER.critical(
                "lifecycle_upload_invariant_violated",
                extra={
                    "event":        "lifecycle_upload_invariant_violated",
                    "account_id":   lc.account_id,
                    "uploads_today": lc.uploads_today,
                    "max_uploads":  max_uploads,
                    "phase":        lc.phase,
                    "decision":     "upload_count_clamped",
                },
            )

    # ── Anomaly handling: increment ONCE, then check threshold ───────────────
    if had_anomaly:
        lc.anomaly_count += 1   # ← Single increment point; trigger_cooldown does NOT re-increment
        if lc.anomaly_count >= _COOLDOWN_ANOMALY_THRESHOLD or severe_anomaly:
            trigger_cooldown(
                lc,
                reason=f"anomaly_threshold (count={lc.anomaly_count}, severe={severe_anomaly})",
                severe=severe_anomaly,
                _increment_anomaly=False,   # already incremented above
            )
    else:
        # Healthy session — gradually reset anomaly count
        lc.anomaly_count = max(0, lc.anomaly_count - 1)

    LOGGER.info(
        "lifecycle_session_recorded",
        extra={
            "event":          "lifecycle_session_recorded",
            "account_id":     lc.account_id,
            "uploaded":       uploaded,
            "had_anomaly":    had_anomaly,
            "phase":          lc.phase,
            "sessions_today": lc.sessions_today,
            "uploads_today":  lc.uploads_today,
            "anomaly_count":  lc.anomaly_count,
            "decision":       "session_end_recorded",
        },
    )


# ── Registry singleton ───────────────────────────────────────────────────────

class LifecycleManager:
    """Process-level store for all account lifecycle states.

    Follows the same singleton pattern as AccountBrainRegistry and
    CrossAccountCoordinator — one instance per worker process.

    Public API:
        evaluate(account_id, requested_intent, trust_score, fatigue_level)
        record_start(account_id)
        record_end(account_id, uploaded, had_anomaly, severe_anomaly)
        set_registered_at(account_id, registered_at_ts)
        trigger_cooldown(account_id, reason, severe)
        snapshot(account_id) → dict
        snapshot_all() → list[dict]
    """

    def __init__(self) -> None:
        self._states: dict[str, AccountLifecycleState] = {}

    def _get(self, account_id: str) -> AccountLifecycleState:
        if account_id not in self._states:
            self._states[account_id] = AccountLifecycleState(account_id=account_id)
            LOGGER.info(
                "lifecycle_state_created",
                extra={
                    "event":      "lifecycle_state_created",
                    "account_id": account_id,
                    "phase":      self._states[account_id].phase,
                },
            )
        return self._states[account_id]

    def set_registered_at(self, account_id: str, registered_at_ts: float) -> None:
        """Set the account's registration timestamp (must be called once at onboarding)."""
        lc = self._get(account_id)
        lc.registered_at = registered_at_ts
        LOGGER.info(
            "lifecycle_registered_at_set",
            extra={
                "event":            "lifecycle_registered_at_set",
                "account_id":       account_id,
                "account_age_days": round(lc.account_age_days, 2),
                "phase":            lc.phase,
            },
        )

    def evaluate(
        self,
        account_id: str,
        requested_intent: str,
        trust_score: float,
        fatigue_level: float,
    ) -> LifecycleGate:
        """Evaluate all lifecycle constraints for an account.

        Call BEFORE executing the session plan from AccountBrain.
        The returned gate's allowed_intent should replace the brain's intent.
        """
        lc = self._get(account_id)
        gate = evaluate_lifecycle_gate(lc, requested_intent, trust_score, fatigue_level)

        LOGGER.info(
            "lifecycle_gate_result",
            extra={
                "event":          "lifecycle_gate_result",
                "account_id":     account_id,
                "requested":      requested_intent,
                "allowed":        gate.allowed,
                "allowed_intent": gate.allowed_intent,
                "phase":          gate.phase,
                "cap_hit":        gate.cap_hit,
                "reason":         gate.reason,
            },
        )
        return gate

    def record_start(self, account_id: str) -> None:
        """Increment daily session counter. Call when a session begins."""
        record_session_start(self._get(account_id))

    def record_end(
        self,
        account_id: str,
        uploaded: bool,
        had_anomaly: bool,
        severe_anomaly: bool = False,
    ) -> None:
        """Update counters and trigger cooldown if warranted."""
        record_session_end(self._get(account_id), uploaded, had_anomaly, severe_anomaly)

    def trigger_cooldown(
        self,
        account_id: str,
        reason: str,
        severe: bool = False,
    ) -> None:
        """Manually trigger cooldown (e.g. operator action or external signal).

        NOTE: This does NOT increment anomaly_count — it is an operator action,
        not an anomaly signal.  Use record_end() for session-driven anomalies.
        """
        trigger_cooldown(self._get(account_id), reason=reason, severe=severe, _increment_anomaly=False)

    def clear_cooldown(self, account_id: str) -> None:
        """Operator: clear cooldown and reset anomaly count."""
        lc = self._get(account_id)
        lc.cooldown_until = None
        lc.anomaly_count  = 0
        LOGGER.info("lifecycle_cooldown_cleared", extra={
            "event": "lifecycle_cooldown_cleared", "account_id": account_id,
        })

    def snapshot(self, account_id: str) -> dict[str, Any] | None:
        if account_id not in self._states:
            return None
        return self._states[account_id].to_dict()

    def snapshot_all(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self._states.values()]

    def dump_states(self) -> dict[str, dict[str, Any]]:
        return {aid: s.to_dict() for aid, s in self._states.items()}

    def load_states(self, data: dict[str, dict[str, Any]]) -> None:
        for account_id, raw in data.items():
            if account_id not in self._states:
                try:
                    self._states[account_id] = AccountLifecycleState.from_dict(raw)
                except Exception as exc:
                    LOGGER.warning("lifecycle_load_failed", extra={
                        "event": "lifecycle_load_failed",
                        "account_id": account_id, "error": str(exc),
                    })


_LIFECYCLE_INSTANCE: LifecycleManager | None = None


def get_lifecycle_manager() -> LifecycleManager:
    """Return the process-level LifecycleManager singleton."""
    global _LIFECYCLE_INSTANCE
    if _LIFECYCLE_INSTANCE is None:
        _LIFECYCLE_INSTANCE = LifecycleManager()
        LOGGER.info("lifecycle_manager_initialised", extra={"event": "lifecycle_manager_initialised"})
    return _LIFECYCLE_INSTANCE
