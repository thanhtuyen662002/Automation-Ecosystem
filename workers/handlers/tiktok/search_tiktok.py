"""Handler for TikTok search through a logged-in AdsPower profile."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import quote

from core.browser_providers import (
    BROWSER_PROVIDER_ADSPOWER_MANUAL,
    account_metadata,
    make_browser_provider,
    resolve_browser_provider,
)
from core.tiktok_search_extractor import (
    JS_EXTRACT_SEARCH_CARDS,
    JS_SEARCH_PAGE_STATE,
    SEARCH_CARD_SELECTOR,
    normalize_tiktok_search_items,
)
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
    require_login = _env_bool("TIKTOK_SEARCH_REQUIRE_LOGIN", default=True)
    allow_photo = _env_bool("TIKTOK_SEARCH_ALLOW_PHOTO", default=False)

    LOGGER.info(
        "tiktok_ads_power_search_start",
        extra={
            "event": "tiktok_ads_power_search_start",
            "account_id": account_id,
            "keyword_count": len(keywords),
            "max_results": max_results,
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
        account = await database.get_account(account_id)
        if account is None:
            raise FatalDependencyError(f"Account {account_id} not found")

        metadata = account_metadata(account)
        account_for_provider = {**account, "account_id": account_id, "metadata": metadata}

        platform = str(account.get("platform") or "").strip().lower()
        if platform != "tiktok":
            raise FatalDependencyError(f"Account {account_id} must be a TikTok account")

        browser_provider = resolve_browser_provider(account_for_provider)
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
        provider = make_browser_provider(account_for_provider, session=session, identity_profile=None)

        await random_jitter(1.0, 3.0)

        all_videos: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        async with _playwright() as pw:
            async with provider.open_publisher_context(pw, headless=False) as (context, page, _opened_profile):
                if require_login and not await _has_tiktok_auth_signal(page, context):
                    raise FatalDependencyError(
                        f"SESSION_NOT_CONNECTED for account {account_id}: AdsPower profile has no TikTok auth signal"
                    )

                for keyword in keywords[:5]:
                    keyword_videos = await _search_keyword_with_page(
                        page,
                        context,
                        keyword=keyword,
                        account_id=account_id,
                        max_results=max_results,
                        scroll_max=scroll_max,
                        stagnant_limit=stagnant_limit,
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
    require_login: bool,
    allow_photo: bool,
) -> list[dict[str, Any]]:
    search_url = _SEARCH_URL.format(keyword=quote(keyword, safe=""))
    started_at = time.monotonic()
    videos: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    stagnant_rounds = 0

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
        normalized = normalize_tiktok_search_items(
            raw_items,
            keyword,
            source=_SOURCE,
            allow_photo=allow_photo,
        )

        before_count = len(videos)
        for video in normalized:
            url = str(video.get("url") or "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                videos.append(video)

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
                "new_videos": new_count,
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
            "elapsed_seconds": round(time.monotonic() - started_at, 1),
            "source": _SOURCE,
        },
    )
    return videos[:max_results]


async def _extract_raw_items(page: Any) -> list[dict[str, Any]]:
    raw = await page.evaluate(JS_EXTRACT_SEARCH_CARDS)
    return raw if isinstance(raw, list) else []


async def _safe_page_state(page: Any) -> dict[str, Any]:
    try:
        state = await page.evaluate(JS_SEARCH_PAGE_STATE)
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


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
