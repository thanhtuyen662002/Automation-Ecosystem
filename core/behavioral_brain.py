"""
Behavioral Brain — Layer 4: Intent + Session Planner + Human Simulation.

Architecture:
    Layer 3 (StealthBrain) → Strategy (what is safe)
         ↓
    Layer 4 (BehavioralBrain) → SessionPlan (how to behave)
         ↓
    Executor → runs steps with human-like timing

Design contracts:
  - BehavioralBrain NEVER touches fingerprint or mutation logic.
  - All randomness is seed-based (deterministic per account, diverse across fleet).
  - Two accounts with same risk level get DIFFERENT behavior plans.
  - Same account gets CONSISTENT personality across sessions.

Usage:
    from core.behavioral_brain import get_behavioral_brain, SessionIntent
    from core.stealth_brain import get_stealth_brain
    from core.mutation_controller import get_mutation_controller

    risk    = await validate_fingerprint(page, profile)
    signals = to_runtime_signals(risk)
    strategy = get_stealth_brain().evaluate(account_id, signals, profile)
    result   = get_mutation_controller().apply(profile, strategy)

    plan = get_behavioral_brain().build_session_plan(account_id, profile, strategy, signals)
    await executor.run(plan)

    get_stealth_brain().record_outcome(account_id, {
        "upload_success": True,
        "session_duration": plan.actual_duration,
        "actions_count": len(plan.steps),
        "abandoned_actions": plan.abandoned_count,
        ...
    }, profile)
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.identity_manager import IdentityProfile
    from core.mutation_controller import Strategy, RiskLevel
    from core.runtime_validator import RuntimeSignals

LOGGER = logging.getLogger("core.behavioral_brain")


# ── PRNG helpers (seed-based, no random module) ───────────────────────────────

def _bseed(account_id: str, slot: int) -> float:
    """Deterministic float in [0, 1) from account_id + slot."""
    h = hashlib.sha256(f"bhv:{account_id}:{slot}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _bpick(account_id: str, slot: int, pool: list) -> Any:
    return pool[int(_bseed(account_id, slot) * len(pool))]


def _bint(account_id: str, slot: int, lo: int, hi: int) -> int:
    return lo + int(_bseed(account_id, slot) * (hi - lo + 1))


def _bfloat(account_id: str, slot: int, lo: float, hi: float) -> float:
    return lo + _bseed(account_id, slot) * (hi - lo)


def _bfloat_session(account_id: str, session_slot: int, sub_slot: int, lo: float, hi: float) -> float:
    """Session-varying float — changes each session but deterministically."""
    h = hashlib.sha256(f"sess:{account_id}:{session_slot}:{sub_slot}".encode()).hexdigest()
    unit = int(h[:8], 16) / 0xFFFFFFFF
    return lo + unit * (hi - lo)


# ── SessionIntent ─────────────────────────────────────────────────────────────

class SessionIntent(str, Enum):
    BROWSE  = "browse"    # passive consumption, no upload
    WARMUP  = "warmup"    # new/risky account: long safe browsing only
    ENGAGE  = "engage"    # like, comment, react — social signals
    UPLOAD  = "upload"    # full content upload flow
    IDLE    = "idle"      # high-risk: do almost nothing


# ── BehaviorProfile ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BehaviorProfile:
    """
    Stable per-account personality traits derived once from account_id seed.
    Controls timing, attention, and aggressiveness across ALL sessions.
    """
    speed: str            # "slow" | "medium" | "fast"
    attention_span: str   # "low" | "medium" | "high"
    aggressiveness: str   # "low" | "medium" | "high"

    # Derived numeric multipliers (pre-computed for convenience)
    pause_multiplier: float   # > 1 for slow, < 1 for fast
    abandon_rate: float       # 0.0–0.40 probability of abandoning a step
    warmup_sessions: int      # number of warmup sessions before first upload

    def to_dict(self) -> dict[str, Any]:
        return {
            "speed":            self.speed,
            "attention_span":   self.attention_span,
            "aggressiveness":   self.aggressiveness,
            "pause_multiplier": round(self.pause_multiplier, 3),
            "abandon_rate":     round(self.abandon_rate, 3),
            "warmup_sessions":  self.warmup_sessions,
        }


def derive_behavior_profile(account_id: str) -> BehaviorProfile:
    """
    Derive stable, seeded personality traits for an account.
    Called once — traits never change unless account_id changes.
    """
    speed       = _bpick(account_id, 0, ["slow", "slow", "medium", "medium", "medium", "fast"])
    attention   = _bpick(account_id, 1, ["low", "medium", "medium", "high"])
    aggressive  = _bpick(account_id, 2, ["low", "medium", "medium", "high"])

    pause_mult = {"slow": 1.8, "medium": 1.0, "fast": 0.6}[speed]
    abandon    = {"low": 0.30, "medium": 0.18, "high": 0.08}[attention]
    warmup_n   = {"low": 5, "medium": 3, "high": 1}[aggressive]

    LOGGER.debug("behavior_profile_derived", extra={
        "account_id":   account_id,
        "speed":        speed,
        "attention":    attention,
        "aggressiveness": aggressive,
    })
    return BehaviorProfile(
        speed            = speed,
        attention_span   = attention,
        aggressiveness   = aggressive,
        pause_multiplier = pause_mult,
        abandon_rate     = abandon,
        warmup_sessions  = warmup_n,
    )


# ── SessionStep / SessionPlan ─────────────────────────────────────────────────

@dataclass
class SessionStep:
    """One atomic action within a session."""
    action:   str           # e.g. "open_app", "scroll_feed", "upload_video"
    duration: float         # seconds (base estimate; executor may vary ±20%)
    metadata: dict          = field(default_factory=dict)
    skippable: bool         = False   # True = can be abandoned without impact


@dataclass
class SessionPlan:
    """Full session execution plan produced by BehavioralBrain."""
    intent:           SessionIntent
    steps:            list[SessionStep]
    behavior_profile: BehaviorProfile
    estimated_duration: float           # sum of step durations (seconds)
    # Mutable runtime tracking (updated by executor)
    actual_duration:  float = 0.0
    abandoned_count:  int   = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent":             self.intent.value,
            "steps":              [{"action": s.action, "duration": round(s.duration, 2), "meta": s.metadata} for s in self.steps],
            "behavior_profile":   self.behavior_profile.to_dict(),
            "estimated_duration": round(self.estimated_duration, 1),
        }


# ── Intent Decision ───────────────────────────────────────────────────────────

def decide_intent(
    account_id: str,
    profile: "IdentityProfile",
    stealth_memory: Any,          # StealthMemory
    signals: "RuntimeSignals",
    session_index: int = 0,       # monotonic session counter for this account
) -> SessionIntent:
    """
    Determine what the account should DO this session.

    Priority rules (highest wins):
      1. High risk (>= 0.60) or banned → IDLE
      2. Webdriver exposed → IDLE
      3. New account (session_index < warmup_sessions) → WARMUP
      4. Recent ban/block in last 3 outcomes → BROWSE only
      5. Recent 3+ captchas → BROWSE only
      6. Repeated successes (3+) + low risk → allow UPLOAD
      7. Moderate risk (0.30–0.60) → BROWSE or ENGAGE (no upload)
      8. Otherwise → probabilistic choice weighted by aggressiveness
    """
    bp = derive_behavior_profile(account_id)
    recent = (stealth_memory.outcome_history or [])[-10:]
    recent_bans     = any(o.get("blocked") for o in recent[-3:])
    recent_captchas = sum(1 for o in recent if o.get("captcha"))
    recent_successes= sum(1 for o in recent[-5:] if o.get("upload_success"))

    # Rule 1+2: critical risk → IDLE
    if signals.risk_score >= 0.60 or not signals.webdriver_hidden:
        LOGGER.info("intent_decided", extra={"account_id": account_id, "intent": "idle", "reason": "high_risk"})
        return SessionIntent.IDLE

    # Rule 3: new account needs warmup
    if session_index < bp.warmup_sessions:
        LOGGER.info("intent_decided", extra={"account_id": account_id, "intent": "warmup", "reason": "new_account"})
        return SessionIntent.WARMUP

    # Rule 4+5: protective → browse only
    if recent_bans or recent_captchas >= 3:
        LOGGER.info("intent_decided", extra={"account_id": account_id, "intent": "browse", "reason": "protective_mode"})
        return SessionIntent.BROWSE

    # Rule 6: earned upload right
    if recent_successes >= 3 and signals.risk_score < 0.30:
        LOGGER.info("intent_decided", extra={"account_id": account_id, "intent": "upload", "reason": "earned"})
        return SessionIntent.UPLOAD

    # Rule 7: moderate risk → no upload
    if signals.risk_score >= 0.30:
        choice = _bpick(account_id, 100 + session_index, [SessionIntent.BROWSE, SessionIntent.ENGAGE])
        LOGGER.info("intent_decided", extra={"account_id": account_id, "intent": choice.value, "reason": "moderate_risk"})
        return choice

    # Rule 8: weighted by aggressiveness
    pools = {
        "low":    [SessionIntent.BROWSE, SessionIntent.BROWSE, SessionIntent.ENGAGE, SessionIntent.IDLE],
        "medium": [SessionIntent.BROWSE, SessionIntent.ENGAGE, SessionIntent.UPLOAD],
        "high":   [SessionIntent.ENGAGE, SessionIntent.UPLOAD, SessionIntent.UPLOAD],
    }
    intent = _bpick(account_id, 200 + session_index, pools[bp.aggressiveness])
    LOGGER.info("intent_decided", extra={"account_id": account_id, "intent": intent.value, "reason": "personality"})
    return intent


# ── Step Builders ─────────────────────────────────────────────────────────────

def _pause(account_id: str, slot: int, bp: BehaviorProfile, lo: float, hi: float) -> float:
    """Return a seeded, personality-adjusted pause duration."""
    base = _bfloat_session(account_id, slot, slot + 500, lo, hi)
    return round(base * bp.pause_multiplier, 2)


def _maybe_abandon(account_id: str, slot: int, bp: BehaviorProfile) -> bool:
    """Return True if this step should be abandoned (probabilistic, seeded)."""
    return _bseed(account_id, slot + 1000) < bp.abandon_rate


def _scroll_steps(account_id: str, bp: BehaviorProfile, slot_base: int, count_lo: int, count_hi: int) -> list[SessionStep]:
    """Generate a variable-length scroll sequence with organic pauses."""
    n = _bint(account_id, slot_base, count_lo, count_hi)
    steps = []
    for i in range(n):
        duration = _pause(account_id, slot_base + i, bp, 1.5, 8.0)
        steps.append(SessionStep(
            action   = "scroll_feed",
            duration = duration,
            metadata = {"scroll_distance": _bint(account_id, slot_base + i + 50, 200, 1800)},
            skippable = True,
        ))
        # Micro-idle: random inactivity between scrolls
        if _bseed(account_id, slot_base + i + 200) < 0.25:
            idle_dur = _pause(account_id, slot_base + i + 300, bp, 2.0, 12.0)
            steps.append(SessionStep(action="micro_idle", duration=idle_dur, skippable=True))
    return steps


def _build_browse(account_id: str, bp: BehaviorProfile, session_slot: int) -> list[SessionStep]:
    steps: list[SessionStep] = []
    steps.append(SessionStep("open_app",    _pause(account_id, session_slot + 1, bp, 1.0, 3.5)))
    steps.append(SessionStep("app_loading", _pause(account_id, session_slot + 2, bp, 0.5, 2.0), skippable=False))
    steps += _scroll_steps(account_id, bp, session_slot + 10, 3, 8)
    # 1–3 item taps
    taps = _bint(account_id, session_slot + 20, 1, 3)
    for i in range(taps):
        if _maybe_abandon(account_id, session_slot + 20 + i, bp):
            break
        steps.append(SessionStep("tap_item", _pause(account_id, session_slot + 20 + i, bp, 3.0, 18.0),
                                 metadata={"item_index": i}, skippable=True))
        steps.append(SessionStep("back_to_feed", _pause(account_id, session_slot + 30 + i, bp, 0.5, 2.0)))
    steps.append(SessionStep("close_app", _pause(account_id, session_slot + 40, bp, 0.5, 2.0)))
    return steps


def _build_warmup(account_id: str, bp: BehaviorProfile, session_slot: int) -> list[SessionStep]:
    """Long passive browsing, no risky actions."""
    steps: list[SessionStep] = []
    steps.append(SessionStep("open_app",    _pause(account_id, session_slot + 1, bp, 1.5, 4.0)))
    steps.append(SessionStep("app_loading", _pause(account_id, session_slot + 2, bp, 0.8, 2.5)))
    steps += _scroll_steps(account_id, bp, session_slot + 10, 6, 14)  # longer scroll
    # Occasional profile view — no interactions
    if _bseed(account_id, session_slot + 80) < 0.4:
        steps.append(SessionStep("view_profile", _pause(account_id, session_slot + 81, bp, 5.0, 20.0), skippable=True))
    steps.append(SessionStep("idle_exit", _pause(account_id, session_slot + 90, bp, 3.0, 10.0)))
    steps.append(SessionStep("close_app", _pause(account_id, session_slot + 91, bp, 0.5, 2.0)))
    return steps


def _build_engage(account_id: str, bp: BehaviorProfile, session_slot: int) -> list[SessionStep]:
    steps: list[SessionStep] = []
    steps.append(SessionStep("open_app",    _pause(account_id, session_slot + 1, bp, 1.0, 3.0)))
    steps.append(SessionStep("app_loading", _pause(account_id, session_slot + 2, bp, 0.5, 2.0)))
    steps += _scroll_steps(account_id, bp, session_slot + 10, 2, 5)
    interactions = _bint(account_id, session_slot + 50, 2, 5)
    for i in range(interactions):
        if _maybe_abandon(account_id, session_slot + 50 + i, bp):
            break
        action = _bpick(account_id, session_slot + 60 + i, ["like_item", "like_item", "comment_view", "share_view"])
        steps.append(SessionStep(action, _pause(account_id, session_slot + 60 + i, bp, 1.5, 8.0),
                                 metadata={"interaction_index": i}, skippable=True))
    steps += _scroll_steps(account_id, bp, session_slot + 70, 1, 4)
    steps.append(SessionStep("close_app", _pause(account_id, session_slot + 80, bp, 0.5, 2.0)))
    return steps


def _build_upload(account_id: str, bp: BehaviorProfile, session_slot: int) -> list[SessionStep]:
    steps: list[SessionStep] = []
    steps.append(SessionStep("open_app",       _pause(account_id, session_slot + 1,  bp, 1.0, 3.0)))
    steps.append(SessionStep("app_loading",    _pause(account_id, session_slot + 2,  bp, 0.5, 2.0)))
    # Brief browse before upload (human behaviour — not going straight to upload)
    steps += _scroll_steps(account_id, bp, session_slot + 10, 1, 3)
    steps.append(SessionStep("navigate_upload",_pause(account_id, session_slot + 20, bp, 1.0, 4.0)))
    steps.append(SessionStep("select_content", _pause(account_id, session_slot + 21, bp, 2.0, 8.0)))
    # Simulate thinking/editing pause
    steps.append(SessionStep("edit_pause",     _pause(account_id, session_slot + 22, bp, 5.0, 25.0),
                             metadata={"reason": "human_thinking"}, skippable=False))
    steps.append(SessionStep("fill_metadata",  _pause(account_id, session_slot + 23, bp, 3.0, 12.0)))
    steps.append(SessionStep("upload_confirm", _pause(account_id, session_slot + 24, bp, 1.0, 3.0), skippable=False))
    steps.append(SessionStep("post_upload_idle", _pause(account_id, session_slot + 25, bp, 4.0, 15.0),
                             metadata={"reason": "post_upload_natural_pause"}, skippable=True))
    steps += _scroll_steps(account_id, bp, session_slot + 30, 1, 3)
    steps.append(SessionStep("close_app",      _pause(account_id, session_slot + 40, bp, 0.5, 2.0)))
    return steps


def _build_idle(account_id: str, bp: BehaviorProfile, session_slot: int) -> list[SessionStep]:
    """High-risk: minimal footprint — brief open and close."""
    steps = [
        SessionStep("open_app",    _pause(account_id, session_slot + 1, bp, 1.0, 2.0)),
        SessionStep("app_loading", _pause(account_id, session_slot + 2, bp, 0.5, 1.5)),
    ]
    if _bseed(account_id, session_slot + 3) < 0.3:
        steps.append(SessionStep("scroll_feed", _pause(account_id, session_slot + 4, bp, 2.0, 5.0), skippable=True))
    steps.append(SessionStep("close_app", _pause(account_id, session_slot + 5, bp, 0.3, 1.5)))
    return steps


# ── BehavioralBrain ───────────────────────────────────────────────────────────

class BehavioralBrain:
    """
    Layer 4 decision engine: strategy (what is safe) → session plan (how to behave).

    Responsibilities:
      - Derive stable per-account personality (BehaviorProfile)
      - Decide SessionIntent based on risk + history
      - Generate realistic, non-linear SessionPlan
      - Ensure no two accounts share identical step sequences
      - Ensure same account behaves consistently across sessions

    Does NOT:
      - Touch fingerprints
      - Call MutationController
      - Modify IdentityProfile directly
    """

    def __init__(self) -> None:
        self._behavior_cache: dict[str, BehaviorProfile] = {}
        self._session_counters: dict[str, int] = {}

    def get_behavior_profile(self, account_id: str) -> BehaviorProfile:
        if account_id not in self._behavior_cache:
            self._behavior_cache[account_id] = derive_behavior_profile(account_id)
        return self._behavior_cache[account_id]

    def next_session_index(self, account_id: str) -> int:
        idx = self._session_counters.get(account_id, 0)
        self._session_counters[account_id] = idx + 1
        return idx

    # ── Public API ────────────────────────────────────────────────────────────

    def build_session_plan(
        self,
        account_id: str,
        profile: "IdentityProfile",
        strategy: "Strategy",
        signals: "RuntimeSignals",
        stealth_memory: Any = None,
        session_index: int | None = None,
    ) -> SessionPlan:
        """
        Build a human-like session plan from a Layer 3 Strategy.

        Args:
            account_id:     Account identifier.
            profile:        Current IdentityProfile (read-only here).
            strategy:       Strategy from StealthBrain (risk_level + delay params).
            signals:        RuntimeSignals for intent gating.
            stealth_memory: StealthMemory for history-based intent decisions.
            session_index:  Override session counter (default: auto-increment).

        Returns:
            SessionPlan ready to execute.
        """
        bp = self.get_behavior_profile(account_id)

        if session_index is None:
            session_index = self.next_session_index(account_id)

        # Use stealth_memory if provided, else a minimal stub
        class _EmptyMem:
            outcome_history: list = []
        mem = stealth_memory or _EmptyMem()

        intent = decide_intent(account_id, profile, mem, signals, session_index)

        # Layer 3 safety override: HIGH risk forces IDLE or BROWSE regardless of intent
        from core.mutation_controller import RiskLevel
        if strategy.risk_level == RiskLevel.HIGH and intent not in (SessionIntent.IDLE, SessionIntent.BROWSE):
            LOGGER.info("intent_overridden_by_layer3", extra={
                "account_id": account_id,
                "original": intent.value,
                "override": "browse",
                "reason": "high_risk_strategy",
            })
            intent = SessionIntent.BROWSE

        # Session slot — varies each session so step durations differ
        session_slot = (session_index * 97 + hash(account_id) % 10_000) & 0xFFFF

        builders = {
            SessionIntent.BROWSE:  _build_browse,
            SessionIntent.WARMUP:  _build_warmup,
            SessionIntent.ENGAGE:  _build_engage,
            SessionIntent.UPLOAD:  _build_upload,
            SessionIntent.IDLE:    _build_idle,
        }
        steps = builders[intent](account_id, bp, session_slot)

        # Apply Layer 3 delay_multiplier to all step durations
        dm = strategy.delay_multiplier
        if dm != 1.0:
            steps = [
                SessionStep(s.action, round(s.duration * dm, 2), s.metadata, s.skippable)
                for s in steps
            ]

        # Warmup delay (extra idle before first step)
        if strategy.warmup_delay > 0 and session_index == 0:
            steps.insert(0, SessionStep(
                "warmup_idle", round(strategy.warmup_delay, 1),
                metadata={"reason": "initial_warmup"}, skippable=False,
            ))

        estimated = round(sum(s.duration for s in steps), 1)

        LOGGER.info("session_plan_built", extra={
            "account_id":       account_id,
            "intent":           intent.value,
            "step_count":       len(steps),
            "estimated_secs":   estimated,
            "risk_level":       strategy.risk_level.value,
            "speed":            bp.speed,
            "attention_span":   bp.attention_span,
            "aggressiveness":   bp.aggressiveness,
        })

        return SessionPlan(
            intent             = intent,
            steps              = steps,
            behavior_profile   = bp,
            estimated_duration = estimated,
        )

    # ── Feedback learning ─────────────────────────────────────────────────────

    def analyze_session(self, plan: SessionPlan) -> dict[str, Any]:
        """
        Post-session analysis: detect suspicious patterns for StealthBrain feedback.

        Returns a dict of behavior signals to include in record_outcome().
        """
        signals: dict[str, Any] = {
            "session_duration":  plan.actual_duration,
            "actions_count":     len(plan.steps),
            "abandoned_actions": plan.abandoned_count,
        }

        # Short session suspicious (< 20% of estimate)
        if plan.actual_duration > 0 and plan.estimated_duration > 0:
            completion = plan.actual_duration / plan.estimated_duration
            signals["completion_ratio"] = round(completion, 2)
            if completion < 0.20:
                LOGGER.warning("behavior_session_too_short", extra={
                    "intent": plan.intent.value,
                    "completion_ratio": round(completion, 2),
                })
                signals["suspicious_short"] = True

        # High abandon rate (> 50% of skippable steps)
        skippable = sum(1 for s in plan.steps if s.skippable)
        if skippable > 0:
            abandon_rate = plan.abandoned_count / skippable
            signals["effective_abandon_rate"] = round(abandon_rate, 2)
            if abandon_rate > 0.50:
                LOGGER.warning("behavior_high_abandon_rate", extra={"rate": round(abandon_rate, 2)})
                signals["suspicious_abandon"] = True

        return signals

    # ── Persistence helpers ───────────────────────────────────────────────────

    def snapshot_all(self) -> dict[str, Any]:
        return {
            "behavior_cache":    {k: v.to_dict() for k, v in self._behavior_cache.items()},
            "session_counters":  dict(self._session_counters),
        }

    def load_all(self, data: dict[str, Any]) -> None:
        self._session_counters = dict(data.get("session_counters", {}))
        # BehaviorProfile is re-derived from seed — no need to restore cache


# ── Singleton ─────────────────────────────────────────────────────────────────

_BEHAVIORAL_BRAIN: BehavioralBrain | None = None


def get_behavioral_brain() -> BehavioralBrain:
    """Return the process-level BehavioralBrain singleton."""
    global _BEHAVIORAL_BRAIN
    if _BEHAVIORAL_BRAIN is None:
        _BEHAVIORAL_BRAIN = BehavioralBrain()
    return _BEHAVIORAL_BRAIN
