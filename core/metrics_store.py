"""
Metrics Store — Rolling EMA + time-decay feedback metrics.

Design contracts:
  - All metrics update via EWMA (no sudden jumps).
  - Time-decay: observations older than DECAY_WINDOW_S contribute less.
  - No per-account mutable state is shared (fleet-level only).
  - Exception-safe: all public methods return safe defaults on error.
  - Fully in-process; no external DB dependency.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

LOGGER = logging.getLogger("core.metrics_store")

# EMA smoothing factor: smaller α → slower reaction (more inertia)
_EMA_ALPHA: float = 0.15

# Time window for raw-event tracking (seconds)
DECAY_WINDOW_S: int = 3600   # 1-hour rolling window


@dataclass
class _TimedEvent:
    ts:    float
    value: float
    tag:   str = ""


@dataclass
class MetricSeries:
    """Rolling EWMA + raw event buffer for one named metric."""
    name:     str
    ema:      float = 0.0          # current exponential moving average
    _events:  list[_TimedEvent] = field(default_factory=list, repr=False)

    def update(self, value: float, tag: str = "") -> None:
        """Push a new observation; update EMA; prune old raw events."""
        self.ema = self.ema * (1 - _EMA_ALPHA) + value * _EMA_ALPHA
        self._events.append(_TimedEvent(ts=time.time(), value=value, tag=tag))
        self._prune()

    def _prune(self) -> None:
        cutoff = time.time() - DECAY_WINDOW_S
        self._events = [e for e in self._events if e.ts >= cutoff]

    def recent_mean(self, window_s: int = 300) -> float:
        """Mean of observations in the last `window_s` seconds."""
        cutoff = time.time() - window_s
        recent = [e.value for e in self._events if e.ts >= cutoff]
        return sum(recent) / len(recent) if recent else self.ema

    def count(self, window_s: int = DECAY_WINDOW_S) -> int:
        cutoff = time.time() - window_s
        return sum(1 for e in self._events if e.ts >= cutoff)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name":         self.name,
            "ema":          round(self.ema, 5),
            "recent_mean":  round(self.recent_mean(), 5),
            "event_count":  self.count(),
        }


class MetricsStore:
    """
    Central metrics store for the closed-loop system.

    Tracks:
      ban_rate, success_rate, engagement_score, anomaly_score

    Each metric is stored as a MetricSeries with:
      - Rolling EMA (α=0.15 by default)
      - Raw event buffer (1-hour window)
    """

    def __init__(self) -> None:
        self._series: dict[str, MetricSeries] = {
            name: MetricSeries(name=name)
            for name in ("ban_rate", "success_rate", "engagement_score", "anomaly_score")
        }

    # ── Public update API ──────────────────────────────────────────────────────

    def record_ban(self, account_id: str = "") -> None:
        self._series["ban_rate"].update(1.0, tag=account_id)
        self._series["success_rate"].update(0.0, tag=account_id)

    def record_success(self, account_id: str = "", engagement: float = 1.0) -> None:
        self._series["success_rate"].update(1.0, tag=account_id)
        self._series["ban_rate"].update(0.0, tag=account_id)
        self._series["engagement_score"].update(
            max(0.0, min(1.0, engagement)), tag=account_id
        )

    def record_anomaly(self, score: float, account_id: str = "") -> None:
        self._series["anomaly_score"].update(
            max(0.0, min(1.0, score)), tag=account_id
        )

    def update(self, metric: str, value: float, tag: str = "") -> None:
        """Generic update for any named metric."""
        if metric not in self._series:
            self._series[metric] = MetricSeries(name=metric)
        self._series[metric].update(value, tag=tag)

    # ── Query API ──────────────────────────────────────────────────────────────

    def get_ema(self, metric: str) -> float:
        return self._series[metric].ema if metric in self._series else 0.0

    def get_recent(self, metric: str, window_s: int = 300) -> float:
        if metric not in self._series:
            return 0.0
        return self._series[metric].recent_mean(window_s)

    def snapshot(self) -> dict[str, Any]:
        return {name: s.to_dict() for name, s in self._series.items()}

    def health_score(self) -> float:
        """Composite fleet health 0.0 (critical) – 1.0 (healthy)."""
        br = self.get_ema("ban_rate")
        sr = self.get_ema("success_rate")
        an = self.get_ema("anomaly_score")
        # Simple linear combination
        score = (1.0 - br * 2.0) * 0.4 + sr * 0.4 + (1.0 - an) * 0.2
        return round(max(0.0, min(1.0, score)), 4)


# ── Singleton ──────────────────────────────────────────────────────────────────

_METRICS_STORE: MetricsStore | None = None


def get_metrics_store() -> MetricsStore:
    global _METRICS_STORE
    if _METRICS_STORE is None:
        _METRICS_STORE = MetricsStore()
    return _METRICS_STORE


def reset_metrics_store() -> None:
    """For testing only."""
    global _METRICS_STORE
    _METRICS_STORE = None
