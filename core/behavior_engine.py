"""
Hybrid Behavior Engine — controlled, detectable-risk-aware behavior simulation.

Design contract:
  - DETERMINISTIC per account: same account_id + day_of_week → same personality
  - SESSION-LEVEL VARIANCE: ±10% jitter applied once per session build
  - RISK-CONSTRAINED: every behavioral parameter is gated by risk_score,
    soft_ban state, proxy latency, and warmup progress
  - NO GLOBAL PATTERNS: per-account noise layer prevents cross-account timing correlation

NOT a perfect human simulator. Goal: reduce ban WAVES, not just delay them.

Integration:
    from core.behavior_engine import build_session_personality, BehaviorEngine

    personality = build_session_personality(account_id, account_data)
    engine = BehaviorEngine(personality)

    await engine.action_delay()
    await engine.typing_delay(char_count=len(caption))
    await engine.simulate_scroll(page)
    await engine.simulate_mouse_move(page)
    skip_decision = engine.should_skip_upload(account_data)
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

LOGGER = logging.getLogger("core.behavior_engine")

# ── Typing literal types ──────────────────────────────────────────────────────

ActivityLevel = Literal["low", "medium", "high"]
SkipDecision  = Literal["skip", "proceed"]

# ── Constants ─────────────────────────────────────────────────────────────────

# Risk thresholds
RISK_HIGH   = 0.6   # Triggers passive-only mode
RISK_MEDIUM = 0.35  # Triggers cautious mode
RISK_LOW    = 0.15  # Normal operation

# Proxy latency thresholds (ms)
PROXY_SLOW_MS   = 2500
PROXY_DANGER_MS = 5000

# Warmup gate — must have at least this many completed sessions to be interactive
WARMUP_GATE = 2

# Per-account time jitter on job start: uniform range in seconds
JOB_START_JITTER_LO = 30
JOB_START_JITTER_HI = 120


# ─────────────────────────────────────────────────────────────────────────────
# Stable seed derivation
# ─────────────────────────────────────────────────────────────────────────────

def _stable_seed(account_id: str, day_of_week: int | None = None) -> int:
    """Derive a stable integer seed from account_id + day_of_week.

    Using day_of_week prevents the seed from being purely static (which would
    produce exactly-identical session cadence every day), while still being
    deterministic enough for consistency within a day.

    day_of_week: 0=Monday … 6=Sunday. Defaults to today (UTC).
    """
    if day_of_week is None:
        # Use UTC weekday so personality is consistent regardless of server timezone.
        day_of_week = datetime.now(timezone.utc).weekday()
    raw = f"{account_id}:dow={day_of_week}"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return int(digest[:16], 16)  # 64-bit int


def _seeded_float(seed: int, index: int, lo: float = 0.0, hi: float = 1.0) -> float:
    """Deterministically pick a float in [lo, hi) from the seed + an index."""
    # Hash seed with index for independent draws
    h = hashlib.sha256(f"{seed}:{index}".encode()).hexdigest()
    unit = int(h[:8], 16) / 0xFFFFFFFF  # [0, 1)
    return lo + unit * (hi - lo)


# ─────────────────────────────────────────────────────────────────────────────
# SessionPersonality — immutable after construction
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SessionPersonality:
    """Per-account session identity. Built once; held for the session lifetime.

    All fields are deterministic (seeded) with a small session-level jitter
    applied at construction time. Personality does NOT change mid-session.
    """

    account_id: str

    # Activity level drives how many actions occur per session
    activity_level: ActivityLevel          # "low" | "medium" | "high"

    # Multiplier on all delay timings: >1 = slower/more hesitant, <1 = faster
    hesitation_factor: float               # 0.7 – 1.6

    # Fraction of page scrolled per scroll event [0.2, 1.0]
    scroll_depth_preference: float

    # Fraction of visible items interacted with [0.01, 0.12]
    interaction_rate: float

    # Base ms-per-character for typing [80, 220]
    typing_speed_base: float

    # Target minutes to stay active in a session [3, 18]
    session_duration_target: float

    # Seed stored for debugging / audit
    _seed: int = field(repr=False)

    # Session-level jitter multiplier [0.90, 1.10]
    _session_jitter: float = field(repr=False)

    def summary(self) -> dict[str, Any]:
        """Return loggable dict of personality traits."""
        return {
            "account_id": self.account_id,
            "activity_level": self.activity_level,
            "hesitation_factor": round(self.hesitation_factor, 3),
            "scroll_depth_preference": round(self.scroll_depth_preference, 3),
            "interaction_rate": round(self.interaction_rate, 4),
            "typing_speed_base_ms": round(self.typing_speed_base, 1),
            "session_duration_target_min": round(self.session_duration_target, 1),
            "session_jitter": round(self._session_jitter, 3),
        }


def build_session_personality(
    account_id: str,
    day_of_week: int | None = None,
) -> SessionPersonality:
    """Build a SessionPersonality deterministically from account_id + day.

    Adds ±10% session-level jitter using a separate fresh-random draw so the
    same account on the same day can still exhibit slight inter-session variance
    (e.g. sometimes a bit faster, sometimes a bit slower) — without invalidating
    the stable base identity.
    """
    seed = _stable_seed(account_id, day_of_week)

    # ── Base personality draws (stable) ───────────────────────────────────────
    activity_raw = _seeded_float(seed, 0, 0.0, 1.0)
    if activity_raw < 0.33:
        activity_level: ActivityLevel = "low"
    elif activity_raw < 0.70:
        activity_level = "medium"
    else:
        activity_level = "high"

    hesitation_factor      = _seeded_float(seed, 1, 0.70, 1.60)
    scroll_depth_pref      = _seeded_float(seed, 2, 0.20, 1.00)
    interaction_rate       = _seeded_float(seed, 3, 0.01, 0.12)
    typing_speed_base      = _seeded_float(seed, 4, 80.0, 220.0)
    session_duration_min   = _seeded_float(seed, 5, 5.0, 18.0)

    # ── Session jitter (fresh per session — not seeded) ───────────────────────
    session_jitter = random.uniform(0.90, 1.10)

    return SessionPersonality(
        account_id=account_id,
        activity_level=activity_level,
        hesitation_factor=hesitation_factor * session_jitter,
        scroll_depth_preference=scroll_depth_pref,
        interaction_rate=interaction_rate,
        typing_speed_base=typing_speed_base * session_jitter,
        session_duration_target=session_duration_min,
        _seed=seed,
        _session_jitter=session_jitter,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Delay functions — non-uniform distributions
# ─────────────────────────────────────────────────────────────────────────────

def _lognormal_delay_seconds(base: float, variance: float) -> float:
    """Sample from log-normal distribution.

    Produces right-skewed delays that mirror real human reaction times:
    median ≈ base seconds, with occasional long tail pauses.

    Args:
        base: Desired median in seconds.
        variance: σ of the underlying normal (larger → more right-skewed).

    Returns:
        Delay in seconds (always > 0).
    """
    # log-normal: X = e^(μ + σ·Z), where μ = ln(base)
    mu = math.log(max(base, 0.01))
    return math.exp(random.gauss(mu, variance))


async def lognormal_delay(base: float, variance: float = 0.35) -> None:
    """Async sleep with log-normal duration.

    Unlike gaussian_delay, the log-normal never goes negative and produces a
    realistic right-skewed distribution (short waits are most common, with
    occasional longer pauses — matching real human hesitation patterns).
    """
    secs = _lognormal_delay_seconds(base, variance)
    await asyncio.sleep(secs)


async def burst_pause(
    personality: SessionPersonality,
    fast_actions_done: int,
) -> None:
    """After a burst of fast actions, decide if a longer pause is warranted.

    Pattern: 3–6 fast actions → 8–25 s pause (burst_pause).
    The burst window and pause probability are modulated by hesitation_factor.

    Args:
        personality: Session personality for this account.
        fast_actions_done: How many fast actions have been taken since last pause.
    """
    burst_window = round(random.uniform(3, 6) * personality.hesitation_factor)
    if fast_actions_done >= burst_window:
        pause_secs = random.uniform(8.0, 25.0) * personality.hesitation_factor
        LOGGER.debug(
            "burst_pause_triggered",
            extra={
                "event": "burst_pause_triggered",
                "account_id": personality.account_id,
                "fast_actions_done": fast_actions_done,
                "pause_secs": round(pause_secs, 1),
            },
        )
        await asyncio.sleep(pause_secs)


# ─────────────────────────────────────────────────────────────────────────────
# Risk-aware constraint resolver
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BehaviorConstraints:
    """Runtime constraints derived from account risk signals.

    Computed once at the start of a session; held for the session lifetime.

    lifecycle_phase: LifecyclePhase string from LifecycleManager.snapshot().
        - "COOLDOWN" forces passive_mode=True regardless of risk_score.
        - "WARM_UP"  forces passive_mode=True (no interactive actions).
    session_duration_ceiling_min: Hard ceiling from SessionPlan.session_duration_min.
        When > 0, session_duration_target is capped to this value.
    """
    risk_score: float              = 0.0
    soft_ban_detected: bool        = False
    proxy_latency_ms: int          = 0
    warmup_sessions_completed: int = 0   # legacy — prefer lifecycle_phase
    lifecycle_phase: str           = "NORMAL"  # NEW: from LifecycleManager
    session_duration_ceiling_min: float = 0.0  # NEW: from SessionPlan (0 = no ceiling)

    # Derived fields (computed in __post_init__)
    delay_multiplier: float        = field(init=False)
    passive_mode: bool             = field(init=False)   # No interactive actions
    minimal_mode: bool             = field(init=False)   # Near-zero activity
    warmup_required: bool          = field(init=False)

    def __post_init__(self) -> None:
        proxy_bad = self.proxy_latency_ms > PROXY_DANGER_MS and self.proxy_latency_ms != -1

        # COOLDOWN and WARM_UP always force passive mode — lifecycle overrides risk_score
        phase_passive = self.lifecycle_phase in ("COOLDOWN", "WARM_UP")

        if self.soft_ban_detected:
            self.delay_multiplier = 2.5
            self.passive_mode     = True
            self.minimal_mode     = False
        elif phase_passive:
            # COOLDOWN: 2x delay + passive. WARM_UP: 1.5x delay + passive.
            self.delay_multiplier = 2.0 if self.lifecycle_phase == "COOLDOWN" else 1.5
            self.passive_mode     = True
            self.minimal_mode     = False
        elif proxy_bad:
            self.delay_multiplier = 1.0
            self.passive_mode     = False
            self.minimal_mode     = True   # Abort / do nothing
        elif self.risk_score >= RISK_HIGH:
            self.delay_multiplier = 2.0
            self.passive_mode     = True
            self.minimal_mode     = False
        elif self.risk_score >= RISK_MEDIUM:
            self.delay_multiplier = 1.4
            self.passive_mode     = False
            self.minimal_mode     = False
        else:
            self.delay_multiplier = 1.0
            self.passive_mode     = False
            self.minimal_mode     = False

        # warmup_required: prefer lifecycle_phase over legacy session count
        if self.lifecycle_phase in ("WARM_UP", "RAMP_UP"):
            self.warmup_required = True
        else:
            self.warmup_required = self.warmup_sessions_completed < WARMUP_GATE

    def apply_session_ceiling(self, personality: "SessionPersonality") -> float:
        """Return session_duration_target capped to the session ceiling.

        Call this instead of reading personality.session_duration_target directly.
        Enforces the hard 20-min ceiling from AccountBrain's SessionPlan.
        """
        target = personality.session_duration_target
        if self.session_duration_ceiling_min > 0:
            target = min(target, self.session_duration_ceiling_min)
        return target

    def summary(self) -> dict[str, Any]:
        return {
            "risk_score":                   round(self.risk_score, 3),
            "soft_ban_detected":            self.soft_ban_detected,
            "proxy_latency_ms":             self.proxy_latency_ms,
            "lifecycle_phase":              self.lifecycle_phase,
            "session_duration_ceiling_min": self.session_duration_ceiling_min,
            "derived.delay_multiplier":     round(self.delay_multiplier, 2),
            "derived.passive_mode":         self.passive_mode,
            "derived.minimal_mode":         self.minimal_mode,
            "derived.warmup_required":      self.warmup_required,
        }


def build_constraints(
    account_data: dict[str, Any],
    proxy_latency_ms: int = 0,
    lifecycle_phase: str = "NORMAL",
    session_duration_ceiling_min: float = 0.0,
) -> BehaviorConstraints:
    """Build BehaviorConstraints from account dict + lifecycle context.

    Args:
        account_data:                 Raw account dict from DB.
        proxy_latency_ms:             Current proxy latency measurement.
        lifecycle_phase:              Current phase from LifecycleManager.snapshot().
        session_duration_ceiling_min: Hard ceiling from SessionPlan.session_duration_min.
    """
    return BehaviorConstraints(
        risk_score=float(account_data.get("risk_score") or 0.0),
        soft_ban_detected=bool(account_data.get("soft_ban_detected", False)),
        proxy_latency_ms=proxy_latency_ms,
        warmup_sessions_completed=int(account_data.get("warmup_sessions_completed") or 0),
        lifecycle_phase=lifecycle_phase,
        session_duration_ceiling_min=session_duration_ceiling_min,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Bezier mouse curve helper
# ─────────────────────────────────────────────────────────────────────────────

def _bezier_cubic(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    t: float,
) -> tuple[float, float]:
    """Evaluate a cubic Bezier curve at parameter t ∈ [0, 1]."""
    mt = 1 - t
    x = mt**3*p0[0] + 3*mt**2*t*p1[0] + 3*mt*t**2*p2[0] + t**3*p3[0]
    y = mt**3*p0[1] + 3*mt**2*t*p1[1] + 3*mt*t**2*p2[1] + t**3*p3[1]
    return x, y


def _bezier_path(
    start: tuple[float, float],
    end: tuple[float, float],
    jitter: float = 40.0,
    steps: int = 12,
) -> list[tuple[float, float]]:
    """Generate a curved mouse path using a cubic Bezier with randomised control points.

    The control points are placed with ±jitter random offsets from the midpoint,
    producing a natural-looking curved path rather than a straight line.
    """
    mx = (start[0] + end[0]) / 2
    my = (start[1] + end[1]) / 2
    p1 = (mx + random.uniform(-jitter, jitter), my + random.uniform(-jitter, jitter))
    p2 = (mx + random.uniform(-jitter, jitter), my + random.uniform(-jitter, jitter))
    return [
        _bezier_cubic(start, p1, p2, end, t / steps)
        for t in range(steps + 1)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# BehaviorEngine — the main session-level execution controller
# ─────────────────────────────────────────────────────────────────────────────

class BehaviorEngine:
    """Stateful session controller that applies risk-constrained human-like behavior.

    Holds:
      - personality: stable per-account identity
      - constraints: runtime risk signals
      - _fast_action_count: burst pause tracker

    Thread / coroutine safety: NOT shared across concurrent tasks.
    Create one instance per publish session.
    """

    def __init__(
        self,
        personality: SessionPersonality,
        constraints: BehaviorConstraints,
    ) -> None:
        self.personality  = personality
        self.constraints  = constraints
        self._fast_action_count = 0
        self._session_start = time.monotonic()

    # ── Delay primitives ──────────────────────────────────────────────────────

    async def action_delay(self) -> None:
        """Standard between-action pause, shaped by personality + risk.

        Uses lognormal distribution (replaces gaussian_delay from browser_context).
        Base ≈ 2.0s, scaled by hesitation_factor and risk delay_multiplier.
        """
        base = 2.0 * self.personality.hesitation_factor * self.constraints.delay_multiplier
        await lognormal_delay(base, variance=0.30)
        self._fast_action_count += 1
        await burst_pause(self.personality, self._fast_action_count)

    async def short_delay(self) -> None:
        """Short post-click pause. Lognormal centred ~0.8s."""
        base = 0.8 * self.personality.hesitation_factor * self.constraints.delay_multiplier
        await lognormal_delay(base, variance=0.25)

    async def warmup_delay(self) -> None:
        """Feed-viewing warmup pause. Lognormal centred ~15s, right-tailed."""
        base = 15.0 * self.personality.hesitation_factor * self.constraints.delay_multiplier
        await lognormal_delay(base, variance=0.25)

    async def typing_delay(self, char_count: int) -> None:
        """Simulate per-character typing with occasional mid-typing pauses.

        Pattern:
          - Each char: typing_speed_base ms (lognormal)
          - 5–10% chance of a 0.5–1.5s thinking pause between chars
          - Total time is proportional to char_count × typing_speed_base
        """
        pause_probability = random.uniform(0.05, 0.10)
        total_ms = 0.0
        for _ in range(char_count):
            char_ms = _lognormal_delay_seconds(
                self.personality.typing_speed_base / 1000.0, 0.25
            ) * 1000
            total_ms += char_ms
            if random.random() < pause_probability:
                total_ms += random.uniform(500, 1500)
        await asyncio.sleep(total_ms / 1000.0)

    async def job_start_jitter(self) -> None:
        """Apply per-account time jitter before starting any automation job.

        Prevents synchronized task starts across accounts (a major cross-account
        correlation signal). Each account gets a unique jitter window.

        Range: JOB_START_JITTER_LO – JOB_START_JITTER_HI seconds.
        The window endpoint is deterministic per account to avoid true uniformity.
        """
        # Upper bound: deterministic per account (so same account always
        # picks from a consistent range), lower bound is fixed.
        seed = self.personality._seed
        jitter_hi = JOB_START_JITTER_LO + (
            (seed % 1000) / 1000.0 * (JOB_START_JITTER_HI - JOB_START_JITTER_LO)
        )
        jitter_secs = random.uniform(JOB_START_JITTER_LO, jitter_hi)
        LOGGER.info(
            "job_start_jitter",
            extra={
                "event": "job_start_jitter",
                "account_id": self.personality.account_id,
                "jitter_secs": round(jitter_secs, 1),
            },
        )
        await asyncio.sleep(jitter_secs)

    # ── Mouse simulation ──────────────────────────────────────────────────────

    async def simulate_mouse_move(
        self,
        page: Any,
        steps: int | None = None,
    ) -> None:
        """Move mouse through a Bezier-curved path across the viewport.

        In passive/minimal mode, skips mouse movement (no unnecessary actions).
        Speed is tied to hesitation_factor (high hesitation = slower movement).

        Args:
            page: Playwright page object.
            steps: Number of waypoints (default: 4–8 based on activity level).
        """
        if self.constraints.minimal_mode:
            return  # Abort
        if self.constraints.passive_mode:
            return  # No interaction in passive mode

        try:
            vp = page.viewport_size or {"width": 1280, "height": 720}
            w, h = vp["width"], vp["height"]

            n_steps = steps or {
                "low": random.randint(3, 5),
                "medium": random.randint(4, 7),
                "high": random.randint(5, 9),
            }[self.personality.activity_level]

            # Generate waypoints through Bezier arcs
            current = (random.uniform(80, w - 80), random.uniform(80, h - 80))
            for _ in range(n_steps):
                target = (random.uniform(80, w - 80), random.uniform(80, h - 80))
                path = _bezier_path(current, target, jitter=min(w, h) * 0.06)
                for px, py in path:
                    # Add tiny per-point jitter to simulate hand tremor
                    jx = random.gauss(0, 1.5)
                    jy = random.gauss(0, 1.5)
                    await page.mouse.move(px + jx, py + jy)
                    # Speed tied to hesitation_factor: high hesitation = more delay
                    move_delay = 0.012 * self.personality.hesitation_factor
                    await asyncio.sleep(max(0.005, move_delay))
                current = target

        except Exception:
            pass  # Non-fatal

    # ── Scroll simulation ─────────────────────────────────────────────────────

    async def simulate_scroll(
        self,
        page: Any,
        scroll_events: int | None = None,
    ) -> None:
        """Scroll the page with variable distance, direction reversal, and mid-pauses.

        Pattern:
          - scroll_events scrolls, each distance proportional to scroll_depth_preference
          - 10–15% chance of a reverse-scroll (humans scroll back to re-read)
          - 20% chance of a mid-scroll pause (reading something)
          - Skipped entirely in minimal_mode

        Args:
            page: Playwright page object.
            scroll_events: Override number of scroll events.
        """
        if self.constraints.minimal_mode:
            return

        try:
            n_scrolls = scroll_events or {
                "low": random.randint(2, 4),
                "medium": random.randint(3, 6),
                "high": random.randint(5, 9),
            }[self.personality.activity_level]

            reverse_prob = random.uniform(0.10, 0.15)

            for i in range(n_scrolls):
                # Scroll distance: base 400px × scroll_depth_preference, lognormal
                base_dist = 400 * self.personality.scroll_depth_preference
                distance = int(_lognormal_delay_seconds(base_dist, 0.30) * 1000 / 1.0)
                # Clamp between 80 and 900 px
                distance = max(80, min(900, distance))

                # Direction: mostly down, occasionally up
                direction = -1 if random.random() < reverse_prob else 1
                await page.evaluate(f"window.scrollBy(0, {direction * distance})")

                # Mid-scroll reading pause (20% chance)
                if random.random() < 0.20:
                    reading_pause = random.uniform(1.5, 4.5) * self.personality.hesitation_factor
                    await asyncio.sleep(reading_pause)
                else:
                    await lognormal_delay(0.7 * self.personality.hesitation_factor, 0.25)

        except Exception:
            pass  # Non-fatal

    # ── Skip logic ────────────────────────────────────────────────────────────

    def should_skip_upload(
        self,
        account_data: dict[str, Any],
    ) -> tuple[SkipDecision, str]:
        """Decide whether to skip the upload step for this session.

        Decision tree (in priority order):
          1. warmup_sessions < WARMUP_GATE → always skip (account too new)
          2. risk_score > 0.5              → skip with high probability
          3. soft_ban_detected             → always skip
          4. recent_activity_high          → maybe skip (reduce footprint)
          5. Otherwise                     → proceed

        Returns:
            (decision, reason) — "skip" | "proceed" and a loggable reason string.
        """
        warmup = int(account_data.get("warmup_sessions_completed") or 0)
        risk   = float(account_data.get("risk_score") or 0.0)
        soft   = bool(account_data.get("soft_ban_detected", False))

        # Recent activity indicator: posts_today or a similar field
        posts_today  = int(account_data.get("posts_today") or 0)
        activity_hi  = posts_today >= 3

        if warmup < WARMUP_GATE:
            reason = f"warmup_incomplete (sessions={warmup} < gate={WARMUP_GATE})"
            return "skip", reason

        if soft:
            reason = "soft_ban_detected"
            return "skip", reason

        if risk > 0.5:
            # Skip probability scales with risk: 0.5 risk → 50%, 1.0 risk → 100%
            skip_prob = min(1.0, (risk - 0.5) * 2.0)
            if random.random() < skip_prob:
                reason = f"high_risk_score (score={risk:.2f}, skip_prob={skip_prob:.2f})"
                return "skip", reason

        if activity_hi and random.random() < 0.35:
            reason = f"recent_activity_high (posts_today={posts_today})"
            return "skip", reason

        return "proceed", "all_checks_passed"

    # ── Logging helpers ───────────────────────────────────────────────────────

    def log_personality(self) -> None:
        """Emit a structured log line summarising the session personality."""
        LOGGER.info(
            "session_personality_built",
            extra={
                "event": "session_personality_built",
                **self.personality.summary(),
            },
        )

    def log_constraints(self) -> None:
        """Emit a structured log line summarising the active behavior constraints."""
        LOGGER.info(
            "behavior_constraints_active",
            extra={
                "event": "behavior_constraints_active",
                **self.constraints.summary(),
            },
        )

    def log_skip_decision(
        self,
        decision: SkipDecision,
        reason: str,
        account_id: str,
    ) -> None:
        """Emit a structured log line for upload skip decisions."""
        level = logging.WARNING if decision == "skip" else logging.INFO
        LOGGER.log(
            level,
            "upload_skip_decision",
            extra={
                "event": "upload_skip_decision",
                "account_id": account_id,
                "decision": decision,
                "reason": reason,
            },
        )


# ─────────────────────────────────────────────────────────────────────────────
# Factory — convenience builder for the publisher
# ─────────────────────────────────────────────────────────────────────────────

def create_behavior_engine(
    account_id: str,
    account_data: dict[str, Any],
    proxy_latency_ms: int = 0,
    day_of_week: int | None = None,
) -> BehaviorEngine:
    """Create a fully-configured BehaviorEngine for a single publish session.

    This is the primary entry point for the publisher integration.

    Args:
        account_id: Account UUID.
        account_data: Raw account dict from DB (risk_score, soft_ban_detected, …).
        proxy_latency_ms: Measured proxy latency from check_proxy_connectivity().
        day_of_week: Override for testing (0=Mon … 6=Sun). Defaults to today.

    Returns:
        A configured BehaviorEngine ready to drive session behavior.
    """
    personality  = build_session_personality(account_id, day_of_week)
    constraints  = build_constraints(account_data, proxy_latency_ms)
    engine       = BehaviorEngine(personality, constraints)
    engine.log_personality()
    engine.log_constraints()
    return engine
