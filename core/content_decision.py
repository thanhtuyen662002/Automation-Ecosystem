"""
Content Decision Layer v4 — Profit-Aware Decision Engine.

Pipeline: ANALYZE → SCORE → EV_GATE → FILTER → PRODUCE → REVIEW → PUBLISH → TRACK
       → profit_eval → scaling → decision_feedback → next cycle

Design contracts:
  - Internal hook estimator — no external dependency
  - Expected Value (EV) = trend × product_intent × hook_potential
  - final_score = EV - real_cost  |  final_score < 0 → immediate DROP
  - Generate mode: EV < 0.1 → cost_rejected
  - Generate mode: profit_score < 0.2 → low_profit_block
  - Remark mode: match_score < 0.6 → match_guard_drop
  - profit_score sourced from profit_engine EMA (feedback loop)
  - should_produce() is the worker-level gate (call before any expensive op)
  - 100% deterministic · zero external I/O · ML-ready EMA tables
"""
from __future__ import annotations

import hashlib
import logging
import math
import random
import re
from dataclasses import dataclass, field
from typing import Any

LOGGER = logging.getLogger("core.content_decision")


# ── Mode thresholds ───────────────────────────────────────────────────────────

_MODE_CONFIG: dict[str, dict[str, float]] = {
    "reup": {
        "threshold":    0.30,
        "keep_ratio":   0.50,
        "explore_pct":  0.10,
        "cost_scale":   0.40,
        "min_ev":       0.00,   # reup: EV >= 0 required
    },
    "remark": {
        "threshold":    0.45,
        "keep_ratio":   0.35,
        "explore_pct":  0.10,
        "cost_scale":   0.70,
        "match_guard":  0.60,
        "min_ev":       0.05,
    },
    "generate": {
        "threshold":    0.55,
        "keep_ratio":   0.25,
        "explore_pct":  0.10,
        "cost_scale":   1.00,
        "min_ev":       0.10,   # HARD RULE: generate needs EV >= 0.1
    },
}
_DEFAULT_MODE_CONFIG = _MODE_CONFIG["remark"]

# ── Signal weights (v4) ──────────────────────────────────────────────────────
# NEW: profit_score (0.15) replaces half of trend weight
# sum of positive weights = 1.00; negative = -0.15
_SCORE_WEIGHTS: dict[str, float] = {
    "trend_score":     0.20,
    "product_intent":  0.20,
    "hook_potential":  0.15,
    "match_score":     0.10,
    "historical_perf": 0.10,
    "profit_score":    0.15,
    "novelty_score":   0.10,
    "production_cost": -0.15,
}
# Normalisation: raw ∈ [−0.15, 0.85] → normalised ∈ [0, 1]
_SCORE_MIN: float   = -0.15
_SCORE_MAX: float   =  0.85
_SCORE_RANGE: float = _SCORE_MAX - _SCORE_MIN  # 1.00

# Hard gate threshold for profit in generate mode
_PROFIT_HARD_GATE_GENERATE: float = 0.20

# ── Hook estimator keywords (curiosity_signal proxy) ─────────────────────────
_CURIOSITY_KEYWORDS: frozenset[str] = frozenset({
    "why", "secret", "top", "best", "hack", "trick", "revealed", "hidden",
    "truth", "real", "honest", "never", "always", "only", "how", "what",
    "shocking", "viral", "must", "need", "stop", "wait", "watch", "look",
    "found", "discovered", "cant", "can't", "wont", "won't", "finally",
    "bí", "thật", "cách", "đỉnh", "hot", "viral", "must-have",
})

# ── EMA stores ────────────────────────────────────────────────────────────────
_HIST_PERF: dict[str, float]           = {}
_HIST_PERF_ALPHA: float                = 0.15
_PRODUCT_INTENT_PERF: dict[str, float] = {}
_PROD_INTENT_ALPHA: float              = 0.20

