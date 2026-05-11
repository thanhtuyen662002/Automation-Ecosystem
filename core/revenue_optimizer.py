"""
core/revenue_optimizer.py — Revenue Signal Provider (signals only)

ROLE CHANGE (unified scoring refactor):
  - NO longer decides strategy (scale/optimize/explore)
  - NO longer controls distribution multiplier
  - ONLY supplies revenue-quality signals to execution_brain

Kept:
  should_explore()        — global exploration guard (delegates to unified_scoring)
  get_distribution_multiplier() — kept as SIGNAL only; execution_brain decides if used

Removed:
  decide_strategy()       — decision authority moved to execution_brain
  _CONSECUTIVE_SCALE      — overfit guard moved to execution_brain
  threshold-based branching
"""
from __future__ import annotations

import hashlib
from typing import Any

# ── Exploration config (mirrors unified_scoring) ──────────────────────────────
_EXPLORE_RATE = 0.10

# ── Distribution multiplier bounds (signal only — brain decides use) ──────────
_SCALE_MULT_MIN = 1.3
_SCALE_MULT_MAX = 1.8


def should_explore(content_id: str, seed: str = "") -> bool:
    """
    Deterministic 10% exploration gate.
    Delegates to unified_scoring.should_explore when available,
    falls back to local hash implementation.
    """
    try:
        from core.unified_scoring import should_explore as _ue
        return _ue(content_id, seed=seed)
    except Exception:
        raw = seed or content_id
        h   = int(hashlib.sha256(raw.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        return h < _EXPLORE_RATE


def get_distribution_multiplier(
    unified_score: float,
    candidate:     dict[str, Any] | None = None,
) -> float:
    """
    Returns a distribution volume multiplier as a SIGNAL.
    execution_brain decides whether to apply it.

    Mapping (driven by unified_score, not local strategy decision):
        unified_score >= 0.75 → 1.3 – 1.8 (pattern_score interpolated)
        otherwise              → 1.0
    """
    if unified_score < 0.75:
        return 1.0
    candidate     = candidate or {}
    pattern_score = float(candidate.get("pattern_score", 0.5))
    t = max(0.0, min(1.0, (pattern_score - 0.5) / 0.5))
    return round(_SCALE_MULT_MIN + t * (_SCALE_MULT_MAX - _SCALE_MULT_MIN), 3)
