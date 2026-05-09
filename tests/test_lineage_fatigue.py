"""
tests/test_lineage_fatigue.py
Unit tests for LINEAGE FATIGUE behaviour in get_ancestry_pressure().

Validates:
  1. No fatigue for gen ≤ 6                   (young lineage)
  2. Fatigue increases with generation         (monotone ramp)
  3. mutation_scale recovers for old lineage   (gen 16)
  4. stability_boost reduces for old lineage   (gen 16)
"""
import importlib.util, sys, types, pathlib

# ---------------------------------------------------------------------------
# Stub the full dependency chain so swarm_dynamics loads in isolation.
# core/__init__.py → workflow_manager → database → aiosqlite (missing in test env).
# ---------------------------------------------------------------------------
def _stub(name: str, attrs: dict | None = None) -> types.ModuleType:
    m = types.ModuleType(name)
    for k, v in (attrs or {}).items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m

# Provide stable_hash_int as a simple deterministic hash so the module loads
def _stable_hash_int(*args: str) -> int:
    import hashlib
    return int(hashlib.sha256("".join(args).encode()).hexdigest(), 16)

_stub("core", {})
_stub("core.mutation_controller", {"stable_hash_int": _stable_hash_int})

import pytest

# ---------------------------------------------------------------------------
# Load swarm_dynamics directly (bypassing core/__init__.py)
# ---------------------------------------------------------------------------
_root = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "swarm_dynamics",
    _root / "core" / "swarm_dynamics.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["swarm_dynamics"] = _mod
_spec.loader.exec_module(_mod)

_CLUSTER_LINEAGE      = _mod._CLUSTER_LINEAGE
_init_lineage         = _mod._init_lineage
get_ancestry_pressure = _mod.get_ancestry_pressure
reset_swarm           = _mod.reset_swarm


def _make_lineage(gen: int, fit: float = 0.8, age: int = 20) -> dict:
    return {
        "parent":          None,
        "generation":      gen,
        "lineage_id":      "test",
        "fitness_ema":     fit,
        "survival_cycles": age,
    }


def _pressure(gen: int, fit: float = 0.8, age: int = 20) -> dict:
    """Inject a synthetic lineage record and query pressure."""
    reset_swarm()
    _CLUSTER_LINEAGE["test_cid"] = _make_lineage(gen, fit, age)
    return get_ancestry_pressure("test_cid")


# ---------------------------------------------------------------------------
# Test 1 — No fatigue for young generations (gen ≤ 6)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("gen", [0, 1, 3, 6])
def test_no_fatigue_young_generation(gen):
    """
    For gen ≤ 6 fatigue=0, so stability and mutation should match the
    base formula exactly (no fatigue modification applied).
    """
    p = _pressure(gen, fit=0.8, age=20)

    # Base formula (no fatigue)
    gen_factor = min(gen / 10.0, 1.0)
    age_factor = 1.0  # age=20 => min(20/20,1)=1
    expected_stability_base = 1.0 + 0.10 * gen_factor * age_factor
    expected_mutation_base  = 1.0 - 0.30 * gen_factor

    # Conditional saturation: only fires when |deviation| > 0.2
    # For gen<=6, deviation = 0.10 * gen_factor * age_factor <= 0.06 → no saturation
    expected_stability_raw = 1.0 + 0.10 * gen_factor * age_factor
    deviation              = expected_stability_raw - 1.0
    if abs(deviation) > 0.2:
        expected_stability = 1.0 + deviation * 0.90   # healthy system, not top → 0.90
    else:
        expected_stability = expected_stability_raw    # below threshold → unchanged
    expected_stability = round(max(0.50, min(1.50, expected_stability)), 6)

    assert abs(p["stability_boost"] - expected_stability) < 1e-4, (
        f"gen={gen}: stability_boost {p['stability_boost']} != expected {expected_stability:.6f}"
    )
    assert abs(p["mutation_scale"] - max(0.50, min(2.00, expected_mutation_base))) < 1e-4, (
        f"gen={gen}: mutation_scale {p['mutation_scale']} != {expected_mutation_base:.6f}"
    )


# ---------------------------------------------------------------------------
# Test 2 — Fatigue increases with generation (monotone ramp)
# ---------------------------------------------------------------------------

def test_fatigue_increases_with_generation():
    """
    stability_boost should decrease and mutation_scale should increase
    monotonically as generation rises past 6 (holding other vars fixed).
    """
    fit, age = 0.8, 20
    generations = [6, 8, 10, 12, 16]
    pressures = [_pressure(g, fit, age) for g in generations]

    for i in range(len(pressures) - 1):
        g_now, g_next = generations[i], generations[i + 1]
        sb_now, sb_next = pressures[i]["stability_boost"], pressures[i + 1]["stability_boost"]
        ms_now, ms_next = pressures[i]["mutation_scale"],  pressures[i + 1]["mutation_scale"]

        assert sb_next <= sb_now, (
            f"stability_boost should not increase from gen={g_now} to gen={g_next}: "
            f"{sb_now} -> {sb_next}"
        )
        assert ms_next >= ms_now, (
            f"mutation_scale should not decrease from gen={g_now} to gen={g_next}: "
            f"{ms_now} -> {ms_next}"
        )


# ---------------------------------------------------------------------------
# Test 3 — mutation_scale recovers for old lineage
# ---------------------------------------------------------------------------

def test_mutation_scale_recovers_for_old_lineage():
    """
    At gen=16 (full fatigue=1.0), mutation_scale should be higher than at gen=6.
    This validates that fatigue re-opens exploratory pressure.
    """
    p_young = _pressure(gen=6,  fit=0.8, age=20)
    p_old   = _pressure(gen=16, fit=0.8, age=20)

    assert p_old["mutation_scale"] > p_young["mutation_scale"], (
        f"Old lineage mutation_scale {p_old['mutation_scale']} should exceed "
        f"young {p_young['mutation_scale']}"
    )


# ---------------------------------------------------------------------------
# Test 4 — stability_boost reduces for old lineage
# ---------------------------------------------------------------------------

def test_stability_reduces_for_old_lineage():
    """
    At gen=16 (full fatigue), stability_boost should be strictly lower than
    at gen=6 (no fatigue), confirming dominance erosion.
    """
    p_young = _pressure(gen=6,  fit=0.8, age=20)
    p_old   = _pressure(gen=16, fit=0.8, age=20)

    assert p_old["stability_boost"] < p_young["stability_boost"], (
        f"Old lineage stability_boost {p_old['stability_boost']} should be lower than "
        f"young {p_young['stability_boost']}"
    )


# ---------------------------------------------------------------------------
# Bonus — bounds are always respected
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("gen", [0, 6, 10, 16, 50])
def test_output_bounds(gen):
    p = _pressure(gen, fit=0.5, age=25)
    assert 0.50 <= p["stability_boost"] <= 1.50, f"stability_boost out of bounds at gen={gen}"
    assert 0.50 <= p["mutation_scale"]  <= 2.00, f"mutation_scale out of bounds at gen={gen}"
