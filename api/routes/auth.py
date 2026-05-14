from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse


router = APIRouter(prefix="/auth", tags=["Deprecated Auth"])


def _gone() -> JSONResponse:
    return JSONResponse(
        status_code=410,
        content={
            "ok": False,
            "licensed": False,
            "status": "deprecated",
            "reason": "The username/session auth flow has been removed. Use /api/license/*.",
            "license": None,
            "device": None,
            "offline_valid_until": None,
        },
    )


@router.api_route("/{_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def deprecated_auth_route(_path: str) -> Any:
    return _gone()


@router.api_route("", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def deprecated_auth_root() -> Any:
    return _gone()
