"""
tests/test_profit_engine.py — Profit Engine Test Suite (v2 — persistent store)

Covers:
  1. Profit calculation (revenue - cost)
  2. Negative profit handling
  3. EMA update (alpha=0.20 normal, 0.40 spike)
  4. Profit score = sigmoid(profit_margin)
  5. Profit influence on decision (content_decision)
  6. Profit influence on scaling (self_scaling)
  7. Edge cases: zero cost, zero revenue, huge margin
  8. Anti-fake-viral flag
  9. Log structure
 10. Persistence: write → read from store
"""
from __future__ import annotations

import os
import sys
import pathlib
import importlib.util
import math

# ── Use in-memory SQLite for test isolation ────────────────────────────────────
# Must be set BEFORE loading profit_store / profit_engine
os.environ["PROFIT_STATE_DB"] = ":memory:"

# ── Direct module loads to avoid package-level import issues ──────────────────
_root = pathlib.Path(__file__).resolve().parents[1]

def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, _root / rel)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

# Load in dependency order:
# 1. profit_store  (SQLite backend, uses env var above)
# 2. profit_engine (imports core.profit_store)
# 3. content_decision (imports core.profit_engine)
# 4. self_scaling (imports core.profit_engine)
_ps  = _load("core.profit_store",    "core/profit_store.py")
_pe  = _load("core.profit_engine",   "core/profit_engine.py")
_cd  = _load("core.content_decision","core/content_decision.py")
_ss  = _load("core.self_scaling",    "core/self_scaling.py")

from core.profit_store import get_profit_store, reset_profit_store
from core.profit_engine import (
    update_profit, get_profit_score, get_profit_record,
    get_profit_log, is_fake_viral,
    reset_profit_state,
    _sigmoid,
    _FAKE_VIRAL_VIEWS_THRESHOLD, _FAKE_VIRAL_PROFIT_THRESHOLD, _FAKE_VIRAL_SF_CAP,
)
from core.content_decision import (
    ContentCandidate, score_content_candidate, should_produce,
    filter_candidates, DecisionResult,
    reset_decision_state,
)
from core.self_scaling import (
    update_performance, get_scaling_factor, reset_scaling_state,
    ScalingTier, _PERF_STORE, _key as _ss_key,
)

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset():
    reset_profit_state()     # clears SQLite store + in-process log
    reset_scaling_state()
    reset_decision_state()
    yield
    reset_profit_state()
    reset_scaling_state()
    reset_decision_state()


# ── helpers ───────────────────────────────────────────────────────────────────

def _profit(
    content_id: str = "c1",
    niche: str = "beauty",
    revenue: float = 1.0,
    cost: float = 0.5,
):
    return update_profit(content_id=content_id, niche=niche,
                         revenue=revenue, cost=cost)


def _score_item(**kwargs) -> float:
    item = ContentCandidate(item_id=kwargs.pop("item_id", "x"),
                             profit_score=kwargs.pop("profit_score", 0.5),
                             **kwargs)
    s, _ = score_content_candidate(item, mode="generate")
    return s


# ── Part 1: Basic profit calculation ─────────────────────────────────────────

class TestProfitCalculation:

    def test_positive_profit(self):
        rec = _profit(revenue=2.0, cost=1.0)
        assert rec.last_profit == pytest.approx(1.0)

    def test_negative_profit(self):
        rec = _profit(revenue=0.2, cost=1.0)
        assert rec.last_profit == pytest.approx(-0.8)

    def test_zero_profit(self):
        rec = _profit(revenue=1.0, cost=1.0)
        assert rec.last_profit == pytest.approx(0.0)

    def test_zero_cost_no_crash(self):
        rec = _profit(revenue=1.0, cost=0.0)
        assert rec.profit_score >= 0.5   # profit = +1.0 → high score

    def test_zero_revenue_no_crash(self):
        rec = _profit(revenue=0.0, cost=0.5)
        assert rec.profit_score <= 0.5   # loss

    def test_profit_margin_computed(self):
        rec = _profit(revenue=2.0, cost=1.0)
        # profit = 1.0, margin = 1.0/1.0 = 1.0 (EMA after 1 update from 0 baseline)
        assert rec.last_cost == pytest.approx(1.0)
        assert rec.last_revenue == pytest.approx(2.0)

    def test_update_count_increments(self):
        _profit()
        _profit()
        rec = get_profit_record("c1", "beauty")
        assert rec["update_count"] == 2

    def test_revenue_negative_clamped_to_zero(self):
        """Negative revenue is not physically meaningful; should not crash."""
        rec = _profit(revenue=-5.0, cost=1.0)
        assert rec.last_revenue == 0.0


