"""
core/product_intelligence.py — Product Intelligence Layer

Shifts optimization from content-level → product-level → profit-level.

Every piece of content maps to a product. The system tracks real business
metrics per product and adjusts decision/scaling signals accordingly.

Architecture:
    content produced
        ↓  register_content_product(content_id, product_id)
    attribution flush
        ↓  update_product_metrics(product_id, revenue, cost, converted)
    score / decide
        ↓  get_product_score(product_id)  →  boost/penalize content_decision
    generate gate
        ↓  is_product_killed(product_id)  →  block if loss after N attempts
    scale
        ↓  get_product_budget_allocation() → budget per product_id

Persistence:
    SQLite WAL + LRU cache (same pattern as profit_store / attribution_store)
    Path override: PRODUCT_STATE_DB env var (use ":memory:" for tests)

Public API:
    register_product(product_id, category, price_range, trend)  → None
    register_content_product(content_id, product_id)            → None
    update_product_metrics(product_id, revenue, cost, converted) → dict
    get_product_metrics(product_id)                             → dict | None
    get_product_score(product_id)                               → float [0,1]
    get_product_score_for_content(content_id)                   → float [0,1]
    get_product_for_content(content_id)                         → str | None
    is_product_killed(product_id)                               → bool
    reset_product_state()                                        # testing only
"""
from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("core.product_intelligence")

# ── Configuration ──────────────────────────────────────────────────────────────

_DEFAULT_DB = Path("data") / "product_state.db"

def _db_path() -> Path:
    env = os.environ.get("PRODUCT_STATE_DB")
    return Path(env) if env else _DEFAULT_DB

# Kill-switch: block after this many losing attempts
_KILL_MIN_ATTEMPTS: int   = 5
_KILL_PROFIT_THRESHOLD: float = 0.0   # profit < 0 = loss

# Product score weights
_PS_WEIGHT_PROFIT_MARGIN:  float = 0.40
_PS_WEIGHT_CONVERSION:     float = 0.25
_PS_WEIGHT_SCALABILITY:    float = 0.20
_PS_WEIGHT_TREND:          float = 0.15

# Sigmoid scale for profit_margin → score
_SIGMOID_K: float = 3.0

# Cache
_CACHE_MAX_KEYS: int   = 2048
_CACHE_TTL_S:   float = 20.0

# Content-decision score adjustment
_BOOST_THRESHOLD:   float =  0.65   # product_score above → +0.05 raw boost
_PENALIZE_THRESHOLD: float =  0.35  # product_score below → -0.05 raw penalty
_SCORE_DELTA:        float =  0.05


# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS products (
    product_id       TEXT PRIMARY KEY,
    category         TEXT    NOT NULL DEFAULT '',
    price_range      TEXT    NOT NULL DEFAULT '',
    trend            REAL    NOT NULL DEFAULT 0.5,
    created_at       REAL    NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS product_metrics (
    product_id       TEXT PRIMARY KEY,
    total_revenue    REAL    NOT NULL DEFAULT 0.0,
    total_cost       REAL    NOT NULL DEFAULT 0.0,
    profit           REAL    NOT NULL DEFAULT 0.0,
    conversion_ema   REAL    NOT NULL DEFAULT 0.0,
    content_count    INTEGER NOT NULL DEFAULT 0,
    attempt_count    INTEGER NOT NULL DEFAULT 0,
    scalability_ema  REAL    NOT NULL DEFAULT 0.0,
    killed           INTEGER NOT NULL DEFAULT 0,
    updated_at       REAL    NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS content_product_map (
    content_id  TEXT PRIMARY KEY,
    product_id  TEXT NOT NULL,
    mapped_at   REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_cpm_product ON content_product_map (product_id);
"""


# ── Cache ──────────────────────────────────────────────────────────────────────

class _CE:
    __slots__ = ("data", "ts")
    def __init__(self, data: Any) -> None:
        self.data = data
        self.ts   = time.monotonic()
    def stale(self) -> bool:
        return (time.monotonic() - self.ts) > _CACHE_TTL_S


# ── Store ──────────────────────────────────────────────────────────────────────

class _ProductStore:
    """SQLite-backed product intelligence store."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or _db_path()
        self._lock    = threading.Lock()
        self._cache:  dict[str, _CE] = {}
        self._conn:   sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(self._db_path), check_same_thread=False, isolation_level=None
            )
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.executescript(_DDL)
            conn.commit()
            self._conn = conn
        except Exception as exc:
            LOGGER.error("product_store init_failed path=%s error=%s", self._db_path, exc)
            self._conn = None

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor | None:
        if self._conn is None:
            return None
        try:
            return self._conn.execute(sql, params)
        except sqlite3.OperationalError as exc:
            LOGGER.warning("product_store db_error error=%s — reconnecting", exc)
            try:
                self._init_db()
                return self._conn.execute(sql, params) if self._conn else None
            except Exception:
                return None

    # Cache helpers
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
        if len(self._cache) > _CACHE_MAX_KEYS:
            evict = max(1, _CACHE_MAX_KEYS // 5)
            for ek in list(self._cache)[:evict]:
                del self._cache[ek]

    def _cinv(self, k: str) -> None:
        """Invalidate a cache key."""
        self._cache.pop(k, None)

    # ── Product registration ─────────────────────────────────────────────────

    def upsert_product(
        self,
        product_id: str,
        category:   str,
        price_range: str,
        trend:      float,
    ) -> None:
        now = time.time()
        self._exec(
            """INSERT INTO products (product_id, category, price_range, trend, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(product_id) DO UPDATE SET
                   category    = excluded.category,
                   price_range = excluded.price_range,
                   trend       = excluded.trend""",
            (product_id, category, price_range, trend, now),
        )
        with self._lock:
            self._cinv(f"prod:{product_id}")

    def get_product(self, product_id: str) -> dict[str, Any] | None:
        k = f"prod:{product_id}"
        with self._lock:
            cached = self._cget(k)
        if cached is not None:
            return cached
        cur = self._exec(
            "SELECT product_id, category, price_range, trend, created_at "
            "FROM products WHERE product_id = ?",
            (product_id,),
        )
        if cur is None:
            return None
        row = cur.fetchone()
        if row is None:
            return None
        data = {"product_id": row[0], "category": row[1],
                "price_range": row[2], "trend": row[3], "created_at": row[4]}
        with self._lock:
            self._cset(k, data)
        return data

    # ── Content → Product map ────────────────────────────────────────────────

    def map_content(self, content_id: str, product_id: str) -> None:
        now = time.time()
        self._exec(
            """INSERT INTO content_product_map (content_id, product_id, mapped_at)
               VALUES (?, ?, ?)
               ON CONFLICT(content_id) DO UPDATE SET
                   product_id = excluded.product_id,
                   mapped_at  = excluded.mapped_at""",
            (content_id, product_id, now),
        )
        with self._lock:
            self._cinv(f"cpm:{content_id}")
            self._cinv(f"pm:{product_id}")

    def get_product_for_content(self, content_id: str) -> str | None:
        k = f"cpm:{content_id}"
        with self._lock:
            cached = self._cget(k)
        if cached is not None:
            return cached
        cur = self._exec(
            "SELECT product_id FROM content_product_map WHERE content_id = ?",
            (content_id,),
        )
        if cur is None:
            return None
        row = cur.fetchone()
        result = row[0] if row else None
        with self._lock:
            if result is not None:
                self._cset(k, result)
        return result

    def count_product_contents(self, product_id: str) -> int:
        cur = self._exec(
            "SELECT COUNT(*) FROM content_product_map WHERE product_id = ?",
            (product_id,),
        )
        if cur is None:
            return 0
        row = cur.fetchone()
        return int(row[0]) if row else 0

    # ── Metrics ──────────────────────────────────────────────────────────────

    def get_metrics(self, product_id: str) -> dict[str, Any] | None:
        k = f"pm:{product_id}"
        with self._lock:
            cached = self._cget(k)
        if cached is not None:
            return cached
        cur = self._exec(
            "SELECT product_id, total_revenue, total_cost, profit, "
            "conversion_ema, content_count, attempt_count, scalability_ema, killed, updated_at "
            "FROM product_metrics WHERE product_id = ?",
            (product_id,),
        )
        if cur is None:
            return None
        row = cur.fetchone()
        if row is None:
            return None
        data = {
            "product_id":     row[0],
            "total_revenue":  float(row[1]),
            "total_cost":     float(row[2]),
            "profit":         float(row[3]),
            "conversion_ema": float(row[4]),
            "content_count":  int(row[5]),
            "attempt_count":  int(row[6]),
            "scalability_ema":float(row[7]),
            "killed":         bool(row[8]),
            "updated_at":     float(row[9]),
        }
        with self._lock:
            self._cset(k, data)
        return data

    def update_metrics(
        self,
        product_id:    str,
        delta_revenue: float,
        delta_cost:    float,
        converted:     bool,   # True if this update includes a conversion
        new_content:   bool,   # True if a new content item was created
        perf_signal:   float,  # [0,1] — content perf to update scalability EMA
    ) -> None:
        """Accumulate revenue/cost, update EMA conversion and scalability."""
        existing = self.get_metrics(product_id)
        if existing is None:
            existing = {
                "total_revenue": 0.0, "total_cost": 0.0, "profit": 0.0,
                "conversion_ema": 0.0, "content_count": 0, "attempt_count": 0,
                "scalability_ema": 0.5, "killed": False, "updated_at": 0.0,
            }

        alpha = 0.20
        new_revenue  = existing["total_revenue"] + delta_revenue
        new_cost     = existing["total_cost"]    + delta_cost
        new_profit   = new_revenue - new_cost
        new_conv_ema = alpha * (1.0 if converted else 0.0) + (1 - alpha) * existing["conversion_ema"]
        new_scal_ema = alpha * perf_signal + (1 - alpha) * existing["scalability_ema"]
        new_cc       = existing["content_count"]  + (1 if new_content  else 0)
        new_att      = existing["attempt_count"]  + 1

        # Kill switch evaluation
        killed = existing["killed"]
        if (
            not killed
            and new_profit < _KILL_PROFIT_THRESHOLD
            and new_att >= _KILL_MIN_ATTEMPTS
        ):
            killed = True
            LOGGER.warning(
                "product_intelligence kill_switch fired product=%s "
                "profit=%.4f attempts=%d",
                product_id, new_profit, new_att,
            )

        now = time.time()
        self._exec(
            """INSERT INTO product_metrics
               (product_id, total_revenue, total_cost, profit, conversion_ema,
                content_count, attempt_count, scalability_ema, killed, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(product_id) DO UPDATE SET
                   total_revenue  = excluded.total_revenue,
                   total_cost     = excluded.total_cost,
                   profit         = excluded.profit,
                   conversion_ema = excluded.conversion_ema,
                   content_count  = excluded.content_count,
                   attempt_count  = excluded.attempt_count,
                   scalability_ema= excluded.scalability_ema,
                   killed         = excluded.killed,
                   updated_at     = excluded.updated_at""",
            (product_id, new_revenue, new_cost, new_profit, new_conv_ema,
             new_cc, new_att, new_scal_ema, int(killed), now),
        )
        with self._lock:
            self._cinv(f"pm:{product_id}")

    # ── Maintenance ───────────────────────────────────────────────────────────

    def clear(self) -> None:
        for table in ("products", "product_metrics", "content_product_map"):
            self._exec(f"DELETE FROM {table}")
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


# ── Singleton ──────────────────────────────────────────────────────────────────

_STORE: _ProductStore | None = None
_STORE_LOCK = threading.Lock()


def _get_store(db_path: Path | None = None) -> _ProductStore:
    global _STORE
    if _STORE is not None:
        return _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = _ProductStore(db_path)
    return _STORE


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _sigmoid(x: float, k: float = _SIGMOID_K) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-k * x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


# ── Part 1: Product registration ─────────────────────────────────────────────

def register_product(
    product_id:  str,
    category:    str   = "",
    price_range: str   = "",
    trend:       float = 0.5,
) -> None:
    """
    Register or update a product in the intelligence store.

    Args:
        product_id:  unique product identifier (e.g. "SP123", "nike-air-max-270")
        category:    product category (e.g. "skincare", "footwear", "tech")
        price_range: price tier (e.g. "budget", "mid", "premium") or "100-200"
        trend:       external trend signal [0,1] (default 0.5)
    """
    _get_store().upsert_product(product_id, category, price_range, _clamp(trend))
    LOGGER.debug("product_intelligence registered product=%s cat=%s", product_id, category)


def register_content_product(content_id: str, product_id: str) -> None:
    """
    Map a content item to a product.

    This link allows:
      - product_score to influence content scoring
      - product kill-switch to block content generation
      - budget allocation to be grouped by product

    Args:
        content_id: content identifier (same as used in profit_engine)
        product_id: registered product identifier
    """
    _get_store().map_content(content_id, product_id)
    LOGGER.debug(
        "product_intelligence mapped content=%s → product=%s", content_id, product_id
    )


# ── Part 2: Metrics update ────────────────────────────────────────────────────

def update_product_metrics(
    product_id:    str,
    revenue:       float,
    cost:          float,
    converted:     bool  = False,
    new_content:   bool  = False,
    perf_signal:   float = 0.5,
) -> dict[str, Any]:
    """
    Update aggregated metrics for a product after a content tracking cycle.

    Called after attribution_engine.flush_to_profit_engine() when real
    revenue is available.

    Args:
        product_id:  product to update
        revenue:     revenue from this cycle (real or estimated)
        cost:        production cost for this cycle
        converted:   True if at least one conversion occurred
        new_content: True if a new content piece was produced for this product
        perf_signal: content performance score [0,1] for scalability EMA

    Returns:
        Current metrics dict after update.
    """
    store = _get_store()
    store.update_metrics(
        product_id    = product_id,
        delta_revenue = max(0.0, revenue),
        delta_cost    = max(0.0, cost),
        converted     = converted,
        new_content   = new_content,
        perf_signal   = _clamp(perf_signal),
    )
    result = store.get_metrics(product_id)
    return result or {}


# ── Part 3: Product scoring ───────────────────────────────────────────────────

def get_product_score(product_id: str) -> float:
    """
    Compute product_score ∈ [0,1].

    product_score =
        0.40 × profit_margin_score    (sigmoid of profit/cost)
      + 0.25 × conversion_rate        (EMA of conversion events)
      + 0.20 × scalability            (EMA of content performance)
      + 0.15 × trend                  (from product registration)

    Returns 0.5 (neutral) if product is unknown or unscored.
    """
    store   = _get_store()
    metrics = store.get_metrics(product_id)
    if metrics is None:
        return 0.5

    # profit_margin → sigmoid score
    cost_base      = max(metrics["total_cost"], 1e-6)
    profit_margin  = metrics["profit"] / cost_base
    profit_score   = _sigmoid(profit_margin)

    # scalability: EMA perf × content diversity bonus
    content_count  = max(metrics["content_count"], 1)
    diversity_bonus = _clamp(math.log(content_count + 1) / math.log(11))  # 0–1 over 10 items
    scalability    = _clamp(metrics["scalability_ema"] * (0.80 + 0.20 * diversity_bonus))

    # trend: from product definition
    prod   = store.get_product(product_id)
    trend  = prod["trend"] if prod else 0.5

    score = (
        _PS_WEIGHT_PROFIT_MARGIN * profit_score
        + _PS_WEIGHT_CONVERSION  * _clamp(metrics["conversion_ema"])
        + _PS_WEIGHT_SCALABILITY * scalability
        + _PS_WEIGHT_TREND       * _clamp(trend)
    )
    return round(_clamp(score), 4)


def get_product_score_for_content(content_id: str) -> float:
    """
    Return product_score for the product linked to this content.

    Returns 0.5 (neutral) if content has no product mapping.
    """
    pid = _get_store().get_product_for_content(content_id)
    if pid is None:
        return 0.5
    return get_product_score(pid)


def get_product_for_content(content_id: str) -> str | None:
    """Return the product_id mapped to a content_id, or None."""
    return _get_store().get_product_for_content(content_id)


# ── Part 6: Kill switch ───────────────────────────────────────────────────────

def is_product_killed(product_id: str) -> bool:
    """
    Returns True if the product kill switch has been fired.

    Kill conditions (ALL must be true):
        1. profit < 0  (money-losing product)
        2. attempt_count >= _KILL_MIN_ATTEMPTS (gave it enough tries)

    Once killed, the product stays killed until reset_product_state().
    Callers should check this BEFORE generating content for the product.

    Returns False for unknown products (default = allow).
    """
    if not product_id:
        return False
    metrics = _get_store().get_metrics(product_id)
    return bool(metrics and metrics.get("killed", False))


def is_content_product_killed(content_id: str) -> bool:
    """
    Returns True if the product linked to this content has been killed.

    Returns False if content has no product mapping.
    """
    pid = get_product_for_content(content_id)
    if pid is None:
        return False
    return is_product_killed(pid)


# ── Part 4 helpers: score delta for content_decision ─────────────────────────

def get_score_delta(content_id: str, product_id: str = "") -> float:
    """
    Return a score adjustment delta for content_decision scoring.

    +_SCORE_DELTA  if product_score > _BOOST_THRESHOLD  (high-profit product → easier to pass)
    -_SCORE_DELTA  if product_score < _PENALIZE_THRESHOLD (bad product → harder to produce)
     0.0            if product_score is neutral or no mapping

    Args:
        content_id: content identifier
        product_id: optional override (if already known)

    Returns:
        float — one of {+0.05, 0.0, -0.05}
    """
    pid = product_id or get_product_for_content(content_id)
    if pid is None:
        return 0.0
    ps = get_product_score(pid)
    if ps > _BOOST_THRESHOLD:
        return +_SCORE_DELTA
    if ps < _PENALIZE_THRESHOLD:
        return -_SCORE_DELTA
    return 0.0


# ── Part 2 extended: metrics query API ───────────────────────────────────────

def get_product_metrics(product_id: str) -> dict[str, Any] | None:
    """
    Return all product metrics.

    Keys: product_id, total_revenue, total_cost, profit, conversion_ema,
          content_count, attempt_count, scalability_ema, killed, updated_at
    """
    return _get_store().get_metrics(product_id)


def get_product_info(product_id: str) -> dict[str, Any] | None:
    """Return product registration info (category, price_range, trend)."""
    return _get_store().get_product(product_id)


# ── Reset ─────────────────────────────────────────────────────────────────────

def reset_product_state() -> None:
    """Clear all product state. For testing only."""
    _get_store().clear()
