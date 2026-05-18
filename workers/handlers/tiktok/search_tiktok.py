"""Handler for TikTok search through a logged-in AdsPower profile."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
import unicodedata
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

_SEARCH_URL = "https://www.tiktok.com/search/video?q={keyword}"
_DEFAULT_MAX_RESULTS = int(os.environ.get("TIKTOK_SEARCH_MAX_RESULTS", "50"))
_DEFAULT_SCROLL_MAX = int(os.environ.get("TIKTOK_SEARCH_SCROLL_MAX", "12"))
_DEFAULT_STAGNANT_LIMIT = int(os.environ.get("TIKTOK_SEARCH_STAGNANT_LIMIT", "5"))
_MIN_ROUNDS_BEFORE_BOTTOM = 3
_SOURCE = "tiktok_ads_power_search"
_STOP_WORDS = {"v\u00e0", "c\u1ee7a", "cho", "v\u1edbi", "the", "and", "for", "to"}
_TIKTOK_VIDEO_URL_RE = re.compile(r"^https://www\.tiktok\.com/@[^/?#]+/video/\d+", re.IGNORECASE)
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


def _empty_text_warn_ratio() -> float:
    return max(0.0, min(1.0, _env_float("TIKTOK_SEARCH_EMPTY_TEXT_WARN_RATIO", 0.7)))


def _coerce_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = list(value) if isinstance(value, tuple | set) else []
    items: list[str] = []
    for item in raw_items:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if text:
            items.append(text)
    return items


def _is_valid_search_query(query: str) -> bool:
    lowered = query.strip().lower()
    junk_set = {"unknown", "product", "item", "shop", "tiktok", "tiktok shop", "s\u1ea3n ph\u1ea9m", "san pham", "h\u00e0ng hot", "hang hot"}
    if lowered in junk_set:
        return False
    words = re.findall(r"[\w\u00c0-\u1ef9]+", lowered, flags=re.UNICODE)
    return 2 <= len(words) <= 5


def _selected_search_queries(search_queries: list[str], keywords: list[str]) -> list[str]:
    selected: list[str] = []
    for query in [*search_queries, *keywords]:
        if not _is_valid_search_query(query):
            continue
        folded = _fold_text(query)
        if folded in {_fold_text(item) for item in selected}:
            continue
        selected.append(query)
        if len(selected) >= 5:
            break
    return selected


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
        search_queries = _coerce_string_list(resolve_parent_result(payload, "search_queries"))
    except KeyError:
        search_queries = _coerce_string_list(payload.get("search_queries"))
    try:
        keywords = _coerce_string_list(resolve_parent_result(payload, "keywords"))
    except KeyError:
        keywords = _coerce_string_list(payload.get("keywords"))

    selected_queries = _selected_search_queries(search_queries, keywords)
    if not selected_queries:
        raise FatalDependencyError(
            f"search_tiktok requires valid search_queries or keywords. "
            f"search_queries={search_queries}, keywords={keywords}"
        )
    search_provider = _search_provider_from_env()
    if search_provider != "adspower":
        raise FatalDependencyError("TIKTOK_SEARCH_PROVIDER must be 'adspower' for tiktok.search_tiktok")

    max_results = max(1, int(payload.get("max_results", _DEFAULT_MAX_RESULTS)))
    scroll_max = max(1, int(payload.get("scroll_max", _DEFAULT_SCROLL_MAX)))
    stagnant_limit = max(1, int(payload.get("stagnant_limit", _DEFAULT_STAGNANT_LIMIT)))
    min_views = max(0, int(payload.get("min_views") or _default_search_min_views()))
    apply_min_views = _env_bool("TIKTOK_SEARCH_APPLY_MIN_VIEWS", default=False)
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
            "search_queries": search_queries,
            "keywords": keywords,
            "selected_queries": selected_queries,
            "max_results": max_results,
            "min_views": min_views,
            "apply_min_views": apply_min_views,
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

                search_diagnostics: dict[str, Any] = {"queries": selected_queries, "per_query": []}
                for keyword in selected_queries:
                    query_result = await _search_keyword_with_page(
                        page,
                        context,
                        keyword=keyword,
                        account_id=account_id,
                        max_results=max_results,
                        scroll_max=scroll_max,
                        stagnant_limit=stagnant_limit,
                        min_views=min_views,
                        apply_min_views=apply_min_views,
                        min_relevance_score=min_relevance_score,
                        max_per_author=max_per_author,
                        viral_bypass_views=viral_bypass_views,
                        require_login=require_login,
                        allow_photo=allow_photo,
                    )
                    keyword_videos = query_result["videos"]
                    search_diagnostics["per_query"].append(query_result["diagnostics"])
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

    result_videos = all_videos[:max_results]

    if not result_videos:
        LOGGER.warning(
            "tiktok_search_no_results",
            extra={
                "event": "tiktok_search_no_results",
                "account_id": account_id,
                "search_queries": selected_queries,
                "search_diagnostics": search_diagnostics,
                "source": _SOURCE,
            },
        )
        raise FatalDependencyError(
            f"TikTok search found no video links for queries={selected_queries}. "
            f"diagnostics={json.dumps(search_diagnostics, ensure_ascii=False)[:2000]}"
        )

    LOGGER.info(
        "tiktok_search_done",
        extra={
            "event": "tiktok_search_done",
            "account_id": account_id,
            "total_videos": len(result_videos),
            "search_queries": selected_queries,
            "source": _SOURCE,
        },
    )

    return {"videos": result_videos, "search_diagnostics": search_diagnostics, "ok": True, "source": _SOURCE}


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
    apply_min_views: bool,
    min_relevance_score: float,
    max_per_author: int,
    viral_bypass_views: int,
    require_login: bool,
    allow_photo: bool,
) -> dict[str, Any]:
    search_url = _SEARCH_URL.format(keyword=quote(keyword, safe=""))
    started_at = time.monotonic()
    videos: list[dict[str, Any]] = []
    raw_collected_videos: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_raw_video_urls: set[str] = set()
    author_counts: dict[str, int] = {}
    stagnant_rounds = 0
    unchanged_scroll_rounds = 0
    total_filter_stats = _empty_filter_stats()
    min_rounds_before_bottom = min(scroll_max, _MIN_ROUNDS_BEFORE_BOTTOM)
    diagnostics: dict[str, Any] = {
        "query": keyword,
        "search_url": search_url,
        "video_tab": {},
        "rounds": [],
        "final_page_state": {},
        "filter_stats": total_filter_stats,
    }

    await page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
    await asyncio.sleep(random.uniform(2.0, 4.0))
    await _dismiss_overlays(page)
    diagnostics["video_tab"] = await _switch_to_video_tab(page)
    await _dismiss_overlays(page)

    page_state = await _safe_page_state(page)
    diagnostics["initial_page_state"] = page_state
    LOGGER.info(
        "tiktok_search_page_loaded",
        extra={
            "event": "tiktok_search_page_loaded",
            "account_id": account_id,
            "keyword": keyword,
            "url": getattr(page, "url", ""),
            "card_count": int(page_state.get("card_count") or 0),
            "active_tab_text": page_state.get("active_tab_text") or "",
            "video_link_count": int(page_state.get("video_link_count") or 0),
            "live_link_count": int(page_state.get("live_link_count") or 0),
            "user_link_count": int(page_state.get("user_link_count") or 0),
            "shop_link_count": int(page_state.get("shop_link_count") or 0),
            "has_error": bool(page_state.get("has_error")),
            "needs_login": bool(page_state.get("needs_login")),
            "video_tab": diagnostics["video_tab"],
            "source": _SOURCE,
        },
    )
    _warn_if_wrong_tab_or_mixed_results(page_state, account_id=account_id, keyword=keyword)

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
        page_state = await _safe_page_state(page)
        raw_items = await _extract_raw_items(page)
        _, _, normalize_items = _ensure_search_extractor()
        normalized = normalize_items(
            raw_items,
            keyword,
            source=_SOURCE,
            allow_photo=allow_photo,
        )
        raw_videos = [video for video in normalized if _is_acceptable_tiktok_video_url(str(video.get("url") or ""))]
        raw_video_urls = [str(video.get("url") or "") for video in raw_videos]
        raw_new_videos: list[dict[str, Any]] = []
        for video in raw_videos:
            raw_url = str(video.get("url") or "")
            if not raw_url or raw_url in seen_raw_video_urls:
                continue
            seen_raw_video_urls.add(raw_url)
            raw_video = dict(video)
            relevance_score, matched_terms = _calculate_relevance_score(raw_video, keyword)
            raw_video["relevance_score"] = round(relevance_score, 4)
            raw_video["matched_keyword_terms"] = matched_terms
            raw_video["needs_selection_filter"] = True
            raw_new_videos.append(raw_video)
            raw_collected_videos.append(raw_video)
        raw_new_video_links = len(raw_new_videos)

        accepted, filter_stats = _filter_search_videos(
            normalized,
            keyword=keyword,
            seen_urls=seen_urls,
            author_counts=author_counts,
            min_views=min_views,
            apply_min_views=apply_min_views,
            apply_quality_filters=apply_min_views,
            min_relevance_score=min_relevance_score,
            max_per_author=max_per_author,
            viral_bypass_views=viral_bypass_views,
        )
        filter_stats["raw_items"] = len(raw_items)
        _add_filter_stats(total_filter_stats, filter_stats)

        before_count = len(videos)
        videos.extend(accepted)

        accepted_new_videos = len(videos) - before_count
        if raw_new_video_links == 0:
            stagnant_rounds += 1
        else:
            stagnant_rounds = 0

        LOGGER.info(
            "tiktok_search_scroll_round",
            extra={
                "event": "tiktok_search_scroll_round",
                "account_id": account_id,
                "keyword": keyword,
                "scroll_round": scroll_round,
                "raw_items": len(raw_items),
                "normalized": filter_stats["normalized"],
                "raw_video_links": len(raw_video_urls),
                "raw_new_video_links": raw_new_video_links,
                "accepted_new_videos": accepted_new_videos,
                "video_link_count_total": int(page_state.get("video_link_count") or 0),
                "new_video_links": raw_new_video_links,
                "current_url": getattr(page, "url", ""),
                "active_tab": page_state.get("active_tab_text") or "",
                "dropped_low_views": filter_stats["dropped_low_views"],
                "dropped_low_relevance": filter_stats["dropped_low_relevance"],
                "collected": len(videos),
                "stagnant_rounds": stagnant_rounds,
                "source": _SOURCE,
            },
        )
        diagnostics["rounds"].append({
            "scroll_round": scroll_round,
            "raw_items": len(raw_items),
            "normalized": filter_stats["normalized"],
            "raw_video_links": len(raw_video_urls),
            "raw_new_video_links": raw_new_video_links,
            "accepted_new_videos": accepted_new_videos,
            "video_link_count_total": int(page_state.get("video_link_count") or 0),
            "new_video_links": raw_new_video_links,
            "current_url": getattr(page, "url", ""),
            "active_tab": page_state.get("active_tab_text") or "",
            "stagnant_rounds": stagnant_rounds,
        })

        if len(videos) >= max_results:
            break

        if stagnant_rounds >= stagnant_limit and scroll_round >= min_rounds_before_bottom:
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

        scroll_state = await _human_search_scroll_strong(page)
        diagnostics["rounds"][-1]["scroll_state"] = scroll_state
        if raw_new_video_links == 0 and _scroll_metrics_unchanged(scroll_state):
            unchanged_scroll_rounds += 1
        else:
            unchanged_scroll_rounds = 0

        bottom_detected = (
            bool(scroll_state.get("at_bottom"))
            and scroll_round >= min_rounds_before_bottom
            and raw_new_video_links == 0
            and unchanged_scroll_rounds >= 2
        )
        if bottom_detected:
            LOGGER.info(
                "tiktok_search_scroll_bottom_detected",
                extra={
                    "event": "tiktok_search_scroll_bottom_detected",
                    "account_id": account_id,
                    "keyword": keyword,
                    "scroll_round": scroll_round,
                    "collected": len(videos),
                    "raw_collected": len(raw_collected_videos),
                    "raw_new_video_links": raw_new_video_links,
                    "unchanged_scroll_rounds": unchanged_scroll_rounds,
                    "scroll_state": scroll_state,
                    "source": _SOURCE,
                },
            )
            break

    if not videos and raw_collected_videos:
        videos = raw_collected_videos
        diagnostics["needs_selection_filter"] = True

    diagnostics["final_page_state"] = await _safe_page_state(page)
    diagnostics["filter_stats"] = total_filter_stats
    diagnostics["raw_video_links_collected"] = len(raw_collected_videos)
    _warn_if_empty_text_high(
        total_filter_stats,
        account_id=account_id,
        keyword=keyword,
        threshold=_empty_text_warn_ratio(),
    )
    LOGGER.info(
        "tiktok_search_keyword_done",
        extra={
            "event": "tiktok_search_keyword_done",
            "account_id": account_id,
            "keyword": keyword,
            "collected": len(videos),
            "raw_collected": len(raw_collected_videos),
            "needs_selection_filter": bool(diagnostics.get("needs_selection_filter")),
            "page_state": diagnostics["final_page_state"],
            "filter_stats": total_filter_stats,
            "elapsed_seconds": round(time.monotonic() - started_at, 1),
            "source": _SOURCE,
        },
    )
    return {"videos": videos[:max_results], "diagnostics": diagnostics}


def _empty_filter_stats() -> dict[str, int]:
    return {
        "raw_items": 0,
        "normalized": 0,
        "dropped_low_views": 0,
        "dropped_low_relevance": 0,
        "dropped_empty_text": 0,
        "dropped_author_cap": 0,
        "dropped_non_video": 0,
        "dropped_duplicate": 0,
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
    apply_min_views: bool = True,
    apply_quality_filters: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    stats = _empty_filter_stats()
    stats["normalized"] = len(normalized)
    accepted: list[dict[str, Any]] = []

    for video in normalized:
        url = str(video.get("url") or "")
        if not _is_acceptable_tiktok_video_url(url):
            stats["dropped_non_video"] += 1
            continue
        if url in seen_urls:
            stats["dropped_duplicate"] += 1
            continue

        views = max(int(video.get("views") or 0), 0)
        if apply_min_views and views < min_views:
            stats["dropped_low_views"] += 1
            continue

        viral_bypass = apply_quality_filters and views >= viral_bypass_views
        relevance_score, matched_terms = _calculate_relevance_score(video, keyword)

        if apply_quality_filters and not _has_meaningful_text(video, keyword) and not viral_bypass:
            stats["dropped_empty_text"] += 1
            continue

        if apply_quality_filters and relevance_score < min_relevance_score and not viral_bypass:
            stats["dropped_low_relevance"] += 1
            continue

        author = _author_key(video)
        if apply_quality_filters and author != "__unknown__" and author_counts.get(author, 0) >= max_per_author:
            stats["dropped_author_cap"] += 1
            continue

        video["relevance_score"] = round(relevance_score, 4)
        video["matched_keyword_terms"] = matched_terms
        video["needs_selection_filter"] = True
        seen_urls.add(url)
        if apply_quality_filters and author != "__unknown__":
            author_counts[author] = author_counts.get(author, 0) + 1
        accepted.append(video)
        stats["accepted"] += 1

    return accepted, stats


def _is_acceptable_tiktok_video_url(url: str) -> bool:
    if not url:
        return False
    if not _TIKTOK_VIDEO_URL_RE.match(url):
        return False
    banned_markers = ("/live", "/photo/", "/tag/", "/music/", "/shop")
    if any(marker in url for marker in banned_markers) or "shop.tiktok.com" in url:
        return False
    return True


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


def _fold_text(text: str) -> str:
    normalized = unicodedata.normalize("NFD", str(text).lower())
    folded = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return folded.replace("\u0111", "d")


def _term_matches(term: str, raw_text: str, folded_text: str) -> bool:
    if term in raw_text:
        return True
    folded = _fold_text(term)
    return bool(folded and folded in folded_text)


def _calculate_relevance_score(video: dict[str, Any], keyword: str) -> tuple[float, list[str]]:
    terms = _keyword_terms(keyword)
    if not terms:
        return 0.5, []

    title = str(video.get("title") or "").lower()
    description = str(video.get("description") or "").lower()
    author = str(video.get("author") or video.get("uploader") or "").lower()
    text = f"{title} {description} {author}"
    text_folded = _fold_text(text)
    title_folded = _fold_text(title)
    author_folded = _fold_text(author)

    matched_terms = [term for term in terms if _term_matches(term, text, text_folded)]
    term_hits = len(matched_terms) / max(len(terms), 1)

    hashtags = re.findall(r"#([0-9a-zA-Z_\u00c0-\u1ef9]+)", description)
    hashtags_raw = " ".join(hashtags).lower()
    hashtags_folded = _fold_text(hashtags_raw)
    hashtag_bonus = 0.15 if any(_term_matches(term, hashtags_raw, hashtags_folded) for term in terms) else 0.0
    title_bonus = 0.15 if any(_term_matches(term, title, title_folded) for term in terms) else 0.0
    author_bonus = 0.05 if any(_term_matches(term, author, author_folded) for term in terms) else 0.0
    score = min(1.0, term_hits * 0.7 + hashtag_bonus + title_bonus + author_bonus)
    return score, matched_terms


def _should_warn_empty_text(stats: dict[str, int], threshold: float) -> tuple[bool, float]:
    normalized = int(stats.get("normalized") or 0)
    if normalized <= 0:
        return False, 0.0
    ratio = int(stats.get("dropped_empty_text") or 0) / normalized
    return ratio >= threshold, ratio


def _warn_if_empty_text_high(
    stats: dict[str, int],
    *,
    account_id: str,
    keyword: str,
    threshold: float,
) -> None:
    should_warn, ratio = _should_warn_empty_text(stats, threshold)
    if not should_warn:
        return
    LOGGER.warning(
        "tiktok_search_empty_text_high",
        extra={
            "event": "tiktok_search_empty_text_high",
            "account_id": account_id,
            "keyword": keyword,
            "normalized": int(stats.get("normalized") or 0),
            "dropped_empty_text": int(stats.get("dropped_empty_text") or 0),
            "ratio": round(ratio, 4),
            "message": "TikTok search extractor may need selector update",
            "source": _SOURCE,
        },
    )


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


async def _switch_to_video_tab(page: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "attempted": True,
        "clicked": False,
        "url_after": getattr(page, "url", ""),
        "tab_text": "",
    }
    if "/search/video" in str(getattr(page, "url", "")):
        result["url_after"] = getattr(page, "url", "")
        result["tab_text"] = "Videos"
        return result
    try:
        clicked = await page.evaluate(
            """() => {
                const candidates = [...document.querySelectorAll('[role="tab"], button, a, div')]
                    .filter((el) => {
                        const text = (el.innerText || el.textContent || '').trim();
                        return /^(Videos?|Video)$/i.test(text);
                    });
                const target = candidates[0];
                if (!target) return { clicked: false, tab_text: '' };
                target.scrollIntoView({ block: 'center', inline: 'center' });
                target.click();
                return { clicked: true, tab_text: (target.innerText || target.textContent || '').trim() };
            }"""
        )
        if isinstance(clicked, dict):
            result["clicked"] = bool(clicked.get("clicked"))
            result["tab_text"] = str(clicked.get("tab_text") or "")
            if result["clicked"]:
                await asyncio.sleep(random.uniform(1.0, 2.0))
    except Exception as exc:
        result["error"] = str(exc)[:200]
    result["url_after"] = getattr(page, "url", "")
    return result


def _warn_if_wrong_tab_or_mixed_results(page_state: dict[str, Any], *, account_id: str, keyword: str) -> None:
    video_link_count = int(page_state.get("video_link_count") or 0)
    live_link_count = int(page_state.get("live_link_count") or 0)
    user_link_count = int(page_state.get("user_link_count") or 0)
    shop_link_count = int(page_state.get("shop_link_count") or 0)
    if video_link_count > 0 or (live_link_count + user_link_count + shop_link_count) <= 0:
        return
    LOGGER.warning(
        "tiktok_search_wrong_tab_or_mixed_results",
        extra={
            "event": "tiktok_search_wrong_tab_or_mixed_results",
            "account_id": account_id,
            "keyword": keyword,
            "active_tab_text": page_state.get("active_tab_text") or "",
            "video_link_count": video_link_count,
            "live_link_count": live_link_count,
            "user_link_count": user_link_count,
            "shop_link_count": shop_link_count,
            "current_url": page_state.get("url") or "",
            "source": _SOURCE,
        },
    )


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


async def _human_search_scroll(page: Any) -> dict[str, Any]:
    return await _human_search_scroll_strong(page)


async def _human_search_scroll_strong(page: Any) -> dict[str, Any]:
    before = await _scroll_metrics(page)
    video_link_count_before = await _video_link_count(page)

    try:
        mouse = getattr(page, "mouse", None)
        if mouse is not None:
            await mouse.wheel(0, random.randint(2200, 2800))
            await asyncio.sleep(random.uniform(0.7, 1.2))
    except Exception:
        pass

    try:
        keyboard = getattr(page, "keyboard", None)
        if keyboard is not None:
            await keyboard.press("PageDown")
            await asyncio.sleep(random.uniform(0.7, 1.3))
    except Exception:
        pass

    scrolled_container_count = 0
    try:
        container_result = await page.evaluate(
            """() => {
                const candidates = [document.scrollingElement, document.documentElement, document.body, ...document.querySelectorAll('main, section, div')]
                    .filter(Boolean)
                    .filter((el, index, arr) => arr.indexOf(el) === index)
                    .filter((el) => {
                        const style = window.getComputedStyle(el);
                        const overflow = `${style.overflowY} ${style.overflow}`;
                        return el.scrollHeight > el.clientHeight + 50 && /(auto|scroll|overlay)/i.test(overflow);
                    })
                    .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight))
                    .slice(0, 5);
                let scrolled = 0;
                for (const el of candidates) {
                    const before = el.scrollTop;
                    el.scrollTop = Math.min(el.scrollTop + 2000, el.scrollHeight);
                    if (el.scrollTop !== before) scrolled += 1;
                }
                return { scrolled_container_count: scrolled };
            }"""
        )
        if isinstance(container_result, dict):
            scrolled_container_count = int(container_result.get("scrolled_container_count") or 0)
        await asyncio.sleep(random.uniform(0.7, 1.2))
    except Exception:
        pass

    try:
        await page.evaluate(
            """() => {
                window.scrollTo(0, document.body.scrollHeight || document.documentElement.scrollHeight || 0);
            }"""
        )
    except Exception:
        pass

    await asyncio.sleep(random.uniform(2.0, 4.0))
    after = await _scroll_metrics(page)
    video_link_count_after = await _video_link_count(page)

    return {
        "before_window": before,
        "after_window": after,
        "scrolled_container_count": scrolled_container_count,
        "video_link_count_before": video_link_count_before,
        "video_link_count_after": video_link_count_after,
        "at_bottom": bool(after.get("at_bottom")),
    }


def _scroll_metrics_unchanged(scroll_state: dict[str, Any]) -> bool:
    before = scroll_state.get("before_window")
    after = scroll_state.get("after_window")
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False
    before_signature = (
        int(before.get("scroll_y") or 0),
        int(before.get("scroll_height") or 0),
        int(before.get("inner_height") or 0),
    )
    after_signature = (
        int(after.get("scroll_y") or 0),
        int(after.get("scroll_height") or 0),
        int(after.get("inner_height") or 0),
    )
    return (
        before_signature == after_signature
        and int(scroll_state.get("scrolled_container_count") or 0) == 0
        and int(scroll_state.get("video_link_count_after") or 0) <= int(scroll_state.get("video_link_count_before") or 0)
    )


async def _video_link_count(page: Any) -> int:
    try:
        count = await page.evaluate(
            """() => document.querySelectorAll('a[href*="/video/"]').length"""
        )
        return int(count) if isinstance(count, int | float) else 0
    except Exception:
        return 0


async def _scroll_metrics(page: Any) -> dict[str, Any]:
    try:
        metrics = await page.evaluate(
            """() => ({
                scroll_y: Math.round(window.scrollY || document.documentElement.scrollTop || 0),
                inner_height: window.innerHeight || 0,
                scroll_height: document.documentElement.scrollHeight || document.body.scrollHeight || 0,
                at_bottom: (window.scrollY + window.innerHeight) >= ((document.documentElement.scrollHeight || document.body.scrollHeight || 0) - 24),
            })"""
        )
        return metrics if isinstance(metrics, dict) else {}
    except Exception:
        return {}


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
