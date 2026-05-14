"""
Browser stealth patches for Playwright.

Implements JavaScript-level anti-detection without third-party stealth plugins.
All patches are applied as init scripts so they run BEFORE any page JavaScript.

Design principles:
  - Patches are additive — they make the browser look MORE like a real user agent
  - NO random fingerprint per run — noise is seeded from account_id for stability
  - Canvas/Audio noise is tiny (imperceptible visually, but defeats exact-match fingerprinting)
  - WebGL vendor is fixed to common Intel values (most common on laptops)

Phase 2 (NOT YET):
  - playwright-stealth package integration (if/when stable for Playwright 1.x)
  - Canvas WebGL full spoofing
  - Timezone via CDP
"""
from __future__ import annotations

import hashlib


# ── Stable noise seed per account ────────────────────────────────────────────

def _account_noise_seed(account_id: str) -> int:
    """Derive a stable integer seed from account_id (consistent across runs)."""
    digest = hashlib.sha256(account_id.encode()).hexdigest()
    return int(digest[:8], 16)  # First 32 bits as int


# ── Individual patch scripts ──────────────────────────────────────────────────

_PATCH_WEBDRIVER = """
// Remove navigator.webdriver property entirely (the #1 detection signal)
Object.defineProperty(navigator, 'webdriver', {
  get: () => undefined,
  configurable: true,
});
"""

_PATCH_CHROME_RUNTIME = """
// Spoof window.chrome.runtime (missing in headless = detected)
if (!window.chrome) window.chrome = {};
if (!window.chrome.runtime) {
  window.chrome.runtime = {
    onConnect:    { addListener: () => {}, removeListener: () => {} },
    onMessage:    { addListener: () => {}, removeListener: () => {} },
    connect:      () => ({ onMessage: { addListener: () => {} }, postMessage: () => {} }),
    sendMessage:  () => {},
    id: undefined,
  };
}
"""

_PATCH_PLUGINS = """
// Add realistic plugin entries (absent in headless = detected)
(function() {
  const fakeMimeType = (type, desc, ext) => ({
    type, description: desc, suffixes: ext,
    enabledPlugin: null,
  });
  const makePlugin = (name, filename, desc, ...mimeTypes) => {
    const p = { name, filename, description: desc, length: mimeTypes.length };
    mimeTypes.forEach((m, i) => { p[i] = m; });
    p.item = (i) => p[i] || null;
    p.namedItem = (n) => null;
    return p;
  };
  const plugins = [
    makePlugin(
      'Chrome PDF Plugin', 'internal-pdf-viewer',
      'Portable Document Format',
      fakeMimeType('application/x-google-chrome-pdf', 'Portable Document Format', 'pdf'),
    ),
    makePlugin(
      'Chrome PDF Viewer', 'mhjfbmdgcfjbbpaeojofohoefgiehjai',
      '',
      fakeMimeType('application/pdf', 'Portable Document Format', 'pdf'),
    ),
    makePlugin(
      'Native Client', 'internal-nacl-plugin',
      '',
      fakeMimeType('application/x-nacl', 'Native Client Executable', 'nexe'),
      fakeMimeType('application/x-pnacl', 'Portable Native Client Executable', 'pexe'),
    ),
  ];
  Object.defineProperty(navigator, 'plugins', {
    get: () => { const a = plugins; a.item = i => a[i]; a.namedItem = n => null; return a; },
    configurable: true,
  });
  Object.defineProperty(navigator, 'mimeTypes', {
    get: () => {
      const m = [plugins[0][0], plugins[1][0], plugins[2][0], plugins[2][1]];
      m.item = i => m[i]; m.namedItem = n => null; return m;
    },
    configurable: true,
  });
})();
"""

_PATCH_LANGUAGES = """
// Ensure navigator.languages looks like a real user (headless often empty)
if (!navigator.languages || navigator.languages.length === 0) {
  Object.defineProperty(navigator, 'languages', {
    get: () => ['vi-VN', 'vi', 'en-US', 'en'],
    configurable: true,
  });
}
"""

_PATCH_WEBGL = """
// Spoof WebGL vendor/renderer to common Intel laptop values
(function() {
  const getParam = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function(parameter) {
    // UNMASKED_VENDOR_WEBGL
    if (parameter === 37445) return 'Intel Inc.';
    // UNMASKED_RENDERER_WEBGL
    if (parameter === 37446) return 'Intel(R) UHD Graphics 620';
    return getParam.call(this, parameter);
  };
  // Also patch WebGL2
  if (window.WebGL2RenderingContext) {
    const getParam2 = WebGL2RenderingContext.prototype.getParameter;
    WebGL2RenderingContext.prototype.getParameter = function(parameter) {
      if (parameter === 37445) return 'Intel Inc.';
      if (parameter === 37446) return 'Intel(R) UHD Graphics 620';
      return getParam2.call(this, parameter);
    };
  }
})();
"""


