"""
Platform-specific configuration for login, success detection, and upload URLs.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformConfig:
    login_url: str
    # URL fragment/substring that indicates successful login
    success_url_fragment: str
    # URL to navigate to for uploading content
    upload_url: str
    # Profile page URL to validate session (check for redirect to login)
    profile_url_fragment: str
    # Display name
    display_name: str


PLATFORM_CONFIGS: dict[str, PlatformConfig] = {
    "tiktok": PlatformConfig(
        login_url="https://www.tiktok.com/login",
        success_url_fragment="tiktok.com/foryou",
        upload_url="https://www.tiktok.com/creator#/upload",
        profile_url_fragment="tiktok.com/@",
        display_name="TikTok",
    ),
    "youtube": PlatformConfig(
        login_url="https://accounts.google.com/signin",
        success_url_fragment="youtube.com",
        upload_url="https://studio.youtube.com/",
        profile_url_fragment="youtube.com/channel",
        display_name="YouTube",
    ),
    "facebook": PlatformConfig(
        login_url="https://www.facebook.com/login",
        success_url_fragment="facebook.com/?",
        upload_url="https://www.facebook.com/",
        profile_url_fragment="facebook.com/profile",
        display_name="Facebook",
    ),
}

# Login page URL fragments — if current URL contains any of these, session is invalid
LOGIN_PAGE_FRAGMENTS: dict[str, list[str]] = {
    "tiktok": ["/login", "tiktok.com/login"],
    "youtube": ["accounts.google.com/signin", "accounts.google.com/v3/signin"],
    "facebook": ["facebook.com/login", "facebook.com/checkpoint"],
}

# Default browser viewport used for all sessions
DEFAULT_VIEWPORT = {"width": 1280, "height": 720}

# Max wait time for manual login (seconds)
LOGIN_TIMEOUT_SECONDS = 300

# Human-like action delay range (seconds)
ACTION_DELAY_MIN = 2.0
ACTION_DELAY_MAX = 5.0


def get_platform_config(platform: str) -> PlatformConfig:
    """Get config for a platform, raising ValueError if unknown."""
    cfg = PLATFORM_CONFIGS.get(platform.lower())
    if cfg is None:
        raise ValueError(
            f"Unknown platform '{platform}'. Supported: {list(PLATFORM_CONFIGS)}"
        )
    return cfg


def is_login_page(url: str, platform: str) -> bool:
    """Return True if the URL indicates the user has been redirected to a login page."""
    fragments = LOGIN_PAGE_FRAGMENTS.get(platform.lower(), [])
    return any(frag in url for frag in fragments)
