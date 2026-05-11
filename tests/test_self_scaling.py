"""
tests/test_self_scaling.py — Self-Scaling Engine Test Suite (v2 — profit-aware)

Validates:
  1. test_scaling_trigger   — tier assignment and scaling factor by performance
  2. test_scaling_decay     — 2-cycle drop reduces scaling factor
  3. test_budget_allocation — 60/25/15 budget split + winner proportionality
  4. Anti-spam guards
  5. Priority queue ordering
  6. Performance normalisation per niche
  7. Log structure
"""
from __future__ import annotations

import sys
import pathlib
import importlib.util

# Direct module load — bypass core/__init__ chain
_root = pathlib.Path(__file__).resolve().parents[1]

# Load profit_engine first (self_scaling imports it)
_pe_spec = importlib.util.spec_from_file_location(
    "core.profit_engine", _root / "core" / "profit_engine.py"
)
_pe_mod = importlib.util.module_from_spec(_pe_spec)
sys.modules["core.profit_engine"] = _pe_mod
_pe_spec.loader.exec_module(_pe_mod)

_spec = importlib.util.spec_from_file_location(
    "self_scaling", _root / "core" / "self_scaling.py"
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["self_scaling"] = _mod
_spec.loader.exec_module(_mod)

from self_scaling import (
    ScalingTier,
    BudgetAllocation,
    update_performance,
    get_scaling_factor,
    get_priority_queue,
    allocate_budget,
    check_anti_spam,
    record_post_timestamp,
    get_scaling_log,
    reset_scaling_state,
    MAX_POSTS_PER_PAGE_PER_DAY,
    MIN_INTERVAL_BETWEEN_POSTS_S,
    BUDGET_WINNER_RATIO,
    BUDGET_NORMAL_RATIO,
    BUDGET_EXPLORATION_RATIO,
    _PERF_STORE,
    _key,
)

import pytest


@pytest.fixture(autouse=True)
def _reset():
    reset_scaling_state()
    yield
    reset_scaling_state()


# ── helpers ───────────────────────────────────────────────────────────────────

def _update(
    content_id:  str   = "c1",
    niche:       str   = "beauty",
    views:       float = 0.5,
    eng:         float = 0.5,
    conv:        float = 0.5,
    profit:      float = 0.5,   # v2: profit_score replaces retention in weights
    cycle:       int   = 0,
    seed:        int   = 42,
):
    """Thin wrapper — maps test params to the v2 update_performance signature."""
    return update_performance(
        content_id      = content_id,
        niche           = niche,
        views           = views,
        engagement_rate = eng,
        conversion_rate = conv,
        profit_score    = profit,
        cycle           = cycle,
        seed            = seed,
    )


# ── Test 1: Scaling trigger (tier assignment) ─────────────────────────────────

class TestScalingTrigger:

    def test_high_perf_becomes_viral(self):
        # All signals maxed (including profit_score=1.0) → perf converges to 1.0
        # Need >80 EMA cycles to cross 0.8 threshold
        rec = None
        for i in range(100):
            rec = _update(views=1.0, eng=1.0, conv=1.0, profit=1.0, cycle=i)
        assert rec.tier == ScalingTier.VIRAL
        assert rec.scaling_factor >= 2.0

    def test_medium_perf_becomes_winner(self):
        # 0.3×0.75 + 0.2×0.75 + 0.2×0.75 + 0.3×0.75 = 0.75 → WINNER after convergence
        rec = None
        for i in range(100):
            rec = _update(views=0.75, eng=0.75, conv=0.75, profit=0.75, cycle=i)
        assert rec.tier == ScalingTier.WINNER
        assert abs(rec.scaling_factor - 1.5) < 0.1

    def test_low_perf_becomes_dead(self):
        rec = None
        for i in range(60):
            rec = _update(views=0.0, eng=0.0, conv=0.0, profit=0.0, cycle=i)
        assert rec.tier == ScalingTier.DEAD
        assert rec.scaling_factor == 0.0

    def test_neutral_perf_stays_normal(self):
        # All signals ~0.45 → perf converges to ~0.45 → NORMAL
        rec = None
        for i in range(60):
            rec = _update(views=0.45, eng=0.45, conv=0.45, profit=0.45, cycle=i)
        assert rec.tier == ScalingTier.NORMAL
        assert abs(rec.scaling_factor - 1.0) < 0.05

    def test_scaling_factor_in_valid_range(self):
        for v in (0.0, 0.3, 0.6, 0.9, 1.0):
            reset_scaling_state()
            for i in range(10):
                rec = _update(views=v, eng=v, conv=v, profit=v, cycle=i)
            assert 0.0 <= rec.scaling_factor <= 3.0

    def test_get_scaling_factor_api(self):
        for i in range(100):
            _update(content_id="sf-test", niche="tech",
                    views=0.9, eng=0.9, conv=0.9, profit=0.9, cycle=i)
        sf = get_scaling_factor("sf-test", "tech")
        assert sf >= 1.5   # at least WINNER

    def test_unknown_content_returns_baseline(self):
        sf = get_scaling_factor("never-seen", "beauty")
        assert sf == 1.0

    def test_viral_scaling_seeded_deterministic(self):
        """Same seed → same jittered VIRAL scaling factor."""
        for i in range(100):
            _update(content_id="vr", niche="fitness",
                    views=1.0, eng=1.0, conv=1.0, profit=1.0, cycle=i, seed=99)
        sf1 = get_scaling_factor("vr", "fitness")
        reset_scaling_state()
        for i in range(100):
            _update(content_id="vr", niche="fitness",
                    views=1.0, eng=1.0, conv=1.0, profit=1.0, cycle=i, seed=99)
        sf2 = get_scaling_factor("vr", "fitness")
        assert abs(sf1 - sf2) < 0.001


# ── Test 2: Decay mechanism ───────────────────────────────────────────────────

class TestScalingDecay:

    def test_two_consecutive_drops_trigger_decay(self):
        """After 2 cycles of declining performance, scaling factor is reduced."""
        for i in range(80):
            _update(content_id="decay-t", niche="home",
                    views=0.75, eng=0.75, conv=0.75, profit=0.75, cycle=i)
        sf_before = get_scaling_factor("decay-t", "home")
        assert sf_before >= 1.0, f"Setup failed — not WINNER yet: {sf_before}"

        _update(content_id="decay-t", niche="home",
                views=0.4, eng=0.4, conv=0.4, profit=0.4, cycle=81)
        _update(content_id="decay-t", niche="home",
                views=0.1, eng=0.1, conv=0.1, profit=0.1, cycle=82)

        sf_after = get_scaling_factor("decay-t", "home")
        k   = _key("decay-t", "home")
        rec = _PERF_STORE[k]
        assert rec.decay_count >= 1 or sf_after <= sf_before, (
            f"Expected decay signal: decay_count={rec.decay_count} sf {sf_before}→{sf_after}"
        )

    def test_decay_count_increments(self):
        # Three strictly descending updates should increment decay_count
        _update(content_id="dc", niche="home", views=0.8, eng=0.8, conv=0.8, profit=0.8, cycle=0)
        _update(content_id="dc", niche="home", views=0.4, eng=0.4, conv=0.4, profit=0.4, cycle=1)
        _update(content_id="dc", niche="home", views=0.1, eng=0.1, conv=0.1, profit=0.1, cycle=2)
        k = _key("dc", "home")
        # EMA smooths the drop; at least 1 decay by cycle 2
        assert _PERF_STORE[k].decay_count >= 1

    def test_recovery_resets_decay(self):
        """Performance recovery resets decay counter."""
        for i in range(5):
            _update(content_id="rv", niche="tech",
                    views=0.7, eng=0.7, conv=0.7, profit=0.7, cycle=i)
        _update(content_id="rv", niche="tech",
                views=0.2, eng=0.2, conv=0.2, profit=0.2, cycle=6)
        _update(content_id="rv", niche="tech",
                views=0.9, eng=0.9, conv=0.9, profit=0.9, cycle=7)
        k = _key("rv", "tech")
        assert _PERF_STORE[k].decay_count == 0

    def test_continued_drop_returns_to_normal(self):
        """Enough decay eventually returns WINNER to NORMAL tier."""
        for i in range(80):
            _update(content_id="cd", niche="food",
                    views=0.75, eng=0.75, conv=0.75, profit=0.75, cycle=i)
        for i in range(80, 100):
            _update(content_id="cd", niche="food",
                    views=0.05, eng=0.05, conv=0.05, profit=0.05, cycle=i)
        sf = get_scaling_factor("cd", "food")
        assert sf <= 1.5, f"Expected decay to reduce SF: {sf}"


# ── Test 3: Budget allocation (v2 = 60/25/15) ────────────────────────────────

class TestBudgetAllocation:

    def _setup_niche(self, niche: str):
        """Seed a niche with WINNER and NORMAL content."""
        for i in range(100):
            _update(content_id="winner-1", niche=niche,
                    views=0.95, eng=0.95, conv=0.95, profit=0.95, cycle=i)
        for i in range(100):
            _update(content_id="winner-2", niche=niche,
                    views=0.75, eng=0.75, conv=0.75, profit=0.75, cycle=i)
        for i in range(60):
            _update(content_id="normal-1", niche=niche,
                    views=0.4, eng=0.4, conv=0.4, profit=0.4, cycle=i)
        for i in range(60):
            _update(content_id="dead-1", niche=niche,
                    views=0.0, eng=0.0, conv=0.0, profit=0.0, cycle=i)

    def test_budget_sums_correctly(self):
        niche = "beauty"
        self._setup_niche(niche)
        alloc = allocate_budget(total_budget=100.0, niche=niche)
        total = alloc.winner_budget + alloc.normal_budget + alloc.explore_budget
        assert abs(total - 100.0) < 0.01

    def test_winner_gets_60_pct(self):
        """v2: winners get 60% of budget."""
        niche = "beauty"
        self._setup_niche(niche)
        alloc = allocate_budget(100.0, niche)
        assert abs(alloc.winner_budget - 100.0 * BUDGET_WINNER_RATIO) < 0.01

    def test_normal_gets_25_pct(self):
        """v2: normal gets 25% of budget."""
        niche = "beauty"
        self._setup_niche(niche)
        alloc = allocate_budget(100.0, niche)
        assert abs(alloc.normal_budget - 100.0 * BUDGET_NORMAL_RATIO) < 0.01

    def test_explore_gets_15_pct(self):
        """v2: exploration gets 15% (≥10% rule maintained)."""
        niche = "beauty"
        self._setup_niche(niche)
        alloc = allocate_budget(100.0, niche)
        assert abs(alloc.explore_budget - 100.0 * BUDGET_EXPLORATION_RATIO) < 0.01
        assert alloc.explore_budget >= 10.0   # never below 10%

    def test_winners_allocated_proportionally(self):
        """Higher-performance winner gets more of the winner budget."""
        niche = "tech"
        self._setup_niche(niche)
        alloc = allocate_budget(100.0, niche, seed=7)
        if "winner-1" in alloc.winner_allocs and "winner-2" in alloc.winner_allocs:
            assert alloc.winner_allocs["winner-1"] >= alloc.winner_allocs["winner-2"]

    def test_returns_budget_allocation_type(self):
        alloc = allocate_budget(50.0, "empty-niche")
        assert isinstance(alloc, BudgetAllocation)
        assert alloc.total_budget == 50.0

    def test_explore_ids_are_from_lower_tiers(self):
        niche = "fashion"
        self._setup_niche(niche)
        alloc = allocate_budget(100.0, niche, seed=42)
        for eid in alloc.explore_ids:
            q = {q["content_id"]: q for q in get_priority_queue(niche)}
            if eid in q:
                assert q[eid]["tier"] in (ScalingTier.DEAD.value, ScalingTier.NORMAL.value)

    def test_allocation_deterministic_same_seed(self):
        niche = "food"
        self._setup_niche(niche)
        alloc1 = allocate_budget(100.0, niche, seed=123)
        alloc2 = allocate_budget(100.0, niche, seed=123)
        assert alloc1.explore_ids == alloc2.explore_ids

    def test_empty_niche_no_crash(self):
        alloc = allocate_budget(100.0, "totally-empty-niche", seed=0)
        assert alloc.winner_budget + alloc.normal_budget + alloc.explore_budget == pytest.approx(100.0, abs=0.01)


# ── Test 4: Anti-spam ─────────────────────────────────────────────────────────

class TestAntiSpam:

    def test_max_posts_per_day_blocks(self):
        allowed, reason = check_anti_spam(
            content_id="spam-c", niche="beauty", account_id="acct-1",
            now_ts=1000, posts_today=MAX_POSTS_PER_PAGE_PER_DAY,
        )
        assert not allowed
        assert "max_posts" in reason

    def test_under_limit_allowed(self):
        allowed, reason = check_anti_spam(
            content_id="ok-c", niche="beauty", account_id="acct-1",
            now_ts=1000, posts_today=0,
        )
        assert allowed

    def test_min_interval_blocks_too_soon(self):
        _update(content_id="interval-c", niche="beauty")
        record_post_timestamp("interval-c", "beauty", "acct-2", now_ts=1000)
        allowed, reason = check_anti_spam(
            content_id="interval-c", niche="beauty", account_id="acct-3",
            now_ts=1100, posts_today=0,
        )
        assert not allowed
        assert "min_interval" in reason

    def test_after_interval_allowed(self):
        _update(content_id="after-c", niche="beauty")
        record_post_timestamp("after-c", "beauty", "acct-4", now_ts=1000)
        allowed, reason = check_anti_spam(
            content_id="after-c", niche="beauty", account_id="acct-5",
            now_ts=1000 + MIN_INTERVAL_BETWEEN_POSTS_S + 1, posts_today=0,
        )
        assert allowed

    def test_duplicate_account_blocked(self):
        _update(content_id="dup-c", niche="beauty")
        record_post_timestamp("dup-c", "beauty", "acct-6", now_ts=1000)
        allowed, reason = check_anti_spam(
            content_id="dup-c", niche="beauty", account_id="acct-6",
            now_ts=1000 + MIN_INTERVAL_BETWEEN_POSTS_S + 1, posts_today=0,
        )
        assert not allowed
        assert "distributed" in reason


# ── Test 5: Priority queue ────────────────────────────────────────────────────

class TestPriorityQueue:

    def test_queue_sorted_descending(self):
        for i in range(5):
            _update(content_id=f"c-{i}", niche="tech",
                    views=float(i)/5, eng=float(i)/5,
                    conv=float(i)/5, profit=float(i)/5, cycle=0)
        queue = get_priority_queue("tech")
        scores = [q["performance_score"] for q in queue]
        assert scores == sorted(scores, reverse=True)

    def test_queue_only_contains_niche(self):
        _update(content_id="tech-c", niche="tech", views=0.8, eng=0.8, conv=0.8, profit=0.8)
        _update(content_id="beauty-c", niche="beauty", views=0.8, eng=0.8, conv=0.8, profit=0.8)
        tech_queue = get_priority_queue("tech")
        assert all(q["niche"] == "tech" for q in tech_queue)
        assert not any(q["content_id"] == "beauty-c" for q in tech_queue)

    def test_queue_entry_has_required_fields(self):
        _update(content_id="qf-c", niche="food", views=0.7, eng=0.7, conv=0.7, profit=0.7)
        queue = get_priority_queue("food")
        assert len(queue) == 1
        entry = queue[0]
        for field in ("content_id", "niche", "performance_score", "tier",
                      "scaling_factor", "decay_count", "actions"):
            assert field in entry, f"Missing field: {field}"

    def test_empty_niche_returns_empty(self):
        assert get_priority_queue("nonexistent-niche") == []


# ── Test 6: Logging ───────────────────────────────────────────────────────────

class TestScalingLog:

    def test_log_populated_after_update(self):
        _update(content_id="log-c", niche="home")
        log = get_scaling_log()
        assert len(log) == 1

    def test_log_has_required_fields(self):
        _update(content_id="log-f", niche="home", views=0.8, eng=0.8, conv=0.8, profit=0.8)
        entry = get_scaling_log(1)[0]
        for field in ("content_id", "niche", "performance_score",
                      "tier", "scaling_factor", "actions"):
            assert field in entry, f"Missing log field: {field}"

    def test_log_resets(self):
        _update(content_id="lr-c", niche="home")
        reset_scaling_state()
        assert get_scaling_log() == []

    def test_log_captures_tier_correctly(self):
        # All signals 1.0 including profit → converges to VIRAL
        for i in range(100):
            _update(content_id="tier-log", niche="tech",
                    views=1.0, eng=1.0, conv=1.0, profit=1.0, cycle=i)
        last = get_scaling_log()[-1]
        assert last["tier"] == ScalingTier.VIRAL.value
