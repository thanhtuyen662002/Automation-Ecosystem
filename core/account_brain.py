"""
Account Brain — intent-driven, memory-aware behavioral controller.

Design contract:
  - INTENT, not randomness: each session decision is derived from AccountState
  - MEMORY: AccountState persists across sessions (last action, fatigue, streak)
  - CONSISTENCY: same account → same time window → predictable but non-robotic behaviour
  - EXPLAINABLE: every decision emits a structured reason string

Architecture position:
                    ┌──────────────────┐
                    │  AccountBrain    │  ← decides WHAT / WHEN
                    └────────┬─────────┘
                             │ SessionPlan
                    ┌────────▼─────────┐
                    │  BehaviorEngine  │  ← decides HOW
                    └────────┬─────────┘
                             │ constraints
                    ┌────────▼─────────────────┐
                    │ CrossAccountCoordinator  │  ← fleet safety
                    └──────────────────────────┘

Integration (publisher):
    from core.account_brain import get_brain_registry, SessionPlan

    registry = get_brain_registry()
    plan = registry.decide_session_plan(account_id, now=datetime.now())

    # Use plan.interaction_level as activity_level override for BehaviorEngine
    engine = create_behavior_engine(account_id, account_data, ...)

    # After session completes:
    registry.record_action(
        account_id,
        action_type=plan.intent,
        session_duration_min=elapsed_min,
        uploaded=did_upload,
    )
"""
from __future__ import annotations

import collections
import hashlib
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Literal

LOGGER = logging.getLogger("core.account_brain")

# ── Typing ────────────────────────────────────────────────────────────────────

Intent = Literal["BROWSE", "UPLOAD", "IDLE"]
InteractionLevel = Literal["low", "medium", "high"]
OperatingMode = Literal["SAFE", "NORMAL", "AGGRESSIVE"]
RiskLevel = Literal["low", "medium", "high"]

# ── Constants ─────────────────────────────────────────────────────────────────

# Fatigue
_FATIGUE_PER_MINUTE: float = 0.015
_FATIGUE_DECAY_PER_HOUR: float = 0.04
_FATIGUE_HIGH_THRESHOLD: float = 0.75

# Timing
_POST_UPLOAD_COOLDOWN_MINUTES: float = 30.0
_RETURNING_USER_GAP_HOURS: float = 4.0
_MIN_UPLOAD_GAP_HOURS: float = 2.0

# Upload trust/fatigue gate — must satisfy BOTH (mirrors lifecycle_manager)
# NOTE: lifecycle_manager enforces these too; AccountBrain is a second layer
_UPLOAD_MIN_TRUST: float   = 0.70   # trust_score must be >= this to upload
_UPLOAD_MAX_FATIGUE: float = 0.70   # fatigue_level must be < this to upload

# Session duration base by operating mode (minutes)
_MODE_BASE_DURATION: dict[str, float] = {"SAFE": 6.0, "NORMAL": 12.0, "AGGRESSIVE": 20.0}
_MODE_DELAY_MULTIPLIER: dict[str, float] = {"SAFE": 2.0, "NORMAL": 1.0, "AGGRESSIVE": 0.85}

# Allowed actions per mode
_MODE_ALLOWED_ACTIONS: dict[str, list[str]] = {
    "SAFE": ["browse", "scroll", "view"],
    "NORMAL": ["browse", "scroll", "view", "like", "comment"],
    "AGGRESSIVE": ["browse", "scroll", "view", "like", "comment", "upload", "interact"],
}

_FATIGUE_DURATION_MODIFIER: list[tuple[float, float]] = [
    (0.0, 1.0), (0.25, 0.85), (0.50, 0.65), (0.75, 0.30),
]

_RECENT_ACTIONS_MAX: int = 10
_SESSION_HISTORY_MAX: int = 10

# Trust score penalties per anomaly type
_TRUST_PENALTY: dict[str, float] = {
    "captcha": 0.15, "action_blocked": 0.10, "soft_ban": 0.25,
    "low_engagement": 0.05, "upload_failed": 0.08,
}
_TRUST_RECOVERY_PER_HEALTHY_SESSION: float = 0.02
_UPLOAD_SUSPEND_HOURS: float = 24.0