def _build_canvas_noise_patch(noise_seed: int) -> str:
    """Canvas fingerprint noise: add imperceptible pixel perturbation.
    The noise amount is determined by account_id seed — stable per account but
    unique per account (different accounts have different canvas fingerprints).
    """
    # Noise magnitude: 0–2 per channel (invisible to human eye, breaks hash match)
    r_noise = (noise_seed & 0x3)           # 0–3
    g_noise = ((noise_seed >> 2) & 0x3)    # 0–3
    b_noise = ((noise_seed >> 4) & 0x3)    # 0–3
    return f"""
(function() {{
  // Canvas toDataURL fingerprint noise — stable per account, invisible to human
  const _toDataURL = HTMLCanvasElement.prototype.toDataURL;
  HTMLCanvasElement.prototype.toDataURL = function(type, quality) {{
    const ctx = this.getContext('2d');
    if (ctx && this.width > 0 && this.height > 0) {{
      try {{
        const imgData = ctx.getImageData(0, 0, 1, 1);
        // Add stable sub-1 noise to avoid detection of exact 0-addition
        imgData.data[0] = Math.min(255, imgData.data[0] ^ {r_noise});
        imgData.data[1] = Math.min(255, imgData.data[1] ^ {g_noise});
        imgData.data[2] = Math.min(255, imgData.data[2] ^ {b_noise});
        ctx.putImageData(imgData, 0, 0);
      }} catch(e) {{}}
    }}
    return _toDataURL.apply(this, arguments);
  }};
  // Also patch toBlob
  const _toBlob = HTMLCanvasElement.prototype.toBlob;
  HTMLCanvasElement.prototype.toBlob = function(callback, type, quality) {{
    return _toBlob.apply(this, arguments);
  }};
}})();
"""


def _build_audio_noise_patch(noise_seed: int) -> str:
    """AudioContext fingerprint noise: adds tiny stable noise to audio processing.
    Magnitude is sub-perceptible (< 1e-4) but defeats exact-hash fingerprinting.
    """
    # Stable noise value between 0.00001 and 0.0001
    magnitude = 0.00001 + ((noise_seed & 0xFF) / 0xFF) * 0.00009
    return f"""
(function() {{
  // AudioContext getChannelData fingerprint noise
  const _getChannelData = AudioBuffer.prototype.getChannelData;
  AudioBuffer.prototype.getChannelData = function(channel) {{
    const results = _getChannelData.call(this, channel);
    for (let i = 0; i < results.length; i += 97) {{
      results[i] += {magnitude:.6f} * (Math.random() > 0.5 ? 1 : -1);
    }}
    return results;
  }};
  // copyFromChannel
  const _copyFromChannel = AudioBuffer.prototype.copyFromChannel;
  if (_copyFromChannel) {{
    AudioBuffer.prototype.copyFromChannel = function(dest, channelNumber, startInChannel) {{
      _copyFromChannel.call(this, dest, channelNumber, startInChannel);
      for (let i = 0; i < dest.length; i += 97) {{
        dest[i] += {magnitude:.6f} * (Math.random() > 0.5 ? 1 : -1);
      }}
    }};
  }}
}})();
"""


_PATCH_PERMISSIONS = """
// Override notification permission query (headless returns 'denied' which is suspicious)
(function() {
  const origQuery = window.navigator.permissions && window.navigator.permissions.query;
  if (origQuery) {
    window.navigator.permissions.query = (parameters) => {
      if (parameters.name === 'notifications') {
        return Promise.resolve({ state: Notification.permission || 'default' });
      }
      return origQuery.call(window.navigator.permissions, parameters);
    };
  }
})();
"""

_PATCH_IFRAME_WEBDRIVER = """
// Also patch webdriver in iframes (some detection scripts check inside iframes)
const _origCreateElement = document.createElement.bind(document);
document.createElement = function(tag, ...args) {
  const el = _origCreateElement(tag, ...args);
  if (tag.toLowerCase() === 'iframe') {
    el.addEventListener('load', () => {
      try {
        Object.defineProperty(el.contentWindow.navigator, 'webdriver', {
          get: () => undefined, configurable: true,
        });
      } catch(e) {}
    });
  }
  return el;
};
"""

_PATCH_HARDWARE_CONCURRENCY = """
// Ensure hardware concurrency looks reasonable (1 is suspicious)
if (navigator.hardwareConcurrency < 2) {
  Object.defineProperty(navigator, 'hardwareConcurrency', {
    get: () => 4,
    configurable: true,
  });
}
"""


# ── Public API ────────────────────────────────────────────────────────────────

def get_stealth_scripts(account_id: str) -> list[str]:
    """Return list of JavaScript strings to apply as Playwright init scripts.

    Scripts are account-specific for canvas/audio noise (stable seed from account_id)
    but generic for all other patches.

    Usage:
        for script in get_stealth_scripts(account_id):
            await context.add_init_script(script)
    """
    seed = _account_noise_seed(account_id)
    return [
        _PATCH_WEBDRIVER,
        _PATCH_CHROME_RUNTIME,
        _PATCH_PLUGINS,
        _PATCH_LANGUAGES,
        _PATCH_WEBGL,
        _build_canvas_noise_patch(seed),
        _build_audio_noise_patch(seed),
        _PATCH_PERMISSIONS,
        _PATCH_IFRAME_WEBDRIVER,
        _PATCH_HARDWARE_CONCURRENCY,
    ]


def fingerprint_hash(account_id: str) -> str:
    """Return a short hex hash representing this account's fingerprint identity.
    Useful for logging to correlate sessions without exposing account details.
    """
    return hashlib.sha256(f"fp:{account_id}".encode()).hexdigest()[:16]
