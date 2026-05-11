"""
core/profit_engine.py — Profit Optimization Layer (v2 — persistent)

Pipeline position:
    TRACK → profit_engine.update_profit() → self_scaling.update_performance()
          → content_decision reads profit_score next cycle

Design contracts:
  - profit = revenue - cost  (actual or estimated)
  - profit_score = sigmoid(profit_margin)  ∈ [0, 1]
  - EMA α = 0.20 normal / 0.40 on high-profit spike
  - All signals bounded [0, 1]
  - Deterministic hashing: sha256(content_id|niche)[:16]
  - Anti-fake-viral: high views + low profit → cap scaling_factor ≤ 1.2

Persistence:
  - State stored in SQLite via core.profit_store (globally consistent)
  - LRU cache (4096 keys, TTL=15s) avoids redundant DB reads
  - In-process log (_PROFIT_LOG) kept in-memory (audit only, non-critical)
  - Module memory is NEVER the authoritative source for profit_score

Public API:
    update_profit(content_id, niche, revenue, cost) -> ProfitRecord
    get_profit_score(content_id, niche)             -> float
    get_profit_record(content_id, niche)            -> dict | None
    get_profit_log(last_n)                          -> list[dict]
    is_fake_viral(content_id, niche, views_ratio)   -> bool
    get_fake_viral_sf_cap()                         -> float
    reset_profit_state()                            # for testing
"""
from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, field
from typing import Any

LOGGER = logging.getLogger("core.profit_engine")

# ── Constants ─────────────────────────────────────────────────────────────────

_PROFIT_ALPHA_NORMAL:  float = 0.20
_PROFIT_ALPHA_SPIKE:   float = 0.40
_SPIKE_THRESHOLD:      float = 0.70   # profit_score above this → spike alpha

# Default commission rate when no explicit value given (affiliate proxy)
_DEFAULT_COMMISSION:   float = 0.05   # 5%

# Sigmoid scale: controls sensitivity of profit_score to profit_margin
# k=3 → score 0.5 at margin=0, saturates quickly near ±2
_SIGMOID_K: float = 3.0

# Anti-fake-viral: if views_ratio > threshold but profit_score < threshold → cap SF
_FAKE_VIRAL_VIEWS_THRESHOLD:  float = 0.70
_FAKE_VIRAL_PROFIT_THRESHOLD: float = 0.35
_FAKE_VIRAL_SF_CAP:           float = 1.20

# ── In-process audit log (non-persistent, ring buffer) ────────────────────────
_PROFIT_LOG:   list[dict[str, Any]] = []
_MAX_LOG_SIZE: int = 5_000


# ── ProfitRecord data class ───────────────────────────────────────────────────

@dataclass
class ProfitRecord:
    """EMA-smoothed per-content profit state (backed by profit_store)."""
    content_id:     str
    niche:          str
    revenue_ema:    float = 0.0
    cost_ema:       float = 0.0
    profit_ema:     float = 0.0   # = revenue_ema - cost_ema (signed)
    profit_score:   float = 0.5   # sigmoid-normalised [0,1]
    profit_margin:  float = 0.0   # profit / max(cost, 1e-6)
    update_count:   int   = 0
    last_revenue:   float = 0.0
    last_cost:      float = 0.0
    last_profit:    float = 0.0
    history:        list[float] = field(default_factory=list)  # rolling profit_score

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "content_id":    self.content_id,
            "niche":         self.niche,
            "revenue_ema":   self.revenue_ema,
            "cost_ema":      self.cost_ema,
            "profit_ema":    self.profit_ema,
            "profit_score":  self.profit_score,
            "profit_margin": self.profit_margin,
            "update_count":  self.update_count,
            "last_revenue":  self.last_revenue,
            "last_cost":     self.last_cost,
            "last_profit":   self.last_profit,
            "history":       list(self.history),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ProfitRecord":
        rec = cls(
            content_id     = d["content_id"],
            niche          = d["niche"],
            revenue_ema    = float(d.get("revenue_ema",    0.0)),
            cost_ema       = float(d.get("cost_ema",       0.0)),
            profit_ema     = float(d.get("profit_ema",     0.0)),
            profit_score   = float(d.get("profit_score",   0.5)),
            profit_margin  = float(d.get("profit_margin",  0.0)),
            update_count   = int(  d.get("update_count",   0  )),
            last_revenue   = float(d.get("last_revenue",   0.0)),
            last_cost      = float(d.get("last_cost",      0.0)),
            last_profit    = float(d.get("last_profit",    0.0)),
            history        = list( d.get("history",        [] )),
        )
        return rec


