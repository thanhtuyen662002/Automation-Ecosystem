"""
execution/publisher_playwright.py — Browser automation publisher (V2).

Supports TikTok and Facebook Reels upload via Playwright (async).
No official API is used. All actions are human-like.

V2 upgrades:
  - account_manager integration (context built per-account with proxy + cookies)
  - Stealth JS injected before every page load
  - Checkpoint-based retry: failed mid-flow restarts from last checkpoint
  - Richer human simulation: mouse movements, pre-action hesitation, typos+corrections
  - Session auto-save after successful login

Design contracts:
  - Async-only: all public functions are coroutines.
  - Retry: up to MAX_RETRIES attempts with exponential back-off.
  - Exception-safe: all public functions return PublishResult (never raise).
  - Credentials never logged.

Usage (V2 preferred):
    from execution.publisher_playwright import publish_v2
    result = asyncio.run(publish_v2(candidate_dict, account_dict))
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("execution.publisher_playwright")

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_RETRIES: int       = 3
RETRY_BASE_S: float    = 2.0        # exponential back-off base
DELAY_MIN_S:  float    = 1.0        # min inter-action delay
DELAY_MAX_S:  float    = 5.0        # max inter-action delay
TYPE_DELAY_MS: tuple[int, int] = (40, 120)   # per-character typing delay range

# Platform upload URLs (no API — direct web upload flow)
_TIKTOK_UPLOAD_URL   = "https://www.tiktok.com/upload"
_FACEBOOK_REELS_URL  = "https://www.facebook.com/reels/create"

# Storage path for saved auth sessions
_SESSION_DIR = Path(os.environ.get("PLAYWRIGHT_SESSION_DIR", "data/sessions"))


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class PublishResult:
    success:    bool
    platform:   str
    content_id: str
    url:        str       = ""
    error:      str       = ""
    attempts:   int       = 1
    elapsed_s:  float     = 0.0
    meta:       dict[str, Any] = field(default_factory=dict)


class SessionStateError(RuntimeError):
    def __init__(self, code: str, message: str, diagnostic: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.diagnostic = diagnostic or code


# ── Human-like helpers ────────────────────────────────────────────────────────

async def _human_delay(lo: float = DELAY_MIN_S, hi: float = DELAY_MAX_S) -> None:
    """Sleep a random duration to mimic human pacing."""
    await asyncio.sleep(random.uniform(lo, hi))


async def _type_humanlike(page: Any, selector: str, text: str) -> None:
    """Type text character-by-character with random per-char delay."""
    await page.click(selector)
    for ch in text:
        await page.keyboard.type(ch)
        await asyncio.sleep(random.randint(*TYPE_DELAY_MS) / 1000)


async def _random_scroll(page: Any, pixels: int = 200) -> None:
    """Scroll slightly to simulate browsing."""
    delta = random.randint(pixels // 2, pixels)
    await page.mouse.wheel(0, delta)
    await asyncio.sleep(random.uniform(0.3, 0.8))


def _account_id(account: dict[str, Any]) -> str:
    return str(account.get("account_id") or account.get("id") or "default")


def _has_connected_session(account: dict[str, Any]) -> bool:
    if bool(account.get("session_valid")):
        return True
    try:
        from core.browser_providers import (
            BROWSER_PROVIDER_ADSPOWER_MANUAL,
            BROWSER_PROVIDER_REAL_CHROME,
            resolve_browser_provider,
        )
        provider = resolve_browser_provider(account)
        if provider == BROWSER_PROVIDER_ADSPOWER_MANUAL:
            return False
        if provider == BROWSER_PROVIDER_REAL_CHROME:
            return bool(account.get("cookies"))
    except Exception:
        pass
    if bool(account.get("browser_data_dir")):
        return True
    cookie_file = str(account.get("cookie_file") or "")
    if cookie_file and Path(cookie_file).exists():
        return True
    platform = str(account.get("platform") or "tiktok")
    default_cookie_file = _session_path(platform, _account_id(account))
    return default_cookie_file.exists()


async def _ensure_connected_session(page: Any, platform: str) -> None:
    """Check an existing manual-connect session without trying to log in."""
    from core.login_diagnostics import (
        LoginBlockStatus,
        classify_login_block,
        login_block_error_message,
    )

    platform_key = platform.lower()
    target = _TIKTOK_UPLOAD_URL if platform_key == "tiktok" else _FACEBOOK_REELS_URL
    await page.goto(target, wait_until="domcontentloaded", timeout=60_000)
    await _human_delay(1.0, 2.0)

    status = await classify_login_block(page)
    if status == LoginBlockStatus.OK:
        return
    if status == LoginBlockStatus.LOGIN_PAGE:
        raise SessionStateError("SESSION_EXPIRED", "SESSION_EXPIRED", status.value)
    if status == LoginBlockStatus.RATE_LIMITED:
        raise SessionStateError("SESSION_LIMITED", login_block_error_message(status), status.value)
    if status == LoginBlockStatus.CAPTCHA_REQUIRED:
        raise SessionStateError("CAPTCHA_REQUIRED", login_block_error_message(status), status.value)
    if status == LoginBlockStatus.CHECKPOINT_REQUIRED:
        raise SessionStateError("CHECKPOINT_REQUIRED", login_block_error_message(status), status.value)


# ── Stealth JS (injected on every new page) ───────────────────────────────────

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins',   {get: () => [1, 2, 3]});
Object.defineProperty(navigator, 'languages', {get: () => ['vi-VN', 'vi', 'en-US', 'en']});
window.chrome = {runtime: {}};
"""


