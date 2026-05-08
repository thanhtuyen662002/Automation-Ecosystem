"""
Runtime Fingerprint Validator — dedicated risk scoring module.

Wraps fingerprint_engine.validate_runtime() with:
  - Structured risk scoring (0.0–1.0)
  - Category-level breakdown (navigator / webgl / timing / behavioral)
  - Formatted report for logging and dashboard

Usage (in publisher after page.goto()):
    from core.runtime_validator import validate_fingerprint, compute_risk_score

    issues = await validate_fingerprint(page, profile)
    result = compute_risk_score(issues)
    # result.score: float
    # result.breakdown: dict[str, float]
    # result.fingerprint_changed: bool  → feed into SessionSignals
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Page
    from core.identity_manager import IdentityProfile

from core.fingerprint_engine import RuntimeValidationIssue, validate_runtime

LOGGER = logging.getLogger("core.runtime_validator")


# ── Per-signal risk weights ───────────────────────────────────────────────────
# Score formula: P(detected) = 1 − Π(1 − w_i)  [probabilistic union]
# Each weight is P(this signal alone would cause detection).
# Range: 0.0 (harmless) → 1.0 (certain detection).

_SIGNAL_WEIGHTS: dict[str, float] = {
    "WEBDRIVER_EXPOSED":            0.90,
    "TIMEZONE_MISMATCH":            0.70,
    "WEBGLVENDOR_MISMATCH":         0.60,
    "PLATFORM_MISMATCH":            0.55,
    "LANGUAGE_MISMATCH":            0.50,
    "WEBGLRENDERER_MISMATCH":       0.30,
    "SCREEN_MISMATCH":              0.25,
    "EVAL_FAILED":                  0.20,
    "HARDWARECONCURRENCY_MISMATCH": 0.15,
    "DEVICEMEMORY_MISMATCH":        0.10,
}

# Category routing for breakdown report
_CODE_CATEGORY: dict[str, str] = {
    "WEBDRIVER_EXPOSED":            "behavioral",
    "EVAL_FAILED":                  "behavioral",
    "PLATFORM_MISMATCH":            "navigator",
    "HARDWARECONCURRENCY_MISMATCH": "navigator",
    "DEVICEMEMORY_MISMATCH":        "navigator",
    "LANGUAGE_MISMATCH":            "navigator",
    "SCREEN_MISMATCH":              "navigator",
    "TIMEZONE_MISMATCH":            "geo",
    "WEBGLVENDOR_MISMATCH":         "rendering",
    "WEBGLRENDERER_MISMATCH":       "rendering",
}


@dataclass
class FingerprintRiskResult:
    """Structured risk assessment from a runtime validation pass."""

    score: float                        # 0.0=clean, 1.0=certain detection
    issues: list[RuntimeValidationIssue]
    breakdown: dict[str, float]         # category → sub-score
    fingerprint_changed: bool           # feed into SessionSignals
    geo_mismatch: bool                  # feed into SessionSignals
    device_mismatch: bool               # feed into SessionSignals
    identity_risk_score: float          # alias of score for SessionSignals

    def to_session_signals(self) -> dict[str, Any]:
        """Return dict compatible with UpdateStrategyRequest / SessionSignals."""
        return {
            "fingerprint_changed":  self.fingerprint_changed,
            "geo_mismatch":         self.geo_mismatch,
            "device_mismatch":      self.device_mismatch,
            "identity_risk_score":  self.score,
            "ip_changed":           False,
        }

    def summary(self) -> dict[str, Any]:
        return {
            "risk_score":         round(self.score, 3),
            "issue_count":        len(self.issues),
            "critical_count":     sum(1 for i in self.issues if i.severity == "CRITICAL"),
            "warning_count":      sum(1 for i in self.issues if i.severity == "WARNING"),
            "breakdown":          {k: round(v, 3) for k, v in self.breakdown.items()},
            "fingerprint_changed": self.fingerprint_changed,
            "geo_mismatch":        self.geo_mismatch,
            "device_mismatch":     self.device_mismatch,
        }


def compute_risk_score(issues: list[RuntimeValidationIssue]) -> FingerprintRiskResult:
    """Compute structured risk assessment from validation issues.

    Score formula: P(detected) = 1 − Π(1 − w_i)  [probabilistic union]
    Naturally non-linear and capped at 1.0.
    Per-signal weights in _SIGNAL_WEIGHTS.
    Breakdown is computed per category using the same formula.
    """
    breakdown: dict[str, float] = {"navigator": 0.0, "rendering": 0.0,
                                   "geo": 0.0,       "behavioral": 0.0}
    codes = {i.code for i in issues}

    global_remaining: float = 1.0
    cat_remaining: dict[str, float] = {k: 1.0 for k in breakdown}

    for issue in issues:
        w   = _SIGNAL_WEIGHTS.get(issue.code, 0.10)
        global_remaining *= (1.0 - w)
        cat = _CODE_CATEGORY.get(issue.code, "behavioral")
        cat_remaining[cat] *= (1.0 - w)

    score = round(1.0 - global_remaining, 4)
    for cat in breakdown:
        breakdown[cat] = round(1.0 - cat_remaining[cat], 4)

    fingerprint_changed = bool(codes & {
        "WEBDRIVER_EXPOSED", "WEBGLVENDOR_MISMATCH", "WEBGLRENDERER_MISMATCH",
        "PLATFORM_MISMATCH", "LANGUAGE_MISMATCH",
    })
    geo_mismatch    = bool(codes & {"TIMEZONE_MISMATCH", "LANGUAGE_MISMATCH"})
    device_mismatch = bool(codes & {"SCREEN_MISMATCH", "HARDWARECONCURRENCY_MISMATCH",
                                    "DEVICEMEMORY_MISMATCH"})

    return FingerprintRiskResult(
        score=score,
        issues=issues,
        breakdown=breakdown,
        fingerprint_changed=fingerprint_changed,
        geo_mismatch=geo_mismatch,
        device_mismatch=device_mismatch,
        identity_risk_score=score,
    )


@dataclass
class RuntimeSignals:
    """Normalized boolean/float view of a FingerprintRiskResult.

    Consumed by StealthBrain.evaluate() — no string parsing or code-set
    operations needed on the brain side. Every signal is a plain bool or float.

    All fields default to True / 0.0 (clean) so that older callers that
    build this manually don't need to specify every field.
    """
    # Navigator surface
    platform_match:          bool  = True
    hardware_match:          bool  = True
    language_match:          bool  = True
    screen_match:            bool  = True

    # Geo
    timezone_match:          bool  = True

    # Rendering
    webgl_vendor_match:      bool  = True
    webgl_renderer_match:    bool  = True

    # Behavioral
    webdriver_hidden:        bool  = True
    eval_ok:                 bool  = True

    # Aggregate
    risk_score:              float = 0.0
    breakdown:               dict  = field(default_factory=dict)

    # Derived flags (mirrors FingerprintRiskResult)
    fingerprint_changed:     bool  = False
    geo_mismatch:            bool  = False
    device_mismatch:         bool  = False


def to_runtime_signals(risk: FingerprintRiskResult) -> RuntimeSignals:
    """Convert a FingerprintRiskResult into a normalized RuntimeSignals object.

    This is the primary feed from RuntimeValidator into StealthBrain.
    All fields are booleans or floats — no code-string matching needed.
    """
    codes = {i.code for i in risk.issues}
    return RuntimeSignals(
        platform_match       = "PLATFORM_MISMATCH"              not in codes,
        hardware_match       = not bool(codes & {
                                    "HARDWARECONCURRENCY_MISMATCH", "DEVICEMEMORY_MISMATCH"}),
        language_match       = "LANGUAGE_MISMATCH"              not in codes,
        screen_match         = "SCREEN_MISMATCH"                not in codes,
        timezone_match       = "TIMEZONE_MISMATCH"              not in codes,
        webgl_vendor_match   = "WEBGLVENDOR_MISMATCH"           not in codes,
        webgl_renderer_match = "WEBGLRENDERER_MISMATCH"         not in codes,
        webdriver_hidden     = "WEBDRIVER_EXPOSED"              not in codes,
        eval_ok              = "EVAL_FAILED"                    not in codes,
        risk_score           = risk.score,
        breakdown            = dict(risk.breakdown),
        fingerprint_changed  = risk.fingerprint_changed,
        geo_mismatch         = risk.geo_mismatch,
        device_mismatch      = risk.device_mismatch,
    )


async def validate_fingerprint(
    page: "Page",
    profile: "IdentityProfile",
) -> FingerprintRiskResult:
    """Full fingerprint validation: evaluate runtime, compute risk, log result.

    This is the primary integration point for publishers. Call after page.goto()
    and before any interaction. Feed result.to_session_signals() into AccountBrain.

    Args:
        page:    Playwright Page object (post navigation)
        profile: IdentityProfile from IdentityRegistry

    Returns:
        FingerprintRiskResult with score, breakdown, and SessionSignals helper.
    """
    issues = await validate_runtime(page, profile)
    result = compute_risk_score(issues)

    log_level = (
        LOGGER.critical if result.score >= 0.6
        else LOGGER.warning if result.score >= 0.3
        else LOGGER.info
    )
    log_level("fingerprint_validation_result", extra={
        "event":        "fingerprint_validation_result",
        "account_id":   profile.account_id,
        **result.summary(),
    })

    return result


async def quick_bot_check(page: "Page") -> dict[str, Any]:
    """Run basic heuristic bot detection checks independently of IdentityProfile.

    Useful for pre-flight checks before a session starts.
    Returns a dict with individual check results and an overall risk_score.
    """
    try:
        checks: dict[str, Any] = await page.evaluate("""() => ({
            webdriver:          navigator.webdriver,
            languages_empty:    !navigator.languages || navigator.languages.length === 0,
            plugins_empty:      navigator.plugins.length === 0,
            chrome_missing:     !window.chrome || !window.chrome.runtime,
            notification_denied: (typeof Notification !== 'undefined' &&
                                   Notification.permission === 'denied'),
            screen_zero:        screen.width === 0 || screen.height === 0,
            hw_concurrency_low: navigator.hardwareConcurrency < 2,
            webgl_missing:      (() => {
                try {
                    const c = document.createElement('canvas');
                    return !c.getContext('webgl') && !c.getContext('webgl2');
                } catch(e) { return true; }
            })(),
        })""")
    except Exception as exc:
        return {"error": str(exc), "risk_score": 0.5}

    penalties: dict[str, float] = {
        "webdriver":          0.40,
        "languages_empty":    0.15,
        "plugins_empty":      0.15,
        "chrome_missing":     0.20,
        "notification_denied": 0.05,
        "screen_zero":        0.25,
        "hw_concurrency_low": 0.10,
        "webgl_missing":      0.15,
    }

    # plugins_empty is authentic on mobile Chrome/Safari — skip that penalty
    ua = str(checks.get("_ua", ""))  # not in payload; use fallback
    is_mobile_ua = any(kw in checks.get("__ua__", "") for kw in ("Mobile", "Android", "iPhone"))
    # Detect via hw_concurrency_low as mobile proxy (4 cores or fewer + no plugins)
    if checks.get("plugins_empty") and checks.get("hw_concurrency_low"):
        # Possibly mobile: don't penalise plugins_empty
        penalties["plugins_empty"] = 0.0

    score = 0.0
    triggered = []
    for key, penalty in penalties.items():
        if checks.get(key):
            score += penalty
            triggered.append(key)

    result = {
        "risk_score":    round(min(1.0, score), 3),
        "triggered":     triggered,
        "checks":        checks,
        "safe":          score < 0.20,
    }
    LOGGER.info("quick_bot_check", extra={"event": "quick_bot_check", **result})
    return result
