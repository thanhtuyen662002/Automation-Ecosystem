"""
TikTok video publisher — production-grade anti-detection build.

Safety stack (in execution order):
  1.  Account status guard (banned / limited)
  2.  HARD proxy requirement — RetryableDependencyError if proxy missing
  3.  Parallelism control — one active publish per account
  4.  Rate-limit cooldown — MIN_PUBLISH_INTERVAL_SECONDS between posts
  5.  Session validation — cookies present + session_valid flag
  6.  Video file existence check
  7.  Persistent browser context (per-account Chromium profile)
  8.  10-script stealth layer (applied automatically by browser_context)
  9.  BehaviorEngine warm-up: lognormal delays, bezier mouse, risk-constrained scroll
 10.  Login / captcha detection on every page transition
 11.  Upload with behavior-engine simulation (skip logic, typing delay)
 12.  Risk score update on success / failure / captcha
 13.  BehaviorEngine replaces all raw gaussian/uniform calls throughout

Phase 2 (NOT YET): playwright-stealth package, canvas/WebGL full spoofing.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("workers.handlers.publish.tiktok")

# ── Error codes ──────────────────────────────────────────────────────────────
ACCOUNT_AUTH_REQUIRED = "ACCOUNT_AUTH_REQUIRED"
ACCOUNT_BANNED        = "ACCOUNT_BANNED"
ACCOUNT_LIMITED       = "ACCOUNT_LIMITED"
ARTIFACT_NOT_APPROVED = "ARTIFACT_NOT_APPROVED"
MISSING_VIDEO_PATH    = "MISSING_VIDEO_PATH"
POLICY_VIOLATION      = "POLICY_VIOLATION"

# ── Safety constants ─────────────────────────────────────────────────────────
MIN_PUBLISH_INTERVAL_SECONDS: int = int(
    os.environ.get("MIN_PUBLISH_INTERVAL_SECONDS", "600")
)

CAPTCHA_INDICATORS = [
    "captcha", "robot", "verify you are human",
    "are you a robot", "security check", "challenge",
    "unusual activity", "suspicious activity",
]


# ── Mouse / scroll helpers (thin wrappers — real logic lives in BehaviorEngine) ──
# These are kept as module-level functions so _detect_session_issues can be called
# before the engine is initialised. All warmup/upload paths use engine methods.

async def _hover_random_point(page: Any) -> None:
    """Hover a random safe point in the lower-centre of the viewport (no click)."""
    try:
        vp = page.viewport_size or {"width": 1280, "height": 720}
        x = random.randint(vp["width"] // 3, 2 * vp["width"] // 3)
        y = random.randint(vp["height"] // 2, vp["height"] - 80)
        await page.mouse.move(x, y)
        await asyncio.sleep(max(0.15, random.gauss(0.35, 0.12)))
    except Exception:
        pass


# ── Detection helpers ─────────────────────────────────────────────────────────

async def _is_captcha(page: Any) -> bool:
    try:
        content = (await page.content()).lower()
        return any(ind in content for ind in CAPTCHA_INDICATORS)
    except Exception:
        return False


async def _detect_session_issues(
    page: Any,
    platform: str,
    account_id: str,
    database: Any,
) -> None:
    """Detect login redirect or captcha and raise FatalDependencyError.
    Also updates risk counters so the account is auto-paused if this repeats.
    """
    from core.platform_config import is_login_page
    from workers.worker_runtime import FatalDependencyError

    url = page.url

    if is_login_page(url, platform):
        risk = await database.record_login_redirect(account_id)
        LOGGER.warning(
            "session_login_redirect",
            extra={
                "event": "session_login_redirect",
                "account_id": account_id,
                "url": url,
                "new_risk_score": risk,
            },
        )
        raise FatalDependencyError(
            f"[{ACCOUNT_AUTH_REQUIRED}] Session expired — redirected to login page. "
            "Use POST /api/v1/accounts/{id}/connect to re-authenticate."
        )

    if await _is_captcha(page):
        risk = await database.record_captcha_hit(account_id)
        LOGGER.warning(
            "captcha_detected",
            extra={
                "event": "captcha_detected",
                "account_id": account_id,
                "url": url,
                "new_risk_score": risk,
            },
        )
        raise FatalDependencyError(
            f"[{ACCOUNT_AUTH_REQUIRED}] Captcha detected (risk_score={risk:.2f}). "
            "Session invalidated — reconnect and solve captcha manually."
        )


# ── Main publisher ────────────────────────────────────────────────────────────

async def publish_tiktok_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Publish a video to TikTok using persistent Playwright session + stealth layer.

    Required payload:
      account_id  (str) — account UUID
      video_path  (str) — absolute path to video file
      caption     (str) — post caption (max 2200 chars)

    Returns:
      {"published": True, "platform": "tiktok", "account_id": ..., ...}

    Fatal (no retry): ACCOUNT_AUTH_REQUIRED, ACCOUNT_BANNED, ARTIFACT_NOT_APPROVED
    Retryable: ACCOUNT_LIMITED, POLICY_VIOLATION, proxy missing, unexpected errors
    """
    from database.database import AutomationDatabase, RetryConfig
    from core.session_crypto import decrypt_cookies
    from core.platform_config import get_platform_config
    from core.stealth import fingerprint_hash
    from core.browser_providers import BROWSER_PROVIDER_PLAYWRIGHT, make_browser_provider, resolve_browser_provider
    from core.behavior_engine import create_behavior_engine
    from core.cross_account_coordinator import get_coordinator
    from workers.worker_runtime import FatalDependencyError, RetryableDependencyError

    account_id: str = payload.get("account_id", "")
    video_path: str = payload.get("video_path", "")
    caption: str    = payload.get("caption", "")

    if not account_id:
        raise FatalDependencyError("publish_tiktok: 'account_id' is required in payload")

    db_url = os.environ.get("DATABASE_URL", "")
    database = AutomationDatabase(db_url, retry_config=RetryConfig())
    await database.open()

    t_publish_start = time.monotonic()
    final_url = ""

    try:
        # ── 1. Load account ──────────────────────────────────────────────────
        account = await database.get_account(account_id)
        if account is None:
            raise FatalDependencyError(f"Account {account_id} not found")

        acc_status = account.get("status", "")
        proxy_url: str | None = account.get("proxy_url") or None
        browser_provider = resolve_browser_provider(account)

        fp_hash = fingerprint_hash(account_id)

        LOGGER.info(
            "tiktok_publish_start",
            extra={
                "event": "tiktok_publish_start",
                "account_id": account_id,
                "account_handle": account.get("account_handle"),
                "has_proxy": bool(proxy_url),
                "browser_provider": browser_provider,
                "fingerprint_hash": fp_hash,
                "video_path": video_path,
                "caption_length": len(caption),
            },
        )

        # ── 2. Account status guard ──────────────────────────────────────────
        if acc_status == "banned":
            raise FatalDependencyError(
                f"[{ACCOUNT_BANNED}] Account {account_id} is banned. Not retrying."
            )
        if acc_status == "limited":
            raise RetryableDependencyError(
                f"[{ACCOUNT_LIMITED}] Account {account_id} is limited. Retry later."
            )

        # ── 2a. Soft-ban check ───────────────────────────────────────────────
        if bool(account.get("soft_ban_detected", 0)):
            raise RetryableDependencyError(
                f"[{ACCOUNT_LIMITED}] Account {account_id} has soft-ban signals detected. "
                "Review account manually, clear flag via POST /api/v1/accounts/{id}/clear-soft-ban."
            )

        # ── 2b. Account age awareness ────────────────────────────────────────
        from core.geo_validator import account_age_days as _account_age_days
        _age_days = _account_age_days(account.get("created_at"))
        _is_new_account = _age_days is not None and _age_days < 7
        if _is_new_account:
            LOGGER.info(
                "new_account_extended_warmup",
                extra={
                    "event": "new_account_extended_warmup",
                    "account_id": account_id,
                    "age_days": _age_days,
                    "note": "Account < 7 days old — extended warm-up will be applied",
                },
            )

        # ── 3. HARD PROXY REQUIREMENT ────────────────────────────────────────
        if not proxy_url:
            raise RetryableDependencyError(
                f"No proxy configured for account {account_id}. "
                "Set proxy_url via PUT /api/v1/accounts/{id} and retry. "
                "Each account MUST use a dedicated proxy for production-scale publishing."
            )

        # ── 3a. Proxy TCP health check ───────────────────────────────────────
        from core.proxy_validator import check_proxy_connectivity, guess_country_from_proxy_url
        _proxy_reachable, _proxy_latency_ms = await check_proxy_connectivity(proxy_url, timeout_seconds=8.0)
        if not _proxy_reachable:
            LOGGER.error(
                "proxy_unreachable",
                extra={
                    "event": "proxy_unreachable",
                    "account_id": account_id,
                    "proxy": proxy_url,
                },
            )
            raise RetryableDependencyError(
                f"Proxy {proxy_url!r} is unreachable (TCP timeout). "
                "Fix or replace the proxy and retry."
            )
        LOGGER.info(
            "proxy_healthy",
            extra={
                "event": "proxy_healthy",
                "account_id": account_id,
                "proxy": proxy_url,
                "latency_ms": _proxy_latency_ms,
            },
        )
        _proxy_country = guess_country_from_proxy_url(proxy_url)
        await database.update_proxy_health(account_id, _proxy_latency_ms, country=_proxy_country)

        # ── 3a-bis. Build BehaviorEngine + apply personality distribution balance ──
        # Built here so constraints reflect actual measured proxy latency.
        _coordinator = get_coordinator()
        _engine = create_behavior_engine(
            account_id=account_id,
            account_data=account,
            proxy_latency_ms=_proxy_latency_ms,
        )
        # Coordinator may downgrade activity_level to keep global distribution healthy
        _engine.personality = _coordinator.adjust_personality(_engine.personality)

        # If proxy is catastrophically slow → abort immediately
        if _engine.constraints.minimal_mode:
            raise RetryableDependencyError(
                f"Proxy {proxy_url!r} latency={_proxy_latency_ms}ms exceeds danger threshold. "
                "Aborting session to protect account. Replace proxy and retry."
            )

        # ── 3b-jitter. Coordinator-driven job-start delay (replaces engine.job_start_jitter) ─
        # Coordinator adds burst-penalty and proxy-stagger on top of the base jitter.
        _coord_delay = await _coordinator.get_start_delay(account_id, proxy_url)
        await asyncio.sleep(_coord_delay)

        # Register job start so subsequent accounts see this slot as occupied
        _coordinator.register_job_start(account_id, proxy_url or "")

        # ── 3b. Proxy over-sharing check (1 account = 1 proxy rule) ─────────
        _proxy_share_count = await database.get_proxy_account_count(proxy_url)
        if _proxy_share_count > 3:
            LOGGER.warning(
                "proxy_overused",
                extra={
                    "event": "proxy_overused",
                    "proxy": proxy_url,
                    "account_count": _proxy_share_count,
                    "account_id": account_id,
                    "warning": "Proxy shared by >3 accounts — HIGH correlation risk",
                },
            )

        # ── 3c. Geo consistency — deferred to after session load (step 6) ────

        # ── 4. Parallelism control ───────────────────────────────────────────
        if await database.has_running_task_for_account(account_id):
            raise RetryableDependencyError(
                f"Account {account_id} already has a running publish task. Retrying."
            )

        # ── 5. Rate-limit cooldown ───────────────────────────────────────────
        elapsed = await database.get_seconds_since_last_publish(account_id)
        if elapsed is not None and elapsed < MIN_PUBLISH_INTERVAL_SECONDS:
            wait = int(MIN_PUBLISH_INTERVAL_SECONDS - elapsed)
            LOGGER.warning(
                "publish_rate_limited",
                extra={
                    "event": "publish_rate_limited",
                    "account_id": account_id,
                    "elapsed_seconds": int(elapsed),
                    "min_interval": MIN_PUBLISH_INTERVAL_SECONDS,
                    "retry_in_seconds": wait,
                },
            )
            raise RetryableDependencyError(
                f"[{POLICY_VIOLATION}] Last publish was {int(elapsed)}s ago. "
                f"Min interval={MIN_PUBLISH_INTERVAL_SECONDS}s. Retry in ~{wait}s."
            )

        # ── 6. Session validation ────────────────────────────────────────────
        session = await database.get_account_session(account_id)
        if not session or not session.get("cookies"):
            raise FatalDependencyError(
                f"[{ACCOUNT_AUTH_REQUIRED}] No session for account {account_id}. "
                "Use POST /api/v1/accounts/{id}/connect."
            )
        if not bool(session.get("session_valid", 0)):
            raise FatalDependencyError(
                f"[{ACCOUNT_AUTH_REQUIRED}] Session expired for account {account_id}. "
                "Use POST /api/v1/accounts/{id}/connect."
            )

        # ── 3c. Geo consistency (proxy country vs account timezone/locale) ────
        from core.geo_validator import check_geo_consistency
        _geo_issues = check_geo_consistency(
            session.get("timezone", ""),
            session.get("locale", ""),
            _proxy_country,
        )
        if _geo_issues:
            LOGGER.warning(
                "geo_inconsistency_detected",
                extra={
                    "event": "geo_inconsistency_detected",
                    "account_id": account_id,
                    "timezone": session.get("timezone"),
                    "locale": session.get("locale"),
                    "proxy_country": _proxy_country,
                    "issues": _geo_issues,
                    "action": "Publishing continues but risk is elevated — fix fingerprint",
                },
            )

        # ── 7. Video file guard ──────────────────────────────────────────────
        if not video_path:
            raise FatalDependencyError(f"[{MISSING_VIDEO_PATH}] 'video_path' required")
        video_file = Path(video_path)
        if not video_file.exists():
            raise FatalDependencyError(
                f"[{MISSING_VIDEO_PATH}] Video not found: {video_path}"
            )

        # ── 8. Decrypt cookies ───────────────────────────────────────────────
        try:
            cookies = decrypt_cookies(session["cookies"])
        except ValueError as exc:
            raise FatalDependencyError(f"[{ACCOUNT_AUTH_REQUIRED}] Cookie decryption failed: {exc}")

        # ── 9. Caption truncation ────────────────────────────────────────────
        MAX_CAPTION = 2200
        if len(caption) > MAX_CAPTION:
            caption = caption[:MAX_CAPTION]
            LOGGER.warning("caption_truncated", extra={"account_id": account_id})

        cfg = get_platform_config("tiktok")

        session_age_hours = None
        if session.get("last_login_at"):
            try:
                from datetime import datetime, timezone
                login_dt = datetime.fromisoformat(str(session["last_login_at"]))
                if login_dt.tzinfo is None:
                    login_dt = login_dt.replace(tzinfo=timezone.utc)
                session_age_hours = round(
                    (datetime.now(timezone.utc) - login_dt).total_seconds() / 3600, 1
                )
            except Exception:
                pass

        LOGGER.info(
            "tiktok_session_info",
            extra={
                "event": "tiktok_session_info",
                "account_id": account_id,
                "fingerprint_hash": fp_hash,
                "proxy": proxy_url or "NONE",
                "viewport": f"{session.get('viewport_width', 1280)}x{session.get('viewport_height', 720)}",
                "timezone": session.get("timezone"),
                "locale": session.get("locale"),
                "session_age_hours": session_age_hours,
            },
        )

        # ── 10. Ensure browser_data_dir persisted to DB ──────────────────────
        from core.browser_context import get_browser_data_dir
        data_dir = get_browser_data_dir(account_id)
        if browser_provider == BROWSER_PROVIDER_PLAYWRIGHT and not account.get("browser_data_dir"):
            await database.set_browser_data_dir(account_id, str(data_dir))

        # ── 11. Launch persistent browser context with stealth ───────────────
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            provider = make_browser_provider({**account, "account_id": account_id}, session=session)
            async with provider.open_publisher_context(pw, headless=True) as (context, page, opened_data_dir):

                # Inject DB cookies as belt-and-suspenders (profile dir may already have them)
                try:
                    await context.add_cookies(cookies)
                except Exception as exc:
                    LOGGER.warning("cookie_inject_warning", extra={"error": str(exc)})

                nav_path: list[str] = []

                # ── Step A: Pre-navigation delay (lognormal via engine) ────────
                await _engine.action_delay()

                # ── Step B: Session warm-up — homepage first ──────────────────
                # New accounts (< 7 days old) get double warm-up passes.
                # Also forced if warmup_required by BehaviorEngine constraints.
                _warmup_passes = 2 if (_is_new_account or _engine.constraints.warmup_required) else 1
                LOGGER.info(
                    "tiktok_warmup_start",
                    extra={
                        "event": "tiktok_warmup_start",
                        "passes": _warmup_passes,
                        "new_account": _is_new_account,
                        "warmup_required_by_engine": _engine.constraints.warmup_required,
                        "activity_level": _engine.personality.activity_level,
                        "hesitation_factor": round(_engine.personality.hesitation_factor, 3),
                    },
                )

                for _warmup_pass in range(_warmup_passes):
                    _target = (
                        "https://www.tiktok.com/explore" if _warmup_pass % 2 == 1
                        else "https://www.tiktok.com/"
                    )
                    await page.goto(_target, wait_until="domcontentloaded", timeout=30000)
                    nav_path.append(page.url)
                    await _detect_session_issues(page, "tiktok", account_id, database)

                    # Behaviour-engine driven warmup: mouse → warmup delay → scroll → hover
                    await _engine.simulate_mouse_move(page)
                    await _engine.warmup_delay()
                    await _engine.simulate_scroll(page)
                    await _hover_random_point(page)
                    await _engine.action_delay()

                # Track warm-up session completion for account conditioning
                _warmup_total = await database.increment_warmup_session(account_id)
                LOGGER.info(
                    "tiktok_warmup_done",
                    extra={
                        "event": "tiktok_warmup_done",
                        "url": page.url,
                        "warmup_sessions_total": _warmup_total,
                        "passes_this_run": _warmup_passes,
                    },
                )

                # ── Step C: Upload + skip decision (dual-layer arbitration) ─────────
                #
                # Layer 1 (per-account):  BehaviorEngine.should_skip_upload()
                #   → checks risk_score, soft_ban, warmup_sessions, posts_today
                # Layer 2 (coordinator):  can_upload_now() + should_allow_skip()
                #   → enforces global upload rate cap and global skip ceiling

                # Layer 1: per-account decision
                _local_skip, _local_skip_reason = _engine.should_skip_upload(account)

                # Layer 2a: if local says proceed, check upload rate cap
                if _local_skip == "proceed":
                    _upload_ok, _upload_reason = _coordinator.can_upload_now(account_id)
                    if not _upload_ok:
                        # Downgrade to skip — too many uploads in rolling window
                        _local_skip        = "skip"
                        _local_skip_reason = f"coordinator_upload_throttle: {_upload_reason}"

                # Layer 2b: run through skip coordinator ceiling
                _skip_decision, _skip_reason = _coordinator.should_allow_skip(
                    account_id, _local_skip
                )
                # If coordinator overrode a skip → combine reasons for audit log
                if _local_skip == "skip" and _skip_decision == "proceed":
                    _skip_reason = (
                        f"coordinator_override_of_local_skip | "
                        f"local_reason={_local_skip_reason} | "
                        f"coord_reason={_skip_reason}"
                    )

                _engine.log_skip_decision(_skip_decision, _skip_reason, account_id)

                # Persist skip reason to account for audit trail
                try:
                    await database.update_account_field(
                        account_id,
                        "last_skip_reason",
                        _skip_reason if _skip_decision == "skip" else None,
                    )
                except Exception:
                    pass  # Non-fatal — skip reason persistence is best-effort

                if _skip_decision == "skip":
                    LOGGER.warning(
                        "tiktok_upload_skipped",
                        extra={
                            "event": "tiktok_upload_skipped",
                            "account_id": account_id,
                            "reason": _skip_reason,
                        },
                    )
                    # Return early — warmup was still completed (good for conditioning)
                    return {
                        "published": False,
                        "skipped": True,
                        "skip_reason": _skip_reason,
                        "platform": "tiktok",
                        "account_id": account_id,
                        "warmup_sessions_total": _warmup_total,
                        "elapsed_seconds": round(time.monotonic() - t_publish_start, 1),
                    }

                # ── Step D: Navigate to upload page ───────────────────────────
                LOGGER.info("tiktok_navigate_upload", extra={"event": "tiktok_navigate_upload"})
                await page.goto(cfg.upload_url, wait_until="domcontentloaded", timeout=30000)
                nav_path.append(page.url)
                await _detect_session_issues(page, "tiktok", account_id, database)
                await _engine.action_delay()

                LOGGER.info(
                    "tiktok_upload_page_ready",
                    extra={"event": "tiktok_upload_page_ready", "url": page.url},
                )

                # ── Step E-pre: Interact before upload (avoid instant file attach) ─
                await _engine.simulate_mouse_move(page, steps=3)
                await _engine.short_delay()

                # ── Step F: Upload video file ──────────────────────────────────
                LOGGER.info("tiktok_file_upload_start", extra={"event": "tiktok_file_upload_start"})
                file_input = page.locator("input[type='file'][accept*='video']").first
                await file_input.wait_for(state="attached", timeout=20000)
                await file_input.set_input_files(str(video_file.resolve()))
                LOGGER.info("tiktok_file_selected", extra={"event": "tiktok_file_selected"})

                # ── Step G: Wait for upload processing ────────────────────────
                await _engine.action_delay()
                try:
                    await page.wait_for_function(
                        "() => !document.querySelector('[class*=\"upload-progress\"]') || "
                        "document.querySelector('[class*=\"upload-progress\"]').style.display === 'none'",
                        timeout=120000,
                    )
                except Exception:
                    LOGGER.warning("tiktok_upload_progress_timeout", extra={"event": "tiktok_upload_progress_timeout"})

                await _engine.action_delay()

                # ── Step H: Fill caption (engine-driven typing delay) ─────────
                LOGGER.info("tiktok_fill_caption", extra={"event": "tiktok_fill_caption"})
                caption_area = page.locator(
                    "[data-e2e='caption-input'], "
                    ".caption-input, "
                    "[contenteditable='true'][class*='caption']"
                ).first
                try:
                    await caption_area.wait_for(state="visible", timeout=15000)
                    await _engine.simulate_mouse_move(page, steps=2)
                    await caption_area.click()
                    await _engine.short_delay()
                    # Type each char with per-char lognormal delay from engine.
                    # We pre-compute total typing duration and sleep once to avoid
                    # per-char async overhead, then batch-type.
                    await _engine.typing_delay(len(caption))
                    for char in caption:
                        await caption_area.type(
                            char,
                            delay=max(8, int(_engine.personality.typing_speed_base * random.uniform(0.7, 1.3))),
                        )
                except Exception as exc:
                    LOGGER.warning(
                        "tiktok_caption_fallback",
                        extra={"event": "tiktok_caption_fallback", "error": str(exc)},
                    )
                    try:
                        await caption_area.fill(caption)
                    except Exception:
                        pass  # Non-fatal

                await _engine.action_delay()

                # ── Step I: Click publish ──────────────────────────────────────
                LOGGER.info("tiktok_click_publish", extra={"event": "tiktok_click_publish"})
                await _engine.simulate_mouse_move(page, steps=3)
                publish_btn = page.locator(
                    "button[data-e2e='publish-btn'], "
                    "button[class*='publish-btn'], "
                    "button:has-text('Post'), "
                    "button:has-text('Publish')"
                ).first
                await publish_btn.wait_for(state="visible", timeout=15000)
                await _engine.short_delay()   # Never click immediately — lognormal pause first
                await publish_btn.click()

                # ── Step J: Wait for confirmation ─────────────────────────────
                await _engine.action_delay()
                try:
                    await page.wait_for_url(
                        lambda url: "tiktok.com/upload" not in url and "tiktok.com/creator" not in url,
                        timeout=30000,
                    )
                except Exception:
                    pass

                final_url = page.url
                nav_path.append(final_url)
                elapsed_total = time.monotonic() - t_publish_start

                LOGGER.info(
                    "tiktok_publish_success",
                    extra={
                        "event": "tiktok_publish_success",
                        "account_id": account_id,
                        "fingerprint_hash": fp_hash,
                        "proxy": proxy_url or "NONE",
                        "final_url": final_url,
                        "nav_path": nav_path,
                        "caption_length": len(caption),
                        "elapsed_seconds": round(elapsed_total, 1),
                        "session_age_hours": session_age_hours,
                    },
                )

        # ── 12. Record success + coordinator upload registration ──────────────
        await database.record_publish_success(account_id)
        _coordinator.register_job_end(
            account_id,
            proxy_url or "",
            uploaded=True,
            activity_level=_engine.personality.activity_level,
        )

        return {
            "published": True,
            "platform": "tiktok",
            "account_id": account_id,
            "caption_length": len(caption),
            "video_path": video_path,
            "final_url": final_url,
            "elapsed_seconds": round(time.monotonic() - t_publish_start, 1),
        }

    except (FatalDependencyError, RetryableDependencyError):
        # Record failure for risk scoring (captcha/login redirect already recorded inline)
        try:
            await database.record_publish_failure(account_id)
        except Exception:
            pass
        raise

    except Exception as exc:
        LOGGER.exception(
            "tiktok_publish_unexpected_error",
            extra={
                "event": "tiktok_publish_unexpected_error",
                "account_id": account_id,
                "error": str(exc),
                "error_type": type(exc).__name__,
                "elapsed_seconds": round(time.monotonic() - t_publish_start, 1),
            },
        )
        try:
            await database.record_publish_failure(account_id)
        except Exception:
            pass
        from workers.worker_runtime import RetryableDependencyError as _R
        raise _R(f"TikTok publish failed unexpectedly: {exc}") from exc

    finally:
        await database.close()
        # Always release coordinator slot — even on error paths.
        # Guard against the case where engine/coordinator were never assigned
        # (e.g., exception thrown before step 3a-bis).
        try:
            _coordinator.register_job_end(
                account_id,
                proxy_url or "",
                uploaded=False,   # failure path: not uploaded
                activity_level=_engine.personality.activity_level,
            )
        except Exception:
            pass  # Best-effort — coordinator state is ephemeral
