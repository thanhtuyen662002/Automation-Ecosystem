"""
execution/content_memory.py — Content Memory System.

Persistent store for all processed content. Prevents duplicate posting,
enables intelligent reup selection by tracking performance over time.

SQLite-backed. Path: CONTENT_MEMORY_DB env var (default: data/content_memory.db).

Public API:
    remember(content_id, source_url, niche, platform, mode, meta)
    has_been_posted(source_url, account_id, platform)     → bool
    record_post(content_id, account_id, platform, post_url)
    update_performance(content_id, views, likes, comments, revenue)
    get_best_reup_candidates(niche, platform, limit)       → list[dict]
    get_content(content_id)                               → dict | None
    get_post_history(account_id, limit)                   → list[dict]
    content_fingerprint(source_url)                       → str
    reset_memory()                                        # testing only
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("execution.content_memory")

# ── Config ────────────────────────────────────────────────────────────────────

_DEFAULT_DB = Path("data") / "content_memory.db"

def _db_path() -> Path:
    env = os.environ.get("CONTENT_MEMORY_DB")
    return Path(env) if env else _DEFAULT_DB

# Minimum days before same source_url can be reposted to same account
_REPOST_COOLDOWN_DAYS: int = int(os.environ.get("REPOST_COOLDOWN_DAYS", "7"))

# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS content (
    content_id      TEXT PRIMARY KEY,
    source_url      TEXT NOT NULL DEFAULT '',
    url_fingerprint TEXT NOT NULL DEFAULT '',
    niche           TEXT NOT NULL DEFAULT '',
    platform        TEXT NOT NULL DEFAULT '',
    mode            TEXT NOT NULL DEFAULT 'reup',
    caption         TEXT NOT NULL DEFAULT '',
    hashtags_json   TEXT NOT NULL DEFAULT '[]',
    created_at      REAL NOT NULL DEFAULT 0.0,
    meta_json       TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS post_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id      TEXT NOT NULL,
    account_id      TEXT NOT NULL,
    platform        TEXT NOT NULL,
    post_url        TEXT NOT NULL DEFAULT '',
    posted_at       REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS performance (
    content_id      TEXT PRIMARY KEY,
    views           INTEGER NOT NULL DEFAULT 0,
    likes           INTEGER NOT NULL DEFAULT 0,
    comments        INTEGER NOT NULL DEFAULT 0,
    shares          INTEGER NOT NULL DEFAULT 0,
    revenue         REAL    NOT NULL DEFAULT 0.0,
    engagement_rate REAL    NOT NULL DEFAULT 0.0,
    profit_score    REAL    NOT NULL DEFAULT 0.0,
    updated_at      REAL    NOT NULL DEFAULT 0.0,
    sample_count    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_content_niche     ON content(niche, platform);
CREATE INDEX IF NOT EXISTS idx_content_fingerprint ON content(url_fingerprint);
CREATE INDEX IF NOT EXISTS idx_post_log_account  ON post_log(account_id, platform, posted_at);
CREATE INDEX IF NOT EXISTS idx_post_log_content  ON post_log(content_id);
CREATE INDEX IF NOT EXISTS idx_perf_score        ON performance(profit_score DESC);
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


# ── Fingerprinting ────────────────────────────────────────────────────────────

def content_fingerprint(source_url: str) -> str:
    """
    Normalise and hash a source URL to detect near-duplicates.

    Strips tracking params, normalises TikTok/FB video IDs.
    Returns a 16-char hex string.
    """
    import re
    url = source_url.lower().strip()
    # Extract core video ID from TikTok
    tt_match = re.search(r"/video/(\d+)", url)
    if tt_match:
        return hashlib.sha256(tt_match.group(1).encode()).hexdigest()[:16]
    # Facebook reel ID
    fb_match = re.search(r"/reel(?:s)?/(\d+)", url)
    if fb_match:
        return hashlib.sha256(fb_match.group(1).encode()).hexdigest()[:16]
    # Generic: hash full URL stripped of query params
    base = url.split("?")[0].rstrip("/")
    return hashlib.sha256(base.encode()).hexdigest()[:16]


# ── Public API ────────────────────────────────────────────────────────────────

def remember(
    content_id:  str,
    source_url:  str,
    niche:       str       = "entertainment",
    platform:    str       = "tiktok",
    mode:        str       = "reup",
    caption:     str       = "",
    hashtags:    list[str] | None = None,
    meta:        dict[str, Any] | None = None,
) -> str:
    """
    Store a content item in memory. Idempotent (INSERT OR IGNORE).
    Returns the content_id.
    """
    fp = content_fingerprint(source_url)
    try:
        _exec(
            "INSERT OR IGNORE INTO content"
            " (content_id, source_url, url_fingerprint, niche, platform, mode,"
            "  caption, hashtags_json, created_at, meta_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (content_id, source_url, fp, niche, platform, mode,
             caption[:500], json.dumps(hashtags or []),
             time.time(), json.dumps(meta or {})),
        )
        LOGGER.debug("content_remembered content_id=%s niche=%s", content_id, niche)
    except Exception as exc:
        LOGGER.warning("content_remember_error error=%s", exc)
    return content_id


def has_been_posted(
    source_url: str,
    account_id: str,
    platform:   str = "tiktok",
) -> bool:
    """
    Check if this source_url (by fingerprint) has been posted by this
    account on this platform within REPOST_COOLDOWN_DAYS.

    Returns True if a duplicate exists → caller should skip.
    """
    fp       = content_fingerprint(source_url)
    since_ts = time.time() - _REPOST_COOLDOWN_DAYS * 86400
    try:
        con = _conn()
        row = con.execute(
            """SELECT pl.id FROM post_log pl
               JOIN content c ON c.content_id = pl.content_id
               WHERE c.url_fingerprint = ?
                 AND pl.account_id = ?
                 AND pl.platform = ?
                 AND pl.posted_at >= ?
               LIMIT 1""",
            (fp, account_id, platform, since_ts),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def record_post(
    content_id: str,
    account_id: str,
    platform:   str,
    post_url:   str = "",
) -> None:
    """Record that this content was posted by this account."""
    try:
        _exec(
            "INSERT INTO post_log (content_id, account_id, platform, post_url, posted_at)"
            " VALUES (?,?,?,?,?)",
            (content_id, account_id, platform, post_url, time.time()),
        )
        LOGGER.debug("post_recorded content_id=%s account=%s", content_id, account_id)
    except Exception as exc:
        LOGGER.warning("post_record_error error=%s", exc)


def update_performance(
    content_id:  str,
    views:       int   = 0,
    likes:       int   = 0,
    comments:    int   = 0,
    shares:      int   = 0,
    revenue:     float = 0.0,
    profit_score: float = 0.0,
) -> None:
    """Upsert performance metrics for a content item using EMA smoothing."""
    try:
        con    = _conn()
        existing = con.execute(
            "SELECT * FROM performance WHERE content_id=?", (content_id,)
        ).fetchone()

        alpha = 0.25   # EMA weight for new observations
        if existing:
            n         = existing["sample_count"] + 1
            ema_views = int(existing["views"] * (1 - alpha) + views * alpha)
            ema_likes = int(existing["likes"] * (1 - alpha) + likes * alpha)
            ema_cmts  = int(existing["comments"] * (1 - alpha) + comments * alpha)
            ema_rev   = existing["revenue"] * (1 - alpha) + revenue * alpha
            ema_eng   = existing["engagement_rate"] * (1 - alpha) + (
                (likes + comments) / max(1, views) * alpha
            )
            ema_prof  = existing["profit_score"] * (1 - alpha) + profit_score * alpha
            _exec(
                "UPDATE performance SET views=?, likes=?, comments=?, revenue=?,"
                " engagement_rate=?, profit_score=?, updated_at=?, sample_count=?"
                " WHERE content_id=?",
                (ema_views, ema_likes, ema_cmts, ema_rev,
                 ema_eng, ema_prof, time.time(), n, content_id),
            )
        else:
            eng = (likes + comments) / max(1, views)
            _exec(
                "INSERT INTO performance"
                " (content_id, views, likes, comments, shares, revenue,"
                "  engagement_rate, profit_score, updated_at, sample_count)"
                " VALUES (?,?,?,?,?,?,?,?,?,1)",
                (content_id, views, likes, comments, shares, revenue, eng,
                 profit_score, time.time()),
            )
        LOGGER.debug("performance_updated content_id=%s views=%d", content_id, views)
    except Exception as exc:
        LOGGER.warning("performance_update_error error=%s", exc)


def get_best_reup_candidates(
    niche:    str,
    platform: str,
    limit:    int = 10,
) -> list[dict[str, Any]]:
    """
    Return top content items for reup — sorted by profit_score × engagement.

    Only returns content not posted in last REPOST_COOLDOWN_DAYS.
    """
    since_ts = time.time() - _REPOST_COOLDOWN_DAYS * 86400
    try:
        rows = _conn().execute(
            """SELECT c.*, p.views, p.likes, p.comments, p.revenue,
                      p.engagement_rate, p.profit_score
               FROM content c
               LEFT JOIN performance p ON p.content_id = c.content_id
               WHERE c.niche = ? AND c.platform = ?
                 AND c.content_id NOT IN (
                     SELECT content_id FROM post_log WHERE posted_at >= ?
                 )
               ORDER BY COALESCE(p.profit_score, 0) * COALESCE(p.engagement_rate, 0) DESC
               LIMIT ?""",
            (niche, platform, since_ts, limit),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["hashtags"] = json.loads(d.pop("hashtags_json", "[]"))
            d["meta"]     = json.loads(d.pop("meta_json", "{}"))
            result.append(d)
        return result
    except Exception as exc:
        LOGGER.warning("get_best_reup_error error=%s", exc)
        return []


def get_content(content_id: str) -> dict[str, Any] | None:
    try:
        row = _conn().execute(
            "SELECT * FROM content WHERE content_id=?", (content_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["hashtags"] = json.loads(d.pop("hashtags_json", "[]"))
        d["meta"]     = json.loads(d.pop("meta_json", "{}"))
        return d
    except Exception:
        return None


def get_post_history(account_id: str, limit: int = 50) -> list[dict[str, Any]]:
    try:
        rows = _conn().execute(
            "SELECT * FROM post_log WHERE account_id=? ORDER BY posted_at DESC LIMIT ?",
            (account_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_stats() -> dict[str, Any]:
    try:
        con = _conn()
        total     = con.execute("SELECT COUNT(*) FROM content").fetchone()[0]
        total_pl  = con.execute("SELECT COUNT(*) FROM post_log").fetchone()[0]
        top_niche = con.execute(
            "SELECT niche, COUNT(*) as cnt FROM content GROUP BY niche ORDER BY cnt DESC LIMIT 1"
        ).fetchone()
        return {
            "total_content": total,
            "total_posts":   total_pl,
            "top_niche":     top_niche["niche"] if top_niche else None,
        }
    except Exception:
        return {}


def reset_memory() -> None:
    """Hard reset — testing only."""
    try:
        con = _conn()
        con.executescript(
            "DELETE FROM content; DELETE FROM post_log; DELETE FROM performance;"
        )
        con.commit()
    except Exception:
        pass
    if hasattr(_local, "conn"):
        try:
            _local.conn.close()
        except Exception:
            pass
        _local.conn = None
