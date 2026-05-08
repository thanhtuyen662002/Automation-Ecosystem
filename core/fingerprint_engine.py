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


# ── Python-side PRNG helpers (deterministic from seed) ───────────────────────

def _mulberry32_float(seed: int) -> float:
    """Single mulberry32 step → float in [0, 1)."""
    s = (seed + 0x6D2B79F5) & 0xFFFFFFFF
    s = (((s ^ (s >> 15)) * (s | 1)) & 0xFFFFFFFF)
    s = ((s ^ (s + (((s ^ (s >> 7)) * (s | 61)) & 0xFFFFFFFF))) & 0xFFFFFFFF)
    return ((s ^ (s >> 14)) & 0xFFFFFFFF) / 0xFFFFFFFF


def _seed_int(base_seed: int, slot: int, lo: int, hi: int) -> int:
    h = hashlib.sha256(f"{base_seed}:{slot}".encode()).hexdigest()
    unit = int(h[:8], 16) / 0xFFFFFFFF
    return lo + int(unit * (hi - lo + 1))


def _seed_pick(base_seed: int, slot: int, pool: list) -> Any:
    h = hashlib.sha256(f"{base_seed}:{slot}".encode()).hexdigest()
    return pool[int(h[:8], 16) % len(pool)]


def _seed_pick_weighted(base_seed: int, slot: int, pool: list, weights: list) -> Any:
    h = hashlib.sha256(f"{base_seed}:{slot}".encode()).hexdigest()
    r = int(h[:8], 16) / 0xFFFFFFFF
    total = sum(weights)
    cumulative = 0.0
    for item, w in zip(pool, weights):
        cumulative += w / total
        if r <= cumulative:
            return item
    return pool[-1]


# ── Weighted GPU pools (vendor, renderer) per OS ──────────────────────────────

_WEBGL_POOL: dict[str, list] = {
    "Windows": [
        ("Intel Inc.",         "Intel(R) UHD Graphics 620"),
        ("Intel Inc.",         "Intel(R) UHD Graphics 630"),
        ("Intel Inc.",         "Intel(R) UHD Graphics 770"),
        ("Intel Inc.",         "Intel(R) Iris(R) Xe Graphics"),
        ("AMD",                "AMD Radeon(TM) RX 6600 XT"),
        ("AMD",                "AMD Radeon(TM) RX 580 Series"),
        ("NVIDIA Corporation", "NVIDIA GeForce GTX 1650/PCIe/SSE2"),
        ("NVIDIA Corporation", "NVIDIA GeForce RTX 3060/PCIe/SSE2"),
    ],
    "Windows_w": [0.20, 0.20, 0.10, 0.10, 0.12, 0.08, 0.10, 0.10],
    "Android": [
        ("Qualcomm", "Adreno (TM) 610"),
        ("Qualcomm", "Adreno (TM) 618"),
        ("Qualcomm", "Adreno (TM) 640"),
        ("Qualcomm", "Adreno (TM) 730"),
        ("ARM",      "Mali-G52 MC2"),
        ("ARM",      "Mali-G76 MC4"),
        ("ARM",      "Mali-G78 MP14"),
    ],
    "Android_w": [0.15, 0.20, 0.20, 0.15, 0.10, 0.10, 0.10],
    "iOS":    [("Apple Inc.", "Apple GPU")],
    "iOS_w":  [1.0],
    "macOS":  [("Apple Inc.", "Apple GPU")],
    "macOS_w": [1.0],
    "Linux": [
        ("Mesa/X.org", "Mesa Intel(R) HD Graphics 620 (KBL GT2)"),
        ("Mesa/X.org", "Mesa Intel(R) UHD Graphics (CML GT2)"),
        ("AMD",        "AMD RENOIR (LLVM 15.0.7, DRM 3.49, 6.2.0-39-generic)"),
    ],
    "Linux_w": [0.45, 0.35, 0.20],
}


# ── Hardware bundles (GPU + CPU + RAM must be coherent) ───────────────────────
# Each bundle: (gpu_vendor, gpu_renderer, hw_concurrency, device_memory_gb)
# Grouped by device class to prevent impossible combos.

