"""
core/unified_scoring.py — Single Source of Truth for Content Decisions  (v4)

Changelog:
  v2 — pattern_strength, 7-component formula, portfolio adjustments,
        compute_decision_score(), compute_pattern_strength()
  v3 — adjusted weights, reduced EV bias, saturation/affinity hooks,
        adaptive explore rate
  v4 — confidence penalty on decision_score (Part 3)
       time-decay saturation penalty (Part 4)
       causal boost from pattern_graph (Part 1)
       per-pattern explore rate via portfolio_memory (Part 5)
       get_market_phase() now returns (phase, confidence) tuple

Public API (all v3 signatures preserved):
    compute_unified_score(signals, pattern_key, niche, active_patterns) -> float
    compute_decision_score(unified, ev_norm, trend_score, confidence)   -> float
    apply_portfolio_adjustments(score, pattern_key, niche, account_id)  -> float
    get_explore_rate(phase, pattern_fatigue)                            -> float
    decide_action(decision_score, content_id, seed, explore_rate)       -> str
    should_explore(content_id, seed, explore_rate)                      -> bool
    compute_pattern_strength(success_rate, total_views, stability)      -> float
"""
from __future__ import annotations

import hashlib
import math
from typing import Any

# ── Thresholds ────────────────────────────────────────────────────────────────

_EXPLORE_RATE       = 0.10
_SCALE_THRESHOLD    = 0.75
_OPTIMIZE_THRESHOLD = 0.50

# ── Normalization ceilings ────────────────────────────────────────────────────

_CTR_CEIL = 0.15
_CVR_CEIL = 0.20
_EPV_CEIL = 5.0

# ── Penalty thresholds ────────────────────────────────────────────────────────

_FATIGUE_THRESHOLD    = 0.70
_SATURATION_THRESHOLD = 0.70


# ── Pattern strength helper ───────────────────────────────────────────────────

def compute_pattern_strength(
    success_rate: float,
    total_views:  float,
    stability:    float,
) -> float:
    """
    pattern_strength = success_rate × log(1 + total_views) × stability
    Normalized to ceiling log1p(10_000) ≈ 9.21
    """
    raw  = success_rate * math.log1p(max(0.0, total_views)) * max(0.0, stability)
    ceil = math.log1p(10_000)
    return round(max(0.0, min(1.0, raw / ceil)), 4) if ceil > 0 else 0.0


# ── compute_unified_score (v3 weights, causal boost, time-decay sat) ─────────

def compute_unified_score(
    signals:         dict[str, Any],
    pattern_key:     str = "",
    niche:           str = "",
    active_patterns: list[str] | None = None,
) -> float:
    """
    v3 weights (sum = 1.0):
        revenue_score    0.25
        performance_score 0.20
        cvr_norm         0.15
        epv_norm         0.15
        pattern_strength 0.15
        funnel_score     0.10
        ctr_norm         0.05

    Post-formula adjustments:
        • Causal graph boost       (Part 1) — only real causal lift
        • Time-decay sat penalty   (Part 4) — exp-decay, not count
    """
    revenue_score     = float(signals.get("revenue_score",     0.5))
    performance_score = float(signals.get("performance_score", 0.5))
    ctr               = float(signals.get("ctr",               0.0))
    cvr               = float(signals.get("cvr",               0.0))
    epv               = float(signals.get("epv",               0.0))
    funnel_score      = float(signals.get("funnel_score",      0.5))
    pattern_strength  = float(signals.get("pattern_strength",  0.5))

    ctr_norm = min(ctr / _CTR_CEIL, 1.0)
    cvr_norm = min(cvr / _CVR_CEIL, 1.0)
    epv_norm = min(epv / _EPV_CEIL, 1.0)

    unified = (
        0.25 * revenue_score     +
        0.20 * performance_score +
        0.15 * cvr_norm          +
        0.15 * epv_norm          +
        0.15 * pattern_strength  +
        0.10 * funnel_score      +
        0.05 * ctr_norm
    )
    unified = max(0.0, min(1.0, unified))

    # ── Part 1: Causal graph boost (confidence-weighted) ─────────────────────
    if pattern_key and active_patterns:
        try:
            from core.portfolio_memory import get_pattern_boost as _get_boost
            boost = _get_boost(pattern_key, active_patterns)
            unified = min(1.0, unified * boost)
        except Exception:
            pass

    # ── Part 4: Time-decay saturation penalty ────────────────────────────────
    if pattern_key:
        try:
            from core.portfolio_memory import get_saturation_penalty as _get_sat_pen
            unified = unified * _get_sat_pen(pattern_key)
        except Exception:
            pass

    return round(max(0.0, min(1.0, unified)), 4)


