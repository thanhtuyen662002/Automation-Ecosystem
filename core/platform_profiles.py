"""
Platform Profiles — behavioural tuning layer for MutationController.

Each profile describes how a single human *feels different* per platform.
These are pure multipliers applied ON TOP of the existing pipeline —
they do NOT replace any determinism, account isolation, or bounds logic.

Key:
    delay_base_mult    : scales the final computed delay (< 1 = faster, > 1 = slower)
    session_multiplier : scales session_boost factor
    burstiness         : scales burst window probability effect (informational; used via _apply_platform_mods)
    obsession_rate     : scales obsession probability threshold
    trend_sensitivity  : scales trend_follow and momentum impact
    delay_floor        : hard lower bound on delay (seconds) — platform-specific min
    skip_prob_mult     : scales skip probability (< 1 = fewer skips = more active)
    micro_var_extra    : additive extra spread on micro_variation [0.0 = off]
    ema_smooth         : extra EMA weight to blend prev delay (1.0 = no smoothing; higher → smoother)

Hard constraints (enforced in _apply_platform_mods):
    - ALL multipliers are clamped to [0.6, 1.4] before application.
    - delay_floor is enforced AFTER all multipliers.
    - No platform touches account isolation or determinism.
"""
from __future__ import annotations

DEFAULT_PROFILE: dict = {
    "delay_base_mult":    1.0,
    "session_multiplier": 1.0,
    "burstiness":         1.0,
    "obsession_rate":     1.0,
    "trend_sensitivity":  1.0,
    "delay_floor":        10,       # seconds
    "skip_prob_mult":     1.0,
    "micro_var_extra":    0.0,
    "ema_smooth":         0.7,      # default inertia weight (matches existing pipeline)
}

PLATFORM_PROFILES: dict[str, dict] = {
    "tiktok": {
        # Fast-paced, short-form: bursts hard, trends quickly, short idle gaps
        "delay_base_mult":    0.80,
        "session_multiplier": 0.70,
        "burstiness":         1.30,
        "obsession_rate":     1.40,
        "trend_sensitivity":  1.30,
        "delay_floor":        8,     # allow shorter floor (≥8s as spec requires)
        "skip_prob_mult":     0.85,  # fewer skips — highly engaging
        "micro_var_extra":    0.02,
        "ema_smooth":         0.65,  # less inertia = snappier transitions
    },
    "facebook": {
        # Social browsing: steady pacing, low burstiness, smooth rhythm
        "delay_base_mult":    1.05,
        "session_multiplier": 1.10,
        "burstiness":         0.80,
        "obsession_rate":     0.70,
        "trend_sensitivity":  0.90,
        "delay_floor":        10,
        "skip_prob_mult":     1.10,
        "micro_var_extra":    0.0,
        "ema_smooth":         0.85,  # extra EMA smoothing (reduces burst spikes)
    },
    "youtube": {
        # Long-form video: long sessions, very few skips, moderate trends
        "delay_base_mult":    1.15,
        "session_multiplier": 1.30,
        "burstiness":         0.60,
        "obsession_rate":     0.80,
        "trend_sensitivity":  1.10,
        "delay_floor":        10,
        "skip_prob_mult":     0.70,  # much fewer skips — watch-through behaviour
        "micro_var_extra":    0.0,
        "ema_smooth":         0.80,
    },
    "instagram": {
        # Visual discovery: trend-heavy, obsession combos, slightly higher variation
        "delay_base_mult":    0.90,
        "session_multiplier": 0.90,
        "burstiness":         1.10,
        "obsession_rate":     1.20,
        "trend_sensitivity":  1.20,
        "delay_floor":        8,
        "skip_prob_mult":     0.90,
        "micro_var_extra":    0.03,  # slightly higher per-action inconsistency
        "ema_smooth":         0.68,
    },
    "zalo": {
        # Messaging: slow, deliberate, almost no bursts, long idle gaps
        "delay_base_mult":    1.20,
        "session_multiplier": 1.20,
        "burstiness":         0.50,
        "obsession_rate":     0.50,
        "trend_sensitivity":  0.60,
        "delay_floor":        15,    # enforce longer idle gaps
        "skip_prob_mult":     1.30,  # more skips — people walk away from chat
        "micro_var_extra":    0.0,
        "ema_smooth":         0.90,  # very smooth transitions
    },
    "shopee": {
        # Shopping intent spikes: high obsession, short focused sessions, fast
        "delay_base_mult":    0.85,
        "session_multiplier": 0.60,
        "burstiness":         1.40,
        "obsession_rate":     1.30,
        "trend_sensitivity":  0.80,
        "delay_floor":        8,
        "skip_prob_mult":     0.80,
        "micro_var_extra":    0.01,
        "ema_smooth":         0.60,  # very snappy — intent-driven browsing
    },
}
