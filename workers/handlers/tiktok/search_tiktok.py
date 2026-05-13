"""
Handler: tiktok.search_tiktok
──────────────────────────────
Input payload:
  keywords:     list[str]   – from parent extract_product_info result
  max_results:  int = 50    – max yt-dlp search results per keyword

Output result:
  videos:  list[{url, title, views, likes, duration, thumbnail}]
  ok:      bool

Strategy: yt-dlp `ytsearch<N>:keyword` with --dump-json.
Random delay applied between keyword batches (anti-abuse).
"""

from __future__ import annotations

import json
import logging
import os
import random
from typing import Any

from workers.handlers.tiktok._base import (
    check_already_processed,
    get_ytdlp_path,
    random_jitter,
    resolve_parent_result,
    run_subprocess,
)

LOGGER = logging.getLogger("workers.handlers.tiktok.search_tiktok")

_DEFAULT_MAX_RESULTS = int(os.environ.get("TIKTOK_SEARCH_MAX_RESULTS", "50"))

# yt-dlp extractor targets
# NOTE: ttsearch (TikTok native search) is no longer supported by yt-dlp.
# Fall back to ytsearch (YouTube) which reliably returns video metadata.
_SEARCH_PREFIXES = ["ytsearch"]


async def search_tiktok_handler(payload: dict[str, Any]) -> dict[str, Any]:
    # ── Idempotency guard ─────────────────────────────────────────────────────
    if (cached := check_already_processed(payload)) is not None:
        return cached

    # ── Input resolution ──────────────────────────────────────────────────────
    try:
        keywords: list[str] = resolve_parent_result(payload, "keywords")
    except KeyError:
        keywords_raw = payload.get("keywords")
        if not keywords_raw:
            raise ValueError("search_tiktok requires 'keywords' in payload or parent_results")
        keywords = list(keywords_raw)

    if not keywords:
        raise ValueError("keywords list is empty")

    max_results: int = int(payload.get("max_results", _DEFAULT_MAX_RESULTS))

    LOGGER.info(
        "search_tiktok_start",
        extra={
            "event": "search_tiktok_start",
            "keyword_count": len(keywords),
            "max_results": max_results,
        },
    )

    await random_jitter(1.0, 4.0)

    all_videos: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for keyword in keywords[:5]:  # cap at 5 keywords to avoid excessive scraping
        videos = await _search_keyword(keyword, max_results)
        for v in videos:
            url = v.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_videos.append(v)

        # Random inter-keyword delay
        delay = random.uniform(3.0, 8.0)
        LOGGER.debug(
            "search_keyword_done",
            extra={"event": "search_keyword_done", "keyword": keyword, "hits": len(videos), "next_delay": round(delay, 1)},
        )
        import asyncio
        await asyncio.sleep(delay)

    LOGGER.info(
        "search_tiktok_done",
        extra={"event": "search_tiktok_done", "total_videos": len(all_videos)},
    )

    return {"videos": all_videos, "ok": True}


async def _search_keyword(keyword: str, max_results: int) -> list[dict[str, Any]]:
    """
    Run yt-dlp for one keyword, returning a normalised list of video dicts.
    Tries ttsearch: (TikTok) first; falls back to ytsearch: if it fails.
    """
    for prefix in _SEARCH_PREFIXES:
        query = f"{prefix}{max_results}:{keyword}"
        try:
            ytdlp = get_ytdlp_path()
            stdout, _ = await run_subprocess(
                ytdlp,
                "--flat-playlist",
                "--dump-json",
                "--no-playlist",
                "--skip-download",
                "--quiet",
                query,
                timeout=120.0,
            )
            return _parse_ytdlp_output(stdout)
        except Exception as exc:
            LOGGER.warning(
                "search_prefix_failed",
                extra={"event": "search_prefix_failed", "prefix": prefix, "keyword": keyword, "error": str(exc)[:200]},
            )
            continue

    LOGGER.error(
        "search_all_prefixes_failed",
        extra={"event": "search_all_prefixes_failed", "keyword": keyword},
    )
    return []


def _parse_ytdlp_output(stdout: str) -> list[dict[str, Any]]:
    """Parse newline-delimited JSON from yt-dlp --dump-json."""
    videos: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        url = data.get("webpage_url") or data.get("url") or ""
        if not url:
            continue

        views = int(data.get("view_count") or 0)
        likes = int(data.get("like_count") or 0)
        duration = float(data.get("duration") or 0.0)
        title = str(data.get("title") or "").strip()
        thumbnail = str(data.get("thumbnail") or "").strip()

        videos.append(
            {
                "url": url,
                "title": title,
                "views": views,
                "likes": likes,
                "duration": round(duration, 1),
                "thumbnail": thumbnail,
            }
        )
    return videos
