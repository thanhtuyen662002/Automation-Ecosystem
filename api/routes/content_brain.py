"""
api/routes/content_brain.py — Execution Brain API router.

Mounts at /api/v1/brain via api/main.py.

Endpoints:
    POST  /brain/decide              → single candidate decision
    POST  /brain/batch-decide        → batch decision
    GET   /brain/queue               → pending approval queue
    POST  /brain/queue/{id}/approve  → human approve
    POST  /brain/queue/{id}/reject   → human reject
    POST  /brain/queue/{id}/override → force-publish
    GET   /brain/stats               → layer health
    GET   /brain/insights            → winning hooks/hours/combos
    GET   /brain/config              → runtime config
    POST  /brain/config              → update config
"""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

LOGGER = logging.getLogger("api.content_brain")
router = APIRouter(prefix="/brain", tags=["Execution Brain"])

# ── In-process config store (survives until process restart) ──────────────────
_CONFIG: dict[str, Any] = {
    "EXECUTION_ENABLED":  True,
    "AUTO_APPROVE":       False,
    "MAX_POSTS_PER_DAY":  5,
    "EXPLORATION_RATE":   0.10,
    "COST_LIMIT":         1.00,
    "MIN_SCORE":          0.26,
}

# ── SQLite queue for pending decisions ────────────────────────────────────────
_QUEUE_DB = Path("data") / "brain_queue.db"
_QUEUE_DDL = """
CREATE TABLE IF NOT EXISTS brain_queue (
    content_id     TEXT PRIMARY KEY,
    platform       TEXT NOT NULL DEFAULT '',
    niche          TEXT NOT NULL DEFAULT '',
    mode           TEXT NOT NULL DEFAULT 'reup',
    status         TEXT NOT NULL DEFAULT 'pending',
    decision       TEXT NOT NULL DEFAULT '',
    reason         TEXT NOT NULL DEFAULT '',
    final_score    REAL NOT NULL DEFAULT 0.0,
    raw_score      REAL NOT NULL DEFAULT 0.0,
    expected_value REAL NOT NULL DEFAULT 0.0,
    confidence     REAL NOT NULL DEFAULT 0.65,
    priority_score REAL NOT NULL DEFAULT 0.0,
    hook           TEXT NOT NULL DEFAULT '',
    caption        TEXT NOT NULL DEFAULT '',
    risk_flags     TEXT NOT NULL DEFAULT '[]',
    signals        TEXT NOT NULL DEFAULT '{}',
    created_at     REAL NOT NULL DEFAULT 0.0,
    decided_at     REAL NOT NULL DEFAULT 0.0,
    approved_by    TEXT NOT NULL DEFAULT ''
);
"""

def _qdb() -> sqlite3.Connection:
    _QUEUE_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(_QUEUE_DB), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_QUEUE_DDL)
    con.commit()
    return con


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    import json
    d = dict(row)
    for key in ("risk_flags", "signals"):
        try:
            d[key] = json.loads(d[key])
        except Exception:
            d[key] = []
    return d


# ── Request / Response schemas ────────────────────────────────────────────────

class CandidateIn(BaseModel):
    content_id:       str
    source_url:       str  = ""
    caption:          str  = ""
    hook:             str  = ""
    niche:            str  = "entertainment"
    platform:         str  = "tiktok"
    trend_score:      float | None = None
    hook_score:       float | None = None
    novelty_score:    float | None = None
    view_count:       int   = 5000
    production_cost:  float = 0.0
    hook_samples:     int   = 0
    trend_age_h:      float = 24.0

class AccountIn(BaseModel):
    account_id:   str
    platform:     str   = "tiktok"
    health_score: float = 0.75
    posts_today:  int   = 0
    niche:        str   = "entertainment"

class DecideRequest(BaseModel):
    candidate: CandidateIn
    accounts:  list[AccountIn]
    mode:      str   = Field("reup", pattern="^(reup|remark|generate)$")
    aov:       float = 25.0
    cost:      float = 0.0

class BatchDecideRequest(BaseModel):
    candidates: list[CandidateIn]
    accounts:   list[AccountIn]
    mode:       str   = "reup"
    aov:        float = 25.0
    cost:       float = 0.0

class ConfigUpdate(BaseModel):
    EXECUTION_ENABLED: bool  | None = None
    AUTO_APPROVE:      bool  | None = None
    MAX_POSTS_PER_DAY: int   | None = None
    EXPLORATION_RATE:  float | None = None
    COST_LIMIT:        float | None = None
    MIN_SCORE:         float | None = None

