"""
core/attribution_store.py — Shared Persistent Attribution State (SQLite-backed)

Stores touch paths, conversions, and attribution results in a durable,
cross-process SQLite database with an in-process LRU+TTL cache layer.

Tables:
    touches       — every click event per tracking_code
    conversions   — every conversion event per tracking_code
    attr_results  — per-content_id attributed revenue summary

Design contracts:
  - WAL mode → concurrent readers, single writer
  - Thread-safe cache via threading.Lock
  - Atomic upserts via INSERT OR REPLACE / INSERT
  - Path override via ATTRIBUTION_STATE_DB env var
  - :memory: path for test isolation

Public API (used only by attribution_engine.py):
    get_attribution_store() -> AttributionStore
    reset_attribution_store()
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("core.attribution_store")

# ── Config ────────────────────────────────────────────────────────────────────

_DEFAULT_DB = Path("data") / "attribution_state.db"

def _db_path() -> Path:
    env = os.environ.get("ATTRIBUTION_STATE_DB")
    return Path(env) if env else _DEFAULT_DB

_CACHE_MAX_KEYS: int   = 1024
_CACHE_TTL_S:   float = 30.0

# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS touches (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    tracking_code  TEXT NOT NULL,
    content_id     TEXT NOT NULL,
    page_id        TEXT NOT NULL,
    niche          TEXT NOT NULL DEFAULT '',
    account_id     TEXT NOT NULL DEFAULT '',
    ts             REAL NOT NULL,
    click_ts       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_touches_code ON touches (tracking_code);
CREATE INDEX IF NOT EXISTS idx_touches_content ON touches (content_id);

CREATE TABLE IF NOT EXISTS conversions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    tracking_code  TEXT NOT NULL,
    content_id     TEXT NOT NULL,
    page_id        TEXT NOT NULL,
    niche          TEXT NOT NULL DEFAULT '',
    account_id     TEXT NOT NULL DEFAULT '',
    revenue        REAL NOT NULL DEFAULT 0.0,
    ts             REAL NOT NULL,
    conversion_ts  REAL NOT NULL,
    attributed     INTEGER NOT NULL DEFAULT 0   -- 0=pending, 1=done
);
CREATE INDEX IF NOT EXISTS idx_conv_code    ON conversions (tracking_code);
CREATE INDEX IF NOT EXISTS idx_conv_content ON conversions (content_id);

CREATE TABLE IF NOT EXISTS attr_results (
    content_id      TEXT NOT NULL,
    niche           TEXT NOT NULL DEFAULT '',
    page_id         TEXT NOT NULL DEFAULT '',
    account_id      TEXT NOT NULL DEFAULT '',
    clicks          INTEGER NOT NULL DEFAULT 0,
    conversions     INTEGER NOT NULL DEFAULT 0,
    attributed_rev  REAL    NOT NULL DEFAULT 0.0,
    assist_rev      REAL    NOT NULL DEFAULT 0.0,
    total_rev       REAL    NOT NULL DEFAULT 0.0,
    updated_at      REAL    NOT NULL DEFAULT 0.0,
    PRIMARY KEY (content_id, niche)
);
"""

# ── Cache ─────────────────────────────────────────────────────────────────────

class _CE:
    __slots__ = ("data", "ts")
    def __init__(self, data: Any) -> None:
        self.data = data
        self.ts   = time.monotonic()
    def stale(self) -> bool:
        return (time.monotonic() - self.ts) > _CACHE_TTL_S


# ── Store ─────────────────────────────────────────────────────────────────────

