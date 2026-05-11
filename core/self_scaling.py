"""
core/self_scaling.py — Self-Scaling Engine

Pipeline position (AFTER tracking):
    PRODUCE → PUBLISH → TRACK → update_performance → scaling_engine → next cycle planning

Design contracts:
  - Deterministic: all sampling is seed-based
  - Pluggable: zero changes to existing pipeline contract; call update_performance() + get_scaling_factor()
  - EV gate is upstream — scaling only applies to content that has ALREADY passed the decision layer
  - No spam: hard anti-spam constraints (max posts/day, min interval, no unm utated duplicates)
  - Decay: 2-cycle performance drop → 30% scale reduction
  - Budget allocator: 50% winners / 30% normal / 20% exploration per cycle

Public API:
    update_performance(content_id, niche, views, engagement_rate, conversion_rate, retention)
    get_scaling_factor(content_id, niche) -> float
    get_priority_queue(niche) -> list[dict]
    allocate_budget(total_budget, niche) -> BudgetAllocation
    get_scaling_log(last_n) -> list[dict]
    reset_scaling_state()   # for testing
"""
from __future__ import annotations

import hashlib
import logging
import math
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

LOGGER = logging.getLogger("core.self_scaling")


# ── Tier definitions ──────────────────────────────────────────────────────────

class ScalingTier(str, Enum):
    DEAD   = "DEAD"    # perf < 0.3  → stop completely
    NORMAL = "NORMAL"  # 0.3 – 0.6   → baseline
    WINNER = "WINNER"  # 0.6 – 0.8   → ×1.5
    VIRAL  = "VIRAL"   # > 0.8       → ×2–3 (capped by anti-spam)


_TIER_THRESHOLDS = {
    ScalingTier.DEAD:   (0.0,  0.30),
    ScalingTier.NORMAL: (0.30, 0.60),
    ScalingTier.WINNER: (0.60, 0.80),
    ScalingTier.VIRAL:  (0.80, 1.01),
}

_TIER_BASE_FACTOR: dict[ScalingTier, float] = {
    ScalingTier.DEAD:   0.0,
    ScalingTier.NORMAL: 1.0,
    ScalingTier.WINNER: 1.5,
    ScalingTier.VIRAL:  2.5,   # centre of ×2–3 range; clamped by anti-spam
}


# ── Performance signal weights ────────────────────────────────────────────────
# performance_score = 0.35×views + 0.25×engagement + 0.25×conversion + 0.15×retention

_PERF_WEIGHTS = {
    "views":        0.30,
    "engagement":   0.20,
    "conversion":   0.20,
    "profit_score": 0.30,
}

# EMA alpha for performance history
_PERF_ALPHA: float = 0.20

# ── Anti-spam hard limits ────────────────────────────────────────────────────
MAX_POSTS_PER_PAGE_PER_DAY: int   = 5
MIN_INTERVAL_BETWEEN_POSTS_S: int = 3600          # 1 hour
MAX_ACCOUNTS_FOR_DISTRIBUTION: int = 10           # max cross-account reuse
REUSE_MUTATION_REQUIRED: bool = True              # must mutate before reuse
DECAY_CONSECUTIVE_DROPS: int  = 2                 # cycles of decline before decay

# Decay multiplier applied when performance drops for N consecutive cycles
DECAY_FACTOR: float = 0.70   # 30% reduction

# ── Budget allocation ratios ──────────────────────────────────────────────────
BUDGET_WINNER_RATIO: float      = 0.60   # 60% high-profit winners
BUDGET_NORMAL_RATIO: float      = 0.25   # 25% normal baseline
BUDGET_EXPLORATION_RATIO: float = 0.15   # 15% exploration (keep >=10%)


# ── In-memory state ───────────────────────────────────────────────────────────

@dataclass
class _PerformanceRecord:
    """EMA-smoothed per-content performance state (v2 — profit-aware)."""
    content_id:        str
    niche:             str
    perf_score:        float = 0.0    # current EMA performance score [0,1]
    raw_views:         float = 0.0    # EMA of normalised views
    raw_engagement:    float = 0.0
    raw_conversion:    float = 0.0
    raw_profit_score:  float = 0.5    # EMA of profit_score from profit_engine
    tier:              ScalingTier = ScalingTier.NORMAL
    scaling_factor:    float = 1.0
    cycle_history:     list[float] = field(default_factory=list)   # rolling last-5 scores
    decay_count:       int   = 0      # consecutive cycles where perf dropped
    post_timestamps:   list[int] = field(default_factory=list)     # for anti-spam
    distributed_to:    set[str]  = field(default_factory=set)      # accounts used


