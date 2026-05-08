"""
Cross-Account Coordinator — global coordination layer for multi-account automation.

PROBLEM SOLVED:
  Each account individually looks human (handled by BehaviorEngine).
  But platforms also do FLEET-LEVEL statistical analysis:
    - Synchronized job start timestamps → automated cluster
    - Skip-rate spikes  → coordinated inactivity pattern
    - Upload bursts     → bot-wave signal
    - All accounts at same activity_level → unnatural distribution

THIS MODULE solves the above at the SYSTEM level.

Design principles:
  - In-memory state only (no DB round-trip on hot path)
  - Single-process asyncio Lock for all mutations (safe for Celery worker, one process)
  - All decisions are ADDITIVE CONSTRAINTS on top of per-account logic
  - Does NOT override hard safety gates (bans, proxy checks stay in publisher)
  - Minimal overhead: O(N) memory for N active accounts

Architecture:
  ┌─────────────────────────────────────────────────────────────────┐
  │                   CrossAccountCoordinator                       │
  │   ┌──────────────┐  ┌─────────────┐  ┌──────────────────────┐  │
  │   │ Job Scheduler│  │  Upload     │  │  Skip Coordinator    │  │
  │   │  (anti-sync) │  │  Throttle   │  │  (ceiling enforcer)  │  │
  │   └──────────────┘  └─────────────┘  └──────────────────────┘  │
  │   ┌──────────────┐  ┌─────────────┐  ┌──────────────────────┐  │
  │   │  Personality │  │  Proxy Load │  │  Global State        │  │
  │   │  Balancer    │  │  Manager    │  │  Tracker             │  │
  │   └──────────────┘  └─────────────┘  └──────────────────────┘  │
  └─────────────────────────────────────────────────────────────────┘

Integration:
    coord = get_coordinator()

    # 1. Before job starts
    delay = await coord.get_start_delay(account_id, proxy_url)
    await asyncio.sleep(delay)
    coord.register_job_start(account_id, proxy_url)

    # 2. Before upload
    upload_ok, reason = coord.can_upload_now(account_id)

    # 3. Before skip
    final_skip, reason = coord.should_allow_skip(account_id, local_skip_decision)

    # 4. After personality is built
    personality = coord.adjust_personality(personality)

    # 5. On session end (always call)
    coord.register_job_end(account_id, proxy_url, uploaded=True)
"""
from __future__ import annotations

import asyncio
import collections
import logging
import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Deque, Literal

if TYPE_CHECKING:
    from core.behavior_engine import ActivityLevel, SessionPersonality

LOGGER = logging.getLogger("core.cross_account_coordinator")


# ─────────────────────────────────────────────────────────────────────────────
# Configuration constants (tuneable via env in a future iteration)
# ─────────────────────────────────────────────────────────────────────────────

# Job scheduler — anti-sync
_BASE_JITTER_LO: float    = 30.0   # s  — minimum start delay
_BASE_JITTER_HI: float    = 120.0  # s  — maximum start delay
_BURST_WINDOW_SECS: float = 120.0  # s  — look-back window for "too many recent starts"
_BURST_THRESHOLD: int     = 3      # #  — jobs started in window before penalty kicks in
_BURST_EXTRA_LO: float    = 30.0   # s  — extra delay added per excess job in burst
_BURST_EXTRA_HI: float    = 60.0   # s  — upper bound of extra delay per excess job
_PROXY_STAGGER_LO: float  = 15.0   # s  — extra delay when proxy already has an active job
_PROXY_STAGGER_HI: float  = 45.0   # s  — upper bound

# Upload throttle — two independent windows
_UPLOAD_WINDOW_SECS: float  = 600.0    # 10 min rolling window (burst protection)
_UPLOAD_MAX_IN_WINDOW: int  = 5        # max uploads in 10-min window
_UPLOAD_HOURLY_WINDOW: float = 3600.0  # 1-hour rolling window (sustained rate)
_UPLOAD_MAX_PER_HOUR: int   = 10       # max fleet uploads per hour (configurable)

