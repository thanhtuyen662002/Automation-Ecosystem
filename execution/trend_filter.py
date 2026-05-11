"""
execution/trend_filter.py — Trend Quality Filter.

Pre-screens RawCandidate objects from trend_crawler BEFORE they enter
the content_decision pipeline. Rejects low-quality, slow, or saturated
inputs early to save compute and avoid publishing dead content.

Metrics computed per candidate:
  - view_velocity   : views per hour since posting (freshness signal)
  - like_ratio      : likes / views   (audience approval signal)
  - comment_ratio   : comments / views (engagement depth signal)
  - viral_score     : composite quality index [0, 1]

Filter modes (controlled by env var TREND_FILTER_MODE):
  - "strict"   : only top 20% pass (for expensive generate mode)
  - "normal"   : top 50% pass (default)
  - "lenient"  : top 70% pass (for cheap reup mode)

Public API:
    filter_candidates(candidates, mode, niche) → FilterResult
    score_candidate(candidate)                 → TrendScore
    is_trending(candidate)                     → bool
    get_filter_stats()                         → dict

Config (env vars):
    TREND_FILTER_MODE     : "strict" | "normal" | "lenient" (default: "normal")
    MIN_VIEW_VELOCITY     : minimum views/hour to pass (default: 50)
    MIN_LIKE_RATIO        : minimum like/view ratio (default: 0.02)
    TREND_FILTER_DB       : SQLite path for audit log (default: data/trend_filter.db)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("execution.trend_filter")

# ── Config ────────────────────────────────────────────────────────────────────

_MODE          = os.environ.get("TREND_FILTER_MODE", "normal")
_MIN_VELOCITY  = float(os.environ.get("MIN_VIEW_VELOCITY", "50"))
_MIN_LIKE_RATIO = float(os.environ.get("MIN_LIKE_RATIO", "0.02"))
_MIN_VIEWS_ABS = int(os.environ.get("MIN_VIEWS_ABSOLUTE", "500"))

# Pass rate by mode
_PASS_RATES: dict[str, float] = {
    "strict":  0.20,
    "normal":  0.50,
    "lenient": 0.70,
}

# Minimum thresholds — any candidate below these is auto-rejected
_HARD_THRESHOLDS: dict[str, float] = {
    "view_velocity": 10.0,   # < 10 views/hour = dead content
    "like_ratio":     0.005,  # < 0.5% like rate = poor quality
    "viral_score":    0.10,   # composite floor
}

# ── Scoring weights ───────────────────────────────────────────────────────────

_WEIGHT_VELOCITY  = 0.40
_WEIGHT_LIKE      = 0.30
_WEIGHT_COMMENT   = 0.15
_WEIGHT_AGE_BONUS = 0.15   # bonus for very recent content (< 6h old)

# ── Schema ────────────────────────────────────────────────────────────────────

_DEFAULT_DB = Path("data") / "trend_filter.db"
_DDL = """
CREATE TABLE IF NOT EXISTS filter_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id    TEXT NOT NULL DEFAULT '',
    source_url      TEXT NOT NULL DEFAULT '',
    niche           TEXT NOT NULL DEFAULT '',
    platform        TEXT NOT NULL DEFAULT '',
    viral_score     REAL NOT NULL DEFAULT 0.0,
    view_velocity   REAL NOT NULL DEFAULT 0.0,
    like_ratio      REAL NOT NULL DEFAULT 0.0,
    comment_ratio   REAL NOT NULL DEFAULT 0.0,
    decision        TEXT NOT NULL DEFAULT 'pass',
    reject_reason   TEXT NOT NULL DEFAULT '',
    mode            TEXT NOT NULL DEFAULT 'normal',
    filtered_at     REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_filter_niche ON filter_log(niche, decision, filtered_at);
