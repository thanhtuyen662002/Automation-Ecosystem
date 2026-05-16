"""
core/tiktok_scraper.py — Real TikTok keyword-search scraper (v2).

Improvements over v1:
  - Author extracted from URL (/@username/) as reliable fallback
  - Likes: scan all <strong> tags per card, pick non-zero values
  - Error-page detection + auto-retry (refresh once)
  - Relaxed filter: keep if views > 50k OR caption contains keyword
  - More scroll cycles (up to 20) for higher coverage
  - Better wait: networkidle on retry, explicit wait for cards
  - Session note: run with headless=False once to log in and save cookies

Anti-detection:
  - Persistent browser profile per _SCRAPER_ACCOUNT_ID
  - All stealth patches from core.stealth
  - Gaussian random delays 1-3s between scrolls
  - Human-like wheel scroll

Output schema per video:
  {
    "video_url":  str,
    "author":     str,   (from URL if DOM extraction fails)
    "caption":    str,
    "views":      int,
    "likes":      int,
    "comments":   int,
    "thumbnail":  str,
    "keyword":    str,
    "scraped_at": int,
    "source":     "tiktok_real"
  }
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import random
import time
from typing import Any

from core.tiktok_search_extractor import (
    JS_EXTRACT_SEARCH_CARDS as _JS_EXTRACT_V2,
    JS_SEARCH_PAGE_STATE as _JS_CHECK_ERROR,
    author_from_url as _author_from_url,
    parse_count as _parse_count,
)

LOGGER = logging.getLogger("core.tiktok_scraper")

_SCRAPER_ACCOUNT_ID = "_tiktok_scraper_"
_SEARCH_URL         = "https://www.tiktok.com/search?q={keyword}"
_MIN_INTERVAL_S     = 25
_DEFAULT_MIN_VIEWS  = int(os.environ.get("TIKTOK_MIN_VIEWS", "10000"))
_DEFAULT_LIMIT      = int(os.environ.get("TIKTOK_SEARCH_MAX_RESULTS", "30"))
_SCROLL_MAX         = 20

_last_scrape: dict[str, float] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _human_scroll(page: Any, distance: int = 700) -> None:
    steps     = random.randint(4, 9)
    step_base = distance // steps
    for _ in range(steps):
        delta = step_base + random.randint(-40, 40)
        await page.mouse.wheel(0, delta)
        await asyncio.sleep(random.uniform(0.07, 0.20))


async def _delay(lo: float = 1.0, hi: float = 3.0) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


async def scrape_keyword(
    keyword:   str,
    limit:     int  = _DEFAULT_LIMIT,
    min_views: int  = _DEFAULT_MIN_VIEWS,
    headless:  bool = True,
) -> list[dict[str, Any]]:
    """
    Search TikTok for *keyword* and return real video metadata.

    Filter logic (Task 3 - relaxed):
      KEEP video if views > 50,000 OR caption contains any keyword word.
      Always reject if views < min_views (hard floor, default 10k).

    Returns list sorted by views DESC.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "playwright not installed: pip install playwright && "
            "playwright install chromium"
        ) from exc

    from core.browser_context import get_browser_data_dir
    from core.stealth import get_stealth_scripts

    # Rate limiting
    now_ts = time.time()
    since  = now_ts - _last_scrape.get(keyword, 0)
    if since < _MIN_INTERVAL_S:
        await asyncio.sleep(_MIN_INTERVAL_S - since)

    data_dir   = get_browser_data_dir(_SCRAPER_ACCOUNT_ID)
    search_url = _SEARCH_URL.format(keyword=keyword.replace(" ", "%20"))

    LOGGER.info(
        "tiktok_scraper_v2_start keyword=%s limit=%d min_views=%d",
        keyword, limit, min_views,
    )

    collected:  list[dict[str, Any]] = []
    seen_urls:  set[str]             = set()

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(data_dir),
            headless    = headless,
            viewport    = {"width": 1280, "height": 900},
            locale      = "en-US",
            timezone_id = "America/New_York",
            args        = [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-infobars",
                "--disable-breakpad",
            ],
        )

        try:
            for script in get_stealth_scripts(_SCRAPER_ACCOUNT_ID):
                await context.add_init_script(script)
        except Exception as exc:
            LOGGER.debug("stealth_apply_error error=%s", exc)

        pages = context.pages
        page  = pages[0] if pages else await context.new_page()

        try:
            # ── Navigate with networkidle for full render ──────────────────
            try:
                await page.goto(
                    search_url,
                    wait_until = "networkidle",
                    timeout    = 35_000,
                )
            except Exception:
                # networkidle may time out on heavy pages — that's OK
                await page.goto(search_url, wait_until="domcontentloaded", timeout=20_000)

            await _delay(3.0, 5.0)

            # ── Dismiss overlays ───────────────────────────────────────────
            for sel in [
                '[data-e2e="modal-close-inner-button"]',
                'button[class*="CloseButton"]',
                '[aria-label="Close"]',
                'div[class*="close-icon"]',
            ]:
                try:
                    btn = await page.query_selector(sel)
                    if btn:
                        await btn.click()
                        await _delay(0.3, 0.8)
                        break
                except Exception:
                    pass

            # ── Check for error page; retry once ──────────────────────────
            page_state = await page.evaluate(_JS_CHECK_ERROR)
            LOGGER.info(
                "tiktok_page_state has_error=%s needs_login=%s cards=%d",
                page_state["has_error"], page_state["needs_login"],
                page_state["card_count"],
            )

            if page_state["has_error"] and page_state["card_count"] == 0:
                LOGGER.warning(
                    "tiktok_error_page detected='%s' — retrying after delay",
                    page_state["error_text"],
                )
                await _delay(4.0, 7.0)
                await page.reload(wait_until="networkidle", timeout=30_000)
                await _delay(3.0, 5.0)
                page_state = await page.evaluate(_JS_CHECK_ERROR)
                LOGGER.info(
                    "tiktok_retry_state has_error=%s cards=%d",
                    page_state["has_error"], page_state["card_count"],
                )

            if page_state["needs_login"] and page_state["card_count"] == 0:
                LOGGER.warning(
                    "tiktok_login_required — profile has no session. "
                    "Run: scrape_keyword_sync(..., headless=False) to log in manually."
                )

            # ── Wait for video cards (even if error, partial cards may load) ─
            try:
                await page.wait_for_selector(
                    '[data-e2e="search_video-item"], '
                    'div[class*="DivItemContainerForSearch"], '
                    'div[class*="DivItemContainer"]',
                    timeout = 12_000,
                )
                await _delay(1.5, 2.5)
            except Exception:
                LOGGER.warning("tiktok_no_cards_after_wait keyword=%s", keyword)

            # ── Scroll + collect loop ──────────────────────────────────────
            kw_words = [w for w in keyword.lower().split() if len(w) > 2]

            for scroll_i in range(_SCROLL_MAX):
                raw_items: list[dict] = await page.evaluate(_JS_EXTRACT_V2)

                for item in raw_items:
                    url = item.get("video_url", "")
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)

                    views    = _parse_count(item.get("views_text",    "0"))
                    likes    = _parse_count(item.get("likes_text",    "0"))
                    comments = _parse_count(item.get("comments_text", "0"))

                    # Hard floor
                    if views < min_views:
                        continue

                    # Author: DOM first, URL fallback
                    author = item.get("author", "").strip()
                    if not author:
                        author = _author_from_url(url)

                    caption      = item.get("caption", "")
                    caption_lower = caption.lower()

                    # RELAXED FILTER (Task 3):
                    # Keep if views > 50k OR caption contains keyword word
                    kw_match = any(w in caption_lower for w in kw_words)
                    high_viral = views >= 50_000
                    if not kw_match and not high_viral:
                        continue

                    collected.append({
                        "video_url":  url,
                        "author":     author,
                        "caption":    caption,
                        "views":      views,
                        "likes":      likes,
                        "comments":   comments,
                        "thumbnail":  item.get("thumbnail", ""),
                        "keyword":    keyword,
                        "scraped_at": int(time.time()),
                        "source":     "tiktok_real",
                    })

                LOGGER.debug(
                    "tiktok_scroll scroll=%d seen_urls=%d collected=%d target=%d",
                    scroll_i, len(seen_urls), len(collected), limit,
                )

                if len(collected) >= limit:
                    break

                # Human scroll: alternate short/long scrolls
                dist = random.randint(400, 1100)
                await _human_scroll(page, distance=dist)
                await _delay(1.0, 3.0)

        except Exception as exc:
            LOGGER.error(
                "tiktok_scraper_error keyword=%s error=%s", keyword, exc,
                exc_info=True,
            )
        finally:
            try:
                await context.close()
            except Exception:
                pass

    _last_scrape[keyword] = time.time()

    # Sort by views DESC
    collected.sort(key=lambda x: -x["views"])
    result = collected[:limit]

    LOGGER.info(
        "tiktok_scraper_done keyword=%s collected=%d returned=%d",
        keyword, len(collected), len(result),
    )
    return result


