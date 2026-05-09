"""
Cross-Account Strategy Engine
==============================

Orchestrates multiple accounts as a coordinated-but-non-detectable ecosystem.

Architecture contract:
  - NO direct account-to-account communication.
  - NO shared mutable state per account (strict isolation).
  - Coordination is achieved through shared READ-ONLY environmental signals
    (global wave, trend momentum) that each account reads independently.
  - All decisions are fully deterministic via stable_hash_int seeding.
  - Roles rotate slowly over time (daily/weekly drift).
  - Global feedback (ban_rate / success_rate) suppresses aggressive roles
    fleet-wide without any per-account coupling.

Coordination model:
  A crowd of independent humans reacting to the same environment —
  not bots following a script.

  The "environment" is:
    - Global activity wave  → when the platform is "busy"
    - Trend momentum        → what topic is hot right now
    - Platform profile      → behavioural archetype per platform
    - Ban-rate signal       → how dangerous is the current climate

  Each account reads these signals, but maps them differently based on:
    - Its AccountRole  (WARMER / EXPLORER / AMPLIFIER / HARVESTER / IDLE)
    - Its PersonaState (activity_bias, risk_tolerance, dominant_niche)
    - Its age bucket   (new / maturing / veteran)
    - Its reaction_group (fast / medium / slow — from mutation_controller)

Coordination patterns (staggered, not simultaneous):
  1. Staggered amplification: AMPLIFIER accounts act 0–3 hours after wave peak.
  2. Delayed reaction chains: WARMER → EXPLORER → AMPLIFIER over consecutive hours.
  3. Partial participation: only fraction of eligible accounts act per cycle (~40%).

Feedback loop:
  - ban_rate  ↑ → suppress HARVESTER + AMPLIFIER, elevate IDLE/WARMER
  - success ↓  → increase EXPLORER ratio (try new things)
  Thresholds are read from GlobalMemory (advisory, exception-safe).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.mutation_controller import stable_hash_int, _normalized_noise

LOGGER = logging.getLogger("core.strategy_engine")


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# Feedback thresholds for role suppression
BAN_RATE_HIGH_THRESHOLD:    float = 0.10   # >10% ban rate → suppress aggressive roles
BAN_RATE_MEDIUM_THRESHOLD:  float = 0.05   # >5%  ban rate → moderate caution
SUCCESS_RATE_LOW_THRESHOLD: float = 0.50   # <50% success  → boost exploration

# Participation rate: fraction of accounts that act per cycle (~40%)
_PARTICIPATION_RATE: float = 0.40

# Role weights under NORMAL conditions (must sum to 100)
_NORMAL_ROLE_WEIGHTS: dict[str, int] = {
    "WARMER":    25,
    "EXPLORER":  20,
    "AMPLIFIER": 25,
    "HARVESTER": 20,
    "IDLE":      10,
}

# Intent timing offsets per role (seconds, relative to cycle start)
_ROLE_TIMING_OFFSETS: dict[str, tuple[int, int]] = {
    "WARMER":    (300,  900),   # 5–15 min lag (build-up)
    "EXPLORER":  (0,    600),   # 0–10 min (early tester)
    "AMPLIFIER": (600, 3600),   # 10 min – 1 hr (after wave)
    "HARVESTER": (1800, 5400),  # 30 min – 90 min (late converter)
    "IDLE":      (3600, 14400), # 1–4 hr (almost inactive)
}

# Max outcome memory depth per account
_OUTCOME_MEMORY_DEPTH: int = 30

# EWMA decay for outcome weight (older entries lose weight)
_OUTCOME_DECAY: float = 0.92


# ─────────────────────────────────────────────────────────────────────────────
# Core enums + dataclasses
# ─────────────────────────────────────────────────────────────────────────────

class AccountRole(str, Enum):
    WARMER    = "WARMER"    # Low risk — build history, establish trust signals
    EXPLORER  = "EXPLORER"  # Test new behaviours / niches / content types
    AMPLIFIER = "AMPLIFIER" # Boost trending content, increase reach signal
    HARVESTER = "HARVESTER" # High-value conversions (follow, purchase, post)
    IDLE      = "IDLE"      # Cooldown / simulated human inactivity


class IntentType(str, Enum):
    BROWSE = "browse"   # Passive scroll, watch, read
    ENGAGE = "engage"   # Like, comment, react, share
    POST   = "post"     # Upload / publish content
    IDLE   = "idle"     # Do nothing (simulated away)


@dataclass
class ActionPlan:
    """The per-account action plan for one cycle."""
    account_id:    str
    role:          AccountRole
    intent_type:   IntentType
    intensity:     float        # 0.0 (minimal) – 1.0 (maximal)
    timing_offset: int          # seconds from cycle start to act
    platform:      str
    reasoning:     str          # human-readable audit trail
    niche:         str          # content niche this cycle


@dataclass
class StrategyOutcome:
    """Record of one strategy cycle result per account."""
    account_id:  str
    role:        str
    intent_type: str
    success:     bool
    ban:         bool
    ts:          float = field(default_factory=time.time)
    weight:      float = 1.0    # decays over time


@dataclass
class EngineState:
    """Process-level shared state (read by all accounts, never written per-account)."""
    # Global feedback metrics — written by record_outcome(), read by plan_actions()
    recent_ban_rate:     float = 0.0
    recent_success_rate: float = 1.0
    anomaly_score:       float = 0.0
    last_updated:        float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Module-level state
# ─────────────────────────────────────────────────────────────────────────────

# Per-account outcome history for strategy memory
_OUTCOME_HISTORY: dict[str, list[StrategyOutcome]] = {}

# Shared engine state (ban_rate etc.)
_ENGINE_STATE = EngineState()

# Per-account role override cache (cleared on daily rotation)
_ROLE_CACHE:  dict[str, tuple[int, AccountRole]] = {}  # account_id → (day, role)


# ─────────────────────────────────────────────────────────────────────────────
# Role assignment (deterministic, slowly rotating)
# ─────────────────────────────────────────────────────────────────────────────

def _age_bucket(account_id: str, created_ts: int, now: int) -> str:
    """Classify account age as 'new' (<7d), 'maturing' (<30d), 'veteran' (≥30d)."""
    age_days = max(0, (now - created_ts) // 86400)
    if age_days < 7:
        return "new"
    if age_days < 30:
        return "maturing"
    return "veteran"


def _feedback_adjusted_weights(base: dict[str, int]) -> dict[str, int]:
    """Adjust role weights based on current global feedback."""
    state = _ENGINE_STATE
    weights = dict(base)

    if state.recent_ban_rate >= BAN_RATE_HIGH_THRESHOLD:
        # Severe: suppress HARVESTER + AMPLIFIER, inflate IDLE/WARMER
        weights["HARVESTER"]  = max(2,  weights["HARVESTER"] - 15)
        weights["AMPLIFIER"]  = max(2,  weights["AMPLIFIER"] - 10)
        weights["IDLE"]      += 15
        weights["WARMER"]    += 10

    elif state.recent_ban_rate >= BAN_RATE_MEDIUM_THRESHOLD:
        # Moderate caution
        weights["HARVESTER"]  = max(5,  weights["HARVESTER"] - 8)
        weights["AMPLIFIER"]  = max(5,  weights["AMPLIFIER"] - 4)
        weights["IDLE"]      += 8
        weights["WARMER"]    += 4

    if state.recent_success_rate < SUCCESS_RATE_LOW_THRESHOLD:
        # Low success → boost EXPLORER (try new things)
        weights["EXPLORER"]  += 10
        weights["HARVESTER"]  = max(2, weights["HARVESTER"] - 5)

    return weights


def assign_role(
    account_id: str,
    created_ts: int = 0,
    now: int | None = None,
    persona_risk_tolerance: float = 0.5,
    persona_activity_bias: float  = 0.5,
) -> AccountRole:
    """Assign a deterministic role, slowly rotating day-by-day.

    Role rotates on a weekly cycle with daily micro-drift:
      - Base role: stable hash of (account_id, week_number)
      - Daily drift: ±1 step in weighted table with 30% probability

    Constraints:
      - New accounts (<7d)  : WARMER or IDLE only
      - Low risk_tolerance  : skips HARVESTER
      - High activity_bias  : skips IDLE
    """
    if now is None:
        now = int(time.time())

    day = now // 86400

    # Return cached role for same calendar day
    cached = _ROLE_CACHE.get(account_id)
    if cached and cached[0] == day:
        return cached[1]

    age = _age_bucket(account_id, created_ts, now)
    week = now // (86400 * 7)

    # Base role slot from weekly seed
    weights = _feedback_adjusted_weights(_NORMAL_ROLE_WEIGHTS)
    total = sum(weights.values())
    base_seed = stable_hash_int(account_id, "role_base", str(week)) % total

    role_name = "IDLE"
    cumulative = 0
    for rname, w in weights.items():
        cumulative += w
        if base_seed < cumulative:
            role_name = rname
            break

    # Daily micro-drift: ~30% of days, shift by one slot in the wheel
    drift_seed = stable_hash_int(account_id, "role_drift", str(day)) % 10
    if drift_seed < 3:
        roles_list = list(weights.keys())
        idx = roles_list.index(role_name)
        direction = 1 if (stable_hash_int(account_id, "role_dir", str(day)) % 2 == 0) else -1
        role_name = roles_list[(idx + direction) % len(roles_list)]

    # Constraint: new accounts must warm first
    if age == "new" and role_name not in ("WARMER", "IDLE"):
        role_name = "WARMER"

    # Constraint: conservative accounts avoid HARVESTER
    if persona_risk_tolerance < 0.30 and role_name == "HARVESTER":
        role_name = "AMPLIFIER"

    # Constraint: very active accounts avoid IDLE
    if persona_activity_bias > 0.80 and role_name == "IDLE":
        role_name = "EXPLORER"

    role = AccountRole(role_name)
    _ROLE_CACHE[account_id] = (day, role)

    LOGGER.debug(
        "strategy_role_assigned account=%s role=%s age=%s week=%d day=%d",
        account_id, role.value, age, week, day,
    )
    return role


# ─────────────────────────────────────────────────────────────────────────────
# Coordination signals (shared environment, no account coupling)
# ─────────────────────────────────────────────────────────────────────────────

def _wave_intensity(now: int) -> float:
    """Platform activity wave 0.0–1.0 for this hour. Shared, not per-account."""
    from core.mutation_controller import _global_activity_wave
    raw = _global_activity_wave(now)   # 0.9–1.1
    return (raw - 0.9) / 0.2           # normalise to 0.0–1.0


def _trend_intensity(now: int) -> float:
    """Trend momentum 0.0–1.0 for this hour."""
    from core.mutation_controller import _trend_momentum
    raw = _trend_momentum(now)          # 0.9–1.1
    return (raw - 0.9) / 0.2           # normalise to 0.0–1.0


def _should_participate(account_id: str, now: int, role: AccountRole) -> bool:
    """~40% base participation rate per cycle, boosted by role and wave."""
    seed = stable_hash_int(account_id, "participate", str(now // 3600)) % 100

    # Effective threshold: AMPLIFIER/HARVESTER slightly more eager
    rate = _PARTICIPATION_RATE
    if role in (AccountRole.AMPLIFIER, AccountRole.HARVESTER):
        rate = min(0.60, rate + 0.10)
    if role == AccountRole.IDLE:
        rate = max(0.10, rate - 0.20)

    # Wave boost: when platform is busy, more accounts join
    wave_boost = _wave_intensity(now) * 0.15
    rate = min(0.80, rate + wave_boost)

    return seed < int(rate * 100)


def _niche_for_cycle(account_id: str, dominant_niche: str, now: int) -> str:
    """Which content niche to focus on this cycle.

    90% of the time: account's dominant niche (persona-driven).
    10% of the time: explore a different niche (soft diversity).
    """
    seed = stable_hash_int(account_id, "niche_cycle", str(now // 3600)) % 100
    if seed < 10:
        from core.persona_engine import NICHES
        alt_seed = stable_hash_int(account_id, "niche_alt", str(now // 3600)) % len(NICHES)
        return NICHES[alt_seed]
    return dominant_niche


# ─────────────────────────────────────────────────────────────────────────────
# Intent mapping per role
# ─────────────────────────────────────────────────────────────────────────────

# Role → possible intents, in priority order
_ROLE_INTENT_MAP: dict[str, list[tuple[IntentType, int]]] = {
    "WARMER":    [(IntentType.BROWSE, 60), (IntentType.ENGAGE, 30), (IntentType.IDLE, 10)],
    "EXPLORER":  [(IntentType.BROWSE, 40), (IntentType.ENGAGE, 40), (IntentType.POST,  20)],
    "AMPLIFIER": [(IntentType.ENGAGE, 50), (IntentType.POST,   30), (IntentType.BROWSE, 20)],
    "HARVESTER": [(IntentType.POST,   50), (IntentType.ENGAGE, 40), (IntentType.BROWSE, 10)],
    "IDLE":      [(IntentType.IDLE,   70), (IntentType.BROWSE, 25), (IntentType.ENGAGE,  5)],
}


def _pick_intent(account_id: str, role: AccountRole, now: int) -> IntentType:
    """Pick intent type deterministically from role's weighted distribution."""
    options = _ROLE_INTENT_MAP.get(role.value, [(IntentType.BROWSE, 100)])
    total = sum(w for _, w in options)
    seed  = stable_hash_int(account_id, "intent_pick", str(now // 1800)) % total
    cumulative = 0
    for intent, w in options:
        cumulative += w
        if seed < cumulative:
            return intent
    return options[-1][0]


def _timing_offset(account_id: str, role: AccountRole, now: int) -> int:
    """Return timing offset in seconds. Staggered per role, jittered per account."""
    lo, hi = _ROLE_TIMING_OFFSETS.get(role.value, (0, 600))
    span   = hi - lo
    seed   = stable_hash_int(account_id, "timing_off", str(now // 3600)) % max(1, span)
    base   = lo + seed

    # Per-account jitter ±60s prevents identical timing even within same role
    jitter_seed = stable_hash_int(account_id, "timing_jitter", str(now)) % 120
    jitter      = jitter_seed - 60
    return max(0, base + jitter)


def _intensity(
    account_id: str,
    role: AccountRole,
    activity_bias: float,
    wave: float,
    trend: float,
    now: int,
) -> float:
    """Compute action intensity 0.0–1.0.

    Combines persona activity bias, platform wave, trend sensitivity, and
    per-account noise. Fully deterministic.
    """
    # Base from persona
    base = 0.3 + activity_bias * 0.5    # 0.3–0.8

    # Role multiplier
    role_mult = {
        "WARMER":    0.60,
        "EXPLORER":  0.80,
        "AMPLIFIER": 1.10,
        "HARVESTER": 1.20,
        "IDLE":      0.20,
    }.get(role.value, 1.0)

    # Environmental boost
    env = 1.0 + wave * 0.15 + trend * 0.10

    # Per-account noise ±5%
    noise = 0.95 + (_normalized_noise(account_id, "intensity", spread=0.05) - 1.0) + 1.0
    noise = max(0.95, min(1.05, noise))

    result = base * role_mult * env * noise
    return round(max(0.0, min(1.0, result)), 3)


# ─────────────────────────────────────────────────────────────────────────────
# Public API: plan_actions
# ─────────────────────────────────────────────────────────────────────────────

def plan_actions(
    account_id: str,
    platform: str = "generic",
    created_ts: int = 0,
    now: int | None = None,
    risk_score: float = 0.0,
) -> ActionPlan | None:
    """Plan one cycle of actions for account_id.

    Returns None when:
      - Account does not participate this cycle (~60% of the time)
      - Risk score is critically high (≥0.90)

    Returns ActionPlan with:
      - role, intent_type, intensity, timing_offset, niche, reasoning
    """
    if now is None:
        now = int(time.time())

    # Hard gate 1: caller-provided critical risk → no action
    if risk_score >= 0.90:
        LOGGER.debug("strategy_skip account=%s reason=critical_risk score=%.3f", account_id, risk_score)
        return None

    # ── Detector Simulator gate (Part 6) ─────────────────────────────────────
    # Read the smoothed detection risk for this account (lazy import).
    _detector_risk = 0.0
    try:
        from core.detector_simulator import get_risk_score as _det_risk
        _detector_risk = _det_risk(account_id)
    except Exception:
        pass

    if _detector_risk >= 0.85:
        LOGGER.info(
            "strategy_skip account=%s reason=detector_risk det_score=%.3f",
            account_id, _detector_risk,
        )
        return None

    # ── Lifecycle integration (Part 6) ────────────────────────────────────────
    lifecycle_stage   = None
    lifecycle_profile = None
    lifecycle_niche   = None
    lifecycle_act_mult = 1.0
    try:
        from core.lifecycle_engine import (
            get_lifecycle_stage, get_stage_profile, get_role_allowlist,
            sample_niche, LifecycleStage,
        )
        lifecycle_stage   = get_lifecycle_stage(account_id, created_ts, now)
        lifecycle_profile = get_stage_profile(lifecycle_stage)
        lifecycle_act_mult = lifecycle_profile.activity_multiplier
        lifecycle_niche   = sample_niche(account_id, now, lifecycle_stage, created_ts)
    except Exception as exc:
        LOGGER.warning("strategy_lifecycle_error account=%s error=%s", account_id, exc)

    # Fetch persona
    persona_activity = 0.5
    persona_risk_tol = 0.5
    dominant_niche   = "entertainment"
    try:
        from core.persona_engine import get_persona_engine
        pe      = get_persona_engine()
        persona = pe.get(account_id)
        persona_activity = persona.activity_bias
        persona_risk_tol = persona.risk_tolerance
        dominant_niche   = persona.dominant_niche()
    except Exception as exc:
        LOGGER.warning("strategy_persona_error account=%s error=%s", account_id, exc)

    # Assign role
    role = assign_role(
        account_id,
        created_ts      = created_ts,
        now             = now,
        persona_risk_tolerance = persona_risk_tol,
        persona_activity_bias  = persona_activity,
    )

    # Participation gate
    if not _should_participate(account_id, now, role):
        LOGGER.debug("strategy_skip account=%s role=%s reason=non_participant", account_id, role.value)
        return None

    # Moderate risk → demote HARVESTER to AMPLIFIER
    if risk_score >= 0.50 and role == AccountRole.HARVESTER:
        role = AccountRole.AMPLIFIER
        LOGGER.debug("strategy_demote account=%s reason=elevated_risk", account_id)

    # ── Lifecycle role gating (Part 6) ────────────────────────────────────────
    # If role is outside the lifecycle allowlist, demote to the closest safe role.
    if lifecycle_stage is not None:
        try:
            allowlist = get_role_allowlist(lifecycle_stage)
            if allowlist and role.value not in allowlist:
                # Pick the highest-weight role that is allowed
                fallback = "WARMER" if "WARMER" in allowlist else next(iter(allowlist))
                LOGGER.debug(
                    "strategy_lifecycle_gate account=%s stage=%s role=%s→%s",
                    account_id, lifecycle_stage.value, role.value, fallback,
                )
                role = AccountRole(fallback)
        except Exception as exc:
            LOGGER.warning("strategy_lifecycle_gate_error account=%s error=%s", account_id, exc)

    # Read shared environment
    wave  = _wave_intensity(now)
    trend = _trend_intensity(now)

    # Intent + timing + intensity
    intent  = _pick_intent(account_id, role, now)
    offset  = _timing_offset(account_id, role, now)
    intens  = _intensity(account_id, role, persona_activity, wave, trend, now)

    # ── Lifecycle intensity scaling (Part 6) ──────────────────────────────────
    intens  = round(max(0.0, min(1.0, intens * lifecycle_act_mult)), 3)

    # Niche: prefer lifecycle niche over persona niche (more specific)
    niche = lifecycle_niche or _niche_for_cycle(account_id, dominant_niche, now)

    reason = (
        f"role={role.value} intent={intent.value} "
        f"wave={wave:.2f} trend={trend:.2f} "
        f"activity={persona_activity:.2f} risk_tol={persona_risk_tol:.2f} "
        f"ban_rate={_ENGINE_STATE.recent_ban_rate:.3f} "
        f"lifecycle={lifecycle_stage.value if lifecycle_stage else 'n/a'} "
        f"act_mult={lifecycle_act_mult:.2f}"
    )

    plan = ActionPlan(
        account_id    = account_id,
        role          = role,
        intent_type   = intent,
        intensity     = intens,
        timing_offset = offset,
        platform      = platform,
        reasoning     = reason,
        niche         = niche,
    )
    LOGGER.info(
        "strategy_plan account=%s role=%s intent=%s intensity=%.2f offset=%ds niche=%s platform=%s lifecycle=%s",
        account_id, role.value, intent.value, intens, offset, niche, platform,
        lifecycle_stage.value if lifecycle_stage else "n/a",
    )
    return plan


# ─────────────────────────────────────────────────────────────────────────────
# Feedback loop: record outcomes + update engine state
# ─────────────────────────────────────────────────────────────────────────────

def record_outcome(
    account_id: str,
    role: AccountRole | str,
    intent_type: IntentType | str,
    success: bool,
    ban: bool,
) -> None:
    """Record a strategy cycle outcome and update global feedback metrics."""
    role_str   = role.value if isinstance(role, AccountRole) else role
    intent_str = intent_type.value if isinstance(intent_type, IntentType) else intent_type

    outcome = StrategyOutcome(
        account_id  = account_id,
        role        = role_str,
        intent_type = intent_str,
        success     = success,
        ban         = ban,
        ts          = time.time(),
        weight      = 1.0,
    )

    history = _OUTCOME_HISTORY.setdefault(account_id, [])
    history.append(outcome)

    # Decay weights of older entries
    for entry in history:
        entry.weight *= _OUTCOME_DECAY

    # Cap history depth
    if len(history) > _OUTCOME_MEMORY_DEPTH:
        history.pop(0)

    # Update global feedback state (weighted averages across ALL accounts)
    _refresh_engine_state()

    LOGGER.info(
        "strategy_outcome account=%s role=%s success=%s ban=%s",
        account_id, role_str, success, ban,
    )


def _refresh_engine_state() -> None:
    """Recompute ban_rate + success_rate from all recent weighted outcomes."""
    all_outcomes: list[StrategyOutcome] = []
    for hist in _OUTCOME_HISTORY.values():
        all_outcomes.extend(hist)

    if not all_outcomes:
        return

    total_w   = sum(o.weight for o in all_outcomes)
    ban_w     = sum(o.weight for o in all_outcomes if o.ban)
    success_w = sum(o.weight for o in all_outcomes if o.success)

    _ENGINE_STATE.recent_ban_rate     = ban_w     / total_w if total_w else 0.0
    _ENGINE_STATE.recent_success_rate = success_w / total_w if total_w else 1.0
    _ENGINE_STATE.last_updated        = time.time()

    LOGGER.debug(
        "strategy_state ban_rate=%.3f success_rate=%.3f outcomes=%d",
        _ENGINE_STATE.recent_ban_rate, _ENGINE_STATE.recent_success_rate, len(all_outcomes),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Observability helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_engine_state() -> dict[str, Any]:
    """Return a snapshot of global engine state for monitoring."""
    return {
        "recent_ban_rate":     _ENGINE_STATE.recent_ban_rate,
        "recent_success_rate": _ENGINE_STATE.recent_success_rate,
        "anomaly_score":       _ENGINE_STATE.anomaly_score,
        "last_updated":        _ENGINE_STATE.last_updated,
        "accounts_tracked":    len(_OUTCOME_HISTORY),
    }


def get_role_distribution(account_ids: list[str], now: int | None = None) -> dict[str, int]:
    """Return role counts across a list of accounts (for observability/tests)."""
    if now is None:
        now = int(time.time())
    counts: dict[str, int] = {r.value: 0 for r in AccountRole}
    for acct in account_ids:
        role = assign_role(acct, now=now)
        counts[role.value] += 1
    return counts


# ─────────────────────────────────────────────────────────────────────────────
# StrategyEngine: public entry point
# ─────────────────────────────────────────────────────────────────────────────

class StrategyEngine:
    """High-level orchestrator. Thread-safe for concurrent account calls.

    Usage:
        engine = get_strategy_engine()
        plan   = engine.plan(account_id, platform="tiktok", risk_score=0.2)
        if plan:
            # execute plan.intent_type with plan.timing_offset delay
            ...
            engine.record(account_id, plan, success=True, ban=False)
    """

    def plan(
        self,
        account_id: str,
        platform:   str   = "generic",
        created_ts: int   = 0,
        risk_score: float = 0.0,
        now:        int | None = None,
    ) -> ActionPlan | None:
        return plan_actions(
            account_id  = account_id,
            platform    = platform,
            created_ts  = created_ts,
            now         = now,
            risk_score  = risk_score,
        )

    def record(
        self,
        account_id:  str,
        plan:        ActionPlan,
        success:     bool,
        ban:         bool,
    ) -> None:
        record_outcome(
            account_id  = account_id,
            role        = plan.role,
            intent_type = plan.intent_type,
            success     = success,
            ban         = ban,
        )

    def state(self) -> dict[str, Any]:
        return get_engine_state()


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_STRATEGY_ENGINE: StrategyEngine | None = None


def get_strategy_engine() -> StrategyEngine:
    """Return the process-level StrategyEngine singleton."""
    global _STRATEGY_ENGINE
    if _STRATEGY_ENGINE is None:
        _STRATEGY_ENGINE = StrategyEngine()
    return _STRATEGY_ENGINE


def _reset_for_testing() -> None:
    """Hard reset all module-level state. For tests only."""
    global _STRATEGY_ENGINE
    _STRATEGY_ENGINE = None
    _OUTCOME_HISTORY.clear()
    _ROLE_CACHE.clear()
    _ENGINE_STATE.recent_ban_rate     = 0.0
    _ENGINE_STATE.recent_success_rate = 1.0
    _ENGINE_STATE.anomaly_score       = 0.0
    _ENGINE_STATE.last_updated        = 0.0
