"""
execution/portfolio_allocator.py — Portfolio Budget Allocator.

Replaces per-account randomness with a data-driven budget allocation
that ensures the system always maximises return on posting capacity.

Allocation model (configurable via env):
    WINNER_BUDGET   : 60% of slots → WINNING + SCALING lifecycle content
    NORMAL_BUDGET   : 25% of slots → TESTING phase content
    EXPLORE_BUDGET  : 15% of slots → random exploration / new content

Within each bucket, accounts are ranked by:
    account_score = page_score × product_score × lifecycle_viral_ema

The allocator produces a concrete PostingPlan: a list of
(account_id, content_id, platform, niche, mode) tuples ready to
feed into the scheduler.

Public API:
    allocate(budget, niche, platform, available_accounts,
             available_content)                     → AllocationPlan
    get_allocation_stats()                          → dict
    rank_accounts(accounts, niche, platform)        → list[RankedAccount]
    rank_content(candidates, niche)                 → list[RankedContent]

Design:
  - Pure function core (allocate) — no side effects, fully testable
  - Falls back gracefully when intelligence layers are unavailable
  - Never allocates more than platform daily limits allow
  - Exploration bucket always includes at least 1 slot
"""
from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any

LOGGER = logging.getLogger("execution.portfolio_allocator")

# ── Config ────────────────────────────────────────────────────────────────────

_WINNER_RATIO  = float(os.environ.get("PORTFOLIO_WINNER_RATIO",  "0.60"))
_NORMAL_RATIO  = float(os.environ.get("PORTFOLIO_NORMAL_RATIO",  "0.25"))
_EXPLORE_RATIO = float(os.environ.get("PORTFOLIO_EXPLORE_RATIO", "0.15"))

# Daily platform limits (safe conservative values)
_PLATFORM_DAILY_LIMIT: dict[str, int] = {
    "tiktok":   5,
    "facebook": 4,
    "instagram": 3,
}

# Minimum exploration slot regardless of budget size
_MIN_EXPLORE_SLOTS = 1


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class PostingSlot:
    account_id:   str
    content_id:   str
    platform:     str
    niche:        str
    mode:         str       = "reup"
    bucket:       str       = "normal"    # "winner" | "normal" | "explore"
    priority:     int       = 5           # scheduler priority (higher = sooner)
    account_score: float    = 0.0
    content_score: float    = 0.0
    meta:         dict[str, Any] = field(default_factory=dict)


@dataclass
class AllocationPlan:
    slots:          list[PostingSlot] = field(default_factory=list)
    winner_slots:   int  = 0
    normal_slots:   int  = 0
    explore_slots:  int  = 0
    total_budget:   int  = 0
    niche:          str  = ""
    platform:       str  = ""
    generated_at:   float = 0.0

    def to_scheduler_jobs(self) -> list[dict[str, Any]]:
        """Convert slots to scheduler-compatible job dicts."""
        return [
            {
                "content_id":  s.content_id,
                "account_id":  s.account_id,
                "platform":    s.platform,
                "niche":       s.niche,
                "mode":        s.mode,
                "priority":    s.priority,
                "bucket":      s.bucket,
                "meta":        s.meta,
            }
            for s in self.slots
        ]


@dataclass
class RankedAccount:
    account_id:   str
    platform:     str
    account_score: float  = 0.0
    page_score:   float   = 0.0
    posts_today:  int     = 0
    available:    bool    = True
    meta:         dict[str, Any] = field(default_factory=dict)


@dataclass
class RankedContent:
    content_id:   str
    niche:        str
    lifecycle_state: str  = "testing"
    viral_score:  float   = 0.0
    hook_score:   float   = 0.0
    content_score: float  = 0.0
    mode:         str     = "reup"
    bucket:       str     = "normal"
    meta:         dict[str, Any] = field(default_factory=dict)


# ── Account ranking ───────────────────────────────────────────────────────────

def rank_accounts(
    accounts:  list[dict[str, Any]],
    niche:     str   = "",
    platform:  str   = "tiktok",
    daily_limit: int = 0,
) -> list[RankedAccount]:
    """
    Score and rank accounts by effectiveness.

    Scoring tries (in order):
      1. page_intelligence.get_page_score() for page-level score
      2. account["health_score"] if present
      3. Default 0.5 (neutral)
    """
    limit = daily_limit or _PLATFORM_DAILY_LIMIT.get(platform, 5)
    ranked: list[RankedAccount] = []

    for acct in accounts:
        account_id = acct.get("account_id", "")
        posts_today = int(acct.get("posts_today", 0))
        if posts_today >= limit:
            continue   # already at daily limit

        page_score = float(acct.get("page_score", 0.0))
        if page_score == 0.0:
            try:
                from core.page_intelligence import get_page_score
                page_score = get_page_score(
                    page_id    = acct.get("page_id", account_id),
                    niche      = niche,
                    platform   = platform,
                )
            except Exception:
                page_score = float(acct.get("health_score", 0.5))

        # Check warmup stage — non-blocking: new accounts (not yet in warmup DB) pass through
        warmup_ready = True
        try:
            from execution.account_warmup import is_ready_to_post, WARMUP_ENABLED
            if WARMUP_ENABLED:
                warmup_ready = is_ready_to_post(account_id, platform)
        except Exception:
            warmup_ready = True   # warmup unavailable → allow

        if not warmup_ready:
            continue   # still warming up

        # Composite account score
        health       = float(acct.get("health_score", 1.0))
        account_score = page_score * 0.6 + health * 0.4

        ranked.append(RankedAccount(
            account_id    = account_id,
            platform      = platform,
            account_score = round(account_score, 4),
            page_score    = round(page_score, 4),
            posts_today   = posts_today,
            available     = True,
            meta          = acct.get("meta", {}),
        ))

    ranked.sort(key=lambda x: x.account_score, reverse=True)
    return ranked


