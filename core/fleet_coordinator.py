"""
Fleet Coordinator — Layer 5: Multi-account orchestration.

Responsibilities:
  - Global rate limiting (uploads/hour, sessions/minute)
  - Staggered scheduling (no two accounts start the same second)
  - Proxy / geo distribution (assign pools, ensure geo consistency)
  - Diversity enforcement (detect identical patterns, force variation)
  - Health monitoring (ban spike detection → fleet-wide SAFE mode)

Design contracts:
  - FleetCoordinator does NOT run sessions itself — it calls SessionOrchestrator.
  - All account-level decisions remain in L3/L4; fleet layer only coordinates TIMING.
  - Stagger jitter is deterministic per account (seeded) to be reproducible.
  - Health downgrade is REVERSIBLE once ban rate normalises.

Usage:
    fleet = get_fleet_coordinator()
    fleet.register_accounts(["acc-1", "acc-2", ...])

    # Schedule a batch of sessions (blocking, for testing / cron):
    results = fleet.run_batch(profiles_by_id, signals_by_id)

    # Or get a scheduled slot and run manually:
    slot = fleet.request_slot("acc-1")
    if slot.allowed:
        time.sleep(slot.delay_secs)
        ctx = orchestrator.prepare("acc-1", signals, profile)
        ...
        fleet.report_result("acc-1", session_result)
"""
from __future__ import annotations

import hashlib
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.identity_manager import IdentityProfile
    from core.runtime_validator import RuntimeSignals
    from core.session_orchestrator import SessionResult
    from core.behavioral_brain import SessionPlan

LOGGER = logging.getLogger("core.fleet_coordinator")


# ── PRNG helper (same pattern as behavioral_brain, different namespace) ────────