class QueueActionRequest(BaseModel):
    reason:      str = ""
    new_caption: str = ""
    new_hook:    str = ""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run_decide(req: DecideRequest) -> dict[str, Any]:
    import json, os
    os.environ.setdefault("WARMUP_ENABLED", "0")
    from execution.execution_brain import decide

    cand = req.candidate.model_dump()
    # Merge optional scores only if provided
    for key in ("trend_score", "hook_score", "novelty_score"):
        if cand[key] is None:
            del cand[key]

    accts = [a.model_dump() for a in req.accounts]
    d = decide(
        candidate=cand, accounts=accts,
        platform=req.candidate.platform,
        niche=req.candidate.niche,
        mode=req.mode, aov=req.aov, cost=req.cost,
    )
    result = d.to_dict()
    result["selected_time"] = result.get("selected_time") or None

    # Persist to queue
    _upsert_queue(d, req.candidate, req.mode)
    return result


def _upsert_queue(d: Any, cand: CandidateIn, mode: str) -> None:
    import json
    # Compute priority_score: confidence × (1 - risk_penalty) × final_score
    risk_flags  = d.signals.get("risk_flags", [])
    _rp         = min(0.5, 0.1 * len(risk_flags))
    _conf       = d.signals.get("confidence", 0.65)
    priority_score = round(_conf * (1.0 - _rp) * d.final_score, 4)

    con = _qdb()
    try:
        con.execute(
            "INSERT OR REPLACE INTO brain_queue"
            " (content_id, platform, niche, mode, status, decision, reason,"
            "  final_score, raw_score, expected_value, confidence, priority_score, hook, caption,"
            "  risk_flags, signals, created_at, decided_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                cand.content_id, cand.platform, cand.niche, mode,
                "pending" if d.decision == "publish" and not _CONFIG["AUTO_APPROVE"] else d.decision,
                d.decision, d.reason,
                d.final_score,
                d.signals.get("raw_score", d.final_score),
                d.expected_value,
                _conf,
                priority_score,
                d.signals.get("best_hook", cand.hook or cand.caption[:80]),
                cand.caption,
                json.dumps(d.signals.get("risk_flags", [])),
                json.dumps({k: v for k, v in d.signals.items()
                            if isinstance(v, (int, float, str, bool))}),
                time.time(), time.time(),
            ),
        )
        con.commit()
    except Exception as exc:
        LOGGER.warning("queue_upsert_error %s", exc)
    finally:
        con.close()


# ── WebSocket broadcast helper ────────────────────────────────────────────────

def _broadcast(event: str, data: dict[str, Any]) -> None:
    try:
        from api.routes.ws import broadcast
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(broadcast(event, data))
        except RuntimeError:
            pass
    except Exception:
        pass


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/decide")
async def decide_endpoint(req: DecideRequest) -> dict[str, Any]:
    """Run execution brain on a single candidate."""
    if not _CONFIG["EXECUTION_ENABLED"]:
        raise HTTPException(503, "Execution engine is disabled")
    try:
        result = _run_decide(req)
        _broadcast("decision_made", {
            "content_id": req.candidate.content_id,
            "decision": result.get("decision"),
            "final_score": result.get("final_score"),
            "expected_value": result.get("expected_value"),
        })
        return result
    except Exception as exc:
        LOGGER.exception("decide_error")
        raise HTTPException(500, str(exc)) from exc


@router.post("/batch-decide")
async def batch_decide_endpoint(req: BatchDecideRequest) -> list[dict[str, Any]]:
    """Run execution brain on a batch of candidates."""
    if not _CONFIG["EXECUTION_ENABLED"]:
        raise HTTPException(503, "Execution engine is disabled")
    try:
        import os; os.environ.setdefault("WARMUP_ENABLED", "0")
        from execution.execution_brain import batch_decide

        cands = []
        for c in req.candidates:
            d = c.model_dump()
            for key in ("trend_score", "hook_score", "novelty_score"):
                if d[key] is None:
                    del d[key]
            cands.append(d)

        accts  = [a.model_dump() for a in req.accounts]
        results = batch_decide(cands, accts, req.candidates[0].platform if req.candidates else "tiktok",
                               req.candidates[0].niche if req.candidates else "entertainment",
                               mode=req.mode, cost_per_item=req.cost, aov=req.aov)
        return [r.to_dict() for r in results]
    except Exception as exc:
        LOGGER.exception("batch_decide_error")
        raise HTTPException(500, str(exc)) from exc


