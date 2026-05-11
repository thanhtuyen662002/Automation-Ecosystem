"""
api/routes/auth.py — Simple license-key authentication.

POST /api/v1/auth/login
  → validates account + license_key against VALID_LICENSES env var
  → returns signed JWT-like token (HMAC, no external deps needed)
  → stored in localStorage by frontend, sent as Bearer token

Token format: base64(account:timestamp:signature)
No library dependencies beyond stdlib.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

LOGGER = logging.getLogger("api.auth")
router = APIRouter(prefix="/auth", tags=["Auth"])

# ── Config ────────────────────────────────────────────────────────────────────
# Set VALID_LICENSES in .env as comma-separated list, e.g.:
#   VALID_LICENSES=AE-XXXX-YYYY-ZZZZ,AE-AAAA-BBBB-CCCC
# Set LICENSE_SECRET in .env for HMAC signing (any random string).
# If VALID_LICENSES is empty, any license key with length >= 8 is accepted (dev mode).

_SECRET = os.getenv("LICENSE_SECRET", "automationecosystem-dev-secret-2025").encode()
_VALID_LICENSES: set[str] = set(
    k.strip() for k in os.getenv("VALID_LICENSES", "").split(",") if k.strip()
)
_DEV_MODE = len(_VALID_LICENSES) == 0  # dev: accept any key >= 8 chars


def _sign_token(account: str, issued_at: float) -> str:
    payload = f"{account}:{issued_at:.0f}"
    sig = hmac.new(_SECRET, payload.encode(), hashlib.sha256).hexdigest()[:16]
    raw = json.dumps({"account": account, "iat": int(issued_at), "sig": sig})
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _verify_token(token: str) -> dict | None:
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        data = json.loads(raw)
        account = data["account"]
        iat = data["iat"]
        sig = data["sig"]
        expected = hmac.new(_SECRET, f"{account}:{iat}".encode(), hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(sig, expected):
            return None
        # Token valid for 30 days
        if time.time() - iat > 86400 * 30:
            return None
        return data
    except Exception:
        return None


# ── Schemas ───────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    account: str
    license_key: str


class LoginResponse(BaseModel):
    token: str
    user: dict


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest) -> LoginResponse:
    """
    Validate account + license key.
    Returns a signed token if credentials are valid.
    """
    account = req.account.strip().lstrip("@")
    key = req.license_key.strip()

    if not account:
        raise HTTPException(400, "Account name is required")
    if not key:
        raise HTTPException(400, "License key is required")

    # Validate license
    if _DEV_MODE:
        # Dev mode: accept any key >= 8 chars
        if len(key) < 8:
            raise HTTPException(401, "Invalid license key (dev mode: must be >= 8 characters)")
        LOGGER.warning("auth_dev_mode_login", extra={"event": "auth_dev_mode_login", "account": account})
    else:
        if key not in _VALID_LICENSES:
            raise HTTPException(401, "Invalid license key")

    token = _sign_token(account, time.time())
    LOGGER.info("auth_login_success", extra={"event": "auth_login_success", "account": account, "dev_mode": _DEV_MODE})

    return LoginResponse(
        token=token,
        user={"account": account, "role": "operator", "dev_mode": _DEV_MODE},
    )


@router.get("/me")
async def me(token: str) -> dict:
    """Verify a token and return user info."""
    data = _verify_token(token)
    if not data:
        raise HTTPException(401, "Invalid or expired token")
    return {"account": data["account"], "valid": True}
