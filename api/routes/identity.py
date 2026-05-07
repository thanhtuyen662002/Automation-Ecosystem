"""
Identity Manager API routes.
Prefix: /api/v1/identity  (registered in api/main.py)
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.identity_manager import get_identity_registry

LOGGER = logging.getLogger("api.identity")
router = APIRouter(prefix="/identity", tags=["identity"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class UpdateProxyRequest(BaseModel):
    proxy_url: str
    proxy_country: str

class ValidateRequest(BaseModel):
    ip_changed: bool = False
    current_fingerprint: str | None = None
    geo_mismatch: bool = False
    ip_rotation_count: int = 0


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", summary="List all identity profiles")
async def list_identities() -> list[dict[str, Any]]:
    return get_identity_registry().snapshot_all()


@router.get("/{account_id}", summary="Get identity profile for one account")
async def get_identity(account_id: str) -> dict[str, Any]:
    reg = get_identity_registry()
    reg.get_or_create(account_id)   # lazily initialize
    snap = reg.snapshot(account_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="Identity not found")
    return snap


@router.post("/{account_id}/generate", summary="Generate identity (idempotent — safe to call multiple times)")
async def generate_identity(
    account_id: str,
    proxy_url: str | None = None,
    proxy_country: str | None = None,
) -> dict[str, Any]:
    """Create or return existing identity. Does NOT overwrite existing profile."""
    reg = get_identity_registry()
    reg.get_or_create(account_id, proxy_url=proxy_url, proxy_country=proxy_country)
    return reg.snapshot(account_id)


@router.post("/{account_id}/regenerate", summary="⚠️ Force-regenerate identity (changes fingerprint — dangerous)")
async def regenerate_identity(
    account_id: str,
    proxy_url: str | None = None,
    proxy_country: str | None = None,
) -> dict[str, Any]:
    """Regenerate identity with a new device/fingerprint.

    DANGEROUS: This resets the fingerprint hash. The account's trust_score
    should be degraded in AccountBrain after this call.
    Blocked if identity is locked.
    """
    reg = get_identity_registry()
    try:
        reg.regenerate(account_id, proxy_url=proxy_url, proxy_country=proxy_country)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    LOGGER.warning("identity_api_regenerated", extra={"event": "identity_api_regenerated", "account_id": account_id})
    return reg.snapshot(account_id)


@router.post("/{account_id}/lock", summary="Lock identity to prevent accidental regeneration")
async def lock_identity(account_id: str) -> dict[str, Any]:
    reg = get_identity_registry()
    reg.lock(account_id)
    return reg.snapshot(account_id) or {}


@router.post("/{account_id}/unlock", summary="Unlock identity to allow regeneration")
async def unlock_identity(account_id: str) -> dict[str, Any]:
    reg = get_identity_registry()
    reg.unlock(account_id)
    return reg.snapshot(account_id) or {}


@router.post("/{account_id}/proxy", summary="Update proxy without changing device/fingerprint")
async def update_proxy(account_id: str, body: UpdateProxyRequest) -> dict[str, Any]:
    reg = get_identity_registry()
    reg.update_proxy(account_id, body.proxy_url, body.proxy_country)
    return reg.snapshot(account_id) or {}


@router.post("/{account_id}/validate", summary="Run consistency checks against runtime environment")
async def validate_identity(account_id: str, body: ValidateRequest) -> dict[str, Any]:
    """Validate identity consistency and return issues list.

    CRITICAL-severity issues indicate the account should enter SAFE MODE.
    Feed body.current_fingerprint from the browser runtime for drift detection.
    """
    reg = get_identity_registry()
    reg.get_or_create(account_id)
    runtime_env = {
        "ip_changed": body.ip_changed,
        "current_fingerprint": body.current_fingerprint,
        "geo_mismatch": body.geo_mismatch,
        "ip_rotation_count": body.ip_rotation_count,
    }
    issues = reg.validate(account_id, runtime_env=runtime_env)
    snap = reg.snapshot(account_id) or {}
    snap["validation_issues"] = [
        {"code": i.code, "severity": i.severity, "message": i.message, "field": i.field}
        for i in issues
    ]
    snap["force_safe_mode"] = any(i.severity == "CRITICAL" for i in issues)
    return snap
