from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

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
        
        endpoint, source, rejected = await _resolve_cdp_endpoint(data, profile_id=profile_id)
        if not endpoint:
            ws_keys = list(data.get("ws").keys()) if isinstance(data.get("ws"), dict) else []
            raise AdsPowerClientError(
                ADSPOWER_START_FAILED,
                "AdsPower profile started but did not return a valid CDP endpoint",
                detail={
                    "profile_id": profile_id,
                    "data_keys": list(data.keys()),
                    "ws_keys": ws_keys,
                    "rejected_candidates": rejected,
                },
            )
        
        ws_keys = list(data.get("ws").keys()) if isinstance(data.get("ws"), dict) else []
        safe_endpoint = _safe_endpoint(endpoint)
                
        LOGGER.info(
            "adspower_debug_endpoint_selected",
            extra={
                "event": "adspower_debug_endpoint_selected",
                "profile_id": profile_id,
                "selected_source": source,
                "selected_endpoint_safe": safe_endpoint,
                "data_keys": list(data.keys()),
                "ws_keys": ws_keys,
                "rejected_count": len(rejected),
            }
        )

        return AdsPowerProfileStartResult(
            profile_id=profile_id,
            debug_endpoint=endpoint,
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


async def _probe_http_cdp_endpoint(endpoint: str) -> str | None:
    url = endpoint.rstrip("/") + "/json/version"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return None
        data = resp.json()
        ws_url = data.get("webSocketDebuggerUrl")
        return ws_url if isinstance(ws_url, str) and ws_url.startswith(("ws://", "wss://")) else None
    except Exception:
        return None

def _safe_endpoint(endpoint: str | None) -> str | None:
    if not endpoint:
        return None
    parsed = urlparse(endpoint)
    if parsed.query:
        return endpoint.replace(parsed.query, "TOKEN_HIDDEN")
    return endpoint

async def _resolve_cdp_endpoint(data: dict[str, Any], profile_id: str) -> tuple[str | None, str, list[dict[str, Any]]]:
    ws = data.get("ws")
    
    if isinstance(ws, dict):
        puppeteer_val = _to_optional_str(ws.get("puppeteer"))
        if puppeteer_val:
            LOGGER.info(
                "adspower_ws_puppeteer_debug",
                extra={
                    "event": "adspower_ws_puppeteer_debug",
                    "ws_puppeteer_present": True,
                    "ws_puppeteer_safe": _safe_endpoint(puppeteer_val)
                }
            )

    candidates: list[tuple[str, Any]] = []

    if isinstance(ws, dict):
        candidates.append(("ws.puppeteer", ws.get("puppeteer")))
    candidates.append(("puppeteer", data.get("puppeteer")))
    if isinstance(ws, dict):
        candidates.append(("ws.cdp", ws.get("cdp")))
    candidates.append(("cdp", data.get("cdp")))
    candidates.append(("debug_endpoint", data.get("debug_endpoint")))
    candidates.append(("debugEndpoint", data.get("debugEndpoint")))
    if isinstance(ws, str):
        candidates.append(("ws", ws))
    if isinstance(ws, dict):
        candidates.append(("ws.playwright", ws.get("playwright")))
    
    debug_port = _to_optional_str(data.get("debug_port") or data.get("debugPort"))
    if debug_port:
        try:
            port = int(debug_port)
            candidates.append(("debug_port", f"http://127.0.0.1:{port}"))
        except ValueError:
            pass

    rejected = []

    for source, value in candidates:
        endpoint = _to_optional_str(value)
        if not endpoint:
            continue

        if source == "ws.selenium":
            reason = "Selenium endpoints are not CDP compatible"
            rejected.append({"source": source, "endpoint_safe": _safe_endpoint(endpoint), "reason": reason})
            continue

        text = endpoint.strip()
        parsed = urlparse(text)
        path = (parsed.path or "").lower()

        if path.rstrip("/") == "/session" or "/session/" in path:
            reason = "Endpoint has /session path (Playwright/Selenium style, not CDP)"
            rejected.append({"source": source, "endpoint_safe": _safe_endpoint(endpoint), "reason": reason})
            LOGGER.info(
                "adspower_cdp_candidate_rejected",
                extra={
                    "event": "adspower_cdp_candidate_rejected",
                    "profile_id": profile_id,
                    "source": source,
                    "endpoint_safe": _safe_endpoint(endpoint),
                    "reason": reason,
                }
            )
            continue

        if text.startswith(("ws://", "wss://")):
            return text, source, rejected

        if text.startswith(("http://", "https://")):
            ws_url = await _probe_http_cdp_endpoint(text)
            if ws_url:
                return ws_url, f"{source}.json_version", rejected
            reason = "/json/version probe failed or did not return webSocketDebuggerUrl"
            rejected.append({"source": source, "endpoint_safe": _safe_endpoint(endpoint), "reason": reason})
            
            LOGGER.info(
                "adspower_cdp_candidate_rejected",
                extra={
                    "event": "adspower_cdp_candidate_rejected",
                    "profile_id": profile_id,
                    "source": source,
                    "endpoint_safe": _safe_endpoint(endpoint),
                    "reason": reason,
                }
            )
            continue

    return None, "none", rejected