# ── Decision log ──────────────────────────────────────────────────────────────
_DECISION_LOG: list[dict[str, Any]] = []
_MAX_LOG_SIZE: int = 10_000


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ContentCandidate:
    """
    Input item for the decision layer (v4).

    New in v4: profit_score feeds back from profit_engine EMA.
        Set profit_score = -1.0 (sentinel) to auto-read from profit_engine.
        Set profit_score ∈ [0,1] to use a caller-provided value.

    metadata dict supports hook estimation signals:
        metadata["text"]           → str: title/caption text for curiosity_signal
        metadata["duration_var"]   → float [0,1]: scene/motion variance proxy
        metadata["visual_clarity"] → float [0,1]: resolution/blur/contrast proxy
        metadata["motion_score"]   → float [0,1]: explicit motion override

    hook_potential = -1.0 (sentinel) means "auto-estimate from metadata".
    Any value in [0,1] supplied by caller is used directly.
    """
    item_id:         str
    trend_score:     float = 0.5
    product_intent:  float = 0.5
    hook_potential:  float = -1.0   # -1 = auto-estimate (v4)
    match_score:     float = 0.5
    novelty_score:   float = 0.5
    production_cost: float = 0.5
    profit_score:    float = -1.0   # -1 = auto-read from profit_engine EMA (v4)
    product_id:      str   = ""     # v5: linked product; "" = no mapping
    metadata:        dict  = field(default_factory=dict)


@dataclass
class DecisionResult:
    """Output record for one candidate decision (v3)."""
    item_id:         str
    score:           float
    decision:        str            # "keep" | "drop" | "explore"
    reason:          str
    breakdown:       dict[str, float]
    historical_perf: float
    mode:            str   = ""
    expected_value:  float = 0.0   # EV = trend × product_intent × hook_potential
    real_cost:       float = 0.0   # mode-normalised cost
    final_score:     float = 0.0   # EV - real_cost
    decision_reason: str   = ""    # "low_ev" | "threshold" | "explore" | "match_guard" | "keep"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))

def _fingerprint(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]

def _get_historical_perf(item_id: str, niche: str = "") -> float:
    return _HIST_PERF.get(_fingerprint(item_id, niche), 0.5)

def _get_product_intent_perf(item_id: str, niche: str = "") -> float:
    return _PRODUCT_INTENT_PERF.get(_fingerprint(item_id, niche), -1.0)

def _compute_real_cost(mode: str, raw_cost: float) -> float:
    """Scale raw cost by production tier. reup ×0.40 / remark ×0.70 / generate ×1.00."""
    scale = _MODE_CONFIG.get(mode, _DEFAULT_MODE_CONFIG).get("cost_scale", 1.0)
    return _clamp(raw_cost * scale)


# ── Part 1: Internal hook estimator ──────────────────────────────────────────

def _estimate_hook_potential(candidate: ContentCandidate) -> float:
    """
    Internal hook_potential estimator.

    Called when candidate.hook_potential == -1.0 (sentinel = auto-estimate).

    hook_potential = 0.4 * motion_score + 0.3 * visual_clarity + 0.3 * curiosity_signal

    Signals sourced from candidate.metadata:
        motion_score:    metadata["motion_score"] or metadata["duration_var"] fallback
        visual_clarity:  metadata["visual_clarity"] fallback 0.5
        curiosity_signal: keyword scan of metadata["text"] fallback 0.5

    All sub-signals clamped to [0, 1].
    """
    meta = candidate.metadata or {}

    # motion_score — explicit override first, then duration_var proxy, then neutral
    if "motion_score" in meta:
        motion_score = _clamp(float(meta["motion_score"]))
    elif "duration_var" in meta:
        motion_score = _clamp(float(meta["duration_var"]))
    else:
        motion_score = 0.5

    # visual_clarity — resolution/blur/contrast proxy
    visual_clarity = _clamp(float(meta.get("visual_clarity", 0.5)))

    # curiosity_signal — keyword-based scan of title/caption text
    text = str(meta.get("text", "")).lower()
    if text:
        tokens = set(re.findall(r"[a-záàảãạăắặẳẵặâấầẩẫậéèẻẽẹêếềểễệíìỉĩịóòỏõọôốồổỗộơớờởỡợúùủũụưứừửữựýỳỷỹỵđ\w]+", text))
        matched = len(tokens & _CURIOSITY_KEYWORDS)
        # Sigmoid-like: 0 hits → 0.3, 1 hit → 0.5, 2+ hits → 0.6-0.85
        if matched == 0:
            curiosity_signal = 0.30
        elif matched == 1:
            curiosity_signal = 0.50
        else:
            curiosity_signal = _clamp(0.50 + 0.10 * min(matched - 1, 4))
    else:
        curiosity_signal = 0.50

    result = (
        0.4 * motion_score
        + 0.3 * visual_clarity
        + 0.3 * curiosity_signal
    )
    return round(_clamp(result), 4)


