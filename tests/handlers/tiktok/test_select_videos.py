"""
Unit tests for tiktok.select_videos handler.

These tests are pure-Python (no subprocess, no DB, no API calls).
Updated for the advanced viral-scoring engine:
  - engagement_rate formula
  - duration window 6–30 s
  - author de-duplication
  - random pool jitter (top_n clamped 3–5)
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import patch


@pytest.fixture()
def sample_videos():
    # Durations adjusted to the new 6–30 s window
    return [
        {"url": "https://tiktok.com/@alice/video/1", "title": "A", "views": 50000,  "likes": 2000, "duration": 20.0},
        {"url": "https://tiktok.com/@bob/video/2",   "title": "B", "views": 8000,   "likes": 1000, "duration": 15.0},  # below min_views
        {"url": "https://tiktok.com/@alice/video/3", "title": "C", "views": 20000,  "likes": 300,  "duration": 10.0},  # 2nd alice video
        {"url": "https://tiktok.com/@carol/video/4", "title": "D", "views": 15000,  "likes": 800,  "duration": 8.0},
        {"url": "https://tiktok.com/@dave/video/5",  "title": "E", "views": 100000, "likes": 5000, "duration": 25.0},
        {"url": "https://tiktok.com/@eve/video/6",   "title": "F", "views": 30000,  "likes": 1200, "duration": 12.0},
        {"url": "https://tiktok.com/@alice/video/7", "title": "G", "views": 40000,  "likes": 1500, "duration": 18.0},  # 3rd alice — should be capped
    ]


@pytest.mark.asyncio
async def test_select_videos_basic_filter(sample_videos):
    from workers.handlers.tiktok.select_videos import select_videos_handler

    payload = {
        "videos": sample_videos,
        "min_views": 10000,
        "min_duration": 6.0,
        "max_duration": 30.0,
        "top_n": 3,
    }
    result = await select_videos_handler(payload)

    assert result["ok"] is True
    selected = result["selected_videos"]
    assert 1 <= len(selected) <= 3
    # All selected must meet thresholds
    for v in selected:
        assert v["views"] >= 10000
        assert 6.0 <= v["duration"] <= 30.0


@pytest.mark.asyncio
async def test_select_videos_sorted_by_score(sample_videos):
    from workers.handlers.tiktok.select_videos import select_videos_handler

    payload = {
        "videos": sample_videos,
        "min_views": 10000,
        "min_duration": 6.0,
        "max_duration": 30.0,
        "top_n": 5,
    }
    result = await select_videos_handler(payload)
    scores = [v["score"] for v in result["selected_videos"]]
    assert scores == sorted(scores, reverse=True), "Videos should be sorted by score descending"


@pytest.mark.asyncio
async def test_select_videos_all_filtered_out():
    from workers.handlers.tiktok.select_videos import select_videos_handler

    payload = {
        "videos": [
            {"url": "https://tiktok.com/@x/video/1", "views": 5, "likes": 1, "duration": 5.0},
        ],
        "min_views": 10000,
        "min_duration": 6.0,
        "max_duration": 30.0,
        "top_n": 3,
    }
    with pytest.raises(RuntimeError, match="No videos passed filters"):
        await select_videos_handler(payload)


@pytest.mark.asyncio
async def test_select_videos_allows_unknown_duration():
    from workers.handlers.tiktok.select_videos import select_videos_handler

    payload = {
        "videos": [
            {"url": "https://tiktok.com/@x/video/1", "views": 50000, "likes": 2500, "duration": 0},
            {"url": "https://tiktok.com/@y/video/2", "views": 60000, "likes": 3000},
        ],
        "min_views": 10000,
        "min_duration": 6.0,
        "max_duration": 30.0,
        "top_n": 3,
    }

    result = await select_videos_handler(payload)

    assert result["ok"] is True
    assert len(result["selected_videos"]) == 2
    assert {video["duration"] for video in result["selected_videos"]} == {0.0}


@pytest.mark.asyncio
async def test_select_videos_idempotency():
    from workers.handlers.tiktok.select_videos import select_videos_handler

    cached = {"selected_videos": [{"url": "x"}], "ok": True}
    payload = {"_idempotent_result": cached}
    result = await select_videos_handler(payload)
    assert result == cached


@pytest.mark.asyncio
async def test_select_videos_filter_stats(sample_videos):
    from workers.handlers.tiktok.select_videos import select_videos_handler

    payload = {
        "videos": sample_videos,
        "min_views": 10000,
        "min_duration": 6.0,
        "max_duration": 30.0,
        "top_n": 5,
    }
    result = await select_videos_handler(payload)
    stats = result["filter_stats"]
    assert stats["total_input"] == len(sample_videos)
    assert stats["passed_filter"] <= stats["total_input"]
    assert "author_deduped" in stats
    assert stats["selected"] == len(result["selected_videos"])


@pytest.mark.asyncio
async def test_select_videos_author_cap():
    """Alice has 3 videos but max_per_author=2, so only 2 should be selected."""
    from workers.handlers.tiktok.select_videos import select_videos_handler

    videos = [
        {"url": f"https://tiktok.com/@alice/video/{i}", "views": 50000, "likes": 2000, "duration": 15.0}
        for i in range(3)
    ]
    payload = {
        "videos": videos,
        "min_views": 10000,
        "min_duration": 6.0,
        "max_duration": 30.0,
        "top_n": 5,
        "max_per_author": 2,
    }
    result = await select_videos_handler(payload)
    alice_count = sum(1 for v in result["selected_videos"] if "alice" in v.get("author", ""))
    assert alice_count <= 2


@pytest.mark.asyncio
async def test_select_videos_engagement_rate_in_output(sample_videos):
    """Each selected video should carry engagement_rate and score fields."""
    from workers.handlers.tiktok.select_videos import select_videos_handler

    payload = {
        "videos": sample_videos,
        "min_views": 10000,
        "min_duration": 6.0,
        "max_duration": 30.0,
        "top_n": 3,
    }
    result = await select_videos_handler(payload)
    for v in result["selected_videos"]:
        assert "engagement_rate" in v
        assert "score" in v
        assert "recency_score" in v
        assert "breakdown" in v
        assert v["engagement_rate"] >= 0.0
        assert v["score"] >= 0.0
        assert isinstance(v["breakdown"], dict)