# ── Session persistence ────────────────────────────────────────────────────────

def _session_path(platform: str, account_id: str) -> Path:
    _SESSION_DIR.mkdir(parents=True, exist_ok=True)
    safe_id = account_id.replace("/", "_").replace(":", "_")
    return _SESSION_DIR / f"{platform}_{safe_id}.json"


# ── Human-like helpers (V2 extended) ─────────────────────────────────────────

async def _mouse_wander(page: Any) -> None:
    """Move mouse to a few random positions — mimics idle human behaviour."""
    for _ in range(random.randint(1, 3)):
        x = random.randint(100, 1100)
        y = random.randint(100, 700)
        await page.mouse.move(x, y)
        await asyncio.sleep(random.uniform(0.1, 0.4))


async def _hesitate(page: Any) -> None:
    """Simulate pre-action hesitation (look before clicking)."""
    await _mouse_wander(page)
    await asyncio.sleep(random.uniform(0.3, 1.2))


async def _type_with_typo(page: Any, selector: str, text: str) -> None:
    """
    Type text with occasional typo-then-backspace to appear human.
    Typo probability: ~8% per character.
    """
    await page.click(selector)
    await asyncio.sleep(random.uniform(0.2, 0.6))
    TYPO_CHARS = "qwertyuiopasdfghjklzxcvbnm"
    for ch in text:
        if random.random() < 0.08:   # 8% typo chance
            wrong = random.choice(TYPO_CHARS)
            await page.keyboard.type(wrong)
            await asyncio.sleep(random.uniform(0.08, 0.25))
            await page.keyboard.press("Backspace")
            await asyncio.sleep(random.uniform(0.05, 0.15))
        await page.keyboard.type(ch)
        await asyncio.sleep(random.randint(*TYPE_DELAY_MS) / 1000)

# ── TikTok ────────────────────────────────────────────────────────────────────

async def login_tiktok(
    page: Any,
    credentials: dict[str, str],
) -> bool:
    """
    Navigate to TikTok login and authenticate.

    credentials: {"username": str, "password": str, "account_id": str}

    Returns True on success. Session is saved to disk.
    """
    try:
        session_file = _session_path("tiktok", credentials.get("account_id", "default"))

        # Restore saved session if it exists
        if session_file.exists():
            await page.context.add_cookies(
                __import__("json").loads(session_file.read_text())
            )
            await page.goto("https://www.tiktok.com/", wait_until="domcontentloaded")
            await _human_delay(1.5, 3.0)
            # Check if we're already logged in
            if await page.locator('[data-e2e="profile-icon"]').count() > 0:
                LOGGER.info("tiktok_session_restored account=%s", credentials.get("account_id"))
                return True

        # Fresh login
        await page.goto("https://www.tiktok.com/login/phone-or-email/email",
                        wait_until="domcontentloaded")
        await _human_delay(2.0, 4.0)

        # Email field
        email_sel = 'input[name="username"], input[type="email"]'
        await page.wait_for_selector(email_sel, timeout=15_000)
        await _type_humanlike(page, email_sel, credentials["username"])
        await _human_delay(0.5, 1.5)

        # Password field
        pass_sel = 'input[type="password"]'
        await _type_humanlike(page, pass_sel, credentials["password"])
        await _human_delay(0.8, 2.0)

        # Submit
        await page.keyboard.press("Enter")
        await _human_delay(3.0, 6.0)

        # Wait for redirect to home / profile
        await page.wait_for_url(
            lambda url: "tiktok.com" in url and "login" not in url,
            timeout=30_000,
        )

        # Persist session cookies
        cookies = await page.context.cookies()
        session_file.write_text(__import__("json").dumps(cookies))
        LOGGER.info("tiktok_login_success account=%s", credentials.get("account_id"))
        return True

    except Exception as exc:
        LOGGER.warning("tiktok_login_failed account=%s error=%s",
                       credentials.get("account_id"), exc)
        return False


