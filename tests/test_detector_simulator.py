"""
Tests for core/detector_simulator.py
"""
import importlib.util, sys, time
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

_load("core/global_memory.py",       "core.global_memory")
_load("core/platform_profiles.py",   "core.platform_profiles")
_load("core/mutation_controller.py", "core.mutation_controller")
_load("core/identity_manager.py",    "core.identity_manager")
_load("core/persona_engine.py",      "core.persona_engine")
_load("core/lifecycle_engine.py",    "core.lifecycle_engine")
_load("core/strategy_engine.py",     "core.strategy_engine")
_load("core/metrics_store.py",       "core.metrics_store")
_load("core/observer.py",            "core.observer")
_load("core/detector_simulator.py",  "core.detector_simulator")
_load("core/optimizer.py",           "core.optimizer")
_load("core/reinforcement.py",       "core.reinforcement")

import core.detector_simulator as ds
import core.strategy_engine    as se
import core.optimizer          as opt_mod
import core.reinforcement      as rl_mod

BASE_TS = 1_716_100_000


def fresh():
    ds.reset_detector()
    se._reset_for_testing()
    try:
        from core.lifecycle_engine import reset_lifecycle_engine
        reset_lifecycle_engine()
    except Exception:
        pass
    try:
        from core.persona_engine import reset_persona_engine
        reset_persona_engine()
    except Exception:
        pass
    opt_mod.reset_optimizer()
    rl_mod.reset_policy()


def _make_logs(
    n: int = 20,
    delay: int | None = None,
    intent: str = "browse",
    role:   str = "WARMER",
    niche:  str = "tech",
    ts_step: float = 300.0,
    base_ts: float | None = None,
) -> list[dict]:
    """Build synthetic observer-style action logs."""
    logs = []
    t = float(base_ts or BASE_TS)
    for i in range(n):
        d = delay if delay is not None else 120 + (i % 5) * 30
        logs.append({
            "ts":        t + i * ts_step,
            "delay_s":   d,
            "intent":    intent,
            "role":      role,
            "niche":     niche,
            "modifiers": {"strategy_intensity": 0.5, "timing_offset_s": d},
        })
    return logs


# ─────────────────────────────────────────────────────────────────────────────
# test_timing_detection
# ─────────────────────────────────────────────────────────────────────────────

def test_timing_detection():
    """Identical delays produce high timing_score."""
    fresh()
    acct = "timing-bot-001"

    # All exactly 120s delay → maximum uniformity
    uniform_logs = _make_logs(30, delay=120, ts_step=120.0)
    result = ds.get_detector().evaluate(acct, now=BASE_TS, logs=uniform_logs)

    assert result.sub_scores["timing"] > 0.40, (
        f"Uniform delays should raise timing_score: got {result.sub_scores['timing']:.3f}"
    )
    print(
        f"PASS test_timing_detection "
        f"(timing_score={result.sub_scores['timing']:.3f})"
    )


# ─────────────────────────────────────────────────────────────────────────────
# test_entropy_detection
# ─────────────────────────────────────────────────────────────────────────────

def test_entropy_detection():
    """All-same intent + niche produces high entropy_score (low diversity)."""
    fresh()
    acct = "entropy-bot-001"

    # 30 actions, all identical intent + niche
    mono_logs = _make_logs(30, intent="browse", niche="tech")
    result = ds.get_detector().evaluate(acct, now=BASE_TS, logs=mono_logs)

    entropy_score = result.sub_scores["entropy"]
    assert entropy_score > 0.60, (
        f"Monotone actions should raise entropy_score: got {entropy_score:.3f}"
    )
    print(f"PASS test_entropy_detection (entropy_score={entropy_score:.3f})")


# ─────────────────────────────────────────────────────────────────────────────
# test_entropy_vs_diverse
# ─────────────────────────────────────────────────────────────────────────────

