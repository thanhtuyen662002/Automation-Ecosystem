"""
execution/cross_layer_learner.py — Cross-Layer Combination Learner.

Finds winning combinations of (hook_pattern, timing_slot, platform,
content_type, niche) by tracking composite performance across all layers.

This is the meta-learning layer: it doesn't optimise any single signal
in isolation — it finds which COMBINATIONS of signals co-produce viral,
high-converting content.

Tracked combinations:
    combo_key = hash(hook_style + hour_utc + platform + content_type + niche)

Scoring (EMA):
    combo_score =
        0.35 * views_ema / baseline_views
      + 0.25 * engagement_ema / baseline_engagement
      + 0.25 * conversion_ema / baseline_conversion
      + 0.15 * ctr_ema / baseline_ctr

After N observations (default 5), a combo is marked "validated" and
the scheduler, platform adapter, and conversion optimizer are nudged
to prefer it via get_winning_combos().

Public API:
    record_execution(post_id, combo_meta, platform, niche)
    update_combo_performance(post_id, views, likes, comments, conversions, ctr)
    get_winning_combos(platform, niche, limit)          → list[ComboResult]
    get_best_combo_for(platform, niche)                 → ComboResult | None
    get_cross_layer_report(platform, niche)             → dict
    reset_combos()                                      # testing only

Config (env):
    CROSS_LAYER_DB          : SQLite path (default: data/cross_layer.db)
    COMBO_MIN_SAMPLES       : samples before validation (default: 5)
    COMBO_EMA_ALPHA         : default 0.20
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("execution.cross_layer_learner")

_DEFAULT_DB  = Path("data") / "cross_layer.db"
_MIN_SAMPLES = int(os.environ.get("COMBO_MIN_SAMPLES", "5"))
_EMA_ALPHA   = float(os.environ.get("COMBO_EMA_ALPHA", "0.20"))

# Platform baselines — used to normalise combo scores
_BASELINES: dict[str, dict[str, float]] = {
    "tiktok":   {"views": 5000,  "engagement": 0.05, "conversion": 0.01, "ctr": 0.02},
    "facebook": {"views": 2000,  "engagement": 0.04, "conversion": 0.015, "ctr": 0.03},
    "instagram":{"views": 3000,  "engagement": 0.06, "conversion": 0.01, "ctr": 0.025},
}
_DEFAULT_BASELINE = {"views": 3000, "engagement": 0.05, "conversion": 0.01, "ctr": 0.02}

_DDL = """
CREATE TABLE IF NOT EXISTS combos (
    combo_id        TEXT PRIMARY KEY,
    hook_style      TEXT NOT NULL DEFAULT '',
    hour_utc        INTEGER NOT NULL DEFAULT 0,
    platform        TEXT NOT NULL DEFAULT '',
    content_type    TEXT NOT NULL DEFAULT '',
    niche           TEXT NOT NULL DEFAULT '',
    views_ema       REAL NOT NULL DEFAULT 0.0,
    engagement_ema  REAL NOT NULL DEFAULT 0.0,
    conversion_ema  REAL NOT NULL DEFAULT 0.0,
    ctr_ema         REAL NOT NULL DEFAULT 0.0,
    combo_score     REAL NOT NULL DEFAULT 0.0,
    sample_count    INTEGER NOT NULL DEFAULT 0,
    validated       INTEGER NOT NULL DEFAULT 0,
    first_seen      REAL NOT NULL DEFAULT 0.0,
    last_updated    REAL NOT NULL DEFAULT 0.0
);
CREATE TABLE IF NOT EXISTS post_combo_map (
    post_id         TEXT PRIMARY KEY,
    combo_id        TEXT NOT NULL,
    platform        TEXT NOT NULL,
    niche           TEXT NOT NULL,
    posted_at       REAL NOT NULL DEFAULT 0.0,
    perf_updated    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_combo_score ON combos(platform, niche, combo_score DESC);
CREATE INDEX IF NOT EXISTS idx_combo_valid ON combos(platform, niche, validated DESC, combo_score DESC);
"""

_local = threading.local()
_lock  = threading.Lock()


def _db_path() -> Path:
    e = os.environ.get("CROSS_LAYER_DB")
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


def _make_combo_id(
    hook_style:   str,
    hour_utc:     int,
    platform:     str,
    content_type: str,
    niche:        str,
) -> str:
    key = f"{hook_style}|{hour_utc}|{platform}|{content_type}|{niche}"
    return hashlib.sha256(key.encode()).hexdigest()[:20]


def _compute_score(
    views_ema: float, eng_ema: float, conv_ema: float, ctr_ema: float,
    platform: str,
) -> float:
    b   = _BASELINES.get(platform, _DEFAULT_BASELINE)
    s   = (
        0.35 * min(1.0, views_ema  / max(0.01, b["views"]))      +
        0.25 * min(1.0, eng_ema    / max(0.01, b["engagement"]))  +
        0.25 * min(1.0, conv_ema   / max(0.01, b["conversion"]))  +
        0.15 * min(1.0, ctr_ema    / max(0.01, b["ctr"]))
    )
    return round(min(1.0, s), 5)


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class ComboResult:
    combo_id:       str
    hook_style:     str
    hour_utc:       int
    platform:       str
    content_type:   str
    niche:          str
    combo_score:    float = 0.0
    views_ema:      float = 0.0
    engagement_ema: float = 0.0
    conversion_ema: float = 0.0
    ctr_ema:        float = 0.0
    sample_count:   int   = 0
    validated:      bool  = False
    meta:           dict[str, Any] = field(default_factory=dict)

    def as_scheduling_hints(self) -> dict[str, Any]:
        """Return hints consumable by scheduler + adapter."""
        return {
            "preferred_hour": self.hour_utc,
            "hook_style":     self.hook_style,
            "content_type":   self.content_type,
            "combo_score":    self.combo_score,
            "validated":      self.validated,
        }


# ── Public API ────────────────────────────────────────────────────────────────

def record_execution(
    post_id:      str,
    platform:     str,
    niche:        str,
    hook_style:   str = "",
    hour_utc:     int = -1,
    content_type: str = "reup",
    combo_meta:   dict[str, Any] | None = None,
) -> str:
    """
    Register that a post was executed with a particular combination.

    hook_style:   e.g. "aggressive" | "storytelling" | "pov" | "question"
    hour_utc:     UTC hour of posting (0-23)
    content_type: "reup" | "remark" | "generate"

    Returns combo_id for later performance update.
    """
    if hour_utc < 0:
        from datetime import datetime, timezone
        hour_utc = datetime.now(timezone.utc).hour

    # Infer hook_style from combo_meta if available
    if not hook_style and combo_meta:
        hook_style = combo_meta.get("hook_style", "")
    if not hook_style:
        hook_style = "generic"

    combo_id = _make_combo_id(hook_style, hour_utc, platform, content_type, niche)

    try:
        # Upsert combo record
        _exec(
            "INSERT OR IGNORE INTO combos"
            " (combo_id,hook_style,hour_utc,platform,content_type,niche,first_seen,last_updated)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (combo_id, hook_style, hour_utc, platform, content_type, niche,
             time.time(), time.time()),
        )

        # Log post → combo mapping
        _exec(
            "INSERT OR IGNORE INTO post_combo_map (post_id,combo_id,platform,niche,posted_at)"
            " VALUES (?,?,?,?,?)",
            (post_id, combo_id, platform, niche, time.time()),
        )
        LOGGER.debug(
            "combo_recorded post_id=%s combo_id=%s hook=%s hour=%d",
            post_id, combo_id, hook_style, hour_utc,
        )
    except Exception as exc:
        LOGGER.warning("record_execution_error %s", exc)

    return combo_id


def update_combo_performance(
    post_id:     str,
    views:       int   = 0,
    likes:       int   = 0,
    comments:    int   = 0,
    conversions: int   = 0,
    ctr:         float = 0.0,
) -> None:
    """
    Feed real post performance back into the combo record.
    Call after metrics are collected (24–48h post-publish).
    """
    try:
        mapping = _conn().execute(
            "SELECT * FROM post_combo_map WHERE post_id=?", (post_id,)
        ).fetchone()
        if not mapping:
            LOGGER.debug("combo_update_no_mapping post_id=%s", post_id)
            return

        combo_id = mapping["combo_id"]
        platform = mapping["platform"]
        combo    = _conn().execute(
            "SELECT * FROM combos WHERE combo_id=?", (combo_id,)
        ).fetchone()
        if not combo:
            return

        a   = _EMA_ALPHA
        eng = (likes + comments) / max(1, views)
        cv  = conversions / max(1, views)

        nv  = float(combo["views_ema"])      * (1-a) + views * a
        ne  = float(combo["engagement_ema"]) * (1-a) + eng   * a
        nc  = float(combo["conversion_ema"]) * (1-a) + cv    * a
        nt  = float(combo["ctr_ema"])        * (1-a) + ctr   * a
        sc  = _compute_score(nv, ne, nc, nt, platform)
        n   = int(combo["sample_count"]) + 1
        val = 1 if n >= _MIN_SAMPLES else 0

        _exec(
            "UPDATE combos SET views_ema=?,engagement_ema=?,conversion_ema=?,ctr_ema=?,"
            "combo_score=?,sample_count=?,validated=?,last_updated=? WHERE combo_id=?",
            (nv, ne, nc, nt, sc, n, val, time.time(), combo_id),
        )
        _exec(
            "UPDATE post_combo_map SET perf_updated=1 WHERE post_id=?", (post_id,)
        )
        LOGGER.info(
            "combo_updated %s score=%.4f validated=%s n=%d", combo_id, sc, bool(val), n
        )
    except Exception as exc:
        LOGGER.warning("update_combo_perf_error %s", exc)


def get_winning_combos(
    platform: str, niche: str, limit: int = 5, validated_only: bool = False
) -> list[ComboResult]:
    """Return top combos sorted by score, optionally only validated ones."""
    try:
        q  = "SELECT * FROM combos WHERE platform=? AND niche=?"
        p  = [platform, niche]
        if validated_only:
            q += " AND validated=1"
        q += " ORDER BY combo_score DESC LIMIT ?"
        p.append(limit)

        rows = _conn().execute(q, tuple(p)).fetchall()
        return [
            ComboResult(
                combo_id       = r["combo_id"],
                hook_style     = r["hook_style"],
                hour_utc       = int(r["hour_utc"]),
                platform       = platform,
                content_type   = r["content_type"],
                niche          = niche,
                combo_score    = float(r["combo_score"]),
                views_ema      = float(r["views_ema"]),
                engagement_ema = float(r["engagement_ema"]),
                conversion_ema = float(r["conversion_ema"]),
                ctr_ema        = float(r["ctr_ema"]),
                sample_count   = int(r["sample_count"]),
                validated      = bool(r["validated"]),
            )
            for r in rows
        ]
    except Exception as exc:
        LOGGER.warning("get_winning_combos_error %s", exc)
        return []


def get_best_combo_for(platform: str, niche: str) -> ComboResult | None:
    """Return single best validated combo, or best unvalidated if none validated."""
    combos = get_winning_combos(platform, niche, limit=1, validated_only=True)
    if combos:
        return combos[0]
    combos = get_winning_combos(platform, niche, limit=1, validated_only=False)
    return combos[0] if combos else None


def get_cross_layer_report(platform: str, niche: str) -> dict[str, Any]:
    """Dashboard-ready summary."""
    try:
        total = _conn().execute(
            "SELECT COUNT(*) as n FROM combos WHERE platform=? AND niche=?",
            (platform, niche),
        ).fetchone()["n"]

        validated = _conn().execute(
            "SELECT COUNT(*) as n FROM combos WHERE platform=? AND niche=? AND validated=1",
            (platform, niche),
        ).fetchone()["n"]

        top = get_winning_combos(platform, niche, limit=5)
        best = get_best_combo_for(platform, niche)

        return {
            "platform":        platform,
            "niche":           niche,
            "total_combos":    total,
            "validated_combos": validated,
            "top_combos":      [asdict(c) for c in top],
            "best_combo":      asdict(best) if best else None,
            "ready_to_optimise": validated >= 1,
        }
    except Exception as exc:
        return {"error": str(exc)}


def reset_combos() -> None:
    """Testing only."""
    try:
        c = _conn()
        c.executescript("DELETE FROM combos; DELETE FROM post_combo_map;")
        c.commit()
    except Exception:
        pass
    if hasattr(_local, "c"):
        try: _local.c.close()
        except Exception: pass
        _local.c = None
