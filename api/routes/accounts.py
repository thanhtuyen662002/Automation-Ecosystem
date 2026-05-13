from __future__ import annotations

import asyncio
import logging
import traceback as _traceback

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from api.dependencies import DatabaseDependency
from api.schemas import (
    AccountCreateRequest,
    AccountHealthRequest,
    AccountResponse,
    AccountUpdateRequest,
    SessionStatusResponse,
)


LOGGER = logging.getLogger("api.accounts")
router = APIRouter(prefix="/accounts", tags=["accounts"])

_VALID_STATUSES = {"healthy", "limited", "banned", "disabled"}

# Login timeout: how long to wait for the user to complete manual login (seconds)
_LOGIN_TIMEOUT_SECONDS = 300


class AccountListResponse(BaseModel):
    items: list[AccountResponse]


@router.get("", response_model=AccountListResponse)
async def list_accounts(
    database: DatabaseDependency,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> AccountListResponse:
    rows = await database.list_accounts(limit=limit, offset=offset)
    LOGGER.info("accounts_listed", extra={"event": "accounts_listed", "count": len(rows)})
    return AccountListResponse(items=[AccountResponse.from_row(row) for row in rows])


@router.post("", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account(
    request: AccountCreateRequest,
    database: DatabaseDependency,
) -> AccountResponse:
    profile_url = request.profile_url or _derive_profile_url(request.platform, request.account_handle)
    row = await database.create_account(
        platform=request.platform,
        account_handle=request.account_handle,
        profile_url=profile_url,
        external_user_id=request.external_user_id,
        proxy_url=request.proxy_url,
        metadata=request.metadata,
    )
    LOGGER.info(
        "account_created",
        extra={"event": "account_created", "account_id": row["id"], "platform": row["platform"]},
    )
    return AccountResponse.from_row(row)


@router.patch("/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: str,
    request: AccountUpdateRequest,
    database: DatabaseDependency,
) -> AccountResponse:
    existing = await database.get_account(account_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Account not found")

    update_fields = request.model_dump(exclude_unset=True)
    if "profile_url" not in update_fields and "account_handle" in update_fields:
        update_fields["profile_url"] = _derive_profile_url(
            existing["platform"],
            str(update_fields["account_handle"]),
        )
    updated = await database.update_account_fields(account_id, update_fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="Account not found")
    LOGGER.info(
        "account_updated",
        extra={"event": "account_updated", "account_id": account_id, "fields": sorted(update_fields)},
    )
    return AccountResponse.from_row(updated)


@router.put("/{account_id}", response_model=AccountResponse)
async def replace_account(
    account_id: str,
    request: AccountUpdateRequest,
    database: DatabaseDependency,
) -> AccountResponse:
    return await update_account(account_id, request, database)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(account_id: str, database: DatabaseDependency) -> None:
    deleted = await database.delete_account(account_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Account not found")
    LOGGER.info("account_deleted", extra={"event": "account_deleted", "account_id": account_id})


@router.post("/{account_id}/health", response_model=AccountResponse)
async def check_account_health(
    account_id: str,
    request: AccountHealthRequest,
    database: DatabaseDependency,
) -> AccountResponse:
    """
    Update an account's operational status.

    - healthy   → account is reachable and not rate-limited
    - limited   → account is temporarily throttled
    - banned    → account is permanently blocked from publishing
    """
    row = await database.get_account(account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if request.status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status '{request.status}'. Must be one of: {sorted(_VALID_STATUSES)}",
        )
    updated = await database.update_account_status(account_id, request.status)
    if updated is None:
        raise HTTPException(status_code=404, detail="Account not found")
    LOGGER.info(
        "account_health_updated",
        extra={"event": "account_health_updated", "account_id": account_id, "status": request.status},
    )
    return AccountResponse.from_row(updated)


@router.post("/{account_id}/mark-soft-ban", status_code=status.HTTP_200_OK)
async def mark_account_soft_ban(account_id: str, database: DatabaseDependency) -> dict:
    """
    Mark an account as shadow-banned (0-view posts, upload success but no reach).

    Sets soft_ban_detected=1 and status='limited' to pause publishing automatically.
    The publisher will reject publish tasks for this account until cleared.

    Use when:
      - Video posted successfully but stays at 0 views for 24h+
      - Upload succeeds but content never appears in feeds
      - TikTok shows upload success but no engagement whatsoever
    """
    row = await database.get_account(account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Account not found")
    await database.mark_soft_ban(account_id)
    LOGGER.warning(
        "account_soft_ban_marked",
        extra={
            "event": "account_soft_ban_marked",
            "account_id": account_id,
            "action": "Publishing suspended pending review",
        },
    )
    return {"account_id": account_id, "soft_ban_detected": True, "status": "limited"}


@router.post("/{account_id}/clear-soft-ban", status_code=status.HTTP_200_OK)
async def clear_account_soft_ban(account_id: str, database: DatabaseDependency) -> dict:
    """
    Clear soft-ban flag after manual review confirms account is healthy again.

    Sets soft_ban_detected=0 and status='healthy' to resume publishing.
    Only use after verifying account can reach audiences normally.
    """
    row = await database.get_account(account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Account not found")
    await database.clear_soft_ban(account_id)
    LOGGER.info(
        "account_soft_ban_cleared",
        extra={"event": "account_soft_ban_cleared", "account_id": account_id},
    )
    return {"account_id": account_id, "soft_ban_detected": False, "status": "healthy"}


@router.get("/{account_id}/session-status", response_model=SessionStatusResponse)
async def get_session_status(account_id: str, database: DatabaseDependency) -> SessionStatusResponse:
    """
    Returns session connectivity state without exposing cookie data.
    """
    row = await database.get_account_session(account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return SessionStatusResponse(
        account_id=account_id,
        session_valid=bool(row.get("session_valid", 0)),
        has_cookies=bool(row.get("cookies")),
        last_login_at=str(row["last_login_at"]) if row.get("last_login_at") else None,
        user_agent=row.get("user_agent"),
    )


@router.post("/{account_id}/connect", response_model=AccountResponse)
async def connect_account(account_id: str, database: DatabaseDependency) -> AccountResponse:
    """
    Launch a visible Playwright browser window so the user can log in manually.

    Flow:
    1. Open Chromium to the platform login page
    2. Wait for the user to complete login (max 5 minutes)
    3. Extract cookies and user-agent
    4. Save encrypted session to DB
    5. Return updated account

    The browser is always visible (non-headless) — credentials are never automated.
    """
    row = await database.get_account(account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Account not found")

    platform = row["platform"]

    try:
        from core.platform_config import get_platform_config, is_login_page, DEFAULT_VIEWPORT
        from core.session_crypto import encrypt_cookies
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Playwright is not available: {exc}. Run: playwright install chromium",
        )

    try:
        cfg = get_platform_config(platform)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    proxy_url: str | None = row.get("proxy_url") or None
    configured_profile_url: str | None = row.get("profile_url") or _derive_profile_url(platform, row["account_handle"])

    LOGGER.info(
        "account_connect_start",
        extra={
            "event": "account_connect_start",
            "account_id": account_id,
            "platform": platform,
            "proxy": proxy_url or "NONE (HIGH RISK)",
        },
    )

    # Pre-initialize so outer scope is safe even if browser block exits early
    profile: dict = {"avatar_url": "", "display_name": ""}
    cookies: list = []
    fingerprint: dict = {"width": 1280, "height": 720, "timezone": "America/New_York", "locale": "en-US"}
    user_agent: str = ""

    try:
        from core.browser_context import create_connect_context, get_browser_data_dir

        async with async_playwright() as pw:
            async with create_connect_context(pw, account_id, proxy_url=proxy_url) as (context, page):

                user_agent = await page.evaluate("navigator.userAgent")
                await page.goto(cfg.login_url, wait_until="domcontentloaded", timeout=60000)

                LOGGER.info(
                    "account_connect_browser_open",
                    extra={"event": "account_connect_browser_open", "url": cfg.login_url},
                )

                # Poll for successful login (max 5 minutes). Do not rely on a single
                # redirect URL; platforms frequently vary post-login destinations.
                deadline = asyncio.get_running_loop().time() + _LOGIN_TIMEOUT_SECONDS
                logged_in = False
                while asyncio.get_running_loop().time() < deadline:
                    try:
                        current_url = page.url
                    except Exception:
                        # Browser window was closed by the user before login
                        raise HTTPException(
                            status_code=400,
                            detail="Browser was closed before login completed. Please try again.",
                        )
                    if await _looks_logged_in(page, context, platform, cfg.success_url_fragment):
                        logged_in = True
                        break
                    await asyncio.sleep(1)

                if not logged_in:
                    raise HTTPException(
                        status_code=408,
                        detail=f"Login timed out after {_LOGIN_TIMEOUT_SECONDS}s. Please try again.",
                    )

                if configured_profile_url:
                    try:
                        await page.goto(configured_profile_url, wait_until="domcontentloaded", timeout=30000)
                    except Exception as exc:
                        LOGGER.warning(
                            "account_profile_navigation_failed",
                            extra={
                                "event": "account_profile_navigation_failed",
                                "account_id": account_id,
                                "profile_url": configured_profile_url,
                                "error": str(exc),
                            },
                        )

                # Extract profile info from the logged-in page.
                _JS_PROFILE: dict[str, str] = {
                    "tiktok": """
                        () => {
                            // TikTok: profile in nav header
                            const avatarEl =
                                document.querySelector('[data-e2e="header-avatar"] img') ||
                                document.querySelector('img[class*="ImgAvatar"]') ||
                                document.querySelector('[data-e2e="nav-avatar"] img');
                            const nameEl =
                                document.querySelector('[data-e2e="user-title"]') ||
                                document.querySelector('p[class*="UserTitle"]') ||
                                document.querySelector('[data-e2e="nav-header-user-info"] span') ||
                                document.querySelector('span[class*="UserName"]');
                            return {
                                avatar_url: avatarEl ? avatarEl.src : '',
                                display_name: nameEl ? nameEl.textContent.trim() : '',
                                profile_url: location.href.includes('/@') ? location.href.split('?')[0] : '',
                                account_handle: (location.pathname.match(/@([^/?]+)/) || [])[1] || ''
                            };
                        }
                    """,
                    "facebook": """
                        () => {
                            const avatarEl =
                                document.querySelector('[data-testid="user-avatar"] img') ||
                                document.querySelector('image[href]') ||
                                document.querySelector('img[class*="ProfilePhoto"]') ||
                                document.querySelector('[aria-label] img');
                            const nameEl =
                                document.querySelector('[data-testid="profile_name_in_profile_page"]') ||
                                document.querySelector('h1[class*="title"]') ||
                                document.querySelector('.profileName');
                            return {
                                avatar_url: avatarEl ? (avatarEl.src || avatarEl.getAttribute('href') || '') : '',
                                display_name: nameEl ? nameEl.textContent.trim() : '',
                                profile_url: location.href.split('?')[0],
                                account_handle: ''
                            };
                        }
                    """,
                }

                profile = {"avatar_url": "", "display_name": ""}
                js_script = _JS_PROFILE.get(platform, "")
                if js_script:
                    try:
                        profile = await page.evaluate(js_script)
                    except Exception as _exc:
                        LOGGER.debug("profile_extract_skipped error=%s", _exc)

                LOGGER.info(
                    "account_profile_extracted",
                    extra={
                        "event": "account_profile_extracted",
                        "account_id": account_id,
                        "display_name": profile.get("display_name"),
                        "has_avatar": bool(profile.get("avatar_url")),
                    },
                )

                # Extract cookies + fingerprint from live browser
                cookies = await context.cookies()
                fingerprint = await page.evaluate("""
                    () => ({
                        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                        locale: navigator.language || 'en-US',
                        width: window.screen.width,
                        height: window.screen.height,
                    })
                """)
                # context.close() is handled by create_connect_context

        # Persist session + fingerprint to DB (also stored in persistent profile dir)
        data_dir = str(get_browser_data_dir(account_id))
        cookies_encrypted = encrypt_cookies(cookies)
        updated = await database.save_account_session(
            account_id,
            cookies_encrypted,
            user_agent,
            viewport_width=fingerprint.get("width", 1280),
            viewport_height=fingerprint.get("height", 720),
            timezone=fingerprint.get("timezone", "America/New_York"),
            locale=fingerprint.get("locale", "en-US"),
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Account not found after session save")

        # Persist browser_data_dir
        await database.set_browser_data_dir(account_id, data_dir)

        # Persist profile info (avatar + display_name)
        if profile.get("avatar_url") or profile.get("display_name") or profile.get("profile_url") or configured_profile_url:
            await database.update_account_profile(
                account_id,
                avatar_url=profile.get("avatar_url") or None,
                display_name=profile.get("display_name") or None,
                profile_url=profile.get("profile_url") or configured_profile_url,
                account_handle=profile.get("account_handle") or None,
            )
            # Re-fetch updated row
            fresh = await database.get_account(account_id)
            if fresh:
                updated = fresh

        LOGGER.info(
            "account_connect_success",
            extra={
                "event": "account_connect_success",
                "account_id": account_id,
                "platform": platform,
                "cookie_count": len(cookies),
                "fingerprint": fingerprint,
                "browser_data_dir": data_dir,
            },
        )
        return AccountResponse.from_row(updated)

    except HTTPException:
        raise
    except Exception as exc:
        tb = _traceback.format_exc()
        LOGGER.error(
            "account_connect_error: type=%s str=%r\n%s",
            type(exc).__name__, str(exc), tb,
            extra={"event": "account_connect_error", "account_id": account_id, "error": str(exc)},
        )
        raise _connect_error_to_http(exc) from exc


def _derive_profile_url(platform: str, account_handle: str | None) -> str | None:
    handle = (account_handle or "").strip()
    if not handle:
        return None
    if handle.startswith("http://") or handle.startswith("https://"):
        return handle
    handle = handle.lstrip("@").strip("/")
    if not handle:
        return None
    platform_key = platform.lower()
    if platform_key == "tiktok":
        return f"https://www.tiktok.com/@{handle}"
    if platform_key == "youtube":
        return f"https://www.youtube.com/@{handle}"
    if platform_key == "facebook":
        return f"https://www.facebook.com/{handle}"
    return None


async def _looks_logged_in(page, context, platform: str, success_url_fragment: str) -> bool:
    from core.platform_config import is_login_page

    current_url = page.url or ""
    if success_url_fragment and success_url_fragment in current_url and not is_login_page(current_url, platform):
        return True
    cookies = await context.cookies()
    cookie_names = {str(cookie.get("name", "")).lower() for cookie in cookies}
    platform_key = platform.lower()
    auth_cookie_names = {
        "tiktok": {"sessionid", "sid_guard", "uid_tt", "passport_csrf_token"},
        "facebook": {"c_user", "xs", "fr"},
        "youtube": {"sid", "hsid", "ssid", "sapisisid", "apisid"},
    }
    has_auth_cookie = bool(cookie_names & auth_cookie_names.get(platform_key, set()))
    if has_auth_cookie and not is_login_page(current_url, platform):
        return True
    if platform_key == "tiktok":
        try:
            avatar = page.locator('[data-e2e="header-avatar"], [data-e2e="nav-avatar"]').first
            return await avatar.is_visible(timeout=500)
        except Exception:
            return False
    return False


def _connect_error_to_http(exc: Exception) -> HTTPException:
    message = str(exc)
    exc_type = type(exc).__name__
    lowered = message.lower()
    if "executable doesn't exist" in lowered or "playwright install" in lowered:
        return HTTPException(
            status_code=503,
            detail="Chromium browser runtime is not installed. Run: python -m playwright install chromium",
        )
    if "target page, context or browser has been closed" in lowered or "browser has been closed" in lowered:
        return HTTPException(
            status_code=400,
            detail="Browser was closed before login completed. Please try again.",
        )
    if "timeout" in lowered:
        return HTTPException(
            status_code=408,
            detail="Browser login page did not load before timeout. Check network/proxy and try again.",
        )
    if "err_proxy" in lowered or "proxy" in lowered:
        return HTTPException(
            status_code=502,
            detail=f"Proxy connection failed while opening the login browser: {message}",
        )
    if "err_internet_disconnected" in lowered or "err_name_not_resolved" in lowered or "err_connection" in lowered:
        return HTTPException(
            status_code=502,
            detail=f"Network error while opening the login browser: {message}",
        )
    # Fallback: include exception type in response for easier debugging
    detail = f"Browser session failed [{exc_type}]: {message}" if message else f"Browser session failed [{exc_type}] — check backend logs for traceback"
    return HTTPException(status_code=500, detail=detail)

