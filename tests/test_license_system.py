from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.services.license_service import LicenseApiResponse, LicenseService
from core.license_key import get_license_key_prefix, mask_license_key, normalize_license_key
from core.license_status import LicenseStatus, parse_license_status


def test_license_key_normalize_prefix_and_mask() -> None:
    normalized = normalize_license_key(" aeco-abcd efgh-ijkl-mnop ")
    assert normalized == "AECO-ABCDEFGH-IJKL-MNOP"
    assert get_license_key_prefix(normalized).startswith("AECO-ABCD")
    assert mask_license_key(normalized).startswith("AECO-****")
    assert "ABCDEFGH" not in mask_license_key(normalized)


def test_parse_unknown_license_status_is_server_error() -> None:
    assert parse_license_status("active") is LicenseStatus.ACTIVE
    assert parse_license_status("unexpected") is LicenseStatus.SERVER_ERROR


def test_cache_does_not_store_network_error_too_long() -> None:
    service = LicenseService(
        client=object(),  # type: ignore[arg-type]
        offline_grace_days=7,
        cache_ttl_seconds=30,
        error_cache_ttl_seconds=0,
    )
    response = LicenseApiResponse(
        ok=False,
        licensed=False,
        status=LicenseStatus.NETWORK_ERROR,
        reason="network",
    )
    service._cache_response(response)
    assert service._get_cached_response() is None


def test_offline_grace_allows_active_offline_until_expired() -> None:
    service = LicenseService(
        client=object(),  # type: ignore[arg-type]
        offline_grace_days=7,
        cache_ttl_seconds=30,
    )
    response = service._offline_response_from_state(
        {
            "license_id": "license-id",
            "device_id": "device-id",
            "local_machine_fingerprint_hash": "local-hash",
            "offline_valid_until": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
        },
        RuntimeError("network"),
    )
    assert response.status is LicenseStatus.ACTIVE_OFFLINE
    assert response.licensed is True


def test_offline_grace_requires_verification_after_expiry() -> None:
    service = LicenseService(
        client=object(),  # type: ignore[arg-type]
        offline_grace_days=7,
        cache_ttl_seconds=30,
    )
    response = service._offline_response_from_state(
        {
            "license_id": "license-id",
            "device_id": "device-id",
            "offline_valid_until": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        },
        RuntimeError("network"),
    )
    assert response.status is LicenseStatus.VERIFICATION_REQUIRED
    assert response.licensed is False
