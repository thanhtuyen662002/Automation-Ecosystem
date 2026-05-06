from __future__ import annotations

import logging
from typing import Any

from database.database import AutomationDatabase

LOGGER = logging.getLogger("database.analytics")

async def save_video_metrics(db: AutomationDatabase, data: dict[str, Any]) -> None:
    """
    Save performance metrics for a posted video.
    Calculates view_velocity and performance_score.
    """
    views = data.get("views", 0)
    likes = data.get("likes", 0)
    comments = data.get("comments", 0)
    shares = data.get("shares", 0)
    
    # Calculate velocity
    hours_since_post = data.get("hours_since_post")
    if hours_since_post is None:
        hours_since_post = 24.0 # Default if unknown

    view_velocity = views / max(float(hours_since_post), 1.0)

    # Calculate performance score
    # performance_score = views*0.3 + velocity*0.3 + likes*0.2 + comments*0.1 + shares*0.1
    performance_score = (views * 0.3) + (view_velocity * 0.3) + (likes * 0.2) + (comments * 0.1) + (shares * 0.1)

    query = """
        INSERT INTO video_metrics (
            video_id, views, likes, comments, shares, watch_time, retention_rate,
            hook_text, template_type, video_length, effect_types, keyword, product_type,
            posted_at, view_velocity, performance_score
        )
        VALUES (
            $1, $2, $3, $4, $5, $6, $7,
            $8, $9, $10, $11, $12, $13,
            COALESCE($14, now()), $15, $16
        )
    """
    
    try:
        await db.execute(
            query,
            str(data.get("video_id", "unknown")),
            int(views),
            int(likes),
            int(comments),
            int(shares),
            data.get("watch_time"),
            data.get("retention_rate"),
            data.get("hook_text"),
            data.get("template_type"),
            data.get("video_length"),
            data.get("effect_types"),
            data.get("keyword"),
            data.get("product_type"),
            data.get("posted_at"),
            float(view_velocity),
            float(performance_score)
        )
        LOGGER.info("saved_video_metrics", extra={"video_id": data.get("video_id"), "score": performance_score})
    except Exception as exc:
        LOGGER.error("failed_to_save_metrics", extra={"error": str(exc)})


async def get_historical_signal(db_url: str, keyword: str | None = None) -> dict[str, float]:
    """
    Calculates historical score for a given keyword vs global average.
    Uses time decay: weight = exp(-days_old / 7).
    Direct read-only query for workers.
    """
    import asyncpg
    
    query_global = """
        SELECT AVG(performance_score) as global_avg
        FROM video_metrics;
    """
    
    query_historical = """
        SELECT 
            SUM(performance_score * exp(-EXTRACT(EPOCH FROM (now() - posted_at)) / 86400.0 / 7.0)) as weighted_sum,
            SUM(exp(-EXTRACT(EPOCH FROM (now() - posted_at)) / 86400.0 / 7.0)) as total_weight
        FROM video_metrics
        WHERE keyword = $1;
    """
    
    conn = None
    try:
        conn = await asyncpg.connect(db_url)
        global_res = await conn.fetchrow(query_global)
        global_avg = float(global_res["global_avg"]) if global_res and global_res["global_avg"] is not None else 0.0
        
        historical_score = global_avg
        
        if keyword:
            hist_res = await conn.fetchrow(query_historical, keyword)
            if hist_res and hist_res["total_weight"] and float(hist_res["total_weight"]) > 0:
                historical_score = float(hist_res["weighted_sum"]) / float(hist_res["total_weight"])
                
        return {
            "global_avg": global_avg,
            "historical_score": historical_score
        }
    except Exception as exc:
        LOGGER.error("failed_to_get_historical_signal", extra={"error": str(exc)})
        return {"global_avg": 0.0, "historical_score": 0.0}
    finally:
        if conn:
            await conn.close()

async def get_top_performing(db: AutomationDatabase, limit: int = 50) -> list[dict[str, Any]]:
    query = """
        SELECT *
        FROM video_metrics
        ORDER BY performance_score DESC
        LIMIT $1;
    """
    rows = await db.fetch_all(query, limit)
    return [dict(r) for r in rows]
