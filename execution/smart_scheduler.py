"""
execution/smart_scheduler.py — Smart Timing Engine.

Learns best posting time per (hour, platform, niche) using EMA performance.
Replaces fixed peak windows with data-driven slot selection.

Default peak priors (used until real data overrides):
    TikTok:   07,12,18,19,21 UTC
    Facebook: 08,09,13,18,20 UTC

Slot score = 0.40*views_ema + 0.30*engagement_ema + 0.20*conversion_ema + 0.10*recency

Public API:
    get_best_slots(platform, niche, n)              → list[TimingSlot]
    get_next_post_time(platform, niche)             → datetime (UTC)
    record_post_time(post_id, platform, niche, hour)
    update_slot_performance(post_id, views, likes, comments, conversions)
    get_timing_report(platform, niche)              → dict
    reset_timing()                                  # testing only

Config (env):
    SMART_SCHEDULER_DB, TIMING_EMA_ALPHA (default 0.25), MIN_SLOT_SAMPLES (default 3)
"""
from __future__ import annotations

import logging
import os
import random
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("execution.smart_scheduler")

_DEFAULT_DB  = Path("data") / "smart_scheduler.db"
_EMA_ALPHA   = float(os.environ.get("TIMING_EMA_ALPHA",  "0.25"))
_MIN_SAMPLES = int(os.environ.get("MIN_SLOT_SAMPLES",    "3"))

_PRIORS: dict[str, list[int]] = {
    "tiktok":   [7, 12, 18, 19, 21, 22],
    "facebook": [8, 9, 13, 18, 19, 20],
    "instagram": [7, 11, 14, 17, 19, 21],
}
_MIN_GAP_H: dict[str, float] = {
    "tiktok": 2.0, "facebook": 3.0, "instagram": 4.0
}

_DDL = """
CREATE TABLE IF NOT EXISTS timing_slots (
    id              TEXT PRIMARY KEY,
    platform        TEXT NOT NULL,
    niche           TEXT NOT NULL,
    hour_utc        INTEGER NOT NULL,
    views_ema       REAL NOT NULL DEFAULT 0.0,
    engagement_ema  REAL NOT NULL DEFAULT 0.0,
    conversion_ema  REAL NOT NULL DEFAULT 0.0,
    slot_score      REAL NOT NULL DEFAULT 0.0,
    sample_count    INTEGER NOT NULL DEFAULT 0,
    last_used       REAL NOT NULL DEFAULT 0.0,
    last_updated    REAL NOT NULL DEFAULT 0.0
);
CREATE TABLE IF NOT EXISTS post_timing_log (
    post_id     TEXT PRIMARY KEY,
    platform    TEXT NOT NULL,
    niche       TEXT NOT NULL,
    hour_utc    INTEGER NOT NULL,
    posted_at   REAL NOT NULL DEFAULT 0.0,
    views       INTEGER NOT NULL DEFAULT 0,
    likes       INTEGER NOT NULL DEFAULT 0,
    comments    INTEGER NOT NULL DEFAULT 0,
    conversions INTEGER NOT NULL DEFAULT 0,
    slot_id     TEXT NOT NULL DEFAULT '',
    updated     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ts_score ON timing_slots(platform, niche, slot_score DESC);
"""

_local = threading.local()
_lock  = threading.Lock()


def _db_path() -> Path:
    e = os.environ.get("SMART_SCHEDULER_DB")
    return Path(e) if e else _DEFAULT_DB


def _conn() -> sqlite3.Connection:
    if not hasattr(_local, "c") or _local.c is None:
        db = _db_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(db), check_same_thread=False, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        with _lock:
            con.executescript(_DDL)
            con.commit()
        _local.c = con
    return _local.c


def _exec(sql: str, p: tuple = ()) -> sqlite3.Cursor:
    c = _conn(); cur = c.execute(sql, p); c.commit(); return cur


def _slot_id(plat: str, niche: str, hour: int) -> str:
    return f"{plat}:{niche}:{hour:02d}"


def _seed(platform: str, niche: str) -> None:
    prior_hours = _PRIORS.get(platform, [7, 12, 18, 20])
    for h in range(24):
        sid   = _slot_id(platform, niche, h)
        score = 0.55 if h in prior_hours else 0.25
        _exec(
            "INSERT OR IGNORE INTO timing_slots"
            " (id,platform,niche,hour_utc,slot_score,last_updated)"
            " VALUES (?,?,?,?,?,?)",
            (sid, platform, niche, h, score, time.time()),
        )


@dataclass
class TimingSlot:
    hour_utc:       int
    platform:       str
    niche:          str
    slot_score:     float = 0.0
    views_ema:      float = 0.0
    engagement_ema: float = 0.0
    conversion_ema: float = 0.0
    sample_count:   int   = 0
    is_prior:       bool  = True
    meta:           dict[str, Any] = field(default_factory=dict)


