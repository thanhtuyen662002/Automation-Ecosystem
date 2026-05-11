"""
execution/execution_brain.py — Execution Brain (Final Decision Maker).

Aggregates signals from all intelligence layers and makes the final
publish / reject / delay decision before any post goes live.

Final score:
    final_score =
        0.25 * trend_score
      + 0.20 * hook_score
      + 0.15 * lifecycle_score
      + 0.15 * account_health
      + 0.15 * expected_conversion
      + 0.10 * novelty_score

Expected value:
    expected_value = (est_views * ctr * conversion_rate * avg_order_value) - cost

Hard REJECT rules:
    - expected_value < 0
    - lifecycle == DEAD
    - duplicate already posted to this account on this platform
    - final_score < threshold (mode-specific) unless in exploration quota

Exploration: 10% of decisions bypass score threshold (random draw).

Public API:
    decide(candidate, accounts, platform, niche, mode) → ExecutionDecision
    batch_decide(candidates, accounts, platform, niche) → list[ExecutionDecision]
    get_brain_stats()                                   → dict

Output dataclass:
    ExecutionDecision.decision: "publish" | "reject" | "delay"
    ExecutionDecision.reason: str
    ExecutionDecision.final_score: float
    ExecutionDecision.expected_value: float
    ExecutionDecision.selected_account: str
    ExecutionDecision.selected_time: datetime | None
    ExecutionDecision.content_mode: str
    ExecutionDecision.signals: dict     ← full breakdown
"""
from __future__ import annotations

import logging
import math
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

LOGGER = logging.getLogger("execution.execution_brain")

# ── Weights (v2 — profit-first rebalance) ────────────────────────────────────
W_TREND      = 0.30
W_HOOK       = 0.25
W_LIFECYCLE  = 0.10
W_ACCOUNT    = 0.10
W_CONVERSION = 0.15
W_NOVELTY    = 0.10

# ── Thresholds per mode (calibrated for confidence-weighted scores) ───────────
# raw_score * min_confidence(0.50) must exceed threshold for strong content to pass
# reup:  0.55 * 0.50 = 0.275 → threshold 0.26
# remark: 0.60 * 0.50 = 0.30 → threshold 0.28
# generate: stays high (expensive — require real data)
_THRESHOLDS: dict[str, float] = {
    "reup":     0.26,
    "remark":   0.28,
    "generate": 0.42,
}
_EXPLORE_RATE     = float(os.environ.get("BRAIN_EXPLORE_RATE",    "0.10"))
_MIN_EV           = float(os.environ.get("BRAIN_MIN_EV",          "-5.0"))   # allow small negative EV
_MIN_COST_FOR_EV  = float(os.environ.get("BRAIN_MIN_COST_FOR_EV", "1.0"))    # only enforce EV when cost > $1
_DEFAULT_AOV      = float(os.environ.get("BRAIN_DEFAULT_AOV",     "25.0"))   # avg order value $
_DEFAULT_CTR      = float(os.environ.get("BRAIN_DEFAULT_CTR",     "0.025"))
_DEFAULT_CVR      = float(os.environ.get("BRAIN_DEFAULT_CVR",     "0.015"))
_DEFAULT_EST_VIEWS= float(os.environ.get("BRAIN_DEFAULT_VIEWS",   "5000"))

# ── Lifecycle state → score map ───────────────────────────────────────────────
_LIFECYCLE_SCORES: dict[str, float] = {
    "testing":   0.40,
    "winning":   0.85,
    "scaling":   0.95,
    "saturated": 0.30,
    "recycle":   0.55,
    "dead":      0.00,
    "unknown":   0.40,
}

# ── Risk hardening constants ──────────────────────────────────────────────────
_DIVERSITY_PRESSURE    = 0.30    # Part 1.1: echo-chamber guard
_LT_SCORE_WEIGHT       = 0.30    # Part 1.2: long-term score blend
_RISK_STACK_PENALTY    = 0.15    # Part 1.3: correlated-risk surcharge
_FATIGUE_MAX           = 0.50    # Part 1.4: max fatigue penalty
_FATIGUE_SCALE         = 10.0   # 10 uses → full fatigue
_MAX_POSTS_PER_DAY: dict[str, int] = {"tiktok": 5, "facebook": 4, "instagram": 3}
_MAX_HOOK_REUSE        = 3       # Part 7: same hook on same account/day

# ── Validation / anomaly constants (Parts 1 + 4) ─────────────────────────────
_VALIDATION_THRESHOLD  = 0.55    # Part 1: minimum validation score for cross-platform routing
_VOLATILITY_PENALTY    = 0.15    # Part 1: view-velocity spike → reduce final_score by 15%
_VOLATILITY_SPIKE_MUL  = 3.0     # Part 4: >3x avg AND low engagement = anomaly
_ANOMALY_PRIORITY_CUT  = 0.20    # Part 4: anomaly → reduce priority_score by 20%
_COST_EV_RATIO_MAX     = 0.80    # Part 4: reject if cost > ev * 0.80

# ── In-process pattern-usage tracker ─────────────────────────────────────────────────────
# {account_id: {pattern_id: usage_count}}
# Persists for process lifetime; reset on restart (acceptable for intraday)
_pattern_usage: dict[str, dict[str, int]] = {}

# Part 1 (ANTI-LOCK): per-niche amplification share tracker
# {niche: (amplified_count, total_count)}
_niche_amp_counts: dict[str, list[int]] = {}   # [amplified, total]

# Part 3 & 4: per-niche angle score ledger for competition + dominance
# {niche: {angle_id: priority_score}}
_niche_angle_scores: dict[str, dict[str, float]] = {}

# Part 5/6: pre-trend score threshold (mirrors angle_engine constant)
_PRE_TREND_THRESHOLD = 0.55

# Part 9: pattern-boost budget — cap at 30% of decisions per process cycle
# [pattern_boosted_count, total_decide_count]
_pattern_boost_budget: list[int] = [0, 0]


def _pattern_id(niche: str, mode: str, hook: str) -> str:
    return f"{niche}:{mode}:{hook[:24]}"


def _increment_pattern(account_id: str, pattern: str) -> None:
    bucket = _pattern_usage.setdefault(account_id, {})
    bucket[pattern] = bucket.get(pattern, 0) + 1


# ── Platform role constants (Part 3) ───────────────────────────────────────────────
# tiktok=discovery/testing, reels=scaling, shorts=harvesting
_PLATFORM_MULTIPLIER: dict[str, float] = {
    "tiktok":    1.00,
    "reels":     1.10,  # scaling platform — boost final_score
    "shorts":    0.90,  # harvesting — prioritise EV over score
    "facebook":  1.00,
    "instagram": 1.00,
}
_PLATFORM_EXPLORE_FACTOR: dict[str, float] = {
    "tiktok": 1.20,  # more exploration on discovery platform
    "reels":  1.00,
    "shorts": 0.85,
}
_PLATFORM_THRESHOLD_FACTOR: dict[str, float] = {
    "reels":  0.95,  # slightly easier gate for scaling
    "shorts": 1.05,  # slightly harder — only proven content
}

# Cross-platform pattern usage {pattern_id: {platform: count}}
_cross_platform_usage: dict[str, dict[str, int]] = {}

# Per-content cross-platform performance {content_id: {platform: score}}
# Used for consistency scoring and kill-switch logic
_xp_performance: dict[str, dict[str, float]] = {}

# Cross-platform kill switch: content_ids blocked from further routing
_xp_killed: set[str] = set()

# Platform role → tier order (used for source_weight decay)
_PLATFORM_TIER: dict[str, int] = {
    "tiktok":    0,   # origin / discovery
    "reels":     1,   # secondary / scaling
    "shorts":    2,   # tertiary / harvest
    "facebook":  1,
    "instagram": 1,
}
_PLATFORM_ROLE: dict[str, str] = {
    "tiktok":    "discovery",
    "reels":     "scaling",
    "shorts":    "harvest",
    "facebook":  "scaling",
    "instagram": "scaling",
}
# Source weight by tier: 0→1.0, 1→0.6, 2→0.3
_SOURCE_WEIGHTS: list[float] = [1.0, 0.6, 0.3]

# Content state machine states
_CONTENT_STATES: list[str] = ["test", "validated", "scaled", "saturated"]
_content_state: dict[str, str] = {}    # content_id → state


def _get_content_state(content_id: str) -> str:
    return _content_state.get(content_id, "test")


def _update_content_state(content_id: str, score: float, fatigue: float) -> str:
    """Advance state machine based on score and fatigue signals."""
    cur = _get_content_state(content_id)
    xp_scores = list(_xp_performance.get(content_id, {}).values())
    multi_platform_success = len([s for s in xp_scores if s > 0.60]) >= 2

    if cur == "test" and score > 0.65:
        nxt = "validated"
    elif cur == "validated" and multi_platform_success:
        nxt = "scaled"
    elif cur == "scaled" and fatigue > 0.4:
        nxt = "saturated"
    else:
        nxt = cur

    _content_state[content_id] = nxt
    return nxt


def _cross_platform_consistency(content_id: str) -> float:
    """Part 2.4: 1 - variance(scores across platforms). Returns [0,1]."""
    scores = list(_xp_performance.get(content_id, {}).values())
    if len(scores) < 2:
        return 0.5   # neutral when insufficient data
    mean = sum(scores) / len(scores)
    variance = sum((s - mean) ** 2 for s in scores) / len(scores)
    return round(max(0.0, min(1.0, 1.0 - variance)), 4)