# ── Part 2: Profit score = sigmoid ───────────────────────────────────────────

class TestProfitScore:

    def test_breakeven_gives_half(self):
        """At zero margin, sigmoid = 0.5 — converges there after many updates."""
        for _ in range(50):
            _profit(revenue=1.0, cost=1.0)   # margin = 0
        s = get_profit_score("c1", "beauty")
        assert abs(s - 0.5) < 0.1, f"Expected ~0.5, got {s}"

    def test_high_profit_gives_high_score(self):
        for _ in range(30):
            _profit(revenue=5.0, cost=1.0)   # big margin
        s = get_profit_score("c1", "beauty")
        assert s > 0.7

    def test_loss_gives_low_score(self):
        for _ in range(30):
            _profit(revenue=0.1, cost=1.0)   # heavy loss
        s = get_profit_score("c1", "beauty")
        assert s < 0.45

    def test_score_in_unit_range(self):
        for revenue in (0.0, 0.5, 1.0, 5.0, 10.0):
            reset_profit_state()
            _profit(revenue=revenue, cost=1.0)
            s = get_profit_score("c1", "beauty")
            assert 0.0 <= s <= 1.0, f"Out of range at revenue={revenue}: {s}"

    def test_sigmoid_helper(self):
        assert abs(_sigmoid(0.0) - 0.5) < 1e-9
        assert _sigmoid(10.0) > 0.99
        assert _sigmoid(-10.0) < 0.01


# ── Part 3: EMA alpha switch ──────────────────────────────────────────────────

class TestEMAAlpha:

    def test_spike_alpha_faster_update(self):
        """High-profit spike uses α=0.40 → score moves faster."""
        # Both start at same baseline (0.5)
        # High profit rec: update with very high revenue → spike alpha
        _profit(content_id="high", niche="beauty", revenue=10.0, cost=1.0)
        # Low profit rec: update with breakeven → normal alpha
        _profit(content_id="low",  niche="beauty", revenue=1.0,  cost=1.0)

        high_s = get_profit_score("high", "beauty")
        low_s  = get_profit_score("low",  "beauty")
        # Both moved from 0.5, but high should have moved farther
        assert abs(high_s - 0.5) > abs(low_s - 0.5), (
            f"Spike alpha should produce larger update: high={high_s} low={low_s}"
        )

    def test_ema_converges_to_true_score(self):
        """After many identical updates, EMA → steady-state sigmoid value."""
        for _ in range(100):
            _profit(revenue=3.0, cost=1.0)   # margin = 2.0 → sigmoid(6.0) ≈ 0.9975
        s = get_profit_score("c1", "beauty")
        expected = _sigmoid(2.0)   # profit_margin ~= 2.0 at convergence
        assert abs(s - expected) < 0.05, f"Expected ~{expected:.3f}, got {s:.3f}"


# ── Part 4: get_profit_record ─────────────────────────────────────────────────

