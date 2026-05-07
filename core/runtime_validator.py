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


# ── Risk weights per issue code ───────────────────────────────────────────────

_CRITICAL_WEIGHT = 0.30
_WARNING_WEIGHT  = 0.10

# Category routing for breakdown report
_CODE_CATEGORY: dict[str, str] = {
    "WEBDRIVER_EXPOSED":       "behavioral",
    "PLATFORM_MISMATCH":       "navigator",
    "HARDWARECONCURRENCY_MISMATCH": "navigator",
    "DEVICEMEMORY_MISMATCH":   "navigator",
    "LANGUAGE_MISMATCH":       "navigator",
    "TIMEZONE_MISMATCH":       "timing",
    "WEBGLVENDOR_MISMATCH":    "webgl",
    "WEBGLRENDERER_MISMATCH":  "webgl",
    "SCREEN_MISMATCH":         "navigator",
    "EVAL_FAILED":             "behavioral",
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
        codes = {i.code for i in self.issues}
        return {
            "fingerprint_changed":  self.fingerprint_changed,
            "geo_mismatch":         self.geo_mismatch,
            "device_mismatch":      self.device_mismatch,
            "identity_risk_score":  self.score,
            "ip_changed":           False,   # caller must set from network layer
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

    Score formula:
        Each CRITICAL issue adds 0.30 to score (capped at 1.0).
        Each WARNING  issue adds 0.10 to score (capped at 1.0).

    Breakdown splits score by category (navigator / webgl / timing / behavioral).
    """
    breakdown: dict[str, float] = {
        "navigator": 0.0, "webgl": 0.0,
        "timing": 0.0,    "behavioral": 0.0,
    }
    total = 0.0
    codes = {i.code for i in issues}

    for issue in issues:
        weight = _CRITICAL_WEIGHT if issue.severity == "CRITICAL" else _WARNING_WEIGHT
        total += weight
        cat = _CODE_CATEGORY.get(issue.code, "behavioral")
        breakdown[cat] = min(1.0, breakdown.get(cat, 0.0) + weight)

    score = min(1.0, total)

    fingerprint_changed = (
        "WEBDRIVER_EXPOSED" in codes
        or "WEBGLVENDOR_MISMATCH" in codes
        or "PLATFORM_MISMATCH" in codes
    )
    geo_mismatch   = "TIMEZONE_MISMATCH" in codes or "LANGUAGE_MISMATCH" in codes
    device_mismatch = "SCREEN_MISMATCH" in codes or "HARDWARECONCURRENCY_MISMATCH" in codes

    return FingerprintRiskResult(
        score=score,
        issues=issues,
        breakdown=breakdown,
        fingerprint_changed=fingerprint_changed,
        geo_mismatch=geo_mismatch,
        device_mismatch=device_mismatch,
        identity_risk_score=score,
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