def _xp_failed_count(content_id: str) -> int:
    """Count platforms where content scored < 0.35 (failure threshold)."""
    return sum(1 for s in _xp_performance.get(content_id, {}).values() if s < 0.35)


def _cross_platform_fatigue(pattern: str, max_platforms: int = 3) -> float:
    """Part 3.7: penalty when same pattern used across >max_platforms platforms."""
    usage = _cross_platform_usage.get(pattern, {})
    extra = max(0, len(usage) - max_platforms)
    return min(0.50, extra * 0.20)


def _record_cross_platform(pattern: str, plat: str) -> None:
    bucket = _cross_platform_usage.setdefault(pattern, {})
    bucket[plat] = bucket.get(plat, 0) + 1


def _get_fatigue(account_id: str, pattern: str) -> float:
    count = _pattern_usage.get(account_id, {}).get(pattern, 0)
    return min(_FATIGUE_MAX, count / _FATIGUE_SCALE)


# ── Output type ───────────────────────────────────────────────────────────────

@dataclass
class ExecutionDecision:
    decision:         str              # "publish" | "reject" | "delay"
    reason:           str
    final_score:      float
    expected_value:   float
    selected_account: str
    content_mode:     str             # "reup" | "remark" | "generate"
    selected_time:    datetime | None = None
    content_id:       str             = ""
    platform:         str             = ""
    niche:            str             = ""
    is_exploration:   bool            = False
    signals:          dict[str, Any]  = field(default_factory=dict)
    meta:             dict[str, Any]  = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "decision":         self.decision,
            "reason":           self.reason,
            "final_score":      round(self.final_score, 4),
            "expected_value":   round(self.expected_value, 4),
            "selected_account": self.selected_account,
            "selected_time":    self.selected_time.isoformat() if self.selected_time else None,
            "content_mode":     self.content_mode,
            "content_id":       self.content_id,
            "platform":         self.platform,
            "niche":            self.niche,
            "is_exploration":   self.is_exploration,
            "signals":          self.signals,
        }
        return d


# ── Signal collectors (graceful degradation) ──────────────────────────────────

def _get_trend_score(candidate: dict[str, Any]) -> float:
    if "trend_score" in candidate:
        return float(candidate["trend_score"])
    try:
        from execution.trend_filter import score_candidate
        ts = score_candidate(candidate)
        return ts.viral_score
    except Exception:
        return 0.4


def _get_hook_score(candidate: dict[str, Any], niche: str, platform: str) -> tuple[float, str]:
    if "hook_score" in candidate:
        return float(candidate["hook_score"]), candidate.get("best_hook", "")
    try:
        from execution.hook_optimizer import optimize_hook
        r = optimize_hook(candidate, niche=niche, platform=platform)
        return r.best_score, r.best_hook
    except Exception:
        return 0.4, candidate.get("caption", "")[:80]


def _get_lifecycle_score(content_id: str, niche: str, platform: str) -> tuple[float, str]:
    try:
        from execution.content_lifecycle import get_lifecycle_report
        r = get_lifecycle_report(content_id)
        state = r.get("state", "unknown")
    except Exception:
        state = "unknown"
    return _LIFECYCLE_SCORES.get(state, 0.40), state


def _get_account_health(account: dict[str, Any], platform: str, niche: str) -> float:
    hs = float(account.get("health_score", 0.7))
    try:
        from core.page_intelligence import get_page_score
        pid = str(account.get("page_id") or account.get("account_id") or "")
        ps  = get_page_score(pid)
        return hs * 0.4 + ps * 0.6
    except Exception:
        return hs


def _get_conversion_score(platform: str, niche: str) -> tuple[float, float, float, int, int]:
    """Returns (conversion_score, ctr, cvr, ctr_samples, cvr_samples).

    FIX 1: CTR/CVR always clamped to default minimum so cold-start never
    collapses EV to zero.  Real data overrides when sample_count > 0.
    """
    ctr_samples = cvr_samples = 0
    ctr, cvr = _DEFAULT_CTR, _DEFAULT_CVR
    try:
        from execution.conversion_optimizer import get_funnel_report
        r    = get_funnel_report(platform, niche)
        _ctr = float(r.get("overall_ctr", 0.0))
        _ctr = max(_ctr, _DEFAULT_CTR)          # FIX 1: never below default
        ctr  = _ctr
        if r.get("top_ctas"):
            top  = r["top_ctas"][0]
            _cvr = float(top.get("cvr_ema", 0.0))
            _cvr = max(_cvr, _DEFAULT_CVR)       # FIX 1: never below default
            cvr  = _cvr
            ctr_samples = int(top.get("sample_count", 0))
            cvr_samples = ctr_samples
    except Exception:
        pass
    score = min(1.0, ctr / 0.10) * 0.6 + min(1.0, cvr / 0.05) * 0.4
    return round(score, 4), ctr, cvr, ctr_samples, cvr_samples


def _get_novelty_score(candidate: dict[str, Any]) -> float:
    if "novelty_score" in candidate:
        return float(candidate["novelty_score"])
    try:
        from execution.content_memory import has_been_posted
        url = candidate.get("source_url", "")
        aid = candidate.get("account_id", "")
        plat = candidate.get("platform", "tiktok")
        if url and has_been_posted(url, aid, plat):
            return 0.0
    except Exception:
        pass
    return 0.60   # default: novel if unknown


def _is_duplicate(
    candidate: dict[str, Any],
    account_id: str,
    platform:   str,
) -> bool:
    try:
        from execution.content_memory import has_been_posted
        url = candidate.get("source_url", "")
        if url:
            return has_been_posted(url, account_id, platform)
    except Exception:
        pass
    return False


# ── Risk control layer (Part 3) ───────────────────────────────────────────────

_MAX_POSTS_PER_DAY: dict[str, int] = {
    "tiktok": 5, "facebook": 4, "instagram": 3
}
_HOOK_SPAM_WINDOW    = int(os.environ.get("BRAIN_HOOK_SPAM_N",        "3"))
_NICHE_SIM_THRESHOLD = float(os.environ.get("BRAIN_NICHE_SIM_MIN",   "0.60"))

# Niche similarity map — dot-product of shared keywords
_NICHE_KEYWORDS: dict[str, set[str]] = {
    "tech":          {"tech", "coding", "software", "ai", "gadget", "programming"},
    "fitness":       {"fitness", "gym", "workout", "health", "exercise", "training"},
    "finance":       {"money", "investing", "crypto", "finance", "wealth", "income"},
    "entertainment": {"funny", "comedy", "meme", "viral", "dance", "music"},
    "food":          {"food", "recipe", "cooking", "restaurant", "eat", "meal"},
    "travel":        {"travel", "trip", "vacation", "explore", "adventure", "hotel"},
}


def _niche_similarity(content_niche: str, account_niche: str) -> float:
    """Jaccard similarity between niche keyword sets. Returns 1.0 if niches match."""
    if not content_niche or not account_niche:
        return 1.0   # cannot determine → allow
    if content_niche == account_niche:
        return 1.0
    a = _NICHE_KEYWORDS.get(content_niche, {content_niche})
    b = _NICHE_KEYWORDS.get(account_niche, {account_niche})
    if not a or not b:
        return 0.5
    inter = len(a & b)
    union = len(a | b)
    return round(inter / max(1, union), 3)


def _recent_hooks(account_id: str, platform: str, n: int = 3) -> list[str]:
    """Return last N hooks posted on this account. Best-effort."""
    try:
        from execution.content_memory import get_post_history
        posts = get_post_history(account_id=account_id, limit=n)
        return [p.get("hook", "") for p in posts if p.get("hook")]
    except Exception:
        return []


def risk_control(
    candidate:  dict[str, Any],
    account:    dict[str, Any],
    platform:   str,
    niche:      str,
    hook:       str = "",
) -> list[str]:
    """
    Run all risk guards. Returns list of risk flag strings.
    Empty list = no risk detected.

    Guards:
        overposting      — posts_today >= platform daily limit
        hook_spam        — same hook repeated in last N posts
        niche_drift      — content niche too far from account niche
        duplicate        — already posted this URL to this account
    """
    flags: list[str] = []
    account_id    = account.get("account_id", "")
    posts_today   = int(account.get("posts_today", 0))
    account_niche = account.get("niche", niche)
    limit         = _MAX_POSTS_PER_DAY.get(platform, 5)

    # 1. Overposting
    if posts_today >= limit:
        flags.append(f"overposting:{posts_today}>={limit}")

    # 2. Hook spam
    if hook:
        recent = _recent_hooks(account_id, platform, n=_HOOK_SPAM_WINDOW)
        hook_norm = hook.strip().lower()[:60]
        spam_count = sum(1 for h in recent if h.strip().lower()[:60] == hook_norm)
        if spam_count >= _HOOK_SPAM_WINDOW:
            flags.append(f"hook_spam:same_hook_last_{_HOOK_SPAM_WINDOW}_posts")

    # 3. Niche drift
    sim = _niche_similarity(niche, account_niche)
    if sim < _NICHE_SIM_THRESHOLD:
        flags.append(f"niche_drift:sim={sim:.2f}<{_NICHE_SIM_THRESHOLD}")

    # 4. Duplicate
    if _is_duplicate(candidate, account_id, platform):
        flags.append("duplicate")

    return flags


# ── Confidence scoring (Part 4) ───────────────────────────────────────────────

