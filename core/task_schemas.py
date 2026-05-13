from typing import Dict, List

TASK_SCHEMAS: Dict[str, Dict[str, List[str]]] = {
    "generate_caption_ai": {
        "required": ["prompt"],
        "optional": ["tags"],
        "output": ["tiktok_caption", "youtube_title", "youtube_description", "youtube_tags", "facebook_description"]
    },
    "publish_tiktok": {
        "required": ["video_path", "caption", "account_id"],
        "optional": [],
        "output": ["video_url", "final_url", "published"]
    },
    "tiktok.extract_product_info": {
        "required": [],
        "optional": ["product_url", "product_image_path"],
        "output": ["title", "description", "keywords", "ok"]
    },
    "tiktok.search_tiktok": {
        "required": ["keywords", "max_results"],
        "optional": [],
        "output": ["videos", "ok"]
    },
    "tiktok.select_videos": {
        "required": ["videos"],
        "optional": ["min_views", "min_likes", "min_duration", "max_duration", "top_n"],
        "output": ["selected_videos", "ok"]
    },
    "tiktok.download_videos": {
        "required": ["selected_videos"],
        "optional": [],
        "output": ["video_paths", "failed_urls", "output_dir", "ok"]
    },
    "tiktok.remake_video": {
        "required": ["video_paths"],
        "optional": ["title", "hook_text", "add_grain", "bgm_path"],
        "output": ["output_path", "duration", "segment_count", "ok"]
    },
    "tiktok.generate_content": {
        "required": ["title", "description", "keywords"],
        "optional": [],
        "output": ["caption", "hashtags", "ok"]
    },
    "tiktok.generate_comment": {
        "required": ["caption", "title", "keywords", "count"],
        "optional": [],
        "output": ["comments", "ok"]
    },
    "publish_youtube": {
        "required": ["video_path", "title", "description", "account_id"],
        "optional": ["tags"],
        "output": ["video_url"]
    },
    "publish_facebook": {
        "required": ["video_path", "description", "account_id"],
        "optional": [],
        "output": ["video_url"]
    },
    "ai": {
        "required": ["prompt"],
        "optional": [],
        "output": ["result"]
    },
    "browser": {
        "required": ["url"],
        "optional": [],
        "output": ["result"]
    },
    "media": {
        "required": ["input_path"],
        "optional": [],
        "output": ["output_path"]
    }
}