# key: sha256(content_id|niche)[:16]
_PERF_STORE: dict[str, _PerformanceRecord] = {}

# Per-niche running stats for normalisation (EMA mean + variance)
_NICHE_STATS: dict[str, dict[str, float]] = {}
_NICHE_ALPHA: float = 0.10

# Scaling decision log
_SCALING_LOG: list[dict[str, Any]] = []
_MAX_LOG_SIZE: int = 5_000


# ── Helpers ───────────────────────────────────────────────────────────────────

def _key(content_id: str, niche: str) -> str:
    return hashlib.sha256(f"{content_id}|{niche}".encode()).hexdigest()[:16]


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _tier_from_score(score: float) -> ScalingTier:
    if score > 0.80:
        return ScalingTier.VIRAL
    if score > 0.60:
        return ScalingTier.WINNER
    if score >= 0.30:
        return ScalingTier.NORMAL
    return ScalingTier.DEAD


def _update_niche_stats(niche: str, raw_value: float, signal: str) -> float:
    """
    Update per-niche EMA mean for a signal, return normalised value [0,1].
    Normalisation: min-max proxy using mean ± 2σ approximation.
    """
    if niche not in _NICHE_STATS:
        _NICHE_STATS[niche] = {}
    ns = _NICHE_STATS[niche]
    key_mean = f"{signal}_mean"
    key_var  = f"{signal}_var"
    mean = ns.get(key_mean, raw_value)
    var  = ns.get(key_var,  0.01)
    new_mean = _NICHE_ALPHA * raw_value + (1 - _NICHE_ALPHA) * mean
    new_var  = _NICHE_ALPHA * (raw_value - new_mean) ** 2 + (1 - _NICHE_ALPHA) * var
    ns[key_mean] = new_mean
    ns[key_var]  = max(new_var, 1e-6)
    std = math.sqrt(ns[key_var])
    # Normalise: centre on mean, ±2σ → [0,1]
    if std < 1e-9:
        return 0.5
    z = (raw_value - new_mean) / (2.0 * std)
    return _clamp(0.5 + z)


# ── Part 1: Performance update ────────────────────────────────────────────────