# ── Part 2: Expected Value ────────────────────────────────────────────────────

def _compute_expected_value(signals: dict[str, float]) -> float:
    """
    EV = trend_score × product_intent × hook_potential

    All three factors must be high for EV to be high.
    This is the monetization multiplier — a great hook with no product intent = low EV.
    Result clamped to [0, 1].
    """
    ev = (
        signals["trend_score"]
        * signals["product_intent"]
        * signals["hook_potential"]
    )
    return round(_clamp(ev), 4)


# ── Core scorer (v3) ──────────────────────────────────────────────────────────

def score_content_candidate(
    item:  ContentCandidate,
    niche: str = "",
    mode:  str = "remark",
) -> tuple[float, dict[str, float]]:
    """
    Score a candidate (v4). Returns (normalised_score, breakdown).

    v4 changes:
      - profit_score (0.15 weight) from profit_engine EMA feedback loop
      - high profit → easier to pass filter; low/negative profit → harder to produce
      - hook_potential auto-estimated if not provided (sentinel = -1.0)
      - breakdown includes: expected_value, real_cost, final_score, profit_score_used
    """
    hist      = _get_historical_perf(item.item_id, niche)
    real_cost = _compute_real_cost(mode, item.production_cost)

    # product_intent: blend declared with conversion history
    pi_hist = _get_product_intent_perf(item.item_id, niche)
    if pi_hist >= 0.0:
        product_intent = _clamp(0.70 * _clamp(item.product_intent) + 0.30 * pi_hist)
    else:
        product_intent = _clamp(item.product_intent)

    # hook_potential: auto-estimate if sentinel
    if item.hook_potential < 0.0:
        hook_potential = _estimate_hook_potential(item)
    else:
        hook_potential = _clamp(item.hook_potential)

    # profit_score (v4): read from profit_engine EMA if sentinel (-1)
    if item.profit_score < 0.0:
        try:
            from core.profit_engine import get_profit_score as _gps
            profit_score = _clamp(_gps(item.item_id, niche))
        except Exception:
            profit_score = 0.5   # neutral fallback if profit_engine unavailable
    else:
        profit_score = _clamp(item.profit_score)

    signals: dict[str, float] = {
        "trend_score":     _clamp(item.trend_score),
        "product_intent":  product_intent,
        "hook_potential":  hook_potential,
        "match_score":     _clamp(item.match_score),
        "historical_perf": _clamp(hist),
        "profit_score":    profit_score,
        "novelty_score":   _clamp(item.novelty_score),
        "production_cost": real_cost,
    }

    raw = sum(_SCORE_WEIGHTS[k] * signals[k] for k in _SCORE_WEIGHTS)

    # v5: Product Intelligence boost / penalize
    # +0.05 raw if product_score > 0.65 (high-profit product)
    # -0.05 raw if product_score < 0.35 (loss product)
    product_score_used: float = 0.5
    product_delta:      float = 0.0
    try:
        from core.product_intelligence import get_score_delta, get_product_score_for_content
        product_delta      = get_score_delta(item.item_id, item.product_id)
        product_score_used = get_product_score_for_content(item.item_id)
        raw               += product_delta
    except Exception:
        pass   # product_intelligence unavailable → no adjustment

    score = _clamp((raw - _SCORE_MIN) / _SCORE_RANGE)

    ev         = _compute_expected_value(signals)
    final_score = round(ev - real_cost, 4)

    breakdown: dict[str, float] = {
        k: round(_SCORE_WEIGHTS[k] * signals[k], 4) for k in _SCORE_WEIGHTS
    }
    breakdown["historical_perf_raw"]    = round(hist,               4)
    breakdown["product_intent_raw"]     = round(item.product_intent, 4)
    breakdown["product_intent_blended"] = round(product_intent,      4)
    breakdown["hook_potential_used"]    = round(hook_potential,      4)
    breakdown["profit_score_used"]      = round(profit_score,        4)
    breakdown["product_score_used"]     = round(product_score_used,  4)
    breakdown["product_delta"]          = round(product_delta,       4)
    breakdown["real_cost"]              = round(real_cost,           4)
    breakdown["expected_value"]         = ev
    breakdown["final_score"]            = final_score

    return round(score, 4), breakdown