def _compute_confidence(
    ctr_samples:  int,
    cvr_samples:  int,
    hook_samples: int,
    trend_age_h:  float = 24.0,
) -> float:
    """Confidence = how much we trust our signals (uncertainty proxy).

    FIX 2:
    - Cold-start (0 samples total) → 0.65 optimistic prior, not minimum clamp.
    - Range: [0.50, 1.0].  0.65 = cold start.  1.0 = fully validated.
    - Confidence is uncertainty, NOT punishment.
    """
    total_samples = ctr_samples + cvr_samples + hook_samples

    # Cold-start: return optimistic prior immediately
    if total_samples == 0:
        return 0.65

    data_size   = min(1.0, total_samples / 60)
    consistency = min(1.0, (
        (1.0 if ctr_samples  > 0 else 0.0) +
        (1.0 if cvr_samples  > 0 else 0.0) +
        (1.0 if hook_samples > 0 else 0.0)
    ) / 3)
    recency = max(0.0, 1.0 - trend_age_h / 168)   # decays over 7 days

    raw = 0.4 * data_size + 0.3 * consistency + 0.3 * recency
    return round(max(0.50, min(1.0, raw)), 4)


# ── Confidence-weighted EV (Part 1) ──────────────────────────────────────────

def _compute_expected_value(
    est_views:   float,
    ctr:         float,
    cvr:         float,
    aov:         float,
    cost:        float,
    ctr_samples: int = 0,
    cvr_samples: int = 0,
) -> tuple[float, float, float]:
    """
    Returns (ev, effective_ctr, effective_cvr).

    Shrinks CTR/CVR toward zero when sample count is low (<20),
    preventing overconfident revenue projections on fresh accounts.
    """
    conf_ctr = min(1.0, ctr_samples / 20) if ctr_samples > 0 else 0.5
    conf_cvr = min(1.0, cvr_samples / 20) if cvr_samples > 0 else 0.5
    effective_ctr = round(ctr * conf_ctr, 6)
    effective_cvr = round(cvr * conf_cvr, 6)
    ev = round(est_views * effective_ctr * effective_cvr * aov - cost, 4)
    return ev, effective_ctr, effective_cvr



