from __future__ import annotations

import logging

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from api.services.license_service import LicenseService
from core.license_status import LicenseStatus


LOGGER = logging.getLogger("api.license_guard")

_SKIP_PREFIXES = (
    "/health",
    "/api/license/",
    "/api/v1/auth/",
)


class LicenseGuard(BaseHTTPMiddleware):
    """
    Local license guard.

    Browser session != device license activation.
    Supabase Auth refresh token != license refresh.
    The app is unlocked by Edge-verified license key + machine binding only.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if request.method == "OPTIONS" or any(path.startswith(prefix) for prefix in _SKIP_PREFIXES):
            return await call_next(request)

        service = getattr(request.app.state, "license_service", None)
        if not isinstance(service, LicenseService):
            return JSONResponse(
                status_code=503,
                content={
                    "ok": False,
                    "licensed": False,
                    "status": LicenseStatus.SERVER_ERROR.value,
                    "reason": "License service is not initialized",
                    "license": None,
                    "device": None,
                    "offline_valid_until": None,
                },
            )

        response = await service.get_license_status(force_refresh=False)
        if response.status in {LicenseStatus.ACTIVE, LicenseStatus.ACTIVE_OFFLINE} and response.licensed:
            request.state.license_status = response.status.value
            request.state.license_id = (response.license or {}).get("id")
            request.state.license_device_id = (response.device or {}).get("id")
            return await call_next(request)

        status_code = 503 if response.status in {LicenseStatus.NETWORK_ERROR, LicenseStatus.SERVER_ERROR} else 403
        LOGGER.warning(
            "license_guard_blocked",
            extra={"event": "license_guard_blocked", "path": path, "status": response.status.value},
        )
        return JSONResponse(status_code=status_code, content=response.to_dict())
