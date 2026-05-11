"""
execution/conversion_optimizer.py — Conversion Optimizer.

Optimises the full conversion funnel: CTA text → bio link → comment bait.
Tracks CTR and conversion rate per (cta_variant, platform, niche) and
feeds results back into profit_engine.

Architecture:
  - SQLite store: cta_variants + bio_variants + comment_bait_variants
  - EMA-based scoring per variant
  - Multi-armed bandit selection (epsilon-greedy, ε=0.15 explore)
  - Auto-feeds conversion events to profit_engine

Conversion funnel:
    Post views → profile visits → link clicks → sales

Metrics tracked:
    CTR            = link_clicks / views
    profile_CVR    = profile_visits / views
    purchase_CVR   = purchases / link_clicks

Public API:
    get_best_cta(platform, niche)                       → CTAVariant
    get_best_bio(platform, niche)                       → BioVariant
    get_comment_bait(platform, niche)                   → str
    record_conversion_event(post_id, event_type, value) → None
    get_funnel_report(platform, niche)                  → dict
    reset_conversion_data()                             # testing only

Config (env):
    CONVERSION_DB           : SQLite path (default: data/conversion.db)
    CONVERSION_EMA_ALPHA    : default 0.20
    BANDIT_EPSILON          : explore fraction (default 0.15)
"""
from __future__ import annotations

import logging
import os
import random
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("execution.conversion_optimizer")

_DEFAULT_DB = Path("data") / "conversion.db"
_EMA_ALPHA  = float(os.environ.get("CONVERSION_EMA_ALPHA", "0.20"))
_EPSILON    = float(os.environ.get("BANDIT_EPSILON",        "0.15"))

# ── CTA variant library ───────────────────────────────────────────────────────

_CTA_VARIANTS: dict[str, list[str]] = {
    "tiktok": [
        "Link in bio — tap now before it's gone 🔗",
        "Get it free → link in bio ⬆️",
        "Bio link = instant access 🎯",
        "Swipe up in bio for full guide",
        "Comment 'SEND' and I'll DM you the link",
        "Follow + bio link for the full breakdown",
        "Drop a follow and check bio 🔗",
        "Link above — limited time only ⏳",
    ],
    "facebook": [
        "Click the link to get started today →",
        "Comment 'YES' if you want the free guide",
        "Save this post and check the link in comments",
        "Share with someone who needs this right now",
        "Drop your biggest question below and I'll answer",
        "Follow the page for daily tips like this",
        "React with ❤️ if this helped you today",
    ],
    "instagram": [
        "Link in bio — tap now 🔗",
        "DM me 'INFO' for the full breakdown",
        "Save this for later and check bio link",
        "Double tap if this hit home 💯",
        "Follow + link in bio for more",
    ],
}

_DEFAULT_CTA = [
    "Link in bio 🔗",
    "Check the link above",
    "Follow for more",
]

# ── Bio funnel templates ───────────────────────────────────────────────────────

_BIO_VARIANTS: dict[str, list[str]] = {
    "tech": [
        "🔧 {niche} tips that actually work | Free guide below 👇",
        "Helping you master {niche} faster 🚀 | Start here ⬇️",
        "Daily {niche} hacks | Free resource: 👇",
    ],
    "fitness": [
        "💪 Real {niche} results, no BS | Free plan below",
        "Train smarter, not harder 🏋️ | Free guide 👇",
        "Your {niche} transformation starts here | Link below ⬇️",
    ],
    "finance": [
        "💰 Building wealth through {niche} | Free training 👇",
        "Making money work for you 📈 | Start here ⬇️",
        "Financial freedom via {niche} | Free masterclass 👇",
    ],
    "entertainment": [
        "😂 Daily entertainment that actually hits | New vid 👇",
        "Your daily dose of {niche} | Subscribe below 🔔",
        "For when you need a laugh 💀 | Watch now ⬇️",
    ],
    "food": [
        "🍳 Recipes that actually impress | Free cookbook 👇",
        "Eat better for less 💡 | Free meal plan ⬇️",
        "Daily {niche} inspiration | Recipe link below 👇",
    ],
    "travel": [
        "✈️ Travel cheaper, see more | Free guide 👇",
        "Your next adventure starts here 🌍 | Link below",
        "Hidden gems & travel hacks | Free itinerary ⬇️",
    ],
}
_DEFAULT_BIO = [
    "Daily content you actually want to see | Link below 👇",
    "Follow for daily value 🔥 | Link in bio",
]

