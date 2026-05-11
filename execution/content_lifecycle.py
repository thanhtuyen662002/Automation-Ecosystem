"""
execution/content_lifecycle.py — Content Lifecycle Engine.

Every piece of content moves through discrete states based on real
performance data. This engine drives posting frequency, mutation
intensity, and retirement decisions — replacing gut-feel with data.

States:
    TESTING    → new content, unknown performance, limited deployment
    WINNING    → above-threshold performance, scale up posting
    SCALING    → winners being amplified across accounts/niches
    SATURATED  → diminishing returns, reduce frequency
    RECYCLE    → retired but mutatable (high-performing skeleton)
    DEAD       → fully retired, do not repost

Transitions (automatic via advance()):
    TESTING  → WINNING    : viral_score > WIN_THRESHOLD for N cycles
    TESTING  → DEAD       : viral_score < FAIL_THRESHOLD after M cycles
    WINNING  → SCALING    : stable wins for SCALE_CYCLES consecutive cycles
    WINNING  → SATURATED  : engagement_rate declining for DECAY_CYCLES
    SCALING  → SATURATED  : velocity drop > VELOCITY_DROP_PCT
    SATURATED → RECYCLE   : content structure still strong (hook_score > 0.6)
    SATURATED → DEAD      : hook_score low, no recovery signal

Public API:
    get_state(content_id)                        → LifecycleState
    advance(content_id, latest_metrics)          → LifecycleState
    get_posting_params(content_id)               → PostingParams
    get_mutation_intensity(content_id)           → float   [0, 1]
    get_scaling_candidates(niche, limit)         → list[dict]
    get_recycle_candidates(niche, limit)         → list[dict]
    get_lifecycle_report(content_id)             → dict
    reset_lifecycle(content_id)                  # testing only
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("execution.content_lifecycle")

# ── State enum ────────────────────────────────────────────────────────────────

class LifecycleState(str, Enum):
    TESTING   = "testing"
    WINNING   = "winning"
    SCALING   = "scaling"
    SATURATED = "saturated"
    RECYCLE   = "recycle"
    DEAD      = "dead"


# ── Thresholds (overridable via env) ─────────────────────────────────────────

_WIN_THRESHOLD    = float(os.environ.get("LC_WIN_THRESHOLD",    "0.55"))
_FAIL_THRESHOLD   = float(os.environ.get("LC_FAIL_THRESHOLD",   "0.15"))
_WIN_CYCLES       = int(os.environ.get("LC_WIN_CYCLES",          "3"))    # cycles above WIN to confirm
_FAIL_CYCLES      = int(os.environ.get("LC_FAIL_CYCLES",         "5"))    # cycles below FAIL to kill
_SCALE_CYCLES     = int(os.environ.get("LC_SCALE_CYCLES",        "5"))    # stable wins to scale
_DECAY_CYCLES     = int(os.environ.get("LC_DECAY_CYCLES",        "4"))    # declining to saturate
_VELOCITY_DROP    = float(os.environ.get("LC_VELOCITY_DROP",    "0.40"))  # 40% drop triggers saturation
_RECYCLE_HOOK_MIN = float(os.environ.get("LC_RECYCLE_HOOK_MIN", "0.60"))  # min hook_score for recycle

# ── Posting params by state ──────────────────────────────────────────────────

@dataclass
class PostingParams:
    posts_per_day:        int   = 1
    mutation_intensity:   float = 0.0    # 0 = exact repost, 1 = full mutation
    caption_variation:    bool  = False
    cross_account:        bool  = False   # post on multiple accounts
    state:                str   = "testing"

_STATE_POSTING_PARAMS: dict[LifecycleState, PostingParams] = {
    LifecycleState.TESTING:   PostingParams(posts_per_day=1, mutation_intensity=0.1,  caption_variation=False, cross_account=False),
    LifecycleState.WINNING:   PostingParams(posts_per_day=2, mutation_intensity=0.25, caption_variation=True,  cross_account=False),
    LifecycleState.SCALING:   PostingParams(posts_per_day=4, mutation_intensity=0.35, caption_variation=True,  cross_account=True),
    LifecycleState.SATURATED: PostingParams(posts_per_day=1, mutation_intensity=0.50, caption_variation=True,  cross_account=False),
    LifecycleState.RECYCLE:   PostingParams(posts_per_day=1, mutation_intensity=0.80, caption_variation=True,  cross_account=True),
    LifecycleState.DEAD:      PostingParams(posts_per_day=0, mutation_intensity=0.0,  caption_variation=False, cross_account=False),
}


# ── Schema ────────────────────────────────────────────────────────────────────

_DEFAULT_DB = Path("data") / "content_lifecycle.db"

def _db_path() -> Path:
    env = os.environ.get("LIFECYCLE_DB")
    return Path(env) if env else _DEFAULT_DB

_DDL = """
CREATE TABLE IF NOT EXISTS lifecycle (
    content_id          TEXT PRIMARY KEY,
    niche               TEXT NOT NULL DEFAULT '',
    platform            TEXT NOT NULL DEFAULT 'tiktok',
    state               TEXT NOT NULL DEFAULT 'testing',
    viral_score_ema     REAL NOT NULL DEFAULT 0.0,
    hook_score          REAL NOT NULL DEFAULT 0.5,
    peak_viral_score    REAL NOT NULL DEFAULT 0.0,
    velocity_ema        REAL NOT NULL DEFAULT 0.0,
    prev_velocity       REAL NOT NULL DEFAULT 0.0,
    cycles_in_state     INTEGER NOT NULL DEFAULT 0,
    consecutive_wins    INTEGER NOT NULL DEFAULT 0,
    consecutive_fails   INTEGER NOT NULL DEFAULT 0,
    total_cycles        INTEGER NOT NULL DEFAULT 0,
    state_changed_at    REAL NOT NULL DEFAULT 0.0,
    created_at          REAL NOT NULL DEFAULT 0.0,
    last_updated        REAL NOT NULL DEFAULT 0.0,
    history_json        TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_lc_state  ON lifecycle(state, niche);
CREATE INDEX IF NOT EXISTS idx_lc_viral  ON lifecycle(viral_score_ema DESC);
"""

_local     = threading.local()
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


# ── State machine ─────────────────────────────────────────────────────────────

def _ensure(content_id: str, niche: str = "", platform: str = "tiktok") -> None:
    _exec(
        "INSERT OR IGNORE INTO lifecycle (content_id, niche, platform, state,"
        " created_at, state_changed_at, last_updated)"
        " VALUES (?,?,?,?,?,?,?)",
        (content_id, niche, platform, LifecycleState.TESTING.value,
         time.time(), time.time(), time.time()),
    )


def _transition(content_id: str, new_state: LifecycleState, reason: str) -> None:
    """Perform a state transition, logging it into history_json."""
    row = _conn().execute(
        "SELECT state, history_json FROM lifecycle WHERE content_id=?",
        (content_id,)
    ).fetchone()
    if not row:
        return
    history = json.loads(row["history_json"] or "[]")
    history.append({
        "from":  row["state"],
        "to":    new_state.value,
        "reason": reason,
        "at":    round(time.time()),
    })
    history = history[-20:]  # keep last 20 transitions
    _exec(
        "UPDATE lifecycle SET state=?, cycles_in_state=0, state_changed_at=?,"
        " last_updated=?, history_json=? WHERE content_id=?",
        (new_state.value, time.time(), time.time(), json.dumps(history), content_id),
    )
    LOGGER.info("lifecycle_transition content_id=%s %s→%s reason=%s",
                content_id, row["state"], new_state.value, reason)


def get_state(content_id: str, niche: str = "", platform: str = "tiktok") -> LifecycleState:
    """Return current lifecycle state. Creates TESTING record if new."""
    _ensure(content_id, niche, platform)
    try:
        row = _conn().execute(
            "SELECT state FROM lifecycle WHERE content_id=?", (content_id,)
        ).fetchone()
        return LifecycleState(row["state"]) if row else LifecycleState.TESTING
    except Exception:
        return LifecycleState.TESTING


def advance(
    content_id:     str,
    latest_metrics: dict[str, Any],
    niche:          str = "",
    platform:       str = "tiktok",
) -> LifecycleState:
    """
    Feed new performance data and advance state machine if warranted.

    latest_metrics keys:
        viral_score     float [0,1]  — from trend_filter or safe_metrics
        view_velocity   float        — views/hour
        engagement_rate float [0,1]
        hook_score      float [0,1]  — from hook_optimizer

    Returns new state.
    """
    _ensure(content_id, niche, platform)

    viral  = float(latest_metrics.get("viral_score",     0.0))
    vel    = float(latest_metrics.get("view_velocity",   0.0))
    eng    = float(latest_metrics.get("engagement_rate", 0.0))
    hook   = float(latest_metrics.get("hook_score",      0.5))

    try:
        row = _conn().execute(
            "SELECT * FROM lifecycle WHERE content_id=?", (content_id,)
        ).fetchone()
        if not row:
            return LifecycleState.TESTING

        cur_state     = LifecycleState(row["state"])
        alpha         = 0.30   # EMA weight

        # Update EMAs
        new_viral_ema = row["viral_score_ema"] * (1 - alpha) + viral * alpha
        new_vel_ema   = row["velocity_ema"]    * (1 - alpha) + vel   * alpha
        peak          = max(float(row["peak_viral_score"]), new_viral_ema)
        c_wins        = int(row["consecutive_wins"])
        c_fails       = int(row["consecutive_fails"])
        cycles        = int(row["cycles_in_state"]) + 1
        total         = int(row["total_cycles"]) + 1

        # Update counters
        if new_viral_ema >= _WIN_THRESHOLD:
            c_wins  += 1
            c_fails  = 0
        elif new_viral_ema <= _FAIL_THRESHOLD:
            c_fails += 1
            c_wins   = 0
        else:
            c_wins  = max(0, c_wins  - 1)
            c_fails = max(0, c_fails - 1)

        # Velocity drop detection
        prev_vel = float(row["prev_velocity"]) or new_vel_ema
        vel_drop  = (prev_vel - new_vel_ema) / max(0.01, prev_vel)

        # Write updated metrics
        _exec(
            "UPDATE lifecycle SET viral_score_ema=?, velocity_ema=?, prev_velocity=?,"
            " peak_viral_score=?, hook_score=?, consecutive_wins=?, consecutive_fails=?,"
            " cycles_in_state=?, total_cycles=?, last_updated=?"
            " WHERE content_id=?",
            (new_viral_ema, new_vel_ema, new_vel_ema, peak, hook,
             c_wins, c_fails, cycles, total, time.time(), content_id),
        )

        # ── State machine transitions ──────────────────────────────────────
        new_state = cur_state

        if cur_state == LifecycleState.TESTING:
            if c_wins >= _WIN_CYCLES:
                new_state = LifecycleState.WINNING
                reason    = f"viral_ema={new_viral_ema:.3f}>={_WIN_THRESHOLD} for {c_wins} cycles"
            elif c_fails >= _FAIL_CYCLES:
                new_state = LifecycleState.DEAD
                reason    = f"viral_ema={new_viral_ema:.3f}<={_FAIL_THRESHOLD} for {c_fails} cycles"
            else:
                reason = ""

        elif cur_state == LifecycleState.WINNING:
            if cycles >= _SCALE_CYCLES and c_wins >= _SCALE_CYCLES:
                new_state = LifecycleState.SCALING
                reason    = f"stable_wins={c_wins}>={_SCALE_CYCLES}"
            elif c_fails >= _DECAY_CYCLES:
                new_state = LifecycleState.SATURATED
                reason    = f"declining_for={c_fails} cycles"
            else:
                reason = ""

        elif cur_state == LifecycleState.SCALING:
            if vel_drop >= _VELOCITY_DROP:
                new_state = LifecycleState.SATURATED
                reason    = f"velocity_drop={vel_drop:.1%}>={_VELOCITY_DROP:.1%}"
            else:
                reason = ""

        elif cur_state == LifecycleState.SATURATED:
            if hook >= _RECYCLE_HOOK_MIN and new_viral_ema < _FAIL_THRESHOLD:
                new_state = LifecycleState.RECYCLE
                reason    = f"hook={hook:.2f}>={_RECYCLE_HOOK_MIN} but viral low"
            elif new_viral_ema <= _FAIL_THRESHOLD and c_fails >= _FAIL_CYCLES:
                new_state = LifecycleState.DEAD
                reason    = f"no_recovery viral={new_viral_ema:.3f}"
            else:
                reason = ""

        elif cur_state == LifecycleState.RECYCLE:
            if new_viral_ema >= _WIN_THRESHOLD:
                new_state = LifecycleState.WINNING   # recycled content revived
                reason    = f"revival viral={new_viral_ema:.3f}"
            elif c_fails >= _FAIL_CYCLES * 2:
                new_state = LifecycleState.DEAD
                reason    = f"recycle_failed fails={c_fails}"
            else:
                reason = ""

        else:  # DEAD — no automatic resurrection
            reason = ""

        if new_state != cur_state:
            _transition(content_id, new_state, reason)

        return new_state

    except Exception as exc:
        LOGGER.warning("lifecycle_advance_error content_id=%s error=%s", content_id, exc)
        return LifecycleState.TESTING


# ── Query API ─────────────────────────────────────────────────────────────────

def get_posting_params(
    content_id: str,
    niche:      str = "",
    platform:   str = "tiktok",
) -> PostingParams:
    """Return recommended posting parameters for the current state."""
    state  = get_state(content_id, niche, platform)
    params = _STATE_POSTING_PARAMS[state]
    params.state = state.value
    return params


def get_mutation_intensity(content_id: str) -> float:
    """
    Return mutation intensity [0, 1].
    Higher = more aggressive content variation on repost.
    """
    params = get_posting_params(content_id)
    return params.mutation_intensity


def get_scaling_candidates(niche: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return content in WINNING or SCALING state, sorted by viral_score_ema desc."""
    try:
        rows = _conn().execute(
            "SELECT * FROM lifecycle WHERE state IN (?,?) AND niche=?"
            " ORDER BY viral_score_ema DESC LIMIT ?",
            (LifecycleState.WINNING.value, LifecycleState.SCALING.value, niche, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_recycle_candidates(niche: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return RECYCLE-state content sorted by hook_score (best templates)."""
    try:
        rows = _conn().execute(
            "SELECT * FROM lifecycle WHERE state=? AND niche=?"
            " ORDER BY hook_score DESC, peak_viral_score DESC LIMIT ?",
            (LifecycleState.RECYCLE.value, niche, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_lifecycle_report(content_id: str) -> dict[str, Any]:
    try:
        row = _conn().execute(
            "SELECT * FROM lifecycle WHERE content_id=?", (content_id,)
        ).fetchone()
        if not row:
            return {"content_id": content_id, "state": "unknown"}
        d = dict(row)
        try:
            d["history"] = json.loads(d.pop("history_json", "[]"))
        except Exception:
            d["history"] = []
        d["posting_params"] = get_posting_params(content_id).__dict__
        return d
    except Exception as exc:
        return {"content_id": content_id, "error": str(exc)}


def get_all_by_state(state: LifecycleState, limit: int = 50) -> list[dict[str, Any]]:
    try:
        rows = _conn().execute(
            "SELECT * FROM lifecycle WHERE state=?"
            " ORDER BY viral_score_ema DESC LIMIT ?",
            (state.value, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_portfolio_summary() -> dict[str, Any]:
    """Count of content per state for dashboard/portfolio use."""
    try:
        rows = _conn().execute(
            "SELECT state, COUNT(*) as cnt, AVG(viral_score_ema) as avg_viral"
            " FROM lifecycle GROUP BY state"
        ).fetchall()
        return {r["state"]: {"count": r["cnt"], "avg_viral": round(r["avg_viral"] or 0, 3)}
                for r in rows}
    except Exception:
        return {}


def reset_lifecycle(content_id: str) -> None:
    """Reset to TESTING state. Testing only."""
    try:
        _exec(
            "UPDATE lifecycle SET state=?, cycles_in_state=0, consecutive_wins=0,"
            " consecutive_fails=0, viral_score_ema=0, velocity_ema=0,"
            " history_json='[]', state_changed_at=?, last_updated=?"
            " WHERE content_id=?",
            (LifecycleState.TESTING.value, time.time(), time.time(), content_id),
        )
    except Exception:
        pass
