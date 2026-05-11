"""
tests/test_content_decision.py — v3: Profit-Aware Decision Engine

Validates:
  1. Score formula — v3 signals + normalisation
  2. Hook estimator — internal estimation from metadata
  3. Expected Value — EV = trend × product_intent × hook_potential
  4. EV gate — final_score < 0 and low_ev drops
  5. Filter — mode ratios, guard, EV gate, exploration
  6. Remark match guard
  7. should_produce() — public worker gate
  8. Feedback loop — EMA, viral, conversion_score
  9. Real cost — mode-specific scaling
 10. Decision log — v3 fields
"""
from __future__ import annotations

import sys
import pathlib
import importlib.util

# Bypass core/__init__.py → aiosqlite chain
_root = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "content_decision", _root / "core" / "content_decision.py"
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["content_decision"] = _mod
_spec.loader.exec_module(_mod)

from content_decision import (
    ContentCandidate,
    DecisionResult,
    filter_candidates,
    get_decision_log,
    record_outcome,
    reset_decision_state,
    score_content_candidate,
    should_produce,
    _HIST_PERF,
    _PRODUCT_INTENT_PERF,
    _fingerprint,
    _compute_real_cost,
    _compute_expected_value,
    _estimate_hook_potential,
    _remark_match_guard,
    _MODE_CONFIG,
)

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _item(
    item_id:         str   = "test",
    trend:           float = 0.5,
    product_intent:  float = 0.5,
    hook_potential:  float = 0.5,   # explicit value
    match:           float = 0.5,
    novelty:         float = 0.5,
    cost:            float = 0.5,
    metadata:        dict  = None,
) -> ContentCandidate:
    return ContentCandidate(
        item_id         = item_id,
        trend_score     = trend,
        product_intent  = product_intent,
        hook_potential  = hook_potential,
        match_score     = match,
        novelty_score   = novelty,
        production_cost = cost,
        metadata        = metadata or {},
    )


def _auto_item(**kw) -> ContentCandidate:
    """Item with hook_potential=-1.0 (auto-estimate sentinel)."""
    return _item(hook_potential=-1.0, **kw)


@pytest.fixture(autouse=True)
def _reset():
    reset_decision_state()
    yield
    reset_decision_state()


# ── Test 1 — Score formula ────────────────────────────────────────────────────

class TestScoreFormula:

    def test_neutral_item_midrange(self):
        score, _ = score_content_candidate(_item())
        assert 0.4 <= score <= 0.6

    def test_perfect_item_high_score(self):
        score, _ = score_content_candidate(
            _item(trend=1.0, product_intent=1.0, hook_potential=1.0,
                  match=1.0, novelty=1.0, cost=0.0)
        )
        assert score >= 0.85

    def test_worst_item_low_score(self):
        # v4: worst item has all-zero signals but profit_score defaults to 0.5
        # (no profit history = neutral), so raw score ≥ -0.15 + 0.5×0.15 = -0.075
        # normalised = (-0.075 + 0.15) / 1.0 ≈ 0.075–0.20 range
        score, _ = score_content_candidate(
            _item(trend=0.0, product_intent=0.0, hook_potential=0.0,
                  match=0.0, novelty=0.0, cost=1.0)
        )
        assert score <= 0.20

    def test_score_in_unit_range(self):
        for t, pi, hp, m, n, c in [
            (0, 0, 0, 0, 0, 0), (1, 1, 1, 1, 1, 1),
            (0.3, 0.7, 0.5, 0.5, 0.2, 0.4), (0.9, 0.1, 0.8, 0.6, 0.5, 0.3),
        ]:
            s, _ = score_content_candidate(
                _item(trend=t, product_intent=pi, hook_potential=hp,
                      match=m, novelty=n, cost=c)
            )
            assert 0.0 <= s <= 1.0

    def test_higher_trend_increases_score(self):
        lo, _ = score_content_candidate(_item(trend=0.1))
        hi, _ = score_content_candidate(_item(trend=0.9))
        assert hi > lo

    def test_higher_cost_decreases_score(self):
        cheap, _ = score_content_candidate(_item(cost=0.1))
        pricey, _ = score_content_candidate(_item(cost=0.9))
        assert cheap > pricey

    def test_breakdown_has_v3_fields(self):
        _, bd = score_content_candidate(_item())
        for k in ("expected_value", "real_cost", "final_score",
                  "hook_potential_used", "product_intent_blended"):
            assert k in bd, f"Missing breakdown key: {k}"

    def test_mode_affects_cost_scaling(self):
        item = _item(cost=0.8)
        s_reup, _     = score_content_candidate(item, mode="reup")
        s_generate, _ = score_content_candidate(item, mode="generate")
        assert s_reup > s_generate