async def upload_video_tiktok(
    page: Any,
    video_path: str,
    caption: str,
    hashtags: list[str] | None = None,
    *,
    tracking_code: str = "",
) -> str:
    """
    Upload a video to TikTok via the web upload flow.

    Returns the post URL if detectable, otherwise "".
    Raises RuntimeError on upload failure (caller handles retries).
    """
    tags_str = " ".join(f"#{h.lstrip('#')}" for h in (hashtags or []))
    full_caption = f"{caption}\n{tags_str}"
    if tracking_code:
        full_caption += f"\n{tracking_code}"

    await page.goto(_TIKTOK_UPLOAD_URL, wait_until="domcontentloaded")
    await _human_delay(2.0, 4.0)

    # File input upload
    file_input = page.locator('input[type="file"]').first
    await file_input.set_input_files(video_path)
    LOGGER.debug("tiktok_upload_file_set path=%s", video_path)

    # Wait for video processing bar to appear then finish
    await page.wait_for_selector('[class*="upload-progress"], [class*="processing"]',
                                 timeout=60_000, state="visible")
    await page.wait_for_selector('[class*="upload-progress"], [class*="processing"]',
                                 timeout=120_000, state="hidden")
    await _human_delay(1.5, 3.0)

    # Caption
    caption_sel = '[data-e2e="caption-input"], div[contenteditable="true"]'
    await page.wait_for_selector(caption_sel, timeout=20_000)
    await page.click(caption_sel)
    await _human_delay(0.5, 1.0)
    await page.keyboard.type(full_caption, delay=random.randint(*TYPE_DELAY_MS))
    await _human_delay(1.0, 2.5)

    # Post button
    post_btn = page.locator('[data-e2e="post-button"], button:has-text("Post")').first
    await post_btn.scroll_into_view_if_needed()
    await _human_delay(0.5, 1.5)
    await post_btn.click()
    await _human_delay(4.0, 8.0)

    # Try to extract post URL from redirect
    post_url = page.url
    if "video" in post_url or "/@" in post_url:
        LOGGER.info("tiktok_upload_success url=%s", post_url)
        return post_url

    # Fallback: check for success toast
    try:
        await page.wait_for_selector('[class*="success"], [class*="posted"]',
                                     timeout=10_000)
    except Exception:
        pass

    LOGGER.info("tiktok_upload_complete url_unknown")
    return ""


# ── Facebook Reels ────────────────────────────────────────────────────────────

async def login_facebook(
    page: Any,
    credentials: dict[str, str],
) -> bool:
    """
    Authenticate to Facebook.

    credentials: {"email": str, "password": str, "account_id": str}
    """
    try:
        session_file = _session_path("facebook", credentials.get("account_id", "default"))

        if session_file.exists():
            await page.context.add_cookies(
                __import__("json").loads(session_file.read_text())
            )
            await page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
            await _human_delay(1.5, 3.0)
            if await page.locator('[aria-label="Your profile"]').count() > 0:
                LOGGER.info("facebook_session_restored account=%s",
                            credentials.get("account_id"))
                return True

        await page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")
        await _human_delay(1.5, 3.5)

        await _type_humanlike(page, '#email', credentials["email"])
        await _human_delay(0.4, 1.2)
        await _type_humanlike(page, '#pass', credentials["password"])
        await _human_delay(0.6, 1.8)

        await page.click('[name="login"]')
        await _human_delay(3.0, 6.0)

        await page.wait_for_url(
            lambda url: "facebook.com" in url and "login" not in url,
            timeout=30_000,
        )

        cookies = await page.context.cookies()
        session_file.write_text(__import__("json").dumps(cookies))
        LOGGER.info("facebook_login_success account=%s", credentials.get("account_id"))
        return True

    except Exception as exc:
        LOGGER.warning("facebook_login_failed account=%s error=%s",
                       credentials.get("account_id"), exc)
        return False