@router.get("/queue")
async def get_queue(status: str = "pending", limit: int = 100) -> list[dict[str, Any]]:
    """
    Return brain queue items ordered by priority_score DESC.
    status: 'pending' | 'approved' | 'rejected' | 'all'
    Use status=all to fetch all records (for ContentQueue full view).
    """
    con = _qdb()
    try:
        if status == "all":
            rows = con.execute(
                "SELECT * FROM brain_queue"
                " ORDER BY priority_score DESC, final_score DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM brain_queue WHERE status=?"
                " ORDER BY priority_score DESC, final_score DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        con.close()



@router.post("/queue/{content_id}/approve")
async def approve_content(content_id: str, req: QueueActionRequest = QueueActionRequest()) -> dict[str, Any]:
    """Human approve a pending item — push to scheduler."""
    con = _qdb()
    try:
        row = con.execute("SELECT * FROM brain_queue WHERE content_id=?", (content_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"content_id {content_id} not in queue")
        updates: list[tuple[str, Any]] = [("status", "approved"), ("approved_by", "human")]
        if req.new_hook:
            updates.append(("hook", req.new_hook))
        if req.new_caption:
            updates.append(("caption", req.new_caption))
        for col, val in updates:
            con.execute(f"UPDATE brain_queue SET {col}=?, decided_at=? WHERE content_id=?",
                        (val, time.time(), content_id))
        con.commit()
        _broadcast("publish_event", {"content_id": content_id, "action": "approved"})
        return {"content_id": content_id, "status": "approved"}
    finally:
        con.close()


@router.post("/queue/{content_id}/reject")
async def reject_content(content_id: str, req: QueueActionRequest = QueueActionRequest()) -> dict[str, Any]:
    """Human reject a pending item."""
    con = _qdb()
    try:
        con.execute(
            "UPDATE brain_queue SET status='rejected', reason=?, decided_at=? WHERE content_id=?",
            (req.reason or "human_rejected", time.time(), content_id),
        )
        con.commit()
        _broadcast("decision_made", {"content_id": content_id, "action": "rejected"})
        return {"content_id": content_id, "status": "rejected"}
    finally:
        con.close()


@router.post("/queue/{content_id}/override")
async def override_content(content_id: str, req: QueueActionRequest = QueueActionRequest()) -> dict[str, Any]:
    """Force-publish — bypass score gate."""
    con = _qdb()
    try:
        con.execute(
            "UPDATE brain_queue SET status='force_published', approved_by='override',"
            " decided_at=? WHERE content_id=?",
            (time.time(), content_id),
        )
        con.commit()
        _broadcast("publish_event", {"content_id": content_id, "action": "force_published"})
        return {"content_id": content_id, "status": "force_published"}
    finally:
        con.close()


@router.get("/stats")
async def get_stats() -> dict[str, Any]:
    """Aggregated health from all execution layers."""
    try:
        from execution.execution_brain import get_brain_stats
        return get_brain_stats()
    except Exception as exc:
        return {"error": str(exc)}


@router.get("/insights")
async def get_insights() -> dict[str, Any]:
    """Winning hooks, best hours, top combos, funnel metrics."""
    insights: dict[str, Any] = {}

    # Top hooks
    try:
        from execution.hook_optimizer import get_top_hooks
        insights["top_hooks"] = {
            niche: get_top_hooks(niche, limit=5)
            for niche in ("entertainment", "finance", "tech", "fitness")
        }
    except Exception:
        insights["top_hooks"] = {}

    # Best timing per platform/niche
    try:
        from execution.smart_scheduler import get_timing_report
        insights["timing"] = {
            f"{pl}_{n}": get_timing_report(pl, n)
            for pl in ("tiktok", "facebook") for n in ("finance", "tech", "entertainment")
        }
    except Exception:
        insights["timing"] = {}

    # Top cross-layer combos
    try:
        from execution.cross_layer_learner import get_winning_combos
        from dataclasses import asdict
        insights["top_combos"] = {
            f"{pl}_{n}": [asdict(c) for c in get_winning_combos(pl, n, limit=5)]
            for pl in ("tiktok", "facebook") for n in ("finance", "tech", "entertainment")
        }
    except Exception:
        insights["top_combos"] = {}

    # Funnel per platform/niche
    try:
        from execution.conversion_optimizer import get_funnel_report
        insights["funnel"] = {
            f"{pl}_{n}": get_funnel_report(pl, n)
            for pl in ("tiktok", "facebook") for n in ("finance", "tech", "entertainment")
        }
    except Exception:
        insights["funnel"] = {}

    # Queue summary
    try:
        con = _qdb()
        rows = con.execute(
            "SELECT status, COUNT(*) as cnt, AVG(final_score) as avg_score,"
            " SUM(CASE WHEN expected_value > 0 THEN expected_value ELSE 0 END) as total_ev"
            " FROM brain_queue GROUP BY status"
        ).fetchall()
        con.close()
        insights["queue_summary"] = [dict(r) for r in rows]
    except Exception:
        insights["queue_summary"] = []

    return insights


@router.get("/config")
async def get_config() -> dict[str, Any]:
    return dict(_CONFIG)


@router.post("/config")
async def update_config(update: ConfigUpdate) -> dict[str, Any]:
    for field, val in update.model_dump(exclude_none=True).items():
        _CONFIG[field] = val
        # Sync env vars used by execution_brain
        import os
        env_map = {
            "EXPLORATION_RATE": "BRAIN_EXPLORE_RATE",
            "COST_LIMIT":       "BRAIN_MIN_COST_FOR_EV",
            "MIN_SCORE":        "BRAIN_MIN_SCORE",
        }
        if field in env_map:
            os.environ[env_map[field]] = str(val)
    LOGGER.info("config_updated fields=%s", list(update.model_dump(exclude_none=True).keys()))
    return dict(_CONFIG)