def update_performance(
    content_id:      str,
    niche:           str,
    views:           float,         # raw view count or normalised [0,1]
    engagement_rate: float,         # [0,1]
    conversion_rate: float,         # [0,1]
    retention:       float  = 0.5,  # [0,1] kept for backward compat (unused in v2 weights)
    cycle:           int    = 0,
    seed:            int    = 0,
    profit_score:    float  = -1.0, # [0,1] or -1 = auto-read from profit_engine
) -> "_PerformanceRecord":
    """
    Update the performance EMA for a content item and recompute its tier
    and scaling factor.

    Call this AFTER the TRACK stage for each published content item.

    Args:
        content_id:      unique content identifier
        niche:           account niche (for normalisation)
        views:           raw view signal (will be niche-normalised)
        engagement_rate: likes+comments+shares / impressions [0,1]
        conversion_rate: affiliate clicks / views [0,1]
        retention:       kept for API compat; not used in v2 weighting
        profit_score:    from profit_engine EMA; -1 = auto-read
        cycle:           current pipeline cycle (for decay tracking)
        seed:            deterministic seed (use account day_seed)

    Returns:
        Updated _PerformanceRecord
    """
    k   = _key(content_id, niche)
    rec = _PERF_STORE.get(k)
    if rec is None:
        rec = _PerformanceRecord(content_id=content_id, niche=niche)
        _PERF_STORE[k] = rec

    # Niche-normalise views; clamp others
    n_views  = _update_niche_stats(niche, _clamp(views), "views")
    n_eng    = _clamp(engagement_rate)
    n_conv   = _clamp(conversion_rate)

    # profit_score: auto-read from profit_engine EMA if sentinel (-1)
    if profit_score < 0.0:
        try:
            from core.profit_engine import get_profit_score as _gps
            n_profit = _clamp(_gps(content_id, niche))
        except Exception:
            n_profit = 0.5   # neutral fallback
    else:
        n_profit = _clamp(profit_score)

    new_perf = (
        _PERF_WEIGHTS["views"]        * n_views
        + _PERF_WEIGHTS["engagement"] * n_eng
        + _PERF_WEIGHTS["conversion"] * n_conv
        + _PERF_WEIGHTS["profit_score"] * n_profit
    )

    # EMA update
    rec.raw_views        = _PERF_ALPHA * n_views  + (1 - _PERF_ALPHA) * rec.raw_views
    rec.raw_engagement   = _PERF_ALPHA * n_eng    + (1 - _PERF_ALPHA) * rec.raw_engagement
    rec.raw_conversion   = _PERF_ALPHA * n_conv   + (1 - _PERF_ALPHA) * rec.raw_conversion
    rec.raw_profit_score = _PERF_ALPHA * n_profit + (1 - _PERF_ALPHA) * rec.raw_profit_score

    old_perf  = rec.perf_score
    rec.perf_score = round(
        _PERF_ALPHA * new_perf + (1 - _PERF_ALPHA) * old_perf, 6
    )

    # Rolling history (last 5 cycles)
    rec.cycle_history.append(round(rec.perf_score, 4))
    if len(rec.cycle_history) > 5:
        rec.cycle_history.pop(0)

    # ── Decay mechanism ──────────────────────────────────────────────────────
    # If performance dropped vs previous cycle:
    if len(rec.cycle_history) >= 2:
        if rec.cycle_history[-1] < rec.cycle_history[-2]:
            rec.decay_count += 1
        else:
            rec.decay_count = 0   # reset on recovery
    else:
        rec.decay_count = 0

    # ── Tier + scaling factor ────────────────────────────────────────────────
    rec.tier = _tier_from_score(rec.perf_score)
    base_sf  = _TIER_BASE_FACTOR[rec.tier]

    # Apply decay: 2+ consecutive drops → 30% reduction; 4+ → revert to NORMAL
    if rec.decay_count >= DECAY_CONSECUTIVE_DROPS:
        decay_mult = DECAY_FACTOR ** min(rec.decay_count - 1, 3)
        base_sf    = base_sf * decay_mult
        if base_sf < _TIER_BASE_FACTOR[ScalingTier.NORMAL]:
            rec.tier = ScalingTier.NORMAL
            base_sf  = _TIER_BASE_FACTOR[ScalingTier.NORMAL]

    # VIRAL: seed-based jitter within ×2–3 range
    if rec.tier == ScalingTier.VIRAL:
        rng    = random.Random(seed ^ hash(content_id) & 0xFFFFFFFF)
        base_sf = rng.uniform(2.0, 3.0)

    rec.scaling_factor = round(_clamp(base_sf, 0.0, 3.0), 3)

    # Anti-fake-viral: high views + low profit -> cap SF <= 1.2
    try:
        from core.profit_engine import (
            _FAKE_VIRAL_VIEWS_THRESHOLD as _fvvt,
            _FAKE_VIRAL_PROFIT_THRESHOLD as _fvpt,
            _FAKE_VIRAL_SF_CAP as _fvcap,
        )
        if n_views >= _fvvt and n_profit < _fvpt:
            rec.scaling_factor = min(rec.scaling_factor, _fvcap)
            LOGGER.debug(
                "self_scaling anti_fake_viral cap content=%s sf=%.2f",
                content_id, rec.scaling_factor,
            )
    except Exception:
        pass

    # ── Log decision ─────────────────────────────────────────────────────────
    actions = _compute_scaling_actions(rec)
    _log_scaling({
        "content_id":      content_id,
        "niche":           niche,
        "cycle":           cycle,
        "performance_score": round(rec.perf_score, 4),
        "tier":            rec.tier.value,
        "scaling_factor":  rec.scaling_factor,
        "decay_count":     rec.decay_count,
        "actions":         actions,
        "signals": {
            "views":        round(n_views,  4),
            "engagement":   round(n_eng,    4),
            "conversion":   round(n_conv,   4),
            "profit_score": round(n_profit, 4),
        },
    })

    LOGGER.debug(
        "self_scaling update content=%s niche=%s perf=%.3f tier=%s sf=%.2f decay=%d",
        content_id, niche, rec.perf_score, rec.tier.value, rec.scaling_factor, rec.decay_count,
    )
    return rec