def test_entropy_vs_diverse():
    """Diverse actions produce lower entropy_score than monotone ones."""
    fresh()

    mono_logs = _make_logs(30, intent="browse", niche="tech")
    result_mono = ds.get_detector().evaluate("mono-acct", now=BASE_TS, logs=mono_logs)

    # Build diverse logs
    intents = ["browse", "post", "like", "comment", "share"]
    niches  = ["tech", "fitness", "finance", "entertainment", "food"]
    diverse_logs = []
    for i in range(30):
        diverse_logs.append({
            "ts":       float(BASE_TS + i * 350),
            "delay_s":  60 + (i * 37) % 200,
            "intent":   intents[i % len(intents)],
            "role":     ["WARMER", "EXPLORER", "IDLE"][i % 3],
            "niche":    niches[i % len(niches)],
            "modifiers": {"strategy_intensity": 0.3 + (i % 7) * 0.1, "timing_offset_s": 200},
        })

    fresh()
    result_div = ds.get_detector().evaluate("diverse-acct", now=BASE_TS, logs=diverse_logs)

    assert result_div.sub_scores["entropy"] < result_mono.sub_scores["entropy"], (
        f"Diverse logs should have lower entropy_score than monotone: "
        f"diverse={result_div.sub_scores['entropy']:.3f} mono={result_mono.sub_scores['entropy']:.3f}"
    )
    print(
        f"PASS test_entropy_vs_diverse "
        f"(diverse={result_div.sub_scores['entropy']:.3f} "
        f"mono={result_mono.sub_scores['entropy']:.3f})"
    )


# ─────────────────────────────────────────────────────────────────────────────
# test_session_detection
# ─────────────────────────────────────────────────────────────────────────────

def test_session_detection():
    """Perfect uniform sessions (no idle gaps) produce high session_score."""
    fresh()
    acct = "session-bot-001"

    # 30 actions with exact 200s spacing — no variation, no idle
    perfect_logs = _make_logs(30, ts_step=200.0, delay=200)
    result = ds.get_detector().evaluate(acct, now=BASE_TS, logs=perfect_logs)

    session_score = result.sub_scores["session"]
    assert session_score > 0.30, (
        f"Perfect sessions should raise session_score: got {session_score:.3f}"
    )
    print(f"PASS test_session_detection (session_score={session_score:.3f})")


# ─────────────────────────────────────────────────────────────────────────────
# test_similarity_detection
# ─────────────────────────────────────────────────────────────────────────────

def test_similarity_detection():
    """Monotone sig-tokens raise similarity score via variety_score component."""
    fresh()
    acct = "sim-bot-001"

    # All identical (role, niche, delay_bucket) → zero entropy in sig_tokens
    mono_logs = _make_logs(30, delay=120, role="WARMER", niche="tech", ts_step=120.0)
    result = ds.get_detector().evaluate(acct, now=BASE_TS, logs=mono_logs)

    sim_score = result.sub_scores["similarity"]
    assert sim_score > 0.20, (
        f"Monotone sig-tokens should raise similarity_score: got {sim_score:.3f}"
    )
    print(f"PASS test_similarity_detection (similarity_score={sim_score:.3f})")


# ─────────────────────────────────────────────────────────────────────────────
# test_lifecycle_mismatch
# ─────────────────────────────────────────────────────────────────────────────

def test_lifecycle_mismatch():
    """NEW account acting as HARVESTER raises lifecycle_score."""
    fresh()
    acct       = "lc-mismatch-001"
    new_created = BASE_TS - 1 * 86400   # 1 day old → NEW stage

    # HARVESTER actions with high intensity
    mismatch_logs = []
    for i in range(15):
        mismatch_logs.append({
            "ts":        float(BASE_TS + i * 300),
            "delay_s":   120,
            "intent":    "harvest",
            "role":      "HARVESTER",
            "niche":     "finance",
            "modifiers": {"strategy_intensity": 0.90, "timing_offset_s": 120},
        })

    result = ds.get_detector().evaluate(
        acct, now=BASE_TS, created_ts=new_created, logs=mismatch_logs
    )
    lc_score = result.sub_scores["lifecycle"]
    assert lc_score > 0.30, (
        f"NEW+HARVESTER mismatch should raise lifecycle_score: got {lc_score:.3f}"
    )
    assert "identity_mismatch" in result.flags or lc_score > 0.0, (
        f"Expected identity_mismatch flag or positive score"
    )
    print(f"PASS test_lifecycle_mismatch (lifecycle_score={lc_score:.3f})")