def _select_account(
    accounts:   list[dict[str, Any]],
    platform:   str,
    niche:      str,
    content_id: str,
    candidate:  dict[str, Any] | None = None,
    rng_local:  random.Random | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Pick best healthy account (warmup-complete, under daily limit).

    FIX 4: Returns (account | None, rejection_reason) so callers can
    distinguish 'no accounts at all' from 'all accounts overposted'.
    Part 2: 70/30 traffic split for amp_score > 0.75 (safe fast scale).
    """
    import math
    limit = {"tiktok": 5, "facebook": 4, "instagram": 3}.get(platform, 5)

    if not accounts:
        return None, "no_accounts_provided"
    
    # Filter by capacity
    eligible = [a for a in accounts if int(a.get("posts_today", 0)) < limit]
    if not eligible:
        return None, "all_accounts_overposted"

    amp_score = float(candidate.get("angle_amplification_score", 0.0)) if candidate else 0.0

    # Part 1 (ANTI-LOCK): compute per-niche amplified_content_share
    _niche_counts = _niche_amp_counts.setdefault(niche, [0, 0])
    _niche_counts[1] += 1   # total
    if amp_score > 0.6:
        _niche_counts[0] += 1  # amplified
    amplified_content_share = _niche_counts[0] / max(1, _niche_counts[1])

    # Part 1 (ANTI-LOCK): if > 60% of niche content is amplified, apply cap
    _amp_effect_multiplier = 1.0
    if amplified_content_share > 0.6:
        _amp_effect_multiplier = 0.7

    # Part 2 (SAFE FAST SCALE): split traffic instead of skipping testing
    if amp_score > 0.75:
        # Separate scaling vs testing accounts
        scaling_eligible = sorted(eligible, key=lambda a: float(a.get("health_score", 0.5)), reverse=True)
        testing_eligible = sorted(eligible, key=lambda a: float(a.get("health_score", 0.5)))
        # 70% → scaling, 30% → testing (round-trip approach: pick from appropriate pool)
        _pop = rng_local.random() if hasattr(rng_local, 'random') else random.random()
        if _pop < 0.70 and scaling_eligible:
            return scaling_eligible[0], ""
        elif testing_eligible:
            return testing_eligible[0], ""


    # Part 3: Micro-Testing — route unvalidated angles to low-risk accounts
    if candidate and candidate.get("angle_id") and not candidate.get("is_validated_angle"):
        # Select low-risk accounts (e.g., lower health score / disposable)
        test_eligible = sorted(eligible, key=lambda a: float(a.get("health_score", 0.5)))
        if test_eligible:
            return test_eligible[0], ""

    # Try portfolio ranker (may filter by warmup stage)
    try:
        from execution.portfolio_allocator import rank_accounts
        ranked = rank_accounts(eligible, niche, platform)
        for ra in ranked:
            acct = next((a for a in eligible if a.get("account_id") == ra.account_id), None)
            if acct:
                return acct, ""
    except Exception:
        pass

    # Fallback: highest health_score
    return max(eligible, key=lambda a: float(a.get("health_score", 0.5))), ""


def _select_time(platform: str, niche: str) -> datetime:
    try:
        from execution.smart_scheduler import get_next_post_time
        return get_next_post_time(platform, niche)
    except Exception:
        return datetime.now(timezone.utc)


def _get_cross_layer_hints(platform: str, niche: str) -> dict[str, Any]:
    try:
        from execution.cross_layer_learner import get_best_combo_for
        combo = get_best_combo_for(platform, niche)
        if combo:
            return combo.as_scheduling_hints()
    except Exception:
        pass
    return {}


# ── Core decision ─────────────────────────────────────────────────────────────

def decide(
    candidate:    dict[str, Any],
    accounts:     list[dict[str, Any]],
    platform:     str,
    niche:        str,
    mode:         str  = "reup",
    cost:         float = 0.0,
    aov:          float = 0.0,
    seed:         int | None = None,
    mutation_groups: dict | None = None,
) -> ExecutionDecision:
    """
    Final gate: aggregate all signals and decide publish / reject / delay.

    candidate   : content candidate dict
    accounts    : list of available account dicts
    platform    : "tiktok" | "facebook"
    niche       : content niche
    mode        : "reup" | "remark" | "generate"
    cost        : estimated production cost ($)
    aov         : average order value ($ per conversion); falls back to env default

    Returns ExecutionDecision — never raises.
    """
    t0    = time.monotonic()
    rng   = random.Random(seed if seed is not None else int(time.time() * 1000) % 2**31)

    content_id = candidate.get("content_id", "")
    _aov       = aov or _DEFAULT_AOV
    _cost      = cost or float(candidate.get("production_cost", 0.0))

    signals: dict[str, Any] = {}

    # ── 0. CEO Strategy Directive ────────────────────────────────────────────
    # Consumes strategy layer; fails silently with neutral defaults.
    try:
        from strategy.ceo_brain import get_strategy
        _directive = get_strategy()
    except Exception:
        from types import SimpleNamespace
        _directive = SimpleNamespace(
            threshold_modifier=1.0, exploration_rate=_EXPLORE_RATE,
            niche_budget={}, account_overrides={}, niche_overrides={},
            growth_mode="balanced",
        )
    signals["strategy_mode"] = _directive.growth_mode

    # Part 4: Integrate Content Alpha Engine (Input Layer Replacement)
    try:
        from core.angle_engine import enrich_candidate
        candidate = enrich_candidate(candidate, niche)
        signals["angle_id"] = candidate.get("angle_id", "")
        signals["is_validated_angle"] = candidate.get("is_validated_angle", False)
    except Exception:
        pass

    # Part 1: Capital-aware behavior ("own niches" model)
    _niche_budget = getattr(_directive, "niche_budget", {})
    _capital      = float(_niche_budget.get(niche, 0.0)) if _niche_budget else 0.0
    _capital      = _capital if _capital > 0 else 0.5   # neutral default
    _niche_action = getattr(_directive, "niche_overrides", {}).get(niche, "normal")
    signals["niche_capital"] = round(_capital, 4)
    signals["niche_action"]  = _niche_action

    def _reject(reason: str, score: float = 0.0, ev: float = 0.0,
                risk_flags: list[str] | None = None) -> ExecutionDecision:
        signals["risk_flags"] = risk_flags or []
        return ExecutionDecision(
            decision="reject", reason=reason,
            final_score=round(score, 4), expected_value=round(ev, 4),
            selected_account="", content_mode=mode,
            content_id=content_id, platform=platform, niche=niche,
            signals=signals,
            meta={"elapsed_ms": round((time.monotonic() - t0) * 1000, 1)},
        )

    # Part 8: Product Matching — AFTER angle_engine + _reject, BEFORE account selection
    try:
        from core.product_matcher import match_product as _match_product

        _product_pool: list = candidate.get("product_pool", [])

        # Auto-fetch targeted product pool from scanner (intent-aware)
        if not _product_pool:
            try:
                from core.product_scanner import get_product_pool_by_candidate as _get_pool
                _product_pool = _get_pool(candidate)
            except Exception:
                _product_pool = []

        _pm_result = _match_product(candidate, _product_pool)

        # Shared explainability signals (always emit)
        signals["intent_type"]       = _pm_result.get("intent_type", "curiosity")
        signals["intent_alignment"]  = _pm_result.get("intent_alignment", "unknown")
        signals["reject_reason"]     = _pm_result.get("reject_reason")

        # no_product_attached — empty pool, neutral pass-through
        if _pm_result.get("no_product_attached"):
            candidate["product_match_score"]    = 0.5
            candidate["conversion_proxy_score"] = 0.5
            signals["product_match_score"]      = 0.5
            signals["conversion_proxy_score"]   = 0.5
            signals["no_product_mode"]          = True

        # soft_match — near-miss, use indirect monetization (DO NOT attach product)
        elif _pm_result.get("soft_match"):
            _sm_score = float(_pm_result.get("product_match_score", 0.5))
            candidate["product_match_score"]    = _sm_score
            candidate["conversion_proxy_score"] = float(_pm_result.get("conversion_proxy_score", 0.5))
            candidate["recommended_product"]    = _pm_result.get("recommended_product")
            candidate["suggest_soft_cta"]       = True
            signals["product_match_score"]      = _sm_score
            signals["conversion_proxy_score"]   = float(_pm_result.get("conversion_proxy_score", 0.5))
            signals["soft_monetization"]        = True
            signals["suggest_soft_cta"]         = True
            signals["monetization_mode"]        = "indirect"
            signals["no_product_mode"]          = False

        elif _pm_result.get("reject"):
            # Part 3: DO NOT hard-reject — fall back to pure content mode.
            # "Content leads. Product follows." — missing product ≠ bad content.
            _pm_score = float(_pm_result.get("product_match_score", 0.0))
            candidate["product_match_score"]    = _pm_score
            candidate["conversion_proxy_score"] = float(_pm_result.get("conversion_proxy_score", 0.0))
            signals["product_match_score"]      = _pm_score
            signals["conversion_proxy_score"]   = float(_pm_result.get("conversion_proxy_score", 0.0))
            signals["no_product_mode"]          = True   # exploration / pure content

        else:
            candidate["product"]                = _pm_result["best_product"]
            candidate["product_match_score"]    = _pm_result["product_match_score"]
            candidate["intent_type"]            = _pm_result["intent_type"]
            candidate["conversion_proxy_score"] = _pm_result.get("conversion_proxy_score", 0.5)
            candidate["keyword_match_score"]    = _pm_result.get("keyword_match_score", 0.0)
            signals["product_match_score"]      = _pm_result["product_match_score"]
            signals["conversion_proxy_score"]   = _pm_result.get("conversion_proxy_score", 0.5)
            signals["keyword_match_score"]      = _pm_result.get("keyword_match_score", 0.0)
            signals["no_product_mode"]          = False

    except Exception:
        signals["product_match_score"]    = candidate.get("product_match_score", 0.5)
        signals["conversion_proxy_score"] = candidate.get("conversion_proxy_score", 0.5)
        signals["intent_type"]            = candidate.get("intent_type", "curiosity")
        signals["no_product_mode"]        = False


    # Part 9-CME: Content Monetization Engine — AFTER product matching
    try:
        from core.content_monetization_engine import monetize as _monetize
        candidate["expected_value"] = candidate.get("expected_value", 0.0)
        _mono_result = _monetize(candidate)
        candidate["script"]            = _mono_result["script"]
        candidate["cta"]               = _mono_result.get("cta", {})
        candidate["cta_placement"]     = _mono_result.get("cta_placement", {})
        candidate["funnel_type"]       = _mono_result.get("funnel_type", "trust")
        candidate["monetization_mode"] = _mono_result["monetization_mode"]
        candidate["cta_type"]          = _mono_result["cta_type"]
        candidate["script_variants"]   = _mono_result.get("variants", [])
        signals["monetization_mode"]   = _mono_result["monetization_mode"]
        signals["product_used"]        = _mono_result["product_used"]
        signals["cta_type"]            = _mono_result["cta_type"]
        signals["funnel_type"]         = _mono_result.get("funnel_type", "trust")
        signals["has_variants"]        = len(_mono_result.get("variants", [])) > 0
    except Exception:
        signals["monetization_mode"]   = candidate.get("monetization_mode", "trust")
        signals["product_used"]        = False
        signals["cta_type"]            = "trust"
        signals["funnel_type"]         = "trust"
        signals["has_variants"]        = False


    # ── 1. Select account (FIX 4: explicit overposting reason) ─────────────────
    account, acct_reject_reason = _select_account(
        accounts, platform, niche, content_id, candidate, rng
    )
    if not account:
        return _reject(acct_reject_reason or "no_available_account")
    account_id = account.get("account_id", "")

    # ── 2. Hard reject: lifecycle DEAD ──────────────────────────────────────
    lifecycle_score, lc_state = _get_lifecycle_score(content_id, niche, platform)
    signals["lifecycle_state"] = lc_state
    signals["lifecycle_score"] = lifecycle_score
    if lc_state == "dead":
        return _reject("lifecycle_dead", lifecycle_score)

    # ── 3. Gather signals early (need hook for risk control) ───────────────
    trend_score                              = _get_trend_score(candidate)
    hook_score, best_hook                    = _get_hook_score(candidate, niche, platform)
    account_health                           = _get_account_health(account, platform, niche)
    conv_score, ctr, cvr, ctr_s, cvr_s      = _get_conversion_score(platform, niche)
    novelty_score                            = _get_novelty_score(candidate)
    cross_hints                              = _get_cross_layer_hints(platform, niche)
    hook_samples                             = int(candidate.get("hook_samples", 0))
    trend_age_h                              = float(candidate.get("trend_age_h", 24.0))

    # ── 4. Risk control layer (all 4 guards) ────────────────────────────────
    risk_flags = risk_control(
        candidate=candidate, account=account,
        platform=platform, niche=niche, hook=best_hook,
    )
    blocking = [f for f in risk_flags if not f.startswith("niche_drift")]
    # niche_drift is a soft warn; overposting/hook_spam/duplicate are hard blocks
    hard_blocks = [f for f in risk_flags
                   if any(f.startswith(k) for k in ("overposting", "hook_spam", "duplicate"))]
    if hard_blocks:
        return _reject(f"risk:{hard_blocks[0]}", risk_flags=risk_flags)

    # ── Part 1.3: Risk stacking — correlated risks compound the danger ────────
    # 3+ risk flags = hard reject (system can't compensate stacked risk)
    if len(risk_flags) >= 3:
        return _reject(f"risk_stack:{len(risk_flags)}_flags", risk_flags=risk_flags)
    # 2 flags = surcharge (applied to final_score later)
    _risk_stack_hit = len(risk_flags) >= 2

    # ── 5. Confidence score ───────────────────────────────────────────────────
    confidence = _compute_confidence(
        ctr_samples  = ctr_s,
        cvr_samples  = cvr_s,
        hook_samples = hook_samples,
        trend_age_h  = trend_age_h,
    )
    
    # Part 1: Non-linear capital confidence boost — strong niches = real edge
    confidence = round(min(1.0, confidence * (1.0 + 0.10 * _capital)), 4)
    signals["confidence_cap_boost"] = round(0.10 * _capital, 4)

    # ── 6. Raw score ────────────────────────────────────────────────────
    raw_score = (
        W_TREND      * trend_score      +
        W_HOOK       * hook_score       +
        W_LIFECYCLE  * lifecycle_score  +
        W_ACCOUNT    * account_health   +
        W_CONVERSION * conv_score       +
        W_NOVELTY    * novelty_score
    )
    raw_score = round(min(1.0, max(0.0, raw_score)), 4)

    # ── 6a. Strategy threshold modifier (CEO adjusts raw_score gate only) ────
    # The modifier shifts the gate, NOT the score itself — raw_score stays pure.

    # ── 7. Confidence-weighted EV ───────────────────────────────────
    est_views                  = float(candidate.get("view_count", _DEFAULT_EST_VIEWS))
    ev, eff_ctr, eff_cvr       = _compute_expected_value(
        est_views, ctr, cvr, _aov, _cost, ctr_s, cvr_s
    )

    # ── 7a. Anti-snowball — CONDITIONAL (only when score is extreme) ────────
    # Only compress when |raw×conf − 1| > 0.2 (i.e. score is far from 0.8).
    # Prevents winner-take-all without flattening normal-range scores.
    _pre_damp = raw_score * confidence
    if abs(_pre_damp - 1.0) > 0.2:
        final_score = 1.0 + (_pre_damp - 1.0) * 0.8
        final_score = round(max(0.0, min(1.0, final_score)), 4)
    else:
        final_score = round(max(0.0, min(1.0, _pre_damp)), 4)

    # Per-account exposure penalty (applied on top of dampened score)
    posts_last_24h   = int(account.get("posts_today", 0))
    daily_limit      = _MAX_POSTS_PER_DAY.get(platform, 5)
    account_exposure = posts_last_24h / max(1, daily_limit)
    exposure_penalty = 1.0 - min(0.5, account_exposure)
    final_score      = round(final_score * exposure_penalty, 4)
    signals["account_exposure"] = round(account_exposure, 3)
    signals["exposure_penalty"] = round(exposure_penalty, 3)

    # Risk stacking penalty (2 flags)
    if _risk_stack_hit:
        final_score = round(max(0.0, final_score - _RISK_STACK_PENALTY), 4)
    signals["risk_stack_hit"] = _risk_stack_hit

    # Part 2: Real behavioral long-term score (audience-building signal)
    _fg = float(account.get("follower_growth_delta", -1.0))
    _rv = float(candidate.get("repeat_view_rate",     -1.0))
    _wt = float(candidate.get("avg_watch_time_ratio", -1.0))
    _has_lt_data = (_fg >= 0 or _rv >= 0 or _wt >= 0)
    if _has_lt_data:
        _fg  = max(0.0, min(1.0, _fg)) if _fg >= 0 else 0.3
        _rv  = max(0.0, min(1.0, _rv)) if _rv >= 0 else 0.3
        _wt  = max(0.0, min(1.0, _wt)) if _wt >= 0 else 0.3
        long_term_score = round(0.5 * _fg + 0.3 * _rv + 0.2 * _wt, 4)
    else:
        long_term_score = round(final_score * 0.5, 4)
    final_score = round(
        (1.0 - _LT_SCORE_WEIGHT) * final_score + _LT_SCORE_WEIGHT * long_term_score, 4
    )
    signals["long_term_score"] = long_term_score
    signals["has_lt_data"]     = _has_lt_data

    # Part 3: Niche saturation penalty (prevents over-farming)
    saturation   = 0.0
    _sat_penalty = 0.0
    try:
        from execution.content_memory import get_best_reup_candidates, get_stats
        _ms     = get_stats()
        _total  = max(1, _ms.get("total_posts", 1))
        _ncands = get_best_reup_candidates(niche, platform, limit=200)
        saturation = round(len(_ncands) / _total, 4)
    except Exception:
        pass
    if saturation > 0.4:
        _sat_penalty = min(0.30, 0.20 * saturation)
        final_score  = round(max(0.0, final_score * (1.0 - _sat_penalty)), 4)
    signals["saturation"]         = saturation
    signals["saturation_penalty"] = round(_sat_penalty, 4)

    # Part 2: Advanced competition model (detect crowding BEFORE saturation)
    _bvals   = list(_niche_budget.values()) if _niche_budget else []
    _avg_cap = sum(_bvals) / max(1, len(_bvals)) if _bvals else 0.5
    # niche_activity_share: how dominant this niche is vs average
    _niche_activity_share = round(min(1.0, _capital / max(0.01, _avg_cap)), 4) \
        if _bvals else 0.5
    # niche_growth_rate: approximate from growth_potential in candidate or budget trend
    _niche_growth_rate = float(candidate.get("niche_growth_rate", 0.0))
    competition_factor = round(
        0.7 * _niche_activity_share + 0.3 * _niche_growth_rate, 4
    )
    # Early-warning spike: fast-growing niche = incoming crowding
    if _niche_growth_rate > 0.25:
        competition_factor = round(min(1.0, competition_factor * 1.2), 4)
    _comp_penalty = 0.0
    if competition_factor > 0.5:
        _comp_penalty = min(0.25, 0.25 * competition_factor)
        final_score   = round(max(0.0, final_score * (1.0 - _comp_penalty)), 4)
    signals["competition_factor"]  = competition_factor
    signals["competition_penalty"] = round(_comp_penalty, 4)
    signals["niche_growth_rate"]   = _niche_growth_rate

    # Part 3.1: Platform score multiplier (role-based)
    _plat_mult = _PLATFORM_MULTIPLIER.get(platform, 1.0)
    final_score = round(max(0.0, min(1.0, final_score * _plat_mult)), 4)
    signals["platform_multiplier"] = _plat_mult

    # Part 2.3: Smart routing (state-driven) — with VALIDATION GATE (Part 1)
    # Compute validation score from behavioral signals before routing
    _retention_rate   = float(candidate.get("retention_rate",   0.5))
    _engagement_rate  = float(candidate.get("engagement_rate",  0.5))
    _replay_rate      = float(candidate.get("replay_rate",       0.5))
    _validation_score = round(
        0.4 * _retention_rate + 0.3 * _engagement_rate + 0.3 * _replay_rate, 4
    )
    _validation_failed = _validation_score < _VALIDATION_THRESHOLD
    
    # Override validation if it's an explicitly validated angle (Part 4)
    if candidate.get("angle_id"):
        _validation_failed = not candidate.get("is_validated_angle", False)
        
    signals["validation_score"]  = _validation_score
    signals["validation_failed"] = _validation_failed

    # Bug #1 FIX: Cross-platform boost with decay + source attribution
    # Prevents circular inflation (TikTok→Reels→Shorts→back)
    _pat_id_base   = _pattern_id(niche, mode, best_hook)
    _xp_boost      = 0.0
    _origin_plat   = "tiktok"  # default; real origin would be stored with content
    _tier          = _PLATFORM_TIER.get(platform, 0)
    _source_weight = _SOURCE_WEIGHTS[min(_tier, len(_SOURCE_WEIGHTS) - 1)]
    _reuse_count   = sum(_cross_platform_usage.get(_pat_id_base, {}).values())
    _decay         = 1.0 / (1.0 + _reuse_count * 0.5)

    try:
        from execution.cross_layer_learner import get_winning_combos
        _xp_combos_niche = get_winning_combos(platform, niche, limit=1)
        if _xp_combos_niche and getattr(_xp_combos_niche[0], "combo_score", 0) > 0.6:
            _raw_boost = _xp_combos_niche[0].combo_score * 0.15 * _source_weight * _decay
            # Hard cap: boost must NEVER exceed 20% of current final_score
            _xp_boost  = min(_raw_boost, 0.20 * final_score)
            final_score = round(min(1.0, final_score + _xp_boost), 4)
    except Exception:
        pass

    signals["cross_platform_boost"]  = round(_xp_boost, 4)
    signals["origin_platform"]       = _origin_plat
    signals["reuse_count"]           = _reuse_count
    signals["source_weight"]         = round(_source_weight, 3)
    signals["platform_role"]         = _PLATFORM_ROLE.get(platform, "scaling")

    # Part 3.7: Anti-cross-spam guard
    _xp_fatigue = _cross_platform_fatigue(_pat_id_base)
    if _xp_fatigue > 0:
        final_score = round(max(0.0, final_score * (1.0 - _xp_fatigue)), 4)
    signals["cross_platform_fatigue"] = round(_xp_fatigue, 4)

    # Part 2.2: Content state machine transition
    _c_state = _update_content_state(content_id, final_score, _xp_fatigue)
    signals["content_state"] = _c_state



    # Part 1: Volatility stability check
    _view_velocity      = float(candidate.get("view_velocity",      0.0))
    _avg_view_velocity  = float(candidate.get("avg_view_velocity",  max(1.0, _view_velocity)))
    _is_volatile        = (
        _avg_view_velocity > 0
        and _view_velocity > _VOLATILITY_SPIKE_MUL * _avg_view_velocity
    )
    if _is_volatile:
        final_score = round(max(0.0, final_score * (1.0 - _VOLATILITY_PENALTY)), 4)
    signals["is_volatile"]    = _is_volatile
    signals["view_velocity"]  = _view_velocity

    _route_to: list[str] = []
    _transform_required  = False
    if content_id not in _xp_killed and not _validation_failed:
        if _c_state == "validated":
            _route_to = ["reels"]          # validated → scale on Reels
            _transform_required = True      # hook + caption + format variation required
        elif _c_state == "scaled":
            _route_to = ["shorts"]         # scaled → harvest on Shorts
            _transform_required = True
    elif _validation_failed and _c_state not in ("test",):
        # Don't route unvalidated content — just log
        signals["routing_blocked_by_validation"] = True
    signals["platform_transform_required"] = _transform_required

    # Part 3.3: Cross-platform confidence boost (2+ platform success)
    _xp_success_count = len([s for s in _xp_performance.get(content_id, {}).values() if s > 0.60])
    if _xp_success_count >= 2:
        confidence = round(min(1.0, confidence * 1.15), 4)
        signals["cross_platform_confidence_boost"] = True
    else:
        signals["cross_platform_confidence_boost"] = False

    # Part 2.6: Cross-platform kill switch
    _failed_count = _xp_failed_count(content_id)
    if _failed_count >= 2:
        _xp_killed.add(content_id)
        signals["cross_platform_killed"] = True
        _route_to = []                      # block further routing
    else:
        signals["cross_platform_killed"] = False

    # Part 2.4: Cross-platform consistency score
    _xp_consistency = _cross_platform_consistency(content_id)
    # Record current score for future consistency computations
    _xp_performance.setdefault(content_id, {})[platform] = final_score
    # Apply: reward stable cross-platform content
    final_score = round(final_score * (0.85 + 0.15 * _xp_consistency), 4)
    signals["cross_platform_score"]    = _xp_consistency
    signals["cross_platform_failures"] = _failed_count

    # Part 4.1: Global anomaly detection (sudden spike + low engagement)
    _is_anomaly = (
        _is_volatile
        and _engagement_rate < 0.20           # low engagement with spike = fake viral
    )
    signals["is_anomaly"] = _is_anomaly


    # ── Part 1.1: Diversity penalty (echo-chamber guard) ─────────────────────
    # novelty_score is the echo-chamber signal: low novelty = high similarity
    _similarity = max(0.0, 1.0 - novelty_score)   # similarity ∈ [0,1]
    diversity_penalty = _similarity * _DIVERSITY_PRESSURE
    # CEO boosts exploration when top-3 patterns dominate >70% traffic
    _div_boost = float(getattr(_directive, "diversity_boost", 0.0))
    if _div_boost > 0:
        diversity_penalty = max(0.0, diversity_penalty - _div_boost)
    final_score = round(max(0.0, final_score * (1.0 - diversity_penalty)), 4)
    signals["diversity_penalty"] = round(diversity_penalty, 4)
    signals["diversity_boost"]   = round(_div_boost, 3)

    # ── Part 1.4: Content fatigue penalty ───────────────────────────────────
    _pat_id = _pattern_id(niche, mode, best_hook)
    fatigue  = _get_fatigue(account_id, _pat_id)
    if fatigue > 0:
        final_score = round(max(0.0, final_score * (1.0 - fatigue)), 4)
    signals["fatigue"]     = round(fatigue, 4)
    signals["pattern_id"]  = _pat_id

    # ── 8. Learning feedback boost (cross_layer validated combo) ───────────
    try:
        from execution.cross_layer_learner import get_winning_combos
        combos = get_winning_combos(platform, niche, limit=1)
        if combos and hasattr(combos[0], "combo_score") and combos[0].validated:
            _boost    = min(0.10, combos[0].combo_score * 0.15)
            raw_score = round(min(1.0, raw_score + _boost), 4)
            signals["learning_boost"] = round(_boost, 4)
        else:
            signals["learning_boost"] = 0.0
    except Exception:
        signals["learning_boost"] = 0.0

    # Part 5: Priority score — 9-factor with validation_score
    _risk_penalty_p = min(0.5, 0.1 * len(risk_flags))
    _ev_norm        = round(max(0.0, min(1.0, ev / max(1.0, abs(ev) + 1.0))), 4) if ev != 0 else 0.5
    _platform_capital = round((_plat_mult - 0.85) / 0.30, 4)
    _platform_capital = max(0.0, min(1.0, _platform_capital))
    _diversity_factor = float(getattr(_directive, "budget_diversity_factor", 0.5))
    priority_score = round(
        min(1.0, max(0.0,
            0.20 * final_score
            + 0.18 * confidence
            + 0.18 * _ev_norm
            + 0.12 * _capital
            + 0.10 * _platform_capital
            + 0.08 * _xp_consistency
            + 0.07 * (1.0 - _risk_penalty_p)
            + 0.07 * _validation_score
        )),
        4,
    )
    
    # Part 1: Priority Override (Relative Boost Only)
    if signals.get("is_validated_angle") and _risk_penalty_p <= 0.5:
        priority_score = priority_score * 1.25
        confidence = confidence * 1.10
        
    # Part 3: Winner Amplification Priority Boost (with ANTI-LOCK multiplier, Part 1)
    amp_score = float(candidate.get("angle_amplification_score", 0.0))
    # Retrieve anti-lock multiplier computed in _select_account context
    _niche_counts_read = _niche_amp_counts.get(niche, [0, 1])
    _amp_share = _niche_counts_read[0] / max(1, _niche_counts_read[1])
    _amp_multiplier = 0.7 if _amp_share > 0.6 else 1.0
    if amp_score > 0.6:
        priority_score *= (1 + 0.2 * amp_score) * _amp_multiplier
        confidence     *= (1 + 0.1 * amp_score) * _amp_multiplier
    signals["amplified_content_share"] = round(_amp_share, 4)
    signals["amp_effect_multiplier"]   = _amp_multiplier

    # Part 3: Cross-angle competition — penalise weaker angles in same niche
    _angle_id_cur = candidate.get("angle_id", "")
    if _angle_id_cur:
        _niche_ledger = _niche_angle_scores.setdefault(niche, {})
        _niche_ledger[_angle_id_cur] = priority_score  # record BEFORE penalty
        if len(_niche_ledger) >= 2:
            _sorted_angles = sorted(_niche_ledger.items(), key=lambda x: x[1], reverse=True)
            _top_ps  = _sorted_angles[0][1]
            # Compare current angle against top; apply penalty if clearly weaker
            if priority_score < _top_ps * 0.80:   # clearly weaker = < 80% of top
                priority_score = round(priority_score * 0.85, 4)
                signals["angle_competition_penalty"] = True
            else:
                signals["angle_competition_penalty"] = False

            # Part 4: Angle dominance control
            _total_angle_score = sum(v for _, v in _sorted_angles)
            if _total_angle_score > 0:
                _dominance_ratio = _sorted_angles[0][1] / _total_angle_score
                if _dominance_ratio > 0.5:
                    _dom_angle_id = _sorted_angles[0][0]
                    # Reduce dominant angle by 10%
                    _niche_ledger[_dom_angle_id] = round(_niche_ledger[_dom_angle_id] * 0.90, 4)
                    if _dom_angle_id == _angle_id_cur:
                        priority_score = round(priority_score * 0.90, 4)
                    # Boost second-best by 10%
                    if len(_sorted_angles) >= 2:
                        _second_id = _sorted_angles[1][0]
                        _niche_ledger[_second_id] = round(
                            _niche_ledger[_second_id] * 1.10, 4
                        )
                        if _second_id == _angle_id_cur:
                            priority_score = round(priority_score * 1.10, 4)
                    signals["angle_dominance_control"] = True
                else:
                    signals["angle_dominance_control"] = False
        else:
            signals["angle_competition_penalty"] = False
            signals["angle_dominance_control"]   = False
    else:
        signals["angle_competition_penalty"] = False
        signals["angle_dominance_control"]   = False

    # Part 6: Predictive pre-trend boost (dual-case)
    _early_trend_score  = float(candidate.get("early_trend_score",  0.0))
    _pattern_match_score = float(candidate.get("pattern_match_score", 0.0))
    _hybrid_pre_trend   = float(candidate.get("hybrid_pre_trend",   0.0))
    _est_views_current  = float(candidate.get("view_count",          0.0))
    _novelty_score_cur  = float(candidate.get("novelty_score",       novelty_score))

    # Part 9: budget check — allow pattern boost only when < 30% of decisions
    _pattern_boost_budget[1] += 1
    _pattern_boost_share = _pattern_boost_budget[0] / max(1, _pattern_boost_budget[1])
    _pattern_budget_ok   = _pattern_boost_share < 0.30

    _pre_trend_detected  = False
    _pattern_pretrend    = False

    # Case A — reactive early signal dominates
    if _early_trend_score > _PRE_TREND_THRESHOLD and _est_views_current < 10_000:
        _boost_factor = 1.15
        # Part 7: novelty false-positive guard
        if _novelty_score_cur < 0.4:
            _boost_factor = 1.0 + (_boost_factor - 1.0) * 0.5   # halve the boost
        priority_score      = round(priority_score * _boost_factor, 4)
        # _effective_explore nudge applied post-initialization (see below)
        _pre_trend_detected = True
        if _pattern_budget_ok:
            _pattern_boost_budget[0] += 1

    # Case B — no reactive signal BUT strong pattern match
    elif (
        _early_trend_score <= _PRE_TREND_THRESHOLD
        and _pattern_match_score > 0.70
        and _pattern_budget_ok
    ):
        _boost_factor = 1.05
        _conf_factor  = 1.03
        # Part 7: novelty false-positive guard
        if _novelty_score_cur < 0.4:
            _boost_factor = 1.0 + (_boost_factor - 1.0) * 0.5
            _conf_factor  = 1.0 + (_conf_factor  - 1.0) * 0.5
        priority_score = round(priority_score * _boost_factor, 4)
        confidence     = round(min(1.0, confidence * _conf_factor), 4)
        _pattern_pretrend = True
        _pattern_boost_budget[0] += 1

    signals["pre_trend_detected"]   = _pre_trend_detected
    signals["pattern_pretrend"]     = _pattern_pretrend
    signals["early_trend_score"]    = round(_early_trend_score, 4)
    signals["pattern_match_score"]  = round(_pattern_match_score, 4)
    signals["hybrid_pre_trend"]     = round(_hybrid_pre_trend, 4)
    signals["pattern_boost_share"]  = round(_pattern_boost_share, 4)

    # Part 10: Unified Scoring + Predictive Decision (single authority)
    try:
        from core.conversion_tracker import (
            get_performance_score   as _get_perf_score,
            get_performance_signals as _get_perf_signals,
            get_revenue_score       as _get_rev_score,
            get_funnel_score        as _get_funnel_score,
            get_best_cta            as _get_best_cta,
        )
        from core.revenue_optimizer import (
            should_explore              as _should_explore,
            get_distribution_multiplier as _get_dist_mult,
        )
        from core.unified_scoring import (
            compute_unified_score       as _compute_unified,
            compute_decision_score      as _compute_decision,
            apply_portfolio_adjustments as _apply_portfolio,
            decide_action               as _decide_action,
            compute_pattern_strength    as _compute_pattern_str,
            get_explore_rate            as _get_explore_rate,
        )
        from core.portfolio_memory import (
            get_pattern_strength         as _get_pat_str,
            get_market_phase             as _get_market_phase,
            get_pattern_fatigue          as _get_pat_fatigue,
            get_pattern_confidence       as _get_pat_conf,
        )

        # ── Step 1: Collect raw signals ───────────────────────────────────────
        _perf_score   = _get_perf_score(content_id)
        _perf_sigs    = _get_perf_signals(content_id)
        _rev_score    = _get_rev_score(content_id)
        _funnel_type  = candidate.get("funnel_type", "trust")
        _funnel_score = _get_funnel_score(_funnel_type)
        _retention    = float(candidate.get("retention_score", 0.5))
        _pattern_key  = candidate.get("pattern_key", f"{niche}:{_funnel_type}")

        signals["performance_score"] = round(_perf_score, 4)
        signals["revenue_score"]     = round(_rev_score, 4)
        signals["ctr"]               = _perf_sigs["ctr"]
        signals["cvr"]               = _perf_sigs["cvr"]
        signals["epv"]               = _perf_sigs["epv"]
        signals["funnel_score"]      = round(_funnel_score, 4)
        signals["retention_score"]   = _retention

        # ── Step 2: pattern_strength from portfolio_memory ────────────────────
        _pat_strength = _get_pat_str(_pattern_key, niche)
        signals["pattern_strength"] = _pat_strength

        # ── Step 3: Compute unified score (7-component, Part 1) ───────────────
        _unified_signals = {
            "revenue_score":     signals["revenue_score"],
            "ctr":               signals["ctr"],
            "cvr":               signals["cvr"],
            "retention_score":   signals["retention_score"],
            "funnel_score":      signals["funnel_score"],
            "performance_score": signals["performance_score"],
            "pattern_strength":  _pat_strength,
        }
        _unified_score = _compute_unified(
            _unified_signals,
            pattern_key=_pattern_key,
            niche=niche,
            active_patterns=candidate.get("active_patterns") or [],
        )

        # ── Step 4: Portfolio adjustments (Part 5) ─────────────────────────────
        _account_id    = str(candidate.get("account_id", ""))
        _unified_score = _apply_portfolio(
            _unified_score, _pattern_key, niche, _account_id
        )
        signals["unified_score"] = _unified_score

        # ── Step 5: Predictive decision_score (Part 2) ────────────────────────
        # ev_norm: expected_value normalized (ceiling = 500)
        _ev_raw  = float(candidate.get("expected_value", 0.0))
        _ev_norm_pred = min(1.0, _ev_raw / 500.0)

        # trend_norm: EWMA-based trend delta mapped [-1,1] → [0,1]
        _prev_score   = float(signals.get("_prev_unified_score", _unified_score))
        _trend_delta  = _unified_score - _prev_score           # [-1, 1]
        _trend_norm   = (_trend_delta + 1.0) / 2.0            # [0, 1]
        signals["_prev_unified_score"] = _unified_score        # rolling update

        _decision_score = _compute_decision(
            _unified_score, _ev_norm_pred, _trend_norm
        )

        # ── Step 5b: Market phase multiplier + confidence (Part 2/3) ──────────
        _phase_result   = _get_market_phase(niche)
        # get_market_phase returns (phase, confidence) in v4
        if isinstance(_phase_result, tuple):
            _market_phase, _phase_conf = _phase_result
        else:
            _market_phase, _phase_conf = str(_phase_result), 0.5

        # Confidence = min of phase confidence and pattern causal confidence
        _pat_conf       = _get_pat_conf(_pattern_key)
        _confidence     = min(_phase_conf, 1.0) * 0.5 + min(_pat_conf, 1.0) * 0.5
        signals["phase_confidence"]   = round(_phase_conf, 4)
        signals["pattern_confidence"] = round(_pat_conf, 4)
        signals["confidence"]         = round(_confidence, 4)

        # Recompute decision_score with confidence penalty (Part 3)
        _decision_score = _compute_decision(
            _unified_score, _ev_norm_pred, _trend_norm,
            confidence=_confidence
        )

        if _market_phase == "rising":
            _decision_score = min(1.0, _decision_score * 1.10)
        elif _market_phase == "declining":
            _decision_score = max(0.0, _decision_score * 0.85)
        signals["market_phase"] = _market_phase

        signals["decision_score"] = round(_decision_score, 4)
        signals["trend_norm"]     = round(_trend_norm, 4)

        # ── Step 6: ALL decisions use decision_score + per-pattern explore ─────
        _explore_seed = f"ro:{content_id}:{niche}"
        _pat_fatigue  = _get_pat_fatigue(_pattern_key)
        _explore_rate = _get_explore_rate(
            _market_phase, _pat_fatigue,
            pattern_key=_pattern_key,
            confidence=_confidence,
        )
        _strategy     = _decide_action(
            _decision_score, content_id,
            seed=_explore_seed, explore_rate=_explore_rate
        )
        _exploring    = (_strategy == "explore")
        signals["explore_rate"] = _explore_rate

        # ── Step 7: Priority score adjustment (decision_score drives boost) ───
        if _decision_score > 0.75:
            priority_score = round(min(1.0, priority_score * 1.15), 4)
            signals["perf_score_boost"] = True
        elif _decision_score < 0.35:
            priority_score = round(max(0.0, priority_score * 0.85), 4)
            signals["perf_score_boost"] = False
        else:
            signals["perf_score_boost"] = False

        # ── Step 8: CTA learning (signal only — no decision power) ────────────
        _cta_channels = ["video", "comment", "bio"]
        _cta_seed     = f"cta:{content_id}:{_pattern_key}"
        _best_ch      = _get_best_cta(_pattern_key, _cta_channels, seed=_cta_seed)
        _existing_cta = candidate.get("cta", {})
        if isinstance(_existing_cta, dict) and _existing_cta.get(_best_ch + "_cta"):
            candidate["cta_override_channel"] = _best_ch
            signals["cta_source"]             = "learned"
        else:
            signals["cta_source"] = "default"

        # ── Step 9: Funnel boost / penalty ────────────────────────────────────
        if _funnel_score > 0.6:
            priority_score = round(min(1.0, priority_score * 1.1), 4)
            signals["funnel_boost"] = True
        elif _funnel_score < 0.3:
            priority_score = round(max(0.0, priority_score * 0.9), 4)
            signals["funnel_boost"] = False
        else:
            signals["funnel_boost"] = False

        # ── Step 10: Distribution multiplier ─────────────────────────────────
        _dist_mult = _get_dist_mult(_unified_score, candidate)
        if _dist_mult > 1.0:
            candidate["distribution_multiplier"] = _dist_mult
            signals["distribution_multiplier"]   = _dist_mult

        # ── Step 11: Variant re-ranking — remove creative decision authority ──
        # (Part 3) semantic engine scores are inputs, not final decisions
        _variants = candidate.get("hook_variants") or candidate.get("variants") or []
        if isinstance(_variants, list) and _variants:
            for _v in _variants:
                if isinstance(_v, dict):
                    _v_score = float(
                        _v.get("variant_score")
                        or _v.get("final_score")
                        or _v.get("score")
                        or 0.5
                    )
                    _v["final_variant_score"] = round(
                        0.7 * _unified_score + 0.3 * _v_score, 4
                    )
            _variants.sort(key=lambda x: -x.get("final_variant_score", 0))
            candidate["hook_variants"] = _variants
            signals["variants_reranked"] = True
        else:
            signals["variants_reranked"] = False

        # ── Step 12: Stamp all decision signals ───────────────────────────────
        signals["strategy"]             = _strategy
        signals["strategy_reason"]      = (
            f"decision_score={_decision_score:.4f} "
            f"(unified={_unified_score:.4f} ev={_ev_norm_pred:.3f} "
            f"trend={_trend_norm:.3f})"
        )
        signals["scale_safe"]           = _decision_score >= 0.75
        signals["optimization_applied"] = True

        # ── Step 13: Portfolio memory updates (Part 6) ────────────────────────
        try:
            from core.portfolio_memory import (
                update_niche_performance,
                update_pattern_performance,
                update_account_performance,
            )
            _pm_metrics = {
                "revenue_score": signals["revenue_score"],
                "ctr":           signals["ctr"],
                "cvr":           signals["cvr"],
                "views":         float(candidate.get("views", 0)),
                "success_rate":  signals["cvr"],
                "roi":           float(candidate.get("account_roi", 1.0)),
                "risk_score":    float(candidate.get("risk_score", 0.3)),
            }
            if niche:
                update_niche_performance(niche, _pm_metrics)
                # Part 2: update niche timeseries (real CTR/CVR/EPV)
                from core.portfolio_memory import update_niche_timeseries
                update_niche_timeseries(
                    niche,
                    ctr=signals["ctr"],
                    cvr=signals["cvr"],
                    epv=float(signals.get("epv", 0.0)),
                )
            if _pattern_key:
                # Fix 2: scale feedback — slow learning when already scaling
                _lw = 0.8 if _strategy == "scale" else 1.0
                update_pattern_performance(_pattern_key, niche, _pm_metrics,
                                           learning_weight=_lw)
                # Part 4: update time-decay saturation
                from core.portfolio_memory import update_pattern_saturation
                update_pattern_saturation(_pattern_key)
                # Part A: true causal lift via accumulated count stats
                _active = candidate.get("active_patterns") or []
                if _active:
                    from core.portfolio_memory import record_causal_exposure
                    _content_success = signals["cvr"] > 0.02
                    for _ap in _active:
                        if _ap != _pattern_key:
                            record_causal_exposure(_ap, _pattern_key, _content_success)
            if _account_id:
                update_account_performance(_account_id, _pm_metrics)
        except Exception:
            pass   # portfolio updates are non-blocking

    except Exception:
        signals["performance_score"]    = 0.5
        signals["revenue_score"]        = 0.5
        signals["ctr"]                  = 0.0
        signals["cvr"]                  = 0.0
        signals["epv"]                  = 0.0
        signals["funnel_score"]         = 0.5
        signals["unified_score"]        = 0.5
        signals["decision_score"]       = 0.5
        signals["pattern_strength"]     = 0.5
        signals["strategy"]             = "explore"
        signals["cta_source"]           = "default"
        signals["optimization_applied"] = False


    # Part 3: Cross-angle competition (Mutation grouping)
    _orig_content_id = candidate.get("original_content_id")
    if _orig_content_id and mutation_groups is not None:
        group = mutation_groups.setdefault(_orig_content_id, [])
        if group:
            _top_mut_score = max(group)
            if priority_score < _top_mut_score * 0.85:
                priority_score *= 0.85
                signals["intra_content_competition"] = True
            else:
                signals["intra_content_competition"] = False
        else:
            signals["intra_content_competition"] = False
        group.append(priority_score)
        
    # ─────────────────────────────────────────────────────────────────────────
        
    # Part 4.1: Anomaly penalty on priority_score
    if _is_anomaly:
        priority_score = priority_score * (1.0 - _ANOMALY_PRIORITY_CUT)
        
    # Part 4: Normalization
    priority_score = round(min(1.0, priority_score), 4)
    confidence = round(min(1.0, confidence), 4)
    
    signals["priority_score"]    = priority_score
    signals["risk_penalty_p"]    = round(_risk_penalty_p, 3)
    signals["ev_norm"]           = _ev_norm
    signals["platform_capital"]  = _platform_capital
    signals["diversity_factor"]  = round(_diversity_factor, 4)


    signals.update({
        "trend_score":      round(trend_score,    4),
        "hook_score":       round(hook_score,     4),
        "account_health":   round(account_health, 4),
        "conv_score":       round(conv_score,     4),
        "novelty_score":    round(novelty_score,  4),
        "ctr":              round(ctr,             5),
        "cvr":              round(cvr,             5),
        "effective_ctr":    round(eff_ctr,         6),
        "effective_cvr":    round(eff_cvr,         6),
        "ctr_samples":      ctr_s,
        "cvr_samples":      cvr_s,
        "confidence":       confidence,
        "raw_score":        raw_score,
        "final_score":      final_score,
        "expected_value":   ev,
        "est_views":        est_views,
        "risk_flags":       risk_flags,
        "best_hook":        best_hook[:80],
        "cross_hints":      cross_hints,
        "account_id":       account_id,
    })

    # ── 8. Hard reject: negative EV (only when cost is meaningful) ─────────
    if ev < _MIN_EV and _cost >= _MIN_COST_FOR_EV:
        return _reject(f"negative_ev:{ev:.4f}", final_score, ev, risk_flags)

    # Part 4.2: Cost protection — reject if production_cost > EV * 0.80
    if _cost > 0 and ev > 0 and _cost > ev * _COST_EV_RATIO_MAX:
        return _reject(
            f"cost_exceeds_ev_ratio:cost={_cost:.3f}>ev={ev:.3f}*{_COST_EV_RATIO_MAX}",
            final_score, ev, risk_flags,
        )

    # Mutation Engine Part 2: Strict Mutation Cost Protection
    _mut_cost = float(candidate.get("mutation_cost", 0.0))
    _mut_ev   = float(candidate.get("mutation_ev", 0.0))
    if _mut_cost > 0 and _mut_cost > _mut_ev * 0.9:
        return _reject(f"mutation_cost_exceeds_ev:{_mut_cost:.3f}>{_mut_ev:.3f}*0.9", final_score, ev, risk_flags)

    # Part 4.1: Anomaly detected → reduce priority_score by 20% (applied post-compute)
    # (anomaly flag set above; penalty applied to priority_score after computation)

    # Part 1 (non-linear) + Part 4.8: Capital-adjusted threshold + exploration
    _threshold_modifier = getattr(_directive, "threshold_modifier", 1.0)
    # Non-linear: sqrt(capital) gives stronger advantage to high-capital niches
    _cap_threshold_adj  = 1.0 - 0.15 * math.sqrt(_capital)   # replaces linear 0.10*capital
    threshold = _THRESHOLDS.get(mode, 0.40) * _threshold_modifier * _cap_threshold_adj

    # Part 3.4: Platform-specific threshold factor
    threshold *= _PLATFORM_THRESHOLD_FACTOR.get(platform, 1.0)
    threshold  = round(max(0.10, min(0.70, threshold)), 4)

    _effective_explore = getattr(_directive, "exploration_rate", _EXPLORE_RATE)
    _effective_explore *= (1.0 + 0.20 * (1.0 - _capital))    # low capital → explore more
    # Part 3.4: Platform-specific exploration factor (tiktok=+20%, shorts=-15%)
    _effective_explore *= _PLATFORM_EXPLORE_FACTOR.get(platform, 1.0)
    _effective_explore  = max(0.05, min(0.30, _effective_explore))

    # Part 6 Case A: apply pre-trend exploration nudge now that _effective_explore is initialized
    if _pre_trend_detected:
        _effective_explore = min(0.30, _effective_explore + 0.05)

    # Niche action modifiers (dominate / expand / exit)
    if _niche_action == "dominate":
        threshold          = round(max(0.10, threshold * 0.90), 4)
        _effective_explore = max(0.05, _effective_explore * 0.50)
    elif _niche_action == "expand":
        threshold          = round(max(0.10, threshold * 0.95), 4)
    elif _niche_action == "exit":
        threshold          = round(min(0.70, threshold * 1.30), 4)
        _effective_explore = min(0.30, _effective_explore * 1.50)

    is_exploration = rng.random() < _effective_explore
    signals["threshold"]              = threshold
    signals["threshold_modifier"]     = round(_threshold_modifier, 3)
    signals["cap_threshold_adj"]      = round(_cap_threshold_adj, 4)
    signals["effective_explore_rate"] = round(_effective_explore, 3)
    signals["is_exploration"]         = is_exploration

    # ── 10. Score gate (gate on raw_score; exploration protects good content) ─
    # raw_score → pass/fail  |  final_score → ranking only
    if raw_score < threshold and not is_exploration:
        return _reject(
            f"score_below_threshold:{raw_score:.4f}<{threshold:.4f}",
            final_score, ev, risk_flags,
        )

    # ── 11. Select time ─────────────────────────────────────────────────────
    selected_time = _select_time(platform, niche)

    # ── 12. Delay if timing suboptimal (generate-mode strict) ───────────────
    decision = "publish"
    reason   = "all_signals_pass"

    if not is_exploration and mode == "generate":
        hrs_away = (selected_time - datetime.now(timezone.utc)).total_seconds() / 3600
        if hrs_away > 2.0:
            decision = "delay"
            reason   = f"scheduled_for_peak_hour:{selected_time.isoformat()}"

    signals["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)

    # Part 1.4 + 3.6: Record pattern usage + cross-platform on publish
    if decision == "publish":
        _increment_pattern(account_id, _pat_id)
        _record_cross_platform(_pat_id_base, platform)

    # Emit the state-machine routing computed earlier in the scoring block
    signals["route_to"] = _route_to   # already computed by state machine (2.3 / 2.6)


    LOGGER.info(
        "brain_decision decision=%s score=%.4f ev=%.4f mode=%s platform=%s explore=%s",
        decision, final_score, ev, mode, platform, is_exploration,
    )

    return ExecutionDecision(
        decision         = decision,
        reason           = reason,
        final_score      = final_score,
        expected_value   = ev,
        selected_account = account_id,
        selected_time    = selected_time,
        content_mode     = mode,
        content_id       = content_id,

        platform         = platform,
        niche            = niche,
        is_exploration   = is_exploration,
        signals          = signals,
        meta             = {"best_hook": best_hook[:80]},
    )


# ── Batch ─────────────────────────────────────────────────────────────────────

def batch_decide(
    candidates:    list[dict[str, Any]],
    accounts:      list[dict[str, Any]],
    platform:      str,
    niche:         str,
    mode:          str   = "reup",
    cost_per_item: float = 0.0,
    aov:           float = 0.0,
) -> list[ExecutionDecision]:
    """
    Run decide() over a batch of candidates.

    Returns decisions in score-descending order (publishes first).
    Never raises.
    """
    from collections import defaultdict
    mutation_groups = defaultdict(list)
    
    results: list[ExecutionDecision] = []
    for cand in candidates:
        try:
            d = decide(
                candidate       = cand,
                accounts        = accounts,
                platform        = platform,
                niche           = niche,
                mode            = mode,
                cost            = cost_per_item,
                aov             = aov,
                mutation_groups = mutation_groups,
            )
            results.append(d)
        except Exception as exc:
            LOGGER.warning("batch_decide_error content_id=%s error=%s",
                           cand.get("content_id"), exc)

    # Part 3: RANKING GUARANTEE (SAFE)
    top_non_angle_score = 0.0
    for r in results:
        if r.decision == "publish" and not r.signals.get("is_validated_angle"):
            top_non_angle_score = max(top_non_angle_score, r.signals.get("priority_score", 0.0))
            
    def _rank_key(x: ExecutionDecision):
        is_pub = (x.decision == "publish")
        ps = x.signals.get("priority_score", 0.0)
        is_val = x.signals.get("is_validated_angle", False)
        
        tier = 0
        if is_pub:
            tier = 1
            if is_val and ps >= (top_non_angle_score * 0.9):
                tier = 2
                
        return (tier, ps)

    results.sort(key=_rank_key, reverse=True)
    n_pub = sum(1 for r in results if r.decision == "publish")
    LOGGER.info(
        "batch_decide done total=%d publish=%d reject=%d delay=%d",
        len(results), n_pub,
        sum(1 for r in results if r.decision == "reject"),
        sum(1 for r in results if r.decision == "delay"),
    )
    return results


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_brain_stats() -> dict[str, Any]:
    """Aggregate health snapshot from all layers."""
    stats: dict[str, Any] = {"timestamp": time.time()}
    try:
        from execution.content_lifecycle import get_portfolio_summary
        stats["lifecycle_portfolio"] = get_portfolio_summary()
    except Exception:
        pass
    try:
        from execution.trend_filter import get_filter_stats
        stats["trend_filter"] = get_filter_stats(days=1)
    except Exception:
        pass
    try:
        from execution.hook_optimizer import get_top_hooks
        stats["top_hooks_sample"] = get_top_hooks("entertainment", limit=3)
    except Exception:
        pass
    try:
        from execution.portfolio_allocator import get_allocation_stats
        stats["portfolio"] = get_allocation_stats()
    except Exception:
        pass
    return stats
