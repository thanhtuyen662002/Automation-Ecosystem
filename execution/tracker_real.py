"""
execution/tracker_real.py — Real-world click and conversion tracker.

Stores tracking links in SQLite. Provides a lightweight endpoint
simulation for local testing. Designed for future replacement with
a real webhook/API.

Public API:
    generate_tracking_link(content_id, page_id)   -> str (full URL)
    record_click(tracking_code)                   -> bool
    record_conversion(tracking_code, revenue)     -> bool
    get_stats(content_id)                         -> dict
    reset_tracker()                               # testing only

Persistence: SQLite WAL. Path via TRACKER_DB env var (default: data/tracker.db).
"""
from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("execution.tracker_real")

# ── Config ────────────────────────────────────────────────────────────────────

_DEFAULT_DB = Path("data") / "tracker.db"

def _db_path() -> Path:
    env = os.environ.get("TRACKER_DB")
    return Path(env) if env else _DEFAULT_DB

# Base URL for tracking links (replace with real domain in production)
_BASE_URL = os.environ.get("TRACKER_BASE_URL", "https://trk.local/r")

_SCHEME = "aff"

# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS tracking_links (
    tracking_code   TEXT PRIMARY KEY,
    content_id      TEXT NOT NULL,
    page_id         TEXT NOT NULL,
    created_at      REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS clicks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tracking_code   TEXT NOT NULL,
    clicked_at      REAL NOT NULL DEFAULT 0.0,
    ip_hash         TEXT NOT NULL DEFAULT '',
    user_agent_hash TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS conversions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tracking_code   TEXT NOT NULL,
    revenue         REAL NOT NULL DEFAULT 0.0,
    converted_at    REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_clicks_code      ON clicks(tracking_code);
