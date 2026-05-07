/**
 * fingerprint_scripts.js — Standalone Stealth Fingerprint Patches
 *
 * This is a REFERENCE IMPLEMENTATION with hardcoded defaults.
 * In production, fingerprint_engine.py generates per-account versions
 * of each script section with values taken from IdentityProfile.
 *
 * Placeholder → IdentityProfile field mapping:
 *   PLATFORM       → profile.os            → _PLATFORM_MAP[os_family]
 *   HW_CONCURRENCY → profile.device_type   → _HW_CONCURRENCY[device_type]
 *   DEVICE_MEMORY  → profile.device_type   → _DEVICE_MEMORY[device_type]
 *   LANGUAGE       → profile.locale
 *   LANG_SHORT     → profile.locale.split('-')[0]
 *   WEBGL_VENDOR   → profile.os            → _WEBGL_PROFILES[os_family].vendor
 *   WEBGL_RENDERER → profile.os            → _WEBGL_PROFILES[os_family].renderer
 *   SCREEN_W/H     → profile.screen_resolution
 *   PIXEL_RATIO    → profile.device_type   → 2 (mobile) or 1 (desktop)
 *   CANVAS_SEED    → profile.canvas_noise_seed
 *
 * Inject order (all as init scripts, BEFORE page JS):
 *   1. Artifact removal
 *   2. Navigator overrides
 *   3. WebGL spoofing
 *   4. Canvas noise
 *   5. Audio noise
 *   6. Screen resolution
 *   7. Chrome runtime
 *   8. Plugins
 *   9. Human signals
 */

// ═══════════════════════════════════════════════════════════════════════════
// SCRIPT 1 — Automation Artifact Removal
// ═══════════════════════════════════════════════════════════════════════════
(function removeAutomationArtifacts() {
  'use strict';

  const AUTOMATION_KEYS = [
    'webdriver', 'driver', 'selenium', '_selenium',
    '__driver_evaluate', '__webdriver_evaluate', '__selenium_evaluate',
    '__fxdriver_evaluate', '__driver_unwrapped', '__webdriver_unwrapped',
    '__selenium_unwrapped', '__fxdriver_unwrapped',
    '_Selenium_IDE_Recorder', 'calledSelenium', 'callPhantom',
    '_phantom', '__nightmare', 'domAutomation', 'domAutomationController',
    'cdc_adoQpoasnfa76pfcZLmcfl_Array', 'cdc_adoQpoasnfa76pfcZLmcfl_Promise',
    'cdc_adoQpoasnfa76pfcZLmcfl_Symbol',
  ];

  AUTOMATION_KEYS.forEach(key => {
    try {
      if (key in window) {
        Object.defineProperty(window, key, { get: () => undefined, configurable: true });
      }
    } catch(e) {}
  });

  Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
    configurable: true,
  });

  // Sanitize Error stack traces that reveal playwright/puppeteer
  const _OrigError = window.Error;
  window.Error = function(...args) {
    const e = new _OrigError(...args);
    if (e.stack) {
      e.stack = e.stack
        .replace(/playwright|puppeteer|selenium|webdriver/gi, 'Chrome')
        .replace(/HeadlessChrome/g, 'Chrome');
    }
    return e;
  };
  Object.setPrototypeOf(window.Error, _OrigError);
  window.Error.prototype = _OrigError.prototype;
})();