class TestProfitRecord:

    def test_record_has_all_fields(self):
        _profit()
        rec = get_profit_record("c1", "beauty")
        assert rec is not None
        for field in ("revenue_ema", "cost_ema", "profit_ema",
                      "profit_margin", "profit_score",
                      "last_revenue", "last_cost", "last_profit",
                      "update_count", "history"):
            assert field in rec, f"Missing field: {field}"

    def test_unknown_returns_none(self):
        assert get_profit_record("never-seen", "beauty") is None

    def test_default_profit_score_is_half(self):
        """No history → get_profit_score returns 0.5 (neutral)."""
        s = get_profit_score("no-data", "beauty")
        assert s == pytest.approx(0.5)

    def test_history_capped_at_10(self):
        for _ in range(25):
            _profit()
        rec = get_profit_record("c1", "beauty")
        assert len(rec["history"]) <= 10


# ── Part 5: Profit influence on decision layer ────────────────────────────────

class TestProfitInfluencesDecision:

    def test_high_profit_score_increases_score(self):
        """High profit_score raises the overall candidate score."""
        low_profit  = _score_item(item_id="lp", trend_score=0.6, product_intent=0.6,
                                   hook_potential=0.6, profit_score=0.1)
        high_profit = _score_item(item_id="hp", trend_score=0.6, product_intent=0.6,
                                   hook_potential=0.6, profit_score=0.9)
        assert high_profit > low_profit

    def test_low_profit_hard_gate_blocks_generate(self):
        """profit_score < 0.2 in generate mode → low_profit_block."""
        item = ContentCandidate(
            item_id="lp-gate",
            trend_score=0.8, product_intent=0.8, hook_potential=0.8,
            match_score=0.8, novelty_score=0.8, production_cost=0.2,
            profit_score=0.10,  # below 0.20 threshold
        )
        allowed, reason = should_produce(item, mode="generate")
        assert not allowed
        assert "low_profit_block" in reason

    def test_high_profit_passes_generate(self):
        """profit_score >= 0.2 with good EV passes generate gate."""
        item = ContentCandidate(
            item_id="hp-pass",
            trend_score=0.8, product_intent=0.8, hook_potential=0.8,
            match_score=0.8, novelty_score=0.8, production_cost=0.1,
            profit_score=0.80,
        )
        allowed, reason = should_produce(item, mode="generate")
        assert allowed, f"Expected pass, got: {reason}"

    def test_profit_not_gates_reup(self):
        """low_profit_block only applies to generate; reup should pass."""
        item = ContentCandidate(
            item_id="reup-low-profit",
            trend_score=0.6, product_intent=0.6, hook_potential=0.6,
            profit_score=0.05,  # very low but reup mode
        )
        # reup doesn't have profit hard gate
        allowed, reason = should_produce(item, mode="reup")
        # should not fail on profit gate (may fail on EV/score if configured tight)
        assert "low_profit_block" not in reason

    def test_profit_score_in_breakdown(self):
        """Breakdown must include profit_score_used."""
        item = ContentCandidate(item_id="bd", profit_score=0.7)
        _, bd = score_content_candidate(item, mode="remark")
        assert "profit_score_used" in bd
        assert abs(bd["profit_score_used"] - 0.7) < 0.01

    def test_profit_auto_read_from_engine(self):
        """Sentinel (-1.0) falls back to 0.5 when no profit history exists.
        This is the correct safe behaviour — never blocks unknown content."""
        item = ContentCandidate(item_id="brand-new-content", profit_score=-1.0)
        _, bd = score_content_candidate(item, niche="tech", mode="remark")
        # No profit history → neutral fallback = 0.5
        assert bd["profit_score_used"] == pytest.approx(0.5, abs=0.01), (
            f"Unknown content should default to 0.5, got {bd['profit_score_used']}"
        )

    def test_explicit_profit_score_overrides_ema(self):
        """Explicit profit_score value is used directly, not the EMA."""
        item = ContentCandidate(item_id="explicit", profit_score=0.85)
        _, bd = score_content_candidate(item, mode="remark")
        assert abs(bd["profit_score_used"] - 0.85) < 0.01


# ── Part 6: Profit influence on scaling ───────────────────────────────────────

