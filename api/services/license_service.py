from __future__ import annotations

import logging
import os
import socket
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from core.license_key import get_license_key_prefix, mask_license_key, normalize_license_key
from core.license_status import ERROR_STATUSES, NORMAL_CACHEABLE_STATUSES, LicenseStatus, parse_license_status
from core.license_store import clear_license_state, read_license_state, write_license_state
from core.machine_id import get_local_machine_fingerprint_hash, get_machine_id, get_platform_label


LOGGER = logging.getLogger("api.license_service")


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat()


def parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True)
class LicenseApiResponse:
    ok: bool
    licensed: bool
    status: LicenseStatus
    reason: str | None = None
    license: dict[str, Any] | None = None
    device: dict[str, Any] | None = None
    offline_valid_until: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "licensed": self.licensed,
            "status": self.status.value,
            "reason": self.reason,
            "license": self.license,
            "device": self.device,
            "offline_valid_until": self.offline_valid_until,
        }


@dataclass
class _CacheEntry:
    response: LicenseApiResponse
    expires_at_monotonic: float


class LicenseEdgeClient:
    """Client for the trusted Supabase Edge Function license backend."""

    def __init__(self, license_api_url: str, anon_key: str, timeout_seconds: float = 15.0) -> None:
        self._url = license_api_url.rstrip("/")
        self._headers = {
            "apikey": anon_key,
            "Authorization": f"Bearer {anon_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self._timeout = httpx.Timeout(timeout_seconds)

    async def call(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = {"action": action, **payload}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self._url, headers=self._headers, json=body)
        data: Any
        try:
            data = response.json()
        except ValueError as exc:
            if response.status_code >= 500:
                raise RuntimeError(f"License API server error: {response.status_code}") from exc
            raise RuntimeError("License API returned invalid JSON") from exc
        if response.status_code >= 500:
            raise RuntimeError(f"License API server error: {response.status_code}")
        if not isinstance(data, dict):
            raise RuntimeError("License API returned an invalid payload")
        return data


class LicenseService:
    def __init__(
        self,
        client: LicenseEdgeClient,
        *,
        offline_grace_days: int,
        cache_ttl_seconds: int,
        error_cache_ttl_seconds: int = 5,
    ) -> None:
        self._client = client
        self._offline_grace_days = max(0, offline_grace_days)
        self._cache_ttl_seconds = max(1, cache_ttl_seconds)
        self._error_cache_ttl_seconds = max(0, min(5, error_cache_ttl_seconds))
        self._cache: _CacheEntry | None = None

    @classmethod
    def from_env(cls) -> "LicenseService":
        supabase_url = os.getenv("SUPABASE_URL", "").strip()
        anon_key = os.getenv("SUPABASE_ANON_KEY", "").strip()
        missing = [
            name
            for name, value in {
                "SUPABASE_URL": supabase_url,
                "SUPABASE_ANON_KEY": anon_key,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(f"Missing required license environment variables: {', '.join(missing)}")
        license_api_url = os.getenv("LICENSE_API_URL", "").strip()
        if not license_api_url:
            license_api_url = f"{supabase_url.rstrip('/')}/functions/v1/license-api"
        return cls(
            LicenseEdgeClient(license_api_url, anon_key),
            offline_grace_days=_int_env("LICENSE_OFFLINE_GRACE_DAYS", 7),
            cache_ttl_seconds=_int_env("LICENSE_STATUS_CACHE_TTL_SECONDS", 30),
        )

    def invalidate_cache(self) -> None:
        self._cache = None

    async def activate_license(
        self,
        *,
        license_key: str,
        device_name: str | None = None,
        app_version: str | None = None,
    ) -> LicenseApiResponse:
        self.invalidate_cache()
        normalized = normalize_license_key(license_key)
        if not normalized:
            return self._response(False, False, LicenseStatus.INVALID_KEY, "License key is required")

        try:
            response = self._edge_response(
                await self._client.call(
                    "activate",
                    {
                        "license_key": normalized,
                        "machine_fingerprint": get_machine_id(),
                        "device_name": device_name,
                        "platform": get_platform_label(),
                        "app_version": app_version,
                        "metadata": _client_metadata(normalized, app_version),
                    },
                )
            )
        except httpx.RequestError as exc:
            LOGGER.warning(
                "license_activation_network_error",
                extra={"event": "license_activation_network_error", "error_type": type(exc).__name__},
            )
            return self._response(False, False, LicenseStatus.NETWORK_ERROR, "Could not reach license server")
        except Exception as exc:
            LOGGER.exception(
                "license_activation_failed",
                extra={"event": "license_activation_failed", "error_type": type(exc).__name__},
            )
            return self._response(False, False, LicenseStatus.SERVER_ERROR, "License activation failed")

        if response.status is LicenseStatus.ACTIVE and response.license and response.device:
            response = self._persist_successful_activation(
                response,
                app_version=app_version,
                license_key_fingerprint=get_license_key_prefix(normalized),
            )

        LOGGER.info(
            "license_activate_result",
            extra={
                "event": "license_activate_result",
                "status": response.status.value,
                "license_key": mask_license_key(normalized),
            },
        )
        self._cache_response(response)
        return response

    async def get_license_status(
        self,
        *,
        force_refresh: bool = False,
        app_version: str | None = None,
        remote_action: str = "status",
    ) -> LicenseApiResponse:
        if not force_refresh:
            cached = self._get_cached_response()
            if cached is not None:
                return cached

        state = read_license_state()
        if not state:
            response = self._response(
                True,
                False,
                LicenseStatus.NOT_ACTIVATED,
                "No local license activation found",
            )
            self._cache_response(response)
            return response

        if self._local_machine_state_mismatch(state):
            await self._remote_status_best_effort(state, app_version=app_version)
            response = self._response(
                False,
                False,
                LicenseStatus.MACHINE_MISMATCH,
                "Local license state does not match this machine",
            )
            self.invalidate_cache()
            return response

        license_id = _as_str(state.get("license_id"))
        device_id = _as_str(state.get("device_id"))
        if not license_id or not device_id:
            response = self._response(False, False, LicenseStatus.NOT_ACTIVATED, "Local license state is incomplete")
            self._cache_response(response)
            return response

        try:
            response = self._edge_response(
                await self._client.call(
                    remote_action,
                    {
                        "license_id": license_id,
                        "device_id": device_id,
                        "machine_fingerprint": get_machine_id(),
                        "app_version": app_version,
                    },
                )
            )
        except httpx.RequestError as exc:
            response = self._offline_response_from_state(state, exc)
            self._cache_response(response)
            return response
        except Exception as exc:
            LOGGER.exception(
                "license_status_server_error",
                extra={"event": "license_status_server_error", "error_type": type(exc).__name__},
            )
            response = self._offline_response_from_state(state, exc, server_error=True)
            self._cache_response(response)
            return response

        if response.status is LicenseStatus.ACTIVE:
            response = self._persist_successful_status(response, state, app_version=app_version)

        self._cache_response(response)
        return response

    async def refresh_license_status(self, *, app_version: str | None = None) -> LicenseApiResponse:
        self.invalidate_cache()
        return await self.get_license_status(force_refresh=True, app_version=app_version, remote_action="refresh")

    async def change_license_key(self, *, license_key: str, app_version: str | None = None) -> LicenseApiResponse:
        self.invalidate_cache()
        normalized = normalize_license_key(license_key)
        if not normalized:
            return self._response(False, False, LicenseStatus.INVALID_KEY, "License key is required")

        state = read_license_state() or {}
        try:
            response = self._edge_response(
                await self._client.call(
                    "change-key",
                    {
                        "license_key": normalized,
                        "previous_license_id": _as_str(state.get("license_id")),
                        "previous_device_id": _as_str(state.get("device_id")),
                        "machine_fingerprint": get_machine_id(),
                        "platform": get_platform_label(),
                        "app_version": app_version,
                        "metadata": _client_metadata(normalized, app_version),
                    },
                )
            )
        except httpx.RequestError as exc:
            LOGGER.warning(
                "license_change_key_network_error",
                extra={"event": "license_change_key_network_error", "error_type": type(exc).__name__},
            )
            return self._response(False, False, LicenseStatus.NETWORK_ERROR, "Could not reach license server")
        except Exception as exc:
            LOGGER.exception(
                "license_change_key_failed",
                extra={"event": "license_change_key_failed", "error_type": type(exc).__name__},
            )
            return self._response(False, False, LicenseStatus.SERVER_ERROR, "License key change failed")

        if response.status is LicenseStatus.ACTIVE and response.license and response.device:
            response = self._persist_successful_activation(
                response,
                app_version=app_version,
                license_key_fingerprint=get_license_key_prefix(normalized),
            )
        self._cache_response(response)
        return response

    async def deactivate_local_license(self) -> LicenseApiResponse:
        clear_license_state()
        self.invalidate_cache()
        response = self._response(
            True,
            False,
            LicenseStatus.NOT_ACTIVATED,
            "Local license activation was removed",
        )
        self._cache_response(response)
        return response

    def _response(
        self,
        ok: bool,
        licensed: bool,
        status: LicenseStatus,
        reason: str | None,
        *,
        license_obj: dict[str, Any] | None = None,
        device_obj: dict[str, Any] | None = None,
        offline_valid_until: str | None = None,
    ) -> LicenseApiResponse:
        return LicenseApiResponse(
            ok=ok,
            licensed=licensed,
            status=status,
            reason=reason,
            license=license_obj,
            device=device_obj,
            offline_valid_until=offline_valid_until,
        )

    def _edge_response(self, payload: dict[str, Any]) -> LicenseApiResponse:
        status = parse_license_status(payload.get("status"))
        return self._response(
            ok=bool(payload.get("ok")),
            licensed=bool(payload.get("licensed")),
            status=status,
            reason=_as_str(payload.get("reason")),
            license_obj=_dict_or_none(payload.get("license")),
            device_obj=_dict_or_none(payload.get("device")),
            offline_valid_until=_as_str(payload.get("offline_valid_until")),
        )

    def _persist_successful_activation(
        self,
        response: LicenseApiResponse,
        *,
        app_version: str | None,
        license_key_fingerprint: str | None,
    ) -> LicenseApiResponse:
        now_iso = iso_now()
        offline_until = (utc_now() + timedelta(days=self._offline_grace_days)).isoformat()
        write_license_state(
            {
                "license_id": response.license["id"] if response.license else None,
                "device_id": response.device["id"] if response.device else None,
                "machine_id_hash": (response.device or {}).get("machine_id_hash"),
                "local_machine_fingerprint_hash": get_local_machine_fingerprint_hash(),
                "license_key_fingerprint": license_key_fingerprint,
                "activated_at": now_iso,
                "last_verified_at": now_iso,
                "offline_valid_until": offline_until,
                "app_version": app_version,
            }
        )
        return LicenseApiResponse(
            ok=True,
            licensed=True,
            status=LicenseStatus.ACTIVE,
            reason=response.reason,
            license=response.license,
            device=response.device,
            offline_valid_until=offline_until,
        )

    def _persist_successful_status(
        self,
        response: LicenseApiResponse,
        state: dict[str, Any],
        *,
        app_version: str | None,
    ) -> LicenseApiResponse:
        now_iso = iso_now()
        offline_until = (utc_now() + timedelta(days=self._offline_grace_days)).isoformat()
        state.update(
            {
                "license_id": (response.license or {}).get("id") or state.get("license_id"),
                "device_id": (response.device or {}).get("id") or state.get("device_id"),
                "machine_id_hash": (response.device or {}).get("machine_id_hash") or state.get("machine_id_hash"),
                "local_machine_fingerprint_hash": get_local_machine_fingerprint_hash(),
                "last_verified_at": now_iso,
                "offline_valid_until": offline_until,
                "app_version": app_version or state.get("app_version"),
            }
        )
        write_license_state(state)
        return LicenseApiResponse(
            ok=True,
            licensed=True,
            status=LicenseStatus.ACTIVE,
            reason=response.reason,
            license=response.license,
            device=response.device,
            offline_valid_until=offline_until,
        )

    def _local_machine_state_mismatch(self, state: dict[str, Any]) -> bool:
        stored = _as_str(state.get("local_machine_fingerprint_hash"))
        if not stored:
            return False
        return stored != get_local_machine_fingerprint_hash()

    async def _remote_status_best_effort(self, state: dict[str, Any], *, app_version: str | None) -> None:
        license_id = _as_str(state.get("license_id"))
        device_id = _as_str(state.get("device_id"))
        if not license_id or not device_id:
            return
        try:
            await self._client.call(
                "status",
                {
                    "license_id": license_id,
                    "device_id": device_id,
                    "machine_fingerprint": get_machine_id(),
                    "app_version": app_version,
                },
            )
        except Exception as exc:
            LOGGER.warning(
                "license_machine_mismatch_audit_failed",
                extra={"event": "license_machine_mismatch_audit_failed", "error_type": type(exc).__name__},
            )

    def _offline_response_from_state(
        self,
        state: dict[str, Any],
        exc: Exception,
        *,
        server_error: bool = False,
    ) -> LicenseApiResponse:
        offline_until = _as_str(state.get("offline_valid_until"))
        valid_until = parse_dt(offline_until)
        if _as_str(state.get("local_machine_fingerprint_hash")) and valid_until is not None and utc_now() <= valid_until:
            LOGGER.warning(
                "license_status_active_offline",
                extra={"event": "license_status_active_offline", "error_type": type(exc).__name__},
            )
            return self._response(
                True,
                True,
                LicenseStatus.ACTIVE_OFFLINE,
                "Using offline grace period",
                license_obj={"id": state.get("license_id")},
                device_obj={"id": state.get("device_id")},
                offline_valid_until=offline_until,
            )
        status = LicenseStatus.SERVER_ERROR if server_error else LicenseStatus.VERIFICATION_REQUIRED
        reason = "License verification failed" if server_error else "Internet connection is required to verify license"
        return self._response(False, False, status, reason, offline_valid_until=offline_until)

    def _get_cached_response(self) -> LicenseApiResponse | None:
        entry = self._cache
        if entry is None:
            return None
        if time.monotonic() >= entry.expires_at_monotonic:
            self._cache = None
            return None
        return entry.response

    def _cache_response(self, response: LicenseApiResponse) -> None:
        ttl: int | None
        if response.status in NORMAL_CACHEABLE_STATUSES:
            ttl = self._cache_ttl_seconds
        elif response.status in ERROR_STATUSES:
            ttl = self._error_cache_ttl_seconds
        else:
            ttl = None
        if ttl is None or ttl <= 0:
            return
        self._cache = _CacheEntry(response=response, expires_at_monotonic=time.monotonic() + ttl)


def _client_metadata(normalized_license_key: str, app_version: str | None) -> dict[str, Any]:
    return {
        "hostname": socket.gethostname(),
        "platform": get_platform_label(),
        "app_version": app_version,
        "license_key_prefix": get_license_key_prefix(normalized_license_key),
    }


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default


def _as_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _dict_or_none(value: object) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None
