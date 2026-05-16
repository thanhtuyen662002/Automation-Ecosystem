from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest


def test_normalize_tiktok_search_items_required_schema():
    from core.tiktok_search_extractor import normalize_tiktok_search_items

    videos = normalize_tiktok_search_items(
        [
            {
                "video_url": "https://www.tiktok.com/@alice/video/123",
                "caption": "A useful product demo",
                "views_text": "1.2M",
                "likes_text": "45K",
                "comments_text": "321",
                "thumbnail": "https://p16-sign-va.tiktokcdn.com/example.jpg",
            }
        ],
        "product demo",
        source="tiktok_ads_power_search",
    )

    assert videos == [
        {
            "url": "https://www.tiktok.com/@alice/video/123",
            "title": "A useful product demo",
            "description": "A useful product demo",
            "author": "alice",
            "uploader": "alice",
            "uploader_id": "alice",
            "views": 1_200_000,
            "likes": 45_000,
            "comments": 321,
            "duration": 0.0,
            "thumbnail": "https://p16-sign-va.tiktokcdn.com/example.jpg",
            "keyword": "product demo",
            "source": "tiktok_ads_power_search",
            "scraped_at": videos[0]["scraped_at"],
        }
    ]


def test_normalize_tiktok_search_items_filters_photos_by_default():
    from core.tiktok_search_extractor import normalize_tiktok_search_items

    raw = [{"video_url": "https://www.tiktok.com/@alice/photo/123", "caption": "photo"}]

    assert normalize_tiktok_search_items(raw, "photo", source="tiktok_ads_power_search") == []
    assert normalize_tiktok_search_items(raw, "photo", source="tiktok_ads_power_search", allow_photo=True)


@pytest.mark.asyncio
async def test_search_tiktok_handler_uses_adspower_profile(monkeypatch):
    import workers.handlers.tiktok.search_tiktok as handler

    class FakeDatabase:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.closed = False

        async def open(self) -> None:
            pass

        async def close(self) -> None:
            self.closed = True

        async def get_account(self, account_id: str) -> dict[str, Any]:
            return {
                "id": account_id,
                "platform": "tiktok",
                "account_handle": "@searcher",
                "status": "healthy",
                "session_valid": 1,
                "metadata": {
                    "browser_provider": "adspower_manual",
                    "adspower_profile_id": "profile-1",
                    "manual_login_state": "connected_by_confirmation",
                },
            }

        async def get_account_session(self, account_id: str) -> dict[str, Any]:
            return {"id": account_id, "session_valid": 1, "metadata": {}}

    class FakeMouse:
        def __init__(self) -> None:
            self.wheels: list[tuple[int, int]] = []

        async def wheel(self, dx: int, dy: int) -> None:
            self.wheels.append((dx, dy))

    class FakePage:
        def __init__(self) -> None:
            self.url = ""
            self.mouse = FakeMouse()
            self.extract_calls = 0

        async def goto(self, url: str, **_kwargs: Any) -> None:
            self.url = url

        async def query_selector(self, _selector: str) -> None:
            return None

        async def wait_for_selector(self, _selector: str, **_kwargs: Any) -> None:
            return None

        async def evaluate(self, script: str) -> Any:
            if script == handler.JS_SEARCH_PAGE_STATE:
                return {"card_count": 1, "needs_login": False, "has_error": False}
            self.extract_calls += 1
            return [
                {
                    "video_url": "https://www.tiktok.com/@alice/video/123",
                    "caption": "A real product demo TikTok result",
                    "views_text": "10K",
                    "likes_text": "1K",
                    "comments_text": "12",
                }
            ]

        def locator(self, _selector: str) -> Any:
            class _Locator:
                first = None

                async def is_visible(self, **_kwargs: Any) -> bool:
                    return False

            _Locator.first = _Locator()
            return _Locator()

    class FakeContext:
        async def cookies(self) -> list[dict[str, str]]:
            return [{"name": "sessionid"}]

    page = FakePage()

    class FakeProvider:
        @asynccontextmanager
        async def open_publisher_context(self, _pw: Any, *, headless: bool) -> Any:
            assert headless is False
            yield FakeContext(), page, "profile-1"

    @asynccontextmanager
    async def fake_playwright() -> Any:
        yield object()

    async def no_sleep(*_args: Any, **_kwargs: Any) -> None:
        pass

    async def no_jitter(*_args: Any, **_kwargs: Any) -> None:
        pass

    monkeypatch.setenv("DATABASE_URL", "sqlite:///unused.db")
    monkeypatch.setenv("TIKTOK_SEARCH_PROVIDER", "adspower")
    monkeypatch.setattr(handler, "AutomationDatabase", FakeDatabase)
    monkeypatch.setattr(handler, "make_browser_provider", lambda *_args, **_kwargs: FakeProvider())
    monkeypatch.setattr(handler, "_playwright", fake_playwright)
    monkeypatch.setattr(handler, "random_jitter", no_jitter)
    monkeypatch.setattr(handler.asyncio, "sleep", no_sleep)

    result = await handler.search_tiktok_handler(
        {
            "account_id": "account-1",
            "keywords": ["product demo"],
            "max_results": 10,
            "scroll_max": 2,
            "stagnant_limit": 1,
        }
    )

    assert result["ok"] is True
    assert result["source"] == "tiktok_ads_power_search"
    assert result["videos"][0]["url"] == "https://www.tiktok.com/@alice/video/123"
    assert result["videos"][0]["uploader_id"] == "alice"
    assert page.url == "https://www.tiktok.com/search?q=product%20demo"
    assert page.mouse.wheels


