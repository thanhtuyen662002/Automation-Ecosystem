"""
api/routes/licenses.py — Admin License Key Management API.

All endpoints require header: X-Admin-Secret: <ADMIN_SECRET from .env>
The secret is validated SERVER-SIDE ONLY — never embedded in frontend bundle.

Routes:
    POST   /api/v1/admin/licenses                 → Create license key
    GET    /api/v1/admin/licenses                 → List all keys + session/event stats
    DELETE /api/v1/admin/licenses/{key}           → Revoke + invalidate all sessions
    POST   /api/v1/admin/licenses/{key}/reset     → Reset machine binding + revoke sessions
    POST   /api/v1/admin/licenses/{key}/activate  → Re-activate revoked key
    POST   /api/v1/admin/licenses/{key}/unflag    → Clear IP anomaly flag
    GET    /api/v1/admin/licenses/{key}/events    → Audit log for a license
"""
from __future__ import annotations

import logging
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from api.security import audit_event, get_client_ip, revoke_sessions_for_license

LOGGER = logging.getLogger("api.licenses")
router = APIRouter(prefix="/admin/licenses", tags=["Admin — Licenses"])

# ── Admin authentication ───────────────────────────────────────────────────────
# ADMIN_SECRET is read ONCE at startup from the environment.
# It is NEVER sent to the frontend; the LicenseManager UI sends it in the
# X-Admin-Secret header which is validated here server-side.
_ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")


def _require_admin(x_admin_secret: str = Header(default="")) -> None:
    """
    Dependency: constant-time comparison of X-Admin-Secret header.
    Returns 503 if ADMIN_SECRET is not configured (misconfigured server).
    Returns 403 on wrong secret.
    """
    if not _ADMIN_SECRET:
        raise HTTPException(503, "ADMIN_SECRET not configured on this server.")
    if not secrets.compare_digest(x_admin_secret.encode(), _ADMIN_SECRET.encode()):
        raise HTTPException(403, "Admin secret không hợp lệ.")


# ── Schemas ────────────────────────────────────────────────────────────────────

class CreateLicenseRequest(BaseModel):
    label: str | None = None
    expires_days: int | None = None
    notes: str | None = None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _generate_key() -> str:
    """Generate AE-XXXX-XXXX-XXXX using cryptographically secure random bytes."""
    parts = [secrets.token_hex(2).upper() for _ in range(3)]
    return f"AE-{parts[0]}-{parts[1]}-{parts[2]}"


def _row_to_dict(row: Any) -> dict:
    return {
        "id":             row["id"],
        "license_key":    row["license_key"],
        "label":          row["label"],
        "machine_id":     row["machine_id"],
        "activated_at":   row["activated_at"],
        "expires_at":     row["expires_at"],
        "is_active":      bool(row["is_active"]),
        "flagged":        bool(row["flagged"]),
        "flagged_reason": row["flagged_reason"],
        "last_ip":        row["last_ip"],
        "last_seen_at":   row["last_seen_at"],
        "notes":          row["notes"],
        "created_at":     row["created_at"],
    }


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post("", dependencies=[Depends(_require_admin)])
async def create_license(req: CreateLicenseRequest, request: Request) -> dict:
    """Create a new license key and record an audit event."""
    db  = request.app.state.database
    key = _generate_key()
    lid = str(uuid.uuid4())
    expires_at = None
    if req.expires_days is not None:
        expires_at = (datetime.now(UTC) + timedelta(days=req.expires_days)).isoformat()

    async with db.connection() as conn:
        await conn.execute(
            "INSERT INTO licenses (id, license_key, label, expires_at, notes) VALUES (?,?,?,?,?)",
            (lid, key, req.label, expires_at, req.notes),
        )
        await conn.commit()
        cur = await conn.execute("SELECT * FROM licenses WHERE id = ?", (lid,))
        row = await cur.fetchone()

    await audit_event(db, "admin_create", license_key=key,
                      ip=get_client_ip(request),
                      detail={"label": req.label, "expires_days": req.expires_days})
    LOGGER.info("license_created", extra={"event": "license_created", "key": key})
    return _row_to_dict(row)


