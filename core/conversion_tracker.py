"""
core/conversion_tracker.py — Conversion Tracking + Learning Engine

Tracks view→click→conversion→revenue, feeds back into decision layer.

Tables:
    content_performance  — per-content_id EWMA metrics
    cta_performance      — per (pattern_key, cta_type) avg CTR/CVR
    funnel_performance   — per funnel_type avg CTR/CVR/EPV

Public API:
    update_performance(content_id, views, clicks, conversions, revenue)
    get_performance_score(content_id) -> float          [0, 1]
    learn_best_cta(pattern_key, cta_type, ctr, cvr)
    get_best_cta(pattern_key, candidates) -> str
    update_funnel(funnel_type, ctr, cvr, epv)
    get_funnel_score(funnel_type) -> float              [0, 1]
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

# ── Config ────────────────────────────────────────────────────────────────────

_DEFAULT_DB = Path("data") / "conversion_tracker.db"
_EWMA_ALPHA  = 0.20      # weight of new observation
_EPV_CEIL    = 0.05      # $0.05/view → 1.0 (realistic e-commerce)
_EXPLORE_RATE = 0.10     # 10% exploration budget

# Normalisation ceilings for revenue score components
_CTR_CEIL    = 0.15      # 15% CTR → 1.0 (strong creator)
_CVR_CEIL    = 0.20      # 20% CVR → 1.0 (high-intent audience)

# ── DB init ───────────────────────────────────────────────────────────────────

# ── DB connection cache (one conn per resolved path, incl. :memory:) ──────────

def _db_path() -> Path:
    env = os.environ.get("CONVERSION_TRACKER_DB")
    return Path(env) if env else _DEFAULT_DB

_CONN_CACHE: dict[str, sqlite3.Connection] = {}


def _get_conn() -> sqlite3.Connection:
    key = os.environ.get("CONVERSION_TRACKER_DB") or str(_db_path())
    if key in _CONN_CACHE:
        return _CONN_CACHE[key]
    if key == ":memory:":
        path_str = key
    else:
        p = Path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        path_str = str(p)
    conn = sqlite3.connect(path_str, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    _ensure_schema(conn)
    _CONN_CACHE[key] = conn
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS content_performance (
        content_id   TEXT PRIMARY KEY,
        views        REAL DEFAULT 0.0,
        clicks       REAL DEFAULT 0.0,
        conversions  REAL DEFAULT 0.0,
        revenue      REAL DEFAULT 0.0,
        ctr          REAL DEFAULT 0.0,
        cvr          REAL DEFAULT 0.0,
        epv          REAL DEFAULT 0.0,
        last_updated REAL DEFAULT 0.0
    );

    CREATE TABLE IF NOT EXISTS cta_performance (
        pattern_key  TEXT NOT NULL,
        cta_type     TEXT NOT NULL,
        avg_ctr      REAL DEFAULT 0.0,
        avg_cvr      REAL DEFAULT 0.0,
        usage_count  INTEGER DEFAULT 0,
        PRIMARY KEY (pattern_key, cta_type)
    );

    CREATE TABLE IF NOT EXISTS funnel_performance (
        funnel_type  TEXT PRIMARY KEY,
        avg_ctr      REAL DEFAULT 0.0,
        avg_cvr      REAL DEFAULT 0.0,
        avg_epv      REAL DEFAULT 0.0,
        usage_count  INTEGER DEFAULT 0
    );
    """)
    conn.commit()


# ── Part 2 — Performance Update ───────────────────────────────────────────────

