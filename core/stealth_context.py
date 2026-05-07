"""
Stealth Context Builder — standalone non-persistent context factory.

Complements browser_context.py (which manages persistent profiles).
Use this module when you need a clean, ephemeral context (e.g., for
fingerprint testing, identity validation, or one-shot tasks).

Key additions over browser_context.py:
  - Human signal patches (mouse jitter, focus/blur, visibility)
  - Idle-detection spoofing
  - WebDriver artifact removal at CDP level
  - Standalone context (no persistent data dir)

Usage:
    async with create_stealth_context(pw, profile) as (ctx, page):
        issues = await validate_fingerprint(page, profile)
        risk   = compute_risk_score(issues)
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, TYPE_CHECKING

if TYPE_CHECKING:
    from core.identity_manager import IdentityProfile

from core.fingerprint_engine import get_identity_scripts

LOGGER = logging.getLogger("core.stealth_context")


# ── Human signal patches ─────────────────────────────────────────────────────
# These simulate behavioral signals that bots typically lack:
# mouse activity, focus events, page visibility, idle detection.
# Timing uses seeded LCG so intervals are deterministic per account.

def _build_human_signals_patch(canvas_seed: int) -> str:
    """Build human behavioral signal patches seeded from canvas_noise_seed.

    Uses a simple LCG (linear congruential generator) for all pseudo-random
    values so behaviour is reproducible per account without Math.random().
    """
    # LCG seed for JS runtime use
    lcg_seed = canvas_seed & 0xFFFFFFFF
    return f"""
(function() {{
  // ── Seeded LCG (no Math.random calls) ─────────────────────────────────
  let _seed = {lcg_seed};
  function _lcg() {{
    _seed = ((_seed * 1664525 + 1013904223) & 0xFFFFFFFF) >>> 0;
    return _seed / 0xFFFFFFFF;
  }}

  // ── Mouse presence simulation ─────────────────────────────────────────
  // Dispatch subtle mousemove events so the page sees mouse activity.
  // Position drifts slowly — realistic idle browsing behaviour.
  (function() {{
    let mx = 200 + (_lcg() * 600) | 0;
    let my = 200 + (_lcg() * 300) | 0;
    const BASE_MS = 3000;

    function jitter() {{
      mx = Math.max(10, Math.min(window.innerWidth  - 10, mx + ((_lcg() - 0.5) * 12) | 0));
      my = Math.max(10, Math.min(window.innerHeight - 10, my + ((_lcg() - 0.5) * 8)  | 0));
      try {{
        document.dispatchEvent(new MouseEvent('mousemove', {{
          clientX: mx, clientY: my, bubbles: true, cancelable: false,
        }}));
      }} catch(e) {{}}
      setTimeout(jitter, BASE_MS + (_lcg() * 4000) | 0);
    }}

    setTimeout(jitter, 1500 + (_lcg() * 2000) | 0);
  }})();

  // ── Focus / blur cycling ──────────────────────────────────────────────
  // Real browsers fire focus/blur when users switch tabs.
  // Inject these events periodically to keep the page's internal
  // activity counters in a "normal" state.
  (function() {{
    function cycleFocus() {{
      try {{
        window.dispatchEvent(new Event('focus'));
        setTimeout(() => window.dispatchEvent(new Event('blur')),
          200 + (_lcg() * 300) | 0);
      }} catch(e) {{}}
      setTimeout(cycleFocus, 25000 + (_lcg() * 35000) | 0);
    }}
    setTimeout(cycleFocus, 8000 + (_lcg() * 12000) | 0);
  }})();

  // ── Page Visibility API spoofing ──────────────────────────────────────
  // Always report page as "visible" — hidden pages get throttled/detected.
  try {{
    Object.defineProperty(document, 'visibilityState', {{
      get: () => 'visible', configurable: true,
    }});
    Object.defineProperty(document, 'hidden', {{
      get: () => false, configurable: true,
    }});
    // Prevent visibilitychange events from being observed as hidden
    const _addEL = document.addEventListener.bind(document);
    document.addEventListener = function(type, fn, opts) {{
      if (type === 'visibilitychange') return;
      return _addEL(type, fn, opts);
    }};
  }} catch(e) {{}}

  // ── Idle detection (Screen Wake Lock / IdleDetector) ─────────────────
  // Stub out IdleDetector API — detecting idle state reveals automation.
  if (!window.IdleDetector) {{
    window.IdleDetector = class IdleDetector {{
      constructor() {{}}
      get userState() {{ return 'active'; }}
      get screenState() {{ return 'unlocked'; }}
      async start() {{}}
      addEventListener() {{}}
      removeEventListener() {{}}
    }};
  }}

  // ── Touch support spoofing (mobile profiles) ──────────────────────────
  // If no touch support present, add minimal touch API.
  if (!('ontouchstart' in window) && !window.TouchEvent) {{
    try {{
      Object.defineProperty(window, 'ontouchstart', {{
        get: () => null, configurable: true,
      }});
      Object.defineProperty(navigator, 'maxTouchPoints', {{
        get: () => 5, configurable: true,
      }});
    }} catch(e) {{}}
  }}

  // ── Scroll behaviour ──────────────────────────────────────────────────
  // Dispatch synthetic scroll events to simulate reading behaviour.
  (function() {{
    let scrollY = 0;
    function scrollStep() {{
      scrollY += ((_lcg() * 80) | 0) - 10;   // -10 to +70 px
      scrollY = Math.max(0, scrollY);
      try {{
        window.dispatchEvent(new CustomEvent('scroll-hint', {{
          detail: {{ y: scrollY }},
        }}));
      }} catch(e) {{}}
      setTimeout(scrollStep, 5000 + (_lcg() * 10000) | 0);
    }}
    setTimeout(scrollStep, 3000 + (_lcg() * 5000) | 0);
  }})();
}})();
"""


def _build_automation_artifact_removal() -> str:
    """Remove all automation artifacts that leak headless/bot status."""
    return """
