import time
from typing import Any, List, Dict

def pre_score_filter(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Compute a cheap score before sending to heavy pipeline.
    Reject if score < threshold (0.4).
    No random noise — all signals are deterministic.
    """
    out = []
    now = time.time()

    for c in candidates:
        eng   = c["engagement_metrics"].get("engagement_rate", 0)
        views = c.get("view_count", 0)

        # Freshness: 1.0 if just published, decays to 0 over 24h
        created_at = float(c.get("created_at", now))
        freshness  = 1.0 - min(1.0, (now - created_at) / 86400)

        score = (
            0.5 * eng +
            0.3 * min(1.0, views / 50000) +
            0.2 * freshness
        )

        if score > 0.4:
            out.append(c)

    return out
