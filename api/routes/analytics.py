from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel
from database.analytics import save_video_metrics, get_top_performing

router = APIRouter(prefix="/analytics", tags=["Analytics"])

_QUEUE_DB = Path("data") / "brain_queue.db"

class MetricsPayload(BaseModel):
    video_id: str
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    watch_time: float | None = None
    retention_rate: float | None = None
    hook_text: str | None = None
    template_type: str | None = None
    video_length: float | None = None
    effect_types: str | None = None
    keyword: str | None = None
    product_type: str | None = None
    hours_since_post: float | None = None

@router.post("/metrics")
async def post_metrics(payload: MetricsPayload, request: Request) -> dict[str, Any]:
    db: AutomationDatabase = request.app.state.database
    await save_video_metrics(db, payload.model_dump())
    return {"status": "ok"}

@router.get("/top-performing")
async def top_performing(request: Request, limit: int = 50) -> dict[str, Any]:
    from database.database import AutomationDatabase
    db: AutomationDatabase = request.app.state.database
    records = await get_top_performing(db, limit)
    return {"status": "ok", "records": records}


@router.get("/overview")
async def get_overview(request: Request) -> dict[str, Any]:
    """
    Executive Dashboard data source.
    Returns:
      - views_trend: 7-day daily approved-content counts + estimated views/revenue
      - funnel: conversion funnel from brain_queue status distribution
      - top_content: top 5 performing videos from analytics table
    """
    from database.database import AutomationDatabase
    db: AutomationDatabase = request.app.state.database

    # ── 7-day views trend from brain_queue ─────────────────────────────────────
    views_trend: list[dict[str, Any]] = []
    funnel: list[dict[str, Any]] = []
    try:
        if _QUEUE_DB.exists():
            con = sqlite3.connect(str(_QUEUE_DB), timeout=5)
            con.row_factory = sqlite3.Row
            now_ts = time.time()
            day_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

            for offset in range(6, -1, -1):
                start_ts = now_ts - (offset + 1) * 86400
                end_ts   = now_ts - offset * 86400
                row = con.execute(
                    "SELECT COUNT(*) as cnt, AVG(final_score) as avg_score,"
                    " SUM(expected_value) as total_ev"
                    " FROM brain_queue"
                    " WHERE created_at >= ? AND created_at < ?",
                    (start_ts, end_ts),
                ).fetchone()
                dt = datetime.fromtimestamp(end_ts, tz=timezone.utc)
                label = day_labels[dt.weekday()]
                cnt = row["cnt"] or 0
                # Approximate views: each approved item × avg_views heuristic
                avg_sc = row["avg_score"] or 0.0
                views_trend.append({
                    "day":     label,
                    "views":   int(cnt * max(1, avg_sc) * 5000),
                    "revenue": round((row["total_ev"] or 0.0), 1),
                    "items":   cnt,
                })

            # Funnel from status distribution
            funnel_rows = con.execute(
                "SELECT status, COUNT(*) as cnt FROM brain_queue GROUP BY status"
            ).fetchall()
            con.close()
            status_map = {r["status"]: r["cnt"] for r in funnel_rows}
            total = sum(status_map.values()) or 1
            decided  = total
            pending  = status_map.get("pending", 0)
            approved = status_map.get("approved", 0)
            published = status_map.get("force_published", 0)
            funnel = [
                {"stage": "Decided",   "value": decided},
                {"stage": "Pending",   "value": pending},
                {"stage": "Approved",  "value": approved},
                {"stage": "Published", "value": published},
            ]
        else:
            # No queue DB yet — return zero-filled 7 days
            day_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            views_trend = [{"day": d, "views": 0, "revenue": 0, "items": 0} for d in day_labels]
            funnel = [
                {"stage": "Decided",   "value": 0},
                {"stage": "Pending",   "value": 0},
                {"stage": "Approved",  "value": 0},
                {"stage": "Published", "value": 0},
            ]
    except Exception:
        day_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        views_trend = [{"day": d, "views": 0, "revenue": 0, "items": 0} for d in day_labels]
        funnel = []

    # ── Top performing content from analytics table ─────────────────────────────
    top_content: list[dict[str, Any]] = []
    try:
        raw = await get_top_performing(db, limit=5)
        top_content = [dict(r) for r in raw]
    except Exception:
        top_content = []

    return {
        "views_trend":  views_trend,
        "funnel":       funnel,
        "top_content":  top_content,
    }