// ═══════════════════════════════════════════════════════════════════════════
// SCRIPT 2 — Navigator Overrides
// Per-account: PLATFORM, HW_CONCURRENCY, DEVICE_MEMORY, LANGUAGE, LANG_SHORT
// ═══════════════════════════════════════════════════════════════════════════
(function patchNavigator() {
  'use strict';

  // Default: Windows desktop, en-US  →  replaced per account in fingerprint_engine.py
  var PLATFORM       = 'Win32';          // profile → _PLATFORM_MAP[os_family]
  var HW_CONCURRENCY = 8;                // profile → _HW_CONCURRENCY[device_type]
  var DEVICE_MEMORY  = 8;                // profile → _DEVICE_MEMORY[device_type]
  var LANGUAGE       = 'en-US';          // profile.locale
  var LANG_SHORT     = 'en';             // profile.locale.split('-')[0]

  var props = {
    platform:            PLATFORM,
    hardwareConcurrency: HW_CONCURRENCY,
    deviceMemory:        DEVICE_MEMORY,
    language:            LANGUAGE,
    languages:           [LANGUAGE, LANG_SHORT],
  };

  for (var key in props) {
    try {
      (function(k, v) {
        Object.defineProperty(navigator, k, {
          get: function() { return v; },
          configurable: true,
        });
      })(key, props[key]);
    } catch(e) {}
  }

  // Spoof connection type (headless often missing)
  if (navigator.connection) {
    try {
      Object.defineProperty(navigator.connection, 'effectiveType', {
        get: function() { return '4g'; }, configurable: true,
      });
      Object.defineProperty(navigator.connection, 'rtt', {
        get: function() { return 50; }, configurable: true,
      });
    } catch(e) {}
  }
})();


// ═══════════════════════════════════════════════════════════════════════════
// SCRIPT 3 — WebGL Spoofing
// Per-account: WEBGL_VENDOR, WEBGL_RENDERER
// ═══════════════════════════════════════════════════════════════════════════
(function patchWebGL() {
  'use strict';

  // Default: Windows Intel  →  replaced per account in fingerprint_engine.py
  var VENDOR   = 'Intel Inc.';                  // profile → _WEBGL_PROFILES[os_family].vendor
  var RENDERER = 'Intel(R) UHD Graphics 630';   // profile → _WEBGL_PROFILES[os_family].renderer
  var UNMASKED_VENDOR_WEBGL   = 37445;
  var UNMASKED_RENDERER_WEBGL = 37446;

  function patch(proto) {
    var orig = proto.getParameter;
    proto.getParameter = function(p) {
      if (p === UNMASKED_VENDOR_WEBGL)   return VENDOR;
      if (p === UNMASKED_RENDERER_WEBGL) return RENDERER;
      return orig.call(this, p);
    };
  }

  if (window.WebGLRenderingContext)  patch(WebGLRenderingContext.prototype);
  if (window.WebGL2RenderingContext) patch(WebGL2RenderingContext.prototype);

  // Spoof WEBGL_debug_renderer_info extension
  var origGetExtension = WebGLRenderingContext.prototype.getExtension;
  WebGLRenderingContext.prototype.getExtension = function(name) {
    if (name === 'WEBGL_debug_renderer_info') {
      return { UNMASKED_VENDOR_WEBGL: 37445, UNMASKED_RENDERER_WEBGL: 37446 };
    }
    return origGetExtension.call(this, name);
  };
})();


// ═══════════════════════════════════════════════════════════════════════════
// SCRIPT 4 — Canvas Noise (deterministic)
// Per-account: CANVAS_SEED (profile.canvas_noise_seed)
// ═══════════════════════════════════════════════════════════════════════════
(function patchCanvas() {
  'use strict';

  // Default seed  →  replaced per account in fingerprint_engine._build_canvas_patch()
  var SEED = 512345;   // profile.canvas_noise_seed
  var R = (SEED & 0x3);
  var G = ((SEED >> 2) & 0x3);
  var B = ((SEED >> 4) & 0x3);

  var _toDataURL = HTMLCanvasElement.prototype.toDataURL;
  HTMLCanvasElement.prototype.toDataURL = function(type, quality) {
    var ctx = this.getContext('2d');
    if (ctx && this.width > 0 && this.height > 0) {
      try {
        var d = ctx.getImageData(0, 0, 1, 1);
        d.data[0] = Math.min(255, d.data[0] ^ R);
        d.data[1] = Math.min(255, d.data[1] ^ G);
        d.data[2] = Math.min(255, d.data[2] ^ B);
        ctx.putImageData(d, 0, 0);
      } catch(e) {}
    }
    return _toDataURL.apply(this, arguments);
  };

  var _toBlob = HTMLCanvasElement.prototype.toBlob;
  HTMLCanvasElement.prototype.toBlob = function(cb, type, q) {
    return _toBlob.call(this, cb, type, q);
  };
})();


