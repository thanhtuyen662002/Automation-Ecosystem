from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

LOGGER = logging.getLogger("core.adspower_client")

ADSPOWER_NOT_CONFIGURED = "ADSPOWER_NOT_CONFIGURED"
ADSPOWER_PROFILE_ID_MISSING = "ADSPOWER_PROFILE_ID_MISSING"
ADSPOWER_START_FAILED = "ADSPOWER_START_FAILED"
ADSPOWER_PROFILE_NOT_FOUND = "ADSPOWER_PROFILE_NOT_FOUND"
ADSPOWER_API_UNAVAILABLE = "ADSPOWER_API_UNAVAILABLE"
ADSPOWER_TIMEOUT = "ADSPOWER_TIMEOUT"
ADSPOWER_BROWSER_UPDATING = "ADSPOWER_BROWSER_UPDATING"


class AdsPowerClientError(RuntimeError):
    def __init__(self, code: str, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


@dataclass(frozen=True)
class AdsPowerSettings:
    api_base: str = "http://local.adspower.net:50325"
    api_key: str | None = None
    open_timeout_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> "AdsPowerSettings":
        api_base = os.environ.get("ADSPOWER_API_BASE", "http://local.adspower.net:50325").strip()
        api_key = os.environ.get("ADSPOWER_API_KEY", "").strip() or None
        raw_timeout = os.environ.get("ADSPOWER_OPEN_TIMEOUT_SECONDS", "60").strip()
        try:
            timeout = float(raw_timeout)
        except ValueError:
            timeout = 60.0
        return cls(api_base=api_base.rstrip("/"), api_key=api_key, open_timeout_seconds=max(timeout, 1.0))


@dataclass(frozen=True)
class AdsPowerProfileStartResult:
    profile_id: str
    debug_endpoint: str | None
    debug_port: str | None
    webdriver: str | None
    raw_status: str


class AdsPowerClient:
    def __init__(self, settings: AdsPowerSettings | None = None) -> None:
        self.settings = settings or AdsPowerSettings.from_env()
        if not self.settings.api_base:
            raise AdsPowerClientError(ADSPOWER_NOT_CONFIGURED, "ADSPOWER_API_BASE is empty")

    async def start_profile(self, profile_id: str) -> AdsPowerProfileStartResult:
        profile_id = _require_profile_id(profile_id)
        payload = await self._get(
            "/api/v1/browser/start",
            params={"user_id": profile_id},
            timeout_seconds=self.settings.open_timeout_seconds,
        )
        self._ensure_success(payload, default_code=ADSPOWER_START_FAILED)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        return AdsPowerProfileStartResult(
            profile_id=profile_id,
            debug_endpoint=_extract_debug_endpoint(data),
            debug_port=_to_optional_str(data.get("debug_port") or data.get("debugPort")),
            webdriver=_to_optional_str(data.get("webdriver")),
            raw_status=str(payload.get("msg") or payload.get("message") or "success"),
        )

    async def stop_profile(self, profile_id: str) -> dict[str, Any]:
        profile_id = _require_profile_id(profile_id)
        payload = await self._get(
            "/api/v1/browser/stop",
            params={"user_id": profile_id},
            timeout_seconds=15.0,
        )
        self._ensure_success(payload, default_code=ADSPOWER_START_FAILED)
        return payload

    async def get_profile_status(self, profile_id: str) -> dict[str, Any]:
        profile_id = _require_profile_id(profile_id)
        payload = await self._get(
            "/api/v1/browser/active",
            params={"user_id": profile_id},
            timeout_seconds=10.0,
        )
        self._ensure_success(payload, default_code=ADSPOWER_PROFILE_NOT_FOUND)
        return payload

    async def get_debug_endpoint(self, profile_id: str) -> str:
        started = await self.start_profile(profile_id)
        if not started.debug_endpoint:
            raise AdsPowerClientError(
                ADSPOWER_START_FAILED,
                "AdsPower profile started but did not return a CDP endpoint",
                detail={"profile_id": profile_id},
            )
        return started.debug_endpoint

    async def test_connection(self) -> bool:
        try:
            async with self._client(timeout_seconds=5.0) as client:
                response = await client.get(f"{self.settings.api_base}/")
                return response.status_code < 500
        except (httpx.ConnectError, httpx.NetworkError, httpx.TimeoutException):
            return False

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        try:
            async with self._client(timeout_seconds=timeout_seconds) as client:
                response = await client.get(f"{self.settings.api_base}{path}", params=params)
        except httpx.TimeoutException as exc:
            raise AdsPowerClientError(ADSPOWER_TIMEOUT, "AdsPower local API timed out") from exc
        except (httpx.ConnectError, httpx.NetworkError, httpx.HTTPError) as exc:
            raise AdsPowerClientError(ADSPOWER_API_UNAVAILABLE, "AdsPower local API is unavailable") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise AdsPowerClientError(
                ADSPOWER_API_UNAVAILABLE,
                f"AdsPower local API returned non-JSON response ({response.status_code})",
            ) from exc

        if response.status_code == 404:
            raise AdsPowerClientError(ADSPOWER_PROFILE_NOT_FOUND, "AdsPower profile was not found")
        if response.status_code >= 500:
            raise AdsPowerClientError(ADSPOWER_API_UNAVAILABLE, "AdsPower local API returned a server error")
        if response.status_code >= 400:
            raise AdsPowerClientError(ADSPOWER_START_FAILED, "AdsPower local API rejected the request")
        return payload if isinstance(payload, dict) else {}

    def _client(self, *, timeout_seconds: float) -> httpx.AsyncClient:
        headers: dict[str, str] = {}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
            headers["X-API-Key"] = self.settings.api_key
        return httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds), headers=headers)

    @staticmethod
    def _ensure_success(payload: dict[str, Any], *, default_code: str) -> None:
        code = payload.get("code")
        if code in (0, "0", None) and str(payload.get("status", "")).lower() not in {"error", "failed"}:
            return
        message = str(payload.get("msg") or payload.get("message") or "AdsPower request failed")
        lowered = message.lower()
        if (
            "is updating" in lowered
            or "waiting for download" in lowered
            or "flowerbrowser" in lowered
            or "browser is updating" in lowered
        ):
            raise AdsPowerClientError(
                ADSPOWER_BROWSER_UPDATING,
                message,
                detail={"action": "wait_or_download_browser_core"},
            )
        error_code = ADSPOWER_PROFILE_NOT_FOUND if "not found" in lowered or "not exist" in lowered else default_code
        raise AdsPowerClientError(error_code, message)


def _require_profile_id(profile_id: str) -> str:
    clean = str(profile_id or "").strip()
    if not clean:
        raise AdsPowerClientError(ADSPOWER_PROFILE_ID_MISSING, "AdsPower profile id is required")
    return clean


def _to_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_debug_endpoint(data: dict[str, Any]) -> str | None:
    ws = data.get("ws")
    if isinstance(ws, dict):
        for key in ("playwright", "puppeteer", "selenium"):
            endpoint = _to_optional_str(ws.get(key))
            if endpoint:
                return endpoint
    for key in ("ws", "debug_endpoint", "debugEndpoint", "cdp", "puppeteer"):
        endpoint = _to_optional_str(data.get(key))
        if endpoint and endpoint.startswith(("ws://", "wss://", "http://", "https://")):
            return endpoint
    return None
