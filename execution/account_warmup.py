"""
execution/account_warmup.py — Account Warmup System.

New accounts must go through a warmup phase before posting to avoid
immediate detection and bans. During warmup, accounts only browse and
engage (like, follow, view) — never post.

Warmup stages:
    STAGE 0 (day 0-2):   Browse only — visit profile, watch videos, no interaction
    STAGE 1 (day 3-5):   Browse + like a few videos
    STAGE 2 (day 6-9):   Browse + like + follow + comment (light)
    STAGE 3 (day 10+):   Posting enabled (gradual: 1→2→5 posts/day)

SQLite-backed. Integrates with account_manager (mark_failed/mark_healthy).
Integrates with lifecycle_engine: posts to same niches the account "follows".

Public API:
    get_warmup_stage(account_id)            → int (0-3)
    is_ready_to_post(account_id)           → bool
    run_warmup_session(account, headless)  → WarmupResult
    advance_stage_if_ready(account_id)    → int (new stage)
    get_warmup_status(account_id)          → dict
    reset_warmup(account_id)              # testing only

Config:
    WARMUP_DB   — SQLite path (default: data/warmup.db)
    WARMUP_ENABLED — "1" to enable (default: "1")
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("execution.account_warmup")

# ── Config ────────────────────────────────────────────────────────────────────

_DEFAULT_DB     = Path("data") / "warmup.db"
WARMUP_ENABLED  = os.environ.get("WARMUP_ENABLED", "1") == "1"

# Days required to graduate from each stage
_STAGE_DAYS: dict[int, int] = {0: 2, 1: 3, 2: 4}   # stage → min days before next
_POST_STAGE  = 3   # stage at which posting is allowed

# Warmup browsing targets (safe, high-traffic pages)
_BROWSE_TARGETS_TIKTOK = [
    "https://www.tiktok.com/",
    "https://www.tiktok.com/explore",
    "https://www.tiktok.com/trending",
]
_BROWSE_TARGETS_FACEBOOK = [
    "https://www.facebook.com/",
    "https://www.facebook.com/reels/",
    "https://www.facebook.com/watch/",
]

# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS warmup (
    account_id      TEXT PRIMARY KEY,
    platform        TEXT NOT NULL DEFAULT 'tiktok',
    stage           INTEGER NOT NULL DEFAULT 0,
    stage_started   REAL NOT NULL DEFAULT 0.0,
    created_at      REAL NOT NULL DEFAULT 0.0,
    last_session    REAL NOT NULL DEFAULT 0.0,
    session_count   INTEGER NOT NULL DEFAULT 0,
    actions_today   INTEGER NOT NULL DEFAULT 0,
    actions_day     INTEGER NOT NULL DEFAULT 0,
    notes           TEXT NOT NULL DEFAULT ''
);
"""

_local     = threading.local()
_init_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        db = Path(os.environ.get("WARMUP_DB", str(_DEFAULT_DB)))
        db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(db), check_same_thread=False, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
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


def _ensure_account(account_id: str, platform: str = "tiktok") -> None:
    """Create warmup record for account if it doesn't exist."""
    _exec(
        "INSERT OR IGNORE INTO warmup (account_id, platform, stage, stage_started, created_at)"
        " VALUES (?,?,0,?,?)",
        (account_id, platform, time.time(), time.time()),
    )


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class WarmupResult:
    account_id:   str
    platform:     str
    stage:        int
    actions_done: int       = 0
    success:      bool      = True
    error:        str       = ""
    actions:      list[str] = field(default_factory=list)


# ── Public API ────────────────────────────────────────────────────────────────

def get_warmup_stage(account_id: str, platform: str = "tiktok") -> int:
    """Return current warmup stage (0-3). Stage 3 = posting allowed."""
    _ensure_account(account_id, platform)
    try:
        row = _conn().execute(
            "SELECT stage FROM warmup WHERE account_id=?", (account_id,)
        ).fetchone()
        return int(row["stage"]) if row else 0
    except Exception:
        return 0


def is_ready_to_post(account_id: str, platform: str = "tiktok") -> bool:
    """Return True if account has completed warmup and can post."""
    if not WARMUP_ENABLED:
        return True
    return get_warmup_stage(account_id, platform) >= _POST_STAGE


