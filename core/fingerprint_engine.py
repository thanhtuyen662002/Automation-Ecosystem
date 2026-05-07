"""
Runtime Fingerprint Enforcement Layer.

Replaces the generic stealth patches with IdentityProfile-driven overrides.
Every JS value injected into the browser is derived from the same IdentityProfile
that was generated once and stored in IdentityRegistry — guaranteeing that
backend identity == browser runtime signals.

Layers:
  1. Navigator overrides  (platform, hardwareConcurrency, deviceMemory, language)
  2. WebGL spoofing       (vendor/renderer matched to OS/device profile)
  3. Canvas stabilization (deterministic noise from canvas_noise_seed)
  4. Audio stabilization  (deterministic noise from webgl_noise_seed as audio seed)
  5. Font consistency     (block non-OS fonts)
  6. Runtime validation   (page.evaluate checks that everything matches)

Usage:
    from core.fingerprint_engine import (
        get_identity_scripts,
        validate_runtime,
        RuntimeValidationIssue,
    )

    # In browser_context.py — replaces get_stealth_scripts():
    for script in get_identity_scripts(profile):
        await context.add_init_script(script)

    # After page loads — for anomaly detection:
    issues = await validate_runtime(page, profile)
    for issue in issues:
        if issue.severity == "CRITICAL":
            # feed into AccountBrain via SessionSignals.fingerprint_changed = True
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Page
    from core.identity_manager import IdentityProfile

LOGGER = logging.getLogger("core.fingerprint_engine")

# ── WebGL profiles keyed by OS ──────────────────────────────────────────────

_WEBGL_PROFILES: dict[str, dict[str, str]] = {
    "iOS":      {"vendor": "Apple Inc.",      "renderer": "Apple GPU"},
    "macOS":    {"vendor": "Apple Inc.",      "renderer": "Apple M2"},
    "Android":  {"vendor": "Qualcomm",        "renderer": "Adreno (TM) 640"},
    "Windows":  {"vendor": "Intel Inc.",      "renderer": "Intel(R) UHD Graphics 630"},
    "Linux":    {"vendor": "Mesa/X.org",      "renderer": "Mesa Intel(R) HD Graphics 620 (KBL GT2)"},
}

# Platform string per OS (navigator.platform)
_PLATFORM_MAP: dict[str, str] = {
    "iOS":     "iPhone",
    "macOS":   "MacIntel",
    "Android": "Linux armv8l",
    "Windows": "Win32",
    "Linux":   "Linux x86_64",
}

# hardware_concurrency per device class
_HW_CONCURRENCY: dict[str, int] = {
    "mobile":  4,
    "desktop": 8,
}

# device_memory (GB) per device class
_DEVICE_MEMORY: dict[str, int] = {
    "mobile":  4,
    "desktop": 8,
}

# Common fonts per OS (for font consistency patch)
_OS_FONTS: dict[str, list[str]] = {
    "iOS":     ["SF Pro", "Helvetica Neue", "Arial", "Georgia", "Times New Roman"],
    "macOS":   ["Helvetica Neue", "SF Pro", "Arial", "Georgia", "Courier New"],
    "Android": ["Roboto", "Noto Sans", "Droid Sans", "Arial", "Georgia"],
    "Windows": ["Segoe UI", "Arial", "Calibri", "Times New Roman", "Courier New"],
    "Linux":   ["Ubuntu", "DejaVu Sans", "Liberation Sans", "Arial", "Noto Sans"],
}


def _os_family(os_str: str) -> str:
    """Extract OS family from version string like 'iOS 17.4' → 'iOS'."""
    for key in ("iOS", "macOS", "Android", "Windows", "Linux"):
        if key.lower() in os_str.lower():
            return key
    return "Windows"  # safe default


# ── Layer 1: Navigator overrides ─────────────────────────────────────────────

def _build_navigator_patch(profile: "IdentityProfile") -> str:
    os_family = _os_family(profile.os)
    platform   = _PLATFORM_MAP.get(os_family, "Win32")
    hw_conc    = _HW_CONCURRENCY.get(profile.device_type, 4)
    dev_mem    = _DEVICE_MEMORY.get(profile.device_type, 4)
    lang       = profile.locale
    lang_short = lang.split("-")[0]   # "vi-VN" → "vi"

    return f"""
