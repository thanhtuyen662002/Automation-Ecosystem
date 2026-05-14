from __future__ import annotations

from enum import StrEnum


class LicenseStatus(StrEnum):
    ACTIVE = "active"
    ACTIVE_OFFLINE = "active_offline"
    NOT_ACTIVATED = "not_activated"
    INVALID_KEY = "invalid_key"
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    REVOKED = "revoked"
    DEVICE_REVOKED = "device_revoked"
    ALREADY_ACTIVATED_ON_ANOTHER_DEVICE = "already_activated_on_another_device"
    MACHINE_MISMATCH = "machine_mismatch"
    VERIFICATION_REQUIRED = "verification_required"
    NETWORK_ERROR = "network_error"
    SERVER_ERROR = "server_error"


NORMAL_CACHEABLE_STATUSES: frozenset[LicenseStatus] = frozenset(
    {
        LicenseStatus.ACTIVE,
        LicenseStatus.ACTIVE_OFFLINE,
        LicenseStatus.EXPIRED,
        LicenseStatus.REVOKED,
        LicenseStatus.SUSPENDED,
        LicenseStatus.DEVICE_REVOKED,
        LicenseStatus.NOT_ACTIVATED,
    }
)

ERROR_STATUSES: frozenset[LicenseStatus] = frozenset(
    {
        LicenseStatus.NETWORK_ERROR,
        LicenseStatus.SERVER_ERROR,
    }
)


def parse_license_status(value: object) -> LicenseStatus:
    try:
        return LicenseStatus(str(value))
    except ValueError:
        return LicenseStatus.SERVER_ERROR