def get_best_slots(
    platform: str, niche: str, n: int = 3,
    exclude_hours: list[int] | None = None,
) -> list[TimingSlot]:
    _seed(platform, niche)
    exc = set(exclude_hours or [])
    try:
        rows = _conn().execute(
            "SELECT * FROM timing_slots WHERE platform=? AND niche=?"
            " ORDER BY slot_score DESC", (platform, niche)
        ).fetchall()
        out: list[TimingSlot] = []
        for r in rows:
            if int(r["hour_utc"]) in exc:
                continue
            out.append(TimingSlot(
                hour_utc=int(r["hour_utc"]), platform=platform, niche=niche,
                slot_score=float(r["slot_score"]),
                views_ema=float(r["views_ema"]),
                engagement_ema=float(r["engagement_ema"]),
                conversion_ema=float(r["conversion_ema"]),
                sample_count=int(r["sample_count"]),
                is_prior=int(r["sample_count"]) < _MIN_SAMPLES,
            ))
        return out[:n]
    except Exception as exc2:
        LOGGER.warning("get_best_slots_error %s", exc2)
        return []


def get_next_post_time(
    platform: str, niche: str,
    jitter_minutes: int = 15,
) -> datetime:
    gap_h    = _MIN_GAP_H.get(platform, 2.0)
    now_utc  = datetime.now(timezone.utc)
    now_h    = now_utc.hour
    excluded = [(now_h - i) % 24 for i in range(int(gap_h))]
    slots    = get_best_slots(platform, niche, n=5, exclude_hours=excluded)

    if slots:
        best_h      = slots[0].hour_utc
        hours_until = (best_h - now_h) % 24 or 24
        target      = now_utc.replace(minute=0, second=0, microsecond=0) + timedelta(hours=hours_until)
    else:
        target = now_utc + timedelta(hours=gap_h)

    target += timedelta(minutes=random.randint(-jitter_minutes, jitter_minutes))
    LOGGER.debug("next_post_time platform=%s niche=%s target=%s", platform, niche, target.isoformat())
    return target


def record_post_time(
    post_id: str, platform: str, niche: str,
    hour_utc: int | None = None, ts: float = 0.0,
) -> None:
    now   = ts or time.time()
    h     = hour_utc if hour_utc is not None else datetime.fromtimestamp(now, tz=timezone.utc).hour
    sid   = _slot_id(platform, niche, h)
    _seed(platform, niche)
    try:
        _exec(
            "INSERT OR IGNORE INTO post_timing_log"
            " (post_id,platform,niche,hour_utc,posted_at,slot_id)"
            " VALUES (?,?,?,?,?,?)",
            (post_id, platform, niche, h, now, sid),
        )
        _exec("UPDATE timing_slots SET last_used=?,last_updated=? WHERE id=?",
              (now, now, sid))
        LOGGER.debug("post_time_recorded post_id=%s hour=%d", post_id, h)
    except Exception as e:
        LOGGER.warning("record_post_time_error %s", e)


def update_slot_performance(
    post_id: str, views: int = 0, likes: int = 0,
    comments: int = 0, conversions: int = 0,
) -> None:
    try:
        log = _conn().execute(
            "SELECT * FROM post_timing_log WHERE post_id=?", (post_id,)
        ).fetchone()
        if not log:
            return
        slot = _conn().execute(
            "SELECT * FROM timing_slots WHERE id=?", (log["slot_id"],)
        ).fetchone()
        if not slot:
            return

        a        = _EMA_ALPHA
        eng      = (likes + comments) / max(1, views)
        conv     = conversions / max(1, views)
        recency  = max(0.0, 1.0 - (time.time() - float(log["posted_at"])) / (7 * 86400))
        nv = float(slot["views_ema"])      * (1-a) + views * a
        ne = float(slot["engagement_ema"]) * (1-a) + eng   * a
        nc = float(slot["conversion_ema"]) * (1-a) + conv  * a
        sc = round(min(1.0, 0.40*min(1.0,nv/100_000) + 0.30*min(1.0,ne/0.10)
                      + 0.20*min(1.0,nc/0.05) + 0.10*recency), 5)
        n  = int(slot["sample_count"]) + 1

        _exec(
            "UPDATE timing_slots SET views_ema=?,engagement_ema=?,conversion_ema=?,"
            "slot_score=?,sample_count=?,last_updated=? WHERE id=?",
            (nv, ne, nc, sc, n, time.time(), log["slot_id"]),
        )
        _exec(
            "UPDATE post_timing_log SET views=?,likes=?,comments=?,conversions=?,updated=1"
            " WHERE post_id=?",
            (views, likes, comments, conversions, post_id),
        )
        LOGGER.info("slot_updated %s score=%.4f views=%d", log["slot_id"], sc, views)
    except Exception as e:
        LOGGER.warning("update_slot_perf_error %s", e)


def get_timing_report(platform: str, niche: str) -> dict[str, Any]:
    _seed(platform, niche)
    try:
        rows = _conn().execute(
            "SELECT hour_utc, slot_score, views_ema, engagement_ema, conversion_ema,"
            " sample_count FROM timing_slots WHERE platform=? AND niche=?"
            " ORDER BY hour_utc", (platform, niche)
        ).fetchall()
        hm   = [dict(r) for r in rows]
        best = max(hm, key=lambda x: x["slot_score"], default={})
        return {
            "platform": platform, "niche": niche, "heatmap": hm,
            "best_hour": best.get("hour_utc"), "best_score": best.get("slot_score"),
            "data_driven": any(r["sample_count"] >= _MIN_SAMPLES for r in hm),
        }
    except Exception as e:
        return {"error": str(e)}


def reset_timing() -> None:
    try:
        c = _conn()
        c.executescript("DELETE FROM timing_slots; DELETE FROM post_timing_log;")
        c.commit()
    except Exception:
        pass
    if hasattr(_local, "c"):
        try: _local.c.close()
        except Exception: pass
        _local.c = None
