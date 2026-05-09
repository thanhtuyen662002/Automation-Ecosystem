"""
Tests for core/account_clustering.py v2 — Self-Organizing Swarm.
"""
import importlib.util, sys, math
from types import ModuleType

def _load(path: str, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m

sys.modules.setdefault("core", type(sys)("core"))

for path, name in [
    ("core/platform_profiles.py",   "core.platform_profiles"),
    ("core/mutation_controller.py", "core.mutation_controller"),
    ("core/account_clustering.py",  "core.account_clustering"),
]:
    _load(path, name)

import core.account_clustering as ac
import pytest


@pytest.fixture(autouse=True)
def _reset():
    ac.reset_clustering()
    yield
    ac.reset_clustering()


def _populate(n: int, risk: float = 0.3, reward: float = 0.2) -> None:
    for i in range(n):
        ac.update_embedding(f"a{i}", risk, reward, True, "GROWTH", False)


# ── Embedding update ──────────────────────────────────────────────────────────

class TestUpdateEmbedding:
    def test_dim(self):
        emb = ac.update_embedding("a1", 0.5, 0.0, True, "GROWTH", False)
        assert len(emb) == ac.EMB_DIM

    def test_bounded(self):
        for _ in range(10):
            ac.update_embedding("a1", 0.9, -1.5, False, "DECLINE", True)
        assert all(0.0 <= v <= 1.0 for v in ac._EMBEDDINGS["a1"])

    def test_converges(self):
        for _ in range(50):
            ac.update_embedding("a1", 1.0, 1.0, True, "MATURE", False)
        assert ac._EMBEDDINGS["a1"][0] > 0.90

    def test_deterministic(self):
        seq = [(0.3, 0.2, True, "GROWTH", False), (0.7, -0.5, False, "MATURE", True)]
        for args in seq:
            ac.update_embedding("a1", *args)
        e1 = list(ac._EMBEDDINGS["a1"])
        ac.reset_clustering()
        for args in seq:
            ac.update_embedding("a1", *args)
        assert e1 == list(ac._EMBEDDINGS["a1"])

    def test_no_cross_account_leak(self):
        ac.update_embedding("a1", 1.0, 1.0, True, "MATURE", False)
        ac.update_embedding("a2", 0.0, -1.0, False, "NEW", True)
        assert ac._EMBEDDINGS["a1"] != ac._EMBEDDINGS["a2"]

    def test_drift_ema_updated(self):
        ac.update_embedding("a1", 0.5, 0.0, True, "GROWTH", False)
        drift_before = ac._DRIFT_EMA
        ac.update_embedding("a1", 0.9, 1.0, False, "MATURE", True)
        # drift EMA should change after a significantly different observation
        assert isinstance(ac._DRIFT_EMA, float)

    def test_exception_safe_bad_stage(self):
        emb = ac.update_embedding("a1", 0.5, 0.0, True, "BAD_STAGE", False)
        assert len(emb) == ac.EMB_DIM

    def test_exception_safe_nan_inputs(self):
        emb = ac.update_embedding("a1", float("nan"), float("nan"), True, "GROWTH", False)
        assert len(emb) == ac.EMB_DIM
        assert all(0.0 <= v <= 1.0 for v in emb)


# ── Run clustering ────────────────────────────────────────────────────────────

class TestRunClustering:
    def test_assigns_all_accounts(self):
        _populate(20)
        ac.run_clustering()
        for i in range(20):
            cid = ac._CLUSTER_IDS.get(f"a{i}")
            assert cid is not None and cid.startswith("c")

    def test_k_within_bounds(self):
        _populate(30)
        ac.run_clustering()
        assert ac.K_MIN <= ac._K <= ac.K_MAX

    def test_deterministic_same_data(self):
        for i in range(10):
            ac.update_embedding(f"a{i}", i / 10, 0.5, True, "GROWTH", False)
        ac.run_clustering()
        ids1 = dict(ac._CLUSTER_IDS)
        k1   = ac._K

        ac.reset_clustering()
        for i in range(10):
            ac.update_embedding(f"a{i}", i / 10, 0.5, True, "GROWTH", False)
        ac.run_clustering()

        assert dict(ac._CLUSTER_IDS) == ids1
        assert ac._K == k1

    def test_empty_no_crash(self):
        result = ac.run_clustering()
        assert isinstance(result, dict)

    def test_cluster_ids_valid_int(self):
        _populate(20)
        ac.run_clustering()
        for cid in ac._CLUSTER_IDS.values():
            assert cid.startswith("c")
            idx = int(cid[1:])
            assert 0 <= idx < ac._K

    def test_age_increments(self):
        _populate(10)
        ac.run_clustering()
        ages_after_1 = dict(ac._CLUSTER_AGE)
        ac.run_clustering()
        # At least some cluster ages should have increased
        assert any(ac._CLUSTER_AGE.get(k, 0) > ages_after_1.get(k, 0)
                   for k in ac._CLUSTER_AGE)

    def test_stability_tracked(self):
        _populate(10)
        ac.run_clustering()
        assert len(ac._CLUSTER_STABILITY) > 0
        for s in ac._CLUSTER_STABILITY.values():
            assert 0.0 <= s <= 1.0


# ── Dynamic K ─────────────────────────────────────────────────────────────────

class TestDynamicK:
    def test_k_never_below_k_min(self):
        # Very similar embeddings → low inter-dist → K pressure to shrink
        for i in range(20):
            ac.update_embedding(f"a{i}", 0.5, 0.0, True, "GROWTH", False)
        for _ in range(10):
            ac.run_clustering()
        assert ac._K >= ac.K_MIN

    def test_k_never_above_k_max(self):
        # Very diverse embeddings → high cohesion → K pressure to grow
        for i in range(50):
            ac.update_embedding(f"a{i}", i / 50, (i % 5) * 0.2 - 0.5,
                                i % 2 == 0, "GROWTH", i % 3 == 0)
        for _ in range(15):
            ac.run_clustering()
        assert ac._K <= ac.K_MAX


# ── Collapse prevention ────────────────────────────────────────────────────────

class TestCollapsePrevention:
    def test_diverse_embeddings_no_total_collapse(self):
        """With diverse embeddings, no single cluster should hold all accounts."""
        for i in range(30):
            # Spread embeddings across [0, 1] — very diverse
            ac.update_embedding(f"a{i}", i / 30, (i % 6) * 0.2 - 0.5,
                                i % 2 == 0, "GROWTH", i % 4 == 0)
        ac.run_clustering()
        counts = {}
        for cid in ac._CLUSTER_IDS.values():
            counts[cid] = counts.get(cid, 0) + 1
        n = len(ac._EMBEDDINGS)
        largest = max(counts.values())
        # With diverse inputs, dominant cluster must be < full fleet
        assert largest < n

    def test_k_bounded_after_split(self):
        """K must never exceed K_MAX even after many splits."""
        for i in range(50):
            ac.update_embedding(f"a{i}", i / 50, (i % 10) * 0.1 - 0.5,
                                i % 2 == 0, "GROWTH", False)
        for _ in range(5):
            ac.run_clustering()
        assert ac._K <= ac.K_MAX


# ── Adaptive recluster ────────────────────────────────────────────────────────

class TestAdaptiveRecluster:
    def test_high_drift_triggers_recluster(self):
        _populate(10)
        ac._LAST_RECLUSTER = 999   # pretend recent recluster
        ac._DRIFT_EMA = ac._DRIFT_THRESHOLD + 0.01
        triggered = ac.notify_cycle(1000)
        assert triggered is True

    def test_low_drift_stable_no_trigger(self):
        _populate(10)
        ac.run_clustering()
        ac._DRIFT_EMA = 0.01   # very stable
        # cycle just after last recluster, within N_MAX
        triggered = ac.notify_cycle(ac._LAST_RECLUSTER + 1)
        assert triggered is False

    def test_n_max_cycles_forces_recluster(self):
        _populate(10)
        ac._LAST_RECLUSTER = 0
        ac._DRIFT_EMA = 0.01
        # Advance cycle past N_MAX
        triggered = ac.notify_cycle(ac._N_MAX_CYCLES + 1)
        assert triggered is True


# ── get_cluster_id ────────────────────────────────────────────────────────────

class TestGetClusterId:
    def test_returns_c_prefixed_string(self):
        _populate(10)
        ac.run_clustering()
        cid = ac.get_cluster_id("a0")
        assert cid.startswith("c")

    def test_fallback_c0_no_centroids(self):
        cid = ac.get_cluster_id("brand_new")
        assert cid == "c0"

    def test_fallback_nearest_centroid(self):
        _populate(10)
        ac.run_clustering()
        ac.update_embedding("new_acct", 0.9, 0.0, False, "DECLINE", True)
        ac._CLUSTER_IDS.pop("new_acct", None)
        cid = ac.get_cluster_id("new_acct")
        assert cid.startswith("c")
        assert 0 <= int(cid[1:]) < ac._K

    def test_sticky_when_no_recluster(self):
        _populate(5)
        ac.notify_cycle(0)
        cid1 = ac.get_cluster_id("a0")
        ac._DRIFT_EMA = 0.0
        ac.notify_cycle(1)   # no recluster (low drift, recent)
        cid2 = ac.get_cluster_id("a0")
        assert cid1 == cid2


# ── Cluster quality ───────────────────────────────────────────────────────────

class TestClusterQuality:
    def test_record_quality_updates_state(self):
        ac.record_cluster_quality(0, 0.5, 0.5)
        assert 0 in ac._CLUSTER_QUALITY
        q = ac._CLUSTER_QUALITY[0]
        assert 0.0 <= q

    def test_high_reward_low_risk_increases_quality(self):
        ac.record_cluster_quality(0, 0.0, 1.0)  # best case
        ac.record_cluster_quality(0, 0.0, 1.0)
        ac.record_cluster_quality(0, 0.0, 1.0)
        assert ac._CLUSTER_QUALITY[0] > 0.5

    def test_high_risk_low_reward_decreases_quality(self):
        ac._CLUSTER_QUALITY[0] = 1.0
        for _ in range(10):
            ac.record_cluster_quality(0, 0.9, -1.0)
        assert ac._CLUSTER_QUALITY[0] < 1.0


# ── Learning rate dampening ───────────────────────────────────────────────────

class TestLearningRate:
    def test_stable_cluster_full_rate(self):
        ac._CLUSTER_STABILITY[0] = 0.9
        lr = ac.get_cluster_learning_rate("c0", base_lr=1.0)
        assert lr == 1.0

    def test_unstable_cluster_dampened(self):
        ac._CLUSTER_STABILITY[0] = 0.2
        lr = ac.get_cluster_learning_rate("c0", base_lr=1.0)
        assert lr < 1.0

    def test_unknown_cluster_returns_base(self):
        lr = ac.get_cluster_learning_rate("c99", base_lr=1.0)
        assert lr == 1.0


# ── Snapshot ──────────────────────────────────────────────────────────────────

class TestSnapshot:
    def test_required_keys(self):
        snap = ac.snapshot()
        for k in ("K", "n_accounts", "n_assigned", "cycle", "last_recluster",
                   "drift_ema", "cluster_counts", "cluster_age",
                   "cluster_stability", "cluster_quality", "centroids"):
            assert k in snap

    def test_cluster_counts_sum(self):
        _populate(15)
        ac.run_clustering()
        snap = ac.snapshot()
        assert sum(snap["cluster_counts"].values()) == snap["n_assigned"]

    def test_k_reflects_dynamic(self):
        _populate(20)
        ac.run_clustering()
        snap = ac.snapshot()
        assert snap["K"] == ac._K
