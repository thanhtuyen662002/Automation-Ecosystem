"""
Global Memory — distributed cross-account environmental awareness via Supabase.

Architecture contract (CRITICAL — unchanged from v1):
  - GlobalMemory is ADVISORY ONLY. It never overrides local decisions.
  - Local risk scores, mutation decisions, and account identities are immutable
    from this module's perspective.
  - Two integration points in stealth_brain.evaluate():
      1. Hard filter: is_fingerprint_banned() → force HIGH if True
      2. Soft signal: get_recent_ban_rate() → bounded adjustment (+0.0 to +0.10)

Storage (v2):
  Primary:  Supabase (PostgreSQL) — shared across all fleet processes.
  Fallback: In-process TTL cache — used when Supabase is down or missing.
  The system MUST work identically when SUPABASE_URL / SUPABASE_KEY are not set.

Safety guards (all preserved from v1):
  - Every public method is wrapped in try/except — remote unavailability is silent.
  - get_recent_ban_rate() returns 0.0 if sample count < MIN_SAMPLE_SIZE (noise floor).
  - Global influence on risk is capped at +0.10 (enforced in stealth_brain, not here).
  - All stored rows carry an expires_at timestamp; TTL enforced on every read.
  - In-memory cache (_cache) reduces Supabase round-trips and acts as fallback.

Required Supabase tables (see database/migrations/005_global_memory.sql):
  global_banned_fingerprints (fingerprint_hash, reason, source_count, expires_at)
  global_risk_events          (event_type, account_id, risk_score, created_at, expires_at)
  global_stats                (key, value, updated_at)

Env vars:
  SUPABASE_URL   — Supabase project URL   (e.g. https://xyz.supabase.co)
  SUPABASE_KEY   — Supabase anon/service key
  If either is missing, the module silently falls back to local cache-only mode.

Public API (unchanged):
  GlobalMemory.is_fingerprint_banned(hash)       -> bool
  GlobalMemory.record_ban(hash, reason)          -> None
  GlobalMemory.get_recent_ban_rate(window_s)     -> float
  GlobalMemory.record_event(type, account, score)-> None
  GlobalMemory.is_available()                    -> bool   [NEW]
  GlobalMemory.set_stat(key, value)              -> None
  GlobalMemory.get_stat(key)                     -> dict
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

LOGGER = logging.getLogger("core.global_memory")

# ── Constants (unchanged) ─────────────────────────────────────────────────────

BAN_TTL_SECONDS:          int = 14 * 86400   # 14 days
EVENT_TTL_SECONDS:        int = 14 * 86400   # 14 days
BAN_RATE_WINDOW_SECONDS:  int = 24 * 3600    # 24 hours
MIN_SAMPLE_SIZE:          int = 5

# In-memory cache TTL (seconds). Reduces Supabase calls; also fallback layer.
_CACHE_TTL:               int = 60

# PART 1: negative ban cache TTL reduced 30→10s for faster cross-node propagation.
_NEG_BAN_TTL:             int = 10

# PART 2 — Multi-region routing
# Set REGION env var to route writes to the nearest Supabase project.
# Unrecognised region falls back to the default SUPABASE_URL/KEY env vars.
_REGION: str = os.getenv("REGION", "ap-southeast-1")

# ── Supabase client (optional) ────────────────────────────────────────────────

def _resolve_region_project(region: str) -> tuple[str, str]:
    """Return (SUPABASE_URL, SUPABASE_KEY) for the given region.

    Reads per-region env vars first (e.g. SUPABASE_URL_AP, SUPABASE_KEY_AP),
    then falls back to the default SUPABASE_URL / SUPABASE_KEY.
    Zero external deps — pure env-var routing.
    """
    suffixes: dict[str, str] = {
        "ap-southeast-1": "AP",
        "us-east-1":      "US",
        "eu-west-1":      "EU",
    }
    suffix = suffixes.get(region)
    if suffix:
        url = os.getenv(f"SUPABASE_URL_{suffix}", "").strip()
        key = os.getenv(f"SUPABASE_KEY_{suffix}", "").strip()
        if url and key:
            return url, key
    # Default fallback
    return os.getenv("SUPABASE_URL", "").strip(), os.getenv("SUPABASE_KEY", "").strip()


def _build_supabase_client() -> Any | None:
    """Return a supabase client for the current REGION, else None.

    Failure to connect is silent — the module works in cache-only mode.
    """
    url, key = _resolve_region_project(_REGION)
    if not url or not key:
        LOGGER.debug("global_memory: no SUPABASE_URL/KEY set — running in local-cache mode")
        return None
    try:
        from supabase import create_client  # type: ignore[import]
        client = create_client(url, key)
        LOGGER.info("global_memory: Supabase client initialized url=%s", url[:40])
        return client
    except Exception as exc:
        LOGGER.warning("global_memory: Supabase client init failed error=%s", exc)
        return None


# ── In-memory cache ───────────────────────────────────────────────────────────
# Keyed by (table, identifier). Stores (value, expires_at_monotonic).
# Thread safety: GIL is sufficient for simple dict reads/writes in CPython.

class _Cache:
    """Simple TTL dict cache. Reduces network calls and provides offline fallback."""

    def __init__(self, ttl: int = _CACHE_TTL) -> None:
        self._store: dict[str, tuple[Any, float]] = {}
        self._ttl = ttl

    def get(self, key: str) -> tuple[bool, Any]:
        """Return (hit, value). hit=False on miss or expiry."""
        entry = self._store.get(key)
        if entry is None:
            return False, None
        value, exp = entry
        if time.monotonic() > exp:
            del self._store[key]
            return False, None
        return True, value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self._store[key] = (value, time.monotonic() + (ttl or self._ttl))

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()


# ── GlobalMemory ──────────────────────────────────────────────────────────────

class GlobalMemory:
    """
    Distributed cross-account environmental awareness store.

    Primary storage: Supabase (PostgreSQL), shared across all fleet processes.
    Fallback: in-process TTL cache when Supabase is unavailable.

    All public methods are exception-safe — Supabase downtime is silent.
    """

    def __init__(self) -> None:
        self._sb  = _build_supabase_client()   # None if no credentials
        self._cache = _Cache()

    # ── Health check ──────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Return True if Supabase is reachable. Non-blocking best-effort check."""
        if self._sb is None:
            return False
        try:
            self._sb.table("global_stats").select("key").limit(1).execute()
            return True
        except Exception:
            return False

    # ── Hard filter ───────────────────────────────────────────────────────────

    def is_fingerprint_banned(self, fingerprint_hash: str) -> bool:
        """Return True if this fingerprint is globally banned.

        Check order: cache → Supabase → False (fail-open).
        Cache hit avoids a network round-trip per evaluate() call.
        """
        cache_key = f"ban:{fingerprint_hash}"

        # 1. Cache lookup (also serves as offline fallback)
        hit, cached = self._cache.get(cache_key)
        if hit:
            return bool(cached)

        # 2. Supabase query
        if self._sb is not None:
            try:
                now_iso = _iso(time.time())
                resp = (
                    self._sb.table("global_banned_fingerprints")
                    .select("fingerprint_hash")
                    .eq("fingerprint_hash", fingerprint_hash)
                    .gt("expires_at", now_iso)
                    .limit(1)
                    .execute()
                )
                result = bool(resp.data)
                # Positive: cache for full TTL. Negative: 10s so bans propagate fast.
                self._cache.set(cache_key, result, ttl=_CACHE_TTL if result else _NEG_BAN_TTL)
                return result
            except Exception as exc:
                LOGGER.warning(
                    "global_memory_ban_check_error hash=%s error=%s",
                    fingerprint_hash[:8], exc,
                )

        return False   # fail-open: local logic takes over

    def record_ban(self, fingerprint_hash: str, reason: str = "") -> None:
        """Record a globally banned fingerprint.

        Upserts via Supabase: if hash already exists, increments source_count
        and refreshes TTL. Invalidates local cache entry.
        Exception-safe (no-op if Supabase is down).
        """
        if self._sb is None:
            return

        now = time.time()
        expires_at_iso = _iso(now + BAN_TTL_SECONDS)
        try:
            # Try update first (idempotent increment)
            resp = (
                self._sb.table("global_banned_fingerprints")
                .select("id, source_count")
                .eq("fingerprint_hash", fingerprint_hash)
                .limit(1)
                .execute()
            )
            if resp.data:
                self._sb.table("global_banned_fingerprints").update({
                    "source_count": resp.data[0]["source_count"] + 1,
                    "expires_at":   expires_at_iso,
                }).eq("fingerprint_hash", fingerprint_hash).execute()
            else:
                self._sb.table("global_banned_fingerprints").insert({
                    "fingerprint_hash": fingerprint_hash,
                    "reason":           reason[:500],
                    "source_count":     1,
                    "created_at":       _iso(now),
                    "expires_at":       expires_at_iso,
                    "region":           _REGION,    # PART 2: tag write with source region
                }).execute()

            # Invalidate cache so next call picks up the fresh ban immediately
            self._cache.invalidate(f"ban:{fingerprint_hash}")
            LOGGER.info(
                "global_memory_ban_recorded hash=%s reason=%s",
                fingerprint_hash[:8], reason[:40],
            )
        except Exception as exc:
            LOGGER.warning("global_memory_record_ban_error error=%s", exc)

    # ── Soft signal ───────────────────────────────────────────────────────────

    def get_recent_ban_rate(self, window_seconds: int = BAN_RATE_WINDOW_SECONDS) -> float:
        """Return fraction of 'ban' events in the last `window_seconds`.

        Returns 0.0 if Supabase is down, or total event count < MIN_SAMPLE_SIZE.
        Result range: [0.0, 1.0]. Influence cap (+0.10) enforced by stealth_brain.
        """
        cache_key = f"ban_rate:{window_seconds}"
        hit, cached = self._cache.get(cache_key)
        if hit:
            return float(cached)

        if self._sb is None:
            return 0.0

        try:
            cutoff_iso = _iso(time.time() - window_seconds)

            # Total event count in window
            total_resp = (
                self._sb.table("global_risk_events")
                .select("id", count="exact")
                .gt("created_at", cutoff_iso)
                .execute()
            )
            total = total_resp.count if total_resp.count is not None else len(total_resp.data or [])
            if total < MIN_SAMPLE_SIZE:
                self._cache.set(cache_key, 0.0)
                return 0.0

            # Ban event count in window
            ban_resp = (
                self._sb.table("global_risk_events")
                .select("id", count="exact")
                .eq("event_type", "ban")
                .gt("created_at", cutoff_iso)
                .execute()
            )
            bans = ban_resp.count if ban_resp.count is not None else len(ban_resp.data or [])
            rate = round(bans / total, 4)
            self._cache.set(cache_key, rate)
            return rate
        except Exception as exc:
            LOGGER.warning("global_memory_ban_rate_error error=%s", exc)
            return 0.0

    def record_event(
        self,
        event_type: str,
        account_id: str,
        risk_score: float,
    ) -> None:
        """Record a risk event for fleet-wide signal aggregation.

        event_type: 'ban' | 'captcha' | 'soft_block' | 'high_risk'.
        Exception-safe. No-op if Supabase is down.
        """
        valid_types = {"ban", "captcha", "soft_block", "high_risk"}
        if event_type not in valid_types:
            LOGGER.warning("global_memory_invalid_event_type type=%s", event_type)
            return

        if self._sb is None:
            return

        now = time.time()
        try:
            self._sb.table("global_risk_events").insert({
                "event_type": event_type,
                "account_id": account_id,
                "risk_score": round(float(risk_score), 4),
                "created_at": _iso(now),
                "expires_at": _iso(now + EVENT_TTL_SECONDS),
                "region":     _REGION,    # PART 2: tag write with source region
            }).execute()
            # Invalidate ban_rate cache so next call reflects the new event
            self._cache.invalidate(f"ban_rate:{BAN_RATE_WINDOW_SECONDS}")
            LOGGER.debug(
                "global_memory_event_recorded type=%s account=%s score=%.3f",
                event_type, account_id, risk_score,
            )
        except Exception as exc:
            LOGGER.warning("global_memory_record_event_error error=%s", exc)

    # ── Stats KV ─────────────────────────────────────────────────────────────

    def set_stat(self, key: str, value: dict) -> None:
        """Upsert a JSON stat entry. Exception-safe."""
        if self._sb is None:
            return
        try:
            self._sb.table("global_stats").upsert({
                "key":        key,
                "value":      json.dumps(value),
                "updated_at": _iso(time.time()),
            }).execute()
            self._cache.invalidate(f"stat:{key}")
        except Exception as exc:
            LOGGER.warning("global_memory_set_stat_error key=%s error=%s", key, exc)

    def get_stat(self, key: str) -> dict:
        """Return a JSON stat entry. Returns {} on error or missing key."""
        cache_key = f"stat:{key}"
        hit, cached = self._cache.get(cache_key)
        if hit:
            return dict(cached) if cached else {}

        if self._sb is None:
            return {}

        try:
            resp = (
                self._sb.table("global_stats")
                .select("value")
                .eq("key", key)
                .limit(1)
                .execute()
            )
            if resp.data:
                result = json.loads(resp.data[0]["value"])
                self._cache.set(cache_key, result)
                return result
            return {}
        except Exception as exc:
            LOGGER.warning("global_memory_get_stat_error key=%s error=%s", key, exc)
            return {}


# ── Utilities ─────────────────────────────────────────────────────────────────

def _iso(ts: float) -> str:
    """Convert Unix timestamp to ISO-8601 string for Supabase timestamp columns."""
    import datetime
    return datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _new_id() -> str:
    """Generate a random hex ID."""
    import secrets
    return secrets.token_hex(16)


# ── Singleton ─────────────────────────────────────────────────────────────────

_GLOBAL_MEMORY: GlobalMemory | None = None


def get_global_memory() -> GlobalMemory:
    """Return the process-level GlobalMemory singleton."""
    global _GLOBAL_MEMORY
    if _GLOBAL_MEMORY is None:
        _GLOBAL_MEMORY = GlobalMemory()
    return _GLOBAL_MEMORY


def reset_global_memory() -> None:
    """Reset singleton — for testing only."""
    global _GLOBAL_MEMORY
    _GLOBAL_MEMORY = None
