"""
execution/trend_crawler.py — Trend & content input layer.

Scrapes TikTok and Facebook for trending content by keyword/hashtag.
Uses Playwright (async). Returns structured candidates compatible with
the content_decision.ContentCandidate format and feed_engine inputs.

Public API:
    crawl_tiktok(keywords, max_results, headless)    → list[RawCandidate]
    crawl_facebook(keywords, max_results, headless)  → list[RawCandidate]
    crawl(platform, keywords, max_results, headless) → list[RawCandidate]

RawCandidate fields:
    source_url, caption, view_count, like_count, author,
    platform, niche (inferred), hashtags, scraped_at

Config:
    TREND_DB  — SQLite cache path (default: data/trend_cache.db)
    TREND_CACHE_TTL_S — seconds before re-scraping same keyword (default: 3600)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("execution.trend_crawler")

# ── Config ────────────────────────────────────────────────────────────────────

_DEFAULT_DB   = Path("data") / "trend_cache.db"
_CACHE_TTL_S  = int(os.environ.get("TREND_CACHE_TTL_S", "3600"))
_DELAY_MIN    = 1.5
_DELAY_MAX    = 4.0
_SCROLL_PAUSE = 1.2
_MAX_RETRIES  = int(os.environ.get("CRAWL_MAX_RETRIES", "3"))
_RETRY_BASE_S = 2.0

# Rotating user-agent pool (12 realistic Chrome variants)
_USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
]

# Niche keyword detection map
_NICHE_KEYWORDS: dict[str, list[str]] = {
    "tech":          ["tech", "coding", "programming", "software", "ai", "gadget", "phone"],
    "fitness":       ["fitness", "gym", "workout", "health", "diet", "exercise", "training"],
    "finance":       ["money", "investing", "crypto", "finance", "wealth", "income", "profit"],
    "entertainment": ["funny", "comedy", "meme", "viral", "dance", "music", "celebrity"],
    "food":          ["food", "recipe", "cooking", "restaurant", "eat", "meal", "baking"],
    "travel":        ["travel", "trip", "vacation", "explore", "adventure", "hotel", "flight"],
}


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class RawCandidate:
    source_url:  str
    caption:     str       = ""
    view_count:  int       = 0
    like_count:  int       = 0
    author:      str       = ""
    platform:    str       = ""
    niche:       str       = "entertainment"
    hashtags:    list[str] = field(default_factory=list)
    scraped_at:  float     = 0.0
    meta:        dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_content_candidate_dict(self) -> dict[str, Any]:
        """Convert to format expected by content_decision.ContentCandidate."""
        return {
            "content_id":       hashlib.sha256(self.source_url.encode()).hexdigest()[:12],
            "mode":             "reup",
            "platform":         self.platform,
            "niche":            self.niche,
            "source_url":       self.source_url,
            "caption":          self.caption,
            "hashtags":         self.hashtags,
            "view_count":       self.view_count,
            "trend_score":      min(1.0, self.view_count / 1_000_000),
            "novelty_score":    0.7,
            "match_score":      0.6,
            "production_cost":  0.15,
        }


# ── Niche inference ───────────────────────────────────────────────────────────

def _infer_niche(text: str) -> str:
    text_lower = text.lower()
    for niche, keywords in _NICHE_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return niche
    return "entertainment"


def _parse_count(text: str) -> int:
    """Parse '1.2M', '45K', '3000' → int."""
    if not text:
        return 0
    text = text.strip().upper().replace(",", "")
    try:
        if text.endswith("M"):
            return int(float(text[:-1]) * 1_000_000)
        if text.endswith("K"):
            return int(float(text[:-1]) * 1_000)
        return int(text)
    except Exception:
        return 0


# ── SQLite cache ──────────────────────────────────────────────────────────────

_local     = threading.local()
_init_lock = threading.Lock()

_DDL = """
CREATE TABLE IF NOT EXISTS trend_cache (
    cache_key    TEXT PRIMARY KEY,
    result_json  TEXT NOT NULL DEFAULT '[]',
    scraped_at   REAL NOT NULL DEFAULT 0.0
);
"""


def _cache_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        db = Path(os.environ.get("TREND_DB", str(_DEFAULT_DB)))
        db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(db), check_same_thread=False, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        with _init_lock:
            con.executescript(_DDL)
            con.commit()
        _local.conn = con
    return _local.conn


def _cache_get(key: str) -> list[dict] | None:
    try:
        row = _cache_conn().execute(
            "SELECT result_json, scraped_at FROM trend_cache WHERE cache_key=?", (key,)
        ).fetchone()
        if row and (time.time() - row["scraped_at"]) < _CACHE_TTL_S:
            return json.loads(row["result_json"])
    except Exception:
        pass
    return None


def _cache_set(key: str, results: list[dict]) -> None:
    try:
        con = _cache_conn()
        con.execute(
            "INSERT OR REPLACE INTO trend_cache (cache_key, result_json, scraped_at)"
            " VALUES (?,?,?)",
            (key, json.dumps(results), time.time()),
        )
        con.commit()
    except Exception:
        pass


# ── Human-like browser helpers ────────────────────────────────────────────────

async def _delay(lo: float = _DELAY_MIN, hi: float = _DELAY_MAX) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


async def _scroll_down(page: Any, times: int = 3) -> None:
    for _ in range(times):
        await page.mouse.wheel(0, random.randint(600, 1200))
        # Random pause between scrolls — humans don't scroll at constant speed
        await asyncio.sleep(random.uniform(_SCROLL_PAUSE, _SCROLL_PAUSE * 2.5))
        if random.random() < 0.3:
            # Occasional upward micro-scroll (natural behaviour)
            await page.mouse.wheel(0, -random.randint(50, 200))
            await asyncio.sleep(random.uniform(0.3, 0.8))


async def _with_retry(coro_fn: Any, *args: Any, label: str = "op") -> Any:
    """
    Retry an async coroutine up to _MAX_RETRIES times with exponential back-off.
    Propagates the last exception if all attempts fail.
    """
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return await coro_fn(*args)
        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                wait = _RETRY_BASE_S * (2 ** (attempt - 1)) + random.uniform(0.5, 2.0)
                LOGGER.debug("%s_retry attempt=%d wait=%.1fs error=%s", label, attempt, wait, exc)
                await asyncio.sleep(wait)
    raise last_exc  # type: ignore[misc]


# ── TikTok crawler ────────────────────────────────────────────────────────────

async def _scrape_tiktok(
    page: Any,
    keyword: str,
    max_results: int,
) -> list[RawCandidate]:
    """Scrape TikTok search results for a keyword."""
    results: list[RawCandidate] = []
    url = f"https://www.tiktok.com/search?q={keyword.replace(' ', '%20')}&type=video"

    try:
        await _with_retry(
            lambda: page.goto(url, wait_until="domcontentloaded", timeout=30_000),
            label="tiktok_goto",
        )
        await _delay(2.5, 6.0)
        await _scroll_down(page, times=random.randint(3, 6))
        await _delay(1.0, 3.0)

        # TikTok renders video cards — extract via JS for reliability
        items = await page.evaluate("""
        () => {
            const cards = document.querySelectorAll(
                'div[data-e2e="search_video-item"], div[class*="DivWrapper"]'
            );
            const out = [];
            cards.forEach(c => {
                const link  = c.querySelector('a[href*="/video/"]');
                const views = c.querySelector('[class*="StrongVideoCount"], [data-e2e*="video-views"]');
                const cap   = c.querySelector('[class*="SpanText"], [data-e2e*="video-desc"]');
                const auth  = c.querySelector('[data-e2e*="search-card-user-unique-id"]');
                if (link) {
                    out.push({
                        url:    link.href,
                        views:  views ? views.innerText : '0',
                        caption: cap  ? cap.innerText   : '',
                        author: auth  ? auth.innerText  : '',
                    });
                }
            });
            return out;
        }
        """)

        for item in (items or []):
            if len(results) >= max_results:
                break
            src_url = item.get("url", "")
            if not src_url or "/video/" not in src_url:
                continue
            caption  = item.get("caption", "")
            tags     = re.findall(r"#(\w+)", caption)
            results.append(RawCandidate(
                source_url = src_url,
                caption    = caption,
                view_count = _parse_count(item.get("views", "0")),
                author     = item.get("author", ""),
                platform   = "tiktok",
                niche      = _infer_niche(f"{keyword} {caption}"),
                hashtags   = tags,
                scraped_at = time.time(),
            ))

        LOGGER.info("tiktok_crawl_done keyword=%s found=%d", keyword, len(results))

    except Exception as exc:
        LOGGER.warning("tiktok_crawl_error keyword=%s error=%s", keyword, exc)

    return results


# ── Facebook crawler ──────────────────────────────────────────────────────────

async def _scrape_facebook(
    page: Any,
    keyword: str,
    max_results: int,
) -> list[RawCandidate]:
    """Scrape Facebook Reels search for a keyword."""
    results: list[RawCandidate] = []
    url = f"https://www.facebook.com/search/reels/?q={keyword.replace(' ', '%20')}"

    try:
        await _with_retry(
            lambda: page.goto(url, wait_until="domcontentloaded", timeout=30_000),
            label="facebook_goto",
        )
        await _delay(2.5, 6.0)
        await _scroll_down(page, times=random.randint(4, 7))
        await _delay(1.5, 3.0)

        items = await page.evaluate("""
        () => {
            const out = [];
            document.querySelectorAll('a[href*="/reel/"], a[href*="/videos/"]').forEach(a => {
                const card = a.closest('[data-pagelet], [role="article"]') || a.parentElement;
                const cap  = card ? (card.innerText || '') : '';
                if (a.href && !out.find(x => x.url === a.href)) {
                    out.push({ url: a.href, caption: cap.slice(0, 500) });
                }
            });
            return out.slice(0, 30);
        }
        """)

        for item in (items or []):
            if len(results) >= max_results:
                break
            src_url = item.get("url", "")
            if not src_url:
                continue
            caption = item.get("caption", "")
            tags    = re.findall(r"#(\w+)", caption)
            results.append(RawCandidate(
                source_url = src_url,
                caption    = caption[:300],
                platform   = "facebook",
                niche      = _infer_niche(f"{keyword} {caption}"),
                hashtags   = tags,
                scraped_at = time.time(),
            ))

        LOGGER.info("facebook_crawl_done keyword=%s found=%d", keyword, len(results))

    except Exception as exc:
        LOGGER.warning("facebook_crawl_error keyword=%s error=%s", keyword, exc)

    return results


# ── Public API ────────────────────────────────────────────────────────────────

async def _run_crawl(
    platform:    str,
    keywords:    list[str],
    max_results: int,
    headless:    bool,
    account:     dict[str, Any] | None,
) -> list[RawCandidate]:
    """Internal async crawl runner."""
    try:
        from playwright.async_api import async_playwright   # type: ignore[import]
    except ImportError:
        LOGGER.warning("playwright not installed — cannot crawl")
        return []

    all_results: list[RawCandidate] = []
    # Pick a random UA for this session
    session_ua = random.choice(_USER_AGENTS)

    async with async_playwright() as pw:
        launch_args: dict[str, Any] = {
            "headless": headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
            ],
        }
        proxy = (account or {}).get("proxy")
        if proxy:
            launch_args["proxy"] = {"server": proxy}

        browser = await pw.chromium.launch(**launch_args)
        ctx     = await browser.new_context(
            user_agent=session_ua,
            viewport={"width": random.choice([1280, 1366, 1440, 1920]),
                      "height": random.choice([800, 900, 960, 1080])},
            locale=random.choice(["en-US", "en-GB", "en-AU"]),
            timezone_id=random.choice(["America/New_York", "America/Chicago",
                                       "America/Los_Angeles", "Europe/London"]),
        )
        page = await ctx.new_page()
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});\n"
            "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});\n"
            "window.chrome = {runtime: {}};"
        )
        LOGGER.debug("crawl_session ua=%s proxy=%s", session_ua[:40], bool(proxy))

        per_keyword = max(1, max_results // max(1, len(keywords)))
        for kw in keywords:
            cache_key = f"{platform}:{kw}"
            cached    = _cache_get(cache_key)
            if cached is not None:
                LOGGER.debug("trend_cache_hit keyword=%s", kw)
                all_results.extend(
                    RawCandidate(**{k: v for k, v in c.items()
                                   if k in RawCandidate.__dataclass_fields__})
                    for c in cached
                )
                continue

            if platform == "tiktok":
                items = await _scrape_tiktok(page, kw, per_keyword)
            else:
                items = await _scrape_facebook(page, kw, per_keyword)

            _cache_set(cache_key, [i.to_dict() for i in items])
            all_results.extend(items)
            # Variable inter-keyword pause — avoid rhythm detection
            await _delay(random.uniform(3.0, 8.0), random.uniform(8.0, 15.0))

        await browser.close()

    return all_results[:max_results]


def crawl(
    platform:    str,
    keywords:    list[str],
    max_results: int = 20,
    headless:    bool = True,
    account:     dict[str, Any] | None = None,
) -> list[RawCandidate]:
    """
    Synchronous crawl wrapper. Returns list[RawCandidate]. Never raises.

    platform: "tiktok" | "facebook"
    keywords: list of search terms / hashtags
    """
    if not keywords:
        return []
    try:
        loop = asyncio.new_event_loop()
        return loop.run_until_complete(
            _run_crawl(platform, keywords, max_results, headless, account)
        )
    except Exception as exc:
        LOGGER.warning("crawl_error platform=%s error=%s", platform, exc)
        return []


def crawl_tiktok(
    keywords:    list[str],
    max_results: int = 20,
    headless:    bool = True,
    account:     dict[str, Any] | None = None,
) -> list[RawCandidate]:
    return crawl("tiktok", keywords, max_results, headless, account)


def crawl_facebook(
    keywords:    list[str],
    max_results: int = 20,
    headless:    bool = True,
    account:     dict[str, Any] | None = None,
) -> list[RawCandidate]:
    return crawl("facebook", keywords, max_results, headless, account)
