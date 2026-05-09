"""
Tests for core/strategy_engine.py

Validates:
  - Role distribution across a large fleet
  - No synchronised timing spikes (anti-sync)
  - Role rotation over time (not static)
  - Global feedback adaptation (ban_rate → role suppression)
  - Cross-account diversity (no two accounts get identical action plans)
"""
import importlib.util, sys
from types import ModuleType

def _load(path: str, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)   # type: ignore[union-attr]
    return m

# ── Stub minimal dependencies ─────────────────────────────────────────────────

import os, tempfile

# core namespace
sys.modules.setdefault("core", type(sys)(  "core"))

# runtime_validator stub
from dataclasses import dataclass, field as _field
@dataclass
class _RS:
    platform_match: bool = True; hardware_match: bool = True
    language_match: bool = True; screen_match: bool = True
    timezone_match: bool = True; webgl_vendor_match: bool = True
    webgl_renderer_match: bool = True; webdriver_hidden: bool = True
    eval_ok: bool = True; risk_score: float = 0.0
    breakdown: dict = _field(default_factory=dict)
    fingerprint_changed: bool = False; geo_mismatch: bool = False
    device_mismatch: bool = False

_rv = type(sys)("core.runtime_validator")
_rv.RuntimeSignals = _RS
sys.modules["core.runtime_validator"] = _rv

# Load real modules in dependency order
_load("core/global_memory.py",      "core.global_memory")
_load("core/platform_profiles.py",  "core.platform_profiles")
_load("core/mutation_controller.py","core.mutation_controller")
_load("core/identity_manager.py",   "core.identity_manager")
_load("core/persona_engine.py",     "core.persona_engine")
_load("core/strategy_engine.py",    "core.strategy_engine")

import core.strategy_engine as se


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

FLEET_SIZE = 200
ACCOUNTS   = [f"fleet-acct-{i:04d}" for i in range(FLEET_SIZE)]
BASE_TS    = 1_716_100_000   # fixed Monday 09:00 UTC


def fresh():
    """Reset all module state between tests."""
    se._reset_for_testing()
    from core.persona_engine import reset_persona_engine
    reset_persona_engine()


# ─────────────────────────────────────────────────────────────────────────────
# test_role_distribution
# ─────────────────────────────────────────────────────────────────────────────

def test_role_distribution():
    """Fleet of 200 accounts → all 5 roles represented, no single role > 60%."""
    fresh()
    counts = se.get_role_distribution(ACCOUNTS, now=BASE_TS)

    total = sum(counts.values())
    assert total == FLEET_SIZE, f"Counts sum mismatch: {total}"

    for role in se.AccountRole:
        assert counts[role.value] > 0, f"Role {role.value} has zero representation"

    for role, count in counts.items():
        pct = count / total
        assert pct < 0.60, f"Role {role} dominates at {pct:.0%}"

    print(f"PASS test_role_distribution {counts}")


# ─────────────────────────────────────────────────────────────────────────────
# test_no_sync_spike
# ─────────────────────────────────────────────────────────────────────────────

def test_no_sync_spike():
    """No two accounts should have identical timing_offset in the same cycle."""
    fresh()

    plans = []
    for acct in ACCOUNTS:
        plan = se.plan_actions(acct, platform="tiktok", now=BASE_TS)
        if plan is not None:
            plans.append(plan)

    assert len(plans) > 10, "Too few participants — check participation rate"

    offsets = [p.timing_offset for p in plans]
    # The number of unique offsets should be nearly as large as the number of plans
    unique_offsets = len(set(offsets))
    duplicate_rate = 1.0 - (unique_offsets / len(offsets))

    # Allow up to 20% collision (natural hash collision floor), not a hard sync
    assert duplicate_rate < 0.20, (
        f"Too many identical timing offsets: {duplicate_rate:.0%} collision rate. "
        f"offsets sample={sorted(offsets)[:10]}"
    )
    print(f"PASS test_no_sync_spike (plans={len(plans)}, unique_offsets={unique_offsets}, "
          f"collision={duplicate_rate:.0%})")


# ─────────────────────────────────────────────────────────────────────────────
# test_role_rotation
# ─────────────────────────────────────────────────────────────────────────────

def test_role_rotation():
    """The same account must have a different role on at least some days over 30 days."""
    fresh()

    acct = "rotation-test-acct"
    roles_seen = set()

    for day in range(30):
        ts = BASE_TS + day * 86400
        se._ROLE_CACHE.pop(acct, None)   # force re-evaluation each day
        role = se.assign_role(acct, now=ts)
        roles_seen.add(role.value)

    assert len(roles_seen) >= 2, (
        f"Account stuck in single role over 30 days: {roles_seen}"
    )
    print(f"PASS test_role_rotation (roles_seen={roles_seen})")


# ─────────────────────────────────────────────────────────────────────────────
# test_feedback_adaptation
# ─────────────────────────────────────────────────────────────────────────────

