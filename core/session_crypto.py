"""
Session cookie encryption/decryption using Fernet symmetric encryption.

The encryption key is loaded from the SESSION_ENCRYPT_KEY environment variable.
Generate a key with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

LOGGER = logging.getLogger("core.session_crypto")

_FALLBACK_WARNING_ISSUED = False


def _get_key() -> bytes:
    """Load and validate the encryption key from environment."""
    global _FALLBACK_WARNING_ISSUED
    key = os.environ.get("SESSION_ENCRYPT_KEY", "").strip()
    if not key:
        if not _FALLBACK_WARNING_ISSUED:
            LOGGER.warning(
                "SESSION_ENCRYPT_KEY not set — cookies stored in plaintext. "
                "Set SESSION_ENCRYPT_KEY in .env for production use."
            )
            _FALLBACK_WARNING_ISSUED = True
        return b""  # plaintext fallback
    try:
        # Validate key format (Fernet keys are 44-char URL-safe base64)
        Fernet(key.encode())
        return key.encode()
    except Exception as exc:
        raise RuntimeError(
            f"SESSION_ENCRYPT_KEY is invalid: {exc}. "
            "Generate a new key with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        ) from exc


def encrypt_cookies(cookies: list[dict[str, Any]]) -> str:
    """
    Serialize and encrypt a list of Playwright cookie dicts.

    Returns a string suitable for storing in the DB.
    If SESSION_ENCRYPT_KEY is not set, stores as plain JSON with a prefix marker.
    """
    payload = json.dumps(cookies, ensure_ascii=False)
    key = _get_key()
    if not key:
        return "plain:" + payload
    f = Fernet(key)
    return "fernet:" + f.encrypt(payload.encode()).decode()


def decrypt_cookies(stored: str) -> list[dict[str, Any]]:
    """
    Decrypt and deserialize cookies previously encrypted by encrypt_cookies().

    Raises:
        ValueError: if the stored value is malformed or decryption fails.
    """
    if not stored:
        return []
    if stored.startswith("plain:"):
        return json.loads(stored[6:])
    if stored.startswith("fernet:"):
        key = _get_key()
        if not key:
            raise ValueError(
                "Cookies are Fernet-encrypted but SESSION_ENCRYPT_KEY is not set."
            )
        try:
            f = Fernet(key)
            payload = f.decrypt(stored[7:].encode()).decode()
            return json.loads(payload)
        except InvalidToken as exc:
            raise ValueError("Failed to decrypt cookies — key may have changed.") from exc
        except Exception as exc:
            raise ValueError(f"Cookie decryption error: {exc}") from exc
    # Legacy: raw JSON (no prefix)
    try:
        return json.loads(stored)
    except Exception as exc:
        raise ValueError(f"Cannot parse stored cookies: {exc}") from exc