_HARDWARE_BUNDLES: dict[str, list[tuple]] = {
    # Android / iOS mobile: realistic SoC + RAM pairings
    "mobile": [
        #  vendor      renderer             cores  mem
        ("Qualcomm", "Adreno (TM) 610",      4,    4),   # Snapdragon 665
        ("Qualcomm", "Adreno (TM) 618",      6,    4),   # Snapdragon 720G
        ("Qualcomm", "Adreno (TM) 640",      8,    6),   # Snapdragon 865
        ("Qualcomm", "Adreno (TM) 730",      8,    8),   # Snapdragon 8 Gen 1
        ("ARM",      "Mali-G52 MC2",          4,    4),   # Helio G85
        ("ARM",      "Mali-G76 MC4",          8,    4),   # Dimensity 800
        ("ARM",      "Mali-G78 MP14",         8,    8),   # Dimensity 9000
        ("Apple Inc.", "Apple GPU",           6,    4),   # A15 Bionic 6-core
        ("Apple Inc.", "Apple GPU",           6,    6),   # A15 Bionic 6-core 6 GB
    ],
    "mobile_w": [0.14, 0.14, 0.13, 0.10, 0.12, 0.12, 0.10, 0.10, 0.05],

    # Windows desktop/laptop: GPU tier determines CPU/RAM tier
    "windows_desktop": [
        ("Intel Inc.",         "Intel(R) UHD Graphics 620",        4,   8),  # ULV laptop
        ("Intel Inc.",         "Intel(R) UHD Graphics 630",        4,   8),  # Core i5 8th-gen
        ("Intel Inc.",         "Intel(R) UHD Graphics 770",        8,  16),  # Core i7 12th-gen
        ("Intel Inc.",         "Intel(R) Iris(R) Xe Graphics",     8,  16),  # Core i5/i7 11th-gen
        ("AMD",                "AMD Radeon(TM) RX 580 Series",     8,  16),  # Ryzen 5 + dGPU
        ("AMD",                "AMD Radeon(TM) RX 6600 XT",       12,  16),  # Ryzen 7 + dGPU
        ("NVIDIA Corporation", "NVIDIA GeForce GTX 1650/PCIe/SSE2", 8, 16), # Core i5 + GTX
        ("NVIDIA Corporation", "NVIDIA GeForce RTX 3060/PCIe/SSE2",12, 16), # Core i7 + RTX
    ],
    "windows_desktop_w": [0.15, 0.20, 0.10, 0.10, 0.08, 0.07, 0.15, 0.15],

    # macOS: Apple Silicon / Intel iMac
    "macos_desktop": [
        ("Apple Inc.", "Apple GPU",  8,  8),   # M1
        ("Apple Inc.", "Apple GPU", 10, 16),   # M1 Pro / M2
        ("Apple Inc.", "Apple GPU", 12, 16),   # M2 Pro
        ("Apple Inc.", "Apple GPU",  4,  8),   # Intel MacBook Air
    ],
    "macos_desktop_w": [0.35, 0.30, 0.20, 0.15],

    # Linux: typically developer workstation
    "linux_desktop": [
        ("Mesa/X.org", "Mesa Intel(R) HD Graphics 620 (KBL GT2)",   4,  8),
        ("Mesa/X.org", "Mesa Intel(R) UHD Graphics (CML GT2)",      8,  8),
        ("AMD",        "AMD RENOIR (LLVM 15.0.7, DRM 3.49, 6.2.0-39-generic)", 8, 16),
    ],
    "linux_desktop_w": [0.30, 0.40, 0.30],
}

# hw_concurrency reported by spec allows ±1 step from bundle value
_HW_CONC_VARIANTS: dict[int, list[int]] = {
    4:  [4],
    6:  [6],
    8:  [8, 8],     # most common — no variants
    10: [8, 10, 12],
    12: [10, 12, 12],
}


# Platform string per OS
_PLATFORM_MAP: dict[str, str] = {
    "iOS":     "iPhone",
    "macOS":   "MacIntel",
    "Android": "Linux armv8l",
    "Windows": "Win32",
    "Linux":   "Linux x86_64",
}

