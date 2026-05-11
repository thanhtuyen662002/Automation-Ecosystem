"""
core/page_intelligence.py — Account & Page Intelligence Layer

Tracks distribution-level performance: page_id → account_id → niche.
Evolves system from content/product optimization → distribution optimization.

Public API:
    register_page(page_id, account_id, niche, is_new)         → None
    update_page_metrics(page_id, views, engagement, revenue,
                        cost, converted, post_count)           → dict
    get_page_score(page_id)                                    → float [0,1]
    get_account_score(account_id)                              → float [0,1]
    get_page_status(page_id)                                   → "active"|"throttled"|"paused"
    get_page_posting_frequency(page_id)                        → float (multiplier)
    is_page_throttled(page_id)                                 → bool
    get_exploration_pages(niche, ratio)                        → list[str]
    get_page_budget_weights(niche)                             → dict[str, float]
    reset_page_state()                                         # testing only

Persistence: SQLite WAL + LRU cache. Path via PAGE_STATE_DB env var.
"""
from __future__ import annotations

import logging
import math
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("core.page_intelligence")

# ── Config ────────────────────────────────────────────────────────────────────

_DEFAULT_DB = Path("data") / "page_state.db"

def _db_path() -> Path:
    env = os.environ.get("PAGE_STATE_DB")
    return Path(env) if env else _DEFAULT_DB

# Kill/throttle thresholds
_THROTTLE_MIN_POSTS:  int   = 5       # require at least N posts before throttle
_THROTTLE_PROFIT_THRESHOLD: float = 0.0
_PAUSE_MIN_POSTS:     int   = 10
_PAUSE_PROFIT_THRESHOLD: float = -5.0  # severe loss → full pause

# Exploration bucket
_EXPLORE_MIN_RATIO:   float = 0.10
_EXPLORE_MAX_RATIO:   float = 0.15
_NEW_PAGE_POSTS_WINDOW: int = 20       # posts before page leaves exploration

# Scoring weights
_PW_PROFIT_MARGIN:   float = 0.35
_PW_CONVERSION:      float = 0.25
_PW_ENGAGEMENT:      float = 0.20
_PW_CONSISTENCY:     float = 0.20

# EMA
_EMA_ALPHA: float = 0.20

# Posting frequency multipliers
_FREQ_WINNER:  float = 1.5
_FREQ_NORMAL:  float = 1.0
_FREQ_WEAK:    float = 0.5
_FREQ_PAUSED:  float = 0.0

# Cache
_CACHE_MAX: int   = 2048
_CACHE_TTL: float = 20.0


# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS pages (
    page_id      TEXT PRIMARY KEY,
    account_id   TEXT NOT NULL DEFAULT '',
    niche        TEXT NOT NULL DEFAULT '',
    is_new       INTEGER NOT NULL DEFAULT 1,
    created_at   REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_pages_account ON pages (account_id);
CREATE INDEX IF NOT EXISTS idx_pages_niche   ON pages (niche);

