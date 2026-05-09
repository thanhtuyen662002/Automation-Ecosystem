"""
Observer — Full decision-pipeline tracing + account timeline replay.

Design contracts:
  - Observer is append-only; never modifies behavior.
  - Every ActionLog is self-contained (can be replayed without live state).
  - Reasoning trace lists every modifier that influenced the decision.
  - No cross-account data is stored in a single log entry.
  - Memory-bounded: max _MAX_LOG_PER_ACCOUNT entries per account.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

LOGGER = logging.getLogger("core.observer")

_MAX_LOG_PER_ACCOUNT: int = 200   # rolling cap


@dataclass
class ModifierSnapshot:
    """All behavioral multipliers active at decision time."""
    persona_activity:   float = 0.5
    persona_risk_tol:   float = 0.5
    persona_niche:      str   = "unknown"
    role:               str   = "UNKNOWN"
    platform:           str   = "generic"
    strategy_intensity: float = 0.5
    mood:               str   = "normal"
    wave_intensity:     float = 0.5
    trend_intensity:    float = 0.5
    ban_rate:           float = 0.0
    timing_offset_s:    int   = 0


@dataclass
class ActionLog:
    """Complete auditable record for one account action."""
    # Identity
    account_id:      str
    ts:              float
    # What happened
    platform:        str
    role:            str
    intent:          str
    delay_s:         int            # actual delay used
    niche:           str
    # Modifiers snapshot
    modifiers:       ModifierSnapshot
    # Full reasoning trace — list of (layer, description) tuples
    reasoning_trace: list[tuple[str, str]] = field(default_factory=list)
    # Outcome (filled in after execution)
    success:         bool | None = None
    ban:             bool | None = None
    anomaly_score:   float       = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id":    self.account_id,
            "ts":            self.ts,
            "platform":      self.platform,
            "role":          self.role,
            "intent":        self.intent,
            "delay_s":       self.delay_s,
            "niche":         self.niche,
            "modifiers": {
                "persona_activity":   self.modifiers.persona_activity,
                "persona_risk_tol":   self.modifiers.persona_risk_tol,
                "persona_niche":      self.modifiers.persona_niche,
                "role":               self.modifiers.role,
                "platform":           self.modifiers.platform,
                "strategy_intensity": self.modifiers.strategy_intensity,
                "mood":               self.modifiers.mood,
                "wave_intensity":     self.modifiers.wave_intensity,
                "trend_intensity":    self.modifiers.trend_intensity,
                "ban_rate":           self.modifiers.ban_rate,
                "timing_offset_s":    self.modifiers.timing_offset_s,
            },
            "reasoning_trace": [{"layer": l, "reason": r} for l, r in self.reasoning_trace],
            "success":       self.success,
            "ban":           self.ban,
            "anomaly_score": self.anomaly_score,
        }


class Observer:
    """
    Immutable audit log and decision tracer.

    Usage:
        obs = get_observer()
        log = obs.record_plan(account_id, plan, delay_s=120, modifiers=...)
        obs.record_outcome(log, success=True, ban=False)
        timeline = obs.replay(account_id)
    """

    def __init__(self) -> None:
        self._logs: dict[str, list[ActionLog]] = {}

    # ── Logging ───────────────────────────────────────────────────────────────

    def record_plan(
        self,
        account_id: str,
        platform:   str,
        role:       str,
        intent:     str,
        delay_s:    int,
        niche:      str,
        modifiers:  ModifierSnapshot,
        extra_reasoning: list[tuple[str, str]] | None = None,
    ) -> ActionLog:
        """Create and store an ActionLog for a planned action."""
        trace = self._build_trace(platform, role, intent, modifiers)
        if extra_reasoning:
            trace.extend(extra_reasoning)

        log = ActionLog(
            account_id      = account_id,
            ts              = time.time(),
            platform        = platform,
            role            = role,
            intent          = intent,
            delay_s         = delay_s,
            niche           = niche,
            modifiers       = modifiers,
            reasoning_trace = trace,
        )

        bucket = self._logs.setdefault(account_id, [])
        bucket.append(log)
        if len(bucket) > _MAX_LOG_PER_ACCOUNT:
            bucket.pop(0)

        LOGGER.debug(
            "observer_plan account=%s role=%s intent=%s delay=%ds platform=%s",
            account_id, role, intent, delay_s, platform,
        )
        return log

    def record_outcome(
        self,
        log:           ActionLog,
        success:       bool,
        ban:           bool,
        anomaly_score: float = 0.0,
    ) -> None:
        """Fill in the outcome fields of an existing ActionLog in-place."""
        log.success       = success
        log.ban           = ban
        log.anomaly_score = anomaly_score
        log.reasoning_trace.append(
            ("outcome", f"success={success} ban={ban} anomaly={anomaly_score:.3f}")
        )

    # ── Replay ────────────────────────────────────────────────────────────────

    def replay(self, account_id: str) -> list[dict[str, Any]]:
        """Return the full timeline for account_id, oldest first."""
        return [log.to_dict() for log in self._logs.get(account_id, [])]

    def latest(self, account_id: str) -> dict[str, Any] | None:
        logs = self._logs.get(account_id, [])
        return logs[-1].to_dict() if logs else None

    def all_logs(self) -> list[dict[str, Any]]:
        """Flat list of all logs across all accounts (newest last)."""
        out = []
        for logs in self._logs.values():
            out.extend(log.to_dict() for log in logs)
        out.sort(key=lambda x: x["ts"])
        return out

    def log_count(self, account_id: str | None = None) -> int:
        if account_id:
            return len(self._logs.get(account_id, []))
        return sum(len(v) for v in self._logs.values())

    # ── Trace builder ─────────────────────────────────────────────────────────

    @staticmethod
    def _build_trace(
        platform: str,
        role:     str,
        intent:   str,
        m:        ModifierSnapshot,
    ) -> list[tuple[str, str]]:
        """Build a structured reasoning trace from modifier snapshot."""
        trace: list[tuple[str, str]] = [
            ("persona",   f"activity={m.persona_activity:.2f} risk_tol={m.persona_risk_tol:.2f} "
                          f"niche={m.persona_niche}"),
            ("role",      f"assigned={role} intensity={m.strategy_intensity:.2f}"),
            ("platform",  f"platform={platform}"),
            ("environment", f"wave={m.wave_intensity:.2f} trend={m.trend_intensity:.2f}"),
            ("mood",      f"mood={m.mood}"),
            ("feedback",  f"ban_rate={m.ban_rate:.3f}"),
            ("timing",    f"offset={m.timing_offset_s}s"),
            ("decision",  f"intent={intent}"),
        ]
        return trace


# ── Singleton ──────────────────────────────────────────────────────────────────

_OBSERVER: Observer | None = None


def get_observer() -> Observer:
    global _OBSERVER
    if _OBSERVER is None:
        _OBSERVER = Observer()
    return _OBSERVER


def reset_observer() -> None:
    """For testing only."""
    global _OBSERVER
    _OBSERVER = None