# ── Remark match guard ────────────────────────────────────────────────────────

def _remark_match_guard(item: ContentCandidate, cfg: dict) -> bool:
    guard_threshold = cfg.get("match_guard", 0.0)
    if guard_threshold <= 0.0:
        return True
    return item.match_score >= guard_threshold


# ── EV hard gate ──────────────────────────────────────────────────────────────

def _ev_gate(
    item:  ContentCandidate,
    mode:  str,
    niche: str,
) -> tuple[bool, str, float, float, float]:
    """
    Returns (passes, decision_reason, ev, real_cost, final_score).

    Hard rules:
      - final_score < 0         → DROP  (decision_reason = "negative_ev")
      - generate + EV < 0.1     → DROP  (decision_reason = "low_ev")
      - reup + EV < 0           → DROP  (decision_reason = "negative_ev")
    """
    _, breakdown = score_content_candidate(item, niche=niche, mode=mode)
    ev          = breakdown["expected_value"]
    real_cost   = breakdown["real_cost"]
    final_score = breakdown["final_score"]
    profit_s    = breakdown.get("profit_score_used", 0.5)
    cfg         = _MODE_CONFIG.get(mode, _DEFAULT_MODE_CONFIG)
    min_ev      = cfg.get("min_ev", 0.0)

    if final_score < 0:
        return False, "negative_ev", ev, real_cost, final_score
    if ev < min_ev:
        return False, "low_ev", ev, real_cost, final_score
    # v4 hard gate: generate mode requires minimum profit score
    if mode == "generate" and profit_s < _PROFIT_HARD_GATE_GENERATE:
        return False, "low_profit_block", ev, real_cost, final_score
    return True, "pass", ev, real_cost, final_score


# ── Part 3: Worker-level gate ─────────────────────────────────────────────────