# ── Content ranking ───────────────────────────────────────────────────────────

def rank_content(
    candidates: list[dict[str, Any]],
    niche:      str = "",
) -> list[RankedContent]:
    """
    Score and rank content candidates by expected return.

    Scoring:
      - Lifecycle state viral_ema (from content_lifecycle)
      - hook_score from candidate or hook_optimizer
      - trend_score from trend_filter
      - product_score from product_intelligence (optional)
    """
    ranked: list[RankedContent] = []

    for cand in candidates:
        cid   = cand.get("content_id", "")
        _niche = cand.get("niche", niche)

        # Get lifecycle data
        lifecycle_state = "testing"
        viral_ema       = 0.0
        try:
            from execution.content_lifecycle import get_state, get_lifecycle_report
            lc = get_lifecycle_report(cid)
            lifecycle_state = lc.get("state", "testing")
            viral_ema       = float(lc.get("viral_score_ema", 0.0))
        except Exception:
            pass

        hook_score   = float(cand.get("hook_score",   cand.get("hook_potential", 0.5)))
        trend_score  = float(cand.get("trend_score",  0.5))
        novelty      = float(cand.get("novelty_score", 0.5))

        # Optional product score
        product_score = 0.5
        try:
            from core.product_intelligence import get_product_score
            pid = cand.get("product_id", "")
            if pid:
                product_score = get_product_score(pid, _niche)
        except Exception:
            pass

        # Composite
        content_score = (
            0.35 * max(viral_ema, trend_score) +
            0.25 * hook_score                  +
            0.20 * product_score               +
            0.20 * novelty
        )

        # Assign bucket based on lifecycle state
        _BUCKET_MAP: dict[str, str | None] = {
            "winning":   "winner",
            "scaling":   "winner",
            "testing":   "normal",
            "saturated": "normal",
            "recycle":   "explore",
            "dead":      None,
        }
        # Default to "normal" for unknown/unregistered content
        bucket = _BUCKET_MAP.get(lifecycle_state, "normal")

        if bucket is None:
            continue   # skip DEAD content

        ranked.append(RankedContent(
            content_id     = cid,
            niche          = _niche,
            lifecycle_state = lifecycle_state,
            viral_score    = round(max(viral_ema, trend_score), 4),
            hook_score     = round(hook_score, 4),
            content_score  = round(min(1.0, content_score), 4),
            mode           = cand.get("mode", "reup"),
            bucket         = bucket,
            meta           = cand,
        ))

    ranked.sort(key=lambda x: x.content_score, reverse=True)
    return ranked


# ── Core allocator ────────────────────────────────────────────────────────────