(function() {{
  // ── Navigator: platform ──────────────────────────────────────────────────
  Object.defineProperty(navigator, 'platform', {{
    get: () => '{platform}',
    configurable: true,
  }});

  // ── Navigator: hardwareConcurrency ───────────────────────────────────────
  Object.defineProperty(navigator, 'hardwareConcurrency', {{
    get: () => {hw_conc},
    configurable: true,
  }});

  // ── Navigator: deviceMemory ──────────────────────────────────────────────
  Object.defineProperty(navigator, 'deviceMemory', {{
    get: () => {dev_mem},
    configurable: true,
  }});

  // ── Navigator: language / languages ─────────────────────────────────────
  Object.defineProperty(navigator, 'language', {{
    get: () => '{lang}',
    configurable: true,
  }});
  Object.defineProperty(navigator, 'languages', {{
    get: () => ['{lang}', '{lang_short}'],
    configurable: true,
  }});

  // ── Navigator: webdriver (remove) ───────────────────────────────────────
  Object.defineProperty(navigator, 'webdriver', {{
    get: () => undefined,
    configurable: true,
  }});
}})();
"""


# ── Layer 2: WebGL spoofing ──────────────────────────────────────────────────

def _build_webgl_patch(profile: "IdentityProfile") -> str:
    os_family = _os_family(profile.os)
    wgl = _WEBGL_PROFILES.get(os_family, _WEBGL_PROFILES["Windows"])
    vendor   = wgl["vendor"]
    renderer = wgl["renderer"]

    return f"""
(function() {{
  const VENDOR   = '{vendor}';
  const RENDERER = '{renderer}';
  const UNMASKED_VENDOR_WEBGL   = 37445;
  const UNMASKED_RENDERER_WEBGL = 37446;

  function patchContext(proto) {{
    const orig = proto.getParameter;
    proto.getParameter = function(p) {{
      if (p === UNMASKED_VENDOR_WEBGL)   return VENDOR;
      if (p === UNMASKED_RENDERER_WEBGL) return RENDERER;
      return orig.call(this, p);
    }};
  }}

  if (window.WebGLRenderingContext)  patchContext(WebGLRenderingContext.prototype);
  if (window.WebGL2RenderingContext) patchContext(WebGL2RenderingContext.prototype);
}})();
"""


# ── Layer 3: Canvas fingerprint stabilization ────────────────────────────────

def _build_canvas_patch(canvas_seed: int) -> str:
    r = (canvas_seed & 0x3)
    g = ((canvas_seed >> 2) & 0x3)
    b = ((canvas_seed >> 4) & 0x3)
    return f"""
(function() {{
  // Stable per-account canvas noise (seed={canvas_seed})
  const _toDataURL = HTMLCanvasElement.prototype.toDataURL;
  HTMLCanvasElement.prototype.toDataURL = function(type, quality) {{
    const ctx = this.getContext('2d');
    if (ctx && this.width > 0 && this.height > 0) {{
      try {{
        const d = ctx.getImageData(0, 0, 1, 1);
        d.data[0] = Math.min(255, d.data[0] ^ {r});
        d.data[1] = Math.min(255, d.data[1] ^ {g});
        d.data[2] = Math.min(255, d.data[2] ^ {b});
        ctx.putImageData(d, 0, 0);
      }} catch(e) {{}}
    }}
    return _toDataURL.apply(this, arguments);
  }};

  const _getContext = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function(type, attrs) {{
    return _getContext.call(this, type, attrs);
  }};
}})();
"""


# ── Layer 4: Audio fingerprint stabilization ─────────────────────────────────

def _build_audio_patch(audio_seed: int) -> str:
    magnitude = 0.00001 + ((audio_seed & 0xFF) / 0xFF) * 0.00009
    return f"""