"""

_local     = threading.local()
_init_lock = threading.Lock()


def _db_path() -> Path:
    env = os.environ.get("TREND_FILTER_DB")
    return Path(env) if env else _DEFAULT_DB


def _conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        db = _db_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(db), check_same_thread=False, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        with _init_lock:
            con.executescript(_DDL)
            con.commit()
        _local.conn = con
    return _local.conn


def _log(candidate_id: str, source_url: str, niche: str, platform: str,
         ts: "TrendScore", decision: str, reason: str, mode: str) -> None:
    try:
        c = _conn()
        c.execute(
            "INSERT INTO filter_log (candidate_id, source_url, niche, platform,"
            " viral_score, view_velocity, like_ratio, comment_ratio,"
            " decision, reject_reason, mode, filtered_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (candidate_id, source_url[:500], niche, platform,
             ts.viral_score, ts.view_velocity, ts.like_ratio, ts.comment_ratio,
             decision, reason, mode, time.time()),
        )
        c.commit()
    except Exception:
        pass


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class TrendScore:
    view_velocity:  float = 0.0   # views per hour
    like_ratio:     float = 0.0   # likes / views
    comment_ratio:  float = 0.0   # comments / views
    age_hours:      float = 0.0   # estimated age
    viral_score:    float = 0.0   # composite [0, 1]
    raw_views:      int   = 0
    raw_likes:      int   = 0
    raw_comments:   int   = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FilterResult:
    passed:        list[dict[str, Any]]  = field(default_factory=list)
    rejected:      list[dict[str, Any]]  = field(default_factory=list)
    scores:        dict[str, TrendScore] = field(default_factory=dict)
    total_in:      int   = 0
    total_passed:  int   = 0
    total_rejected: int  = 0
    mode:          str   = "normal"
    elapsed_ms:    float = 0.0


# ── Scoring ───────────────────────────────────────────────────────────────────

def score_candidate(candidate: dict[str, Any]) -> TrendScore:
    """
    Compute TrendScore for a single candidate dict.

    Accepts either RawCandidate.to_dict() format or generic candidate dicts.
    Estimates age from scraped_at if available; defaults to 24h.
    """
    views    = int(candidate.get("view_count",   candidate.get("views",    0)))
    likes    = int(candidate.get("like_count",   candidate.get("likes",    0)))
    comments = int(candidate.get("comment_count", candidate.get("comments", 0)))

    scraped_at = float(candidate.get("scraped_at", 0.0))
    published_at = float(candidate.get("published_at", 0.0))

    if published_at > 0:
        age_hours = max(0.1, (time.time() - published_at) / 3600)
    elif scraped_at > 0:
        # If we only have scraped_at, assume content is at least 1h old
        age_hours = max(1.0, (time.time() - scraped_at) / 3600)
    else:
        age_hours = 24.0  # safe default

    view_velocity = views / max(1.0, age_hours)
    like_ratio    = likes    / max(1, views)
    comment_ratio = comments / max(1, views)

    # Normalise each signal to [0, 1]
    # Velocity: 10K views/hour = 1.0
    vel_norm  = min(1.0, view_velocity / 10_000)
    # Like ratio: 10% = 1.0 (typical viral content)
    like_norm = min(1.0, like_ratio / 0.10)
    # Comment ratio: 2% = 1.0
    cmnt_norm = min(1.0, comment_ratio / 0.02)
    # Age bonus: content < 6h old gets recency bonus
    age_bonus = max(0.0, 1.0 - age_hours / 6.0) if age_hours < 6.0 else 0.0

    viral_score = (
        _WEIGHT_VELOCITY  * vel_norm  +
        _WEIGHT_LIKE      * like_norm +
        _WEIGHT_COMMENT   * cmnt_norm +
        _WEIGHT_AGE_BONUS * age_bonus
    )

    return TrendScore(
        view_velocity  = round(view_velocity, 2),
        like_ratio     = round(like_ratio, 4),
        comment_ratio  = round(comment_ratio, 4),
        age_hours      = round(age_hours, 2),
        viral_score    = round(min(1.0, viral_score), 4),
        raw_views      = views,
        raw_likes      = likes,
        raw_comments   = comments,
    )


def is_trending(candidate: dict[str, Any]) -> bool:
    """Quick boolean check — passes normal-mode filter."""
    ts = score_candidate(candidate)
    return (
        ts.view_velocity >= _HARD_THRESHOLDS["view_velocity"] and
        ts.like_ratio    >= _HARD_THRESHOLDS["like_ratio"]    and
        ts.viral_score   >= _HARD_THRESHOLDS["viral_score"]   and
        ts.raw_views     >= _MIN_VIEWS_ABS
    )


# ── Main filter ───────────────────────────────────────────────────────────────

def filter_candidates(
    candidates: list[dict[str, Any]],
    mode:       str = "",
    niche:      str = "",
    log:        bool = True,
) -> FilterResult:
    """
    Filter a batch of candidates by trend quality.

    Steps:
      1. Score every candidate
      2. Hard-reject below absolute thresholds
      3. Sort remaining by viral_score descending
      4. Keep top N% based on mode pass-rate
      5. Inject trend_score into each passing candidate dict

    Returns FilterResult with passed/rejected splits.
    Never raises.
    """
    t0      = time.monotonic()
    _mode   = mode or _MODE
    rate    = _PASS_RATES.get(_mode, 0.50)
    result  = FilterResult(total_in=len(candidates), mode=_mode)

    if not candidates:
        return result

    # Step 1 & 2: Score + hard-reject
    scored: list[tuple[dict[str, Any], TrendScore, str]] = []  # (cand, score, reject_reason)
    for cand in candidates:
        ts = score_candidate(cand)
        cid = cand.get("content_id", cand.get("source_url", "")[:40])

        # Hard thresholds
        reason = ""
        if ts.raw_views < _MIN_VIEWS_ABS and ts.raw_views > 0:
            reason = f"low_views:{ts.raw_views}<{_MIN_VIEWS_ABS}"
        elif ts.view_velocity < _HARD_THRESHOLDS["view_velocity"]:
            reason = f"low_velocity:{ts.view_velocity:.1f}"
        elif ts.like_ratio < _HARD_THRESHOLDS["like_ratio"]:
            reason = f"low_like_ratio:{ts.like_ratio:.4f}"
        elif ts.viral_score < _HARD_THRESHOLDS["viral_score"]:
            reason = f"low_viral_score:{ts.viral_score:.3f}"

        scored.append((cand, ts, reason))
        result.scores[cid] = ts

    # Separate hard-rejected from eligible
    hard_rejected  = [(c, ts, r) for c, ts, r in scored if r]
    eligible       = [(c, ts)    for c, ts, r in scored if not r]

    # Step 3: Sort eligible by viral_score
    eligible.sort(key=lambda x: x[1].viral_score, reverse=True)

    # Step 4: Apply pass-rate cut
    n_keep = max(1, round(len(eligible) * rate))
    passed_set  = eligible[:n_keep]
    soft_rejected = eligible[n_keep:]

    # Step 5: Enrich passing candidates with trend data
    for cand, ts in passed_set:
        cand["trend_score"]    = ts.viral_score
        cand["view_velocity"]  = ts.view_velocity
        cand["like_ratio"]     = ts.like_ratio
        cand["comment_ratio"]  = ts.comment_ratio
        result.passed.append(cand)

    for cand, ts, reason in hard_rejected:
        cand["trend_score"] = ts.viral_score
        result.rejected.append(cand)

    for cand, ts in soft_rejected:
        cand["trend_score"] = ts.viral_score
        result.rejected.append(cand)

    result.total_passed   = len(result.passed)
    result.total_rejected = len(result.rejected)
    result.elapsed_ms     = round((time.monotonic() - t0) * 1000, 1)

    LOGGER.info(
        "trend_filter mode=%s passed=%d/%d rejected=%d elapsed_ms=%.1f",
        _mode, result.total_passed, len(candidates),
        result.total_rejected, result.elapsed_ms,
    )

    # Audit log (async-safe, best-effort)
    if log:
        _niche = niche or (candidates[0].get("niche", "") if candidates else "")
        for cand, ts in passed_set:
            _log(
                cand.get("content_id", ""),
                cand.get("source_url", ""),
                _niche, cand.get("platform", ""),
                ts, "pass", "", _mode,
            )
        for cand, ts, reason in hard_rejected:
            _log(
                cand.get("content_id", ""),
                cand.get("source_url", ""),
                _niche, cand.get("platform", ""),
                ts, "hard_reject", reason, _mode,
            )
        for cand, ts in soft_rejected:
            _log(
                cand.get("content_id", ""),
                cand.get("source_url", ""),
                _niche, cand.get("platform", ""),
                ts, "soft_reject", f"below_rate_cut:{rate}", _mode,
            )

    return result


def get_filter_stats(niche: str = "", days: int = 7) -> dict[str, Any]:
    """Return pass/reject counts and average viral scores for the last N days."""
    since = time.time() - days * 86400
    try:
        con   = _conn()
        base  = "FROM filter_log WHERE filtered_at >= ?"
        args: tuple = (since,)
        if niche:
            base += " AND niche=?"
            args += (niche,)

        totals = con.execute(
            f"SELECT decision, COUNT(*) as cnt, AVG(viral_score) as avg_score {base}"
            " GROUP BY decision", args
        ).fetchall()

        top_niches = con.execute(
            f"SELECT niche, COUNT(*) as cnt, AVG(viral_score) as avg {base}"
            " AND decision='pass' GROUP BY niche ORDER BY avg DESC LIMIT 5", args
        ).fetchall()

        return {
            "decisions":  {r["decision"]: {"count": r["cnt"], "avg_viral": round(r["avg_score"] or 0, 3)}
                           for r in totals},
            "top_niches": [{"niche": r["niche"], "count": r["cnt"], "avg_viral": round(r["avg"] or 0, 3)}
                           for r in top_niches],
            "since_days": days,
        }
    except Exception as exc:
        return {"error": str(exc)}


def reset_filter_log() -> None:
    """Testing only."""
    try:
        c = _conn()
        c.executescript("DELETE FROM filter_log;")
        c.commit()
    except Exception:
        pass
    if hasattr(_local, "conn"):
        try:
            _local.conn.close()
        except Exception:
            pass
        _local.conn = None