def test_feedback_adaptation():
    """High ban_rate must suppress HARVESTER + AMPLIFIER roles."""
    fresh()

    # Record 50 ban events spread across accounts
    for i in range(50):
        se.record_outcome(
            account_id  = f"banned-acct-{i}",
            role        = se.AccountRole.HARVESTER,
            intent_type = se.IntentType.POST,
            success     = False,
            ban         = True,
        )
    # Record some successes to avoid full collapse
    for i in range(10):
        se.record_outcome(
            account_id  = f"ok-acct-{i}",
            role        = se.AccountRole.WARMER,
            intent_type = se.IntentType.BROWSE,
            success     = True,
            ban         = False,
        )

    state = se.get_engine_state()
    assert state["recent_ban_rate"] > se.BAN_RATE_HIGH_THRESHOLD, (
        f"Expected ban_rate > {se.BAN_RATE_HIGH_THRESHOLD}, got {state['recent_ban_rate']:.3f}"
    )

    # Under high ban rate, HARVESTER + AMPLIFIER weights should be reduced
    adjusted = se._feedback_adjusted_weights(se._NORMAL_ROLE_WEIGHTS)
    normal   = se._NORMAL_ROLE_WEIGHTS

    assert adjusted["HARVESTER"] < normal["HARVESTER"], (
        f"HARVESTER weight not reduced under high ban_rate: {adjusted}"
    )
    assert adjusted["AMPLIFIER"] < normal["AMPLIFIER"], (
        f"AMPLIFIER weight not reduced under high ban_rate: {adjusted}"
    )
    assert adjusted["IDLE"] > normal["IDLE"], (
        f"IDLE weight not boosted under high ban_rate: {adjusted}"
    )
    print(f"PASS test_feedback_adaptation (ban_rate={state['recent_ban_rate']:.3f}, "
          f"harvester={adjusted['HARVESTER']} amplifier={adjusted['AMPLIFIER']})")


# ─────────────────────────────────────────────────────────────────────────────
# test_cross_account_diversity
# ─────────────────────────────────────────────────────────────────────────────

def test_cross_account_diversity():
    """No two accounts should produce identical (role, intent, offset) tuples."""
    fresh()

    fingerprints = []
    for acct in ACCOUNTS:
        plan = se.plan_actions(acct, platform="instagram", now=BASE_TS)
        if plan:
            fingerprints.append(
                (plan.role.value, plan.intent_type.value, plan.timing_offset, plan.niche)
            )

    assert len(fingerprints) > 20, "Too few participants for diversity test"

    unique = len(set(fingerprints))
    collision_rate = 1.0 - (unique / len(fingerprints))

    # Should be very diverse — allow at most 15% full-tuple collision
    assert collision_rate < 0.15, (
        f"Too many identical action profiles: {collision_rate:.0%}. "
        f"fingerprints sample={fingerprints[:5]}"
    )
    print(f"PASS test_cross_account_diversity (plans={len(fingerprints)}, "
          f"unique={unique}, collision={collision_rate:.0%})")


# ─────────────────────────────────────────────────────────────────────────────
# test_plan_determinism
# ─────────────────────────────────────────────────────────────────────────────

def test_plan_determinism():
    """Same (account_id, now) must produce identical plan on repeated calls."""
    fresh()

    acct = "determinism-acct"
    plan_a = se.plan_actions(acct, platform="youtube", now=BASE_TS)
    plan_b = se.plan_actions(acct, platform="youtube", now=BASE_TS)

    if plan_a is None:
        assert plan_b is None, "plan_a was None but plan_b was not"
    else:
        assert plan_b is not None
        assert plan_a.role          == plan_b.role
        assert plan_a.intent_type   == plan_b.intent_type
        assert plan_a.timing_offset == plan_b.timing_offset
        assert plan_a.intensity     == plan_b.intensity
        assert plan_a.niche         == plan_b.niche

    print(f"PASS test_plan_determinism (plan={'None' if plan_a is None else plan_a.role.value})")


# ─────────────────────────────────────────────────────────────────────────────
# test_critical_risk_gate
# ─────────────────────────────────────────────────────────────────────────────

def test_critical_risk_gate():
    """risk_score >= 0.90 must always return None (hard gate)."""
    fresh()

    for acct in ACCOUNTS[:20]:
        plan = se.plan_actions(acct, platform="tiktok", now=BASE_TS, risk_score=0.95)
        assert plan is None, f"Expected None for critical risk, got {plan}"

    print("PASS test_critical_risk_gate (20 accounts blocked at risk=0.95)")


# ─────────────────────────────────────────────────────────────────────────────
# test_new_account_warmer_only
# ─────────────────────────────────────────────────────────────────────────────

def test_new_account_warmer_only():
    """Accounts < 7 days old must receive WARMER or IDLE role only."""
    fresh()

    acct      = "new-acct-0001"
    created   = BASE_TS - 3 * 86400   # 3 days old
    roles_seen = set()

    # Check across multiple days/hours for this young account
    for hour in range(48):
        se._ROLE_CACHE.pop(acct, None)
        role = se.assign_role(acct, created_ts=created, now=BASE_TS + hour * 3600)
        roles_seen.add(role.value)

    assert roles_seen.issubset({"WARMER", "IDLE"}), (
        f"New account got non-warmer role: {roles_seen}"
    )
    print(f"PASS test_new_account_warmer_only (roles={roles_seen})")


if __name__ == "__main__":
    for fn_name in [k for k in list(globals()) if k.startswith("test_")]:
        fn = globals()[fn_name]
        try:
            fn()
        except AssertionError as e:
            print(f"FAIL {fn_name}: {e}")
        except Exception as e:
            print(f"ERROR {fn_name}: {type(e).__name__}: {e}")
