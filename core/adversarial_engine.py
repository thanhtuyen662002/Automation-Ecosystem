"""
Adversarial Adaptation Engine — Closes the agent-vs-detector feedback loop.

This module implements Parts 2–5 of the adversarial upgrade:

  Part 2 — Risk-Component Feedback → Optimizer / Strategy gating
    Extended detector output (risk_components dict) feeds into:
      - Optimizer: component-specific tuning of delay, burstiness, entropy
      - Strategy: hard-stop at risk > 0.85, role restriction at risk 0.60-0.85

  Part 3 — Reinforcement Penalty Injection
    Extra reward shaping:  reward -= spike_penalty * 0.2
    (risk_score penalty already implemented in reinforcement.py)

  Part 4 — Slow Adaptation Cycle (fleet-level EMA pressure tracker)
    Every N cycles:
      detector_pressure = EMA(risk_score, alpha=0.10)
      rising pressure  → increase delay, exploration, entropy
      falling pressure → allow more burst, more exploitation
    All changes clamped ±10%/cycle, total bounds [0.6, 1.4]

  Part 5 — Diversity Injection (per-account jitter, anti-sync)
    exploration_rate += deterministic jitter derived from account_id
    Tie-break jitter in batch ranking via stable_hash_int

Architecture contracts:
  - Deterministic: all variation via stable_hash_int only.
  - Bounded: all outputs in [0.6, 1.4] or [0.0, 1.0] as appropriate.
  - No cross-account state mutation.
  - Stateless per-call except for _FLEET_PRESSURE EMA accumulator.
"""
from __future__ import annotations

import logging
import math
from typing import Any

from core.mutation_controller import stable_hash_int

LOGGER = logging.getLogger("core.adversarial_engine")

# ── Constants ─────────────────────────────────────────────────────────────────

# Part 2 — Risk thresholds for strategy gating
RISK_HARD_STOP:    float = 0.85   # plan_actions returns None above this
RISK_RESTRICT:     float = 0.60   # HARVESTER disabled, AMPLIFIER reduced

# Part 4 — Slow adaptation cycle
_PRESSURE_EMA_ALPHA: float = 0.10           # slow-moving fleet pressure
_ADAPT_CLAMP:        float = 0.10           # max ±10% per cycle
_MULT_MIN:           float = 0.60
_MULT_MAX:           float = 1.40

# Part 5 — Per-account jitter bounds (adds ±0.025 exploration spread)
_JITTER_SCALE: float = 0.05

# ── Fleet-level pressure state ─────────────────────────────────────────────────

# fleet_id → (pressure_ema, cycle_count)
_FLEET_PRESSURE: dict[str, tuple[float, int]] = {}

# Part 2/3 — Per-account exploration rate EMA cache (anti-jitter)
# account_id → smoothed_rate
_EXPLORATION_CACHE: dict[str, float] = {}


def reset_adversarial_engine() -> None:
    """Full state reset — for testing only."""
    _FLEET_PRESSURE.clear()
    _EXPLORATION_CACHE.clear()


# ── Part 2 — Risk component parser ────────────────────────────────────────────

def parse_risk_components(det_result: Any) -> dict[str, float]:
    """
    Extract per-component sub-scores from a DetectionResult.

    Returns dict with keys: timing, entropy, session, similarity, lifecycle.
    Falls back to zeros if field is missing (backward-compat).
    """
    try:
        return dict(det_result.sub_scores)   # already the right shape
    except AttributeError:
        return {
            "timing":     0.0,
            "entropy":    0.0,
            "session":    0.0,
            "similarity": 0.0,
            "lifecycle":  0.0,
        }