# ─────────────────────────────────────────────────────────────────────────────
# test_risk_smoothing
# ─────────────────────────────────────────────────────────────────────────────

def test_risk_smoothing():
    """Risk score changes gradually (EMA): no single-cycle jumps > 0.40."""
    fresh()
    acct = "smooth-acct-001"
    det  = ds.get_detector()

    # Start safe
    safe_logs = _make_logs(20, ts_step=500.0, delay=500)
    safe_logs_diverse = []
    intents = ["browse", "post", "like", "comment", "search"]
    niches  = ["tech", "fitness", "finance", "entertainment", "food"]
    for i in range(20):
        safe_logs_diverse.append({
            "ts":       float(BASE_TS + i * 500),
            "delay_s":  200 + i * 50,
            "intent":   intents[i % len(intents)],
            "role":     "EXPLORER",
            "niche":    niches[i % len(niches)],
            "modifiers": {"strategy_intensity": 0.4, "timing_offset_s": 300},
        })

    r1 = det.evaluate(acct, now=BASE_TS, logs=safe_logs_diverse)

    # Switch to bot-like
    bot_logs = _make_logs(40, delay=120, ts_step=120.0, intent="browse", niche="tech")
    r2 = det.evaluate(acct, now=BASE_TS + 3600, logs=bot_logs)
    r3 = det.evaluate(acct, now=BASE_TS + 7200, logs=bot_logs)

    delta_12 = abs(r2.risk_score - r1.risk_score)
    delta_23 = abs(r3.risk_score - r2.risk_score)

    assert delta_12 <= ds._MAX_RISK_JUMP + 0.01, (
        f"Risk jump too large: {r1.risk_score:.3f} → {r2.risk_score:.3f} (delta={delta_12:.3f})"
    )
    assert delta_23 <= ds._MAX_RISK_JUMP + 0.01, (
        f"Risk jump too large: {r2.risk_score:.3f} → {r3.risk_score:.3f} (delta={delta_23:.3f})"
    )
    # Risk must increase toward bot-like logs
    assert r3.risk_score > r1.risk_score, (
        f"Risk should increase toward bot-like logs: {r1.risk_score:.3f} → {r3.risk_score:.3f}"
    )
    print(
        f"PASS test_risk_smoothing "
        f"(r1={r1.risk_score:.3f} r2={r2.risk_score:.3f} r3={r3.risk_score:.3f})"
    )


# ─────────────────────────────────────────────────────────────────────────────
# test_no_cross_account_leak
# ─────────────────────────────────────────────────────────────────────────────

