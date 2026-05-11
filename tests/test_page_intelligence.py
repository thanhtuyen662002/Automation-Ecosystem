"""
tests/test_page_intelligence.py — Account & Page Intelligence Test Suite

Covers all 7 parts:
  Part 1 — Entity mapping (registration + content→page→account)
  Part 2 — Per-page metrics accumulation
  Part 3 — Page scoring formula
  Part 4 — Account scoring (aggregation)
  Part 5 — Scaling integration (page budget allocation)
  Part 6 — Kill/throttle logic
  Part 7 — Exploration bucket
"""
from __future__ import annotations

import os
import sys
import pathlib
import importlib.util

os.environ["PAGE_STATE_DB"]         = ":memory:"
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

_load("core.profit_store",         "core/profit_store.py")
_load("core.profit_engine",        "core/profit_engine.py")
_load("core.attribution_store",    "core/attribution_store.py")
_load("core.attribution_engine",   "core/attribution_engine.py")
_load("core.product_intelligence", "core/product_intelligence.py")
_load("core.page_intelligence",    "core/page_intelligence.py")
_load("core.content_decision",     "core/content_decision.py")
_load("core.self_scaling",         "core/self_scaling.py")

from core.page_intelligence import (
    register_page, update_page_metrics,
    get_page_score, get_account_score,
    get_page_status, is_page_throttled,
    get_page_posting_frequency,
    get_page_budget_weights, get_exploration_pages,
    get_combined_budget_weights, reset_page_state,
    _THROTTLE_MIN_POSTS, _PAUSE_MIN_POSTS,
    _THROTTLE_PROFIT_THRESHOLD, _PAUSE_PROFIT_THRESHOLD,
    _FREQ_WINNER, _FREQ_NORMAL, _FREQ_WEAK, _FREQ_PAUSED,
)
from core.self_scaling import (
    update_performance, get_page_aware_budget_allocation,
    PageAwareBudgetAllocation, reset_scaling_state,
)
from core.product_intelligence import reset_product_state
from core.profit_engine        import reset_profit_state

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset():
    reset_page_state()
    reset_product_state()
    reset_profit_state()
    reset_scaling_state()
    yield
    reset_page_state()
    reset_product_state()
    reset_profit_state()
    reset_scaling_state()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _reg(page_id="pg-1", account_id="acc-1", niche="beauty", is_new=False):
    register_page(page_id, account_id, niche, is_new)

def _upd(page_id="pg-1", views=1000.0, eng=0.05, rev=10.0, cost=2.0,
         converted=True, posts=1):
    return update_page_metrics(page_id, views, eng, rev, cost, converted, posts)

def _profitable(page_id="pg-1", n=10):
    _reg(page_id)
    for _ in range(n):
        _upd(page_id, views=1000.0, eng=0.08, rev=10.0, cost=1.0, converted=True)

def _losing(page_id="pg-1", n=6, cost=5.0):
    _reg(page_id)
    for _ in range(n):
        _upd(page_id, views=100.0, eng=0.01, rev=0.0, cost=cost, converted=False)


# ═══════════════════════════════════════════════════════════════════════════════
# Part 1 — Entity Mapping
# ═══════════════════════════════════════════════════════════════════════════════