def optimizer_risk_feedback(
    optimizer:      Any,                    # core.optimizer.Optimizer instance
    risk_score:     float,
    risk_components: dict[str, float],
) -> None:
    """
    Part 2: Apply per-component risk signals to Optimizer state.

    Supplements the existing optimizer.update() call with fine-grained
    component feedback that the generic anomaly_score can't capture.

    This function directly nudges optimizer._state values — it should be
    called AFTER optimizer.update() so generic signals don't overwrite it.
    """
    import importlib
    opt_mod = importlib.import_module("core.optimizer")
    MAX_D   = opt_mod.MAX_DELTA_PER_CYCLE
    EMA     = opt_mod._EMA_ALPHA

    def _nudge(key: str, delta: float) -> None:
        """Apply bounded EMA nudge to optimizer state key."""
        delta  = max(-MAX_D, min(MAX_D, delta))
        target = optimizer._state.get(key, 1.0) + delta
        optimizer._state[key] = round(
            max(_MULT_MIN, min(_MULT_MAX,
                optimizer._state.get(key, 1.0) * (1 - EMA) + target * EMA
            )), 5
        )

    timing_s     = risk_components.get("timing",     0.0)
    entropy_s    = risk_components.get("entropy",    0.0)
    similarity_s = risk_components.get("similarity", 0.0)

    # High timing anomaly → slow down
    if timing_s > 0.70:
        _nudge("platform_delay_base_mult",  0.05)
        _nudge("platform_burstiness_mult", -0.05)

    # Low behavioral entropy → push more exploration
    if entropy_s > 0.70:
        _nudge("strategy_explorer_weight_mult",  0.05)
        _nudge("strategy_harvester_weight_mult", -0.04)

    # High cross-account similarity → increase diversity weight
    if similarity_s > 0.75:
        _nudge("strategy_explorer_weight_mult",  0.06)
        _nudge("strategy_amplifier_weight_mult", -0.04)

    LOGGER.debug(
        "adversarial_optimizer_feedback risk=%.3f timing=%.3f entropy=%.3f sim=%.3f",
        risk_score, timing_s, entropy_s, similarity_s,
    )


def strategy_risk_gate(risk_score: float, role: str) -> tuple[bool, str]:
    """
    Part 2: Evaluate whether a role is allowed given current detection risk.

    Returns:
        (allow: bool, reason: str)

    Hard stop (risk > RISK_HARD_STOP):
        All roles blocked → plan_actions should return None.

    Restriction zone (RISK_RESTRICT < risk <= RISK_HARD_STOP):
        HARVESTER → blocked
        AMPLIFIER → allowed but flagged (caller should reduce intensity)
    """
    if risk_score >= RISK_HARD_STOP:
        return False, f"hard_stop risk={risk_score:.3f}"

    if risk_score > RISK_RESTRICT:
        if role == "HARVESTER":
            return False, f"harvester_blocked risk={risk_score:.3f}"
        # AMPLIFIER still allowed but caller should halve intensity
        return True, f"restricted risk={risk_score:.3f}"

    return True, "ok"


# ── Part 3 — Spike penalty for reinforcement ──────────────────────────────────

def compute_spike_penalty(
    burst_density:   float,   # 0.0–1.0: actions in the last minute / capacity
    timing_anomaly:  float,   # risk_components["timing"]
) -> float:
    """
    Part 3 reward supplement: spike_penalty injected into RL reward.

    reward -= spike_penalty * 0.2

    Returns spike_penalty [0.0, 1.0].
    """
    penalty = burst_density * 0.6 + timing_anomaly * 0.4
    return round(max(0.0, min(1.0, penalty)), 4)


# ── Part 4 — Fleet pressure adaptation ───────────────────────────────────────

def update_fleet_pressure(
    fleet_id:   str,
    risk_score: float,
    optimizer:  Any,        # core.optimizer.Optimizer instance
) -> float:
    """
    Part 4: EMA-smooth the fleet-wide detection pressure and adapt optimizer.

    Called once per cycle after collecting all per-account risk scores.

    Pressure EMA:
        pressure = prev * (1 - alpha) + risk_score * alpha

    Rising pressure (vs prev cycle):
        → increase delay_mult   +5%
        → reduce burstiness     -5%
        → increase explorer     +5%

    Falling pressure:
        → allow burstiness      +3%
        → allow exploitation    -3% explorer

    All changes bounded ±10%, totals [0.6, 1.4].

    Returns updated pressure EMA.
    """
    prev_pressure, cycle_count = _FLEET_PRESSURE.get(fleet_id, (0.5, 0))
    new_pressure = prev_pressure * (1 - _PRESSURE_EMA_ALPHA) + risk_score * _PRESSURE_EMA_ALPHA
    new_pressure = round(max(0.0, min(1.0, new_pressure)), 5)

    delta = new_pressure - prev_pressure

    import importlib
    opt_mod = importlib.import_module("core.optimizer")
    MAX_D   = opt_mod.MAX_DELTA_PER_CYCLE
    EMA_A   = opt_mod._EMA_ALPHA

    def _clamp_nudge(key: str, delta: float) -> None:
        d = max(-_ADAPT_CLAMP, min(_ADAPT_CLAMP, delta))
        cur = optimizer._state.get(key, 1.0)
        tgt = cur + d
        optimizer._state[key] = round(
            max(_MULT_MIN, min(_MULT_MAX, cur * (1 - EMA_A) + tgt * EMA_A)), 5
        )

    if delta > 0.02:
        # Pressure rising — tighten up
        _clamp_nudge("platform_delay_base_mult",    +0.05)
        _clamp_nudge("platform_burstiness_mult",    -0.05)
        _clamp_nudge("strategy_explorer_weight_mult", +0.05)
        LOGGER.debug("fleet_pressure rising fleet=%s p=%.3f→%.3f", fleet_id, prev_pressure, new_pressure)

    elif delta < -0.02:
        # Pressure falling — loosen up
        _clamp_nudge("platform_burstiness_mult",     +0.03)
        _clamp_nudge("strategy_explorer_weight_mult", -0.03)
        LOGGER.debug("fleet_pressure falling fleet=%s p=%.3f→%.3f", fleet_id, prev_pressure, new_pressure)

    _FLEET_PRESSURE[fleet_id] = (new_pressure, cycle_count + 1)
    return new_pressure