# ── Comment bait templates ────────────────────────────────────────────────────

_COMMENT_BAITS: dict[str, list[str]] = {
    "tiktok": [
        "Comment '1' if you want part 2 👇",
        "Drop a ❤️ if this was helpful",
        "What's your biggest question about this? Comment below",
        "Tag someone who needs to see this 👇",
        "Comment 'SEND' and I'll DM you more info",
        "Save this + comment your #1 takeaway",
    ],
    "facebook": [
        "Which tip surprised you most? Tell me in the comments 👇",
        "Share this with someone who needs it right now",
        "React if you've struggled with this before",
        "What would you add to this list? Drop it below",
        "Tag a friend who needs to hear this today",
    ],
    "instagram": [
        "Double tap if this helped 💯",
        "Save this post — you'll thank yourself later",
        "Comment your biggest takeaway below 👇",
        "Share with someone who needs this right now",
    ],
}
_DEFAULT_COMMENT_BAIT = [
    "Drop a comment below 👇",
    "What do you think?",
    "Save this for later!",
]

# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS cta_performance (
    id              TEXT PRIMARY KEY,
    cta_text        TEXT NOT NULL,
    platform        TEXT NOT NULL,
    niche           TEXT NOT NULL,
    impressions     INTEGER NOT NULL DEFAULT 0,
    clicks          INTEGER NOT NULL DEFAULT 0,
    conversions     INTEGER NOT NULL DEFAULT 0,
    ctr_ema         REAL NOT NULL DEFAULT 0.0,
    cvr_ema         REAL NOT NULL DEFAULT 0.0,
    composite_ema   REAL NOT NULL DEFAULT 0.0,
    sample_count    INTEGER NOT NULL DEFAULT 0,
    last_updated    REAL NOT NULL DEFAULT 0.0
);
CREATE TABLE IF NOT EXISTS conversion_events (
    event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id         TEXT NOT NULL,
    platform        TEXT NOT NULL,
    niche           TEXT NOT NULL,
    cta_id          TEXT NOT NULL DEFAULT '',
    event_type      TEXT NOT NULL,
    value           REAL NOT NULL DEFAULT 0.0,
    recorded_at     REAL NOT NULL DEFAULT 0.0
);
CREATE TABLE IF NOT EXISTS post_cta_map (
    post_id         TEXT PRIMARY KEY,
    cta_id          TEXT NOT NULL,
    platform        TEXT NOT NULL,
    niche           TEXT NOT NULL,
    assigned_at     REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_cta_score ON cta_performance(platform, niche, composite_ema DESC);
CREATE INDEX IF NOT EXISTS idx_ce_post   ON conversion_events(post_id, event_type);
"""

_local = threading.local()
_lock  = threading.Lock()


def _db_path() -> Path:
    e = os.environ.get("CONVERSION_DB")
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


def _cta_id(cta: str, platform: str, niche: str) -> str:
    import hashlib
    return hashlib.sha256(f"{platform}:{niche}:{cta}".encode()).hexdigest()[:16]


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class CTAVariant:
    cta_id:        str
    cta_text:      str
    platform:      str
    niche:         str
    ctr_ema:       float = 0.0
    cvr_ema:       float = 0.0
    composite_ema: float = 0.0
    sample_count:  int   = 0
    is_explore:    bool  = False   # True = epsilon-greedy explore pick


@dataclass
class BioVariant:
    bio_text:   str
    niche:      str
    platform:   str


# ── Seed helpers ──────────────────────────────────────────────────────────────

def _seed_ctas(platform: str, niche: str) -> None:
    variants = _CTA_VARIANTS.get(platform, _DEFAULT_CTA)
    for cta in variants:
        cid = _cta_id(cta, platform, niche)
        _exec(
            "INSERT OR IGNORE INTO cta_performance"
            " (id, cta_text, platform, niche, last_updated)"
            " VALUES (?,?,?,?,?)",
            (cid, cta, platform, niche, time.time()),
        )


# ── Core: bandit selection ────────────────────────────────────────────────────

def get_best_cta(
    platform: str,
    niche:    str,
    post_id:  str = "",
) -> CTAVariant:
    """
    Select best CTA via epsilon-greedy multi-armed bandit.
    ε=15% exploration → random pick from full pool.
    ε=85% exploitation → highest composite_ema.

    Optionally logs assignment for later performance tracking.
    Never raises.
    """
    _seed_ctas(platform, niche)
    try:
        explore = random.random() < _EPSILON
        if explore:
            rows = _conn().execute(
                "SELECT * FROM cta_performance WHERE platform=? AND niche=?"
                " ORDER BY RANDOM() LIMIT 1", (platform, niche)
            ).fetchall()
        else:
            rows = _conn().execute(
                "SELECT * FROM cta_performance WHERE platform=? AND niche=?"
                " ORDER BY composite_ema DESC LIMIT 1", (platform, niche)
            ).fetchall()

        if not rows:
            txt = random.choice(_CTA_VARIANTS.get(platform, _DEFAULT_CTA))
            return CTAVariant(
                cta_id=_cta_id(txt, platform, niche), cta_text=txt,
                platform=platform, niche=niche, is_explore=True,
            )

        r = rows[0]
        v = CTAVariant(
            cta_id=r["id"], cta_text=r["cta_text"], platform=platform, niche=niche,
            ctr_ema=float(r["ctr_ema"]), cvr_ema=float(r["cvr_ema"]),
            composite_ema=float(r["composite_ema"]),
            sample_count=int(r["sample_count"]), is_explore=explore,
        )

        # Log assignment
        if post_id:
            _exec(
                "INSERT OR IGNORE INTO post_cta_map (post_id,cta_id,platform,niche,assigned_at)"
                " VALUES (?,?,?,?,?)",
                (post_id, v.cta_id, platform, niche, time.time()),
            )
        return v
    except Exception as exc:
        LOGGER.warning("get_best_cta_error %s", exc)
        txt = random.choice(_CTA_VARIANTS.get(platform, _DEFAULT_CTA))
        return CTAVariant(cta_id="", cta_text=txt, platform=platform, niche=niche)


def get_best_bio(platform: str, niche: str) -> BioVariant:
    """Return best bio template for niche. Simple random until tracking added."""
    templates = _BIO_VARIANTS.get(niche, _DEFAULT_BIO)
    bio = random.choice(templates).replace("{niche}", niche)
    return BioVariant(bio_text=bio, niche=niche, platform=platform)


def get_comment_bait(platform: str, niche: str = "") -> str:
    """Return a randomised comment-bait line for the platform."""
    pool = _COMMENT_BAITS.get(platform, _DEFAULT_COMMENT_BAIT)
    return random.choice(pool)


# ── Feedback ──────────────────────────────────────────────────────────────────

def record_conversion_event(
    post_id:    str,
    event_type: str,   # "view" | "profile_visit" | "link_click" | "purchase"
    value:      float = 0.0,
    platform:   str   = "",
    niche:      str   = "",
) -> None:
    """
    Record a funnel event for a published post.

    event_type hierarchy:
        view → profile_visit → link_click → purchase

    After recording, updates:
      1. CTA performance EMA
      2. profit_engine (if event_type == 'purchase')

    Never raises.
    """
    try:
        # Resolve CTA assignment
        row = _conn().execute(
            "SELECT * FROM post_cta_map WHERE post_id=?", (post_id,)
        ).fetchone()
        cta_id   = row["cta_id"]   if row else ""
        platform = platform or (row["platform"] if row else "")
        niche    = niche    or (row["niche"]    if row else "")

        _exec(
            "INSERT INTO conversion_events"
            " (post_id, platform, niche, cta_id, event_type, value, recorded_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (post_id, platform, niche, cta_id, event_type, value, time.time()),
        )

        # Update CTA EMA when we have enough signal
        if cta_id and event_type in ("link_click", "purchase"):
            _update_cta_ema(cta_id, event_type, value)

        # Feed to profit_engine
        if event_type == "purchase" and value > 0:
            try:
                from core.profit_engine import update_profit
                update_profit(
                    content_id = post_id,
                    niche      = niche,
                    revenue    = value,
                    cost       = 0.0,
                )
            except Exception:
                pass

        LOGGER.debug(
            "conversion_event post_id=%s type=%s value=%.2f", post_id, event_type, value
        )
    except Exception as exc:
        LOGGER.warning("record_conversion_event_error %s", exc)


def _update_cta_ema(cta_id: str, event_type: str, value: float) -> None:
    try:
        row = _conn().execute(
            "SELECT * FROM cta_performance WHERE id=?", (cta_id,)
        ).fetchone()
        if not row:
            return

        a     = _EMA_ALPHA
        clicks = int(row["clicks"]) + (1 if event_type == "link_click" else 0)
        convs  = int(row["conversions"]) + (1 if event_type == "purchase" else 0)
        impr   = max(1, int(row["impressions"]))

        new_ctr  = row["ctr_ema"] * (1 - a) + (clicks / impr) * a
        new_cvr  = row["cvr_ema"] * (1 - a) + (convs / max(1, clicks)) * a
        new_comp = 0.60 * min(1.0, new_ctr / 0.10) + 0.40 * min(1.0, new_cvr / 0.05)
        n        = int(row["sample_count"]) + 1

        _exec(
            "UPDATE cta_performance SET clicks=?,conversions=?,ctr_ema=?,cvr_ema=?,"
            "composite_ema=?,sample_count=?,last_updated=? WHERE id=?",
            (clicks, convs, round(new_ctr, 5), round(new_cvr, 5),
             round(new_comp, 5), n, time.time(), cta_id),
        )
    except Exception as exc:
        LOGGER.warning("update_cta_ema_error %s", exc)


def record_impressions(post_id: str, impressions: int, platform: str = "", niche: str = "") -> None:
    """Record impression count — used to compute CTR denominator."""
    try:
        row = _conn().execute(
            "SELECT cta_id FROM post_cta_map WHERE post_id=?", (post_id,)
        ).fetchone()
        if row and row["cta_id"]:
            _exec(
                "UPDATE cta_performance SET impressions=impressions+?,last_updated=?"
                " WHERE id=?",
                (impressions, time.time(), row["cta_id"]),
            )
    except Exception as exc:
        LOGGER.warning("record_impressions_error %s", exc)


# ── Report ────────────────────────────────────────────────────────────────────

def get_funnel_report(platform: str, niche: str) -> dict[str, Any]:
    """Return conversion funnel summary for a (platform, niche) pair."""
    try:
        # Top CTAs
        rows = _conn().execute(
            "SELECT cta_text, ctr_ema, cvr_ema, composite_ema, sample_count"
            " FROM cta_performance WHERE platform=? AND niche=?"
            " ORDER BY composite_ema DESC LIMIT 5",
            (platform, niche),
        ).fetchall()
        top_ctas = [dict(r) for r in rows]

        # Event totals
        evts = _conn().execute(
            "SELECT event_type, COUNT(*) as cnt, SUM(value) as total"
            " FROM conversion_events WHERE platform=? AND niche=?"
            " GROUP BY event_type",
            (platform, niche),
        ).fetchall()
        events = {r["event_type"]: {"count": r["cnt"], "total_value": r["total"] or 0}
                  for r in evts}

        views   = events.get("view",         {}).get("count", 0)
        clicks  = events.get("link_click",   {}).get("count", 0)
        revenue = events.get("purchase",     {}).get("total_value", 0.0)

        return {
            "platform":   platform,
            "niche":      niche,
            "top_ctas":   top_ctas,
            "events":     events,
            "overall_ctr": round(clicks / max(1, views), 4),
            "total_revenue": round(revenue, 2),
        }
    except Exception as exc:
        return {"error": str(exc)}


def reset_conversion_data() -> None:
    """Testing only."""
    try:
        c = _conn()
        c.executescript(
            "DELETE FROM cta_performance;"
            "DELETE FROM conversion_events;"
            "DELETE FROM post_cta_map;"
        )
        c.commit()
    except Exception:
        pass
    if hasattr(_local, "c"):
        try: _local.c.close()
        except Exception: pass
        _local.c = None
