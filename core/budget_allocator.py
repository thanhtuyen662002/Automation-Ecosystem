"""
core/budget_allocator.py — Budget Allocation Engine

Distributes budget proportionally to unified_score (from execution_brain).
Kill gate (3-cycle safety rule) is the ONLY decision made here.
All scale/optimize/explore decisions remain in execution_brain.

Public API:
    allocate(contents, total_budget, accounts) -> list[dict]
    get_account_roi_multiplier(account_id)     -> float
    record_kill_cycle(content_id)
    reset_kill_cycles(content_id)

SQLite table: budget_kill_cycles
    content_id TEXT PRIMARY KEY
    bad_cycles  INTEGER DEFAULT 0
    last_updated REAL
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any

# ── Config ────────────────────────────────────────────────────────────────────

_DEFAULT_DB    = Path("data") / "budget_allocator.db"

# Kill gate (safety guard — NOT a decision)
_KILL_THRESHOLD  = 0.30   # unified_score below this = bad cycle
_KILL_CYCLES_MAX = 3

_ACCOUNT_ROI_LOW  = 0.80   # below → budget × 0.50
_ACCOUNT_ROI_HIGH = 1.50   # above → budget × 1.30

# ── Connection cache ──────────────────────────────────────────────────────────

_CONN_CACHE: dict[str, sqlite3.Connection] = {}


def _db_path() -> str:
    env = os.environ.get("BUDGET_ALLOCATOR_DB")
    return env if env else str(_DEFAULT_DB)


def _get_conn() -> sqlite3.Connection:
    key = _db_path()
    if key in _CONN_CACHE:
        return _CONN_CACHE[key]
    if key != ":memory:":
        Path(key).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(key, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS budget_kill_cycles (
            content_id   TEXT PRIMARY KEY,
            bad_cycles   INTEGER DEFAULT 0,
            last_updated REAL    DEFAULT 0.0
        );
    """)
    conn.commit()
    _CONN_CACHE[key] = conn
    return conn


# ── Kill cycle tracking ───────────────────────────────────────────────────────

def record_kill_cycle(content_id: str) -> int:
    """Increment bad-cycle counter. Returns new count."""
    try:
        conn = _get_conn()
        with conn:
            conn.execute(
                """INSERT INTO budget_kill_cycles (content_id, bad_cycles, last_updated)
                   VALUES (?, 1, ?)
                   ON CONFLICT(content_id) DO UPDATE SET
                       bad_cycles   = bad_cycles + 1,
                       last_updated = excluded.last_updated""",
                (content_id, time.time())
            )
            row = conn.execute(
                "SELECT bad_cycles FROM budget_kill_cycles WHERE content_id = ?",
                (content_id,)
            ).fetchone()
            return int(row["bad_cycles"]) if row else 1
    except Exception:
        return 0


def reset_kill_cycles(content_id: str) -> None:
    """Reset bad-cycle counter when content shows recovery."""
    try:
        conn = _get_conn()
        with conn:
            conn.execute(
                "UPDATE budget_kill_cycles SET bad_cycles = 0, last_updated = ? "
                "WHERE content_id = ?",
                (time.time(), content_id)
            )
    except Exception:
        pass


def _get_bad_cycles(content_id: str) -> int:
    try:
        row = _get_conn().execute(
            "SELECT bad_cycles FROM budget_kill_cycles WHERE content_id = ?",
            (content_id,)
        ).fetchone()
        return int(row["bad_cycles"]) if row else 0
    except Exception:
        return 0


# ── Account ROI multiplier ────────────────────────────────────────────────────

def get_account_roi_multiplier(account_id: str,
                                accounts: list[dict[str, Any]] | None = None) -> float:
    """
    Returns budget multiplier based on account historical ROI.
    Looks up account_id in the provided accounts list (from execution_brain).
    Falls back to 1.0 if missing.
    """
    if not accounts:
        return 1.0
    for acct in accounts:
        if str(acct.get("account_id", "")) == str(account_id):
            roi = float(acct.get("historical_roi", 1.0))
            if roi < _ACCOUNT_ROI_LOW:
                return 0.50
            if roi > _ACCOUNT_ROI_HIGH:
                return 1.30
            return 1.0
    return 1.0


# ── Core allocator ────────────────────────────────────────────────────────────

def allocate(
    contents:     list[dict[str, Any]],
    total_budget: float,
    accounts:     list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Rank and allocate budget proportionally to unified_score.
    Each item must have 'unified_score' (set by execution_brain).
    Falls back to 'revenue_score' for backward compatibility.

    Kill gate (safety only): unified_score < 0.30 for 3 cycles → kill.
    """
    if not contents or total_budget <= 0:
        return []

    results:  list[dict[str, Any]] = []
    enriched: list[dict[str, Any]] = []

    for item in contents:
        cid       = str(item.get("content_id", ""))
        acct_id   = str(item.get("account_id", ""))
        u_score   = float(item.get("unified_score",
                          item.get("revenue_score", 0.5)))
        acct_mult = get_account_roi_multiplier(acct_id, accounts)

        # ── Kill gate (safety only) ───────────────────────────────────────────
        if u_score < _KILL_THRESHOLD:
            new_bad = record_kill_cycle(cid)
            if new_bad >= _KILL_CYCLES_MAX:
                results.append({
                    "content_id":       cid,
                    "unified_score":    u_score,
                    "budget_allocated": 0.0,
                    "action":           "kill",
                    "reason": (
                        f"unified_score={u_score:.3f}<{_KILL_THRESHOLD} "
                        f"for {new_bad} consecutive cycles"
                    ),
                })
                continue
        else:
            reset_kill_cycles(cid)

        enriched.append({**item,
                         "_cid":       cid,
                         "_u_score":   u_score,
                         "_acct_mult": acct_mult})

    if not enriched:
        return results

    # ── Proportional allocation by unified_score ──────────────────────────────
    total_score = sum(x["_u_score"] for x in enriched) or 1.0

    for item in enriched:
        cid     = item["_cid"]
        u_score = item["_u_score"]
        a_mult  = item["_acct_mult"]
        share   = (u_score / total_score) * total_budget
        budget  = round(share * a_mult, 4)

        reason_parts = [f"unified_score={u_score:.3f}",
                        f"share={u_score/total_score:.2%}"]
        if a_mult != 1.0:
            direction = "boosted" if a_mult > 1.0 else "reduced"
            reason_parts.append(f"account_{direction}={a_mult}x")

        results.append({
            "content_id":       cid,
            "unified_score":    u_score,
            "budget_allocated": budget,
            "action":           "allocated",
            "reason":           " | ".join(reason_parts),
        })

    results.sort(key=lambda r: (r["action"] == "kill", -r["budget_allocated"]))
    return results


# ── Summary helper ────────────────────────────────────────────────────────────

def summarize(allocation: list[dict[str, Any]]) -> dict[str, Any]:
    """Returns aggregate stats over an allocation result."""
    total     = sum(r["budget_allocated"] for r in allocation)
    by_action: dict[str, int] = {}
    for r in allocation:
        by_action[r["action"]] = by_action.get(r["action"], 0) + 1
    return {
        "total_budget_allocated": round(total, 4),
        "content_count":          len(allocation),
        "by_action":              by_action,
    }
