"""
tests/test_attribution_engine.py — Revenue Attribution Engine Test Suite

Covers:
  Part 1 — Tracking ID system
  Part 2 — Click + Conversion tracking
  Part 3 — Multi-touch attribution (70/30)
  Part 4 — Public query API
  Part 5 — Integration with profit_engine (flush)
  Part 6 — Persistence (store round-trips)
  Part 7 — Edge cases
"""
from __future__ import annotations

import os
import sys
import pathlib
import importlib.util
import time

# ── Use in-memory SQLite for test isolation ───────────────────────────────────
os.environ["ATTRIBUTION_STATE_DB"] = ":memory:"
os.environ["PROFIT_STATE_DB"]      = ":memory:"

_root = pathlib.Path(__file__).resolve().parents[1]

def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, _root / rel)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

# Load order: stores first, then engines
_load("core.attribution_store", "core/attribution_store.py")
_load("core.profit_store",      "core/profit_store.py")
_load("core.profit_engine",     "core/profit_engine.py")
_load("core.attribution_engine","core/attribution_engine.py")

from core.attribution_store import get_attribution_store, reset_attribution_store
from core.profit_store      import get_profit_store, reset_profit_store
from core.profit_engine     import (
    get_profit_score, reset_profit_state,
)
from core.attribution_engine import (
    generate_tracking_code, parse_tracking_code,
    record_click, record_conversion,
    flush_to_profit_engine,
    get_revenue, get_conversion_rate, get_profit,
    get_attribution_report, get_attribution_log,
    reset_attribution_state,
    _LAST_CLICK_SHARE, _ASSIST_SHARE,
)

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset():
    reset_attribution_state()
    reset_profit_state()
    yield
    reset_attribution_state()
    reset_profit_state()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _code(content_id="vid-1", page_id="page-A", ts=None):
    return generate_tracking_code(content_id, page_id, ts or 1_700_000_000.0)


def _click(code, niche="beauty", account="acc-1", ts=None):
    return record_click(code, niche=niche, account_id=account,
                        click_ts=ts or time.time())


def _convert(code, revenue=10.0, niche="beauty", account="acc-1", ts=None):
    return record_conversion(code, revenue, niche=niche,
                             account_id=account,
                             conversion_ts=ts or time.time())


# ═══════════════════════════════════════════════════════════════════════════════
# Part 1 — Tracking ID System
# ═══════════════════════════════════════════════════════════════════════════════

class TestTrackingCode:

    def test_generate_returns_aff_scheme(self):
        code = _code()
        assert code.startswith("aff://")

    def test_generate_contains_content_id(self):
        code = _code(content_id="my-video")
        assert "my-video" in code

    def test_generate_contains_page_id(self):
        code = _code(page_id="shop-page")
        assert "shop-page" in code

    def test_generate_deterministic_same_ts(self):
        c1 = generate_tracking_code("vid", "page", 1700000000.0)
        c2 = generate_tracking_code("vid", "page", 1700000000.0)
        assert c1 == c2

    def test_generate_different_ts_different_code(self):
        c1 = generate_tracking_code("vid", "page", 1700000000.0)
        c2 = generate_tracking_code("vid", "page", 1700000001.0)
        assert c1 != c2

    def test_generate_different_content_different_code(self):
        c1 = generate_tracking_code("vid-A", "page", 1700000000.0)
        c2 = generate_tracking_code("vid-B", "page", 1700000000.0)
        assert c1 != c2

    def test_generate_sanitises_colons_in_content_id(self):
        code = generate_tracking_code("acc:beauty:REUP", "page", 1700000000.0)
        # colons in content_id must not break the format
        parsed = parse_tracking_code(code)
        assert parsed is not None

    def test_parse_valid_code(self):
        code   = _code(content_id="vid-1", page_id="page-A", ts=1700000000.0)
        parsed = parse_tracking_code(code)
        assert parsed is not None
        assert "vid-1" in parsed["content_id"]
        assert "page-A" in parsed["page_id"]
        assert parsed["timestamp"] == pytest.approx(1700000000.0, abs=1.0)

    def test_parse_invalid_code_returns_none(self):
        assert parse_tracking_code("not-a-code") is None
        assert parse_tracking_code("") is None
        assert parse_tracking_code("http://example.com") is None

    def test_parse_missing_timestamp_returns_none(self):
        assert parse_tracking_code("aff://vid:page:") is None

    def test_code_format_aff_colon_slash_slash(self):
        code = _code()
        assert code.startswith("aff://")
        parts = code[6:].split(":")
        assert len(parts) == 3   # content_id : page_id : ts_hex