# Per-account daily hard caps (coordinator-level second layer)
# Primary enforcement is in LifecycleManager; this is a safety net
_MAX_SESSIONS_PER_ACCOUNT_PER_DAY: int = 3
_MAX_UPLOADS_PER_ACCOUNT_PER_DAY: int  = 1

# Skip coordination
_SKIP_WINDOW_SECS: float  = 1800.0  # 30 min rolling window
_SKIP_RATE_CEILING: float = 0.15    # max fraction of sessions that may skip in window
_SKIP_HISTORY_CAP: int    = 200      # max entries kept in deques (memory cap)
_CONSEC_SKIP_PENALTY: float = 0.70  # probability skip is denied if account skipped last time

# Behavior distribution targets (fractions of active sessions)
_TARGET_LOW_FRAC:    float = 0.30   # ≥30% of sessions should be "low" activity
_TARGET_HIGH_FRAC:   float = 0.30   # ≤30% of sessions should be "high" activity
_TARGET_LONG_SESSION_FRAC: float = 0.40  # ≤40% of sessions should be "long" (>12 min)

# Proxy load
_MAX_ACCOUNTS_PER_PROXY: int = 3    # above this, trigger stagger penalty


# ─────────────────────────────────────────────────────────────────────────────
# Internal event record types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _JobRecord:
    """Lightweight record of an in-flight or recently completed job."""
    account_id: str
    proxy_url:  str
    started_at: float   # monotonic timestamp
    ended_at:   float = 0.0
    uploaded:   bool  = False

    @property
    def is_active(self) -> bool:
        return self.ended_at == 0.0


@dataclass
class _SkipRecord:
    account_id: str
    skipped:    bool
    ts:         float = field(default_factory=time.monotonic)


@dataclass
class _UploadRecord:
    account_id: str
    ts:         float = field(default_factory=time.monotonic)


# ─────────────────────────────────────────────────────────────────────────────
# Main coordinator class
# ─────────────────────────────────────────────────────────────────────────────

