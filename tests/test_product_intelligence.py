"""
tests/test_product_intelligence.py — Product Intelligence Layer Test Suite

Covers all 6 parts:
  Part 1 — Product registration + content mapping
  Part 2 — Aggregate metrics accumulation
  Part 3 — Product scoring formula
  Part 4 — content_decision integration (boost/penalize/kill-switch)
  Part 5 — self_scaling product budget allocation
  Part 6 — Kill switch fire/persist/block
"""
from __future__ import annotations

import os
import sys
import pathlib
import importlib.util

# ── In-memory DBs for test isolation ─────────────────────────────────────────
os.environ["PRODUCT_STATE_DB"]      = ":memory:"
os.environ["PROFIT_STATE_DB"]       = ":memory:"
os.environ["ATTRIBUTION_STATE_DB"]  = ":memory:"

_root = pathlib.Path(__file__).resolve().parents[1]

def _load(name: str, rel: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _root / rel)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

# Load order: stores first, then engines
_load("core.profit_store",          "core/profit_store.py")
_load("core.profit_engine",         "core/profit_engine.py")
_load("core.attribution_store",     "core/attribution_store.py")
_load("core.attribution_engine",    "core/attribution_engine.py")
_load("core.product_intelligence",  "core/product_intelligence.py")
_load("core.content_decision",      "core/content_decision.py")
_load("core.self_scaling",          "core/self_scaling.py")

from core.product_intelligence import (
    register_product, register_content_product,
    update_product_metrics, get_product_metrics,
    get_product_score, get_product_score_for_content,
    get_product_for_content, is_product_killed, is_content_product_killed,
    get_score_delta, reset_product_state,
    _KILL_MIN_ATTEMPTS, _BOOST_THRESHOLD, _PENALIZE_THRESHOLD, _SCORE_DELTA,
)
from core.content_decision import (
    ContentCandidate, score_content_candidate, should_produce,
    reset_decision_state,
)
from core.self_scaling import (
    update_performance, get_product_budget_allocation,
    ProductBudgetAllocation, reset_scaling_state,
)
from core.profit_engine import reset_profit_state

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset():
    reset_product_state()
    reset_profit_state()
    reset_decision_state()
    reset_scaling_state()
    yield
    reset_product_state()
    reset_profit_state()
    reset_decision_state()
    reset_scaling_state()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _reg(pid="p-beauty-serum", cat="skincare", price="mid", trend=0.6):
    register_product(pid, category=cat, price_range=price, trend=trend)

def _map(cid="vid-1", pid="p-beauty-serum"):
    register_content_product(cid, pid)

def _update(pid="p-beauty-serum", revenue=10.0, cost=3.0,
            converted=True, new_content=False, perf=0.7):
    return update_product_metrics(pid, revenue, cost, converted, new_content, perf)


# ═══════════════════════════════════════════════════════════════════════════════
# Part 1 — Product Registration + Content Mapping
# ═══════════════════════════════════════════════════════════════════════════════

class TestProductRegistration:

    def test_register_creates_record(self):
        _reg()
        from core.product_intelligence import get_product_info
        info = get_product_info("p-beauty-serum")
        assert info is not None
        assert info["product_id"] == "p-beauty-serum"
        assert info["category"]   == "skincare"

    def test_register_stores_price_range(self):
        _reg(price="premium")
        from core.product_intelligence import get_product_info
        info = get_product_info("p-beauty-serum")
        assert info["price_range"] == "premium"

    def test_register_stores_trend(self):
        _reg(trend=0.8)
        from core.product_intelligence import get_product_info
        info = get_product_info("p-beauty-serum")
        assert info["trend"] == pytest.approx(0.8, abs=0.01)

    def test_register_upsert_overwrites(self):
        _reg(trend=0.5)
        register_product("p-beauty-serum", category="beauty", trend=0.9)
        from core.product_intelligence import get_product_info
        info = get_product_info("p-beauty-serum")
        assert info["category"] == "beauty"
        assert info["trend"]    == pytest.approx(0.9, abs=0.01)

    def test_map_content_to_product(self):
        _reg()
        _map("vid-1", "p-beauty-serum")
        pid = get_product_for_content("vid-1")
        assert pid == "p-beauty-serum"

    def test_map_unknown_content_returns_none(self):
        assert get_product_for_content("ghost-vid") is None

    def test_map_remap_overwrites(self):
        _reg("p1")
        _reg("p2")
        register_content_product("vid", "p1")
        register_content_product("vid", "p2")
        assert get_product_for_content("vid") == "p2"

    def test_multiple_contents_to_same_product(self):
        _reg()
        for i in range(5):
            register_content_product(f"vid-{i}", "p-beauty-serum")
        for i in range(5):
            assert get_product_for_content(f"vid-{i}") == "p-beauty-serum"


