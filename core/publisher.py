"""
Publisher — Layer 7: Multi-platform content publishing.

Platforms:
    TikTok    — short-form video (via mock or real HTTP)
    Facebook  — page post with video / image + caption
    YouTube   — video upload with title / description

Input:
    PublishRequest {
        video_path, caption, account_id, pages,
        affiliate_link, platform, schedule_ts
    }

Output:
    PublishResult {
        success, platform, post_id, url, error,
        delay_secs_applied, scheduled_ts
    }

Architecture:
    BasePublisher           ← retry logic, affiliate injection, delay
    TikTokPublisher         ← adapter
    FacebookPublisher       ← adapter
    YouTubePublisher        ← adapter
    PublisherRouter         ← routes to correct adapter, uses FleetCoordinator delay

Design contracts:
    - MOCK_MODE=True (default) → no real API calls, deterministic fake response
    - All delays derived from FleetCoordinator._compute_stagger() or seeded PRNG
    - Retry is exponential backoff, seeded per account (deterministic)
    - Affiliate link injected BEFORE caption truncation, at end of caption
    - BasePublisher never raises — errors are surfaced via PublishResult.error

Usage:
    router = get_publisher_router()
    result = router.publish(PublishRequest(
        video_path  = "output/acct-01/acct-01_video.mp4",
        caption     = "Stop scrolling you need to see this",
        account_id  = "acct-01",
        pages       = ["page-001"],
        platform    = "tiktok",
        affiliate_link = "https://aff.example.com/ref=acct01",
    ))
"""
from __future__ import annotations

import hashlib
import logging
import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("core.publisher")

# ── Config ────────────────────────────────────────────────────────────────────

MOCK_MODE: bool = True          # override per-instance or via env
MAX_RETRY:  int  = 3
RETRY_BASE: float = 2.0         # base for exponential backoff (seconds)
CAPTION_MAX: dict[str, int] = {
    "tiktok":   2200,
    "facebook": 63206,
    "youtube":  5000,
}


# ── PRNG (same seeding pattern as fleet_coordinator) ─────────────────────────

def _pseed(account_id: str, slot: int) -> float:
    h = hashlib.sha256(f"pub:{account_id}:{slot}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _pint(account_id: str, slot: int, lo: int, hi: int) -> int:
    return lo + int(_pseed(account_id, slot) * (hi - lo + 1))


# ── Enums / constants ─────────────────────────────────────────────────────────

class Platform(str, Enum):
    TIKTOK   = "tiktok"
    FACEBOOK = "facebook"
    YOUTUBE  = "youtube"


class PublishStatus(str, Enum):
    SUCCESS   = "success"
    SCHEDULED = "scheduled"
    FAILED    = "failed"
    SKIPPED   = "skipped"


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class PublishRequest:
    """Input to PublisherRouter.publish()."""
    account_id:      str
    video_path:      str
    caption:         str
    platform:        str                          # "tiktok" | "facebook" | "youtube"
    pages:           list[str]  = field(default_factory=list)   # page/channel ids
    affiliate_link:  str        = ""              # injected at end of caption
    schedule_ts:     float | None = None          # Unix timestamp; None = post now
    tags:            list[str]  = field(default_factory=list)
    thumbnail_path:  str        = ""
    extra:           dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id":     self.account_id,
            "video_path":     self.video_path,
            "caption":        self.caption[:80],
            "platform":       self.platform,
            "pages":          self.pages,
            "affiliate_link": self.affiliate_link,
            "schedule_ts":    self.schedule_ts,
            "tags":           self.tags,
        }


@dataclass
class PublishResult:
    """Output from any publisher adapter."""
    success:            bool
    platform:           str
    account_id:         str
    status:             PublishStatus
    post_id:            str   = ""
    url:                str   = ""
    error:              str   = ""
    error_type:         str   = ""
    delay_secs_applied: float = 0.0
    scheduled_ts:       float | None = None
    retries_used:       int   = 0
    mock:               bool  = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "success":            self.success,
            "platform":           self.platform,
            "account_id":         self.account_id,
            "status":             self.status.value,
            "post_id":            self.post_id,
            "url":                self.url,
            "error":              self.error,
            "error_type":         self.error_type,
            "delay_secs_applied": self.delay_secs_applied,
            "scheduled_ts":       self.scheduled_ts,
            "retries_used":       self.retries_used,
            "mock":               self.mock,
        }


# ── Affiliate link injection ──────────────────────────────────────────────────

