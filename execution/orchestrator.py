"""
execution/orchestrator.py — Top-level execution coordinator.

Connects:
    content_pipeline  → produces local .mp4
    publisher_playwright → uploads to TikTok / Facebook
    tracker_real      → generates and persists tracking link

Public API:
    run_execution_pipeline(candidate, credentials, *, headless, loop)
        → ExecutionResult

    run_execution_pipeline_sync(candidate, credentials, *, headless)
        → ExecutionResult   (synchronous wrapper for use in pipeline.py)

Safety:
    - Max 5 posts per page per day (enforced in SQLite rate limiter).
    - Min 60s between posts (enforced via sleep).
    - All failures are captured; orchestrator never raises.

Rate limit state: SQLite in data/exec_rate.db (RATE_LIMIT_DB env var).
Action log:       logs/execution.log (EXEC_LOG_PATH env var).
"""
from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import os
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from execution.content_pipeline  import process as _video_process
from execution.publisher_playwright import publish as _publish
from execution.tracker_real      import generate_tracking_link, get_stats

LOGGER = logging.getLogger("execution.orchestrator")

# ── Logging to file ───────────────────────────────────────────────────────────

_EXEC_LOG_PATH = Path(os.environ.get("EXEC_LOG_PATH", "logs/execution.log"))
_EXEC_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

_file_handler = logging.handlers.RotatingFileHandler(
    _EXEC_LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8",
)
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
)
logging.getLogger("execution").addHandler(_file_handler)
logging.getLogger("execution").setLevel(logging.INFO)

# ── Config ────────────────────────────────────────────────────────────────────

MAX_POSTS_PER_PAGE_PER_DAY: int   = 5
MIN_INTERVAL_BETWEEN_POSTS_S: int = 60
_RATE_DB_PATH = Path(os.environ.get("RATE_LIMIT_DB", "data/exec_rate.db"))

# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class ExecutionResult:
    status:        str          # "success" | "skipped" | "failed"
    platform:      str
    content_id:    str
    url:           str = ""
    tracking_link: str = ""
    error:         str = ""
    elapsed_s:     float = 0.0
    meta:          dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Rate limiter (SQLite-backed) ──────────────────────────────────────────────

_rate_lock = threading.Lock()
_rate_local = threading.local()


def _rate_conn() -> sqlite3.Connection:
    if not hasattr(_rate_local, "conn") or _rate_local.conn is None:
        _RATE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(_RATE_DB_PATH), check_same_thread=False, timeout=10)
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript("""
            CREATE TABLE IF NOT EXISTS post_log (
                page_id    TEXT NOT NULL,
                platform   TEXT NOT NULL,
                posted_at  REAL NOT NULL DEFAULT 0.0,
                content_id TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_post_log_page ON post_log(page_id, posted_at);
        """)
        con.commit()
        _rate_local.conn = con
    return _rate_local.conn