def get_fleet_pressure(fleet_id: str) -> float:
    """Return current pressure EMA for a fleet (default 0.5 = neutral)."""
    p, _ = _FLEET_PRESSURE.get(fleet_id, (0.5, 0))
    return p


# ── Part 5 — Per-account exploration jitter ───────────────────────────────────

def get_exploration_rate(
    account_id:            str,
    base_exploration_bias: float,
    now:                   int = 0,
) -> float:
    """
    Part 5 (v2): Per-account deterministic exploration rate with diversity jitter.

    Two independent hash components:
      h1 — static personality hash (account_id only, never changes)
      h2 — time-varying drift (account_id + 1-hour bucket, drifts slowly)

    Blend:
        raw = 0.6 * lifecycle_bias + 0.2 * h1 + 0.2 * h2

    Then maps raw proportionally into stage bounds via clamp_exploration_rate(),
    and applies EWMA smoothing (α=0.15) against the previous call's value
    to suppress jitter spikes (Part 3).

    Returns smoothed exploration_rate in [0.0, 1.0].
    Call clamp_exploration_rate() afterwards to constrain to stage bounds.
    """
    # h1: static personality — only changes if account_id changes
    h1 = (stable_hash_int(account_id, "explore_h1") % 1000) / 1000.0
    # h2: time-varying drift — shifts every hour
    time_bucket = now // 3600 if now else 0
    h2 = (stable_hash_int(account_id, "explore_h2", str(time_bucket)) % 1000) / 1000.0

    raw = 0.6 * base_exploration_bias + 0.2 * h1 + 0.2 * h2
    raw = max(0.0, min(1.0, raw))

    # Part 3: EWMA smoothing against previous value (α=0.15 → slow drift)
    prev = _EXPLORATION_CACHE.get(account_id)
    if prev is not None:
        smoothed = prev * 0.85 + raw * 0.15
    else:
        smoothed = raw
    _EXPLORATION_CACHE[account_id] = smoothed

    return round(smoothed, 4)


def get_stage_exploration_bounds(stage: str) -> tuple[float, float]:
    """
    Part 1 constraint table: min/max exploration_rate per lifecycle stage.

    Returns (min, max) tuple.
    """
    return {
        "NEW":      (0.40, 0.70),
        "WARMUP":   (0.40, 0.70),
        "GROWTH":   (0.20, 0.40),
        "MATURE":   (0.05, 0.15),
        "DECLINE":  (0.20, 0.40),
        "RECOVERY": (0.30, 0.60),
    }.get(stage, (0.20, 0.40))


def clamp_exploration_rate(rate: float, stage: str) -> float:
    """
    Map exploration_rate into stage-appropriate bounds while preserving
    inter-account diversity.

    Instead of hard-clipping (which collapses all values above hi to hi),
    this maps the rate proportionally into [lo, hi]:

        mapped = lo + (rate % 1.0) * (hi - lo)

    This keeps accounts with different rates distinct even if their raw
    blended rate would exceed the stage ceiling.
    """
    lo, hi = get_stage_exploration_bounds(stage)
    span   = hi - lo
    if span < 0.02:
        return round(max(lo, min(hi, rate)), 4)
    # Map rate into [lo, hi] proportionally (wrapping to avoid all collapsing)
    mapped = lo + (rate % 1.0) * span
    return round(max(lo, min(hi, mapped)), 4)