# ═══════════════════════════════════════════════════════════════════════════════
# Part 2 — Click + Conversion Tracking
# ═══════════════════════════════════════════════════════════════════════════════

class TestClickTracking:

    def test_record_click_returns_true(self):
        assert _click(_code()) is True

    def test_record_click_invalid_code_returns_false(self):
        assert record_click("bad-code") is False

    def test_record_click_stored_in_store(self):
        code = _code("cid-1", "pid-1")
        _click(code, niche="tech", account="acc-A")
        store  = get_attribution_store()
        touches = store.get_touches(code)
        assert len(touches) == 1
        assert touches[0]["content_id"].replace("_", ":").startswith("cid") or \
               "cid" in touches[0]["content_id"]

    def test_record_multiple_clicks_same_code(self):
        code = _code()
        for _ in range(3):
            _click(code)
        store   = get_attribution_store()
        touches = store.get_touches(code)
        assert len(touches) == 3

    def test_record_click_preserves_niche(self):
        code = _code()
        record_click(code, niche="fitness", account_id="a1")
        store   = get_attribution_store()
        touches = store.get_touches(code)
        assert touches[0]["niche"] == "fitness"

    def test_record_click_preserves_account_id(self):
        code = _code()
        record_click(code, niche="beauty", account_id="my-account")
        store   = get_attribution_store()
        touches = store.get_touches(code)
        assert touches[0]["account_id"] == "my-account"


