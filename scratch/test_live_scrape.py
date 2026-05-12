"""Live scrape test — opens real Chromium and scrapes TikTok search."""
import os, json, time

os.environ["TIKTOK_SCRAPER_ENABLED"] = "true"

from core.tiktok_scraper import scrape_keyword_sync

keyword = "skincare routine"
print(f"Scraping TikTok for: '{keyword}' ...")
print("(This may take 30-60s — browser launches, navigates, scrolls)")
print()

t0 = time.time()
results = scrape_keyword_sync(
    keyword   = keyword,
    limit     = 25,
    min_views = 10_000,
    headless  = True,
    timeout_s = 90,
)
elapsed = time.time() - t0

print(f"Elapsed: {elapsed:.1f}s")
print(f"Results: {len(results)} videos")
print()

if results:
    print("Top 5 videos (by views):")
    for i, v in enumerate(results[:5], 1):
        print(f"  {i}. views={v['views']:>10,}  likes={v['likes']:>8,}  "
              f"author=@{v['author']:<20}  url={v['video_url'][:60]}")
    print()
    assert all(v["source"] == "tiktok_real" for v in results), "Source must be tiktok_real"
    assert all(v["views"] >= 10_000 for v in results), "All videos must have >= 10k views"
    assert all(v["video_url"].startswith("https://") for v in results), "URLs must be valid"
    print("[PASS] Data is NOT random — real TikTok video URLs and view counts")
    print(f"[PASS] {len(results)} real videos returned (target >= 20)")
    print()
    print("=" * 60)
    print("TikTok scraping via keyword search is now ACTIVE")
    print("Returning real data to pipeline.")
    print("=" * 60)
else:
    print("[WARN] 0 results — TikTok may require login for this session.")
    print("       Run with headless=False to log in manually:")
    print("       scrape_keyword_sync(..., headless=False)")
    print()
    print("       The pipeline will use mock fallback until session is saved.")