# Language fallback chains
_LANG_CHAIN: dict[str, list] = {
    "vi-VN": ["vi-VN", "vi", "en-US", "en"],
    "en-US": ["en-US", "en"],
    "en-GB": ["en-GB", "en", "en-US"],
    "th-TH": ["th-TH", "th", "en-US", "en"],
    "id-ID": ["id-ID", "id", "en-US", "en"],
    "zh-TW": ["zh-TW", "zh", "en-US", "en"],
    "ja-JP": ["ja-JP", "ja", "en-US", "en"],
    "ko-KR": ["ko-KR", "ko", "en-US", "en"],
    "de-DE": ["de-DE", "de", "en-US", "en"],
    "fr-FR": ["fr-FR", "fr", "en-US", "en"],
}

# Common fonts per OS
_OS_FONTS: dict[str, list] = {
    "iOS":     ["SF Pro", "Helvetica Neue", "Arial", "Georgia", "Times New Roman"],
    "macOS":   ["Helvetica Neue", "SF Pro", "Arial", "Georgia", "Courier New"],
    "Android": ["Roboto", "Noto Sans", "Droid Sans", "Arial", "Georgia"],
    "Windows": ["Segoe UI", "Arial", "Calibri", "Times New Roman", "Courier New"],
    "Linux":   ["Ubuntu", "DejaVu Sans", "Liberation Sans", "Arial", "Noto Sans"],
}


# ── Deterministic derivation functions ───────────────────────────────────────

def _os_family(os_str: str) -> str:
    for key in ("iOS", "macOS", "Android", "Windows", "Linux"):
        if key.lower() in os_str.lower():
            return key
    return "Windows"


def _bundle_key(profile: "IdentityProfile") -> str:
    """Map profile to the hardware bundle table key."""
    if profile.device_type == "mobile":
        return "mobile"
    os_fam = _os_family(profile.os)
    return {
        "Windows": "windows_desktop",
        "macOS":   "macos_desktop",
        "iOS":     "mobile",
        "Android": "mobile",
        "Linux":   "linux_desktop",
    }.get(os_fam, "windows_desktop")


def derive_hardware_profile(profile: "IdentityProfile") -> dict:
    """Select a coherent hardware bundle from ONE seed.

    Returns a dict with: gpu_vendor, gpu_renderer, hw_concurrency, device_memory.
    All four signals are co-derived from profile.webgl_noise_seed, so they
    describe a realistic device. Impossible combos (e.g., Adreno 610 + 16 cores)
    are structurally impossible because all values come from the same bundle row.

    The bundle is stable per account (deterministic from webgl_noise_seed).
    Rotating webgl_noise_seed via MutationController shifts the bundle coherently.
    """
    key      = _bundle_key(profile)
    bundles  = _HARDWARE_BUNDLES[key]
    weights  = _HARDWARE_BUNDLES[f"{key}_w"]
    bundle   = _seed_pick_weighted(profile.webgl_noise_seed, 20, bundles, weights)
    vendor, renderer, base_hw, mem = bundle

    # Allow minor hw_concurrency variance within bundle tier (still realistic)
    hw_variants = _HW_CONC_VARIANTS.get(base_hw, [base_hw])
    hw_conc = _seed_pick(profile.canvas_noise_seed, 32, hw_variants)

    LOGGER.debug("hardware_profile_chosen", extra={
        "account_id":   profile.account_id,
        "bundle_key":   key,
        "gpu_vendor":   vendor,
        "gpu_renderer": renderer,
        "hw_concurrency": hw_conc,
        "device_memory":  mem,
    })
    return {
        "gpu_vendor":     vendor,
        "gpu_renderer":   renderer,
        "hw_concurrency": hw_conc,
        "device_memory":  mem,
    }


def _derive_webgl(profile: "IdentityProfile") -> tuple[str, str]:
    """Seed-based WebGL vendor+renderer — reads from hardware bundle for coherence."""
    hw = derive_hardware_profile(profile)
    return hw["gpu_vendor"], hw["gpu_renderer"]


def _derive_hw_concurrency(profile: "IdentityProfile") -> int:
    """Reads from hardware bundle — consistent with GPU tier."""
    return derive_hardware_profile(profile)["hw_concurrency"]


