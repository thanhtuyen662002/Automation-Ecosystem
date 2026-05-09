"""
Meta-Learning Layer v2 — Contextual meta-policy over optimizer regimes.

Upgrades over v1:
  1. Contextual key: hash(param_state, risk_bucket, lifecycle_stage, platform_phase)
     → learns "what works in THIS context", not globally
  2. Top-K similar-key lookup via Hamming distance on key tokens
     → interpolates from nearby contexts when exact match is cold
  3. Uncertainty penalty: bias shrinks toward 0 when sample_count < threshold
     → no early over-commitment
  4. Per-cycle decay: META_Q *= 0.995 → forgets stale regimes
  5. Meta-exploration: 5% of cycles perturb params outside learned bias
     → prevents meta-policy collapse / local optima lock-in

Design contracts:
  - Deterministic: all key derivation uses sorted string repr (no random())
  - Bias bounded ±5% (scale ∈ [0.95, 1.05]) — hard clamp always applied
  - State is process-local; never shared cross-account
  - Backward-compatible: record_meta / get_meta_bias signatures preserved

Integration:
  optimizer.py  → get_meta_bias(params, context) → scale state after update()
  pipeline.py   → record_meta(params, reward, context) → per-cycle feedback
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Any

from core.mutation_controller import stable_hash_int

LOGGER = logging.getLogger("core.meta_learning")

# ── Tuning constants ──────────────────────────────────────────────────────────

# EMA alpha for Q-value updates
_META_ALPHA: float = 0.07        # slow learning (between 0.05 and 0.10)

# Bias clamp — max ±5% scaling
_BIAS_MIN: float = -0.05
_BIAS_MAX: float =  0.05

# Uncertainty: minimum observations before applying full bias
_MIN_OBS_THRESHOLD: int = 5

# Per-cycle decay factor (forget stale regimes)
_DECAY_FACTOR: float = 0.995

# Top-K similar keys for interpolated bias
_TOP_K: int = 3

# Meta-exploration: fraction of cycles where we perturb params
_EXPLORE_RATE_PCT: int = 5   # 5 out of every 100 record_meta() calls

# Exploration perturbation magnitude (±2.5% extra)
_EXPLORE_PERTURB: float = 0.025

# ── State ─────────────────────────────────────────────────────────────────────

# context_key → EMA Q-value
_META_Q:     defaultdict[str, float] = defaultdict(float)

# context_key → observation count
_META_COUNT: defaultdict[str, int]   = defaultdict(int)

# Global cycle counter for decay + exploration gate
_CYCLE: int = 0

# Distribution-shift tracking: EMA of fleet risk + reward
# Used by partial_reset() to detect significant distribution changes.
_SHIFT_HISTORY: dict[str, float] = {
    "risk_ema":   0.5,    # EMA of fleet avg risk score
    "reward_ema": 0.0,    # EMA of fleet avg reward
}
_SHIFT_ALPHA: float = 0.20   # EMA alpha for shift detection (faster)
_SHIFT_THRESHOLD: float = 0.15  # delta that triggers partial reset


def reset_meta_learning() -> None:
    """Full state reset — for testing only."""
    global _CYCLE
    _META_Q.clear()
    _META_COUNT.clear()
    _CYCLE = 0
    _SHIFT_HISTORY["risk_ema"]   = 0.5
    _SHIFT_HISTORY["reward_ema"] = 0.0


# ── Context helpers ───────────────────────────────────────────────────────────

def _risk_bucket(params: dict[str, Any]) -> str:
    """Coarse risk bucket from optimizer state."""
    delay = params.get("platform_delay_base_mult", 1.0)
    burst = params.get("platform_burstiness_mult", 1.0)
    # High delay + low burst → conservative
    if delay > 1.10 or burst < 0.90:
        return "high"
    if delay > 1.03 or burst < 0.97:
        return "med"
    return "low"


def _platform_phase(params: dict[str, Any]) -> str:
    """
    Infer platform phase from strategy weight mix.
    Heavy harvester/amplifier → peak; otherwise offpeak.
    """
    harv = params.get("strategy_harvester_weight_mult", 1.0)
    ampl = params.get("strategy_amplifier_weight_mult", 1.0)
    if harv > 1.02 and ampl > 1.02:
        return "peak"
    return "offpeak"


def _context_key(
    params:         dict[str, Any],
    lifecycle_stage: str = "",
    risk_bucket:    str = "",
    platform_phase: str = "",
) -> str:
    """
    Deterministic contextual key.

    Format:
        [(param_k, rounded_v), ...] | rb=<bucket> | lc=<stage> | ph=<phase>

    Rounded to 1 decimal place for coarser bucketing (more key reuse).
    """
    # Coarsen params to 1dp for better key hit-rate
    rounded = {k: round(float(v), 1) for k, v in params.items()}
    param_str = str(sorted(rounded.items()))
    rb = risk_bucket  or _risk_bucket(params)
    ph = platform_phase or _platform_phase(params)
    lc = lifecycle_stage or "unknown"
    return f"{param_str}|rb={rb}|lc={lc}|ph={ph}"


def _config_key(params: dict[str, Any]) -> str:
    """v1-compatible key (no context). Used in backward-compat path."""
    rounded = {k: round(float(v), 2) for k, v in params.items()}
    return str(sorted(rounded.items()))


# ── Hamming-distance top-K lookup ─────────────────────────────────────────────

def _hamming_distance(a: str, b: str) -> int:
    """
    Token-level Hamming distance between two context keys.
    Splits by '|' and counts non-matching tokens.
    """
    ta = a.split("|")
    tb = b.split("|")
    # Pad to same length
    length = max(len(ta), len(tb))
    ta += [""] * (length - len(ta))
    tb += [""] * (length - len(tb))
    return sum(1 for x, y in zip(ta, tb) if x != y)


def _top_k_similar(key: str) -> list[tuple[str, float, int]]:
    """
    Return up to _TOP_K most similar keys from _META_Q by Hamming distance.
    Returns list of (key, q_value, count) sorted by distance ascending.
    """
    if not _META_Q:
        return []
    scored: list[tuple[int, str]] = []
    for k in _META_Q:
        d = _hamming_distance(key, k)
        scored.append((d, k))
    scored.sort()
    result = []
    for d, k in scored[:_TOP_K]:
        result.append((k, _META_Q[k], _META_COUNT[k]))
    return result


# ── Uncertainty penalty ───────────────────────────────────────────────────────

def _uncertainty_scale(count: int) -> float:
    """
    Shrink bias toward 0 when sample count is low.

    scale = min(1.0, count / threshold)
    → 0 obs = 0 bias, threshold obs = full bias.
    """
    return min(1.0, count / max(1, _MIN_OBS_THRESHOLD))


# ── Per-cycle decay ───────────────────────────────────────────────────────────

def _apply_decay() -> None:
    """Multiply all Q-values by _DECAY_FACTOR (forget stale regimes)."""
    for k in list(_META_Q.keys()):
        _META_Q[k] = round(_META_Q[k] * _DECAY_FACTOR, 7)


# ── Meta-exploration ──────────────────────────────────────────────────────────

def _should_explore(cycle: int) -> bool:
    """Deterministic: fire on cycles where cycle % 100 < _EXPLORE_RATE_PCT."""
    return (cycle % 100) < _EXPLORE_RATE_PCT


def _exploration_perturb(
    params: dict[str, Any],
    cycle:  int,
) -> dict[str, Any]:
    """
    Deterministic perturbation of params for meta-exploration.

    For each key, perturb by ±_EXPLORE_PERTURB based on stable_hash_int.
    Returns a *copy* — original params are not mutated.
    """
    out: dict[str, Any] = {}
    for k, v in params.items():
        seed   = stable_hash_int(k, "meta_explore", str(cycle)) % 1000
        sign   = 1 if seed >= 500 else -1
        perturb = sign * _EXPLORE_PERTURB
        out[k] = round(max(0.60, min(1.40, float(v) + perturb)), 5)
    return out


# ── Core API ──────────────────────────────────────────────────────────────────

def record_meta(
    params:          dict[str, Any],
    reward:          float,
    lifecycle_stage: str = "",
    risk_bucket_str: str = "",
    platform_phase:  str = "",
) -> None:
    """
    Record a (context, reward) observation and update META_Q.

    Also applies per-cycle decay and meta-exploration (5% of cycles).
    Exception-safe: never raises.
    """
    global _CYCLE
    try:
        _CYCLE += 1

        # Per-cycle decay (every cycle, not just on record)
        _apply_decay()

        k = _context_key(params, lifecycle_stage, risk_bucket_str, platform_phase)
        _META_COUNT[k] += 1
        _META_Q[k] = (1.0 - _META_ALPHA) * _META_Q[k] + _META_ALPHA * reward

        LOGGER.debug(
            "meta_record key=%s reward=%.3f q=%.4f n=%d cycle=%d",
            k[-40:], reward, _META_Q[k], _META_COUNT[k], _CYCLE,
        )

        # Meta-exploration: on 5% of cycles, also record perturbed params
        if _should_explore(_CYCLE):
            perturbed = _exploration_perturb(params, _CYCLE)
            pk = _context_key(perturbed, lifecycle_stage, risk_bucket_str, platform_phase)
            # Record with 0.5x weight (exploration credit is partial)
            _META_COUNT[pk] += 1
            _META_Q[pk] = (1.0 - _META_ALPHA) * _META_Q[pk] + _META_ALPHA * (reward * 0.5)
            LOGGER.debug(
                "meta_explore key=%s perturbed_reward=%.3f", pk[-40:], reward * 0.5
            )

    except Exception as exc:
        LOGGER.debug("meta_record_error error=%s", exc)


def get_meta_bias(
    params:          dict[str, Any],
    lifecycle_stage: str = "",
    risk_bucket_str: str = "",
    platform_phase:  str = "",
) -> float:
    """
    Return the contextual learned bias for the current parameter regime.

    Algorithm:
        1. Compute context key for exact match
        2. If exact match found: use its Q-value with uncertainty scaling
        3. Else: interpolate from top-K similar keys (distance-weighted avg)
        4. Apply uncertainty penalty to final bias
        5. Clamp to [_BIAS_MIN, _BIAS_MAX]

    Returns 0.0 (neutral) when no observations available.
    """
    try:
        key = _context_key(params, lifecycle_stage, risk_bucket_str, platform_phase)

        if key in _META_Q and _META_COUNT[key] > 0:
            # Exact match
            raw   = _META_Q[key]
            scale = _uncertainty_scale(_META_COUNT[key])
            bias  = raw * scale
        else:
            # Top-K interpolation
            similar = _top_k_similar(key)
            if not similar:
                return 0.0
            # Distance-weighted average (closer = higher weight)
            total_w = 0.0
            weighted_q = 0.0
            for sk, sq, sc in similar:
                d = _hamming_distance(key, sk)
                w = 1.0 / (1.0 + d)   # inverse-distance weight
                u = _uncertainty_scale(sc)
                weighted_q += sq * u * w
                total_w    += w
            bias = weighted_q / total_w if total_w > 0 else 0.0

        return max(_BIAS_MIN, min(_BIAS_MAX, round(bias, 5)))

    except Exception:
        return 0.0


def snapshot() -> dict[str, Any]:
    """Observability snapshot of current meta-Q table."""
    return {
        "n_contexts":   len(_META_Q),
        "total_obs":    sum(_META_COUNT.values()),
        "cycle":        _CYCLE,
        "shift_history": dict(_SHIFT_HISTORY),
        "top_contexts": sorted(
            ((k[-60:], round(v, 5), _META_COUNT[k]) for k, v in _META_Q.items()),
            key=lambda x: x[1], reverse=True,
        )[:5],
    }


# ── Cluster-based multi-context recording ────────────────────────────────────────────────────

def _activity_level(detection_risk: float, reward: float) -> str:
    """
    Coarse activity level for a single account signal.

    high: low risk + positive reward (account is healthy and productive)
    low:  high risk OR negative reward (account is suppressed or struggling)
    """
    if detection_risk < 0.45 and reward >= 0.0:
        return "high"
    return "low"


class AccountSignal:
    """
    Lightweight struct carrying per-account signal data for clustering.
    All fields are plain Python scalars.
    """
    __slots__ = ("risk_bucket", "lifecycle_stage", "activity_level", "reward")

    def __init__(
        self,
        risk_bucket:     str,
        lifecycle_stage: str,
        activity_level:  str,
        reward:          float,
    ) -> None:
        self.risk_bucket     = risk_bucket
        self.lifecycle_stage = lifecycle_stage
        self.activity_level  = activity_level
        self.reward          = reward

    @property
    def cluster_key(self) -> str:
        """Deterministic cluster identity string."""
        return f"rb={self.risk_bucket}|lc={self.lifecycle_stage}|al={self.activity_level}"


def cluster_meta_record(
    params:   dict[str, Any],
    signals:  list["AccountSignal"],
    platform_phase: str = "",
) -> None:
    """
    Group account signals into behavioral clusters and call record_meta
    once per cluster with the cluster's average reward.

    Clustering key = (risk_bucket, lifecycle_stage, activity_level).
    Replaces the single-fleet record_meta call to eliminate the
    "average illusion" across heterogeneous account populations.

    Exception-safe: never raises.
    """
    if not signals:
        return
    try:
        # Group by cluster_key
        clusters: dict[str, list[float]] = {}
        cluster_lc: dict[str, str] = {}
        cluster_rb: dict[str, str] = {}
        for sig in signals:
            ck = sig.cluster_key
            clusters.setdefault(ck, []).append(sig.reward)
            cluster_lc[ck] = sig.lifecycle_stage
            cluster_rb[ck] = sig.risk_bucket

        for ck, rewards in clusters.items():
            cluster_reward = sum(rewards) / len(rewards)
            record_meta(
                params,
                cluster_reward,
                lifecycle_stage = cluster_lc[ck],
                risk_bucket_str = cluster_rb[ck],
                platform_phase  = platform_phase,
            )
            LOGGER.debug(
                "cluster_meta_record cluster=%s n=%d avg_reward=%.3f",
                ck, len(rewards), cluster_reward,
            )
    except Exception as exc:
        LOGGER.debug("cluster_meta_record_error error=%s", exc)


# ── Distribution-shift partial reset ──────────────────────────────────────────────────────────

def _update_shift_history(avg_risk: float, avg_reward: float) -> tuple[bool, bool]:
    """
    Update EMA shift trackers and return (risk_shifted, reward_shifted).
    True when the new value deviates > _SHIFT_THRESHOLD from the EMA.
    """
    prev_risk   = _SHIFT_HISTORY["risk_ema"]
    prev_reward = _SHIFT_HISTORY["reward_ema"]

    new_risk   = prev_risk   * (1 - _SHIFT_ALPHA) + avg_risk   * _SHIFT_ALPHA
    new_reward = prev_reward * (1 - _SHIFT_ALPHA) + avg_reward * _SHIFT_ALPHA

    _SHIFT_HISTORY["risk_ema"]   = new_risk
    _SHIFT_HISTORY["reward_ema"] = new_reward

    risk_shifted   = abs(new_risk   - prev_risk)   > _SHIFT_THRESHOLD
    reward_shifted = abs(new_reward - prev_reward) > _SHIFT_THRESHOLD
    return risk_shifted, reward_shifted


def partial_reset(avg_risk: float, avg_reward: float, wipe_fraction: float = 0.30) -> bool:
    """
    If risk or reward distribution shifts significantly:
        - Wipe the lowest-quality `wipe_fraction` of META_Q entries
          (i.e. those with the worst Q-values).
        - Preserves high-performing contexts.

    Returns True if a partial reset was triggered, False otherwise.
    Exception-safe: never raises.
    """
    try:
        risk_shifted, reward_shifted = _update_shift_history(avg_risk, avg_reward)
        if not (risk_shifted or reward_shifted):
            return False

        if not _META_Q:
            return False

        # Sort keys by Q-value ascending (worst first)
        sorted_keys = sorted(_META_Q.keys(), key=lambda k: _META_Q[k])
        n_wipe = max(1, int(len(sorted_keys) * wipe_fraction))
        for k in sorted_keys[:n_wipe]:
            del _META_Q[k]
            _META_COUNT.pop(k, None)

        reason = "risk" if risk_shifted else "reward"
        LOGGER.info(
            "meta_partial_reset reason=%s wiped=%d remaining=%d",
            reason, n_wipe, len(_META_Q),
        )
        return True

    except Exception as exc:
        LOGGER.debug("meta_partial_reset_error error=%s", exc)
        return False
