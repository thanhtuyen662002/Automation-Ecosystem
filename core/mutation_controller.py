"""
Mutation Controller — Gatekeeper for all IdentityProfile fingerprint mutations.

Design contract:
  - StealthBrain CANNOT modify IdentityProfile directly.
  - All mutations go through MutationController.apply(profile, strategy).
  - Mutations are deterministic (seed-based), never fully random.
  - Continuity constraints: device_type, OS, locale, timezone are FROZEN.

Shared types (RiskLevel, Action, Strategy) are imported by StealthBrain
and any other caller that needs to communicate mutation intent.

Usage:
    from core.mutation_controller import (
        get_mutation_controller, Strategy, Action, RiskLevel,
    )

    strategy = Strategy(
        risk_level=RiskLevel.MEDIUM,
        actions=[Action("rotate_gpu", ["webgl_noise_seed"], 0.5)],
    )
    result = get_mutation_controller().apply(profile, strategy)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.identity_manager import IdentityProfile

LOGGER = logging.getLogger("core.mutation_controller")


# ── Drift limits ───────────────────────────────────────────────────────────
# After MAX_DRIFT partial mutations the identity is forced to a full
# regeneration from base so it never drifts into a different "person".
MAX_DRIFT: int = 3
# Hamming distance threshold (as fraction of hash length).
# If active diverges > 50% of bits from base → force regen.
_MAX_DISTANCE: float = 0.50


# ── Shared types ──────────────────────────────────────────────────────────────

class RiskLevel(str, Enum):
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"


@dataclass
class Action:
    """A single mutation instruction from StealthBrain to MutationController."""
    type: str            # "rotate_gpu" | "rotate_canvas" | "rotate_audio"
                         # "sync_geo"   | "cooldown"
    targets: list[str]   # profile field names that will be modified
    intensity: float     # 0.0–1.0 (how aggressively to mutate)
    metadata: dict[str, Any] = field(default_factory=dict)  # extra data (e.g. new_tz)


@dataclass
class Strategy:
    """Full instruction set returned by StealthBrain."""
    risk_level: RiskLevel
    actions: list[Action]
    delay_multiplier: float     = 1.0
    warmup_delay: float         = 5.0
    interaction_mode: str       = "NORMAL"   # "SAFE" | "NORMAL" | "AGGRESSIVE"
    fingerprint_patch_level: str = "STRICT"  # "STRICT" | "RELAXED"


@dataclass
class MutationResult:
    """Result of MutationController.apply()."""
    mutation_type: str          # "none" | "partial" | "full"
    changed_fields: list[str]
    new_fingerprint_hash: str
    mutation_state: int


# ── Mutation Controller ───────────────────────────────────────────────────────

class MutationController:
    """
    Stateless gatekeeper: all IdentityProfile mutations go through apply().

    Enforces:
      - Continuity constraints (device_type / OS / locale / timezone frozen)
      - Deterministic drift (seed-based, reproducible from base_fingerprint)
      - Audit trail (every mutation logged to profile.mutation_history)
    """

    def apply(
        self,
        profile: "IdentityProfile",
        strategy: Strategy,
        slot_offset: int = 0,
    ) -> MutationResult:
        """Apply strategy to profile. Returns a MutationResult describing changes.

        Drift guard: if MEDIUM is requested but mutation_state >= MAX_DRIFT,
        or if fingerprint_distance(base, active) > _MAX_DISTANCE, the call
        is automatically escalated to HIGH and the drift counter is reset.
        """
        # ── Drift guard ──────────────────────────────────────────────
        if strategy.risk_level == RiskLevel.MEDIUM:
            dist = fingerprint_distance(profile.base_fingerprint, profile.active_fingerprint)
            # Count partials accumulated since the last full regen.
            # mutation_state only increments on full regens, so we inspect history.
            count = 0
            for entry in reversed(profile.mutation_history):
                if entry.get("type") == "full":
                    break
                if entry.get("type") == "partial":
                    count += 1
            partials_since_full = count

            drift_exceeded = (
                partials_since_full >= MAX_DRIFT
                or dist > _MAX_DISTANCE
            )


            if drift_exceeded:
                LOGGER.warning("mutation_drift_exceeded", extra={
                    "account_id":     profile.account_id,
                    "mutation_state": profile.mutation_state,
                    "fingerprint_distance": round(dist, 3),
                    "max_drift":      MAX_DRIFT,
                    "action":         "escalate_to_full",
                })
                # Escalate: full regen resets mutation_state counter to 0
                escalated = Strategy(
                    risk_level   = RiskLevel.HIGH,
                    actions      = strategy.actions,
                    delay_multiplier    = strategy.delay_multiplier,
                    warmup_delay        = strategy.warmup_delay,
                    interaction_mode    = strategy.interaction_mode,
                    fingerprint_patch_level = strategy.fingerprint_patch_level,
                )
                return self._apply_full(profile, escalated, slot_offset)

        if strategy.risk_level == RiskLevel.LOW:
            return self._apply_none(profile)
        elif strategy.risk_level == RiskLevel.MEDIUM:
            return self._apply_partial(profile, strategy, slot_offset)
        else:
            return self._apply_full(profile, strategy, slot_offset)

    # ── None ──────────────────────────────────────────────────────────────────

    def _apply_none(self, profile: "IdentityProfile") -> MutationResult:
        LOGGER.debug("mutation_skipped", extra={
            "account_id": profile.account_id, "reason": "low_risk",
        })
        return MutationResult(
            mutation_type="none",
            changed_fields=[],
            new_fingerprint_hash=profile.fingerprint_hash,
            mutation_state=profile.mutation_state,
        )

    # ── Partial (MEDIUM) ──────────────────────────────────────────────────────

    def _apply_partial(
        self,
        profile: "IdentityProfile",
        strategy: Strategy,
        slot_offset: int,
    ) -> MutationResult:
        from core.identity_manager import generate_fingerprint, _seeded_int

        action_types = {a.type for a in strategy.actions}
        changed: list[str] = []

        # Seeds drift deterministically from current seeds + mutation_state
        # so they are reproducible. device/OS/locale/timezone: FROZEN.
        drift_base = (
            (profile.canvas_noise_seed ^ profile.webgl_noise_seed)
            + (profile.mutation_state + slot_offset + 1) * 0x6FDE5
        ) & 0xFFFFFFFF

        if "rotate_gpu" in action_types:
            profile.webgl_noise_seed = _seeded_int(drift_base, 50 + slot_offset, 100_000, 999_999)
            changed.append("webgl_noise_seed")

        if "rotate_canvas" in action_types or "rotate_audio" in action_types:
            profile.canvas_noise_seed = _seeded_int(drift_base, 60 + slot_offset, 100_000, 999_999)
            changed.append("canvas_noise_seed")

        # Geo sync: safe to mutate locale/timezone (doesn't break device identity)
        if "sync_geo" in action_types:
            for action in strategy.actions:
                if action.type == "sync_geo":
                    new_tz   = action.metadata.get("timezone")
                    new_lang = action.metadata.get("language")
                    if new_tz and new_tz != profile.timezone:
                        profile.timezone = new_tz
                        changed.append("timezone")
                    if new_lang and new_lang != profile.locale:
                        profile.locale = new_lang
                        changed.append("locale")

        new_hash = generate_fingerprint(profile)
        profile.fingerprint_hash   = new_hash
        profile.active_fingerprint = new_hash

        self._record_history(profile, "partial", changed, new_hash)

        LOGGER.info("mutation_partial_applied", extra={
            "account_id":    profile.account_id,
            "changed_fields": changed,
            "new_hash":       new_hash[:12],
            "mutation_state": profile.mutation_state,
        })
        return MutationResult(
            mutation_type="partial",
            changed_fields=changed,
            new_fingerprint_hash=new_hash,
            mutation_state=profile.mutation_state,
        )

    # ── Full (HIGH) ───────────────────────────────────────────────────────────

    def _apply_full(
        self,
        profile: "IdentityProfile",
        strategy: Strategy,
        slot_offset: int,
    ) -> MutationResult:
        from core.identity_manager import generate_fingerprint, _seeded_int

        # Deterministic regen from base_fingerprint + new mutation_state
        base_seed = (
            int(profile.base_fingerprint[:8], 16)
            if profile.base_fingerprint else profile.canvas_noise_seed
        )
        new_state = profile.mutation_state + 1

        profile.webgl_noise_seed  = _seeded_int(base_seed, 200 + new_state, 100_000, 999_999)
        profile.canvas_noise_seed = _seeded_int(base_seed, 201 + new_state, 100_000, 999_999)
        profile.mutation_state    = new_state

        # Continuity: device_type / OS / locale / timezone NOT touched
        new_hash = generate_fingerprint(profile)
        profile.fingerprint_hash   = new_hash
        profile.active_fingerprint = new_hash

        changed = ["webgl_noise_seed", "canvas_noise_seed", "fingerprint_hash"]
        self._record_history(profile, "full", changed, new_hash)

        LOGGER.warning("mutation_full_applied", extra={
            "account_id":    profile.account_id,
            "new_hash":       new_hash[:12],
            "mutation_state": new_state,
        })
        return MutationResult(
            mutation_type="full",
            changed_fields=changed,
            new_fingerprint_hash=new_hash,
            mutation_state=new_state,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _record_history(
        profile: "IdentityProfile",
        mut_type: str,
        changed: list[str],
        new_hash: str,
    ) -> None:
        """Append a mutation record to profile.mutation_history (max 20)."""
        entry: dict[str, Any] = {
            "ts":             time.time(),
            "type":           mut_type,
            "changed":        changed,
            "hash":           new_hash[:12],
            "mutation_state": profile.mutation_state,
        }
        profile.mutation_history.append(entry)
        if len(profile.mutation_history) > 20:
            profile.mutation_history.pop(0)


# ── Singleton ─────────────────────────────────────────────────────────────────

_MUTATION_CONTROLLER: MutationController | None = None


def get_mutation_controller() -> MutationController:
    """Return the process-level MutationController singleton."""
    global _MUTATION_CONTROLLER
    if _MUTATION_CONTROLLER is None:
        _MUTATION_CONTROLLER = MutationController()
    return _MUTATION_CONTROLLER


def fingerprint_distance(base: str, active: str) -> float:
    """Hamming bit-distance between two hex fingerprint hashes (0.0=identical, 1.0=opposite).

    Compares the shared prefix length (min of both). Returns fraction of bits
    that differ. Used by MutationController to detect identity drift.

    Example:
        fingerprint_distance("aabbccdd", "aabbccdd") == 0.0   # identical
        fingerprint_distance("aabbccdd", "ffeebbaa") ~= 0.5   # half bits differ
    """
    if not base or not active:
        return 0.0
    length = min(len(base), len(active))
    # Compare byte-by-byte via XOR on hex pairs
    diff_bits = 0
    total_bits = 0
    for i in range(0, length - 1, 2):
        b1 = int(base[i:i+2], 16)
        b2 = int(active[i:i+2], 16)
        xor = b1 ^ b2
        diff_bits  += bin(xor).count("1")
        total_bits += 8
    return diff_bits / total_bits if total_bits else 0.0