(function() {
  // ── Remove webdriver traces ───────────────────────────────────────────
  ['webdriver', 'driver', 'selenium', '__driver_evaluate',
   '__webdriver_evaluate', '__selenium_evaluate', '__fxdriver_evaluate',
   '__driver_unwrapped', '__webdriver_unwrapped', '__selenium_unwrapped',
   '__fxdriver_unwrapped', '_Selenium_IDE_Recorder',
   'calledSelenium', '_selenium', 'callPhantom', '_phantom',
   '__nightmare', 'domAutomation', 'domAutomationController',
  ].forEach(key => {
    try {
      if (key in window) delete window[key];
      Object.defineProperty(navigator, key, { get: () => undefined, configurable: true });
    } catch(e) {}
  });

  // ── CDP / Runtime detection ──────────────────────────────────────────
  Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined, configurable: true,
  });

  // ── Camo: disguise Error stack traces ────────────────────────────────
  const _err = window.Error;
  window.Error = function(...args) {
    const e = new _err(...args);
    if (e.stack) {
      e.stack = e.stack.replace(/playwright|puppeteer|selenium/gi, 'Chrome');
    }
    return e;
  };
  window.Error.prototype = _err.prototype;
})();
"""


# ── Stealth args ─────────────────────────────────────────────────────────────

_STEALTH_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-infobars",
    "--disable-extensions",
    "--disable-breakpad",
    "--disable-component-update",
    "--disable-background-networking",
    "--metrics-recording-only",
    "--no-report-upload",
    # Reduce fingerprint surface area
    "--disable-features=TranslateUI",
    "--disable-ipc-flooding-protection",
]


# ── Context factory ──────────────────────────────────────────────────────────

@asynccontextmanager
async def create_stealth_context(
    browser: Any,
    profile: "IdentityProfile",
) -> AsyncGenerator[tuple[Any, Any], None]:
    """Create an ephemeral (non-persistent) stealth browser context.

    Applies all identity scripts + human signal patches + artifact removal
    before any page JavaScript runs.

    Args:
        browser: Playwright Browser object (already launched)
        profile: IdentityProfile from IdentityRegistry

    Yields:
        (context, page)

    Example:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(args=_STEALTH_LAUNCH_ARGS)
            async with create_stealth_context(browser, profile) as (ctx, page):
                await page.goto("https://example.com")
                issues = await validate_fingerprint(page, profile)
    """
    try:
        w, h = profile.screen_resolution.split("x")
        vp_w, vp_h = int(w), int(h)
    except (ValueError, AttributeError):
        vp_w, vp_h = 1280, 720

    ctx_kwargs: dict[str, Any] = {
        "viewport":    {"width": vp_w, "height": vp_h},
        "user_agent":  profile.user_agent,
        "timezone_id": profile.timezone,
        "locale":      profile.locale,
    }
    if profile.proxy_url:
        ctx_kwargs["proxy"] = {"server": profile.proxy_url}

    context = await browser.new_context(**ctx_kwargs)

    # 1. Identity scripts (navigator, WebGL, canvas, audio, screen, fonts)
    for script in get_identity_scripts(profile):
        await context.add_init_script(script)

    # 2. Human signal patches (mouse, focus, visibility, idle)
    await context.add_init_script(_build_human_signals_patch(profile.canvas_noise_seed))

    # 3. Automation artifact removal
    await context.add_init_script(_build_automation_artifact_removal())

    LOGGER.info("stealth_context_created", extra={
        "event":       "stealth_context_created",
        "account_id":  profile.account_id,
        "device_type": profile.device_type,
        "os":          profile.os,
        "fingerprint": profile.fingerprint_hash[:12],
        "proxy":       bool(profile.proxy_url),
    })

    page = await context.new_page()

    try:
        yield context, page
    finally:
        try:
            await context.close()
        except Exception:
            pass


async def launch_stealth_browser(pw: Any) -> Any:
    """Launch a Chromium browser with stealth launch args pre-applied.

    Call once; pass the returned browser to create_stealth_context().
    """
    return await pw.chromium.launch(
        headless=True,
        args=_STEALTH_LAUNCH_ARGS,
    )