async def upload_video_facebook(
    page: Any,
    video_path: str,
    caption: str,
    *,
    tracking_code: str = "",
) -> str:
    """
    Upload a Reel to Facebook via the web creator flow.

    Returns post URL or "".
    """
    full_caption = caption
    if tracking_code:
        full_caption += f"\n{tracking_code}"

    await page.goto(_FACEBOOK_REELS_URL, wait_until="domcontentloaded")
    await _human_delay(2.0, 4.0)

    # Video file input
    file_input = page.locator('input[type="file"][accept*="video"]').first
    await file_input.set_input_files(video_path)
    LOGGER.debug("facebook_upload_file_set path=%s", video_path)

    # Wait for processing
    await page.wait_for_selector('[aria-label*="processing"], [role="progressbar"]',
                                 timeout=60_000, state="visible")
    await page.wait_for_selector('[aria-label*="processing"], [role="progressbar"]',
                                 timeout=180_000, state="hidden")
    await _human_delay(1.5, 3.0)

    # Caption / description field
    desc_sel = '[aria-label*="caption"], [aria-label*="description"], div[contenteditable="true"]'
    try:
        await page.wait_for_selector(desc_sel, timeout=15_000)
        await page.click(desc_sel)
        await page.keyboard.type(full_caption, delay=random.randint(*TYPE_DELAY_MS))
    except Exception:
        LOGGER.debug("facebook_caption_selector_miss — skipping caption")
    await _human_delay(1.0, 2.0)

    # Publish button
    pub_btn = page.locator('div[role="button"]:has-text("Publish"), button:has-text("Share")').first
    await pub_btn.scroll_into_view_if_needed()
    await _human_delay(0.5, 1.5)
    await pub_btn.click()
    await _human_delay(3.0, 7.0)

    post_url = page.url
    LOGGER.info("facebook_upload_complete url=%s", post_url)
    return post_url if "facebook.com" in post_url else ""


# ── Shared browser builder ────────────────────────────────────────────────────

async def _build_browser(pw: Any, account: dict[str, Any] | None, headless: bool):
    """
    Build a stealth browser + context.

    Uses account_manager if account dict provided (preferred).
    Falls back to plain context for backwards-compatibility.
    """
    if account:
        try:
            from execution.account_manager import build_playwright_context
            managed_account = dict(account)
            managed_account["headless"] = headless
            return await build_playwright_context(managed_account, pw)
        except Exception as exc:
            LOGGER.warning("managed_context_build_failed account=%s error=%s", _account_id(account), exc)
            raise

    # Legacy fallback
    browser = await pw.chromium.launch(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled",
              "--no-sandbox", "--disable-dev-shm-usage"],
    )
    ctx  = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
        locale="vi-VN",
    )
    page = await ctx.new_page()
    await page.add_init_script(_STEALTH_JS)
    return browser, ctx, page


# ── High-level publish entry points ───────────────────────────────────────────

async def publish(
    content_id:  str,
    platform:    str,
    video_path:  str,
    caption:     str,
    credentials: dict[str, str],
    hashtags:    list[str] | None = None,
    tracking_code: str = "",
    headless:    bool = True,
) -> PublishResult:
    """
    V1 publish — backwards compatible. Uses credential dict directly.
    For new code, prefer publish_v2() which uses account_manager.

    platform: "tiktok" | "facebook"
    Returns PublishResult (never raises).
    """
    account = dict(credentials)
    account.setdefault("account_id", credentials.get("account_id", "default"))
    account.setdefault("platform",   platform)
    return await publish_v2(
        content_id    = content_id,
        platform      = platform,
        video_path    = video_path,
        caption       = caption,
        account       = account,
        hashtags      = hashtags,
        tracking_code = tracking_code,
        headless      = headless,
    )