def _fseed(account_id: str, slot: int) -> float:
    h = hashlib.sha256(f"fleet:{account_id}:{slot}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


# ── Rate limiter ──────────────────────────────────────────────────────────────

class _SlidingWindowCounter:
    """Thread-unsafe sliding window counter (designed for single-threaded scheduler)."""

    def __init__(self, window_secs: int, limit: int) -> None:
        self._window  = window_secs
        self._limit   = limit
        self._events: deque[float] = deque()

    def can_proceed(self) -> bool:
        now = time.monotonic()
        while self._events and self._events[0] < now - self._window:
            self._events.popleft()
        return len(self._events) < self._limit

    def record(self) -> None:
        self._events.append(time.monotonic())

    @property
    def current_count(self) -> int:
        now = time.monotonic()
        return sum(1 for t in self._events if t >= now - self._window)


# ── ScheduleSlot ─────────────────────────────────────────────────────────────

@dataclass
class ScheduleSlot:
    """Result of FleetCoordinator.request_slot()."""
    account_id:  str
    allowed:     bool
    delay_secs:  float     # how long to wait before starting
    reason:      str       # "ok" | "rate_limit_uploads" | "rate_limit_sessions" | "safe_mode"
    fleet_mode:  str       # "NORMAL" | "SAFE"


# ── ProxyPool ─────────────────────────────────────────────────────────────────

@dataclass
class ProxyPool:
    """A named group of proxies sharing a geo/country."""
    name:      str
    country:   str
    proxies:   list[str] = field(default_factory=list)

    def assign(self, account_id: str, rotation_slot: int = 0) -> str | None:
        """Deterministically assign one proxy to an account from this pool.

        rotation_slot > 0 means the account has cycled past its stickiness window
        and we pick a different proxy by shifting the seed slot.
        """
        if not self.proxies:
            return None
        idx = int(_fseed(account_id, rotation_slot) * len(self.proxies))
        return self.proxies[idx]


# ── ProxyAssignment ───────────────────────────────────────────────────────────

@dataclass
class ProxyAssignment:
    """Tracks which proxy an account is currently stuck to and for how long."""
    proxy:              str
    pool_name:          str
    assigned_at_session:int   # global session counter when this proxy was assigned
    rotation_slot:      int   # increments each time the proxy is rotated


# ── AccountHealth ─────────────────────────────────────────────────────────────

@dataclass
class AccountHealth:
    """Per-account rolling health metrics tracked by FleetCoordinator."""
    account_id:        str
    last_session_ts:   float = 0.0
    uploads_this_hour: int   = 0
    bans_this_hour:    int   = 0
    sessions_today:    int   = 0
    # Rolling window of session fingerprint hashes (for diversity detection)
    recent_plan_hashes: list[str] = field(default_factory=list)

    def record_session(self, result: "SessionResult") -> None:
        self.last_session_ts = time.monotonic()
        self.sessions_today += 1
        if result.upload_success:
            self.uploads_this_hour += 1
        if result.blocked:
            self.bans_this_hour += 1
        # Store a plan fingerprint for diversity analysis
        fp = hashlib.md5(f"{result.intent}:{result.actual_secs:.0f}:{result.abandoned_count}".encode()).hexdigest()[:8]
        self.recent_plan_hashes.append(fp)
        if len(self.recent_plan_hashes) > 20:
            self.recent_plan_hashes.pop(0)


# ── FleetCoordinator ─────────────────────────────────────────────────────────

class FleetCoordinator:
    """
    Layer 5: Global orchestration of N accounts.

    Features:
      - Global rate limiting: max_uploads_per_hour, max_sessions_per_minute
      - Staggered scheduling: seeded jitter ensures no two accounts start together
      - Proxy/geo distribution: ProxyPool assignment per account
      - Diversity enforcement: similarity score across recent plans
      - Health monitoring: ban spike → SAFE mode for all accounts
    """

    def __init__(
        self,
        max_uploads_per_hour:    int   = 10,
        max_sessions_per_minute: int   = 5,
        ban_spike_threshold:     int   = 3,
        min_stagger_secs:        float = 1.0,
        max_stagger_secs:        float = 30.0,
        diversity_threshold:     float = 0.70,
        proxy_stickiness_window: int   = 7,   # sessions before proxy rotation
        diversity_jitter_lo:     float = 0.10, # ±10%
        diversity_jitter_hi:     float = 0.25, # ±25%
    ) -> None:
        self._upload_limiter  = _SlidingWindowCounter(3600, max_uploads_per_hour)
        self._session_limiter = _SlidingWindowCounter(60,   max_sessions_per_minute)
        self._ban_window      = _SlidingWindowCounter(3600, ban_spike_threshold)
        self._min_stagger          = min_stagger_secs
        self._max_stagger          = max_stagger_secs
        self._diversity_thresh     = diversity_threshold
        self._proxy_stickiness     = proxy_stickiness_window
        self._diversity_jitter_lo  = diversity_jitter_lo
        self._diversity_jitter_hi  = diversity_jitter_hi

        self._fleet_mode:  str = "NORMAL"
        self._accounts:    dict[str, AccountHealth] = {}
        self._proxy_pools: list[ProxyPool]           = []
        # Replaced simple dict with ProxyAssignment for stickiness tracking
        self._proxy_assignments: dict[str, ProxyAssignment] = {}
        # Per-account diversity hint (set when similarity > threshold)
        self._diversity_hints: dict[str, float] = {}  # account_id → similarity_score
        # Global session counter (incremented each time record_session_start is called)
        self._global_session_counter: int = 0

        # Last-scheduled time per account (for anti-collision staggering)
        self._last_start: dict[str, float] = {}

        LOGGER.info("fleet_coordinator_initialized", extra={
            "max_uploads_per_hour":    max_uploads_per_hour,
            "max_sessions_per_minute": max_sessions_per_minute,
            "ban_spike_threshold":     ban_spike_threshold,
            "proxy_stickiness_window": proxy_stickiness_window,
        })

    # ── Account registration ──────────────────────────────────────────────────

    def register_accounts(self, account_ids: list[str]) -> None:
        """Register accounts into the fleet."""
        for aid in account_ids:
            if aid not in self._accounts:
                self._accounts[aid] = AccountHealth(account_id=aid)
        LOGGER.info("accounts_registered", extra={"count": len(account_ids)})

    def add_proxy_pool(self, pool: ProxyPool) -> None:
        """Add a proxy pool. Accounts are assigned deterministically on first use."""
        self._proxy_pools.append(pool)

    # ── Slot scheduling ───────────────────────────────────────────────────────

    def request_slot(self, account_id: str) -> ScheduleSlot:
        """
        Ask the fleet if account_id can start a session now.

        Returns a ScheduleSlot with:
          - allowed: False if hard-blocked (rate limit or SAFE mode)
          - delay_secs: how many seconds to wait before starting
          - fleet_mode: current fleet health mode
        """
        if account_id not in self._accounts:
            self._accounts[account_id] = AccountHealth(account_id=account_id)

        # Fleet-wide SAFE mode blocks uploads
        if self._fleet_mode == "SAFE":
            LOGGER.warning("slot_denied_safe_mode", extra={"account_id": account_id})
            return ScheduleSlot(account_id, False, 0.0, "safe_mode", "SAFE")

        # Global session rate limit
        if not self._session_limiter.can_proceed():
            LOGGER.warning("slot_denied_session_rate", extra={"account_id": account_id})
            return ScheduleSlot(account_id, False, 0.0, "rate_limit_sessions", self._fleet_mode)

        # Stagger: ensure minimum gap between any two session starts
        delay = self._compute_stagger(account_id)

        return ScheduleSlot(
            account_id = account_id,
            allowed    = True,
            delay_secs = delay,
            reason     = "ok",
            fleet_mode = self._fleet_mode,
        )

    def request_upload_slot(self, account_id: str) -> bool:
        """
        Check whether an upload is permitted globally right now.
        Must be called before actually uploading; record with record_upload() on success.
        """
        if self._fleet_mode == "SAFE":
            return False
        return self._upload_limiter.can_proceed()

    def record_upload(self) -> None:
        """Call after a successful upload to track the rate limit window."""
        self._upload_limiter.record()

    def record_session_start(self, account_id: str) -> None:
        """Call just before executing a session to register it in the rate limiter."""
        self._session_limiter.record()
        self._last_start[account_id] = time.monotonic()
        self._global_session_counter += 1

    # ── Result reporting ──────────────────────────────────────────────────────

    def report_result(self, account_id: str, result: "SessionResult") -> None:
        """
        Report a completed session result to update health metrics.

        Triggers:
          - Ban spike detection → fleet-wide SAFE mode
          - Plan diversity check → logs warning if too similar
        """
        health = self._accounts.get(account_id)
        if health is None:
            return

        health.record_session(result)

        if result.blocked:
            self._ban_window.record()
            LOGGER.warning("fleet_ban_recorded", extra={
                "account_id":        account_id,
                "fleet_bans_in_window": self._ban_window.current_count,
            })
            self._check_ban_spike()

        if result.upload_success:
            self.record_upload()

        self._check_diversity(account_id, health)

    # ── Proxy / geo assignment ────────────────────────────────────────────────

    def get_proxy(self, account_id: str) -> str | None:
        """Return the sticky proxy URL for an account.

        Proxy stickiness:
          - The same proxy is reused for `proxy_stickiness_window` consecutive sessions.
          - After the window expires, the proxy rotates deterministically to a new one.
          - rotation_slot increments on each rotation so successive proxies differ.
          - Result: no rapid IP switching; controlled, predictable rotation.
        """
        if not self._proxy_pools:
            return None

        # Determine which pool this account belongs to (deterministic, never changes)
        pool_idx = int(_fseed(account_id, 1) * len(self._proxy_pools))
        pool     = self._proxy_pools[pool_idx]

        existing = self._proxy_assignments.get(account_id)
        health   = self._accounts.get(account_id) or AccountHealth(account_id=account_id)
        sessions_done = health.sessions_today

        if existing is not None:
            sessions_since_assign = sessions_done - existing.assigned_at_session
            if sessions_since_assign < self._proxy_stickiness:
                # Still within stickiness window — reuse
                LOGGER.debug("proxy_sticky", extra={
                    "account_id":  account_id,
                    "proxy":       existing.proxy[:20] + "...",
                    "sessions_remaining": self._proxy_stickiness - sessions_since_assign,
                })
                return existing.proxy
            # Stickiness window expired — rotate
            new_slot  = existing.rotation_slot + 1
            new_proxy = pool.assign(account_id, rotation_slot=new_slot)
            if new_proxy is None:
                return existing.proxy  # fallback: keep old if pool empty
            self._proxy_assignments[account_id] = ProxyAssignment(
                proxy              = new_proxy,
                pool_name          = pool.name,
                assigned_at_session= sessions_done,
                rotation_slot      = new_slot,
            )
            LOGGER.info("proxy_rotated", extra={
                "account_id":  account_id,
                "pool":        pool.name,
                "rotation":    new_slot,
                "new_proxy":   new_proxy[:20] + "...",
            })
            return new_proxy

        # First-ever assignment
        proxy = pool.assign(account_id, rotation_slot=0)
        if proxy:
            self._proxy_assignments[account_id] = ProxyAssignment(
                proxy              = proxy,
                pool_name          = pool.name,
                assigned_at_session= sessions_done,
                rotation_slot      = 0,
            )
            LOGGER.info("proxy_assigned", extra={
                "account_id": account_id,
                "pool":       pool.name,
                "country":    pool.country,
                "proxy":      proxy[:20] + "...",
            })
        return proxy

    def get_proxy_info(self, account_id: str) -> ProxyAssignment | None:
        """Return the current ProxyAssignment for an account (or None)."""
        return self._proxy_assignments.get(account_id)

    # ── Fleet batch runner ────────────────────────────────────────────────────

    def run_batch(
        self,
        profiles_by_id: dict[str, "IdentityProfile"],
        signals_by_id:  dict[str, "RuntimeSignals"],
    ) -> list["SessionResult"]:
        """
        Run one session per registered account (sequentially with staggering).

        For async/concurrent use, call prepare_with_slot() per account and
        execute them independently.

        Returns list of SessionResult (only for accounts that received a slot).
        """
        from core.session_orchestrator import get_session_orchestrator

        orch = get_session_orchestrator()
        results: list[SessionResult] = []

        for account_id, profile in profiles_by_id.items():
            signals = signals_by_id.get(account_id)
            if signals is None:
                LOGGER.warning("batch_skipped_no_signals", extra={"account_id": account_id})
                continue

            slot = self.request_slot(account_id)
            if not slot.allowed:
                LOGGER.info("batch_slot_denied", extra={
                    "account_id": account_id, "reason": slot.reason,
                })
                continue

            if slot.delay_secs > 0:
                # In production: await asyncio.sleep(slot.delay_secs)
                # Here: we just record the intent without actually sleeping
                LOGGER.debug("batch_stagger", extra={
                    "account_id": account_id, "delay_secs": round(slot.delay_secs, 2),
                })

            self.record_session_start(account_id)

            try:
                # Check for pending diversity hint — apply before session runs
                similarity = self._diversity_hints.pop(account_id, 0.0)

                ctx = orch.prepare(account_id, signals, profile)

                # Enforce diversity at L4 level if needed
                if similarity >= self._diversity_thresh:
                    ctx.plan = self.enforce_diversity(ctx.plan, similarity)

                # Simulate execution (run_sync semantics: actual == estimated)
                ctx.plan.actual_duration = ctx.plan.estimated_duration
                ctx.plan.abandoned_count = 0
                result = orch.finalize(ctx)

                self.report_result(account_id, result)
                results.append(result)
            except Exception as exc:
                LOGGER.error("batch_session_error", extra={
                    "account_id": account_id, "error": str(exc),
                })

        return results

    # ── Diversity enforcement ─────────────────────────────────────────────────

    def enforce_diversity(
        self,
        plan: "SessionPlan",
        similarity_score: float,
    ) -> "SessionPlan":
        """
        Actively break identical session patterns when similarity_score >= threshold.

        Rules:
          - NEVER modifies Strategy.risk_level (L3 safety not touched).
          - ONLY mutates the L4 SessionPlan: step durations, micro-idles, scroll steps.
          - Timing jitter: ±10–25% (seeded from plan content hash so reproducible).
          - Intent injection: probabilistically inserts a BROWSE/ENGAGE micro-step
            to break identical intent sequences.
          - Randomizes non-critical skippable steps (scroll count, micro-idle duration).

        Args:
            plan:             SessionPlan from BehavioralBrain (mutated in-place copy).
            similarity_score: Fraction of recent identical plans (0.0 – 1.0).

        Returns:
            Modified SessionPlan (new object, original untouched).
        """
        from core.behavioral_brain import SessionStep, SessionPlan as _Plan, SessionIntent
        import copy

        # Seed from plan content so the variation is deterministic per similarity event
        seed_str = "||".join(s.action for s in plan.steps)
        seed_int = int(hashlib.sha256(seed_str.encode()).hexdigest()[:8], 16)

        def _jitter(value: float, slot: int) -> float:
            """Apply seeded ±jitter strictly bounded to ±jitter_hi of the original value."""
            safe_slot  = slot % 24
            jitter_frac = self._diversity_jitter_lo + (
                (self._diversity_jitter_hi - self._diversity_jitter_lo)
                * ((seed_int >> safe_slot) & 0xFF) / 255
            )
            sign    = 1.0 if ((seed_int >> ((safe_slot + 4) % 24)) & 1) else -1.0
            mutated = value * (1.0 + sign * jitter_frac)
            # Clamp strictly: never go outside ±(jitter_hi + 2% margin) of original
            ceiling = value * (1.0 + self._diversity_jitter_hi + 0.02)
            floor_v = max(0.1, value * (1.0 - self._diversity_jitter_hi - 0.02))
            return round(max(floor_v, min(mutated, ceiling)), 2)



        new_steps: list[SessionStep] = []
        injected_idle = False

        for i, step in enumerate(plan.steps):
            # Apply timing jitter to ALL steps
            new_dur = _jitter(step.duration, i)
            new_step = SessionStep(
                action    = step.action,
                duration  = new_dur,
                metadata  = dict(step.metadata),
                skippable = step.skippable,
            )

            # Inject a micro-idle before the first skippable scroll (breaks uniform flow)
            if (
                not injected_idle
                and step.action == "scroll_feed"
                and step.skippable
                and similarity_score >= self._diversity_thresh
            ):
                idle_dur = _jitter(3.5, i + 1000)
                new_steps.append(SessionStep(
                    action    = "micro_idle",
                    duration  = idle_dur,
                    metadata  = {"reason": "diversity_break"},
                    skippable = True,
                ))
                injected_idle = True

            new_steps.append(new_step)

        # If plan has no scrolls, inject a browse micro-step at end
        has_scroll = any(s.action == "scroll_feed" for s in plan.steps)
        if not has_scroll and plan.intent not in (SessionIntent.IDLE, SessionIntent.UPLOAD):
            micro_browse_dur = _jitter(4.0, 9999)
            new_steps.insert(
                max(0, len(new_steps) - 1),  # before close_app
                SessionStep(
                    action    = "micro_browse",
                    duration  = micro_browse_dur,
                    metadata  = {"reason": "diversity_intent_break"},
                    skippable = True,
                ),
            )

        new_estimated = round(sum(s.duration for s in new_steps), 1)

        LOGGER.info("diversity_enforced", extra={
            "intent":          plan.intent.value,
            "similarity":      round(similarity_score, 2),
            "original_steps":  len(plan.steps),
            "new_steps":       len(new_steps),
            "original_est":    plan.estimated_duration,
            "new_est":         new_estimated,
        })

        return _Plan(
            intent             = plan.intent,
            steps              = new_steps,
            behavior_profile   = plan.behavior_profile,
            estimated_duration = new_estimated,
            actual_duration    = plan.actual_duration,
            abandoned_count    = plan.abandoned_count,
        )

    def get_fleet_stats(self) -> dict[str, Any]:
        """Return a snapshot of current fleet health metrics."""
        return {
            "fleet_mode":          self._fleet_mode,
            "registered_accounts": len(self._accounts),
            "uploads_in_window":   self._upload_limiter.current_count,
            "sessions_in_window":  self._session_limiter.current_count,
            "bans_in_window":      self._ban_window.current_count,
            "proxy_pools":         [{"name": p.name, "country": p.country, "size": len(p.proxies)}
                                    for p in self._proxy_pools],
            "diversity_hints":     dict(self._diversity_hints),
        }

    def set_fleet_mode(self, mode: str) -> None:
        """Manually set fleet mode. mode ∈ {'NORMAL', 'SAFE'}."""
        assert mode in ("NORMAL", "SAFE"), f"Invalid mode: {mode}"
        if mode != self._fleet_mode:
            LOGGER.warning("fleet_mode_changed", extra={"old": self._fleet_mode, "new": mode})
            self._fleet_mode = mode

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _compute_stagger(self, account_id: str) -> float:
        """
        Compute a seeded but varied delay so no two accounts start simultaneously.

        Base jitter: seeded from account_id (stable per account identity).
        Variance:    shifted by current minute-of-day so it changes each scheduling cycle.
        """
        base_jitter = _fseed(account_id, int(time.time()) // 60) * (self._max_stagger - self._min_stagger)
        return round(self._min_stagger + base_jitter, 2)

    def _check_ban_spike(self) -> None:
        """Downgrade entire fleet to SAFE if ban spike threshold is exceeded."""
        if not self._ban_window.can_proceed():
            if self._fleet_mode != "SAFE":
                LOGGER.critical("fleet_ban_spike_detected", extra={
                    "bans_in_window": self._ban_window.current_count,
                    "action": "downgrade_to_SAFE",
                })
                self._fleet_mode = "SAFE"

    def _check_diversity(self, account_id: str, health: AccountHealth) -> None:
        """Detect high similarity and set a pending diversity hint for the NEXT session."""
        hashes = health.recent_plan_hashes
        if len(hashes) < 5:
            return
        most_common = max(set(hashes), key=hashes.count)
        similarity  = hashes.count(most_common) / len(hashes)
        if similarity >= self._diversity_thresh:
            # Store hint — enforce_diversity() will consume it on next session start
            self._diversity_hints[account_id] = similarity
            LOGGER.warning("fleet_diversity_low", extra={
                "account_id":      account_id,
                "similarity":      round(similarity, 2),
                "most_common_plan":most_common,
                "action":          "diversity_hint_set_for_next_session",
            })


# ── Singleton ─────────────────────────────────────────────────────────────────

_FLEET_COORDINATOR: FleetCoordinator | None = None


def get_fleet_coordinator(**kwargs: Any) -> FleetCoordinator:
    """Return the process-level FleetCoordinator singleton."""
    global _FLEET_COORDINATOR
    if _FLEET_COORDINATOR is None:
        _FLEET_COORDINATOR = FleetCoordinator(**kwargs)
    return _FLEET_COORDINATOR