def should_produce(candidate: ContentCandidate, mode: str, niche: str = "") -> tuple[bool, str]:
    """
    Public worker-level gate. Call this BEFORE any expensive operation:
        media_generator.render() / generate_content() / any AI API call

    Returns (allowed: bool, reason: str).

    Usage:
        allowed, reason = should_produce(candidate, mode="generate")
        if not allowed:
            return {"ok": False, "skipped": True, "reason": reason}
    """
    cfg = _MODE_CONFIG.get(mode, _DEFAULT_MODE_CONFIG)

    # v5: Product kill-switch — block immediately if product is loss-making past N attempts
    try:
        from core.product_intelligence import is_content_product_killed
        if is_content_product_killed(candidate.item_id) or (
            candidate.product_id and __import__("core.product_intelligence",
                fromlist=["is_product_killed"]).is_product_killed(candidate.product_id)
        ):
            reason = f"product_killed: product={candidate.product_id or 'mapped'} blocked"
            LOGGER.debug("should_produce BLOCKED item=%s %s", candidate.item_id, reason)
            return False, reason
    except Exception:
        pass   # product_intelligence unavailable → do not block

    # Match guard (remark only)
    if not _remark_match_guard(candidate, cfg):
        reason = (
            f"match_guard_drop: match={candidate.match_score:.3f} "
            f"< {cfg.get('match_guard', 0.6):.2f} for {mode}"
        )
        LOGGER.debug("should_produce BLOCKED item=%s %s", candidate.item_id, reason)
        return False, reason

    # EV gate
    passes, decision_reason, ev, real_cost, final_score = _ev_gate(candidate, mode, niche)
    if not passes:
        reason = (
            f"{decision_reason}: EV={ev:.3f} cost={real_cost:.3f} "
            f"final={final_score:.3f} mode={mode}"
        )
        LOGGER.debug("should_produce BLOCKED item=%s %s", candidate.item_id, reason)
        return False, reason

    # Score threshold
    score, _ = score_content_candidate(candidate, niche=niche, mode=mode)
    threshold = cfg["threshold"]
    if score < threshold:
        reason = f"score_below_threshold: score={score:.3f} < {threshold} mode={mode}"
        LOGGER.debug("should_produce BLOCKED item=%s %s", candidate.item_id, reason)
        return False, reason

    return True, f"pass: score={score:.3f} EV={ev:.3f} final={final_score:.3f}"


# ── Batch filter ──────────────────────────────────────────────────────────────

