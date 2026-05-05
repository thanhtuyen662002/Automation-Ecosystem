"""
Handler: tiktok.select_videos
───────────────────────────────
Input payload:
  min_views:    int   = env TIKTOK_MIN_VIEWS  (default 10000)
  min_likes:    int   = env TIKTOK_MIN_LIKES  (default 500)
  min_duration: float = env TIKTOK_MIN_DURATION (default 15.0 seconds)
  max_duration: float = env TIKTOK_MAX_DURATION (default 180.0 seconds)
  top_n:        int   = env TIKTOK_TOP_N       (default 5)

Reads 'videos' from parent search_tiktok result (via parent_results or payload).

Output result:
  selected_videos: list[{url, title, views, likes, duration, score}]
  filter_stats:    {total_input, passed_filter, selected}
  ok:              bool
"""

from __future__ import annotations

import logging
import os
import random
from typing import Any

from workers.handlers.tiktok._base import (
    check_already_processed,
    resolve_parent_result,
)

LOGGER = logging.getLogger("workers.handlers.tiktok.select_videos")

_DEFAULT_MIN_VIEWS = int(os.environ.get("TIKTOK_MIN_VIEWS", "10000"))
_DEFAULT_MIN_LIKES = int(os.environ.get("TIKTOK_MIN_LIKES", "500"))
_DEFAULT_MIN_DURATION = float(os.environ.get("TIKTOK_MIN_DURATION", "15.0"))
_DEFAULT_MAX_DURATION = float(os.environ.get("TIKTOK_MAX_DURATION", "180.0"))
_DEFAULT_TOP_N = int(os.environ.get("TIKTOK_TOP_N", "5"))


async def select_videos_handler(payload: dict[str, Any]) -> dict[str, Any]:
    # ── Idempotency guard ─────────────────────────────────────────────────────
    if (cached := check_already_processed(payload)) is not None:
        return cached

    # ── Resolve input videos from parent result ───────────────────────────────
    try:
        videos: list[dict[str, Any]] = list(resolve_parent_result(payload, "videos"))
    except KeyError:
        videos_raw = payload.get("videos")
        if not videos_raw:
            raise ValueError("select_videos requires 'videos' in payload or parent_results")
        videos = list(videos_raw)

    # ── Thresholds ────────────────────────────────────────────────────────────
    min_views: int = int(payload.get("min_views", _DEFAULT_MIN_VIEWS))
    min_likes: int = int(payload.get("min_likes", _DEFAULT_MIN_LIKES))
    min_duration: float = float(payload.get("min_duration", _DEFAULT_MIN_DURATION))
    max_duration: float = float(payload.get("max_duration", _DEFAULT_MAX_DURATION))
    top_n: int = int(payload.get("top_n", _DEFAULT_TOP_N))

    LOGGER.info(
        "select_videos_start",
        extra={
            "event": "select_videos_start",
            "total_input": len(videos),
            "min_views": min_views,
            "min_likes": min_likes,
            "min_duration": min_duration,
            "max_duration": max_duration,
            "top_n": top_n,
        },
    )

    # ── Filter ────────────────────────────────────────────────────────────────
    filtered: list[dict[str, Any]] = [
        v for v in videos
        if (
            v.get("views", 0) >= min_views
            and v.get("likes", 0) >= min_likes
            and min_duration <= v.get("duration", 0.0) <= max_duration
            and v.get("url", "")
        )
    ]

    if not filtered:
        LOGGER.warning(
            "select_videos_no_candidates",
            extra={
                "event": "select_videos_no_candidates",
                "total_input": len(videos),
                "thresholds": {"min_views": min_views, "min_likes": min_likes},
            },
        )
        raise RuntimeError(
            f"No videos passed filters (tried {len(videos)} candidates). "
            "Lower min_views / min_likes thresholds or broaden keywords."
        )

    # ── Score: weighted engagement ────────────────────────────────────────────
    # Score = 0.6 * normalised_views + 0.4 * normalised_likes
    # Normalise within this batch so both axes contribute fairly.
    max_views = max(v.get("views", 1) for v in filtered) or 1
    max_likes = max(v.get("likes", 1) for v in filtered) or 1

    for v in filtered:
        norm_views = v.get("views", 0) / max_views
        norm_likes = v.get("likes", 0) / max_likes
        v["score"] = round(0.6 * norm_views + 0.4 * norm_likes, 6)

    # Sort by score descending; shuffle ties randomly for anti-duplication
    filtered.sort(key=lambda v: (v["score"], random.random()), reverse=True)

    selected = filtered[:top_n]

    # Clean up internal score field (keep it for transparency)
    result = {
        "selected_videos": [
            {
                "url": v["url"],
                "title": v.get("title", ""),
                "views": v.get("views", 0),
                "likes": v.get("likes", 0),
                "duration": v.get("duration", 0.0),
                "score": v["score"],
            }
            for v in selected
        ],
        "filter_stats": {
            "total_input": len(videos),
            "passed_filter": len(filtered),
            "selected": len(selected),
        },
        "ok": True,
    }

    LOGGER.info(
        "select_videos_done",
        extra={
            "event": "select_videos_done",
            "passed_filter": len(filtered),
            "selected": len(selected),
        },
    )
    return result