def _count_posts_today(page_id: str, platform: str) -> int:
    """Count posts for this page today (UTC day)."""
    day_start = (int(time.time()) // 86400) * 86400
    con = _rate_conn()
    row = con.execute(
        "SELECT COUNT(*) FROM post_log WHERE page_id=? AND platform=? AND posted_at>=?",
        (page_id, platform, float(day_start)),
    ).fetchone()
    return row[0] if row else 0


def _last_post_ts(page_id: str, platform: str) -> float:
    """Return timestamp of the last post for this page."""
    con = _rate_conn()
    row = con.execute(
        "SELECT MAX(posted_at) FROM post_log WHERE page_id=? AND platform=?",
        (page_id, platform),
    ).fetchone()
    return float(row[0]) if row and row[0] else 0.0


def _record_post(page_id: str, platform: str, content_id: str) -> None:
    con = _rate_conn()
    con.execute(
        "INSERT INTO post_log (page_id, platform, posted_at, content_id) VALUES (?,?,?,?)",
        (page_id, platform, time.time(), content_id),
    )
    con.commit()


def _check_rate_limit(page_id: str, platform: str) -> tuple[bool, str]:
    """
    Check and enforce rate limits.

    Returns (allowed, reason).
    """
    with _rate_lock:
        count = _count_posts_today(page_id, platform)
        if count >= MAX_POSTS_PER_PAGE_PER_DAY:
            return False, f"rate_limit_daily: {count}/{MAX_POSTS_PER_PAGE_PER_DAY} posts today"

        last_ts = _last_post_ts(page_id, platform)
        elapsed = time.time() - last_ts
        if last_ts > 0 and elapsed < MIN_INTERVAL_BETWEEN_POSTS_S:
            wait = MIN_INTERVAL_BETWEEN_POSTS_S - elapsed
            return False, f"rate_limit_interval: wait {wait:.0f}s more"

    return True, "ok"


# ── Core orchestrator ─────────────────────────────────────────────────────────

async def _run_async(
    candidate:   dict[str, Any],
    credentials: dict[str, str],
    headless:    bool,
) -> ExecutionResult:
    """
    Internal async execution pipeline.

    candidate keys (required):
        content_id  : str
        mode        : "reup" | "remark"
        platform    : "tiktok" | "facebook"
        source_url  : str
        niche       : str
        caption     : str   (optional — auto-generated if missing)
        hashtags    : list[str]   (optional)
        product_id  : str   (optional — used in CTA)

    credentials keys (platform-specific):
        TikTok:   {"username": ..., "password": ..., "account_id": ...}
        Facebook: {"email": ...,    "password": ..., "account_id": ...}
    """
    t0 = time.monotonic()

    content_id = candidate.get("content_id", f"exec_{int(time.time())}")
    platform   = candidate.get("platform", "tiktok").lower()
    page_id    = credentials.get("account_id", content_id)

    LOGGER.info(
        "orchestrator_start content_id=%s platform=%s mode=%s",
        content_id, platform, candidate.get("mode", "reup"),
    )

    # ── Safety: rate limit check ──────────────────────────────────────────────
    allowed, reason = _check_rate_limit(page_id, platform)
    if not allowed:
        LOGGER.info("orchestrator_skipped content_id=%s reason=%s", content_id, reason)
        return ExecutionResult(
            status="skipped", platform=platform, content_id=content_id,
            error=reason, elapsed_s=round(time.monotonic() - t0, 2),
        )

    # ── Step 1: Video processing ──────────────────────────────────────────────
    pipeline_result = await asyncio.get_event_loop().run_in_executor(
        None, _video_process, candidate,
    )

    if not pipeline_result.success:
        err = f"content_pipeline_failed: {pipeline_result.error}"
        LOGGER.warning("orchestrator_pipeline_error content_id=%s error=%s", content_id, err)
        return ExecutionResult(
            status="failed", platform=platform, content_id=content_id,
            error=err, elapsed_s=round(time.monotonic() - t0, 2),
        )

    LOGGER.info(
        "orchestrator_video_ready content_id=%s path=%s",
        content_id, pipeline_result.video_path,
    )

    # ── Step 2: Generate tracking link ────────────────────────────────────────
    tracking_link = generate_tracking_link(content_id, page_id)

    # ── Step 3: Publish ───────────────────────────────────────────────────────
    caption   = candidate.get("caption") or f"#{candidate.get('niche', 'viral')} 🔥 link in bio"
    hashtags  = candidate.get("hashtags") or [candidate.get("niche", "viral"), "trending"]
    # Embed a short tracking reference in the caption
    tracking_note = f"[{content_id[:12]}]"

    publish_result = await _publish(
        content_id    = content_id,
        platform      = platform,
        video_path    = pipeline_result.video_path,
        caption       = caption,
        credentials   = credentials,
        hashtags      = hashtags,
        tracking_code = tracking_note,
        headless      = headless,
    )

    elapsed = round(time.monotonic() - t0, 2)

    if not publish_result.success:
        err = f"publish_failed: {publish_result.error}"
        LOGGER.warning("orchestrator_publish_error content_id=%s error=%s", content_id, err)
        return ExecutionResult(
            status="failed", platform=platform, content_id=content_id,
            tracking_link=tracking_link, error=err, elapsed_s=elapsed,
        )

    # ── Record post in rate limiter ───────────────────────────────────────────
    _record_post(page_id, platform, content_id)

    LOGGER.info(
        "orchestrator_success content_id=%s platform=%s url=%s tracking=%s elapsed=%.1fs",
        content_id, platform, publish_result.url, tracking_link, elapsed,
    )

    return ExecutionResult(
        status="success",
        platform=platform,
        content_id=content_id,
        url=publish_result.url,
        tracking_link=tracking_link,
        elapsed_s=elapsed,
        meta={
            "attempts":    publish_result.attempts,
            "video_path":  pipeline_result.video_path,
            "tracker_stats": get_stats(content_id),
        },
    )


def run_execution_pipeline(
    candidate:   dict[str, Any],
    credentials: dict[str, str],
    *,
    headless:    bool = True,
    loop:        asyncio.AbstractEventLoop | None = None,
) -> ExecutionResult:
    """
    Synchronous entry point.

    Runs the full execution pipeline (video → publish → track).
    Safe to call from a synchronous context (pipeline.py).

    Returns ExecutionResult.  Never raises.
    """
    try:
        _loop = loop or asyncio.new_event_loop()
        result = _loop.run_until_complete(
            _run_async(candidate, credentials, headless)
        )
        return result
    except Exception as exc:
        LOGGER.error("orchestrator_unhandled_error error=%s", exc)
        return ExecutionResult(
            status="failed",
            platform=candidate.get("platform", "unknown"),
            content_id=candidate.get("content_id", "unknown"),
            error=str(exc),
        )


# ── Optional n8n webhook trigger ──────────────────────────────────────────────

def trigger_n8n_webhook(
    webhook_url: str,
    payload:     dict[str, Any],
    timeout_s:   int = 10,
) -> bool:
    """
    POST a JSON payload to an n8n webhook URL.

    Use this to trigger downstream n8n workflows (e.g. CRM update,
    Slack notification, analytics push).

    Returns True on HTTP 200/201. Never raises.
    """
    import urllib.request
    try:
        data = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            webhook_url,
            data    = data,
            headers = {"Content-Type": "application/json"},
            method  = "POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:   # noqa: S310
            ok = resp.status in (200, 201)
            LOGGER.info("n8n_webhook_sent status=%d url=%s", resp.status, webhook_url)
            return ok
    except Exception as exc:
        LOGGER.warning("n8n_webhook_error url=%s error=%s", webhook_url, exc)
        return False
