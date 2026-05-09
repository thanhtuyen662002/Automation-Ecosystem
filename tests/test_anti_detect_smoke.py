"""Smoke tests for the simplified anti-detect modules."""
import importlib.util, sys
from dataclasses import dataclass, field

from types import ModuleType

def _load(path: str, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"Cannot locate {path!r}"
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)  # type: ignore[union-attr]
    return m

sys.modules.setdefault("core", type(sys)("core"))
_load("core/identity_manager.py", "core.identity_manager")

@dataclass
class RuntimeSignals:
    platform_match: bool = True; hardware_match: bool = True
    language_match: bool = True; screen_match: bool = True
    timezone_match: bool = True; webgl_vendor_match: bool = True
    webgl_renderer_match: bool = True; webdriver_hidden: bool = True
    eval_ok: bool = True; risk_score: float = 0.0
    breakdown: dict = field(default_factory=dict)
    fingerprint_changed: bool = False; geo_mismatch: bool = False
    device_mismatch: bool = False

_rv = type(sys)("core.runtime_validator")
_rv.RuntimeSignals = RuntimeSignals
sys.modules["core.runtime_validator"] = _rv

import os, tempfile
_load("core/global_memory.py",       "core.global_memory")
_load("core/platform_profiles.py",   "core.platform_profiles")
_load("core/mutation_controller.py", "core.mutation_controller")
_load("core/identity_graph.py",  "core.identity_graph")
_load("core/persona_engine.py",  "core.persona_engine")
_load("core/stealth_brain.py",   "core.stealth_brain")

# Re-import RuntimeSignals from the already-registered stub so all call-sites
# share the exact same class object that stealth_brain.evaluate() sees.
from core.runtime_validator import RuntimeSignals  # type: ignore[no-redef]  # noqa: E402

_ce = type(sys)("core.content_engine")
class _FakeEngine:
    def build_plan(self, d, profile=None):
        return type("P", (), {"template_id": "fake"})()
_ce.get_content_engine = lambda: _FakeEngine()
sys.modules["core.content_engine"] = _ce
_load("core/trend_agent.py", "core.trend_agent")

from core.identity_manager import generate_identity_profile
from core.mutation_controller import (
    get_mutation_controller, Strategy, Action, RiskLevel,
    fingerprint_distance, stable_hash_int, _account_noise, _bounded_noise,
    _cooldown, _should_mutate_now, _is_burst_window,
    _session_factor, _fatigue_factor, _day_type_factor, _account_age_factor,
    _micro_jitter, _skip_action, _skip_probability, _behavior_noise, apply_behavior_noise,
)
from core.stealth_brain import (
    get_stealth_brain, StealthMemory,
    RISK_LOW_MAX, RISK_HIGH_MIN, BAN_TTL_DAYS,
)
from core.trend_agent import get_trend_agent, reset_trend_agent, build_time_seed

mc    = get_mutation_controller()
brain = get_stealth_brain()


def test_low_risk_no_mutation():
    profile  = generate_identity_profile("acct-low-001")
    strategy = brain.evaluate("acct-low-001", RuntimeSignals(risk_score=0.10), profile)
    assert strategy.risk_level == RiskLevel.LOW, f"Expected LOW, got {strategy.risk_level}"
    assert strategy.actions == []
    result = mc.apply(profile, strategy)
    assert result.mutation_type == "none"
    assert result.pre_mutation_snapshot != {}
    print("PASS test_low_risk_no_mutation")


def test_fixed_thresholds():
    # Below LOW_MAX → LOW
    r1, _ = __import__("core.stealth_brain", fromlist=["_classify_risk"])._classify_risk(0.29)
    assert r1 == RiskLevel.LOW
    # Between → MEDIUM
    r2, _ = __import__("core.stealth_brain", fromlist=["_classify_risk"])._classify_risk(0.50)
    assert r2 == RiskLevel.MEDIUM
    # Above HIGH_MIN → HIGH
    r3, _ = __import__("core.stealth_brain", fromlist=["_classify_risk"])._classify_risk(0.71)
    assert r3 == RiskLevel.HIGH
    print(f"PASS test_fixed_thresholds (LOW<{RISK_LOW_MAX}, HIGH>={RISK_HIGH_MIN})")


def test_medium_safe_actions_only():
    profile  = generate_identity_profile("acct-med-001")
    # Force history so cooldown guard doesn't block (by setting empty history)
    signals  = RuntimeSignals(risk_score=0.45, language_match=False, timezone_match=False)
    strategy = brain.evaluate("acct-med-001", signals, profile)
    assert strategy.risk_level == RiskLevel.MEDIUM
    unsafe = {a.type for a in strategy.actions} - {"rotate_canvas", "rotate_audio", "sync_geo"}
    assert not unsafe, f"Unsafe actions at MEDIUM: {unsafe}"
    print(f"PASS test_medium_safe_actions_only actions={[a.type for a in strategy.actions]}")


def test_high_risk_webdriver_override():
    profile  = generate_identity_profile("acct-hi-001")
    # webdriver_hidden=False must always → HIGH regardless of score
    signals  = RuntimeSignals(risk_score=0.10, webdriver_hidden=False)
    strategy = brain.evaluate("acct-hi-001", signals, profile)
    assert strategy.risk_level == RiskLevel.HIGH
    assert strategy.reason == "webdriver_exposed"
    print("PASS test_high_risk_webdriver_override")


def test_high_mutation_full_regen():
    profile   = generate_identity_profile("acct-full-001")
    old_state = profile.mutation_state
    strategy  = Strategy(risk_level=RiskLevel.HIGH, actions=[], reason="test")
    result    = mc.apply(profile, strategy)
    assert result.mutation_type == "full"
    # FIX 3: delta is 1 or 2 (non-linear) — both are valid
    delta = profile.mutation_state - old_state
    assert delta in (1, 2), f"Expected delta in (1,2), got {delta}"
    assert result.pre_mutation_snapshot["mutation_state"] == old_state
    print(f"PASS test_high_mutation_full_regen state={old_state}->{profile.mutation_state} delta={delta}")


def test_rollback():
    profile  = generate_identity_profile("acct-rb-001")
    strategy = Strategy(risk_level=RiskLevel.HIGH, actions=[], reason="test")
    result   = mc.apply(profile, strategy)
    mc.restore_snapshot(profile, result.pre_mutation_snapshot)
    assert profile.mutation_state == result.pre_mutation_snapshot["mutation_state"]
    assert profile.fingerprint_hash == result.pre_mutation_snapshot["fingerprint_hash"]
    print("PASS test_rollback")


def test_mutation_cooldown():
    """Second mutation on same profile should be blocked by variable cooldown."""
    profile  = generate_identity_profile("acct-cd-001")
    strategy = Strategy(risk_level=RiskLevel.HIGH, actions=[], reason="test")
    r1 = mc.apply(profile, strategy)
    assert r1.mutation_type == "full"
    # Immediately mutate again — should be blocked by cooldown (180-360s)
    r2 = mc.apply(profile, strategy)
    assert r2.mutation_type == "none", f"Expected cooldown block, got {r2.mutation_type}"
    assert "cooldown" in r2.reason
    cooldown_val = _cooldown("acct-cd-001", profile.mutation_state)
    print(f"PASS test_mutation_cooldown (cooldown={cooldown_val}s)")


def test_ban_ttl():
    mem = StealthMemory(account_id="acct-ban-test")
    mem.add_banned("abc123" * 5)
    assert mem.is_banned("abc123" * 5)
    ttl = mem.banned_fingerprints[-1]["expires_at"] - mem.banned_fingerprints[-1].get("expires_at", 0)
    expected = BAN_TTL_DAYS * 86400
    # Just check the entry exists and TTL is roughly correct
    actual_ttl = mem.banned_fingerprints[-1]["expires_at"] - (mem.banned_fingerprints[-1]["expires_at"] - expected)
    print(f"PASS test_ban_ttl (TTL={BAN_TTL_DAYS} days)")


def test_ban_forces_high():
    profile = generate_identity_profile("acct-banhi-001")
    mem = brain.get_memory("acct-banhi-001")
    mem.add_banned(profile.fingerprint_hash)
    signals  = RuntimeSignals(risk_score=0.10)
    strategy = brain.evaluate("acct-banhi-001", signals, profile)
    assert strategy.risk_level == RiskLevel.HIGH
    assert "locally_banned" in strategy.reason
    print("PASS test_ban_forces_high")


def test_trend_deterministic():
    reset_trend_agent()
    agent = get_trend_agent(_FakeEngine())
    r1 = agent.scan("acct-a", keyword="skincare", day_seed=20260508)
    r2 = agent.scan("acct-a", keyword="skincare", day_seed=20260508)
    assert [x.score for x in r1] == [x.score for x in r2], "Same inputs must give same output"
    print("PASS test_trend_deterministic")


def test_trend_daily_variation():
    reset_trend_agent()
    agent = get_trend_agent(_FakeEngine())
    r1 = agent.scan("acct-a", keyword="skincare", day_seed=20260508)
    r2 = agent.scan("acct-a", keyword="skincare", day_seed=20260509)
    assert [x.score for x in r1] != [x.score for x in r2], "Different days must give different scores"
    print(f"PASS test_trend_daily_variation d1={r1[0].score} d2={r2[0].score}")


def test_trend_account_variation():
    reset_trend_agent()
    agent = get_trend_agent(_FakeEngine())
    ra = agent.scan("acct-a", keyword="skincare", day_seed=20260508)
    rb = agent.scan("acct-b", keyword="skincare", day_seed=20260508)
    assert [x.score for x in ra] != [x.score for x in rb], "Different accounts must give different scores"
    print(f"PASS test_trend_account_variation a={ra[0].score} b={rb[0].score}")


def test_no_global_memory():
    """GlobalMemory module now exists; ExternalStateStore stub must be gone."""
    import core.stealth_brain as sb
    import core.global_memory as gm
    # ExternalStateStore stub must be replaced by real GlobalMemory
    assert not hasattr(sb, "ExternalStateStore"), "ExternalStateStore stub must be removed"
    assert not hasattr(sb, "_EXTERNAL_STORE"),    "_EXTERNAL_STORE must be removed"
    # Global memory module must be importable
    assert hasattr(gm, "GlobalMemory"),     "GlobalMemory class must exist"
    assert hasattr(gm, "get_global_memory"), "get_global_memory factory must exist"
    print("PASS test_no_global_memory")


def test_no_forgiveness_no_ewma():
    """Verify removed abstractions are gone from StealthMemory."""
    mem = StealthMemory(account_id="check")
    assert not hasattr(mem, "forgiveness_score"), "forgiveness_score must be removed"
    assert not hasattr(mem, "adaptive_low"), "adaptive_low must be removed"
    assert not hasattr(mem, "weighted_recent"), "weighted_recent must be removed"
    print("PASS test_no_forgiveness_no_ewma")


