"""
execution/metrics_collector_playwright.py — Post-publish metrics collector.

After a post is published, revisits the post URL to scrape:
  - current view count
  - like count
  - comment count

Then pushes updates to:
  - core.metrics_store  (EMA update)
  - core.profit_engine  (revenue proxy via engagement proxy)

Tracked posts are stored in SQLite. The collector can be:
  - run manually: collect_all_due()
  - scheduled: called from your automation loop every N hours

Config:
    METRICS_COLLECTOR_DB  — SQLite path (default: data/metrics_collector.db)
    COLLECT_AFTER_HOURS   — hours after publish to first collect (default: 24)
    RECOLLECT_INTERVAL_H  — hours between re-collections (default: 48)
    MAX_COLLECT_CYCLES    — max collections per post (default: 3)

Public API:
    register_post(content_id, post_url, platform, account_id, niche)
    collect_all_due(headless)       → list[CollectResult]
    collect_one(post_record, headless) → CollectResult
    get_tracked_posts(status)       → list[dict]
    reset_collector()              # testing only
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("execution.metrics_collector_playwright")

# ── Config ────────────────────────────────────────────────────────────────────

_DEFAULT_DB          = Path("data") / "metrics_collector.db"
_COLLECT_AFTER_H     = int(os.environ.get("COLLECT_AFTER_HOURS",    "24"))
_RECOLLECT_INTERVAL_H = int(os.environ.get("RECOLLECT_INTERVAL_H",  "48"))
_MAX_COLLECT_CYCLES  = int(os.environ.get("MAX_COLLECT_CYCLES",      "3"))

def _db_path() -> Path:
    env = os.environ.get("METRICS_COLLECTOR_DB")
    return Path(env) if env else _DEFAULT_DB

# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS tracked_posts (
    content_id      TEXT PRIMARY KEY,
    post_url        TEXT NOT NULL DEFAULT '',
    platform        TEXT NOT NULL DEFAULT '',
    account_id      TEXT NOT NULL DEFAULT '',
    niche           TEXT NOT NULL DEFAULT '',
    published_at    REAL NOT NULL DEFAULT 0.0,
    last_collected  REAL NOT NULL DEFAULT 0.0,
    collect_count   INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'active',
    last_views      INTEGER NOT NULL DEFAULT 0,
    last_likes      INTEGER NOT NULL DEFAULT 0,
    last_comments   INTEGER NOT NULL DEFAULT 0
);
"""

_local     = threading.local()
_init_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        db = _db_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(db), check_same_thread=False, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        with _init_lock:
            con.executescript(_DDL)
            con.commit()
        _local.conn = con
    return _local.conn


