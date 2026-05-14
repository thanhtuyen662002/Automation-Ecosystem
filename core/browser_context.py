"""
Persistent Playwright browser context management.

Creates a per-account Chromium profile directory so cookies, localStorage,
sessionStorage, and IndexedDB are preserved across runs automatically.

v2: Context parameters are now driven by IdentityProfile from IdentityRegistry,
    ensuring the actual browser runtime matches the backend identity exactly.
    Falls back to session dict values when no IdentityProfile exists.

Usage:
    async with create_publisher_context(pw, session, account_id) as (ctx, page):
        await page.goto(...)
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import random
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

from core.fingerprint_engine import get_identity_scripts, validate_runtime, runtime_issues_to_session_signals
from core.identity_manager import detect_local_locale_profile
from core.stealth import get_stealth_scripts

LOGGER = logging.getLogger("core.browser_context")


# ── Profile directory resolution ─────────────────────────────────────────────

def get_browser_data_dir(account_id: str) -> Path:
    """Return stable per-account Chromium profile directory path."""
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


def _resolve_browser_data_dir(
    account_id: str,
    session: dict[str, Any] | None = None,
    browser_data_dir: str | Path | None = None,
) -> Path:
    configured = browser_data_dir or (session or {}).get("browser_data_dir")
    data_dir = Path(configured) if configured else get_browser_data_dir(account_id)
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


# ── Delay helpers ────────────────────────────────────────────────────────────

async def gaussian_delay(mean: float, std: float, lo: float, hi: float) -> None:
    secs = max(lo, min(hi, random.gauss(mean, std)))
    await asyncio.sleep(secs)

async def action_delay() -> None:
    await gaussian_delay(mean=2.5, std=0.8, lo=1.0, hi=5.0)

async def short_delay() -> None:
    await gaussian_delay(mean=1.2, std=0.4, lo=0.5, hi=2.5)

async def warmup_delay() -> None:
    await gaussian_delay(mean=18.0, std=5.0, lo=10.0, hi=30.0)


# ── IdentityProfile context builder ─────────────────────────────────────────

def _build_launch_kwargs(
    session: dict[str, Any],
    identity_profile: Any | None,
    headless: bool,
) -> dict[str, Any]:
    """Merge session dict + IdentityProfile into Playwright launch kwargs.

    IdentityProfile always wins when present (it is the source of truth).
    Session dict acts as fallback for legacy compatibility.
    """
    if identity_profile is not None:
        # IdentityProfile-driven (authoritative)
        try:
            w, h = identity_profile.screen_resolution.split("x")
            vp_w, vp_h = int(w), int(h)
        except (ValueError, AttributeError):
            vp_w, vp_h = 1280, 720

        user_agent   = identity_profile.user_agent
        timezone     = identity_profile.timezone
        locale       = identity_profile.locale
        proxy_url    = identity_profile.proxy_url

        LOGGER.info("browser_context_identity_profile", extra={
            "event":       "browser_context_identity_profile",
            "device_type": identity_profile.device_type,
            "os":          identity_profile.os,
            "browser":     identity_profile.browser,
            "timezone":    timezone,
            "locale":      locale,
            "fingerprint": identity_profile.fingerprint_hash[:12],
        })
    else:
        # Session-dict fallback (legacy)
        fallback_timezone, fallback_locale = detect_local_locale_profile()
        vp_w       = int(session.get("viewport_width")  or 1280)
        vp_h       = int(session.get("viewport_height") or 720)
        user_agent = session.get("user_agent") or None
        timezone   = session.get("timezone")   or fallback_timezone
        locale     = session.get("locale")     or fallback_locale
        proxy_url  = session.get("proxy_url")  or None
        LOGGER.warning("browser_context_no_identity", extra={
            "event": "browser_context_no_identity",
            "warning": "No IdentityProfile — using session dict fallback (lower consistency)",
        })

    kwargs: dict[str, Any] = {
        "headless":    headless,
        "viewport":    {"width": vp_w, "height": vp_h},
        "locale":      locale,
        "timezone_id": timezone,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-infobars",
            "--disable-extensions",
            # Suppress crash reporter to reduce fingerprint surface
            "--disable-breakpad",
        ],
    }
    if user_agent:
        kwargs["user_agent"] = user_agent
    if proxy_url:
        kwargs["proxy"] = {"server": proxy_url}
        LOGGER.info("browser_using_proxy", extra={
            "event": "browser_using_proxy", "has_proxy": True,
        })
    else:
        LOGGER.warning("browser_no_proxy", extra={
            "event": "browser_no_proxy",
            "warning": "Publishing without proxy — HIGH RISK of cross-account correlation",
        })
    return kwargs


# ── Geolocation from proxy_country ───────────────────────────────────────────

_COUNTRY_GEO: dict[str, tuple[float, float]] = {
    "VN": (10.8231, 106.6297),   # Ho Chi Minh City
    "US": (37.7749, -122.4194),  # San Francisco
    "GB": (51.5074, -0.1278),    # London
    "TH": (13.7563, 100.5018),   # Bangkok
    "ID": (-6.2088, 106.8456),   # Jakarta
    "TW": (25.0330, 121.5654),   # Taipei
    "JP": (35.6762, 139.6503),   # Tokyo
    "KR": (37.5665, 126.9780),   # Seoul
    "DE": (52.5200, 13.4050),    # Berlin
    "FR": (48.8566, 2.3522),     # Paris
    "AU": (-33.8688, 151.2093),  # Sydney
    "SG": (1.3521, 103.8198),    # Singapore
}


async def _apply_geolocation(context: Any, proxy_country: str | None) -> None:
    """Grant geolocation permission and set coords matching proxy country."""
    if not proxy_country:
        return
    geo = _COUNTRY_GEO.get(proxy_country.upper())
    if not geo:
        return
    lat, lon = geo
    try:
        await context.grant_permissions(["geolocation"])
        await context.set_geolocation({"latitude": lat, "longitude": lon, "accuracy": 50})
        LOGGER.debug("geolocation_set", extra={
            "event": "geolocation_set",
            "country": proxy_country, "lat": lat, "lon": lon,
        })
    except Exception as exc:
        LOGGER.warning("geolocation_set_failed", extra={"error": str(exc)})


# ── Context builders ─────────────────────────────────────────────────────────

@asynccontextmanager
async def create_publisher_context(
    pw: Any,
    session: dict[str, Any],
    account_id: str,
    headless: bool = True,
) -> AsyncGenerator[tuple[Any, Any], None]:
    """Create a persistent Playwright browser context with identity enforcement.

    Args:
        pw:         Playwright instance
        session:    Account session dict from database
        account_id: UUID of the account
        headless:   Whether to run headless

    Yields:
        (context, page)

    The context is built from IdentityProfile when available (preferred),
    falling back to session dict. All JS overrides are applied as init scripts
    BEFORE any page JavaScript runs.
    """
    # Load IdentityProfile if available
    identity_profile: Any | None = None
    try:
        from core.identity_manager import get_identity_registry
        reg = get_identity_registry()
        metadata = session.get("metadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("identity_profile"), dict):
            reg.load_profiles({account_id: metadata["identity_profile"]})
        identity_profile = reg.get(account_id)
        if identity_profile is None:
            # Auto-create from session data
            proxy_url     = session.get("proxy_url") or None
            proxy_country = session.get("proxy_country") or None
            identity_profile = reg.get_or_create(account_id, proxy_url=proxy_url, proxy_country=proxy_country)
        else:
            proxy_url = session.get("proxy_url") or None
            proxy_country = session.get("proxy_country") or None
            if proxy_url != getattr(identity_profile, "proxy_url", None) or proxy_country != getattr(identity_profile, "proxy_country", None):
                identity_profile.proxy_url = proxy_url
                identity_profile.proxy_country = proxy_country
    except Exception as exc:
        LOGGER.warning("identity_registry_unavailable", extra={"error": str(exc)})

    data_dir    = _resolve_browser_data_dir(account_id, session=session)
    launch_kw   = _build_launch_kwargs(session, identity_profile, headless)

    LOGGER.info("browser_context_launch", extra={
        "event":      "browser_context_launch",
        "account_id": account_id,
        "browser_data_dir": str(data_dir),
        "headless":   headless,
        "has_proxy":  bool(launch_kw.get("proxy")),
        "timezone":   launch_kw.get("timezone_id"),
        "locale":     launch_kw.get("locale"),
        "viewport":   f"{launch_kw['viewport']['width']}x{launch_kw['viewport']['height']}",
        "identity_profile_id": getattr(identity_profile, "identity_id", "") or getattr(identity_profile, "fingerprint_hash", "")[:12],
    })

    context = await pw.chromium.launch_persistent_context(str(data_dir), **launch_kw)

    # ── Apply identity init scripts (before any page JS) ──────────────────
    if identity_profile is not None:
        from core.fingerprint_engine import get_identity_scripts
        for script in get_identity_scripts(identity_profile):
            await context.add_init_script(script)
        LOGGER.debug("fingerprint_scripts_applied", extra={
            "event": "fingerprint_scripts_applied", "account_id": account_id,
        })
    else:
        # Fall back to generic stealth
        for script in get_stealth_scripts(account_id):
            await context.add_init_script(script)

    # ── Geolocation ───────────────────────────────────────────────────────
    proxy_country = getattr(identity_profile, "proxy_country", None) or session.get("proxy_country")
    await _apply_geolocation(context, proxy_country)

    pages = context.pages
    page  = pages[0] if pages else await context.new_page()

    try:
        yield context, page
    finally:
        try:
            await context.close()
        except Exception:
            pass


@asynccontextmanager
async def create_connect_context(
    pw: Any,
    account_id: str,
    proxy_url: str | None = None,
    session: dict[str, Any] | None = None,
    identity_profile: Any | None = None,
    browser_data_dir: str | Path | None = None,
) -> AsyncGenerator[tuple[Any, Any], None]:
    """Create a persistent context for the /connect (manual login) flow.

    Always non-headless. Uses IdentityProfile if available.
    """
    from core.platform_config import DEFAULT_VIEWPORT

    session = session or {}
    try:
        if identity_profile is None:
            from core.identity_manager import get_identity_registry
            reg = get_identity_registry()
            metadata = session.get("metadata")
            if isinstance(metadata, dict) and isinstance(metadata.get("identity_profile"), dict):
                reg.load_profiles({account_id: metadata["identity_profile"]})
            identity_profile = reg.get(account_id)
            if identity_profile is None:
                identity_profile = reg.get_or_create(
                    account_id,
                    proxy_url=proxy_url or session.get("proxy_url"),
                    proxy_country=session.get("proxy_country"),
                )
    except Exception as exc:
        LOGGER.warning("connect_identity_unavailable", extra={"event": "connect_identity_unavailable", "error": str(exc)})

    data_dir = _resolve_browser_data_dir(account_id, session=session, browser_data_dir=browser_data_dir)
    if identity_profile is not None:
        desired_proxy_url = proxy_url or session.get("proxy_url") or None
        desired_proxy_country = session.get("proxy_country") or None
        if desired_proxy_url != getattr(identity_profile, "proxy_url", None) or desired_proxy_country != getattr(identity_profile, "proxy_country", None):
            identity_profile.proxy_url = desired_proxy_url
            identity_profile.proxy_country = desired_proxy_country

    # For connect flow: always use profile's UA/tz/locale if available
    if identity_profile:
        try:
            w, h = identity_profile.screen_resolution.split("x")
            vp = {"width": int(w), "height": int(h)}
        except Exception:
            vp = DEFAULT_VIEWPORT
        ua = identity_profile.user_agent
        tz = identity_profile.timezone
        lc = identity_profile.locale
        px = identity_profile.proxy_url or proxy_url
        proxy_country = getattr(identity_profile, "proxy_country", None)
    else:
        fallback_timezone, fallback_locale = detect_local_locale_profile()
        vp = DEFAULT_VIEWPORT
        ua = session.get("user_agent") or None
        tz = session.get("timezone") or fallback_timezone
        lc = session.get("locale") or fallback_locale
        px = proxy_url or session.get("proxy_url")
        proxy_country = session.get("proxy_country")

    launch_kw: dict[str, Any] = {
        "headless":    False,
        "viewport":    vp,
        "timezone_id": tz,
        "locale":      lc,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    }
    if ua:
        launch_kw["user_agent"] = ua
    if px:
        launch_kw["proxy"] = {"server": px}
        LOGGER.info("connect_using_proxy", extra={"event": "connect_using_proxy", "account_id": account_id, "has_proxy": True})
    else:
        LOGGER.warning("connect_no_proxy", extra={"event": "connect_no_proxy", "account_id": account_id, "has_proxy": False})

    LOGGER.info("connect_context_launch", extra={
        "event": "connect_context_launch",
        "account_id": account_id,
        "browser_data_dir": str(data_dir),
        "timezone": tz,
        "locale": lc,
        "viewport": f"{vp['width']}x{vp['height']}",
        "has_proxy": bool(px),
        "identity_profile_id": getattr(identity_profile, "identity_id", "") or getattr(identity_profile, "fingerprint_hash", "")[:12],
    })

    context = await pw.chromium.launch_persistent_context(str(data_dir), **launch_kw)

    if identity_profile:
        for script in get_identity_scripts(identity_profile):
            await context.add_init_script(script)
    else:
        for script in get_stealth_scripts(account_id):
            await context.add_init_script(script)

    await _apply_geolocation(context, proxy_country)

    pages = context.pages
    page  = pages[0] if pages else await context.new_page()

    try:
        yield context, page
    finally:
        try:
            await context.close()
        except Exception:
            pass
