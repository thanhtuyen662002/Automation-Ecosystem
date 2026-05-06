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
        "output": ["video_url"]
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
