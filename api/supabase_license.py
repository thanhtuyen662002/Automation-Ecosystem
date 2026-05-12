"""
api/supabase_license.py
────────────────────────────────────────────────────────────────────────────
License validation via Supabase (cloud-managed key store).

Active when SUPABASE_URL + SUPABASE_SERVICE_KEY are set in environment.
Falls back silently to local SQLite when these vars are missing.

Required Supabase table (create in dashboard → SQL Editor):

    CREATE TABLE licenses (
      id               uuid        DEFAULT gen_random_uuid() PRIMARY KEY,
      license_key      text        UNIQUE NOT NULL,
      label            text,
      role             text        DEFAULT 'operator',
      max_accounts     integer     DEFAULT 10,
      is_active        boolean     DEFAULT true,
      expires_at       timestamptz,
      machine_id       text,
      last_ip          text,
      last_seen_at     timestamptz,
      activated_at     timestamptz,
      flagged          boolean     DEFAULT false,
      flagged_reason   text,
      created_at       timestamptz DEFAULT now()
    );
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

LOGGER = logging.getLogger("api.supabase_license")

# ── Lazy singleton ─────────────────────────────────────────────────────────────
_client: Any = None


def _get_supabase() -> Any:
    """Return (and lazily create) the synchronous Supabase client."""
    global _client
    if _client is not None:
        return _client

    try:
        from supabase import create_client  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "supabase package not installed. Run: pip install 'supabase>=2.3.0'"
        ) from exc

    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")

    _client = create_client(url, key)
    return _client


def is_supabase_configured() -> bool:
    """Return True if both Supabase env vars are present and non-empty."""
    return bool(
        os.environ.get("SUPABASE_URL", "").strip()
        and os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    )


# ── Public async interface ─────────────────────────────────────────────────────

async def validate_and_bind(key: str, machine_fp: str, ip: str) -> dict[str, Any]:
    """
    Validate a license key against Supabase and bind the machine fingerprint
    on first activation. Subsequent logins verify the fingerprint matches.

    Returns the full license row dict on success.
    Raises HTTPException on any validation failure.
    """
    loop = asyncio.get_event_loop()

    # All Supabase calls are synchronous; run in thread pool to stay async
    row = await loop.run_in_executor(None, _fetch_license, key)

    if row is None:
        raise HTTPException(401, "License key không hợp lệ.")

    if not row.get("is_active", False):
        raise HTTPException(403, "License key đã bị thu hồi.")

    expires_at = row.get("expires_at")
    if expires_at:
        try:
            # Supabase returns ISO-8601 with trailing 'Z' or '+00:00'
            expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if datetime.now(UTC) > expires:
                raise HTTPException(403, "License key đã hết hạn.")
        except HTTPException:
            raise
        except Exception:
            pass  # malformed date — skip expiry check

    if row.get("flagged"):
        reason = row.get("flagged_reason") or "hoạt động bất thường"
        raise HTTPException(403, f"License bị khóa: {reason}.")

    bound_fp = row.get("machine_id")
    if not bound_fp:
        # First activation — bind this machine's fingerprint
        await loop.run_in_executor(None, _bind_machine, key, machine_fp, ip)
        LOGGER.info(
            "license_activated_supabase",
            extra={"event": "license_activated_supabase", "key": key[:8], "fp": machine_fp[:8]},
        )
    else:
        if bound_fp != machine_fp:
            LOGGER.warning(
                "license_machine_mismatch_supabase",
                extra={"event": "license_machine_mismatch_supabase", "key": key[:8]},
            )
            raise HTTPException(
                401,
                "License key này đã được kích hoạt trên một thiết bị khác. "
                "Liên hệ quản trị viên để reset machine_id nếu cần.",
            )
        # Update last_seen metadata
        await loop.run_in_executor(None, _update_last_seen, key, ip)

    return row


# ── Synchronous DB helpers (executed in thread pool) ──────────────────────────

def _fetch_license(key: str) -> dict[str, Any] | None:
    sb = _get_supabase()
    result = sb.table("licenses").select("*").eq("license_key", key).execute()
    if result.data:
        return result.data[0]
    return None


def _bind_machine(key: str, machine_fp: str, ip: str) -> None:
    sb = _get_supabase()
    now_iso = datetime.now(UTC).isoformat()
    sb.table("licenses").update({
        "machine_id":    machine_fp,
        "activated_at":  now_iso,
        "last_ip":       ip,
        "last_seen_at":  now_iso,
    }).eq("license_key", key).execute()


def _update_last_seen(key: str, ip: str) -> None:
    sb = _get_supabase()
    sb.table("licenses").update({
        "last_ip":      ip,
        "last_seen_at": datetime.now(UTC).isoformat(),
    }).eq("license_key", key).execute()
