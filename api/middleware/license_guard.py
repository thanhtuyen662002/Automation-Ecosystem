"""
api/middleware/license_guard.py — Per-request license validation middleware.

Runs on EVERY request except the explicitly skipped paths.
Validates:
  1. Bearer token present and signature valid
  2. Session exists in DB and is not revoked
  3. License is still active and not expired

On failure:
  - 401: missing/invalid/expired token
  - 403: license revoked or expired (valid token but license no longer valid)

SKIPPED paths (no token required):
  - /health
  - /api/v1/auth/login
  - /api/v1/auth/refresh
  - /api/v1/admin/*   ← admin routes use ADMIN_SECRET instead
"""
from __future__ import annotations

import logging

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from api.security import lookup_session, verify_token

LOGGER = logging.getLogger("api.license_guard")

# Paths that bypass the license guard entirely
_SKIP_PREFIXES = (
    "/health",
    "/api/v1/auth/",
    "/api/v1/admin/",
)


class LicenseGuard(BaseHTTPMiddleware):
    """
    Starlette middleware that enforces license + session validity
    on every protected API request.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        # Skip non-protected paths
        if any(path.startswith(prefix) for prefix in _SKIP_PREFIXES):
            return await call_next(request)

        # Allow CORS preflight requests to pass through
        if request.method == "OPTIONS":
            return await call_next(request)

        # Extract Bearer token
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"error": "Unauthorized", "message": "Authentication required."},
            )

        token = auth_header[len("Bearer "):]

        # Step 1: Verify token signature + expiry (no DB hit)
        payload = verify_token(token)
        if payload is None:
            return JSONResponse(
                status_code=401,
                content={"error": "Unauthorized", "message": "Token invalid or expired."},
            )

        # Step 2: Validate DB session (checks revocation + license status)
        db = getattr(request.app.state, "database", None)
        if db is None:
            return JSONResponse(
                status_code=503,
                content={"error": "ServiceUnavailable", "message": "Database not ready."},
            )

        session = await lookup_session(db, token)
        if session is None:
            return JSONResponse(
                status_code=401,
                content={"error": "Unauthorized", "message": "Session expired or revoked. Please log in again."},
            )

        # Step 3: Check license is still active in DB
        if not session["license_active"]:
            LOGGER.warning(
                "request_with_revoked_license",
                extra={
                    "event": "request_with_revoked_license",
                    "license_key": payload.get("lid"),
                    "path": path,
                },
            )
            return JSONResponse(
                status_code=403,
                content={"error": "Forbidden", "message": "License has been revoked."},
            )

        if session["license_expires"] and session["license_expires"] < "9999":
            from datetime import UTC, datetime
            try:
                expires = datetime.fromisoformat(session["license_expires"])
                if datetime.now(UTC) > expires:
                    return JSONResponse(
                        status_code=403,
                        content={"error": "Forbidden", "message": "License has expired."},
                    )
            except Exception:
                pass

        # Attach session info to request state for downstream handlers
        request.state.license_key  = payload["lid"]
        request.state.machine_fp   = payload["fp"]
        request.state.session_id   = payload["sid"]

        return await call_next(request)