# ── Portfolio adjustments (fatigue + account) ─────────────────────────────────

def apply_portfolio_adjustments(
    score:       float,
    pattern_key: str = "",
    niche:       str = "",
    account_id:  str = "",
) -> float:
    """
    Multiplicative portfolio boosts/penalties:
      - pattern in top_patterns  → × 1.10
      - fatigue_score > 0.70     → × 0.75
      - account_trend < 0.40     → × 0.85
    """
    try:
        from core.portfolio_memory import (
            get_top_patterns,
            get_pattern_fatigue,
            get_account_trend,
        )
        if pattern_key and niche:
            top = get_top_patterns(niche, k=5)
            if pattern_key in top:
                score = min(1.0, score * 1.10)
            fat = get_pattern_fatigue(pattern_key)
            if fat > _FATIGUE_THRESHOLD:
                score = score * 0.75

        if account_id:
            trend = get_account_trend(account_id)
            if trend < 0.40:
                score = score * 0.85
    except Exception:
        pass
    return round(max(0.0, min(1.0, score)), 4)


# ── Part 3: Predictive decision score with confidence penalty ─────────────────

def compute_decision_score(
    unified_score: float,
    ev_norm:       float,
    trend_norm:    float,
    confidence:    float = 1.0,
) -> float:
    """
    Forward-aware decision score [0,1] with confidence penalty.

    Formula:
        raw =  0.65 * unified_score
             + 0.25 * ev_norm
             + 0.10 * trend_norm

    Confidence penalty (Part 3):
        decision_score = raw * (0.70 + 0.30 * confidence)

    confidence=1.0 → no penalty (full signal)
    confidence=0.0 → score scaled to 70% of raw
    """
    raw = (
        0.65 * max(0.0, min(1.0, unified_score)) +
        0.25 * max(0.0, min(1.0, ev_norm))       +
        0.10 * max(0.0, min(1.0, trend_norm))
    )
    conf_factor = 0.70 + 0.30 * max(0.0, min(1.0, confidence))
    return round(max(0.0, min(1.0, raw * conf_factor)), 4)


# ── Part 5: exploration rate (backward-compat + per-pattern delegation) ───────

def get_explore_rate(
    phase:           str   = "neutral",
    pattern_fatigue: float = 0.0,
    pattern_key:     str   = "",
    confidence:      float = 1.0,
) -> float:
    """
    Context-aware exploration rate.

    If pattern_key is provided, delegates to portfolio_memory for per-pattern rate.
    Otherwise falls back to legacy phase + fatigue logic.

    Per-pattern formula (Part 5):
        base = {rising: 0.05, neutral: 0.10, declining: 0.20}[phase]
        rate = base + (1 - confidence) * 0.15

    Legacy fallback:
        fatigue > 0.7 → 0.25
        declining     → 0.20
        rising        → 0.05
        stable/neutral→ 0.10
    """
    if pattern_key:
        try:
            from core.portfolio_memory import get_explore_rate_for_pattern
            return get_explore_rate_for_pattern(pattern_key, phase, confidence)
        except Exception:
            pass
    # Legacy fallback
    if pattern_fatigue > 0.70:
        rate = 0.25
    elif phase == "declining":
        rate = 0.20
    elif phase == "rising":
        rate = 0.05
    else:
        rate = 0.10
    # Fix 3: safety floor for low confidence
    if confidence < 0.30:
        rate = max(rate, 0.20)
    return rate


# ── Decision gate ─────────────────────────────────────────────────────────────

def decide_action(
    decision_score: float,
    content_id:     str   = "",
    seed:           str   = "",
    explore_rate:   float = _EXPLORE_RATE,
) -> str:
    """Returns 'scale' | 'optimize' | 'explore'."""
    if should_explore(content_id, seed=seed, explore_rate=explore_rate):
        return "explore"
    if decision_score >= _SCALE_THRESHOLD:
        return "scale"
    if decision_score >= _OPTIMIZE_THRESHOLD:
        return "optimize"
    return "explore"


def should_explore(
    content_id:   str,
    seed:         str   = "",
    explore_rate: float = _EXPLORE_RATE,
) -> bool:
    """Deterministic exploration gate."""
    raw = seed or content_id
    h   = int(hashlib.sha256(raw.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return h < explore_rate
