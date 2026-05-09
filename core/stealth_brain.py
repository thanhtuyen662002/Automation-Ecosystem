"""
Stealth Brain — Anti-detect decision engine.

Architecture:
    RuntimeSignals → StealthBrain.evaluate() → Strategy → MutationController.apply()

Design contracts:
  - StealthBrain NEVER modifies IdentityProfile directly.
  - Each account is fully isolated: no shared in-process state between accounts.
  - Risk classification uses fixed thresholds: LOW < 0.3, MEDIUM 0.3–0.7, HIGH > 0.7.
  - StealthMemory is per-account only.
  - Escalation rules are explicit, ordered, and logged with a single reason string.
  - GlobalMemory is ADVISORY ONLY: it contributes at most +0.10 to risk_score
    and can trigger a hard-filter HIGH if a fingerprint is globally banned.
    Local decisions always take precedence.

StealthMemory keeps:
  - banned_fingerprints: list[{hash, expires_at}] with 7-day TTL, auto-purged.
  - outcome_history: last 10 session records (no weighting, plain list).
  - consecutive_bad: int counter reset on each clean signal.
  - session counters: total_sessions, total_bans, total_checkpoints.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from core.runtime_validator import RuntimeSignals
from core.mutation_controller import RiskLevel, Action, Strategy
from core.identity_manager import IdentityProfile

LOGGER = logging.getLogger("core.stealth_brain")

# ── Constants ─────────────────────────────────────────────────────────────────

# Fixed risk thresholds — do NOT make these dynamic.
RISK_LOW_MAX:  float = 0.30   # score < 0.30  → LOW
RISK_HIGH_MIN: float = 0.70   # score >= 0.70 → HIGH
# 0.30 <= score < 0.70 → MEDIUM

# Banned fingerprint TTL.
BAN_TTL_DAYS: float = 7.0

# Consecutive bad signals needed to escalate one tier.
CONSECUTIVE_BAD_THRESHOLD: int = 3

# Recent outcome window for captcha/block checks.
RECENT_OUTCOME_WINDOW: int = 5


# ── Global Memory integration ────────────────────────────────────────────────
# GlobalMemory is ADVISORY ONLY — it never overrides local decisions.
# Imported lazily so the system works even if the DB file is missing.

from core.global_memory import get_global_memory  # noqa: E402  (after stdlib imports)
from core.mutation_controller import (             # noqa: E402  (after stdlib imports)
    _account_noise, apply_behavior_noise, _behavior_noise, stable_hash_int,
    _normalized_noise,
)
from core.persona_engine import get_persona_engine   # noqa: E402  (after stdlib imports)

# P5: per-account inertia for global adj (process-scoped, zero cross-account state).
_PREV_ADJ: dict[str, float] = {}


# ── StealthMemory ─────────────────────────────────────────────────────────────

@dataclass
class StealthMemory:
    """Per-account brain state. Never shared between accounts."""

    account_id:          str
    # Banned fingerprints with 7-day TTL: [{hash, expires_at}]
    banned_fingerprints: list[dict]  = field(default_factory=list)
    # Plain session outcome records, last 10 (no weighting).
    outcome_history:     list[dict]  = field(default_factory=list)
    consecutive_bad:     int         = 0
    total_sessions:      int         = 0
    total_bans:          int         = 0
    total_checkpoints:   int         = 0

    # ── Ban TTL management ────────────────────────────────────────────────────

    def add_banned(self, fingerprint: str) -> None:
        """Add fingerprint to local ban list with 7-day TTL."""
        self.banned_fingerprints.append({
            "hash":       fingerprint,
            "expires_at": time.time() + BAN_TTL_DAYS * 86400,
        })

    def purge_expired_bans(self) -> None:
        """Remove expired ban entries. Called on every evaluate()."""
        now = time.time()
        self.banned_fingerprints = [
            b for b in self.banned_fingerprints if b["expires_at"] > now
        ]

    def is_banned(self, fingerprint: str) -> bool:
        """Return True if fingerprint is in the active (non-expired) ban list."""
        self.purge_expired_bans()
        return any(b["hash"] == fingerprint for b in self.banned_fingerprints)

    # ── Outcome history ───────────────────────────────────────────────────────

    def add_outcome(self, record: dict) -> None:
        """Append outcome record. Keeps last 10 only."""
        self.outcome_history.append(record)
        if len(self.outcome_history) > 10:
            self.outcome_history.pop(0)

    def recent_count(self, key: str, window: int = RECENT_OUTCOME_WINDOW) -> int:
        """Count how many of the last `window` outcomes have key=True."""
        return sum(
            1 for o in self.outcome_history[-window:] if o.get(key)
        )

    def any_recent(self, key: str, window: int = 3) -> bool:
        """Return True if any of the last `window` outcomes have key=True."""
        return any(o.get(key) for o in self.outcome_history[-window:])

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id":          self.account_id,
            "banned_fingerprints": self.banned_fingerprints,
            "outcome_history":     self.outcome_history,
            "consecutive_bad":     self.consecutive_bad,
            "total_sessions":      self.total_sessions,
            "total_bans":          self.total_bans,
            "total_checkpoints":   self.total_checkpoints,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StealthMemory":
        now = time.time()
        bans: list[dict] = []
        for entry in d.get("banned_fingerprints", []):
            if isinstance(entry, str):
                # Backward compat: v1 stored plain strings
                bans.append({"hash": entry, "expires_at": now + BAN_TTL_DAYS * 86400})
            elif isinstance(entry, dict) and "hash" in entry:
                bans.append(entry)
        return cls(
            account_id          = d["account_id"],
            banned_fingerprints = bans,
            outcome_history     = d.get("outcome_history", []),
            consecutive_bad     = int(d.get("consecutive_bad", 0)),
            total_sessions      = int(d.get("total_sessions", 0)),
            total_bans          = int(d.get("total_bans", 0)),
            total_checkpoints   = int(d.get("total_checkpoints", 0)),
        )


# ── StealthBrain ──────────────────────────────────────────────────────────────

class StealthBrain:
    """
    Decision engine: RuntimeSignals in → Strategy out.

    Never modifies IdentityProfile. Fully account-isolated (no shared state).
    Risk is classified by fixed thresholds. Escalation rules are explicit and ordered.
    """

    def __init__(self) -> None:
        self._memories: dict[str, StealthMemory] = {}

    def get_memory(self, account_id: str) -> StealthMemory:
        """Return per-account memory, creating it lazily on first access."""
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
        """Classify risk and return a Strategy.

        Fixed thresholds:
            LOW    score < 0.30  → no mutation
            MEDIUM 0.30–0.70     → canvas/geo rotation
            HIGH   score > 0.70  → full regen

        Escalation rules (evaluated in priority order, highest wins):
            1. webdriver_hidden=False         → force HIGH (critical leak)
            2. locally banned fingerprint     → force HIGH
            3. externally banned fingerprint  → force HIGH
            4. any recent blocked outcome     → force HIGH
            5. 3+ recent captchas             → force HIGH
            6. 3+ consecutive bad signals     → escalate one tier
        """
        mem = self.get_memory(account_id)
        mem.purge_expired_bans()

        # Soft signal: bounded global ban-rate adjustment (+0.0 to +0.06 max).
        # P5: cap narrowed 0.08→0.06, multiplier 0.15→0.12 to reduce herd sync.
        # P8: key namespaced as 'global:gm_weight'.
        _gm = get_global_memory()
        _raw_rate  = _gm.get_recent_ban_rate()
        _weight    = 0.7 + (stable_hash_int(account_id, "global:gm_weight") % 300) / 1000.0  # 0.70-1.00
        _ban_rate  = _raw_rate * _weight
        _adj       = min(0.06, _ban_rate * 0.12)   # P5: cap 0.06, multiplier 0.12
        # P5: single _normalized_noise perturbation (±20%) — one noise source only.
        _adj      *= _normalized_noise(account_id, "global:gm_noise", spread=0.20)
        _adj       = max(0.0, min(0.06, _adj))     # re-clamp after noise

        # P5 (memory decay): apply decay to ban_rate signal for this account.
        _day_bucket = int(time.time()) // 86400
        _rate_decay = (stable_hash_int(account_id, "global:memory_decay", str(_day_bucket)) % 5) * 0.01
        _adj        = _adj * (1.0 - _rate_decay)

        # P7 (inertia): 0.8/0.2 EMA — slower reaction, further dampens herd spikes.
        _prev_adj   = _PREV_ADJ.get(account_id, _adj)
        _adj        = _prev_adj * 0.8 + _adj * 0.2
        _adj        = max(0.0, min(0.06, _adj))    # final safety clamp
        _PREV_ADJ[account_id] = _adj

        # Part 8 / 6: reaction lag - per-account STABLE hash (not stateful counter).
        # lag=0: react immediately; lag=1: ignore first evaluate; lag=2: ignore first 2.
        # Consistent across all evaluations for same account.
        _lag = stable_hash_int(account_id, "reaction_lag") % 3   # 0, 1, or 2
        # Use total_sessions as the cycle counter (incremented in record_outcome)
        if mem.total_sessions <= _lag:
            _adj = 0.0
            LOGGER.debug("stealth_reaction_lag account=%s lag=%d sessions=%d",
                         account_id, _lag, mem.total_sessions)

        _effective_score = min(1.0, signals.risk_score + _adj)
        if _adj > 0.0:
            LOGGER.debug(
                "stealth_global_adj account=%s raw_rate=%.3f weight=%.2f adj=+%.3f effective=%.3f",
                account_id, _raw_rate, _weight, _adj, _effective_score,
            )

        # Base classification from fixed thresholds (on adjusted score)
        effective_risk, reason = _classify_risk(_effective_score)

        # Persona modifier: nudge effective score based on account's risk tolerance.
        # High risk_tolerance → slightly less sensitive (score nudged down).
        # Low  risk_tolerance → slightly more sensitive (score nudged up).
        # Influence capped at ±20% of the raw (pre-global) score only.
        try:
            _pe    = get_persona_engine()
            _mods  = _pe.get_behavior_modifiers(account_id)
            # Part 4: behavior noise — perturb modifiers ~10% of evaluations.
            _now_i = int(time.time())
            _mods  = apply_behavior_noise(account_id, _now_i, _mods)
            _agg   = _mods["mutation_aggressiveness"]   # 0.8-1.2 (post-noise)
            # Apply: score adjustment = raw_score * (agg - 1.0) * 0.2
            _persona_adj  = signals.risk_score * (_agg - 1.0) * 0.2
            _effective_score = min(1.0, max(0.0, _effective_score + _persona_adj))
            if abs(_persona_adj) > 0.001:
                LOGGER.debug(
                    "stealth_persona_adj account=%s agg=%.3f adj=%+.3f effective=%.3f",
                    account_id, _agg, _persona_adj, _effective_score,
                )
            # Re-classify with persona-adjusted score
            effective_risk, reason = _classify_risk(_effective_score)
        except Exception as exc:
            LOGGER.warning("stealth_persona_error account=%s error=%s", account_id, exc)

        # Rule 6: consecutive bad signals → escalate one tier
        if signals.risk_score >= RISK_LOW_MAX:
            mem.consecutive_bad += 1
        else:
            mem.consecutive_bad = 0

        if mem.consecutive_bad >= CONSECUTIVE_BAD_THRESHOLD:
            if effective_risk == RiskLevel.LOW:
                effective_risk = RiskLevel.MEDIUM
                reason = f"consecutive_bad={mem.consecutive_bad}"
            elif effective_risk == RiskLevel.MEDIUM:
                effective_risk = RiskLevel.HIGH
                reason = f"consecutive_bad={mem.consecutive_bad}"

        # Rule 5: 3+ captchas in recent window → HIGH
        captcha_count = mem.recent_count("captcha")
        if captcha_count >= 3:
            effective_risk = RiskLevel.HIGH
            reason = f"captcha_count={captcha_count}"

        # Rule 4: any blocked outcome in last 3 sessions → HIGH
        if mem.any_recent("blocked", window=3):
            effective_risk = RiskLevel.HIGH
            reason = "recent_blocked"

        # Rule 3: globally banned fingerprint → HIGH (real GlobalMemory check).
        # Exception-safe: is_fingerprint_banned() returns False if DB is down.
        if _gm.is_fingerprint_banned(profile.fingerprint_hash):
            effective_risk = RiskLevel.HIGH
            reason = f"globally_banned_fingerprint hash={profile.fingerprint_hash[:8]}"

        # Rule 2: locally banned fingerprint → HIGH
        if mem.is_banned(profile.fingerprint_hash):
            effective_risk = RiskLevel.HIGH
            reason = f"locally_banned hash={profile.fingerprint_hash[:8]}"

        # Rule 1: webdriver exposed → always HIGH (highest priority)
        if not signals.webdriver_hidden:
            effective_risk = RiskLevel.HIGH
            reason = "webdriver_exposed"

        actions  = _build_actions(signals, effective_risk)
        strategy = _build_strategy(effective_risk, actions, reason)

        LOGGER.info(
            "stealth_evaluate account=%s score=%.3f risk=%s reason=%s actions=%s",
            account_id, signals.risk_score, effective_risk.value, reason,
            [a.type for a in actions],
        )
        return strategy

    def record_outcome(
        self,
        account_id: str,
        outcome: dict[str, Any],
        profile: IdentityProfile,
    ) -> None:
        """Record a session outcome into per-account memory.

        Outcome keys (all optional, all bool/float):
            upload_success, captcha, blocked, shadow_ban_signal,
            completion_ratio, suspicious_short, suspicious_abandon.
        """
        mem = self.get_memory(account_id)
        record: dict[str, Any] = {
            "ts":                time.time(),
            "upload_success":    bool(outcome.get("upload_success",    False)),
            "captcha":           bool(outcome.get("captcha",           False)),
            "blocked":           bool(outcome.get("blocked",           False)),
            "shadow_ban_signal": bool(outcome.get("shadow_ban_signal", False)),
            "completion_ratio":  float(outcome.get("completion_ratio", 1.0)),
            "suspicious_short":  bool(outcome.get("suspicious_short",  False)),
            "suspicious_abandon":bool(outcome.get("suspicious_abandon", False)),
            "fingerprint":       profile.fingerprint_hash[:12],
        }
        mem.add_outcome(record)
        mem.total_sessions += 1

        if record["blocked"]:
            if not mem.is_banned(profile.fingerprint_hash):
                mem.add_banned(profile.fingerprint_hash)
                # Report to global memory: advisory signal for other accounts.
                # Exception-safe — DB unavailability does not affect local logic.
                _gm = get_global_memory()
                _gm.record_ban(profile.fingerprint_hash, reason=f"account={account_id}")
                _gm.record_event("ban", account_id, float(record.get("completion_ratio", 0.0)))
            mem.total_bans += 1
            LOGGER.critical(
                "stealth_blocked account=%s hash=%s total_bans=%d",
                account_id, profile.fingerprint_hash[:8], mem.total_bans,
            )
        elif record["captcha"]:
            mem.total_checkpoints += 1
            LOGGER.warning("stealth_captcha account=%s total=%d", account_id, mem.total_checkpoints)
        elif record["shadow_ban_signal"]:
            LOGGER.warning("stealth_shadow_ban account=%s", account_id)
        elif record["upload_success"]:
            LOGGER.info("stealth_success account=%s", account_id)

        # Part 3: evolve persona based on this session's outcome.
        # Part 4: push persona summary to GlobalMemory (advisory, exception-safe).
        try:
            _pe = get_persona_engine()
            _persona = _pe.evolve(account_id, outcome, now=int(record["ts"]))
            # Part 4: store summary in global_memory stats (non-blocking, best-effort)
            _gm = get_global_memory()
            _gm.set_stat(f"persona:{account_id}", {
                "dominant_niche":   _persona.dominant_niche(),
                "risk_bucket":      _persona.risk_bucket(),
                "activity_bias":    round(_persona.activity_bias, 3),
                "session_count":    _persona.session_count,
            })
        except Exception as exc:
            LOGGER.warning("stealth_persona_evolve_error account=%s error=%s", account_id, exc)

    def process_session_outcome(
        self,
        account_id: str,
        outcome: Literal["success", "checkpoint", "ban"],
        profile: IdentityProfile,
    ) -> None:
        """Legacy string-based recorder. Prefer record_outcome() for new code."""
        mapping: dict[str, dict] = {
            "success":    {"upload_success": True},
            "checkpoint": {"captcha": True},
            "ban":        {"blocked": True},
        }
        self.record_outcome(account_id, mapping.get(outcome, {}), profile)

    # ── Persistence ───────────────────────────────────────────────────────────

    def snapshot_all(self) -> dict[str, Any]:
        """Serialize all per-account memories for external persistence."""
        return {k: v.to_dict() for k, v in self._memories.items()}

    def load_all(self, data: dict[str, dict[str, Any]]) -> None:
        """Restore per-account memories from serialized data."""
        for k, v in data.items():
            self._memories[k] = StealthMemory.from_dict(v)


# ── Module-level helpers (stateless) ─────────────────────────────────────────

def _classify_risk(score: float) -> tuple[RiskLevel, str]:
    """Map risk score to RiskLevel using fixed thresholds.

    LOW    score < 0.30
    MEDIUM 0.30 <= score < 0.70
    HIGH   score >= 0.70
    """
    if score >= RISK_HIGH_MIN:
        return RiskLevel.HIGH, f"score={score:.3f} >= {RISK_HIGH_MIN}"
    if score >= RISK_LOW_MAX:
        return RiskLevel.MEDIUM, f"score={score:.3f} in [{RISK_LOW_MAX},{RISK_HIGH_MIN})"
    return RiskLevel.LOW, f"score={score:.3f} < {RISK_LOW_MAX}"


def _build_actions(signals: RuntimeSignals, risk: RiskLevel) -> list[Action]:
    """Build action list based on risk level and signal failures.

    LOW    → no actions.
    MEDIUM → canvas rotation and/or geo sync (safe surface only).
    HIGH   → canvas + GPU rotation, geo sync, cooldown if webdriver exposed.
    """
    if risk == RiskLevel.LOW:
        return []

    actions: list[Action] = []

    # Canvas rotation: triggered by navigator or screen mismatches.
    if not signals.language_match or not signals.platform_match or not signals.screen_match:
        actions.append(Action(type="rotate_canvas", targets=["canvas_noise_seed"]))

    # Geo sync: triggered by timezone or language mismatch.
    if not signals.timezone_match or not signals.language_match:
        actions.append(Action(type="sync_geo", targets=["timezone", "locale"], metadata={}))

    if risk == RiskLevel.HIGH:
        # GPU rotation: triggered by WebGL mismatch.
        if not signals.webgl_vendor_match or not signals.webgl_renderer_match:
            actions.append(Action(type="rotate_gpu", targets=["webgl_noise_seed"]))
        # Cooldown: triggered by webdriver or eval failure.
        if not signals.webdriver_hidden or not signals.eval_ok:
            actions.append(Action(type="cooldown", targets=[]))

    return actions


def _build_strategy(
    risk: RiskLevel,
    actions: list[Action],
    reason: str,
) -> Strategy:
    """Map risk level to Strategy parameters."""
    if risk == RiskLevel.LOW:
        return Strategy(
            risk_level=RiskLevel.LOW, actions=[],
            delay_multiplier=1.0, warmup_delay=5.0,
            interaction_mode="NORMAL", reason=reason,
        )
    if risk == RiskLevel.MEDIUM:
        return Strategy(
            risk_level=RiskLevel.MEDIUM, actions=actions,
            delay_multiplier=1.5, warmup_delay=15.0,
            interaction_mode="SAFE", reason=reason,
        )
    return Strategy(
        risk_level=RiskLevel.HIGH, actions=actions,
        delay_multiplier=2.5, warmup_delay=30.0,
        interaction_mode="SAFE", reason=reason,
    )


# ── Singleton ─────────────────────────────────────────────────────────────────

_STEALTH_BRAIN: StealthBrain | None = None


def get_stealth_brain() -> StealthBrain:
    """Return the process-level StealthBrain singleton."""
    global _STEALTH_BRAIN
    if _STEALTH_BRAIN is None:
        _STEALTH_BRAIN = StealthBrain()
    return _STEALTH_BRAIN