CREATE INDEX IF NOT EXISTS idx_conversions_code ON conversions(tracking_code);
CREATE INDEX IF NOT EXISTS idx_links_content    ON tracking_links(content_id);
"""

# ── Connection pool (one connection per thread) ───────────────────────────────

_local  = threading.local()
_init_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        db = _db_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(db), check_same_thread=False, timeout=10)
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


# ── Tracking code generation ──────────────────────────────────────────────────

def _make_code(content_id: str, page_id: str) -> str:
    """
    Deterministic tracking code: aff://{cid}:{pid}:{ts_hex}

    Same format as core/attribution_engine.generate_tracking_code().
    """
    ts_hex = format(int(time.time()), "x")
    cid = content_id.replace(":", "_").replace("/", "_")
    pid = page_id.replace(":", "_").replace("/", "_")
    return f"{_SCHEME}://{cid}:{pid}:{ts_hex}"


def _code_to_url(tracking_code: str) -> str:
    """Convert tracking code to a clickable tracking URL."""
    encoded = tracking_code.replace("://", "%3A%2F%2F").replace("/", "%2F")
    short = hashlib.sha256(tracking_code.encode()).hexdigest()[:8]
    return f"{_BASE_URL}/{short}?ref={encoded}"


# ── Public API ────────────────────────────────────────────────────────────────

def generate_tracking_link(
    content_id: str,
    page_id:    str,
) -> str:
    """
    Generate and persist a tracking link for a piece of content.

    Returns the full tracking URL (e.g. https://trk.local/r/abc12345?ref=aff%3A%2F%2F...)

    Idempotent: calling twice with same (content_id, page_id) within the same
    timestamp second returns the same URL.
    """
    code = _make_code(content_id, page_id)
    try:
        _exec(
            "INSERT OR IGNORE INTO tracking_links (tracking_code, content_id, page_id, created_at)"
            " VALUES (?, ?, ?, ?)",
            (code, content_id, page_id, time.time()),
        )
        url = _code_to_url(code)
        LOGGER.debug("tracking_link_generated content_id=%s url=%s", content_id, url)
        return url
    except Exception as exc:
        LOGGER.warning("tracking_link_error content_id=%s error=%s", content_id, exc)
        return _code_to_url(code)


def record_click(
    tracking_code: str,
    ip_hash:       str = "",
    user_agent:    str = "",
) -> bool:
    """
    Log a click for a tracking code.

    In production: call this from your redirect endpoint.
    For local testing: call directly.

    ip_hash / user_agent are pre-hashed before storage (privacy).
    """
    try:
        ua_hash = hashlib.sha256(user_agent.encode()).hexdigest()[:16] if user_agent else ""
        _exec(
            "INSERT INTO clicks (tracking_code, clicked_at, ip_hash, user_agent_hash)"
            " VALUES (?, ?, ?, ?)",
            (tracking_code, time.time(), ip_hash[:64], ua_hash),
        )
        LOGGER.debug("click_recorded code=%s", tracking_code[:40])
        return True
    except Exception as exc:
        LOGGER.warning("click_record_error error=%s", exc)
        return False


def record_conversion(
    tracking_code: str,
    revenue:       float,
) -> bool:
    """
    Log a conversion (sale) for a tracking code.

    In production: call from your affiliate network webhook.
    """
    try:
        _exec(
            "INSERT INTO conversions (tracking_code, revenue, converted_at) VALUES (?, ?, ?)",
            (tracking_code, max(0.0, revenue), time.time()),
        )
        LOGGER.info("conversion_recorded code=%s revenue=%.2f", tracking_code[:40], revenue)
        return True
    except Exception as exc:
        LOGGER.warning("conversion_record_error error=%s", exc)
        return False


def get_stats(content_id: str) -> dict[str, Any]:
    """
    Return aggregated click + conversion stats for a content_id.

    Returns:
        {
            "content_id":      str,
            "tracking_codes":  int,
            "total_clicks":    int,
            "total_conversions": int,
            "total_revenue":   float,
            "conversion_rate": float,
        }
    """
    try:
        con = _conn()
        codes = [
            r[0] for r in
            con.execute("SELECT tracking_code FROM tracking_links WHERE content_id = ?",
                        (content_id,)).fetchall()
        ]
        if not codes:
            return {"content_id": content_id, "tracking_codes": 0,
                    "total_clicks": 0, "total_conversions": 0,
                    "total_revenue": 0.0, "conversion_rate": 0.0}

        placeholders = ",".join("?" * len(codes))
        clicks = con.execute(
            f"SELECT COUNT(*) FROM clicks WHERE tracking_code IN ({placeholders})", codes
        ).fetchone()[0]
        conv_row = con.execute(
            f"SELECT COUNT(*), COALESCE(SUM(revenue),0) FROM conversions"
            f" WHERE tracking_code IN ({placeholders})", codes
        ).fetchone()
        n_conv, revenue = conv_row[0], float(conv_row[1])
        conv_rate = round(n_conv / max(1, clicks), 4)

        return {
            "content_id":        content_id,
            "tracking_codes":    len(codes),
            "total_clicks":      clicks,
            "total_conversions": n_conv,
            "total_revenue":     round(revenue, 4),
            "conversion_rate":   conv_rate,
        }
    except Exception as exc:
        LOGGER.warning("get_stats_error content_id=%s error=%s", content_id, exc)
        return {"content_id": content_id, "error": str(exc)}


def get_recent_links(limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent tracking links (for dashboard/debug)."""
    try:
        rows = _conn().execute(
            "SELECT tracking_code, content_id, page_id, created_at"
            " FROM tracking_links ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {"tracking_code": r[0], "content_id": r[1],
             "page_id": r[2], "created_at": r[3]}
            for r in rows
        ]
    except Exception:
        return []


def reset_tracker() -> None:
    """Hard reset — for testing only."""
    try:
        con = _conn()
        con.executescript(
            "DELETE FROM tracking_links; DELETE FROM clicks; DELETE FROM conversions;"
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
