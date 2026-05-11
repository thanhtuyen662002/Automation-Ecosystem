"""
execution/safe_metrics_collector.py — Safe Metrics Collector.

Wraps metrics_collector_playwright with comprehensive failure handling.
If live scraping fails, falls back to estimated metrics based on:
  - content age (views grow over time)
  - niche baseline engagement rates
  - historical performance from content_memory

System NEVER breaks due to missing metrics. All paths return valid data.

Public API:
    safe_collect(content_id, post_url, platform, niche, account_id)
                                                   → MetricsSnapshot
    safe_collect_all_due()                         → list[MetricsSnapshot]
    estimate_metrics(content_id, niche, age_hours) → MetricsSnapshot
    get_snapshot(content_id)                       → MetricsSnapshot

MetricsSnapshot is compatible with what metrics_store.update() expects.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

LOGGER = logging.getLogger("execution.safe_metrics_collector")

# ── Baseline engagement rates by niche ────────────────────────────────────────
# Median views at 24h for each niche (very conservative estimates)

_NICHE_BASELINES: dict[str, dict[str, float]] = {
    "tech":          {"views_24h": 1500,  "eng_rate": 0.045, "viral_prob": 0.08},
    "fitness":       {"views_24h": 2000,  "eng_rate": 0.055, "viral_prob": 0.10},
    "finance":       {"views_24h": 1200,  "eng_rate": 0.040, "viral_prob": 0.06},
    "entertainment": {"views_24h": 5000,  "eng_rate": 0.065, "viral_prob": 0.18},
    "food":          {"views_24h": 3500,  "eng_rate": 0.060, "viral_prob": 0.14},
    "travel":        {"views_24h": 2500,  "eng_rate": 0.050, "viral_prob": 0.10},
}
_DEFAULT_BASELINE = {"views_24h": 1000, "eng_rate": 0.040, "viral_prob": 0.06}

# Growth curve: view multiplier at N hours post-publish
_GROWTH_CURVE: list[tuple[float, float]] = [
    (1,   0.05),
    (3,   0.12),
    (6,   0.25),
    (12,  0.50),
    (24,  1.00),
    (48,  1.30),
    (72,  1.45),
    (168, 1.60),   # 7 days
    (720, 1.70),   # 30 days
]


def _growth_multiplier(age_hours: float) -> float:
    """Interpolate view growth multiplier based on post age."""
    if age_hours <= 0:
        return 0.0
    prev_h, prev_m = 0.0, 0.0
    for h, m in _GROWTH_CURVE:
        if age_hours <= h:
            # Linear interpolation
            if h == prev_h:
                return m
            frac = (age_hours - prev_h) / (h - prev_h)
            return prev_m + frac * (m - prev_m)
        prev_h, prev_m = h, m
    return _GROWTH_CURVE[-1][1]


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class MetricsSnapshot:
    content_id:    str
    post_url:      str        = ""
    platform:      str        = "tiktok"
    niche:         str        = "entertainment"
    views:         int        = 0
    likes:         int        = 0
    comments:      int        = 0
    shares:        int        = 0
    engagement_rate: float    = 0.0
    estimated:     bool       = False   # True = fallback estimate, not real data
    collected_at:  float      = 0.0
    error:         str        = ""
    meta:          dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.views > 0 or self.estimated

    def to_metrics_store_dict(self) -> dict[str, Any]:
        """Convert to format compatible with metrics_store.update()."""
        return {
            "views":           self.views,
            "likes":           self.likes,
            "comments":        self.comments,
            "engagement":      self.engagement_rate,
            "estimated":       self.estimated,
        }


# ── Estimation ────────────────────────────────────────────────────────────────

def estimate_metrics(
    content_id:  str,
    niche:       str   = "entertainment",
    age_hours:   float = 24.0,
    platform:    str   = "tiktok",
) -> MetricsSnapshot:
    """
    Estimate metrics when real scraping is unavailable.

    Uses niche baseline × growth curve × content_memory history (if available).
    """
    baseline = _NICHE_BASELINES.get(niche, _DEFAULT_BASELINE)
    mult     = _growth_multiplier(age_hours)

    base_views   = baseline["views_24h"]
    eng_rate     = baseline["eng_rate"]

    # Check content_memory for historical performance of similar content
    try:
        from execution.content_memory import get_best_reup_candidates
        peers = get_best_reup_candidates(niche, platform, limit=5)
        if peers:
            import statistics
            peer_views = [p.get("views", 0) for p in peers if p.get("views", 0) > 0]
            if peer_views:
                base_views = int(statistics.median(peer_views))
    except Exception:
        pass

    est_views    = int(base_views * mult)
    est_likes    = int(est_views * eng_rate * 0.7)
    est_comments = int(est_views * eng_rate * 0.15)
    est_shares   = int(est_views * eng_rate * 0.05)

    LOGGER.debug(
        "metrics_estimated content_id=%s views=%d age_h=%.1f niche=%s",
        content_id, est_views, age_hours, niche,
    )
    return MetricsSnapshot(
        content_id     = content_id,
        platform       = platform,
        niche          = niche,
        views          = est_views,
        likes          = est_likes,
        comments       = est_comments,
        shares         = est_shares,
        engagement_rate = eng_rate,
        estimated      = True,
        collected_at   = time.time(),
        meta           = {"age_hours": age_hours, "growth_mult": mult},
    )


# ── Safe collection ───────────────────────────────────────────────────────────

def safe_collect(
    content_id:  str,
    post_url:    str,
    platform:    str   = "tiktok",
    niche:       str   = "entertainment",
    account_id:  str   = "",
    published_at: float = 0.0,
    headless:    bool  = True,
) -> MetricsSnapshot:
    """
    Attempt to collect real metrics from post_url.
    On any failure, returns estimated metrics instead.
    Never raises.

    Flow:
        1. Try live scraper (metrics_collector_playwright.collect_one())
        2. On failure → estimate_metrics()
        3. Always push to content_memory.update_performance()
        4. Always push to profit_engine if revenue estimable
    """
    age_hours = (time.time() - published_at) / 3600 if published_at else 24.0
    snap      = MetricsSnapshot(
        content_id=content_id, post_url=post_url, platform=platform, niche=niche,
        collected_at=time.time(),
    )

    # ── Step 1: Try live scraper ───────────────────────────────────────────
    live_ok = False
    if post_url:
        try:
            from execution.metrics_collector_playwright import collect_one
            post_record = {
                "content_id":  content_id,
                "post_url":    post_url,
                "platform":    platform,
                "niche":       niche,
                "account_id":  account_id,
                "collect_count": 0,
            }
            cr = collect_one(post_record, headless=headless)
            if cr.success and cr.views > 0:
                snap.views          = cr.views
                snap.likes          = cr.likes
                snap.comments       = cr.comments
                snap.engagement_rate = (cr.likes + cr.comments) / max(1, cr.views)
                snap.estimated      = False
                live_ok             = True
                LOGGER.info(
                    "safe_metrics_live content_id=%s views=%d", content_id, cr.views
                )
            else:
                snap.error = cr.error or "zero_views"
        except Exception as exc:
            snap.error = str(exc)
            LOGGER.warning(
                "safe_metrics_live_failed content_id=%s error=%s", content_id, exc
            )

    # ── Step 2: Fallback to estimation ────────────────────────────────────
    if not live_ok:
        est = estimate_metrics(content_id, niche, age_hours, platform)
        snap.views           = est.views
        snap.likes           = est.likes
        snap.comments        = est.comments
        snap.shares          = est.shares
        snap.engagement_rate = est.engagement_rate
        snap.estimated       = True
        snap.meta            = est.meta
        if not snap.error:
            snap.error = "live_scrape_unavailable"
        LOGGER.info(
            "safe_metrics_estimated content_id=%s views=%d (fallback)",
            content_id, snap.views,
        )

    # ── Step 3: Push to content_memory ────────────────────────────────────
    try:
        from execution.content_memory import update_performance
        update_performance(
            content_id   = content_id,
            views        = snap.views,
            likes        = snap.likes,
            comments     = snap.comments,
            shares       = snap.shares,
            revenue      = 0.0,
            profit_score = snap.engagement_rate,
        )
    except Exception as exc:
        LOGGER.debug("safe_metrics_memory_error error=%s", exc)

    # ── Step 4: Push to profit_engine ─────────────────────────────────────
    try:
        from core.profit_engine import update_profit
        est_revenue = snap.views * snap.engagement_rate * 0.001
        update_profit(
            content_id = content_id,
            niche      = niche,
            revenue    = est_revenue,
            cost       = 0.0,
        )
    except Exception as exc:
        LOGGER.debug("safe_metrics_profit_error error=%s", exc)

    return snap


def safe_collect_all_due(headless: bool = True) -> list[MetricsSnapshot]:
    """
    Collect metrics for all registered tracked posts that are due.
    Falls back to estimates for any that fail.
    Returns list of MetricsSnapshot. Never raises.
    """
    results: list[MetricsSnapshot] = []

    try:
        from execution.metrics_collector_playwright import get_tracked_posts
        posts = get_tracked_posts(status="active")
    except Exception as exc:
        LOGGER.warning("safe_collect_all_get_posts_error error=%s", exc)
        return results

    for post in posts:
        content_id   = post.get("content_id", "")
        post_url     = post.get("post_url", "")
        platform     = post.get("platform", "tiktok")
        niche        = post.get("niche", "entertainment")
        account_id   = post.get("account_id", "")
        published_at = post.get("published_at", 0.0)

        if not content_id:
            continue

        snap = safe_collect(
            content_id   = content_id,
            post_url     = post_url,
            platform     = platform,
            niche        = niche,
            account_id   = account_id,
            published_at = published_at,
            headless     = headless,
        )
        results.append(snap)

    LOGGER.info("safe_collect_all_done count=%d", len(results))
    return results


def get_snapshot(content_id: str) -> MetricsSnapshot:
    """
    Return the latest metrics for a content_id from content_memory.
    If not found, returns zero estimate snapshot.
    """
    snap = MetricsSnapshot(content_id=content_id, estimated=True)
    try:
        from execution.content_memory import get_content
        c = get_content(content_id)
        if c:
            snap.niche    = c.get("niche", "entertainment")
            snap.platform = c.get("platform", "tiktok")
            snap.views    = c.get("views", 0)
            snap.likes    = c.get("likes", 0)
            snap.comments = c.get("comments", 0)
            snap.engagement_rate = c.get("engagement_rate", 0.0)
    except Exception:
        pass
    snap.collected_at = time.time()
    return snap
