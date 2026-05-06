from typing import Any
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from database.database import AutomationDatabase
from database.analytics import save_video_metrics, get_top_performing

router = APIRouter(prefix="/analytics", tags=["Analytics"])

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
    db: AutomationDatabase = request.app.state.database
    records = await get_top_performing(db, limit)
    return {"status": "ok", "records": records}
