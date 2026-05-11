"""
api/routes/strategy.py — CEO Brain / Strategy Layer API.

Mounted at /api/v1/strategy via api/main.py.

Routes:
    GET  /strategy/state               → current StrategyState
    POST /strategy/state               → update StrategyState fields
    GET  /strategy/recommendations     → list of strategic recommendations
    GET  /strategy/niche-performance   → per-niche budget/win-rate table
    POST /strategy/niche-performance   → upsert niche performance data
    GET  /strategy/accounts            → account scores + allocation shares
    POST /strategy/accounts/score      → score / update an account
    POST /strategy/accounts/allocate   → compute post allocation
    GET  /strategy/overrides           → active system overrides
    POST /strategy/overrides           → add an override (freeze/boost/kill)
    DELETE /strategy/overrides/{id}    → remove an override
    POST /strategy/metrics             → closed-loop metrics update (Part 4)
    GET  /strategy/log                 → strategy event log
"""
from __future__ import annotations

from typing import Any
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

LOGGER = logging.getLogger("api.strategy")
router = APIRouter(prefix="/strategy", tags=["Strategy / CEO Brain"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class StateUpdate(BaseModel):
    target_daily_views:   float | None = None
    target_daily_revenue: float | None = None
    max_risk_level:       float | None = Field(None, ge=0, le=1)
    growth_mode:          str   | None = Field(None, pattern="^(conservative|balanced|aggressive)$")
    threshold_modifier:   float | None = Field(None, ge=0.5, le=2.0)
    exploration_rate:     float | None = Field(None, ge=0.0, le=0.5)


class NichePerformanceIn(BaseModel):
    niche:            str
    platform:         str   = "tiktok"
    win_rate:         float = Field(0.0, ge=0, le=1)
    avg_views:        float = 0.0
    avg_revenue:      float = 0.0
    posts_count:      int   = 0
    growth_potential: float = Field(0.5, ge=0, le=1)


class AccountScoreIn(BaseModel):
    account_id:      str
    platform:        str   = "tiktok"
    engagement_rate: float = Field(0.0, ge=0, le=1)
    conversion_rate: float = Field(0.0, ge=0, le=1)
    consistency:     float = Field(0.5, ge=0, le=1)
    risk_penalty:    float = Field(0.0, ge=0, le=1)


class AllocateIn(BaseModel):
    account_ids:  list[str]
    platform:     str = "tiktok"
    total_posts:  int = Field(10, ge=1, le=200)


class OverrideIn(BaseModel):
    target_id:   str
    target_type: str  = Field(..., pattern="^(account|niche|content)$")
    override:    str  = Field(..., pattern="^(freeze|boost|kill|force_publish|restrict)$")
    reason:      str  = ""
    ttl_hours:   float = 24.0


class MetricsUpdate(BaseModel):
    actual_daily_views:   float = 0.0
    actual_daily_revenue: float = 0.0
    niche_data:    list[NichePerformanceIn] | None = None
    account_data:  list[AccountScoreIn]     | None = None


# ── Helper ────────────────────────────────────────────────────────────────────

def _ceo():
    try:
        from strategy.ceo_brain import _get_ceo
        return _get_ceo()
    except Exception as exc:
        raise HTTPException(503, f"CEO brain unavailable: {exc}") from exc


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/state")
async def get_state() -> dict[str, Any]:
    from dataclasses import asdict
    return asdict(_ceo().get_state())


@router.post("/state")
async def update_state(body: StateUpdate) -> dict[str, Any]:
    from dataclasses import asdict
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(400, "No fields provided")
    state = _ceo().update_state(**changes)
    return asdict(state)


@router.get("/recommendations")
async def get_recommendations() -> list[dict[str, Any]]:
    return _ceo().get_recommendations()


@router.get("/niche-performance")
async def get_niche_performance() -> list[dict[str, Any]]:
    return _ceo().get_niche_performance()


@router.post("/niche-performance")
async def upsert_niche(body: NichePerformanceIn) -> dict[str, Any]:
    _ceo().update_niche_performance(
        niche=body.niche, platform=body.platform,
        win_rate=body.win_rate, avg_views=body.avg_views,
        avg_revenue=body.avg_revenue, posts_count=body.posts_count,
        growth_potential=body.growth_potential,
    )
    return {"status": "ok", "niche": body.niche}


@router.get("/accounts")
async def get_accounts() -> list[dict[str, Any]]:
    return _ceo().get_account_scores()


@router.post("/accounts/score")
async def score_account(body: AccountScoreIn) -> dict[str, Any]:
    score = _ceo().score_account(
        account_id=body.account_id, platform=body.platform,
        engagement_rate=body.engagement_rate, conversion_rate=body.conversion_rate,
        consistency=body.consistency, risk_penalty=body.risk_penalty,
    )
    return {"account_id": body.account_id, "account_score": score}


@router.post("/accounts/allocate")
async def allocate_posts(body: AllocateIn) -> dict[str, Any]:
    allocation = _ceo().allocate_posts(body.account_ids, body.platform, body.total_posts)
    return {"allocation": allocation, "total_posts": body.total_posts}


@router.get("/overrides")
async def get_overrides() -> dict[str, Any]:
    # Return active overrides from DB
    import sqlite3, os
    from pathlib import Path
    import time
    db_path = Path(os.environ.get("CEO_BRAIN_DB", "data/ceo_brain.db"))
    if not db_path.exists():
        return {"overrides": []}
    con = sqlite3.connect(str(db_path)); con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT * FROM strategy_overrides WHERE active=1 AND expires_at > ? ORDER BY created_at DESC",
            (time.time(),),
        ).fetchall()
        return {"overrides": [dict(r) for r in rows]}
    finally:
        con.close()


@router.post("/overrides")
async def add_override(body: OverrideIn) -> dict[str, Any]:
    _ceo().add_override(
        target_id=body.target_id, target_type=body.target_type,
        override=body.override, reason=body.reason, ttl_hours=body.ttl_hours,
    )
    return {"status": "ok", "target_id": body.target_id, "override": body.override}


@router.delete("/overrides/{target_id}")
async def remove_override(target_id: str, override: str = "freeze") -> dict[str, Any]:
    _ceo().remove_override(target_id, override)
    return {"status": "removed", "target_id": target_id}


@router.post("/metrics")
async def update_from_metrics(body: MetricsUpdate) -> dict[str, Any]:
    """
    Part 4 — Closed-loop: execution → metrics → learning → strategy → execution.
    Call this after each reporting cycle to feed live data back into the CEO brain.
    """
    from dataclasses import asdict
    niche_data   = [n.model_dump() for n in body.niche_data]   if body.niche_data   else None
    account_data = [a.model_dump() for a in body.account_data] if body.account_data else None
    state = _ceo().update_from_metrics(
        actual_daily_views   = body.actual_daily_views,
        actual_daily_revenue = body.actual_daily_revenue,
        niche_data           = niche_data,
        account_data         = account_data,
    )
    return {
        "status": "updated",
        "performance_ratio":   state.performance_ratio,
        "threshold_modifier":  state.threshold_modifier,
        "exploration_rate":    state.exploration_rate,
        "growth_mode":         state.growth_mode,
    }


@router.get("/log")
async def get_log(limit: int = 50) -> list[dict[str, Any]]:
    return _ceo().get_log(limit)