# ── Sync wrapper ──────────────────────────────────────────────────────────────

def scrape_keyword_sync(
    keyword:   str,
    limit:     int  = _DEFAULT_LIMIT,
    min_views: int  = _DEFAULT_MIN_VIEWS,
    headless:  bool = True,
    timeout_s: int  = 150,
) -> list[dict[str, Any]]:
    """
    Synchronous wrapper — safe to call from sync or async contexts.
    Spawns a new thread with its own event loop.
    """
    def _run() -> list[dict[str, Any]]:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                scrape_keyword(keyword, limit=limit,
                               min_views=min_views, headless=headless)
            )
        finally:
            loop.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_run)
        try:
            return fut.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError:
            LOGGER.error(
                "tiktok_scraper_sync_timeout keyword=%s timeout=%ds",
                keyword, timeout_s,
            )
            return []
        except Exception as exc:
            LOGGER.error(
                "tiktok_scraper_sync_error keyword=%s error=%s", keyword, exc
            )
            return []


# ── One-time login helper ─────────────────────────────────────────────────────

def login_interactive() -> None:
    """
    Open a non-headless browser for manual TikTok login.
    Once you log in, cookies are saved to the persistent profile and
    all future headless scrapes will use the session automatically.

    Usage:
        from core.tiktok_scraper import login_interactive
        login_interactive()
    """
    import asyncio as _asyncio
    from playwright.sync_api import sync_playwright

    from core.browser_context import get_browser_data_dir
    from core.stealth import get_stealth_scripts

    data_dir = get_browser_data_dir(_SCRAPER_ACCOUNT_ID)
    print(f"Opening browser at: {data_dir}")
    print("Log in to TikTok manually, then close the browser window.")

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(data_dir),
            headless    = False,
            viewport    = {"width": 1280, "height": 900},
            locale      = "en-US",
            timezone_id = "America/New_York",
            args        = ["--disable-blink-features=AutomationControlled"],
        )
        for script in get_stealth_scripts(_SCRAPER_ACCOUNT_ID):
            ctx.add_init_script(script)

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.tiktok.com/login")

        print("Waiting for you to log in... (press Ctrl+C when done)")
        try:
            page.wait_for_url("**/foryou**", timeout=300_000)
            print("Login detected! Session saved.")
        except Exception:
            print("Timeout or window closed — session may or may not be saved.")
        finally:
            ctx.close()