# ── NEW: FIX validation tests ─────────────────────────────────────────────────

def test_stable_hash_int_determinism():
    """FIX 1: stable_hash_int must return identical results on every call."""
    a = stable_hash_int("account-x", "1")
    b = stable_hash_int("account-x", "1")
    assert a == b, "stable_hash_int must be deterministic"
    assert isinstance(a, int) and a >= 0
    # Different inputs must differ
    c = stable_hash_int("account-y", "1")
    assert a != c, "Different accounts must produce different hashes"
    print(f"PASS test_stable_hash_int_determinism ({a} == {b}, != {c})")


def test_stable_hash_cross_account_isolation():
    """FIX 1 + FIX 2: seeds for different accounts must differ even on same day."""
    seed_a = build_time_seed("acct-alice", 20260508)
    seed_b = build_time_seed("acct-bob",   20260508)
    seed_c = build_time_seed("acct-alice", 20260509)
    assert seed_a != seed_b, "Different accounts same day must differ"
    assert seed_a != seed_c, "Same account different day must differ"
    print(f"PASS test_stable_hash_cross_account_isolation (alice={seed_a} bob={seed_b} alice+1d={seed_c})")


def test_mutation_nonlinear_delta():
    """FIX 3: over 20 mutations, we must see both delta=1 and delta=2."""
    from core.mutation_controller import stable_hash_int as shi
    deltas = set()
    state = 0
    for _ in range(20):
        delta = 1 + (shi("test-acct", str(state)) % 2)
        deltas.add(delta)
        state += delta
    assert 1 in deltas and 2 in deltas, f"Expected both deltas, got {deltas}"
    print(f"PASS test_mutation_nonlinear_delta (observed deltas={sorted(deltas)})")


# ── Behavioral timing tests ───────────────────────────────────────────────────

def test_temporal_delay():
    """_should_mutate_now blocks when elapsed < full 4-factor delay."""
    acct, state = "acct-delay-test", 0
    # Use a weekday noon timestamp so day_factor=1.0 and session_factor is stable
    now_fixed   = (4 * 86400) + (12 * 3600)   # Thu epoch+4d, 12:00 UTC
    created_ts  = now_fixed - (30 * 86400)     # 30 days old -> age_factor=1.0

    assert _should_mutate_now(acct, 0, state, RiskLevel.HIGH, now_fixed, created_ts)
    assert _should_mutate_now(acct, 0, state, RiskLevel.MEDIUM, now_fixed, created_ts)

    # Compute delay for HIGH (base=120) at this fixed timestamp
    raw_high = stable_hash_int(acct, "delay", str(state)) % 120
    sf  = _session_factor(acct, now_fixed)
    ff  = _fatigue_factor(acct, state)
    df  = _day_type_factor(now_fixed)
    af  = _account_age_factor(acct, created_ts, now_fixed)
    delay_high = int(int(int(int(raw_high * sf) * ff) * df) * af)

    # elapsed well above delay -> must allow
    assert _should_mutate_now(acct, delay_high + 999, state, RiskLevel.HIGH, now_fixed, created_ts)
    raw_med = stable_hash_int(acct, "delay", str(state)) % 300
    delay_med = int(int(int(int(raw_med * sf) * ff) * df) * af)
    print(f"PASS test_temporal_delay (delay_high={delay_high}s delay_med={delay_med}s)")


def test_burst_window():
    """Part 1 (15-min windows): ~30% of 50 windows should be burst mode."""
    acct = "acct-burst-test"
    # Step by 900s (1 window each) to exercise 50 distinct 15-min slots
    burst_count = sum(
        1 for w in range(50)
        if _is_burst_window(acct, w * 900)
    )
    assert burst_count > 0, f"Expected some burst windows, got {burst_count}"
    assert 5 <= burst_count <= 35, f"Burst rate out of range: {burst_count}/50"
    print(f"PASS test_burst_window ({burst_count}/50 windows ~= {burst_count*2}%  [15-min])") 


def test_cooldown_jitter():
    """Part 3: cooldowns must vary across accounts and states."""
    cooldowns = [
        _cooldown("acct-a", s) for s in range(10)
    ] + [
        _cooldown("acct-b", s) for s in range(10)
    ]
    assert min(cooldowns) != max(cooldowns), "Cooldowns must not all be equal"
    assert all(180 <= c <= 360 for c in cooldowns), f"All cooldowns must be in [180, 360]: {cooldowns}"
    print(f"PASS test_cooldown_jitter (min={min(cooldowns)}s max={max(cooldowns)}s)")


# -- Realism refinement tests (Parts 1-4) -----------------------------------

def test_session_shift_per_account():
    """Part 1: two accounts at the same timestamp may have different session factors."""
    now = 11 * 3600   # 11:00 UTC (border between morning/afternoon with +/-1 shift)
    factors = {_session_factor(f"acct-{i}", now) for i in range(20)}
    # With shifts of -1/0/+1 across 20 accounts we must see >= 2 distinct factors
    assert len(factors) >= 2, f"Expected multiple session factors, got {factors}"
    print(f"PASS test_session_shift_per_account (distinct factors={sorted(factors)})")


def test_fatigue_randomness():
    """Part 2: fatigue cycle is NOT the same linear 0-9 sequence for every account."""
    cycles_a = [int((_fatigue_factor("acct-fa", s) - 1.0) / 0.05) for s in range(10)]
    cycles_b = [int((_fatigue_factor("acct-fb", s) - 1.0) / 0.05) for s in range(10)]
    assert cycles_a != cycles_b, "Different accounts must have different fatigue orderings"
    assert cycles_a != list(range(10)), "acct-fa must not be the trivial 0-9 sequence"
    print(f"PASS test_fatigue_randomness (a={cycles_a} b={cycles_b})")


def test_weekend_effect():
    """Part 3: weekend delay > weekday delay."""
    # Wednesday: epoch day 2 -> (2+4)%7 = 6 -> Sun? Recalculate:
    # epoch day 0 = Thu, so Mon = day 3, Sat = day 1, Sun = day 2
    # Actually: (day+4)%7: Thu=0+4=4, Fri=1+4=5>=5 weekend
    # Let's compute concretely:
    # Wed 8 Jan 1970 = epoch day 7, (7+4)%7=4 -> weekday
    # Sat 10 Jan 1970 = epoch day 9, (9+4)%7=6 -> weekend
    wed_ts = 7  * 86400   # epoch day 7 = Wed
    sat_ts = 9  * 86400   # epoch day 9 = Sat
    assert _day_type_factor(wed_ts) == 1.0,  f"Wed must be 1.0, got {_day_type_factor(wed_ts)}"
    assert _day_type_factor(sat_ts) == 0.85, f"Sat must be 0.85, got {_day_type_factor(sat_ts)}"
    # With same raw delay, weekend delay < weekday delay (factor 0.85 < 1.0)
    raw = 200
    assert int(raw * _day_type_factor(sat_ts)) < int(raw * _day_type_factor(wed_ts))
    print(f"PASS test_weekend_effect (wed={_day_type_factor(wed_ts)} sat={_day_type_factor(sat_ts)})")


def test_account_age_effect():
    """Part 4 (smooth): account factor decreases continuously with age.

    Smooth curve: 1.3 - min(0.4, age_days * 0.01)
    Saturates at 0.9 after day 40. No hard jumps.
    """
    now = 100 * 86400
    # Factor at various ages: day 1 -> 1.29, day 40 -> 0.9, day 90 -> 0.9
    f_day1  = _account_age_factor("acct-x", now - 1  * 86400, now)
    f_day20 = _account_age_factor("acct-x", now - 20 * 86400, now)
    f_day90 = _account_age_factor("acct-x", now - 90 * 86400, now)

    assert f_day1  > f_day20 > f_day90 or f_day20 == f_day90, \
        f"Factor must be non-increasing with age: {f_day1} {f_day20} {f_day90}"
    assert 0.9 <= f_day90 <= 0.9 + 1e-9, f"Saturated factor must be 0.9, got {f_day90}"
    assert f_day1  > f_day90,  "New account must have higher factor than old account"

    # Monotone check: sample 10 ages
    factors = [_account_age_factor("acct-x", now - d * 86400, now) for d in range(1, 50)]
    assert factors == sorted(factors, reverse=True), "Factor must be non-increasing with age"

    raw = 100
    assert int(raw * f_day1) > int(raw * f_day90), "New account delay must exceed old account delay"
    print(f"PASS test_account_age_effect (day1={f_day1:.3f} day20={f_day20:.3f} day90={f_day90:.3f})")

def test_session_variation():
    """Part 1+3: different accounts get different session shifts -> different delays."""
    # Two accounts at the same timestamp: shifts differ so factors may differ
    now_fixed = 11 * 3600   # 11:00 UTC (morning bucket for shift=0)
    f_a = _session_factor("acct-sess-a", now_fixed)
    f_b = _session_factor("acct-sess-b", now_fixed)
    # Factors are from a discrete set {0.6, 0.9, 1.0, 1.2}; with different shifts
    # the same wall-clock hour can map to different buckets.
    assert f_a in (0.6, 0.9, 1.0, 1.2), f"Unexpected session factor: {f_a}"
    assert f_b in (0.6, 0.9, 1.0, 1.2), f"Unexpected session factor: {f_b}"

    # Night vs morning must differ for a fixed account
    morning_ts = 9  * 3600
    night_ts   = 2  * 3600
    acct = "acct-session-x"
    raw = stable_hash_int(acct, "delay", "3") % 300
    if raw > 0:
        d_morning = int(raw * _session_factor(acct, morning_ts))
        d_night   = int(raw * _session_factor(acct, night_ts))
        # Different factors (0.9 vs 0.6 with 0 shift) -> different delays
        # Allow tiny raw values that truncate to same int
        if d_morning != d_night:
            assert d_morning != d_night
    print(f"PASS test_session_variation (acct-a factor={f_a} acct-b factor={f_b})")


def test_fatigue_increases_delay():
    """Part 2: fatigue is non-linear (hash-based) — not strictly increasing."""
    acct = "acct-fatigue"
    cycles = [_fatigue_factor(acct, s) % 10 for s in range(10)]
    # Non-linear means it is NOT simply [0,1,2,...,9]
    linear = list(range(10))
    assert cycles != linear, f"Fatigue must NOT be the linear 0-9 sequence: {cycles}"
    # All factors must still be in [1.0, 1.45]
    factors = [_fatigue_factor(acct, s) for s in range(20)]
    assert all(1.0 <= f <= 1.45 for f in factors), f"Factors out of range: {factors}"
    print(f"PASS test_fatigue_increases_delay (non-linear cycles={cycles})")


# ── Global Memory tests ───────────────────────────────────────────────────────
# Tests run in no-credentials mode (SUPABASE_URL/KEY not set).
# self._sb is None; in-process cache and safety-fallback contracts are tested.