def _exec(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    c = _conn()
    cur = c.execute(sql, params)
    c.commit()
    return cur


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class CollectResult:
    content_id:    str
    post_url:      str
    platform:      str
    views:         int   = 0
    likes:         int   = 0
    comments:      int   = 0
    success:       bool  = False
    error:         str   = ""
    metrics_updated: bool = False
    profit_updated:  bool = False


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


async def _delay(lo: float = 1.0, hi: float = 3.0) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


# ── Scraping ──────────────────────────────────────────────────────────────────

async def _collect_tiktok(page: Any, url: str) -> tuple[int, int, int]:
    """Returns (views, likes, comments)."""
    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    await _delay(2.0, 4.0)
    data = await page.evaluate("""
    () => {
        const sel = (s) => {
            const el = document.querySelector(s);
            return el ? el.innerText : '0';
        };
        return {
            views:    sel('[data-e2e="browse-video-like-count"], [class*="StrongVideoCount"]'),
            likes:    sel('[data-e2e="like-count"]'),
            comments: sel('[data-e2e="comment-count"]'),
        };
    }
    """)
    if not data:
        return 0, 0, 0
    return (
        _parse_count(data.get("views", "0")),
        _parse_count(data.get("likes", "0")),
        _parse_count(data.get("comments", "0")),
    )


async def _collect_facebook(page: Any, url: str) -> tuple[int, int, int]:
    """Returns (views, likes, comments)."""
    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    await _delay(2.0, 4.0)
    text = await page.evaluate("() => document.body ? document.body.innerText : ''")
    views = comments = likes = 0
    if text:
        vm = re.search(r"([\d,\.]+[KkMm]?)\s*[Vv]iews?", text)
        lm = re.search(r"([\d,\.]+[KkMm]?)\s*[Ll]ikes?", text)
        cm = re.search(r"([\d,\.]+[KkMm]?)\s*[Cc]omments?", text)
        if vm:
            views    = _parse_count(vm.group(1))
        if lm:
            likes    = _parse_count(lm.group(1))
        if cm:
            comments = _parse_count(cm.group(1))
    return views, likes, comments


# ── Core collector ────────────────────────────────────────────────────────────

async def _run_collect(
    posts:    list[dict[str, Any]],
    headless: bool,
) -> list[CollectResult]:
    results: list[CollectResult] = []
    if not posts:
        return results

    try:
        from playwright.async_api import async_playwright   # type: ignore[import]
    except ImportError:
        LOGGER.warning("playwright not installed — metrics collection skipped")
        return [CollectResult(content_id=p["content_id"], post_url=p["post_url"],
                              platform=p["platform"], error="playwright_not_installed")
                for p in posts]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx  = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = await ctx.new_page()
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        for post in posts:
            content_id = post["content_id"]
            post_url   = post["post_url"]
            platform   = post["platform"]
            niche      = post.get("niche", "entertainment")
            cr = CollectResult(content_id=content_id, post_url=post_url, platform=platform)

            try:
                if platform == "tiktok":
                    views, likes, comments = await _collect_tiktok(page, post_url)
                elif platform == "facebook":
                    views, likes, comments = await _collect_facebook(page, post_url)
                else:
                    views = likes = comments = 0

                cr.views    = views
                cr.likes    = likes
                cr.comments = comments
                cr.success  = True

                # ── Push to metrics_store ──────────────────────────────────
                try:
                    from core.metrics_store import get_metrics_store
                    store = get_metrics_store()
                    engagement = (likes + comments * 2) / max(1, views)
                    store.update(
                        account_id = post.get("account_id", content_id),
                        ban        = False,
                        success    = views > 0,
                        reward     = min(1.0, engagement * 10),
                    )
                    cr.metrics_updated = True
                except Exception as me:
                    LOGGER.debug("metrics_store_update_error content_id=%s error=%s",
                                 content_id, me)

                # ── Push to profit_engine ──────────────────────────────────
                try:
                    from core.profit_engine import update_profit
                    # Revenue proxy: views × engagement × 0.001 (CPM-style estimate)
                    est_revenue = views * engagement * 0.001
                    update_profit(
                        content_id = content_id,
                        niche      = niche,
                        revenue    = est_revenue,
                        cost       = 0.0,   # cost already recorded at publish time
                    )
                    cr.profit_updated = True
                except Exception as pe:
                    LOGGER.debug("profit_engine_update_error content_id=%s error=%s",
                                 content_id, pe)

                # Update DB
                new_count = post.get("collect_count", 0) + 1
                status    = "completed" if new_count >= _MAX_COLLECT_CYCLES else "active"
                _exec(
                    "UPDATE tracked_posts SET last_collected=?, collect_count=?, status=?,"
                    " last_views=?, last_likes=?, last_comments=? WHERE content_id=?",
                    (time.time(), new_count, status, views, likes, comments, content_id),
                )
                LOGGER.info(
                    "metrics_collected content_id=%s views=%d likes=%d comments=%d",
                    content_id, views, likes, comments,
                )

            except Exception as exc:
                cr.error = str(exc)
                LOGGER.warning("metrics_collect_error content_id=%s error=%s",
                               content_id, exc)
                _exec(
                    "UPDATE tracked_posts SET last_collected=? WHERE content_id=?",
                    (time.time(), content_id),
                )

            results.append(cr)
            await _delay(2.0, 5.0)

        await browser.close()
    return results


# ── Public API ────────────────────────────────────────────────────────────────

def register_post(
    content_id: str,
    post_url:   str,
    platform:   str,
    account_id: str = "",
    niche:      str = "entertainment",
) -> None:
    """Register a published post for metrics tracking."""
    if not post_url:
        return
    try:
        _exec(
            "INSERT OR IGNORE INTO tracked_posts"
            " (content_id, post_url, platform, account_id, niche, published_at)"
            " VALUES (?,?,?,?,?,?)",
            (content_id, post_url, platform, account_id, niche, time.time()),
        )
        LOGGER.debug("metrics_post_registered content_id=%s url=%s", content_id, post_url)
    except Exception as exc:
        LOGGER.warning("metrics_register_error error=%s", exc)


def collect_all_due(headless: bool = True) -> list[CollectResult]:
    """
    Collect metrics for all posts that are due.

    A post is due if:
      - status='active'
      - collect_count < MAX_COLLECT_CYCLES
      - (last_collected == 0 AND published_at is old enough)
        OR (last_collected > 0 AND interval has elapsed)

    Returns CollectResult per post processed.
    """
    now           = time.time()
    first_due_ts  = now - _COLLECT_AFTER_H * 3600
    recollect_ts  = now - _RECOLLECT_INTERVAL_H * 3600

    try:
        rows = _conn().execute(
            """SELECT * FROM tracked_posts
               WHERE status='active'
                 AND collect_count < ?
                 AND (
                   (last_collected = 0 AND published_at <= ?)
                   OR (last_collected > 0 AND last_collected <= ?)
                 )
               ORDER BY published_at ASC""",
            (_MAX_COLLECT_CYCLES, first_due_ts, recollect_ts),
        ).fetchall()
    except Exception as exc:
        LOGGER.warning("metrics_due_query_error error=%s", exc)
        return []

    posts = [dict(r) for r in rows]
    if not posts:
        LOGGER.debug("metrics_no_due_posts")
        return []

    LOGGER.info("metrics_collecting count=%d", len(posts))
    try:
        loop = asyncio.new_event_loop()
        return loop.run_until_complete(_run_collect(posts, headless))
    except Exception as exc:
        LOGGER.warning("metrics_collect_all_error error=%s", exc)
        return []


def collect_one(
    post_record: dict[str, Any],
    headless:    bool = True,
) -> CollectResult:
    """Collect metrics for a single post record dict."""
    try:
        loop = asyncio.new_event_loop()
        results = loop.run_until_complete(_run_collect([post_record], headless))
        return results[0] if results else CollectResult(
            content_id=post_record.get("content_id", ""),
            post_url=post_record.get("post_url", ""),
            platform=post_record.get("platform", ""),
            error="no_result",
        )
    except Exception as exc:
        return CollectResult(
            content_id=post_record.get("content_id", ""),
            post_url=post_record.get("post_url", ""),
            platform=post_record.get("platform", ""),
            error=str(exc),
        )


def get_tracked_posts(status: str = "active") -> list[dict[str, Any]]:
    """Return tracked posts by status."""
    try:
        rows = _conn().execute(
            "SELECT * FROM tracked_posts WHERE status=? ORDER BY published_at DESC",
            (status,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def reset_collector() -> None:
    try:
        con = _conn()
        con.executescript("DELETE FROM tracked_posts;")
        con.commit()
    except Exception:
        pass
    if hasattr(_local, "conn"):
        try:
            _local.conn.close()
        except Exception:
            pass
        _local.conn = None
