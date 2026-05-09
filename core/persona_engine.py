"""
Persona Engine — Long-term account behavior identity & slow evolution.

Architecture contract:
  - PersonaState is per-account, in-process. No global mutable state.
  - Initial state is DETERMINISTIC: same account_id always produces the same seed.
  - Evolution (drift) IS stateful: it persists across sessions via snapshot/load.
  - Drift is BOUNDED: each session can shift interests by at most ±DRIFT_MAX (0.05).
  - Behavior modifiers cap their influence on external systems (see get_behavior_modifiers).
  - Exception-safe: all public methods trap errors and return safe defaults.

Usage:
    from core.persona_engine import get_persona_engine

    engine = get_persona_engine()
    persona = engine.get(account_id)
    mods    = engine.get_behavior_modifiers(account_id)
    engine.evolve(account_id, outcome={"blocked": True}, now=int(time.time()))
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from core.mutation_controller import stable_hash_int, _account_noise

LOGGER = logging.getLogger("core.persona_engine")

# ── Constants ─────────────────────────────────────────────────────────────────

# Available interest niches (index → name)
NICHES: list[str] = ["tech", "fitness", "finance", "entertainment", "food", "travel"]

# Interest drift per session: [min, max] shift magnitude
DRIFT_MIN: float = 0.01
DRIFT_MAX: float = 0.05

# P4: maximum cumulative drift per calendar day across all evolve() calls.
MAX_PERSONA_DELTA_PER_DAY: float = 0.05

# Behavior modifier clamps (keep influence tight)
_POSTING_FREQ_MIN: float = 0.7
_POSTING_FREQ_MAX: float = 1.3
_MUTATION_AGG_MIN: float = 0.8
_MUTATION_AGG_MAX: float = 1.2

# P4: per-account daily drift budget: account_id → (day_bucket, drift_used_today).
_DAILY_DRIFT: dict[str, tuple[int, float]] = {}


# ── Imperfection helpers (Part 1 + Part 5) ───────────────────────────────────

def _persona_volatility(account_id: str, now: int) -> bool:
    """~20% of days are 'volatile' — drift magnitude spikes 2–3x.

    Deterministic per (account_id, day_bucket). Creates natural interest jumps
    that mirror real-world events (viral trends, life changes).
    """
    day_bucket = now // 86400
    v = stable_hash_int(account_id, "volatility", str(day_bucket)) % 10
    return v < 2


def _persona_stagnation(account_id: str, now: int) -> bool:
    """~10% of days are 'stagnant' — drift almost stops (×0.1).

    Models dormant periods: user on holiday, low activity, distracted.
    Deterministic per (account_id, day_bucket).
    """
    day_bucket = now // 86400
    v = stable_hash_int(account_id, "stagnation", str(day_bucket)) % 10
    return v == 0


def _memory_decay(interests: dict, account_id: str, now: int) -> dict:
    """Part 5: Apply soft memory decay to interest weights before normalization.

    Simulates forgetting — interests drift slightly toward uniform over time.
    Decay rate: 0–4% per session (deterministic per account+day).
    Small enough to be invisible short-term, visible over weeks.
    """
    day_bucket  = now // 86400
    decay_pct   = (stable_hash_int(account_id, "memory_decay", str(day_bucket)) % 5) * 0.01  # 0–4%
    if decay_pct == 0.0:
        return interests
    uniform = 1.0 / len(interests) if interests else 0.0
    return {
        k: v * (1.0 - decay_pct) + uniform * decay_pct
        for k, v in interests.items()
    }


# ── PersonaState ──────────────────────────────────────────────────────────────

@dataclass
class PersonaState:
    """Mutable per-account long-term behavioral identity.

    interests:          dict niche→weight, always sums to 1.0
    activity_bias:      0.0–1.0, higher = more active
    risk_tolerance:     0.0–1.0, higher = less sensitive to risk signals
    last_updated:       Unix timestamp of last evolution step
    session_count:      total sessions evolved (not total sessions overall)
    """
    account_id:      str
    interests:       dict[str, float] = field(default_factory=dict)
    activity_bias:   float            = 0.5
    risk_tolerance:  float            = 0.5
    last_updated:    int              = 0
    session_count:   int              = 0

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "account_id":     self.account_id,
            "interests":      self.interests,
            "activity_bias":  self.activity_bias,
            "risk_tolerance": self.risk_tolerance,
            "last_updated":   self.last_updated,
            "session_count":  self.session_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PersonaState:
        p = cls(account_id=d["account_id"])
        p.interests      = dict(d.get("interests", {}))
        p.activity_bias  = float(d.get("activity_bias", 0.5))
        p.risk_tolerance = float(d.get("risk_tolerance", 0.5))
        p.last_updated   = int(d.get("last_updated", 0))
        p.session_count  = int(d.get("session_count", 0))
        return p

    def dominant_niche(self) -> str:
        """Return the niche with the highest weight."""
        if not self.interests:
            return "unknown"
        return max(self.interests, key=lambda k: self.interests[k])

    def risk_bucket(self) -> str:
        """Coarse risk tolerance label for logging/global memory."""
        if self.risk_tolerance < 0.33:
            return "conservative"
        if self.risk_tolerance < 0.67:
            return "moderate"
        return "aggressive"


# ── PersonaEngine ─────────────────────────────────────────────────────────────

class PersonaEngine:
    """Manages per-account PersonaState lifecycle: init, evolve, query."""

    def __init__(self) -> None:
        self._states: dict[str, PersonaState] = {}

    # ── Init ──────────────────────────────────────────────────────────────────

    def get(self, account_id: str) -> PersonaState:
        """Return existing state or create a fresh deterministic one."""
        if account_id not in self._states:
            self._states[account_id] = _init_persona(account_id)
        return self._states[account_id]

    # ── Evolution ─────────────────────────────────────────────────────────────

    def evolve(
        self,
        account_id: str,
        outcome: dict[str, Any],
        now: int | None = None,
    ) -> PersonaState:
        """Apply one evolution step based on session outcome.

        Drift rules (capped at DRIFT_MAX per interest per session):
          - upload_success  → reinforce dominant interest, nudge activity up
          - blocked         → shift away from dominant, nudge risk_tolerance down
          - captcha         → mild negative nudge on activity
          - shadow_ban      → reduce activity_bias, increase risk_tolerance awareness

        All deltas are BOUNDED so no single session can flip the persona.
        """
        if now is None:
            now = int(time.time())

        persona = self.get(account_id)
        old_dominant = persona.dominant_niche()

        blocked    = bool(outcome.get("blocked",           False))
        success    = bool(outcome.get("upload_success",    False))
        captcha    = bool(outcome.get("captcha",           False))
        shadow_ban = bool(outcome.get("shadow_ban_signal", False))

        # Drift magnitude: slightly varied per account (keeps personas diverging)
        drift_mag = DRIFT_MIN + (_account_noise(account_id, f"drift_{persona.session_count}") * (DRIFT_MAX - DRIFT_MIN))

        # Part 1: volatility spike (~20% of days) — 2.0–3.0x jump
        if _persona_volatility(account_id, now):
            spike = 2.0 + (stable_hash_int(account_id, "persona:vol_spike", str(now // 86400)) % 100) / 100.0
            drift_mag *= spike
            LOGGER.debug("persona_volatility account=%s day=%d spike=%.2fx drift=%.4f",
                         account_id, now // 86400, spike, drift_mag)

        # Part 1: stagnation phase (~10% of days) - nearly no change
        elif _persona_stagnation(account_id, now):
            drift_mag *= 0.1
            LOGGER.debug("persona_stagnation account=%s day=%d drift=%.4f",
                         account_id, now // 86400, drift_mag)

        # P3: hard cap 0.02/session (tighter than 0.03) + dead-zone
        drift_mag = min(drift_mag, 0.02)
        if drift_mag < 0.005:
            drift_mag = 0.0   # suppress micro-noise drift

        # P4: daily drift budget — cap total drift consumed across all sessions in one day.
        _day_bucket = now // 86400
        _stored = _DAILY_DRIFT.get(account_id)
        _used_today = _stored[1] if _stored and _stored[0] == _day_bucket else 0.0
        _remaining = max(0.0, MAX_PERSONA_DELTA_PER_DAY - _used_today)
        drift_mag = min(drift_mag, _remaining)
        _DAILY_DRIFT[account_id] = (_day_bucket, _used_today + drift_mag)

        # P3: EWMA alpha=0.85 (was 0.9) for slightly faster adaptation
        _ewma = 0.85
        # Apply interest drift with EWMA smoothing on scalar fields
        if success:
            _reinforce_interest(persona, drift_mag)
            new_ab = min(1.0, persona.activity_bias + drift_mag * 0.5)
            delta  = new_ab - persona.activity_bias
            if abs(delta) >= 0.005:
                persona.activity_bias = persona.activity_bias * _ewma + new_ab * (1 - _ewma)
        elif blocked:
            _shift_away(persona, drift_mag * 1.5)
            new_rt = max(0.0, persona.risk_tolerance - drift_mag)
            new_ab = max(0.0, persona.activity_bias  - drift_mag * 0.5)
            if abs(new_rt - persona.risk_tolerance) >= 0.005:
                persona.risk_tolerance = persona.risk_tolerance * _ewma + new_rt * (1 - _ewma)
            if abs(new_ab - persona.activity_bias) >= 0.005:
                persona.activity_bias  = persona.activity_bias  * _ewma + new_ab * (1 - _ewma)
        elif captcha:
            new_ab = max(0.0, persona.activity_bias - drift_mag * 0.3)
            if abs(new_ab - persona.activity_bias) >= 0.005:
                persona.activity_bias = persona.activity_bias * _ewma + new_ab * (1 - _ewma)
        elif shadow_ban:
            new_ab = max(0.0, persona.activity_bias  - drift_mag * 0.7)
            new_rt = max(0.0, persona.risk_tolerance - drift_mag * 0.5)
            if abs(new_ab - persona.activity_bias) >= 0.005:
                persona.activity_bias  = persona.activity_bias  * _ewma + new_ab * (1 - _ewma)
            if abs(new_rt - persona.risk_tolerance) >= 0.005:
                persona.risk_tolerance = persona.risk_tolerance * _ewma + new_rt * (1 - _ewma)

        # Part 5: soft memory decay on interests before normalization
        persona.interests = _memory_decay(persona.interests, account_id, now)
        _normalize_interests(persona)

        # P4: anchor pull — 5% gentle pull back toward initial seed values.
        # Prevents permanent extreme divergence over long horizons.
        _init_ab = 0.3 + (_account_noise(account_id, "init_activity") * 0.5)
        _init_rt = 0.3 + (_account_noise(account_id, "init_risk_tol") * 0.5)
        persona.activity_bias  = round(persona.activity_bias  * 0.95 + _init_ab * 0.05, 5)
        persona.risk_tolerance = round(persona.risk_tolerance * 0.95 + _init_rt * 0.05, 5)

        persona.last_updated = now
        persona.session_count += 1

        new_dominant = persona.dominant_niche()
        LOGGER.info(
            "persona_evolve account=%s session=%d dominant=%s→%s "
            "activity=%.3f risk_tol=%.3f drift=%.4f outcome=%s",
            account_id, persona.session_count, old_dominant, new_dominant,
            persona.activity_bias, persona.risk_tolerance, drift_mag,
            {k: v for k, v in outcome.items() if v},
        )
        return persona

    # ── Behavior modifiers ────────────────────────────────────────────────────

    def get_behavior_modifiers(self, account_id: str) -> dict[str, float]:
        """Return behavior modifier dict for consumption by stealth_brain.

        Keys (all floats):
          posting_frequency_factor  : clamped [0.7, 1.3]
          mutation_aggressiveness   : clamped [0.8, 1.2]
          niche_focus_score         : 0.0–1.0 (how concentrated interests are)
        """
        persona = self.get(account_id)
        return _get_behavior_modifiers(persona)

    # ── Persistence support ───────────────────────────────────────────────────

    def snapshot_all(self) -> dict[str, dict]:
        """Serialise all persona states for external persistence."""
        return {k: v.to_dict() for k, v in self._states.items()}

    def load_all(self, data: dict[str, dict]) -> None:
        """Restore persona states from serialised data."""
        for k, v in data.items():
            try:
                self._states[k] = PersonaState.from_dict(v)
            except Exception as exc:
                LOGGER.warning("persona_load_error account=%s error=%s", k, exc)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _init_persona(account_id: str) -> PersonaState:
    """Deterministic initial persona seeded from account_id."""
    persona = PersonaState(account_id=account_id)

    # Assign interest weights from SHA-based seeds
    raw: dict[str, float] = {}
    for i, niche in enumerate(NICHES):
        v = stable_hash_int(account_id, "interest", niche, str(i)) % 1000
        raw[niche] = float(v)
    total = sum(raw.values()) or 1.0
    persona.interests = {k: round(v / total, 5) for k, v in raw.items()}

    # Activity bias and risk tolerance: 0.3–0.8 range seeded per account
    persona.activity_bias  = 0.3 + (_account_noise(account_id, "init_activity")  * 0.5)
    persona.risk_tolerance = 0.3 + (_account_noise(account_id, "init_risk_tol")  * 0.5)
    persona.last_updated   = int(time.time())

    LOGGER.debug(
        "persona_init account=%s dominant=%s activity=%.3f risk_tol=%.3f",
        account_id, persona.dominant_niche(),
        persona.activity_bias, persona.risk_tolerance,
    )
    return persona


def _reinforce_interest(persona: PersonaState, delta: float) -> None:
    """Strengthen the dominant niche, weaken all others slightly."""
    dominant = persona.dominant_niche()
    for k in persona.interests:
        if k == dominant:
            persona.interests[k] = min(1.0, persona.interests[k] + delta)
        else:
            persona.interests[k] = max(0.0, persona.interests[k] - delta / (len(persona.interests) - 1))


def _shift_away(persona: PersonaState, delta: float) -> None:
    """Weaken dominant niche, distribute weight to others."""
    dominant = persona.dominant_niche()
    others   = [k for k in persona.interests if k != dominant]
    if not others:
        return
    persona.interests[dominant] = max(0.0, persona.interests[dominant] - delta)
    per_other = delta / len(others)
    for k in others:
        persona.interests[k] = min(1.0, persona.interests[k] + per_other)


def _normalize_interests(persona: PersonaState) -> None:
    """Ensure all interest weights sum to 1.0."""
    total = sum(persona.interests.values())
    if total > 0:
        persona.interests = {k: round(v / total, 5) for k, v in persona.interests.items()}


def _get_behavior_modifiers(persona: PersonaState) -> dict[str, float]:
    """Compute and clamp behavior modifiers from PersonaState."""
    # posting_frequency: high activity → posts more often
    freq = _POSTING_FREQ_MIN + (persona.activity_bias * (_POSTING_FREQ_MAX - _POSTING_FREQ_MIN))
    freq = max(_POSTING_FREQ_MIN, min(_POSTING_FREQ_MAX, freq))

    # mutation_aggressiveness: low risk_tolerance → mutates more aggressively
    agg = _MUTATION_AGG_MAX - (persona.risk_tolerance * (_MUTATION_AGG_MAX - _MUTATION_AGG_MIN))
    agg = max(_MUTATION_AGG_MIN, min(_MUTATION_AGG_MAX, agg))

    # niche_focus_score: how concentrated interests are (max weight of any niche)
    focus = max(persona.interests.values()) if persona.interests else 0.5

    mods = {
        "posting_frequency_factor": round(freq, 4),
        "mutation_aggressiveness":  round(agg,  4),
        "niche_focus_score":        round(focus, 4),
    }
    LOGGER.debug(
        "persona_modifiers account=%s dominant=%s freq=%.3f agg=%.3f focus=%.3f",
        persona.account_id, persona.dominant_niche(),
        mods["posting_frequency_factor"],
        mods["mutation_aggressiveness"],
        mods["niche_focus_score"],
    )
    return mods


# ── Singleton ─────────────────────────────────────────────────────────────────

_PERSONA_ENGINE: PersonaEngine | None = None


def get_persona_engine() -> PersonaEngine:
    """Return the process-level PersonaEngine singleton."""
    global _PERSONA_ENGINE
    if _PERSONA_ENGINE is None:
        _PERSONA_ENGINE = PersonaEngine()
    return _PERSONA_ENGINE


def reset_persona_engine() -> None:
    """Reset singleton and daily drift state — for testing only."""
    global _PERSONA_ENGINE
    _PERSONA_ENGINE = None
    _DAILY_DRIFT.clear()