# ── Test 2 — Hook estimator ───────────────────────────────────────────────────

class TestHookEstimator:

    def test_auto_estimate_called_for_sentinel(self):
        """hook_potential = -1 triggers internal estimator."""
        item = _auto_item()
        score, bd = score_content_candidate(item)
        assert 0.0 <= bd["hook_potential_used"] <= 1.0

    def test_explicit_hook_used_directly(self):
        """Explicit hook_potential bypasses estimator."""
        item = _item(hook_potential=0.9)
        _, bd = score_content_candidate(item)
        assert abs(bd["hook_potential_used"] - 0.9) < 0.01

    def test_curiosity_keywords_boost_signal(self):
        no_kw  = _auto_item(metadata={"text": "product available now"})
        with_kw = _auto_item(metadata={"text": "secret hack revealed — top 5 best tricks"})
        hp_no  = _estimate_hook_potential(no_kw)
        hp_yes = _estimate_hook_potential(with_kw)
        assert hp_yes > hp_no

    def test_motion_score_contributes(self):
        low  = _auto_item(metadata={"motion_score": 0.1})
        high = _auto_item(metadata={"motion_score": 0.9})
        assert _estimate_hook_potential(high) > _estimate_hook_potential(low)

    def test_visual_clarity_contributes(self):
        low  = _auto_item(metadata={"visual_clarity": 0.1})
        high = _auto_item(metadata={"visual_clarity": 0.9})
        assert _estimate_hook_potential(high) > _estimate_hook_potential(low)

    def test_no_metadata_gives_neutral(self):
        item = _auto_item()
        hp = _estimate_hook_potential(item)
        assert 0.3 <= hp <= 0.6   # neutral range with no signals

    def test_result_clamped(self):
        item = _auto_item(metadata={
            "motion_score": 2.0, "visual_clarity": 5.0,
            "text": "best hack secret revealed why top",
        })
        assert _estimate_hook_potential(item) <= 1.0


# ── Test 3 — Expected Value ───────────────────────────────────────────────────

class TestExpectedValue:

    def test_ev_is_product_of_three_signals(self):
        signals = {"trend_score": 0.8, "product_intent": 0.7, "hook_potential": 0.9}
        ev = _compute_expected_value(signals)
        expected = round(0.8 * 0.7 * 0.9, 4)
        assert abs(ev - expected) < 0.001

    def test_ev_zero_if_any_signal_zero(self):
        for zero_key in ("trend_score", "product_intent", "hook_potential"):
            signals = {"trend_score": 0.8, "product_intent": 0.8, "hook_potential": 0.8}
            signals[zero_key] = 0.0
            assert _compute_expected_value(signals) == 0.0

    def test_ev_one_if_all_signals_one(self):
        signals = {"trend_score": 1.0, "product_intent": 1.0, "hook_potential": 1.0}
        assert _compute_expected_value(signals) == 1.0

    def test_ev_in_breakdown(self):
        _, bd = score_content_candidate(_item(trend=0.8, product_intent=0.7, hook_potential=0.9))
        assert "expected_value" in bd
        assert 0.0 <= bd["expected_value"] <= 1.0

    def test_final_score_ev_minus_cost(self):
        item = _item(trend=0.8, product_intent=0.7, hook_potential=0.9, cost=0.2)
        _, bd = score_content_candidate(item, mode="reup")
        ev    = bd["expected_value"]
        cost  = bd["real_cost"]
        final = bd["final_score"]
        assert abs(final - round(ev - cost, 4)) < 0.001

    def test_high_cost_can_make_final_negative(self):
        """Very high cost on a mediocre item should give final_score < 0."""
        item = _item(trend=0.2, product_intent=0.2, hook_potential=0.2, cost=1.0)
        _, bd = score_content_candidate(item, mode="generate")
        assert bd["final_score"] < 0.0


