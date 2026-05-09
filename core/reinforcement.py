"""
Reinforcement Learning Layer — Deterministic bandit-style policy.

Design contracts:
  - NO randomness: exploration uses stable_hash_int (deterministic).
  - Q-values updated via EWMA (α=0.15).
  - State is a coarse discrete key: (role, platform, risk_bucket, intent).
  - Action is one of: {role_shift, intensity_up, intensity_down, timing_extend, timing_compress}.
  - Reward: +1.0 success, -2.0 ban, -1.5 anomaly.
  - Policy: pick action with highest Q-value; break ties via stable_hash_int.
  - All changes bounded to ≤ ±20% total effect (hard clamp in apply()).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

LOGGER = logging.getLogger("core.reinforcement")

# EMA alpha for Q-value updates (lower = more inertia / slower learning)
_Q_ALPHA: float = 0.15

# Discount factor for future rewards (γ)
_GAMMA: float = 0.85

# Reward signal weights
REWARD_SUCCESS:  float =  1.0
REWARD_BAN:      float = -2.0
REWARD_ANOMALY:  float = -1.5

# Valid actions
ACTIONS: list[str] = [
    "role_shift_conservative",  # nudge toward WARMER/IDLE
    "role_shift_aggressive",    # nudge toward HARVESTER/AMPLIFIER
    "intensity_up",             # increase action intensity +10%
    "intensity_down",           # decrease action intensity -10%
    "timing_extend",            # longer delay between actions
    "timing_compress",          # shorter delay between actions
    "no_op",                    # do nothing
]

# Clamp bounds for all adjustments
_ADJ_MIN: float = 0.80
_ADJ_MAX: float = 1.20


@dataclass
class PolicyState:
    """Coarse discrete state key for Q-table lookup."""
    role:         str   # e.g. "WARMER"
    platform:     str   # e.g. "tiktok"
    risk_bucket:  str   # "conservative" | "moderate" | "aggressive"
    intent:       str   # e.g. "browse"

    def key(self) -> str:
        return f"{self.role}|{self.platform}|{self.risk_bucket}|{self.intent}"


@dataclass
class PolicyOutput:
    """Result of one policy evaluation."""
    state_key:  str
    action:     str
    q_value:    float
    adjustments: dict[str, float] = field(default_factory=dict)


def _compute_reward(
    success:         bool,
    ban:             bool,
    anomaly_score:   float,
    lifecycle_bonus: float = 0.0,
) -> float:
    r = 0.0
    if success:
        r += REWARD_SUCCESS
    if ban:
        r += REWARD_BAN
    r += REWARD_ANOMALY * anomaly_score
    # Lifecycle alignment bonus: max |±0.5|, keeps reward proportionate
    r += max(-0.5, min(0.5, lifecycle_bonus))
    return r


class ReinforcementPolicy:
    """
    Tabular Q-learning with deterministic exploration.

    Usage:
        policy = get_policy()
        output = policy.select_action(state)
        # ... execute ...
        policy.update(state.key(), output.action, reward=..., next_state_key=...)
    """

    def __init__(self) -> None:
        # Q-table: {state_key → {action → q_value}}
        self._q: dict[str, dict[str, float]] = {}
        self._episode_count: int = 0
        self._total_reward: float = 0.0
        self._last_updated: float = 0.0

    def _get_q(self, state_key: str, action: str) -> float:
        return self._q.get(state_key, {}).get(action, 0.0)

    def _set_q(self, state_key: str, action: str, value: float) -> None:
        if state_key not in self._q:
            self._q[state_key] = {}
        self._q[state_key][action] = round(value, 6)

    def select_action(self, state: PolicyState, now: int | None = None) -> PolicyOutput:
        """
        Deterministic bandit: pick action with highest Q-value.
        Ties broken by stable_hash_int(state_key, action, episode).
        """
        from core.mutation_controller import stable_hash_int

        if now is None:
            now = int(time.time())

        state_key = state.key()
        q_row = self._q.get(state_key, {})

        best_action = "no_op"
        best_q = float("-inf")
        for action in ACTIONS:
            q = q_row.get(action, 0.0)
            # Tie-break: add tiny deterministic perturbation
            tiebreak = stable_hash_int(state_key, action, str(self._episode_count)) % 1000 / 1e6
            effective_q = q + tiebreak
            if effective_q > best_q:
                best_q = effective_q
                best_action = action

        adjustments = _action_to_adjustments(best_action)

        LOGGER.debug(
            "rl_select state=%s action=%s q=%.4f",
            state_key, best_action, best_q,
        )
        return PolicyOutput(
            state_key   = state_key,
            action      = best_action,
            q_value     = self._get_q(state_key, best_action),
            adjustments = adjustments,
        )

    def update(
        self,
        state_key:      str,
        action:         str,
        success:        bool,
        ban:            bool,
        anomaly_score:  float,
        next_state_key: str | None = None,
        # Lifecycle reward shaping (optional)
        role:           str = "",
        niche:          str = "",
        lifecycle_stage: str = "",
        created_ts:     int = 0,
        account_id:     str = "",
        # Detector risk penalty (Part 8)
        detector_risk_score: float = 0.0,
        # Part 1: cycle timestamp for delayed reward buffer
        now:            int = 0,
    ) -> float:
        """
        EMA Q-value update (Bellman-style) with lifecycle reward shaping,
        detection risk penalty, and delayed reward buffer (credit assignment fix).
        Returns the reward used for the Q-update (0.0 if still buffering).
        """
        # Compute lifecycle bonus (exception-safe)
        lifecycle_bonus = 0.0
        if role and niche and lifecycle_stage and account_id:
            try:
                from core.lifecycle_engine import (
                    compute_lifecycle_reward_bonus, LifecycleStage,
                )
                stage_enum = LifecycleStage(lifecycle_stage)
                lifecycle_bonus = compute_lifecycle_reward_bonus(
                    account_id  = account_id,
                    role        = role,
                    niche       = niche,
                    stage       = stage_enum,
                    success     = success,
                    created_ts  = created_ts,
                )
            except Exception as exc:
                LOGGER.debug("rl_lifecycle_bonus_error state=%s error=%s", state_key, exc)

        reward = _compute_reward(success, ban, anomaly_score, lifecycle_bonus)

        # Part 8: detection risk penalty — reward -= risk * 0.5
        # Penalises bot-like behavior detected by the simulator
        if detector_risk_score > 0.0:
            reward -= max(0.0, min(1.0, detector_risk_score)) * 0.5

        # Adaptive Lag Window (credit assignment upgrade)
        # ─────────────────────────────────────────────────────────────────────
        # W ∈ [2, 5] based on reward variance:
        #   low variance (<0.10)  → W=2  (stable signal, release quickly)
        #   med variance (<0.30)  → W=3
        #   high variance (<0.50) → W=4
        #   very high    (≥0.50)  → W=5  (smooth out noisy delayed rewards)
        #
        # Exponential decay weights (λ=0.70): newest entry has weight 1.0,
        #   oldest has weight λ^(W-1).  agg = dot(w, rewards) / sum(w)
        #
        # Timeout flush: if oldest entry > T=5 cycle-steps old, flush now
        #   with 0.5x partial credit (don't hold signal indefinitely).
        if account_id:
            _TIMEOUT_S: float = 5 * 3600     # 5 cycles × assumed 3600s/cycle
            _LAM:       float = 0.70         # exponential decay lambda

            buf_key = f"{account_id}|{state_key}|{action}"
            buf = _REWARD_BUFFER.setdefault(buf_key, [])
            buf.append((float(now), reward))

            # Adaptive window size from variance of buffered rewards
            vals = [r for _, r in buf]
            if len(vals) >= 2:
                mean_v = sum(vals) / len(vals)
                var    = sum((v - mean_v) ** 2 for v in vals) / len(vals)
            else:
                var = 0.0

            W = 2 if var < 0.10 else 3 if var < 0.30 else 4 if var < 0.50 else 5

            # Timeout check: flush with partial credit if oldest is too stale
            oldest_ts   = buf[0][0]
            age_s       = float(now) - oldest_ts
            if age_s >= _TIMEOUT_S and len(buf) < W:
                all_rewards = [r for _, r in buf]
                agg = (sum(all_rewards) / len(all_rewards)) * 0.5
                _REWARD_BUFFER[buf_key] = []
                reward = agg
                LOGGER.debug(
                    "rl_buffer_timeout account=%s age_s=%.0f agg=%.3f",
                    account_id, age_s, agg,
                )
            elif len(buf) < W:
                # Still accumulating — wait for more signal
                LOGGER.debug(
                    "rl_buffer account=%s buf=%d W=%d var=%.3f waiting",
                    account_id, len(buf), W, var,
                )
                return 0.0   # deferred; no Q-update this cycle
            else:
                # Window full — weighted aggregation
                window  = [r for _, r in buf[:W]]
                weights = [_LAM ** (W - 1 - i) for i in range(W)]
                w_sum   = sum(weights)
                agg     = sum(w * r for w, r in zip(weights, window)) / w_sum
                _REWARD_BUFFER[buf_key] = buf[W:]
                reward = agg
                LOGGER.debug(
                    "rl_buffer_release account=%s W=%d var=%.3f agg=%.3f",
                    account_id, W, var, agg,
                )
        # Max Q of next state (greedy target)
        next_max_q = 0.0
        if next_state_key and next_state_key in self._q:
            next_max_q = max(self._q[next_state_key].values(), default=0.0)

        # Bellman target
        target = reward + _GAMMA * next_max_q

        # EMA update
        old_q = self._get_q(state_key, action)
        new_q = old_q * (1 - _Q_ALPHA) + target * _Q_ALPHA
        self._set_q(state_key, action, new_q)

        self._episode_count += 1
        self._total_reward  += reward
        self._last_updated   = time.time()

        LOGGER.debug(
            "rl_update state=%s action=%s reward=%.2f lc_bonus=%.3f old_q=%.4f new_q=%.4f",
            state_key, action, reward, lifecycle_bonus, old_q, new_q,
        )
        return reward

    def avg_reward(self) -> float:
        if self._episode_count == 0:
            return 0.0
        return self._total_reward / self._episode_count

    def snapshot(self) -> dict[str, Any]:
        return {
            "episode_count":    self._episode_count,
            "total_reward":     round(self._total_reward, 4),
            "avg_reward":       round(self.avg_reward(), 4),
            "q_table_size":     len(self._q),
            "last_updated":     self._last_updated,
        }

    def reset(self) -> None:
        self._q.clear()
        self._episode_count = 0
        self._total_reward  = 0.0
        self._last_updated  = 0.0


def _action_to_adjustments(action: str) -> dict[str, float]:
    """Map a policy action to numeric adjustment multipliers."""
    return {
        "role_shift_conservative": {"aggressiveness": 0.90},
        "role_shift_aggressive":   {"aggressiveness": 1.10},
        "intensity_up":            {"intensity": 1.10},
        "intensity_down":          {"intensity": 0.90},
        "timing_extend":           {"delay_mult": 1.10},
        "timing_compress":         {"delay_mult": 0.92},
        "no_op":                   {},
    }.get(action, {})


def build_state(
    role:         str,
    platform:     str,
    risk_tolerance: float,
    intent:       str,
) -> PolicyState:
    """Build a coarse state for Q-table lookup."""
    if risk_tolerance < 0.33:
        bucket = "conservative"
    elif risk_tolerance < 0.67:
        bucket = "moderate"
    else:
        bucket = "aggressive"
    return PolicyState(role=role, platform=platform, risk_bucket=bucket, intent=intent)


# ── Singleton ──────────────────────────────────────────────────────────────────

_POLICY: ReinforcementPolicy | None = None

# Part 1: Delayed reward buffer — credit assignment fix.
# Keyed by "account_id|state_key|action" to preserve context.
# buf_key → list of (timestamp, raw_reward)
_REWARD_BUFFER: dict[str, list[tuple[float, float]]] = {}

# Number of cycles to wait before releasing buffered reward
_REWARD_DELAY: int = 2


def get_policy() -> ReinforcementPolicy:
    global _POLICY
    if _POLICY is None:
        _POLICY = ReinforcementPolicy()
    return _POLICY


def reset_policy() -> None:
    global _POLICY
    _POLICY = None
    _REWARD_BUFFER.clear()