# Stable time-window seed parameters
_WINDOW_HOUR_LO: int = 16
_WINDOW_HOUR_HI: int = 23
_WINDOW_SPAN_LO: int = 2
_WINDOW_SPAN_HI: int = 5


# ─────────────────────────────────────────────────────────────────────────────
# Stable seed (reuse behavior_engine pattern)
# ─────────────────────────────────────────────────────────────────────────────

def _stable_seed(account_id: str) -> int:
    """Derive a stable integer seed from account_id alone (not day-dependent).

    This ensures the preferred_time_window is fixed per account — like a real
    person who always comes online in the same evening window.
    """
    digest = hashlib.sha256(account_id.encode()).hexdigest()
    return int(digest[:16], 16)


def _seeded_int(seed: int, index: int, lo: int, hi: int) -> int:
    """Deterministically pick an int in [lo, hi] from seed + index."""
    h = hashlib.sha256(f"{seed}:{index}".encode()).hexdigest()
    unit = int(h[:8], 16) / 0xFFFFFFFF
    return lo + int(unit * (hi - lo + 1))


# ─────────────────────────────────────────────────────────────────────────────
# AccountState — persistent memory per account
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AccountState:
    """Per-account memory — trust, fatigue, history, risk, and intent."""

    account_id: str

    # Core memory
    last_active_at: float | None = None
    last_upload_at: float | None = None
    recent_actions: list[str] = field(default_factory=list)
    activity_streak_days: int = 0
    fatigue_level: float = 0.0

    # NEW: Trust & risk
    trust_score: float = 0.80          # 0=untrusted, 1=fully trusted
    consecutive_anomalies: int = 0
    uploads_suspended_until: float | None = None  # Unix ts; uploads blocked until then

    # Session history (last 10 sessions as plain dicts for JSON-safety)
    session_history: list[dict[str, Any]] = field(default_factory=list)

    # Time window
    preferred_hour_start: int = 18
    preferred_hour_end: int = 22

    # Flags
    content_ready: bool = False
    intent_override: Intent | None = None
    mode_override: OperatingMode | None = None  # operator-forced mode (persistent)

    # ── Derived properties ────────────────────────────────────────────────────

    @property
    def risk_level(self) -> RiskLevel:
        if self.consecutive_anomalies >= 3 or self.trust_score < 0.30:
            return "high"
        if self.consecutive_anomalies >= 1 or self.trust_score < 0.60:
            return "medium"
        return "low"

    @property
    def operating_mode(self) -> OperatingMode:
        if self.mode_override:
            return self.mode_override
        if self.risk_level == "high" or self.trust_score < 0.30:
            return "SAFE"
        if self.risk_level == "medium" or self.trust_score < 0.65:
            return "NORMAL"
        # AGGRESSIVE only when trust is high AND no recent anomalies
        if self.trust_score > 0.85 and self.consecutive_anomalies == 0:
            return "AGGRESSIVE"
        return "NORMAL"

    @property
    def uploads_suspended(self) -> bool:
        if self.uploads_suspended_until is None:
            return False
        return time.time() < self.uploads_suspended_until

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "last_active_at": self.last_active_at,
            "last_upload_at": self.last_upload_at,
            "recent_actions": list(self.recent_actions),
            "activity_streak_days": self.activity_streak_days,
            "fatigue_level": round(self.fatigue_level, 4),
            "trust_score": round(self.trust_score, 4),
            "consecutive_anomalies": self.consecutive_anomalies,
            "uploads_suspended_until": self.uploads_suspended_until,
            "session_history": list(self.session_history),
            "preferred_hour_start": self.preferred_hour_start,
            "preferred_hour_end": self.preferred_hour_end,
            "content_ready": self.content_ready,
            "intent_override": self.intent_override,
            "mode_override": self.mode_override,
            # Derived
            "risk_level": self.risk_level,
            "operating_mode": self.operating_mode,
            "uploads_suspended": self.uploads_suspended,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AccountState":
        return cls(
            account_id=data["account_id"],
            last_active_at=data.get("last_active_at"),
            last_upload_at=data.get("last_upload_at"),
            recent_actions=list(data.get("recent_actions") or []),
            activity_streak_days=int(data.get("activity_streak_days") or 0),
            fatigue_level=float(data.get("fatigue_level") or 0.0),
            trust_score=float(data.get("trust_score") or 0.80),
            consecutive_anomalies=int(data.get("consecutive_anomalies") or 0),
            uploads_suspended_until=data.get("uploads_suspended_until"),
            session_history=list(data.get("session_history") or []),
            preferred_hour_start=int(data.get("preferred_hour_start") or 18),
            preferred_hour_end=int(data.get("preferred_hour_end") or 22),
            content_ready=bool(data.get("content_ready", False)),
            intent_override=data.get("intent_override"),
            mode_override=data.get("mode_override"),
        )

    @classmethod
    def new_for_account(cls, account_id: str) -> "AccountState":
        seed = _stable_seed(account_id)
        start = _seeded_int(seed, 10, _WINDOW_HOUR_LO, _WINDOW_HOUR_HI - _WINDOW_SPAN_LO)
        span = _seeded_int(seed, 11, _WINDOW_SPAN_LO, _WINDOW_SPAN_HI)
        return cls(
            account_id=account_id,
            preferred_hour_start=start,
            preferred_hour_end=min(start + span, 23),
        )


