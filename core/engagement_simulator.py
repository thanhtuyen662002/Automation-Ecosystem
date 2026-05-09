"""
Engagement Simulator — Simulates real user reactions to agent-posted content.

v2: Upgraded to consume feed_engine v2 signals:
  - Attention budget → skip_rate multiplier (Part 2)
  - Viral cascade state → viral reach amplifier (Part 3)
  - Feed position → engagement multiplier (Part 4)
  - Creator exposure → engagement diminishing returns (Part 5)

Unchanged contracts:
  - Fully deterministic: all variation via stable_hash_int.
  - No cross-account state reads.
  - All rates and scores bounded [0.0, 1.0].
  - EngagementResult data type unchanged (backward compat).
  - outcome_from_engagement() unchanged.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from core.mutation_controller import stable_hash_int
from core.feed_engine import (
    FeedResult, ContentPost,
    update_viral_state,
    _attention_skip_mult,
    _ATTENTION_STATE, _ATTENTION_SESSION_S,
    _make_content_id,
)

LOGGER = logging.getLogger("core.engagement_simulator")

# ── Base engagement rate ranges ───────────────────────────────────────────────

_BASE_RATES: dict[str, tuple[float, float]] = {
    "like":    (0.03, 0.15),
    "comment": (0.005, 0.03),
    "share":   (0.003, 0.02),
    "save":    (0.01, 0.05),
    "skip":    (0.40, 0.80),
}

_ENGAGEMENT_WEIGHTS: dict[str, float] = {
    "like":    0.20,
    "comment": 0.35,
    "share":   0.30,
    "save":    0.15,
}

_BASE_IMPRESSIONS: int = 10_000

# Part 4: position effect on engagement
_POS_TOP_ENG_BOOST:    float = 1.20
_POS_BOTTOM_ENG_PENALTY: float = 0.70

# Part 5: creator exposure diminishing engagement returns
_EXPOSURE_ENG_DECAY: float = 0.40  # per-exposure decay rate on engagement


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class EngagementResult:
    """Simulated user engagement for one FeedResult."""
    account_id:       str
    platform:         str
    niche:            str
    like_rate:        float
    comment_rate:     float
    share_rate:       float
    save_rate:        float
    skip_rate:        float
    engagement_score: float
    reach_count:      int
    like_count:       int
    comment_count:    int
    share_count:      int
    is_viral:         bool = False
    is_suppressed:    bool = False
    reasoning:        dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id":       self.account_id,
            "platform":         self.platform,
            "niche":            self.niche,
            "like_rate":        round(self.like_rate,        4),
            "comment_rate":     round(self.comment_rate,     4),
            "share_rate":       round(self.share_rate,       4),
            "save_rate":        round(self.save_rate,        4),
            "skip_rate":        round(self.skip_rate,        4),
            "engagement_score": round(self.engagement_score, 4),
            "reach_count":      self.reach_count,
            "is_viral":         self.is_viral,
            "is_suppressed":    self.is_suppressed,
        }


# ── Signal helpers ────────────────────────────────────────────────────────────

def _seeded_rate(
    account_id: str, action: str, day: int,
    base_min: float, base_max: float,
) -> float:
    seed = stable_hash_int(account_id, "engagement", action, str(day)) % 1000
    return base_min + (base_max - base_min) * (seed / 1000)


def _niche_audience_fit(niche: str, platform: str, day: int) -> float:
    seed = stable_hash_int("audience", platform, niche, str(day)) % 1000
    return 0.50 + seed / 1000 * 0.50


# ── Core simulation ────────────────────────────────────────────────────────────

def simulate_engagement(
    feed_result: FeedResult,
    post:        ContentPost,
) -> EngagementResult:
    """
    Simulate user engagement for a ranked post, consuming all v2 feed signals.

    New multipliers vs v1:
      - attention_skip_mult: low user attention → more skips
      - viral_cascade: update EMA, apply viral engagement boost
      - position_mult: top feed items get engagement boost
      - exposure_eng_mult: repeated creator → diminishing engagement returns
    """
    now          = feed_result.now
    day          = now // 86400
    hour         = (now // 3600) % 24

    # ── Base multipliers (unchanged from v1) ──────────────────────────────────
    ranking_mult   = 0.5 + feed_result.ranking_score * 1.5    # [0.5, 2.0]
    virality_mult  = 1.0 + feed_result.virality_score * 3.0   # [1.0, 4.0]
    audience_fit   = _niche_audience_fit(post.niche, post.platform, day)
    authority_mult = 0.70 + float(post.lifecycle_stage in ("MATURE", "GROWTH")) * 0.30
    peak_mult      = 1.20 if hour in {7, 8, 9, 19, 20, 21, 22} else 0.90

    # ── Part 2: Attention budget → skip modifier ───────────────────────────────
    # Read current budget from feed_engine state (set by rank_batch)
    session_bucket = now // _ATTENTION_SESSION_S
    _, budget      = _ATTENTION_STATE.get(post.account_id, (session_bucket, 1.0))
    attn_skip_mult = _attention_skip_mult(budget)

    # ── Part 3: Viral cascade (v3: keyed by content_id) ──────────────────────
    # Use content_id from FeedResult (set by rank_batch v3), else derive it.
    content_id        = feed_result.content_id or _make_content_id(post.account_id, post.niche, now)
    engagement_signal = min(1.0, feed_result.ranking_score * (1.0 + feed_result.virality_score))
    viral_score_new   = update_viral_state(content_id, engagement_signal, now)
    # Viral EMA boost: extra multiplier on engagement rates when score is high
    viral_eng_boost   = 1.0 + max(0.0, viral_score_new - 0.20) * 2.0   # [1.0, 2.6]
    viral_eng_boost   = min(viral_eng_boost, 3.0)

    # ── Part 4: Feed position effect on engagement ─────────────────────────────
    pos = feed_result.position   # 0 = single-post call (no batch)
    if pos > 0:
        if pos <= 3:
            position_mult = _POS_TOP_ENG_BOOST
        elif feed_result.ranking_score < 0.40:
            position_mult = _POS_BOTTOM_ENG_PENALTY
        else:
            position_mult = 1.0
    else:
        position_mult = 1.0

    # ── Part 5: Creator exposure diminishing engagement ────────────────────────
    exposure         = feed_result.creator_exposure
    exp_eng_mult     = max(0.30, 1.0 / (1.0 + exposure * _EXPOSURE_ENG_DECAY))

    # ── Per-action rates ───────────────────────────────────────────────────────
    rates: dict[str, float] = {}
    for action, (base_min, base_max) in _BASE_RATES.items():
        raw = _seeded_rate(post.account_id, action, day, base_min, base_max)

        if action == "skip":
            # Skip = inverse of engagement quality × attention depletion
            boosted = (
                raw
                * (1.0 / max(0.5, ranking_mult))
                * (1.0 / max(0.5, audience_fit))
                * attn_skip_mult          # Part 2: low budget → more skips
                * (1.0 / max(0.5, position_mult))  # top items skipped less
            )
        else:
            boosted = (
                raw
                * ranking_mult
                * virality_mult
                * audience_fit
                * authority_mult
                * peak_mult
                * viral_eng_boost         # Part 3: viral cascade boost
                * position_mult           # Part 4: top/bottom position
                * exp_eng_mult            # Part 5: creator fatigue
            )
        rates[action] = round(max(0.0, min(1.0, boosted)), 5)

    # ── Composite engagement_score ─────────────────────────────────────────────
    raw_score        = sum(rates[a] * w for a, w in _ENGAGEMENT_WEIGHTS.items())
    engagement_score = round(min(1.0, raw_score / 0.25), 4)

    # ── Absolute counts ────────────────────────────────────────────────────────
    reach_count   = max(1, int(feed_result.reach_score * _BASE_IMPRESSIONS))
    like_count    = int(reach_count * rates["like"])
    comment_count = int(reach_count * rates["comment"])
    share_count   = int(reach_count * rates["share"])

    is_viral      = (
        rates["share"] > 0.015
        or feed_result.virality_score > 0.40
        or viral_score_new > 0.50   # Part 3: sustained viral state
    )
    is_suppressed = (
        feed_result.ranking_score < 0.10
        or "novelty_suppressed" in feed_result.flags
        or "creator_saturated" in feed_result.flags   # Part 5
    )

    LOGGER.debug(
        "engagement_sim account=%s niche=%s score=%.3f viral=%.3f pos=%d exp=%d",
        post.account_id, post.niche, engagement_score,
        viral_score_new, pos, exposure,
    )

    return EngagementResult(
        account_id       = post.account_id,
        platform         = post.platform,
        niche            = post.niche,
        like_rate        = rates["like"],
        comment_rate     = rates["comment"],
        share_rate       = rates["share"],
        save_rate        = rates["save"],
        skip_rate        = rates["skip"],
        engagement_score = engagement_score,
        reach_count      = reach_count,
        like_count       = like_count,
        comment_count    = comment_count,
        share_count      = share_count,
        is_viral         = is_viral,
        is_suppressed    = is_suppressed,
        reasoning        = {
            "ranking_mult":    round(ranking_mult,    4),
            "virality_mult":   round(virality_mult,   4),
            "audience_fit":    round(audience_fit,    4),
            "authority_mult":  round(authority_mult,  4),
            "peak_mult":       peak_mult,
            "attention_budget": round(budget, 4),
            "attn_skip_mult":  round(attn_skip_mult,  4),
            "viral_score_new": round(viral_score_new, 4),
            "viral_eng_boost": round(viral_eng_boost, 4),
            "position_mult":   round(position_mult,   4),
            "exp_eng_mult":    round(exp_eng_mult,    4),
            "raw_rates":       {k: round(v, 5) for k, v in rates.items()},
        },
    )


# ── Convenience: engagement → RL signal ──────────────────────────────────────

def outcome_from_engagement(eng: EngagementResult) -> tuple[bool, bool]:
    """
    Convert engagement result to (success, ban) booleans for RL update.
    Unchanged from v1.
    """
    success = eng.engagement_score > 0.25 and not eng.is_suppressed
    ban     = eng.is_suppressed and eng.engagement_score < 0.05
    return success, ban
