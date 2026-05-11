"""
core/expansion_engine.py — Auto Expansion Engine

Automatically replicates winning product+page combos into new markets.

Pipeline position (AFTER scaling):
    track → profit_eval → scaling → page_intel → EXPANSION_CHECK → next cycle

Public API:
    evaluate_expansion_candidates(niche)  → list[ExpansionCandidate]
    create_expansion_plan(candidate)      → ExpansionPlan
    record_expansion_result(page_id, revenue, cost, posts) → dict
    should_kill_expansion(page_id)        → bool
    merge_expansion_page(page_id)         → bool
    get_expansion_log(last_n)             → list[dict]
    reset_expansion_state()               # testing only

Persistence: SQLite WAL. Path via EXPANSION_STATE_DB env var.
"""
from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("core.expansion_engine")

# ── Config ────────────────────────────────────────────────────────────────────

_DEFAULT_DB = Path("data") / "expansion_state.db"

def _db_path() -> Path:
    v = os.environ.get("EXPANSION_STATE_DB")
    return Path(v) if v else _DEFAULT_DB

# Part 1: Trigger thresholds
EXPANSION_PRODUCT_SCORE_MIN: float = 0.70
EXPANSION_PAGE_SCORE_MIN:    float = 0.65
EXPANSION_STABLE_CYCLES:     int   = 3     # N profitable cycles required

# Part 4: Risk controls
MAX_NEW_PAGES_PER_DAY:  int   = 3
MAX_BUDGET_PER_EXPAND:  float = 50.0   # production units
EXPANSION_KILL_POSTS:   int   = 8      # kill if underperforming after N posts
EXPANSION_KILL_PROFIT:  float = 0.0    # profit threshold to kill expansion page

# Part 5: Feedback thresholds
EXPANSION_WIN_SCORE:    float = 0.60   # page_score to merge into main
EXPANSION_LOSE_SCORE:   float = 0.30   # page_score to kill quickly

# Geo / market expansion variants
GEO_VARIANTS: list[str] = ["vi", "en", "id", "th", "zh"]  # language codes
AUDIENCE_SEGMENTS: list[str] = ["18-24", "25-34", "35-44", "broad"]


# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS expansion_pages (
    page_id         TEXT PRIMARY KEY,
    source_page_id  TEXT NOT NULL DEFAULT '',
    product_id      TEXT NOT NULL DEFAULT '',
    niche           TEXT NOT NULL DEFAULT '',
    strategy        TEXT NOT NULL DEFAULT 'clone',
    geo_variant     TEXT NOT NULL DEFAULT '',
    audience_seg    TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS expansion_metrics (
    page_id         TEXT PRIMARY KEY,
    total_revenue   REAL    NOT NULL DEFAULT 0.0,
    total_cost      REAL    NOT NULL DEFAULT 0.0,
    profit          REAL    NOT NULL DEFAULT 0.0,
    post_count      INTEGER NOT NULL DEFAULT 0,
    cycle_profits   TEXT    NOT NULL DEFAULT '[]',
    status          TEXT    NOT NULL DEFAULT 'tracking',
    updated_at      REAL    NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS expansion_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event       TEXT NOT NULL,
    page_id     TEXT NOT NULL DEFAULT '',
    product_id  TEXT NOT NULL DEFAULT '',
    niche       TEXT NOT NULL DEFAULT '',
    data        TEXT NOT NULL DEFAULT '{}',
    ts          REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS daily_expansion_count (
    date_key    TEXT PRIMARY KEY,
    count       INTEGER NOT NULL DEFAULT 0
);
"""


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ExpansionCandidate:
    """A product+page combination that qualifies for expansion."""
    source_page_id:  str
    product_id:      str
    niche:           str
    product_score:   float
    page_score:      float
    stable_cycles:   int
    geo_variants:    list[str] = field(default_factory=list)
    audience_segs:   list[str] = field(default_factory=list)


@dataclass
class ExpansionPlan:
    """Concrete expansion actions for one candidate."""
    source_page_id:  str
    product_id:      str
    niche:           str
    new_page_ids:    list[str]        # generated page IDs to create
    strategy:        str              # "clone" | "geo" | "segment"
    geo_variant:     str = ""
    audience_seg:    str = ""
    budget_per_page: float = 0.0
    blocked:         bool  = False
    block_reason:    str   = ""


# ── Store ─────────────────────────────────────────────────────────────────────

class _ExpansionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init()

    def _init(self) -> None:
        try:
            p = _db_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(p), check_same_thread=False, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.executescript(_DDL)
            self._conn = conn
        except Exception as e:
            LOGGER.error("expansion_store init error=%s", e)
            self._conn = None

    def _exec(self, sql: str, p: tuple = ()) -> sqlite3.Cursor | None:
        if not self._conn:
            return None
        try:
            return self._conn.execute(sql, p)
        except Exception as e:
            LOGGER.warning("expansion_store db_error %s", e)
            return None

    # Daily limit helpers
    def get_today_count(self) -> int:
        key = time.strftime("%Y-%m-%d")
        cur = self._exec("SELECT count FROM daily_expansion_count WHERE date_key = ?", (key,))
        if cur is None:
            return 0
        row = cur.fetchone()
        return int(row[0]) if row else 0

    def increment_today_count(self, n: int = 1) -> None:
        key = time.strftime("%Y-%m-%d")
        self._exec(
            """INSERT INTO daily_expansion_count (date_key, count) VALUES (?, ?)
               ON CONFLICT(date_key) DO UPDATE SET count = count + excluded.count""",
            (key, n),
        )

    # Expansion page registration
    def register_expansion_page(
        self, page_id: str, source_page_id: str, product_id: str,
        niche: str, strategy: str, geo_variant: str, audience_seg: str,
    ) -> None:
        self._exec(
            """INSERT INTO expansion_pages
               (page_id, source_page_id, product_id, niche, strategy,
                geo_variant, audience_seg, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)
               ON CONFLICT(page_id) DO NOTHING""",
            (page_id, source_page_id, product_id, niche, strategy,
             geo_variant, audience_seg, time.time()),
        )

    def get_expansion_page(self, page_id: str) -> dict[str, Any] | None:
        cur = self._exec(
            "SELECT page_id, source_page_id, product_id, niche, strategy, "
            "geo_variant, audience_seg, status, created_at "
            "FROM expansion_pages WHERE page_id = ?",
            (page_id,),
        )
        if cur is None:
            return None
        row = cur.fetchone()
        if row is None:
            return None
        return {"page_id": row[0], "source_page_id": row[1], "product_id": row[2],
                "niche": row[3], "strategy": row[4], "geo_variant": row[5],
                "audience_seg": row[6], "status": row[7], "created_at": row[8]}

    def update_expansion_status(self, page_id: str, status: str) -> None:
        self._exec(
            "UPDATE expansion_pages SET status = ? WHERE page_id = ?", (status, page_id)
        )

    def get_expansion_pages_by_niche(self, niche: str) -> list[dict[str, Any]]:
        cur = self._exec(
            "SELECT page_id, source_page_id, product_id, strategy, status "
            "FROM expansion_pages WHERE niche = ?",
            (niche,),
        )
        if cur is None:
            return []
        return [{"page_id": r[0], "source_page_id": r[1], "product_id": r[2],
                 "strategy": r[3], "status": r[4]} for r in cur.fetchall()]

    # Expansion metrics
    def update_metrics(self, page_id: str, delta_rev: float,
                       delta_cost: float, delta_posts: int) -> dict[str, Any]:
        import json
        cur = self._exec(
            "SELECT total_revenue, total_cost, profit, post_count, cycle_profits "
            "FROM expansion_metrics WHERE page_id = ?",
            (page_id,),
        )
        ex_row: dict[str, Any] | None = None
        if cur is not None:
            row = cur.fetchone()
            if row is not None:
                ex_row = {
                    "total_revenue": float(row[0]),
                    "total_cost":    float(row[1]),
                    "profit":        float(row[2]),
                    "post_count":    int(row[3]),
                    "cycle_profits": json.loads(row[4]),
                }

        ex: dict[str, Any] = ex_row if ex_row is not None else {
            "total_revenue": 0.0,
            "total_cost":    0.0,
            "profit":        0.0,
            "post_count":    0,
            "cycle_profits": [],
        }

        total_rev:  float     = float(ex["total_revenue"]) + max(0.0, delta_rev)
        total_cost: float     = float(ex["total_cost"])    + max(0.0, delta_cost)
        new_rev:    float     = total_rev
        new_cost:   float     = total_cost
        new_profit: float     = new_rev - new_cost
        new_pc:     int       = int(ex["post_count"]) + delta_posts
        prev_cycles: list[float] = list(ex["cycle_profits"])
        cycle_profits: list[float] = prev_cycles + [round(new_profit, 4)]
        if len(cycle_profits) > 20:
            cycle_profits = cycle_profits[-20:]

        # Determine status
        status = "tracking"
        if new_pc >= EXPANSION_KILL_POSTS:
            if new_profit <= EXPANSION_KILL_PROFIT:
                status = "losing"
            else:
                status = "winning"

        self._exec(
            """INSERT INTO expansion_metrics
               (page_id, total_revenue, total_cost, profit, post_count,
                cycle_profits, status, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(page_id) DO UPDATE SET
                   total_revenue = excluded.total_revenue,
                   total_cost    = excluded.total_cost,
                   profit        = excluded.profit,
                   post_count    = excluded.post_count,
                   cycle_profits = excluded.cycle_profits,
                   status        = excluded.status,
                   updated_at    = excluded.updated_at""",
            (page_id, new_rev, new_cost, new_profit, new_pc,
             json.dumps(cycle_profits), status, time.time()),
        )
        return {"page_id": page_id, "total_revenue": new_rev, "total_cost": new_cost,
                "profit": new_profit, "post_count": new_pc,
                "cycle_profits": cycle_profits, "status": status}

    def get_metrics(self, page_id: str) -> dict[str, Any] | None:
        import json
        cur = self._exec(
            "SELECT page_id, total_revenue, total_cost, profit, post_count, "
            "cycle_profits, status, updated_at FROM expansion_metrics WHERE page_id = ?",
            (page_id,),
        )
        if cur is None:
            return None
        row = cur.fetchone()
        if row is None:
            return None
        return {"page_id": row[0], "total_revenue": float(row[1]),
                "total_cost": float(row[2]), "profit": float(row[3]),
                "post_count": int(row[4]), "cycle_profits": json.loads(row[5]),
                "status": row[6], "updated_at": float(row[7])}

    # Log
    def log_event(self, event: str, page_id: str, product_id: str,
                  niche: str, data: dict) -> None:
        import json
        self._exec(
            "INSERT INTO expansion_log (event, page_id, product_id, niche, data, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (event, page_id, product_id, niche, json.dumps(data), time.time()),
        )

    def get_log(self, last_n: int) -> list[dict[str, Any]]:
        import json
        cur = self._exec(
            f"SELECT event, page_id, product_id, niche, data, ts "
            f"FROM expansion_log ORDER BY id DESC LIMIT {max(1, last_n)}"
        )
        if cur is None:
            return []
        return [{"event": r[0], "page_id": r[1], "product_id": r[2],
                 "niche": r[3], "data": json.loads(r[4]), "ts": r[5]}
                for r in cur.fetchall()]

    def clear(self) -> None:
        for t in ("expansion_pages", "expansion_metrics",
                  "expansion_log", "daily_expansion_count"):
            self._exec(f"DELETE FROM {t}")


# ── Singleton ─────────────────────────────────────────────────────────────────

_STORE: _ExpansionStore | None = None
_STORE_LOCK = threading.Lock()


def _get_store() -> _ExpansionStore:
    global _STORE
    if _STORE is not None:
        return _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = _ExpansionStore()
    return _STORE


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _gen_page_id(source: str, suffix: str) -> str:
    h = hashlib.sha256(f"{source}|{suffix}|{time.time()}".encode()).hexdigest()[:8]
    return f"{source}_exp_{h}"


def _get_product_score(product_id: str) -> float:
    try:
        from core.product_intelligence import get_product_score
        return get_product_score(product_id)
    except Exception:
        return 0.5


def _get_page_score(page_id: str) -> float:
    try:
        from core.page_intelligence import get_page_score
        return get_page_score(page_id)
    except Exception:
        return 0.5


def _get_pages_for_niche(niche: str) -> list[str]:
    try:
        from core.page_intelligence import _get_store as _ps
        return [p["page_id"] for p in _ps().get_pages_by_niche(niche)]
    except Exception:
        return []


def _count_stable_cycles(page_id: str) -> int:
    """Count consecutive profitable cycles.

    For expansion pages: reads cycle_profits list.
    For source pages: a page with profit > 0 and post_count >= EXPANSION_STABLE_CYCLES
    is treated as having enough stable cycles.
    """
    m = _get_store().get_metrics(page_id)
    if m is not None:
        # Expansion page — count consecutive positive profits from tail
        profits = m.get("cycle_profits", [])
        count = 0
        for p in reversed(profits):
            if p > 0:
                count += 1
            else:
                break
        return count

    # Source page — use page_intelligence
    try:
        from core.page_intelligence import _get_store as _pi
        pm = _pi().get_metrics(page_id)
        if pm and pm["profit"] > 0 and pm["post_count"] >= EXPANSION_STABLE_CYCLES:
            # Estimate stable cycles as proportional to profitable post history
            return min(
                int(pm["post_count"] // max(1, EXPANSION_STABLE_CYCLES)),
                EXPANSION_STABLE_CYCLES * 3,  # cap at 3× threshold
            )
    except Exception:
        pass
    return 0


# ── Part 1: Identify expansion candidates ─────────────────────────────────────

def evaluate_expansion_candidates(niche: str) -> list[ExpansionCandidate]:
    """
    Scan all pages in niche for expansion eligibility.

    Qualifies if ALL:
        product_score > EXPANSION_PRODUCT_SCORE_MIN (0.70)
        page_score    > EXPANSION_PAGE_SCORE_MIN    (0.65)
        stable_cycles >= EXPANSION_STABLE_CYCLES    (3 profitable cycles)

    Returns sorted list (highest combined score first).
    Excludes pages already in expansion status or paused.
    """
    page_ids   = _get_pages_for_niche(niche)
    expansion_page_ids = {
        p["source_page_id"]
        for p in _get_store().get_expansion_pages_by_niche(niche)
    }
    candidates: list[ExpansionCandidate] = []

    for pid in page_ids:
        if pid in expansion_page_ids:
            continue   # already has expansion pages spawned from it

        try:
            from core.page_intelligence import get_page_status, _get_store as _pi
            if get_page_status(pid) in ("throttled", "paused"):
                continue
            pm = _pi().get_metrics(pid)
            if pm is None:
                continue
        except Exception:
            continue

        page_s   = _get_page_score(pid)
        if page_s < EXPANSION_PAGE_SCORE_MIN:
            continue

        # Find linked product via page_intelligence store → content_product_map is N/A here.
        # Best-effort: look up product via page_id proxy, else use neutral score.
        try:
            from core.product_intelligence import get_product_score_for_content
            prod_s = get_product_score_for_content(pid)
            from core.product_intelligence import get_product_for_content
            product_id = get_product_for_content(pid) or ""
        except Exception:
            prod_s     = 0.5
            product_id = ""

        if prod_s < EXPANSION_PRODUCT_SCORE_MIN:
            continue

        stable = _count_stable_cycles(pid)
        if stable < EXPANSION_STABLE_CYCLES:
            continue

        candidates.append(ExpansionCandidate(
            source_page_id = pid,
            product_id     = product_id,
            niche          = niche,
            product_score  = round(prod_s, 4),
            page_score     = round(page_s, 4),
            stable_cycles  = stable,
            geo_variants   = GEO_VARIANTS[:3],
            audience_segs  = AUDIENCE_SEGMENTS[:2],
        ))

    candidates.sort(key=lambda c: c.product_score + c.page_score, reverse=True)
    return candidates


# ── Part 2 + 3: Create expansion plan ────────────────────────────────────────

def create_expansion_plan(
    candidate:    ExpansionCandidate,
    strategy:     str  = "clone",   # "clone" | "geo" | "segment"
    geo_variant:  str  = "",
    audience_seg: str  = "",
) -> ExpansionPlan:
    """
    Build an expansion plan for a qualified candidate.

    Strategies:
        clone   — same niche, new page, mutated content variants
        geo     — new language/region variant of winning content
        segment — new audience segment targeting

    Risk control:
        Blocked if MAX_NEW_PAGES_PER_DAY already reached today.
        Budget capped at MAX_BUDGET_PER_EXPAND per page.

    Returns ExpansionPlan (check .blocked before executing).
    """
    store = _get_store()

    # Part 4: daily rate limit
    today_count = store.get_today_count()
    if today_count >= MAX_NEW_PAGES_PER_DAY:
        LOGGER.warning(
            "expansion_engine daily_limit reached count=%d max=%d",
            today_count, MAX_NEW_PAGES_PER_DAY,
        )
        return ExpansionPlan(
            source_page_id = candidate.source_page_id,
            product_id     = candidate.product_id,
            niche          = candidate.niche,
            new_page_ids   = [],
            strategy       = strategy,
            blocked        = True,
            block_reason   = f"daily_limit: {today_count}/{MAX_NEW_PAGES_PER_DAY}",
        )

    # Determine geo/segment overrides
    if strategy == "geo" and not geo_variant:
        geo_variant = candidate.geo_variants[0] if candidate.geo_variants else "en"
    if strategy == "segment" and not audience_seg:
        audience_seg = candidate.audience_segs[0] if candidate.audience_segs else "broad"

    suffix   = geo_variant or audience_seg or strategy
    new_pid  = _gen_page_id(candidate.source_page_id, suffix)
    budget   = min(MAX_BUDGET_PER_EXPAND,
                   round(candidate.page_score * candidate.product_score * 100.0, 2))

    # Register the expansion page
    store.register_expansion_page(
        page_id        = new_pid,
        source_page_id = candidate.source_page_id,
        product_id     = candidate.product_id,
        niche          = candidate.niche,
        strategy       = strategy,
        geo_variant    = geo_variant,
        audience_seg   = audience_seg,
    )
    store.increment_today_count(1)
    store.log_event(
        event      = "expansion_created",
        page_id    = new_pid,
        product_id = candidate.product_id,
        niche      = candidate.niche,
        data       = {
            "source": candidate.source_page_id,
            "strategy": strategy,
            "geo": geo_variant,
            "segment": audience_seg,
            "budget": budget,
            "product_score": candidate.product_score,
            "page_score": candidate.page_score,
        },
    )

    LOGGER.info(
        "expansion_engine created page=%s from=%s strategy=%s niche=%s",
        new_pid, candidate.source_page_id, strategy, candidate.niche,
    )

    return ExpansionPlan(
        source_page_id = candidate.source_page_id,
        product_id     = candidate.product_id,
        niche          = candidate.niche,
        new_page_ids   = [new_pid],
        strategy       = strategy,
        geo_variant    = geo_variant,
        audience_seg   = audience_seg,
        budget_per_page= budget,
    )


# ── Part 5: Feedback loop ─────────────────────────────────────────────────────

def record_expansion_result(
    page_id:    str,
    revenue:    float,
    cost:       float,
    posts:      int = 1,
) -> dict[str, Any]:
    """
    Record a performance cycle for an expansion page.

    Status transitions:
        tracking → winning  if post_count >= KILL_POSTS and profit > 0
        tracking → losing   if post_count >= KILL_POSTS and profit <= 0

    Returns current expansion metrics dict.
    """
    result = _get_store().update_metrics(page_id, revenue, cost, posts)
    _page_info = _get_store().get_expansion_page(page_id)
    _product_id: str = _page_info["product_id"] if _page_info is not None else ""
    _get_store().log_event(
        "expansion_update", page_id, _product_id, "",
        {"revenue": revenue, "cost": cost, "profit": result["profit"],
         "status": result["status"]},
    )
    return result


def should_kill_expansion(page_id: str) -> bool:
    """
    True if expansion page should be killed (quickly).

    Kill if:
        status == "losing"  (post_count >= KILL_POSTS and profit <= 0)
    OR
        page_score (from page_intelligence) < EXPANSION_LOSE_SCORE after enough posts
    """
    m = _get_store().get_metrics(page_id)
    if m and m["status"] == "losing":
        return True

    page_s = _get_page_score(page_id)
    if m and m["post_count"] >= EXPANSION_KILL_POSTS and page_s < EXPANSION_LOSE_SCORE:
        return True

    return False


def merge_expansion_page(page_id: str) -> bool:
    """
    Promote a winning expansion page into the main system.

    Merge conditions:
        expansion status == "winning"
        OR page_score >= EXPANSION_WIN_SCORE

    Actions:
        1. Update expansion status to "merged"
        2. Register page in page_intelligence as a normal (non-new) page
        3. Log merge event

    Returns True if merge succeeded, False if conditions not met.
    """
    store = _get_store()
    info  = store.get_expansion_page(page_id)
    if info is None:
        return False

    m      = store.get_metrics(page_id)
    page_s = _get_page_score(page_id)
    is_winning = (m and m["status"] == "winning") or page_s >= EXPANSION_WIN_SCORE

    if not is_winning:
        return False

    store.update_expansion_status(page_id, "merged")
    store.log_event(
        "expansion_merged", page_id, info["product_id"], info["niche"],
        {"source": info["source_page_id"], "page_score": page_s,
         "profit": m["profit"] if m else 0.0},
    )

    # Register into page_intelligence as a normal (graduated) page
    try:
        from core.page_intelligence import register_page
        register_page(
            page_id    = page_id,
            account_id = info["source_page_id"],   # inherit account from source
            niche      = info["niche"],
            is_new     = False,
        )
    except Exception as e:
        LOGGER.warning("expansion merge page_intel register error=%s", e)

    LOGGER.info(
        "expansion_engine merged page=%s niche=%s page_score=%.3f",
        page_id, info["niche"], page_s,
    )
    return True


def kill_expansion_page(page_id: str) -> bool:
    """
    Kill an underperforming expansion page.

    Actions:
        1. Update expansion status to "killed"
        2. Throttle page in page_intelligence if registered
        3. Log kill event

    Returns True if kill was applied.
    """
    store = _get_store()
    info  = store.get_expansion_page(page_id)
    if info is None:
        return False

    store.update_expansion_status(page_id, "killed")
    m = store.get_metrics(page_id)
    store.log_event(
        "expansion_killed", page_id, info["product_id"], info["niche"],
        {"profit": m["profit"] if m else 0.0,
         "post_count": m["post_count"] if m else 0},
    )
    LOGGER.info("expansion_engine killed page=%s", page_id)
    return True


# ── Query helpers ─────────────────────────────────────────────────────────────

def get_expansion_page_info(page_id: str) -> dict[str, Any] | None:
    """Return expansion page registration info."""
    return _get_store().get_expansion_page(page_id)


def get_expansion_metrics(page_id: str) -> dict[str, Any] | None:
    """Return tracked metrics for an expansion page."""
    return _get_store().get_metrics(page_id)


def get_expansion_log(last_n: int = 50) -> list[dict[str, Any]]:
    """Return the most recent N expansion events."""
    return _get_store().get_log(last_n)


def get_daily_expansion_count() -> int:
    """Return number of expansion pages created today."""
    return _get_store().get_today_count()


# ── Reset ─────────────────────────────────────────────────────────────────────

def reset_expansion_state() -> None:
    """Clear all expansion state. For testing only."""
    _get_store().clear()