def _derive_device_memory(profile: "IdentityProfile") -> int:
    """Spec-valid values — reads from hardware bundle for coherence."""
    return derive_hardware_profile(profile)["device_memory"]


def _derive_vendor(profile: "IdentityProfile") -> str:
    if "Safari" in profile.browser and "Chrome" not in profile.user_agent:
        return "Apple Computer, Inc."
    return "Google Inc."


def _derive_max_touch(profile: "IdentityProfile") -> int:
    return 5 if profile.device_type == "mobile" else 0


# ── Layer 1: Navigator overrides ─────────────────────────────────────────────

def _build_navigator_patch(profile: "IdentityProfile") -> str:
    os_family   = _os_family(profile.os)
    platform    = _PLATFORM_MAP.get(os_family, "Win32")
    # Call once — all hardware signals come from the same coherent bundle
    hw          = derive_hardware_profile(profile)
    hw_conc     = hw["hw_concurrency"]
    dev_mem     = hw["device_memory"]
    max_touch   = _derive_max_touch(profile)
    vendor      = _derive_vendor(profile)
    lang        = profile.locale
    product_sub = "20030107"
    app_version = (profile.user_agent.replace("Mozilla/", "", 1)
                   if profile.user_agent.startswith("Mozilla/") else profile.user_agent)
    lang_chain  = _LANG_CHAIN.get(lang, [lang, lang.split("-")[0], "en-US", "en"])
    lang_js     = str(lang_chain).replace("'", '"')

    return f"""
(function() {{
  Object.defineProperty(navigator, 'platform',            {{ get: () => '{platform}',    configurable: true }});
  Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {hw_conc},       configurable: true }});
  Object.defineProperty(navigator, 'deviceMemory',        {{ get: () => {dev_mem},       configurable: true }});
  Object.defineProperty(navigator, 'maxTouchPoints',      {{ get: () => {max_touch},     configurable: true }});
  Object.defineProperty(navigator, 'vendor',              {{ get: () => '{vendor}',      configurable: true }});
  Object.defineProperty(navigator, 'productSub',          {{ get: () => '{product_sub}', configurable: true }});
  Object.defineProperty(navigator, 'appVersion',          {{ get: () => '{app_version}', configurable: true }});
  Object.defineProperty(navigator, 'language',            {{ get: () => '{lang}',        configurable: true }});
  Object.defineProperty(navigator, 'languages',           {{ get: () => {lang_js},       configurable: true }});
  Object.defineProperty(navigator, 'webdriver',           {{ get: () => undefined,       configurable: true }});
}})();
"""


# ── Layer 2: WebGL spoofing ──────────────────────────────────────────────────

def _build_webgl_patch(profile: "IdentityProfile") -> str:
    vendor, renderer = _derive_webgl(profile)
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
    # Derive patch area from seed (Python-side, injected as constants)
    px_size = _seed_int(canvas_seed, 40, 3, 5)
    px_ox   = _seed_int(canvas_seed, 41, 0, 8)
    px_oy   = _seed_int(canvas_seed, 42, 0, 8)
    return f"""
(function() {{
  // Stable per-account canvas noise — mulberry32 PRNG (seed={canvas_seed})
  const SEED    = {canvas_seed};
  const PX_SIZE = {px_size};
  const PX_OX   = {px_ox};
  const PX_OY   = {px_oy};

  function mulberry32(s) {{
    s  = (s + 0x6D2B79F5) >>> 0;
    s  = Math.imul(s ^ (s >>> 15), s | 1) >>> 0;
    s ^= s + Math.imul(s ^ (s >>> 7), s | 61) >>> 0;
    return ((s ^ (s >>> 14)) >>> 0) / 4294967296;
  }}

  function applyNoise(ctx) {{
    if (!ctx || ctx.canvas.width < PX_OX + PX_SIZE || ctx.canvas.height < PX_OY + PX_SIZE) return;
    try {{
      const d = ctx.getImageData(PX_OX, PX_OY, PX_SIZE, PX_SIZE);
      let s = SEED >>> 0;
      for (let i = 0; i < d.data.length; i += 4) {{
        const r1 = mulberry32(s); s = (s * 1664525 + 1013904223) >>> 0;
        const r2 = mulberry32(s); s = (s * 1664525 + 1013904223) >>> 0;
        const r3 = mulberry32(s); s = (s * 1664525 + 1013904223) >>> 0;
        d.data[i]   = Math.min(255, Math.max(0, d.data[i]   + Math.floor(r1 * 5) - 2));
        d.data[i+1] = Math.min(255, Math.max(0, d.data[i+1] + Math.floor(r2 * 5) - 2));
        d.data[i+2] = Math.min(255, Math.max(0, d.data[i+2] + Math.floor(r3 * 5) - 2));
      }}
      ctx.putImageData(d, PX_OX, PX_OY);
    }} catch(e) {{}}
  }}

  const _toDataURL = HTMLCanvasElement.prototype.toDataURL;
  HTMLCanvasElement.prototype.toDataURL = function(type, quality) {{
    applyNoise(this.getContext('2d'));
    return _toDataURL.apply(this, arguments);
  }};
  const _toBlob = HTMLCanvasElement.prototype.toBlob;
  HTMLCanvasElement.prototype.toBlob = function(cb, type, quality) {{
    applyNoise(this.getContext('2d'));
    return _toBlob.apply(this, arguments);
  }};
}})();
"""


