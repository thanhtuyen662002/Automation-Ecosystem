"""
execution/account_manager.py — Multi-account pool manager.

Loads accounts from JSON config, manages per-account Playwright contexts
with optional proxy support, tracks health, and provides round-robin rotation.

Account JSON format (data/accounts.json or ACCOUNTS_FILE env var):
[
  {
    "account_id": "tiktok_acc_01",
    "platform":   "tiktok",
    "username":   "user@email.com",     # optional if cookies provided
    "password":   "...",                # optional if cookies provided
    "email":      "user@email.com",     # for Facebook
    "cookie_file": "data/sessions/tiktok_tiktok_acc_01.json",   # optional
    "proxy":      "http://user:pass@host:port",                  # optional
    "enabled":    true
  }
]

Public API:
    load_accounts(path)                     → int (accounts loaded)
    get_next_account(platform)              → dict | None
    get_account(account_id)                 → dict | None
    mark_healthy(account_id)               → None
    mark_failed(account_id, reason)        → None
    mark_banned(account_id)               → None
    get_health_report()                    → list[dict]
    build_playwright_context(account, pw)  → BrowserContext (async)
    reset_account_manager()               # testing only
"""
from __future__ import annotations

import json
import logging
import os
import time
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("execution.account_manager")

# ── Config ────────────────────────────────────────────────────────────────────

_ACCOUNTS_FILE = Path(os.environ.get("ACCOUNTS_FILE", "data/accounts.json"))
_SESSION_DIR   = Path(os.environ.get("PLAYWRIGHT_SESSION_DIR", "data/sessions"))

# Health constants
_MAX_CONSECUTIVE_FAILS: int = 3
_BAN_COOLDOWN_S: int        = 86400 * 3   # 3 days before retrying a banned account
_FAIL_COOLDOWN_S: int       = 3600        # 1 hour before retrying a failed account


# ── Account health record ─────────────────────────────────────────────────────

@dataclass
class AccountHealth:
    account_id:       str
    platform:         str
    status:           str    = "healthy"   # "healthy" | "cooldown" | "banned"
    consecutive_fails: int   = 0
    last_fail_ts:     float  = 0.0
    last_success_ts:  float  = 0.0
    ban_ts:           float  = 0.0
    fail_reason:      str    = ""
    total_posts:      int    = 0
    total_fails:      int    = 0

    def is_available(self) -> bool:
        now = time.time()
        if self.status == "banned":
            return now - self.ban_ts > _BAN_COOLDOWN_S
        if self.status == "cooldown":
            return now - self.last_fail_ts > _FAIL_COOLDOWN_S
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id":        self.account_id,
            "platform":          self.platform,
            "status":            self.status,
            "consecutive_fails": self.consecutive_fails,
            "last_fail_ts":      self.last_fail_ts,
            "last_success_ts":   self.last_success_ts,
            "ban_ts":            self.ban_ts,
            "fail_reason":       self.fail_reason,
            "total_posts":       self.total_posts,
            "total_fails":       self.total_fails,
        }


# ── Module state ──────────────────────────────────────────────────────────────

_ACCOUNTS:    list[dict[str, Any]]       = []
_HEALTH:      dict[str, AccountHealth]   = {}
_ROUND_ROBIN: dict[str, int]             = {}   # platform → next index


# ── Loader ────────────────────────────────────────────────────────────────────

def load_accounts(path: str | Path | None = None) -> int:
    """
    Load accounts from JSON file.

    Returns number of enabled accounts loaded.
    Safe to call multiple times (reloads on each call).
    """
    global _ACCOUNTS
    fp = Path(path) if path else _ACCOUNTS_FILE

    if not fp.exists():
        LOGGER.warning("accounts_file_not_found path=%s — creating empty template", fp)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(json.dumps(_EXAMPLE_ACCOUNTS, indent=2), encoding="utf-8")
        return 0

    try:
        raw: list[dict] = json.loads(fp.read_text(encoding="utf-8"))
        enabled = [a for a in raw if a.get("enabled", True)]
        _ACCOUNTS = enabled

        # Initialise health records for new accounts
        for acct in enabled:
            aid = acct["account_id"]
            if aid not in _HEALTH:
                _HEALTH[aid] = AccountHealth(
                    account_id=aid,
                    platform=acct.get("platform", "tiktok"),
                )

        LOGGER.info("accounts_loaded count=%d file=%s", len(enabled), fp)
        return len(enabled)
    except Exception as exc:
        LOGGER.error("accounts_load_error path=%s error=%s", fp, exc)
        return 0


# ── Account selection ─────────────────────────────────────────────────────────

def get_next_account(platform: str) -> dict[str, Any] | None:
    """
    Return the next available healthy account for a platform (round-robin).

    Returns None if no accounts are available.
    """
    pool = [a for a in _ACCOUNTS if a.get("platform", "tiktok") == platform]
    if not pool:
        # Try loading from disk if pool is empty
        if not _ACCOUNTS:
            load_accounts()
            pool = [a for a in _ACCOUNTS if a.get("platform", "tiktok") == platform]
    if not pool:
        return None

    start_idx = _ROUND_ROBIN.get(platform, 0) % len(pool)
    for offset in range(len(pool)):
        idx  = (start_idx + offset) % len(pool)
        acct = pool[idx]
        aid  = acct["account_id"]
        h    = _HEALTH.get(aid)
        if h is None or h.is_available():
            _ROUND_ROBIN[platform] = (idx + 1) % len(pool)
            return deepcopy(acct)

    LOGGER.warning("no_available_accounts platform=%s", platform)
    return None


def get_account(account_id: str) -> dict[str, Any] | None:
    """Return account config by ID, or None."""
    for a in _ACCOUNTS:
        if a["account_id"] == account_id:
            return deepcopy(a)
    return None