def filter_candidates(
    items:   list[ContentCandidate],
    mode:    str  = "remark",
    niche:   str  = "",
    explore: bool = True,
    seed:    int  = 0,
) -> tuple[list[DecisionResult], list[DecisionResult]]:
    """
    Score + filter a batch of candidates (v3).

    Gate order:
        1. Remark match guard (instant drop)
        2. EV gate: final_score < 0 or EV < min_ev → drop
        3. Score + threshold + keep_ratio
        4. Exploration bucket (10% of dropped)
    """
    cfg         = _MODE_CONFIG.get(mode, _DEFAULT_MODE_CONFIG)
    threshold   = cfg["threshold"]
    keep_ratio  = cfg["keep_ratio"]
    explore_pct = cfg["explore_pct"]

    if not items:
        return [], []

    # ── Step 1: Match guard ───────────────────────────────────────────────────
    guard_dropped: list[DecisionResult] = []
    after_guard:   list[ContentCandidate] = []

    for item in items:
        if not _remark_match_guard(item, cfg):
            bd: dict[str, float] = {
                "trend_score": 0.0, "product_intent": 0.0, "hook_potential": 0.0,
                "match_score": round(item.match_score, 4),
                "historical_perf": 0.0, "novelty_score": 0.0, "production_cost": 0.0,
                "expected_value": 0.0, "real_cost": 0.0, "final_score": 0.0,
            }
            guard_dropped.append(DecisionResult(
                item_id         = item.item_id,
                score           = 0.0,
                decision        = "drop",
                reason          = (
                    f"match_guard_drop: match={item.match_score:.3f} "
                    f"< required {cfg.get('match_guard', 0.6):.2f} for {mode}"
                ),
                breakdown       = bd,
                historical_perf = _get_historical_perf(item.item_id, niche),
                mode            = mode,
                expected_value  = 0.0,
                real_cost       = 0.0,
                final_score     = 0.0,
                decision_reason = "match_guard",
            ))
        else:
            after_guard.append(item)

    # ── Step 2: EV gate + scoring ────────────────────────────────────────────
    ev_dropped: list[DecisionResult] = []
    scored:     list[tuple[float, dict, ContentCandidate]] = []

    for item in after_guard:
        score, breakdown = score_content_candidate(item, niche=niche, mode=mode)
        ev          = breakdown["expected_value"]
        real_cost   = breakdown["real_cost"]
        final_score = breakdown["final_score"]
        min_ev      = cfg.get("min_ev", 0.0)

        if final_score < 0:
            ev_dropped.append(_make_result(
                item, score, breakdown, "drop", niche, mode,
                decision_reason="negative_ev",
            ))
        elif ev < min_ev:
            ev_dropped.append(_make_result(
                item, score, breakdown, "drop", niche, mode,
                decision_reason="low_ev",
            ))
        else:
            scored.append((score, breakdown, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    n = len(scored)

    if n == 0:
        all_dropped = guard_dropped + ev_dropped
        _log_decisions(all_dropped, mode=mode)
        return [], all_dropped

    # ── Step 3: Threshold + keep_ratio ───────────────────────────────────────
    keep_n   = max(1, math.ceil(n * keep_ratio))
    pass_idx = next((i for i, (s, _, _) in enumerate(scored) if s < threshold), n)
    keep_n   = min(max(keep_n, pass_idx), math.ceil(n * (keep_ratio + 0.20)))
    keep_n   = min(keep_n, n)

    kept_raw    = scored[:keep_n]
    dropped_raw = scored[keep_n:]

    # ── Step 4: Exploration bucket ────────────────────────────────────────────
    explore_results: list[DecisionResult] = []
    if explore and dropped_raw:
        n_explore  = max(1, math.ceil(len(dropped_raw) * explore_pct))
        rng        = random.Random(seed)
        exp_sample = rng.sample(dropped_raw, min(n_explore, len(dropped_raw)))
        explore_results = [
            _make_result(item, score, breakdown, "explore", niche, mode)
            for score, breakdown, item in exp_sample
        ]
        exp_ids     = {r.item_id for r in explore_results}
        dropped_raw = [x for x in dropped_raw if x[2].item_id not in exp_ids]

    kept_results = [
        _make_result(item, s, bd, "keep", niche, mode)
        for s, bd, item in kept_raw
    ]
    kept_results.extend(explore_results)

    dropped_scored = [
        _make_result(item, s, bd, "drop", niche, mode)
        for s, bd, item in dropped_raw
    ]

    all_dropped = guard_dropped + ev_dropped + dropped_scored
    _log_decisions(kept_results + all_dropped, mode=mode)

    LOGGER.info(
        "content_decision mode=%s niche=%s total=%d guard=%d ev_drop=%d "
        "scored=%d kept=%d explore=%d dropped=%d",
        mode, niche, len(items),
        len(guard_dropped), len(ev_dropped), n, len(kept_raw),
        len(explore_results), len(dropped_scored),
    )
    return kept_results, all_dropped


def _make_result(
    item:            ContentCandidate,
    score:           float,
    breakdown:       dict[str, float],
    decision:        str,
    niche:           str,
    mode:            str,
    decision_reason: str = "",
) -> DecisionResult:
    """Build a DecisionResult with structured reason and EV fields."""
    hist        = _get_historical_perf(item.item_id, niche)
    ev          = breakdown.get("expected_value", 0.0)
    real_cost   = breakdown.get("real_cost", 0.0)
    final_score = breakdown.get("final_score", 0.0)

    positive_keys = [k for k in _SCORE_WEIGHTS if _SCORE_WEIGHTS[k] > 0]
    top_signal    = max(positive_keys, key=lambda k: breakdown.get(k, 0.0))

    if not decision_reason:
        if decision == "keep":
            decision_reason = "keep"
        elif decision == "explore":
            decision_reason = "explore"
        else:
            cfg = _MODE_CONFIG.get(mode, _DEFAULT_MODE_CONFIG)
            if mode == "generate" and ev < cfg.get("min_ev", 0.1):
                decision_reason = "low_ev"
            elif final_score < 0:
                decision_reason = "negative_ev"
            else:
                decision_reason = "threshold"

    if decision == "keep":
        reason = (
            f"score={score:.3f} EV={ev:.3f} final={final_score:.3f} "
            f"— led by {top_signal}={breakdown.get(top_signal, 0):.3f}"
        )
    elif decision == "explore":
        reason = f"explore: score={score:.3f} EV={ev:.3f} — diversity pick"
    else:
        reason = (
            f"{decision_reason}: score={score:.3f} EV={ev:.3f} "
            f"final={final_score:.3f} cost={real_cost:.3f}"
        )

    return DecisionResult(
        item_id         = item.item_id,
        score           = score,
        decision        = decision,
        reason          = reason,
        breakdown       = breakdown,
        historical_perf = round(hist, 4),
        mode            = mode,
        expected_value  = ev,
        real_cost       = real_cost,
        final_score     = final_score,
        decision_reason = decision_reason,
    )


# ── Part 4: Decision log (v3) ────────────────────────────────────────────────

def _log_decisions(results: list[DecisionResult], mode: str) -> None:
    """Append decisions to in-memory log with full v3 fields."""
    for r in results:
        _DECISION_LOG.append({
            "id":              r.item_id,
            "item_id":         r.item_id,
            "mode":            r.mode or mode,
            "score":           r.score,
            "decision":        r.decision,
            "decision_reason": r.decision_reason,
            "reason":          r.reason,
            "breakdown":       r.breakdown,
            "hist_perf":       r.historical_perf,
            # v3 profit fields
            "expected_value":  r.expected_value,
            "real_cost":       r.real_cost,
            "final_score":     r.final_score,
            # v2 compat
            "product_intent_blended": r.breakdown.get("product_intent_blended", 0.0),
            "hook_potential":         r.breakdown.get("hook_potential_used",    0.0),
        })
    if len(_DECISION_LOG) > _MAX_LOG_SIZE:
        del _DECISION_LOG[: len(_DECISION_LOG) - _MAX_LOG_SIZE]


def get_decision_log(last_n: int = 100) -> list[dict[str, Any]]:
    return _DECISION_LOG[-last_n:]


# ── Feedback loop ─────────────────────────────────────────────────────────────

def record_outcome(
    item_id:          str,
    niche:            str,
    engagement:       float,
    viral:            bool  = False,
    decay_bad:        bool  = True,
    conversion_score: float = 0.0,
) -> None:
    """Update historical_perf and product_intent EMA after TRACK stage."""
    key     = _fingerprint(item_id, niche)
    current = _HIST_PERF.get(key, 0.5)
    value   = _clamp(engagement)

    if viral:
        value = _clamp(value * 1.20)
        alpha = min(0.40, _HIST_PERF_ALPHA * 3)
    elif decay_bad and value < 0.3:
        alpha = _HIST_PERF_ALPHA * 1.5
    else:
        alpha = _HIST_PERF_ALPHA

    updated = round(alpha * value + (1.0 - alpha) * current, 6)
    _HIST_PERF[key] = updated
    LOGGER.debug(
        "content_decision_feedback item=%s niche=%s eng=%.3f viral=%s "
        "hist_perf: %.4f -> %.4f",
        item_id, niche, engagement, viral, current, updated,
    )

    if conversion_score > 0.0:
        conv_val   = _clamp(conversion_score)
        pi_current = _PRODUCT_INTENT_PERF.get(key, 0.5)
        pi_alpha   = _PROD_INTENT_ALPHA
        if conv_val >= 0.7:
            pi_alpha = min(0.40, pi_alpha * 2)
        _PRODUCT_INTENT_PERF[key] = round(
            pi_alpha * conv_val + (1.0 - pi_alpha) * pi_current, 6
        )


def reset_decision_state() -> None:
    """Reset all in-process state. For testing."""
    _HIST_PERF.clear()
    _PRODUCT_INTENT_PERF.clear()
    _DECISION_LOG.clear()