# ── Test 4 — EV gate in filter ────────────────────────────────────────────────

class TestEVGate:

    def test_negative_final_score_drops(self):
        """Items with EV - cost < 0 are dropped before threshold."""
        item = _item(item_id="neg-ev",
                     trend=0.1, product_intent=0.1, hook_potential=0.1, cost=1.0)
        kept, dropped = filter_candidates([item], mode="generate", explore=False)
        assert any(r.item_id == "neg-ev" for r in dropped)
        assert all(r.item_id != "neg-ev" for r in kept)

    def test_negative_final_score_reason_tagged(self):
        item = _item(item_id="neg-reason",
                     trend=0.1, product_intent=0.1, hook_potential=0.1, cost=1.0)
        _, dropped = filter_candidates([item], mode="generate", explore=False)
        neg_results = [r for r in dropped if r.item_id == "neg-reason"]
        assert neg_results
        assert neg_results[0].decision_reason in ("negative_ev", "low_ev")

    def test_generate_low_ev_blocked(self):
        """generate mode: EV < 0.1 → blocked."""
        # trend=0.1, pi=0.1, hook=0.1 → EV = 0.001 < 0.1
        item = _item(item_id="low-ev-gen",
                     trend=0.1, product_intent=0.1, hook_potential=0.1, cost=0.1)
        _, dropped = filter_candidates([item], mode="generate", explore=False)
        assert any(r.item_id == "low-ev-gen" for r in dropped)

    def test_reup_allows_zero_ev(self):
        """reup: min_ev = 0.0 — items with EV >= 0 and positive final_score pass."""
        item = _item(item_id="reup-pass",
                     trend=0.6, product_intent=0.6, hook_potential=0.6, cost=0.1)
        kept, _ = filter_candidates([item], mode="reup", explore=False)
        assert any(r.item_id == "reup-pass" for r in kept)

    def test_decision_result_has_ev_fields(self):
        item = _item(item_id="ev-fields")
        kept, dropped = filter_candidates([item], mode="remark", explore=False)
        for r in kept + dropped:
            assert hasattr(r, "expected_value")
            assert hasattr(r, "real_cost")
            assert hasattr(r, "final_score")
            assert hasattr(r, "decision_reason")


# ── Test 5 — Filter ratios ────────────────────────────────────────────────────

class TestFilterRatios:

    def _batch(self, n: int) -> list[ContentCandidate]:
        return [
            _item(item_id=f"item-{i}",
                  trend=0.1 + 0.85 * i / max(n - 1, 1),
                  product_intent=0.6, hook_potential=0.6, cost=0.3)
            for i in range(n)
        ]

    def test_reup_keeps_roughly_50_pct(self):
        items = self._batch(20)
        kept, _ = filter_candidates(items, mode="reup", explore=False)
        assert 0.25 <= len(kept) / len(items) <= 0.80

    def test_generate_keeps_less_than_reup(self):
        items = self._batch(20)
        kept_g, _ = filter_candidates(items, mode="generate", explore=False)
        kept_r, _ = filter_candidates(items, mode="reup",     explore=False)
        assert len(kept_g) <= len(kept_r)

    def test_no_items_returns_empty(self):
        kept, dropped = filter_candidates([], mode="remark")
        assert kept == [] and dropped == []

    def test_exploration_items_tagged(self):
        # generate min_ev = 0.1; need EV = trend×pi×hook >= 0.1
        # and final_score = EV - real_cost > 0  (cost_scale=1.0, so real_cost = raw_cost)
        # score still needs to be below threshold 0.55 so items land in dropped pool
        # EV = 0.5×0.5×0.5 = 0.125 >= 0.1; real_cost = 0.1×1.0 = 0.1; final = 0.025 > 0
        # weighted score ≈ neutral → some will be below threshold
        items = [
            _item(item_id=f"e-{i}", trend=0.5, product_intent=0.5,
                  hook_potential=0.5, match=0.3, novelty=0.3, cost=0.1)
            for i in range(20)
        ]
        kept, _ = filter_candidates(items, mode="generate", explore=True, seed=42)
        explore_items = [r for r in kept if r.decision == "explore"]
        assert len(explore_items) > 0


