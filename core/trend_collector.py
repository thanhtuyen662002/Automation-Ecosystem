"""
core/trend_collector.py — Trending video data source.

Tries real TikTok scraping via Playwright first.
Falls back to mock data on error or when TIKTOK_SCRAPER_ENABLED=false.

Public API:
    fetch_trending(limit, keyword) -> list[dict]

Each dict contains:
    content_id, caption, hook_text, view_count, engagement_metrics,
    niche, created_at, source, [optional: video_url, author, likes, comments]
"""
from __future__ import annotations

import logging
import os
import random
import time
from typing import Any, Dict, List

LOGGER = logging.getLogger("core.trend_collector")

# Set TIKTOK_SCRAPER_ENABLED=false in .env to force mock mode
_SCRAPER_ENABLED = os.environ.get("TIKTOK_SCRAPER_ENABLED", "true").lower() not in (
    "false", "0", "no", "off"
)


def _normalize_real(item: dict[str, Any], index: int) -> dict[str, Any]:
    """Convert a raw tiktok_scraper dict → pipeline-compatible trend dict."""
    views       = item.get("views", 0)
    likes       = item.get("likes", 0)
    comments    = item.get("comments", 0)
    caption     = item.get("caption", "")

    # Derived engagement metrics (safe even when counts are 0)
    eng_rate  = round(likes / views, 4) if views > 0 else 0.05
    share_est = round(comments / views, 4) if views > 0 else 0.02

    return {
        "content_id": f"real_{item.get('keyword', 'kw')}_{index}",
        "video_url":  item.get("video_url", ""),
        "author":     item.get("author", ""),
        "caption":    caption,
        "hook_text":  caption[:120] if caption else "",   # first 120 chars as hook
        "view_count": views,
        "likes":      likes,
        "comments":   comments,
        "thumbnail":  item.get("thumbnail", ""),
        "engagement_metrics": {
            "engagement_rate": min(1.0, eng_rate),
            "share_rate":      min(1.0, share_est),
            "save_rate":       round(eng_rate * 0.3, 4),  # estimated
        },
        "niche":      item.get("keyword", "general"),
        "created_at": item.get("scraped_at", int(time.time())),
        "source":     "tiktok_real",
    }


def _mock_items(limit: int, keyword: str) -> List[Dict[str, Any]]:
    """Return deterministic mock trending items as fallback."""
    LOGGER.debug("trend_collector_mock keyword=%s limit=%d", keyword, limit)
    return [
        {
            "content_id": f"mock_{keyword}_{i}",
            "video_url":  "",
            "author":     f"creator_{i}",
            "caption":    f"{keyword} viral content example #{i}",
            "hook_text":  f"You won't believe this {keyword} hack",
            "view_count": random.randint(10_000, 500_000),
            "likes":      random.randint(500, 50_000),
            "comments":   random.randint(50, 5_000),
            "thumbnail":  "",
            "engagement_metrics": {
                "engagement_rate": random.uniform(0.03, 0.12),
                "share_rate":      random.uniform(0.01, 0.08),
                "save_rate":       random.uniform(0.01, 0.05),
            },
            "niche":      keyword,
            "created_at": int(time.time()),
            "source":     "mock",
        }
        for i in range(limit)
    ]


def fetch_trending(
    limit:   int = 20,
    keyword: str = "general",
) -> List[Dict[str, Any]]:
    """
    Fetch trending TikTok videos for *keyword*.

    Priority:
      1. Real Playwright scraper (when TIKTOK_SCRAPER_ENABLED=true, default)
      2. Mock fallback (on error or disabled)

    Args:
        limit:   Number of videos to return
        keyword: Search keyword (injected by Gemini / trend_agent upstream)

    Returns:
        List of pipeline-compatible trend dicts.
    """
    if _SCRAPER_ENABLED:
        try:
            from core.tiktok_scraper import scrape_keyword_sync

            raw = scrape_keyword_sync(
                keyword   = keyword,
                limit     = limit,
                min_views = int(os.environ.get("TIKTOK_MIN_VIEWS", "10000")),
                headless  = True,
            )

            if raw:
                results = [_normalize_real(item, i) for i, item in enumerate(raw)]
                LOGGER.info(
                    "trend_collector_real keyword=%s returned=%d",
                    keyword, len(results),
                )
                return results

            LOGGER.warning(
                "trend_collector_real_empty keyword=%s → falling back to mock",
                keyword,
            )

        except Exception as exc:
            LOGGER.warning(
                "trend_collector_scraper_error keyword=%s error=%s "
                "→ falling back to mock",
                keyword, exc,
            )

    return _mock_items(limit, keyword)