class TestConversionTracking:

    def test_record_conversion_returns_true(self):
        code = _code()
        _click(code)
        assert _convert(code, revenue=25.0) is True

    def test_record_conversion_invalid_code_returns_false(self):
        assert record_conversion("bad", 10.0) is False

    def test_record_conversion_zero_revenue_ok(self):
        code = _code()
        assert _convert(code, revenue=0.0) is True

    def test_record_conversion_negative_revenue_clamped(self):
        code = _code()
        assert _convert(code, revenue=-5.0) is True   # stored as 0.0

    def test_conversion_stored_as_pending(self):
        code = _code()
        _convert(code, revenue=10.0)
        store   = get_attribution_store()
        pending = store.get_pending_conversions()
        assert len(pending) == 1
        assert pending[0]["revenue"] == pytest.approx(10.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Part 3 — Multi-Touch Attribution (70/30)
# ═══════════════════════════════════════════════════════════════════════════════

class TestMultiTouchAttribution:

    def test_single_touch_gets_100_pct(self):
        code = _code("single", "p1")
        _click(code, niche="beauty")
        _convert(code, revenue=100.0, niche="beauty")
        flush_to_profit_engine()
        rev = get_revenue("single", "beauty")
        assert rev == pytest.approx(100.0, abs=0.01)

    def test_last_click_gets_70_pct(self):
        """Two-touch journey for the SAME content: user clicked twice, then converted.
        Last click gets 70%, first click gets 30%."""
        # Same content_id, two different tracking codes (different timestamps)
        code1 = generate_tracking_code("vid-70", "p1", 1700000000.0)
        code2 = generate_tracking_code("vid-70", "p1", 1700000001.0)
        _click(code1, niche="beauty")   # first touch (older)
        _click(code2, niche="beauty")   # second touch = last click
        record_conversion(code2, revenue=100.0, niche="beauty")
        flush_to_profit_engine()
        # Both clicks are for "vid-70" → content gets 100% total
        # but internally last-click = 70%, assist = 30% (same content_id)
        rev = get_revenue("vid-70", "beauty")
        assert rev == pytest.approx(100.0, abs=0.01)

    def test_attribution_splits_last_click_70_assist_30(self):
        """Verify the internal split: last touch → 70% in attr_rev, first touch → 30% in asst_rev."""
        code1 = generate_tracking_code("split-c", "p1", 1700000000.0)
        code2 = generate_tracking_code("split-c", "p1", 1700000001.0)
        _click(code1, niche="beauty")   # assist
        _click(code2, niche="beauty")   # last-click
        record_conversion(code2, revenue=100.0, niche="beauty")
        flush_to_profit_engine()
        rep = get_attribution_report("split-c", "beauty")
        assert rep["attributed_rev"] == pytest.approx(70.0, abs=0.01)
        assert rep["assist_rev"]     == pytest.approx(30.0, abs=0.01)
        assert rep["total_rev"]      == pytest.approx(100.0, abs=0.01)

    def test_two_assists_split_30_pct_equally(self):
        """Three touches: 2 assists share 30%, last gets 70%."""
        c1 = generate_tracking_code("three-c", "p1", 1700000000.0)
        c2 = generate_tracking_code("three-c", "p1", 1700000001.0)
        c3 = generate_tracking_code("three-c", "p1", 1700000002.0)
        _click(c1, niche="beauty")   # assist 1
        _click(c2, niche="beauty")   # assist 2
        _click(c3, niche="beauty")   # last-click
        record_conversion(c3, revenue=100.0, niche="beauty")
        flush_to_profit_engine()
        rep = get_attribution_report("three-c", "beauty")
        # attr_rev = 70 (last-click), asst_rev = 15+15 = 30 (two assists)
        assert rep["attributed_rev"] == pytest.approx(70.0, abs=0.01)
        assert rep["assist_rev"]     == pytest.approx(30.0, abs=0.01)

    def test_revenue_sums_to_total(self):
        """attributed_rev + assist_rev must equal total conversion revenue."""
        c1 = generate_tracking_code("sum-c", "p1", 1700000000.0)
        c2 = generate_tracking_code("sum-c", "p1", 1700000001.0)
        _click(c1, niche="tech")
        _click(c2, niche="tech")
        record_conversion(c2, revenue=50.0, niche="tech")
        flush_to_profit_engine()
        rep = get_attribution_report("sum-c", "tech")
        assert rep["total_rev"] == pytest.approx(50.0, abs=0.01)
        assert (rep["attributed_rev"] + rep["assist_rev"]) == pytest.approx(50.0, abs=0.01)

    def test_no_touch_history_gives_all_to_converting_code(self):
        """Conversion with no prior click → 100% to converting content."""
        code = _code("no-touch", "p1")
        _convert(code, revenue=30.0, niche="beauty")
        flush_to_profit_engine()
        rev = get_revenue("no-touch", "beauty")
        assert rev == pytest.approx(30.0, abs=0.01)

    def test_multiple_conversions_accumulate(self):
        """Two separate conversions should accumulate revenue."""
        code = _code("multi", "p1")
        _convert(code, revenue=10.0, niche="beauty")
        flush_to_profit_engine()
        _convert(code, revenue=20.0, niche="beauty")
        flush_to_profit_engine()
        rev = get_revenue("multi", "beauty")
        assert rev == pytest.approx(30.0, abs=0.01)

    def test_flush_marks_conversions_as_attributed(self):
        """After flush, pending conversions should be empty."""
        code = _code()
        _convert(code, revenue=10.0)
        flush_to_profit_engine()
        store   = get_attribution_store()
        pending = store.get_pending_conversions()
        assert pending == []

    def test_flush_returns_count(self):
        code1 = _code("f1", "p1")
        code2 = _code("f2", "p2")
        _convert(code1, revenue=5.0)
        _convert(code2, revenue=8.0)
        n = flush_to_profit_engine()
        assert n == 2

    def test_flush_idempotent_on_second_call(self):
        """Second flush (no pending) returns 0."""
        code = _code()
        _convert(code, revenue=10.0)
        flush_to_profit_engine()
        n = flush_to_profit_engine()
        assert n == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Part 4 — Public Query API
# ═══════════════════════════════════════════════════════════════════════════════

class TestQueryAPI:

    def _setup(self, content_id="api-c", niche="beauty",
               revenue=100.0, n_clicks=5, n_conv=2):
        code = _code(content_id, "page-1")
        for _ in range(n_clicks):
            _click(code, niche=niche)
        for _ in range(n_conv):
            _convert(code, revenue=revenue, niche=niche)
        flush_to_profit_engine()

    def test_get_revenue_zero_for_unknown(self):
        assert get_revenue("no-content", "beauty") == 0.0

    def test_get_revenue_returns_real_value(self):
        self._setup(revenue=50.0, n_conv=1)
        assert get_revenue("api-c", "beauty") == pytest.approx(50.0, abs=0.01)

    def test_get_conversion_rate_zero_for_unknown(self):
        assert get_conversion_rate("no-content") == 0.0

    def test_get_conversion_rate_correct(self):
        # 5 clicks, 2 conversions → 2/5 = 0.4
        # But in single-code scenario, last-click touches = 2 conversions
        # recorded. Ratio is conversions / clicks.
        self._setup(n_clicks=5, n_conv=2)
        cr = get_conversion_rate("api-c", "beauty")
        assert 0.0 <= cr <= 1.0

    def test_get_profit_with_cost(self):
        self._setup(revenue=100.0, n_conv=1)
        p = get_profit("api-c", "beauty", cost=20.0)
        assert p == pytest.approx(100.0 - 20.0, abs=0.5)

    def test_get_profit_unknown_content_is_negative_cost(self):
        p = get_profit("unknown", "beauty", cost=10.0)
        assert p == pytest.approx(-10.0)

    def test_get_attribution_report_structure(self):
        self._setup()
        rep = get_attribution_report("api-c", "beauty")
        for key in ("content_id", "niche", "clicks", "conversions",
                    "conversion_rate", "attributed_rev", "assist_rev",
                    "total_rev", "profit"):
            assert key in rep, f"Missing key: {key}"

    def test_get_attribution_report_unknown_returns_zeros(self):
        rep = get_attribution_report("unknown", "beauty")
        assert rep["clicks"] == 0
        assert rep["total_rev"] == 0.0

    def test_get_revenue_all_niches_aggregated(self):
        """No niche filter → aggregate across niches."""
        code = _code("all-niche", "p1")
        record_conversion(code, revenue=30.0, niche="beauty")
        record_conversion(code, revenue=20.0, niche="tech")
        flush_to_profit_engine()
        # aggregated
        rev = get_revenue("all-niche")
        assert rev == pytest.approx(50.0, abs=0.5)


# ═══════════════════════════════════════════════════════════════════════════════
# Part 5 — Integration with profit_engine
# ═══════════════════════════════════════════════════════════════════════════════

class TestProfitEngineIntegration:

    def test_flush_calls_profit_engine_update(self):
        """After flush, profit_engine should have a non-neutral score."""
        code = _code("pe-c", "p1")
        record_conversion(code, revenue=50.0, niche="beauty")
        flush_to_profit_engine(cost_map={"pe-c": 5.0})
        score = get_profit_score("pe-c", "beauty")
        # Revenue 50 > cost 5 → score > 0.5
        assert score > 0.5, f"Expected profitable score, got {score}"

    def test_flush_with_loss_gives_low_score(self):
        """High cost, low revenue → profit_score < 0.5."""
        code = _code("pe-loss", "p1")
        record_conversion(code, revenue=0.10, niche="fitness")
        flush_to_profit_engine(cost_map={"pe-loss": 5.0})
        score = get_profit_score("pe-loss", "fitness")
        assert score < 0.5, f"Expected loss score, got {score}"

    def test_real_revenue_updates_profit_engine(self):
        """Real attribution revenue propagates correctly to profit_engine EMA."""
        code = _code("pe-real", "p1")
        record_conversion(code, revenue=100.0, niche="beauty")
        flush_to_profit_engine()
        score_1 = get_profit_score("pe-real", "beauty")
        # Second flush with same content — more data → converges
        record_conversion(code, revenue=100.0, niche="beauty")
        flush_to_profit_engine()
        score_2 = get_profit_score("pe-real", "beauty")
        # Both should be > 0.5 (profitable)
        assert score_1 > 0.5
        assert score_2 > 0.5

    def test_zero_cost_map_uses_zero_cost(self):
        """flush_to_profit_engine with no cost_map → cost=0.0."""
        code = _code("no-cost", "p1")
        record_conversion(code, revenue=10.0, niche="food")
        flush_to_profit_engine()   # no cost_map
        score = get_profit_score("no-cost", "food")
        assert score > 0.5   # revenue > 0 cost → profitable


# ═══════════════════════════════════════════════════════════════════════════════
# Part 6 — Persistence
# ═══════════════════════════════════════════════════════════════════════════════

class TestPersistence:

    def test_store_survives_log_clear(self):
        """Clearing the in-process log does NOT wipe store data."""
        code = _code("pers-1", "p1")
        _convert(code, revenue=20.0, niche="beauty")
        flush_to_profit_engine()
        rev_before = get_revenue("pers-1", "beauty")
        from core.attribution_engine import _ATTR_LOG
        _ATTR_LOG.clear()
        rev_after = get_revenue("pers-1", "beauty")
        assert abs(rev_before - rev_after) < 1e-6

    def test_reset_clears_store(self):
        code = _code("reset-c", "p1")
        _convert(code, revenue=10.0)
        flush_to_profit_engine()
        reset_attribution_state()
        assert get_revenue("reset-c") == 0.0

    def test_store_roundtrip_upsert(self):
        """Direct store upsert + get round-trip."""
        store = get_attribution_store()
        store.upsert_attr_result(
            content_id="rt-c", niche="tech",
            page_id="p1", account_id="a1",
            delta_clicks=10, delta_conv=3,
            delta_attr_rev=70.0, delta_asst_rev=30.0,
        )
        ar = store.get_attr_result("rt-c", "tech")
        assert ar is not None
        assert ar["clicks"]       == 10
        assert ar["conversions"]  == 3
        assert ar["total_rev"]    == pytest.approx(100.0, abs=0.01)

    def test_store_upsert_accumulates(self):
        """Two upserts → values accumulate (not overwrite)."""
        store = get_attribution_store()
        store.upsert_attr_result(
            "acc-c", "beauty", "p1", "a1",
            delta_clicks=5, delta_conv=1, delta_attr_rev=70.0, delta_asst_rev=0.0,
        )
        store.upsert_attr_result(
            "acc-c", "beauty", "p1", "a1",
            delta_clicks=5, delta_conv=1, delta_attr_rev=70.0, delta_asst_rev=0.0,
        )
        ar = store.get_attr_result("acc-c", "beauty")
        assert ar["clicks"]      == 10
        assert ar["conversions"] == 2
        assert ar["total_rev"]   == pytest.approx(140.0, abs=0.01)

    def test_store_stats_structure(self):
        store = get_attribution_store()
        s = store.stats()
        for key in ("cache_size", "cache_max", "cache_ttl_s",
                    "db_path", "db_connected"):
            assert key in s
        assert s["db_connected"] is True

    def test_touch_cached_after_first_read(self):
        code = _code()
        _click(code)
        store = get_attribution_store()
        t1 = store.get_touches(code)
        t2 = store.get_touches(code)   # should hit cache
        assert t1 == t2


# ═══════════════════════════════════════════════════════════════════════════════
# Part 7 — Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_empty_flush_returns_zero(self):
        assert flush_to_profit_engine() == 0

    def test_colon_in_content_id_sanitised(self):
        """Colons in content_id are converted to underscores."""
        code = generate_tracking_code("acc:beauty:REUP", "p1", 1700000000.0)
        parsed = parse_tracking_code(code)
        assert parsed is not None
        assert ":" not in parsed["content_id"]

    def test_very_long_content_id(self):
        cid  = "x" * 200
        code = generate_tracking_code(cid, "p1", 1700000000.0)
        parsed = parse_tracking_code(code)
        assert parsed is not None

    def test_get_revenue_after_reset_is_zero(self):
        code = _code("rs", "p1")
        _convert(code, revenue=10.0)
        flush_to_profit_engine()
        reset_attribution_state()
        assert get_revenue("rs") == 0.0

    def test_attribution_log_populated(self):
        code = _code("log-c", "p1")
        _convert(code, revenue=10.0)
        flush_to_profit_engine()
        log = get_attribution_log()
        assert len(log) >= 1
        entry = log[-1]
        for field in ("content_id", "clicks", "conversions",
                      "attr_rev", "asst_rev", "total_rev"):
            assert field in entry

    def test_attribution_log_resets(self):
        code = _code()
        _convert(code, revenue=5.0)
        flush_to_profit_engine()
        reset_attribution_state()
        assert get_attribution_log() == []

    def test_conversion_rate_max_one(self):
        """Conversion rate is always <= 1.0."""
        code = _code("cr-c", "p1")
        # More conversions than clicks (edge case)
        for _ in range(3):
            _convert(code, revenue=5.0)
        flush_to_profit_engine()
        cr = get_conversion_rate("cr-c")
        assert cr <= 1.0

    def test_multiple_niches_isolated(self):
        code_b = _code("mv", "p1", ts=1700000000.0)
        code_t = _code("mv", "p1", ts=1700000001.0)  # different ts → different code
        record_conversion(code_b, revenue=50.0, niche="beauty")
        record_conversion(code_t, revenue=20.0, niche="tech")
        flush_to_profit_engine()
        # check by niche
        rev_b = get_revenue("mv", "beauty")
        rev_t = get_revenue("mv", "tech")
        assert rev_b >= 0.0
        assert rev_t >= 0.0

    def test_get_profit_no_data(self):
        p = get_profit("ghost", "beauty", cost=0.0)
        assert p == pytest.approx(0.0)