# ═══════════════════════════════════════════════════════════════════════════════
# Part 2 — Aggregate Metrics
# ═══════════════════════════════════════════════════════════════════════════════

class TestProductMetrics:

    def test_update_creates_metrics(self):
        _reg()
        _update(revenue=10.0, cost=3.0)
        m = get_product_metrics("p-beauty-serum")
        assert m is not None
        assert m["total_revenue"] == pytest.approx(10.0, abs=0.01)
        assert m["total_cost"]    == pytest.approx(3.0,  abs=0.01)
        assert m["profit"]        == pytest.approx(7.0,  abs=0.01)

    def test_update_accumulates_revenue(self):
        _reg()
        _update(revenue=10.0, cost=2.0)
        _update(revenue=5.0,  cost=1.0)
        m = get_product_metrics("p-beauty-serum")
        assert m["total_revenue"] == pytest.approx(15.0, abs=0.01)
        assert m["total_cost"]    == pytest.approx(3.0,  abs=0.01)
        assert m["profit"]        == pytest.approx(12.0, abs=0.01)

    def test_attempt_count_increments(self):
        _reg()
        for _ in range(3):
            _update()
        m = get_product_metrics("p-beauty-serum")
        assert m["attempt_count"] == 3

    def test_content_count_increments_on_new_content(self):
        _reg()
        _update(new_content=True)
        _update(new_content=True)
        _update(new_content=False)
        m = get_product_metrics("p-beauty-serum")
        assert m["content_count"] == 2

    def test_conversion_ema_updates(self):
        _reg()
        _update(converted=True)
        m = get_product_metrics("p-beauty-serum")
        assert m["conversion_ema"] > 0.0

    def test_no_conversion_drives_ema_down(self):
        _reg()
        _update(converted=True)
        for _ in range(10):
            _update(converted=False)
        m = get_product_metrics("p-beauty-serum")
        assert m["conversion_ema"] < 0.3

    def test_unknown_product_returns_none(self):
        assert get_product_metrics("ghost") is None

    def test_negative_revenue_clamped_to_zero(self):
        _reg()
        _update(revenue=-100.0)
        m = get_product_metrics("p-beauty-serum")
        assert m["total_revenue"] == pytest.approx(0.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Part 3 — Product Scoring
# ═══════════════════════════════════════════════════════════════════════════════

class TestProductScoring:

    def test_unknown_product_returns_neutral(self):
        assert get_product_score("ghost") == pytest.approx(0.5)

    def test_profitable_product_scores_above_half(self):
        _reg(trend=0.7)
        for _ in range(5):
            _update(revenue=10.0, cost=1.0, converted=True, perf=0.8)
        score = get_product_score("p-beauty-serum")
        assert score > 0.5, f"Profitable product should score > 0.5, got {score}"

    def test_loss_product_scores_below_half(self):
        _reg(trend=0.3)
        for _ in range(5):
            _update(revenue=0.1, cost=5.0, converted=False, perf=0.2)
        score = get_product_score("p-beauty-serum")
        assert score < 0.5, f"Loss product should score < 0.5, got {score}"

    def test_score_in_unit_range(self):
        _reg()
        _update(revenue=5.0, cost=2.0)
        score = get_product_score("p-beauty-serum")
        assert 0.0 <= score <= 1.0

    def test_score_for_content_with_no_mapping_is_neutral(self):
        assert get_product_score_for_content("unmapped-vid") == pytest.approx(0.5)

    def test_score_for_content_reflects_product(self):
        _reg(trend=0.8)
        _map("vid-1", "p-beauty-serum")
        for _ in range(5):
            _update(revenue=20.0, cost=1.0, converted=True)
        assert get_product_score_for_content("vid-1") > 0.5

    def test_higher_trend_increases_score(self):
        _reg("p-low",  trend=0.1)
        _reg("p-high", trend=0.9)
        _update("p-low",  revenue=5.0, cost=3.0)
        _update("p-high", revenue=5.0, cost=3.0)
        assert get_product_score("p-high") > get_product_score("p-low")


# ═══════════════════════════════════════════════════════════════════════════════
# Part 4 — content_decision Integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestDecisionIntegration:

    def _candidate(self, cid="vid-1", **kwargs):
        defaults = dict(
            trend_score=0.6, product_intent=0.6, hook_potential=0.6,
            match_score=0.6, novelty_score=0.6, production_cost=0.3,
        )
        defaults.update(kwargs)
        return ContentCandidate(item_id=cid, **defaults)

    def test_score_delta_neutral_no_mapping(self):
        delta = get_score_delta("unmapped-vid")
        assert delta == 0.0

    def test_score_delta_boost_high_product(self):
        """High-profit product → +_SCORE_DELTA."""
        _reg(trend=0.9)
        _map("vid-b", "p-beauty-serum")
        for _ in range(10):
            _update(revenue=30.0, cost=1.0, converted=True, perf=0.9)
        delta = get_score_delta("vid-b")
        assert delta == pytest.approx(+_SCORE_DELTA, abs=0.001)

    def test_score_delta_penalize_low_product(self):
        """Loss-making product → -_SCORE_DELTA."""
        _reg(trend=0.1)
        _map("vid-p", "p-beauty-serum")
        for _ in range(10):
            _update(revenue=0.01, cost=10.0, converted=False, perf=0.1)
        delta = get_score_delta("vid-p")
        assert delta == pytest.approx(-_SCORE_DELTA, abs=0.001)

    def test_product_boost_increases_score(self):
        """A high-profit product mapping should increase the candidate score."""
        _reg("p-hot", trend=0.9)
        for _ in range(10):
            update_product_metrics("p-hot", 20.0, 1.0, True, False, 0.9)

        c_no_map = self._candidate("vid-no")
        c_mapped = self._candidate("vid-yes")
        register_content_product("vid-yes", "p-hot")

        s_no, _ = score_content_candidate(c_no_map)
        s_yes, _ = score_content_candidate(c_mapped)
        assert s_yes > s_no

    def test_product_penalize_decreases_score(self):
        """A loss-making product should lower the candidate score."""
        _reg("p-bad", trend=0.1)
        for _ in range(10):
            update_product_metrics("p-bad", 0.01, 10.0, False, False, 0.1)

        c_no_map = self._candidate("vid-none")
        c_bad    = self._candidate("vid-bad")
        register_content_product("vid-bad", "p-bad")

        s_none, _ = score_content_candidate(c_no_map)
        s_bad,  _ = score_content_candidate(c_bad)
        assert s_bad < s_none

    def test_breakdown_has_product_score_fields(self):
        _reg()
        _map("vid-1", "p-beauty-serum")
        _, bd = score_content_candidate(self._candidate("vid-1"))
        assert "product_score_used" in bd
        assert "product_delta"      in bd

    def test_kill_switch_blocks_should_produce(self):
        """Content mapped to a killed product → should_produce returns False."""
        _reg("p-dead")
        _map("vid-dead", "p-dead")
        # Fire kill switch: negative profit + enough attempts
        for _ in range(_KILL_MIN_ATTEMPTS + 1):
            update_product_metrics("p-dead", 0.0, 5.0, False)
        assert is_product_killed("p-dead")

        c = ContentCandidate(item_id="vid-dead", product_id="p-dead",
                             trend_score=1.0, product_intent=1.0, hook_potential=1.0,
                             match_score=1.0, novelty_score=1.0, production_cost=0.0)
        allowed, reason = should_produce(c, mode="generate", niche="beauty")
        assert not allowed
        assert "product_killed" in reason

    def test_no_kill_switch_on_good_product(self):
        """Profitable product → should_produce not blocked by kill switch."""
        _reg("p-good")
        _map("vid-good", "p-good")
        for _ in range(10):
            update_product_metrics("p-good", 10.0, 1.0, True)
        assert not is_product_killed("p-good")

        c = ContentCandidate(item_id="vid-good", product_id="p-good",
                             trend_score=1.0, product_intent=1.0, hook_potential=1.0,
                             match_score=1.0, novelty_score=1.0, production_cost=0.0)
        allowed, _ = should_produce(c, mode="remark", niche="beauty")
        assert allowed


# ═══════════════════════════════════════════════════════════════════════════════
# Part 5 — self_scaling Product Budget Allocation
# ═══════════════════════════════════════════════════════════════════════════════

class TestProductBudgetAllocation:

    def _setup_content(self, cid, niche, product_id, revenue, cost, views=0.7):
        _reg(product_id, trend=0.6)
        register_content_product(cid, product_id)
        for _ in range(5):
            update_product_metrics(product_id, revenue, cost, revenue > cost, False, views)
        update_performance(cid, niche, views=views, engagement_rate=0.5,
                           conversion_rate=0.3, profit_score=0.7)

    def test_empty_queue_returns_unattributed_total(self):
        alloc = get_product_budget_allocation(100.0, "empty-niche")
        assert isinstance(alloc, ProductBudgetAllocation)
        assert alloc.unattributed == pytest.approx(100.0, abs=0.01)

    def test_budget_distributed_across_products(self):
        self._setup_content("vid-A", "beauty", "p-A", 10.0, 1.0)
        self._setup_content("vid-B", "beauty", "p-B", 5.0,  2.0)
        alloc = get_product_budget_allocation(100.0, "beauty")
        # Both products should get some budget
        total = sum(alloc.per_product.values()) + alloc.unattributed
        assert total == pytest.approx(100.0, abs=1.0)

    def test_higher_performing_product_gets_more_budget(self):
        self._setup_content("vid-hot", "tech", "p-hot", 20.0, 1.0, views=0.9)
        self._setup_content("vid-cold","tech", "p-cold", 0.1, 5.0, views=0.2)
        alloc = get_product_budget_allocation(100.0, "tech")
        budget_hot  = alloc.per_product.get("p-hot",  0.0)
        budget_cold = alloc.per_product.get("p-cold", 0.0)
        assert budget_hot > budget_cold

    def test_killed_product_gets_zero_budget(self):
        self._setup_content("vid-kill", "beauty", "p-kill", 0.0, 5.0)
        # Force kill
        for _ in range(_KILL_MIN_ATTEMPTS + 1):
            update_product_metrics("p-kill", 0.0, 5.0, False)
        assert is_product_killed("p-kill")
        alloc = get_product_budget_allocation(100.0, "beauty")
        assert alloc.per_product.get("p-kill", 0.0) == 0.0
        assert "p-kill" in alloc.killed_products

    def test_content_without_mapping_goes_to_unattributed(self):
        # Content with no product mapping
        update_performance("orphan-vid", "beauty", views=0.5,
                           engagement_rate=0.5, conversion_rate=0.3)
        alloc = get_product_budget_allocation(100.0, "beauty")
        assert alloc.unattributed > 0.0

    def test_allocation_sums_to_total(self):
        self._setup_content("v1", "food", "p1", 5.0, 1.0)
        self._setup_content("v2", "food", "p2", 8.0, 2.0)
        alloc = get_product_budget_allocation(100.0, "food")
        total = sum(alloc.per_product.values()) + alloc.unattributed
        assert total == pytest.approx(100.0, abs=1.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Part 6 — Kill Switch
# ═══════════════════════════════════════════════════════════════════════════════

class TestKillSwitch:

    def test_not_killed_before_min_attempts(self):
        _reg()
        for _ in range(_KILL_MIN_ATTEMPTS - 1):
            _update(revenue=0.0, cost=5.0)
        assert not is_product_killed("p-beauty-serum")

    def test_killed_after_min_attempts_with_loss(self):
        _reg()
        for _ in range(_KILL_MIN_ATTEMPTS + 1):
            _update(revenue=0.0, cost=5.0)
        assert is_product_killed("p-beauty-serum")

    def test_not_killed_if_profitable(self):
        _reg()
        for _ in range(_KILL_MIN_ATTEMPTS + 10):
            _update(revenue=10.0, cost=1.0)
        assert not is_product_killed("p-beauty-serum")

    def test_kill_persists_in_store(self):
        _reg()
        for _ in range(_KILL_MIN_ATTEMPTS + 1):
            _update(revenue=0.0, cost=5.0)
        # Read again from store
        m = get_product_metrics("p-beauty-serum")
        assert m["killed"] is True

    def test_kill_does_not_fire_at_exactly_threshold_minus_one(self):
        """At exactly _KILL_MIN_ATTEMPTS - 1 attempts, not yet killed."""
        _reg()
        for _ in range(_KILL_MIN_ATTEMPTS - 1):
            _update(revenue=0.0, cost=10.0)
        assert not is_product_killed("p-beauty-serum")

    def test_kill_fires_at_exact_min_attempts(self):
        """Kill fires at attempt_count == _KILL_MIN_ATTEMPTS (with loss)."""
        _reg()
        for _ in range(_KILL_MIN_ATTEMPTS):
            _update(revenue=0.0, cost=5.0)
        assert is_product_killed("p-beauty-serum")

    def test_is_content_product_killed_no_mapping(self):
        """Content with no product mapping → not killed."""
        assert not is_content_product_killed("no-product-vid")

    def test_is_content_product_killed_with_mapping(self):
        _reg()
        _map("vid-kill", "p-beauty-serum")
        for _ in range(_KILL_MIN_ATTEMPTS + 1):
            _update(revenue=0.0, cost=5.0)
        assert is_content_product_killed("vid-kill")

    def test_reset_clears_kill_state(self):
        _reg()
        for _ in range(_KILL_MIN_ATTEMPTS + 1):
            _update(revenue=0.0, cost=5.0)
        assert is_product_killed("p-beauty-serum")
        reset_product_state()
        assert not is_product_killed("p-beauty-serum")

    def test_unknown_product_not_killed(self):
        assert not is_product_killed("ghost-product")