def _compute_scaling_actions(rec: _PerformanceRecord) -> dict[str, Any]:
    """
    Determine which scaling actions are recommended for a content item.

    Variant Expansion: WINNER (2 variants) or VIRAL (3 variants)
    Distribution Expansion: VIRAL only, up to MAX_ACCOUNTS_FOR_DISTRIBUTION
    Frequency Boost: WINNER (1.5×) or VIRAL (2×)
    """
    if rec.tier == ScalingTier.DEAD:
        return {"variant": 0, "distribution": 0, "frequency": 0.0, "stop": True}

    if rec.tier == ScalingTier.NORMAL:
        return {"variant": 0, "distribution": 0, "frequency": 1.0, "stop": False}

    if rec.tier == ScalingTier.WINNER:
        return {
            "variant":      2,     # generate 2 hook/caption variants
            "distribution": 0,     # no cross-account yet
            "frequency":    1.5,   # post 1.5× faster
            "stop":         False,
            "mutations":    ["hook", "caption"],
        }

    # VIRAL
    return {
        "variant":      3,
        "distribution": min(
            MAX_ACCOUNTS_FOR_DISTRIBUTION,
            max(1, 5 - len(rec.distributed_to)),   # respect already used
        ),
        "frequency":    2.0,
        "stop":         False,
        "mutations":    ["hook", "caption", "structure", "audio"],
    }


# ── Part 2: Public API ────────────────────────────────────────────────────────

def get_scaling_factor(content_id: str, niche: str) -> float:
    """
    Return the current scaling factor for a content item.

    1.0 = baseline (NORMAL)
    0.0 = DEAD (stop producing)
    1.5 = WINNER
    2.0–3.0 = VIRAL

    Returns 1.0 if the content has never been tracked (no history yet).
    """
    k   = _key(content_id, niche)
    rec = _PERF_STORE.get(k)
    if rec is None:
        return 1.0
    return rec.scaling_factor


def get_priority_queue(niche: str) -> list[dict[str, Any]]:
    """
    Return all tracked content for a niche, sorted by performance_score descending.

    Each entry:
        {content_id, niche, performance_score, tier, scaling_factor, decay_count, actions}
    """
    results = []
    for rec in _PERF_STORE.values():
        if rec.niche != niche:
            continue
        actions = _compute_scaling_actions(rec)
        results.append({
            "content_id":       rec.content_id,
            "niche":            rec.niche,
            "performance_score":round(rec.perf_score, 4),
            "tier":             rec.tier.value,
            "scaling_factor":   rec.scaling_factor,
            "decay_count":      rec.decay_count,
            "actions":          actions,
        })
    results.sort(key=lambda x: x["performance_score"], reverse=True)
    return results


# ── Part 4: Budget allocator ──────────────────────────────────────────────────

@dataclass
class BudgetAllocation:
    """Output from allocate_budget()."""
    total_budget:    float
    winner_budget:   float
    normal_budget:   float
    explore_budget:  float
    winner_allocs:   dict[str, float]   # content_id → allocated budget share
    explore_ids:     list[str]          # randomly selected exploration items


def allocate_budget(
    total_budget: float,
    niche:        str,
    seed:         int = 0,
) -> BudgetAllocation:
    """
    Allocate production budget across WINNER, NORMAL, and EXPLORATION tiers.

    Budget split:
        50% → WINNERs + VIRALs (proportional to performance_score)
        30% → NORMAL baseline
        20% → exploration (random from DEAD/NORMAL pool)

    Args:
        total_budget: total production unit budget for this cycle
        niche:        niche to allocate within
        seed:         deterministic seed for exploration sampling

    Returns:
        BudgetAllocation with per-content winner shares.
    """
    queue = get_priority_queue(niche)

    winner_budget  = round(total_budget * BUDGET_WINNER_RATIO, 4)
    normal_budget  = round(total_budget * BUDGET_NORMAL_RATIO, 4)
    explore_budget = round(total_budget * BUDGET_EXPLORATION_RATIO, 4)

    # Rank winners by performance_score and allocate proportionally
    winners = [q for q in queue if q["tier"] in (ScalingTier.WINNER.value, ScalingTier.VIRAL.value)]
    winner_allocs: dict[str, float] = {}

    if winners:
        total_wperf = sum(w["performance_score"] for w in winners)
        if total_wperf > 0:
            for w in winners:
                share = winner_budget * (w["performance_score"] / total_wperf)
                winner_allocs[w["content_id"]] = round(share, 4)

    # Exploration: random sample from DEAD + NORMAL pool (10% of dropped)
    explore_pool = [q["content_id"] for q in queue if q["tier"] in (ScalingTier.DEAD.value, ScalingTier.NORMAL.value)]
    n_explore    = max(1, math.ceil(len(explore_pool) * 0.10))
    rng          = random.Random(seed)
    explore_ids  = rng.sample(explore_pool, min(n_explore, len(explore_pool))) if explore_pool else []

    LOGGER.debug(
        "self_scaling budget niche=%s total=%.1f winners=%d explore=%d",
        niche, total_budget, len(winners), len(explore_ids),
    )
    return BudgetAllocation(
        total_budget   = total_budget,
        winner_budget  = winner_budget,
        normal_budget  = normal_budget,
        explore_budget = explore_budget,
        winner_allocs  = winner_allocs,
        explore_ids    = explore_ids,
    )