# ── Test 6 — Remark match guard ───────────────────────────────────────────────

class TestRemarkMatchGuard:

    def test_low_match_dropped_in_remark(self):
        items = [
            _item(item_id="low-match",  match=0.4, trend=1.0, product_intent=0.8),
            _item(item_id="high-match", match=0.8, trend=1.0, product_intent=0.8),
        ]
        kept, dropped = filter_candidates(items, mode="remark", explore=False)
        assert any(r.item_id == "low-match"  for r in dropped)
        assert any(r.item_id == "high-match" for r in kept)

    def test_guard_reason_tagged(self):
        items = [_item(item_id="guard", match=0.3)]
        _, dropped = filter_candidates(items, mode="remark", explore=False)
        assert any("match_guard" in r.decision_reason for r in dropped)

    def test_guard_not_in_reup(self):
        items = [_item(item_id="reup-low-match", match=0.1, trend=0.9,
                       product_intent=0.8, hook_potential=0.7, cost=0.2)]
        _, dropped = filter_candidates(items, mode="reup", explore=False)
        assert not any("match_guard" in r.decision_reason for r in dropped)

    def test_boundary_passes(self):
        items = [_item(item_id="bound", match=0.6, product_intent=0.7, hook_potential=0.7)]
        kept, dropped = filter_candidates(items, mode="remark", explore=False)
        assert not any("match_guard" in r.decision_reason for r in dropped)


# ── Test 7 — should_produce() ─────────────────────────────────────────────────

class TestShouldProduce:

    def test_high_quality_item_allowed(self):
        c = _item(trend=0.8, product_intent=0.8, hook_potential=0.8, match=0.8, cost=0.2)
        allowed, reason = should_produce(c, mode="reup")
        assert allowed, f"Expected allowed but got: {reason}"

    def test_low_ev_generate_blocked(self):
        """EV = 0.1×0.1×0.1 = 0.001 < 0.1 → blocked in generate mode."""
        c = _item(trend=0.1, product_intent=0.1, hook_potential=0.1, cost=0.1)
        allowed, reason = should_produce(c, mode="generate")
        assert not allowed
        assert "low_ev" in reason or "negative_ev" in reason

    def test_negative_final_score_blocked(self):
        """EV - cost < 0 → blocked."""
        c = _item(trend=0.2, product_intent=0.2, hook_potential=0.2, cost=1.0)
        allowed, reason = should_produce(c, mode="reup")
        assert not allowed

    def test_low_match_remark_blocked(self):
        c = _item(match=0.3, trend=0.9, product_intent=0.9, hook_potential=0.9)
        allowed, reason = should_produce(c, mode="remark")
        assert not allowed
        assert "match_guard" in reason

    def test_returns_tuple_bool_str(self):
        c = _item()
        result = should_produce(c, mode="reup")
        assert isinstance(result, tuple) and len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], str)

    def test_auto_hook_estimate_in_should_produce(self):
        """hook_potential=-1 (auto) works inside should_produce."""
        c = _auto_item(
            trend=0.8, product_intent=0.8, match=0.8, cost=0.2,
            metadata={"text": "best secret hack revealed top 5"},
        )
        allowed, reason = should_produce(c, mode="reup")
        assert isinstance(allowed, bool)