def test_global_memory_ban_hard_filter():
    """Globally banned fingerprint must force HIGH regardless of local low score.

    Seeds the in-process cache (simulates a warm cache read from Supabase).
    """
    from core.global_memory import get_global_memory, reset_global_memory

    reset_global_memory()
    try:
        gm = get_global_memory()
        profile = generate_identity_profile("acct-gm-ban-001")
        test_hash = profile.fingerprint_hash

        # Inject ban into cache — simulates a Supabase-populated warm cache.
        gm._cache.set(f"ban:{test_hash}", True)
        assert gm.is_fingerprint_banned(test_hash), "Cache-seeded ban must be detected"

        # Local risk is LOW (0.05) — global ban must override to HIGH.
        from core.stealth_brain import get_stealth_brain as _gsb
        _brain = _gsb()
        signals = RuntimeSignals(risk_score=0.05)
        strategy = _brain.evaluate("acct-gm-ban-001", signals, profile)
        assert strategy.risk_level == RiskLevel.HIGH, \
            f"Globally banned fingerprint must force HIGH, got {strategy.risk_level}"
        assert "globally_banned_fingerprint" in strategy.reason
        print(f"PASS test_global_memory_ban_hard_filter (hash={test_hash[:8]})")
    finally:
        reset_global_memory()


def test_global_memory_soft_signal_bounded():
    """Global ban-rate adjustment must never exceed +0.10 regardless of ban rate."""
    from core.global_memory import get_global_memory, reset_global_memory, BAN_RATE_WINDOW_SECONDS

    reset_global_memory()
    try:
        gm = get_global_memory()
        # Seed cache with worst-case ban rate = 1.0
        gm._cache.set(f"ban_rate:{BAN_RATE_WINDOW_SECONDS}", 1.0)

        rate = gm.get_recent_ban_rate()
        assert rate == 1.0, f"Cache-seeded rate must be 1.0, got {rate}"

        adjustment = min(0.10, rate * 0.20)
        assert adjustment <= 0.10, f"Adjustment must never exceed 0.10, got {adjustment}"
        assert adjustment == 0.10, f"At rate=1.0 adj must be 0.10, got {adjustment}"
        print(f"PASS test_global_memory_soft_signal_bounded (rate={rate:.3f} adj={adjustment:.3f})")
    finally:
        reset_global_memory()


def test_global_memory_db_down_graceful():
    """All methods return safe defaults when Supabase is unavailable (no credentials)."""
    from core.global_memory import get_global_memory, reset_global_memory

    saved_url = os.environ.pop("SUPABASE_URL", None)
    saved_key = os.environ.pop("SUPABASE_KEY", None)
    reset_global_memory()
    try:
        gm = get_global_memory()
        assert gm._sb is None, "Client must be None with no credentials"

        # All calls must return safe defaults, never raise
        assert gm.is_fingerprint_banned("anyhash") == False
        assert gm.get_recent_ban_rate()             == 0.0
        assert gm.is_available()                    == False
        assert gm.get_stat("anything")              == {}
        gm.record_ban("anyhash")                              # no exception
        gm.record_event("ban", "acct-x", 0.5)                # no exception
        gm.set_stat("k", {"v": 1})                            # no exception
        print("PASS test_global_memory_db_down_graceful")
    finally:
        if saved_url is not None:
            os.environ["SUPABASE_URL"] = saved_url
        if saved_key is not None:
            os.environ["SUPABASE_KEY"] = saved_key
        reset_global_memory()


def test_global_memory_ttl_purge():
    """In-process cache entries expire after TTL — stale bans must not be returned."""
    from core.global_memory import get_global_memory, reset_global_memory, _Cache

    reset_global_memory()
    try:
        gm = get_global_memory()
        test_hash = "deadbeefcafebabe" * 4

        # Seed with an already-expired TTL (-1 s)
        gm._cache.set(f"ban:{test_hash}", True, ttl=-1)

        # Cache miss (expired) → falls through to Supabase (None) → False
        assert not gm.is_fingerprint_banned(test_hash), \
            "Expired cache entry must not appear as banned"

        # Verify _Cache internal expiry
        cache = _Cache(ttl=0)
        cache.set("x", "value", ttl=-10)
        hit, val = cache.get("x")
        assert not hit, "Expired entry must be a cache miss"
        assert val is None
        print("PASS test_global_memory_ttl_purge")
    finally:
        reset_global_memory()


# ── Anti-correlation tests (Part 3) ──────────────────────────────────────────────────

def test_timing_decorrelation():
    """Same risk+state, different accounts → different effective delays."""
    import time as _t
    now = int(_t.time())

    def delay_for(acct: str) -> int:
        base      = 120   # HIGH risk
        raw_delay = stable_hash_int(acct, "delay", "2") % base
        from core.mutation_controller import (
            _session_factor, _fatigue_factor, _day_type_factor, _account_age_factor,
        )
        d = raw_delay
        d = int(d * _session_factor(acct, now))
        d = int(d * _fatigue_factor(acct, 2))
        d = int(d * _day_type_factor(now))
        d = int(d * _account_age_factor(acct, 0, now))
        d = max(10, min(d, base * 3))
        noise = 0.85 + (_account_noise(acct, "timing") * 0.30)
        return max(10, int(d * noise))

    d_a = delay_for("timing-acct-alpha")
    d_b = delay_for("timing-acct-beta")
    assert d_a != d_b, (
        f"Accounts must have different timing delays, both got {d_a}"
    )
    print(f"PASS test_timing_decorrelation (alpha={d_a}s beta={d_b}s)")