class AttributionStore:
    """Thread-safe SQLite-backed attribution store with LRU cache."""

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
                str(self._db_path),
                check_same_thread=False,
                isolation_level=None,
            )
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA cache_size=-4000;")
            conn.executescript(_DDL)
            conn.commit()
            self._conn = conn
            LOGGER.debug("attribution_store DB ready path=%s", self._db_path)
        except Exception as exc:
            LOGGER.error("attribution_store init_failed path=%s error=%s", self._db_path, exc)
            self._conn = None

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor | None:
        if self._conn is None:
            return None
        try:
            return self._conn.execute(sql, params)
        except sqlite3.OperationalError as exc:
            LOGGER.warning("attribution_store db_error error=%s — reconnecting", exc)
            try:
                self._init_db()
                return self._conn.execute(sql, params) if self._conn else None
            except Exception:
                return None

    # ── Cache helpers ──────────────────────────────────────────────────────────

    def _cget(self, k: str) -> Any:
        e = self._cache.get(k)
        if e is None or e.stale():
            self._cache.pop(k, None)
            return None
        self._cache[k] = self._cache.pop(k)  # LRU
        return e.data

    def _cset(self, k: str, v: Any) -> None:
        if k in self._cache:
            del self._cache[k]
        self._cache[k] = _CE(v)
        if len(self._cache) > _CACHE_MAX_KEYS:
            evict = max(1, _CACHE_MAX_KEYS // 5)
            for ek in list(self._cache)[:evict]:
                del self._cache[ek]

    # ── Touch API ──────────────────────────────────────────────────────────────

    def insert_touch(
        self,
        tracking_code: str,
        content_id:    str,
        page_id:       str,
        niche:         str,
        account_id:    str,
        origin_ts:     float,
        click_ts:      float,
    ) -> None:
        """Record a single click touch event."""
        self._exec(
            """INSERT INTO touches
               (tracking_code, content_id, page_id, niche, account_id, ts, click_ts)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (tracking_code, content_id, page_id, niche, account_id, origin_ts, click_ts),
        )
        # Invalidate attr_results cache for this content
        with self._lock:
            self._cache.pop(f"ar:{content_id}:{niche}", None)

    def get_touches(self, tracking_code: str) -> list[dict[str, Any]]:
        """Return all touch records for a specific tracking code."""
        k = f"t:{tracking_code}"
        with self._lock:
            cached = self._cget(k)
        if cached is not None:
            return cached
        cur = self._exec(
            "SELECT content_id, page_id, niche, account_id, ts, click_ts "
            "FROM touches WHERE tracking_code = ? ORDER BY click_ts ASC",
            (tracking_code,),
        )
        if cur is None:
            return []
        rows = [
            {"content_id": r[0], "page_id": r[1], "niche": r[2],
             "account_id": r[3], "ts": r[4], "click_ts": r[5]}
            for r in cur.fetchall()
        ]
        with self._lock:
            self._cset(k, rows)
        return rows

    def get_touches_by_content(self, content_id: str) -> list[dict[str, Any]]:
        """
        Return ALL touch records for a content_id, across any tracking code.

        Used by multi-touch attribution to find assist touches that may have
        been recorded under different codes (different page_ids / timestamps).
        Ordered by click_ts ASC (oldest first).
        """
        k = f"tc:{content_id}"
        with self._lock:
            cached = self._cget(k)
        if cached is not None:
            return cached
        cur = self._exec(
            "SELECT tracking_code, content_id, page_id, niche, account_id, ts, click_ts "
            "FROM touches WHERE content_id = ? ORDER BY click_ts ASC",
            (content_id,),
        )
        if cur is None:
            return []
        rows = [
            {"tracking_code": r[0], "content_id": r[1], "page_id": r[2],
             "niche": r[3], "account_id": r[4], "ts": r[5], "click_ts": r[6]}
            for r in cur.fetchall()
        ]
        with self._lock:
            self._cset(k, rows)
        return rows

    # ── Conversion API ─────────────────────────────────────────────────────────

    def insert_conversion(
        self,
        tracking_code: str,
        content_id:    str,
        page_id:       str,
        niche:         str,
        account_id:    str,
        revenue:       float,
        origin_ts:     float,
        conversion_ts: float,
    ) -> int:
        """Insert conversion; return new row id."""
        cur = self._exec(
            """INSERT INTO conversions
               (tracking_code, content_id, page_id, niche, account_id,
                revenue, ts, conversion_ts, attributed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (tracking_code, content_id, page_id, niche, account_id,
             revenue, origin_ts, conversion_ts),
        )
        with self._lock:
            self._cache.pop(f"ar:{content_id}:{niche}", None)
        return (cur.lastrowid or -1) if cur else -1

    def get_pending_conversions(self) -> list[dict[str, Any]]:
        """Return all conversions not yet attributed."""
        cur = self._exec(
            "SELECT id, tracking_code, content_id, page_id, niche, "
            "account_id, revenue, ts, conversion_ts "
            "FROM conversions WHERE attributed = 0 ORDER BY conversion_ts ASC",
        )
        if cur is None:
            return []
        return [
            {"id": r[0], "tracking_code": r[1], "content_id": r[2],
             "page_id": r[3], "niche": r[4], "account_id": r[5],
             "revenue": r[6], "ts": r[7], "conversion_ts": r[8]}
            for r in cur.fetchall()
        ]

    def mark_attributed(self, conversion_ids: list[int]) -> None:
        """Mark conversions as attributed."""
        if not conversion_ids:
            return
        placeholders = ",".join("?" * len(conversion_ids))
        self._exec(
            f"UPDATE conversions SET attributed = 1 WHERE id IN ({placeholders})",
            tuple(conversion_ids),
        )

    # ── Attribution Results API ────────────────────────────────────────────────

    def upsert_attr_result(
        self,
        content_id:     str,
        niche:          str,
        page_id:        str,
        account_id:     str,
        delta_clicks:   int,
        delta_conv:     int,
        delta_attr_rev: float,
        delta_asst_rev: float,
    ) -> None:
        """Accumulate attribution deltas atomically."""
        now = time.time()
        self._exec(
            """INSERT INTO attr_results
               (content_id, niche, page_id, account_id,
                clicks, conversions, attributed_rev, assist_rev, total_rev, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(content_id, niche) DO UPDATE SET
                   clicks          = clicks + excluded.clicks,
                   conversions     = conversions + excluded.conversions,
                   attributed_rev  = attributed_rev + excluded.attributed_rev,
                   assist_rev      = assist_rev + excluded.assist_rev,
                   total_rev       = total_rev + excluded.attributed_rev + excluded.assist_rev,
                   updated_at      = excluded.updated_at""",
            (content_id, niche, page_id, account_id,
             delta_clicks, delta_conv, delta_attr_rev, delta_asst_rev,
             delta_attr_rev + delta_asst_rev, now),
        )
        with self._lock:
            self._cache.pop(f"ar:{content_id}:{niche}", None)

    def get_attr_result(
        self, content_id: str, niche: str = ""
    ) -> dict[str, Any] | None:
        """Return attribution result for content_id (+ optional niche filter)."""
        k = f"ar:{content_id}:{niche}"
        with self._lock:
            cached = self._cget(k)
        if cached is not None:
            return cached

        if niche:
            cur = self._exec(
                "SELECT content_id, niche, page_id, account_id, "
                "clicks, conversions, attributed_rev, assist_rev, total_rev, updated_at "
                "FROM attr_results WHERE content_id = ? AND niche = ?",
                (content_id, niche),
            )
        else:
            # Aggregate across all niches for this content_id
            cur = self._exec(
                "SELECT content_id, '' as niche, '' as page_id, '' as account_id, "
                "SUM(clicks), SUM(conversions), SUM(attributed_rev), "
                "SUM(assist_rev), SUM(total_rev), MAX(updated_at) "
                "FROM attr_results WHERE content_id = ?",
                (content_id,),
            )
        if cur is None:
            return None
        row = cur.fetchone()
        if row is None or row[0] is None:
            return None
        data = {
            "content_id":     row[0],
            "niche":          row[1],
            "page_id":        row[2],
            "account_id":     row[3],
            "clicks":         int(row[4] or 0),
            "conversions":    int(row[5] or 0),
            "attributed_rev": float(row[6] or 0.0),
            "assist_rev":     float(row[7] or 0.0),
            "total_rev":      float(row[8] or 0.0),
            "updated_at":     float(row[9] or 0.0),
        }
        with self._lock:
            self._cset(k, data)
        return data

    def click_count(self, content_id: str) -> int:
        """Total click count for a content_id."""
        ar = self.get_attr_result(content_id)
        return ar["clicks"] if ar else 0

    def conversion_count(self, content_id: str) -> int:
        """Total conversion count for a content_id."""
        ar = self.get_attr_result(content_id)
        return ar["conversions"] if ar else 0

    # ── Maintenance ────────────────────────────────────────────────────────────

    def clear(self) -> None:
        """Wipe all data (testing only)."""
        for table in ("touches", "conversions", "attr_results"):
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

    def stats(self) -> dict[str, Any]:
        with self._lock:
            cs = len(self._cache)
        return {
            "cache_size":   cs,
            "cache_max":    _CACHE_MAX_KEYS,
            "cache_ttl_s":  _CACHE_TTL_S,
            "db_path":      str(self._db_path),
            "db_connected": self._conn is not None,
        }


# ── Singleton ──────────────────────────────────────────────────────────────────

_STORE: AttributionStore | None = None
_STORE_LOCK = threading.Lock()


def get_attribution_store(db_path: Path | None = None) -> AttributionStore:
    global _STORE
    if _STORE is not None:
        return _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = AttributionStore(db_path)
    return _STORE


def reset_attribution_store(db_path: Path | None = None) -> None:
    """Clear all data and reset singleton (testing only)."""
    global _STORE
    with _STORE_LOCK:
        if _STORE is not None:
            _STORE.clear()
            _STORE.close()
        _STORE = AttributionStore(db_path) if db_path else None