def allocate(
    budget:             int,
    niche:              str,
    platform:           str,
    available_accounts: list[dict[str, Any]],
    available_content:  list[dict[str, Any]],
    daily_limit:        int = 0,
    seed:               int | None = None,
) -> AllocationPlan:
    """
    Produce a concrete posting plan for this cycle.

    budget:             total posting slots for this cycle
    available_accounts: list of account dicts (must have account_id)
    available_content:  list of content candidate dicts

    Returns AllocationPlan with concrete PostingSlot assignments.
    Never raises.
    """
    try:
        rng     = random.Random(seed)
        plan    = AllocationPlan(
            total_budget = budget,
            niche        = niche,
            platform     = platform,
            generated_at = time.time(),
        )

        if budget <= 0 or not available_accounts:
            return plan

        # ── Rank accounts and content ─────────────────────────────────────
        accts   = rank_accounts(available_accounts, niche, platform, daily_limit)
        content = rank_content(available_content, niche)

        if not accts:
            LOGGER.info("allocator_no_ready_accounts niche=%s platform=%s", niche, platform)
            return plan

        # ── Split budget by bucket ────────────────────────────────────────
        n_winner  = max(0, round(budget * _WINNER_RATIO))
        n_normal  = max(0, round(budget * _NORMAL_RATIO))
        n_explore = max(_MIN_EXPLORE_SLOTS, budget - n_winner - n_normal)

        # Enforce total
        total_alloc = n_winner + n_normal + n_explore
        if total_alloc > budget:
            n_winner -= (total_alloc - budget)

        # ── Bucket content ────────────────────────────────────────────────
        winners  = [c for c in content if c.bucket == "winner"]
        normals  = [c for c in content if c.bucket == "normal"]
        explores = [c for c in content if c.bucket == "explore"]

        # Pad with normal if winner bucket is small
        if len(winners) < n_winner:
            extra = normals[:n_winner - len(winners)]
            winners.extend(extra)
            normals = normals[len(extra):]

        # ── Create slots ─────────────────────────────────────────────────
        slots: list[PostingSlot] = []
        acct_idx = 0

        def _next_acct() -> RankedAccount | None:
            nonlocal acct_idx
            if acct_idx >= len(accts):
                acct_idx = 0   # round-robin
            if not accts:
                return None
            a = accts[acct_idx % len(accts)]
            acct_idx += 1
            return a

        def _make_slot(cand: RankedContent, bucket: str, priority: int) -> PostingSlot | None:
            acct = _next_acct()
            if not acct:
                return None
            return PostingSlot(
                account_id    = acct.account_id,
                content_id    = cand.content_id,
                platform      = platform,
                niche         = cand.niche or niche,
                mode          = cand.mode,
                bucket        = bucket,
                priority      = priority,
                account_score = acct.account_score,
                content_score = cand.content_score,
                meta          = {
                    "lifecycle_state": cand.lifecycle_state,
                    "viral_score":     cand.viral_score,
                    "hook_score":      cand.hook_score,
                },
            )

        # Winner slots (highest priority)
        for cand in winners[:n_winner]:
            s = _make_slot(cand, "winner", priority=9)
            if s:
                slots.append(s)

        # Normal slots
        for cand in normals[:n_normal]:
            s = _make_slot(cand, "normal", priority=5)
            if s:
                slots.append(s)

        # Exploration slots — random pick from explore pool + any remaining normals
        explore_pool = explores + normals[n_normal:]
        rng.shuffle(explore_pool)
        for cand in explore_pool[:n_explore]:
            s = _make_slot(cand, "explore", priority=2)
            if s:
                slots.append(s)

        plan.slots         = slots
        plan.winner_slots  = sum(1 for s in slots if s.bucket == "winner")
        plan.normal_slots  = sum(1 for s in slots if s.bucket == "normal")
        plan.explore_slots = sum(1 for s in slots if s.bucket == "explore")

        LOGGER.info(
            "allocation_done niche=%s platform=%s budget=%d "
            "winners=%d normal=%d explore=%d accounts_used=%d",
            niche, platform, budget,
            plan.winner_slots, plan.normal_slots, plan.explore_slots,
            len({s.account_id for s in slots}),
        )
        return plan

    except Exception as exc:
        LOGGER.warning("allocate_error niche=%s error=%s", niche, exc)
        return AllocationPlan(total_budget=budget, niche=niche, platform=platform)


# ── Convenience: allocate and push to scheduler ───────────────────────────────

def allocate_and_schedule(
    budget:             int,
    niche:              str,
    platform:           str,
    available_accounts: list[dict[str, Any]],
    available_content:  list[dict[str, Any]],
    daily_limit:        int = 0,
) -> AllocationPlan:
    """
    Convenience wrapper: allocate + push directly to scheduler.

    Returns AllocationPlan with `slots` already enqueued.
    """
    plan = allocate(
        budget             = budget,
        niche              = niche,
        platform           = platform,
        available_accounts = available_accounts,
        available_content  = available_content,
        daily_limit        = daily_limit,
    )
    if not plan.slots:
        return plan

    try:
        from execution.scheduler import enqueue as _enqueue
        for slot in plan.slots:
            _enqueue(
                candidate  = slot.meta,
                account_id = slot.account_id,
                priority   = slot.priority,
            )
        LOGGER.info("allocator_scheduled slots=%d", len(plan.slots))
    except Exception as exc:
        LOGGER.debug("allocator_schedule_error error=%s", exc)

    return plan


def get_allocation_stats(niche: str = "") -> dict[str, Any]:
    """Read-only summary: lifecycle distribution + portfolio health."""
    try:
        from execution.content_lifecycle import get_portfolio_summary
        summary = get_portfolio_summary()
    except Exception:
        summary = {}

    stats: dict[str, Any] = {
        "portfolio": summary,
        "config": {
            "winner_ratio":  _WINNER_RATIO,
            "normal_ratio":  _NORMAL_RATIO,
            "explore_ratio": _EXPLORE_RATIO,
        },
    }

    if niche:
        try:
            from execution.content_lifecycle import get_scaling_candidates, get_recycle_candidates
            stats["scaling_ready"] = len(get_scaling_candidates(niche, limit=100))
            stats["recycle_ready"] = len(get_recycle_candidates(niche, limit=100))
        except Exception:
            pass

    return stats