def advance_stage_if_ready(account_id: str, platform: str = "tiktok") -> int:
    """
    Check if the account has spent enough days in current stage.
    If yes, advance to next stage.

    Returns new stage number.
    """
    _ensure_account(account_id, platform)
    try:
        row = _conn().execute(
            "SELECT stage, stage_started FROM warmup WHERE account_id=?",
            (account_id,)
        ).fetchone()
        if not row:
            return 0

        stage         = int(row["stage"])
        stage_started = float(row["stage_started"])
        days_in_stage = (time.time() - stage_started) / 86400

        if stage < _POST_STAGE:
            min_days = _STAGE_DAYS.get(stage, 3)
            if days_in_stage >= min_days:
                new_stage = stage + 1
                _exec(
                    "UPDATE warmup SET stage=?, stage_started=? WHERE account_id=?",
                    (new_stage, time.time(), account_id),
                )
                LOGGER.info(
                    "warmup_stage_advanced account_id=%s stage=%d→%d",
                    account_id, stage, new_stage,
                )
                return new_stage

        return stage
    except Exception as exc:
        LOGGER.warning("warmup_advance_error account_id=%s error=%s", account_id, exc)
        return 0


def get_warmup_status(account_id: str) -> dict[str, Any]:
    try:
        row = _conn().execute(
            "SELECT * FROM warmup WHERE account_id=?", (account_id,)
        ).fetchone()
        if not row:
            return {"account_id": account_id, "stage": 0, "ready_to_post": False}
        d = dict(row)
        d["ready_to_post"] = d["stage"] >= _POST_STAGE
        d["days_in_stage"] = (time.time() - d["stage_started"]) / 86400
        d["stage_label"]   = ["browse_only", "browse+like", "browse+like+comment+follow",
                               "posting_enabled"].get(d["stage"], "unknown")
        return d
    except Exception:
        return {"account_id": account_id, "error": "db_error"}


# ── Playwright warmup sessions ────────────────────────────────────────────────