# ── Helpers ───────────────────────────────────────────────────────────────────

def _key(content_id: str, niche: str) -> str:
    """Deterministic SHA-256[:16] key. Same across all processes/workers."""
    return hashlib.sha256(f"{content_id}|{niche}".encode()).hexdigest()[:16]


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _sigmoid(x: float, k: float = _SIGMOID_K) -> float:
    """
    Sigmoid centred at 0.
      x = 0   → 0.5  (breakeven)
      x → +∞  → 1.0  (high profit)
      x → -∞  → 0.0  (loss)
    """
    try:
        return 1.0 / (1.0 + math.exp(-k * x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def _get_store():
    """Lazy import to avoid circular dependency at module load."""
    from core.profit_store import get_profit_store
    return get_profit_store()


def _load_record(k: str, content_id: str, niche: str) -> ProfitRecord:
    """Load record from persistent store, or create a new blank one."""
    try:
        store = _get_store()
        data  = store.get(k)
        if data is not None:
            return ProfitRecord.from_dict(data)
    except Exception as exc:
        LOGGER.warning("profit_engine load_failed key=%s error=%s — using fresh", k, exc)
    return ProfitRecord(content_id=content_id, niche=niche)


def _save_record(k: str, rec: ProfitRecord) -> None:
    """Persist record to shared store (SQLite + cache)."""
    try:
        store = _get_store()
        store.set(k, rec.to_dict(), content_id=rec.content_id, niche=rec.niche)
    except Exception as exc:
        LOGGER.warning("profit_engine save_failed key=%s error=%s", k, exc)


# ── Part 1: Profit update ─────────────────────────────────────────────────────

def update_profit(
    content_id:     str,
    niche:          str,
    revenue:        float,          # estimated or real revenue (USD or proxy units)
    cost:           float,          # total production + compute cost
    affiliate_rate: float = 0.0,    # optional: affiliate commission already included in revenue
) -> ProfitRecord:
    """
    Update profit EMA for a content item after TRACK stage.

    State is read from and written to the shared SQLite store so that all
    workers, pipeline instances, and decision layer processes see the same
    profit_score for a given (content_id, niche) pair.

    Profit formula:
        profit = revenue - cost
        profit_margin = profit / max(cost, 1e-6)
        profit_score  = sigmoid(profit_margin)   ∈ [0, 1]

    EMA alpha:
        α = 0.40 if profit_score >= 0.70 (high-profit spike)
        α = 0.20 otherwise

    Args:
        content_id:     unique content identifier
        niche:          account niche (for isolation)
        revenue:        estimated revenue (affiliate × conversion × commission, etc.)
        cost:           total production cost (AI + compute + time)
        affiliate_rate: optional rate already factored into revenue

    Returns:
        Updated ProfitRecord (also written to shared store)
    """
    k   = _key(content_id, niche)
    rec = _load_record(k, content_id, niche)

    # Clamp inputs to reasonable range (revenue can exceed 1.0 in raw form)
    revenue = max(0.0, revenue)
    cost    = max(0.0, cost)

    profit        = revenue - cost
    profit_margin = profit / max(cost, 1e-6)
    raw_score     = _sigmoid(profit_margin)

    # EMA alpha: spike if current reading is high-profit
    alpha = _PROFIT_ALPHA_SPIKE if raw_score >= _SPIKE_THRESHOLD else _PROFIT_ALPHA_NORMAL

    # EMA updates
    rec.revenue_ema   = alpha * revenue  + (1 - alpha) * rec.revenue_ema
    rec.cost_ema      = alpha * cost     + (1 - alpha) * rec.cost_ema
    rec.profit_ema    = alpha * profit   + (1 - alpha) * rec.profit_ema
    rec.profit_score  = round(
        alpha * raw_score + (1 - alpha) * rec.profit_score, 6
    )
    rec.profit_margin = round(
        rec.profit_ema / max(rec.cost_ema, 1e-6), 6
    )

    rec.last_revenue  = revenue
    rec.last_cost     = cost
    rec.last_profit   = profit
    rec.update_count += 1

    # Rolling history (last 10)
    rec.history.append(round(rec.profit_score, 4))
    if len(rec.history) > 10:
        rec.history.pop(0)

    # ── Persist to shared store ───────────────────────────────────────────────
    _save_record(k, rec)

    # ── In-process audit log (ring buffer) ───────────────────────────────────
    entry: dict[str, Any] = {
        "content_id":    content_id,
        "niche":         niche,
        "revenue":       round(revenue, 4),
        "cost":          round(cost, 4),
        "profit":        round(profit, 4),
        "profit_margin": round(profit_margin, 4),
        "profit_score":  round(rec.profit_score, 4),
        "alpha_used":    alpha,
        "update_count":  rec.update_count,
    }
    _PROFIT_LOG.append(entry)
    if len(_PROFIT_LOG) > _MAX_LOG_SIZE:
        del _PROFIT_LOG[: len(_PROFIT_LOG) - _MAX_LOG_SIZE]

    LOGGER.debug(
        "profit_engine update content=%s niche=%s rev=%.3f cost=%.3f "
        "profit=%.3f margin=%.3f score=%.3f alpha=%.2f",
        content_id, niche, revenue, cost, profit, profit_margin,
        rec.profit_score, alpha,
    )
    return rec


# ── Part 1 API ────────────────────────────────────────────────────────────────

def get_profit_score(content_id: str, niche: str) -> float:
    """
    Return the current EMA profit score [0, 1] from the shared store.

    0.5 = breakeven baseline (no profit data yet)
    > 0.5 = profitable
    < 0.5 = loss-making

    Reads from SQLite (cache-first). Safe to call from any worker/process.
    Returns 0.5 (neutral) on any error.
    """
    k = _key(content_id, niche)
    try:
        store = _get_store()
        data  = store.get(k)
        if data is not None:
            return float(data.get("profit_score", 0.5))
    except Exception as exc:
        LOGGER.warning("profit_engine get_score_failed key=%s error=%s", k, exc)
    return 0.5


def get_profit_record(content_id: str, niche: str) -> dict[str, Any] | None:
    """Return full profit record as dict, or None if unknown."""
    k = _key(content_id, niche)
    try:
        store = _get_store()
        data  = store.get(k)
        if data is None:
            return None
        return {
            "content_id":    data.get("content_id",    content_id),
            "niche":         data.get("niche",          niche),
            "revenue_ema":   round(float(data.get("revenue_ema",   0.0)), 4),
            "cost_ema":      round(float(data.get("cost_ema",      0.0)), 4),
            "profit_ema":    round(float(data.get("profit_ema",    0.0)), 4),
            "profit_margin": round(float(data.get("profit_margin", 0.0)), 4),
            "profit_score":  round(float(data.get("profit_score",  0.5)), 4),
            "update_count":  int(  data.get("update_count",        0  )),
            "last_revenue":  round(float(data.get("last_revenue",  0.0)), 4),
            "last_cost":     round(float(data.get("last_cost",     0.0)), 4),
            "last_profit":   round(float(data.get("last_profit",   0.0)), 4),
            "history":       list( data.get("history",             [] )),
        }
    except Exception as exc:
        LOGGER.warning("profit_engine get_record_failed key=%s error=%s", k, exc)
        return None


def get_profit_log(last_n: int = 100) -> list[dict[str, Any]]:
    """Return the most recent N profit update entries (in-process log)."""
    return _PROFIT_LOG[-last_n:]


def is_fake_viral(content_id: str, niche: str, views_ratio: float) -> bool:
    """
    Anti-fake-viral check (reads from shared store).

    Returns True if content has high views but low profit →
    caller should cap scaling_factor ≤ 1.2.

    Args:
        views_ratio: normalised views signal [0,1]
    """
    if views_ratio < _FAKE_VIRAL_VIEWS_THRESHOLD:
        return False
    ps = get_profit_score(content_id, niche)
    return ps < _FAKE_VIRAL_PROFIT_THRESHOLD


def get_fake_viral_sf_cap() -> float:
    """Return the scaling factor cap applied to fake-viral content."""
    return _FAKE_VIRAL_SF_CAP


# ── Reset ─────────────────────────────────────────────────────────────────────

def reset_profit_state() -> None:
    """
    Clear all profit state (shared store + in-process log).

    For testing only. Wipes the SQLite table and cache.
    """
    global _PROFIT_LOG
    _PROFIT_LOG.clear()
    try:
        store = _get_store()
        store.clear()
    except Exception as exc:
        LOGGER.warning("profit_engine reset_failed error=%s", exc)
