"""
DEPRECATED_LICENSE_AUTH_EDGE_FUNCTION client.

The license flow no longer calls the legacy license-auth Edge Function,
Supabase Auth sessions, access tokens, or refresh tokens. Use
api.services.license_service and /api/license/*, which call license-api.
"""
from __future__ import annotations


class LicenseAuthorityUnavailable(RuntimeError):
    pass


def is_supabase_configured() -> bool:
    return False


async def activate(*_args, **_kwargs):
    raise LicenseAuthorityUnavailable("Deprecated license-auth Edge Function client is disabled")


async def refresh(*_args, **_kwargs):
    raise LicenseAuthorityUnavailable("Deprecated license-auth Edge Function client is disabled")


async def heartbeat(*_args, **_kwargs):
    raise LicenseAuthorityUnavailable("Deprecated license-auth Edge Function client is disabled")


async def logout(*_args, **_kwargs):
    return None