@router.get("", dependencies=[Depends(_require_admin)])
async def list_licenses(request: Request) -> dict:
    """Return all licenses with active session counts."""
    db = request.app.state.database
    async with db.connection() as conn:
        cur = await conn.execute(
            "SELECT * FROM licenses ORDER BY created_at DESC"
        )
        rows = await cur.fetchall()
        # Count active sessions per key
        s_cur = await conn.execute(
            """SELECT license_key, COUNT(*) AS cnt
               FROM sessions WHERE revoked = 0 AND expires_at > CURRENT_TIMESTAMP
               GROUP BY license_key"""
        )
        session_counts = {r["license_key"]: r["cnt"] for r in await s_cur.fetchall()}

    items = []
    for r in rows:
        d = _row_to_dict(r)
        d["active_sessions"] = session_counts.get(r["license_key"], 0)
        items.append(d)
    return {"items": items, "total": len(items)}


@router.delete("/{key}", dependencies=[Depends(_require_admin)])
async def revoke_license(key: str, request: Request) -> dict:
    """
    Revoke license key AND immediately invalidate all active sessions.
    The user will be kicked out within their next API call (max 60s delay due to token TTL).
    """
    db = request.app.state.database
    async with db.connection() as conn:
        cur = await conn.execute(
            "UPDATE licenses SET is_active = 0 WHERE license_key = ?", (key,)
        )
        await conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, f"License not found: {key}")

    # Immediately invalidate all sessions for this key
    await revoke_sessions_for_license(db, key, reason="admin_revoked")
    await audit_event(db, "admin_revoke", license_key=key, ip=get_client_ip(request))
    LOGGER.info("license_revoked", extra={"event": "license_revoked", "key": key})
    return {"revoked": True, "license_key": key, "sessions_invalidated": True}


@router.post("/{key}/reset", dependencies=[Depends(_require_admin)])
async def reset_machine_binding(key: str, request: Request) -> dict:
    """
    Clear machine binding so the key can be activated on a new machine.
    Also revokes all active sessions (forces re-login from new machine).
    """
    db = request.app.state.database
    async with db.connection() as conn:
        cur = await conn.execute(
            "UPDATE licenses SET machine_id = NULL, activated_at = NULL WHERE license_key = ?",
            (key,),
        )
        await conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, f"License not found: {key}")

    await revoke_sessions_for_license(db, key, reason="machine_reset")
    await audit_event(db, "admin_reset_machine", license_key=key, ip=get_client_ip(request))
    LOGGER.info("machine_reset", extra={"event": "machine_reset", "key": key})
    return {"reset": True, "license_key": key, "sessions_invalidated": True}


@router.post("/{key}/activate", dependencies=[Depends(_require_admin)])
async def reactivate_license(key: str, request: Request) -> dict:
    """Re-activate a previously revoked license."""
    db = request.app.state.database
    async with db.connection() as conn:
        cur = await conn.execute(
            "UPDATE licenses SET is_active = 1 WHERE license_key = ?", (key,)
        )
        await conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, f"License not found: {key}")
    await audit_event(db, "admin_reactivate", license_key=key, ip=get_client_ip(request))
    return {"activated": True, "license_key": key}


@router.post("/{key}/unflag", dependencies=[Depends(_require_admin)])
async def unflag_license(key: str, request: Request) -> dict:
    """Clear the IP anomaly flag on a license."""
    db = request.app.state.database
    async with db.connection() as conn:
        cur = await conn.execute(
            "UPDATE licenses SET flagged = 0, flagged_reason = NULL WHERE license_key = ?",
            (key,),
        )
        await conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, f"License not found: {key}")
    await audit_event(db, "admin_unflag", license_key=key, ip=get_client_ip(request))
    return {"unflagged": True, "license_key": key}


@router.get("/{key}/events", dependencies=[Depends(_require_admin)])
async def get_license_events(key: str, request: Request, limit: int = 50) -> dict:
    """Return recent audit events for a specific license key."""
    db = request.app.state.database
    async with db.connection() as conn:
        cur = await conn.execute(
            """SELECT id, event_type, ip, machine_fp, detail, created_at
               FROM license_events
               WHERE license_key = ?
               ORDER BY created_at DESC LIMIT ?""",
            (key, limit),
        )
        rows = await cur.fetchall()
    return {
        "license_key": key,
        "events": [dict(r) for r in rows],
        "total": len(rows),
    }