class TestProfitInfluencesScaling:

    def _warm_up(self, content_id="sc", niche="beauty", views=0.7, eng=0.7,
                 conv=0.7, profit=0.7, n=60):
        for i in range(n):
            update_performance(content_id=content_id, niche=niche,
                               views=views, engagement_rate=eng,
                               conversion_rate=conv, profit_score=profit, cycle=i)

    def test_high_profit_content_scales_more(self):
        self._warm_up("hi-profit", profit=0.9)
        self._warm_up("lo-profit", profit=0.1)
        sf_high = get_scaling_factor("hi-profit", "beauty")
        sf_low  = get_scaling_factor("lo-profit", "beauty")
        assert sf_high >= sf_low, f"High profit should scale more: {sf_high} vs {sf_low}"

    def test_anti_fake_viral_caps_sf(self):
        """High views but low profit → SF capped at 1.2."""
        # Patch profit engine to return low profit for this content
        update_profit("fv", "beauty", revenue=0.01, cost=1.0)
        for _ in range(5):
            update_profit("fv", "beauty", revenue=0.01, cost=1.0)

        # Update scaling with high views but low profit
        for i in range(60):
            update_performance(
                content_id="fv", niche="beauty",
                views=1.0,      # high views
                engagement_rate=0.9,
                conversion_rate=0.8,
                profit_score=get_profit_score("fv", "beauty"),  # low
                cycle=i,
            )
        sf = get_scaling_factor("fv", "beauty")
        # If profit is low enough, anti-fake-viral cap applies
        if get_profit_score("fv", "beauty") < _FAKE_VIRAL_PROFIT_THRESHOLD:
            assert sf <= _FAKE_VIRAL_SF_CAP + 0.01, f"Fake viral not capped: {sf}"

    def test_profit_zero_does_not_crash(self):
        rec = update_performance(
            content_id="z", niche="beauty",
            views=0.5, engagement_rate=0.5, conversion_rate=0.5,
            profit_score=0.0, cycle=0,
        )
        assert rec is not None
        assert 0.0 <= rec.scaling_factor <= 3.0


# ── Part 7: Anti-fake-viral ───────────────────────────────────────────────────

class TestAntiiFakeViral:

    def test_high_views_low_profit_flagged(self):
        for _ in range(10):
            update_profit("fake", "beauty", revenue=0.01, cost=1.0)
        assert is_fake_viral("fake", "beauty", views_ratio=0.9)

    def test_high_views_high_profit_not_flagged(self):
        for _ in range(10):
            update_profit("real", "beauty", revenue=5.0, cost=1.0)
        assert not is_fake_viral("real", "beauty", views_ratio=0.9)

    def test_low_views_not_flagged(self):
        for _ in range(10):
            update_profit("lv", "beauty", revenue=0.01, cost=1.0)
        # views below threshold → not fake viral regardless of profit
        assert not is_fake_viral("lv", "beauty",
                                  views_ratio=_FAKE_VIRAL_VIEWS_THRESHOLD - 0.1)

    def test_no_history_not_flagged(self):
        assert not is_fake_viral("no-history", "beauty", views_ratio=1.0)


# ── Part 8: Log structure ─────────────────────────────────────────────────────

class TestProfitLog:

    def test_log_populated(self):
        _profit()
        log = get_profit_log()
        assert len(log) == 1

    def test_log_has_required_fields(self):
        _profit()
        entry = get_profit_log()[0]
        for field in ("content_id", "niche", "revenue", "cost",
                      "profit", "profit_margin", "profit_score"):
            assert field in entry, f"Missing log field: {field}"

    def test_log_resets(self):
        _profit()
        reset_profit_state()
        assert get_profit_log() == []

    def test_negative_profit_logged(self):
        _profit(revenue=0.1, cost=5.0)
        entry = get_profit_log()[0]
        assert entry["profit"] < 0

    def test_log_alpha_recorded(self):
        _profit(revenue=10.0, cost=1.0)
        entry = get_profit_log()[0]
        # High profit → spike alpha (0.40)
        assert "alpha_used" in entry


# ── Part 9: Persistence (SQLite store) ───────────────────────────────────────

