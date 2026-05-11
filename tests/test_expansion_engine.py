"""
tests/test_expansion_engine.py — Auto Expansion Engine Test Suite

Parts tested:
  Part 1 — evaluate_expansion_candidates (trigger thresholds)
  Part 2 — create_expansion_plan (clone/geo/segment)
  Part 3 — Geo/market variants
  Part 4 — Risk controls (daily limit, budget cap)
  Part 5 — Feedback loop (record, win/lose, merge, kill)
"""
from __future__ import annotations

import os
import sys
import pathlib
import importlib.util

os.environ["EXPANSION_STATE_DB"]    = ":memory:"
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

_load("core.profit_store",        "core/profit_store.py")
_load("core.profit_engine",       "core/profit_engine.py")
_load("core.attribution_store",   "core/attribution_store.py")
_load("core.attribution_engine",  "core/attribution_engine.py")
_load("core.product_intelligence","core/product_intelligence.py")
_load("core.page_intelligence",   "core/page_intelligence.py")
_load("core.content_decision",    "core/content_decision.py")
_load("core.self_scaling",        "core/self_scaling.py")
_load("core.expansion_engine",    "core/expansion_engine.py")

from core.expansion_engine import (
    evaluate_expansion_candidates, create_expansion_plan,
    record_expansion_result, should_kill_expansion, merge_expansion_page,
    kill_expansion_page, get_expansion_page_info, get_expansion_metrics,
    get_expansion_log, get_daily_expansion_count, reset_expansion_state,
    ExpansionCandidate, ExpansionPlan,
    EXPANSION_PRODUCT_SCORE_MIN, EXPANSION_PAGE_SCORE_MIN,
    EXPANSION_STABLE_CYCLES, MAX_NEW_PAGES_PER_DAY, MAX_BUDGET_PER_EXPAND,
    EXPANSION_KILL_POSTS, EXPANSION_WIN_SCORE, EXPANSION_LOSE_SCORE,
)
from core.page_intelligence import (
    register_page, update_page_metrics, get_page_score, reset_page_state,
)
from core.product_intelligence import (
    register_product, register_content_product, update_product_metrics,
    get_product_score, reset_product_state,
)
from core.profit_engine import reset_profit_state
from core.self_scaling   import reset_scaling_state

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset():
    reset_expansion_state()
    reset_page_state()
    reset_product_state()
    reset_profit_state()
    reset_scaling_state()
    yield
    reset_expansion_state()
    reset_page_state()
    reset_product_state()
    reset_profit_state()
    reset_scaling_state()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_winning_page(page_id="pg-win", niche="beauty", product_id="p-serum", n=15):
    """Create a page+product combo that qualifies for expansion."""
    register_product(product_id, category="skincare", trend=0.8)
    register_content_product(page_id, product_id)
    register_page(page_id, "acc-1", niche, is_new=False)
    for _ in range(n):
        update_page_metrics(page_id, views=2000.0, engagement=200.0,
                            revenue=20.0, cost=1.0, converted=True)
        update_product_metrics(product_id, 20.0, 1.0, True, False, 0.85)
    return page_id, product_id


