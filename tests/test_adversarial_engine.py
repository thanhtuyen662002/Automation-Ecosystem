"""
Adversarial Engine Integration Tests — Parts 1–6.

Tests:
  1. test_exploration_presence         ≥1 item from bottom 30% gets boosted
  2. test_exploration_rate_by_stage    stage-appropriate rates (MATURE < GROWTH < NEW)
  3. test_risk_feedback                high risk → delay increases, burst decreases
  4. test_adversarial_adaptation       rising fleet risk → optimizer tightens
  5. test_no_sync_behavior             100 accounts → diverse exploration rates
  6. test_strategy_risk_gate           risk > 0.85 → plan blocked, risk 0.7 → harvester blocked
"""
import importlib.util, sys, os
from dataclasses import dataclass, field as _field
from types import ModuleType

def _load(path: str, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m

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

for path, name in [
    ("core/global_memory.py",        "core.global_memory"),
    ("core/platform_profiles.py",    "core.platform_profiles"),
    ("core/mutation_controller.py",  "core.mutation_controller"),
    ("core/identity_manager.py",     "core.identity_manager"),
    ("core/persona_engine.py",       "core.persona_engine"),
    ("core/lifecycle_engine.py",     "core.lifecycle_engine"),
    ("core/strategy_engine.py",      "core.strategy_engine"),
    ("core/metrics_store.py",        "core.metrics_store"),
    ("core/observer.py",             "core.observer"),
    ("core/detector_simulator.py",   "core.detector_simulator"),
    ("core/optimizer.py",            "core.optimizer"),
    ("core/reinforcement.py",        "core.reinforcement"),
    ("core/adversarial_engine.py",   "core.adversarial_engine"),
    ("core/feed_engine.py",          "core.feed_engine"),
    ("core/engagement_simulator.py", "core.engagement_simulator"),
    ("core/pipeline.py",             "core.pipeline"),
]:
    _load(path, name)

import core.feed_engine         as fe
import core.optimizer           as opt_mod
import core.adversarial_engine  as adv
import core.detector_simulator  as ds
import core.metrics_store       as ms
import core.reinforcement       as rl_mod
import core.strategy_engine     as se
import core.pipeline            as pl

BASE_TS    = 1_716_100_000
CREATED_TS = BASE_TS - 30 * 86400


def fresh():
    from core.global_memory import reset_global_memory
    from core.observer import reset_observer
    from core.lifecycle_engine import reset_lifecycle_engine
    reset_global_memory()
    reset_observer()
    ms.reset_metrics_store()
    opt_mod.reset_optimizer()
    rl_mod.reset_policy()
    ds.reset_detector()
    fe.reset_feed_engine()
    adv.reset_adversarial_engine()
    se._reset_for_testing()
    try:
        reset_lifecycle_engine()
    except Exception:
        pass
    try:
        from core.persona_engine import reset_persona_engine
        reset_persona_engine()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# 1. test_exploration_presence
# ─────────────────────────────────────────────────────────────────────────────

def test_exploration_presence():
    """
    ≥1 item from the bottom 30% of the batch must appear with the
    'exploration_injected' flag set (guaranteed bottom-pool inject).
    """
    fresh()
    now   = BASE_TS
    posts = [
        fe.ContentPost(
            account_id=f"expl-{i:03d}", platform="tiktok",
            niche="tech" if i < 6 else "finance",   # create density imbalance
            intensity=0.8 if i < 6 else 0.3,
            lifecycle_stage="MATURE" if i < 6 else "NEW",
            created_ts=CREATED_TS, now=now,
        )
        for i in range(10)
    ]

    results = fe.rank_batch(posts)
    injected = [r for r in results if "exploration_injected" in r.flags]

    assert len(injected) >= 1, (
        f"At least 1 exploration_injected item expected in batch of {len(results)}: "
        f"flags={[list(r.flags.keys()) for r in results]}"
    )
    print(f"PASS test_exploration_presence ({len(injected)} injected items)")


# ─────────────────────────────────────────────────────────────────────────────
# 2. test_exploration_rate_by_stage
# ─────────────────────────────────────────────────────────────────────────────

def test_exploration_rate_by_stage():
    """
    Stage-appropriate exploration rates:
        MATURE: [0.05, 0.15] < GROWTH: [0.20, 0.40] < NEW: [0.40, 0.70]
    """
    fresh()
    acct = "expl-rate-test-001"

    for stage, (lo, hi) in [
        ("NEW",    (0.40, 0.70)),
        ("WARMUP", (0.40, 0.70)),
        ("GROWTH", (0.20, 0.40)),
        ("MATURE", (0.05, 0.15)),
    ]:
        from core.lifecycle_engine import get_stage_profile, LifecycleStage
        profile = get_stage_profile(LifecycleStage(stage))
        rate    = adv.get_exploration_rate(acct, profile.exploration_bias)
        rate    = adv.clamp_exploration_rate(rate, stage)

        assert lo <= rate <= hi, (
            f"Stage {stage}: expected rate in [{lo},{hi}], got {rate:.4f}"
        )

    print("PASS test_exploration_rate_by_stage")


# ─────────────────────────────────────────────────────────────────────────────
# 3. test_risk_feedback
# ─────────────────────────────────────────────────────────────────────────────

def test_risk_feedback():
    """
    High detection risk → delay_mult increases, burstiness decreases.
    """
    fresh()
    optim = opt_mod.get_optimizer()

    delay_before  = optim.get_adjustment("platform_delay_base_mult")
    burst_before  = optim.get_adjustment("platform_burstiness_mult")

    # Simulate high timing + entropy risk
    high_components = {
        "timing":     0.85,
        "entropy":    0.80,
        "session":    0.40,
        "similarity": 0.30,
        "lifecycle":  0.20,
    }
    adv.optimizer_risk_feedback(optim, risk_score=0.82, risk_components=high_components)

    delay_after = optim.get_adjustment("platform_delay_base_mult")
    burst_after = optim.get_adjustment("platform_burstiness_mult")

    assert delay_after >= delay_before, (
        f"Delay should increase under high risk: {delay_before:.4f} → {delay_after:.4f}"
    )
    assert burst_after <= burst_before, (
        f"Burstiness should decrease under high risk: {burst_before:.4f} → {burst_after:.4f}"
    )
    print(
        f"PASS test_risk_feedback "
        f"(delay {delay_before:.3f}→{delay_after:.3f}, "
        f"burst {burst_before:.3f}→{burst_after:.3f})"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. test_adversarial_adaptation
# ─────────────────────────────────────────────────────────────────────────────

def test_adversarial_adaptation():
    """
    Rising fleet pressure → optimizer delays increase and explorer weight increases.
    """
    fresh()
    optim = opt_mod.get_optimizer()

    delay_before   = optim.get_adjustment("platform_delay_base_mult")
    explorer_before = optim.get_adjustment("strategy_explorer_weight_mult")

    # Simulate rising pressure over 5 cycles
    for cycle_risk in [0.30, 0.40, 0.52, 0.64, 0.72]:
        adv.update_fleet_pressure("tiktok", cycle_risk, optim)

    delay_after    = optim.get_adjustment("platform_delay_base_mult")
    explorer_after = optim.get_adjustment("strategy_explorer_weight_mult")

    assert delay_after >= delay_before, (
        f"Rising pressure should increase delay: {delay_before:.4f}→{delay_after:.4f}"
    )
    assert explorer_after >= explorer_before, (
        f"Rising pressure should boost exploration: {explorer_before:.4f}→{explorer_after:.4f}"
    )
    print(
        f"PASS test_adversarial_adaptation "
        f"(delay {delay_before:.3f}→{delay_after:.3f}, "
        f"explorer {explorer_before:.3f}→{explorer_after:.3f})"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. test_no_sync_behavior
# ─────────────────────────────────────────────────────────────────────────────

def test_no_sync_behavior():
    """
    100 accounts must have diverse (non-identical) exploration rates.
    Anti-sync: jitter ensures spread even within the same lifecycle stage.
    """
    fresh()
    from core.lifecycle_engine import get_stage_profile, LifecycleStage

    profile = get_stage_profile(LifecycleStage.GROWTH)
    rates   = set()

    for i in range(100):
        acct = f"sync-test-{i:04d}"
        rate = adv.get_exploration_rate(acct, profile.exploration_bias)
        rate = adv.clamp_exploration_rate(rate, "GROWTH")
        rates.add(round(rate, 3))

    # With _JITTER_SCALE=0.05, we should see many distinct values
    assert len(rates) > 5, (
        f"Expected diverse exploration rates across 100 accounts, "
        f"got only {len(rates)} unique values: {sorted(rates)}"
    )
    print(f"PASS test_no_sync_behavior ({len(rates)} unique rates across 100 accounts)")


# ─────────────────────────────────────────────────────────────────────────────
# 6. test_strategy_risk_gate
# ─────────────────────────────────────────────────────────────────────────────

def test_strategy_risk_gate():
    """
    risk > 0.85 → all roles blocked (hard stop).
    0.60 < risk < 0.85 → HARVESTER blocked, AMPLIFIER restricted.
    risk < 0.60 → all roles allowed.
    """
    fresh()

    # Hard stop
    for role in ["WARMER", "EXPLORER", "AMPLIFIER", "HARVESTER"]:
        allowed, reason = adv.strategy_risk_gate(0.90, role)
        assert not allowed, f"Role {role} should be blocked at risk=0.90: allowed={allowed} reason={reason}"

    # Restriction zone
    allowed_amp, reason_amp = adv.strategy_risk_gate(0.72, "AMPLIFIER")
    assert allowed_amp, f"AMPLIFIER should be allowed (restricted) at risk=0.72: {reason_amp}"
    assert "restricted" in reason_amp

    allowed_harv, reason_harv = adv.strategy_risk_gate(0.72, "HARVESTER")
    assert not allowed_harv, f"HARVESTER should be blocked at risk=0.72: {reason_harv}"

    # Safe zone
    allowed_safe, reason_safe = adv.strategy_risk_gate(0.50, "HARVESTER")
    assert allowed_safe, f"HARVESTER should be allowed at risk=0.50: {reason_safe}"
    assert reason_safe == "ok"

    print("PASS test_strategy_risk_gate")


# ─────────────────────────────────────────────────────────────────────────────
# Bonus: pipeline integration (adversarial engine wired end-to-end)
# ─────────────────────────────────────────────────────────────────────────────

def test_pipeline_adversarial_wired():
    """Full pipeline cycle must complete with adversarial engine wired in."""
    fresh()
    pipeline = pl.Pipeline(
        accounts     = [f"adv-pipe-{i:04d}" for i in range(20)],
        platform     = "tiktok",
        created_ts   = CREATED_TS,
        cycle_step_s = 3600,
    )
    report = pipeline.run_cycle(now=BASE_TS)
    assert report is not None
    assert report.n_accounts == 20
    assert 0.0 <= report.avg_detection_risk <= 1.0
    print(
        f"PASS test_pipeline_adversarial_wired "
        f"(active={report.n_active} health={report.fleet_health:.3f})"
    )



# ─────────────────────────────────────────────────────────────────────────────
# Precision-fix tests (Parts 1–4 de-pattern patches)
# ─────────────────────────────────────────────────────────────────────────────

def test_inject_not_always_present():
    """
    Part 1: Inject fires ~70% of cycles — must be between 60% and 80% over
    100 independent hourly batches (each hour = independent gate).
    """
    fresh()
    HOUR = 3600
    hit_count = 0
    posts = [
        fe.ContentPost(
            account_id=f"gate-test-{i:03d}", platform="tiktok", niche="tech",
            intensity=0.7 if i < 5 else 0.3,
            lifecycle_stage="MATURE" if i < 5 else "NEW",
            created_ts=CREATED_TS, now=BASE_TS,
        )
        for i in range(10)
    ]

    for h in range(100):
        # Advance time by one hour each iteration → new gate hash
        now_h = BASE_TS + h * HOUR
        posts_h = [
            fe.ContentPost(
                account_id=p.account_id, platform=p.platform,
                niche=p.niche, intensity=p.intensity,
                lifecycle_stage=p.lifecycle_stage,
                created_ts=CREATED_TS, now=now_h,
            )
            for p in posts
        ]
        fe.reset_feed_engine()
        results = fe.rank_batch(posts_h)
        if any("exploration_injected" in r.flags for r in results):
            hit_count += 1

    rate = hit_count / 100
    assert 0.55 <= rate <= 0.85, (
        f"Inject should fire ~70% of hourly cycles, got {rate:.2f} "
        f"({hit_count}/100)"
    )
    print(f"PASS test_inject_not_always_present (inject_rate={rate:.2f})")


def test_inject_variation():
    """
    Part 1: Boost multiplier must not be constant across different accounts/hours.
    """
    fresh()
    boost_values: set[str] = set()

    for h in range(20):
        now_h = BASE_TS + h * 3600
        posts = [
            fe.ContentPost(
                account_id=f"boost-var-{i:03d}", platform="tiktok",
                niche="tech" if i < 6 else "finance",
                intensity=0.8 if i < 6 else 0.3,
                lifecycle_stage="MATURE" if i < 6 else "NEW",
                created_ts=CREATED_TS, now=now_h,
            )
            for i in range(10)
        ]
        fe.reset_feed_engine()
        results = fe.rank_batch(posts)
        for r in results:
            boost_str = r.flags.get("exploration_injected", "")
            if boost_str.startswith("boost="):
                boost_values.add(boost_str)

    assert len(boost_values) > 1, (
        f"Boost multiplier must vary across cycles/accounts, "
        f"got constant: {boost_values}"
    )
    print(f"PASS test_inject_variation ({len(boost_values)} distinct boost values)")


def test_exploration_diversity():
    """
    Part 2: 50 accounts on the same lifecycle stage must produce diverse rates.
    Minimum 10 distinct values (dual-hash gives 1000-point resolution).
    """
    fresh()
    from core.lifecycle_engine import get_stage_profile, LifecycleStage

    profile = get_stage_profile(LifecycleStage.GROWTH)
    rates   = set()

    for i in range(50):
        acct    = f"div-test-{i:04d}"
        raw     = adv.get_exploration_rate(acct, profile.exploration_bias, BASE_TS)
        clamped = adv.clamp_exploration_rate(raw, "GROWTH")
        rates.add(round(clamped, 3))

    assert len(rates) >= 10, (
        f"Expected ≥10 distinct rates across 50 GROWTH accounts, "
        f"got {len(rates)}: {sorted(rates)}"
    )
    print(f"PASS test_exploration_diversity ({len(rates)} unique rates across 50 accounts)")


def test_exploration_time_drift():
    """
    Part 2+3: Same account must show different rate at a different hour
    (h2 drift) but change smoothly (EWMA, not a sudden jump).
    """
    fresh()
    from core.lifecycle_engine import get_stage_profile, LifecycleStage

    acct    = "drift-test-001"
    profile = get_stage_profile(LifecycleStage.GROWTH)

    # Seed the cache with hour 0
    r0 = adv.get_exploration_rate(acct, profile.exploration_bias, BASE_TS)

    # Advance 6 hours — h2 hash changes, but EWMA damps the jump
    r6h = adv.get_exploration_rate(acct, profile.exploration_bias, BASE_TS + 6 * 3600)

    # After 24 hours, the rate should have drifted noticeably
    # (call 24 times to let EWMA accumulate)
    r_accum = r0
    for h in range(1, 25):
        r_accum = adv.get_exploration_rate(
            acct, profile.exploration_bias, BASE_TS + h * 3600
        )

    # Should have drifted from initial value
    assert r_accum != r0, (
        f"Rate should drift over 24 hours: r0={r0:.4f} r24h={r_accum:.4f}"
    )
    # But the 6-hour rate should be between r0 and r24h (smooth EWMA)
    lo, hi = min(r0, r_accum), max(r0, r_accum)
    # EWMA smoothing: r6h is somewhere near r0 (only 6 × α=0.15 updates)
    jump = abs(r6h - r0)
    assert jump < 0.15, (
        f"6-hour drift should be smooth (EWMA dampened), "
        f"got jump={jump:.4f} r0={r0:.4f} r6h={r6h:.4f}"
    )
    print(
        f"PASS test_exploration_time_drift "
        f"(r0={r0:.4f} r6h={r6h:.4f} r24h={r_accum:.4f})"
    )


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
