"""
execution/content_scraper.py — Single-video metadata extractor.

Given a video URL (TikTok, Facebook Reel), extracts:
  - canonical video URL (for download)
  - full caption / description
  - view count, like count, comment count
  - author handle
  - hashtags
  - duration (if available)

Uses Playwright for JS-rendered pages. Falls back to HTTP for simple cases.

Public API:
    scrape(url, headless, account)       → ScrapedContent | None
    scrape_batch(urls, headless, account) → list[ScrapedContent]

No caching (caller should deduplicate). Never raises.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

LOGGER = logging.getLogger("execution.content_scraper")

_DELAY_MIN    = 1.0
_DELAY_MAX    = 3.5
_MAX_RETRIES  = int(os.environ.get("SCRAPER_MAX_RETRIES", "3"))
_RETRY_BASE_S = 2.0

# Rotating UA pool (shared with trend_crawler for consistency)
_USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
]


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class ScrapedContent:
    url:           str
    canonical_url: str       = ""
    caption:       str       = ""
    view_count:    int       = 0
    like_count:    int       = 0
    comment_count: int       = 0
    share_count:   int       = 0
    author:        str       = ""
    platform:      str       = ""
    hashtags:      list[str] = field(default_factory=list)
    duration_s:    float     = 0.0
    scraped_at:    float     = 0.0
    error:         str       = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def success(self) -> bool:
        return bool(self.canonical_url or self.caption) and not self.error


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_count(text: str) -> int:
    if not text:
        return 0
    t = text.strip().upper().replace(",", "").replace(" ", "")
    try:
        if t.endswith("M"):
            return int(float(t[:-1]) * 1_000_000)
        if t.endswith("K"):
            return int(float(t[:-1]) * 1_000)
        return int(re.sub(r"[^\d]", "", t) or "0")
    except Exception:
        return 0


def _detect_platform(url: str) -> str:
    if "tiktok.com" in url:
        return "tiktok"
    if "facebook.com" in url or "fb.com" in url:
        return "facebook"
    if "instagram.com" in url:
        return "instagram"
    return "unknown"


async def _delay(lo: float = _DELAY_MIN, hi: float = _DELAY_MAX) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


async def _with_retry(coro_fn: Any, *args: Any, label: str = "op") -> Any:
    """Retry a coroutine up to _MAX_RETRIES times with exponential back-off."""
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return await coro_fn(*args)
        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                wait = _RETRY_BASE_S * (2 ** (attempt - 1)) + random.uniform(0.5, 1.5)
                LOGGER.debug("%s_retry attempt=%d wait=%.1fs", label, attempt, wait)
                await asyncio.sleep(wait)
    raise last_exc  # type: ignore[misc]


# ── TikTok scraper ────────────────────────────────────────────────────────────

async def _scrape_tiktok_page(page: Any, url: str) -> ScrapedContent:
    result = ScrapedContent(url=url, platform="tiktok", scraped_at=time.time())
    try:
        await _with_retry(
            lambda: page.goto(url, wait_until="domcontentloaded", timeout=30_000),
            label="tiktok_page_load",
        )
        await _delay(2.5, 5.0)

        # Extract via JS — TikTok is heavily JS-rendered
        data = await page.evaluate("""
        () => {
            const cap  = document.querySelector('[data-e2e="browse-video-desc"], [class*="SpanText"]');
            const views = document.querySelector('[data-e2e="browse-video-like-count"], [class*="StrongVideoCount"]');
            const likes = document.querySelector('[data-e2e="like-count"], [class*="StrongLikes"]');
            const cmts  = document.querySelector('[data-e2e="comment-count"]');
            const auth  = document.querySelector('[data-e2e="browse-username"], [class*="AvatarWrapper"] a');
            // Try to find the video element src
            const vid   = document.querySelector('video');

            return {
                caption:       cap    ? cap.innerText           : document.title,
                view_count:    views  ? views.innerText         : '0',
                like_count:    likes  ? likes.innerText         : '0',
                comment_count: cmts   ? cmts.innerText          : '0',
                author:        auth   ? (auth.innerText || auth.href) : '',
                video_src:     vid    ? (vid.src || vid.currentSrc) : '',
                canonical_url: window.location.href,
            };
        }
        """)
        if data:
            caption  = data.get("caption", "")
            result.canonical_url  = data.get("canonical_url", url)
            result.caption        = caption[:500]
            result.view_count     = _parse_count(data.get("view_count", "0"))
            result.like_count     = _parse_count(data.get("like_count", "0"))
            result.comment_count  = _parse_count(data.get("comment_count", "0"))
            result.author         = data.get("author", "")
            result.hashtags       = re.findall(r"#(\w+)", caption)
            # canonical_url is the TikTok watch page; yt-dlp can download from it
    except Exception as exc:
        result.error = str(exc)
        LOGGER.warning("tiktok_scrape_error url=%s error=%s", url, exc)
    return result


# ── Facebook scraper ──────────────────────────────────────────────────────────

async def _scrape_facebook_page(page: Any, url: str) -> ScrapedContent:
    result = ScrapedContent(url=url, platform="facebook", scraped_at=time.time())
    try:
        await _with_retry(
            lambda: page.goto(url, wait_until="domcontentloaded", timeout=30_000),
            label="facebook_page_load",
        )
        await _delay(2.5, 5.0)

        data = await page.evaluate("""
        () => {
            const texts = Array.from(document.querySelectorAll('[dir="auto"]'))
                               .map(el => el.innerText).filter(t => t.length > 10);
            const meta  = document.querySelector('meta[property="og:description"]');
            const title = document.querySelector('meta[property="og:title"]');
            const vurl  = document.querySelector('meta[property="og:video"]');
            return {
                caption:       meta   ? meta.getAttribute('content')  : (texts[0] || ''),
                title:         title  ? title.getAttribute('content') : '',
                canonical_url: vurl   ? vurl.getAttribute('content')  : window.location.href,
                page_text:     document.body ? document.body.innerText.slice(0, 2000) : '',
            };
        }
        """)
        if data:
            caption = data.get("caption") or data.get("title", "")
            result.canonical_url = data.get("canonical_url", url)
            result.caption       = caption[:500]
            result.hashtags      = re.findall(r"#(\w+)", caption)

            # Try to parse counts from page text
            page_text = data.get("page_text", "")
            view_match = re.search(r"([\d,\.]+[KkMm]?)\s*[Vv]iews?", page_text)
            like_match = re.search(r"([\d,\.]+[KkMm]?)\s*[Ll]ikes?", page_text)
            if view_match:
                result.view_count = _parse_count(view_match.group(1))
            if like_match:
                result.like_count = _parse_count(like_match.group(1))

    except Exception as exc:
        result.error = str(exc)
        LOGGER.warning("facebook_scrape_error url=%s error=%s", url, exc)
    return result


# ── Public API ────────────────────────────────────────────────────────────────

async def _scrape_one(page: Any, url: str) -> ScrapedContent:
    platform = _detect_platform(url)
    if platform == "tiktok":
        return await _scrape_tiktok_page(page, url)
    if platform == "facebook":
        return await _scrape_facebook_page(page, url)
    # Generic fallback
    result = ScrapedContent(url=url, platform=platform, scraped_at=time.time())
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        data = await page.evaluate("""
        () => {
            const m = document.querySelector('meta[property="og:description"]');
            const v = document.querySelector('meta[property="og:video"]');
            return {
                caption: m ? m.getAttribute('content') : document.title,
                canonical_url: v ? v.getAttribute('content') : window.location.href,
            };
        }
        """)
        result.caption       = (data or {}).get("caption", "")[:500]
        result.canonical_url = (data or {}).get("canonical_url", url)
    except Exception as exc:
        result.error = str(exc)
    return result


async def _run_scrape_batch(
    urls:     list[str],
    headless: bool,
    account:  dict[str, Any] | None,
) -> list[ScrapedContent]:
    try:
        from playwright.async_api import async_playwright   # type: ignore[import]
    except ImportError:
        LOGGER.warning("playwright not installed")
        return [ScrapedContent(url=u, error="playwright_not_installed") for u in urls]

    results: list[ScrapedContent] = []
    session_ua = random.choice(_USER_AGENTS)

    async with async_playwright() as pw:
        launch_args: dict[str, Any] = {
            "headless": headless,
            "args": ["--disable-blink-features=AutomationControlled",
                     "--no-sandbox", "--disable-dev-shm-usage",
                     "--disable-infobars"],
        }
        proxy = (account or {}).get("proxy")
        if proxy:
            launch_args["proxy"] = {"server": proxy}

        browser = await pw.chromium.launch(**launch_args)
        ctx     = await browser.new_context(
            user_agent=session_ua,
            viewport={"width": random.choice([1280, 1366, 1440]),
                      "height": random.choice([800, 900, 1080])},
            locale=random.choice(["en-US", "en-GB", "en-AU"]),
            timezone_id=random.choice(["America/New_York", "America/Chicago",
                                       "America/Los_Angeles"]),
        )
        page = await ctx.new_page()
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});\n"
            "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});\n"
            "window.chrome = {runtime: {}};"
        )
        LOGGER.debug("scraper_session ua=%s proxy=%s", session_ua[:40], bool(proxy))

        for url in urls:
            r = await _scrape_one(page, url)
            results.append(r)
            await _delay(1.5, 3.5)

        await browser.close()
    return results


def scrape(
    url:      str,
    headless: bool = True,
    account:  dict[str, Any] | None = None,
) -> ScrapedContent | None:
    """Scrape a single video URL. Returns ScrapedContent or None on error."""
    results = scrape_batch([url], headless=headless, account=account)
    return results[0] if results else None


def scrape_batch(
    urls:     list[str],
    headless: bool = True,
    account:  dict[str, Any] | None = None,
) -> list[ScrapedContent]:
    """Scrape multiple URLs in a single browser session. Never raises."""
    if not urls:
        return []
    try:
        loop = asyncio.new_event_loop()
        return loop.run_until_complete(_run_scrape_batch(urls, headless, account))
    except Exception as exc:
        LOGGER.warning("scrape_batch_error error=%s", exc)
        return [ScrapedContent(url=u, error=str(exc)) for u in urls]
