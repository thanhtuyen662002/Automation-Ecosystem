"""
Handler: tiktok.select_videos
───────────────────────────────
Input payload:
  min_views:           int   = env TIKTOK_MIN_VIEWS        (default 10 000)
  min_duration:        float = env TIKTOK_MIN_DURATION     (default 5.0 s)
  max_duration:        float = env TIKTOK_MAX_DURATION     (default 40.0 s)
  min_engagement_rate: float = (default 0.02)
  top_n:               int   = env TIKTOK_TOP_N             (default 5)
  max_per_author:      int   = env TIKTOK_MAX_PER_AUTHOR   (default 2)

Reads 'videos' from parent search_tiktok result (via parent_results or payload).

Viral scoring formula:
  engagement_rate = likes / max(views, 1)
  recency_boost = max(0, 1 - days_old / 7)
  
  views_norm = (views - min) / (max - min + 1)
  likes_norm = (likes - min) / (max - min + 1)
  er_norm = (er - min) / (max - min + 1)
  
  score = (views_norm * 0.4) + (likes_norm * 0.2) + (er_norm * 0.2) + (recency_boost * 0.2)
  score += 0.1 (if ER >= 90th percentile)
  score += random(0, 0.05)
  score *= hook_multiplier (1.3 max)

Output result:
  selected_videos: list[{url, title, author, views, likes, duration,
                          engagement_rate, recency_score, score, breakdown}]
  filter_stats:    {total_input, passed_filter, author_deduped, selected}
  ok:              bool
"""

from __future__ import annotations

import datetime
import logging
import os
import random
import re
from typing import Any

from workers.handlers.tiktok._base import (
    check_already_processed,
    random_seed,
    resolve_parent_result,
)

LOGGER = logging.getLogger("workers.handlers.tiktok.select_videos")

_DEFAULT_MIN_VIEWS = int(os.environ.get("TIKTOK_MIN_VIEWS", "10000"))
_DEFAULT_MIN_DURATION = float(os.environ.get("TIKTOK_MIN_DURATION", "5.0"))
_DEFAULT_MAX_DURATION = float(os.environ.get("TIKTOK_MAX_DURATION", "40.0"))
_DEFAULT_TOP_N = int(os.environ.get("TIKTOK_TOP_N", "5"))
_DEFAULT_MAX_PER_AUTHOR = int(os.environ.get("TIKTOK_MAX_PER_AUTHOR", "2"))
_DEFAULT_MIN_ER = 0.02

# ── Viral Hooks ───────────────────────────────────────────────────────────────

_HOOK_WORDS = [
    "không thể tin", "bất ngờ", "sự thật", "tại sao", "lý do",
    "cú sốc", "đừng mua", "không biết", "bí mật", "cảnh báo",
    "kết quả", "chưa từng", "kinh ngạc", "wow", "omg", "shock",
    "có thể bạn chưa biết", "ai ngờ", "cái kết", "hết hồn"
]

# ── Author extraction ─────────────────────────────────────────────────────────

_TIKTOK_AUTHOR_RE = re.compile(r"tiktok\.com/@([^/?&#]+)", re.IGNORECASE)


def _extract_author(video: dict[str, Any]) -> str:
    for field in ("uploader_id", "uploader"):
        val = video.get(field, "")
        if val:
            return str(val).lower().strip()
    url = video.get("url", "")
    m = _TIKTOK_AUTHOR_RE.search(url)
    if m:
        return m.group(1).lower()
    return "__unknown__"


# ── Scoring Helpers ───────────────────────────────────────────────────────────

def _calculate_days_old(upload_date: str) -> float:
    if not upload_date or len(upload_date) != 8:
        return 14.0
    try:
        dt = datetime.datetime.strptime(upload_date, "%Y%m%d")
        delta = datetime.datetime.now() - dt
        return max(0.0, delta.days + (delta.seconds / 86400.0))
    except Exception:
        return 14.0


# ── Handler ───────────────────────────────────────────────────────────────────

