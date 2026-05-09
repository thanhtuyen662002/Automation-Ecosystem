"""
Mutation Controller — Gatekeeper for all IdentityProfile fingerprint mutations.

Design contracts:
  - StealthBrain CANNOT modify IdentityProfile directly.
  - All mutations go through MutationController.apply(profile, strategy).
  - Mutations are deterministic: seed = stable_hash_int(account_id, str(mutation_state)).
  - FROZEN fields: device_type, os, screen_resolution, user_agent.
  - MUTABLE (MEDIUM via sync_geo): timezone, locale.
  - MUTABLE (noise surfaces): canvas_noise_seed, webgl_noise_seed.

Risk-gated mutation rules:
  LOW    → no mutation applied, no fields changed.
  MEDIUM → at most canvas_noise_seed + geo (max 2 attributes).
  HIGH   → full regen: both noise seeds from new deterministic seed.

Drift constraints:
  - MAX_PARTIALS_BEFORE_FULL: after N partial mutations, force a full regen.
  - _cooldown(): per-account variable cooldown (180-360s), replaces fixed interval.
  - _should_mutate_now(): risk-aware temporal smoothing with 4 context factors.
  - _is_burst_window(): ~30% of 15-min windows enter burst mode (skip delay checks).

Snapshot / rollback:
  MutationResult.pre_mutation_snapshot captures the mutable surface before
  any change. Pass to restore_snapshot() to undo the mutation.
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

from core.platform_profiles import PLATFORM_PROFILES, DEFAULT_PROFILE as _DEFAULT_PLATFORM_PROFILE

LOGGER = logging.getLogger("core.mutation_controller")

# ── Drift constants ───────────────────────────────────────────────────────────

# Force a full regen after this many consecutive partial mutations.
MAX_PARTIALS_BEFORE_FULL: int = 5

# Max Hamming distance (base vs active) before partial escalates to full.
_MAX_DISTANCE: float = 0.50

# Action types allowed at MEDIUM risk (canvas/audio/geo surface only).
_MEDIUM_SAFE_ACTIONS: frozenset[str] = frozenset({"rotate_canvas", "rotate_audio", "sync_geo"})

# P1/P5/P10/P11: Global noise budget — no multiplier chain may push output beyond ±40%.
# Raised to 0.40 to accommodate social context waves and lag layers.
MAX_NOISE_IMPACT: float = 0.40


# ── Shared types ──────────────────────────────────────────────────────────────

class RiskLevel(str, Enum):
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"


@dataclass
class Action:
    """A single mutation instruction from StealthBrain to MutationController.

    type:     "rotate_canvas" | "rotate_audio" | "rotate_gpu" | "sync_geo" | "cooldown"
    targets:  profile field names that will be modified
    metadata: optional extra data, e.g. {"timezone": "Asia/Tokyo"}
    """
    type: str
    targets: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Strategy:
    """Full instruction set returned by StealthBrain → consumed by MutationController."""
    risk_level:      RiskLevel
    actions:         list[Action]
    delay_multiplier: float = 1.0
    warmup_delay:    float  = 5.0
    interaction_mode: str   = "NORMAL"    # "SAFE" | "NORMAL"
    reason:          str    = ""          # single-line human-readable reason


@dataclass
class MutationResult:
    """Result of MutationController.apply()."""
    mutation_type:        str          # "none" | "partial" | "full"
    changed_fields:       list[str]
    new_fingerprint_hash: str
    mutation_state:       int
    pre_mutation_snapshot: dict = field(default_factory=dict)
    reason:               str   = ""


# ── Stable deterministic hash helpers ────────────────────────────────────────

def stable_hash_int(*parts: str, mod: int = 10 ** 9) -> int:
    """Stable, process-invariant integer hash from one or more string parts.

    Uses SHA-256 so output is identical across Python processes, machines,
    and runs — unlike built-in hash() which uses per-process seed randomisation.

    mod: upper bound (exclusive). Default 10^9 keeps values in safe int range.
    """
    joined = "|".join(parts)
    h = hashlib.sha256(joined.encode()).hexdigest()
    return int(h[:16], 16) % mod


def _account_noise(account_id: str, key: str) -> float:
    """PART 3: Deterministic per-account noise in [0.0, 1.0).

    Derived solely from account_id + key — no global state, no random().
    Used to decorrelate timing, burst windows, and mutation targets across
    accounts without breaking per-account determinism.
    """
    return (stable_hash_int(account_id, key) % 1000) / 1000.0


def _bounded_noise(account_id: str, key: str, base: float = 1.0, spread: float = 0.20) -> float:
    """Normalised noise multiplier in [base-spread, base+spread].

    Max ±20% by default. NEVER stack more than 1 of these per code path.
    Deterministic: derived from (account_id, key) only.
    """
    n = (stable_hash_int(account_id, key) % 1000) / 1000.0
    return base + (n - 0.5) * 2.0 * spread


def _normalized_noise(account_id: str, key: str, spread: float = 0.15) -> float:
    """P1/P8: Unified noise gate. Symmetric [−spread, +spread] around 1.0.

    Hard-clamped to ±MAX_NOISE_IMPACT so no noise source can exceed the global budget.
    All subsystems MUST use namespaced keys:
      'timing:*'  'persona:*'  'device:*'  'global:*'
    NEVER reuse same key across layers.
    """
    n = (stable_hash_int(account_id, key) % 1000) / 1000.0
    raw = 1.0 + (n - 0.5) * 2.0 * spread
    # P1: global budget clamp — even if spread > MAX_NOISE_IMPACT, output stays bounded.
    return max(1.0 - MAX_NOISE_IMPACT, min(1.0 + MAX_NOISE_IMPACT, raw))


# ── Imperfection helpers (Parts 3 + 4) ──────────────────────────────────

def _micro_jitter(account_id: str, now: int) -> int:
    """Part 3: Additive timing noise in [-10, +10] seconds.

    Breaks perfectly smooth delay curves. Deterministic per (account_id, now)
    so the same session always gets the same jitter — still auditable.
    """
    return (stable_hash_int(account_id, "jitter", str(now)) % 21) - 10


def _skip_probability(account_id: str, state: int) -> bool:
    """Part 7: Humanized skip — probability rises with fatigue.

    base = 5%, + 1% per fatigue cycle (state % 10).
    A tired account skips more often (up to ~14%).
    Deterministic per (account_id, state).
    """
    fatigue_cycle = stable_hash_int(account_id, "fatigue", str(state)) % 10
    # Threshold in per-mille: 50 base + fatigue * 10
    threshold = 50 + fatigue_cycle * 10   # 50–140 out of 1000 = 5–14%
    roll = stable_hash_int(account_id, "skip_roll", str(state)) % 1000
    return roll < threshold


def _skip_action(account_id: str, now: int, elapsed: float = 1.0) -> bool:
    """P7: Probability-based skip: base 4% + fatigue*0.8% per cycle, max ~12%.

    Noise-perturbed so the threshold differs slightly per account-window.
    Uses namespaced 'timing:skip_*' keys (P8).
    NEVER fires on first mutation (elapsed == 0 guard — P9 invariant).
    """
    if elapsed == 0:          # P9: hard invariant — no skip on first call
        return False
    state = now // 3600   # hourly state proxy for fatigue
    fatigue_cycle = stable_hash_int(account_id, "timing:fatigue_skip", str(state)) % 10
    prob = 0.04 + fatigue_cycle * 0.008           # 0.04–0.112
    prob *= _normalized_noise(account_id, "timing:skip_noise", spread=0.20)  # ±20%
    prob  = max(0.01, min(0.15, prob))            # hard bounds 1%–15%
    # Convert to per-mille integer threshold for deterministic comparison
    threshold = int(prob * 1000)
    roll = stable_hash_int(account_id, "timing:skip_roll", str(now // 60)) % 1000
    decision = roll < threshold
    
    # Part 10.4: Memory Conflict (flip skip logic)
    if _memory_conflict(account_id, state):
        decision = not decision
        
    return decision


def _behavior_noise(account_id: str, now: int) -> bool:
    """Part 4: ~10% of evaluations receive a behavior modifier perturbation.

    Applied as a second-order noise layer on top of persona modifiers.
    Deterministic per (account_id, 15-min window).
    """
    window = now // 900
    return stable_hash_int(account_id, "behavior_noise", str(window)) % 10 == 0


def apply_behavior_noise(
    account_id: str,
    now: int,
    mods: dict,
) -> dict:
    """Part 4: Perturb behavior modifiers if _behavior_noise fires.

    Mutation aggressiveness: ×0.7–1.3 (deterministic)
    Posting frequency:       ×0.8–1.2
    All values remain within module-level clamp bounds.
    P8: Keys namespaced under 'persona:*' to prevent cross-layer seed reuse.
    """
    if not _behavior_noise(account_id, now):
        return mods
    mods = dict(mods)   # never mutate the original
    # P8: namespaced 'persona:agg_noise' / 'persona:freq_noise'
    agg_noise   = 0.7  + (stable_hash_int(account_id, "persona:agg_noise",  str(now // 900)) % 1000) / 1666.0  # 0.70–1.30
    freq_noise  = 0.8  + (stable_hash_int(account_id, "persona:freq_noise", str(now // 900)) % 1000) / 2500.0  # 0.80–1.20
    # Part 5: apply weak global trend bias to aggressiveness + posting frequency only.
    # Multiplied in before the clamp so the final value stays within allowed bounds.
    trend = _trend_bias(now)
    mods["mutation_aggressiveness"]   = round(max(0.8, min(1.2, mods["mutation_aggressiveness"]  * agg_noise  * trend)), 4)
    mods["posting_frequency_factor"]  = round(max(0.7, min(1.3, mods["posting_frequency_factor"] * freq_noise * trend)), 4)
    LOGGER.debug("behavior_noise_applied account=%s agg=%.3f freq=%.3f trend=%.3f",
                 account_id, mods["mutation_aggressiveness"], mods["posting_frequency_factor"], trend)
    return mods


def _seeded_int(seed: int, slot: int, lo: int, hi: int) -> int:
    """Derive a deterministic integer in [lo, hi] from seed + slot.

    Uses stable_hash_int — safe across processes.
    """
    unit = stable_hash_int(str(seed), str(slot)) / (10 ** 9 - 1)
    return lo + int(unit * (hi - lo + 1))


# ── Behavioral timing helpers ─────────────────────────────────────────────────

def _cooldown(account_id: str, state: int) -> int:
    """Per-account variable cooldown: 180–360 seconds (3–6 min).

    Replaces the old fixed MIN_MUTATION_INTERVAL_S = 300.
    Deterministic: same (account_id, state) always returns the same value.
    Different accounts have different cooldown durations — harder to fingerprint.
    """
    return 180 + (stable_hash_int(account_id, "cooldown", str(state)) % 181)


def _session_factor(account_id: str, now: int) -> float:
    """Time-of-day multiplier, shifted +-1h per account to break cohort patterns.

    PART 1: each account has a deterministic -1/0/+1 hour shift so accounts
    do NOT all spike at the same morning/evening boundary.

    Buckets (UTC hour after shift):
      06-12 morning   0.9  (slightly faster)
      12-18 afternoon 1.0  (neutral)
      18-24 evening   1.2  (more active)
      00-06 night     0.6  (mostly inactive)
    """
    shift = (stable_hash_int(account_id, "session_shift") % 3) - 1  # -1, 0, +1
    hour  = ((now // 3600) + shift) % 24
    if 6 <= hour < 12:
        return 0.9
    elif 12 <= hour < 18:
        return 1.0
    elif 18 <= hour < 24:
        return 1.2
    else:
        return 0.6


def _fatigue_factor(account_id: str, state: int) -> float:
    """Non-linear fatigue: delay grows within a session but not in a fixed cycle.

    PART 2: cycle is now account+state-seeded so the 0->9 ramp is not
    predictable from the outside. Same account always gets the same ramp
    shape, but different accounts have different ramp orderings.
    Factor range: 1.0 - 1.45 (same as before).
    """
    cycle = stable_hash_int(account_id, "fatigue", str(state)) % 10
    return 1.0 + (cycle * 0.05)


def _day_type_factor(now: int) -> float:
    """Weekday vs weekend multiplier.

    PART 3: Unix epoch day 0 = Thu 1970-01-01, so +4 shifts to Mon=0 ... Sun=6.
    Weekends (day >= 5) are slower / less active.
    """
    day = (now // 86400 + 4) % 7
    return 0.85 if day >= 5 else 1.0


def _account_age_factor(account_id: str, created_ts: int, now: int) -> float:  # noqa: ARG001
    """Maturity curve: new accounts are cautious (longer delay), old ones bolder.

    Smooth continuous decay from 1.3 (day 1) to 0.9 (day 50+).
    Replaces the old 4-step function to eliminate detectable hard jumps
    at the 3/14/60-day boundaries.
    """
    age_days = max(1, (now - created_ts) // 86400)
    decay = min(0.4, age_days * 0.008)  # 0.008/day, saturates at 0.4 (day 50)
    return 1.3 - decay


# P2: per-account delay inertia storage (module-level, process-scoped).
# Smooths timing: new_delay = 0.7 * prev + 0.3 * computed
_PREV_DELAYS: dict[str, int] = {}

# P6: consecutive skip tracker — ensures no 3 skips in a row (process-scoped, per-account).
_CONSECUTIVE_SKIPS: dict[str, int] = {}


# ── Habit-Bias Memory (Part 1) ────────────────────────────────────────────────

# (account_id, hour) → cached bias float (base only; drift applied at call-time)
_GLOBAL_HABIT: dict[tuple[str, int], float] = {}

# account_id → accumulated daily drift (EWMA, updated once per day per call)
_HABIT_DRIFT: dict[str, float] = {}


def _habit_bias(account_id: str, now: int) -> float:
    """Per-account, per-hour habit bias with slow daily drift.

    Base in [0.85, 1.15] is seeded from (account_id, hour).
    A small EWMA drift (±1%/day) evolves the bias over time without
    making it unpredictable. Final value is clamped to [0.85, 1.15].
    No global state is shared across accounts.
    """
    hour = (now // 3600) % 24
    key  = (account_id, hour)

    # Base: deterministic per (account_id, hour)
    if key not in _GLOBAL_HABIT:
        seed = stable_hash_int(account_id, "habit", str(hour)) % 1000 / 1000.0
        _GLOBAL_HABIT[key] = 0.85 + seed * 0.30   # [0.85, 1.15]
    base = _GLOBAL_HABIT[key]

    # Drift: small EWMA updated once per day bucket (max ±2% total movement/day)
    day        = now // 86400
    drift_seed = stable_hash_int(account_id, "habit_drift", str(day)) % 1000 / 1000.0
    prev_drift = _HABIT_DRIFT.get(account_id, 0.0)
    drift      = prev_drift * 0.9 + (drift_seed - 0.5) * 0.02   # ±1% contribution
    _HABIT_DRIFT[account_id] = drift

    return max(0.85, min(1.15, base + drift))


# ── Daily Routine Signature (Part 2) ─────────────────────────────────────────

_CACHE_ROUTINE: dict[str, dict] = {}


def _build_daily_routine(account_id: str) -> dict:
    """Build a persistent daily routine for an account.

    Returns:
        wake_hour  : 6–8
        sleep_hour : 22–23
        peak_hours : list of two peak-activity UTC hours
    """
    base = stable_hash_int(account_id, "routine")
    return {
        "wake_hour":  6  + (base % 3),          # 6–8
        "sleep_hour": 22 + (base % 2),           # 22–23
        "peak_hours": [
            12 + (base % 2),                      # 12–13
            19 + (base % 3),                      # 19–21
        ],
    }


def _get_routine(account_id: str) -> dict:
    """Return (and cache) the daily routine for an account."""
    if account_id not in _CACHE_ROUTINE:
        _CACHE_ROUTINE[account_id] = _build_daily_routine(account_id)
    return _CACHE_ROUTINE[account_id]


# ── Routine Break — anti-perfect rhythm (Part 1) ─────────────────────────────

def _routine_break(account_id: str, now: int) -> bool:
    """Return True on ~8% of calendar days to model routine disruption.

    Examples: travel, illness, unusual schedule, late night.
    Deterministic per (account_id, day). No cross-account dependency.
    """
    day  = now // 86400
    seed = stable_hash_int(account_id, "routine_break", str(day)) % 100
    return seed < 8   # 8% of days


# ── Human Irrationality (Part 10) ─────────────────────────────────────────────

def _intent_drift(account_id: str, now: int) -> bool:
    """Return True on ~12% of hours to model mood/rhythm mismatch (intent drift)."""
    seed = stable_hash_int(account_id, "intent", str(now // 3600)) % 100
    return seed < 12


def _contradiction(account_id: str, now: int) -> bool:
    """Return True on ~0.5% of days to model behavior contradiction."""
    seed = stable_hash_int(account_id, "contradict", str(now // 86400)) % 200
    return seed == 0


_OBSESSION: dict[str, tuple[int, int, int]] = {}

def _obsession(account_id: str, now: int) -> bool:
    """Return True if account is currently in a short-term obsession spike."""
    day = now // 86400
    state = _OBSESSION.get(account_id)
    if not state or state[2] != day:
        seed = stable_hash_int(account_id, "obsession", str(day)) % 100
        if seed < 5:  # ~5%
            _OBSESSION[account_id] = (now, 3600 * (1 + seed % 3), day)
        else:
            _OBSESSION[account_id] = (0, 0, day)

    start, dur, _ = _OBSESSION[account_id]
    return start > 0 and start <= now <= start + dur


def _memory_conflict(account_id: str, state: int) -> bool:
    """Return True on ~5% of mutations to flip the skip decision."""
    seed = stable_hash_int(account_id, "memory_conflict", str(state)) % 100
    return seed < 5


def _rhythm_factor(account_id: str, now: int) -> float:
    """Smooth routine-aware activity multiplier with occasional break days.

    Normal days:
      Outside wake/sleep window : 0.5 (mostly inactive)
      Inside window             : 0.9 + peak_proximity_boost  (0.9–1.2)
      peak_proximity_boost decays linearly from 0.3 (at peak) to 0.0 (6h away).

    Break days (~8%):
      Returns a noise-distorted value in roughly [0.45, 0.75], modelling
      a day where normal rhythm is disrupted.
    """
    # Part 1: routine break overrides normal rhythm
    if _routine_break(account_id, now):
        # _normalized_noise returns [0.85, 1.15]; shift to produce [0.45, 0.75]
        return 0.7 + (_normalized_noise(account_id, "timing:break", spread=0.15) - 1.0) * 0.5

    hour = (now // 3600) % 24
    r    = _get_routine(account_id)

    awake = r["wake_hour"] <= hour <= r["sleep_hour"]
    if not awake:
        return 0.5

    dist_peak  = min(abs(hour - h) for h in r["peak_hours"])
    peak_boost = max(0.0, 1.0 - dist_peak / 6.0) * 0.3   # 0.0–0.3
    
    # Part 10.1: intent drift
    if _intent_drift(account_id, now) and peak_boost > 0:
        peak_boost = -peak_boost  # reduce activity instead of increase

    return 0.9 + peak_boost   # [0.6, 1.2]


# ── Session Clustering (Part 4 → personality + anti-repeat) ──────────────────

# account_id → {"start": int, "count": int, "last_end": int}
_SESSION_STATE: dict[str, dict] = {}

# account_id → per-account session personality
_SESSION_PROFILE: dict[str, dict] = {}


def _get_session_profile(account_id: str) -> dict:
    """Lazily build and cache a per-account session personality.

    session_chance : probability of starting a session per minute-bucket (15–35%)
    session_length : max actions per session (5–12)
    cooldown_bias  : scales the 10-min inter-session cooldown (0.8–1.2)
    """
    if account_id not in _SESSION_PROFILE:
        seed = stable_hash_int(account_id, "session_profile") % 1000 / 1000.0
        _SESSION_PROFILE[account_id] = {
            "session_chance": 0.15 + seed * 0.20,   # 0.15–0.35
            "session_length": 5 + int(seed * 7),    # 5–12
            "cooldown_bias":  0.8 + seed * 0.40,    # 0.8–1.2
        }
    return _SESSION_PROFILE[account_id]


def _can_start_session(account_id: str, now: int) -> bool:
    """Enforce a per-account inter-session cooldown to prevent back-to-back sessions.

    Base cooldown: 600 s * cooldown_bias (480–720 s).
    Returns True if no session has run yet, or if the cooldown has elapsed.
    """
    state = _SESSION_STATE.get(account_id)
    if not state or "last_end" not in state:
        return True
    profile  = _get_session_profile(account_id)
    cooldown = 600.0 * profile["cooldown_bias"]   # 480–720 s
    return (now - state["last_end"]) > cooldown


def _in_session(account_id: str, now: int) -> bool:
    """Return True if the account is currently inside a 30-minute activity session."""
    state = _SESSION_STATE.get(account_id)
    if not state or "start" not in state:
        return False
    return now - state["start"] < 1800   # 30-minute session window


# ── Outlier Sessions — rare human spike (Part 2) ─────────────────────────────

def _outlier_session(account_id: str, now: int) -> bool:
    """Return True on ~0.5% of days to model an unusually long/fast session.

    Deterministic per (account_id, day). No cross-account dependency.
    """
    day  = now // 86400
    seed = stable_hash_int(account_id, "outlier", str(day)) % 200
    return seed == 0   # 0.5% of days


# ── Mood Drift — short-term behavior shift (Part 3) ───────────────────────────

def _get_mood(account_id: str, now: int) -> str:
    """Return the account's mood for the current 6-hour window.

    Mood is deterministic per (account_id, 6-hour bucket).
    Distribution: 20% "low", 20% "high", 60% "normal".
    Applied as a delay multiplier: low→×1.2, high→×0.85, normal→×1.0.
    """
    bucket = now // 21600   # 6-hour window
    seed   = stable_hash_int(account_id, "mood", str(bucket)) % 1000 / 1000.0
    mood = "normal"
    if seed < 0.20:
        mood = "low"
    elif seed > 0.80:
        mood = "high"
        
    # Part 10.1: intent drift
    if mood == "low" and _intent_drift(account_id, now):
        mood = "normal"
        
    return mood


_MOOD_MULT: dict[str, float] = {"low": 1.20, "high": 0.85, "normal": 1.0}


# ── Micro-Variation — per-action inconsistency (Part 4) ───────────────────────

def _micro_variation(account_id: str, state: int) -> float:
    """Add ±5% per-action noise even inside a consistent session.

    Breaks the unnatural uniformity of identical inter-action delays.
    Deterministic per (account_id, mutation_state).
    Range: [0.95, 1.05].
    """
    seed = stable_hash_int(account_id, "micro", str(state)) % 1000 / 1000.0
    return 0.95 + seed * 0.10


def _session_boost(account_id: str, now: int) -> float:
    """Return a speed-up multiplier when the account is inside an active session.

    In-session: 0.7 base (faster actions). Doubled session length on outlier days.
    Out-of-session: start probability comes from per-account session personality;
    back-to-back starts are prevented by _can_start_session().
    """
    # Part 10.3: short-term obsession spike
    if _obsession(account_id, now):
        return 0.6
        
    if _in_session(account_id, now):
        state   = _SESSION_STATE[account_id]
        start   = state["start"]
        count   = state["count"]
        profile = _get_session_profile(account_id)
        # Part 2: outlier day → double session length
        session_len = profile["session_length"]
        if _outlier_session(account_id, now):
            session_len = session_len * 2
        if count >= session_len:
            # Session exhausted — record end time, clear active marker
            _SESSION_STATE[account_id] = {"last_end": now}
            return 1.0
        # Still inside session → increment counter
        _SESSION_STATE[account_id] = {"start": start, "count": count + 1, "last_end": state.get("last_end", 0)}
        # Part 2: outlier day → 0.6 speed multiplier (even faster burst)
        return 0.6 if _outlier_session(account_id, now) else 0.7
    else:
        if not _can_start_session(account_id, now):
            return 1.0   # inter-session cooldown not elapsed
        # Start probability: per-account personality, deterministic per minute-bucket
        profile = _get_session_profile(account_id)
        noise   = stable_hash_int(account_id, "session_start", str(now // 60)) % 1000 / 1000.0
        if noise < profile["session_chance"]:
            prev_last_end = _SESSION_STATE.get(account_id, {}).get("last_end", 0)
            _SESSION_STATE[account_id] = {"start": now, "count": 1, "last_end": prev_last_end}
        return 1.0


# ── Weak Ecosystem Trend Signal (Part 5 → smoothed) ─────────────────────────

def _trend_bias(now: int) -> float:
    """Global trend noise in [0.95, 1.05], interpolated across two hour-buckets.

    Blends the current bucket (70%) with the previous bucket (30%) to avoid
    the abrupt 1-hour step-changes of the original single-bucket version.
    Applied ONLY to posting frequency and mutation aggressiveness.
    """
    bucket = now // 3600
    t1 = stable_hash_int("global", "trend", str(bucket))     % 1000 / 1000.0
    t2 = stable_hash_int("global", "trend", str(bucket - 1)) % 1000 / 1000.0
    blended = t1 * 0.7 + t2 * 0.3
    return 0.95 + blended * 0.10   # [0.95, 1.05]


# ── Social Context (Part 11) ──────────────────────────────────────────────────

def _global_activity_wave(now: int) -> float:
    """Part 11.1: Global hourly wave multiplier (0.9–1.1)."""
    hour = now // 3600
    seed = stable_hash_int("global", "activity_wave", str(hour)) % 1000 / 1000.0
    return 0.9 + seed * 0.2


def _soft_sync(account_id: str, now: int) -> float:
    """Part 11.2: 30-min per-account bucket multiplier (0.95–1.05)."""
    bucket = now // 1800
    base = stable_hash_int(account_id, "sync", str(bucket)) % 1000 / 1000.0
    return 0.95 + base * 0.10


def _trend_follow(account_id: str, now: int) -> bool:
    """Part 11.3: 25% of accounts follow trend (faster) per hour."""
    seed = stable_hash_int(account_id, "trend_follow", str(now // 3600)) % 100
    return seed < 25


def _reaction_group(account_id: str) -> int:
    """Part 11.4: Reaction lag (0 fast, 1 medium, 2 slow)."""
    return stable_hash_int(account_id, "reaction_group") % 3


_TREND_STATE: dict[int, float] = {}

def _trend_momentum(now: int) -> float:
    """Part 12: Global trend momentum rolling state."""
    hour = now // 3600
    prev = _TREND_STATE.get(hour - 1, 0.5)
    noise = stable_hash_int("global", "trend_momentum", str(hour)) % 1000 / 1000.0
    value = prev * 0.7 + noise * 0.3
    _TREND_STATE[hour] = value
    return 0.9 + value * 0.2  # 0.9–1.1


def _is_burst_window(account_id: str, now: int) -> bool:
    """P6: ~24% effective burst rate (30% base * 80% after skip-6 gate).

    Adds window_id jitter (+0/+1) to break perfect 15-min periodicity.
    """
    offset    = stable_hash_int(account_id, "timing:burst_offset") % 300
    window_id = (now + offset) // 900
    # P6: micro-jitter on window boundary (±1 window)
    window_id += stable_hash_int(account_id, "timing:burst_jitter", str(window_id)) % 2
    v = stable_hash_int(account_id, "timing:burst", str(window_id)) % 10
    if v >= 3:
        return False
    # P6: tighten soft-skip to 1-in-6 (was 1-in-5)
    if stable_hash_int(account_id, "timing:burst_skip", str(window_id)) % 6 == 0:
        return False
    return True


def _should_mutate_now(
    account_id: str,
    elapsed: float,
    state: int,
    risk: str,
    now: int,
    created_ts: int = 0,
    platform: str = "generic",
) -> bool:
    """Risk-aware temporal smoothing — 4 context factors applied in sequence.

    Pipeline:
      base  = 120 (HIGH) | 300 (MEDIUM)
      raw   = stable_hash_int(account_id, "delay", state) % base
      delay = raw
            * _session_factor(account_id, now)    # time-of-day + per-account shift
            * _fatigue_factor(account_id, state)   # non-linear fatigue
            * _day_type_factor(now)                # weekday vs weekend
            * _account_age_factor(account_id, created_ts, now)

    First mutation (elapsed == 0) is always allowed.
    All computations are deterministic; no random() used.
    """
    if elapsed == 0:
        return True   # first-ever mutation, never block

    base      = 120 if risk == RiskLevel.HIGH else 300
    raw_delay = stable_hash_int(account_id, "timing:delay", str(state)) % base
    delay     = raw_delay
    delay     = int(delay * _session_factor(account_id, now))
    delay     = int(delay * _fatigue_factor(account_id, state))
    delay     = int(delay * _day_type_factor(now))
    delay     = int(delay * _account_age_factor(account_id, created_ts, now))
    # P1 HARD CLAMP: HIGH max ~300s, MEDIUM max ~750s.
    delay     = max(10, min(delay, int(base * 2.5)))
    # Part 10.2: Contradiction Injection
    is_contradict = _contradiction(account_id, now)

    # P1: single normalised-noise call (max ±15%). Namespaced key.
    noise_mult = _normalized_noise(account_id, "timing:noise", spread=0.15)
    # Part 1 — layer habit bias on top of noise, then clamp total to ±35%
    habit_mult = 1.0 if is_contradict else _habit_bias(account_id, now)
    combined   = max(1.0 - MAX_NOISE_IMPACT, min(1.0 + MAX_NOISE_IMPACT, noise_mult * habit_mult))
    delay     = max(10, int(delay * combined))
    
    # Part 3 — rhythm factor (routine-aware activity level, includes break days)
    rhythm    = 1.0 if is_contradict else _rhythm_factor(account_id, now)
    delay     = max(5, int(delay * rhythm))
    
    # Part 3 (mood) — 6-hour mood shift
    mood      = 1.0 if is_contradict else _MOOD_MULT[_get_mood(account_id, now)]
    delay     = max(5, int(delay * mood))
    
    # Part 4 — session clustering boost (includes outlier 0.6× on spike days)
    session   = 1.0 if is_contradict else _session_boost(account_id, now)
    delay     = max(5, int(delay * session))
    
    # Part 11 — Social Context
    wave      = 1.0 if is_contradict else _global_activity_wave(now)
    delay     = max(5, int(delay * wave))
    
    sync      = 1.0 if is_contradict else _soft_sync(account_id, now)
    delay     = max(5, int(delay * sync))
    
    momentum  = 1.0 if is_contradict else _trend_momentum(now)
    delay     = max(5, int(delay * momentum))
    
    trend     = 1.0 if is_contradict else (0.9 if _trend_follow(account_id, now) else 1.05)
    delay     = max(5, int(delay * trend))
    
    lag_group = 0 if is_contradict else _reaction_group(account_id)
    if lag_group == 1:
        delay = max(5, int(delay * 1.1))
    elif lag_group == 2:
        delay = max(5, int(delay * 1.25))
    
    # Part 4 — micro-variation: ±5% per-action inconsistency
    delay     = max(5, int(delay * _micro_variation(account_id, state)))
    # Additive micro-jitter ±10s.
    delay     = max(5, delay + _micro_jitter(account_id, now))
    # P2: inertia — blend with previous delay to smooth timing.
    prev = _PREV_DELAYS.get(account_id, delay)
    delay = int(prev * 0.7 + delay * 0.3)
    delay = max(10, min(delay, int(base * 2.5)))  # re-clamp after blend
    _PREV_DELAYS[account_id] = delay
    # Part 13 — Platform tuning (final layer, applied after all other factors)
    delay = _apply_platform_mods(account_id, state, delay, platform, is_contradict)
    delay = max(10, min(delay, int(base * 2.5)))  # re-clamp post-platform
    # Part 14 — Lifecycle activity multiplier (lazy import, no circular dep)
    try:
        from core.lifecycle_engine import get_activity_mult
        lc_mult = get_activity_mult(account_id, created_ts, now)
        delay   = max(10, min(int(base * 2.5), int(delay * lc_mult)))
    except Exception:
        pass
    return elapsed >= delay


# ── Platform-specific tuning (Part 13) ───────────────────────────────────────

def _apply_platform_mods(
    account_id: str,
    state: int,
    delay: int,
    platform: str,
    is_contradict: bool,
) -> int:
    """Apply platform profile multipliers as the final tuning layer.

    All multipliers are clamped to [0.6, 1.4] before application (Part 13 hard constraint).
    Micro-variation extra spread and delay floor are also enforced here.
    Skips all adjustments if a contradiction anomaly is active.
    """
    if is_contradict:
        return delay

    prof   = PLATFORM_PROFILES.get(platform, _DEFAULT_PLATFORM_PROFILE)

    # Clamp the base delay multiplier within [0.6, 1.4]
    base_m = max(0.6, min(1.4, prof["delay_base_mult"]))
    delay  = int(delay * base_m)

    # Platform micro-variation extra (additive spread on top of existing ±5%)
    extra  = prof.get("micro_var_extra", 0.0)
    if extra > 0:
        seed  = stable_hash_int(account_id, "platform_micro", str(state)) % 1000 / 1000.0
        delay = int(delay * (1.0 + (seed - 0.5) * 2.0 * extra))

    # Platform-specific EMA smoothing (overrides / refines existing inertia blend)
    ema   = prof.get("ema_smooth", 0.7)
    prev  = _PREV_DELAYS.get(account_id, delay)
    delay = int(prev * ema + delay * (1.0 - ema))
    _PREV_DELAYS[account_id] = delay

    # Enforce platform delay floor
    floor = prof.get("delay_floor", 10)
    return max(floor, delay)


# ── MutationController ────────────────────────────────────────────────────────

class MutationController:
    """
    Stateless gatekeeper: all IdentityProfile mutations go through apply().

    Guarantees:
      - Per-risk action filtering (MEDIUM safe-list, HIGH full access).
      - Variable per-account cooldown via _cooldown() (180-360s).
      - Risk-aware temporal smoothing via _should_mutate_now().
      - Session factor (time-of-day) and fatigue factor (mutation cycle).
      - Burst mode via _is_burst_window() (~30% of 15-min windows).
      - Partial-drift cap (MAX_PARTIALS_BEFORE_FULL).
      - Snapshot captured before every mutation for rollback support.
      - Audit trail appended to profile.mutation_history (max 20 entries).
    """

    def apply(
        self,
        profile: "IdentityProfile",
        strategy: Strategy,
        platform: str = "generic",
    ) -> MutationResult:
        """Apply strategy to profile and return a MutationResult.

        Skips mutation (returns "none") when:
          - risk_level is LOW
          - last mutation was less than MIN_MUTATION_INTERVAL_S ago

        Auto-escalates MEDIUM → HIGH when partial drift cap is exceeded.
        platform: name of target platform, used for behavioural tuning (Part 13).
        """
        snapshot = _capture_snapshot(profile)

        # LOW → no-op
        if strategy.risk_level == RiskLevel.LOW:
            LOGGER.debug(
                "mutation_skipped account=%s reason=low_risk", profile.account_id
            )
            return MutationResult(
                mutation_type="none",
                changed_fields=[],
                new_fingerprint_hash=profile.fingerprint_hash,
                mutation_state=profile.mutation_state,
                pre_mutation_snapshot=snapshot,
                reason="low_risk",
            )

        # ── Cooldown + temporal smoothing + burst gating ──────────────────
        # elapsed: seconds since last mutation (0 if no history = first mutation)
        elapsed = 0.0
        last_ts = 0.0
        if profile.mutation_history:
            last_ts = profile.mutation_history[-1].get("ts", 0.0)
            elapsed = time.time() - last_ts

        now = int(time.time())
        cooldown = _cooldown(profile.account_id, profile.mutation_state)

        if profile.mutation_history and elapsed < cooldown:
            # Hard cooldown: always block regardless of burst or risk level.
            LOGGER.info(
                "mutation_skipped account=%s reason=cooldown elapsed=%.0fs cooldown=%ds",
                profile.account_id, elapsed, cooldown,
            )
            return MutationResult(
                mutation_type="none",
                changed_fields=[],
                new_fingerprint_hash=profile.fingerprint_hash,
                mutation_state=profile.mutation_state,
                pre_mutation_snapshot=snapshot,
                reason=f"cooldown: {elapsed:.0f}s < {cooldown}s",
            )

        # Past cooldown: apply temporal smoothing unless we're in a burst window.
        # Burst window -> skip delay check (aggressive micro-burst).
        # Outside burst -> full 4-factor delay pipeline.
        # P8: suppress burst mode on first session — no precedent to burst against.
        in_burst = _is_burst_window(profile.account_id, now) if profile.mutation_history else False
        risk_str  = strategy.risk_level

        # Resolve account creation timestamp (fallback: oldest history entry or now)
        created_ts: int = getattr(profile, "created_at", 0) or 0
        if not created_ts and profile.mutation_history:
            created_ts = int(profile.mutation_history[0].get("ts", now))
        if not created_ts:
            created_ts = now

        if not in_burst and not _should_mutate_now(
            profile.account_id, elapsed, profile.mutation_state, risk_str, now, created_ts, platform
        ):
            base  = 120 if risk_str == RiskLevel.HIGH else 300
            raw   = stable_hash_int(profile.account_id, "delay", str(profile.mutation_state)) % base
            delay = raw
            delay = int(delay * _session_factor(profile.account_id, now))
            delay = int(delay * _fatigue_factor(profile.account_id, profile.mutation_state))
            delay = int(delay * _day_type_factor(now))
            delay = int(delay * _account_age_factor(profile.account_id, created_ts, now))
            # Log-path mirrors _should_mutate_now for accurate skip-reason reporting
            is_contradict = _contradiction(profile.account_id, now)

            # P1: single normalised-noise call (max ±15%). Namespaced key.
            noise_mult = _normalized_noise(profile.account_id, "timing:noise", spread=0.15)
            # Part 1 — layer habit bias on top of noise, then clamp total to ±35%
            habit_mult = 1.0 if is_contradict else _habit_bias(profile.account_id, now)
            combined   = max(1.0 - MAX_NOISE_IMPACT, min(1.0 + MAX_NOISE_IMPACT, noise_mult * habit_mult))
            delay     = max(10, int(delay * combined))
            
            # Part 3 — rhythm factor (routine-aware activity level, includes break days)
            rhythm    = 1.0 if is_contradict else _rhythm_factor(profile.account_id, now)
            delay     = max(5, int(delay * rhythm))
            
            # Part 3 (mood) — 6-hour mood shift
            mood      = 1.0 if is_contradict else _MOOD_MULT[_get_mood(profile.account_id, now)]
            delay     = max(5, int(delay * mood))
            
            # Part 4 — session clustering boost (includes outlier 0.6× on spike days)
            session   = 1.0 if is_contradict else _session_boost(profile.account_id, now)
            delay     = max(5, int(delay * session))
            
            # Part 11 — Social Context
            wave      = 1.0 if is_contradict else _global_activity_wave(now)
            delay     = max(5, int(delay * wave))
            
            sync      = 1.0 if is_contradict else _soft_sync(profile.account_id, now)
            delay     = max(5, int(delay * sync))
            
            momentum  = 1.0 if is_contradict else _trend_momentum(now)
            delay     = max(5, int(delay * momentum))
            
            trend     = 1.0 if is_contradict else (0.9 if _trend_follow(profile.account_id, now) else 1.05)
            delay     = max(5, int(delay * trend))
            
            lag_group = 0 if is_contradict else _reaction_group(profile.account_id)
            if lag_group == 1:
                delay = max(5, int(delay * 1.1))
            elif lag_group == 2:
                delay = max(5, int(delay * 1.25))
            
            # Part 4 — micro-variation: ±5% per-action inconsistency
            delay     = max(5, int(delay * _micro_variation(profile.account_id, profile.mutation_state)))
            
            delay = max(10, min(delay, int(base * 2.5)))
            # Part 13 — Platform tuning (log-path mirrors _should_mutate_now)
            delay = _apply_platform_mods(profile.account_id, profile.mutation_state,
                                         delay, platform, is_contradict)
            delay = max(10, min(delay, int(base * 2.5)))  # re-clamp post-platform
            # Part 14 — Lifecycle activity multiplier (log-path)
            try:
                from core.lifecycle_engine import get_activity_mult
                lc_mult = get_activity_mult(profile.account_id, created_ts, now)
                delay   = max(10, min(int(base * 2.5), int(delay * lc_mult)))
            except Exception:
                pass
            LOGGER.debug(
                "mutation_skipped account=%s reason=temporal_delay elapsed=%.0fs delay=%ds",
                profile.account_id, elapsed, delay,
            )
            return MutationResult(
                mutation_type="none",
                changed_fields=[],
                new_fingerprint_hash=profile.fingerprint_hash,
                mutation_state=profile.mutation_state,
                pre_mutation_snapshot=snapshot,
                reason=f"temporal_delay: {elapsed:.0f}s < {delay}s (risk={risk_str.value} burst=False)",
            )

        # P6/P9: skip — models user distraction / interrupted session.
        # Recovery rule: if 2+ consecutive skips → force execute (reset counter).
        _skip_consec = _CONSECUTIVE_SKIPS.get(profile.account_id, 0)
        _force_execute = _skip_consec >= 2   # P6: max 2 consecutive skips
        if not _force_execute and profile.mutation_history and _skip_action(profile.account_id, now, elapsed):
            _CONSECUTIVE_SKIPS[profile.account_id] = _skip_consec + 1
            LOGGER.debug("mutation_skipped account=%s reason=user_skip consec=%d",
                         profile.account_id, _skip_consec + 1)
            return MutationResult(
                mutation_type="none",
                changed_fields=[],
                new_fingerprint_hash=profile.fingerprint_hash,
                mutation_state=profile.mutation_state,
                pre_mutation_snapshot=snapshot,
                reason="user_skip",
            )
        _CONSECUTIVE_SKIPS[profile.account_id] = 0  # reset on any successful proceed

        # MEDIUM drift cap: count partials since last full regen
        if strategy.risk_level == RiskLevel.MEDIUM:
            partials = _count_partials_since_full(profile)
            dist = fingerprint_distance(profile.base_fingerprint, profile.active_fingerprint)
            if partials >= MAX_PARTIALS_BEFORE_FULL or dist > _MAX_DISTANCE:
                LOGGER.warning(
                    "mutation_drift_exceeded account=%s partials=%d dist=%.3f → full",
                    profile.account_id, partials, dist,
                )
                strategy = Strategy(
                    risk_level=RiskLevel.HIGH,
                    actions=strategy.actions,
                    delay_multiplier=strategy.delay_multiplier,
                    warmup_delay=strategy.warmup_delay,
                    interaction_mode=strategy.interaction_mode,
                    reason=f"drift_exceeded partials={partials} dist={dist:.3f}",
                )

        if strategy.risk_level == RiskLevel.MEDIUM:
            return self._apply_partial(profile, strategy, snapshot)
        else:
            return self._apply_full(profile, strategy, snapshot)

    # ── Partial (MEDIUM) ──────────────────────────────────────────────────────

    @staticmethod
    def _apply_partial(
        profile: "IdentityProfile",
        strategy: Strategy,
        snapshot: dict,
    ) -> MutationResult:
        """Rotate at most: canvas_noise_seed (rotate_canvas/rotate_audio) + geo (sync_geo).

        Only safe action types are executed. rotate_gpu and cooldown are ignored at MEDIUM.
        Seed is derived from (account_id, mutation_state) — no external entropy.
        """
        from core.identity_manager import generate_fingerprint

        action_types = {a.type for a in strategy.actions} & _MEDIUM_SAFE_ACTIONS
        changed: list[str] = []

        # Seed for partial: stable hash of (account_id, next_state).
        # Not yet incremented — partial keeps mutation_state unchanged.
        seed = stable_hash_int(profile.account_id, str(profile.mutation_state + 1))

        if "rotate_canvas" in action_types or "rotate_audio" in action_types:
            profile.canvas_noise_seed = _seeded_int(seed, 10, 100_000, 999_999)
            changed.append("canvas_noise_seed")

        if "sync_geo" in action_types:
            for action in strategy.actions:
                if action.type == "sync_geo":
                    new_tz   = action.metadata.get("timezone")
                    new_lang = action.metadata.get("language")
                    if new_tz and new_tz != profile.timezone:
                        profile.timezone = new_tz
                        changed.append("timezone")
                    if new_lang and new_lang != profile.locale:
                        profile.locale = new_lang
                        changed.append("locale")

        if not changed:
            # No matching safe actions — treat as no-op
            return MutationResult(
                mutation_type="none",
                changed_fields=[],
                new_fingerprint_hash=profile.fingerprint_hash,
                mutation_state=profile.mutation_state,
                pre_mutation_snapshot=snapshot,
                reason="medium_no_safe_actions",
            )

        new_hash = generate_fingerprint(profile)
        profile.fingerprint_hash   = new_hash
        profile.active_fingerprint = new_hash
        _record_history(profile, "partial", changed, new_hash)

        reason = f"partial changed={changed} seed={seed:#010x}"
        LOGGER.info(
            "mutation_partial account=%s changed=%s hash=%s→%s",
            profile.account_id, changed,
            snapshot["fingerprint_hash"][:8], new_hash[:8],
        )
        return MutationResult(
            mutation_type="partial",
            changed_fields=changed,
            new_fingerprint_hash=new_hash,
            mutation_state=profile.mutation_state,
            pre_mutation_snapshot=snapshot,
            reason=reason,
        )

    # ── Full (HIGH) ───────────────────────────────────────────────────────────

    @staticmethod
    def _apply_full(
        profile: "IdentityProfile",
        strategy: Strategy,
        snapshot: dict,
    ) -> MutationResult:
        """Full identity regen: increment mutation_state, regen both noise seeds.

        Seed = hash(account_id + new_mutation_state) — reproducible, no entropy mixing.
        Hardware identity (device_type, os, screen, UA) is NEVER touched.
        Geo (timezone/locale) updated only when explicit sync_geo action is present.
        """
        from core.identity_manager import generate_fingerprint

        # FIX 3: non-linear delta (1 or 2) — breaks perfectly linear drift pattern.
        # Deterministic: same (account_id, mutation_state) always gives same delta.
        delta = 1 + (stable_hash_int(profile.account_id, str(profile.mutation_state)) % 2)
        new_state = profile.mutation_state + delta

        # Base seed from stable hash of (account_id, new_state).
        base_seed = stable_hash_int(profile.account_id, str(new_state))

        # BONUS: micro-jitter XOR — prevents identical fingerprints across
        # accounts that happen to land on the same effective mutation_state.
        jitter = stable_hash_int(profile.account_id, "jitter", str(new_state)) % 1000
        seed = base_seed ^ jitter

        profile.canvas_noise_seed = _seeded_int(seed, 10, 100_000, 999_999)
        profile.webgl_noise_seed  = _seeded_int(seed, 11, 100_000, 999_999)
        profile.mutation_state    = new_state

        # Geo update only when explicitly requested
        action_types = {a.type for a in strategy.actions}
        if "sync_geo" in action_types:
            for action in strategy.actions:
                if action.type == "sync_geo":
                    if action.metadata.get("timezone"):
                        profile.timezone = action.metadata["timezone"]
                    if action.metadata.get("language"):
                        profile.locale = action.metadata["language"]

        new_hash = generate_fingerprint(profile)
        profile.fingerprint_hash   = new_hash
        profile.active_fingerprint = new_hash

        changed = ["canvas_noise_seed", "webgl_noise_seed"]
        # PART 3: per-account target rotation — field order differs per account
        # so mutation sequences don't cluster visibly across the fleet.
        shift = stable_hash_int(profile.account_id, "target_shift") % len(changed)
        changed = changed[shift:] + changed[:shift]
        reason = f"full state={new_state} seed={seed:#010x} trigger={strategy.reason!r}"
        _record_history(profile, "full", changed, new_hash)

        LOGGER.warning(
            "mutation_full account=%s state=%d hash=%s→%s reason=%s",
            profile.account_id, new_state,
            snapshot["fingerprint_hash"][:8], new_hash[:8], strategy.reason,
        )
        return MutationResult(
            mutation_type="full",
            changed_fields=changed,
            new_fingerprint_hash=new_hash,
            mutation_state=new_state,
            pre_mutation_snapshot=snapshot,
            reason=reason,
        )

    # ── Snapshot / Rollback ───────────────────────────────────────────────────

    @staticmethod
    def restore_snapshot(profile: "IdentityProfile", snapshot: dict) -> None:
        """Restore profile mutable surface from a pre_mutation_snapshot.

        Call this if post-mutation validation reveals the new fingerprint
        triggers additional detectors. Hardware fields are never affected.
        """
        from core.identity_manager import generate_fingerprint
        profile.canvas_noise_seed  = snapshot["canvas_noise_seed"]
        profile.webgl_noise_seed   = snapshot["webgl_noise_seed"]
        profile.mutation_state     = snapshot["mutation_state"]
        profile.timezone           = snapshot["timezone"]
        profile.locale             = snapshot["locale"]
        restored = generate_fingerprint(profile)
        profile.fingerprint_hash   = restored
        profile.active_fingerprint = restored
        LOGGER.warning(
            "mutation_rolled_back account=%s hash=%s",
            profile.account_id, restored[:8],
        )


# ── Module-level helpers ──────────────────────────────────────────────────────

def _capture_snapshot(profile: "IdentityProfile") -> dict:
    """Snapshot the mutable surface (noise seeds + geo) before any mutation."""
    return {
        "fingerprint_hash":   profile.fingerprint_hash,
        "active_fingerprint": profile.active_fingerprint,
        "canvas_noise_seed":  profile.canvas_noise_seed,
        "webgl_noise_seed":   profile.webgl_noise_seed,
        "mutation_state":     profile.mutation_state,
        "timezone":           profile.timezone,
        "locale":             profile.locale,
        "captured_at":        time.time(),
    }


def _record_history(
    profile: "IdentityProfile",
    mut_type: str,
    changed: list[str],
    new_hash: str,
) -> None:
    """Append mutation entry to profile.mutation_history (capped at 20)."""
    profile.mutation_history.append({
        "ts":             time.time(),
        "type":           mut_type,
        "changed":        changed,
        "hash":           new_hash[:12],
        "mutation_state": profile.mutation_state,
    })
    if len(profile.mutation_history) > 20:
        profile.mutation_history.pop(0)


def _count_partials_since_full(profile: "IdentityProfile") -> int:
    """Count consecutive partial mutations since the last full regen."""
    count = 0
    for entry in reversed(profile.mutation_history):
        if entry.get("type") == "full":
            break
        if entry.get("type") == "partial":
            count += 1
    return count


def fingerprint_distance(base: str, active: str) -> float:
    """Hamming bit-distance between two hex fingerprint hashes.

    Returns 0.0 (identical) to 1.0 (all bits differ).
    Used to detect excessive drift from base identity.
    """
    if not base or not active:
        return 0.0
    length = min(len(base), len(active))
    diff_bits = total_bits = 0
    for i in range(0, length - 1, 2):
        xor = int(base[i:i+2], 16) ^ int(active[i:i+2], 16)
        diff_bits  += bin(xor).count("1")
        total_bits += 8
    return diff_bits / total_bits if total_bits else 0.0


# ── Singleton ─────────────────────────────────────────────────────────────────

_MUTATION_CONTROLLER: MutationController | None = None


def get_mutation_controller() -> MutationController:
    """Return the process-level MutationController singleton."""
    global _MUTATION_CONTROLLER
    if _MUTATION_CONTROLLER is None:
        _MUTATION_CONTROLLER = MutationController()
    return _MUTATION_CONTROLLER