def _make_candidate(page_id="pg-win", niche="beauty", product_id="p-serum") -> ExpansionCandidate:
    return ExpansionCandidate(
        source_page_id = page_id,
        product_id     = product_id,
        niche          = niche,
        product_score  = 0.80,
        page_score     = 0.75,
        stable_cycles  = EXPANSION_STABLE_CYCLES,
        geo_variants   = ["vi", "en", "id"],
        audience_segs  = ["18-24", "25-34"],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Part 1 — Expansion Candidate Evaluation
# ═══════════════════════════════════════════════════════════════════════════════

class TestCandidateEvaluation:

    def test_no_pages_returns_empty(self):
        result = evaluate_expansion_candidates("empty-niche")
        assert result == []

    def test_winning_combo_detected(self):
        _make_winning_page()
        # Verify each qualifying condition directly before testing evaluate
        from core.page_intelligence import get_page_score, get_page_status
        from core.product_intelligence import get_product_score_for_content
        from core.expansion_engine import _count_stable_cycles

        ps = get_page_score("pg-win")
        assert ps >= EXPANSION_PAGE_SCORE_MIN, f"page_score too low: {ps}"
        assert get_page_status("pg-win") == "active"

        prod_s = get_product_score_for_content("pg-win")
        assert prod_s >= EXPANSION_PRODUCT_SCORE_MIN, f"product_score too low: {prod_s}"

        stable = _count_stable_cycles("pg-win")
        assert stable >= EXPANSION_STABLE_CYCLES, f"stable cycles too low: {stable}"

        candidates = evaluate_expansion_candidates("beauty")
        assert len(candidates) >= 1, (
            f"Expected candidate but got 0. "
            f"page_score={ps:.3f} prod_score={prod_s:.3f} stable={stable}"
        )

    def test_candidate_has_required_fields(self):
        _make_winning_page()
        cands = evaluate_expansion_candidates("beauty")
        if cands:
            c = cands[0]
            assert hasattr(c, "source_page_id")
            assert hasattr(c, "product_score")
            assert hasattr(c, "page_score")
            assert hasattr(c, "stable_cycles")

    def test_weak_page_not_candidate(self):
        register_page("pg-weak", "acc-1", "tech")
        for _ in range(5):
            update_page_metrics("pg-weak", 100.0, 1.0, 0.0, 5.0, False)
        cands = evaluate_expansion_candidates("tech")
        ids = [c.source_page_id for c in cands]
        assert "pg-weak" not in ids

    def test_candidates_sorted_by_score_desc(self):
        _make_winning_page("pg-a", "beauty", "p-a", n=20)
        _make_winning_page("pg-b", "beauty", "p-b", n=15)
        cands = evaluate_expansion_candidates("beauty")
        if len(cands) >= 2:
            scores = [c.product_score + c.page_score for c in cands]
            assert scores == sorted(scores, reverse=True)

    def test_paused_page_excluded(self):
        _make_winning_page()
        # Force pause by adding big losses on top
        for _ in range(15):
            update_page_metrics("pg-win", 0.0, 0.0, 0.0, 100.0, False)
        cands = evaluate_expansion_candidates("beauty")
        ids = [c.source_page_id for c in cands]
        assert "pg-win" not in ids


# ═══════════════════════════════════════════════════════════════════════════════
# Part 2 — Expansion Plan Creation
# ═══════════════════════════════════════════════════════════════════════════════

class TestExpansionPlan:

    def test_create_plan_returns_plan(self):
        c = _make_candidate()
        plan = create_expansion_plan(c)
        assert isinstance(plan, ExpansionPlan)
        assert not plan.blocked

    def test_plan_has_new_page_id(self):
        c = _make_candidate()
        plan = create_expansion_plan(c)
        assert len(plan.new_page_ids) == 1
        assert plan.new_page_ids[0].startswith("pg-win_exp_")

    def test_plan_registers_expansion_page(self):
        c = _make_candidate()
        plan = create_expansion_plan(c)
        pid = plan.new_page_ids[0]
        info = get_expansion_page_info(pid)
        assert info is not None
        assert info["source_page_id"] == "pg-win"
        assert info["niche"] == "beauty"

    def test_clone_strategy(self):
        c = _make_candidate()
        plan = create_expansion_plan(c, strategy="clone")
        assert plan.strategy == "clone"

    def test_budget_within_max(self):
        c = _make_candidate()
        plan = create_expansion_plan(c)
        assert plan.budget_per_page <= MAX_BUDGET_PER_EXPAND

    def test_budget_proportional_to_scores(self):
        high = _make_candidate()
        high.product_score = 0.95
        high.page_score    = 0.90
        low  = _make_candidate("pg-2", "tech", "p-2")
        low.product_score  = 0.71
        low.page_score     = 0.66
        reset_expansion_state()
        plan_hi = create_expansion_plan(high)
        plan_lo = create_expansion_plan(low)
        assert plan_hi.budget_per_page >= plan_lo.budget_per_page

    def test_daily_count_increments(self):
        c = _make_candidate()
        before = get_daily_expansion_count()
        create_expansion_plan(c)
        assert get_daily_expansion_count() == before + 1

    def test_plan_logged(self):
        c = _make_candidate()
        create_expansion_plan(c)
        log = get_expansion_log(10)
        assert any(e["event"] == "expansion_created" for e in log)


# ═══════════════════════════════════════════════════════════════════════════════
# Part 3 — Geo / Market Expansion
# ═══════════════════════════════════════════════════════════════════════════════

class TestGeoExpansion:

    def test_geo_strategy_sets_variant(self):
        c = _make_candidate()
        plan = create_expansion_plan(c, strategy="geo", geo_variant="vi")
        assert plan.geo_variant == "vi"
        assert plan.strategy == "geo"

    def test_geo_auto_selects_first_variant(self):
        c = _make_candidate()
        plan = create_expansion_plan(c, strategy="geo")
        assert plan.geo_variant in c.geo_variants

    def test_segment_strategy_sets_audience(self):
        c = _make_candidate()
        plan = create_expansion_plan(c, strategy="segment", audience_seg="18-24")
        assert plan.audience_seg == "18-24"
        assert plan.strategy == "segment"

    def test_segment_auto_selects_first_segment(self):
        c = _make_candidate()
        plan = create_expansion_plan(c, strategy="segment")
        assert plan.audience_seg in c.audience_segs

    def test_expansion_page_stores_geo_variant(self):
        c = _make_candidate()
        plan = create_expansion_plan(c, strategy="geo", geo_variant="en")
        info = get_expansion_page_info(plan.new_page_ids[0])
        assert info["geo_variant"] == "en"


# ═══════════════════════════════════════════════════════════════════════════════
# Part 4 — Risk Controls
# ═══════════════════════════════════════════════════════════════════════════════

class TestRiskControls:

    def test_daily_limit_blocks_after_max(self):
        for i in range(MAX_NEW_PAGES_PER_DAY):
            c = _make_candidate(f"pg-{i}", "beauty", f"p-{i}")
            plan = create_expansion_plan(c)
            assert not plan.blocked, f"Plan {i} blocked early"

        # Next one should be blocked
        c = _make_candidate("pg-extra", "beauty", "p-extra")
        plan = create_expansion_plan(c)
        assert plan.blocked
        assert "daily_limit" in plan.block_reason

    def test_blocked_plan_has_empty_pages(self):
        for i in range(MAX_NEW_PAGES_PER_DAY):
            create_expansion_plan(_make_candidate(f"pg-{i}", "beauty", f"p-{i}"))
        plan = create_expansion_plan(_make_candidate("pg-x", "beauty", "p-x"))
        assert plan.new_page_ids == []

    def test_budget_never_exceeds_max(self):
        c = _make_candidate()
        c.product_score = 1.0
        c.page_score    = 1.0
        plan = create_expansion_plan(c)
        assert plan.budget_per_page <= MAX_BUDGET_PER_EXPAND


# ═══════════════════════════════════════════════════════════════════════════════
# Part 5 — Feedback Loop
# ═══════════════════════════════════════════════════════════════════════════════

class TestFeedbackLoop:

    def _create_page(self, strategy="clone"):
        c    = _make_candidate()
        plan = create_expansion_plan(c, strategy=strategy)
        return plan.new_page_ids[0] if plan.new_page_ids else "fallback-pg"

    def test_record_result_accumulates(self):
        pid = self._create_page()
        record_expansion_result(pid, revenue=10.0, cost=2.0)
        record_expansion_result(pid, revenue=5.0,  cost=1.0)
        m = get_expansion_metrics(pid)
        assert m["total_revenue"] == pytest.approx(15.0)
        assert m["profit"]        == pytest.approx(12.0)

    def test_status_tracking_before_kill_posts(self):
        pid = self._create_page()
        for _ in range(EXPANSION_KILL_POSTS - 1):
            record_expansion_result(pid, revenue=0.0, cost=1.0)
        m = get_expansion_metrics(pid)
        assert m["status"] == "tracking"

    def test_status_becomes_losing_after_kill_posts(self):
        pid = self._create_page()
        for _ in range(EXPANSION_KILL_POSTS + 1):
            record_expansion_result(pid, revenue=0.0, cost=2.0)
        m = get_expansion_metrics(pid)
        assert m["status"] == "losing"

    def test_status_becomes_winning_with_profit(self):
        pid = self._create_page()
        for _ in range(EXPANSION_KILL_POSTS + 1):
            record_expansion_result(pid, revenue=10.0, cost=1.0)
        m = get_expansion_metrics(pid)
        assert m["status"] == "winning"

    def test_should_kill_losing_page(self):
        pid = self._create_page()
        for _ in range(EXPANSION_KILL_POSTS + 1):
            record_expansion_result(pid, revenue=0.0, cost=5.0)
        assert should_kill_expansion(pid)

    def test_should_not_kill_winning_page(self):
        pid = self._create_page()
        for _ in range(EXPANSION_KILL_POSTS + 1):
            record_expansion_result(pid, revenue=20.0, cost=1.0)
        assert not should_kill_expansion(pid)

    def test_merge_winning_page(self):
        pid = self._create_page()
        for _ in range(EXPANSION_KILL_POSTS + 1):
            record_expansion_result(pid, revenue=20.0, cost=1.0)
        result = merge_expansion_page(pid)
        assert result is True
        info = get_expansion_page_info(pid)
        assert info["status"] == "merged"

    def test_merge_logs_event(self):
        pid = self._create_page()
        for _ in range(EXPANSION_KILL_POSTS + 1):
            record_expansion_result(pid, revenue=20.0, cost=1.0)
        merge_expansion_page(pid)
        log = get_expansion_log(20)
        assert any(e["event"] == "expansion_merged" and e["page_id"] == pid for e in log)

    def test_merge_failing_page_returns_false(self):
        pid = self._create_page()
        for _ in range(EXPANSION_KILL_POSTS + 1):
            record_expansion_result(pid, revenue=0.0, cost=5.0)
        result = merge_expansion_page(pid)
        assert result is False

    def test_kill_expansion_page(self):
        pid = self._create_page()
        result = kill_expansion_page(pid)
        assert result is True
        info = get_expansion_page_info(pid)
        assert info["status"] == "killed"

    def test_kill_logs_event(self):
        pid = self._create_page()
        kill_expansion_page(pid)
        log = get_expansion_log(10)
        assert any(e["event"] == "expansion_killed" and e["page_id"] == pid for e in log)

    def test_kill_unknown_page_returns_false(self):
        assert kill_expansion_page("ghost-page") is False

    def test_cycle_profits_tracked(self):
        pid = self._create_page()
        for i in range(5):
            record_expansion_result(pid, revenue=float(i), cost=1.0)
        m = get_expansion_metrics(pid)
        assert isinstance(m["cycle_profits"], list)
        assert len(m["cycle_profits"]) == 5

    def test_cycle_profits_capped_at_20(self):
        pid = self._create_page()
        for _ in range(25):
            record_expansion_result(pid, revenue=2.0, cost=1.0)
        m = get_expansion_metrics(pid)
        assert len(m["cycle_profits"]) <= 20

    def test_reset_clears_all_state(self):
        pid = self._create_page()
        record_expansion_result(pid, revenue=5.0, cost=1.0)
        reset_expansion_state()
        assert get_expansion_metrics(pid) is None
        assert get_expansion_page_info(pid) is None
        assert get_daily_expansion_count() == 0