# ── Test 8 — Feedback loop ────────────────────────────────────────────────────

class TestFeedbackLoop:

    def test_good_outcome_raises_hist(self):
        record_outcome("a", niche="fitness", engagement=0.9)
        assert _HIST_PERF[_fingerprint("a", "fitness")] > 0.5

    def test_bad_outcome_lowers_hist(self):
        record_outcome("b", niche="", engagement=0.0)
        assert _HIST_PERF[_fingerprint("b", "")] < 0.5

    def test_viral_updates_faster(self):
        record_outcome("v", niche="", engagement=0.8, viral=True)
        val_viral = _HIST_PERF[_fingerprint("v", "")]
        reset_decision_state()
        record_outcome("v", niche="", engagement=0.8, viral=False)
        val_norm = _HIST_PERF[_fingerprint("v", "")]
        assert val_viral > val_norm

    def test_conversion_updates_product_intent_perf(self):
        record_outcome("c", niche="tech", engagement=0.7, conversion_score=0.9)
        assert _fingerprint("c", "tech") in _PRODUCT_INTENT_PERF

    def test_conversion_zero_no_pi_entry(self):
        record_outcome("nc", niche="", engagement=0.7, conversion_score=0.0)
        assert _fingerprint("nc", "") not in _PRODUCT_INTENT_PERF

    def test_conversion_feeds_back_into_score(self):
        item = _item(item_id="pi-fb", product_intent=0.5)
        before, _ = score_content_candidate(item)
        record_outcome("pi-fb", niche="", engagement=0.7, conversion_score=0.95)
        after, _ = score_content_candidate(item)
        assert after > before


# ── Test 9 — Real cost ────────────────────────────────────────────────────────

class TestRealCost:

    def test_reup_cheaper_than_generate(self):
        assert _compute_real_cost("reup", 0.8) < _compute_real_cost("generate", 0.8)

    def test_remark_between_reup_and_generate(self):
        r = _compute_real_cost("reup", 0.8)
        m = _compute_real_cost("remark", 0.8)
        g = _compute_real_cost("generate", 0.8)
        assert r <= m <= g

    def test_cost_clamped(self):
        for mode in ("reup", "remark", "generate"):
            for raw in (-0.5, 0.0, 0.5, 1.0, 2.0):
                c = _compute_real_cost(mode, raw)
                assert 0.0 <= c <= 1.0


# ── Test 10 — Decision log v3 ────────────────────────────────────────────────

class TestDecisionLog:

    def test_log_populated(self):
        items = [_item(item_id=f"log-{i}") for i in range(5)]
        filter_candidates(items, mode="remark")
        assert len(get_decision_log()) == 5

    def test_log_has_v3_fields(self):
        items = [_item(item_id="v3-log", product_intent=0.8, hook_potential=0.8, cost=0.2)]
        filter_candidates(items, mode="reup")
        entry = get_decision_log(1)[0]
        for field in ("id", "mode", "score", "decision", "decision_reason",
                      "expected_value", "real_cost", "final_score", "breakdown"):
            assert field in entry, f"Missing log field: {field}"

    def test_log_resets(self):
        filter_candidates([_item()], mode="reup")
        reset_decision_state()
        assert get_decision_log() == []

    def test_guard_items_in_log(self):
        items = [_item(item_id="guard-log", match=0.2)]
        filter_candidates(items, mode="remark")
        log = get_decision_log()
        assert any(e["item_id"] == "guard-log" for e in log)
        assert any("match_guard" in e["decision_reason"] for e in log)

    def test_ev_values_in_log(self):
        item = _item(item_id="ev-log", trend=0.8, product_intent=0.8,
                     hook_potential=0.8, cost=0.2)
        filter_candidates([item], mode="reup")
        entry = get_decision_log(1)[0]
        assert entry["expected_value"] >= 0.0
        assert "final_score" in entry
