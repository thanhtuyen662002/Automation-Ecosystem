"""
Account Brain API routes — dashboard control endpoints.
Prefix: /api/v1/account-brain  (registered in api/main.py)
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from core.account_brain import Intent, OperatingMode, SessionSignals, get_brain_registry

LOGGER = logging.getLogger("api.account_brain")
router = APIRouter(prefix="/account-brain", tags=["account-brain"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class ForceIntentRequest(BaseModel):
    intent: Literal["BROWSE", "UPLOAD", "IDLE"]

class ContentReadyRequest(BaseModel):
    ready: bool

class SetModeRequest(BaseModel):
    mode: Literal["SAFE", "NORMAL", "AGGRESSIVE"] | None = None

class RecordActionRequest(BaseModel):
    action_type: str
    session_duration_min: float = Field(default=0.0, ge=0)
    uploaded: bool = False

class UpdateStrategyRequest(BaseModel):
    captcha_hit: bool = False
    action_blocked: bool = False
    soft_ban_detected: bool = False
    low_engagement: bool = False
    upload_failed: bool = False
    engagement_score: float = Field(default=1.0, ge=0.0, le=1.0)
    session_duration_min: float = Field(default=0.0, ge=0)
    uploaded: bool = False
    intent: str = "BROWSE"

class BrainStateResponse(BaseModel):
    account_id: str
    fatigue_level: float
    trust_score: float
    activity_streak_days: int
    content_ready: bool
    intent_override: str | None
    mode_override: str | None
    preferred_hour_start: int
    preferred_hour_end: int
    active_window: str
    last_active_at: float | None
    last_upload_at: float | None
    minutes_since_active: float | None
    minutes_since_upload: float | None
    recent_actions: list[str]
    session_history: list[dict]
    consecutive_anomalies: int
    uploads_suspended: bool
    uploads_suspended_until: float | None
    # Derived
    risk_level: str
    operating_mode: str
    current_intent: str
    intent_reason: str
    session_duration_min: float
    interaction_level: str
    allowed_actions: list[str]
    delay_multiplier: float


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=list[BrainStateResponse])
async def list_brain_states() -> list[dict[str, Any]]:
    return get_brain_registry().snapshot_all()


@router.get("/log")
async def get_decision_log(limit: int = Query(default=50, ge=1, le=200)) -> list[dict[str, Any]]:
    return get_brain_registry().get_decision_log(limit=limit)


@router.post("/emergency-safe-mode")
async def emergency_safe_mode() -> dict[str, Any]:
    """Force ALL accounts to SAFE mode immediately."""
    affected = get_brain_registry().emergency_safe_mode()
    LOGGER.warning("account_brain_api_emergency_safe_mode", extra={"event": "emergency_safe_mode", "count": len(affected)})
    return {"affected_accounts": affected, "count": len(affected), "mode": "SAFE"}


@router.post("/clear-safe-mode")
async def clear_safe_mode() -> dict[str, Any]:
    """Restore automatic mode detection for all accounts."""
    affected = get_brain_registry().clear_safe_mode()
    return {"cleared_accounts": affected, "count": len(affected)}


@router.get("/{account_id}", response_model=BrainStateResponse)
async def get_brain_state(account_id: str) -> dict[str, Any]:
    registry = get_brain_registry()
    registry.get_state(account_id)  # creates default if not found
    snap = registry.snapshot(account_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="Account not found in brain registry")
    return snap


@router.post("/{account_id}/force-intent", response_model=BrainStateResponse)
async def force_intent(account_id: str, body: ForceIntentRequest) -> dict[str, Any]:
    """Override intent for next session only (one-shot)."""
    registry = get_brain_registry()
    registry.force_intent(account_id, body.intent)
    return _snap_or_404(registry, account_id)


@router.post("/{account_id}/reset-fatigue", response_model=BrainStateResponse)
async def reset_fatigue(account_id: str) -> dict[str, Any]:
    registry = get_brain_registry()
    registry.reset_fatigue(account_id)
    return _snap_or_404(registry, account_id)


@router.post("/{account_id}/content-ready", response_model=BrainStateResponse)
async def set_content_ready(account_id: str, body: ContentReadyRequest) -> dict[str, Any]:
    registry = get_brain_registry()
    registry.set_content_ready(account_id, body.ready)
    return _snap_or_404(registry, account_id)


@router.post("/{account_id}/set-mode", response_model=BrainStateResponse)
async def set_mode(account_id: str, body: SetModeRequest) -> dict[str, Any]:
    """Force or clear operating mode. Send null mode to restore auto-detection."""
    registry = get_brain_registry()
    registry.set_operating_mode(account_id, body.mode)
    LOGGER.info("account_brain_api_set_mode", extra={"account_id": account_id, "mode": body.mode})
    return _snap_or_404(registry, account_id)


@router.post("/{account_id}/update-strategy", response_model=BrainStateResponse)
async def update_strategy(account_id: str, body: UpdateStrategyRequest) -> dict[str, Any]:
    """Feed session outcome signals back into the brain's adaptive learning layer.

    Called by publisher workers after each session. Also usable by operators
    to simulate anomalies for testing mode transitions.
    """
    registry = get_brain_registry()
    signals = SessionSignals(
        captcha_hit=body.captcha_hit,
        action_blocked=body.action_blocked,
        soft_ban_detected=body.soft_ban_detected,
        low_engagement=body.low_engagement,
        upload_failed=body.upload_failed,
        engagement_score=body.engagement_score,
        session_duration_min=body.session_duration_min,
        uploaded=body.uploaded,
        intent=body.intent,
    )
    result = registry.update_strategy(account_id, signals)
    LOGGER.info("account_brain_api_update_strategy", extra=result)
    return _snap_or_404(registry, account_id)


@router.post("/{account_id}/record-action", response_model=BrainStateResponse)
async def record_action(account_id: str, body: RecordActionRequest) -> dict[str, Any]:
    registry = get_brain_registry()
    registry.record_action(
        account_id=account_id,
        action_type=body.action_type,
        session_duration_min=body.session_duration_min,
        uploaded=body.uploaded,
    )
    return _snap_or_404(registry, account_id)


def _snap_or_404(registry: Any, account_id: str) -> dict[str, Any]:
    snap = registry.snapshot(account_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="Account not found in brain registry")
    return snap