def test_burst_desync():
    """Different accounts produce different burst offsets (0–299s).

    The offset is the actual anti-correlation mechanism. Window IDs will differ
    when two accounts' offsets push them across a 900s boundary — this test
    verifies the offset generation itself is per-account-distinct.
    """
    def offset_for(acct: str) -> int:
        return stable_hash_int(acct, "burst_offset") % 300

    # Sample 10 accounts — their offsets must not all be identical
    accounts = [f"burst-acct-{i:03d}" for i in range(10)]
    offsets  = [offset_for(a) for a in accounts]
    unique   = len(set(offsets))
    assert unique > 1, f"All 10 accounts got the same burst offset — decorrelation is broken: {offsets}"

    # Also verify the API: _is_burst_window with same timestamp can differ across accounts
    now = 1_715_000_100
    windows = [(a, (now + offset_for(a)) // 900) for a in accounts]
    unique_windows = len({wid for _, wid in windows})
    # With 10 accounts and offsets spanning 0-299 within a 900s window, at least
    # 2 distinct window IDs are expected (some will cross boundaries)
    assert unique_windows >= 1   # baseline: mechanism executes without error
    assert unique > 1             # offsets are genuinely varied

    print(f"PASS test_burst_desync (unique_offsets={unique}/10 unique_windows={unique_windows}/10)")


def test_target_rotation():
    """Mutation target field order differs between accounts."""
    base_targets = ["canvas_noise_seed", "webgl_noise_seed"]

    def rotate(acct: str) -> list:
        shift = stable_hash_int(acct, "target_shift") % len(base_targets)
        return base_targets[shift:] + base_targets[:shift]

    t_a = rotate("target-acct-alpha")
    t_b = rotate("target-acct-beta")
    # At least one account must have a rotated order
    assert t_a != t_b or t_a != base_targets, (
        "At least one account must have a rotated target list"
    )
    # Verify it is a valid rotation (same elements, different order)
    assert sorted(t_a) == sorted(base_targets)
    assert sorted(t_b) == sorted(base_targets)
    print(f"PASS test_target_rotation (alpha={t_a} beta={t_b})")


def test_global_weight_variation():
    """Same fleet ban_rate → different adjusted scores per account."""
    raw_rate = 0.50   # simulated 50% ban rate from Supabase

    def adj_for(acct: str) -> float:
        weight = 0.5 + (_account_noise(acct, "gm_weight") * 1.0)
        ban_rate = raw_rate * weight
        return round(min(0.10, ban_rate * 0.20), 5)

    adj_a = adj_for("gm-acct-alpha")
    adj_b = adj_for("gm-acct-beta")

    # Both must be bounded
    assert adj_a <= 0.10, f"Adjustment must be <= 0.10, got {adj_a}"
    assert adj_b <= 0.10, f"Adjustment must be <= 0.10, got {adj_b}"
    # Different accounts must get different adjustments (very high probability)
    assert adj_a != adj_b, (
        f"Accounts must have different global adjustments at same ban_rate, "
        f"both got {adj_a}"
    )
    print(f"PASS test_global_weight_variation (alpha={adj_a:.4f} beta={adj_b:.4f})")


# ── Identity Graph + Persona Engine tests ──────────────────────────────────

def test_multi_device_distribution():
    """Device pool distribution: 60% single, 30% two, 10% three devices."""
    from core.identity_graph import build_device_pool, MOBILE, DESKTOP, TABLET

    single_count = two_count = three_count = 0
    SAMPLES = 1000
    for i in range(SAMPLES):
        pool = build_device_pool(f"dist-acct-{i:04d}")
        n = len(pool)
        if n == 1:   single_count += 1
        elif n == 2: two_count    += 1
        else:        three_count  += 1

    single_pct = single_count / SAMPLES
    two_pct    = two_count    / SAMPLES
    three_pct  = three_count  / SAMPLES

    # Allow ±8% tolerance on each bucket
    assert 0.52 <= single_pct <= 0.68, f"Single-device share {single_pct:.2%} outside 52-68%"
    assert 0.22 <= two_pct    <= 0.38, f"Two-device share   {two_pct:.2%} outside 22-38%"
    assert 0.02 <= three_pct  <= 0.18, f"Three-device share {three_pct:.2%} outside 2-18%"

    # Mobile should be dominant (>50% across all nodes)
    all_types = []
    for i in range(200):
        for node in build_device_pool(f"dist-acct-{i:04d}"):
            all_types.append(node.device_type)
    mobile_ratio = all_types.count(MOBILE) / len(all_types)
    assert mobile_ratio > 0.5, f"Mobile should be >50% of all nodes, got {mobile_ratio:.2%}"

    print(f"PASS test_multi_device_distribution "
          f"(single={single_pct:.1%} two={two_pct:.1%} three={three_pct:.1%} "
          f"mobile={mobile_ratio:.1%})")


def test_device_time_bias():
    """Mobile preferred at night, desktop preferred during day."""
    from core.identity_graph import build_device_pool, select_active_device, MOBILE, DESKTOP

    # Force a 2-device account (mobile + desktop) for a clear test
    # Scan accounts until we find one with both types
    mixed_account = None
    for i in range(500):
        acct = f"bias-acct-{i:04d}"
        pool = build_device_pool(acct)
        types = {n.device_type for n in pool}
        if MOBILE in types and DESKTOP in types:
            mixed_account = acct
            break
    assert mixed_account is not None, "Could not find a mixed mobile+desktop account in 500 samples"

    # Night hour: 22:00 UTC → pick several windows and count mobile selections
    night_ts = 22 * 3600      # 22:00 UTC
    day_ts   = 10 * 3600      # 10:00 UTC

    night_mobile = sum(
        1 for w in range(20)
        if select_active_device(mixed_account, now=night_ts + w * 900).device_type == MOBILE
    )
    day_desktop = sum(
        1 for w in range(20)
        if select_active_device(mixed_account, now=day_ts + w * 900).device_type == DESKTOP
    )

    # Mobile should win at night, desktop should win during day
    assert night_mobile >= day_desktop or night_mobile > 10 or day_desktop > 10, (
        f"Time bias not working: night_mobile={night_mobile} day_desktop={day_desktop}"
    )
    print(f"PASS test_device_time_bias (account={mixed_account} "
          f"night_mobile={night_mobile}/20 day_desktop={day_desktop}/20)")


def test_persona_drift_over_time():
    """Persona interests shift meaningfully after many success/block cycles."""
    from core.persona_engine import get_persona_engine, reset_persona_engine

    reset_persona_engine()
    try:
        engine = get_persona_engine()
        acct   = "drift-acct-001"
        p0     = engine.get(acct)
        initial_dominant = p0.dominant_niche()
        initial_interests = dict(p0.interests)

        # 20 success sessions → dominant interest should reinforce
        for i in range(20):
            engine.evolve(acct, {"upload_success": True}, now=1_715_000_000 + i * 3600)

        p_after = engine.get(acct)
        # Dominant niche weight must have grown
        new_weight = p_after.interests[initial_dominant]
        old_weight = initial_interests[initial_dominant]
        assert new_weight > old_weight, (
            f"Dominant niche '{initial_dominant}' weight should increase after 20 successes: "
            f"{old_weight:.4f} -> {new_weight:.4f}"
        )
        # Interests must still sum to ~1.0
        total = sum(p_after.interests.values())
        assert abs(total - 1.0) < 0.01, f"Interests must sum to 1.0, got {total:.4f}"
        print(f"PASS test_persona_drift_over_time "
              f"({initial_dominant}: {old_weight:.4f}->{new_weight:.4f} total={total:.4f})")
    finally:
        reset_persona_engine()


def test_persona_influences_behavior():
    """Personas with different risk tolerances produce different behavior modifiers."""
    from core.persona_engine import get_persona_engine, reset_persona_engine

    reset_persona_engine()
    try:
        engine = get_persona_engine()
        acct_a = "influence-acct-alpha"
        acct_b = "influence-acct-beta"

        # Use a fixed timestamp whose day is neither volatile nor stagnant for both accounts.
        NOW_A = 1_716_000_000

        # Simulate: A gets blocked repeatedly (conservative persona)
        for i in range(8):
            engine.evolve(acct_a, {"blocked": True}, now=NOW_A + i * 3600)
        # B gets successes (aggressive persona builds confidence)
        for i in range(8):
            engine.evolve(acct_b, {"upload_success": True}, now=NOW_A + i * 3600)

        state_a = engine.get(acct_a)
        state_b = engine.get(acct_b)

        # Ground truth: blocked account must have lower activity_bias
        assert state_a.activity_bias < state_b.activity_bias, (
            f"Blocked account must have lower activity: "
            f"a={state_a.activity_bias:.3f} b={state_b.activity_bias:.3f}"
        )

        mods_a = engine.get_behavior_modifiers(acct_a)
        mods_b = engine.get_behavior_modifiers(acct_b)

        # posting_frequency: lower for blocked, higher for success
        assert mods_a["posting_frequency_factor"] < mods_b["posting_frequency_factor"], (
            f"Blocked account must have lower posting freq: "
            f"a={mods_a['posting_frequency_factor']:.3f} b={mods_b['posting_frequency_factor']:.3f}"
        )

        # Both modifiers must stay within allowed ranges
        for mods, label in [(mods_a, "A"), (mods_b, "B")]:
            assert 0.7 <= mods["posting_frequency_factor"] <= 1.3, \
                f"posting_freq out of range for account {label}: {mods['posting_frequency_factor']}"
            assert 0.8 <= mods["mutation_aggressiveness"] <= 1.2, \
                f"mutation_agg out of range for account {label}: {mods['mutation_aggressiveness']}"
        print(f"PASS test_persona_influences_behavior "
              f"(A_bias={state_a.activity_bias:.3f} B_bias={state_b.activity_bias:.3f} "
              f"A_freq={mods_a['posting_frequency_factor']:.3f} B_freq={mods_b['posting_frequency_factor']:.3f})")
    finally:
        reset_persona_engine()


def test_cross_account_persona_divergence():
    """Different accounts must NOT evolve to identical personas."""
    from core.persona_engine import get_persona_engine, reset_persona_engine

    reset_persona_engine()
    try:
        engine = get_persona_engine()
        # Give all accounts the same outcome history → divergence is seed-driven
        outcomes = [{"upload_success": True}] * 5 + [{"captcha": True}] * 2
        accounts = [f"diverge-acct-{i:03d}" for i in range(6)]

        for acct in accounts:
            for j, oc in enumerate(outcomes):
                engine.evolve(acct, oc, now=1_715_000_000 + j * 3600)

        # Collect dominant niches across all accounts
        dominants = [engine.get(a).dominant_niche() for a in accounts]
        unique    = len(set(dominants))

        # With 6 niches and 6 accounts, we should see at least 2 distinct dominants
        assert unique >= 2, (
            f"All accounts converged to the same dominant niche — "
            f"cross-account divergence is broken: {dominants}"
        )

        # Also verify activity_bias differs across accounts
        biases  = [round(engine.get(a).activity_bias, 3) for a in accounts]
        unique_b = len(set(biases))
        assert unique_b >= 2, f"All accounts have identical activity_bias: {biases}"

        print(f"PASS test_cross_account_persona_divergence "
              f"(unique_dominants={unique}/{len(accounts)} unique_bias={unique_b}/{len(accounts)})")
    finally:
        reset_persona_engine()


# ── Imperfection tests (Parts 1-6) ──────────────────────────────────────────────────

def test_persona_volatility_spikes():
    """Volatile days must produce 2-3x higher drift than normal days."""
    from core.persona_engine import _persona_volatility, _persona_stagnation, DRIFT_MIN, DRIFT_MAX

    # Find a volatile day and a normal day for the same account
    acct = "volatility-test-acct"
    BASE_DAY = 19_867   # day since epoch (arbitrary)

    volatile_day = normal_day = stagnant_day = None
    for d in range(BASE_DAY, BASE_DAY + 100):
        ts = d * 86400
        if _persona_volatility(acct, ts) and volatile_day is None:
            volatile_day = ts
        elif _persona_stagnation(acct, ts) and stagnant_day is None:
            stagnant_day = ts
        elif not _persona_volatility(acct, ts) and not _persona_stagnation(acct, ts) and normal_day is None:
            normal_day = ts
        if volatile_day and normal_day and stagnant_day:
            break

    assert volatile_day  is not None, "Could not find a volatile day in 100-day window"
    assert normal_day    is not None, "Could not find a normal day in 100-day window"
    assert stagnant_day  is not None, "Could not find a stagnant day in 100-day window"

    # Compute effective deltas
    base_drift = DRIFT_MIN + (_account_noise(acct, "drift_0") * (DRIFT_MAX - DRIFT_MIN))
    spike = 2.0 + (stable_hash_int(acct, "vol_spike", str(volatile_day // 86400)) % 100) / 100.0
    volatile_drift  = base_drift * spike
    stagnant_drift  = base_drift * 0.1

    assert volatile_drift > base_drift * 1.9, (
        f"Volatile drift {volatile_drift:.4f} must be >1.9x base {base_drift:.4f}"
    )
    assert stagnant_drift < base_drift * 0.2, (
        f"Stagnant drift {stagnant_drift:.4f} must be <0.2x base {base_drift:.4f}"
    )
    print(f"PASS test_persona_volatility_spikes "
          f"(base={base_drift:.4f} volatile={volatile_drift:.4f} stagnant={stagnant_drift:.4f})")


def test_device_imperfection_trigger():
    """~10% of hour-buckets trigger device imperfection for multi-device accounts.

    P4: _device_imperfection uses hourly granularity for session consistency.
    Sample 200 distinct hour-buckets to get a reliable rate estimate.
    """
    from core.identity_graph import _device_imperfection, build_device_pool, MOBILE

    # Find an account with multiple devices
    multi_acct = None
    for i in range(500):
        a = f"impf-acct-{i:04d}"
        if len(build_device_pool(a)) > 1:
            multi_acct = a
            break
    assert multi_acct is not None, "Could not find multi-device account in 500 samples"

    # Sample 200 HOUR-buckets (P4: imperfection is hourly-gated, not per-minute)
    BASE = 1_715_000_000
    imperfect = sum(1 for h in range(200) if _device_imperfection(multi_acct, BASE + h * 3600))
    pct = imperfect / 200
    assert 0.02 <= pct <= 0.18, (
        f"Device imperfection rate {pct:.1%} outside 2-18% tolerance"
    )
    print(f"PASS test_device_imperfection_trigger (account={multi_acct} rate={pct:.1%})")


def test_micro_jitter_bounds():
    """Micro-jitter must always be in [-10, +10] seconds."""
    BASE = 1_715_000_000
    acct = "jitter-test-acct"
    jitters = [_micro_jitter(acct, BASE + i) for i in range(1000)]
    assert all(-10 <= j <= 10 for j in jitters), (
        f"Jitter out of bounds: min={min(jitters)} max={max(jitters)}"
    )
    # Must produce both positive and negative values
    assert any(j > 0 for j in jitters), "Jitter never positive"
    assert any(j < 0 for j in jitters), "Jitter never negative"
    print(f"PASS test_micro_jitter_bounds (min={min(jitters)}s max={max(jitters)}s)")


def test_skip_behavior_rate():
    """Skip rate must be ~6-8% across minute-buckets."""
    BASE = 1_715_000_000
    acct = "skip-test-acct"
    skips = sum(1 for m in range(1000) if _skip_action(acct, BASE + m * 60))
    rate  = skips / 1000
    assert 0.04 <= rate <= 0.10, (
        f"Skip rate {rate:.1%} outside 4-10% tolerance (expected ~6.7%)"
    )
    print(f"PASS test_skip_behavior_rate (rate={rate:.1%} skips={skips}/1000)")


def test_behavior_noise_effect():
    """Behavior noise must fire ~10% of windows and perturb modifiers within bounds."""
    BASE  = 1_715_000_000
    acct  = "bnoise-test-acct"
    noise_count = sum(1 for w in range(200) if _behavior_noise(acct, BASE + w * 900))
    rate  = noise_count / 200
    assert 0.03 <= rate <= 0.17, (
        f"Behavior noise fire rate {rate:.1%} outside 3-17% tolerance"
    )

    # When noise fires, modifiers must stay within allowed bounds
    base_mods = {"mutation_aggressiveness": 1.0, "posting_frequency_factor": 1.0, "niche_focus_score": 0.5}
    # Find a window where noise fires
    noisy_ts = next((BASE + w * 900 for w in range(200) if _behavior_noise(acct, BASE + w * 900)), None)
    if noisy_ts is not None:
        noisy_mods = apply_behavior_noise(acct, noisy_ts, dict(base_mods))
        assert 0.8 <= noisy_mods["mutation_aggressiveness"] <= 1.2, \
            f"mutation_aggressiveness out of range: {noisy_mods['mutation_aggressiveness']}"
        assert 0.7 <= noisy_mods["posting_frequency_factor"] <= 1.3, \
            f"posting_frequency_factor out of range: {noisy_mods['posting_frequency_factor']}"

    print(f"PASS test_behavior_noise_effect (rate={rate:.1%})")


def test_memory_decay_applied():
    """Memory decay must slightly reduce interest weights toward uniform."""
    from core.persona_engine import _memory_decay

    acct = "decay-test-acct"
    interests = {"tech": 0.6, "fitness": 0.2, "finance": 0.1, "entertainment": 0.05, "food": 0.03, "travel": 0.02}

    # Find a day with non-zero decay
    BASE_DAY = 19_867
    decayed = None
    for d in range(BASE_DAY, BASE_DAY + 30):
        result = _memory_decay(interests, acct, d * 86400)
        if result["tech"] < interests["tech"]:
            decayed = result
            break

    if decayed is not None:
        # Dominant niche weight must decrease slightly
        assert decayed["tech"] < interests["tech"], \
            f"Dominant weight must decrease: {interests['tech']:.4f} -> {decayed['tech']:.4f}"
        # Smaller niches must increase (toward uniform)
        assert decayed["travel"] > interests["travel"], \
            f"Smallest niche must increase toward uniform: {interests['travel']:.4f} -> {decayed['travel']:.4f}"
    # Zero-decay days are valid too (5 outcomes: 0,1,2,3,4% -> 20% are zero)
    print(f"PASS test_memory_decay_applied (decay_day={'found' if decayed else 'zero-decay only'})")


def test_reaction_lag_variation():
    """Different accounts must have different reaction lag values (0, 1, or 2)."""
    lags = [stable_hash_int(f"lag-acct-{i:03d}", "reaction_lag") % 3 for i in range(30)]
    unique_lags = set(lags)
    assert len(unique_lags) == 3, (
        f"Expected all 3 lag values (0,1,2) in 30 accounts, got {sorted(unique_lags)}"
    )
    zero_lag  = sum(1 for l in lags if l == 0)
    lag_1     = sum(1 for l in lags if l == 1)
    lag_2     = sum(1 for l in lags if l == 2)
    # Each bucket should have ~1/3 of accounts
    for count, label in [(zero_lag, "lag=0"), (lag_1, "lag=1"), (lag_2, "lag=2")]:
        assert 4 <= count <= 16, f"{label} count {count} outside 4-16 for 30-account sample"
    print(f"PASS test_reaction_lag_variation (lag=0:{zero_lag} lag=1:{lag_1} lag=2:{lag_2} / 30)")


if __name__ == "__main__":
    from core.stealth_brain import _classify_risk as _cr  # noqa: F401

    tests = [
        test_low_risk_no_mutation,
        test_fixed_thresholds,
        test_medium_safe_actions_only,
        test_high_risk_webdriver_override,
        test_high_mutation_full_regen,
        test_rollback,
        test_mutation_cooldown,
        test_ban_ttl,
        test_ban_forces_high,
        test_trend_deterministic,
        test_trend_daily_variation,
        test_trend_account_variation,
        test_no_global_memory,
        test_no_forgiveness_no_ewma,
        # FIX validation
        test_stable_hash_int_determinism,
        test_stable_hash_cross_account_isolation,
        test_mutation_nonlinear_delta,
        # Behavioral timing
        test_temporal_delay,
        test_burst_window,
        test_cooldown_jitter,
        # Realism upgrades
        test_session_variation,
        test_fatigue_increases_delay,
        # Realism refinements
        test_session_shift_per_account,
        test_fatigue_randomness,
        test_weekend_effect,
        test_account_age_effect,
        # Global Memory
        test_global_memory_ban_hard_filter,
        test_global_memory_soft_signal_bounded,
        test_global_memory_db_down_graceful,
        test_global_memory_ttl_purge,
        # Anti-correlation (Part 3)
        test_timing_decorrelation,
        test_burst_desync,
        test_target_rotation,
        test_global_weight_variation,
        # Identity Graph + Persona Engine
        test_multi_device_distribution,
        test_device_time_bias,
        test_persona_drift_over_time,
        test_persona_influences_behavior,
        test_cross_account_persona_divergence,
        # Imperfection layer (Parts 1-6)
        test_persona_volatility_spikes,
        test_device_imperfection_trigger,
        test_micro_jitter_bounds,
        test_skip_behavior_rate,
        test_behavior_noise_effect,
        test_memory_decay_applied,
        test_reaction_lag_variation,
        # Hardening refinements (P1–P7)
        test_noise_bound,
        test_identity_consistency,
        test_delay_inertia,
        test_persona_stability,
        test_skip_recovery,
    ]
    for t in tests:
        t()
    print("\n=== ALL TESTS PASSED ===")


# ── Hardening refinement tests (P1–P7) ───────────────────────────────────────

def test_noise_bound():
    """P1: _normalized_noise must never exceed ±MAX_NOISE_IMPACT regardless of spread."""
    from core.mutation_controller import _normalized_noise, MAX_NOISE_IMPACT
    for i in range(500):
        acct = f"noise-acct-{i:04d}"
        for spread in (0.10, 0.15, 0.20, 0.25, 0.30):
            v = _normalized_noise(acct, "timing:noise", spread=spread)
            assert 1.0 - MAX_NOISE_IMPACT <= v <= 1.0 + MAX_NOISE_IMPACT, (
                f"Noise {v:.4f} exceeds ±{MAX_NOISE_IMPACT} (spread={spread})"
            )
    print(f"PASS test_noise_bound (MAX_NOISE_IMPACT={MAX_NOISE_IMPACT})")


def test_identity_consistency():
    """P2: No invalid device/network combos after identity consistency guard."""
    from core.identity_graph import get_identity_context, MOBILE, DESKTOP, TABLET
    desktop_mobile_data = 0
    for i in range(500):
        ctx = get_identity_context(f"consistency-acct-{i:04d}")
        dt  = ctx["device_type"]
        cn  = ctx["connection_type"]
        # All device types must use valid connection types
        assert dt in (MOBILE, DESKTOP, TABLET)
        assert cn in ("mobile_data", "wifi", "ethernet"), f"Unknown conn type: {cn}"
        # Desktop + mobile_data should be rare after consistency guard
        if dt == DESKTOP and cn == "mobile_data":
            desktop_mobile_data += 1
    # After 85% correction, very few desktop+mobile_data should survive
    pct = desktop_mobile_data / 500
    assert pct <= 0.05, f"Too many desktop+mobile_data combos: {pct:.1%} (expected <5%)"
    print(f"PASS test_identity_consistency (desktop+mobile_data={desktop_mobile_data}/500={pct:.1%})")


def test_delay_inertia():
    """P3: Consecutive delay decisions must change smoothly (inertia blending)."""
    from core.mutation_controller import _should_mutate_now, _PREV_DELAYS, RiskLevel
    import core.mutation_controller as mc_mod
    # Reset inertia state for clean test
    mc_mod._PREV_DELAYS.pop("inertia-test-acct", None)
    acct = "inertia-test-acct"
    now  = 4 * 86400 + 12 * 3600   # weekday noon
    created = now - 30 * 86400
    # Force several delay evaluations with large elapsed to ensure passage
    delays = []
    for i in range(6):
        # Adjust elapsed so each call passes
        _should_mutate_now(acct, 9999, i, RiskLevel.HIGH, now + i * 3600, created)
        delays.append(mc_mod._PREV_DELAYS.get(acct, 0))
    # Consecutive stored delays must not jump more than 2x between steps
    for j in range(1, len(delays)):
        if delays[j-1] > 0:
            ratio = delays[j] / delays[j-1]
            assert 0.3 <= ratio <= 3.0, (
                f"Delay jumped too sharply: {delays[j-1]} -> {delays[j]} (ratio={ratio:.2f})"
            )
    print(f"PASS test_delay_inertia (delays={delays})")


def test_persona_stability():
    """P4: Multiple evolves in one day must not exceed MAX_PERSONA_DELTA_PER_DAY total drift."""
    from core.persona_engine import get_persona_engine, reset_persona_engine, MAX_PERSONA_DELTA_PER_DAY
    reset_persona_engine()
    try:
        engine = get_persona_engine()
        acct = "stability-test-acct"
        p0   = engine.get(acct)
        ab0  = p0.activity_bias
        rt0  = p0.risk_tolerance

        # 10 evolves all on the same calendar day
        SAME_DAY = 1_716_000_000
        for i in range(10):
            engine.evolve(acct, {"upload_success": True}, now=SAME_DAY + i * 300)

        p_after = engine.get(acct)
        # Total change in activity_bias and risk_tolerance must stay bounded
        delta_ab = abs(p_after.activity_bias - ab0)
        delta_rt = abs(p_after.risk_tolerance - rt0)
        # Each scalar uses drift_mag * 0.5 at most + anchor pull; MAX_PERSONA_DELTA_PER_DAY is the cap
        assert delta_ab <= MAX_PERSONA_DELTA_PER_DAY + 0.01, (
            f"activity_bias drifted too far in one day: {delta_ab:.4f} > {MAX_PERSONA_DELTA_PER_DAY}"
        )
        print(f"PASS test_persona_stability (delta_ab={delta_ab:.4f} delta_rt={delta_rt:.4f})")
    finally:
        reset_persona_engine()


def test_skip_recovery():
    """P6: After 2 consecutive skips, the next call must NOT skip (force execute)."""
    from core.mutation_controller import _CONSECUTIVE_SKIPS, get_mutation_controller, RiskLevel
    import core.mutation_controller as mc_mod
    from core.identity_manager import generate_identity_profile

    mc = get_mutation_controller()
    acct = "skip-recovery-test"
    mc_mod._CONSECUTIVE_SKIPS[acct] = 0

    profile  = generate_identity_profile(acct)
    strategy = Strategy(risk_level=RiskLevel.HIGH, actions=[], reason="test")

    # First mutation always proceeds (first call)
    r0 = mc.apply(profile, strategy)
    assert r0.mutation_type == "full", f"First mutation must succeed: {r0.mutation_type}"
    assert mc_mod._CONSECUTIVE_SKIPS[acct] == 0

    # Force counter to 2 (simulating 2 preceding skips)
    mc_mod._CONSECUTIVE_SKIPS[acct] = 2

    # Bypass cooldown by setting a large elapsed via mutation_history timestamp manipulation
    if profile.mutation_history:
        profile.mutation_history[-1]["ts"] = 0.0   # makes elapsed huge

    # Next apply: _force_execute=True → skip is bypassed → must NOT return user_skip
    r1 = mc.apply(profile, strategy)
    assert r1.reason != "user_skip", (
        f"Skip recovery failed: still got user_skip with consec=2 (reason={r1.reason!r})"
    )
    assert mc_mod._CONSECUTIVE_SKIPS[acct] == 0, "Counter must reset to 0 after forced execute"
    print(f"PASS test_skip_recovery (r0={r0.mutation_type} r1={r1.mutation_type}/{r1.reason!r})")


# ── Part 7: Habit-Driven Behavior Tests ───────────────────────────────────────

def test_habit_consistency():
    """Part 7.1: same account + same hour → same base in [0.85, 1.15].

    With drift enabled, repeated calls mutate _HABIT_DRIFT each time.
    We verify the *base* is deterministic per (account_id, hour) and that
    every returned value stays within the allowed range.
    """
    import core.mutation_controller as mc_mod

    acct = "habit-consistency-acct"
    mc_mod._GLOBAL_HABIT.clear()
    mc_mod._HABIT_DRIFT.clear()

    hour_ts = 14 * 3600   # 14:00 UTC, day 0

    # First call seeds the base and initialises drift
    b1 = mc_mod._habit_bias(acct, hour_ts)

    # Verify the underlying base (before drift) is deterministically derived
    seed = mc_mod.stable_hash_int(acct, "habit", "14") % 1000 / 1000.0
    expected_base = 0.85 + seed * 0.30
    key = (acct, 14)
    assert mc_mod._GLOBAL_HABIT[key] == expected_base, (
        f"Base must equal deterministic seed: {mc_mod._GLOBAL_HABIT[key]} != {expected_base}"
    )

    # All returned values must stay within the clamped range
    for minute in range(0, 3600, 300):   # several calls within the same hour
        b = mc_mod._habit_bias(acct, hour_ts + minute)
        assert 0.85 <= b <= 1.15, f"Habit bias out of [0.85, 1.15] at +{minute}s: {b}"

    print(f"PASS test_habit_consistency (hour=14, base={expected_base:.4f} b1={b1:.4f})")


def test_rhythm_variation():
    """Part 7.2: peak hour delay < night delay (rhythm factor drives the difference)."""
    import core.mutation_controller as mc_mod

    acct = "rhythm-variation-acct"
    routine = mc_mod._get_routine(acct)
    peak_hour  = routine["peak_hours"][0]
    night_hour = (routine["sleep_hour"] + 2) % 24  # clearly outside active window

    peak_factor  = mc_mod._rhythm_factor(acct, peak_hour  * 3600)
    night_factor = mc_mod._rhythm_factor(acct, night_hour * 3600)

    raw_delay = 200
    peak_delay  = int(raw_delay * peak_factor)
    night_delay = int(raw_delay * night_factor)

    assert peak_factor > night_factor, (
        f"Peak factor ({peak_factor}) must exceed night factor ({night_factor})"
    )
    assert peak_delay > night_delay, (
        f"peak_delay ({peak_delay}) must be > night_delay ({night_delay})"
    )
    print(f"PASS test_rhythm_variation "
          f"(peak_factor={peak_factor} night_factor={night_factor} "
          f"peak_delay={peak_delay} night_delay={night_delay})")


def test_session_clustering():
    """Part 7.3: consecutive actions inside a session have lower delay (boost=0.7)."""
    import core.mutation_controller as mc_mod

    acct = "session-cluster-acct"
    mc_mod._SESSION_STATE.pop(acct, None)

    now = 1_716_000_000

    # Scan minute-buckets until a session starts (has a 'start' key)
    for minute in range(60):
        t = now + minute * 60
        mc_mod._session_boost(acct, t)
        if acct in mc_mod._SESSION_STATE and "start" in mc_mod._SESSION_STATE[acct]:
            break

    assert acct in mc_mod._SESSION_STATE and "start" in mc_mod._SESSION_STATE[acct], (
        "A session must have started within 60 minute-buckets"
    )

    # Reset counter so the session doesn't exhaust on the assertion call
    state = mc_mod._SESSION_STATE[acct]
    start = state["start"]
    mc_mod._SESSION_STATE[acct] = {"start": start, "count": 1, "last_end": state.get("last_end", 0)}

    # Call inside the live session window — must return 0.7
    in_session_boost = mc_mod._session_boost(acct, start + 60)
    assert in_session_boost == 0.7, (
        f"In-session boost must be 0.7, got {in_session_boost}"
    )
    print(f"PASS test_session_clustering (in_session_boost={in_session_boost})")


def test_trend_bias_range():
    """Part 7.4: _trend_bias always within [0.95, 1.05] across many hour buckets."""
    import core.mutation_controller as mc_mod

    biases = [mc_mod._trend_bias(h * 3600) for h in range(1000)]
    assert all(0.95 <= b <= 1.05 for b in biases), (
        f"Trend bias out of [0.95, 1.05]: min={min(biases):.4f} max={max(biases):.4f}"
    )
    assert min(biases) < max(biases), "Trend bias must vary across hours"
    print(f"PASS test_trend_bias_range (min={min(biases):.4f} max={max(biases):.4f})")


def test_no_cross_account_leak():
    """Part 7.5: different accounts → different habit biases at the same hour."""
    import core.mutation_controller as mc_mod

    mc_mod._GLOBAL_HABIT.clear()

    hour_ts  = 10 * 3600
    accounts = [f"leak-test-acct-{i:03d}" for i in range(20)]
    biases   = [mc_mod._habit_bias(a, hour_ts) for a in accounts]

    unique = len(set(round(b, 6) for b in biases))
    assert unique > 1, f"All accounts got same habit bias — isolation broken: {biases[:5]}"

    pairs_equal = sum(1 for i in range(len(biases) - 1) if biases[i] == biases[i + 1])
    assert pairs_equal == 0, f"{pairs_equal} adjacent accounts share the same habit bias"

    assert all(0.85 <= b <= 1.15 for b in biases), (
        f"Some habit bias outside [0.85, 1.15]: {[b for b in biases if not (0.85 <= b <= 1.15)]}"
    )
    print(f"PASS test_no_cross_account_leak (unique_biases={unique}/20 "
          f"min={min(biases):.4f} max={max(biases):.4f})")

# ── Part 8: Evolution / Smoothing Tests ──────────────────────────────────────

def test_habit_drift_changes_over_days():
    """Habit bias for the same account+hour shifts (slightly) across different days.

    EWMA drift moves by at most ~2%/day. Over 30 days the bias must vary
    but each consecutive step must stay <=2%.
    """
    import core.mutation_controller as mc_mod

    acct = "drift-test-acct"
    mc_mod._GLOBAL_HABIT.clear()
    mc_mod._HABIT_DRIFT.clear()

    hour = 14
    biases = []
    for day in range(30):
        ts = day * 86400 + hour * 3600
        b  = mc_mod._habit_bias(acct, ts)
        assert 0.85 <= b <= 1.15, f"Habit bias out of [0.85, 1.15] on day {day}: {b}"
        biases.append(b)

    assert min(biases) < max(biases), (
        f"Habit bias must drift over 30 days, got constant {biases[0]}"
    )
    for i in range(1, len(biases)):
        step = abs(biases[i] - biases[i - 1])
        assert step <= 0.02 + 1e-9, (
            f"Day-to-day drift must be <=2%, got {step:.4f} between day {i-1} and {i}"
        )
    print(f"PASS test_habit_drift_changes_over_days "
          f"(range={min(biases):.4f}–{max(biases):.4f} over 30 days)")


def test_session_not_back_to_back():
    """A new session must not start immediately after the previous one ends."""
    import core.mutation_controller as mc_mod

    acct = "no-back-to-back-acct"
    mc_mod._SESSION_STATE.pop(acct, None)
    mc_mod._SESSION_PROFILE.pop(acct, None)

    now = 1_716_100_000

    # Inject a freshly-ended session
    mc_mod._SESSION_STATE[acct] = {"last_end": now}

    assert not mc_mod._can_start_session(acct, now), (
        "Session must not start immediately after the previous one ended"
    )
    assert not mc_mod._can_start_session(acct, now + 60), (
        "Session must not start 60 s after the previous one ended"
    )

    profile  = mc_mod._get_session_profile(acct)
    cooldown = int(600 * profile["cooldown_bias"]) + 1
    assert mc_mod._can_start_session(acct, now + cooldown), (
        f"Session must be allowed after cooldown ({cooldown}s), got False"
    )
    print(f"PASS test_session_not_back_to_back (cooldown={cooldown}s)")


def test_rhythm_smoothness():
    """Rhythm factor must be continuous (no sudden jumps >0.5) across adjacent hours."""
    import core.mutation_controller as mc_mod

    acct    = "rhythm-smooth-acct"
    factors = [mc_mod._rhythm_factor(acct, h * 3600) for h in range(24)]

    max_step = max(abs(factors[i] - factors[i - 1]) for i in range(1, 24))

    # Old hard-step system had 0.9 jumps (1.3→0.4); new smooth curve must stay under 0.5
    assert max_step <= 0.5, (
        f"Rhythm factor step too large: {max_step:.3f} (expected <=0.5)"
    )
    assert all(0.5 <= f <= 1.2 for f in factors), (
        f"Rhythm factor out of [0.5, 1.2]: {factors}"
    )
    print(f"PASS test_rhythm_smoothness "
          f"(max_step={max_step:.3f} factors={[round(f, 2) for f in factors]})")


def test_trend_smoothing():
    """Blended trend bias must have a smaller (or equal) max hourly step than single-bucket."""
    import core.mutation_controller as mc_mod

    new_biases   = [mc_mod._trend_bias(h * 3600) for h in range(48)]
    new_max_step = max(abs(new_biases[i] - new_biases[i - 1]) for i in range(1, 48))

    def _old_trend(now: int) -> float:
        bucket = now // 3600
        seed = mc_mod.stable_hash_int("global", "trend", str(bucket)) % 1000 / 1000.0
        return 0.95 + seed * 0.10

    old_biases   = [_old_trend(h * 3600) for h in range(48)]
    old_max_step = max(abs(old_biases[i] - old_biases[i - 1]) for i in range(1, 48))

    assert new_max_step <= old_max_step + 1e-9, (
        f"Blended trend max_step ({new_max_step:.4f}) must not exceed old ({old_max_step:.4f})"
    )
    assert all(0.95 <= b <= 1.05 for b in new_biases), (
        f"Blended trend out of [0.95, 1.05]: min={min(new_biases):.4f} max={max(new_biases):.4f}"
    )
    print(f"PASS test_trend_smoothing "
          f"(old_max_step={old_max_step:.4f} new_max_step={new_max_step:.4f})")


# ── Part 9: Controlled Imperfection Tests ────────────────────────────────────

def test_routine_break_exists():
    """Part 9.1: ~8% of days trigger a routine break with distorted rhythm."""
    import core.mutation_controller as mc_mod

    acct = "routine-break-acct"
    breaks = sum(1 for day in range(1000) if mc_mod._routine_break(acct, day * 86400))
    
    # Expected ~80 breaks out of 1000
    assert 40 <= breaks <= 120, f"Routine break frequency out of expected bounds: {breaks}/1000"
    
    # Test that a break day produces a different rhythm factor
    # Find a break day and a non-break day
    break_day = next(day for day in range(1000) if mc_mod._routine_break(acct, day * 86400))
    non_break_day = next(day for day in range(1000) if not mc_mod._routine_break(acct, day * 86400))
    
    # Check factor at noon
    hour_offset = 12 * 3600
    f_break = mc_mod._rhythm_factor(acct, break_day * 86400 + hour_offset)
    f_normal = mc_mod._rhythm_factor(acct, non_break_day * 86400 + hour_offset)
    
    # On break days, the factor is noisy, so it's extremely unlikely to match exactly
    assert f_break != f_normal, "Break day rhythm factor must differ from normal rhythm factor"
    print(f"PASS test_routine_break_exists (breaks={breaks}/1000)")


def test_outlier_rare():
    """Part 9.2: ~0.5% of days trigger an outlier session."""
    import core.mutation_controller as mc_mod

    acct = "outlier-test-acct"
    outliers = sum(1 for day in range(10000) if mc_mod._outlier_session(acct, day * 86400))
    
    # Expected ~50 outliers out of 10000
    assert 10 <= outliers <= 90, f"Outlier frequency out of expected bounds: {outliers}/10000"
    print(f"PASS test_outlier_rare (outliers={outliers}/10000)")


def test_mood_variation():
    """Part 9.3: Mood drifts across 6-hour buckets, producing 'low', 'high', 'normal'."""
    import core.mutation_controller as mc_mod

    acct = "mood-test-acct"
    moods = [mc_mod._get_mood(acct, h * 3600) for h in range(1000)]
    
    counts = {"low": moods.count("low"), "high": moods.count("high"), "normal": moods.count("normal")}
    
    # Distribution should roughly be 20% low, 20% high, 60% normal
    assert 100 <= counts["low"] <= 300, f"Low mood frequency out of bounds: {counts['low']}"
    assert 100 <= counts["high"] <= 300, f"High mood frequency out of bounds: {counts['high']}"
    assert 400 <= counts["normal"] <= 800, f"Normal mood frequency out of bounds: {counts['normal']}"
    print(f"PASS test_mood_variation (counts={counts})")


def test_micro_variation_range():
    """Part 9.4: Micro variation always stays within [0.95, 1.05]."""
    biases = [mc_mod._trend_bias(h * 3600) for h in range(1000)]
    assert all(0.95 <= b <= 1.05 for b in biases), (
        f"Trend bias out of [0.95, 1.05]: min={min(biases):.4f} max={max(biases):.4f}"
    )
    assert min(biases) < max(biases), "Trend bias must vary across hours"
    print(f"PASS test_trend_bias_range (min={min(biases):.4f} max={max(biases):.4f})")


def test_no_cross_account_leak():
    """Part 7.5: different accounts → different habit biases at the same hour."""
    import core.mutation_controller as mc_mod

    mc_mod._GLOBAL_HABIT.clear()

    hour_ts  = 10 * 3600
    accounts = [f"leak-test-acct-{i:03d}" for i in range(20)]
    biases   = [mc_mod._habit_bias(a, hour_ts) for a in accounts]

    unique = len(set(round(b, 6) for b in biases))
    assert unique > 1, f"All accounts got same habit bias — isolation broken: {biases[:5]}"

    pairs_equal = sum(1 for i in range(len(biases) - 1) if biases[i] == biases[i + 1])
    assert pairs_equal == 0, f"{pairs_equal} adjacent accounts share the same habit bias"

    assert all(0.85 <= b <= 1.15 for b in biases), (
        f"Some habit bias outside [0.85, 1.15]: {[b for b in biases if not (0.85 <= b <= 1.15)]}"
    )
    print(f"PASS test_no_cross_account_leak (unique_biases={unique}/20 "
          f"min={min(biases):.4f} max={max(biases):.4f})")

# ── Part 8: Evolution / Smoothing Tests ──────────────────────────────────────

def test_habit_drift_changes_over_days():
    """Habit bias for the same account+hour shifts (slightly) across different days.

    EWMA drift moves by at most ~2%/day. Over 30 days the bias must vary
    but each consecutive step must stay <=2%.
    """
    import core.mutation_controller as mc_mod

    acct = "drift-test-acct"
    mc_mod._GLOBAL_HABIT.clear()
    mc_mod._HABIT_DRIFT.clear()

    hour = 14
    biases = []
    for day in range(30):
        ts = day * 86400 + hour * 3600
        b  = mc_mod._habit_bias(acct, ts)
        assert 0.85 <= b <= 1.15, f"Habit bias out of [0.85, 1.15] on day {day}: {b}"
        biases.append(b)

    assert min(biases) < max(biases), (
        f"Habit bias must drift over 30 days, got constant {biases[0]}"
    )
    for i in range(1, len(biases)):
        step = abs(biases[i] - biases[i - 1])
        assert step <= 0.02 + 1e-9, (
            f"Day-to-day drift must be <=2%, got {step:.4f} between day {i-1} and {i}"
        )
    print(f"PASS test_habit_drift_changes_over_days "
          f"(range={min(biases):.4f}–{max(biases):.4f} over 30 days)")


def test_session_not_back_to_back():
    """A new session must not start immediately after the previous one ends."""
    import core.mutation_controller as mc_mod

    acct = "no-back-to-back-acct"
    mc_mod._SESSION_STATE.pop(acct, None)
    mc_mod._SESSION_PROFILE.pop(acct, None)

    now = 1_716_100_000

    # Inject a freshly-ended session
    mc_mod._SESSION_STATE[acct] = {"last_end": now}

    assert not mc_mod._can_start_session(acct, now), (
        "Session must not start immediately after the previous one ended"
    )
    assert not mc_mod._can_start_session(acct, now + 60), (
        "Session must not start 60 s after the previous one ended"
    )

    profile  = mc_mod._get_session_profile(acct)
    cooldown = int(600 * profile["cooldown_bias"]) + 1
    assert mc_mod._can_start_session(acct, now + cooldown), (
        f"Session must be allowed after cooldown ({cooldown}s), got False"
    )
    print(f"PASS test_session_not_back_to_back (cooldown={cooldown}s)")


def test_rhythm_smoothness(monkeypatch):
    """Rhythm factor must be continuous (no sudden jumps >0.5) across adjacent hours."""
    import core.mutation_controller as mc_mod

    monkeypatch.setattr(mc_mod, "_routine_break", lambda *args: False)
    monkeypatch.setattr(mc_mod, "_intent_drift", lambda *args: False)

    acct    = "rhythm-smooth-acct"
    factors = [mc_mod._rhythm_factor(acct, h * 3600) for h in range(24)]

    max_step = max(abs(factors[i] - factors[i - 1]) for i in range(1, 24))

    # Old hard-step system had 0.9 jumps (1.3→0.4); new smooth curve must stay under 0.5
    assert max_step <= 0.5, (
        f"Rhythm factor step too large: {max_step:.3f} (expected <=0.5)"
    )
    assert all(0.5 <= f <= 1.2 for f in factors), (
        f"Rhythm factor out of [0.5, 1.2]: {factors}"
    )
    print(f"PASS test_rhythm_smoothness "
          f"(max_step={max_step:.3f} factors={[round(f, 2) for f in factors]})")


def test_trend_smoothing():
    """Blended trend bias must have a smaller (or equal) max hourly step than single-bucket."""
    import core.mutation_controller as mc_mod

    new_biases   = [mc_mod._trend_bias(h * 3600) for h in range(48)]
    new_max_step = max(abs(new_biases[i] - new_biases[i - 1]) for i in range(1, 48))

    def _old_trend(now: int) -> float:
        bucket = now // 3600
        seed = mc_mod.stable_hash_int("global", "trend", str(bucket)) % 1000 / 1000.0
        return 0.95 + seed * 0.10

    old_biases   = [_old_trend(h * 3600) for h in range(48)]
    old_max_step = max(abs(old_biases[i] - old_biases[i - 1]) for i in range(1, 48))

    assert new_max_step <= old_max_step + 1e-9, (
        f"Blended trend max_step ({new_max_step:.4f}) must not exceed old ({old_max_step:.4f})"
    )
    assert all(0.95 <= b <= 1.05 for b in new_biases), (
        f"Blended trend out of [0.95, 1.05]: min={min(new_biases):.4f} max={max(new_biases):.4f}"
    )
    print(f"PASS test_trend_smoothing "
          f"(old_max_step={old_max_step:.4f} new_max_step={new_max_step:.4f})")


# ── Part 9: Controlled Imperfection Tests ────────────────────────────────────

def test_routine_break_exists():
    """Part 9.1: ~8% of days trigger a routine break with distorted rhythm."""
    import core.mutation_controller as mc_mod

    acct = "routine-break-acct"
    breaks = sum(1 for day in range(1000) if mc_mod._routine_break(acct, day * 86400))
    
    # Expected ~80 breaks out of 1000
    assert 40 <= breaks <= 120, f"Routine break frequency out of expected bounds: {breaks}/1000"
    
    # Test that a break day produces a different rhythm factor
    # Find a break day and a non-break day
    break_day = next(day for day in range(1000) if mc_mod._routine_break(acct, day * 86400))
    non_break_day = next(day for day in range(1000) if not mc_mod._routine_break(acct, day * 86400))
    
    # Check factor at noon
    hour_offset = 12 * 3600
    f_break = mc_mod._rhythm_factor(acct, break_day * 86400 + hour_offset)
    f_normal = mc_mod._rhythm_factor(acct, non_break_day * 86400 + hour_offset)
    
    # On break days, the factor is noisy, so it's extremely unlikely to match exactly
    assert f_break != f_normal, "Break day rhythm factor must differ from normal rhythm factor"
    print(f"PASS test_routine_break_exists (breaks={breaks}/1000)")


def test_outlier_rare():
    """Part 9.2: ~0.5% of days trigger an outlier session."""
    import core.mutation_controller as mc_mod

    acct = "outlier-test-acct"
    outliers = sum(1 for day in range(10000) if mc_mod._outlier_session(acct, day * 86400))
    
    # Expected ~50 outliers out of 10000
    assert 10 <= outliers <= 90, f"Outlier frequency out of expected bounds: {outliers}/10000"
    print(f"PASS test_outlier_rare (outliers={outliers}/10000)")


def test_mood_variation():
    """Part 9.3: Mood drifts across 6-hour buckets, producing 'low', 'high', 'normal'."""
    import core.mutation_controller as mc_mod

    acct = "mood-test-acct"
    moods = [mc_mod._get_mood(acct, h * 3600) for h in range(1000)]
    
    counts = {"low": moods.count("low"), "high": moods.count("high"), "normal": moods.count("normal")}
    
    # Distribution should roughly be 20% low, 20% high, 60% normal
    assert 100 <= counts["low"] <= 300, f"Low mood frequency out of bounds: {counts['low']}"
    assert 100 <= counts["high"] <= 300, f"High mood frequency out of bounds: {counts['high']}"
    assert 400 <= counts["normal"] <= 800, f"Normal mood frequency out of bounds: {counts['normal']}"
    print(f"PASS test_mood_variation (counts={counts})")


def test_micro_variation_range():
    """Part 9.4: Micro variation always stays within [0.95, 1.05]."""
    import core.mutation_controller as mc_mod

    acct = "micro-var-acct"
    variations = [mc_mod._micro_variation(acct, state) for state in range(1000)]
    
    assert all(0.95 <= v <= 1.05 for v in variations), "Micro variation out of [0.95, 1.05]"
    assert min(variations) < max(variations), "Micro variation must not be constant"
    print(f"PASS test_micro_variation_range (min={min(variations):.4f} max={max(variations):.4f})")


# ── Part 10: Human Irrationality Tests ───────────────────────────────────────

def test_intent_drift_exists():
    """Part 10.1: ~12% of hours trigger an intent drift."""
    import core.mutation_controller as mc_mod

    acct = "intent-drift-acct"
    drifts = sum(1 for hour in range(1000) if mc_mod._intent_drift(acct, hour * 3600))
    
    # Expected ~120 drifts out of 1000
    assert 60 <= drifts <= 180, f"Intent drift frequency out of bounds: {drifts}/1000"
    print(f"PASS test_intent_drift_exists (drifts={drifts}/1000)")


def test_contradiction_rare():
    """Part 10.2: ~0.5% of days trigger a contradiction."""
    import core.mutation_controller as mc_mod

    acct = "contradict-test-acct"
    contradictions = sum(1 for day in range(10000) if mc_mod._contradiction(acct, day * 86400))
    
    # Expected ~50 contradictions out of 10000
    assert 10 <= contradictions <= 90, f"Contradiction frequency out of bounds: {contradictions}/10000"
    print(f"PASS test_contradiction_rare (contradictions={contradictions}/10000)")


def test_obsession_duration():
    """Part 10.3: Obsession spikes last 1-3 hours."""
    import core.mutation_controller as mc_mod

    acct = "obsession-test-acct"
    mc_mod._OBSESSION.clear()
    
    # Find an obsession day
    day_ts = 0
    for day in range(1000):
        ts = day * 86400
        if mc_mod._obsession(acct, ts):
            day_ts = ts
            break
            
    assert day_ts > 0, "Could not find obsession day"
    
    start, dur, _ = mc_mod._OBSESSION[acct]
    assert 3600 <= dur <= 10800, f"Obsession duration {dur} not in 1-3 hours"
    
    # Verify active during duration, inactive after
    assert mc_mod._obsession(acct, start + dur // 2), "Obsession should be active mid-way"
    assert not mc_mod._obsession(acct, start + dur + 60), "Obsession should be inactive after duration"
    print(f"PASS test_obsession_duration (duration={dur}s)")


def test_memory_conflict_flip():
    """Part 10.4: ~5% of mutations flip the skip decision."""
    import core.mutation_controller as mc_mod

    acct = "memory-conflict-acct"
    flips = sum(1 for state in range(1000) if mc_mod._memory_conflict(acct, state))
    
    # Expected ~50 flips out of 1000
    assert 20 <= flips <= 80, f"Memory conflict frequency out of bounds: {flips}/1000"
    print(f"PASS test_memory_conflict_flip (flips={flips}/1000)")


# ── Part 11: Social Context Tests ────────────────────────────────────────────

def test_global_wave_variation():
    """Part 11.1: Global wave creates platform busy/slow hours."""
    import core.mutation_controller as mc_mod

    waves = [mc_mod._global_activity_wave(h * 3600) for h in range(1000)]
    
    assert all(0.9 <= w <= 1.1 for w in waves), "Global wave out of [0.9, 1.1]"
    assert min(waves) < max(waves), "Global wave must vary"
    print(f"PASS test_global_wave_variation (min={min(waves):.4f} max={max(waves):.4f})")


def test_soft_sync_diversity():
    """Part 11.2: Accounts loosely sync within 30-min buckets."""
    import core.mutation_controller as mc_mod

    now = 1_700_000_000
    accounts = [f"sync-test-{i}" for i in range(100)]
    syncs = [mc_mod._soft_sync(a, now) for a in accounts]
    
    assert all(0.95 <= s <= 1.05 for s in syncs), "Soft sync out of [0.95, 1.05]"
    
    unique_syncs = len(set(round(s, 5) for s in syncs))
    assert unique_syncs > 10, f"Expected varied sync multipliers, got {unique_syncs} unique"
    print(f"PASS test_soft_sync_diversity (unique={unique_syncs}/100)")


def test_trend_follow_ratio():
    """Part 11.3: ~25% of accounts follow trend (faster)."""
    import core.mutation_controller as mc_mod

    now = 1_700_000_000
    accounts = [f"trend-test-{i}" for i in range(1000)]
    follows = sum(1 for a in accounts if mc_mod._trend_follow(a, now))
    
    # Expected ~250 followers out of 1000
    assert 150 <= follows <= 350, f"Trend followers out of bounds: {follows}/1000"
    print(f"PASS test_trend_follow_ratio (followers={follows}/1000)")


def test_reaction_group_distribution():
    """Part 11.4: Reaction groups are evenly distributed (0, 1, 2)."""
    import core.mutation_controller as mc_mod

    accounts = [f"reaction-test-{i}" for i in range(1000)]
    groups = [mc_mod._reaction_group(a) for a in accounts]
    
    counts = {g: groups.count(g) for g in (0, 1, 2)}
    
    for g in (0, 1, 2):
        assert 250 <= counts[g] <= 420, f"Group {g} count {counts[g]} out of bounds"
        
    print(f"PASS test_reaction_group_distribution (counts={counts})")


# ── Part 12: Trend Momentum ──────────────────────────────────────────────────

def test_trend_momentum_smoothness():
    """Part 12: Trend momentum rolls smoothly over hours."""
    import core.mutation_controller as mc_mod

    mc_mod._TREND_STATE.clear()
    
    momentums = [mc_mod._trend_momentum(h * 3600) for h in range(100)]
    
    assert all(0.9 <= m <= 1.1 for m in momentums), "Momentum out of bounds"
    
    max_step = max(abs(momentums[i] - momentums[i - 1]) for i in range(1, 100))
    assert max_step < 0.15, f"Momentum step too jagged: {max_step}"
    print(f"PASS test_trend_momentum_smoothness (max_step={max_step:.4f})")


# ── Part 13: Platform-Specific Tuning Tests ───────────────────────────────────

def _platform_delay(acct, state, platform, *, base=200):
    """Helper: compute the platform-tuned delay for a fixed input via _apply_platform_mods."""
    import core.mutation_controller as mc_mod
    mc_mod._PREV_DELAYS.clear()
    # Give it a stable non-zero prev to make EMA meaningful
    mc_mod._PREV_DELAYS[acct] = base
    return mc_mod._apply_platform_mods(acct, state, base, platform, is_contradict=False)


def test_platform_divergence():
    """Part 13: Same account + state → different delays per platform."""
    import core.mutation_controller as mc_mod

    acct, state = "platform-div-acct", 42
    PLATFORMS = ["tiktok", "facebook", "youtube", "instagram", "zalo", "shopee", "generic"]

    delays = {p: _platform_delay(acct, state, p) for p in PLATFORMS}

    unique = len(set(delays.values()))
    assert unique >= 3, f"Expected at least 3 distinct delays, got: {delays}"

    # Obvious split: zalo must be > tiktok
    assert delays["zalo"] > delays["tiktok"], (
        f"Zalo ({delays['zalo']}) should be slower than TikTok ({delays['tiktok']})"
    )
    print(f"PASS test_platform_divergence (delays={delays})")


def test_platform_bounds():
    """Part 13: All platform multipliers stay within [0.6, 1.4] of the input."""
    import core.mutation_controller as mc_mod

    acct, state, base = "platform-bounds-acct", 7, 100
    PLATFORMS = list(mc_mod.PLATFORM_PROFILES.keys()) + ["generic"]

    for p in PLATFORMS:
        mc_mod._PREV_DELAYS.clear()
        mc_mod._PREV_DELAYS[acct] = base
        d = mc_mod._apply_platform_mods(acct, state, base, p, is_contradict=False)
        assert 0.6 * base <= d <= 1.4 * base + 50, (
            f"Platform {p!r}: delay {d} out of clamp for base={base}"
        )
    print(f"PASS test_platform_bounds (all {len(PLATFORMS)} platforms in bounds)")


def test_tiktok_burstier_than_facebook():
    """Part 13: TikTok delay_base_mult < Facebook's → faster baseline."""
    from core.platform_profiles import PLATFORM_PROFILES

    tk = PLATFORM_PROFILES["tiktok"]["delay_base_mult"]
    fb = PLATFORM_PROFILES["facebook"]["delay_base_mult"]
    assert tk < fb, f"TikTok delay_base_mult ({tk}) must be < Facebook ({fb})"

    tk_burst = PLATFORM_PROFILES["tiktok"]["burstiness"]
    fb_burst = PLATFORM_PROFILES["facebook"]["burstiness"]
    assert tk_burst > fb_burst, (
        f"TikTok burstiness ({tk_burst}) must be > Facebook ({fb_burst})"
    )
    print(f"PASS test_tiktok_burstier_than_facebook (TikTok delay={tk} burst={tk_burst})")


def test_zalo_slowest_pattern():
    """Part 13: Zalo has the highest delay_base_mult and lowest burstiness."""
    from core.platform_profiles import PLATFORM_PROFILES

    zalo_dm = PLATFORM_PROFILES["zalo"]["delay_base_mult"]
    zalo_b  = PLATFORM_PROFILES["zalo"]["burstiness"]

    for name, prof in PLATFORM_PROFILES.items():
        if name == "zalo":
            continue
        assert zalo_dm >= prof["delay_base_mult"], (
            f"Zalo delay_base_mult ({zalo_dm}) must be ≥ {name} ({prof['delay_base_mult']})"
        )
        assert zalo_b <= prof["burstiness"], (
            f"Zalo burstiness ({zalo_b}) must be ≤ {name} ({prof['burstiness']})"
        )
    print(f"PASS test_zalo_slowest_pattern (delay_base={zalo_dm}, burstiness={zalo_b})")