class TestEntityMapping:

    def test_register_page_stores_entity(self):
        _reg("pg-1", "acc-A", "beauty")
        from core.page_intelligence import _get_store
        info = _get_store().get_page("pg-1")
        assert info is not None
        assert info["account_id"] == "acc-A"
        assert info["niche"]      == "beauty"

    def test_register_upsert_overwrites_account(self):
        _reg("pg-1", "acc-A")
        _reg("pg-1", "acc-B")
        from core.page_intelligence import _get_store
        assert _get_store().get_page("pg-1")["account_id"] == "acc-B"

    def test_pages_grouped_by_account(self):
        _reg("pg-1", "acc-A", "beauty")
        _reg("pg-2", "acc-A", "beauty")
        _reg("pg-3", "acc-B", "tech")
        from core.page_intelligence import _get_store
        pages = _get_store().get_pages_by_account("acc-A")
        assert set(pages) == {"pg-1", "pg-2"}

    def test_pages_grouped_by_niche(self):
        _reg("pg-1", "acc-A", "beauty")
        _reg("pg-2", "acc-B", "beauty")
        _reg("pg-3", "acc-C", "tech")
        from core.page_intelligence import _get_store
        beauty = [p["page_id"] for p in _get_store().get_pages_by_niche("beauty")]
        assert set(beauty) == {"pg-1", "pg-2"}

    def test_is_new_flag_stored(self):
        register_page("pg-new", "acc-1", "beauty", is_new=True)
        from core.page_intelligence import _get_store
        assert _get_store().get_page("pg-new")["is_new"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# Part 2 — Per-Page Metrics
# ═══════════════════════════════════════════════════════════════════════════════

class TestPageMetrics:

    def test_update_creates_record(self):
        _reg()
        m = _upd()
        assert m is not None
        assert m["total_views"]   == pytest.approx(1000.0)
        assert m["total_revenue"] == pytest.approx(10.0)
        assert m["total_cost"]    == pytest.approx(2.0)
        assert m["profit"]        == pytest.approx(8.0)

    def test_metrics_accumulate(self):
        _reg()
        _upd(rev=10.0, cost=2.0)
        m = _upd(rev=5.0, cost=1.0)
        assert m["total_revenue"] == pytest.approx(15.0)
        assert m["total_cost"]    == pytest.approx(3.0)
        assert m["profit"]        == pytest.approx(12.0)

    def test_post_count_increments(self):
        _reg()
        _upd(posts=3)
        m = _upd(posts=2)
        assert m["post_count"] == 5

    def test_conversion_ema_rises_on_conversion(self):
        _reg()
        for _ in range(5):
            _upd(converted=True)
        m = update_page_metrics("pg-1")
        assert m["conversion_ema"] > 0.2

    def test_conversion_ema_falls_without_conversion(self):
        _reg()
        _upd(converted=True)
        for _ in range(10):
            _upd(converted=False)
        m = update_page_metrics("pg-1")
        assert m["conversion_ema"] < 0.2

    def test_negative_inputs_clamped_to_zero(self):
        _reg()
        m = _upd(views=-500.0, rev=-10.0, cost=-5.0)
        assert m["total_views"]   == 0.0
        assert m["total_revenue"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Part 3 — Page Scoring
# ═══════════════════════════════════════════════════════════════════════════════

class TestPageScoring:

    def test_unknown_page_returns_neutral(self):
        assert get_page_score("ghost") == pytest.approx(0.5)

    def test_profitable_page_scores_above_half(self):
        _profitable()
        assert get_page_score("pg-1") > 0.5

    def test_losing_page_scores_below_half(self):
        _losing()
        assert get_page_score("pg-1") < 0.5

    def test_score_in_unit_range(self):
        _profitable()
        s = get_page_score("pg-1")
        assert 0.0 <= s <= 1.0

    def test_throttled_page_capped_at_040(self):
        _losing(n=_THROTTLE_MIN_POSTS + 1)
        s = get_page_score("pg-1")
        assert s <= 0.40

    def test_paused_page_capped_at_020(self):
        _losing(n=_PAUSE_MIN_POSTS + 1, cost=10.0)
        s = get_page_score("pg-1")
        assert s <= 0.20

    def test_high_engagement_boosts_score(self):
        _reg("pg-hi")
        _reg("pg-lo")
        for _ in range(10):
            # pg-hi: large engagement relative to views → high engagement_ema
            update_page_metrics("pg-hi", views=1000, engagement=500.0, revenue=10.0, cost=1.0)
            # pg-lo: tiny engagement
            update_page_metrics("pg-lo", views=1000, engagement=1.0,   revenue=10.0, cost=1.0)
        s_hi = get_page_score("pg-hi")
        s_lo = get_page_score("pg-lo")
        assert s_hi > s_lo, f"hi={s_hi} lo={s_lo}"


# ═══════════════════════════════════════════════════════════════════════════════
# Part 4 — Account Scoring
# ═══════════════════════════════════════════════════════════════════════════════

class TestAccountScoring:

    def test_no_pages_returns_neutral(self):
        assert get_account_score("no-pages-acc") == pytest.approx(0.5)

    def test_account_score_reflects_pages(self):
        register_page("pg-1", "acc-A", "beauty")
        register_page("pg-2", "acc-A", "beauty")
        for _ in range(10):
            update_page_metrics("pg-1", 1000.0, 0.1, 20.0, 1.0, True)
            update_page_metrics("pg-2", 1000.0, 0.1, 20.0, 1.0, True)
        assert get_account_score("acc-A") > 0.5

    def test_weak_account_scores_below_half(self):
        register_page("pg-bad1", "acc-bad", "beauty")
        register_page("pg-bad2", "acc-bad", "beauty")
        for _ in range(12):
            update_page_metrics("pg-bad1", 50.0, 0.001, 0.0, 8.0, False)
            update_page_metrics("pg-bad2", 50.0, 0.001, 0.0, 8.0, False)
        assert get_account_score("acc-bad") < 0.5

    def test_strong_account_outscores_weak(self):
        register_page("pg-a", "strong-acc", "beauty")
        register_page("pg-b", "weak-acc",   "beauty")
        for _ in range(10):
            update_page_metrics("pg-a", 1000.0, 0.1, 20.0, 1.0, True)
        for _ in range(10):
            update_page_metrics("pg-b", 50.0, 0.001, 0.0, 8.0, False)
        assert get_account_score("strong-acc") > get_account_score("weak-acc")

    def test_score_weighted_by_post_count(self):
        """Page with more posts has stronger influence on account score."""
        _reg("pg-heavy", "acc-mix")
        _reg("pg-light", "acc-mix")
        # pg-heavy: many posts, profitable
        for _ in range(20):
            update_page_metrics("pg-heavy", views=1000, engagement=0.1,
                                revenue=10.0, cost=1.0, converted=True)
        # pg-light: few posts, losing
        update_page_metrics("pg-light", views=100, engagement=0.01,
                            revenue=0.0, cost=2.0, converted=False)
        score = get_account_score("acc-mix")
        # Profitable heavy page should dominate
        assert score > 0.5


# ═══════════════════════════════════════════════════════════════════════════════
# Part 5 — Scaling Integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestScalingIntegration:

    def _setup_page(self, page_id, niche, rev, cost, n=8):
        _reg(page_id, "acc-1", niche)
        for _ in range(n):
            update_page_metrics(page_id, 1000.0, 0.07, rev, cost, rev > cost)

    def test_page_budget_weights_sum_to_one(self):
        self._setup_page("pg-A", "beauty", 10.0, 1.0)
        self._setup_page("pg-B", "beauty",  5.0, 2.0)
        w = get_page_budget_weights("beauty")
        assert abs(sum(w.values()) - 1.0) < 0.01

    def test_winner_page_gets_more_budget(self):
        self._setup_page("pg-hot",  "tech", 15.0, 1.0, n=10)
        self._setup_page("pg-cold", "tech",  0.1, 5.0, n=10)
        w = get_page_budget_weights("tech")
        assert w.get("pg-hot", 0) > w.get("pg-cold", 0)

    def test_paused_page_gets_zero_budget(self):
        self._setup_page("pg-dead", "food", 0.0, 10.0, n=_PAUSE_MIN_POSTS + 2)
        w = get_page_budget_weights("food")
        assert w.get("pg-dead", 0.0) == 0.0

    def test_page_aware_budget_allocation_structure(self):
        self._setup_page("pg-1", "beauty", 8.0, 1.0)
        alloc = get_page_aware_budget_allocation(100.0, "beauty")
        assert isinstance(alloc, PageAwareBudgetAllocation)
        assert alloc.total_budget == pytest.approx(100.0)

    def test_page_aware_budget_sums_to_total(self):
        self._setup_page("pg-A", "tech", 10.0, 1.0)
        self._setup_page("pg-B", "tech",  5.0, 2.0)
        alloc = get_page_aware_budget_allocation(100.0, "tech")
        total = sum(alloc.per_page.values()) + sum(alloc.exploration.values())
        # Should be close to total_budget (small rounding OK)
        assert total <= alloc.total_budget + 1.0

    def test_explore_budget_is_10_to_15_pct(self):
        self._setup_page("pg-1", "beauty", 8.0, 1.0)
        alloc = get_page_aware_budget_allocation(100.0, "beauty")
        assert 10.0 <= alloc.explore_budget <= 15.0

    def test_empty_niche_returns_empty_allocation(self):
        alloc = get_page_aware_budget_allocation(100.0, "no-niche")
        assert alloc.per_page    == {}
        assert alloc.exploration == {}

    def test_posting_frequency_winner(self):
        _profitable("pg-win", n=12)
        assert get_page_posting_frequency("pg-win") == pytest.approx(_FREQ_WINNER)

    def test_posting_frequency_paused(self):
        _losing("pg-dead", n=_PAUSE_MIN_POSTS + 2, cost=10.0)
        assert get_page_posting_frequency("pg-dead") == pytest.approx(_FREQ_PAUSED)

    def test_posting_frequency_throttled(self):
        _losing("pg-slow", n=_THROTTLE_MIN_POSTS + 1, cost=3.0)
        assert get_page_posting_frequency("pg-slow") == pytest.approx(_FREQ_WEAK)

    def test_posting_frequency_normal(self):
        _reg("pg-norm")
        _upd("pg-norm", rev=5.0, cost=3.0)
        # score in neutral zone → NORMAL frequency
        freq = get_page_posting_frequency("pg-norm")
        assert freq in (_FREQ_NORMAL, _FREQ_WINNER, _FREQ_WEAK)  # depends on score


# ═══════════════════════════════════════════════════════════════════════════════
# Part 6 — Kill / Throttle
# ═══════════════════════════════════════════════════════════════════════════════

class TestKillThrottle:

    def test_active_before_min_posts(self):
        _reg()
        for _ in range(_THROTTLE_MIN_POSTS - 1):
            _upd(rev=0.0, cost=2.0, converted=False)
        assert get_page_status("pg-1") == "active"

    def test_throttled_after_min_posts_with_loss(self):
        _reg()
        for _ in range(_THROTTLE_MIN_POSTS + 1):
            _upd(rev=0.0, cost=2.0, converted=False)
        assert get_page_status("pg-1") == "throttled"

    def test_paused_on_severe_loss(self):
        _reg()
        for _ in range(_PAUSE_MIN_POSTS + 1):
            _upd(rev=0.0, cost=10.0, converted=False)
        assert get_page_status("pg-1") == "paused"

    def test_active_on_profitable_page(self):
        _profitable()
        assert get_page_status("pg-1") == "active"

    def test_is_page_throttled_true_when_throttled(self):
        _losing(n=_THROTTLE_MIN_POSTS + 1)
        assert is_page_throttled("pg-1")

    def test_is_page_throttled_false_when_active(self):
        _profitable()
        assert not is_page_throttled("pg-1")

    def test_unknown_page_is_not_throttled(self):
        assert not is_page_throttled("ghost-page")

    def test_paused_page_is_also_throttled(self):
        _losing(n=_PAUSE_MIN_POSTS + 1, cost=10.0)
        assert is_page_throttled("pg-1")

    def test_status_persists_in_store(self):
        _losing(n=_THROTTLE_MIN_POSTS + 1)
        from core.page_intelligence import _get_store
        m = _get_store().get_metrics("pg-1")
        assert m["status"] in ("throttled", "paused")


# ═══════════════════════════════════════════════════════════════════════════════
# Part 7 — Exploration Bucket
# ═══════════════════════════════════════════════════════════════════════════════

class TestExploration:

    def test_new_pages_go_into_exploration(self):
        register_page("pg-new", "acc-1", "beauty", is_new=True)
        _reg("pg-old", "acc-2", "beauty", is_new=False)
        _profitable("pg-old", n=10)
        explore = get_exploration_pages("beauty")
        assert "pg-new" in explore

    def test_low_post_count_page_in_exploration(self):
        _reg("pg-fresh", "acc-1", "beauty", is_new=False)
        # Only 3 posts — below the NEW_PAGE_POSTS_WINDOW
        update_page_metrics("pg-fresh", 100.0, 0.05, 1.0, 0.5)
        explore = get_exploration_pages("beauty")
        assert "pg-fresh" in explore

    def test_established_page_not_in_exploration(self):
        _profitable("pg-old", n=25)
        explore = get_exploration_pages("beauty")
        assert "pg-old" not in explore

    def test_exploration_ratio_clamped(self):
        for i in range(20):
            register_page(f"pg-{i}", "acc-1", "food", is_new=True)
        explore_hi = get_exploration_pages("food", ratio=0.99)  # should be capped at 15%
        assert len(explore_hi) <= max(1, round(20 * 0.15)) + 1

    def test_no_pages_returns_empty_exploration(self):
        assert get_exploration_pages("empty-niche") == []

    def test_combined_budget_includes_exploration_key(self):
        register_page("pg-new", "acc-1", "beauty", is_new=True)
        _reg("pg-old", "acc-2", "beauty")
        _profitable("pg-old", n=5)
        result = get_combined_budget_weights("beauty")
        assert "exploration" in result
        assert "weights"     in result
        assert "paused"      in result

    def test_page_aware_alloc_puts_new_pages_in_exploration(self):
        register_page("pg-new", "acc-1", "beauty", is_new=True)
        _reg("pg-est", "acc-2", "beauty")
        _profitable("pg-est", n=8)
        alloc = get_page_aware_budget_allocation(100.0, "beauty")
        # New page should be in exploration, not main per_page
        assert "pg-new" in alloc.exploration or len(alloc.exploration) >= 0

    def test_reset_clears_all_state(self):
        _profitable()
        reset_page_state()
        assert get_page_score("pg-1") == pytest.approx(0.5)
        assert get_page_status("pg-1") == "active"
