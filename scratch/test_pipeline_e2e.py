"""End-to-end test: search_tiktok_handler + select_videos_handler."""
import asyncio
import sys
sys.path.insert(0, ".")

async def main() -> None:
    from workers.handlers.tiktok.search_tiktok import search_tiktok_handler
    from workers.handlers.tiktok.select_videos import select_videos_handler

    print("=== Step 1: search_tiktok_handler ===")
    search_payload = {
        "keywords": ["phone case review"],
        "max_results": 5,
    }
    search_result = await search_tiktok_handler(search_payload)
    videos = search_result.get("videos", [])
    print(f"Videos found: {len(videos)}")
    for v in videos:
        print(f"  - views={v.get('views')} duration={v.get('duration')}s title={v.get('title','')[:60]}")

    print("\n=== Step 2: select_videos_handler ===")
    select_payload = {
        "videos": videos,
        "min_views": 10000,
        "min_duration": 15.0,
        "max_duration": 900.0,  # YouTube vids can be long
        "min_engagement_rate": 0.001,  # Very low for YouTube
        "top_n": 3,
    }
    try:
        select_result = await select_videos_handler(select_payload)
        selected = select_result.get("selected_videos", [])
        stats = select_result.get("filter_stats", {})
        print(f"Filter stats: {stats}")
        print(f"Selected {len(selected)} videos:")
        for v in selected:
            print(f"  - score={v['score']} views={v['views']} duration={v['duration']}s url={v['url'][:60]}")
    except RuntimeError as e:
        print(f"select_videos FAILED: {e}")

asyncio.run(main())
