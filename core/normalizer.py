from typing import Any, List, Dict

def normalize(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Normalize raw scraped data into a unified candidate format.
    """
    out = []
    for r in raw:
        out.append({
            "content_id": r.get("content_id"),
            "caption": r.get("caption", ""),
            "hook_text": r.get("hook_text", ""),
            "view_count": float(r.get("view_count", 0)),
            "engagement_metrics": r.get("engagement_metrics", {}),
            "niche": r.get("niche", "general"),
            "created_at": r.get("created_at")
        })
    return out