@dataclass
class ProductBudgetAllocation:
    """Output from get_product_budget_allocation() — budget grouped by product."""
    total_budget:      float
    per_product:       dict[str, float]   # product_id → budget share
    killed_products:   list[str]          # product_ids blocked by kill-switch
    unattributed:      float              # budget for content with no product mapping


def get_product_budget_allocation(
    total_budget: float,
    niche:        str,
    seed:         int = 0,
) -> "ProductBudgetAllocation":
    """
    Allocate production budget per PRODUCT (not just per content).

    Strategy:
        1. Group all tracked content in niche by product_id
        2. For each product: budget ∝ product_score × sum(content_perf_scores)
        3. Killed products receive 0 budget
        4. Content with no product mapping shares the remainder proportionally

    Args:
        total_budget: total production unit budget for this niche cycle
        niche:        niche to allocate within
        seed:         deterministic RNG seed

    Returns:
        ProductBudgetAllocation with per-product budget shares.
    """
    queue = get_priority_queue(niche)
    if not queue:
        return ProductBudgetAllocation(
            total_budget=total_budget,
            per_product={},
            killed_products=[],
            unattributed=total_budget,
        )

    # Build product → [content_perf] map using product_intelligence
    product_perf:   dict[str, float]  = {}   # product_id → weighted sum of perf
    killed_set:     set[str]          = set()
    unattributed_perf: float          = 0.0

    for entry in queue:
        cid  = entry["content_id"]
        perf = entry["performance_score"]

        try:
            from core.product_intelligence import (
                get_product_for_content, get_product_score, is_product_killed,
            )
            pid = get_product_for_content(cid)
            if pid is None:
                unattributed_perf += perf
                continue
            if is_product_killed(pid):
                killed_set.add(pid)
                continue   # 0 budget for killed products
            ps = get_product_score(pid)
            # Weight = product_score × content_perf (both [0,1])
            product_perf[pid] = product_perf.get(pid, 0.0) + ps * perf
        except Exception:
            unattributed_perf += perf

    total_weighted = sum(product_perf.values()) + unattributed_perf
    if total_weighted <= 0:
        return ProductBudgetAllocation(
            total_budget=total_budget,
            per_product={},
            killed_products=list(killed_set),
            unattributed=total_budget,
        )

    per_product:  dict[str, float] = {}
    for pid, w in product_perf.items():
        share = total_budget * (w / total_weighted)
        per_product[pid] = round(share, 4)

    unattributed = round(total_budget * (unattributed_perf / total_weighted), 4)

    LOGGER.debug(
        "self_scaling product_budget niche=%s total=%.1f products=%d killed=%d",
        niche, total_budget, len(per_product), len(killed_set),
    )
    return ProductBudgetAllocation(
        total_budget    = total_budget,
        per_product     = per_product,
        killed_products = list(killed_set),
        unattributed    = unattributed,
    )


# ── Part 5b: Page-aware budget allocation ─────────────────────────────────────

@dataclass
class PageAwareBudgetAllocation:
    """Budget allocation using product_score × page_score weighting."""
    total_budget:    float
    per_page:        dict[str, float]   # page_id → budget share
    exploration:     dict[str, float]   # new pages → fixed exploration budget
    paused_pages:    list[str]          # zero-budget pages
    explore_budget:  float


