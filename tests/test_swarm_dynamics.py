"""
Tests for core/swarm_dynamics.py -- Genotype/Phenotype separation + Darwin layer.
"""
from __future__ import annotations

import importlib.util
import sys
from types import ModuleType


def _load(path: str, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


sys.modules.setdefault("core", type(sys)("core"))
for _path, _name in [
    ("core/platform_profiles.py",  "core.platform_profiles"),
    ("core/mutation_controller.py", "core.mutation_controller"),
    ("core/swarm_dynamics.py",      "core.swarm_dynamics"),
]:
    _load(_path, _name)

import core.swarm_dynamics as sd
import pytest


@pytest.fixture(autouse=True)
def _reset():
    sd.reset_swarm()
    yield
    sd.reset_swarm()


def _seed_clusters(n: int, reward: float = 0.5, risk: float = 0.2) -> list[str]:
    ids = [f"c{i}" for i in range(n)]
    for cid in ids:
        sd.update_cluster_fitness(cid, reward, risk)
    return ids


def _full_state() -> dict:
    """Return a representative optimizer state for encoding tests."""
    return {
        "behavior_aggressiveness_mult":   1.10,
        "strategy_explorer_weight_mult":  0.90,
        "strategy_harvester_weight_mult": 0.95,
        "platform_burstiness_mult":       1.05,
        "platform_delay_base_mult":       1.20,
    }


# ---------------------------------------------------------------------------
# PART 1 -- encode_genome
# ---------------------------------------------------------------------------

class TestEncodeGenome:
    def test_returns_all_genome_keys(self):
        g = sd.encode_genome(_full_state())
        assert set(g.keys()) == set(sd.GENOME_KEYS)

    def test_aggressiveness_base_maps_correctly(self):
        g = sd.encode_genome({"behavior_aggressiveness_mult": 1.15})
        assert abs(g["aggressiveness_base"] - 1.15) < 1e-5

    def test_exploration_base_maps_correctly(self):
        g = sd.encode_genome({"strategy_explorer_weight_mult": 0.80})
        assert abs(g["exploration_base"] - 0.80) < 1e-5

    def test_risk_tolerance_inverse_of_delay(self):
        g = sd.encode_genome({"platform_delay_base_mult": 1.0})
        assert abs(g["risk_tolerance"] - 1.0) < 1e-5
        g2 = sd.encode_genome({"platform_delay_base_mult": 2.0})
        assert abs(g2["risk_tolerance"] - 0.5) < 1e-5

    def test_diversity_bias_maps_correctly(self):
        g = sd.encode_genome({"strategy_harvester_weight_mult": 1.05})
        assert abs(g["diversity_bias"] - 1.05) < 1e-5


# ---------------------------------------------------------------------------
# PART 2 -- decode_genome
# ---------------------------------------------------------------------------

class TestDecodeGenome:
    def test_returns_optimizer_keys(self):
        genome = {k: 1.0 for k in sd.GENOME_KEYS}
        d = sd.decode_genome(genome)
        assert "behavior_aggressiveness_mult" in d
        assert "strategy_explorer_weight_mult" in d
        assert "platform_delay_base_mult" in d
        assert "strategy_harvester_weight_mult" in d

    def test_high_aggressiveness_decodes_correctly(self):
        genome = {k: sd.GENOME_NEUTRAL for k in sd.GENOME_KEYS}
        genome["aggressiveness_base"] = 1.20
        d = sd.decode_genome(genome)
        assert d["behavior_aggressiveness_mult"] > 1.0

    def test_empty_genome_returns_empty(self):
        d = sd.decode_genome({})
        assert isinstance(d, dict)


# ---------------------------------------------------------------------------
# PART 3 -- record_cluster_genome (EMA smooth update + Context bleed)
# ---------------------------------------------------------------------------

class TestRecordClusterGenome:
    def test_first_record_seeds_all_contexts(self):
        sd.record_cluster_genome("c0", _full_state(), risk=0.5)
        assert "c0" in sd._CLUSTER_GENOME
        for ctx in sd.GENOME_CONTEXTS:
            assert ctx in sd._CLUSTER_GENOME["c0"]

    def test_second_record_ema_blends_active_context(self):
        state_a = {"behavior_aggressiveness_mult": 1.0,
                   "strategy_explorer_weight_mult": 1.0,
                   "strategy_harvester_weight_mult": 1.0,
                   "platform_delay_base_mult": 1.0}
        state_b = {"behavior_aggressiveness_mult": 1.40,
                   "strategy_explorer_weight_mult": 1.40,
                   "strategy_harvester_weight_mult": 1.40,
                   "platform_delay_base_mult": 1.0}
        sd.record_cluster_genome("c0", state_a, risk=0.9) # high risk
        first_val = sd._CLUSTER_GENOME["c0"]["high_risk"]["aggressiveness_base"]

        sd.record_cluster_genome("c0", state_b, risk=0.9)
        second_val = sd._CLUSTER_GENOME["c0"]["high_risk"]["aggressiveness_base"]

        assert second_val > first_val

    def test_cross_context_bleed(self):
        state_a = {"behavior_aggressiveness_mult": 1.0}
        state_b = {"behavior_aggressiveness_mult": 1.40}
        sd.record_cluster_genome("c0", state_a, risk=0.1) # low risk
        
        # update low risk again to cause bleed to high risk and default
        sd.record_cluster_genome("c0", state_b, risk=0.1) 
        
        low_risk_val = sd._CLUSTER_GENOME["c0"]["low_risk"]["aggressiveness_base"]
        high_risk_val = sd._CLUSTER_GENOME["c0"]["high_risk"]["aggressiveness_base"]
        
        assert high_risk_val > 1.0 # Bleed worked
        assert high_risk_val < low_risk_val # Bleed is smaller than main update


# ---------------------------------------------------------------------------
# PART 4 -- _mutate_genome (DNA-level mutation)
# ---------------------------------------------------------------------------

class TestMutateGenome:
    def test_all_keys_and_contexts_present(self):
        parent = {ctx: {k: 1.0 for k in sd.GENOME_KEYS} for ctx in sd.GENOME_CONTEXTS}
        child  = sd._mutate_genome(parent, "d0", "c0", 0)
        for ctx in sd.GENOME_CONTEXTS:
            assert ctx in child
            assert set(child[ctx].keys()) == set(sd.GENOME_KEYS)

    def test_deterministic(self):
        parent = {ctx: {k: 1.0 for k in sd.GENOME_KEYS} for ctx in sd.GENOME_CONTEXTS}
        c1 = sd._mutate_genome(parent, "d0", "c0", 0)
        c2 = sd._mutate_genome(parent, "d0", "c0", 0)
        assert c1 == c2


# ---------------------------------------------------------------------------
# PART 5 -- apply_fitness_feedback_to_genome
# ---------------------------------------------------------------------------

class TestFitnessFeedback:
    def test_strong_fitness_increases_aggressiveness(self):
        genome = {ctx: {k: 1.0 for k in sd.GENOME_KEYS} for ctx in sd.GENOME_CONTEXTS}
        sd._CLUSTER_GENOME["c0"] = dict(genome)
        sd._CLUSTER_FITNESS["c0"] = {"default": sd.GENOME_PRESSURE_STRONG_THRESH + 0.1}
        sd.apply_fitness_feedback_to_genome("c0")
        assert sd._CLUSTER_GENOME["c0"]["default"]["aggressiveness_base"] > 1.0

    def test_weak_fitness_increases_exploration(self):
        genome = {ctx: {k: 1.0 for k in sd.GENOME_KEYS} for ctx in sd.GENOME_CONTEXTS}
        sd._CLUSTER_GENOME["c0"] = dict(genome)
        sd._CLUSTER_FITNESS["c0"] = {"default": sd.GENOME_PRESSURE_WEAK_THRESH - 0.1}
        sd.apply_fitness_feedback_to_genome("c0")
        assert sd._CLUSTER_GENOME["c0"]["default"]["exploration_base"] > 1.0


# ---------------------------------------------------------------------------
# PART 6 -- normalize_genome / _clamp_genome
# ---------------------------------------------------------------------------

class TestNormalizeGenome:
    def test_clamp_above_max(self):
        sd._CLUSTER_GENOME["c0"] = {"default": {k: 99.0 for k in sd.GENOME_KEYS}}
        sd.normalize_genome("c0")
        for v in sd._CLUSTER_GENOME["c0"]["default"].values():
            assert v == sd.GENOME_CLAMP_MAX


# ---------------------------------------------------------------------------
# blend_genome_into_state
# ---------------------------------------------------------------------------

class TestBlendGenomeIntoState:
    def test_blend_shifts_toward_active_context(self):
        genome = {"high_risk": {k: 1.0 for k in sd.GENOME_KEYS}}
        genome["high_risk"]["aggressiveness_base"] = 1.30
        sd._CLUSTER_GENOME["c0"] = genome
        state = {"behavior_aggressiveness_mult": 1.00,
                 "strategy_explorer_weight_mult": 1.00,
                 "platform_delay_base_mult": 1.00,
                 "strategy_harvester_weight_mult": 1.00}
        sd.blend_genome_into_state("c0", state, risk=0.9)
        assert state["behavior_aggressiveness_mult"] > 1.00


# ---------------------------------------------------------------------------
# Genome decay
# ---------------------------------------------------------------------------

class TestGenomeDecay:
    def test_above_neutral_moves_down(self):
        sd._CLUSTER_GENOME["c0"] = {"default": {k: 1.20 for k in sd.GENOME_KEYS}}
        sd.apply_genome_decay()
        for v in sd._CLUSTER_GENOME["c0"]["default"].values():
            assert v < 1.20


# ---------------------------------------------------------------------------
# Fitness scoring
# ---------------------------------------------------------------------------

class TestFitnessScoring:
    def test_returns_float(self):
        assert isinstance(sd.update_cluster_fitness("c0", 0.5, 0.2), float)

    def test_ema_smoothing(self):
        # Risk 0.5 maps to "default" context
        sd.update_cluster_fitness("c0", 0.5, 0.5)
        prev = sd._CLUSTER_FITNESS["c0"]["default"]
        sd.update_cluster_fitness("c0", 100.0, 0.5)
        new = sd._CLUSTER_FITNESS["c0"]["default"]
        assert prev < new < 100.0 + sd.FITNESS_REWARD_SHIFT


# ---------------------------------------------------------------------------
# Resource allocation
# ---------------------------------------------------------------------------

class TestResourceAllocation:
    def test_sums_to_one(self):
        _seed_clusters(5)
        sd.allocate_resources()
        total = sum(v for k, v in sd._CLUSTER_RESOURCE.items()
                    if k not in sd._INACTIVE_CLUSTERS)
        assert abs(total - 1.0) < 1e-3


# ---------------------------------------------------------------------------
# Elimination
# ---------------------------------------------------------------------------

class TestElimination:
    def test_streak_increments(self):
        # Drop all contexts directly to pull global fitness below 0.40
        sd._CLUSTER_FITNESS["c0"] = {"default": 0.1, "high_risk": 0.1, "low_risk": 0.1}
        sd.tick_weak_streak("c0")
        assert sd._CLUSTER_WEAK_STREAK.get("c0", 0) == 1


# ---------------------------------------------------------------------------
# Reproduction
# ---------------------------------------------------------------------------

class TestReproduction:
    def test_strong_cluster_spawns(self):
        sd._CLUSTER_FITNESS["c0"] = {"default": sd.REPRODUCTION_FITNESS_MIN + 0.5}
        d = sd.maybe_reproduce("c0", sd.REPRODUCTION_SIZE_MIN + 5, ["c0"])
        assert d is not None
        assert d.startswith("c0_d")

    def test_daughter_has_genome(self):
        genome = {ctx: {k: 1.10 for k in sd.GENOME_KEYS} for ctx in sd.GENOME_CONTEXTS}
        sd._CLUSTER_GENOME["c0"] = dict(genome)
        sd._CLUSTER_FITNESS["c0"] = {"default": sd.REPRODUCTION_FITNESS_MIN + 0.5}
        d = sd.maybe_reproduce("c0", sd.REPRODUCTION_SIZE_MIN + 5, ["c0"])
        assert d in sd._CLUSTER_GENOME


# ---------------------------------------------------------------------------
# Darwin cycle integration
# ---------------------------------------------------------------------------

class TestDarwinCycle:
    def test_returns_summary(self):
        r = sd.run_darwin_cycle({"c0": 0.5}, {"c0": 0.1}, {"c0": 3})
        for k in ("eliminated", "reproduced", "weak_clusters", "resource_map", "fitness_map"):
            assert k in r

    def test_genome_decays_after_cycle(self):
        # Use a mid-fitness cluster (no feedback fires) so decay is the only force.
        # fitness=0.5+0.1 -> EMA puts it in the neutral zone (no strong/weak feedback)
        sd._CLUSTER_GENOME["c0"] = {"default": {k: 1.30 for k in sd.GENOME_KEYS}}
        # risk_tolerance is NOT a feedback target, so it decays cleanly
        pre = sd._CLUSTER_GENOME["c0"]["default"]["risk_tolerance"]
        sd.run_darwin_cycle({"c0": 0.5}, {"c0": 0.5}, {"c0": 3})
        # risk_tolerance must have moved toward neutral (decay only, no feedback)
        assert sd._CLUSTER_GENOME["c0"]["default"]["risk_tolerance"] < pre


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

class TestSnapshot:
    def test_required_keys(self):
        snap = sd.snapshot()
        for k in ("fitness", "resources", "weak_streaks", "inactive",
                  "daughters", "genomes", "n_active", "resource_total"):
            assert k in snap