async def _browse_session(page: Any, platform: str, stage: int) -> list[str]:
    """
    Run a warmup browsing session appropriate for the stage.
    Returns list of action descriptions.
    """
    actions: list[str] = []

    targets = _BROWSE_TARGETS_TIKTOK if platform == "tiktok" else _BROWSE_TARGETS_FACEBOOK
    target  = random.choice(targets)

    try:
        await page.goto(target, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(random.uniform(3.0, 7.0))
        actions.append(f"browse:{target}")

        # Random scrolling — simulate watching content
        for _ in range(random.randint(3, 8)):
            await page.mouse.wheel(0, random.randint(400, 1000))
            await asyncio.sleep(random.uniform(2.0, 6.0))
        actions.append("scroll:feed")

        # Stage 1+: Like a random video
        if stage >= 1:
            like_selectors = [
                '[data-e2e="like-icon"]',
                '[aria-label*="Like"]',
                'span[class*="LikeIcon"]',
            ]
            for sel in like_selectors:
                try:
                    likes = await page.locator(sel).all()
                    if likes:
                        target_like = random.choice(likes[:5])
                        await target_like.scroll_into_view_if_needed()
                        await asyncio.sleep(random.uniform(0.5, 1.5))
                        await target_like.click()
                        await asyncio.sleep(random.uniform(1.0, 3.0))
                        actions.append("like:video")
                        break
                except Exception:
                    continue

        # Stage 2+: Follow someone / leave a short comment
        if stage >= 2:
            follow_sels = ['[data-e2e="follow-button"]', 'button:has-text("Follow")']
            for sel in follow_sels:
                try:
                    buttons = await page.locator(sel).all()
                    if buttons:
                        btn = random.choice(buttons[:3])
                        await btn.scroll_into_view_if_needed()
                        await asyncio.sleep(random.uniform(1.0, 3.0))
                        await btn.click()
                        await asyncio.sleep(random.uniform(1.0, 2.0))
                        actions.append("follow:user")
                        break
                except Exception:
                    continue

    except Exception as exc:
        actions.append(f"error:{exc}")
        LOGGER.debug("warmup_browse_error platform=%s stage=%d error=%s",
                     platform, stage, exc)

    return actions


def _has_connected_session(account: dict[str, Any]) -> bool:
    if bool(account.get("session_valid")) or bool(account.get("browser_data_dir")):
        return True
    try:
        from core.browser_providers import (
            BROWSER_PROVIDER_REAL_CHROME,
            account_metadata,
            get_real_chrome_user_data_dir,
            resolve_browser_provider,
        )
        metadata = account_metadata(account)
        if resolve_browser_provider(account) == BROWSER_PROVIDER_REAL_CHROME:
            account_id = str(account.get("account_id") or account.get("id") or "default")
            configured = account.get("real_chrome_user_data_dir") or metadata.get("real_chrome_user_data_dir")
            if configured and Path(str(configured)).exists():
                return True
            return get_real_chrome_user_data_dir(account_id, account, create=False).exists()
    except Exception:
        pass
    cookie_file = str(account.get("cookie_file") or "")
    return bool(cookie_file and Path(cookie_file).exists())


async def _verify_connected_session(page: Any, platform: str) -> None:
    from core.login_diagnostics import (
        LoginBlockStatus,
        classify_login_block,
        login_block_error_message,
    )

    platform_key = platform.lower()
    target = "https://www.tiktok.com/" if platform_key == "tiktok" else "https://www.facebook.com/"
    await page.goto(target, wait_until="domcontentloaded", timeout=30_000)
    await asyncio.sleep(random.uniform(1.0, 2.0))

    status = await classify_login_block(page)
    if status == LoginBlockStatus.RATE_LIMITED:
        raise RuntimeError(login_block_error_message(status))
    if status in {LoginBlockStatus.CAPTCHA_REQUIRED, LoginBlockStatus.CHECKPOINT_REQUIRED}:
        raise RuntimeError(status.value)
    if status == LoginBlockStatus.LOGIN_PAGE:
        raise RuntimeError("SESSION_EXPIRED")

    cookies = await page.context.cookies()
    names = {str(cookie.get("name", "")).lower() for cookie in cookies}
    auth_cookie_names = {
        "tiktok": {"sessionid", "sid_guard", "uid_tt", "passport_csrf_token"},
        "facebook": {"c_user", "xs", "fr"},
    }
    if not (names & auth_cookie_names.get(platform_key, set())):
        raise RuntimeError("SESSION_NOT_CONNECTED")


async def _run_warmup_session(
    account:  dict[str, Any],
    headless: bool,
) -> WarmupResult:
    account_id = str(account.get("account_id") or account.get("id") or "default")
    platform   = account.get("platform", "tiktok")

    _ensure_account(account_id, platform)
    stage = advance_stage_if_ready(account_id, platform)

    result = WarmupResult(account_id=account_id, platform=platform, stage=stage)

    if not _has_connected_session(account):
        result.success = False
        result.error = "SESSION_NOT_CONNECTED"
        return result

    try:
        from playwright.async_api import async_playwright   # type: ignore[import]
    except ImportError:
        result.success = False
        result.error   = "playwright_not_installed"
        return result

    try:
        async with async_playwright() as pw:
            from execution.account_manager import build_playwright_context
            managed_account = dict(account)
            managed_account["headless"] = headless
            browser, ctx, page = await build_playwright_context(managed_account, pw)
            try:
                await _verify_connected_session(page, platform)

                # Run browsing session
                n_sessions = random.randint(2, 4)
                for _ in range(n_sessions):
                    actions = await _browse_session(page, platform, stage)
                    result.actions.extend(actions)
                    result.actions_done += len(actions)
                    await asyncio.sleep(random.uniform(5.0, 15.0))
            finally:
                await browser.close()

        # Update DB
        _exec(
            "UPDATE warmup SET last_session=?, session_count=session_count+1,"
            " actions_today=actions_today+?"
            " WHERE account_id=?",
            (time.time(), result.actions_done, account_id),
        )
        LOGGER.info(
            "warmup_session_done account_id=%s stage=%d actions=%d",
            account_id, stage, result.actions_done,
        )

    except Exception as exc:
        result.success = False
        result.error   = str(exc)
        LOGGER.warning("warmup_session_error account_id=%s error=%s", account_id, exc)

    return result


def run_warmup_session(
    account:  dict[str, Any],
    headless: bool = True,
) -> WarmupResult:
    """
    Run one warmup browsing session for an account. Synchronous wrapper.
    Returns WarmupResult. Never raises.
    """
    account_id = str(account.get("account_id") or account.get("id") or "default")

    if not WARMUP_ENABLED:
        return WarmupResult(account_id=account_id, platform=account.get("platform", "tiktok"),
                            stage=_POST_STAGE, success=True, actions=["warmup_disabled"])

    try:
        loop = asyncio.new_event_loop()
        return loop.run_until_complete(_run_warmup_session(account, headless))
    except Exception as exc:
        return WarmupResult(
            account_id=account_id,
            platform=account.get("platform", "tiktok"),
            stage=0, success=False, error=str(exc),
        )


def reset_warmup(account_id: str) -> None:
    """Reset warmup state to stage 0. Testing only."""
    try:
        _exec(
            "UPDATE warmup SET stage=0, stage_started=?, session_count=0, actions_today=0"
            " WHERE account_id=?",
            (time.time(), account_id),
        )
    except Exception:
        pass