# ── Health tracking ───────────────────────────────────────────────────────────

def mark_healthy(account_id: str) -> None:
    h = _HEALTH.setdefault(account_id, AccountHealth(account_id, ""))
    h.status            = "healthy"
    h.consecutive_fails = 0
    h.last_success_ts   = time.time()
    h.total_posts      += 1
    LOGGER.debug("account_mark_healthy account_id=%s posts=%d", account_id, h.total_posts)


def mark_failed(account_id: str, reason: str = "") -> None:
    h = _HEALTH.setdefault(account_id, AccountHealth(account_id, ""))
    h.consecutive_fails += 1
    h.last_fail_ts       = time.time()
    h.fail_reason        = reason
    h.total_fails       += 1
    if h.consecutive_fails >= _MAX_CONSECUTIVE_FAILS:
        h.status = "cooldown"
        LOGGER.warning("account_in_cooldown account_id=%s fails=%d reason=%s",
                       account_id, h.consecutive_fails, reason)
    else:
        LOGGER.info("account_fail account_id=%s fails=%d reason=%s",
                    account_id, h.consecutive_fails, reason)


def mark_banned(account_id: str) -> None:
    h = _HEALTH.setdefault(account_id, AccountHealth(account_id, ""))
    h.status  = "banned"
    h.ban_ts  = time.time()
    LOGGER.warning("account_banned account_id=%s", account_id)


def get_health_report() -> list[dict[str, Any]]:
    """Return health status for all tracked accounts."""
    return [h.to_dict() for h in _HEALTH.values()]


# ── Playwright context builder ────────────────────────────────────────────────

# Anti-detection stealth JS (injected before any page load)
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
window.chrome = {runtime: {}};
Object.defineProperty(navigator, 'permissions', {
    query: (p) => Promise.resolve({state: p.name === 'notifications' ? 'denied' : 'granted'})
});
"""

# Realistic Chrome user agents (rotated per account deterministically)
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


def _ua_for_account(account_id: str) -> str:
    """Pick a deterministic but varied user-agent per account."""
    idx = hash(account_id) % len(_USER_AGENTS)
    return _USER_AGENTS[idx]


async def build_playwright_context(account: dict[str, Any], pw: Any) -> Any:
    """
    Launch Playwright browser + context for an account.

    Applies:
      - stealth JS
      - per-account user-agent
      - optional proxy
      - saved cookie session if available

    Returns (browser, context, page) tuple.
    Caller is responsible for closing the browser.
    """
    account_id = account["account_id"]
    platform   = account.get("platform", "tiktok")
    proxy_url  = account.get("proxy")

    ua = _ua_for_account(account_id)

    launch_args: dict[str, Any] = {
        "headless": True,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-infobars",
            "--window-size=1280,800",
        ],
    }
    if proxy_url:
        launch_args["proxy"] = {"server": proxy_url}

    browser = await pw.chromium.launch(**launch_args)

    ctx_args: dict[str, Any] = {
        "user_agent": ua,
        "viewport":   {"width": 1280, "height": 800},
        "locale":     "en-US",
        "timezone_id": "America/New_York",
        "geolocation": {"latitude": 40.71, "longitude": -74.01},
        "permissions": ["geolocation"],
    }
    if proxy_url:
        ctx_args["proxy"] = {"server": proxy_url}

    ctx  = await browser.new_context(**ctx_args)
    page = await ctx.new_page()
    await page.add_init_script(_STEALTH_JS)

    # Restore cookies if available
    cookie_file = Path(account.get("cookie_file", ""))
    if not cookie_file.exists():
        # Check default session dir
        _SESSION_DIR.mkdir(parents=True, exist_ok=True)
        safe_id = account_id.replace("/", "_").replace(":", "_")
        cookie_file = _SESSION_DIR / f"{platform}_{safe_id}.json"

    if cookie_file.exists():
        try:
            cookies = json.loads(cookie_file.read_text(encoding="utf-8"))
            await ctx.add_cookies(cookies)
            LOGGER.debug("cookies_restored account_id=%s", account_id)
        except Exception as exc:
            LOGGER.debug("cookie_restore_failed account_id=%s error=%s", account_id, exc)

    return browser, ctx, page


async def save_session(account: dict[str, Any], ctx: Any) -> None:
    """Persist current browser cookies to session file."""
    account_id = account["account_id"]
    platform   = account.get("platform", "tiktok")
    _SESSION_DIR.mkdir(parents=True, exist_ok=True)
    safe_id     = account_id.replace("/", "_").replace(":", "_")
    cookie_file = _SESSION_DIR / f"{platform}_{safe_id}.json"
    try:
        cookies = await ctx.cookies()
        cookie_file.write_text(json.dumps(cookies), encoding="utf-8")
        LOGGER.debug("session_saved account_id=%s", account_id)
    except Exception as exc:
        LOGGER.debug("session_save_failed account_id=%s error=%s", account_id, exc)


# ── Reset ─────────────────────────────────────────────────────────────────────

def reset_account_manager() -> None:
    """Full reset — for testing only."""
    global _ACCOUNTS
    _ACCOUNTS = []
    _HEALTH.clear()
    _ROUND_ROBIN.clear()


# ── Example accounts template ─────────────────────────────────────────────────

_EXAMPLE_ACCOUNTS: list[dict] = [
    {
        "account_id": "tiktok_acc_01",
        "platform":   "tiktok",
        "username":   "your_email@example.com",
        "password":   "your_password",
        "proxy":      "",
        "enabled":    True,
    },
    {
        "account_id": "facebook_acc_01",
        "platform":   "facebook",
        "email":      "your_email@example.com",
        "password":   "your_password",
        "proxy":      "",
        "enabled":    True,
    },
]
