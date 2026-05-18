"""
Unit tests for tiktok.select_videos handler.

These tests are pure-Python (no subprocess, no DB, no API calls).
Updated for the advanced viral-scoring engine:
  - engagement_rate formula
  - duration window 6–30 s
  - author de-duplication
  - random pool jitter (top_n clamped 1-10)
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import patch


def _valid_video(idx: int, *, author: str | None = None) -> dict:
    author = author or f"author{idx}"
    return {
        "url": f"https://tiktok.com/@{author}/video/{idx}",
        "title": f"Video {idx}",
        "author": author,
        "uploader": f"uploader_{idx}",
        "uploader_id": f"uploader_id_{idx}",
        "views": 50000 + idx,
        "likes": 1000 + idx,
        "duration": 0,
    }


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
async def test_select_videos_keeps_mobile_app_candidates_without_views():
    from workers.handlers.tiktok.select_videos import select_videos_handler

    payload = {
        "videos": [
            {
                "url": "https://www.tiktok.com/@shop/video/123",
                "title": "Shop demo",
                "views": 0,
                "likes": 0,
                "duration": 0,
                "source": "mobile_tiktok_shop",
                "requires_mobile_app": True,
            },
        ],
        "min_views": 10000,
        "min_duration": 6.0,
        "max_duration": 30.0,
        "top_n": 1,
    }

    result = await select_videos_handler(payload)

    assert result["ok"] is True
    assert result["selected_videos"][0]["requires_mobile_app"] is True


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
        "max_per_author": 3,
    }

    result = await select_videos_handler(payload)

    assert result["ok"] is True
    assert len(result["selected_videos"]) == 2
    assert {video["duration"] for video in result["selected_videos"]} == {0.0}


@pytest.mark.asyncio
async def test_select_videos_respects_min_likes_when_known():
    from workers.handlers.tiktok.select_videos import select_videos_handler

    payload = {
        "videos": [
            {"url": "https://tiktok.com/@x/video/1", "views": 100000, "likes": 100, "duration": 0},
            {"url": "https://tiktok.com/@y/video/2", "views": 100000, "likes": 1000, "duration": 0},
            {"url": "https://tiktok.com/@z/video/3", "views": 100000, "likes": 0, "duration": 0},
        ],
        "min_views": 10000,
        "min_likes": 500,
        "min_engagement_rate": 0.0,
        "min_duration": 6.0,
        "max_duration": 30.0,
        "top_n": 3,
        "max_per_author": 3,
    }

    result = await select_videos_handler(payload)
    selected_urls = {video["url"] for video in result["selected_videos"]}

    assert "https://tiktok.com/@x/video/1" not in selected_urls
    assert "https://tiktok.com/@y/video/2" in selected_urls
    assert "https://tiktok.com/@z/video/3" in selected_urls


@pytest.mark.asyncio
async def test_select_videos_respects_top_n_one():
    from workers.handlers.tiktok.select_videos import select_videos_handler

    result = await select_videos_handler(
        {
            "videos": [_valid_video(1), _valid_video(2), _valid_video(3)],
            "top_n": 1,
            "min_views": 1000,
            "min_likes": 10,
            "min_engagement_rate": 0.0,
        }
    )

    assert len(result["selected_videos"]) == 1


@pytest.mark.asyncio
async def test_select_videos_respects_top_n_two():
    from workers.handlers.tiktok.select_videos import select_videos_handler

    result = await select_videos_handler(
        {
            "videos": [_valid_video(1), _valid_video(2), _valid_video(3)],
            "top_n": 2,
            "min_views": 1000,
            "min_likes": 10,
            "min_engagement_rate": 0.0,
        }
    )

    assert len(result["selected_videos"]) == 2


@pytest.mark.asyncio
async def test_select_videos_clamps_top_n_above_ten():
    from workers.handlers.tiktok.select_videos import select_videos_handler

    result = await select_videos_handler(
        {
            "videos": [_valid_video(idx) for idx in range(1, 13)],
            "top_n": 20,
            "min_views": 1000,
            "min_likes": 10,
            "min_engagement_rate": 0.0,
            "max_per_author": 20,
        }
    )

    assert len(result["selected_videos"]) == 10
    assert result["filter_stats"]["selected"] == 10


@pytest.mark.asyncio
async def test_select_videos_preserves_search_metadata():
    from workers.handlers.tiktok.select_videos import select_videos_handler

    videos = [
        {
            "url": f"https://tiktok.com/@a/video/{idx}",
            "title": "kem chong nang",
            "uploader": "uploader_a",
            "uploader_id": "uploader_id_a",
            "description": "caption here",
            "thumbnail": "https://example.com/thumb.jpg",
            "keyword": "kem ch\u1ed1ng n\u1eafng",
            "source": "tiktok_ads_power_search",
            "scraped_at": 123,
            "views": 100000 + idx,
            "likes": 1000 + idx,
            "comments": 50 + idx,
            "duration": 0,
            "relevance_score": 0.9,
            "matched_keyword_terms": ["kem", "n\u1eafng"],
        }
        for idx in range(1, 4)
    ]
    payload = {
        "videos": videos,
        "min_views": 10000,
        "min_likes": 500,
        "min_engagement_rate": 0.0,
        "min_duration": 6.0,
        "max_duration": 30.0,
        "top_n": 3,
        "max_per_author": 3,
    }

    result = await select_videos_handler(payload)
    selected = result["selected_videos"]

    assert len(selected) == 3
    for video in selected:
        assert video["description"] == "caption here"
        assert video["author"] == "uploader_id_a"
        assert video["uploader"] == "uploader_a"
        assert video["uploader_id"] == "uploader_id_a"
        assert video["thumbnail"] == "https://example.com/thumb.jpg"
        assert video["keyword"] == "kem ch\u1ed1ng n\u1eafng"
        assert video["source"] == "tiktok_ads_power_search"
        assert video["scraped_at"] == 123
        assert video["relevance_score"] == 0.9
        assert video["matched_keyword_terms"] == ["kem", "n\u1eafng"]
        assert video["comments"] >= 51
        assert video["url"]


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
