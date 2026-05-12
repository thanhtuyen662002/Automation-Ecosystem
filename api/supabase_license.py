"""
Supabase license authority client.

Packaged desktop builds must not use SUPABASE_SERVICE_KEY locally. This module
talks to a Supabase Edge Function that owns service-role access and returns a
rotating refresh token. The local FastAPI backend then issues its own short
local access token for requests on 127.0.0.1.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import HTTPException


LOGGER = logging.getLogger("api.supabase_license")


class LicenseAuthorityUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class LicenseAuthorityResult:
    license_key: str
    account: str
    role: str
    max_accounts: int
    activation_id: str | None
    refresh_token: str | None
    refresh_expires_at: str | None
    expires_at: str | None
    offline_grace_until: str | None
    app_config: dict[str, Any]

    @classmethod
    def from_response(cls, data: dict[str, Any], *, fallback_account: str = "") -> "LicenseAuthorityResult":
        user = data.get("user") if isinstance(data.get("user"), dict) else {}
        return cls(
            license_key=str(data.get("license_key") or user.get("license_key") or ""),
            account=str(data.get("account") or user.get("account") or fallback_account),
            role=str(data.get("role") or user.get("role") or "operator"),
            max_accounts=int(data.get("max_accounts") or user.get("max_accounts") or 10),
            activation_id=data.get("activation_id"),
            refresh_token=data.get("refresh_token"),
            refresh_expires_at=data.get("refresh_expires_at"),
            expires_at=data.get("expires_at"),
            offline_grace_until=data.get("offline_grace_until"),
            app_config=data.get("app_config") if isinstance(data.get("app_config"), dict) else {},
        )


def _authority_url() -> str:
    explicit = os.getenv("LICENSE_AUTHORITY_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    supabase_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    if not supabase_url:
        return ""
    function_name = os.getenv("LICENSE_AUTH_FUNCTION", "license-auth").strip() or "license-auth"
    return f"{supabase_url}/functions/v1/{function_name}"


def _public_key() -> str:
    return (
        os.getenv("LICENSE_AUTHORITY_PUBLIC_KEY", "").strip()
        or os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip()
        or os.getenv("SUPABASE_ANON_KEY", "").strip()
        or os.getenv("SUPABASE_KEY", "").strip()
        # Prefer the anon key above. Service key is used as last-resort fallback
        # because this backend runs server-side (never exposed to the browser).
        or os.getenv("SUPABASE_SERVICE_KEY", "").strip()
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    )


def is_supabase_configured() -> bool:
    """Return True when a remote license authority endpoint is configured."""
    flag = os.getenv("LICENSE_AUTHORITY_ENABLED", "").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    if os.getenv("LICENSE_AUTHORITY_URL", "").strip():
        return True
    return bool(os.getenv("SUPABASE_URL", "").strip() and _public_key())


def _headers() -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Automation-Ecosystem-Desktop",
    }
    public_key = _public_key()
    if public_key:
        headers["apikey"] = public_key
        headers["Authorization"] = f"Bearer {public_key}"
    return headers


async def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    base = _authority_url()
    if not base:
        raise LicenseAuthorityUnavailable("License authority is not configured")
    url = f"{base}/{path.lstrip('/')}"
    timeout = httpx.Timeout(float(os.getenv("LICENSE_AUTHORITY_TIMEOUT_SECONDS", "15")))
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=_headers(), json=payload)
    except httpx.RequestError as exc:
        raise LicenseAuthorityUnavailable(str(exc)) from exc

    try:
        body = response.json() if response.content else {}
    except ValueError:
        body = {}
    if 500 <= response.status_code <= 599:
        message = body.get("message") if isinstance(body, dict) else None
        raise LicenseAuthorityUnavailable(message or f"License authority HTTP {response.status_code}")
    if response.status_code >= 400:
        message = (body.get("message") or body.get("error")) if isinstance(body, dict) else None
        raise HTTPException(response.status_code, message or "License authority rejected the request")
    if not isinstance(body, dict):
        raise LicenseAuthorityUnavailable("License authority returned an invalid response")
    return body


async def activate(
    *,
    account: str,
    license_key: str,
    machine_fp: str,
    ip: str,
) -> LicenseAuthorityResult:
    data = await _post(
        "activate",
        {
            "account": account,
            "license_key": license_key,
            "machine_id": machine_fp,
            "ip": ip,
            "app_version": os.getenv("APP_VERSION", "0.1.0"),
        },
    )
    result = LicenseAuthorityResult.from_response(data, fallback_account=account)
    if not result.license_key or not result.refresh_token:
        raise LicenseAuthorityUnavailable("License authority activation response is missing required fields")
    return result


async def refresh(
    *,
    refresh_token: str,
    machine_fp: str,
    activation_id: str | None = None,
) -> LicenseAuthorityResult:
    data = await _post(
        "refresh",
        {
            "refresh_token": refresh_token,
            "activation_id": activation_id,
            "machine_id": machine_fp,
            "app_version": os.getenv("APP_VERSION", "0.1.0"),
        },
    )
    result = LicenseAuthorityResult.from_response(data)
    if not result.license_key:
        raise LicenseAuthorityUnavailable("License authority refresh response is missing license_key")
    return result


async def heartbeat(
    *,
    refresh_token: str,
    machine_fp: str,
    activation_id: str | None = None,
) -> None:
    await _post(
        "heartbeat",
        {
            "refresh_token": refresh_token,
            "activation_id": activation_id,
            "machine_id": machine_fp,
            "app_version": os.getenv("APP_VERSION", "0.1.0"),
        },
    )


async def logout(refresh_token: str) -> None:
    try:
        await _post("logout", {"refresh_token": refresh_token})
    except LicenseAuthorityUnavailable as exc:
        LOGGER.info("license_authority_logout_skipped", extra={"event": "license_authority_logout_skipped", "error": str(exc)})
