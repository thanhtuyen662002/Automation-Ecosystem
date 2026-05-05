"""
Unit tests for tiktok.select_videos handler.

These tests are pure-Python (no subprocess, no DB, no API calls).
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import patch


@pytest.fixture()
def sample_videos():
    return [
        {"url": "https://tiktok.com/v1", "title": "A", "views": 50000, "likes": 2000, "duration": 30.0},
        {"url": "https://tiktok.com/v2", "title": "B", "views": 8000,  "likes": 1000, "duration": 60.0},  # below min_views
        {"url": "https://tiktok.com/v3", "title": "C", "views": 20000, "likes": 300,  "duration": 45.0},  # below min_likes
        {"url": "https://tiktok.com/v4", "title": "D", "views": 15000, "likes": 800,  "duration": 10.0},  # too short
        {"url": "https://tiktok.com/v5", "title": "E", "views": 100000,"likes": 5000, "duration": 90.0},
        {"url": "https://tiktok.com/v6", "title": "F", "views": 30000, "likes": 1200, "duration": 120.0},
    ]


@pytest.mark.asyncio
async def test_select_videos_basic_filter(sample_videos):
    from workers.handlers.tiktok.select_videos import select_videos_handler

    payload = {
        "videos": sample_videos,
        "min_views": 10000,
        "min_likes": 500,
        "min_duration": 15.0,
        "max_duration": 180.0,
        "top_n": 3,
    }
    result = await select_videos_handler(payload)

    assert result["ok"] is True
    selected = result["selected_videos"]
    assert len(selected) <= 3
    # All selected must meet thresholds
    for v in selected:
        assert v["views"] >= 10000
        assert v["likes"] >= 500
        assert 15.0 <= v["duration"] <= 180.0


@pytest.mark.asyncio
async def test_select_videos_sorted_by_score(sample_videos):
    from workers.handlers.tiktok.select_videos import select_videos_handler

    payload = {
        "videos": sample_videos,
        "min_views": 10000,
        "min_likes": 500,
        "min_duration": 15.0,
        "max_duration": 180.0,
        "top_n": 10,
    }
    result = await select_videos_handler(payload)
    scores = [v["score"] for v in result["selected_videos"]]
    assert scores == sorted(scores, reverse=True), "Videos should be sorted by score descending"


@pytest.mark.asyncio
async def test_select_videos_all_filtered_out():
    from workers.handlers.tiktok.select_videos import select_videos_handler

    payload = {
        "videos": [
            {"url": "https://tiktok.com/v1", "views": 5, "likes": 1, "duration": 5.0},
        ],
        "min_views": 10000,
        "min_likes": 500,
        "min_duration": 15.0,
        "max_duration": 180.0,
        "top_n": 3,
    }
    with pytest.raises(RuntimeError, match="No videos passed filters"):
        await select_videos_handler(payload)


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
        "min_likes": 500,
        "min_duration": 15.0,
        "max_duration": 180.0,
        "top_n": 5,
    }
    result = await select_videos_handler(payload)
    stats = result["filter_stats"]
    assert stats["total_input"] == len(sample_videos)
    assert stats["passed_filter"] <= stats["total_input"]
    assert stats["selected"] == len(result["selected_videos"])