def update_performance(
    content_id:  str,
    views:       float,
    clicks:      float,
    conversions: float,
    revenue:     float,
) -> None:
    """
    Upsert EWMA metrics for a content_id.
    Safe to call with any combination of zeros.
    """
    views       = max(0.0, float(views))
    clicks      = max(0.0, float(clicks))
    conversions = max(0.0, float(conversions))
    revenue     = max(0.0, float(revenue))

    # Compute current-cycle ratios (safe div)
    cur_ctr = (clicks      / views)  if views  > 0 else 0.0
    cur_cvr = (conversions / clicks) if clicks > 0 else 0.0
    cur_epv = (revenue     / views)  if views  > 0 else 0.0

    try:
        conn = _get_conn()
        with conn:
            row = conn.execute(
                "SELECT ctr, cvr, epv, views, clicks, conversions, revenue "
                "FROM content_performance WHERE content_id = ?",
                (content_id,)
            ).fetchone()

            now = time.time()
            if row is None:
                conn.execute(
                    """INSERT INTO content_performance
                       (content_id, views, clicks, conversions, revenue,
                        ctr, cvr, epv, last_updated)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (content_id, views, clicks, conversions, revenue,
                     cur_ctr, cur_cvr, cur_epv, now)
                )
            else:
                a = _EWMA_ALPHA
                new_ctr = (1 - a) * float(row["ctr"]) + a * cur_ctr
                new_cvr = (1 - a) * float(row["cvr"]) + a * cur_cvr
                new_epv = (1 - a) * float(row["epv"]) + a * cur_epv
                conn.execute(
                    """UPDATE content_performance
                       SET views = views + ?,
                           clicks = clicks + ?,
                           conversions = conversions + ?,
                           revenue = revenue + ?,
                           ctr = ?, cvr = ?, epv = ?,
                           last_updated = ?
                       WHERE content_id = ?""",
                    (views, clicks, conversions, revenue,
                     new_ctr, new_cvr, new_epv, now, content_id)
                )
    except Exception:
        pass   # never crash pipeline


# ── Part 3 — Performance Score ────────────────────────────────────────────────

def get_performance_score(content_id: str) -> float:
    """
    score = 0.35*ctr + 0.35*cvr + 0.30*norm_epv
    Returns 0.5 (neutral) when no data exists.
    All components clamped [0, 1].
    """
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT ctr, cvr, epv FROM content_performance WHERE content_id = ?",
            (content_id,)
        ).fetchone()
        if row is None:
            return 0.5

        ctr      = max(0.0, min(1.0, float(row["ctr"])))
        cvr      = max(0.0, min(1.0, float(row["cvr"])))
        norm_epv = max(0.0, min(1.0, float(row["epv"]) / _EPV_CEIL))

        score = 0.35 * ctr + 0.35 * cvr + 0.30 * norm_epv
        return round(max(0.0, min(1.0, score)), 4)
    except Exception:
        return 0.5


def get_performance_signals(content_id: str) -> dict[str, float]:
    """Return raw CTR/CVR/EPV for signal emission. Defaults 0.0 if missing."""
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT ctr, cvr, epv FROM content_performance WHERE content_id = ?",
            (content_id,)
        ).fetchone()
        if row is None:
            return {"ctr": 0.0, "cvr": 0.0, "epv": 0.0}
        return {
            "ctr": round(float(row["ctr"]), 6),
            "cvr": round(float(row["cvr"]), 6),
            "epv": round(float(row["epv"]), 6),
        }
    except Exception:
        return {"ctr": 0.0, "cvr": 0.0, "epv": 0.0}


# ── Part 4 — CTA Learning ─────────────────────────────────────────────────────

def learn_best_cta(
    pattern_key: str,
    cta_type:    str,
    ctr:         float,
    cvr:         float,
) -> None:
    """
    EWMA-update the (pattern_key, cta_type) performance record.
    cta_type: "video" | "comment" | "bio"
    """
    ctr = max(0.0, min(1.0, float(ctr)))
    cvr = max(0.0, min(1.0, float(cvr)))
    try:
        conn = _get_conn()
        with conn:
            row = conn.execute(
                "SELECT avg_ctr, avg_cvr, usage_count FROM cta_performance "
                "WHERE pattern_key = ? AND cta_type = ?",
                (pattern_key, cta_type)
            ).fetchone()

            a = _EWMA_ALPHA
            if row is None:
                conn.execute(
                    """INSERT INTO cta_performance
                       (pattern_key, cta_type, avg_ctr, avg_cvr, usage_count)
                       VALUES (?, ?, ?, ?, 1)""",
                    (pattern_key, cta_type, ctr, cvr)
                )
            else:
                new_ctr = (1 - a) * float(row["avg_ctr"]) + a * ctr
                new_cvr = (1 - a) * float(row["avg_cvr"]) + a * cvr
                conn.execute(
                    """UPDATE cta_performance
                       SET avg_ctr = ?, avg_cvr = ?, usage_count = usage_count + 1
                       WHERE pattern_key = ? AND cta_type = ?""",
                    (new_ctr, new_cvr, pattern_key, cta_type)
                )
    except Exception:
        pass


def get_best_cta(pattern_key: str, candidates: list[str],
                 seed: str = "") -> str:
    """
    Return the highest (avg_ctr + avg_cvr)-scoring CTA type for this pattern.
    Part 8: 10% of calls return a random choice for exploration.
    Falls back to candidates[0] if no data.
    """
    if not candidates:
        return "video"

    # Exploration bucket
    _seed = seed or f"{pattern_key}:{time.time():.0f}"
    h = int(hashlib.sha256(_seed.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    if h < _EXPLORE_RATE:
        idx = int(h / _EXPLORE_RATE * len(candidates)) % len(candidates)
        return candidates[idx]

    try:
        conn = _get_conn()
        placeholders = ",".join("?" * len(candidates))
        rows = conn.execute(
            f"SELECT cta_type, avg_ctr + avg_cvr AS combined "
            f"FROM cta_performance "
            f"WHERE pattern_key = ? AND cta_type IN ({placeholders}) "
            f"ORDER BY combined DESC LIMIT 1",
            (pattern_key, *candidates)
        ).fetchall()
        if rows:
            return rows[0]["cta_type"]
    except Exception:
        pass
    return candidates[0]


# ── Part 5 — Funnel Performance ───────────────────────────────────────────────

def update_funnel(
    funnel_type: str,
    ctr:         float,
    cvr:         float,
    epv:         float,
) -> None:
    """EWMA-update funnel_performance for a given funnel_type."""
    ctr = max(0.0, min(1.0, float(ctr)))
    cvr = max(0.0, min(1.0, float(cvr)))
    epv = max(0.0, float(epv))
    try:
        conn = _get_conn()
        with conn:
            row = conn.execute(
                "SELECT avg_ctr, avg_cvr, avg_epv, usage_count "
                "FROM funnel_performance WHERE funnel_type = ?",
                (funnel_type,)
            ).fetchone()

            a = _EWMA_ALPHA
            if row is None:
                conn.execute(
                    """INSERT INTO funnel_performance
                       (funnel_type, avg_ctr, avg_cvr, avg_epv, usage_count)
                       VALUES (?, ?, ?, ?, 1)""",
                    (funnel_type, ctr, cvr, epv)
                )
            else:
                conn.execute(
                    """UPDATE funnel_performance
                       SET avg_ctr = ?,
                           avg_cvr = ?,
                           avg_epv = ?,
                           usage_count = usage_count + 1
                       WHERE funnel_type = ?""",
                    (
                        (1 - a) * float(row["avg_ctr"]) + a * ctr,
                        (1 - a) * float(row["avg_cvr"]) + a * cvr,
                        (1 - a) * float(row["avg_epv"]) + a * epv,
                        funnel_type,
                    )
                )
    except Exception:
        pass


def get_funnel_score(funnel_type: str) -> float:
    """
    Composite funnel score: 0.40*ctr + 0.40*cvr + 0.20*norm_epv
    Returns 0.5 if no data.
    """
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT avg_ctr, avg_cvr, avg_epv "
            "FROM funnel_performance WHERE funnel_type = ?",
            (funnel_type,)
        ).fetchone()
        if row is None:
            return 0.5
        ctr      = max(0.0, min(1.0, float(row["avg_ctr"])))
        cvr      = max(0.0, min(1.0, float(row["avg_cvr"])))
        norm_epv = max(0.0, min(1.0, float(row["avg_epv"]) / _EPV_CEIL))
        return round(0.40 * ctr + 0.40 * cvr + 0.20 * norm_epv, 4)
    except Exception:
        return 0.5


# ── Part 7 — Full Learning Cycle ──────────────────────────────────────────────

def run_learning_cycle(
    content_id:  str,
    views:       float,
    clicks:      float,
    conversions: float,
    revenue:     float,
    pattern_key: str  = "",
    cta_type:    str  = "video",
    funnel_type: str  = "trust",
) -> dict[str, Any]:
    """
    Convenience function: updates all three tables in one call.
    Returns updated scores for use in downstream tracking.
    """
    update_performance(content_id, views, clicks, conversions, revenue)

    # Derive CTR/CVR for CTA + funnel learning
    ctr = (clicks      / views)  if views  > 0 else 0.0
    cvr = (conversions / clicks) if clicks > 0 else 0.0
    epv = (revenue     / views)  if views  > 0 else 0.0

    if pattern_key:
        learn_best_cta(pattern_key, cta_type, ctr, cvr)
    update_funnel(funnel_type, ctr, cvr, epv)

    return {
        "performance_score": get_performance_score(content_id),
        "funnel_score":      get_funnel_score(funnel_type),
        **get_performance_signals(content_id),
    }


# ── Part 1 (Revenue Optimizer) — Revenue Score ──────────────────────────

import math as _math


def _log_norm(x: float, ceil: float) -> float:
    """
    Log-scale normalisation: maps [0, ceil] → [0, 1].
    Uses log(1+x)/log(1+ceil) — sensitive at low values, still bounded.
    """
    if ceil <= 0 or x <= 0:
        return 0.0
    return min(1.0, _math.log1p(x) / _math.log1p(ceil))


def get_revenue_score(content_id: str) -> float:
    """
    Revenue-centric composite score — optimises EPV + CVR over CTR.

    Formula (log-scale normalisation):
        revenue_score =
            0.40 * log_norm(epv,  _EPV_CEIL)
          + 0.30 * log_norm(cvr,  _CVR_CEIL)
          + 0.20 * log_norm(ctr,  _CTR_CEIL)
          + 0.10 * consistency

    consistency: 1 – clamp(|norm_epv – norm_ctr|, 0, 1)
        → high when EPV and CTR are proportionally stable.

    Returns 0.5 (neutral) when no data.
    """
    try:
        conn = _get_conn()
        row  = conn.execute(
            "SELECT ctr, cvr, epv FROM content_performance WHERE content_id = ?",
            (content_id,)
        ).fetchone()
        if row is None:
            return 0.5

        norm_ctr = _log_norm(float(row["ctr"]), _CTR_CEIL)
        norm_cvr = _log_norm(float(row["cvr"]), _CVR_CEIL)
        norm_epv = _log_norm(float(row["epv"]), _EPV_CEIL)

        consistency = 1.0 - min(1.0, abs(norm_epv - norm_cvr))

        score = (
            0.40 * norm_epv +
            0.30 * norm_cvr +
            0.20 * norm_ctr +
            0.10 * consistency
        )
        return round(max(0.0, min(1.0, score)), 4)
    except Exception:
        return 0.5
