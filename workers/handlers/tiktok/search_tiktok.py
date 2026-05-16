"""Handler for TikTok search through a logged-in AdsPower profile."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import quote

from database.database import AutomationDatabase, RetryConfig
from workers.handlers.tiktok._base import (
    check_already_processed,
    random_jitter,
    resolve_parent_result,
)
from workers.worker_runtime import FatalDependencyError, RetryableDependencyError

LOGGER = logging.getLogger("workers.handlers.tiktok.search_tiktok")

_SEARCH_URL = "https://www.tiktok.com/search?q={keyword}"
_DEFAULT_MAX_RESULTS = int(os.environ.get("TIKTOK_SEARCH_MAX_RESULTS", "50"))
_DEFAULT_SCROLL_MAX = int(os.environ.get("TIKTOK_SEARCH_SCROLL_MAX", "12"))
_DEFAULT_STAGNANT_LIMIT = int(os.environ.get("TIKTOK_SEARCH_STAGNANT_LIMIT", "3"))
_SOURCE = "tiktok_ads_power_search"
_STOP_WORDS = {"v\u00e0", "c\u1ee7a", "cho", "v\u1edbi", "the", "and", "for", "to"}
BROWSER_PROVIDER_ADSPOWER_MANUAL = "adspower_manual"
SEARCH_CARD_SELECTOR = (
    '[data-e2e="search_video-item"], '
    'div[class*="DivItemContainerForSearch"], '
    'div[class*="DivItemContainer"]'
)
try:
    from core.tiktok_search_extractor import (
        JS_EXTRACT_SEARCH_CARDS,
        JS_SEARCH_PAGE_STATE,
        normalize_tiktok_search_items,
    )
except ModuleNotFoundError:
    JS_EXTRACT_SEARCH_CARDS = ""
    JS_SEARCH_PAGE_STATE = ""
    normalize_tiktok_search_items = None  # type: ignore[assignment]

account_metadata = None
make_browser_provider = None
resolve_browser_provider = None


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _search_provider_from_env() -> str:
    provider = os.environ.get("TIKTOK_SEARCH_PROVIDER", "adspower").strip().lower()
    if provider == "adspower_manual":
        return "adspower"
    return provider


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _default_search_min_views() -> int:
    return _env_int("TIKTOK_SEARCH_MIN_VIEWS", _env_int("TIKTOK_MIN_VIEWS", 10_000))


def _ensure_browser_provider_tools() -> tuple[Any, Any, Any]:
    global account_metadata, make_browser_provider, resolve_browser_provider

    if account_metadata is None or make_browser_provider is None or resolve_browser_provider is None:
        from core.browser_providers import (
            account_metadata as _account_metadata,
            make_browser_provider as _make_browser_provider,
            resolve_browser_provider as _resolve_browser_provider,
        )

        if account_metadata is None:
            account_metadata = _account_metadata
        if make_browser_provider is None:
            make_browser_provider = _make_browser_provider
        if resolve_browser_provider is None:
            resolve_browser_provider = _resolve_browser_provider

    return account_metadata, make_browser_provider, resolve_browser_provider


def _ensure_search_extractor() -> tuple[str, str, Any]:
    global JS_EXTRACT_SEARCH_CARDS, JS_SEARCH_PAGE_STATE, normalize_tiktok_search_items

    if not JS_EXTRACT_SEARCH_CARDS or not JS_SEARCH_PAGE_STATE or normalize_tiktok_search_items is None:
        from core.tiktok_search_extractor import (
            JS_EXTRACT_SEARCH_CARDS as _js_extract_search_cards,
            JS_SEARCH_PAGE_STATE as _js_search_page_state,
            normalize_tiktok_search_items as _normalize_tiktok_search_items,
        )

        JS_EXTRACT_SEARCH_CARDS = _js_extract_search_cards
        JS_SEARCH_PAGE_STATE = _js_search_page_state
        normalize_tiktok_search_items = _normalize_tiktok_search_items

    return JS_EXTRACT_SEARCH_CARDS, JS_SEARCH_PAGE_STATE, normalize_tiktok_search_items


async def search_tiktok_handler(payload: dict[str, Any]) -> dict[str, Any]:
    if (cached := check_already_processed(payload)) is not None:
        return cached

    account_id = str(payload.get("account_id") or "").strip()
    if not account_id:
        raise FatalDependencyError("search_tiktok requires 'account_id' in payload")

    try:
        keywords: list[str] = resolve_parent_result(payload, "keywords")
    except KeyError:
        keywords_raw = payload.get("keywords")
        if not keywords_raw:
            raise FatalDependencyError("search_tiktok requires 'keywords' in payload or parent_results")
        keywords = list(keywords_raw)

    keywords = [str(keyword).strip() for keyword in keywords if str(keyword).strip()]
    if not keywords:
        raise FatalDependencyError("search_tiktok received an empty keyword list")

    search_provider = _search_provider_from_env()
    if search_provider != "adspower":
        raise FatalDependencyError("TIKTOK_SEARCH_PROVIDER must be 'adspower' for tiktok.search_tiktok")

    max_results = max(1, int(payload.get("max_results", _DEFAULT_MAX_RESULTS)))
    scroll_max = max(1, int(payload.get("scroll_max", _DEFAULT_SCROLL_MAX)))
    stagnant_limit = max(1, int(payload.get("stagnant_limit", _DEFAULT_STAGNANT_LIMIT)))
    min_views = max(0, int(payload.get("min_views") or _default_search_min_views()))
    min_relevance_score = max(
        0.0,
        min(1.0, float(payload.get("min_relevance_score") or _env_float("TIKTOK_SEARCH_MIN_RELEVANCE_SCORE", 0.25))),
    )
    max_per_author = max(1, int(payload.get("max_per_author") or _env_int("TIKTOK_SEARCH_MAX_PER_AUTHOR", 3)))
    viral_bypass_views = max(
        0,
        int(payload.get("viral_bypass_views") or _env_int("TIKTOK_SEARCH_VIRAL_BYPASS_VIEWS", 300_000)),
    )
    require_login = _env_bool("TIKTOK_SEARCH_REQUIRE_LOGIN", default=True)
    allow_photo = _env_bool("TIKTOK_SEARCH_ALLOW_PHOTO", default=False)

    LOGGER.info(
        "tiktok_ads_power_search_start",
        extra={
            "event": "tiktok_ads_power_search_start",
            "account_id": account_id,
            "keyword_count": len(keywords),
            "max_results": max_results,
            "min_views": min_views,
            "min_relevance_score": min_relevance_score,
            "max_per_author": max_per_author,
            "viral_bypass_views": viral_bypass_views,
            "scroll_max": scroll_max,
            "stagnant_limit": stagnant_limit,
            "require_login": require_login,
            "allow_photo": allow_photo,
            "source": _SOURCE,
        },
    )

    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url:
        raise FatalDependencyError("DATABASE_URL is required for tiktok.search_tiktok")

    database = AutomationDatabase(db_url, retry_config=RetryConfig())
    await database.open()

    try:
        _account_metadata, _make_browser_provider, _resolve_browser_provider = _ensure_browser_provider_tools()

        account = await database.get_account(account_id)
        if account is None:
            raise FatalDependencyError(f"Account {account_id} not found")

        metadata = _account_metadata(account)
        account_for_provider = {**account, "account_id": account_id, "metadata": metadata}

        platform = str(account.get("platform") or "").strip().lower()
        if platform != "tiktok":
            raise FatalDependencyError(f"Account {account_id} must be a TikTok account")

        browser_provider = _resolve_browser_provider(account_for_provider)
        if browser_provider != BROWSER_PROVIDER_ADSPOWER_MANUAL:
            raise FatalDependencyError(
                f"tiktok.search_tiktok requires browser_provider={BROWSER_PROVIDER_ADSPOWER_MANUAL}"
            )

        manual_connected = metadata.get("manual_login_state") == "connected_by_confirmation"
        if not bool(account.get("session_valid", 0)) and not manual_connected:
            raise FatalDependencyError(
                f"Account {account_id} must have a valid AdsPower manual TikTok session"
            )

        session = await database.get_account_session(account_id) or {}
        provider = _make_browser_provider(account_for_provider, session=session, identity_profile=None)

        await random_jitter(1.0, 3.0)

        all_videos: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        async with _playwright() as pw:
            async with provider.open_publisher_context(pw, headless=False) as (context, page, _opened_profile):
                await _confirm_tiktok_auth(page, context, account_id=account_id, require_login=require_login)

                for keyword in keywords[:5]:
                    keyword_videos = await _search_keyword_with_page(
                        page,
                        context,
                        keyword=keyword,
                        account_id=account_id,
                        max_results=max_results,
                        scroll_max=scroll_max,
                        stagnant_limit=stagnant_limit,
                        min_views=min_views,
                        min_relevance_score=min_relevance_score,
                        max_per_author=max_per_author,
                        viral_bypass_views=viral_bypass_views,
                        require_login=require_login,
                        allow_photo=allow_photo,
                    )
                    for video in keyword_videos:
                        url = str(video.get("url") or "")
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            all_videos.append(video)

                    if len(all_videos) >= max_results:
                        break

                    delay = random.uniform(2.0, 5.0)
                    await asyncio.sleep(delay)

    except FatalDependencyError:
        raise
    except Exception as exc:
        raise RetryableDependencyError(f"TikTok AdsPower search failed: {exc}") from exc
    finally:
        await database.close()

    all_videos.sort(key=lambda video: int(video.get("views") or 0), reverse=True)
    result_videos = all_videos[:max_results]

    if not result_videos:
        LOGGER.warning(
            "tiktok_search_no_results",
            extra={
                "event": "tiktok_search_no_results",
                "account_id": account_id,
                "keyword_count": len(keywords),
                "source": _SOURCE,
            },
        )

    LOGGER.info(
        "tiktok_search_done",
        extra={
            "event": "tiktok_search_done",
            "account_id": account_id,
            "total_videos": len(result_videos),
            "source": _SOURCE,
        },
    )

    return {"videos": result_videos, "ok": True, "source": _SOURCE}


async def _search_keyword_with_page(
    page: Any,
    context: Any,
    *,
    keyword: str,
    account_id: str,
    max_results: int,
    scroll_max: int,
    stagnant_limit: int,
    min_views: int,
    min_relevance_score: float,
    max_per_author: int,
    viral_bypass_views: int,
    require_login: bool,
    allow_photo: bool,
) -> list[dict[str, Any]]:
    search_url = _SEARCH_URL.format(keyword=quote(keyword, safe=""))
    started_at = time.monotonic()
    videos: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    author_counts: dict[str, int] = {}
    stagnant_rounds = 0
    total_filter_stats = _empty_filter_stats()

    await page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
    await asyncio.sleep(random.uniform(2.0, 4.0))
    await _dismiss_overlays(page)

    page_state = await _safe_page_state(page)
    LOGGER.info(
        "tiktok_search_page_loaded",
        extra={
            "event": "tiktok_search_page_loaded",
            "account_id": account_id,
            "keyword": keyword,
            "url": getattr(page, "url", ""),
            "card_count": int(page_state.get("card_count") or 0),
            "has_error": bool(page_state.get("has_error")),
            "needs_login": bool(page_state.get("needs_login")),
            "source": _SOURCE,
        },
    )

    if require_login and bool(page_state.get("needs_login")) and not await _has_tiktok_auth_signal(page, context):
        raise FatalDependencyError(
            f"SESSION_NOT_CONNECTED for account {account_id}: TikTok search page asks for login"
        )

    try:
        await page.wait_for_selector(SEARCH_CARD_SELECTOR, timeout=10_000)
        await asyncio.sleep(random.uniform(0.8, 1.6))
    except Exception:
        pass

    for scroll_round in range(1, scroll_max + 1):
        raw_items = await _extract_raw_items(page)
        _, _, normalize_items = _ensure_search_extractor()
        normalized = normalize_items(
            raw_items,
            keyword,
            source=_SOURCE,
            allow_photo=allow_photo,
        )
        accepted, filter_stats = _filter_search_videos(
            normalized,
            keyword=keyword,
            seen_urls=seen_urls,
            author_counts=author_counts,
            min_views=min_views,
            min_relevance_score=min_relevance_score,
            max_per_author=max_per_author,
            viral_bypass_views=viral_bypass_views,
        )
        filter_stats["raw_items"] = len(raw_items)
        _add_filter_stats(total_filter_stats, filter_stats)

        before_count = len(videos)
        videos.extend(accepted)

        new_count = len(videos) - before_count
        if new_count == 0:
            stagnant_rounds += 1
        else:
            stagnant_rounds = 0

        LOGGER.info(
            "tiktok_search_scroll_round",
            extra={
                "event": "tiktok_search_scroll_round",
                "account_id": account_id,
                "keyword": keyword,
                "round": scroll_round,
                "raw_items": len(raw_items),
                "normalized": filter_stats["normalized"],
                "accepted_new_videos": new_count,
                "dropped_low_views": filter_stats["dropped_low_views"],
                "dropped_low_relevance": filter_stats["dropped_low_relevance"],
                "collected": len(videos),
                "stagnant_rounds": stagnant_rounds,
                "source": _SOURCE,
            },
        )

        if len(videos) >= max_results:
            break

        if stagnant_rounds >= stagnant_limit:
            LOGGER.info(
                "tiktok_search_stagnant_detected",
                extra={
                    "event": "tiktok_search_stagnant_detected",
                    "account_id": account_id,
                    "keyword": keyword,
                    "round": scroll_round,
                    "stagnant_rounds": stagnant_rounds,
                    "collected": len(videos),
                    "source": _SOURCE,
                },
            )
            break

        await _human_search_scroll(page)

    videos.sort(key=lambda video: int(video.get("views") or 0), reverse=True)
    LOGGER.info(
        "tiktok_search_keyword_done",
        extra={
            "event": "tiktok_search_keyword_done",
            "account_id": account_id,
            "keyword": keyword,
            "collected": len(videos),
            "filter_stats": total_filter_stats,
            "elapsed_seconds": round(time.monotonic() - started_at, 1),
            "source": _SOURCE,
        },
    )
    return videos[:max_results]


def _empty_filter_stats() -> dict[str, int]:
    return {
        "raw_items": 0,
        "normalized": 0,
        "dropped_low_views": 0,
        "dropped_low_relevance": 0,
        "dropped_empty_text": 0,
        "dropped_author_cap": 0,
        "dropped_non_video": 0,
        "accepted": 0,
    }


def _add_filter_stats(total: dict[str, int], delta: dict[str, int]) -> None:
    for key, value in delta.items():
        total[key] = int(total.get(key, 0)) + int(value)


def _filter_search_videos(
    normalized: list[dict[str, Any]],
    *,
    keyword: str,
    seen_urls: set[str],
    author_counts: dict[str, int],
    min_views: int,
    min_relevance_score: float,
    max_per_author: int,
    viral_bypass_views: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    stats = _empty_filter_stats()
    stats["normalized"] = len(normalized)
    accepted: list[dict[str, Any]] = []

    for video in normalized:
        url = str(video.get("url") or "")
        if not url or "/video/" not in url or "tiktok.com" not in url:
            stats["dropped_non_video"] += 1
            continue
        if url in seen_urls:
            continue

        views = max(int(video.get("views") or 0), 0)
        if views < min_views:
            stats["dropped_low_views"] += 1
            continue

        viral_bypass = views >= viral_bypass_views
        if not _has_meaningful_text(video, keyword) and not viral_bypass:
            stats["dropped_empty_text"] += 1
            continue

        relevance_score, matched_terms = _calculate_relevance_score(video, keyword)
        if relevance_score < min_relevance_score and not viral_bypass:
            stats["dropped_low_relevance"] += 1
            continue

        author = _author_key(video)
        if author != "__unknown__" and author_counts.get(author, 0) >= max_per_author:
            stats["dropped_author_cap"] += 1
            continue

        video["relevance_score"] = round(relevance_score, 4)
        video["matched_keyword_terms"] = matched_terms
        seen_urls.add(url)
        if author != "__unknown__":
            author_counts[author] = author_counts.get(author, 0) + 1
        accepted.append(video)
        stats["accepted"] += 1

    return accepted, stats


def _has_meaningful_text(video: dict[str, Any], keyword: str) -> bool:
    title = str(video.get("title") or "").strip()
    description = str(video.get("description") or "").strip()
    if description:
        return True
    return bool(title and title.lower() != keyword.lower())


def _author_key(video: dict[str, Any]) -> str:
    author = str(video.get("uploader_id") or video.get("uploader") or video.get("author") or "").strip().lower()
    return author or "__unknown__"


def _keyword_terms(keyword: str) -> list[str]:
    terms = re.split(r"[\s,\-_/]+", keyword.lower())
    return [term for term in terms if len(term) > 2 and term not in _STOP_WORDS]


def _calculate_relevance_score(video: dict[str, Any], keyword: str) -> tuple[float, list[str]]:
    terms = _keyword_terms(keyword)
    if not terms:
        return 0.5, []

    title = str(video.get("title") or "").lower()
    description = str(video.get("description") or "").lower()
    author = str(video.get("author") or video.get("uploader") or "").lower()
    text = f"{title} {description} {author}"
    matched_terms = [term for term in terms if term in text]
    term_hits = len(matched_terms) / max(len(terms), 1)

    hashtags = re.findall(r"#([0-9a-zA-Z_\u00c0-\u1ef9]+)", description)
    hashtag_bonus = 0.15 if any(any(term in tag for tag in hashtags) for term in terms) else 0.0
    title_bonus = 0.15 if any(term in title for term in terms) else 0.0
    author_bonus = 0.05 if any(term in author for term in terms) else 0.0
    score = min(1.0, term_hits * 0.7 + hashtag_bonus + title_bonus + author_bonus)
    return score, matched_terms


async def _extract_raw_items(page: Any) -> list[dict[str, Any]]:
    extract_script, _, _ = _ensure_search_extractor()
    raw = await page.evaluate(extract_script)
    return raw if isinstance(raw, list) else []


async def _safe_page_state(page: Any) -> dict[str, Any]:
    try:
        _, page_state_script, _ = _ensure_search_extractor()
        state = await page.evaluate(page_state_script)
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


async def _confirm_tiktok_auth(
    page: Any,
    context: Any,
    *,
    account_id: str,
    require_login: bool,
) -> None:
    await page.goto("https://www.tiktok.com/", wait_until="domcontentloaded", timeout=30_000)
    await asyncio.sleep(random.uniform(1.5, 3.0))
    await _dismiss_overlays(page)

    if not require_login:
        return

    if not await _has_tiktok_auth_signal(page, context):
        LOGGER.warning(
            "tiktok_ads_power_auth_missing",
            extra={
                "event": "tiktok_ads_power_auth_missing",
                "account_id": account_id,
                "current_url": getattr(page, "url", ""),
                "source": _SOURCE,
            },
        )
        raise FatalDependencyError(
            f"SESSION_NOT_CONNECTED for account {account_id}: AdsPower profile has no TikTok auth signal"
        )

    LOGGER.info(
        "tiktok_ads_power_auth_confirmed",
        extra={
            "event": "tiktok_ads_power_auth_confirmed",
            "account_id": account_id,
            "current_url": getattr(page, "url", ""),
            "source": _SOURCE,
        },
    )


async def _human_search_scroll(page: Any) -> None:
    await page.mouse.wheel(0, random.randint(1000, 1800))
    await asyncio.sleep(random.uniform(1.0, 2.3))
    await page.mouse.wheel(0, -random.randint(120, 360))
    await asyncio.sleep(random.uniform(0.7, 1.6))
    await page.mouse.wheel(0, random.randint(600, 1300))
    await asyncio.sleep(random.uniform(1.2, 2.8))


async def _dismiss_overlays(page: Any) -> None:
    for selector in (
        '[data-e2e="modal-close-inner-button"]',
        'button[class*="CloseButton"]',
        '[aria-label="Close"]',
        'div[class*="close-icon"]',
    ):
        try:
            button = await page.query_selector(selector)
            if button:
                await button.click()
                await asyncio.sleep(random.uniform(0.2, 0.6))
                return
        except Exception:
            continue


async def _has_tiktok_auth_signal(page: Any, context: Any) -> bool:
    try:
        cookies = await context.cookies()
        cookie_names = {str(cookie.get("name", "")).lower() for cookie in cookies}
        if cookie_names & {"sessionid", "sid_guard", "uid_tt", "passport_csrf_token"}:
            return True
    except Exception:
        pass
    try:
        avatar = page.locator('[data-e2e="header-avatar"], [data-e2e="nav-avatar"]').first
        return bool(await avatar.is_visible(timeout=800))
    except Exception:
        return False


@asynccontextmanager
async def _playwright() -> AsyncIterator[Any]:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        yield pw
