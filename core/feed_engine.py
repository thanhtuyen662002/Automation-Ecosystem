"""
Feed Engine — Production-grade platform content ranking simulation.

v3: Final production realism upgrade.
v4: Exploration injection + adversarial anti-sync.

NEW in v3 (over v2):
  Part 1 — Content-Level Virality
    viral_state keyed by content_id (account_id+niche+day), not account_id.
    Time decay: viral *= exp(-Δt / tau) instead of linear subtraction.
    Viral boost applied AFTER normalisation (correct ranking order).

  Part 2 — Content Fatigue (per-account × niche)
    _FATIGUE[(account_id, niche)] tracks exposure fatigue [0.0, 0.60].
    Increases on each exposure, decays over time.
    Applied to raw_ranking BEFORE normalisation.

  Part 3 — Cross-Session Memory
    Fatigue persists across sessions (unlike attention budget which resets).
    Hard cap at _FATIGUE_MAX = 0.60.

  Part 4 — Ranking Integration Order
    fatigue  → applied in _score_post (pre-normalisation)
    viral    → applied post-normalisation as reach boost

Unchanged from v2:
  ContentPost / FeedResult types (FeedResult gains content_fatigue field).
  rank_content() backward-compat wrapper.
  All scores clamped [0.0, 1.0].
  Fully deterministic (stable_hash_int only, no random).
  No cross-account state reads.
  reset_feed_engine() for testing.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any

from core.mutation_controller import stable_hash_int

LOGGER = logging.getLogger("core.feed_engine")

# ── Constants ─────────────────────────────────────────────────────────────────

_PEAK_HOURS_MORNING: set[int] = {7, 8, 9}
_PEAK_HOURS_EVENING: set[int] = {19, 20, 21, 22}
_PEAK_MULT: float     = 1.20
_OFF_PEAK_MULT: float = 0.85

_NICHE_PLATFORM_FIT: dict[str, dict[str, float]] = {
    "tiktok":    {"entertainment": 0.95, "fitness": 0.85, "food": 0.80,
                  "tech": 0.65, "finance": 0.55, "travel": 0.75},
    "instagram": {"fitness": 0.90, "food": 0.90, "travel": 0.88,
                  "entertainment": 0.75, "tech": 0.55, "finance": 0.50},
    "youtube":   {"tech": 0.90, "finance": 0.85, "entertainment": 0.80,
                  "fitness": 0.75, "travel": 0.80, "food": 0.65},
    "facebook":  {"entertainment": 0.75, "food": 0.80, "finance": 0.70,
                  "tech": 0.65, "fitness": 0.65, "travel": 0.70},
    "shopee":    {"food": 0.90, "fitness": 0.80, "tech": 0.75,
                  "entertainment": 0.60, "finance": 0.55, "travel": 0.50},
    "zalo":      {"entertainment": 0.70, "food": 0.75, "travel": 0.65,
                  "tech": 0.60, "fitness": 0.60, "finance": 0.55},
}
_DEFAULT_FIT: float = 0.60

_STAGE_AUTHORITY: dict[str, float] = {
    "NEW": 0.25, "WARMUP": 0.45, "GROWTH": 0.65,
    "MATURE": 0.90, "DECLINE": 0.50, "RECOVERY": 0.55,
}

_NOVELTY_WINDOW_H: int  = 6
_NOVELTY_PENALTY: float = 0.25

# Part 2 — Attention Budget (unchanged)
_ATTENTION_SESSION_S: int   = 1800
_ATTENTION_DECAY_PER_POST:  float = 0.08
_ATTENTION_LOW_THRESHOLD:   float = 0.25

# Part 1 v3 — Content-Level Viral Cascade
_VIRAL_EMA_DECAY: float    = 0.85
_VIRAL_EMA_INJECT: float   = 0.15
_VIRAL_THRESHOLD: float    = 0.35
_VIRAL_REACH_BOOST: float  = 2.50
_VIRAL_MAX_SCORE: float    = 1.00
_VIRAL_TAU_HOURS: float    = 6.0   # exp decay half-life (~4.16h at tau=6)

# Part 2/3 v3 — Content Fatigue
_FATIGUE_MAX: float          = 0.60   # hard cap (Part 3)
_FATIGUE_PER_EXPOSURE: float = 0.12   # fatigue increase per view
_FATIGUE_DECAY_PER_HOUR: float = 0.04 # per-hour recovery rate (Part 3: persists cross-session)

# Part 4 — Feed Position (unchanged)
_POSITION_TOP_BOOST: float    = 1.20
_POSITION_BOTTOM_PENALTY: float = 0.70

# Part 5 — Creator Exposure (unchanged)
_MAX_CREATOR_EXPOSURE: int  = 5
_EXPOSURE_DECAY_BASE: float = 0.50

# Part 1 v4 — Exploration Injection
# exploration_score = 0.5 * novelty_factor + 0.5 * (1 - creator_exposure_norm)
# final_score = base * (1 - exploration_rate) + exploration_score * exploration_rate
_EXPLORATION_INJECT_BOTTOM_PCT: float = 0.30   # bottom 30% pool for guaranteed inject
_EXPLORATION_INJECT_BOOST: float = 1.15         # ×1.15 boost for injected item


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class ContentPost:
    """A unit of content submitted by an agent for ranking."""
    account_id:       str
    platform:         str
    niche:            str
    intensity:        float
    lifecycle_stage:  str  = "GROWTH"
    created_ts:       int  = 0
    now:              int  = field(default_factory=lambda: int(time.time()))
    action_diversity: float = 0.5
    extra:            dict = field(default_factory=dict)


@dataclass
class FeedResult:
    """Platform feed ranking output for one ContentPost."""
    account_id:       str
    platform:         str
    niche:            str
    reach_score:      float
    virality_score:   float
    ranking_score:    float
    # v2 fields
    position:         int   = 0
    attention_mult:   float = 1.0
    viral_state:      float = 0.0
    creator_exposure: int   = 0
    # v3 additions
    content_id:       str   = ""     # Part 1: content-level viral key
    content_fatigue:  float = 0.0    # Part 2: fatigue at time of ranking
    flags:            dict[str, str] = field(default_factory=dict)
    reasoning:        dict[str, Any] = field(default_factory=dict)
    now:              int   = field(default_factory=lambda: int(time.time()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id":       self.account_id,
            "platform":         self.platform,
            "niche":            self.niche,
            "reach_score":      round(self.reach_score,    4),
            "virality_score":   round(self.virality_score, 4),
            "ranking_score":    round(self.ranking_score,  4),
            "position":         self.position,
            "attention_mult":   round(self.attention_mult, 4),
            "viral_state":      round(self.viral_state,    4),
            "creator_exposure": self.creator_exposure,
            "content_id":       self.content_id,
            "content_fatigue":  round(self.content_fatigue, 4),
            "flags":            self.flags,
        }


# ── State stores ──────────────────────────────────────────────────────────────

_POST_HISTORY: dict[str, list[tuple[int, str]]] = {}

# Part 2 — Attention budget (session-scoped, resets each 30-min window)
_ATTENTION_STATE: dict[str, tuple[int, float]] = {}  # account_id → (session_bucket, budget)

# Part 1 v3 — Content-level viral state (keyed by content_id)
# content_id → (ema_score, last_update_ts)
_VIRAL_STATE: dict[str, tuple[float, int]] = {}

# Part 2/3 v3 — Fatigue (account_id, niche) → (fatigue_score, last_update_ts)
# Persists cross-session (Part 3)
_FATIGUE: dict[tuple[str, str], tuple[float, int]] = {}

# Part 5 — Creator exposure per (platform, session_bucket)
_CREATOR_EXPOSURE: dict[str, dict[str, int]] = {}


# ── Content ID derivation ─────────────────────────────────────────────────────

def _make_content_id(account_id: str, niche: str, now: int) -> str:
    """
    Deterministic content identifier: (account, niche, day).
    One content piece per account per niche per day — stable across calls.
    """
    day = now // 86400
    h   = stable_hash_int(account_id, "content_id", niche, str(day)) % 1_000_000
    return f"{account_id}:{niche}:{day}:{h}"


# ── Part 1 v3: Content-Level Viral Cascade ────────────────────────────────────

def get_viral_state(content_id: str) -> float:
    """Return current viral EMA score for a piece of content [0.0, 1.0]."""
    score, _ = _VIRAL_STATE.get(content_id, (0.0, 0))
    return score


def update_viral_state(
    content_id:       str,
    engagement_signal: float,
    now:              int,
) -> float:
    """
    Advance content viral EMA with exponential time decay.

    Formula:
        decayed   = prev * exp(-Δt / tau_seconds)
        new_score = decayed * 0.85 + signal * 0.15

    Returns updated viral score.
    """
    prev_score, last_ts = _VIRAL_STATE.get(content_id, (0.0, now))

    # Exponential time decay (Part 1 v3 — replaces linear subtraction)
    delta_s = max(0, now - last_ts)
    tau_s   = _VIRAL_TAU_HOURS * 3600
    decay_f = math.exp(-delta_s / tau_s) if delta_s > 0 else 1.0
    decayed = prev_score * decay_f

    new_score = decayed * _VIRAL_EMA_DECAY + engagement_signal * _VIRAL_EMA_INJECT
    new_score = round(max(0.0, min(_VIRAL_MAX_SCORE, new_score)), 5)
    _VIRAL_STATE[content_id] = (new_score, now)
    return new_score


def _viral_reach_boost(viral_score: float) -> float:
    """Reach multiplier: linear sub-threshold, then exponential above 0.35."""
    if viral_score <= _VIRAL_THRESHOLD:
        return 1.0 + viral_score * 0.3
    excess = viral_score - _VIRAL_THRESHOLD
    return 1.0 + (excess / (1.0 - _VIRAL_THRESHOLD)) * (_VIRAL_REACH_BOOST - 1.0)


# ── Part 2/3 v3: Content Fatigue ─────────────────────────────────────────────

def get_fatigue(account_id: str, niche: str, now: int) -> float:
    """
    Return current fatigue score for (account, niche) [0.0, _FATIGUE_MAX].

    Decays over time at _FATIGUE_DECAY_PER_HOUR.
    Persists across sessions (not reset by session boundary) — Part 3.
    """
    key = (account_id, niche)
    score, last_ts = _FATIGUE.get(key, (0.0, now))
    elapsed_h = max(0.0, (now - last_ts) / 3600)
    decayed   = max(0.0, score - elapsed_h * _FATIGUE_DECAY_PER_HOUR)
    return round(min(_FATIGUE_MAX, decayed), 5)


def increment_fatigue(account_id: str, niche: str, now: int) -> float:
    """
    Increase fatigue for (account, niche) by _FATIGUE_PER_EXPOSURE.
    Returns new fatigue value.
    """
    key     = (account_id, niche)
    current = get_fatigue(account_id, niche, now)
    new_val = min(_FATIGUE_MAX, current + _FATIGUE_PER_EXPOSURE)
    _FATIGUE[key] = (new_val, now)
    return new_val


# ── Part 2: Attention Budget (unchanged from v2) ──────────────────────────────

def _get_attention_budget(account_id: str, now: int) -> float:
    session_bucket = now // _ATTENTION_SESSION_S
    prev_bucket, prev_budget = _ATTENTION_STATE.get(account_id, (-1, 1.0))
    budget = 1.0 if session_bucket != prev_bucket \
        else max(0.0, prev_budget - _ATTENTION_DECAY_PER_POST)
    _ATTENTION_STATE[account_id] = (session_bucket, budget)
    return budget


def _attention_skip_mult(budget: float) -> float:
    if budget > _ATTENTION_LOW_THRESHOLD:
        return 1.0
    return 1.0 + (1.0 - budget / _ATTENTION_LOW_THRESHOLD)


# ── Part 1 v4: Exploration Helpers ───────────────────────────────────────────

def _exploration_score(novelty_mult: float, exposure: int) -> float:
    """
    Compute content exploration score [0.0, 1.0].

    exploration_score =
        0.5 * novelty_factor
        + 0.5 * (1 - creator_exposure_norm)

    novelty_factor       = novelty_mult (already [0,1] from _score_post)
    creator_exposure_norm = min(exposure / MAX_CREATOR_EXPOSURE, 1.0)
    """
    novelty_factor        = max(0.0, min(1.0, novelty_mult))
    exposure_norm         = min(1.0, exposure / max(1, _MAX_CREATOR_EXPOSURE))
    return round(0.5 * novelty_factor + 0.5 * (1.0 - exposure_norm), 4)


def _blend_exploration(
    base_score:       float,
    exploration_score: float,
    exploration_rate: float,
) -> float:
    """
    Blend base ranking with exploration score.

    final = base * (1 - rate) + exploration_score * rate
    """
    blended = base_score * (1.0 - exploration_rate) + exploration_score * exploration_rate
    return round(max(0.0, min(1.0, blended)), 5)


# ── Part 5: Creator Exposure (unchanged from v2) ──────────────────────────────

def _get_creator_exposure(account_id: str, platform: str, now: int) -> int:
    key = f"{platform}:{now // _ATTENTION_SESSION_S}"
    return _CREATOR_EXPOSURE.setdefault(key, {}).get(account_id, 0)


def _increment_creator_exposure(account_id: str, platform: str, now: int) -> int:
    key = f"{platform}:{now // _ATTENTION_SESSION_S}"
    counts = _CREATOR_EXPOSURE.setdefault(key, {})
    counts[account_id] = counts.get(account_id, 0) + 1
    return counts[account_id]


def _creator_exposure_mult(exposure: int) -> float:
    return round(1.0 / (1.0 + exposure * _EXPOSURE_DECAY_BASE), 4)


# ── Signal helpers (unchanged) ────────────────────────────────────────────────

def _record_post(account_id: str, now: int, niche: str) -> None:
    bucket = _POST_HISTORY.setdefault(account_id, [])
    bucket.append((now, niche))
    _POST_HISTORY[account_id] = [(t, n) for t, n in bucket if t >= now - 86400]


def _recent_niche_count(account_id: str, niche: str, now: int) -> int:
    cutoff = now - _NOVELTY_WINDOW_H * 3600
    return sum(1 for t, n in _POST_HISTORY.get(account_id, [])
               if n == niche and t >= cutoff)


def _niche_demand(niche: str, now: int) -> float:
    hour = (now // 3600) % 24
    seed = stable_hash_int("feed:niche_demand", niche, str(hour)) % 1000
    return round(0.70 + seed / 1000 * 0.40, 4)


def _timing_boost(now: int) -> float:
    hour = (now // 3600) % 24
    if hour in _PEAK_HOURS_MORNING or hour in _PEAK_HOURS_EVENING:
        return _PEAK_MULT
    if hour in {0, 1, 2, 3, 4, 5}:
        return 0.70
    return _OFF_PEAK_MULT


def _platform_fit(niche: str, platform: str) -> float:
    return _NICHE_PLATFORM_FIT.get(platform, {}).get(niche, _DEFAULT_FIT)


def _authority(lifecycle_stage: str) -> float:
    return _STAGE_AUTHORITY.get(lifecycle_stage, 0.50)


def _virality_spark(account_id: str, niche_demand: float,
                    content_quality: float, platform_fit: float, now: int) -> float:
    day        = now // 86400
    spark_seed = stable_hash_int(account_id, "feed:virality_spark", str(day)) % 100
    spark      = 0.80 if spark_seed < 8 else 0.10
    return round(min(1.0, spark * niche_demand * content_quality * platform_fit), 4)


# ── Core: per-post signal scoring ────────────────────────────────────────────

def _score_post(post: ContentPost) -> dict[str, Any]:
    """
    Compute raw per-post signals.
    v3: fatigue applied to raw_ranking BEFORE returning (Part 4 integration order).
    """
    now    = post.now
    flags: dict[str, str] = {}

    demand    = _niche_demand(post.niche, now)
    timing    = _timing_boost(now)
    authority = _authority(post.lifecycle_stage)
    fit       = _platform_fit(post.niche, post.platform)
    quality   = max(0.0, min(1.0,
                    post.intensity * 0.70 + post.action_diversity * 0.30))

    repeat_count = _recent_niche_count(post.account_id, post.niche, now)
    novelty_mult = max(0.0, 1.0 - repeat_count * _NOVELTY_PENALTY)
    if repeat_count > 0:
        flags["novelty_suppressed"] = f"niche={post.niche} repeat={repeat_count}"

    raw_reach = authority * timing * demand * fit * novelty_mult
    raw_reach = max(0.0, min(1.0, raw_reach))

    virality = _virality_spark(post.account_id, demand, quality, fit, now)

    raw_ranking = (
        0.35 * raw_reach +
        0.25 * quality +
        0.25 * demand +
        0.15 * virality
    )
    raw_ranking = max(0.0, min(1.0, raw_ranking))

    # Part 2/4 v3: apply fatigue BEFORE normalisation
    fatigue      = get_fatigue(post.account_id, post.niche, now)
    fatigued_ranking = raw_ranking * (1.0 - fatigue)
    fatigued_ranking = max(0.0, min(1.0, fatigued_ranking))
    if fatigue > 0.20:
        flags["fatigue_suppressed"] = f"niche={post.niche} fatigue={fatigue:.3f}"

    if authority < 0.35 and quality > 0.75:
        flags["authority_mismatch"] = f"authority={authority:.2f} quality={quality:.2f}"

    return {
        "demand":           demand,
        "timing":           timing,
        "authority":        authority,
        "fit":              fit,
        "quality":          quality,
        "novelty_mult":     novelty_mult,
        "repeat_count":     repeat_count,
        "raw_reach":        raw_reach,
        "virality":         virality,
        "raw_ranking":      raw_ranking,
        "fatigued_ranking": fatigued_ranking,   # used for normalisation
        "fatigue":          fatigue,
        "flags":            flags,
    }


# ── Competitive Batch Ranking ─────────────────────────────────────────────────

def rank_batch(posts: list[ContentPost]) -> list[FeedResult]:
    """
    Rank a batch of ContentPost objects.

    v3 integration order (Part 4):
      1. Score each post → _score_post (includes fatigue pre-penalty)
      2. Competition density penalty: score /= (1 + local_density)
      3. Relative normalisation against batch max
      4. Assign feed positions
      5. [POST-NORM] Apply content-level viral boost (Part 1 v3)
      6. Apply feed position effect (Part 4 v2)
      7. Apply creator exposure (Part 5 v2)
      8. Apply attention budget (Part 2 v2)
      9. Increment fatigue for this exposure (Part 2 v3)
    """
    if not posts:
        return []

    now = posts[0].now

    # Step 1: per-post scores (fatigue applied inside)
    signals = [_score_post(p) for p in posts]

    # Step 2: niche competition density
    niche_counts: dict[str, int] = {}
    for p in posts:
        niche_counts[p.niche] = niche_counts.get(p.niche, 0) + 1
    n_total = max(1, len(posts))

    penalised: list[float] = []
    for i, sig in enumerate(signals):
        density = niche_counts[posts[i].niche] / n_total
        # Use fatigued_ranking so fatigue feeds into competition (Part 4 order)
        penalised.append(sig["fatigued_ranking"] / (1.0 + density))

    # Step 3: relative normalisation
    max_score   = max(max(penalised), 1e-6)
    norm_scores = [s / max_score for s in penalised]

    # Step 4: assign positions
    sorted_idx = sorted(range(len(norm_scores)), key=lambda i: norm_scores[i], reverse=True)
    positions  = [0] * len(posts)
    for rank, idx in enumerate(sorted_idx):
        positions[idx] = rank + 1
    median_score = sorted(norm_scores)[len(norm_scores) // 2]

    results: list[FeedResult | None] = [None] * len(posts)

    for i, (post, sig) in enumerate(zip(posts, signals)):
        position  = positions[i]
        norm_rank = norm_scores[i]
        raw_reach = sig["raw_reach"]
        fatigue   = sig["fatigue"]

        # Step 5 [POST-NORM]: Content-level viral boost (Part 1 v3)
        content_id  = _make_content_id(post.account_id, post.niche, now)
        viral_score = get_viral_state(content_id)
        reach_boost = _viral_reach_boost(viral_score)

        # Step 6: Feed position
        if position <= 3:
            pos_mult = _POSITION_TOP_BOOST
        elif norm_rank < median_score:
            pos_mult = _POSITION_BOTTOM_PENALTY
        else:
            pos_mult = 1.0

        # Step 7: Creator exposure
        exposure      = _get_creator_exposure(post.account_id, post.platform, now)
        exposure_mult = _creator_exposure_mult(exposure)
        _increment_creator_exposure(post.account_id, post.platform, now)
        if exposure >= _MAX_CREATOR_EXPOSURE:
            sig["flags"]["creator_saturated"] = f"exposure={exposure}"

        # Step 8: Attention budget
        budget    = _get_attention_budget(post.account_id, now)
        attn_mult = max(0.5, 1.0 - (1.0 - budget) * 0.30)

        # Step 9: Increment fatigue for this exposure
        new_fatigue = increment_fatigue(post.account_id, post.niche, now)

        # Step 10 [EXPLORATION INJECT]: Part 1 v4
        # Order: after fatigue+norm, before viral boost (per spec)
        try:
            from core.adversarial_engine import (
                get_exploration_rate, clamp_exploration_rate,
            )
            from core.lifecycle_engine import get_stage_profile, LifecycleStage
            lc_profile       = get_stage_profile(LifecycleStage(post.lifecycle_stage))
            base_expl_bias   = lc_profile.exploration_bias
            exploration_rate = get_exploration_rate(post.account_id, base_expl_bias, now)
            exploration_rate = clamp_exploration_rate(exploration_rate, post.lifecycle_stage)
        except Exception:
            exploration_rate = 0.20   # safe fallback

        expl_score   = _exploration_score(sig["novelty_mult"], exposure)
        blended_rank = _blend_exploration(norm_rank * pos_mult * exposure_mult,
                                          expl_score, exploration_rate)

        # Composite reach (viral boost applied post-norm per Part 4)
        reach = raw_reach * pos_mult * exposure_mult * reach_boost * attn_mult
        reach = round(max(0.0, min(1.0, reach)), 4)

        # Composite ranking uses exploration-blended score; viral is reach-only
        ranking = round(max(0.0, min(1.0, blended_rank)), 4)

        _record_post(post.account_id, now, post.niche)

        results[i] = FeedResult(
            account_id       = post.account_id,
            platform         = post.platform,
            niche            = post.niche,
            reach_score      = reach,
            virality_score   = sig["virality"],
            ranking_score    = ranking,
            position         = position,
            attention_mult   = round(attn_mult, 4),
            viral_state      = viral_score,
            creator_exposure = exposure,
            content_id       = content_id,
            content_fatigue  = round(fatigue, 4),
            flags            = sig["flags"],
            reasoning        = {
                "niche_demand":       sig["demand"],
                "timing_boost":       sig["timing"],
                "authority":          sig["authority"],
                "platform_fit":       sig["fit"],
                "content_quality":    round(sig["quality"], 4),
                "novelty_mult":       round(sig["novelty_mult"], 4),
                "repeat_count":       sig["repeat_count"],
                "raw_ranking":        round(sig["raw_ranking"], 4),
                "fatigue":            round(fatigue, 4),
                "fatigued_ranking":   round(sig["fatigued_ranking"], 4),
                "penalised_rank":     round(penalised[i], 4),
                "norm_rank":          round(norm_rank, 4),
                "competition_density": round(niche_counts[post.niche] / n_total, 4),
                "pos_mult":           round(pos_mult, 4),
                "exposure_mult":      round(exposure_mult, 4),
                "exploration_rate":   round(exploration_rate, 4),
                "exploration_score":  round(expl_score, 4),
                "blended_rank":       round(blended_rank, 4),
                "viral_score":        viral_score,
                "reach_boost":        round(reach_boost, 4),
                "attention_budget":   round(budget, 4),
                "attn_mult":          round(attn_mult, 4),
                "new_fatigue":        round(new_fatigue, 4),
            },
            now = now,
        )

    # ── Part 1 v4: Probabilistic exploration inject ───────────────────────────
    # Fires ~70% of cycles only (removes every-cycle pattern).
    # Boost multiplier varies per account/time (removes fixed ×1.15 fingerprint).
    if len(results) >= 4:
        time_bucket = now // 3600   # 1-hour bucket for gate stability
        inject_gate = stable_hash_int(
            posts[0].platform, "explore_gate", str(time_bucket)
        ) % 100
        if inject_gate < 70:        # ~70% of cycles
            bottom_cutoff = max(1, int(len(results) * _EXPLORATION_INJECT_BOTTOM_PCT))
            # Sort by ranking_score ascending → first N are bottom pool
            scored = sorted(enumerate(results), key=lambda x: x[1].ranking_score)
            bottom_pool_idx = [idx for idx, _ in scored[:bottom_cutoff]]
            # Pick deterministically from pool (not random)
            pick_seed = stable_hash_int(
                posts[0].platform, "explore_inject", str(now)
            ) % len(bottom_pool_idx)
            inject_idx = bottom_pool_idx[pick_seed]
            r = results[inject_idx]
            if r is not None:
                # Variable boost [1.10, 1.19] — different per account + time
                boost_var  = stable_hash_int(
                    r.account_id, "inject_boost", str(time_bucket)
                ) % 10
                boost_mult = 1.10 + boost_var / 100.0
                boosted_reach   = round(min(1.0, r.reach_score   * boost_mult), 4)
                boosted_ranking = round(min(1.0, r.ranking_score * boost_mult), 4)
                r.flags["exploration_injected"] = f"boost={boost_mult:.2f}"
                r.reasoning["exploration_injected"] = True
                r.reasoning["inject_boost_mult"]    = boost_mult
                results[inject_idx] = FeedResult(
                    account_id       = r.account_id,
                    platform         = r.platform,
                    niche            = r.niche,
                    reach_score      = boosted_reach,
                    virality_score   = r.virality_score,
                    ranking_score    = boosted_ranking,
                    position         = r.position,
                    attention_mult   = r.attention_mult,
                    viral_state      = r.viral_state,
                    creator_exposure = r.creator_exposure,
                    content_id       = r.content_id,
                    content_fatigue  = r.content_fatigue,
                    flags            = r.flags,
                    reasoning        = r.reasoning,
                    now              = r.now,
                )

    LOGGER.debug("rank_batch n=%d platform=%s", len(posts), posts[0].platform)
    return results  # type: ignore[return-value]


def rank_content(post: ContentPost) -> FeedResult:
    """Single-post ranking — backward-compatible wrapper around rank_batch."""
    return rank_batch([post])[0]


# ── Reset ─────────────────────────────────────────────────────────────────────

def reset_feed_engine() -> None:
    """Full state reset — for testing only."""
    _POST_HISTORY.clear()
    _ATTENTION_STATE.clear()
    _VIRAL_STATE.clear()
    _FATIGUE.clear()
    _CREATOR_EXPOSURE.clear()
