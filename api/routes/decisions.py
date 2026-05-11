"""
api/routes/decisions.py — Unified Decision Feed for the Command Dashboard.

GET /api/v1/system/decisions

Aggregates REAL data from:
  - brain_queue (content decisions, status=pending)
  - fleet-health accounts (high-risk / fatigued accounts)
  - brain config (execution engine state)

Returns prioritized list of DecisionBlock-ready items.
Each item has: id, type, title, reason, expected_value,
               confidence, risk_flags, action, priority_score, metadata
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter

LOGGER = logging.getLogger("api.decisions")
router = APIRouter(prefix="/system", tags=["Decision Feed"])

# Brain queue DB path — same as content_brain.py
_QUEUE_DB = Path("data") / "brain_queue.db"


def _qdb() -> sqlite3.Connection | None:
    if not _QUEUE_DB.exists():
        return None
    con = sqlite3.connect(str(_QUEUE_DB), timeout=5)
    con.row_factory = sqlite3.Row
    return con


def _get_brain_config() -> dict[str, Any]:
    """Read live execution config from content_brain module."""
    try:
        from api.routes.content_brain import _CONFIG
        return dict(_CONFIG)
    except Exception:
        return {"EXECUTION_ENABLED": True, "AUTO_APPROVE": False}


def _get_fleet_accounts() -> list[dict[str, Any]]:
    """Pull live account snapshots from brain registry."""
    try:
        from core.account_brain import get_brain_registry
        from core.lifecycle_manager import get_lifecycle_manager
        registry = get_brain_registry()
        lifecycle = get_lifecycle_manager()
        brain_snaps = registry.snapshot_all()
        lc_snaps = {s["account_id"]: s for s in lifecycle.snapshot_all()}

        accounts = []
        for brain in brain_snaps:
            aid = brain.get("account_id", "")
            lc = lc_snaps.get(aid, {})
            accounts.append({
                "account_id":              aid,
                "phase":                   lc.get("phase", "NORMAL"),
                "risk_level":              brain.get("risk_level", "low"),
                "trust_score":             brain.get("trust_score", 0.75),
                "fatigue_level":           brain.get("fatigue_level", 0.0),
                "anomaly_count":           brain.get("consecutive_anomalies", 0),
                "cooldown_remaining_hours": lc.get("cooldown_remaining_hours", 0.0),
                "uploads_suspended":       brain.get("uploads_suspended", False),
                "operating_mode":          brain.get("operating_mode", "NORMAL"),
            })
        return accounts
    except Exception as exc:
        LOGGER.warning("decisions_fleet_unavailable: %s", exc)
        return []


@router.get("/decisions")
async def get_decisions(limit: int = 5) -> list[dict[str, Any]]:
    """
    Return the unified decision feed for the Command Dashboard.
    Items are sorted by priority_score DESC.
    Max `limit` items returned (default 5, per UX spec).
    """
    items: list[dict[str, Any]] = []
    now = time.time()
    cfg = _get_brain_config()

    # ── 1. SYSTEM ALERT — execution engine off ────────────────────────────────
    if not cfg.get("EXECUTION_ENABLED", True):
        items.append({
            "id":             "sys-engine-off",
            "type":           "system",
            "title":          "Máy thực thi đang TẮT — không có gì được đăng",
            "reason":         "Tất cả lịch trình bị tạm dừng. Không có nội dung nào được xuất bản.",
            "expected_value": 0.0,
            "confidence":     1.0,
            "risk_flags":     ["execution_disabled"],
            "action":         "enable_execution",
            "priority_score": 1.0,
            "metadata":       {"config_key": "EXECUTION_ENABLED"},
        })

    # ── 2. FLEET ALERTS — high risk / fatigue accounts ────────────────────────
    accounts = _get_fleet_accounts()
    for acct in accounts:
        risk = acct.get("risk_level", "low")
        anomalies = acct.get("anomaly_count", 0)
        fatigue = acct.get("fatigue_level", 0.0)
        phase = acct.get("phase", "NORMAL")
        cooldown_h = acct.get("cooldown_remaining_hours", 0.0)
        aid = acct.get("account_id", "?")

        if risk == "high" or anomalies >= 2:
            items.append({
                "id":             f"fleet-{aid}-danger",
                "type":           "account",
                "title":          f"{aid} — {anomalies} bất thường được phát hiện",
                "reason":         f"Trust {round(acct['trust_score']*100)}% · Fatigue {round(fatigue*100)}% · Pha: {phase}",
                "expected_value": 0.0,
                "confidence":     1.0,
                "risk_flags":     [f"{anomalies}_anomalies_detected", f"risk_level_{risk}"],
                "action":         "freeze",
                "priority_score": 0.95,
                "metadata":       {"account_id": aid, "phase": phase, "trust_score": acct["trust_score"]},
            })
        elif fatigue > 0.70:
            items.append({
                "id":             f"fleet-{aid}-fatigue",
                "type":           "account",
                "title":          f"{aid} — Mệt mỏi {round(fatigue*100)}%",
                "reason":         f"Trust {round(acct['trust_score']*100)}% · Pha: {phase} · Nên giảm tần suất",
                "expected_value": 0.0,
                "confidence":     0.85,
                "risk_flags":     [f"fatigue_{round(fatigue*100)}pct"],
                "action":         "pause",
                "priority_score": 0.75,
                "metadata":       {"account_id": aid, "fatigue_level": fatigue},
            })
        elif phase == "COOLDOWN" and cooldown_h > 0 and anomalies > 0:
            items.append({
                "id":             f"fleet-{aid}-cooldown",
                "type":           "account",
                "title":          f"{aid} — Còn {cooldown_h:.1f}h cooldown",
                "reason":         f"Trust {round(acct['trust_score']*100)}% · {anomalies} bất thường",
                "expected_value": 0.0,
                "confidence":     0.80,
                "risk_flags":     ["cooldown_with_anomalies"],
                "action":         "monitor",
                "priority_score": 0.60,
                "metadata":       {"account_id": aid, "cooldown_remaining_hours": cooldown_h},
            })

    # ── 3. CONTENT DECISIONS — pending brain_queue items ─────────────────────
    con = _qdb()
    if con:
        try:
            rows = con.execute(
                "SELECT content_id, hook, niche, platform, mode, final_score,"
                " expected_value, confidence, priority_score, risk_flags, reason"
                " FROM brain_queue WHERE status='pending'"
                " ORDER BY priority_score DESC, expected_value DESC LIMIT 10"
            ).fetchall()
            for row in rows:
                r = dict(row)
                try:
                    flags = json.loads(r.get("risk_flags", "[]"))
                except Exception:
                    flags = []
                items.append({
                    "id":             r["content_id"],
                    "type":           "content",
                    "title":          f'"{r["hook"][:65]}{"..." if len(r["hook"]) > 65 else ""}"',
                    "reason":         f'{r["niche"]} · {r["platform"]} · {r["mode"]} · Điểm {round(r["final_score"]*100)}/100',
                    "expected_value": r["expected_value"],
                    "confidence":     r["confidence"],
                    "risk_flags":     flags,
                    "action":         "approve",
                    "priority_score": r["priority_score"],
                    "metadata":       {
                        "content_id":  r["content_id"],
                        "niche":       r["niche"],
                        "platform":    r["platform"],
                        "mode":        r["mode"],
                        "final_score": r["final_score"],
                    },
                })
        except Exception as exc:
            LOGGER.warning("decisions_queue_error: %s", exc)
        finally:
            con.close()

    # Sort by priority descending, cap at limit
    items.sort(key=lambda x: x["priority_score"], reverse=True)
    return items[:limit]