(function() {{
  // Stable per-account AudioContext noise (magnitude={magnitude:.8f})
  const _getChannelData = AudioBuffer.prototype.getChannelData;
  AudioBuffer.prototype.getChannelData = function(ch) {{
    const buf = _getChannelData.call(this, ch);
    for (let i = 0; i < buf.length; i += 97) {{
      buf[i] += {magnitude:.8f} * ((i % 2 === 0) ? 1 : -1);
    }}
    return buf;
  }};
}})();
"""


# ── Layer 5: Font consistency ────────────────────────────────────────────────

def _build_font_patch(profile: "IdentityProfile") -> str:
    os_family  = _os_family(profile.os)
    safe_fonts = _OS_FONTS.get(os_family, _OS_FONTS["Windows"])
    fonts_json = str(safe_fonts).replace("'", '"')
    return f"""
(function() {{
  // Font consistency: report only OS-appropriate fonts
  const OS_FONTS = {fonts_json};
  const origCheck = document.fonts && document.fonts.check;
  if (origCheck) {{
    document.fonts.check = function(font, text) {{
      const family = (font || '').replace(/^[\\d.]+px\\s+/, '').replace(/['"]/g, '');
      if (OS_FONTS.some(f => family.toLowerCase().includes(f.toLowerCase()))) {{
        return true;
      }}
      return origCheck.call(document.fonts, font, text);
    }};
  }}
}})();
"""


# ── Layer 6: Screen resolution enforcement ───────────────────────────────────

def _build_screen_patch(profile: "IdentityProfile") -> str:
    try:
        w, h = profile.screen_resolution.split("x")
        width, height = int(w), int(h)
    except (ValueError, AttributeError):
        width, height = 1280, 720

    return f"""
(function() {{
  // Screen resolution enforcement
  Object.defineProperty(screen, 'width',       {{ get: () => {width},  configurable: true }});
  Object.defineProperty(screen, 'height',      {{ get: () => {height}, configurable: true }});
  Object.defineProperty(screen, 'availWidth',  {{ get: () => {width},  configurable: true }});
  Object.defineProperty(screen, 'availHeight', {{ get: () => {height - 40}, configurable: true }});
  Object.defineProperty(screen, 'colorDepth',  {{ get: () => 24, configurable: true }});
  Object.defineProperty(screen, 'pixelDepth',  {{ get: () => 24, configurable: true }});
  // window.devicePixelRatio — mobile=2, desktop=1
  Object.defineProperty(window, 'devicePixelRatio', {{
    get: () => {'2' if profile.device_type == 'mobile' else '1'},
    configurable: true,
  }});
}})();
"""


# ── Layer 7: Chrome runtime stub ────────────────────────────────────────────

_PATCH_CHROME_RUNTIME = """
(function() {
  if (!window.chrome) window.chrome = {};
  if (!window.chrome.runtime) {
    window.chrome.runtime = {
      onConnect:   { addListener: () => {}, removeListener: () => {} },
      onMessage:   { addListener: () => {}, removeListener: () => {} },
      connect:     () => ({ onMessage: { addListener: () => {} }, postMessage: () => {} }),
      sendMessage: () => {},
      id: undefined,
    };
  }
})();
"""

_PATCH_PERMISSIONS = """
(function() {
  const orig = navigator.permissions && navigator.permissions.query;
  if (orig) {
    navigator.permissions.query = (p) => {
      if (p.name === 'notifications') {
        return Promise.resolve({ state: Notification.permission || 'default' });
      }
      return orig.call(navigator.permissions, p);
    };
  }
})();
"""

_PATCH_PLUGINS = """
(function() {
  const fMime = (t, d, e) => ({ type: t, description: d, suffixes: e, enabledPlugin: null });
  const mkP = (name, file, desc, ...mimes) => {
    const p = { name, filename: file, description: desc, length: mimes.length };
    mimes.forEach((m, i) => { p[i] = m; });
    p.item = i => p[i] || null; p.namedItem = () => null; return p;
  };
  const plugins = [
    mkP('Chrome PDF Plugin','internal-pdf-viewer','Portable Document Format',fMime('application/x-google-chrome-pdf','PDF','pdf')),
    mkP('Chrome PDF Viewer','mhjfbmdgcfjbbpaeojofohoefgiehjai','',fMime('application/pdf','PDF','pdf')),
    mkP('Native Client','internal-nacl-plugin','',fMime('application/x-nacl','NaCl','nexe')),
  ];
  Object.defineProperty(navigator, 'plugins', {
    get: () => { const a = [...plugins]; a.item = i => a[i]; a.namedItem = () => null; return a; },
    configurable: true,
  });
})();
"""


# ── Public API ───────────────────────────────────────────────────────────────

def get_identity_scripts(profile: "IdentityProfile") -> list[str]:
    """Return ordered list of init scripts that enforce the IdentityProfile in-browser.

    All scripts are deterministic — same profile → same JS overrides every run.
    Apply via: for s in get_identity_scripts(profile): await ctx.add_init_script(s)

    Order matters:
      1. Navigator (platform, concurrency, memory, language)
      2. WebGL (vendor/renderer per OS)
      3. Canvas noise (stable per canvas_noise_seed)
      4. Audio noise (stable per webgl_noise_seed used as audio seed)
      5. Font list (per OS)
      6. Screen resolution
      7. Chrome runtime stub
      8. Permissions stub
      9. Plugin list
    """
    return [
        _build_navigator_patch(profile),
        _build_webgl_patch(profile),
        _build_canvas_patch(profile.canvas_noise_seed),
        _build_audio_patch(profile.webgl_noise_seed),   # reuse seed for audio
        _build_font_patch(profile),
        _build_screen_patch(profile),
        _PATCH_CHROME_RUNTIME,
        _PATCH_PERMISSIONS,
        _PATCH_PLUGINS,
    ]


# ── Runtime validation ───────────────────────────────────────────────────────

@dataclass
class RuntimeValidationIssue:
    code: str
    severity: str   # "WARNING" | "CRITICAL"
    expected: str
    actual: str
    message: str


async def validate_runtime(
    page: "Page",
    profile: "IdentityProfile",
) -> list[RuntimeValidationIssue]:
    """Evaluate the live page and confirm all values match the IdentityProfile.

    Call this after page.goto() but before any interaction.
    CRITICAL issues → set SessionSignals.fingerprint_changed = True in AccountBrain.

    Returns:
        List of RuntimeValidationIssue (empty = clean).
    """
    issues: list[RuntimeValidationIssue] = []

    try:
        runtime: dict[str, Any] = await page.evaluate("""() => ({
            platform:            navigator.platform,
            hardwareConcurrency: navigator.hardwareConcurrency,
            deviceMemory:        navigator.deviceMemory,
            language:            navigator.language,
            languages:           Array.from(navigator.languages || []),
            webdriver:           navigator.webdriver,
            screenWidth:         screen.width,
            screenHeight:        screen.height,
            timezone:            Intl.DateTimeFormat().resolvedOptions().timeZone,
            webglVendor: (() => {
                try {
                    const c = document.createElement('canvas');
                    const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
                    return gl ? gl.getParameter(37445) : null;
                } catch(e) { return null; }
            })(),
            webglRenderer: (() => {
                try {
                    const c = document.createElement('canvas');
                    const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
                    return gl ? gl.getParameter(37446) : null;
                } catch(e) { return null; }
            })(),
        })""")
    except Exception as exc:
        LOGGER.warning("validate_runtime_eval_failed", extra={"error": str(exc)})
        return [RuntimeValidationIssue(
            code="EVAL_FAILED", severity="WARNING",
            expected="page.evaluate to succeed", actual=str(exc),
            message="Could not evaluate runtime values — page may not be ready",
        )]

    os_family  = _os_family(profile.os)
    exp_plat   = _PLATFORM_MAP.get(os_family, "Win32")
    exp_hw     = _HW_CONCURRENCY.get(profile.device_type, 4)
    exp_mem    = _DEVICE_MEMORY.get(profile.device_type, 4)
    exp_wgl    = _WEBGL_PROFILES.get(os_family, _WEBGL_PROFILES["Windows"])

    checks = [
        ("platform",      exp_plat,              runtime.get("platform"),            "CRITICAL"),
        ("hardwareConcurrency", str(exp_hw),      str(runtime.get("hardwareConcurrency")), "WARNING"),
        ("deviceMemory",  str(exp_mem),           str(runtime.get("deviceMemory")),   "WARNING"),
        ("language",      profile.locale,         runtime.get("language"),            "CRITICAL"),
        ("timezone",      profile.timezone,       runtime.get("timezone"),            "CRITICAL"),
        ("webglVendor",   exp_wgl["vendor"],      runtime.get("webglVendor"),         "CRITICAL"),
        ("webglRenderer", exp_wgl["renderer"],    runtime.get("webglRenderer"),       "WARNING"),
        ("webdriver",     "undefined",            str(runtime.get("webdriver")),      "CRITICAL"),
    ]

    for field, expected, actual, severity in checks:
        if field == "webdriver":
            if actual not in ("None", "undefined", "False", "false"):
                issues.append(RuntimeValidationIssue(
                    code="WEBDRIVER_EXPOSED", severity="CRITICAL",
                    expected="undefined", actual=str(actual),
                    message="navigator.webdriver is exposed — automation detected",
                ))
        elif expected and actual and str(expected).lower() != str(actual).lower():
            issues.append(RuntimeValidationIssue(
                code=f"{field.upper()}_MISMATCH", severity=severity,
                expected=str(expected), actual=str(actual),
                message=f"Runtime {field}={actual!r} does not match profile {expected!r}",
            ))

    # Screen resolution
    try:
        w, h = profile.screen_resolution.split("x")
        exp_w, exp_h = int(w), int(h)
        if runtime.get("screenWidth") != exp_w or runtime.get("screenHeight") != exp_h:
            issues.append(RuntimeValidationIssue(
                code="SCREEN_MISMATCH", severity="WARNING",
                expected=profile.screen_resolution,
                actual=f"{runtime.get('screenWidth')}x{runtime.get('screenHeight')}",
                message="Screen resolution does not match profile",
            ))
    except (ValueError, AttributeError):
        pass

    for issue in issues:
        log_fn = LOGGER.critical if issue.severity == "CRITICAL" else LOGGER.warning
        log_fn("runtime_validation_issue", extra={
            "event": "runtime_validation_issue",
            "code": issue.code,
            "severity": issue.severity,
            "expected": issue.expected,
            "actual": issue.actual,
        })

    if not issues:
        LOGGER.info("runtime_validation_clean", extra={"event": "runtime_validation_clean",
            "timezone": runtime.get("timezone"), "platform": runtime.get("platform")})

    return issues


def runtime_issues_to_session_signals(
    issues: list[RuntimeValidationIssue],
) -> dict[str, Any]:
    """Convert validation issues to SessionSignals-compatible dict.

    Feed this into AccountBrain.update_strategy() after each session.
    """
    codes = {i.code for i in issues}
    has_critical = any(i.severity == "CRITICAL" for i in issues)
    return {
        "fingerprint_changed": "WEBDRIVER_EXPOSED" in codes or "WEBGLFROM_MISMATCH" in codes,
        "device_mismatch":     "PLATFORM_MISMATCH" in codes or "SCREEN_MISMATCH" in codes,
        "geo_mismatch":        "TIMEZONE_MISMATCH" in codes or "LANGUAGE_MISMATCH" in codes,
        "identity_risk_score": min(1.0, len(issues) * 0.15),
    }