def test_search_tiktok_handler_has_no_video_search_fallbacks():
    import workers.handlers.tiktok.search_tiktok as handler

    source = Path(handler.__file__).read_text(encoding="utf-8").lower()
    assert "ytsearch" not in source
    assert "youtube" not in source
    assert "get_ytdlp_path" not in source
    assert "run_subprocess" not in source


def test_search_relevance_helper_scores_keyword_matches():
    from workers.handlers.tiktok.search_tiktok import _calculate_relevance_score

    high_score, matched = _calculate_relevance_score(
        {
            "title": "Kem chống nắng nâng tone",
            "description": "Review #kemchongnang dùng hằng ngày",
            "author": "skincare_store",
        },
        "kem chống nắng",
    )
    low_score, low_matched = _calculate_relevance_score(
        {
            "title": "Máy xay sinh tố",
            "description": "Đồ gia dụng nhà bếp",
            "author": "home_store",
        },
        "kem chống nắng",
    )

    assert high_score > 0.25
    assert set(matched) >= {"kem", "chống", "nắng"}
    assert low_score < 0.25
    assert low_matched == []


def test_search_filter_viral_bypass_and_low_relevance_drop():
    from workers.handlers.tiktok.search_tiktok import _filter_search_videos

    seen_urls: set[str] = set()
    author_counts: dict[str, int] = {}
    accepted, stats = _filter_search_videos(
        [
            {
                "url": "https://www.tiktok.com/@a/video/1",
                "title": "unrelated",
                "description": "nothing relevant",
                "author": "a",
                "uploader_id": "a",
                "views": 100_000,
            },
            {
                "url": "https://www.tiktok.com/@b/video/2",
                "title": "unrelated viral",
                "description": "nothing relevant",
                "author": "b",
                "uploader_id": "b",
                "views": 500_000,
            },
        ],
        keyword="kem chống nắng",
        seen_urls=seen_urls,
        author_counts=author_counts,
        min_views=10_000,
        min_relevance_score=0.25,
        max_per_author=3,
        viral_bypass_views=300_000,
    )

    assert [video["url"] for video in accepted] == ["https://www.tiktok.com/@b/video/2"]
    assert accepted[0]["relevance_score"] < 0.25
    assert stats["dropped_low_relevance"] == 1
    assert stats["accepted"] == 1