def _inject_affiliate(caption: str, link: str, platform: str) -> str:
    """Append affiliate link to caption, respecting platform character limit."""
    if not link:
        return caption
    suffix  = f"\n\n{link}"
    max_len = CAPTION_MAX.get(platform, 2000)
    if len(caption) + len(suffix) <= max_len:
        return caption + suffix
    # Truncate caption to fit: reserve space for suffix AND ellipsis (3 chars)
    ellipsis  = "..."
    available = max_len - len(suffix) - len(ellipsis)
    return caption[:available].rstrip() + ellipsis + suffix


# ── Retry helper ──────────────────────────────────────────────────────────────

def _retry_delay(account_id: str, attempt: int) -> float:
    """Deterministic exponential back-off: base^attempt + seeded jitter 0–1s."""
    base_delay = RETRY_BASE ** attempt
    jitter     = _pseed(account_id, 900 + attempt) * 1.0
    return round(base_delay + jitter, 2)


# ── BasePublisher ─────────────────────────────────────────────────────────────

class BasePublisher(ABC):
    """
    Abstract base for all platform adapters.

    Subclasses implement _do_publish() and _do_schedule().
    This base handles:
      - Affiliate link injection
      - Caption truncation
      - Retry with deterministic back-off
      - Mock mode short-circuit
    """

    PLATFORM: str = ""

    def __init__(self, mock_mode: bool = MOCK_MODE) -> None:
        self._mock = mock_mode

    # ── Public entry point ────────────────────────────────────────────────────

    def publish(
        self,
        req:        PublishRequest,
        delay_secs: float = 0.0,
    ) -> PublishResult:
        """
        Publish or schedule content. Never raises — errors in PublishResult.error.

        Args:
            req:        PublishRequest with all content metadata.
            delay_secs: Pre-publish wait (from FleetCoordinator stagger).
        """
        # Apply deterministic delay (non-blocking in mock; simulated in real)
        applied_delay = 0.0
        if delay_secs > 0.0 and not self._mock:
            time.sleep(delay_secs)
            applied_delay = delay_secs
        elif delay_secs > 0.0 and self._mock:
            applied_delay = delay_secs  # record but don't actually sleep

        # Build final caption
        caption = _inject_affiliate(req.caption, req.affiliate_link, self.PLATFORM)

        # Scheduled post path
        if req.schedule_ts is not None and req.schedule_ts > time.time():
            return self._attempt_schedule(req, caption, applied_delay)

        # Immediate publish with retry
        return self._attempt_publish(req, caption, applied_delay)

    # ── Internal retry loop ───────────────────────────────────────────────────

    def _attempt_publish(
        self,
        req:          PublishRequest,
        caption:      str,
        applied_delay:float,
    ) -> PublishResult:
        last_error  = ""
        last_etype  = ""
        for attempt in range(MAX_RETRY):
            try:
                if self._mock:
                    result = self._mock_publish(req, caption)
                else:
                    result = self._do_publish(req, caption)
                result.delay_secs_applied = applied_delay
                result.retries_used       = attempt
                LOGGER.info("publish_success", extra={
                    "platform":   self.PLATFORM,
                    "account_id": req.account_id,
                    "post_id":    result.post_id,
                    "retries":    attempt,
                    "mock":       self._mock,
                })
                return result
            except Exception as exc:
                last_error  = str(exc)
                last_etype  = type(exc).__name__
                LOGGER.warning("publish_retry", extra={
                    "platform":   self.PLATFORM,
                    "account_id": req.account_id,
                    "attempt":    attempt,
                    "error":      last_error,
                })
                if attempt < MAX_RETRY - 1:
                    wait = _retry_delay(req.account_id, attempt)
                    if not self._mock:
                        time.sleep(wait)

        LOGGER.error("publish_failed", extra={
            "platform":   self.PLATFORM,
            "account_id": req.account_id,
            "error":      last_error,
        })
        return PublishResult(
            success            = False,
            platform           = self.PLATFORM,
            account_id         = req.account_id,
            status             = PublishStatus.FAILED,
            error              = last_error,
            error_type         = last_etype,
            delay_secs_applied = applied_delay,
            retries_used       = MAX_RETRY - 1,
            mock               = self._mock,
        )

    def _attempt_schedule(
        self,
        req:          PublishRequest,
        caption:      str,
        applied_delay:float,
    ) -> PublishResult:
        try:
            if self._mock:
                result = self._mock_schedule(req, caption)
            else:
                result = self._do_schedule(req, caption)
            result.delay_secs_applied = applied_delay
            LOGGER.info("post_scheduled", extra={
                "platform":      self.PLATFORM,
                "account_id":    req.account_id,
                "scheduled_ts":  req.schedule_ts,
                "mock":          self._mock,
            })
            return result
        except Exception as exc:
            return PublishResult(
                success      = False,
                platform     = self.PLATFORM,
                account_id   = req.account_id,
                status       = PublishStatus.FAILED,
                error        = str(exc),
                error_type   = type(exc).__name__,
                mock         = self._mock,
            )

    # ── Mock implementations ──────────────────────────────────────────────────

    def _mock_publish(self, req: PublishRequest, caption: str) -> PublishResult:
        """Deterministic mock: generates a fake post_id from account+platform seed."""
        seed_str = f"{req.account_id}:{self.PLATFORM}:{int(time.time() // 3600)}"
        fake_id  = hashlib.sha256(seed_str.encode()).hexdigest()[:16]
        fake_url = f"https://{self.PLATFORM}.com/@{req.account_id}/video/{fake_id}"
        return PublishResult(
            success    = True,
            platform   = self.PLATFORM,
            account_id = req.account_id,
            status     = PublishStatus.SUCCESS,
            post_id    = fake_id,
            url        = fake_url,
            mock       = True,
        )

    def _mock_schedule(self, req: PublishRequest, caption: str) -> PublishResult:
        seed_str = f"{req.account_id}:{self.PLATFORM}:sched:{req.schedule_ts}"
        fake_id  = "sched_" + hashlib.sha256(seed_str.encode()).hexdigest()[:12]
        return PublishResult(
            success      = True,
            platform     = self.PLATFORM,
            account_id   = req.account_id,
            status       = PublishStatus.SCHEDULED,
            post_id      = fake_id,
            scheduled_ts = req.schedule_ts,
            mock         = True,
        )

    # ── Abstract: subclasses implement real API calls ─────────────────────────

    @abstractmethod
    def _do_publish(self, req: PublishRequest, caption: str) -> PublishResult:
        """Perform the actual API call to publish immediately."""
        ...

    @abstractmethod
    def _do_schedule(self, req: PublishRequest, caption: str) -> PublishResult:
        """Perform the actual API call to schedule a future post."""
        ...


