"""
core/profit_store.py — Shared Persistent Profit State (SQLite-backed)

Replaces the in-memory dict in profit_engine with a durable, cross-process store.

Architecture:
    ┌─────────────────────────────────────────────────────┐
    │              profit_engine (callers)                │
    │  update_profit()   get_profit_score()               │
    └───────────────────┬─────────────────────────────────┘
                        │ read-through / write-through
    ┌───────────────────▼─────────────────────────────────┐
    │           ProfitStore (this module)                 │
    │                                                     │
    │  LRU cache (max 4096 keys, TTL = 15s)              │
    │      ↓ miss / write                                 │
    │  SQLite (data/profit_state.db)                      │
    │      · WAL mode → concurrent readers OK            │
    │      · Thread-safe via sqlite3 check_same_thread=F │
    │      · Atomic upsert via INSERT OR REPLACE          │
    └─────────────────────────────────────────────────────┘

Design contracts:
  - Same content_id + niche → same SHA-256[:16] key (deterministic)
  - All reads/writes are exception-safe; failure → neutral fallback (0.5)
  - TTL cache prevents thundering-herd on hot keys
  - WAL journal lets multiple workers read concurrently without blocking
  - DB path controllable via PROFIT_STATE_DB env var

Public API (used only by profit_engine.py):
    ProfitStore.get(key)                     -> dict | None
    ProfitStore.set(key, data)               -> None
    ProfitStore.delete(key)                  -> None
    ProfitStore.clear()                      -> None  (testing only)
    get_profit_store()                       -> ProfitStore
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

LOGGER = logging.getLogger("core.profit_store")

# ── Configuration ──────────────────────────────────────────────────────────────

_DEFAULT_DB = Path("data") / "profit_state.db"

def _db_path() -> Path:
    env = os.environ.get("PROFIT_STATE_DB")
    return Path(env) if env else _DEFAULT_DB

# LRU cache parameters
_CACHE_MAX_KEYS: int   = 4096
_CACHE_TTL_S:   float = 15.0    # seconds before a cached entry is considered stale


# ── Cache entry ────────────────────────────────────────────────────────────────

class _CacheEntry:
    __slots__ = ("data", "ts")

    def __init__(self, data: dict[str, Any]) -> None:
        self.data: dict[str, Any] = data
        self.ts:   float          = time.monotonic()

    def is_stale(self) -> bool:
        return (time.monotonic() - self.ts) > _CACHE_TTL_S


# ── SQLite schema ──────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS profit_records (
    key          TEXT PRIMARY KEY,
    content_id   TEXT NOT NULL,
    niche        TEXT NOT NULL,
    data_json    TEXT NOT NULL,      -- full ProfitRecord fields as JSON
    updated_at   REAL NOT NULL       -- unix timestamp of last update
);
"""


# ── ProfitStore ────────────────────────────────────────────────────────────────

