from __future__ import annotations

from enum import StrEnum
from typing import Any


class LoginBlockStatus(StrEnum):
    OK = "OK"
    RATE_LIMITED = "RATE_LIMITED"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    CHECKPOINT_REQUIRED = "CHECKPOINT_REQUIRED"
    LOGIN_PAGE = "LOGIN_PAGE"
    UNKNOWN = "UNKNOWN"


_RATE_LIMIT_PATTERNS = (
    "maximum number of attempts reached",
    "try again later",
    "too many attempts",
    "too many login attempts",
    "too many tries",
)

_CAPTCHA_PATTERNS = (
    "captcha",
    "verify you are human",
    "complete the verification",
    "security verification",
)

_CHECKPOINT_PATTERNS = (
    "checkpoint",
    "unusual activity",
    "verify your identity",
    "confirm it's you",
    "confirm it is you",
)

_LOGIN_PATTERNS = (
    "log in to tiktok",
    "login to tiktok",
    "sign up for tiktok",
    "use phone / email / username",
    "use phone or email",
)


def classify_login_text(text: str, url: str = "") -> LoginBlockStatus:
    """Classify a platform login page without attempting to bypass it."""
    haystack = f"{url}\n{text}".lower()
    if any(pattern in haystack for pattern in _RATE_LIMIT_PATTERNS):
        return LoginBlockStatus.RATE_LIMITED
    if any(pattern in haystack for pattern in _CAPTCHA_PATTERNS):
        return LoginBlockStatus.CAPTCHA_REQUIRED
    if any(pattern in haystack for pattern in _CHECKPOINT_PATTERNS):
        return LoginBlockStatus.CHECKPOINT_REQUIRED
    if "/login" in haystack or any(pattern in haystack for pattern in _LOGIN_PATTERNS):
        return LoginBlockStatus.LOGIN_PAGE
    return LoginBlockStatus.OK


async def classify_login_block(page: Any) -> LoginBlockStatus:
    """Inspect the current page for known login blocks.

    The function is read-only: it does not solve CAPTCHA, submit forms, or retry
    authentication. It only helps callers stop cleanly and surface a clear error.
    """
    try:
        url = str(getattr(page, "url", "") or "")
    except Exception:
        url = ""

    text = ""
    for method_name in ("inner_text", "text_content", "content"):
        try:
            method = getattr(page, method_name)
        except Exception:
            continue
        try:
            if method_name in {"inner_text", "text_content"}:
                value = await method("body", timeout=750)
            else:
                value = await method()
            if value:
                text = str(value)
                break
        except Exception:
            continue

    if not url and not text:
        return LoginBlockStatus.UNKNOWN
    return classify_login_text(text, url)


def login_block_error_message(status: LoginBlockStatus) -> str:
    if status == LoginBlockStatus.RATE_LIMITED:
        return (
            "TikTok temporarily blocked login for this account/session. "
            "Wait before retrying. No repeated login attempts were made by the app."
        )
    if status == LoginBlockStatus.CAPTCHA_REQUIRED:
        return "TikTok requires a manual security verification. The app stopped without retrying login."
    if status == LoginBlockStatus.CHECKPOINT_REQUIRED:
        return "TikTok requires an account checkpoint. The app stopped without retrying login."
    if status == LoginBlockStatus.LOGIN_PAGE:
        return "SESSION_EXPIRED"
    return "UNKNOWN_LOGIN_STATE"