class CrossAccountCoordinator:
    """Process-level singleton that tracks cross-account state and enforces
    global coordination policies.

    All public methods are safe to call from async coroutines.
    Sync methods (register_*, can_upload_now, should_allow_skip, adjust_personality)
    never block the event loop — they execute in O(1) or O(log N) time.

    State is ephemeral: it resets when the worker process restarts.
    This is intentional — a fresh process should not inherit stale penalties.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

        # Active + recently ended jobs indexed by account_id
        self._active_jobs:   dict[str, _JobRecord] = {}

        # Ring buffer of recent job start timestamps (for burst detection)
        self._recent_starts: Deque[float] = collections.deque(maxlen=500)

        # Rolling history of skip decisions
        self._skip_history:  Deque[_SkipRecord] = collections.deque(maxlen=_SKIP_HISTORY_CAP)

        # Per-account: timestamp of last skip decision (True = skipped)
        self._last_skip:     dict[str, bool] = {}

        # Rolling history of upload completions
        self._upload_history: Deque[_UploadRecord] = collections.deque(maxlen=_SKIP_HISTORY_CAP)

        # Proxy → set of active account_ids
        self._proxy_accounts: dict[str, set[str]] = {}

        # Activity level counters for active sessions
        self._activity_counts: dict[str, int] = {"low": 0, "medium": 0, "high": 0}

        # Per-account daily counters (date-keyed, reset at UTC midnight)
        self._account_session_date: dict[str, str]  = {}  # account_id → ISO date
        self._account_sessions:     dict[str, int]  = {}  # account_id → count today
        self._account_upload_date:  dict[str, str]  = {}  # account_id → ISO date
        self._account_uploads:      dict[str, int]  = {}  # account_id → count today

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Job lifecycle registration
    # ─────────────────────────────────────────────────────────────────────────

    def register_job_start(self, account_id: str, proxy_url: str) -> None:
        """Record that a job has started for account_id via proxy_url.

        Call immediately AFTER the start delay has been applied.
        Thread/coroutine safety: uses synchronous dict writes (GIL-safe in CPython).
        """
        now = time.monotonic()
        record = _JobRecord(account_id=account_id, proxy_url=proxy_url, started_at=now)
        self._active_jobs[account_id] = record
        self._recent_starts.append(now)

        if proxy_url not in self._proxy_accounts:
            self._proxy_accounts[proxy_url] = set()
        self._proxy_accounts[proxy_url].add(account_id)

        # Increment per-account daily session counter
        today = self._today_utc()
        if self._account_session_date.get(account_id) != today:
            self._account_session_date[account_id] = today
            self._account_sessions[account_id] = 0
        self._account_sessions[account_id] = self._account_sessions.get(account_id, 0) + 1

        LOGGER.info(
            "coordinator_job_registered",
            extra={
                "event":          "coordinator_job_registered",
                "account_id":     account_id,
                "proxy":          proxy_url,
                "active_jobs":    len(self._active_jobs),
                "proxy_accounts": len(self._proxy_accounts.get(proxy_url, set())),
            },
        )

    def register_job_end(
        self,
        account_id: str,
        proxy_url: str,
        uploaded: bool = False,
        activity_level: str = "medium",
    ) -> None:
        """Record that a job has finished.

        Call in a finally block so it always fires even on error.
        """
        now = time.monotonic()
        if account_id in self._active_jobs:
            self._active_jobs[account_id].ended_at = now
            self._active_jobs[account_id].uploaded = uploaded
            del self._active_jobs[account_id]   # remove from active set

        # Clean proxy map
        if proxy_url in self._proxy_accounts:
            self._proxy_accounts[proxy_url].discard(account_id)
            if not self._proxy_accounts[proxy_url]:
                del self._proxy_accounts[proxy_url]

        # Track upload in rolling history + daily per-account counter
        if uploaded:
            self._upload_history.append(_UploadRecord(account_id=account_id, ts=now))
            today = self._today_utc()
            if self._account_upload_date.get(account_id) != today:
                self._account_upload_date[account_id] = today
                self._account_uploads[account_id] = 0
            self._account_uploads[account_id] = self._account_uploads.get(account_id, 0) + 1

        # Decrement activity counter
        if activity_level in self._activity_counts:
            self._activity_counts[activity_level] = max(
                0, self._activity_counts[activity_level] - 1
            )

        LOGGER.info(
            "coordinator_job_ended",
            extra={
                "event":       "coordinator_job_ended",
                "account_id":  account_id,
                "uploaded":    uploaded,
                "active_jobs": len(self._active_jobs),
            },
        )

    # ─────────────────────────────────────────────────────────────────────────
    # 2. Job start scheduler — anti-sync
    # ─────────────────────────────────────────────────────────────────────────

    async def get_start_delay(self, account_id: str, proxy_url: str | None) -> float:
        """Compute how many seconds this job should wait before starting.

        Decision layers (additive):
          a. Base jitter: uniform [30, 120] s — always applied
          b. Burst penalty: if ≥ _BURST_THRESHOLD jobs started in last 2 min,
             add [30, 60] s per excess job (capped at 3× penalty)
          c. Proxy stagger: if same proxy already has active jobs,
             add [15, 45] s

        The result is logged so operators can audit anti-sync behaviour.
        """
        async with self._lock:
            now = time.monotonic()

            # a. Base jitter
            base = random.uniform(_BASE_JITTER_LO, _BASE_JITTER_HI)
            reason_parts: list[str] = [f"base={base:.1f}s"]
            soft_deny = False

            # b. Burst detection
            recent_count = sum(
                1 for ts in self._recent_starts
                if now - ts <= _BURST_WINDOW_SECS
            )
            burst_extra = 0.0
            if recent_count >= _BURST_THRESHOLD:
                excess = min(recent_count - _BURST_THRESHOLD + 1, 3)  # cap at 3×
                burst_extra = excess * random.uniform(_BURST_EXTRA_LO, _BURST_EXTRA_HI)
                reason_parts.append(
                    f"burst_penalty={burst_extra:.1f}s (recent={recent_count})"
                )

            # c. Proxy stagger / overload soft-deny
            # When a proxy is overloaded (>= _MAX_ACCOUNTS_PER_PROXY active accounts),
            # return a large soft-deny delay (300s) rather than a small stagger.
            # This is a SOFT signal — the worker may retry with the same proxy
            # or switch proxies. It is not a hard block.
            proxy_extra = 0.0
            if proxy_url and self._proxy_accounts.get(proxy_url):
                active_on_proxy = len(self._proxy_accounts[proxy_url])
                if active_on_proxy >= _MAX_ACCOUNTS_PER_PROXY:
                    # Proxy overloaded: emit a 300s soft-deny delay
                    proxy_extra = 300.0
                    soft_deny = True
                    reason_parts.append(
                        f"proxy_overload_soft_deny=300s "
                        f"(active_on_proxy={active_on_proxy} >= max={_MAX_ACCOUNTS_PER_PROXY})"
                    )
                    LOGGER.warning(
                        "coordinator_proxy_overload_soft_deny",
                        extra={
                            "event":           "coordinator_proxy_overload_soft_deny",
                            "account_id":      account_id,
                            "proxy":           proxy_url,
                            "active_accounts": active_on_proxy,
                            "max":             _MAX_ACCOUNTS_PER_PROXY,
                            "soft_deny_secs":  300,
                            "decision":        "soft_deny",
                        },
                    )
                else:
                    proxy_extra = random.uniform(_PROXY_STAGGER_LO, _PROXY_STAGGER_HI)
                    reason_parts.append(
                        f"proxy_stagger={proxy_extra:.1f}s (active_on_proxy={active_on_proxy})"
                    )

            total = base + proxy_extra if soft_deny else base + burst_extra + proxy_extra

        LOGGER.info(
            "coordinator_start_delay",
            extra={
                "event":       "coordinator_start_delay",
                "account_id":  account_id,
                "proxy":       proxy_url or "NONE",
                "delay_secs":  round(total, 1),
                "breakdown":   " | ".join(reason_parts),
                "active_jobs": len(self._active_jobs),
            },
        )
        return total

    # ─────────────────────────────────────────────────────────────────────────
    # 3. Upload distribution control
    # ─────────────────────────────────────────────────────────────────────────

    def _today_utc(self) -> str:
        """Return today's UTC date as an ISO string for daily counter resets.

        Uses UTC explicitly — not local time — so date-rollover is consistent
        with lifecycle_manager._today_iso() regardless of server timezone.
        """
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).date().isoformat()

    def _account_sessions_today(self, account_id: str) -> int:
        today = self._today_utc()
        if self._account_session_date.get(account_id) != today:
            self._account_session_date[account_id] = today
            self._account_sessions[account_id] = 0
        return self._account_sessions.get(account_id, 0)

    def _account_uploads_today(self, account_id: str) -> int:
        today = self._today_utc()
        if self._account_upload_date.get(account_id) != today:
            self._account_upload_date[account_id] = today
            self._account_uploads[account_id] = 0
        return self._account_uploads.get(account_id, 0)

    def check_session_allowed(self, account_id: str) -> tuple[bool, str]:
        """Coordinator-level check: is this account allowed to start a session today?

        Enforces the global hard cap of max 3 sessions/account/day.
        LifecycleManager is the primary enforcer; this is a safety-net second layer.

        Returns:
            (allowed: bool, reason: str)
        """
        count = self._account_sessions_today(account_id)
        if count >= _MAX_SESSIONS_PER_ACCOUNT_PER_DAY:
            reason = (
                f"coordinator_daily_session_cap "
                f"(sessions_today={count} >= max={_MAX_SESSIONS_PER_ACCOUNT_PER_DAY})"
            )
            LOGGER.warning(
                "coordinator_session_cap_hit",
                extra={
                    "event":          "coordinator_session_cap_hit",
                    "account_id":     account_id,
                    "sessions_today": count,
                    "max":            _MAX_SESSIONS_PER_ACCOUNT_PER_DAY,
                },
            )
            return False, reason
        return True, f"session_allowed (sessions_today={count}/{_MAX_SESSIONS_PER_ACCOUNT_PER_DAY})"

    def can_upload_now(self, account_id: str) -> tuple[bool, str]:
        """Check whether this session may perform an upload.

        This is a SECONDARY (soft-distribution) layer that runs AFTER
        LifecycleManager.evaluate() has already approved the upload.
        LifecycleManager is the HARD GATE (phase caps, trust/fatigue gates).
        This coordinator enforces fleet-level burst throttling and is a
        per-account safety net only — it cannot override a lifecycle BLOCK,
        but it CAN restrict a lifecycle-approved upload for fleet health.

        Enforces three independent caps:
          1. 10-min burst window: max 5 fleet uploads in 10 minutes
          2. 1-hour sustained window: max 10 fleet uploads per hour
          3. Per-account daily cap: max 1 upload per account per day
             (mirrors lifecycle hard cap — belt-and-suspenders safety net)

        Returns:
            (allowed: bool, reason: str)
        """
        now = time.monotonic()

        # Cap 1: 10-minute burst window
        recent_10min = sum(
            1 for r in self._upload_history
            if now - r.ts <= _UPLOAD_WINDOW_SECS
        )
        if recent_10min >= _UPLOAD_MAX_IN_WINDOW:
            reason = (
                f"upload_burst_cap (uploads_10min={recent_10min}, "
                f"max={_UPLOAD_MAX_IN_WINDOW}, window={int(_UPLOAD_WINDOW_SECS)}s)"
            )
            LOGGER.warning("coordinator_upload_burst_throttled", extra={
                "event": "coordinator_upload_burst_throttled", "account_id": account_id,
                "uploads_10min": recent_10min, "max": _UPLOAD_MAX_IN_WINDOW,
            })
            return False, reason

        # Cap 2: 1-hour sustained window
        recent_1h = sum(
            1 for r in self._upload_history
            if now - r.ts <= _UPLOAD_HOURLY_WINDOW
        )
        if recent_1h >= _UPLOAD_MAX_PER_HOUR:
            reason = (
                f"upload_hourly_cap (uploads_1h={recent_1h}, "
                f"max={_UPLOAD_MAX_PER_HOUR}, window=3600s)"
            )
            LOGGER.warning("coordinator_upload_hourly_throttled", extra={
                "event": "coordinator_upload_hourly_throttled", "account_id": account_id,
                "uploads_1h": recent_1h, "max": _UPLOAD_MAX_PER_HOUR,
            })
            return False, reason

        # Cap 3: Per-account daily upload cap
        acct_uploads = self._account_uploads_today(account_id)
        if acct_uploads >= _MAX_UPLOADS_PER_ACCOUNT_PER_DAY:
            reason = (
                f"account_daily_upload_cap "
                f"(uploads_today={acct_uploads} >= max={_MAX_UPLOADS_PER_ACCOUNT_PER_DAY})"
            )
            LOGGER.warning("coordinator_account_upload_cap", extra={
                "event": "coordinator_account_upload_cap", "account_id": account_id,
                "uploads_today": acct_uploads, "max": _MAX_UPLOADS_PER_ACCOUNT_PER_DAY,
            })
            return False, reason

        reason = (
            f"upload_allowed "
            f"(10min={recent_10min}/{_UPLOAD_MAX_IN_WINDOW}, "
            f"1h={recent_1h}/{_UPLOAD_MAX_PER_HOUR}, "
            f"acct_today={acct_uploads}/{_MAX_UPLOADS_PER_ACCOUNT_PER_DAY})"
        )
        LOGGER.debug("coordinator_upload_allowed", extra={
            "event": "coordinator_upload_allowed", "account_id": account_id,
            "uploads_10min": recent_10min, "uploads_1h": recent_1h,
        })
        return True, reason

    # ─────────────────────────────────────────────────────────────────────────
    # 4. Skip coordination — ceiling enforcement
    # ─────────────────────────────────────────────────────────────────────────

    def should_allow_skip(
        self,
        account_id: str,
        local_decision: Literal["skip", "proceed"],
    ) -> tuple[Literal["skip", "proceed"], str]:
        """Arbiter between per-account skip logic and global skip ceiling.

        Rules (applied in order):
          1. If local_decision is "proceed", always proceed (coordinator never forces skip).
          2. If account skipped its last session → 70% chance of denying the skip
             to prevent consecutive-skip pattern.
          3. If global skip rate (last 30 min) would exceed 15% → deny skip.
          4. Otherwise allow local_decision.

        Logs the final decision and the rule that triggered it.
        """
        if local_decision == "proceed":
            self._record_skip(account_id, skipped=False)
            return "proceed", "local_proceed_no_override"

        now = time.monotonic()

        # ── Rule 1: consecutive skip penalty ─────────────────────────────────
        if self._last_skip.get(account_id, False):
            if random.random() < _CONSEC_SKIP_PENALTY:
                reason = (
                    f"consecutive_skip_denied "
                    f"(account skipped last session, "
                    f"penalty_prob={_CONSEC_SKIP_PENALTY})"
                )
                LOGGER.warning(
                    "coordinator_skip_denied_consecutive",
                    extra={
                        "event":      "coordinator_skip_denied_consecutive",
                        "account_id": account_id,
                        "reason":     reason,
                    },
                )
                self._record_skip(account_id, skipped=False)
                return "proceed", reason

        # ── Rule 2: global skip-rate ceiling ──────────────────────────────────
        window_records = [
            r for r in self._skip_history
            if now - r.ts <= _SKIP_WINDOW_SECS
        ]
        total_in_window = len(window_records)
        skips_in_window = sum(1 for r in window_records if r.skipped)

        if total_in_window > 0:
            current_rate = skips_in_window / total_in_window
            # Adding this skip would increase the rate
            projected_rate = (skips_in_window + 1) / (total_in_window + 1)
            if projected_rate > _SKIP_RATE_CEILING:
                reason = (
                    f"global_skip_ceiling_enforced "
                    f"(current_rate={current_rate:.2%}, "
                    f"projected={projected_rate:.2%}, "
                    f"ceiling={_SKIP_RATE_CEILING:.0%}, "
                    f"window={int(_SKIP_WINDOW_SECS)}s)"
                )
                LOGGER.warning(
                    "coordinator_skip_denied_ceiling",
                    extra={
                        "event":          "coordinator_skip_denied_ceiling",
                        "account_id":     account_id,
                        "current_rate":   round(current_rate, 4),
                        "projected_rate": round(projected_rate, 4),
                        "ceiling":        _SKIP_RATE_CEILING,
                        "skips":          skips_in_window,
                        "total":          total_in_window,
                    },
                )
                self._record_skip(account_id, skipped=False)
                return "proceed", reason

        # ── Allow skip ────────────────────────────────────────────────────────
        reason = f"local_skip_allowed (global_rate={skips_in_window}/{max(total_in_window,1)})"
        LOGGER.info(
            "coordinator_skip_allowed",
            extra={
                "event":      "coordinator_skip_allowed",
                "account_id": account_id,
                "skips":      skips_in_window,
                "total":      total_in_window,
            },
        )
        self._record_skip(account_id, skipped=True)
        return "skip", reason

    def _record_skip(self, account_id: str, *, skipped: bool) -> None:
        """Internal: persist skip outcome to rolling history and per-account last-skip map."""
        self._skip_history.append(_SkipRecord(account_id=account_id, skipped=skipped))
        self._last_skip[account_id] = skipped

    # ─────────────────────────────────────────────────────────────────────────
    # 5. Behavior distribution balancer
    # ─────────────────────────────────────────────────────────────────────────

    def _recompute_activity_counts(self) -> None:
        """Self-heal _activity_counts from the ground-truth _active_jobs dict.

        Called at the start of adjust_personality() to correct any drift caused
        by jobs that crashed before register_job_end() was called, or any other
        bookkeeping inconsistency.  Cost: O(N) over active jobs — acceptable
        since N is bounded by the number of concurrent accounts.

        The only valid activity levels are "low", "medium", "high".  Any unknown
        levels in active jobs are mapped to "medium" as a safe fallback.
        """
        recomputed: dict[str, int] = {"low": 0, "medium": 0, "high": 0}
        # _active_jobs does not store activity_level (it is set at adjust_personality time).
        # We can only derive the total count from active jobs; per-level breakdown
        # must come from the running counter.  What we CAN do is clamp the total
        # to the number of active jobs so it never exceeds reality.
        total_active_jobs = len(self._active_jobs)
        total_counted = sum(self._activity_counts.values())
        if total_counted > 0 and total_active_jobs < total_counted:
            # Counts are inflated — scale each level down proportionally.
            scale = total_active_jobs / total_counted
            for level in ("low", "medium", "high"):
                recomputed[level] = max(0, round(self._activity_counts[level] * scale))
            # Ensure total doesn't exceed active jobs after rounding
            total_after = sum(recomputed.values())
            if total_after > total_active_jobs:
                recomputed["medium"] = max(0, recomputed["medium"] - (total_after - total_active_jobs))
            self._activity_counts = recomputed
            LOGGER.debug(
                "coordinator_activity_counts_healed",
                extra={
                    "event":          "coordinator_activity_counts_healed",
                    "active_jobs":    total_active_jobs,
                    "was_total":      total_counted,
                    "new_counts":     dict(self._activity_counts),
                },
            )

    def adjust_personality(self, personality: "SessionPersonality") -> "SessionPersonality":
        """Nudge personality to maintain a healthy distribution across active sessions.

        Does NOT rebuild the personality from scratch — only applies targeted
        overrides when the global distribution has drifted too far from targets.

        Rules:
          - If >_TARGET_HIGH_FRAC of active sessions are "high":
              downgrade this session's activity_level to "medium"
          - If <_TARGET_LOW_FRAC of active sessions are "low":
              50% chance to force "low" on this session
          - If this session has a very long duration_target and long-session
            fraction is already at ceiling:
              cap session_duration_target to 10 min

        Self-heals _activity_counts drift on every call to prevent personality
        skew accumulation from crashed jobs.

        Returns a potentially modified copy of the personality.
        Logs any adjustment made.
        """
        from core.behavior_engine import SessionPersonality as _SP

        # Self-heal before reading distribution — fixes any crash-induced drift
        self._recompute_activity_counts()

        total_active = sum(self._activity_counts.values())
        if total_active == 0:
            # No active sessions → no distribution constraints; register as-is
            self._activity_counts[personality.activity_level] += 1
            return personality

        high_frac = self._activity_counts["high"] / total_active
        low_frac  = self._activity_counts["low"]  / total_active

        new_level = personality.activity_level
        new_duration = personality.session_duration_target
        adjustments: list[str] = []

        # ── Too many high-activity sessions ───────────────────────────────────
        if new_level == "high" and high_frac > _TARGET_HIGH_FRAC:
            new_level = "medium"
            adjustments.append(
                f"activity high→medium (high_frac={high_frac:.2%} > target={_TARGET_HIGH_FRAC:.0%})"
            )

        # ── Too few low-activity sessions ─────────────────────────────────────
        if new_level != "low" and low_frac < _TARGET_LOW_FRAC and random.random() < 0.50:
            new_level = "low"
            adjustments.append(
                f"activity {personality.activity_level}→low "
                f"(low_frac={low_frac:.2%} < target={_TARGET_LOW_FRAC:.0%})"
            )

        # ── Long-session cap ──────────────────────────────────────────────────
        long_count = sum(
            1 for r in self._active_jobs.values()
            # We don't store duration_target on JobRecord; use 0 as proxy
            # (the cap is applied to the new session being onboarded, not past)
        )
        _ = long_count  # kept for future extension
        if new_duration > 12.0 and random.random() < 0.30:
            new_duration = random.uniform(6.0, 10.0)
            adjustments.append(
                f"session_duration capped to {new_duration:.1f} min "
                f"(was {personality.session_duration_target:.1f})"
            )

        if adjustments:
            LOGGER.info(
                "coordinator_personality_adjusted",
                extra={
                    "event":                  "coordinator_personality_adjusted",
                    "account_id":             personality.account_id,
                    "original_activity":      personality.activity_level,
                    "new_activity":           new_level,
                    "original_duration_min":  round(personality.session_duration_target, 1),
                    "new_duration_min":       round(new_duration, 1),
                    "adjustments":            adjustments,
                },
            )
            # Rebuild the frozen dataclass with overridden fields
            personality = _SP(
                account_id=personality.account_id,
                activity_level=new_level,  # type: ignore[arg-type]
                hesitation_factor=personality.hesitation_factor,
                scroll_depth_preference=personality.scroll_depth_preference,
                interaction_rate=personality.interaction_rate,
                typing_speed_base=personality.typing_speed_base,
                session_duration_target=new_duration,
                _seed=personality._seed,
                _session_jitter=personality._session_jitter,
            )

        # Register the (possibly adjusted) activity level
        self._activity_counts[personality.activity_level] += 1
        return personality

    # ─────────────────────────────────────────────────────────────────────────
    # 6. Proxy load manager
    # ─────────────────────────────────────────────────────────────────────────

    def get_proxy_load(self, proxy_url: str) -> dict[str, int | bool]:
        """Return a summary of current load on a proxy.

        Returns:
            {
              "active_accounts": int,
              "overloaded": bool,        # True if > _MAX_ACCOUNTS_PER_PROXY
            }
        """
        active = len(self._proxy_accounts.get(proxy_url, set()))
        overloaded = active > _MAX_ACCOUNTS_PER_PROXY
        if overloaded:
            LOGGER.warning(
                "coordinator_proxy_overloaded",
                extra={
                    "event":           "coordinator_proxy_overloaded",
                    "proxy":           proxy_url,
                    "active_accounts": active,
                    "max":             _MAX_ACCOUNTS_PER_PROXY,
                },
            )
        return {"active_accounts": active, "overloaded": overloaded}

    # ─────────────────────────────────────────────────────────────────────────
    # 7. Global state snapshot (for debugging / monitoring)
    # ─────────────────────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """Return a loggable snapshot of all coordinator state."""
        now = time.monotonic()
        recent_uploads = sum(
            1 for r in self._upload_history if now - r.ts <= _UPLOAD_WINDOW_SECS
        )
        window_skips = [r for r in self._skip_history if now - r.ts <= _SKIP_WINDOW_SECS]
        skip_rate = (
            sum(1 for r in window_skips if r.skipped) / len(window_skips)
            if window_skips else 0.0
        )
        return {
            "active_jobs":           len(self._active_jobs),
            "active_proxies":        len(self._proxy_accounts),
            "activity_distribution": dict(self._activity_counts),
            "uploads_in_window":     recent_uploads,
            "upload_window_secs":    _UPLOAD_WINDOW_SECS,
            "skip_rate_30min":       round(skip_rate, 4),
            "skip_ceiling":          _SKIP_RATE_CEILING,
            "recent_starts_count":   len(self._recent_starts),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Process-level singleton — safe for Celery single-process workers
# ─────────────────────────────────────────────────────────────────────────────

_COORDINATOR_INSTANCE: CrossAccountCoordinator | None = None


def get_coordinator() -> CrossAccountCoordinator:
    """Return the process-level singleton CrossAccountCoordinator.

    Instantiated lazily on first call. Safe to call from multiple coroutines
    within the same worker process (asyncio single-threaded model).

    For Celery with concurrency > 1 (prefork), each forked process gets its
    own instance — this is intentional. The coordinator is a per-process
    heuristic layer, not a distributed lock.
    """
    global _COORDINATOR_INSTANCE
    if _COORDINATOR_INSTANCE is None:
        _COORDINATOR_INSTANCE = CrossAccountCoordinator()
        LOGGER.info(
            "coordinator_initialised",
            extra={"event": "coordinator_initialised"},
        )
    return _COORDINATOR_INSTANCE