class ProfitStore:
    """
    Thread-safe, file-backed profit state store.

    · Writes go directly to SQLite (WAL mode) and invalidate the cache.
    · Reads check cache first; on miss fetch from SQLite and populate cache.
    · LRU eviction: when cache exceeds _CACHE_MAX_KEYS, evict the 20%
      least-recently-accessed entries (order preserved by dict insertion order).

    Thread safety:
        _lock protects the in-memory cache only.
        SQLite connection uses check_same_thread=False and serialize_row is
        handled per-call (no shared cursor).
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or _db_path()
        self._lock    = threading.Lock()
        self._cache:  dict[str, _CacheEntry] = {}   # key → CacheEntry
        self._conn:   sqlite3.Connection | None = None
        self._init_db()

    # ── DB init ───────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(self._db_path),
                check_same_thread=False,
                isolation_level=None,   # autocommit — we manage transactions
            )
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")   # safe + fast
            conn.execute("PRAGMA cache_size=-4000;")     # 4 MB page cache
            conn.execute(_DDL)
            conn.commit()
            self._conn = conn
            LOGGER.debug("profit_store DB initialised path=%s", self._db_path)
        except Exception as exc:
            LOGGER.error(
                "profit_store DB init failed path=%s error=%s — falling back to memory-only",
                self._db_path, exc,
            )
            self._conn = None

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor | None:
        """Execute a SQL statement; reconnect once on failure."""
        if self._conn is None:
            return None
        try:
            return self._conn.execute(sql, params)
        except sqlite3.OperationalError as exc:
            LOGGER.warning("profit_store db_error sql=%r error=%s — retrying", sql[:60], exc)
            try:
                self._init_db()
                return self._conn.execute(sql, params) if self._conn else None
            except Exception:
                return None

    # ── Cache helpers ─────────────────────────────────────────────────────────

    def _cache_get(self, key: str) -> dict[str, Any] | None:
        """Return cached data if present and fresh, else None."""
        entry = self._cache.get(key)
        if entry is None:
            return None
        if entry.is_stale():
            del self._cache[key]
            return None
        # LRU: move to end by re-inserting (Python 3.7+ dicts are ordered)
        self._cache[key] = self._cache.pop(key)
        return entry.data

    def _cache_set(self, key: str, data: dict[str, Any]) -> None:
        """Insert/update cache entry; evict 20% oldest if over capacity."""
        if key in self._cache:
            del self._cache[key]
        self._cache[key] = _CacheEntry(data)
        if len(self._cache) > _CACHE_MAX_KEYS:
            evict_n = max(1, _CACHE_MAX_KEYS // 5)
            for k in list(self._cache.keys())[:evict_n]:
                del self._cache[k]

    def _cache_del(self, key: str) -> None:
        self._cache.pop(key, None)

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, key: str) -> dict[str, Any] | None:
        """
        Read profit record for key.

        Returns full data dict or None if not found.
        Cache → SQLite read-through.
        """
        with self._lock:
            cached = self._cache_get(key)
            if cached is not None:
                return cached

        # Cache miss → SQLite read (outside lock for concurrency)
        try:
            cur = self._execute(
                "SELECT data_json FROM profit_records WHERE key = ?", (key,)
            )
            if cur is None:
                return None
            row = cur.fetchone()
            if row is None:
                return None
            data: dict[str, Any] = json.loads(row[0])
            with self._lock:
                self._cache_set(key, data)
            return data
        except Exception as exc:
            LOGGER.warning("profit_store get_failed key=%s error=%s", key, exc)
            return None

    def set(self, key: str, data: dict[str, Any],
            content_id: str = "", niche: str = "") -> None:
        """
        Write profit record for key.

        Upserts to SQLite then updates cache.
        """
        json_str = json.dumps(data)
        now      = time.time()
        try:
            self._execute(
                """
                INSERT OR REPLACE INTO profit_records
                    (key, content_id, niche, data_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (key, content_id, niche, json_str, now),
            )
        except Exception as exc:
            LOGGER.warning("profit_store set_failed key=%s error=%s", key, exc)
            # Still update cache so in-process reads work
        with self._lock:
            self._cache_set(key, data)

    def delete(self, key: str) -> None:
        """Remove a single key from store and cache."""
        try:
            self._execute("DELETE FROM profit_records WHERE key = ?", (key,))
        except Exception as exc:
            LOGGER.warning("profit_store delete_failed key=%s error=%s", key, exc)
        with self._lock:
            self._cache_del(key)

    def clear(self) -> None:
        """Wipe all data. For testing only."""
        try:
            self._execute("DELETE FROM profit_records")
        except Exception as exc:
            LOGGER.warning("profit_store clear_failed error=%s", exc)
        with self._lock:
            self._cache.clear()

    def close(self) -> None:
        """Close the SQLite connection cleanly."""
        with self._lock:
            self._cache.clear()
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def cache_stats(self) -> dict[str, Any]:
        """Return current cache statistics for monitoring."""
        with self._lock:
            return {
                "cache_size":    len(self._cache),
                "cache_max":     _CACHE_MAX_KEYS,
                "cache_ttl_s":   _CACHE_TTL_S,
                "db_path":       str(self._db_path),
                "db_connected":  self._conn is not None,
            }


# ── Singleton ──────────────────────────────────────────────────────────────────

_STORE: ProfitStore | None  = None
_STORE_LOCK = threading.Lock()


def get_profit_store(db_path: Path | None = None) -> ProfitStore:
    """
    Return the process-wide ProfitStore singleton.

    Thread-safe: double-checked locking with module-level lock.
    db_path is only respected on first call (ignored afterwards).
    """
    global _STORE
    if _STORE is not None:
        return _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = ProfitStore(db_path)
    return _STORE


def reset_profit_store(db_path: Path | None = None) -> None:
    """
    Reset singleton and wipe all data.

    For testing only — clears both SQLite and cache.
    If db_path is provided, forces a new store at that path.
    """
    global _STORE
    with _STORE_LOCK:
        if _STORE is not None:
            _STORE.clear()
            _STORE.close()
        _STORE = ProfitStore(db_path) if db_path else None