def test_no_cross_account_leak():
    """Different accounts with same logs produce different (non-identical) risk scores."""
    fresh()
    det = ds.get_detector()

    # Same identical logs for every account
    logs = _make_logs(25, delay=120, ts_step=120.0, intent="browse", niche="tech")

    scores = {}
    for i in range(10):
        acct = f"leak-acct-{i:04d}"
        result = det.evaluate(acct, now=BASE_TS, logs=logs)
        scores[acct] = result.risk_score

    # Similarity_score component is account-specific (cluster_id differs)
    # So sub_scores["similarity"] must differ across accounts
    sim_scores = set()
    for i in range(10):
        acct   = f"leak-acct-{i:04d}"
        result = det.evaluate(acct, now=BASE_TS, logs=logs)
        sim_scores.add(round(result.sub_scores["similarity"], 3))

    assert len(sim_scores) > 1, (
        f"All accounts got identical similarity_score — cross-account leak! "
        f"scores={sim_scores}"
    )

    # Verify RISK_MEMORY only has per-account entries, not shared
    for i in range(10):
        acct = f"leak-acct-{i:04d}"
        assert acct in ds._RISK_MEMORY, f"Account {acct} missing from _RISK_MEMORY"

    print(
        f"PASS test_no_cross_account_leak "
        f"({len(sim_scores)} unique sim_scores across 10 accounts)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# test_strategy_block
# ─────────────────────────────────────────────────────────────────────────────

def test_strategy_block():
    """Account with detector risk ≥ 0.85 must get no action plan."""
    fresh()
    acct = "strategy-block-001"

    # Manually force a high risk score into _RISK_MEMORY
    ds._RISK_MEMORY[acct] = 0.90

    plan = se.plan_actions(
        account_id = acct,
        platform   = "tiktok",
        created_ts = BASE_TS - 30 * 86400,
        now        = BASE_TS,
        risk_score = 0.1,   # caller-level risk is LOW, detector should block
    )

    assert plan is None, (
        f"Account with detector risk=0.90 should be blocked but got plan: "
        f"role={plan.role.value if plan else 'N/A'}"
    )
    print("PASS test_strategy_block (detector_risk=0.90 → plan=None)")


# ─────────────────────────────────────────────────────────────────────────────
# test_optimizer_detector_response
# ─────────────────────────────────────────────────────────────────────────────

def test_optimizer_detector_response():
    """High detector_risk_score must increase delay and reduce burstiness."""
    fresh()
    optim = opt_mod.get_optimizer()

    # Baseline with low risk
    state_safe = optim.update(
        ban_rate=0.01, success_rate=0.85, anomaly_score=0.05,
        health_score=0.90, spike_flag=False, detector_risk_score=0.10,
    )
    delay_safe = state_safe["platform_delay_base_mult"]

    # High detector risk
    state_danger = None
    for _ in range(5):
        state_danger = optim.update(
            ban_rate=0.05, success_rate=0.60, anomaly_score=0.30,
            health_score=0.60, spike_flag=False, detector_risk_score=0.90,
        )

    assert state_danger is not None
    delay_danger = state_danger["platform_delay_base_mult"]
    burst_danger = state_danger["platform_burstiness_mult"]

    assert delay_danger > delay_safe, (
        f"Delay mult should increase under high detector risk: "
        f"safe={delay_safe:.4f} danger={delay_danger:.4f}"
    )
    assert burst_danger < 1.0, (
        f"Burstiness mult should decrease under high detector risk: {burst_danger:.4f}"
    )
    print(
        f"PASS test_optimizer_detector_response "
        f"(delay: {delay_safe:.3f}→{delay_danger:.3f}, burst={burst_danger:.3f})"
    )


# ─────────────────────────────────────────────────────────────────────────────
# test_reinforcement_penalty
# ─────────────────────────────────────────────────────────────────────────────

def test_reinforcement_penalty():
    """High detector_risk_score reduces reward in RL update."""
    fresh()
    policy = rl_mod.get_policy()

    state_key = "WARMER|tiktok|moderate|browse"
    action    = "no_op"

    # Same outcome, different detector risk
    reward_safe = policy.update(
        state_key=state_key, action=action,
        success=True, ban=False, anomaly_score=0.0,
        detector_risk_score=0.10,
    )
    policy.reset()

    reward_risky = policy.update(
        state_key=state_key, action=action,
        success=True, ban=False, anomaly_score=0.0,
        detector_risk_score=0.90,
    )

    assert reward_safe > reward_risky, (
        f"High detector risk should reduce reward: "
        f"safe={reward_safe:.3f} risky={reward_risky:.3f}"
    )
    print(
        f"PASS test_reinforcement_penalty "
        f"(safe_reward={reward_safe:.3f} risky_reward={reward_risky:.3f})"
    )


# ─────────────────────────────────────────────────────────────────────────────
# test_all_scores_bounded
# ─────────────────────────────────────────────────────────────────────────────

def test_all_scores_bounded():
    """All sub-scores and composite risk must be in [0.0, 1.0]."""
    fresh()
    det = ds.get_detector()

    test_cases = [
        _make_logs(30, delay=120, ts_step=120.0),   # bot-like
        _make_logs(30, delay=500, ts_step=600.0),   # slow
        [],   # empty
        _make_logs(2),   # very few
    ]

    for i, logs in enumerate(test_cases):
        result = det.evaluate(f"bounds-acct-{i}", now=BASE_TS, logs=logs)
        assert 0.0 <= result.risk_score <= 1.0, (
            f"risk_score out of bounds: {result.risk_score}"
        )
        for key, score in result.sub_scores.items():
            assert 0.0 <= score <= 1.0, (
                f"sub_score[{key}] out of bounds: {score}"
            )

    print("PASS test_all_scores_bounded")


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
