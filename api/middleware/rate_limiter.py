"""
api/middleware/rate_limiter.py — DB-backed IP rate limiter.

Limits login attempts to MAX_ATTEMPTS per WINDOW_SECONDS per IP.
Uses the `login_attempts` table (migration 009) for persistence across restarts.

Falls back to in-memory if DB is unavailable (startup race / cold boot).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

LOGGER = logging.getLogger("api.rate_limiter")

_MAX_ATTEMPTS  = int(os.getenv("LOGIN_RATE_LIMIT_MAX",    "10"))
_WINDOW_S      = int(os.getenv("LOGIN_RATE_LIMIT_WINDOW", "60"))  # seconds

# ── In-memory fallback (used when DB not available) ───────────────────────────
_mem_attempts: dict[str, deque[float]] = defaultdict(deque)
_mem_lock = asyncio.Lock()
_last_cleanup = time.monotonic()
_CLEANUP_EVERY = 300   # purge old entries every 5 minutes


async def _mem_cleanup() -> None:
    global _last_cleanup
    now = time.monotonic()
    if now - _last_cleanup < _CLEANUP_EVERY:
        return
    cutoff = time.time() - _WINDOW_S
    dead = [ip for ip, dq in _mem_attempts.items() if not dq or dq[-1] < cutoff]
    for ip in dead:
        del _mem_attempts[ip]
    _last_cleanup = now


async def _check_mem_rate_limit(ip: str) -> None:
    """Pure in-memory rate limiting (fallback)."""
    now = time.time()
    cutoff = now - _WINDOW_S
    async with _mem_lock:
        dq = _mem_attempts[ip]
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= _MAX_ATTEMPTS:
            LOGGER.warning(
                "rate_limit_exceeded",
                extra={"event": "rate_limit_exceeded", "ip": ip, "count": len(dq), "backend": "memory"},
            )
            raise HTTPException(
                status_code=429,
                detail="Too many login attempts. Please wait before trying again.",
                headers={"Retry-After": str(_WINDOW_S)},
            )
        dq.append(now)
        await _mem_cleanup()


# ── DB-backed rate limiting ───────────────────────────────────────────────────

async def _check_db_rate_limit(db, ip: str) -> None:
    """
    DB-backed rate limiting using login_attempts table.
    Persists across server restarts.
    Raises HTTP 429 if limit exceeded.
    """
    window_start = time.strftime(
        "%Y-%m-%d %H:%M:%S",
        time.gmtime(time.time() - _WINDOW_S),
    )

    async with db.connection() as conn:
        # Count recent attempts from this IP
        cur = await conn.execute(
            """SELECT COUNT(*) AS cnt
               FROM login_attempts
               WHERE ip = ? AND attempted_at > ?""",
            (ip, window_start),
        )
        row = await cur.fetchone()
        count = row["cnt"] if row else 0

        if count >= _MAX_ATTEMPTS:
            LOGGER.warning(
                "rate_limit_exceeded",
                extra={"event": "rate_limit_exceeded", "ip": ip, "count": count, "backend": "db"},
            )
            raise HTTPException(
                status_code=429,
                detail="Too many login attempts. Please wait before trying again.",
                headers={"Retry-After": str(_WINDOW_S)},
            )

        # Record this attempt
        await conn.execute(
            "INSERT INTO login_attempts (ip) VALUES (?)",
            (ip,),
        )

        # Periodic cleanup: purge old records > 2× window
        # Use a lightweight probabilistic purge (1 in 20 requests)
        import random
        if random.randint(1, 20) == 1:
            old_cutoff = time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.gmtime(time.time() - _WINDOW_S * 2),
            )
            await conn.execute(
                "DELETE FROM login_attempts WHERE attempted_at < ?",
                (old_cutoff,),
            )

        await conn.commit()


async def record_login_attempt(db, ip: str, license_key: str | None = None) -> None:
    """
    Record a login attempt in the DB (called after rate limit check passes).
    Attach license_key if available (for per-license anomaly queries).
    """
    try:
        async with db.connection() as conn:
            await conn.execute(
                "INSERT INTO login_attempts (ip, license_key) VALUES (?, ?)",
                (ip, license_key),
            )
            await conn.commit()
    except Exception as exc:
        LOGGER.debug("login_attempt_record_failed", extra={"err": str(exc)})


async def check_login_rate_limit(request: Request) -> None:
    """
    Call this at the START of the login endpoint.
    Uses DB-backed rate limiting if DB is available, falls back to in-memory.
    Raises HTTP 429 if the IP has exceeded the allowed attempts.
    """
    from api.security import get_client_ip
    ip = get_client_ip(request)

    db = getattr(getattr(request, "app", None), "state", None)
    db = getattr(db, "database", None) if db else None

    if db is not None:
        try:
            await _check_db_rate_limit(db, ip)
            return
        except HTTPException:
            raise
        except Exception as exc:
            LOGGER.warning(
                "db_rate_limit_fallback",
                extra={"event": "db_rate_limit_fallback", "err": str(exc)},
            )
            # Fall through to in-memory

    await _check_mem_rate_limit(ip)