async def publish_v2(
    content_id:    str,
    platform:      str,
    video_path:    str,
    caption:       str,
    account:       dict[str, Any],
    hashtags:      list[str] | None = None,
    tracking_code: str = "",
    headless:      bool = True,
) -> PublishResult:
    """
    V2 publish — uses account_manager for context + stealth + session.

    account: full account dict from account_manager (includes proxy, cookie_file, etc.)
    Returns PublishResult (never raises).
    """
    try:
        from playwright.async_api import async_playwright   # type: ignore[import]
    except ImportError:
        return PublishResult(
            success=False, platform=platform, content_id=content_id,
            error="playwright not installed — run: pip install playwright && playwright install chromium",
        )

    t0          = time.monotonic()
    account_id  = _account_id(account)
    last_checkpoint = "start"

    if platform.lower() in {"tiktok", "facebook"} and not _has_connected_session(account):
        return PublishResult(
            success=False,
            platform=platform,
            content_id=content_id,
            error="SESSION_NOT_CONNECTED",
            attempts=0,
            elapsed_s=round(time.monotonic() - t0, 2),
            meta={"account_id": account_id, "error_code": "SESSION_NOT_CONNECTED"},
        )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with async_playwright() as pw:
                browser, ctx, page = await _build_browser(pw, account, headless)
                try:
                    last_checkpoint = "session_check"
                    await _ensure_connected_session(page, platform)
                    if platform == "tiktok":
                        last_checkpoint = "upload"
                        await _hesitate(page)
                        url = await upload_video_tiktok(
                            page, video_path, caption,
                            hashtags=hashtags, tracking_code=tracking_code,
                        )
                    elif platform == "facebook":
                        last_checkpoint = "upload"
                        await _hesitate(page)
                        url = await upload_video_facebook(
                            page, video_path, caption, tracking_code=tracking_code,
                        )
                    else:
                        raise ValueError(f"unsupported platform: {platform}")

                    # Save session after successful operation
                    try:
                        from execution.account_manager import save_session
                        await save_session(account, ctx)
                    except Exception:
                        pass

                    await browser.close()
                    elapsed = round(time.monotonic() - t0, 2)
                    LOGGER.info(
                        "publish_v2_success platform=%s account=%s attempt=%d checkpoint=%s elapsed=%.1fs",
                        platform, account_id, attempt, last_checkpoint, elapsed,
                    )
                    return PublishResult(
                        success=True, platform=platform, content_id=content_id,
                        url=url, attempts=attempt, elapsed_s=elapsed,
                        meta={"account_id": account_id, "checkpoint": last_checkpoint},
                    )

                except Exception as inner_exc:
                    LOGGER.warning(
                        "publish_v2_inner_error platform=%s attempt=%d checkpoint=%s error=%s",
                        platform, attempt, last_checkpoint, inner_exc,
                    )
                    await browser.close()
                    raise inner_exc

        except SessionStateError as exc:
            elapsed = round(time.monotonic() - t0, 2)
            try:
                from execution.account_manager import mark_failed
                mark_failed(account_id, exc.code)
            except Exception:
                pass
            event = {
                "SESSION_EXPIRED": "account_session_expired",
                "SESSION_LIMITED": "tiktok_login_rate_limited",
                "CAPTCHA_REQUIRED": "tiktok_checkpoint_required",
                "CHECKPOINT_REQUIRED": "tiktok_checkpoint_required",
            }.get(exc.code, "publish_v2_session_blocked")
            LOGGER.warning(
                "%s platform=%s account=%s checkpoint=%s code=%s diagnostic=%s",
                event, platform, account_id, last_checkpoint, exc.code, exc.diagnostic,
                extra={"event": event, "account_id": account_id, "platform": platform, "code": exc.code, "diagnostic": exc.diagnostic},
            )
            return PublishResult(
                success=False,
                platform=platform,
                content_id=content_id,
                error=str(exc),
                attempts=attempt,
                elapsed_s=elapsed,
                meta={"account_id": account_id, "checkpoint": last_checkpoint, "error_code": exc.code, "diagnostic": exc.diagnostic},
            )
        except Exception as exc:
            LOGGER.warning(
                "publish_v2_attempt_failed platform=%s attempt=%d/%d error=%s",
                platform, attempt, MAX_RETRIES, exc,
            )
            if "login_failed" in str(exc).lower():
                break
            if attempt < MAX_RETRIES:
                backoff = RETRY_BASE_S * (2 ** (attempt - 1)) + random.uniform(0, 2)
                LOGGER.info("publish_v2_retry in=%.1fs checkpoint=%s", backoff, last_checkpoint)
                await asyncio.sleep(backoff)

    elapsed = round(time.monotonic() - t0, 2)
    return PublishResult(
        success=False, platform=platform, content_id=content_id,
        error=f"failed after {MAX_RETRIES} attempts (last checkpoint: {last_checkpoint})",
        attempts=MAX_RETRIES, elapsed_s=elapsed,
        meta={"account_id": account_id},
    )
