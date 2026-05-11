import time
from collections import defaultdict
from typing import Any, List, Dict

def get_candidates(limit: int = 200) -> List[Dict[str, Any]]:
    """
    Main entry point for the Input Engine.
    Discovers trends, normalizes, deduplicates, and pre-scores candidates.
    """
    from .trend_collector import fetch_trending
    from .normalizer import normalize
    from .dedup import deduplicate
    from .pre_score import pre_score_filter

    # 1. Fetch raw trending data from sources
    raw = fetch_trending(limit)

    # 2. Normalize to standard internal format
    norm = normalize(raw)

    # 3. Deduplicate based on content hashes
    clean = deduplicate(norm)

    # 4. Filter cheap candidates out
    filtered = pre_score_filter(clean)

    # 5. Hard cap per niche — prevents any single niche from spamming pipeline
    _NICHE_CAP = 30
    cap: defaultdict[str, int] = defaultdict(int)
    final: List[Dict[str, Any]] = []
    for c in filtered:
        n = c.get("niche", "general")
        if cap[n] >= _NICHE_CAP:
            continue
        cap[n] += 1
        final.append(c)

    return final
