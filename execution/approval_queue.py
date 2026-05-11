"""
execution/approval_queue.py — CEO Approval Layer.

Stores AI-selected content candidates for human review before publishing.
By default all content requires manual approval (AUTO_APPROVE env var = "0").

SQLite-backed. Path via APPROVAL_DB env var (default: data/approval_queue.db).

Public API:
    submit(candidate)                          → str (item_id)
    approve(item_id)                           → bool
    reject(item_id, reason)                   → bool
    batch_approve(item_ids)                   → int (count approved)
    get_pending()                             → list[dict]
    get_approved(limit)                       → list[dict]
    get_rejected(limit)                       → list[dict]
    get_item(item_id)                         → dict | None
    reset_approval_queue()                    # testing only

AUTO_APPROVE mode:
    Set env var AUTO_APPROVE=1 to skip human approval.
    Useful for fully automated runs.
    Default: OFF (0).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("execution.approval_queue")

# ── Config ────────────────────────────────────────────────────────────────────

_DEFAULT_DB = Path("data") / "approval_queue.db"

def _db_path() -> Path:
    env = os.environ.get("APPROVAL_DB")
    return Path(env) if env else _DEFAULT_DB

AUTO_APPROVE: bool = os.environ.get("AUTO_APPROVE", "0") == "1"

# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS queue (
    item_id       TEXT PRIMARY KEY,
    status        TEXT NOT NULL DEFAULT 'pending',
    candidate_json TEXT NOT NULL DEFAULT '{}',
    score         REAL NOT NULL DEFAULT 0.0,
    niche         TEXT NOT NULL DEFAULT '',
    platform      TEXT NOT NULL DEFAULT '',
    mode          TEXT NOT NULL DEFAULT '',
    submitted_at  REAL NOT NULL DEFAULT 0.0,
    reviewed_at   REAL NOT NULL DEFAULT 0.0,
    reject_reason TEXT NOT NULL DEFAULT '',
    auto_approved INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status, submitted_at);
CREATE INDEX IF NOT EXISTS idx_queue_niche  ON queue(niche, status);
"""

# ── Thread-local connection ────────────────────────────────────────────────────

_local = threading.local()
_init_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        db = _db_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(db), check_same_thread=False, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        with _init_lock:
            con.executescript(_DDL)
            con.commit()
        _local.conn = con
    return _local.conn


def _exec(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    c = _conn()
    cur = c.execute(sql, params)
    c.commit()
    return cur


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    try:
        d["candidate"] = json.loads(d.pop("candidate_json", "{}"))
    except Exception:
        d["candidate"] = {}
    return d


# ── ID generation ─────────────────────────────────────────────────────────────

def _make_item_id(candidate: dict[str, Any]) -> str:
    """Deterministic item_id from candidate content."""
    key = f"{candidate.get('content_id', '')}:{candidate.get('niche', '')}:{int(time.time())}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# ── Public API ────────────────────────────────────────────────────────────────

def submit(candidate: dict[str, Any]) -> str:
    """
    Submit a candidate for approval.

    If AUTO_APPROVE=1, immediately marks as approved.

    Returns item_id.
    """
    item_id = candidate.get("approval_id") or _make_item_id(candidate)
    score   = float(candidate.get("score", 0.0))
    niche   = candidate.get("niche", "")
    platform = candidate.get("platform", "tiktok")
    mode    = candidate.get("mode", "reup")
    auto    = 1 if AUTO_APPROVE else 0
    status  = "approved" if AUTO_APPROVE else "pending"

    try:
        _exec(
            """INSERT OR REPLACE INTO queue
               (item_id, status, candidate_json, score, niche, platform, mode,
                submitted_at, reviewed_at, auto_approved)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (item_id, status, json.dumps(candidate), score, niche, platform, mode,
             time.time(), time.time() if AUTO_APPROVE else 0.0, auto),
        )
        LOGGER.info(
            "approval_submit item_id=%s status=%s niche=%s platform=%s score=%.3f",
            item_id, status, niche, platform, score,
        )
    except Exception as exc:
        LOGGER.warning("approval_submit_error error=%s", exc)

    return item_id


def approve(item_id: str) -> bool:
    """Approve a pending item for scheduling."""
    try:
        cur = _exec(
            "UPDATE queue SET status='approved', reviewed_at=? WHERE item_id=? AND status='pending'",
            (time.time(), item_id),
        )
        ok = cur.rowcount > 0
        if ok:
            LOGGER.info("approval_approved item_id=%s", item_id)
        else:
            LOGGER.debug("approval_not_found_or_not_pending item_id=%s", item_id)
        return ok
    except Exception as exc:
        LOGGER.warning("approval_approve_error error=%s", exc)
        return False


def reject(item_id: str, reason: str = "") -> bool:
    """Reject a pending item."""
    try:
        cur = _exec(
            "UPDATE queue SET status='rejected', reviewed_at=?, reject_reason=?"
            " WHERE item_id=? AND status='pending'",
            (time.time(), reason[:500], item_id),
        )
        ok = cur.rowcount > 0
        if ok:
            LOGGER.info("approval_rejected item_id=%s reason=%s", item_id, reason)
        return ok
    except Exception as exc:
        LOGGER.warning("approval_reject_error error=%s", exc)
        return False


def batch_approve(item_ids: list[str]) -> int:
    """Approve multiple items at once. Returns count approved."""
    count = 0
    for iid in item_ids:
        if approve(iid):
            count += 1
    LOGGER.info("batch_approve approved=%d of=%d", count, len(item_ids))
    return count


def get_pending() -> list[dict[str, Any]]:
    """Return all pending items ordered by score descending."""
    try:
        rows = _conn().execute(
            "SELECT * FROM queue WHERE status='pending' ORDER BY score DESC, submitted_at ASC"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    except Exception as exc:
        LOGGER.warning("approval_get_pending_error error=%s", exc)
        return []


def get_approved(limit: int = 100) -> list[dict[str, Any]]:
    """Return approved items not yet dispatched."""
    try:
        rows = _conn().execute(
            "SELECT * FROM queue WHERE status='approved'"
            " ORDER BY score DESC, reviewed_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    except Exception as exc:
        LOGGER.warning("approval_get_approved_error error=%s", exc)
        return []


def get_rejected(limit: int = 100) -> list[dict[str, Any]]:
    try:
        rows = _conn().execute(
            "SELECT * FROM queue WHERE status='rejected'"
            " ORDER BY reviewed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    except Exception as exc:
        return []


def get_item(item_id: str) -> dict[str, Any] | None:
    try:
        row = _conn().execute(
            "SELECT * FROM queue WHERE item_id=?", (item_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None
    except Exception:
        return None


def mark_dispatched(item_id: str) -> bool:
    """Mark an approved item as dispatched (sent to scheduler)."""
    try:
        cur = _exec(
            "UPDATE queue SET status='dispatched' WHERE item_id=? AND status='approved'",
            (item_id,),
        )
        return cur.rowcount > 0
    except Exception:
        return False


def get_stats() -> dict[str, Any]:
    """Return counts per status."""
    try:
        con = _conn()
        rows = con.execute(
            "SELECT status, COUNT(*) as cnt FROM queue GROUP BY status"
        ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}
    except Exception:
        return {}


def reset_approval_queue() -> None:
    """Hard reset — for testing only."""
    try:
        con = _conn()
        con.executescript("DELETE FROM queue;")
        con.commit()
    except Exception:
        pass
    if hasattr(_local, "conn"):
        try:
            _local.conn.close()
        except Exception:
            pass
        _local.conn = None