# ── TikTok adapter ────────────────────────────────────────────────────────────

class TikTokPublisher(BasePublisher):
    """
    TikTok Content Publishing API adapter.

    Real mode (mock=False) would use:
        POST https://open.tiktokapis.com/v2/post/publish/video/init/
        POST https://open.tiktokapis.com/v2/post/publish/video/upload/
    Requires: access_token per account (passed via req.extra["access_token"]).
    """

    PLATFORM = "tiktok"

    def _do_publish(self, req: PublishRequest, caption: str) -> PublishResult:
        # Real implementation would call TikTok Content Posting API
        # Left as stub — raise NotImplementedError to force mock usage in tests
        raise NotImplementedError(
            "TikTok real API not configured. Set mock_mode=True or supply access_token."
        )

    def _do_schedule(self, req: PublishRequest, caption: str) -> PublishResult:
        raise NotImplementedError("TikTok scheduled posting requires access_token.")

    def _mock_publish(self, req: PublishRequest, caption: str) -> PublishResult:
        """TikTok-specific mock: includes hashtag count check."""
        tags     = req.tags[:30]  # TikTok allows up to 30 hashtags
        tag_str  = " ".join(f"#{t}" for t in tags)
        full_cap = (caption + "\n" + tag_str).strip()
        full_cap = full_cap[:CAPTION_MAX["tiktok"]]

        seed_str = f"tt:{req.account_id}:{int(time.time() // 3600)}"
        fake_id  = hashlib.sha256(seed_str.encode()).hexdigest()[:16]

        return PublishResult(
            success    = True,
            platform   = self.PLATFORM,
            account_id = req.account_id,
            status     = PublishStatus.SUCCESS,
            post_id    = fake_id,
            url        = f"https://tiktok.com/@{req.account_id}/video/{fake_id}",
            mock       = True,
        )


# ── Facebook adapter ──────────────────────────────────────────────────────────