# ── Layer 4: Audio fingerprint stabilization ─────────────────────────────────

def _build_audio_patch(audio_seed: int) -> str:
    # Stride 30–69, magnitude range, both from seed
    stride    = _seed_int(audio_seed, 50, 30, 69)
    magnitude = 0.000015 + _mulberry32_float(audio_seed ^ 0xA5A5) * 0.000075
    return f"""
(function() {{
  // Stable per-account audio noise — xorshift32 PRNG (seed={audio_seed})
  const STRIDE    = {stride};
  const MAGNITUDE = {magnitude:.8f};
  let _xs = ({audio_seed} >>> 0) || 1;

  function xorshift32(s) {{ s ^= s << 13; s ^= s >>> 17; s ^= s << 5; return s >>> 0; }}
  function nextFloat()   {{ _xs = xorshift32(_xs); return _xs / 4294967296; }}

  function applyNoise(buf) {{
    _xs = ({audio_seed} >>> 0) || 1;   // reset for determinism
    for (let i = 0; i < buf.length; i += STRIDE) {{
      buf[i] += MAGNITUDE * (nextFloat() > 0.5 ? 1 : -1) * nextFloat();
    }}
  }}

  const _getChannelData = AudioBuffer.prototype.getChannelData;
  AudioBuffer.prototype.getChannelData = function(ch) {{
    const buf = _getChannelData.call(this, ch); applyNoise(buf); return buf;
  }};
  if (AudioBuffer.prototype.copyFromChannel) {{
    const _copyFrom = AudioBuffer.prototype.copyFromChannel;
    AudioBuffer.prototype.copyFromChannel = function(dest, ch, off) {{
      _copyFrom.call(this, dest, ch, off); applyNoise(dest);
    }};
  }}
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

def _build_plugin_patch(profile: "IdentityProfile") -> str:
    """OS-aware plugin list: mobile=empty, macOS=PDF only, Windows/Linux=PDF+NaCl."""
    os_fam = _os_family(profile.os)
    if profile.device_type == "mobile":
        return """(function() {
  Object.defineProperty(navigator, 'plugins',   { get: () => { const a=[]; a.item=()=>null; a.namedItem=()=>null; return a; }, configurable: true });
  Object.defineProperty(navigator, 'mimeTypes', { get: () => { const a=[]; a.item=()=>null; a.namedItem=()=>null; return a; }, configurable: true });
})();"""
    pdf_only = os_fam == "macOS"
    nacl_plugin = "" if pdf_only else ",mkP('Native Client','internal-nacl-plugin','',fMime('application/x-nacl','NaCl','nexe'))"
    return f"""(function() {{
  const fMime = (t,d,e) => ({{type:t,description:d,suffixes:e,enabledPlugin:null}});
  const mkP = (name,file,desc,...mimes) => {{ const p={{name,filename:file,description:desc,length:mimes.length}}; mimes.forEach((m,i)=>{{p[i]=m;}}); p.item=i=>p[i]||null; p.namedItem=()=>null; return p; }};
  const plugins = [mkP('Chrome PDF Plugin','internal-pdf-viewer','Portable Document Format',fMime('application/x-google-chrome-pdf','PDF','pdf')),mkP('Chrome PDF Viewer','mhjfbmdgcfjbbpaeojofohoefgiehjai','',fMime('application/pdf','PDF','pdf')){nacl_plugin}];
  Object.defineProperty(navigator, 'plugins', {{ get: () => {{ const a=[...plugins]; a.item=i=>a[i]; a.namedItem=()=>null; return a; }}, configurable: true }});
}})();"""


# ── Public API ───────────────────────────────────────────────────────────────

def get_identity_scripts(profile: "IdentityProfile") -> list[str]:
    """Return ordered list of init scripts enforcing the IdentityProfile in-browser.

    All scripts are deterministic — same profile → same JS overrides every run.
    Apply via: for s in get_identity_scripts(profile): await ctx.add_init_script(s)
    """
    return [
        _build_navigator_patch(profile),
        _build_webgl_patch(profile),
        _build_canvas_patch(profile.canvas_noise_seed),
        _build_audio_patch(profile.webgl_noise_seed),
        _build_font_patch(profile),
        _build_screen_patch(profile),
        _PATCH_CHROME_RUNTIME,
        _PATCH_PERMISSIONS,
        _build_plugin_patch(profile),   # OS-aware: mobile=empty, macOS=PDF, Win=PDF+NaCl
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

    os_family = _os_family(profile.os)
    exp_plat  = _PLATFORM_MAP.get(os_family, "Win32")
    exp_hw    = _derive_hw_concurrency(profile)    # seed-derived, matches what was injected
    exp_mem   = _derive_device_memory(profile)     # seed-derived
    exp_wgl_v, exp_wgl_r = _derive_webgl(profile)  # seed-derived

    checks = [
        ("platform",           exp_plat,         runtime.get("platform"),                 "CRITICAL"),
        ("hardwareConcurrency", str(exp_hw),      str(runtime.get("hardwareConcurrency")), "WARNING"),
        ("deviceMemory",        str(exp_mem),     str(runtime.get("deviceMemory")),        "WARNING"),
        ("language",            profile.locale,   runtime.get("language"),                 "CRITICAL"),
        ("timezone",            profile.timezone, runtime.get("timezone"),                 "CRITICAL"),
        ("webglVendor",         exp_wgl_v,        runtime.get("webglVendor"),              "CRITICAL"),
        ("webglRenderer",       exp_wgl_r,        runtime.get("webglRenderer"),            "WARNING"),
        ("webdriver",           "undefined",      str(runtime.get("webdriver")),           "CRITICAL"),
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
    return {
        "fingerprint_changed": bool(codes & {"WEBDRIVER_EXPOSED", "WEBGLVENDOR_MISMATCH",
                                             "WEBGLRENDERER_MISMATCH", "PLATFORM_MISMATCH",
                                             "LANGUAGE_MISMATCH"}),
        "device_mismatch":     bool(codes & {"PLATFORM_MISMATCH", "SCREEN_MISMATCH",
                                             "HARDWARECONCURRENCY_MISMATCH"}),
        "geo_mismatch":        bool(codes & {"TIMEZONE_MISMATCH", "LANGUAGE_MISMATCH"}),
        "identity_risk_score": min(1.0, len(issues) * 0.15),
    }


# ── Public anti-detect API ───────────────────────────────────────────────────
# Used by MutationController and StealthBrain.

def generate_base(profile: "IdentityProfile") -> str:
    """Recompute the base fingerprint hash from identity fields.

    Used by MutationController after HIGH-risk full mutation to verify
    the new active_fingerprint is correctly diverged from base.
    """
    from core.identity_manager import generate_fingerprint
    return generate_fingerprint(profile)


def derive_active(profile: "IdentityProfile") -> list[str]:
    """Return the ordered list of JS init scripts for the current active state.

    Alias of get_identity_scripts() — the name makes the intent explicit:
    'derive the browser-injected scripts from the active identity state'.
    """
    return get_identity_scripts(profile)


# Public alias so MutationController can import without touching private symbols
seed_int = _seed_int
