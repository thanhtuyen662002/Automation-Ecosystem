"""
Validator — Anti-pattern detection, entropy analysis, and risk flagging.
"""
from __future__ import annotations

import hashlib
import logging
import math
from typing import Any

LOGGER = logging.getLogger("core.validator")

MIN_ENTROPY_BITS: float = 1.5
SPIKE_COLLISION_THRESHOLD: float = 0.25
MAX_REPEAT_CHAIN_LEN: int = 3
BURST_DENSITY_THRESHOLD: float = 0.60
BURST_WINDOW_S: int = 600


def _shannon_entropy(values: list[str]) -> float:
    if not values:
        return 0.0
    from collections import Counter
    counts = Counter(values)
    n = len(values)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _fingerprint(sequence: list[str]) -> str:
    joined = "|".join(sequence)
    return hashlib.sha256(joined.encode()).hexdigest()[:12]


def validate_account(logs: list[dict[str, Any]]) -> dict[str, Any]:
    """Run all per-account checks. Returns risk_score + flags dict."""
    if not logs:
        return {"risk_score": 0.0, "flags": [], "entropy_bits": 0.0,
                "chain_repeat_count": 0, "burst_density": 0.0}

    flags: list[str] = []
    risk = 0.0

    # 1. Shannon entropy
    intents = [log.get("intent", "") for log in logs]
    ent = _shannon_entropy(intents)
    if ent < MIN_ENTROPY_BITS and len(logs) >= 10:
        flags.append(f"low_entropy_intent:{ent:.2f}bits")
        risk += 0.25

    # 2. Repeated action-chain detection
    chain_repeats = 0
    if len(logs) >= MAX_REPEAT_CHAIN_LEN * 2:
        window = MAX_REPEAT_CHAIN_LEN
        seen_fps: dict[str, int] = {}
        for i in range(len(logs) - window + 1):
            seq = [logs[j].get("intent", "") + ":" + logs[j].get("role", "")
                   for j in range(i, i + window)]
            fp = _fingerprint(seq)
            seen_fps[fp] = seen_fps.get(fp, 0) + 1
        chain_repeats = sum(1 for c in seen_fps.values() if c > 1)
        if chain_repeats > 2:
            flags.append(f"repeated_action_chains:{chain_repeats}")
            risk += min(0.30, chain_repeats * 0.05)

    # 3. Burst density
    timestamps = [log.get("ts", 0.0) for log in logs if log.get("ts")]
    burst_density = 0.0
    if len(timestamps) >= 5:
        t_min, t_max = min(timestamps), max(timestamps)
        in_window = sum(1 for t in timestamps if t >= t_max - BURST_WINDOW_S)
        burst_density = in_window / len(timestamps)
        if burst_density > BURST_DENSITY_THRESHOLD:
            flags.append(f"burst_density:{burst_density:.2f}")
            risk += 0.20

    return {
        "risk_score":         round(min(1.0, risk), 4),
        "flags":              flags,
        "entropy_bits":       round(ent, 3),
        "chain_repeat_count": chain_repeats,
        "burst_density":      round(burst_density, 3),
    }


def validate_fleet(account_logs: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Fleet-wide validation: spike detection + system health score."""
    per_account: dict[str, Any] = {}
    all_timings: list[int] = []

    for acct, logs in account_logs.items():
        per_account[acct] = validate_account(logs)
        for log in logs:
            offset = log.get("modifiers", {}).get("timing_offset_s")
            if offset is not None:
                all_timings.append(int(offset))

    spike_flag = False
    spike_collision_rate = 0.0
    if len(all_timings) >= 10:
        from collections import Counter
        counts = Counter(all_timings)
        n = len(all_timings)
        non_unique = sum(c for c in counts.values() if c > 1)
        spike_collision_rate = non_unique / n
        if spike_collision_rate > SPIKE_COLLISION_THRESHOLD:
            spike_flag = True

    high_risk = [a for a, r in per_account.items() if r["risk_score"] >= 0.40]
    avg_risk = (sum(r["risk_score"] for r in per_account.values()) / len(per_account)
                if per_account else 0.0)
    health = round(max(0.0, min(1.0, 1.0 - avg_risk * 0.6 - (0.4 if spike_flag else 0.0))), 4)

    return {
        "system_health_score":  health,
        "spike_flag":           spike_flag,
        "spike_collision_rate": round(spike_collision_rate, 4),
        "per_account":          per_account,
        "high_risk_accounts":   high_risk,
    }