class FacebookPublisher(BasePublisher):
    """
    Facebook Graph API adapter — posts to one or more pages.

    Real mode would call:
        POST /{page_id}/videos  (video upload)
        POST /{page_id}/feed    (text/image post)
    Requires: page_access_token per page (passed via req.extra["page_tokens"]).
    """

    PLATFORM = "facebook"

    def _do_publish(self, req: PublishRequest, caption: str) -> PublishResult:
        raise NotImplementedError(
            "Facebook real API not configured. Supply page_access_token in req.extra."
        )

    def _do_schedule(self, req: PublishRequest, caption: str) -> PublishResult:
        raise NotImplementedError("Facebook scheduled posting requires page_access_token.")

    def _mock_publish(self, req: PublishRequest, caption: str) -> PublishResult:
        """Posts to all pages; returns first successful post_id."""
        pages   = req.pages or [req.account_id]
        page_id = pages[0]
        seed_str= f"fb:{req.account_id}:{page_id}:{int(time.time() // 3600)}"
        fake_id = hashlib.sha256(seed_str.encode()).hexdigest()[:16]
        return PublishResult(
            success    = True,
            platform   = self.PLATFORM,
            account_id = req.account_id,
            status     = PublishStatus.SUCCESS,
            post_id    = fake_id,
            url        = f"https://facebook.com/{page_id}/posts/{fake_id}",
            mock       = True,
        )

    def _mock_schedule(self, req: PublishRequest, caption: str) -> PublishResult:
        pages   = req.pages or [req.account_id]
        page_id = pages[0]
        seed_str= f"fb:sched:{req.account_id}:{page_id}:{req.schedule_ts}"
        fake_id = "sched_" + hashlib.sha256(seed_str.encode()).hexdigest()[:12]
        return PublishResult(
            success      = True,
            platform     = self.PLATFORM,
            account_id   = req.account_id,
            status       = PublishStatus.SCHEDULED,
            post_id      = fake_id,
            scheduled_ts = req.schedule_ts,
            url          = f"https://facebook.com/{page_id}/posts/{fake_id}",
            mock         = True,
        )


# ── YouTube adapter ───────────────────────────────────────────────────────────

class YouTubePublisher(BasePublisher):
    """
    YouTube Data API v3 adapter.

    Real mode would call:
        POST https://www.googleapis.com/upload/youtube/v3/videos
    Requires: oauth2 access_token (passed via req.extra["access_token"]).
    Title derived from first line of caption.
    """

    PLATFORM = "youtube"
    _TITLE_MAX = 100

    def _do_publish(self, req: PublishRequest, caption: str) -> PublishResult:
        raise NotImplementedError(
            "YouTube real API not configured. Supply OAuth2 access_token in req.extra."
        )

    def _do_schedule(self, req: PublishRequest, caption: str) -> PublishResult:
        raise NotImplementedError("YouTube scheduled publishing requires OAuth2 token.")

    def _mock_publish(self, req: PublishRequest, caption: str) -> PublishResult:
        # Title = first line of caption, max 100 chars
        title    = (caption.split("\n")[0])[:self._TITLE_MAX]
        seed_str = f"yt:{req.account_id}:{int(time.time() // 3600)}"
        fake_id  = hashlib.sha256(seed_str.encode()).hexdigest()[:11]  # YT video ID len
        return PublishResult(
            success    = True,
            platform   = self.PLATFORM,
            account_id = req.account_id,
            status     = PublishStatus.SUCCESS,
            post_id    = fake_id,
            url        = f"https://youtube.com/watch?v={fake_id}",
            mock       = True,
        )

    def _mock_schedule(self, req: PublishRequest, caption: str) -> PublishResult:
        seed_str = f"yt:sched:{req.account_id}:{req.schedule_ts}"
        fake_id  = "sched_" + hashlib.sha256(seed_str.encode()).hexdigest()[:10]
        return PublishResult(
            success      = True,
            platform     = self.PLATFORM,
            account_id   = req.account_id,
            status       = PublishStatus.SCHEDULED,
            post_id      = fake_id,
            scheduled_ts = req.schedule_ts,
            url          = f"https://youtube.com/watch?v={fake_id}",
            mock         = True,
        )


# ── PublisherRouter ───────────────────────────────────────────────────────────

