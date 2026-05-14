from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse


router = APIRouter(prefix="/admin/licenses", tags=["Deprecated Admin Licenses"])


@router.api_route("/{_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def deprecated_admin_license_route(_path: str) -> Any:
    return JSONResponse(
        status_code=410,
        content={
            "ok": False,
            "reason": "Browser/admin license management was removed. Use scripts/create_license.py and related admin scripts.",
        },
    )


@router.api_route("", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def deprecated_admin_license_root() -> Any:
    return await deprecated_admin_license_route("")
