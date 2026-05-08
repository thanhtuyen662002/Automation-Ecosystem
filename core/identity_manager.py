"""
Identity Manager — stable digital persona per account.

Each account gets ONE identity generated deterministically from its account_id.
Identities are NEVER randomly rotated. Changes require explicit operator action.

Architecture:
    IdentityRegistry (singleton)
        └── IdentityProfile (per account)
                ├── device_profile  (OS, browser, screen, user_agent)
                ├── network_profile (proxy_url, proxy_country)
                ├── locale_profile  (timezone, locale)
                └── fingerprint_hash (stable, deterministic)

Integration:
    from core.identity_manager import get_identity_registry

    reg = get_identity_registry()
    profile = reg.get_or_create(account_id, proxy_url=..., proxy_country=...)
    issues  = reg.validate(account_id, runtime_env={"ip_changed": False})
    # critical issues → force SAFE MODE in AccountBrain
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

LOGGER = logging.getLogger("core.identity_manager")

# ─────────────────────────────────────────────────────────────────────────────
# Device pool — realistic, production user agents
# ─────────────────────────────────────────────────────────────────────────────

_DEVICE_POOL: list[dict[str, str]] = [
    {"device_type": "mobile", "os": "iOS 17.4", "browser": "Safari", "browser_version": "17.4",
     "screen_resolution": "390x844",
     "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"},
    {"device_type": "mobile", "os": "iOS 16.7", "browser": "Safari", "browser_version": "16.6",
     "screen_resolution": "375x812",
     "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"},
    {"device_type": "mobile", "os": "Android 14", "browser": "Chrome", "browser_version": "124",
     "screen_resolution": "412x915",
     "user_agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"},
    {"device_type": "mobile", "os": "Android 13", "browser": "Chrome", "browser_version": "122",
     "screen_resolution": "360x780",
     "user_agent": "Mozilla/5.0 (Linux; Android 13; SM-A546B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36"},
    {"device_type": "mobile", "os": "Android 12", "browser": "Chrome", "browser_version": "120",
     "screen_resolution": "393x851",
     "user_agent": "Mozilla/5.0 (Linux; Android 12; SAMSUNG SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/23.0 Chrome/115.0.0.0 Mobile Safari/537.36"},
    {"device_type": "desktop", "os": "Windows 11", "browser": "Chrome", "browser_version": "124",
     "screen_resolution": "1920x1080",
     "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"},
    {"device_type": "desktop", "os": "Windows 10", "browser": "Chrome", "browser_version": "122",
     "screen_resolution": "1366x768",
     "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"},
    {"device_type": "desktop", "os": "macOS 14.4", "browser": "Safari", "browser_version": "17.4",
     "screen_resolution": "2560x1600",
     "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"},
    {"device_type": "desktop", "os": "macOS 13.6", "browser": "Chrome", "browser_version": "123",
     "screen_resolution": "1440x900",
     "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"},
]

_LOCALE_POOL: list[str] = [
    "vi-VN", "en-US", "en-GB", "th-TH", "id-ID",
    "zh-TW", "ja-JP", "ko-KR", "de-DE", "fr-FR",
]

_TIMEZONE_POOL: list[str] = [
    "Asia/Ho_Chi_Minh", "America/New_York", "America/Los_Angeles",
    "Europe/London", "Asia/Bangkok", "Asia/Jakarta",
    "Asia/Taipei", "Asia/Tokyo", "Asia/Seoul", "Europe/Berlin",
]

# Geographic alignment maps
_TIMEZONE_TO_COUNTRY: dict[str, str] = {
    "Asia/Ho_Chi_Minh": "VN", "Asia/Bangkok": "TH", "Asia/Jakarta": "ID",
    "Asia/Taipei": "TW",      "Asia/Tokyo": "JP",   "Asia/Seoul": "KR",
    "Asia/Singapore": "SG",   "Asia/Shanghai": "CN","Asia/Hong_Kong": "HK",
    "America/New_York": "US", "America/Los_Angeles": "US", "America/Chicago": "US",
    "Europe/London": "GB",    "Europe/Berlin": "DE", "Europe/Paris": "FR",
    "Australia/Sydney": "AU",
}

_LOCALE_TO_COUNTRY: dict[str, str] = {
    "vi-VN": "VN", "th-TH": "TH", "id-ID": "ID", "zh-TW": "TW",
    "ja-JP": "JP", "ko-KR": "KR", "de-DE": "DE", "fr-FR": "FR",
    "en-US": "US", "en-GB": "GB", "en-AU": "AU",  "zh-CN": "CN",
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _stable_seed(account_id: str) -> int:
    digest = hashlib.sha256(account_id.encode()).hexdigest()
    return int(digest[:16], 16)


def _seeded_choice(seed: int, index: int, pool_size: int) -> int:
    h = hashlib.sha256(f"{seed}:{index}".encode()).hexdigest()
    return int(h[:8], 16) % pool_size


def _seeded_int(seed: int, index: int, lo: int, hi: int) -> int:
    h = hashlib.sha256(f"{seed}:{index}".encode()).hexdigest()
    unit = int(h[:8], 16) / 0xFFFFFFFF
    return lo + int(unit * (hi - lo + 1))


# ─────────────────────────────────────────────────────────────────────────────
# IdentityProfile
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class IdentityProfile:
    """Stable digital persona for one account.

    Generated ONCE per account from a deterministic seed.
    The base identity (device, OS, locale, timezone) is IMMUTABLE.
    Only noise seeds and active_fingerprint evolve through controlled mutations.
    """

    account_id: str

    # Device
    device_type: str        # "mobile" | "desktop"
    os: str
    browser: str
    browser_version: str
    screen_resolution: str
    user_agent: str

    # Network / locale
    timezone: str
    locale: str
    proxy_url: str | None
    proxy_country: str | None   # ISO-2 code, e.g. "VN"

    # Fingerprint (mutable surface — noise seeds only)
    fingerprint_hash: str       # mirrors active_fingerprint; kept for backward compat
    canvas_noise_seed: int      # mutated by MutationController for canvas noise drift
    webgl_noise_seed: int       # mutated by MutationController for GPU rotation

    # State
    created_at: float
    last_seen_at: float | None = None
    is_locked: bool = False     # if True, regenerate() is blocked
    identity_risk_score: float = 0.0   # 0=clean 1=high risk; updated by brain

    # ── Stateful anti-detect fields ──────────────────────────────────────────
    identity_id: str = ""              # Immutable SHA-256 prefix derived from account_id
    base_fingerprint: str = ""         # Locked at creation; NEVER mutated
    active_fingerprint: str = ""       # Runtime fingerprint; drifts on mutation
    mutation_state: int = 0            # Increments on every HIGH-risk mutation
    mutation_history: list = field(default_factory=list)   # Last 20 mutation records
    risk_history: list = field(default_factory=list)       # Last 10 risk score records

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id":          self.account_id,
            "device_type":         self.device_type,
            "os":                  self.os,
            "browser":             self.browser,
            "browser_version":     self.browser_version,
            "screen_resolution":   self.screen_resolution,
            "user_agent":          self.user_agent,
            "timezone":            self.timezone,
            "locale":              self.locale,
            "proxy_url":           self.proxy_url,
            "proxy_country":       self.proxy_country,
            "fingerprint_hash":    self.fingerprint_hash,
            "canvas_noise_seed":   self.canvas_noise_seed,
            "webgl_noise_seed":    self.webgl_noise_seed,
            "created_at":          self.created_at,
            "last_seen_at":        self.last_seen_at,
            "is_locked":           self.is_locked,
            "identity_risk_score": round(self.identity_risk_score, 4),
            # Stateful fields
            "identity_id":         self.identity_id,
            "base_fingerprint":    self.base_fingerprint,
            "active_fingerprint":  self.active_fingerprint,
            "mutation_state":      self.mutation_state,
            "mutation_history":    self.mutation_history,
            "risk_history":        self.risk_history,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "IdentityProfile":
        return cls(
            account_id=d["account_id"],
            device_type=d["device_type"],
            os=d["os"],
            browser=d["browser"],
            browser_version=d["browser_version"],
            screen_resolution=d["screen_resolution"],
            user_agent=d["user_agent"],
            timezone=d["timezone"],
            locale=d["locale"],
            proxy_url=d.get("proxy_url"),
            proxy_country=d.get("proxy_country"),
            fingerprint_hash=d["fingerprint_hash"],
            canvas_noise_seed=int(d["canvas_noise_seed"]),
            webgl_noise_seed=int(d["webgl_noise_seed"]),
            created_at=float(d["created_at"]),
            last_seen_at=d.get("last_seen_at"),
            is_locked=bool(d.get("is_locked", False)),
            identity_risk_score=float(d.get("identity_risk_score", 0.0)),
            # Stateful fields — graceful defaults for pre-refactor serialized profiles
            identity_id=d.get("identity_id", ""),
            base_fingerprint=d.get("base_fingerprint", d["fingerprint_hash"]),
            active_fingerprint=d.get("active_fingerprint", d["fingerprint_hash"]),
            mutation_state=int(d.get("mutation_state", 0)),
            mutation_history=list(d.get("mutation_history", [])),
            risk_history=list(d.get("risk_history", [])),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Fingerprint engine
# ─────────────────────────────────────────────────────────────────────────────

def generate_fingerprint(profile: IdentityProfile) -> str:
    """Deterministic fingerprint hash from identity fields.

    Combines user_agent + screen + timezone + locale + noise seeds.
    Result: 32-char hex — unique per account, stable across sessions.
    Not obviously synthetic because seeds come from realistic device data.
    """
    components = [
        profile.user_agent,
        profile.screen_resolution,
        profile.timezone,
        profile.locale,
        str(profile.canvas_noise_seed),
        str(profile.webgl_noise_seed),
    ]
    return hashlib.sha256("|".join(components).encode()).hexdigest()[:32]


def generate_identity_profile(
    account_id: str,
    proxy_url: str | None = None,
    proxy_country: str | None = None,
) -> IdentityProfile:
    """Create a stable identity for an account from its account_id seed.

    Call once per account; result should be persisted. The same account_id
    always produces the same device/locale/fingerprint (deterministic).
    """
    seed = _stable_seed(account_id)

    # Pick device from pool
    device_idx = _seeded_choice(seed, 0, len(_DEVICE_POOL))
    device = _DEVICE_POOL[device_idx]

    # Pick locale and timezone
    locale_idx = _seeded_choice(seed, 1, len(_LOCALE_POOL))
    locale = _LOCALE_POOL[locale_idx]

    # Align timezone to locale country where possible, otherwise pick independently
    locale_country = _LOCALE_TO_COUNTRY.get(locale)
    tz_candidates = [tz for tz, c in _TIMEZONE_TO_COUNTRY.items() if c == locale_country]
    if not tz_candidates:
        tz_idx = _seeded_choice(seed, 2, len(_TIMEZONE_POOL))
        timezone = _TIMEZONE_POOL[tz_idx]
    else:
        tz_idx = _seeded_choice(seed, 2, len(tz_candidates))
        timezone = tz_candidates[tz_idx]

    # Noise seeds for browser spoofing (Playwright inject)
    canvas_seed = _seeded_int(seed, 10, 100_000, 999_999)
    webgl_seed  = _seeded_int(seed, 11, 100_000, 999_999)

    profile = IdentityProfile(
        account_id=account_id,
        device_type=device["device_type"],
        os=device["os"],
        browser=device["browser"],
        browser_version=device["browser_version"],
        screen_resolution=device["screen_resolution"],
        user_agent=device["user_agent"],
        timezone=timezone,
        locale=locale,
        proxy_url=proxy_url,
        proxy_country=proxy_country,
        fingerprint_hash="",        # filled below
        canvas_noise_seed=canvas_seed,
        webgl_noise_seed=webgl_seed,
        created_at=time.time(),
    )
    fp = generate_fingerprint(profile)
    profile.fingerprint_hash   = fp
    # Stateful fields — set once, never overwritten by normal flow
    profile.identity_id        = hashlib.sha256(f"{account_id}:identity".encode()).hexdigest()[:16]
    profile.base_fingerprint   = fp   # locked forever
    profile.active_fingerprint = fp   # starts equal to base
    return profile


# ─────────────────────────────────────────────────────────────────────────────
# Consistency checker
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ConsistencyIssue:
    code: str
    severity: str    # "WARNING" | "CRITICAL"
    message: str
    field: str


def check_identity_consistency(
    profile: IdentityProfile,
    runtime_env: dict[str, Any] | None = None,
) -> list[ConsistencyIssue]:
    """Validate that the profile's fields are internally consistent.

    Args:
        profile:     The stored identity profile.
        runtime_env: Optional runtime signals, e.g.:
                     {"ip_changed": bool, "current_fingerprint": str,
                      "geo_mismatch": bool, "ip_rotation_count": int}

    Returns:
        List of ConsistencyIssue. CRITICAL issues should trigger SAFE MODE.
    """
    issues: list[ConsistencyIssue] = []

    # 1. Timezone vs proxy country
    tz_country = _TIMEZONE_TO_COUNTRY.get(profile.timezone)
    if tz_country and profile.proxy_country and tz_country != profile.proxy_country:
        issues.append(ConsistencyIssue(
            code="TZ_PROXY_MISMATCH", severity="CRITICAL", field="timezone",
            message=f"Timezone {profile.timezone!r} expects country {tz_country!r} but proxy is in {profile.proxy_country!r}",
        ))

    # 2. Locale vs proxy country
    locale_country = _LOCALE_TO_COUNTRY.get(profile.locale)
    if locale_country and profile.proxy_country and locale_country != profile.proxy_country:
        issues.append(ConsistencyIssue(
            code="LOCALE_PROXY_MISMATCH", severity="WARNING", field="locale",
            message=f"Locale {profile.locale!r} suggests country {locale_country!r} but proxy is {profile.proxy_country!r}",
        ))

    # 3. User-agent vs device_type mismatch
    ua_is_mobile = any(kw in profile.user_agent for kw in ("Mobile", "Android", "iPhone", "iPad"))
    if profile.device_type == "mobile" and not ua_is_mobile:
        issues.append(ConsistencyIssue(
            code="UA_DEVICE_MISMATCH", severity="WARNING", field="user_agent",
            message=f"device_type=mobile but user_agent lacks mobile indicators",
        ))
    if profile.device_type == "desktop" and ua_is_mobile:
        issues.append(ConsistencyIssue(
            code="UA_DEVICE_MISMATCH", severity="WARNING", field="user_agent",
            message=f"device_type=desktop but user_agent contains mobile indicators",
        ))

    if runtime_env:
        # 4. IP rotation
        if runtime_env.get("ip_changed"):
            issues.append(ConsistencyIssue(
                code="IP_CHANGED", severity="WARNING", field="proxy_url",
                message="IP address changed since last session",
            ))
        rotation_count = runtime_env.get("ip_rotation_count", 0)
        if isinstance(rotation_count, int) and rotation_count >= 3:
            issues.append(ConsistencyIssue(
                code="IP_ROTATION_FREQUENT", severity="CRITICAL", field="proxy_url",
                message=f"IP rotated {rotation_count}x — suspicious churn pattern",
            ))

        # 5. Fingerprint drift
        current_fp = runtime_env.get("current_fingerprint")
        if current_fp and current_fp != profile.fingerprint_hash:
            issues.append(ConsistencyIssue(
                code="FINGERPRINT_DRIFT", severity="CRITICAL", field="fingerprint_hash",
                message=f"Stored fingerprint {profile.fingerprint_hash[:8]}… does not match runtime {current_fp[:8]}…",
            ))

        # 6. Geo mismatch from platform detection
        if runtime_env.get("geo_mismatch"):
            issues.append(ConsistencyIssue(
                code="GEO_MISMATCH", severity="CRITICAL", field="proxy_country",
                message="Platform detected geo inconsistency between declared locale and connection origin",
            ))

    return issues


def identity_risk_from_issues(issues: list[ConsistencyIssue]) -> float:
    """Derive a 0–1 risk score from consistency issues."""
    if not issues:
        return 0.0
    score = 0.0
    for issue in issues:
        score += 0.25 if issue.severity == "CRITICAL" else 0.08
    return min(1.0, score)


# ─────────────────────────────────────────────────────────────────────────────
# IdentityRegistry — process-level singleton
# ─────────────────────────────────────────────────────────────────────────────

class IdentityRegistry:
    """Process-level store for all account identity profiles.

    Identities are immutable once created unless explicitly regenerated.
    Regeneration is dangerous and should be logged as a critical event.
    """

    def __init__(self) -> None:
        self._profiles: dict[str, IdentityProfile] = {}

    def get_or_create(
        self,
        account_id: str,
        proxy_url: str | None = None,
        proxy_country: str | None = None,
    ) -> IdentityProfile:
        """Return existing profile or generate a new stable one."""
        if account_id not in self._profiles:
            profile = generate_identity_profile(account_id, proxy_url, proxy_country)
            self._profiles[account_id] = profile
            LOGGER.info("identity_created", extra={
                "event": "identity_created",
                "account_id": account_id,
                "device_type": profile.device_type,
                "os": profile.os,
                "browser": profile.browser,
                "timezone": profile.timezone,
                "locale": profile.locale,
                "fingerprint_hash": profile.fingerprint_hash,
            })
        return self._profiles[account_id]

    def get(self, account_id: str) -> IdentityProfile | None:
        return self._profiles.get(account_id)

    def regenerate(
        self,
        account_id: str,
        proxy_url: str | None = None,
        proxy_country: str | None = None,
    ) -> IdentityProfile:
        """Force-regenerate identity. DANGEROUS — changes fingerprint.

        Blocked if is_locked=True. This operation always degrades trust_score
        in AccountBrain because a real human doesn't change devices.
        """
        existing = self._profiles.get(account_id)
        if existing and existing.is_locked:
            raise ValueError(f"Account {account_id} identity is locked — unlock first")

        # Use a time-salted seed so the new identity is different from the old one
        salt = str(int(time.time()))
        salted_id = f"{account_id}:{salt}"
        new_profile = generate_identity_profile(salted_id, proxy_url, proxy_country)
        new_profile.account_id = account_id  # restore correct account_id
        new_profile.fingerprint_hash = generate_fingerprint(new_profile)  # recompute
        self._profiles[account_id] = new_profile

        LOGGER.warning("identity_regenerated", extra={
            "event": "identity_regenerated",
            "account_id": account_id,
            "new_fingerprint": new_profile.fingerprint_hash,
            "old_fingerprint": existing.fingerprint_hash if existing else None,
        })
        return new_profile

    def lock(self, account_id: str) -> None:
        """Prevent regeneration of this account's identity."""
        profile = self.get_or_create(account_id)
        profile.is_locked = True
        LOGGER.info("identity_locked", extra={"event": "identity_locked", "account_id": account_id})

    def unlock(self, account_id: str) -> None:
        profile = self.get_or_create(account_id)
        profile.is_locked = False

    def update_proxy(
        self,
        account_id: str,
        proxy_url: str,
        proxy_country: str,
    ) -> IdentityProfile:
        """Update proxy fields without changing the fingerprint or device."""
        profile = self.get_or_create(account_id)
        profile.proxy_url = proxy_url
        profile.proxy_country = proxy_country
        LOGGER.info("identity_proxy_updated", extra={
            "event": "identity_proxy_updated",
            "account_id": account_id,
            "proxy_country": proxy_country,
        })
        return profile

    def validate(
        self,
        account_id: str,
        runtime_env: dict[str, Any] | None = None,
    ) -> list[ConsistencyIssue]:
        """Run consistency checks and update identity_risk_score."""
        profile = self.get_or_create(account_id)
        issues = check_identity_consistency(profile, runtime_env)
        profile.identity_risk_score = identity_risk_from_issues(issues)
        profile.last_seen_at = time.time()

        for issue in issues:
            lvl = LOGGER.critical if issue.severity == "CRITICAL" else LOGGER.warning
            lvl("identity_issue", extra={
                "event": "identity_issue",
                "account_id": account_id,
                "code": issue.code,
                "severity": issue.severity,
                "message": issue.message,
                "field": issue.field,
            })
        return issues

    def snapshot_all(self) -> list[dict[str, Any]]:
        result = []
        for account_id, profile in self._profiles.items():
            d = profile.to_dict()
            issues = check_identity_consistency(profile)
            d["consistency_issues"] = [
                {"code": i.code, "severity": i.severity, "message": i.message, "field": i.field}
                for i in issues
            ]
            d["has_critical_issues"] = any(i.severity == "CRITICAL" for i in issues)
            result.append(d)
        return result

    def snapshot(self, account_id: str) -> dict[str, Any] | None:
        profile = self._profiles.get(account_id)
        if not profile:
            return None
        d = profile.to_dict()
        issues = check_identity_consistency(profile)
        d["consistency_issues"] = [
            {"code": i.code, "severity": i.severity, "message": i.message, "field": i.field}
            for i in issues
        ]
        d["has_critical_issues"] = any(i.severity == "CRITICAL" for i in issues)
        return d

    def load_profiles(self, data: dict[str, dict[str, Any]]) -> None:
        """Restore profiles from external persistence (first-wins)."""
        for account_id, raw in data.items():
            if account_id not in self._profiles:
                try:
                    self._profiles[account_id] = IdentityProfile.from_dict(raw)
                except Exception as exc:
                    LOGGER.warning("identity_load_failed", extra={
                        "event": "identity_load_failed",
                        "account_id": account_id,
                        "error": str(exc),
                    })

    def dump_profiles(self) -> dict[str, dict[str, Any]]:
        return {aid: p.to_dict() for aid, p in self._profiles.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_IDENTITY_REGISTRY: IdentityRegistry | None = None


def get_identity_registry() -> IdentityRegistry:
    """Return the process-level IdentityRegistry singleton."""
    global _IDENTITY_REGISTRY
    if _IDENTITY_REGISTRY is None:
        _IDENTITY_REGISTRY = IdentityRegistry()
        LOGGER.info("identity_registry_initialised", extra={"event": "identity_registry_initialised"})
    return _IDENTITY_REGISTRY
