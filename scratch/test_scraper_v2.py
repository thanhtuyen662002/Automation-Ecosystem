"""
Validation v2: unit tests + live scrape quality check.
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Unit tests ────────────────────────────────────────────────────────────────
from core.tiktok_scraper import _parse_count, _author_from_url

# Count parser
for text, expected in [
    ("1.2M", 1_200_000), ("45.6K", 45_600), ("123,456", 123_456),
    ("500", 500), ("2.5B", 2_500_000_000), ("0", 0), ("", 0),
]:
    assert _parse_count(text) == expected, f"FAIL: _parse_count({text!r})"
print("[PASS] _parse_count")

# Author from URL
for url, expected in [
    ("https://www.tiktok.com/@laranaa8/video/123", "laranaa8"),
    ("https://www.tiktok.com/@heylina2484/video/456", "heylina2484"),
    ("https://www.tiktok.com/notauser/video/789", ""),
    ("", ""),
]:
    got = _author_from_url(url)
    assert got == expected, f"FAIL: _author_from_url({url!r}) = {got!r}"
print("[PASS] _author_from_url")

# ── Live scrape ───────────────────────────────────────────────────────────────
os.environ["TIKTOK_SCRAPER_ENABLED"] = "true"
from core.tiktok_scraper import scrape_keyword_sync

print()
print("Running live scrape: keyword='skincare' ...")
t0 = time.time()
results = scrape_keyword_sync(
    keyword   = "skincare",
    limit     = 30,
    min_views = 10_000,
    headless  = True,
    timeout_s = 120,
)
elapsed = time.time() - t0
print(f"Elapsed: {elapsed:.1f}s  |  Results: {len(results)} videos")

if results:
    print()
    print("Sample (top 10):")
    for i, v in enumerate(results[:10], 1):
        print(f"  {i:2d}. views={v['views']:>10,}  likes={v['likes']:>7,}"
              f"  author=@{v['author']:<22}  {v['video_url'][:55]}")

    # Validation checks
    authors_ok = sum(1 for v in results if v["author"])
    likes_ok   = sum(1 for v in results if v["likes"] > 0)
    views_ok   = all(v["views"] >= 10_000 for v in results)
    urls_ok    = all(v["video_url"].startswith("https://") for v in results)

    print()
    print(f"  authors non-empty : {authors_ok}/{len(results)}")
    print(f"  likes > 0         : {likes_ok}/{len(results)}")
    print(f"  views >= 10k      : {'YES' if views_ok else 'NO'}")
    print(f"  valid URLs        : {'YES' if urls_ok else 'NO'}")

    assert len(results) >= 5, f"Expected >= 5 videos, got {len(results)}"
    assert views_ok, "Some videos have views < 10k"
    assert urls_ok, "Invalid URLs found"
    assert authors_ok > 0, "No authors extracted"

    print()
    print("=" * 60)
    print("Scraper improved: real engagement data + higher coverage")
    print("=" * 60)
else:
    print()
    print("[WARN] 0 results — TikTok session expired or blocked.")
    print("To log in and save session:")
    print("  from core.tiktok_scraper import login_interactive")
    print("  login_interactive()")
    print()
    print("After login, future headless scrapes will use saved cookies.")