async def select_videos_handler(payload: dict[str, Any]) -> dict[str, Any]:
    # ── Idempotency guard ─────────────────────────────────────────────────────
    if (cached := check_already_processed(payload)) is not None:
        return cached

    # ── Resolve input videos ──────────────────────────────────────────────────
    try:
        videos: list[dict[str, Any]] = list(resolve_parent_result(payload, "videos"))
    except KeyError:
        videos_raw = payload.get("videos")
        if not videos_raw:
            raise ValueError("select_videos requires 'videos' in payload or parent_results")
        videos = list(videos_raw)

    # ── Thresholds ────────────────────────────────────────────────────────────
    min_views: int = int(payload.get("min_views", _DEFAULT_MIN_VIEWS))
    min_duration: float = float(payload.get("min_duration", _DEFAULT_MIN_DURATION))
    max_duration: float = float(payload.get("max_duration", _DEFAULT_MAX_DURATION))
    min_engagement_rate: float = float(payload.get("min_engagement_rate", _DEFAULT_MIN_ER))
    top_n: int = max(3, min(10, int(payload.get("top_n", _DEFAULT_TOP_N))))
    max_per_author: int = int(payload.get("max_per_author", _DEFAULT_MAX_PER_AUTHOR))

    seed = random_seed()
    random.seed(seed)

    LOGGER.info(
        "select_videos_start",
        extra={
            "event": "select_videos_start",
            "total_input": len(videos),
            "min_views": min_views,
            "min_duration": min_duration,
            "max_duration": max_duration,
            "min_er": min_engagement_rate,
            "top_n": top_n,
            "max_per_author": max_per_author,
            "seed": seed,
        },
    )

    # ── Step 1: Basic hard filters & Pre-compute stats ────────────────────────
    valid_videos = []
    for v in videos:
        views = max(int(v.get("views", 0)), 0)
        likes = max(int(v.get("likes", 0)), 0)
        er = likes / max(views, 1)

        if views < min_views:
            continue
        if not (min_duration <= v.get("duration", 0.0) <= max_duration):
            continue
        if not v.get("url", ""):
            continue
        # Only apply ER filter when we have actual likes data.
        # When likes=0 the source (e.g. yt-dlp --flat-playlist) may not return
        # like_count, so we treat the ER as unknown and let the video pass.
        if likes > 0 and er < min_engagement_rate:
            continue

        days_old = _calculate_days_old(v.get("upload_date", ""))
        
        v["_raw_views"] = views
        v["_raw_likes"] = likes
        v["_raw_er"] = er
        v["_recency_boost"] = max(0.0, 1.0 - (days_old / 7.0))
        v["_days_old"] = days_old
        valid_videos.append(v)

    passed_filter_count = len(valid_videos)

    if not valid_videos:
        raise RuntimeError(
            f"No videos passed filters (tried {len(videos)} candidates). "
            "Lower min_views / min_engagement_rate or adjust duration thresholds."
        )

    # ── Step 2: Deduplicate by URL ────────────────────────────────────────────
    seen_urls: set[str] = set()
    filtered: list[dict[str, Any]] = []
    for v in valid_videos:
        url = v["url"]
        if url not in seen_urls:
            seen_urls.add(url)
            filtered.append(v)

    # ── Step 3: Compute batch relative ranges & percentiles ───────────────────
    min_views_b = min(v["_raw_views"] for v in filtered)
    max_views_b = max(v["_raw_views"] for v in filtered)
    min_likes_b = min(v["_raw_likes"] for v in filtered)
    max_likes_b = max(v["_raw_likes"] for v in filtered)
    min_er_b = min(v["_raw_er"] for v in filtered)
    max_er_b = max(v["_raw_er"] for v in filtered)

    er_sorted = sorted(v["_raw_er"] for v in filtered)
    p90_idx = int(len(er_sorted) * 0.9)
    er_p90 = er_sorted[p90_idx] if er_sorted else 0.0

    # ── Fetch Historical Signal ───────────────────────────────────────────────
    keyword = payload.get("keyword") or payload.get("query")
    db_url = os.environ.get("DATABASE_URL", "")
    
    historical_boost = 0.0
    if db_url and keyword:
        from database.analytics import get_historical_signal
        signals = await get_historical_signal(db_url, keyword)
        hist_score = signals.get("historical_score", 0.0)
        glob_avg = signals.get("global_avg", 0.0)
        boost = (hist_score - glob_avg) * 0.3
        historical_boost = max(-0.2, min(0.2, boost))

    # ── Step 4: Relative Scoring ──────────────────────────────────────────────
    for v in filtered:
        views = v["_raw_views"]
        likes = v["_raw_likes"]
        er = v["_raw_er"]
        recency = v["_recency_boost"]

        # Relative normalization per batch
        views_norm = (views - min_views_b) / (max_views_b - min_views_b + 1)
        likes_norm = (likes - min_likes_b) / (max_likes_b - min_likes_b + 1)
        er_norm = (er - min_er_b) / (max_er_b - min_er_b + 1)

        base_score = (views_norm * 0.4) + (likes_norm * 0.2) + (er_norm * 0.2) + (recency * 0.2)
        
        # Apply Historical Boost
        base_score += historical_boost

        # Outlier boost (> 90th percentile)
        outlier_boost = 0.1 if er >= er_p90 else 0.0
        base_score += outlier_boost

        # Small random noise (anti-duplication)
        noise = random.uniform(0.0, 0.05)
        base_score += noise

        # Hook multiplier (clamp at 1.3)
        title_desc = f"{v.get('title', '')} {v.get('description', '')}".lower()
        has_hook = any(w in title_desc for w in _HOOK_WORDS)
        hook_multiplier = 1.3 if has_hook else 1.0

        final_score = base_score * hook_multiplier

        v["engagement_rate"] = round(er, 6)
        v["recency_score"] = round(recency, 4)
        v["score"] = round(final_score, 4)
        v["breakdown"] = {
            "views_norm": round(views_norm, 4),
            "likes_norm": round(likes_norm, 4),
            "er_norm": round(er_norm, 4),
            "recency_boost": round(recency, 4),
            "historical_boost": round(historical_boost, 4),
            "outlier_boost": outlier_boost,
            "noise": round(noise, 4),
            "hook_multiplier": hook_multiplier,
            "days_old": round(v["_days_old"], 1),
        }

    # ── Step 5: Author bias cap (max N per author) ────────────────────────────
    # Sort by score first to keep the best from each author
    filtered.sort(key=lambda v: v["score"], reverse=True)
    
    author_counts: dict[str, int] = {}
    author_capped: list[dict[str, Any]] = []
    for v in filtered:
        author = _extract_author(v)
        v["_author"] = author  # stash for output
        count = author_counts.get(author, 0)
        if count < max_per_author:
            author_counts[author] = count + 1
            author_capped.append(v)

    author_deduped_count = len(author_capped)
    filtered = author_capped

    if not filtered:
        raise RuntimeError("All candidates eliminated by author de-duplication.")

    # ── Step 6: Random pick from top pool ─────────────────────────────────────
    pool_size = min(len(filtered), max(top_n * 2, top_n + 3))
    pool = filtered[:pool_size]
    selected = random.sample(pool, min(top_n, len(pool)))

    # Re-sort selected by score so the output is ranked high→low
    selected.sort(key=lambda v: v["score"], reverse=True)

    # Clean up intermediate keys
    for v in selected:
        for k in ["_raw_views", "_raw_likes", "_raw_er", "_recency_boost", "_days_old"]:
            v.pop(k, None)

    # ── Output ────────────────────────────────────────────────────────────────
    result = {
        "selected_videos": [
            {
                "url": v["url"],
                "title": v.get("title", ""),
                "author": v.get("_author", "__unknown__"),
                "views": v.get("views", 0),
                "likes": v.get("likes", 0),
                "duration": v.get("duration", 0.0),
                "engagement_rate": v["engagement_rate"],
                "recency_score": v["recency_score"],
                "score": v["score"],
                "breakdown": v["breakdown"],
            }
            for v in selected
        ],
        "filter_stats": {
            "total_input": len(videos),
            "passed_filter": passed_filter_count,
            "author_deduped": author_deduped_count,
            "selected": len(selected),
        },
        "ok": True,
    }

    LOGGER.info(
        "select_videos_done",
        extra={
            "event": "select_videos_done",
            "passed_filter": passed_filter_count,
            "author_deduped": author_deduped_count,
            "selected": len(selected),
            "top_score": selected[0]["score"] if selected else 0,
        },
    )
    return result