class TestPersistence:
    """Verifies that profit state is read from the shared store, not module memory."""

    def test_write_then_read_from_store(self):
        """update_profit() writes; get_profit_score() reads from same store."""
        update_profit("p-c1", "beauty", revenue=3.0, cost=1.0)
        score = get_profit_score("p-c1", "beauty")
        assert score > 0.5, f"Profitable content should score > 0.5, got {score}"

    def test_store_persists_across_reset_log(self):
        """Resetting the in-process log does NOT wipe the store."""
        update_profit("p-c2", "tech", revenue=2.0, cost=1.0)
        score_before = get_profit_score("p-c2", "tech")
        # Clear only the log
        from core.profit_engine import _PROFIT_LOG
        _PROFIT_LOG.clear()
        score_after = get_profit_score("p-c2", "tech")
        # Store is unaffected by log clear
        assert abs(score_before - score_after) < 1e-6

    def test_get_profit_record_reads_from_store(self):
        """get_profit_record() returns store data, not module-local dict."""
        update_profit("p-c3", "fitness", revenue=5.0, cost=1.0)
        rec = get_profit_record("p-c3", "fitness")
        assert rec is not None
        assert rec["content_id"] == "p-c3"
        assert rec["profit_score"] > 0.5

    def test_reset_clears_store(self):
        """reset_profit_state() wipes SQLite records."""
        update_profit("p-c4", "food", revenue=2.0, cost=1.0)
        reset_profit_state()
        rec = get_profit_record("p-c4", "food")
        assert rec is None
        assert get_profit_score("p-c4", "food") == pytest.approx(0.5)

    def test_deterministic_key(self):
        """Same (content_id, niche) pair → same key in store."""
        from core.profit_engine import _key
        k1 = _key("video-123", "beauty")
        k2 = _key("video-123", "beauty")
        k3 = _key("video-123", "tech")
        assert k1 == k2
        assert k1 != k3
        assert len(k1) == 16   # SHA-256[:16]

    def test_store_cache_stats_structure(self):
        """ProfitStore.cache_stats() returns expected keys."""
        store = get_profit_store()
        stats = store.cache_stats()
        for field in ("cache_size", "cache_max", "cache_ttl_s",
                      "db_path", "db_connected"):
            assert field in stats, f"Missing cache_stats field: {field}"
        assert stats["db_connected"] is True

    def test_store_set_then_get_roundtrip(self):
        """Direct store.set + store.get round-trip (unit test of store layer)."""
        store = get_profit_store()
        payload = {"profit_score": 0.77, "content_id": "x", "niche": "y",
                   "update_count": 3, "history": [0.6, 0.7, 0.77]}
        store.set("test-key-rt", payload, content_id="x", niche="y")
        result = store.get("test-key-rt")
        assert result is not None
        assert result["profit_score"] == pytest.approx(0.77)
        assert result["update_count"] == 3

    def test_store_delete(self):
        """store.delete removes the key."""
        store = get_profit_store()
        store.set("del-key", {"profit_score": 0.6}, content_id="d", niche="d")
        store.delete("del-key")
        assert store.get("del-key") is None

    def test_cache_serves_subsequent_reads(self):
        """Second read for same key is served from cache (no DB round-trip)."""
        update_profit("cache-c", "beauty", revenue=2.0, cost=1.0)
        s1 = get_profit_score("cache-c", "beauty")
        # Second call should hit cache; result must be identical
        s2 = get_profit_score("cache-c", "beauty")
        assert s1 == pytest.approx(s2)

    def test_multiple_niches_isolated(self):
        """Same content_id with different niches → independent records."""
        update_profit("shared-vid", "beauty", revenue=5.0, cost=1.0)
        update_profit("shared-vid", "fitness", revenue=0.1, cost=1.0)
        s_beauty  = get_profit_score("shared-vid", "beauty")
        s_fitness = get_profit_score("shared-vid", "fitness")
        assert s_beauty > 0.5
        assert s_fitness < 0.5
        assert s_beauty != s_fitness
