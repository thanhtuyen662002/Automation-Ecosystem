"""
Stealth Brain — Adaptive anti-detect decision engine.

Architecture:
    RuntimeValidator  →  RuntimeSignals  →  StealthBrain.evaluate()
                                                     │
                                                     ▼
                                               Strategy (risk_level + actions[])
                                                     │
                                                     ▼
                                         MutationController.apply(profile, strategy)

Design contract:
  - StealthBrain NEVER modifies IdentityProfile directly.
  - It reads RuntimeSignals, inspects StealthMemory, and returns a Strategy.
  - MutationController executes the strategy against the profile.
  - This separation makes the mutation logic testable and auditable.

Usage:
    from core.stealth_brain import get_stealth_brain
    from core.runtime_validator import to_runtime_signals, validate_fingerprint
    from core.mutation_controller import get_mutation_controller

    # After page.goto():
    risk    = await validate_fingerprint(page, profile)
    signals = to_runtime_signals(risk)
    strategy = get_stealth_brain().evaluate(account_id, signals, profile)
    result  = get_mutation_controller().apply(profile, strategy)

    # After session completes:
    get_stealth_brain().process_session_outcome(account_id, "success", profile)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from core.runtime_validator import RuntimeSignals
from core.mutation_controller import (
    RiskLevel, Action, Strategy,
    MutationResult,
)
from core.identity_manager import IdentityProfile

LOGGER = logging.getLogger("core.stealth_brain")


# ── StealthMemory ─────────────────────────────────────────────────────────────

@dataclass
class StealthMemory:
    """Persistent per-account brain state (separate from IdentityProfile)."""
    account_id: str
    banned_fingerprints: list[str] = field(default_factory=list)
    total_sessions:    int = 0
    total_bans:        int = 0
    total_checkpoints: int = 0
    consecutive_bad:   int = 0   # Consecutive sessions with risk >= 0.30
    # Rolling outcome records (last 10) — drives adaptive escalation
    outcome_history:   list = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id":          self.account_id,
            "banned_fingerprints": self.banned_fingerprints,
            "total_sessions":      self.total_sessions,
            "total_bans":          self.total_bans,
            "total_checkpoints":   self.total_checkpoints,
            "consecutive_bad":     self.consecutive_bad,
            "outcome_history":     self.outcome_history,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StealthMemory":
        return cls(
            account_id         = d["account_id"],
            banned_fingerprints= d.get("banned_fingerprints", []),
            total_sessions     = d.get("total_sessions", 0),
            total_bans         = d.get("total_bans", 0),
            total_checkpoints  = d.get("total_checkpoints", 0),
            consecutive_bad    = d.get("consecutive_bad", 0),
            outcome_history    = d.get("outcome_history", []),
        )


# ── StealthBrain ──────────────────────────────────────────────────────────────

class StealthBrain:
    """
    Decision engine: normalised signals in → typed Strategy out.
    Never modifies IdentityProfile. Never calls MutationController directly.
    """

    def __init__(self) -> None:
        self._memories: dict[str, StealthMemory] = {}

    def get_memory(self, account_id: str) -> StealthMemory:
        if account_id not in self._memories:
            self._memories[account_id] = StealthMemory(account_id=account_id)
        return self._memories[account_id]

    # ── Public API ────────────────────────────────────────────────────────────

    def evaluate(
        self,
        account_id: str,
        signals: RuntimeSignals,
        profile: IdentityProfile,
    ) -> Strategy:
        """Evaluate RuntimeSignals and return a typed Strategy.

        Risk level classification:
          LOW    (score < 0.30)  : no mutation
          MEDIUM (0.30 - 0.60)   : partial — rotate rendering seeds only
          HIGH   (score >= 0.60) : full — regen from base + increment mutation_state

        Escalation rules (in priority order):
          1. webdriver exposed          → always HIGH
          2. fingerprint in ban list    → always HIGH
          3. 3+ recent captchas         → escalate to HIGH immediately
          4. blocked outcome            → escalate to HIGH
          5. 3+ consecutive bad signals → bump one tier
          6. 3+ recent successes        → bias toward LOW (de-escalate)
        """
        mem = self.get_memory(account_id)

        # Record risk score into profile history (max 10)
        risk_entry: dict[str, Any] = {
            "ts":         time.time(),
            "score":      signals.risk_score,
            "breakdown":  dict(signals.breakdown),
        }
        profile.risk_history.append(risk_entry)
        if len(profile.risk_history) > 10:
            profile.risk_history.pop(0)

        # ── Outcome-driven pre-evaluation ────────────────────────────────
        recent = mem.outcome_history[-10:]   # last 10 outcomes
        recent_captchas  = sum(1 for o in recent if o.get("captcha"))
        recent_blocked   = any(o.get("blocked") for o in recent[-3:])
        recent_successes = sum(1 for o in recent[-5:] if o.get("upload_success"))

        # Behavioral risk signals from Layer 4 (completion_ratio, session_duration)
        recent_suspicious = sum(
            1 for o in recent[-5:]
            if o.get("suspicious_short") or o.get("suspicious_abandon")
            or o.get("completion_ratio", 1.0) < 0.30
        )
        # Independently check session_duration vs expected (stored in record)
        recent_short_dur = sum(
            1 for o in recent[-5:]
            if o.get("session_duration", 999) > 0
            and o.get("estimated_duration", 0) > 0
            and o.get("session_duration", 999) < o.get("estimated_duration", 1) * 0.30
        )

        # Update consecutive bad counter
        if signals.risk_score >= 0.30:
            mem.consecutive_bad += 1
        else:
            mem.consecutive_bad = 0


        # Determine base risk level
        base_risk = self._classify_risk(signals.risk_score)
        effective_risk = base_risk

        # Rule 6: 3+ recent successes → de-escalate one tier
        if recent_successes >= 3 and effective_risk == RiskLevel.MEDIUM:
            effective_risk = RiskLevel.LOW
            LOGGER.info("stealth_deescalation", extra={
                "account_id": account_id, "reason": "repeated_success",
                "recent_successes": recent_successes,
            })

        # Rule 5: 3+ consecutive bad signals → bump one tier
        if mem.consecutive_bad >= 3 and effective_risk == RiskLevel.LOW:
            effective_risk = RiskLevel.MEDIUM
        elif mem.consecutive_bad >= 3 and effective_risk == RiskLevel.MEDIUM:
            effective_risk = RiskLevel.HIGH

        # Rule 4.5: Behavioral risk — short/abandoned sessions signal bot-like behavior
        if recent_suspicious >= 2 or recent_short_dur >= 2:
            if effective_risk == RiskLevel.LOW:
                effective_risk = RiskLevel.MEDIUM
            elif effective_risk == RiskLevel.MEDIUM:
                effective_risk = RiskLevel.HIGH
            LOGGER.warning("stealth_behavioral_escalation", extra={
                "account_id":       account_id,
                "recent_suspicious":recent_suspicious,
                "recent_short_dur": recent_short_dur,
                "effective_risk":   effective_risk.value,
            })

        # Rule 4: recent blocked outcome → HIGH
        if recent_blocked:
            effective_risk = RiskLevel.HIGH
            LOGGER.warning("stealth_escalation_blocked", extra={
                "account_id": account_id, "reason": "recent_blocked_outcome",
            })

        # Rule 3: 3+ captchas in recent history → HIGH immediately
        if recent_captchas >= 3:
            effective_risk = RiskLevel.HIGH
            LOGGER.warning("stealth_escalation_captcha", extra={
                "account_id": account_id,
                "reason": "repeated_captcha",
                "recent_captchas": recent_captchas,
            })

        # Rule 2: banned fingerprint → force HIGH regardless
        if profile.fingerprint_hash in mem.banned_fingerprints:
            effective_risk = RiskLevel.HIGH
            LOGGER.warning("stealth_banned_fingerprint_detected", extra={
                "account_id": account_id, "hash": profile.fingerprint_hash[:12],
            })

        # Rule 1: webdriver exposed → always HIGH (critical leak)
        if not signals.webdriver_hidden:
            effective_risk = RiskLevel.HIGH


        actions   = self._build_actions(signals, profile)
        strategy  = self._build_strategy(effective_risk, actions, signals)

        LOGGER.info("stealth_strategy_produced", extra={
            "account_id":      account_id,
            "risk_score":      round(signals.risk_score, 3),
            "base_risk":       base_risk.value,
            "effective_risk":  effective_risk.value,
            "consecutive_bad": mem.consecutive_bad,
            "actions":         [a.type for a in actions],
            "interaction_mode": strategy.interaction_mode,
        })
        return strategy

    def record_outcome(
        self,
        account_id: str,
        outcome: dict[str, Any],
        profile: IdentityProfile,
    ) -> None:
        """Record a structured session outcome and update memory + profile risk_history.

        Outcome schema (all fields optional):
            {
              # Layer 3 stealth signals
              "upload_success":    bool,
              "captcha":           bool,
              "blocked":           bool,
              "shadow_ban_signal": bool,
              # Layer 4 behavior signals (from BehavioralBrain.analyze_session())
              "session_duration":  float,   # actual seconds
              "actions_count":     int,
              "abandoned_actions": int,
              "completion_ratio":  float,   # actual / estimated duration
              "suspicious_short":  bool,
              "suspicious_abandon":bool,
            }
        """
        mem = self.get_memory(account_id)
        record = {
            "ts":                time.time(),
            "upload_success":    bool(outcome.get("upload_success",    False)),
            "captcha":           bool(outcome.get("captcha",           False)),
            "blocked":           bool(outcome.get("blocked",           False)),
            "shadow_ban_signal": bool(outcome.get("shadow_ban_signal", False)),
            "fingerprint":       profile.fingerprint_hash[:12],
            # Layer 4 behavioral signals
            "session_duration":  float(outcome.get("session_duration",  0.0)),
            "actions_count":     int(outcome.get("actions_count",       0)),
            "abandoned_actions": int(outcome.get("abandoned_actions",   0)),
            "completion_ratio":   float(outcome.get("completion_ratio",   1.0)),
            "suspicious_short":   bool(outcome.get("suspicious_short",   False)),
            "suspicious_abandon": bool(outcome.get("suspicious_abandon",  False)),
            "estimated_duration": float(outcome.get("estimated_duration", 0.0)),
        }


        # Detect behavioral risk: too-short sessions or high abandon → flag
        if record["suspicious_short"] or record["suspicious_abandon"]:
            LOGGER.warning("stealth_behavioral_risk_flagged", extra={
                "account_id":        account_id,
                "suspicious_short":  record["suspicious_short"],
                "suspicious_abandon":record["suspicious_abandon"],
                "completion_ratio":  record["completion_ratio"],
            })

        mem.outcome_history.append(record)
        if len(mem.outcome_history) > 10:
            mem.outcome_history.pop(0)

        # Also persist into profile.risk_history so it survives restarts
        profile.risk_history.append({"outcome": record})
        if len(profile.risk_history) > 10:
            profile.risk_history.pop(0)

        if record["blocked"]:
            if profile.fingerprint_hash not in mem.banned_fingerprints:
                mem.banned_fingerprints.append(profile.fingerprint_hash)
            mem.total_bans += 1
            LOGGER.critical("stealth_outcome_blocked", extra={
                "account_id": account_id,
                "hash":       profile.fingerprint_hash[:12],
                "total_bans": mem.total_bans,
            })

        elif record["captcha"]:
            mem.total_checkpoints += 1
            LOGGER.warning("stealth_outcome_captcha", extra={
                "account_id":       account_id,
                "total_captchas":   mem.total_checkpoints,
            })

        elif record["shadow_ban_signal"]:
            LOGGER.warning("stealth_outcome_shadow_ban", extra={
                "account_id": account_id,
                "fingerprint": profile.fingerprint_hash[:12],
            })

        elif record["upload_success"]:
            LOGGER.info("stealth_outcome_success", extra={
                "account_id": account_id,
            })

    def process_session_outcome(
        self,
        account_id: str,
        outcome: Literal["success", "checkpoint", "ban"],
        profile: IdentityProfile,
    ) -> None:
        """Legacy string-based outcome recorder. Prefer record_outcome() for new code."""
        mapping = {
            "success":    {"upload_success": True,  "captcha": False, "blocked": False, "shadow_ban_signal": False},
            "checkpoint": {"upload_success": False, "captcha": True,  "blocked": False, "shadow_ban_signal": False},
            "ban":        {"upload_success": False, "captcha": False, "blocked": True,  "shadow_ban_signal": False},
        }
        self.record_outcome(account_id, mapping.get(outcome, mapping["success"]), profile)

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _classify_risk(score: float) -> RiskLevel:
        if score >= 0.60:
            return RiskLevel.HIGH
        if score >= 0.30:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    @staticmethod
    def _build_actions(signals: RuntimeSignals, profile: IdentityProfile) -> list[Action]:
        """Translate normalized signals into a typed Action list."""
        actions: list[Action] = []

        # Rendering issues → rotate GPU + canvas
        if not signals.webgl_vendor_match or not signals.webgl_renderer_match:
            actions.append(Action(
                type="rotate_gpu",
                targets=["webgl_noise_seed"],
                intensity=0.8 if not signals.webgl_vendor_match else 0.4,
            ))

        if not signals.language_match or not signals.platform_match:
            actions.append(Action(
                type="rotate_canvas",
                targets=["canvas_noise_seed"],
                intensity=0.5,
            ))

        # Geo mismatch → sync geo (metadata carries detected values)
        if not signals.timezone_match or not signals.language_match:
            actions.append(Action(
                type="sync_geo",
                targets=["timezone", "locale"],
                intensity=1.0,
                # NOTE: actual detected values would come from runtime_env;
                # here we emit the action — the coordinator fills metadata.
                metadata={},
            ))

        # Behavioral signals → add cooldown action
        if not signals.webdriver_hidden or not signals.eval_ok:
            actions.append(Action(
                type="cooldown",
                targets=[],
                intensity=1.0,
            ))

        # Screen/hardware → rotate canvas noise
        if not signals.screen_match or not signals.hardware_match:
            actions.append(Action(
                type="rotate_canvas",
                targets=["canvas_noise_seed"],
                intensity=0.3,
            ))

        return actions

    @staticmethod
    def _build_strategy(
        risk: RiskLevel,
        actions: list[Action],
        signals: RuntimeSignals,
    ) -> Strategy:
        """Map risk level to delay/warmup/interaction parameters."""
        if risk == RiskLevel.LOW:
            return Strategy(
                risk_level           = RiskLevel.LOW,
                actions              = actions,
                delay_multiplier     = 1.0,
                warmup_delay         = 5.0,
                interaction_mode     = "NORMAL",
                fingerprint_patch_level = "STRICT",
            )
        elif risk == RiskLevel.MEDIUM:
            return Strategy(
                risk_level           = RiskLevel.MEDIUM,
                actions              = actions,
                delay_multiplier     = 1.5,
                warmup_delay         = 15.0,
                interaction_mode     = "SAFE",
                fingerprint_patch_level = "STRICT",
            )
        else:  # HIGH
            return Strategy(
                risk_level           = RiskLevel.HIGH,
                actions              = actions,
                delay_multiplier     = 2.5,
                warmup_delay         = 30.0,
                interaction_mode     = "SAFE",
                fingerprint_patch_level = "STRICT",
            )

    # ── Persistence helpers ───────────────────────────────────────────────────

    def snapshot_all(self) -> dict[str, Any]:
        return {k: v.to_dict() for k, v in self._memories.items()}

    def load_all(self, data: dict[str, dict[str, Any]]) -> None:
        for k, v in data.items():
            self._memories[k] = StealthMemory.from_dict(v)


# ── Singleton ─────────────────────────────────────────────────────────────────

_STEALTH_BRAIN: StealthBrain | None = None


def get_stealth_brain() -> StealthBrain:
    """Return the process-level StealthBrain singleton."""
    global _STEALTH_BRAIN
    if _STEALTH_BRAIN is None:
        _STEALTH_BRAIN = StealthBrain()
    return _STEALTH_BRAIN