# ─────────────────────────────────────────────────────────────────────────────
# SessionSignals — feedback from a completed session
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SessionSignals:
    """Anomaly signals collected after a session ends.

    Pass to update_account_strategy() to adapt the account's behavior.
    All flags default to False / healthy so callers only set what they detect.

    Identity fields (Layer 4 — Fingerprint integration):
        ip_changed          True if the proxy IP differed from last session.
        fingerprint_changed True if runtime fingerprint != stored hash.
        geo_mismatch        True if platform detected geo inconsistency.
        device_mismatch     True if detected device type != profile device_type.
    """
    # Session outcome
    captcha_hit: bool = False
    action_blocked: bool = False
    soft_ban_detected: bool = False
    low_engagement: bool = False
    upload_failed: bool = False
    engagement_score: float = 1.0
    session_duration_min: float = 0.0
    uploaded: bool = False
    intent: str = "BROWSE"
    # Identity / fingerprint signals
    ip_changed: bool = False
    fingerprint_changed: bool = False
    geo_mismatch: bool = False
    device_mismatch: bool = False
    identity_risk_score: float = 0.0   # from IdentityRegistry.validate()


# ─────────────────────────────────────────────────────────────────────────────
# SessionPlan — output of the brain's decision engine
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SessionPlan:
    """Full session prescription from the brain."""

    intent: Intent
    intent_reason: str
    session_duration_min: float
    interaction_level: InteractionLevel
    operating_mode: OperatingMode
    allowed_actions: list[str]
    delay_multiplier: float
    risk_level: RiskLevel

    def summary(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "intent_reason": self.intent_reason,
            "session_duration_min": round(self.session_duration_min, 1),
            "interaction_level": self.interaction_level,
            "operating_mode": self.operating_mode,
            "allowed_actions": self.allowed_actions,
            "delay_multiplier": round(self.delay_multiplier, 2),
            "risk_level": self.risk_level,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Core decision functions
# ─────────────────────────────────────────────────────────────────────────────

def _is_in_active_window(state: AccountState, now: datetime) -> bool:
    """Return True if `now` falls within the account's preferred active hours."""
    h = now.hour
    if state.preferred_hour_start <= state.preferred_hour_end:
        return state.preferred_hour_start <= h < state.preferred_hour_end
    # Handles wrap-around (e.g. 22:00–02:00)
    return h >= state.preferred_hour_start or h < state.preferred_hour_end


def _minutes_since(ts: float | None) -> float | None:
    """Return minutes elapsed since a Unix timestamp, or None if ts is None."""
    if ts is None:
        return None
    return (time.time() - ts) / 60.0


def _hours_since(ts: float | None) -> float | None:
    if ts is None:
        return None
    return (time.time() - ts) / 3600.0


def decide_intent(state: AccountState, now: datetime) -> tuple[Intent, str]:
    """Determine what the account should do next.

    Decision tree (priority order, first match wins):
      1. Operator override
      2. SAFE mode -> BROWSE (never UPLOAD in SAFE)
      3. High fatigue -> IDLE
      4. Outside active window -> IDLE
      5. Uploads suspended (post-anomaly ban) -> BROWSE
      6. High risk -> BROWSE (no upload)
      7. content_ready + sufficient gap + not high risk -> UPLOAD
      8. Post-upload cooldown -> BROWSE
      9. Returning user (long gap) -> BROWSE
      10. Default -> BROWSE
    """
    # 1. Operator intent override (one-shot)
    if state.intent_override is not None:
        return state.intent_override, "operator_override"

    mode = state.operating_mode

    # 2. SAFE mode forces browse (never upload)
    if mode == "SAFE":
        if state.fatigue_level >= _FATIGUE_HIGH_THRESHOLD:
            return "IDLE", f"safe_mode+fatigue_high (fatigue={state.fatigue_level:.2f})"
        return "BROWSE", f"safe_mode_forced_browse (risk={state.risk_level}, trust={state.trust_score:.2f})"

    # 3. High fatigue
    if state.fatigue_level >= _FATIGUE_HIGH_THRESHOLD:
        return "IDLE", f"fatigue_high (fatigue={state.fatigue_level:.2f})"

    # 4. Outside active window
    if not _is_in_active_window(state, now):
        return "IDLE", (
            f"out_of_active_window "
            f"(window={state.preferred_hour_start:02d}:00–{state.preferred_hour_end:02d}:00, "
            f"now={now.hour:02d}:{now.minute:02d})"
        )

    # 5. Upload suspension (post-anomaly)
    if state.uploads_suspended:
        remaining_h = ((state.uploads_suspended_until or 0) - time.time()) / 3600
        return "BROWSE", f"uploads_suspended ({remaining_h:.1f}h remaining, anomalies={state.consecutive_anomalies})"

    # 6. High risk → no upload
    if state.risk_level == "high":
        return "BROWSE", f"high_risk_no_upload (trust={state.trust_score:.2f}, anomalies={state.consecutive_anomalies})"

    # 7. Upload trust gate: trust >= 0.70 AND fatigue < 0.70 required
    if state.content_ready:
        if state.trust_score < _UPLOAD_MIN_TRUST:
            return "BROWSE", (
                f"upload_trust_gate_failed "
                f"(trust={state.trust_score:.2f} < required={_UPLOAD_MIN_TRUST})"
            )
        if state.fatigue_level >= _UPLOAD_MAX_FATIGUE:
            return "BROWSE", (
                f"upload_fatigue_gate_failed "
                f"(fatigue={state.fatigue_level:.2f} >= threshold={_UPLOAD_MAX_FATIGUE})"
            )

    # 8. Content ready + gap OK + trust/fatigue gates passed → UPLOAD
    hours_since_upload = _hours_since(state.last_upload_at)
    if state.content_ready and (hours_since_upload is None or hours_since_upload >= _MIN_UPLOAD_GAP_HOURS):
        return "UPLOAD", (
            f"content_ready+trust_ok+fatigue_ok+active_time "
            f"(gap={f'{hours_since_upload:.1f}h' if hours_since_upload else 'never'}, "
            f"trust={state.trust_score:.2f}, fatigue={state.fatigue_level:.2f})"
        )

    # 9. Post-upload cooldown
    minutes_since_upload = _minutes_since(state.last_upload_at)
    if minutes_since_upload is not None and minutes_since_upload < _POST_UPLOAD_COOLDOWN_MINUTES:
        return "BROWSE", (
            f"post_upload_cooldown "
            f"(uploaded {minutes_since_upload:.0f} min ago, cooldown={_POST_UPLOAD_COOLDOWN_MINUTES:.0f} min)"
        )

    # 10. Returning user
    hours_since_active = _hours_since(state.last_active_at)
    if hours_since_active is None or hours_since_active >= _RETURNING_USER_GAP_HOURS:
        return "BROWSE", f"returning_user (last_active={f'{hours_since_active:.1f}h ago' if hours_since_active else 'never'})"

    return "BROWSE", "default_browse"


def _compute_session_duration(state: AccountState, intent: Intent) -> float:
    if intent == "IDLE":
        return 0.0
    base = _MODE_BASE_DURATION.get(state.operating_mode, 12.0)
    modifier = 1.0
    for threshold, mod in reversed(_FATIGUE_DURATION_MODIFIER):
        if state.fatigue_level >= threshold:
            modifier = mod
            break
    duration = base * modifier
    if intent == "UPLOAD":
        duration *= 1.25
    return round(min(max(duration, 5.0), 20.0), 1)  # hard bounds: 5–20 min


def _compute_interaction_level(state: AccountState, intent: Intent) -> InteractionLevel:
    if intent == "IDLE":
        return "low"
    mode = state.operating_mode
    if mode == "SAFE":
        return "low"
    if intent == "UPLOAD":
        return "medium" if state.fatigue_level > 0.5 else "high"
    if state.fatigue_level > 0.6 or mode == "NORMAL":
        return "low" if state.fatigue_level > 0.6 else "medium"
    return "medium"


def decide_session_plan(state: AccountState, now: datetime | None = None) -> SessionPlan:
    """Full session prescription: intent + mode + actions + delay multiplier."""
    if now is None:
        now = datetime.now()
    intent, reason = decide_intent(state, now)
    duration = _compute_session_duration(state, intent)
    level = _compute_interaction_level(state, intent)
    mode = state.operating_mode
    allowed = list(_MODE_ALLOWED_ACTIONS.get(mode, _MODE_ALLOWED_ACTIONS["NORMAL"]))
    # If intent is IDLE, always restrict to view-only
    if intent == "IDLE":
        allowed = ["view"]
    delay_mult = _MODE_DELAY_MULTIPLIER.get(mode, 1.0)
    # Extra delay when fatigued
    if state.fatigue_level > 0.5:
        delay_mult *= (1.0 + state.fatigue_level * 0.5)
    return SessionPlan(
        intent=intent,
        intent_reason=reason,
        session_duration_min=duration,
        interaction_level=level,
        operating_mode=mode,
        allowed_actions=allowed,
        delay_multiplier=round(delay_mult, 2),
        risk_level=state.risk_level,
    )


# ─────────────────────────────────────────────────────────────────────────────
# update_account_strategy — adaptive feedback loop (Layer 4)
# ─────────────────────────────────────────────────────────────────────────────

def update_account_strategy(
    state: AccountState,
    signals: SessionSignals,
) -> dict[str, Any]:
    """Adaptive learning: update trust_score and behavior based on session outcome.

    This is the core feedback loop. Call after every session ends.
    Anomalies degrade trust; healthy sessions slowly recover it.
    Severe anomalies (soft_ban) trigger an upload suspension.

    Returns a summary dict for structured logging.
    """
    anomalies: list[str] = []
    penalty_total = 0.0

    anomaly_map = {
        "captcha": signals.captcha_hit,
        "action_blocked": signals.action_blocked,
        "soft_ban": signals.soft_ban_detected,
        "low_engagement": signals.low_engagement,
        "upload_failed": signals.upload_failed,
        # Identity signals
        "fingerprint_changed": signals.fingerprint_changed,
        "geo_mismatch": signals.geo_mismatch,
        "ip_changed": signals.ip_changed,
        "device_mismatch": signals.device_mismatch,
    }
    for name, triggered in anomaly_map.items():
        if triggered:
            anomalies.append(name)
            penalty = _TRUST_PENALTY.get(name, 0.05)
            penalty_total += penalty
            state.trust_score = max(0.0, state.trust_score - penalty)

    # Soft ban triggers a temporary upload suspension
    if signals.soft_ban_detected:
        state.uploads_suspended_until = time.time() + _UPLOAD_SUSPEND_HOURS * 3600

    # Identity-specific penalties
    if signals.fingerprint_changed:
        state.trust_score = max(0.0, state.trust_score - 0.25)
        state.consecutive_anomalies += 1          # extra increment for severity
    if signals.geo_mismatch and not state.mode_override:
        state.mode_override = "SAFE"              # force SAFE MODE on geo mismatch
    if signals.device_mismatch:
        # Don't ban, but reduce interaction capacity via trust
        state.trust_score = max(0.0, state.trust_score - 0.10)
    # Blend identity_risk_score into trust (weighted 30%)
    if signals.identity_risk_score > 0:
        identity_penalty = signals.identity_risk_score * 0.30
        state.trust_score = max(0.0, state.trust_score - identity_penalty)

    if anomalies:
        state.consecutive_anomalies += 1
    else:
        # Healthy session: recover trust gradually
        state.consecutive_anomalies = max(0, state.consecutive_anomalies - 1)
        recovery = _TRUST_RECOVERY_PER_HEALTHY_SESSION * signals.engagement_score
        state.trust_score = min(1.0, state.trust_score + recovery)

    # Record session in rolling history
    record: dict[str, Any] = {
        "ts": time.time(),
        "intent": signals.intent,
        "duration_min": round(signals.session_duration_min, 1),
        "uploaded": signals.uploaded,
        "anomalies": anomalies,
        "engagement_score": round(signals.engagement_score, 3),
        "trust_after": round(state.trust_score, 4),
        "mode": state.operating_mode,
    }
    state.session_history.append(record)
    if len(state.session_history) > _SESSION_HISTORY_MAX:
        state.session_history = state.session_history[-_SESSION_HISTORY_MAX:]

    summary = {
        "event": "account_brain_strategy_updated",
        "account_id": state.account_id,
        "anomalies": anomalies,
        "penalty_total": round(penalty_total, 3),
        "new_trust_score": round(state.trust_score, 4),
        "consecutive_anomalies": state.consecutive_anomalies,
        "new_risk_level": state.risk_level,
        "new_operating_mode": state.operating_mode,
        "uploads_suspended": state.uploads_suspended,
    }
    LOGGER.info("account_brain_strategy_updated", extra=summary)
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# Fatigue model
# ─────────────────────────────────────────────────────────────────────────────

def apply_fatigue_increase(state: AccountState, session_duration_min: float) -> None:
    """Increase fatigue after a completed session. Mutates state in-place."""
    delta = session_duration_min * _FATIGUE_PER_MINUTE
    state.fatigue_level = min(1.0, state.fatigue_level + delta)


def apply_fatigue_decay(state: AccountState) -> None:
    """Decay fatigue based on time elapsed since last activity. Mutates state in-place."""
    hours = _hours_since(state.last_active_at)
    if hours is None or hours <= 0:
        return
    decay = hours * _FATIGUE_DECAY_PER_HOUR
    state.fatigue_level = max(0.0, state.fatigue_level - decay)


# ─────────────────────────────────────────────────────────────────────────────
# AccountBrainRegistry — process-level singleton
# ─────────────────────────────────────────────────────────────────────────────

class AccountBrainRegistry:
    """Process-level store for all account brain states.

    Mirrors CrossAccountCoordinator's singleton pattern — in-memory only,
    intentionally resets on process restart (fresh state = no stale penalties).

    Public API (all synchronous, O(1)):
        get_state(account_id)
        decide_session_plan(account_id, now)
        record_action(account_id, action_type, session_duration_min, uploaded)
        force_intent(account_id, intent)
        reset_fatigue(account_id)
        set_content_ready(account_id, ready)
        snapshot_all() -> list[dict]

    Optional persistence:
        load_states(data: dict[str, dict])  – restore from external store
        dump_states() -> dict[str, dict]    – export for external persistence
    """

    def __init__(self) -> None:
        self._states: dict[str, AccountState] = {}
        self._decision_log: Deque[dict[str, Any]] = collections.deque(maxlen=200)

    # ── State access ──────────────────────────────────────────────────────────

    def get_state(self, account_id: str) -> AccountState:
        """Return (or lazily create) the AccountState for an account."""
        if account_id not in self._states:
            self._states[account_id] = AccountState.new_for_account(account_id)
            LOGGER.info(
                "account_brain_state_created",
                extra={
                    "event": "account_brain_state_created",
                    "account_id": account_id,
                    "preferred_window": (
                        f"{self._states[account_id].preferred_hour_start:02d}:00"
                        f"–{self._states[account_id].preferred_hour_end:02d}:00"
                    ),
                },
            )
        return self._states[account_id]

    # ── Primary decision API ──────────────────────────────────────────────────

    def decide_session_plan(
        self,
        account_id: str,
        now: datetime | None = None,
    ) -> SessionPlan:
        """Apply fatigue decay then decide the session plan."""
        state = self.get_state(account_id)
        apply_fatigue_decay(state)
        plan = decide_session_plan(state, now=now)
        # Clear one-shot override after use
        if state.intent_override is not None:
            state.intent_override = None
        log_entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "account_id": account_id,
            "event": "account_brain_decision",
            **plan.summary(),
            "fatigue": round(state.fatigue_level, 3),
            "trust_score": round(state.trust_score, 3),
            "risk_level": state.risk_level,
            "content_ready": state.content_ready,
            "streak_days": state.activity_streak_days,
        }
        LOGGER.info("account_brain_decision", extra=log_entry)
        self._decision_log.append(log_entry)
        return plan

    def update_strategy(
        self,
        account_id: str,
        signals: "SessionSignals",
    ) -> dict[str, Any]:
        """Apply adaptive feedback from a completed session."""
        state = self.get_state(account_id)
        return update_account_strategy(state, signals)

    def set_operating_mode(
        self,
        account_id: str,
        mode: OperatingMode | None,
    ) -> None:
        """Force (or clear) the operating mode for an account."""
        state = self.get_state(account_id)
        state.mode_override = mode
        LOGGER.info(
            "account_brain_mode_set",
            extra={"event": "account_brain_mode_set", "account_id": account_id, "mode": mode},
        )

    def emergency_safe_mode(self) -> list[str]:
        """Force ALL accounts to SAFE mode immediately."""
        affected = []
        for account_id, state in self._states.items():
            state.mode_override = "SAFE"
            affected.append(account_id)
        LOGGER.warning(
            "account_brain_emergency_safe_mode",
            extra={"event": "account_brain_emergency_safe_mode", "accounts_affected": len(affected)},
        )
        return affected

    def clear_safe_mode(self) -> list[str]:
        """Clear SAFE mode override from all accounts (restore auto-detection)."""
        affected = []
        for account_id, state in self._states.items():
            if state.mode_override == "SAFE":
                state.mode_override = None
                affected.append(account_id)
        return affected

    # ── Session outcome recording ─────────────────────────────────────────────

    def record_action(
        self,
        account_id: str,
        action_type: str,
        session_duration_min: float = 0.0,
        uploaded: bool = False,
    ) -> None:
        """Record the outcome of a completed session.

        Call in a finally block so it always fires, even on error.

        Args:
            account_id:          Account UUID.
            action_type:         e.g. "BROWSE", "UPLOAD", "IDLE".
            session_duration_min: Actual elapsed session time in minutes.
            uploaded:            True if an upload was successfully completed.
        """
        state = self.get_state(account_id)
        now_ts = time.time()

        # Update timestamps
        state.last_active_at = now_ts
        if uploaded:
            state.last_upload_at = now_ts

        # Rolling action log
        state.recent_actions.append(action_type)
        if len(state.recent_actions) > _RECENT_ACTIONS_MAX:
            state.recent_actions = state.recent_actions[-_RECENT_ACTIONS_MAX:]

        # Fatigue accumulation
        apply_fatigue_increase(state, session_duration_min)

        # Streak tracking (simplified: any action today increments streak)
        state.activity_streak_days = min(state.activity_streak_days + 1, 365)

        # If uploaded, content is no longer "ready"
        if uploaded:
            state.content_ready = False

        LOGGER.info(
            "account_brain_action_recorded",
            extra={
                "event": "account_brain_action_recorded",
                "account_id": account_id,
                "action_type": action_type,
                "session_duration_min": round(session_duration_min, 1),
                "uploaded": uploaded,
                "new_fatigue": round(state.fatigue_level, 3),
                "streak_days": state.activity_streak_days,
            },
        )

    # ── Operator controls ─────────────────────────────────────────────────────

    def force_intent(self, account_id: str, intent: Intent) -> None:
        """Force a specific intent for the next session only."""
        state = self.get_state(account_id)
        state.intent_override = intent
        LOGGER.info(
            "account_brain_intent_forced",
            extra={
                "event": "account_brain_intent_forced",
                "account_id": account_id,
                "forced_intent": intent,
            },
        )

    def reset_fatigue(self, account_id: str) -> None:
        """Reset fatigue to 0.0 (operator intervention)."""
        state = self.get_state(account_id)
        old = state.fatigue_level
        state.fatigue_level = 0.0
        LOGGER.info(
            "account_brain_fatigue_reset",
            extra={
                "event": "account_brain_fatigue_reset",
                "account_id": account_id,
                "old_fatigue": round(old, 3),
            },
        )

    def set_content_ready(self, account_id: str, ready: bool) -> None:
        """Toggle the content_ready flag."""
        state = self.get_state(account_id)
        state.content_ready = ready
        LOGGER.info(
            "account_brain_content_ready_set",
            extra={
                "event": "account_brain_content_ready_set",
                "account_id": account_id,
                "content_ready": ready,
            },
        )

    # ── Monitoring / dashboard ────────────────────────────────────────────────

    def snapshot_all(self) -> list[dict[str, Any]]:
        """Return a dashboard-ready snapshot of all account states."""
        result = []
        for account_id, state in self._states.items():
            apply_fatigue_decay(state)
            plan = decide_session_plan(state, now=datetime.now())
            mins_active = _minutes_since(state.last_active_at)
            mins_upload = _minutes_since(state.last_upload_at)
            result.append({
                **state.to_dict(),
                "current_intent": plan.intent,
                "intent_reason": plan.intent_reason,
                "session_duration_min": plan.session_duration_min,
                "interaction_level": plan.interaction_level,
                "operating_mode": plan.operating_mode,
                "allowed_actions": plan.allowed_actions,
                "delay_multiplier": plan.delay_multiplier,
                "minutes_since_active": round(mins_active, 1) if mins_active is not None else None,
                "minutes_since_upload": round(mins_upload, 1) if mins_upload is not None else None,
                "active_window": f"{state.preferred_hour_start:02d}:00–{state.preferred_hour_end:02d}:00",
            })
        return result

    def snapshot(self, account_id: str) -> dict[str, Any] | None:
        if account_id not in self._states:
            return None
        state = self._states[account_id]
        apply_fatigue_decay(state)
        plan = decide_session_plan(state, now=datetime.now())
        mins_active = _minutes_since(state.last_active_at)
        mins_upload = _minutes_since(state.last_upload_at)
        return {
            **state.to_dict(),
            "current_intent": plan.intent,
            "intent_reason": plan.intent_reason,
            "session_duration_min": plan.session_duration_min,
            "interaction_level": plan.interaction_level,
            "operating_mode": plan.operating_mode,
            "allowed_actions": plan.allowed_actions,
            "delay_multiplier": plan.delay_multiplier,
            "minutes_since_active": round(mins_active, 1) if mins_active is not None else None,
            "minutes_since_upload": round(mins_upload, 1) if mins_upload is not None else None,
            "active_window": f"{state.preferred_hour_start:02d}:00–{state.preferred_hour_end:02d}:00",
        }

    def get_decision_log(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent brain decisions, newest first."""
        entries = list(self._decision_log)
        entries.reverse()
        return entries[:limit]

    # ── Persistence hooks ─────────────────────────────────────────────────────

    def load_states(self, data: dict[str, dict[str, Any]]) -> None:
        """Restore states from an external store (e.g. DB or JSON file).

        Call once at startup if persistence is desired. Does not overwrite
        states that are already loaded (first-wins).
        """
        for account_id, raw in data.items():
            if account_id not in self._states:
                try:
                    self._states[account_id] = AccountState.from_dict(raw)
                except Exception as exc:
                    LOGGER.warning(
                        "account_brain_load_failed",
                        extra={
                            "event": "account_brain_load_failed",
                            "account_id": account_id,
                            "error": str(exc),
                        },
                    )

    def dump_states(self) -> dict[str, dict[str, Any]]:
        """Export all states as a serialisable dict for external persistence."""
        return {aid: state.to_dict() for aid, state in self._states.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Process-level singleton
# ─────────────────────────────────────────────────────────────────────────────

_BRAIN_REGISTRY_INSTANCE: AccountBrainRegistry | None = None


def get_brain_registry() -> AccountBrainRegistry:
    """Return the process-level AccountBrainRegistry singleton.

    Instantiated lazily on first call. Matches CrossAccountCoordinator's
    get_coordinator() pattern for architectural consistency.
    """
    global _BRAIN_REGISTRY_INSTANCE
    if _BRAIN_REGISTRY_INSTANCE is None:
        _BRAIN_REGISTRY_INSTANCE = AccountBrainRegistry()
        LOGGER.info(
            "account_brain_registry_initialised",
            extra={"event": "account_brain_registry_initialised"},
        )
    return _BRAIN_REGISTRY_INSTANCE