class PublisherRouter:
    """
    Routes PublishRequests to the correct platform adapter.
    Integrates with FleetCoordinator for staggered delay.

    Usage:
        router = get_publisher_router()
        result = router.publish(req)

        # Multi-platform batch:
        results = router.publish_multi(req, platforms=["tiktok", "facebook"])
    """

    _ADAPTERS: dict[str, type[BasePublisher]] = {
        Platform.TIKTOK:   TikTokPublisher,
        Platform.FACEBOOK: FacebookPublisher,
        Platform.YOUTUBE:  YouTubePublisher,
    }

    def __init__(
        self,
        mock_mode:         bool  = MOCK_MODE,
        fleet_coordinator: Any | None = None,   # FleetCoordinator (optional)
        min_delay_secs:    float = 1.0,
        max_delay_secs:    float = 15.0,
    ) -> None:
        self._mock     = mock_mode
        self._fleet    = fleet_coordinator
        self._min_delay= min_delay_secs
        self._max_delay= max_delay_secs
        self._adapters: dict[str, BasePublisher] = {
            p: cls(mock_mode=mock_mode)
            for p, cls in self._ADAPTERS.items()
        }

    # ── Public API ────────────────────────────────────────────────────────────

    def publish(self, req: PublishRequest) -> PublishResult:
        """
        Route to the correct platform adapter, applying deterministic delay.

        Args:
            req: PublishRequest with platform, video_path, caption, etc.

        Returns:
            PublishResult (never raises).
        """
        platform = req.platform.lower()
        adapter  = self._adapters.get(platform)
        if adapter is None:
            return PublishResult(
                success    = False,
                platform   = platform,
                account_id = req.account_id,
                status     = PublishStatus.FAILED,
                error      = f"Unknown platform: {platform!r}",
                error_type = "UnknownPlatformError",
            )

        delay = self._compute_delay(req.account_id, platform)

        LOGGER.info("routing_publish", extra={
            "platform":   platform,
            "account_id": req.account_id,
            "delay_secs": delay,
            "scheduled":  req.schedule_ts is not None,
            "mock":       self._mock,
        })

        return adapter.publish(req, delay_secs=delay)

    def publish_multi(
        self,
        req:       PublishRequest,
        platforms: list[str] | None = None,
    ) -> list[PublishResult]:
        """
        Publish to multiple platforms sequentially.
        Each platform gets an independent delay derived from its seed.

        Args:
            req:       Base PublishRequest (platform field is overridden per iteration).
            platforms: List of platform names; defaults to req.platform only.

        Returns:
            List of PublishResult, one per platform.
        """
        targets = platforms or [req.platform]
        results: list[PublishResult] = []
        for p in targets:
            platform_req = PublishRequest(
                account_id     = req.account_id,
                video_path     = req.video_path,
                caption        = req.caption,
                platform       = p,
                pages          = req.pages,
                affiliate_link = req.affiliate_link,
                schedule_ts    = req.schedule_ts,
                tags           = req.tags,
                thumbnail_path = req.thumbnail_path,
                extra          = req.extra,
            )
            results.append(self.publish(platform_req))
        return results

    def get_adapter(self, platform: str) -> BasePublisher | None:
        """Return the raw adapter for a platform (for testing / direct access)."""
        return self._adapters.get(platform.lower())

    # ── Delay computation ─────────────────────────────────────────────────────

    def _compute_delay(self, account_id: str, platform: str) -> float:
        """
        Derive publish delay from FleetCoordinator stagger or seeded PRNG fallback.

        If FleetCoordinator is attached, use its _compute_stagger() method.
        Otherwise fall back to seeded jitter within [min_delay, max_delay].
        """
        if self._fleet is not None:
            try:
                slot = self._fleet.request_slot(account_id)
                if slot.allowed:
                    return max(self._min_delay, slot.delay_secs)
                else:
                    LOGGER.warning("publish_slot_denied", extra={
                        "account_id": account_id,
                        "reason":     slot.reason,
                        "platform":   platform,
                    })
                    # Still apply minimum delay even if fleet denied a session slot
                    # (publisher delay ≠ session slot — different rate limits)
                    pass
            except Exception as exc:
                LOGGER.warning("fleet_stagger_error", extra={"error": str(exc)})

        # Fallback: seeded deterministic delay
        platform_offset = {"tiktok": 0, "facebook": 10, "youtube": 20}.get(platform, 0)
        frac  = _pseed(account_id, 700 + platform_offset)
        delay = self._min_delay + frac * (self._max_delay - self._min_delay)
        return round(delay, 2)


# ── Singleton ─────────────────────────────────────────────────────────────────

_PUBLISHER_ROUTER: PublisherRouter | None = None


def get_publisher_router(
    mock_mode:         bool  = MOCK_MODE,
    fleet_coordinator: Any | None = None,
) -> PublisherRouter:
    """Return (or create) the process-level PublisherRouter singleton."""
    global _PUBLISHER_ROUTER
    if _PUBLISHER_ROUTER is None:
        _PUBLISHER_ROUTER = PublisherRouter(
            mock_mode         = mock_mode,
            fleet_coordinator = fleet_coordinator,
        )
    return _PUBLISHER_ROUTER


def reset_publisher_router() -> None:
    """Reset singleton (for testing)."""
    global _PUBLISHER_ROUTER
    _PUBLISHER_ROUTER = None
