"""
Optimizer — EMA-smoothed parameter adjustment from metrics + validator output.

v2 — Per-cluster parameter state (CLUSTER_PARAM_STATE).

Design contracts:
  - Changes are bounded to ≤ ±10% per cycle (clamped hard).
  - All updates via EWMA (α=0.20) — no sudden jumps.
  - Optimizer only writes to its own state dicts; never touches core modules.
  - Callers read adjusted values via get_adjustment() and apply them.
  - Per-cluster params are stored in CLUSTER_PARAM_STATE[cluster_key].
    Unseen clusters are initialized from global default + deterministic jitter.
  - Global _state (single Optimizer instance) is the fallback and display state.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from core.mutation_controller import stable_hash_int

LOGGER = logging.getLogger("core.optimizer")

# Max allowed change per metric per cycle (multiplicative)
MAX_DELTA_PER_CYCLE: float = 0.10   # ±10%
_EMA_ALPHA: float = 0.20            # smoothing for adjustments

# Clamp bounds for all multipliers
_MULT_MIN: float = 0.60
_MULT_MAX: float = 1.40

# Initial (neutral) state for all tunable dimensions
_DEFAULT_STATE: dict[str, float] = {
    "strategy_harvester_weight_mult": 1.0,
    "strategy_amplifier_weight_mult": 1.0,
    "strategy_explorer_weight_mult":  1.0,
    "platform_delay_base_mult":       1.0,
    "platform_burstiness_mult":       1.0,
    "behavior_aggressiveness_mult":   1.0,
}

# Jitter magnitude for new-cluster initialization (±_JITTER_MAX)
_JITTER_MAX: float = 0.03

# ── Per-cluster parameter state ───────────────────────────────────────────────
#
# cluster_key (e.g. "rb=low|lc=GROWTH|al=high") → param_state dict.
# Each cluster evolves its own parameter regime independently.
# Populated lazily on first encounter; initialized with global default + jitter.
#
CLUSTER_PARAM_STATE: dict[str, dict[str, float]] = {}


def _cluster_init_state(cluster_key: str) -> dict[str, float]:
    """
    Initialize a cluster's param state from the global default +
    a small deterministic jitter per key.

    Jitter = ±_JITTER_MAX, seeded by (cluster_key, param_key).
    Ensures clusters start distinguishable but close to neutral.
    """
    state: dict[str, float] = {}
    for k, v in _DEFAULT_STATE.items():
        seed = stable_hash_int(cluster_key, "cluster_init", k) % 1000
        # Map [0, 999] → [-_JITTER_MAX, +_JITTER_MAX]
        jitter = ((seed / 999.0) * 2 - 1.0) * _JITTER_MAX
        state[k] = round(max(_MULT_MIN, min(_MULT_MAX, v + jitter)), 5)
    return state


def get_cluster_state(cluster_key: str) -> dict[str, float]:
    """
    Return (and lazily initialize) the param state for a given cluster.
    Thread-unsafe intentionally — single-process in-memory only.
    """
    if cluster_key not in CLUSTER_PARAM_STATE:
        CLUSTER_PARAM_STATE[cluster_key] = _cluster_init_state(cluster_key)
        LOGGER.debug("cluster_init cluster=%s state=%s", cluster_key, CLUSTER_PARAM_STATE[cluster_key])
    return CLUSTER_PARAM_STATE[cluster_key]


def reset_cluster_states() -> None:
    """Full cluster state reset — for testing only."""
    CLUSTER_PARAM_STATE.clear()


# ── Core delta computation (shared) ──────────────────────────────────────────

def _compute_changes(
    ban_rate:            float,
    success_rate:        float,
    anomaly_score:       float,
    health_score:        float,
    spike_flag:          bool,
    risk_used:           float,   # EMA-smoothed detector risk
) -> dict[str, float]:
    """
    Pure function: compute delta dict from metrics.
    Shared by both global update() and per-cluster update_for_cluster().
    """
    changes: dict[str, float] = {}

    # ── Strategy weight adjustments ───────────────────────────────────────────
    if ban_rate > 0.10:
        changes["strategy_harvester_weight_mult"] = -0.10
        changes["strategy_amplifier_weight_mult"] = -0.07
    elif ban_rate > 0.05:
        changes["strategy_harvester_weight_mult"] = -0.05
        changes["strategy_amplifier_weight_mult"] = -0.03

    if success_rate < 0.50:
        changes["strategy_explorer_weight_mult"]  =  0.08
        changes["strategy_harvester_weight_mult"]  = changes.get(
            "strategy_harvester_weight_mult", 0.0) - 0.03

    # ── Platform tuning ───────────────────────────────────────────────────────
    if anomaly_score > 0.60:
        changes["platform_delay_base_mult"]  =  0.08
        changes["platform_burstiness_mult"]  = -0.08
    elif anomaly_score > 0.30:
        changes["platform_delay_base_mult"]  =  0.04
        changes["platform_burstiness_mult"]  = -0.04
    elif anomaly_score < 0.10 and ban_rate < 0.02:
        changes["platform_delay_base_mult"]  = -0.02

    if spike_flag:
        changes["platform_delay_base_mult"]  = max(
            changes.get("platform_delay_base_mult", 0.0), 0.08)

    # ── Detector risk signal (EMA-smoothed) ───────────────────────────────────
    if risk_used > 0.80:
        changes["platform_delay_base_mult"]  = max(
            changes.get("platform_delay_base_mult", 0.0), 0.10)
        changes["platform_burstiness_mult"]  = min(
            changes.get("platform_burstiness_mult", 0.0), -0.10)
        changes["strategy_explorer_weight_mult"] = max(
            changes.get("strategy_explorer_weight_mult", 0.0), 0.08)
    elif risk_used > 0.65:
        changes["platform_delay_base_mult"]  = max(
            changes.get("platform_delay_base_mult", 0.0), 0.05)
        changes["platform_burstiness_mult"]  = min(
            changes.get("platform_burstiness_mult", 0.0), -0.05)

    # ── Behavior aggressiveness ───────────────────────────────────────────────
    if health_score < 0.40:
        changes["behavior_aggressiveness_mult"] = -0.10
    elif health_score < 0.60:
        changes["behavior_aggressiveness_mult"] = -0.05
    elif health_score > 0.85 and ban_rate < 0.02:
        changes["behavior_aggressiveness_mult"] =  0.03

    return changes


def _apply_changes(
    state:     dict[str, float],
    changes:   dict[str, float],
    ema_alpha: float = _EMA_ALPHA,
) -> dict[str, float]:
    """
    Apply EMA-smoothed changes to a param state dict (in-place + return).
    Hard clamped to [_MULT_MIN, _MULT_MAX].
    ema_alpha can be overridden for per-cluster stability dampening.
    """
    for key, delta in changes.items():
        delta  = max(-MAX_DELTA_PER_CYCLE, min(MAX_DELTA_PER_CYCLE, delta))
        target = state[key] + delta
        state[key] = (
            state[key] * (1 - ema_alpha) + target * ema_alpha
        )
        state[key] = round(max(_MULT_MIN, min(_MULT_MAX, state[key])), 5)
    return state


def _apply_meta_bias(state: dict[str, float]) -> dict[str, float]:
    """
    Apply contextual meta-learning bias to a state dict (in-place + return).
    scale ∈ [0.95, 1.05]; hard clamp [0.60, 1.40] preserved.
    Exception-safe.
    """
    try:
        from core.meta_learning import get_meta_bias
        bias  = get_meta_bias(dict(state))
        scale = 1.0 + bias
        if scale != 1.0:
            for k in list(state):
                state[k] = round(max(_MULT_MIN, min(_MULT_MAX, state[k] * scale)), 5)
    except Exception:
        pass
    return state


class Optimizer:
    """
    Continuous parameter optimizer for the closed-loop system.

    v2: Exposes update_for_cluster() for per-cluster parameter state.
        Global update() operates on the singleton _state (fleet-level fallback).
    """

    def __init__(self) -> None:
        self._state: dict[str, float] = dict(_DEFAULT_STATE)
        self._last_updated: float = 0.0
        # EMA-smoothed detector risk (anti-overreaction, α=0.25)
        self._RISK_EMA: float = 0.0

    # ── Global (fleet-level) update ───────────────────────────────────────────

    def update(
        self,
        ban_rate:            float,
        success_rate:        float,
        anomaly_score:       float,
        health_score:        float,
        spike_flag:          bool  = False,
        detector_risk_score: float = 0.0,
    ) -> dict[str, float]:
        """
        Global fleet-level update.
        Updates the singleton _state (used as fallback + for display).
        Returns current state after adjustment.
        """
        # EMA-smooth the detector risk (single-cycle spike absorbed)
        self._RISK_EMA = self._RISK_EMA * 0.75 + detector_risk_score * 0.25
        risk_used = self._RISK_EMA

        changes = _compute_changes(
            ban_rate, success_rate, anomaly_score,
            health_score, spike_flag, risk_used,
        )
        _apply_changes(self._state, changes)
        _apply_meta_bias(self._state)

        self._last_updated = time.time()
        LOGGER.debug(
            "optimizer_update ban_rate=%.3f success=%.3f anomaly=%.3f health=%.3f state=%s",
            ban_rate, success_rate, anomaly_score, health_score, self._state,
        )
        return dict(self._state)

    # ── Per-cluster update ────────────────────────────────────────────────────

    def update_for_cluster(
        self,
        cluster_key:         str,
        ban_rate:            float,
        success_rate:        float,
        anomaly_score:       float,
        health_score:        float,
        spike_flag:          bool  = False,
        detector_risk_score: float = 0.0,
    ) -> dict[str, float]:
        """
        Update and return the param state for a specific behavioral cluster.

        - Fetches (or lazily initializes with jitter) CLUSTER_PARAM_STATE[cluster_key].
        - Applies the same EMA-delta logic as global update().
        - Applies contextual meta-learning bias using cluster-specific state.
        - Writes result back to CLUSTER_PARAM_STATE[cluster_key].
        - Also refreshes the global _state via EMA blend toward the cluster result
          (keeps global state useful as a fleet-wide running average).

        Returns the updated cluster-specific state dict (copy).
        """
        # Per-cluster EMA risk is stored on the cluster state under a sentinel key
        risk_ema_key = "__risk_ema__"
        c_state  = get_cluster_state(cluster_key)
        risk_ema = c_state.pop(risk_ema_key, 0.0)
        risk_ema = risk_ema * 0.75 + detector_risk_score * 0.25
        risk_used = risk_ema

        # PART 4 -- Genome bias applied BEFORE metric-driven changes.
        # Blends inherited strategy traits into the cluster state so that
        # metric changes are computed on top of the genetic baseline.
        try:
            from core.swarm_dynamics import blend_genome_into_state
            blend_genome_into_state(cluster_key, c_state, risk=detector_risk_score)
        except Exception:
            pass   # genome bias is advisory; never blocks update

        changes = _compute_changes(
            ban_rate, success_rate, anomaly_score,
            health_score, spike_flag, risk_used,
        )

        # Dampen learning rate for unstable clusters (swarm stability guard)
        effective_alpha = _EMA_ALPHA
        try:
            from core.account_clustering import get_cluster_learning_rate
            lr_mult = get_cluster_learning_rate(cluster_key, base_lr=1.0)
            effective_alpha = _EMA_ALPHA * lr_mult
        except Exception:
            pass

        _apply_changes(c_state, changes, ema_alpha=effective_alpha)
        _apply_meta_bias(c_state)

        # PART 5 -- Mutation pressure: scale aggressiveness by fitness tier.
        # Weak clusters explore harder; strong clusters consolidate.
        try:
            from core.swarm_dynamics import get_mutation_pressure, get_context
            ctx = get_context(detector_risk_score)
            pressure = get_mutation_pressure(cluster_key, ctx)
            if pressure != 1.0 and "behavior_aggressiveness_mult" in c_state:
                raw = c_state["behavior_aggressiveness_mult"] * pressure
                c_state["behavior_aggressiveness_mult"] = round(
                    max(_MULT_MIN, min(_MULT_MAX, raw)), 5)
        except Exception:
            pass   # mutation pressure is advisory; never blocks update

        # Persist risk EMA back into cluster state
        c_state[risk_ema_key] = round(risk_ema, 5)
        CLUSTER_PARAM_STATE[cluster_key] = c_state

        # Blend global _state toward this cluster result (EMA, alpha=0.10)
        for k in _DEFAULT_STATE:
            self._state[k] = round(
                max(_MULT_MIN, min(_MULT_MAX,
                    self._state[k] * 0.90 + c_state[k] * 0.10
                )), 5,
            )

        self._last_updated = time.time()
        LOGGER.debug(
            "cluster_update cluster=%s ban=%.3f success=%.3f state=%s",
            cluster_key, ban_rate, success_rate,
            {k: v for k, v in c_state.items() if not k.startswith("__")},
        )
        # Return clean copy (no sentinel keys)
        return {k: v for k, v in c_state.items() if not k.startswith("__")}

    # ── Read / introspection ──────────────────────────────────────────────────

    def get_adjustment(self, key: str) -> float:
        """Return current global multiplier for a given key (default 1.0)."""
        return self._state.get(key, 1.0)

    def get_cluster_adjustment(self, cluster_key: str, key: str) -> float:
        """Return per-cluster multiplier (fallback to global if cluster unknown)."""
        c = CLUSTER_PARAM_STATE.get(cluster_key)
        if c:
            return c.get(key, self._state.get(key, 1.0))
        return self._state.get(key, 1.0)

    def snapshot(self) -> dict[str, Any]:
        return {
            "global_state":    dict(self._state),
            "n_clusters":      len(CLUSTER_PARAM_STATE),
            "cluster_states":  {
                ck: {k: v for k, v in cs.items() if not k.startswith("__")}
                for ck, cs in CLUSTER_PARAM_STATE.items()
            },
            "last_updated":    self._last_updated,
        }

    def reset(self) -> None:
        self._state = dict(_DEFAULT_STATE)
        self._last_updated = 0.0
        self._RISK_EMA = 0.0


# ── Singleton ──────────────────────────────────────────────────────────────────

_OPTIMIZER: Optimizer | None = None


def get_optimizer() -> Optimizer:
    global _OPTIMIZER
    if _OPTIMIZER is None:
        _OPTIMIZER = Optimizer()
    return _OPTIMIZER


def reset_optimizer() -> None:
    global _OPTIMIZER
    _OPTIMIZER = None
    CLUSTER_PARAM_STATE.clear()
    try:
        from core.account_clustering import reset_clustering
        reset_clustering()
    except Exception:
        pass