def get_page_aware_budget_allocation(
    total_budget: float,
    niche:        str,
    explore_ratio: float = 0.12,
    seed:          int   = 0,
) -> PageAwareBudgetAllocation:
    """
    Allocate production budget per PAGE using product_score × page_score.

    Budget split:
        (1 - explore_ratio) × total → active pages, weighted by score
        explore_ratio × total        → new/exploration pages, split equally

    Winner pages (score > 0.65):
        - More budget slots
        - Higher posting frequency (handled downstream via get_page_posting_frequency)

    Throttled/paused pages:
        - Throttled: weight × 0.3
        - Paused:    weight = 0.0

    Args:
        total_budget:  total production slots for the niche cycle
        niche:         niche to allocate within
        explore_ratio: fraction for exploration (default 12%)
        seed:          deterministic seed (unused currently, reserved)

    Returns:
        PageAwareBudgetAllocation
    """
    try:
        from core.page_intelligence import get_combined_budget_weights
        combined = get_combined_budget_weights(niche)
    except Exception as exc:
        LOGGER.debug("self_scaling page_aware_budget error=%s", exc)
        return PageAwareBudgetAllocation(
            total_budget   = total_budget,
            per_page       = {},
            exploration    = {},
            paused_pages   = [],
            explore_budget = 0.0,
        )

    explore_budget   = round(total_budget * _clamp(explore_ratio, 0.10, 0.15), 4)
    main_budget      = total_budget - explore_budget

    weights    = combined.get("weights", {})
    explore_ids= combined.get("exploration", [])
    paused     = combined.get("paused", [])

    # Main allocation
    per_page: dict[str, float] = {}
    total_w = sum(weights.values())
    if total_w > 0:
        for pid, w in weights.items():
            per_page[pid] = round(main_budget * (w / total_w), 4)

    # Exploration: equal split among new pages
    exploration: dict[str, float] = {}
    if explore_ids:
        each = round(explore_budget / len(explore_ids), 4)
        for pid in explore_ids:
            exploration[pid] = each

    LOGGER.debug(
        "self_scaling page_budget niche=%s total=%.1f pages=%d explore=%d paused=%d",
        niche, total_budget, len(per_page), len(exploration), len(paused),
    )
    return PageAwareBudgetAllocation(
        total_budget   = total_budget,
        per_page       = per_page,
        exploration    = exploration,
        paused_pages   = paused,
        explore_budget = explore_budget,
    )


def check_anti_spam(
    content_id:    str,
    niche:         str,
    account_id:    str,
    now_ts:        int,
    posts_today:   int,
) -> tuple[bool, str]:
    """
    Hard anti-spam constraint check.

    Rules:
        1. max posts per page/day ≤ MAX_POSTS_PER_PAGE_PER_DAY
        2. min interval between posts ≥ MIN_INTERVAL_BETWEEN_POSTS_S
        3. content_id must be mutated before cross-account distribution
        4. Decay triggers if repeated exposure drops performance

    Returns (allowed: bool, reason: str).
    """
    if posts_today >= MAX_POSTS_PER_PAGE_PER_DAY:
        return False, f"spam_guard: max_posts/day={MAX_POSTS_PER_PAGE_PER_DAY} reached"

    k   = _key(content_id, niche)
    rec = _PERF_STORE.get(k)
    if rec is None:
        return True, "no_history_allow"

    # Min interval check using last post timestamp
    if rec.post_timestamps:
        last_post = max(rec.post_timestamps)
        if (now_ts - last_post) < MIN_INTERVAL_BETWEEN_POSTS_S:
            wait = MIN_INTERVAL_BETWEEN_POSTS_S - (now_ts - last_post)
            return False, f"spam_guard: min_interval not met (wait {wait}s)"

    # Cross-account distribution: require mutation flag in account_id label
    if REUSE_MUTATION_REQUIRED and account_id in rec.distributed_to:
        return False, f"spam_guard: content already distributed to {account_id} without mutation"

    return True, "pass"


def record_post_timestamp(content_id: str, niche: str, account_id: str, now_ts: int) -> None:
    """Record that content was posted. Called AFTER anti-spam passes."""
    k   = _key(content_id, niche)
    rec = _PERF_STORE.get(k)
    if rec is None:
        return
    rec.post_timestamps.append(now_ts)
    if len(rec.post_timestamps) > 50:   # rolling cap
        rec.post_timestamps = rec.post_timestamps[-50:]
    rec.distributed_to.add(account_id)


# ── Logging ───────────────────────────────────────────────────────────────────

def _log_scaling(entry: dict[str, Any]) -> None:
    _SCALING_LOG.append(entry)
    if len(_SCALING_LOG) > _MAX_LOG_SIZE:
        del _SCALING_LOG[: len(_SCALING_LOG) - _MAX_LOG_SIZE]


def get_scaling_log(last_n: int = 100) -> list[dict[str, Any]]:
    """Return the most recent N scaling decisions."""
    return _SCALING_LOG[-last_n:]


# ── Reset (for testing) ───────────────────────────────────────────────────────

def reset_scaling_state() -> None:
    """Clear all in-memory state. For testing only."""
    _PERF_STORE.clear()
    _NICHE_STATS.clear()
    _SCALING_LOG.clear()