// ═══════════════════════════════════════════════════════════════════════════
// SCRIPT 5 — Audio Fingerprint Noise (deterministic)
// Per-account: CANVAS_SEED (reused as audio seed in fingerprint_engine.py)
// ═══════════════════════════════════════════════════════════════════════════
(function patchAudio() {
  'use strict';

  var SEED      = 512345;   // profile.canvas_noise_seed (same seed reused for audio)
  var MAGNITUDE = 0.00001 + ((SEED & 0xFF) / 255) * 0.00009;

  var _getChannelData = AudioBuffer.prototype.getChannelData;
  AudioBuffer.prototype.getChannelData = function(ch) {
    var buf = _getChannelData.call(this, ch);
    for (var i = 0; i < buf.length; i += 97) {
      buf[i] += MAGNITUDE * (i % 2 === 0 ? 1 : -1);
    }
    return buf;
  };

  if (window.AnalyserNode) {
    var _getFFT = AnalyserNode.prototype.getFloatFrequencyData;
    if (_getFFT) {
      AnalyserNode.prototype.getFloatFrequencyData = function(arr) {
        _getFFT.call(this, arr);
        arr[0] += MAGNITUDE;
      };
    }
  }
})();


// ═══════════════════════════════════════════════════════════════════════════
// SCRIPT 6 — Screen Resolution
// Per-account: SCREEN_W, SCREEN_H, PIXEL_RATIO
// ═══════════════════════════════════════════════════════════════════════════
(function patchScreen() {
  'use strict';

  // Default: 1920×1080 desktop  →  replaced per account in fingerprint_engine.py
  var W   = 1920;   // profile.screen_resolution.split('x')[0]
  var H   = 1080;   // profile.screen_resolution.split('x')[1]
  var DPR = 1;      // 2 for mobile, 1 for desktop

  var overrides = [
    ['width',       W],
    ['height',      H],
    ['availWidth',  W],
    ['availHeight', H - 40],
    ['colorDepth',  24],
    ['pixelDepth',  24],
  ];

  overrides.forEach(function(pair) {
    try {
      (function(prop, val) {
        Object.defineProperty(screen, prop, {
          get: function() { return val; },
          configurable: true,
        });
      })(pair[0], pair[1]);
    } catch(e) {}
  });

  try {
    Object.defineProperty(window, 'devicePixelRatio', {
      get: function() { return DPR; },
      configurable: true,
    });
  } catch(e) {}
})();


// ═══════════════════════════════════════════════════════════════════════════
// SCRIPT 7 — Chrome Runtime Stub
// ═══════════════════════════════════════════════════════════════════════════
(function patchChrome() {
  'use strict';

  if (!window.chrome) window.chrome = {};
  if (!window.chrome.runtime) {
    window.chrome.runtime = {
      onConnect:   { addListener: function() {}, removeListener: function() {} },
      onMessage:   { addListener: function() {}, removeListener: function() {} },
      connect:     function() { return { onMessage: { addListener: function() {} }, postMessage: function() {} }; },
      sendMessage: function() {},
      id: undefined,
    };
  }
  if (!window.chrome.app) {
    window.chrome.app = {
      isInstalled: false,
      getDetails:     function() { return null; },
      getIsInstalled: function() { return false; },
    };
  }
})();


