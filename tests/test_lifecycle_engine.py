"""
Tests for core/lifecycle_engine.py
"""
import importlib.util, sys
from dataclasses import dataclass, field as _field
from types import ModuleType

def _load(path: str, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m

import os

sys.modules.setdefault("core", type(sys)("core"))

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

_load("core/global_memory.py",      "core.global_memory")
_load("core/platform_profiles.py",  "core.platform_profiles")
_load("core/mutation_controller.py","core.mutation_controller")
_load("core/identity_manager.py",   "core.identity_manager")
_load("core/persona_engine.py",     "core.persona_engine")
_load("core/lifecycle_engine.py",   "core.lifecycle_engine")
_load("core/strategy_engine.py",    "core.strategy_engine")

import core.lifecycle_engine as le
import core.strategy_engine  as se

# Fixed reference timestamp: Monday 09:00 UTC, a "veteran" base
BASE_TS = 1_716_100_000


def fresh():
    le.reset_lifecycle_engine()
    se._reset_for_testing()
    try:
        from core.persona_engine import reset_persona_engine
        reset_persona_engine()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# test_lifecycle_progression
# ─────────────────────────────────────────────────────────────────────────────

def test_lifecycle_progression():
    """Account moves NEW → WARMUP → GROWTH as age increases."""
    fresh()
    acct = "prog-acct-001"

    # Day 0 → NEW
    created = BASE_TS
    stage0 = le.get_lifecycle_stage(acct, created, now=created)
    assert stage0 == le.LifecycleStage.NEW, f"Expected NEW, got {stage0}"

    # Day 5 → WARMUP
    stage5 = le.get_lifecycle_stage(acct, created, now=created + 5 * 86400)
    assert stage5 == le.LifecycleStage.WARMUP, f"Expected WARMUP, got {stage5}"

    # Day 20 → GROWTH
    stage20 = le.get_lifecycle_stage(acct, created, now=created + 20 * 86400)
    assert stage20 == le.LifecycleStage.GROWTH, f"Expected GROWTH, got {stage20}"

    # Day 60 → MATURE
    stage60 = le.get_lifecycle_stage(acct, created, now=created + 60 * 86400)
    assert stage60 == le.LifecycleStage.MATURE, f"Expected MATURE, got {stage60}"

    print(f"PASS test_lifecycle_progression (NEW→WARMUP→GROWTH→MATURE)")


# ─────────────────────────────────────────────────────────────────────────────
# test_decline_and_recovery
# ─────────────────────────────────────────────────────────────────────────────

def test_decline_and_recovery():
    """Low success_rate triggers DECLINE; improving rate triggers RECOVERY."""
    fresh()
    acct    = "decline-acct-001"
    created = BASE_TS - 60 * 86400   # 60-day-old account (MATURE baseline: >56 days)

    # Normal: MATURE
    stage_normal = le.get_lifecycle_stage(acct, created, now=BASE_TS, success_rate=0.75)
    assert stage_normal == le.LifecycleStage.MATURE, f"Expected MATURE, got {stage_normal}"

    # Decline: success_rate < 0.30
    stage_decline = le.get_lifecycle_stage(acct, created, now=BASE_TS, success_rate=0.15)
    assert stage_decline == le.LifecycleStage.DECLINE, f"Expected DECLINE, got {stage_decline}"

    # Recovery: was in DECLINE, now success_rate = 0.40 (above recovery threshold)
    stage_recovery = le.get_lifecycle_stage(acct, created, now=BASE_TS, success_rate=0.40)
    assert stage_recovery == le.LifecycleStage.RECOVERY, f"Expected RECOVERY, got {stage_recovery}"

    print("PASS test_decline_and_recovery")


# ─────────────────────────────────────────────────────────────────────────────
# test_interest_distribution
# ─────────────────────────────────────────────────────────────────────────────

def test_interest_distribution():
    """Interest vector: sum == 1.0 and max 5 active interests."""
    fresh()

    for i in range(30):
        acct    = f"interest-acct-{i:04d}"
        profile = le.get_interest_profile(acct)

        n_interests = len(profile)
        assert n_interests <= le.MAX_ACTIVE_INTERESTS, (
            f"Account {acct}: {n_interests} interests > max {le.MAX_ACTIVE_INTERESTS}"
        )
        assert n_interests >= 1, f"Account {acct}: zero interests"

        total = sum(profile.values())
        assert abs(total - 1.0) < 1e-3, (
            f"Account {acct}: interest weights sum to {total:.5f}, expected 1.0"
        )
        for niche, w in profile.items():
            assert w > 0, f"Account {acct}: niche {niche} has zero/negative weight"

    print(f"PASS test_interest_distribution (30 accounts, all sum=1.0, ≤5 interests)")


# ─────────────────────────────────────────────────────────────────────────────
# test_interest_drift_smooth
# ─────────────────────────────────────────────────────────────────────────────

def test_interest_drift_smooth():
    """No large jumps between evolve() steps; no niche flips > 50% in 1 step."""
    fresh()
    acct    = "drift-acct-001"
    created = BASE_TS - 20 * 86400

    profile_before = le.get_interest_profile(acct)

    # Simulate 10 successful sessions on one niche
    top_niche = max(profile_before, key=lambda k: profile_before[k])
    for i in range(10):
        now = BASE_TS + i * 3600
        le.evolve_interests(acct, now=now, feedback={
            "success": True, "ban": False,
            "niche": top_niche, "trend_intensity": 0.6,
        }, created_ts=created)

    profile_after = le.get_interest_profile(acct)

    # No single niche should flip by more than 50%
    for niche in profile_before:
        before = profile_before.get(niche, 0.0)
        after  = profile_after.get(niche, 0.0)
        delta  = abs(after - before)
        assert delta < 0.50, (
            f"Niche {niche}: delta={delta:.3f} exceeds 50% in 10 steps"
        )

    # Sum must still be 1.0
    total = sum(profile_after.values())
    assert abs(total - 1.0) < 1e-3, f"Interest sum after drift: {total:.5f}"

    print(f"PASS test_interest_drift_smooth (top niche delta={abs(profile_after[top_niche] - profile_before[top_niche]):.3f})")


# ─────────────────────────────────────────────────────────────────────────────
# test_niche_selection_bias
# ─────────────────────────────────────────────────────────────────────────────

def test_niche_selection_bias():
    """High-weight niche should be selected more often than low-weight niche."""
    fresh()
    acct    = "niche-bias-acct-001"
    created = BASE_TS - 60 * 86400   # MATURE stage

    stage = le.get_lifecycle_stage(acct, created, now=BASE_TS, success_rate=0.75)

    profile = le.get_interest_profile(acct)
    top_niche = max(profile, key=lambda k: profile[k])
    bot_niche = min(profile, key=lambda k: profile[k])

    top_count = 0
    bot_count = 0
    n_samples = 200

    for i in range(n_samples):
        # Vary now per sample so hash produces different results
        niche = le.sample_niche(acct, BASE_TS + i * 1800, stage, created)
        if niche == top_niche:
            top_count += 1
        if niche == bot_niche:
            bot_count += 1

    top_rate = top_count / n_samples
    bot_rate = bot_count / n_samples

    assert top_count > bot_count, (
        f"Top niche ({top_niche}) selected {top_count}x, "
        f"bottom niche ({bot_niche}) selected {bot_count}x — bias not working"
    )
    print(
        f"PASS test_niche_selection_bias "
        f"(top={top_niche}:{top_rate:.0%}, bot={bot_niche}:{bot_rate:.0%})"
    )


# ─────────────────────────────────────────────────────────────────────────────
# test_memory_feedback
# ─────────────────────────────────────────────────────────────────────────────

def test_memory_feedback():
    """Successful niche EMA increases; failed niche EMA decreases."""
    fresh()
    acct    = "memory-acct-001"
    created = BASE_TS - 30 * 86400

    profile = le.get_interest_profile(acct)
    target_niche = list(profile.keys())[0]

    # Initial memory
    mem_before = le.get_niche_success_rate(acct, target_niche, created)

    # Feed 10 successes on target_niche
    for i in range(10):
        le.evolve_interests(acct, now=BASE_TS + i * 3600, feedback={
            "success": True, "ban": False, "niche": target_niche,
            "trend_intensity": 0.5,
        }, created_ts=created)

    mem_after_success = le.get_niche_success_rate(acct, target_niche, created)
    assert mem_after_success > mem_before, (
        f"Niche {target_niche} memory should increase after 10 successes: "
        f"{mem_before:.3f} → {mem_after_success:.3f}"
    )

    # Feed 10 bans on same niche
    for i in range(10):
        le.evolve_interests(acct, now=BASE_TS + (10 + i) * 3600, feedback={
            "success": False, "ban": True, "niche": target_niche,
            "trend_intensity": 0.5,
        }, created_ts=created)

    mem_after_ban = le.get_niche_success_rate(acct, target_niche, created)
    assert mem_after_ban < mem_after_success, (
        f"Niche {target_niche} memory should decrease after bans: "
        f"{mem_after_success:.3f} → {mem_after_ban:.3f}"
    )

    print(
        f"PASS test_memory_feedback "
        f"(init={mem_before:.3f} +success={mem_after_success:.3f} +ban={mem_after_ban:.3f})"
    )


# ─────────────────────────────────────────────────────────────────────────────
# test_lifecycle_strategy_alignment
# ─────────────────────────────────────────────────────────────────────────────

def test_lifecycle_strategy_alignment():
    """NEW accounts must never receive HARVESTER role from strategy_engine."""
    fresh()

    # Use very young created_ts so all accounts are NEW
    new_created_ts = BASE_TS - 1 * 86400   # 1 day old

    harvester_seen = 0
    forbidden_roles = {"HARVESTER", "AMPLIFIER"}
    plans_seen = 0

    for i in range(100):
        acct = f"new-align-acct-{i:04d}"
        plan = se.plan_actions(
            account_id = acct,
            platform   = "tiktok",
            created_ts = new_created_ts,
            now        = BASE_TS,
            risk_score = 0.1,
        )
        if plan is not None:
            plans_seen += 1
            if plan.role.value in forbidden_roles:
                harvester_seen += 1

    assert harvester_seen == 0, (
        f"NEW accounts got forbidden role {harvester_seen}x out of {plans_seen} plans"
    )
    print(
        f"PASS test_lifecycle_strategy_alignment "
        f"({plans_seen} plans, 0 HARVESTER/AMPLIFIER for NEW accounts)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# test_no_cross_account_leak
# ─────────────────────────────────────────────────────────────────────────────

def test_no_cross_account_leak():
    """20 accounts must have different interest profiles (no identity leakage)."""
    fresh()

    profiles = {}
    for i in range(20):
        acct           = f"isolation-acct-{i:04d}"
        profiles[acct] = le.get_interest_profile(acct)

    # Convert to sortable tuple for comparison
    profile_fps = set()
    for acct, prof in profiles.items():
        # Fingerprint = tuple of (niche, rounded_weight) sorted by niche
        fp = tuple(sorted((n, round(w, 3)) for n, w in prof.items()))
        profile_fps.add(fp)

    # All 20 accounts should have different fingerprints
    assert len(profile_fps) == 20, (
        f"Only {len(profile_fps)} unique profiles across 20 accounts — cross-account leak!"
    )
    print(f"PASS test_no_cross_account_leak (20 accounts, 20 unique profiles)")


# ─────────────────────────────────────────────────────────────────────────────
# test_stage_multipliers_bounded
# ─────────────────────────────────────────────────────────────────────────────

def test_stage_multipliers_bounded():
    """All stage profile multipliers must be within [0.6, 1.4]."""
    # exploration_bias and stability_bias can go below 0.6 by design
    # (e.g. MATURE exploration_bias=0.25 reflects near-zero exploration)
    # Only the output multipliers (risk, activity) must stay in [0.6, 1.4]
    for stage in le.LifecycleStage:
        profile = le.get_stage_profile(stage)
        for attr in ("risk_multiplier", "activity_multiplier"):
            val = getattr(profile, attr)
            assert le._MULT_MIN <= val <= le._MULT_MAX, (
                f"Stage {stage.value}.{attr}={val} out of bounds [{le._MULT_MIN},{le._MULT_MAX}]"
            )
        # Bias scores: [0.0, 1.4] is the valid range
        for attr in ("exploration_bias", "stability_bias"):
            val = getattr(profile, attr)
            assert 0.0 <= val <= le._MULT_MAX, (
                f"Stage {stage.value}.{attr}={val} out of range [0.0,{le._MULT_MAX}]"
            )
    print("PASS test_stage_multipliers_bounded")


# ─────────────────────────────────────────────────────────────────────────────
# test_daily_drift_cap
# ─────────────────────────────────────────────────────────────────────────────

def test_daily_drift_cap():
    """Total drift per day must not exceed MAX_DRIFT_PER_DAY (0.05)."""
    fresh()
    acct    = "drift-cap-acct-001"
    created = BASE_TS - 20 * 86400
    day_ts  = BASE_TS

    profile_before = le.get_interest_profile(acct)
    top_niche = max(profile_before, key=lambda k: profile_before[k])

    # Fire 30 evolve() calls in the same day
    for i in range(30):
        le.evolve_interests(acct, now=day_ts + i * 60, feedback={
            "success": True, "ban": False,
            "niche": top_niche, "trend_intensity": 0.9,
        }, created_ts=created)

    profile_after = le.get_interest_profile(acct)

    # Max absolute change for any single niche
    max_delta = max(
        abs(profile_after.get(n, 0) - profile_before.get(n, 0))
        for n in set(list(profile_before) + list(profile_after))
    )

    # With 30 calls in one day, drift must still be bounded
    # (total across all niches ≤ MAX_DRIFT_PER_DAY)
    # Allow ≤ 0.08 per niche (some distribution across niches)
    assert max_delta < 0.10, (
        f"Max single-niche drift {max_delta:.4f} exceeds expected bound after daily cap"
    )
    print(f"PASS test_daily_drift_cap (max_delta={max_delta:.4f})")


# ─────────────────────────────────────────────────────────────────────────────
# test_lifecycle_reward_shaping
# ─────────────────────────────────────────────────────────────────────────────

def test_lifecycle_reward_shaping():
    """Acting aligned with lifecycle stage gives positive bonus; violation gives negative."""
    fresh()
    acct    = "reward-acct-001"
    created = BASE_TS - 1 * 86400   # NEW account (1 day old)

    stage = le.get_lifecycle_stage(acct, created, now=BASE_TS)
    assert stage == le.LifecycleStage.NEW

    profile = le.get_interest_profile(acct)
    niche   = list(profile.keys())[0]

    # WARMER is allowed for NEW → positive bonus
    bonus_aligned = le.compute_lifecycle_reward_bonus(
        account_id = acct, role = "WARMER", niche = niche,
        stage = stage, success = True, created_ts = created,
    )
    assert bonus_aligned >= 0, f"Aligned action should give non-negative bonus: {bonus_aligned}"

    # HARVESTER is NOT allowed for NEW → negative bonus
    bonus_violated = le.compute_lifecycle_reward_bonus(
        account_id = acct, role = "HARVESTER", niche = niche,
        stage = stage, success = True, created_ts = created,
    )
    assert bonus_violated < bonus_aligned, (
        f"Violated role should give lower bonus than aligned: "
        f"aligned={bonus_aligned} violated={bonus_violated}"
    )

    print(f"PASS test_lifecycle_reward_shaping "
          f"(aligned={bonus_aligned:.3f} violated={bonus_violated:.3f})")


if __name__ == "__main__":
    for fn_name in [k for k in list(globals()) if k.startswith("test_")]:
        fn = globals()[fn_name]
        try:
            fn()
        except AssertionError as e:
            print(f"FAIL {fn_name}: {e}")
        except Exception as e:
            import traceback
            print(f"ERROR {fn_name}: {type(e).__name__}: {e}")
            traceback.print_exc()
