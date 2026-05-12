"""Validation: tiktok_scraper + trend_collector integration test."""
import os, sys, json

# ── 1. Mock fallback test ──────────────────────────────────────────────────────
os.environ["TIKTOK_SCRAPER_ENABLED"] = "false"

from core.trend_collector import fetch_trending

results = fetch_trending(limit=5, keyword="skincare")
assert len(results) == 5, f"Expected 5 mock items, got {len(results)}"
assert all(r["source"] == "mock" for r in results), "All should be mock"
assert all(r["view_count"] > 0 for r in results), "Views must be > 0"
assert all("engagement_metrics" in r for r in results), "Missing engagement_metrics"
print(f"[PASS] Mock fallback: {len(results)} items")
for r in results[:2]:
    print(f"       source={r['source']} views={r['view_count']:,} niche={r['niche']}")

# ── 2. Count parser test ───────────────────────────────────────────────────────
from core.tiktok_scraper import _parse_count
cases = [
    ("1.2M", 1_200_000),
    ("45.6K", 45_600),
    ("123,456", 123_456),
    ("500", 500),
    ("2.5B", 2_500_000_000),
    ("0", 0),
    ("", 0),
]
for text, expected in cases:
    got = _parse_count(text)
    assert got == expected, f"_parse_count({text!r}) = {got}, want {expected}"
print("[PASS] _parse_count: all cases correct")

# ── 3. Normalize real item test ────────────────────────────────────────────────
import importlib
tc = importlib.import_module("core.trend_collector")
raw_item = {
    "video_url":  "https://www.tiktok.com/@user/video/123",
    "author":     "testuser",
    "caption":    "Amazing skincare routine for glowing skin",
    "views":      150_000,
    "likes":      12_000,
    "comments":   800,
    "thumbnail":  "https://cdn.tiktok.com/thumb.jpg",
    "keyword":    "skincare",
    "scraped_at": 1700000000,
    "source":     "tiktok_real",
}
normed = tc._normalize_real(raw_item, 0)
assert normed["view_count"] == 150_000
assert normed["source"] == "tiktok_real"
assert normed["video_url"] == raw_item["video_url"]
assert 0 < normed["engagement_metrics"]["engagement_rate"] < 1
print("[PASS] _normalize_real: fields correct")
print(f"       eng_rate={normed['engagement_metrics']['engagement_rate']:.4f}")

# ── 4. Pipeline compatibility test ────────────────────────────────────────────
# Ensure output is JSON-serializable (pipeline serialises results)
try:
    json.dumps(results)
    json.dumps(normed)
    print("[PASS] JSON serialisable: OK")
except Exception as e:
    print(f"[FAIL] JSON serialisation error: {e}")
    sys.exit(1)

print()
print("=" * 55)
print("ALL VALIDATION TESTS PASSED")
print("TikTok scraping via keyword search is now active.")
print("Real scraper: TIKTOK_SCRAPER_ENABLED=true (default)")
print("Mock fallback: TIKTOK_SCRAPER_ENABLED=false")
print("=" * 55)
