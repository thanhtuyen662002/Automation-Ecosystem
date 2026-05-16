from __future__ import annotations

import asyncio
import json
import logging
import os
import traceback as _traceback
from datetime import UTC, datetime
from typing import Any

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
    metadata = _merge_browser_provider_metadata(
        request.metadata,
        {
            "browser_provider": request.browser_provider,
            "real_chrome_user_data_dir": request.real_chrome_user_data_dir,
            "real_chrome_debug_port": request.real_chrome_debug_port,
            "adspower_profile_id": request.adspower_profile_id,
        },
    )
    row = await database.create_account(
        platform=request.platform,
        account_handle=request.account_handle,
        profile_url=profile_url,
        external_user_id=request.external_user_id,
        proxy_url=request.proxy_url,
        metadata=metadata,
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
    browser_metadata_updates = {
        key: update_fields.pop(key)
        for key in ("browser_provider", "real_chrome_user_data_dir", "real_chrome_debug_port", "adspower_profile_id")
        if key in update_fields
    }
    if browser_metadata_updates:
        base_metadata = update_fields.get("metadata")
        if base_metadata is None:
            base_metadata = _metadata_dict(existing)
        update_fields["metadata"] = _merge_browser_provider_metadata(base_metadata, browser_metadata_updates)
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
    metadata = _metadata_dict(row)
    browser_provider = str(metadata.get("browser_provider") or "playwright").strip().lower()
    if browser_provider == "adspower":
        browser_provider = "adspower_manual"
    session_status = _session_status_for_row(row, metadata)
    return SessionStatusResponse(
        account_id=account_id,
        session_valid=bool(row.get("session_valid", 0)),
        status=session_status,
        browser_provider=browser_provider,
        has_cookies=bool(row.get("cookies")),
        last_login_at=str(row["last_login_at"]) if row.get("last_login_at") else None,
        user_agent=row.get("user_agent"),
        browser_data_dir=row.get("browser_data_dir"),
        real_chrome_user_data_dir=metadata.get("real_chrome_user_data_dir"),
        adspower_profile_id=metadata.get("adspower_profile_id"),
        timezone=row.get("timezone"),
        locale=row.get("locale"),
    )


@router.post("/{account_id}/connect")
async def connect_account(account_id: str, database: DatabaseDependency) -> Any:
    """
    Launch a visible browser window so the user can log in manually.

    Playwright/Real Chrome open the login page, wait for manual login, and
    save the session. AdsPower Manual only starts the AdsPower profile and
    returns immediately; the app never attaches CDP during the login phase.

    The browser is always visible (non-headless) — credentials are never automated.
    """
    row = await database.get_account(account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Account not found")

    platform = row["platform"]
    metadata = _metadata_dict(row)
    session = await database.get_account_session(account_id) or {}

    try:
        from core.browser_providers import (
            BROWSER_PROVIDER_ADSPOWER_MANUAL,
            resolve_browser_provider,
        )

        browser_provider = resolve_browser_provider({**row, "metadata": metadata})
    except Exception as exc:
        LOGGER.warning(
            "account_connect_provider_resolve_failed",
            extra={"event": "account_connect_provider_resolve_failed", "account_id": account_id, "error": str(exc)},
        )
        raise HTTPException(status_code=500, detail=f"Failed to resolve browser provider: {exc}") from exc

    if browser_provider == BROWSER_PROVIDER_ADSPOWER_MANUAL:
        return await _open_adspower_manual_login(account_id, row, metadata, database)

    try:
        from core.platform_config import get_platform_config, DEFAULT_VIEWPORT
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

    try:
        from core.browser_context import get_browser_data_dir
        from core.browser_providers import (
            BROWSER_PROVIDER_ADSPOWER_MANUAL,
            BROWSER_PROVIDER_PLAYWRIGHT,
            BROWSER_PROVIDER_REAL_CHROME,
            make_browser_provider,
            resolve_browser_provider,
        )

        browser_provider = resolve_browser_provider({**row, "metadata": metadata})
        account_for_provider = {**row, "account_id": account_id, "metadata": metadata}
        browser_data_dir = str(row.get("browser_data_dir") or session.get("browser_data_dir") or get_browser_data_dir(account_id))
        if browser_provider == BROWSER_PROVIDER_PLAYWRIGHT and not row.get("browser_data_dir"):
            await database.set_browser_data_dir(account_id, browser_data_dir)
        identity_profile = None
        if browser_provider not in {BROWSER_PROVIDER_REAL_CHROME, BROWSER_PROVIDER_ADSPOWER_MANUAL}:
            identity_profile = await _ensure_identity_profile(account_id, row, database)
        provider = make_browser_provider(account_for_provider, session=session, identity_profile=identity_profile)
        provider_data_dir = str(getattr(provider, "user_data_dir", browser_data_dir))
    except Exception as exc:
        LOGGER.warning(
            "account_connect_identity_prepare_failed",
            extra={"event": "account_connect_identity_prepare_failed", "account_id": account_id, "error": str(exc)},
        )
        raise HTTPException(status_code=500, detail=f"Failed to prepare account browser identity: {exc}") from exc

    LOGGER.info(
        "account_connect_start",
        extra={
            "event": "account_connect_start",
            "account_id": account_id,
            "platform": platform,
            "browser_provider": browser_provider,
            "user_data_dir": provider_data_dir,
            "browser_data_dir": browser_data_dir if browser_provider == BROWSER_PROVIDER_PLAYWRIGHT else None,
            "timezone": getattr(identity_profile, "timezone", None),
            "locale": getattr(identity_profile, "locale", None),
            "viewport": getattr(identity_profile, "screen_resolution", None),
            "has_proxy": bool(proxy_url),
            "identity_profile_id": getattr(identity_profile, "identity_id", "") or getattr(identity_profile, "fingerprint_hash", "")[:12],
        },
    )

    # Pre-initialize so outer scope is safe even if browser block exits early
    profile: dict = {"avatar_url": "", "display_name": ""}
    cookies: list = []
    fallback_width = int(DEFAULT_VIEWPORT.get("width", 1280))
    fallback_height = int(DEFAULT_VIEWPORT.get("height", 720))
    try:
        fallback_width, fallback_height = [int(part) for part in identity_profile.screen_resolution.split("x", 1)]
    except Exception:
        pass
    fingerprint: dict = {
        "width": fallback_width,
        "height": fallback_height,
        "timezone": getattr(identity_profile, "timezone", "Asia/Ho_Chi_Minh"),
        "locale": getattr(identity_profile, "locale", "vi-VN"),
    }
    user_agent: str = ""

    try:
        from core.browser_providers import collect_runtime_diagnostics
        from core.login_diagnostics import LoginBlockStatus, classify_login_block

        async with async_playwright() as pw:
            connect_session = dict(session)
            connect_session["metadata"] = metadata
            connect_session["browser_data_dir"] = browser_data_dir
            async with provider.open_connect_context(pw) as (context, page, opened_data_dir):

                user_agent = await page.evaluate("navigator.userAgent")
                await page.goto(cfg.login_url, wait_until="domcontentloaded", timeout=60000)
                provider_data_dir = str(opened_data_dir)

                LOGGER.info(
                    "account_connect_browser_open",
                    extra={
                        "event": "account_connect_browser_open",
                        "url": cfg.login_url,
                        "account_id": account_id,
                        "platform": platform,
                        "browser_provider": browser_provider,
                        "user_data_dir": provider_data_dir,
                    },
                )
                runtime = await collect_runtime_diagnostics(page, context)
                LOGGER.info(
                    "account_connect_runtime",
                    extra={
                        "event": "account_connect_runtime",
                        "account_id": account_id,
                        "platform": platform,
                        "browser_provider": browser_provider,
                        "runtime": runtime,
                    },
                )

                # Poll for successful login (max 5 minutes). Do not rely on a single
                # redirect URL; platforms frequently vary post-login destinations.
                deadline = asyncio.get_running_loop().time() + _LOGIN_TIMEOUT_SECONDS
                logged_in = False
                while asyncio.get_running_loop().time() < deadline:
                    try:
                        _ = page.url
                    except Exception:
                        # Browser window was closed by the user before login
                        raise HTTPException(
                            status_code=400,
                            detail="Browser was closed before login completed. Please try again.",
                        )
                    diagnostic = await classify_login_block(page)
                    if diagnostic in {
                        LoginBlockStatus.RATE_LIMITED,
                        LoginBlockStatus.CAPTCHA_REQUIRED,
                        LoginBlockStatus.CHECKPOINT_REQUIRED,
                    }:
                        await _raise_login_block(account_id, platform, diagnostic, database)
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
                        locale: navigator.language || 'vi-VN',
                        width: window.screen.width,
                        height: window.screen.height,
                    })
                """)
                # context.close() is handled by create_connect_context

        # Persist session + fingerprint to DB (also stored in persistent profile dir)
        data_dir = provider_data_dir
        cookies_encrypted = encrypt_cookies(cookies)
        updated = await database.save_account_session(
            account_id,
            cookies_encrypted,
            user_agent,
            viewport_width=fingerprint.get("width") or fallback_width,
            viewport_height=fingerprint.get("height") or fallback_height,
            timezone=fingerprint.get("timezone") or getattr(identity_profile, "timezone", "Asia/Ho_Chi_Minh"),
            locale=fingerprint.get("locale") or getattr(identity_profile, "locale", "vi-VN"),
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Account not found after session save")

        if browser_provider == BROWSER_PROVIDER_REAL_CHROME:
            await database.patch_account_metadata(account_id, {
                "browser_provider": BROWSER_PROVIDER_REAL_CHROME,
                "real_chrome_user_data_dir": data_dir,
            })
        else:
            await database.set_browser_data_dir(account_id, data_dir)
        if browser_provider != BROWSER_PROVIDER_REAL_CHROME and identity_profile is not None:
            await database.save_account_identity_profile(account_id, identity_profile.to_dict())

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
        else:
            fresh = await database.get_account(account_id)
            if fresh:
                updated = fresh

        LOGGER.info(
            "account_connect_success",
            extra={
                "event": "account_connect_success",
                "account_id": account_id,
                "platform": platform,
                "browser_provider": browser_provider,
                "cookie_count": len(cookies),
                "fingerprint": fingerprint,
                "user_data_dir": data_dir,
                "browser_data_dir": data_dir if browser_provider == BROWSER_PROVIDER_PLAYWRIGHT else None,
                "identity_profile_id": getattr(identity_profile, "identity_id", "") or getattr(identity_profile, "fingerprint_hash", "")[:12],
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
        raise _connect_error_to_http(exc, browser_provider=browser_provider) from exc


@router.post("/{account_id}/confirm-manual-login", response_model=AccountResponse)
async def confirm_manual_login(account_id: str, database: DatabaseDependency) -> AccountResponse:
    row = await database.get_account(account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Account not found")

    metadata = _metadata_dict(row)
    from core.browser_providers import (
        BROWSER_PROVIDER_ADSPOWER_MANUAL,
        get_adspower_profile_id,
        resolve_browser_provider,
    )

    browser_provider = resolve_browser_provider({**row, "metadata": metadata})
    if browser_provider != BROWSER_PROVIDER_ADSPOWER_MANUAL:
        raise HTTPException(status_code=422, detail="Manual login confirmation is only for AdsPower Manual accounts")

    profile_id = get_adspower_profile_id({**row, "metadata": metadata})
    if not profile_id:
        raise HTTPException(status_code=422, detail="AdsPower profile id is required")

    diagnostic: dict[str, Any] = {
        "status": "manual_confirmed",
        "provider": BROWSER_PROVIDER_ADSPOWER_MANUAL,
        "confirmed_at": datetime.now(UTC).isoformat(),
        "verification_mode": "user_confirmation",
        "cookies_captured": False,
        "session_source": "adspower_profile",
    }

    if _env_bool("ADSPOWER_VERIFY_AFTER_LOGIN", default=False):
        diagnostic = await _verify_adspower_login_after_confirmation(account_id, row, metadata, database)
        if not isinstance(diagnostic, dict):
            raise HTTPException(status_code=500, detail="AdsPower verification returned no diagnostic")

    updated = await database.mark_account_manual_login_confirmed(
        account_id,
        browser_provider=BROWSER_PROVIDER_ADSPOWER_MANUAL,
        metadata_patch={
            "browser_provider": BROWSER_PROVIDER_ADSPOWER_MANUAL,
            "adspower_profile_id": profile_id,
            "manual_login_state": "connected_by_confirmation",
            "last_login_diagnostic": diagnostic,
        },
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Account not found after manual login confirmation")

    LOGGER.info(
        "account_manual_login_confirmed",
        extra={
            "event": "account_manual_login_confirmed",
            "account_id": account_id,
            "platform": row["platform"],
            "browser_provider": BROWSER_PROVIDER_ADSPOWER_MANUAL,
            "adspower_profile_id": profile_id,
            "verified": diagnostic.get("verification_mode") == "cdp_after_confirmation",
        },
    )
    return AccountResponse.from_row(updated)


@router.get("/{account_id}/browser-diagnostic")
async def get_browser_diagnostic(account_id: str, database: DatabaseDependency) -> dict[str, Any]:
    if not _browser_diagnostics_enabled():
        raise HTTPException(status_code=404, detail="Browser diagnostics are disabled")
    row = await database.get_account(account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Account not found")
    metadata = _metadata_dict(row)

    from core.browser_providers import (
        BROWSER_PROVIDER_ADSPOWER_MANUAL,
        make_browser_provider,
        resolve_browser_provider,
        collect_runtime_diagnostics,
    )
    from core.platform_config import get_platform_config
    from playwright.async_api import async_playwright

    browser_provider = resolve_browser_provider({**row, "metadata": metadata})
    if browser_provider == BROWSER_PROVIDER_ADSPOWER_MANUAL and not bool(row.get("session_valid", 0)):
        raise HTTPException(status_code=409, detail="Confirm manual login before running AdsPower diagnostics")

    cfg = get_platform_config(row["platform"])
    session = await database.get_account_session(account_id) or {}
    account_for_provider = {**row, "account_id": account_id, "metadata": metadata}
    provider = make_browser_provider(account_for_provider, session=session, identity_profile=None)
    async with async_playwright() as pw:
        try:
            async with provider.open_publisher_context(pw, headless=False) as (context, page, opened_data_dir):
                target_url = "https://www.tiktok.com/" if row["platform"].lower() == "tiktok" else cfg.login_url
                await page.goto(target_url, wait_until="domcontentloaded", timeout=30_000)
                runtime = await collect_runtime_diagnostics(page, context)
                return {
                    "account_id": account_id,
                    "platform": row["platform"],
                    "browser_provider": browser_provider,
                    "profile": str(opened_data_dir),
                    "diagnostic": runtime,
                }
        except Exception as exc:
            error_detail = getattr(exc, "detail", "")
            raise HTTPException(
                status_code=502,
                detail=f"AdsPower returned no valid Chrome DevTools endpoint. Error: {exc} {error_detail}"
            ) from exc


async def _open_adspower_manual_login(
    account_id: str,
    row: dict,
    metadata: dict,
    database,
) -> dict[str, Any]:
    from core.adspower_client import AdsPowerClient, AdsPowerClientError
    from core.browser_providers import BROWSER_PROVIDER_ADSPOWER_MANUAL, get_adspower_profile_id

    profile_id = get_adspower_profile_id({**row, "metadata": metadata})
    if not profile_id:
        raise HTTPException(status_code=422, detail="AdsPower profile id is required")

    client = AdsPowerClient()
    try:
        started = await client.start_profile(profile_id)
    except AdsPowerClientError as exc:
        LOGGER.warning(
            "adspower_manual_open_failed",
            extra={
                "event": "adspower_manual_open_failed",
                "account_id": account_id,
                "platform": row["platform"],
                "browser_provider": BROWSER_PROVIDER_ADSPOWER_MANUAL,
                "adspower_profile_id": profile_id,
                "error_code": exc.code,
            },
        )
        raise _adspower_error_to_http(exc) from exc

    await database.patch_account_metadata(
        account_id,
        {
            "browser_provider": BROWSER_PROVIDER_ADSPOWER_MANUAL,
            "adspower_profile_id": profile_id,
            "manual_login_state": "browser_opened",
            "last_login_diagnostic": {
                "status": "browser_opened",
                "provider": BROWSER_PROVIDER_ADSPOWER_MANUAL,
                "opened_at": datetime.now(UTC).isoformat(),
                "has_cdp_endpoint": bool(started.debug_endpoint),
            },
        },
    )

    LOGGER.info(
        "adspower_manual_browser_opened",
        extra={
            "event": "adspower_manual_browser_opened",
            "account_id": account_id,
            "platform": row["platform"],
            "browser_provider": BROWSER_PROVIDER_ADSPOWER_MANUAL,
            "adspower_profile_id": profile_id,
        },
    )
    return {
        "account_id": account_id,
        "platform": row["platform"],
        "browser_provider": BROWSER_PROVIDER_ADSPOWER_MANUAL,
        "adspower_profile_id": profile_id,
        "status": "browser_opened",
        "message": "AdsPower profile opened. Please login manually, then click Confirm Login.",
    }


async def _verify_adspower_login_after_confirmation(
    account_id: str,
    row: dict,
    metadata: dict,
    database,
) -> dict[str, Any]:
    from core.browser_providers import (
        BROWSER_PROVIDER_ADSPOWER_MANUAL,
        collect_runtime_diagnostics,
        make_browser_provider,
    )
    from core.login_diagnostics import LoginBlockStatus, classify_login_block
    from core.platform_config import get_platform_config
    from core.session_crypto import encrypt_cookies
    from playwright.async_api import async_playwright

    cfg = get_platform_config(row["platform"])
    session = await database.get_account_session(account_id) or {}
    provider = make_browser_provider(
        {**row, "account_id": account_id, "metadata": metadata},
        session=session,
    )

    LOGGER.info(
        "account_manual_login_verify_start",
        extra={
            "event": "account_manual_login_verify_start",
            "account_id": account_id,
            "platform": row["platform"],
        }
    )

    try:
        async with async_playwright() as pw:
            async with provider.open_publisher_context(pw, headless=False) as (context, page, _opened_data_dir):
                target_url = "https://www.tiktok.com/" if row["platform"].lower() == "tiktok" else cfg.login_url
                await page.goto(target_url, wait_until="domcontentloaded", timeout=30_000)

                diagnostic_status = await classify_login_block(page)
                if diagnostic_status in {
                    LoginBlockStatus.RATE_LIMITED,
                    LoginBlockStatus.CAPTCHA_REQUIRED,
                    LoginBlockStatus.CHECKPOINT_REQUIRED,
                }:
                    LOGGER.warning(
                        "account_manual_login_verify_blocked",
                        extra={
                            "event": "account_manual_login_verify_blocked",
                            "account_id": account_id,
                            "platform": row["platform"],
                            "diagnostic_status": diagnostic_status,
                        }
                    )
                    await _raise_login_block(account_id, row["platform"], diagnostic_status, database)

                runtime = await collect_runtime_diagnostics(page, context)

                try:
                    cookies = await context.cookies()
                except Exception:
                    cookies = []

                logged_in = await _looks_logged_in(page, context, row["platform"], cfg.success_url_fragment)

                diagnostic = {
                    "status": "verified" if logged_in else "not_connected",
                    "provider": BROWSER_PROVIDER_ADSPOWER_MANUAL,
                    "verified_at": datetime.now(UTC).isoformat(),
                    "verification_mode": "cdp_after_confirmation",
                    "cookies_captured": bool(cookies),
                    "session_source": "adspower_profile",
                    "runtime": runtime,
                }

                if not logged_in:
                    LOGGER.warning(
                        "account_manual_login_verify_not_connected",
                        extra={
                            "event": "account_manual_login_verify_not_connected",
                            "account_id": account_id,
                            "platform": row["platform"],
                        }
                    )
                    await database.patch_account_metadata(
                        account_id,
                        {
                            "manual_login_state": "verification_failed",
                            "last_login_diagnostic": diagnostic,
                        },
                    )
                    raise HTTPException(status_code=409, detail="SESSION_NOT_CONNECTED")

                if cookies:
                    try:
                        user_agent = await page.evaluate("navigator.userAgent")
                    except Exception:
                        user_agent = ""

                    try:
                        fingerprint = await page.evaluate("""
                            () => ({
                                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                                locale: navigator.language || 'vi-VN',
                                width: window.screen.width,
                                height: window.screen.height,
                            })
                        """)
                    except Exception:
                        fingerprint = {}

                    await database.save_account_session(
                        account_id,
                        encrypt_cookies(cookies),
                        user_agent,
                        viewport_width=fingerprint.get("width") or row.get("viewport_width") or 1280,
                        viewport_height=fingerprint.get("height") or row.get("viewport_height") or 720,
                        timezone=fingerprint.get("timezone") or row.get("timezone") or "Asia/Ho_Chi_Minh",
                        locale=fingerprint.get("locale") or row.get("locale") or "vi-VN",
                    )

                LOGGER.info(
                    "account_manual_login_verify_success",
                    extra={
                        "event": "account_manual_login_verify_success",
                        "account_id": account_id,
                        "platform": row["platform"],
                        "cookies_captured": bool(cookies),
                    }
                )
                return diagnostic

    except HTTPException:
        raise
    except Exception as exc:
        error_detail = getattr(exc, "detail", "")
        LOGGER.error(
            "account_manual_login_verify_error",
            extra={
                "event": "account_manual_login_verify_error",
                "account_id": account_id,
                "error": str(exc),
                "error_detail": error_detail,
            }
        )
        raise HTTPException(
            status_code=502,
            detail=f"AdsPower returned no valid Chrome DevTools endpoint. Error: {exc} {error_detail}",
        ) from exc


def _metadata_dict(row: dict) -> dict:
    metadata = row.get("metadata")
    if isinstance(metadata, dict):
        return metadata
    if isinstance(metadata, str):
        try:
            parsed = json.loads(metadata)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _session_status_for_row(row: dict, metadata: dict) -> str:
    browser_provider = str(metadata.get("browser_provider") or "playwright").strip().lower()
    if browser_provider == "adspower":
        browser_provider = "adspower_manual"
    manual_connected = (
        browser_provider == "adspower_manual"
        and metadata.get("manual_login_state") == "connected_by_confirmation"
        and bool(row.get("session_valid", 0))
    )
    if manual_connected:
        diagnostic = metadata.get("last_login_diagnostic")
        diagnostic_status = str(diagnostic.get("status") if isinstance(diagnostic, dict) else "").upper()
        if diagnostic_status in {"RATE_LIMITED", "CAPTCHA_REQUIRED", "CHECKPOINT_REQUIRED"}:
            return "limited"
        return "connected"
    if row.get("status") == "limited":
        return "limited"
    if bool(row.get("session_valid", 0)):
        return "connected"
    if row.get("last_login_at"):
        return "expired"
    return "not_connected"


def _merge_browser_provider_metadata(metadata: dict | None, values: dict) -> dict:
    merged = dict(metadata or {})
    for key, value in values.items():
        if key == "browser_provider" and isinstance(value, str) and value.strip().lower() == "adspower":
            value = "adspower_manual"
        if value is None:
            if key in {"real_chrome_user_data_dir", "real_chrome_debug_port", "adspower_profile_id"}:
                merged.pop(key, None)
            continue
        merged[key] = value
    if not merged.get("browser_provider"):
        merged["browser_provider"] = "playwright"
    return merged


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _browser_diagnostics_enabled() -> bool:
    return _env_bool("BROWSER_DIAGNOSTICS_ENABLED", default=os.environ.get("APP_ENV") == "development")


def _adspower_error_to_http(exc: Exception) -> HTTPException:
    from core.adspower_client import (
        ADSPOWER_API_UNAVAILABLE,
        ADSPOWER_BROWSER_UPDATING,
        ADSPOWER_PROFILE_ID_MISSING,
        ADSPOWER_PROFILE_NOT_FOUND,
        ADSPOWER_START_FAILED,
        ADSPOWER_TIMEOUT,
    )

    code = getattr(exc, "code", ADSPOWER_START_FAILED)
    detail = str(exc)
    if code == ADSPOWER_PROFILE_ID_MISSING:
        return HTTPException(status_code=422, detail=detail)
    if code == ADSPOWER_PROFILE_NOT_FOUND:
        return HTTPException(status_code=404, detail=detail)
    if code == ADSPOWER_API_UNAVAILABLE:
        return HTTPException(status_code=503, detail=detail)
    if code == ADSPOWER_TIMEOUT:
        return HTTPException(status_code=504, detail=detail)
    if code == ADSPOWER_BROWSER_UPDATING:
        return HTTPException(
            status_code=409,
            detail=(
                "AdsPower is downloading/updating FlowerBrowser for this profile. "
                "Open AdsPower and wait until the browser core finishes downloading, then try again."
            ),
        )
    return HTTPException(status_code=502, detail=detail)


async def _ensure_identity_profile(account_id: str, row: dict, database):
    from core.identity_manager import get_identity_registry

    registry = get_identity_registry()
    persisted = await database.get_account_identity_profile(account_id)
    if persisted:
        registry.load_profiles({account_id: persisted})
        profile = registry.get(account_id)
        if profile is not None:
            desired_proxy_url = row.get("proxy_url") or None
            desired_proxy_country = row.get("proxy_country") or None
            if profile.proxy_url != desired_proxy_url or profile.proxy_country != desired_proxy_country:
                profile.proxy_url = desired_proxy_url
                profile.proxy_country = desired_proxy_country
                await database.save_account_identity_profile(account_id, profile.to_dict())
            return profile

    desired_proxy_url = row.get("proxy_url") or None
    desired_proxy_country = row.get("proxy_country") or None
    profile = registry.get_or_create(
        account_id,
        proxy_url=desired_proxy_url,
        proxy_country=desired_proxy_country,
    )
    profile.proxy_url = desired_proxy_url
    profile.proxy_country = desired_proxy_country
    await database.save_account_identity_profile(account_id, profile.to_dict())
    return profile


async def _raise_login_block(account_id: str, platform: str, diagnostic, database) -> None:
    from core.login_diagnostics import LoginBlockStatus, login_block_error_message

    event_by_status = {
        LoginBlockStatus.RATE_LIMITED: "tiktok_login_rate_limited",
        LoginBlockStatus.CAPTCHA_REQUIRED: "tiktok_checkpoint_required",
        LoginBlockStatus.CHECKPOINT_REQUIRED: "tiktok_checkpoint_required",
    }
    event = event_by_status.get(diagnostic, "account_login_blocked")
    status_code = 429 if diagnostic == LoginBlockStatus.RATE_LIMITED else 403
    detail = login_block_error_message(diagnostic)

    await database.record_login_diagnostic(
        account_id,
        diagnostic.value,
        platform=platform,
        status="limited",
    )
    LOGGER.warning(
        event,
        extra={
            "event": event,
            "account_id": account_id,
            "platform": platform,
            "diagnostic": diagnostic.value,
        },
    )
    raise HTTPException(status_code=status_code, detail=detail)


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


def _connect_error_to_http(exc: Exception, *, browser_provider: str = "playwright") -> HTTPException:
    message = str(exc)
    exc_type = type(exc).__name__
    lowered = message.lower()
    if "executable doesn't exist" in lowered or "playwright install" in lowered or "chrome" in lowered and "not found" in lowered:
        if browser_provider == "real_chrome":
            return HTTPException(
                status_code=503,
                detail=(
                    "Chrome Stable was not found for Real Chrome provider. "
                    "Install Google Chrome or set CHROME_EXECUTABLE_PATH to chrome.exe."
                ),
            )
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

