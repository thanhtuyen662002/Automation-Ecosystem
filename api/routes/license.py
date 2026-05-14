from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.services.license_service import LicenseApiResponse, LicenseService


LOGGER = logging.getLogger("api.license")
router = APIRouter(prefix="/api/license", tags=["License"])


class ActivateLicenseRequest(BaseModel):
    license_key: str = Field(min_length=1)
    device_name: str | None = None
    app_version: str | None = None


class RefreshLicenseRequest(BaseModel):
    app_version: str | None = None


class ChangeLicenseKeyRequest(BaseModel):
    license_key: str = Field(min_length=1)
    app_version: str | None = None


def _license_service(request: Request) -> LicenseService:
    service = getattr(request.app.state, "license_service", None)
    if not isinstance(service, LicenseService):
        raise HTTPException(503, "License service is not initialized")
    return service


def _json(response: LicenseApiResponse) -> dict[str, Any]:
    return response.to_dict()


@router.get("/status")
async def get_license_status(request: Request) -> dict[str, Any]:
    response = await _license_service(request).get_license_status(force_refresh=False)
    LOGGER.info("license_status_read", extra={"event": "license_status_read", "status": response.status.value})
    return _json(response)


@router.post("/activate")
async def activate_license(req: ActivateLicenseRequest, request: Request) -> dict[str, Any]:
    response = await _license_service(request).activate_license(
        license_key=req.license_key,
        device_name=req.device_name,
        app_version=req.app_version,
    )
    LOGGER.info("license_activate", extra={"event": "license_activate", "status": response.status.value})
    return _json(response)


@router.post("/refresh")
async def refresh_license(req: RefreshLicenseRequest, request: Request) -> dict[str, Any]:
    response = await _license_service(request).refresh_license_status(app_version=req.app_version)
    LOGGER.info("license_refresh", extra={"event": "license_refresh", "status": response.status.value})
    return _json(response)


@router.post("/change-key")
async def change_license_key(req: ChangeLicenseKeyRequest, request: Request) -> dict[str, Any]:
    response = await _license_service(request).change_license_key(
        license_key=req.license_key,
        app_version=req.app_version,
    )
    LOGGER.info("license_change_key", extra={"event": "license_change_key", "status": response.status.value})
    return _json(response)


@router.post("/deactivate-local")
async def deactivate_local_license(request: Request) -> dict[str, Any]:
    response = await _license_service(request).deactivate_local_license()
    LOGGER.info("license_deactivate_local", extra={"event": "license_deactivate_local"})
    return _json(response)
