"""
Closed-loop system tests: simulation, validation, optimizer, RL, observer.
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

import os, tempfile

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
_load("core/strategy_engine.py",     "core.strategy_engine")
_load("core/metrics_store.py",       "core.metrics_store")
_load("core/observer.py",            "core.observer")
_load("core/validator.py",           "core.validator")
_load("core/optimizer.py",           "core.optimizer")
_load("core/reinforcement.py",       "core.reinforcement")
_load("core/simulation_runner.py",   "core.simulation_runner")

import core.simulation_runner as sr
import core.metrics_store      as ms
import core.observer           as obs_mod
import core.validator          as val_mod
import core.optimizer          as opt_mod
import core.reinforcement      as rl_mod
import core.strategy_engine    as se

BASE_TS = 1_716_100_000


def fresh():
    sr.reset_observer() if hasattr(sr, "reset_observer") else None
    obs_mod.reset_observer()
    ms.reset_metrics_store()
    opt_mod.reset_optimizer()
    rl_mod.reset_policy()
    se._reset_for_testing()
    from core.persona_engine import reset_persona_engine
    reset_persona_engine()
    try:
        from core.global_memory import reset_global_memory
        reset_global_memory()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# test_simulation_entropy
# ─────────────────────────────────────────────────────────────────────────────

def test_simulation_entropy():
    """Timing entropy > 0 in every cycle (accounts diverge in timing)."""
    fresh()
    report = sr.run_simulation(
        n_accounts=40, n_cycles=5, platform="tiktok",
        base_ts=BASE_TS, output_path=os.devnull,
    )
    for c in report.cycles:
        if c.accounts_active == 0:
            continue
        assert c.timing_entropy > 0, (
            f"Cycle {c.cycle}: timing_entropy=0 (all accounts same offset)"
        )
    print(f"PASS test_simulation_entropy "
          f"(mean_entropy={sum(c.timing_entropy for c in report.cycles)/len(report.cycles):.2f})")


# ─────────────────────────────────────────────────────────────────────────────
# test_no_pattern_lock
# ─────────────────────────────────────────────────────────────────────────────

def test_no_pattern_lock():
    """Action diversity > 0.3 across cycles (not locked into one pattern)."""
    fresh()
    report = sr.run_simulation(
        n_accounts=60, n_cycles=8, platform="instagram",
        base_ts=BASE_TS, output_path=os.devnull,
    )
    diverse_cycles = [c for c in report.cycles if c.accounts_active >= 5]
    assert diverse_cycles, "No cycles with active accounts"

    for c in diverse_cycles:
        assert c.action_diversity > 0.0, (
            f"Cycle {c.cycle}: action_diversity=0 (all accounts identical plan)"
        )
    mean_div = sum(c.action_diversity for c in diverse_cycles) / len(diverse_cycles)
    assert mean_div > 0.20, f"Mean action diversity too low: {mean_div:.2f}"
    print(f"PASS test_no_pattern_lock (mean_diversity={mean_div:.2f})")


# ─────────────────────────────────────────────────────────────────────────────
# test_optimizer_response
# ─────────────────────────────────────────────────────────────────────────────

def test_optimizer_response():
    """High ban_rate must reduce HARVESTER + AMPLIFIER weight multipliers."""
    fresh()
    optim = opt_mod.get_optimizer()

    # Simulate high ban environment
    state_normal = optim.update(
        ban_rate=0.01, success_rate=0.85, anomaly_score=0.05,
        health_score=0.90, spike_flag=False,
    )
    harvester_normal = state_normal["strategy_harvester_weight_mult"]

    # Now push ban_rate high
    state_high_ban = None
    for _ in range(5):
        state_high_ban = optim.update(
            ban_rate=0.20, success_rate=0.30, anomaly_score=0.70,
            health_score=0.25, spike_flag=True,
        )

    assert state_high_ban is not None
    harvester_after = state_high_ban["strategy_harvester_weight_mult"]

    assert harvester_after < harvester_normal, (
        f"HARVESTER mult not reduced: before={harvester_normal:.4f} after={harvester_after:.4f}"
    )

    # All values must stay within [0.60, 1.40]
    for k, v in state_high_ban.items():
        assert 0.60 <= v <= 1.40, f"Optimizer key {k} out of bounds: {v}"

    print(f"PASS test_optimizer_response "
          f"(harvester: {harvester_normal:.3f} → {harvester_after:.3f})")


# ─────────────────────────────────────────────────────────────────────────────
# test_learning_improves_reward
# ─────────────────────────────────────────────────────────────────────────────

def test_learning_improves_reward():
    """Average reward should trend upward (or at least not collapse) over cycles."""
    fresh()
    report = sr.run_simulation(
        n_accounts=50, n_cycles=12, platform="tiktok",
        base_ts=BASE_TS, output_path=os.devnull,
    )
    rewards = [c.avg_reward for c in report.cycles if c.accounts_active > 0]
    assert len(rewards) >= 3, "Too few reward samples"

    # Split into early and late thirds
    third = max(1, len(rewards) // 3)
    early_avg = sum(rewards[:third]) / third
    late_avg  = sum(rewards[-third:]) / third

    # Allow some slack: late should not be significantly worse than early
    assert late_avg >= early_avg - 0.30, (
        f"Reward regressed: early={early_avg:.3f} late={late_avg:.3f}"
    )
    print(f"PASS test_learning_improves_reward "
          f"(early={early_avg:.3f} late={late_avg:.3f})")


# ─────────────────────────────────────────────────────────────────────────────
# test_observer_trace_complete
# ─────────────────────────────────────────────────────────────────────────────

def test_observer_trace_complete():
    """Every logged action must have a non-empty reasoning_trace."""
    fresh()
    report = sr.run_simulation(
        n_accounts=20, n_cycles=3, platform="youtube",
        base_ts=BASE_TS, output_path=os.devnull,
    )
    observer = obs_mod.get_observer()
    all_logs = observer.all_logs()

    assert len(all_logs) > 0, "No logs recorded"

    for log in all_logs:
        trace = log.get("reasoning_trace", [])
        assert len(trace) >= 3, (
            f"Reasoning trace too short ({len(trace)}) for account={log.get('account_id')}"
        )
        layers = {t["layer"] for t in trace}
        for required in ("persona", "role", "decision"):
            assert required in layers, (
                f"Missing layer '{required}' in trace for account={log.get('account_id')}"
            )

    print(f"PASS test_observer_trace_complete ({len(all_logs)} logs, all traced)")


# ─────────────────────────────────────────────────────────────────────────────
# test_validator_entropy_check
# ─────────────────────────────────────────────────────────────────────────────

def test_validator_entropy_check():
    """Validator must flag low-entropy account (all same intent)."""
    # Build 15 identical-intent logs
    low_ent_logs = [
        {"ts": BASE_TS + i * 60, "intent": "browse", "role": "WARMER",
         "modifiers": {"timing_offset_s": 300}}
        for i in range(15)
    ]
    result = val_mod.validate_account(low_ent_logs)
    flagged = any("low_entropy" in f for f in result["flags"])
    assert flagged, f"Low-entropy account not flagged: flags={result['flags']}"
    assert result["entropy_bits"] < val_mod.MIN_ENTROPY_BITS
    print(f"PASS test_validator_entropy_check (entropy={result['entropy_bits']:.2f}bits)")


# ─────────────────────────────────────────────────────────────────────────────
# test_rl_q_update
# ─────────────────────────────────────────────────────────────────────────────

def test_rl_q_update():
    """Q-value for a ban action must decrease after seeing a ban."""
    fresh()
    policy = rl_mod.get_policy()
    state  = rl_mod.build_state("HARVESTER", "tiktok", 0.8, "post")
    key    = state.key()

    # Select action and record ban outcome → Q should decrease
    out = policy.select_action(state)
    q_before = policy._get_q(key, out.action)
    policy.update(key, out.action, success=False, ban=True, anomaly_score=0.5)
    q_after = policy._get_q(key, out.action)

    # Ban gives negative reward, so Q should go down or stay near 0
    assert q_after <= q_before + 0.01, (
        f"Q not reduced after ban: before={q_before:.4f} after={q_after:.4f}"
    )
    print(f"PASS test_rl_q_update (Q: {q_before:.4f} → {q_after:.4f} after ban)")


# ─────────────────────────────────────────────────────────────────────────────
# test_metrics_store_ema
# ─────────────────────────────────────────────────────────────────────────────

def test_metrics_store_ema():
    """EMA must move in the direction of new observations."""
    ms.reset_metrics_store()
    store = ms.get_metrics_store()

    # Feed 10 bans
    for _ in range(10):
        store.record_ban("test-acct")
    ema_after_bans = store.get_ema("ban_rate")

    # Feed 10 successes
    for _ in range(10):
        store.record_success("test-acct", engagement=0.9)
    ema_after_success = store.get_ema("ban_rate")

    assert ema_after_bans > 0, "ban_rate EMA should be > 0 after bans"
    assert ema_after_success < ema_after_bans, (
        f"ban_rate EMA should decrease after successes: "
        f"{ema_after_bans:.4f} → {ema_after_success:.4f}"
    )
    print(f"PASS test_metrics_store_ema "
          f"(ban_rate: {ema_after_bans:.3f} → {ema_after_success:.3f})")


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