// ═══════════════════════════════════════════════════════════════════════════
// SCRIPT 8 — Plugins & MimeTypes
// ═══════════════════════════════════════════════════════════════════════════
(function patchPlugins() {
  'use strict';

  function mkMime(t, d, e) { return { type: t, description: d, suffixes: e, enabledPlugin: null }; }
  function mkPlugin(name, file, desc) {
    var mimes = Array.prototype.slice.call(arguments, 3);
    var p = Object.create(null);
    p.name = name; p.filename = file; p.description = desc; p.length = mimes.length;
    mimes.forEach(function(m, i) { p[i] = m; });
    p.item = function(i) { return p[i] || null; };
    p.namedItem = function() { return null; };
    return p;
  }

  var plugins = [
    mkPlugin('Chrome PDF Plugin', 'internal-pdf-viewer', 'Portable Document Format',
      mkMime('application/x-google-chrome-pdf', 'Portable Document Format', 'pdf')),
    mkPlugin('Chrome PDF Viewer', 'mhjfbmdgcfjbbpaeojofohoefgiehjai', '',
      mkMime('application/pdf', 'Portable Document Format', 'pdf')),
    mkPlugin('Native Client', 'internal-nacl-plugin', '',
      mkMime('application/x-nacl', 'Native Client Executable', 'nexe'),
      mkMime('application/x-pnacl', 'Portable Native Client Executable', 'pexe')),
  ];

  Object.defineProperty(navigator, 'plugins', {
    get: function() {
      var a = plugins.slice();
      a.item = function(i) { return a[i]; };
      a.namedItem = function() { return null; };
      return a;
    },
    configurable: true,
  });

  Object.defineProperty(navigator, 'mimeTypes', {
    get: function() {
      var m = [plugins[0][0], plugins[1][0], plugins[2][0], plugins[2][1]];
      m.item = function(i) { return m[i]; };
      m.namedItem = function() { return null; };
      return m;
    },
    configurable: true,
  });
})();


// ═══════════════════════════════════════════════════════════════════════════
// SCRIPT 9 — Human Signal Patches (LCG seeded — no Math.random)
// Per-account: CANVAS_SEED
// ═══════════════════════════════════════════════════════════════════════════
(function patchHumanSignals() {
  'use strict';

  // Seeded LCG — deterministic per account, replaced in stealth_context.py
  var _s = (512345) >>> 0;   // profile.canvas_noise_seed
  function lcg() {
    _s = ((_s * 1664525 + 1013904223) & 0xFFFFFFFF) >>> 0;
    return _s / 0xFFFFFFFF;
  }

  // Mouse movement simulation
  var mx = (200 + lcg() * 600) | 0;
  var my = (200 + lcg() * 300) | 0;
  (function moveMouse() {
    mx = Math.max(10, Math.min(window.innerWidth  - 10, mx + ((lcg() - 0.5) * 12) | 0));
    my = Math.max(10, Math.min(window.innerHeight - 10, my + ((lcg() - 0.5) * 8)  | 0));
    try {
      document.dispatchEvent(new MouseEvent('mousemove', {
        clientX: mx, clientY: my, bubbles: true, cancelable: false,
      }));
    } catch(e) {}
    setTimeout(moveMouse, (3000 + lcg() * 4000) | 0);
  })();

  // Page visibility — always visible
  try {
    Object.defineProperty(document, 'visibilityState', {
      get: function() { return 'visible'; }, configurable: true,
    });
    Object.defineProperty(document, 'hidden', {
      get: function() { return false; }, configurable: true,
    });
  } catch(e) {}

  // Permissions API — return 'granted' for common permissions
  if (navigator.permissions && navigator.permissions.query) {
    var _origQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = function(p) {
      if (['notifications', 'geolocation', 'camera', 'microphone'].indexOf(p.name) !== -1) {
        return Promise.resolve({ state: 'granted', onchange: null });
      }
      return _origQuery(p);
    };
  }

  // IdleDetector stub
  if (!window.IdleDetector) {
    window.IdleDetector = (function() {
      function IdleDetector() {}
      IdleDetector.prototype = Object.create(EventTarget.prototype);
      Object.defineProperty(IdleDetector.prototype, 'userState',   { get: function() { return 'active'; } });
      Object.defineProperty(IdleDetector.prototype, 'screenState', { get: function() { return 'unlocked'; } });
      IdleDetector.prototype.start = function() { return Promise.resolve(); };
      return IdleDetector;
    })();
  }
})();
