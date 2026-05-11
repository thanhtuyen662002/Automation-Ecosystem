import random
import time
from typing import Any, List, Dict

def fetch_trending(limit: int) -> List[Dict[str, Any]]:
    """
    Scrape trending feeds (TikTok, Reels, Shorts).
    Extracts video_url, caption, hashtags, view_count, metrics, etc.
    
    Currently a MOCK SAFE VERSION (to be replaced with real API/scraper).
    """
    return [
        {
            "content_id": f"trend_{i}",
            "caption": "viral content example",
            "hook_text": "this is crazy",
            "view_count": random.randint(1000, 50000),
            "engagement_metrics": {
                "engagement_rate": random.uniform(0.03, 0.12),
                "share_rate": random.uniform(0.01, 0.08),
                "save_rate": random.uniform(0.01, 0.05),
            },
            "niche": "general",
            "created_at": time.time()
        }
        for i in range(limit)
    ]
