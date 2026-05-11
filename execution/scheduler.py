"""
execution/scheduler.py — Publishing job scheduler.

SQLite-backed queue. Dispatches approved jobs at optimal time windows,
enforcing per-page rate limits and minimum post intervals.

Optimal windows (UTC): 07-09h, 12-13h, 19-22h (peak engagement).

Public API:
    enqueue(candidate, account_id, priority, not_before, approval_id) → str (job_id)
    tick()                   → list[JobResult]   (run due jobs, call periodically)
    get_queue_status()       → dict
    get_job(job_id)          → dict | None
    cancel_job(job_id)       → bool
    reset_scheduler()        # testing only

Config (env vars):
    SCHEDULER_DB          path to SQLite DB (default: data/scheduler.db)
    MAX_POSTS_PER_DAY     per account per day (default: 5)
    MIN_POST_INTERVAL_S   seconds between posts (default: 3600)
    MAX_JOBS_PER_TICK     max dispatches per tick() call (default: 3)
    POST_JITTER_MINUTES   random delay added to window (default: 15)
"""
from __future__ import annotations

import json
import logging
import os
import random
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("execution.scheduler")

# ── Config ────────────────────────────────────────────────────────────────────

_DEFAULT_DB = Path("data") / "scheduler.db"

def _db_path() -> Path:
    env = os.environ.get("SCHEDULER_DB")
    return Path(env) if env else _DEFAULT_DB

MAX_POSTS_PER_PAGE_PER_DAY: int = int(os.environ.get("MAX_POSTS_PER_DAY", "5"))
MIN_INTERVAL_S:             int = int(os.environ.get("MIN_POST_INTERVAL_S", "3600"))
MAX_JOBS_PER_TICK:          int = int(os.environ.get("MAX_JOBS_PER_TICK", "3"))
_JITTER_MIN:                int = int(os.environ.get("POST_JITTER_MINUTES", "15"))

_PEAK_HOURS = {7, 8, 12, 19, 20, 21}
_GOOD_HOURS = {9, 10, 11, 13, 14, 15, 16, 17}

# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id         TEXT PRIMARY KEY,
    approval_id    TEXT NOT NULL DEFAULT '',
    account_id     TEXT NOT NULL DEFAULT '',
    platform       TEXT NOT NULL DEFAULT 'tiktok',
    candidate_json TEXT NOT NULL DEFAULT '{}',
    priority       INTEGER NOT NULL DEFAULT 5,
    status         TEXT NOT NULL DEFAULT 'queued',
    not_before     REAL NOT NULL DEFAULT 0.0,
    created_at     REAL NOT NULL DEFAULT 0.0,
    dispatched_at  REAL NOT NULL DEFAULT 0.0,
    completed_at   REAL NOT NULL DEFAULT 0.0,
    error          TEXT NOT NULL DEFAULT '',
    result_json    TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS page_post_log (
    account_id TEXT NOT NULL,
    platform   TEXT NOT NULL,
    posted_at  REAL NOT NULL DEFAULT 0.0,
    job_id     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_jobs_status   ON jobs(status, not_before);
CREATE INDEX IF NOT EXISTS idx_page_post_log ON page_post_log(account_id, platform, posted_at);
"""

_local     = threading.local()
_init_lock = threading.Lock()
_tick_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        db = _db_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(db), check_same_thread=False, timeout=15)
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


# ── Timing ────────────────────────────────────────────────────────────────────

def _next_peak_window(from_ts: float | None = None) -> float:
    """Return timestamp of next peak posting window + random jitter."""
    import datetime
    base = from_ts or time.time()
    now  = datetime.datetime.utcfromtimestamp(base)
    h    = now.hour
    if h in _PEAK_HOURS or h in _GOOD_HOURS:
        return base + random.randint(0, _JITTER_MIN * 60)
    for offset in range(1, 25):
        nh = (h + offset) % 24
        if nh in _PEAK_HOURS:
            target = now.replace(minute=0, second=0, microsecond=0)
            target += datetime.timedelta(hours=offset)
            return target.timestamp() + random.randint(0, _JITTER_MIN * 60)
    return base + 4 * 3600


def _daily_post_count(account_id: str, platform: str) -> int:
    day_start = (int(time.time()) // 86400) * 86400
    row = _conn().execute(
        "SELECT COUNT(*) FROM page_post_log WHERE account_id=? AND platform=? AND posted_at>=?",
        (account_id, platform, float(day_start)),
    ).fetchone()
    return int(row[0]) if row else 0


def _last_post_ts(account_id: str, platform: str) -> float:
    row = _conn().execute(
        "SELECT MAX(posted_at) FROM page_post_log WHERE account_id=? AND platform=?",
        (account_id, platform),
    ).fetchone()
    return float(row[0]) if row and row[0] else 0.0


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class JobResult:
    job_id:     str
    status:     str
    platform:   str
    account_id: str
    content_id: str = ""
    url:        str = ""
    error:      str = ""
    meta:       dict[str, Any] = field(default_factory=dict)


# ── Public API ────────────────────────────────────────────────────────────────

def enqueue(
    candidate:   dict[str, Any],
    account_id:  str = "",
    *,
    priority:    int = 5,
    not_before:  float | None = None,
    approval_id: str = "",
) -> str:
    """Queue a publishing job. Returns job_id."""
    job_id   = str(uuid.uuid4())
    platform = candidate.get("platform", "tiktok")
    sched_ts = not_before or _next_peak_window()
    try:
        _exec(
            "INSERT INTO jobs (job_id, approval_id, account_id, platform, candidate_json,"
            " priority, status, not_before, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (job_id, approval_id, account_id, platform,
             json.dumps(candidate), priority, "queued", sched_ts, time.time()),
        )
        LOGGER.info("scheduler_enqueue job_id=%s platform=%s not_before=%.0f",
                    job_id, platform, sched_ts)
    except Exception as exc:
        LOGGER.warning("scheduler_enqueue_error error=%s", exc)
    return job_id


def tick() -> list[JobResult]:
    """
    Process all due queued jobs (up to MAX_JOBS_PER_TICK).
    Call this periodically from your automation loop.
    """
    results: list[JobResult] = []
    with _tick_lock:
        now = time.time()
        try:
            rows = _conn().execute(
                "SELECT * FROM jobs WHERE status='queued' AND not_before<=?"
                " ORDER BY priority ASC, not_before ASC LIMIT ?",
                (now, MAX_JOBS_PER_TICK),
            ).fetchall()
        except Exception as exc:
            LOGGER.warning("scheduler_tick_query_error error=%s", exc)
            return results

        for row in rows:
            job_id     = row["job_id"]
            account_id = row["account_id"]
            platform   = row["platform"]

            # Daily rate limit
            if _daily_post_count(account_id, platform) >= MAX_POSTS_PER_PAGE_PER_DAY:
                import datetime
                tmr = (datetime.datetime.utcnow().replace(hour=0, minute=0, second=0)
                       + datetime.timedelta(days=1))
                _exec("UPDATE jobs SET not_before=? WHERE job_id=?",
                      (_next_peak_window(tmr.timestamp()), job_id))
                LOGGER.info("scheduler_reschedule_tomorrow job_id=%s", job_id)
                continue

            # Minimum interval
            last_ts = _last_post_ts(account_id, platform)
            gap = now - last_ts
            if last_ts > 0 and gap < MIN_INTERVAL_S:
                wait = MIN_INTERVAL_S - gap + random.randint(0, 300)
                _exec("UPDATE jobs SET not_before=? WHERE job_id=?", (now + wait, job_id))
                LOGGER.debug("scheduler_interval_wait job_id=%s wait_s=%.0f", job_id, wait)
                continue

            # Mark running
            _exec("UPDATE jobs SET status='running', dispatched_at=? WHERE job_id=?",
                  (now, job_id))

            candidate: dict[str, Any] = {}
            try:
                candidate = json.loads(row["candidate_json"])
            except Exception:
                pass

            creds: dict[str, Any] = {}
            try:
                from execution.account_manager import get_account
                creds = get_account(account_id) or {}
            except Exception:
                pass

            jr = JobResult(job_id=job_id, status="failed",
                           platform=platform, account_id=account_id)
            try:
                from execution.orchestrator import run_execution_pipeline
                res = run_execution_pipeline(candidate, creds)
                jr.status     = res.status
                jr.content_id = res.content_id
                jr.url        = res.url
                jr.error      = res.error
                jr.meta       = res.meta
                if res.status == "success":
                    _exec("INSERT INTO page_post_log (account_id, platform, posted_at, job_id)"
                          " VALUES (?,?,?,?)", (account_id, platform, time.time(), job_id))
                    try:
                        from execution.account_manager import mark_healthy
                        mark_healthy(account_id)
                    except Exception:
                        pass
                elif res.status == "failed":
                    try:
                        from execution.account_manager import mark_failed
                        mark_failed(account_id, res.error)
                    except Exception:
                        pass
            except Exception as exc:
                jr.error = str(exc)
                LOGGER.warning("scheduler_dispatch_error job_id=%s error=%s", job_id, exc)

            final = jr.status if jr.status in ("success", "failed", "skipped") else "failed"
            _exec("UPDATE jobs SET status=?, completed_at=?, error=?, result_json=?"
                  " WHERE job_id=?",
                  (final, time.time(), jr.error[:1000], json.dumps({"url": jr.url}), job_id))
            LOGGER.info("scheduler_done job_id=%s status=%s url=%s", job_id, jr.status, jr.url)
            results.append(jr)
    return results


def get_queue_status() -> dict[str, Any]:
    try:
        con = _conn()
        counts = {r["status"]: r["cnt"]
                  for r in con.execute("SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status")}
        nxt = con.execute("SELECT MIN(not_before) FROM jobs WHERE status='queued'").fetchone()
        return {"counts": counts, "next_job_ts": float(nxt[0]) if nxt and nxt[0] else None,
                "max_per_day": MAX_POSTS_PER_PAGE_PER_DAY, "min_interval_s": MIN_INTERVAL_S}
    except Exception as exc:
        return {"error": str(exc)}


def get_job(job_id: str) -> dict[str, Any] | None:
    try:
        row = _conn().execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["candidate"] = json.loads(d.pop("candidate_json", "{}"))
        d["result"]    = json.loads(d.pop("result_json", "{}"))
        return d
    except Exception:
        return None


def cancel_job(job_id: str) -> bool:
    try:
        cur = _exec("UPDATE jobs SET status='cancelled' WHERE job_id=? AND status='queued'",
                    (job_id,))
        return cur.rowcount > 0
    except Exception:
        return False


def reset_scheduler() -> None:
    try:
        con = _conn()
        con.executescript("DELETE FROM jobs; DELETE FROM page_post_log;")
        con.commit()
    except Exception:
        pass
    if hasattr(_local, "conn"):
        try:
            _local.conn.close()
        except Exception:
            pass
        _local.conn = None
