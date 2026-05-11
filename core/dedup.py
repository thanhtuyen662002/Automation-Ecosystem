from typing import Any, List, Dict

_seen = set()

def deduplicate(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Remove duplicated contents based on perceptual hash (or caption+hook fallback).
    """
    out = []
    for c in candidates:
        key = (c.get("caption", "")[:50], c.get("hook_text", "")[:50])
        if key in _seen:
            continue
        _seen.add(key)
        out.append(c)

    return out
