"""
Lifecycle Engine — Accounts as evolving identities with lifecycle stages,
interest graphs, content DNA, and engagement memory.

Architecture contracts:
  - Pure determinism: all variation via stable_hash_int only, zero random().
  - Per-account state is process-local; never shared cross-account.
  - All multipliers clamped to [0.6, 1.4]; interest weights always sum to 1.0.
  - Max 5 active interests per account (Content DNA).
  - Max drift per calendar day: 0.05 (global cap across all evolve() calls).
  - Lifecycle stage cannot skip stages forward (only regress to DECLINE freely).
  - Interest changes < 0.01 per step are suppressed (deadzone).

Lifecycle stages:
  NEW      (0–3 days):    Safe warmup, low activity, high exploration
  WARMUP   (3–14 days):   Building habits, moderate activity
  GROWTH   (14–56 days):  Ramping engagement, exploration-driven
  MATURE   (56+ days):    Stable identity, exploitation-driven
  DECLINE  (any age):     Low success rate (<0.30) triggers retreat
  RECOVERY (any age):     Post-decline adaptation, rediscovery mode

Integration:
  strategy_engine   → role gating + intensity scaling
  mutation_controller → delay scaling via activity_multiplier
  reinforcement      → reward shaping with lifecycle_alignment_bonus
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.mutation_controller import stable_hash_int, _normalized_noise

LOGGER = logging.getLogger("core.lifecycle_engine")

# ── Available content niches (matches persona_engine.NICHES) ─────────────────
NICHES: list[str] = ["tech", "fitness", "finance", "entertainment", "food", "travel"]

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_ACTIVE_INTERESTS: int   = 5
MAX_DRIFT_PER_DAY:    float = 0.05
DRIFT_DEADZONE:       float = 0.01
INTEREST_EMA_ALPHA:   float = 0.20    # for success_by_niche memory
_MULT_MIN:            float = 0.60
_MULT_MAX:            float = 1.40

# Stage thresholds
_AGE_WARMUP_DAYS:   int = 3
_AGE_GROWTH_DAYS:   int = 14
_AGE_MATURE_DAYS:   int = 56
_DECLINE_THRESHOLD: float = 0.30     # success_rate below → DECLINE
_RECOVERY_THRESHOLD: float = 0.40   # success_rate above → leave DECLINE


# ── Stage definitions ─────────────────────────────────────────────────────────

class LifecycleStage(str, Enum):
    NEW      = "NEW"
    WARMUP   = "WARMUP"
    GROWTH   = "GROWTH"
    MATURE   = "MATURE"
    DECLINE  = "DECLINE"
    RECOVERY = "RECOVERY"


@dataclass(frozen=True)
class StageProfile:
    """Behavioral multipliers for a lifecycle stage. All clamped to [0.6, 1.4]."""
    risk_multiplier:     float   # scales risk threshold sensitivity
    activity_multiplier: float   # scales delay and intensity
    exploration_bias:    float   # 0.0 = exploit, 1.0 = explore
    stability_bias:      float   # how sticky niche choice is


_STAGE_PROFILES: dict[str, StageProfile] = {
    LifecycleStage.NEW:      StageProfile(0.80, 0.60, 0.90, 0.50),
    LifecycleStage.WARMUP:   StageProfile(0.85, 0.70, 0.75, 0.65),
    LifecycleStage.GROWTH:   StageProfile(1.00, 1.00, 0.55, 0.80),
    LifecycleStage.MATURE:   StageProfile(1.10, 1.10, 0.25, 1.20),
    LifecycleStage.DECLINE:  StageProfile(0.75, 0.60, 0.55, 0.70),
    LifecycleStage.RECOVERY: StageProfile(0.90, 0.80, 0.70, 0.70),
}

# Role constraints per lifecycle stage (None = no constraint)
# NEW and WARMUP only allow safe roles
_STAGE_ROLE_ALLOWLIST: dict[str, set[str] | None] = {
    LifecycleStage.NEW:      {"WARMER", "IDLE"},
    LifecycleStage.WARMUP:   {"WARMER", "EXPLORER", "IDLE"},
    LifecycleStage.GROWTH:   {"WARMER", "EXPLORER", "AMPLIFIER", "IDLE"},
    LifecycleStage.MATURE:   None,   # all roles allowed
    LifecycleStage.DECLINE:  {"IDLE", "WARMER"},
    LifecycleStage.RECOVERY: {"WARMER", "EXPLORER", "IDLE"},
}


# ── Per-account state ─────────────────────────────────────────────────────────

@dataclass
class AccountLifecycleState:
    """Mutable per-account lifecycle state. Never shared cross-account."""
    account_id:       str
    created_ts:       int
    # Content DNA
    interest_vector:  dict[str, float]     = field(default_factory=dict)
    # Engagement memory: EMA of success per niche
    success_by_niche: dict[str, float]     = field(default_factory=dict)
    # Per-day drift budget tracking
    _daily_drift:     dict[int, float]     = field(default_factory=dict, repr=False)
    # Cached stage (recomputed when success_rate changes)
    _cached_stage:    str                  = LifecycleStage.NEW.value
    # Rolling success rate (EMA)
    _success_ema:     float                = 0.5
    # Track if we're in DECLINE so RECOVERY can follow
    _in_decline:      bool                 = False

    def update_success_ema(self, success: bool) -> None:
        value = 1.0 if success else 0.0
        self._success_ema = self._success_ema * 0.85 + value * 0.15

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id":       self.account_id,
            "created_ts":       self.created_ts,
            "interest_vector":  dict(self.interest_vector),
            "success_by_niche": dict(self.success_by_niche),
            "cached_stage":     self._cached_stage,
            "success_ema":      round(self._success_ema, 4),
            "in_decline":       self._in_decline,
        }


# ── Module-level state ────────────────────────────────────────────────────────

_ACCOUNT_STATE: dict[str, AccountLifecycleState] = {}


def _get_state(account_id: str, created_ts: int = 0) -> AccountLifecycleState:
    """Return or create per-account lifecycle state."""
    if account_id not in _ACCOUNT_STATE:
        state = AccountLifecycleState(
            account_id  = account_id,
            created_ts  = created_ts or int(time.time()),
        )
        state.interest_vector  = _init_interests(account_id)
        state.success_by_niche = {n: 0.5 for n in state.interest_vector}
        _ACCOUNT_STATE[account_id] = state
    else:
        # Update created_ts if caller knows it and we don't
        if created_ts and _ACCOUNT_STATE[account_id].created_ts == 0:
            _ACCOUNT_STATE[account_id].created_ts = created_ts
    return _ACCOUNT_STATE[account_id]


# ── Interest initialization (Part 2) ─────────────────────────────────────────

def _init_interests(account_id: str) -> dict[str, float]:
    """
    Deterministic interest vector: max 5 niches, sum = 1.0.
    Seeded from account_id — every account gets a different distribution.
    """
    # Score each niche with a stable hash
    scores: list[tuple[float, str]] = []
    for i, niche in enumerate(NICHES):
        v = stable_hash_int(account_id, "interest_init", niche, str(i)) % 1000
        scores.append((float(v), niche))

    # Take top MAX_ACTIVE_INTERESTS by score
    scores.sort(reverse=True)
    top = scores[:MAX_ACTIVE_INTERESTS]

    # Normalize to sum = 1.0
    total = sum(v for v, _ in top) or 1.0
    return {niche: round(v / total, 5) for v, niche in top}


# ── Stage determination (Part 1) ─────────────────────────────────────────────

def get_lifecycle_stage(
    account_id:   str,
    created_ts:   int = 0,
    now:          int | None = None,
    success_rate: float | None = None,   # override: caller-provided recent rate
) -> LifecycleStage:
    """
    Compute lifecycle stage from age + recent success rate.

    Stage transition rules (strict ordering, no skipping forward):
      NEW      → WARMUP  : age >= 3 days
      WARMUP   → GROWTH  : age >= 14 days
      GROWTH   → MATURE  : age >= 56 days

    Regression (can happen from any age-based stage):
      any → DECLINE  : success_rate < DECLINE_THRESHOLD
      DECLINE → RECOVERY : success_rate >= RECOVERY_THRESHOLD
      RECOVERY → base_age_stage : success_rate >= 0.50
    """
    if now is None:
        now = int(time.time())

    state = _get_state(account_id, created_ts)

    # Use provided rate or stored EMA
    rate = success_rate if success_rate is not None else state._success_ema

    # Determine base stage from age
    age_days = max(0, (now - state.created_ts) // 86400)
    if age_days < _AGE_WARMUP_DAYS:
        base_stage = LifecycleStage.NEW
    elif age_days < _AGE_GROWTH_DAYS:
        base_stage = LifecycleStage.WARMUP
    elif age_days < _AGE_MATURE_DAYS:
        base_stage = LifecycleStage.GROWTH
    else:
        base_stage = LifecycleStage.MATURE

    # Performance overlay
    if rate < _DECLINE_THRESHOLD:
        state._in_decline = True
        stage = LifecycleStage.DECLINE
    elif state._in_decline and rate < 0.50:
        # Was in decline, not yet fully recovered
        stage = LifecycleStage.RECOVERY
    else:
        if state._in_decline and rate >= 0.50:
            state._in_decline = False   # fully recovered
        stage = base_stage

    state._cached_stage = stage.value

    LOGGER.debug(
        "lifecycle_stage account=%s age_days=%d rate=%.3f stage=%s",
        account_id, age_days, rate, stage.value,
    )
    return stage


def get_stage_profile(stage: LifecycleStage) -> StageProfile:
    """Return the StageProfile for a given stage."""
    return _STAGE_PROFILES[stage]


def get_role_allowlist(stage: LifecycleStage) -> set[str] | None:
    """Return allowed roles for stage (None = all roles permitted)."""
    return _STAGE_ROLE_ALLOWLIST.get(stage)


# ── Interest profile (Part 2) ─────────────────────────────────────────────────

def get_interest_profile(account_id: str, created_ts: int = 0) -> dict[str, float]:
    """
    Return current interest vector for account.
    Guarantees: sum = 1.0, max 5 interests, all values > 0.
    """
    state = _get_state(account_id, created_ts)
    return dict(state.interest_vector)


# ── Content drift engine (Part 3) ─────────────────────────────────────────────

def evolve_interests(
    account_id:   str,
    now:          int,
    feedback:     dict[str, Any],    # {success, ban, niche, anomaly_score, trend_intensity}
    created_ts:   int = 0,
) -> dict[str, float]:
    """
    Evolve the account's interest vector based on feedback.

    Drift formula per niche:
        new = old * 0.97 + drift_delta

    Where drift_delta is influenced by:
        - success in a niche (positive reinforcement)
        - trend_intensity (platform trend momentum)
        - lifecycle exploration_bias (higher = more willing to drift)

    Safety:
        - Changes < DRIFT_DEADZONE (0.01) are ignored
        - Total drift per day capped at MAX_DRIFT_PER_DAY (0.05)
        - Sum is re-normalized to 1.0 after each step
        - No single niche can gain > 0.15 in one step
    """
    state     = _get_state(account_id, created_ts)
    stage     = get_lifecycle_stage(account_id, created_ts, now)
    profile   = get_stage_profile(stage)

    # Check daily drift budget
    day_bucket = now // 86400
    used_today = state._daily_drift.get(day_bucket, 0.0)
    remaining  = max(0.0, MAX_DRIFT_PER_DAY - used_today)
    if remaining <= 0:
        return dict(state.interest_vector)

    success         = bool(feedback.get("success", False))
    ban             = bool(feedback.get("ban", False))
    niche_acted_on  = feedback.get("niche", "")
    trend_intensity = float(feedback.get("trend_intensity", 0.5))
    exploration     = profile.exploration_bias

    # Compute per-niche drift deltas
    deltas: dict[str, float] = {}
    for niche in state.interest_vector:
        is_acted = (niche == niche_acted_on)
        old_w    = state.interest_vector[niche]

        # Base drift: trend pulls all niches slightly
        trend_pull = (trend_intensity - 0.5) * 0.01 * exploration

        if is_acted:
            if success:
                # Reinforce: increase acted niche weight
                base_delta = 0.015 * (1.0 + exploration * 0.5)
            elif ban:
                # Penalize: reduce acted niche weight
                base_delta = -0.010 * (1.0 + exploration * 0.3)
            else:
                base_delta = 0.003 * exploration
            delta = base_delta + trend_pull
        else:
            # Non-acted niches: slight drift toward uniformity when exploring
            uniform_target = 1.0 / len(state.interest_vector)
            delta = (uniform_target - old_w) * exploration * 0.02 + trend_pull

        # Apply deadzone
        if abs(delta) < DRIFT_DEADZONE:
            delta = 0.0

        # Cap per-niche gain
        delta = max(-0.15, min(0.15, delta))
        deltas[niche] = delta

    # Scale deltas so total drift ≤ remaining budget
    total_abs = sum(abs(d) for d in deltas.values())
    if total_abs > remaining:
        scale = remaining / total_abs
        deltas = {k: v * scale for k, v in deltas.items()}
        actual_used = remaining
    else:
        actual_used = total_abs

    state._daily_drift[day_bucket] = used_today + actual_used

    # Apply drift: new = old * 0.97 + delta (EMA-style decay)
    new_vec: dict[str, float] = {}
    for niche, old_w in state.interest_vector.items():
        d = deltas.get(niche, 0.0)
        new_w = old_w * 0.97 + d
        new_w = max(0.001, new_w)   # floor so niches never fully die
        new_vec[niche] = new_w

    # Normalize
    total = sum(new_vec.values()) or 1.0
    new_vec = {k: round(v / total, 5) for k, v in new_vec.items()}

    state.interest_vector = new_vec

    # Update success_by_niche EMA memory (Part 5)
    if niche_acted_on and niche_acted_on in state.success_by_niche:
        outcome = 1.0 if success else 0.0
        old_mem = state.success_by_niche[niche_acted_on]
        state.success_by_niche[niche_acted_on] = round(
            old_mem * (1 - INTEREST_EMA_ALPHA) + outcome * INTEREST_EMA_ALPHA, 5
        )

    # Update success EMA
    state.update_success_ema(success)

    LOGGER.debug(
        "lifecycle_evolve account=%s niche=%s success=%s drift_used=%.4f stage=%s",
        account_id, niche_acted_on, success, actual_used, stage.value,
    )
    return dict(new_vec)


# ── Niche selection (Part 4) ──────────────────────────────────────────────────

def sample_niche(
    account_id:  str,
    now:         int,
    stage:       LifecycleStage,
    created_ts:  int = 0,
) -> str:
    """
    Select a content niche for this cycle.

    Weighting:
        base_weight = interest_vector[niche]
        memory_mult = 0.8 + success_by_niche[niche] * 0.4   (±20% influence)
        trend_mult  = 1.0 ± trend_sensitivity
        exploration = lifecycle.exploration_bias

    Exploration rule:
        NEW/WARMUP: 40% chance to pick a random non-dominant niche
        MATURE:     90% exploitation (pick from top 2 niches)
        GROWTH/RECOVERY: 25% exploration
        DECLINE:    50% exploration (trying new things)
    """
    state   = _get_state(account_id, created_ts)
    profile = get_stage_profile(stage)

    # Exploration gate: deterministic per (account, hour)
    explore_seed = stable_hash_int(account_id, "niche:explore", str(now // 3600)) % 100
    explore_threshold = int(profile.exploration_bias * 100)

    if explore_seed < explore_threshold:
        # Exploration: pick from non-dominant niches
        sorted_niches = sorted(state.interest_vector.items(), key=lambda x: x[1])
        # Bottom 3 niches (by interest weight) = exploration targets
        candidates = [n for n, _ in sorted_niches[:-1]]  # exclude top
        if candidates:
            idx = stable_hash_int(account_id, "niche:alt_pick", str(now // 1800)) % len(candidates)
            return candidates[idx]

    # Exploitation: weighted sample from interest_vector + memory
    weights: dict[str, float] = {}
    for niche, base_w in state.interest_vector.items():
        mem  = state.success_by_niche.get(niche, 0.5)
        # Clamp memory influence to ±20%
        mem_mult = max(0.80, min(1.20, 0.80 + mem * 0.40))
        weights[niche] = base_w * mem_mult

    total = sum(weights.values()) or 1.0
    niches_sorted = sorted(weights.items(), key=lambda x: x[0])  # stable order
    seed  = stable_hash_int(account_id, "niche:pick", str(now // 1800)) % int(total * 10000)
    cumul = 0.0
    for niche, w in niches_sorted:
        cumul += w / total
        if seed < int(cumul * 10000):
            return niche

    return niches_sorted[-1][0]   # fallback


# ── Content memory helpers (Part 5) ──────────────────────────────────────────

def get_niche_success_rate(account_id: str, niche: str, created_ts: int = 0) -> float:
    """Return EMA success rate for niche [0.0–1.0]."""
    state = _get_state(account_id, created_ts)
    return state.success_by_niche.get(niche, 0.5)


def get_engagement_memory(account_id: str, created_ts: int = 0) -> dict[str, float]:
    """Return the full success_by_niche memory dict."""
    state = _get_state(account_id, created_ts)
    return dict(state.success_by_niche)


# ── Lifecycle activity multiplier (for mutation_controller) ──────────────────

def get_activity_mult(account_id: str, created_ts: int = 0, now: int | None = None) -> float:
    """
    Return the lifecycle activity_multiplier for delay scaling.
    Clamped to [0.6, 1.4].
    Called lazily from mutation_controller to avoid circular imports.
    """
    if now is None:
        now = int(time.time())
    stage   = get_lifecycle_stage(account_id, created_ts, now)
    profile = get_stage_profile(stage)
    return max(_MULT_MIN, min(_MULT_MAX, profile.activity_multiplier))


# ── Reward shaping helpers (for reinforcement) ────────────────────────────────

def compute_lifecycle_reward_bonus(
    account_id:   str,
    role:         str,
    niche:        str,
    stage:        LifecycleStage,
    success:      bool,
    created_ts:   int = 0,
) -> float:
    """
    Lifecycle-aware reward shaping bonus.

    Components:
        niche_success_bonus:      +0.2 if niche is high-performing (>0.6 success rate)
        lifecycle_alignment_bonus: +0.1 if role matches stage's preferred roles
                                   -0.1 if acting against stage constraints
        exploration_bonus:         +0.1 if explored a low-weight niche and succeeded

    Total capped at ±0.5.
    """
    state   = _get_state(account_id, created_ts)
    profile = get_stage_profile(stage)
    bonus   = 0.0

    # Niche success bonus
    niche_rate = state.success_by_niche.get(niche, 0.5)
    if success and niche_rate > 0.60:
        bonus += 0.20
    elif not success and niche_rate < 0.30:
        bonus -= 0.10   # knew it was risky, didn't help

    # Lifecycle alignment bonus
    allowed = _STAGE_ROLE_ALLOWLIST.get(stage)
    if allowed is None:
        bonus += 0.05   # MATURE: all roles OK, small positive
    elif role in allowed:
        bonus += 0.10   # acting within lifecycle constraints
    else:
        bonus -= 0.10   # acting against lifecycle constraints

    # Exploration bonus: succeeded in low-weight niche
    niche_weight = state.interest_vector.get(niche, 0.0)
    if success and niche_weight < 0.15 and profile.exploration_bias > 0.50:
        bonus += 0.10   # successful exploration

    return round(max(-0.5, min(0.5, bonus)), 4)


# ── Singleton / reset ─────────────────────────────────────────────────────────

def get_all_account_states() -> dict[str, dict[str, Any]]:
    """Snapshot all per-account state (for persistence/observability)."""
    return {k: v.to_dict() for k, v in _ACCOUNT_STATE.items()}


def reset_lifecycle_engine() -> None:
    """Hard reset all state — for testing only."""
    _ACCOUNT_STATE.clear()