CREATE TABLE IF NOT EXISTS page_metrics (
    page_id          TEXT PRIMARY KEY,
    total_views      REAL    NOT NULL DEFAULT 0.0,
    total_engagement REAL    NOT NULL DEFAULT 0.0,
    total_revenue    REAL    NOT NULL DEFAULT 0.0,
    total_cost       REAL    NOT NULL DEFAULT 0.0,
    profit           REAL    NOT NULL DEFAULT 0.0,
    conversion_ema   REAL    NOT NULL DEFAULT 0.0,
    engagement_ema   REAL    NOT NULL DEFAULT 0.0,
    consistency_ema  REAL    NOT NULL DEFAULT 0.5,
    post_count       INTEGER NOT NULL DEFAULT 0,
    status           TEXT    NOT NULL DEFAULT 'active',
    updated_at       REAL    NOT NULL DEFAULT 0.0
);
"""


# ── Cache ─────────────────────────────────────────────────────────────────────

class _CE:
    __slots__ = ("data", "ts")
    def __init__(self, d: Any) -> None:
        self.data = d
        self.ts   = time.monotonic()
    def stale(self) -> bool:
        return (time.monotonic() - self.ts) > _CACHE_TTL


# ── Store ──────────────────────────────────────────────────────────────────────

class _PageStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db   = db_path or _db_path()
        self._lock = threading.Lock()
        self._cache: dict[str, _CE] = {}
        self._conn: sqlite3.Connection | None = None
        self._init()

    def _init(self) -> None:
        try:
            self._db.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._db), check_same_thread=False, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.executescript(_DDL)
            self._conn = conn
        except Exception as e:
            LOGGER.error("page_store init_failed error=%s", e)
            self._conn = None

    def _exec(self, sql: str, p: tuple = ()) -> sqlite3.Cursor | None:
        if not self._conn:
            return None
        try:
            return self._conn.execute(sql, p)
        except sqlite3.OperationalError as e:
            LOGGER.warning("page_store db_error %s", e)
            self._init()
            try:
                return self._conn.execute(sql, p) if self._conn else None
            except Exception:
                return None

    def _cget(self, k: str) -> Any:
        e = self._cache.get(k)
        if e is None or e.stale():
            self._cache.pop(k, None)
            return None
        self._cache[k] = self._cache.pop(k)
        return e.data

    def _cset(self, k: str, v: Any) -> None:
        if k in self._cache:
            del self._cache[k]
        self._cache[k] = _CE(v)
        if len(self._cache) > _CACHE_MAX:
            for ek in list(self._cache)[: _CACHE_MAX // 5]:
                del self._cache[ek]

    def _inv(self, *keys: str) -> None:
        for k in keys:
            self._cache.pop(k, None)

    # ── Page registration ────────────────────────────────────────────────────

    def upsert_page(self, page_id: str, account_id: str, niche: str, is_new: bool) -> None:
        self._exec(
            """INSERT INTO pages (page_id, account_id, niche, is_new, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(page_id) DO UPDATE SET
                   account_id = excluded.account_id,
                   niche      = excluded.niche""",
            (page_id, account_id, niche, int(is_new), time.time()),
        )
        self._inv(f"pg:{page_id}", f"acc:{account_id}", f"niche:{niche}")

    def get_page(self, page_id: str) -> dict[str, Any] | None:
        k = f"pg:{page_id}"
        with self._lock:
            c = self._cget(k)
        if c is not None:
            return c
        cur = self._exec(
            "SELECT page_id, account_id, niche, is_new, created_at FROM pages WHERE page_id = ?",
            (page_id,),
        )
        if cur is None:
            return None
        row = cur.fetchone()
        if row is None:
            return None
        data = {"page_id": row[0], "account_id": row[1], "niche": row[2],
                "is_new": bool(row[3]), "created_at": row[4]}
        with self._lock:
            self._cset(k, data)
        return data

    def get_pages_by_account(self, account_id: str) -> list[str]:
        cur = self._exec(
            "SELECT page_id FROM pages WHERE account_id = ?", (account_id,)
        )
        if cur is None:
            return []
        return [r[0] for r in cur.fetchall()]

    def get_pages_by_niche(self, niche: str) -> list[dict[str, Any]]:
        k = f"niche:{niche}"
        with self._lock:
            c = self._cget(k)
        if c is not None:
            return c
        cur = self._exec(
            "SELECT page_id, account_id, is_new FROM pages WHERE niche = ?", (niche,)
        )
        if cur is None:
            return []
        rows = [{"page_id": r[0], "account_id": r[1], "is_new": bool(r[2])}
                for r in cur.fetchall()]
        with self._lock:
            self._cset(k, rows)
        return rows

    # ── Metrics ───────────────────────────────────────────────────────────────

    def get_metrics(self, page_id: str) -> dict[str, Any] | None:
        k = f"pm:{page_id}"
        with self._lock:
            c = self._cget(k)
        if c is not None:
            return c
        cur = self._exec(
            "SELECT page_id, total_views, total_engagement, total_revenue, total_cost, "
            "profit, conversion_ema, engagement_ema, consistency_ema, "
            "post_count, status, updated_at "
            "FROM page_metrics WHERE page_id = ?",
            (page_id,),
        )
        if cur is None:
            return None
        row = cur.fetchone()
        if row is None:
            return None
        data = {
            "page_id":          row[0],
            "total_views":      float(row[1]),
            "total_engagement": float(row[2]),
            "total_revenue":    float(row[3]),
            "total_cost":       float(row[4]),
            "profit":           float(row[5]),
            "conversion_ema":   float(row[6]),
            "engagement_ema":   float(row[7]),
            "consistency_ema":  float(row[8]),
            "post_count":       int(row[9]),
            "status":           row[10],
            "updated_at":       float(row[11]),
        }
        with self._lock:
            self._cset(k, data)
        return data

    def update_metrics(
        self,
        page_id:    str,
        delta_views:      float,
        delta_engagement: float,
        delta_revenue:    float,
        delta_cost:       float,
        converted:        bool,
        delta_posts:      int,
    ) -> None:
        ex = self.get_metrics(page_id) or {
            "total_views": 0.0, "total_engagement": 0.0,
            "total_revenue": 0.0, "total_cost": 0.0, "profit": 0.0,
            "conversion_ema": 0.0, "engagement_ema": 0.5, "consistency_ema": 0.5,
            "post_count": 0, "status": "active",
        }
        new_views  = ex["total_views"]      + max(0.0, delta_views)
        new_eng    = ex["total_engagement"] + max(0.0, delta_engagement)
        new_rev    = ex["total_revenue"]    + max(0.0, delta_revenue)
        new_cost   = ex["total_cost"]       + max(0.0, delta_cost)
        new_profit = new_rev - new_cost
        new_pc     = ex["post_count"] + delta_posts

        # EMA signals
        eng_signal  = delta_engagement / max(delta_views, 1.0) if delta_views > 0 else 0.0
        conv_signal = 1.0 if converted else 0.0
        # Consistency: whether this post performed near the EMA (low variance → high consistency)
        eng_diff    = abs(eng_signal - ex["engagement_ema"])
        cons_signal = max(0.0, 1.0 - eng_diff * 2.0)

        new_conv_ema  = _EMA_ALPHA * conv_signal + (1 - _EMA_ALPHA) * ex["conversion_ema"]
        new_eng_ema   = _EMA_ALPHA * eng_signal  + (1 - _EMA_ALPHA) * ex["engagement_ema"]
        new_cons_ema  = _EMA_ALPHA * cons_signal + (1 - _EMA_ALPHA) * ex["consistency_ema"]

        # Status evaluation
        status = ex["status"]
        if status != "paused":
            if new_pc >= _PAUSE_MIN_POSTS and new_profit < _PAUSE_PROFIT_THRESHOLD:
                status = "paused"
            elif new_pc >= _THROTTLE_MIN_POSTS and new_profit < _THROTTLE_PROFIT_THRESHOLD:
                status = "throttled"
            else:
                status = "active"

        self._exec(
            """INSERT INTO page_metrics
               (page_id, total_views, total_engagement, total_revenue, total_cost,
                profit, conversion_ema, engagement_ema, consistency_ema,
                post_count, status, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(page_id) DO UPDATE SET
                   total_views      = excluded.total_views,
                   total_engagement = excluded.total_engagement,
                   total_revenue    = excluded.total_revenue,
                   total_cost       = excluded.total_cost,
                   profit           = excluded.profit,
                   conversion_ema   = excluded.conversion_ema,
                   engagement_ema   = excluded.engagement_ema,
                   consistency_ema  = excluded.consistency_ema,
                   post_count       = excluded.post_count,
                   status           = excluded.status,
                   updated_at       = excluded.updated_at""",
            (page_id, new_views, new_eng, new_rev, new_cost, new_profit,
             new_conv_ema, new_eng_ema, new_cons_ema, new_pc, status, time.time()),
        )
        with self._lock:
            self._inv(f"pm:{page_id}")

    # ── Maintenance ───────────────────────────────────────────────────────────

    def clear(self) -> None:
        for t in ("pages", "page_metrics"):
            self._exec(f"DELETE FROM {t}")
        with self._lock:
            self._cache.clear()

    def close(self) -> None:
        with self._lock:
            self._cache.clear()
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None


# ── Singleton ─────────────────────────────────────────────────────────────────

_STORE: _PageStore | None = None
_STORE_LOCK = threading.Lock()


def _get_store() -> _PageStore:
    global _STORE
    if _STORE is not None:
        return _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = _PageStore()
    return _STORE


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _sigmoid(x: float, k: float = 3.0) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-k * x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


# ── Part 1: Entity registration ──────────────────────────────────────────────

def register_page(
    page_id:    str,
    account_id: str,
    niche:      str,
    is_new:     bool = True,
) -> None:
    """
    Register or update a page entity.

    Args:
        page_id:    platform page/profile ID
        account_id: owner account
        niche:      content niche (beauty, tech, fitness…)
        is_new:     True for new/untested pages → exploration bucket
    """
    _get_store().upsert_page(page_id, account_id, niche, is_new)
    LOGGER.debug("page_intelligence registered page=%s account=%s niche=%s", page_id, account_id, niche)


# ── Part 2: Metrics update ────────────────────────────────────────────────────

def update_page_metrics(
    page_id:    str,
    views:      float = 0.0,
    engagement: float = 0.0,
    revenue:    float = 0.0,
    cost:       float = 0.0,
    converted:  bool  = False,
    post_count: int   = 1,
) -> dict[str, Any]:
    """
    Update page-level metrics after a posting cycle.

    Tracked signals:
        total_views, total_engagement, total_revenue, total_cost, profit
        conversion_ema, engagement_ema, consistency_ema, post_count, status

    Status transitions (auto):
        active → throttled  if profit < 0 after _THROTTLE_MIN_POSTS
        active → paused     if profit < _PAUSE_PROFIT_THRESHOLD after _PAUSE_MIN_POSTS

    Returns the current metrics dict after update.
    """
    _get_store().update_metrics(
        page_id    = page_id,
        delta_views      = views,
        delta_engagement = engagement,
        delta_revenue    = revenue,
        delta_cost       = cost,
        converted        = converted,
        delta_posts      = post_count,
    )
    return _get_store().get_metrics(page_id) or {}


# ── Part 3: Page scoring ──────────────────────────────────────────────────────

def get_page_score(page_id: str) -> float:
    """
    Compute page_score ∈ [0,1].

    page_score =
        0.35 × profit_margin_score   (sigmoid of profit / cost)
      + 0.25 × conversion_rate       (EMA)
      + 0.20 × engagement_quality    (EMA engagement ratio)
      + 0.20 × consistency           (EMA low-variance signal)

    Returns 0.5 (neutral) for unknown pages.
    Throttled/paused pages are penalized (score capped at 0.4).
    """
    m = _get_store().get_metrics(page_id)
    if m is None:
        return 0.5

    cost_base     = max(m["total_cost"], 1e-6)
    profit_margin = m["profit"] / cost_base
    profit_score  = _sigmoid(profit_margin)

    score = (
        _PW_PROFIT_MARGIN * profit_score
        + _PW_CONVERSION  * _clamp(m["conversion_ema"])
        + _PW_ENGAGEMENT  * _clamp(m["engagement_ema"])
        + _PW_CONSISTENCY * _clamp(m["consistency_ema"])
    )
    score = _clamp(score)

    # Status penalty
    if m["status"] == "paused":
        score = min(score, 0.20)
    elif m["status"] == "throttled":
        score = min(score, 0.40)

    return round(score, 4)


# ── Part 4: Account scoring ───────────────────────────────────────────────────

def get_account_score(account_id: str) -> float:
    """
    Aggregate page scores for an account → account_score ∈ [0,1].

    Strategy:
        - Collect all pages for account
        - Weighted average by post_count (more active pages matter more)
        - Accounts with no pages → 0.5 neutral

    Strong accounts (score > 0.65) → more production slots
    Weak accounts (score < 0.35)   → reduce or pause
    """
    store    = _get_store()
    page_ids = store.get_pages_by_account(account_id)
    if not page_ids:
        return 0.5

    total_weight = 0.0
    weighted_sum = 0.0
    for pid in page_ids:
        m = store.get_metrics(pid)
        if m is None:
            continue
        weight        = max(float(m["post_count"]), 1.0)
        weighted_sum += get_page_score(pid) * weight
        total_weight += weight

    if total_weight == 0:
        return 0.5
    return round(_clamp(weighted_sum / total_weight), 4)


# ── Part 5: Scaling integration helpers ──────────────────────────────────────

def get_page_status(page_id: str) -> str:
    """Return 'active' | 'throttled' | 'paused'. Default 'active' if unknown."""
    m = _get_store().get_metrics(page_id)
    return m["status"] if m else "active"


def is_page_throttled(page_id: str) -> bool:
    """True if page is throttled or paused."""
    return get_page_status(page_id) in ("throttled", "paused")


def get_page_posting_frequency(page_id: str) -> float:
    """
    Return recommended posting frequency multiplier for a page.

    Winners  (score > 0.65): ×1.5  — more slots, higher frequency
    Normal   (0.35–0.65):    ×1.0  — baseline
    Weak     (< 0.35):       ×0.5  — reduce
    Paused:                   ×0.0  — stop

    Returns float in [0.0, 1.5].
    """
    status = get_page_status(page_id)
    if status == "paused":
        return _FREQ_PAUSED
    score = get_page_score(page_id)
    if status == "throttled":
        return _FREQ_WEAK
    if score > 0.65:
        return _FREQ_WINNER
    if score >= 0.35:
        return _FREQ_NORMAL
    return _FREQ_WEAK


def get_page_budget_weights(niche: str) -> dict[str, float]:
    """
    Return budget weight per page for a niche.

    Weight = product_score × page_score for each active page.
    Throttled pages → weight × 0.3
    Paused pages    → weight = 0.0
    New pages       → fixed exploration share (handled separately)

    Returns dict {page_id: weight} normalised to sum=1.0.
    """
    store = _get_store()
    pages = store.get_pages_by_niche(niche)
    if not pages:
        return {}

    weights: dict[str, float] = {}
    for p in pages:
        pid    = p["page_id"]
        status = get_page_status(pid)
        if status == "paused":
            continue
        ps = get_page_score(pid)
        # Optional: multiply by product_score if available
        try:
            from core.product_intelligence import get_product_score_for_content
            # page_id used as proxy content_id for product lookup (best-effort)
            prod_s = get_product_score_for_content(pid)
        except Exception:
            prod_s = 0.5
        w = ps * prod_s
        if status == "throttled":
            w *= 0.3
        weights[pid] = w

    total = sum(weights.values())
    if total <= 0:
        return {}
    return {pid: round(w / total, 4) for pid, w in weights.items()}


# ── Part 7: Exploration ───────────────────────────────────────────────────────

def get_exploration_pages(niche: str, ratio: float = _EXPLORE_MIN_RATIO) -> list[str]:
    """
    Return new/untested pages for the exploration bucket (10–15%).

    A page is 'new' if:
        - is_new flag is True, OR
        - post_count < _NEW_PAGE_POSTS_WINDOW

    Args:
        niche: filter by niche
        ratio: fraction of total pages to sample (clamped to 10–15%)

    Returns:
        List of page_ids for exploration. Empty if none available.
    """
    ratio = _clamp(ratio, _EXPLORE_MIN_RATIO, _EXPLORE_MAX_RATIO)
    store = _get_store()
    all_pages = store.get_pages_by_niche(niche)

    new_pages = []
    for p in all_pages:
        pid = p["page_id"]
        if p.get("is_new"):
            new_pages.append(pid)
            continue
        m = store.get_metrics(pid)
        if m and m["post_count"] < _NEW_PAGE_POSTS_WINDOW:
            new_pages.append(pid)

    n = max(1, round(len(all_pages) * ratio))
    return new_pages[:n]


# ── Combined budget allocation (product_score × page_score) ──────────────────

def get_combined_budget_weights(niche: str) -> dict[str, dict[str, float]]:
    """
    Return budget weights combining product_score × page_score per page.

    Used by self_scaling to drive slot allocation.

    Returns:
        {
            "weights":     {page_id: normalised_weight},
            "exploration": [page_id, ...],
            "paused":      [page_id, ...],
        }
    """
    store   = _get_store()
    pages   = store.get_pages_by_niche(niche)
    weights: dict[str, float] = {}
    paused:  list[str]        = []
    explore_ids: list[str]    = get_exploration_pages(niche)
    explore_set  = set(explore_ids)

    for p in pages:
        pid    = p["page_id"]
        status = get_page_status(pid)
        if status == "paused":
            paused.append(pid)
            continue
        if pid in explore_set:
            continue   # exploration handled separately with fixed ratio
        w = get_page_score(pid)
        try:
            from core.product_intelligence import get_product_score_for_content
            w *= get_product_score_for_content(pid)
        except Exception:
            pass
        if status == "throttled":
            w *= 0.3
        weights[pid] = w

    total = sum(weights.values())
    norm  = {pid: round(w / total, 4) for pid, w in weights.items()} if total > 0 else {}

    return {
        "weights":     norm,
        "exploration": explore_ids,
        "paused":      paused,
    }


# ── Reset ─────────────────────────────────────────────────────────────────────

def reset_page_state() -> None:
    """Clear all page state. For testing only."""
    _get_store().clear()
