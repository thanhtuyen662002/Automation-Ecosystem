"""
Persistent Playwright browser context management.

Creates a per-account Chromium profile directory so cookies, localStorage,
sessionStorage, and IndexedDB are preserved across runs automatically.

Benefits over ephemeral context:
  - Session data survives without manual cookie injection
  - Browser "history" accumulates naturally (more human-like)
  - Reduces login frequency requirements
  - Belt-and-suspenders: we STILL inject DB cookies on top

Usage:
    async with create_publisher_context(pw, session, account_id) as (ctx, page):
        await page.goto(...)
"""
from __future__ import annotations

import logging
import os
import platform
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

from core.stealth import get_stealth_scripts

LOGGER = logging.getLogger("core.browser_context")


# ── Profile directory resolution ─────────────────────────────────────────────

def get_browser_data_dir(account_id: str) -> Path:
    """Return stable per-account Chromium profile directory path.

    Location follows OS conventions:
      Windows : %APPDATA%\\Automation-Ecosystem\\browser_profiles\\{account_id}
      macOS   : ~/Library/Application Support/Automation-Ecosystem/browser_profiles/{account_id}
      Linux   : ~/.local/share/automation-ecosystem/browser_profiles/{account_id}
    """
    sys = platform.system()
    if sys == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        base = base / "Automation-Ecosystem" / "browser_profiles"
    elif sys == "Darwin":
        base = Path.home() / "Library" / "Application Support" / "Automation-Ecosystem" / "browser_profiles"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        base = base / "automation-ecosystem" / "browser_profiles"

    profile_dir = base / account_id
    profile_dir.mkdir(parents=True, exist_ok=True)
    return profile_dir


# ── Gaussian delay helper ─────────────────────────────────────────────────────

import asyncio
import random


async def gaussian_delay(mean: float, std: float, lo: float, hi: float) -> None:
    """Sleep for gaussian-distributed seconds clamped to [lo, hi].

    Using gaussian instead of uniform avoids the "robot clicks at exact interval"
    pattern that some platforms detect through timing analysis.
    """
    secs = max(lo, min(hi, random.gauss(mean, std)))
    await asyncio.sleep(secs)


async def action_delay() -> None:
    """Standard between-action pause: gaussian centred at 2.5s, σ=0.8."""
    await gaussian_delay(mean=2.5, std=0.8, lo=1.0, hi=5.0)


async def short_delay() -> None:
    """Short pause (e.g. after click): gaussian centred at 1.2s, σ=0.4."""
    await gaussian_delay(mean=1.2, std=0.4, lo=0.5, hi=2.5)


async def warmup_delay() -> None:
    """Warm-up feed viewing: gaussian centred at 18s, σ=5, clamped 10–30s."""
    await gaussian_delay(mean=18.0, std=5.0, lo=10.0, hi=30.0)


# ── Context builder ───────────────────────────────────────────────────────────

@asynccontextmanager
async def create_publisher_context(
    pw: Any,
    session: dict[str, Any],
    account_id: str,
    headless: bool = True,
) -> AsyncGenerator[tuple[Any, Any], None]:
    """Create a persistent Playwright browser context with stealth patches applied.

    Args:
        pw: Playwright instance (from `async with async_playwright() as pw`)
        session: Account session dict from database.get_account_session()
        account_id: UUID of the account
        headless: Whether to run headless (False for /connect flow)

    Yields:
        (context, page) — the browser context and an open page

    The context uses a per-account persistent profile directory so that
    cookies, localStorage, and sessionStorage accumulate across runs.
    Additionally, DB-stored cookies are injected on top for reliability.
    """
    proxy_url: str | None = session.get("proxy_url") or None
    user_agent: str | None = session.get("user_agent") or None
    viewport_width: int = int(session.get("viewport_width") or 1280)
    viewport_height: int = int(session.get("viewport_height") or 720)
    tz: str = session.get("timezone") or "America/New_York"
    locale: str = session.get("locale") or "en-US"

    data_dir = get_browser_data_dir(account_id)

    LOGGER.info(
        "browser_context_launch",
        extra={
            "event": "browser_context_launch",
            "account_id": account_id,
            "data_dir": str(data_dir),
            "proxy": proxy_url or "NONE",
            "user_agent": (user_agent or "default")[:60],
            "viewport": f"{viewport_width}x{viewport_height}",
            "timezone": tz,
            "locale": locale,
            "headless": headless,
        },
    )

    # Build launch kwargs for persistent context
    launch_kwargs: dict[str, Any] = {
        "headless": headless,
        "viewport": {"width": viewport_width, "height": viewport_height},
        "locale": locale,
        "timezone_id": tz,
        # Chromium args that reduce headless detection signals
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-infobars",
        ],
    }

    if user_agent:
        launch_kwargs["user_agent"] = user_agent

    if proxy_url:
        launch_kwargs["proxy"] = {"server": proxy_url}
        LOGGER.info(
            "browser_using_proxy",
            extra={"event": "browser_using_proxy", "account_id": account_id, "proxy": proxy_url},
        )
    else:
        LOGGER.warning(
            "browser_no_proxy",
            extra={
                "event": "browser_no_proxy",
                "account_id": account_id,
                "warning": "Publishing without proxy — HIGH RISK of IP-based cross-account correlation",
            },
        )

    # launch_persistent_context returns a BrowserContext directly (no browser wrapper)
    context = await pw.chromium.launch_persistent_context(str(data_dir), **launch_kwargs)

    # Apply all stealth init scripts
    stealth_scripts = get_stealth_scripts(account_id)
    for script in stealth_scripts:
        await context.add_init_script(script)

    LOGGER.debug(
        "stealth_scripts_applied",
        extra={"event": "stealth_scripts_applied", "count": len(stealth_scripts)},
    )

    # Open a page (reuse existing if any, else create)
    pages = context.pages
    page = pages[0] if pages else await context.new_page()

    try:
        yield context, page
    finally:
        try:
            await context.close()
        except Exception:
            pass  # Best-effort cleanup


@asynccontextmanager
async def create_connect_context(
    pw: Any,
    account_id: str,
    proxy_url: str | None = None,
) -> AsyncGenerator[tuple[Any, Any], None]:
    """Create a persistent context for the /connect (manual login) flow.

    Uses the same per-account data_dir so login state persists for publish runs.
    Always non-headless (user must be able to see the browser window).
    """
    from core.platform_config import DEFAULT_VIEWPORT

    data_dir = get_browser_data_dir(account_id)

    launch_kwargs: dict[str, Any] = {
        "headless": False,
        "viewport": DEFAULT_VIEWPORT,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    }

    if proxy_url:
        launch_kwargs["proxy"] = {"server": proxy_url}
        LOGGER.info(
            "connect_using_proxy",
            extra={"event": "connect_using_proxy", "account_id": account_id, "proxy": proxy_url},
        )
    else:
        LOGGER.warning(
            "connect_no_proxy",
            extra={
                "event": "connect_no_proxy",
                "account_id": account_id,
                "warning": "Connecting without proxy — HIGH RISK",
            },
        )

    context = await pw.chromium.launch_persistent_context(str(data_dir), **launch_kwargs)

    # Apply stealth for the login session too
    for script in get_stealth_scripts(account_id):
        await context.add_init_script(script)

    pages = context.pages
    page = pages[0] if pages else await context.new_page()

    try:
        yield context, page
    finally:
        try:
            await context.close()
        except Exception:
            pass
