"""
Detector Simulator — Platform-side detection AI simulation.

Simulates how real platforms detect automation using 5 independent
signal groups, weighted aggregation, temporal smoothing, and flag generation.

Architecture contracts:
  - ZERO cross-account state access: cross-account signals use deterministic
    pseudo-grouping via stable_hash_int only.
  - All scores bounded [0.0, 1.0].
  - Risk changes smoothly: EMA(prev=0.7, current=0.3).
  - No jumps > 0.40 in a single cycle (hard delta clamp).
  - Deterministic: same (account_id, logs, now) → same result.
  - Explainable: every sub-score has a reasoning entry.

Signal groups (weights):
  A. Timing Anomaly        0.25  — unnatural delay patterns
  B. Behavioral Entropy    0.20  — lack of action diversity
  C. Session Pattern       0.20  — artificial session clustering
  D. Cross-Account Sim.    0.20  — pseudo-cluster behavioral fingerprint
  E. Lifecycle Consistency 0.15  — mismatch between stage and behavior

Flags raised when sub-score > threshold:
  timing_score > 0.70       → "timing_bot_like"
  entropy_score > 0.70      → "low_diversity"
  session_score > 0.70      → "unnatural_sessions"
  similarity_score > 0.75   → "cluster_behavior"
  lifecycle_score > 0.70    → "identity_mismatch"
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any

from core.mutation_controller import stable_hash_int

LOGGER = logging.getLogger("core.detector_simulator")

# ── Constants ─────────────────────────────────────────────────────────────────

# How many observer logs to use per evaluation
_LOG_WINDOW: int = 50

# EMA smoothing weights for risk score
_RISK_EMA_PREV:    float = 0.70
_RISK_EMA_CURRENT: float = 0.30

# Max single-cycle risk delta (smoothing safety)
_MAX_RISK_JUMP: float = 0.40

# Signal group weights (must sum to 1.0)
_WEIGHTS = {
    "timing":    0.25,
    "entropy":   0.20,
    "session":   0.20,
    "similarity": 0.20,
    "lifecycle": 0.15,
}

# Flag thresholds
_FLAG_THRESHOLDS = {
    "timing":    (0.70, "timing_bot_like"),
    "entropy":   (0.70, "low_diversity"),
    "session":   (0.70, "unnatural_sessions"),
    "similarity": (0.75, "cluster_behavior"),
    "lifecycle": (0.70, "identity_mismatch"),
}

# Cluster window size (seconds) for pseudo-grouping
_CLUSTER_WINDOW_S: int = 1800   # 30-minute buckets


# ── Output types ──────────────────────────────────────────────────────────────

@dataclass
class DetectionResult:
    account_id:  str
    risk_score:  float                  # 0.0 – 1.0 composite
    flags:       dict[str, float]       # flag_name → contributing sub-score
    reasoning:   dict[str, Any]         # full explainable breakdown
    sub_scores:  dict[str, float]       # raw group scores before weighting
    ts:          float = field(default_factory=time.time)

    def is_flagged(self) -> bool:
        return bool(self.flags)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "risk_score": round(self.risk_score, 4),
            "flags":      self.flags,
            "sub_scores": {k: round(v, 4) for k, v in self.sub_scores.items()},
            "reasoning":  self.reasoning,
            "ts":         self.ts,
        }


# ── Per-account risk memory ───────────────────────────────────────────────────

_RISK_MEMORY: dict[str, float] = {}   # account_id → smoothed risk score


# ── Helper: Shannon entropy ───────────────────────────────────────────────────

def _entropy(values: list[str]) -> float:
    """Shannon entropy (bits) normalised to [0, log2(n)] → [0, 1]."""
    if not values:
        return 0.0
    from collections import Counter
    counts = Counter(values)
    n = len(values)
    raw = -sum((c / n) * math.log2(c / n) for c in counts.values())
    max_entropy = math.log2(n) if n > 1 else 1.0
    return round(raw / max_entropy, 5) if max_entropy > 0 else 0.0


def _coefficient_of_variation(vals: list[float]) -> float:
    """CV = stddev/mean. Higher → more varied (more human)."""
    if len(vals) < 2:
        return 0.0
    mean = sum(vals) / len(vals)
    if mean == 0:
        return 0.0
    variance = sum((v - mean) ** 2 for v in vals) / len(vals)
    return math.sqrt(variance) / mean


# ── Signal A: Timing Anomaly ──────────────────────────────────────────────────

def _score_timing(logs: list[dict]) -> tuple[float, dict]:
    """
    Detect unnatural delay uniformity.

    Signals:
      - Low CV of delays (too consistent)
      - Repeated exact delay values (hash collision)
      - Burst density variance too low
    """
    delays = [log.get("delay_s", 0) for log in logs if log.get("delay_s", 0) > 0]
    reasoning: dict[str, Any] = {"n_delays": len(delays)}

    if len(delays) < 3:
        reasoning["skip"] = "insufficient data"
        return 0.0, reasoning

    # Signal 1: low CV → delays are unnaturally uniform
    cv = _coefficient_of_variation([float(d) for d in delays])
    uniformity_score = max(0.0, 1.0 - min(cv, 1.0))   # low CV → high score

    # Signal 2: repeated exact intervals
    from collections import Counter
    delay_counts = Counter(delays)
    n = len(delays)
    most_common_frac = delay_counts.most_common(1)[0][1] / n
    repetition_score = min(1.0, most_common_frac * 2.5)   # >40% same = 1.0

    # Signal 3: inter-arrival variance across timestamps
    timestamps = sorted(log.get("ts", 0.0) for log in logs if log.get("ts"))
    if len(timestamps) >= 4:
        gaps  = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
        gap_cv = _coefficient_of_variation(gaps)
        gap_score = max(0.0, 1.0 - min(gap_cv, 1.0))
    else:
        gap_score = 0.0

    score = 0.45 * uniformity_score + 0.35 * repetition_score + 0.20 * gap_score
    score = max(0.0, min(1.0, score))

    reasoning.update({
        "cv":               round(cv, 4),
        "uniformity_score": round(uniformity_score, 4),
        "repetition_score": round(repetition_score, 4),
        "gap_score":        round(gap_score, 4),
        "most_common_frac": round(most_common_frac, 4),
    })
    return score, reasoning


# ── Signal B: Behavioral Entropy ─────────────────────────────────────────────

def _score_entropy(logs: list[dict]) -> tuple[float, dict]:
    """
    Measure diversity of action types, niches, and roles.

    Low diversity = high detection risk.
    """
    intents  = [log.get("intent", "") for log in logs if log.get("intent")]
    niches   = [log.get("niche",  "") for log in logs if log.get("niche")]
    roles    = [log.get("role",   "") for log in logs if log.get("role")]

    reasoning: dict[str, Any] = {
        "n_intents": len(intents),
        "n_niches":  len(niches),
        "n_roles":   len(roles),
    }

    if len(intents) < 5:
        reasoning["skip"] = "insufficient data"
        return 0.0, reasoning

    intent_ent  = _entropy(intents)
    niche_ent   = _entropy(niches)
    role_ent    = _entropy(roles)

    # Invert: low entropy = high detection score
    intent_risk = 1.0 - intent_ent
    niche_risk  = 1.0 - niche_ent
    role_risk   = 1.0 - role_ent

    score = 0.40 * intent_risk + 0.40 * niche_risk + 0.20 * role_risk
    score = max(0.0, min(1.0, score))

    reasoning.update({
        "intent_entropy":  round(intent_ent, 4),
        "niche_entropy":   round(niche_ent, 4),
        "role_entropy":    round(role_ent, 4),
        "intent_risk":     round(intent_risk, 4),
        "niche_risk":      round(niche_risk, 4),
    })
    return score, reasoning


# ── Signal C: Session Pattern ─────────────────────────────────────────────────

def _score_session(logs: list[dict], account_id: str) -> tuple[float, dict]:
    """
    Detect unnatural session clustering and uniformity.

    Signals:
      - Action gaps cluster too uniformly (no idle periods)
      - Session-start patterns too regular
      - No realistic idle/burst asymmetry
    """
    timestamps = sorted(log.get("ts", 0.0) for log in logs if log.get("ts"))
    reasoning: dict[str, Any] = {"n_timestamps": len(timestamps)}

    if len(timestamps) < 6:
        reasoning["skip"] = "insufficient data"
        return 0.0, reasoning

    gaps = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]

    # Signal 1: gap CV (low = too regular)
    gap_cv     = _coefficient_of_variation(gaps)
    regularity = max(0.0, 1.0 - min(gap_cv / 0.8, 1.0))  # normalized at CV=0.8

    # Signal 2: no idle gaps (real humans have gaps > 3600s between sessions)
    long_gaps  = sum(1 for g in gaps if g > 3600)
    idle_ratio = long_gaps / len(gaps)
    no_idle    = 1.0 - min(idle_ratio * 3, 1.0)   # 0 idle → 1.0 risk

    # Signal 3: session density — ratio of actions in burst windows
    # Use deterministic time buckets (1-hour windows)
    bucket_counts: dict[int, int] = {}
    for ts in timestamps:
        b = int(ts) // 3600
        bucket_counts[b] = bucket_counts.get(b, 0) + 1
    if bucket_counts:
        bucket_vals = list(bucket_counts.values())
        density_cv  = _coefficient_of_variation([float(v) for v in bucket_vals])
        density_risk = max(0.0, 1.0 - min(density_cv, 1.0))
    else:
        density_risk = 0.0

    score = 0.40 * regularity + 0.35 * no_idle + 0.25 * density_risk
    score = max(0.0, min(1.0, score))

    reasoning.update({
        "gap_cv":       round(gap_cv, 4),
        "regularity":   round(regularity, 4),
        "idle_ratio":   round(idle_ratio, 4),
        "no_idle_risk": round(no_idle, 4),
        "density_risk": round(density_risk, 4),
        "long_gaps":    long_gaps,
    })
    return score, reasoning


# ── Signal D: Cross-Account Similarity (pseudo-cluster) ──────────────────────

def _score_similarity(
    account_id: str,
    logs: list[dict],
    now: int,
) -> tuple[float, dict]:
    """
    Detect cluster-like behavior WITHOUT accessing real peer state.

    Method:
      1. Hash account_id into a pseudo-cluster (deterministic).
      2. Compute a "behavioral signature" for this account's recent actions.
      3. Compare the signature's variance against what a real cluster signal
         would look like — accounts in the same cluster that behave identically
         produce a low-variance signature fingerprint.

    The insight: if this account's timing + niche + role pattern EXACTLY matches
    what a hash-seeded peer would produce for the same time bucket, it looks
    like scripted coordination.

    No real cross-account state is read.
    """
    reasoning: dict[str, Any] = {}

    if len(logs) < 4:
        reasoning["skip"] = "insufficient data"
        return 0.0, reasoning

    # Assign account to a pseudo-cluster via stable hash
    cluster_bucket = now // _CLUSTER_WINDOW_S
    cluster_id     = stable_hash_int(account_id, "detector:cluster", str(cluster_bucket)) % 20

    # Compute behavioral signature: (role, niche, delay_bucket, acct_jitter) fingerprint
    # acct_jitter makes this account-specific even with identical action logs
    sig_tokens: list[str] = []
    for i, log in enumerate(logs[-10:]):
        role    = log.get("role", "?")
        niche   = log.get("niche", "?")
        delay   = log.get("delay_s", 0)
        d_bucket = delay // 60   # 1-min delay bucket
        # Per-account deterministic micro-jitter: ensures unique token namespace
        acct_jitter = stable_hash_int(account_id, "detector:sig_jitter", str(i)) % 4
        sig_tokens.append(f"{role}:{niche}:{d_bucket}:{acct_jitter}")

    # Compute how "expected" this signature is for this cluster
    # i.e., how closely it matches the cluster's deterministic seed pattern
    cluster_seed_tokens: list[str] = []
    for i in range(10):
        r_seed = stable_hash_int("cluster", str(cluster_id), "role", str(i)) % 5
        n_seed = stable_hash_int("cluster", str(cluster_id), "niche", str(i)) % 6
        d_seed = stable_hash_int("cluster", str(cluster_id), "delay", str(i)) % 10
        role_names  = ["WARMER", "EXPLORER", "AMPLIFIER", "HARVESTER", "IDLE"]
        niche_names = ["tech", "fitness", "finance", "entertainment", "food", "travel"]
        cluster_seed_tokens.append(
            f"{role_names[r_seed]}:{niche_names[n_seed]}:{d_seed}"
        )

    # Match fraction: how many of account's tokens appear in cluster seed
    sig_set  = set(sig_tokens)
    seed_set = set(cluster_seed_tokens)
    overlap  = len(sig_set & seed_set)

    # Also penalise very low intra-account variance (looks like all accounts
    # in cluster produce the same output — scripted)
    token_ent  = _entropy(sig_tokens)
    variety_score = 1.0 - token_ent   # low variety in own actions → looks scripted

    # Cluster match score: high overlap = high detection risk
    match_score   = min(1.0, overlap / max(1, len(seed_set)) * 3)

    score = 0.50 * match_score + 0.50 * variety_score
    score = max(0.0, min(1.0, score))

    reasoning.update({
        "cluster_id":    cluster_id,
        "sig_tokens":    sig_tokens[-5:],   # last 5 only (log brevity)
        "overlap":       overlap,
        "match_score":   round(match_score, 4),
        "token_entropy": round(token_ent, 4),
        "variety_score": round(variety_score, 4),
    })
    return score, reasoning


# ── Signal E: Lifecycle Consistency ──────────────────────────────────────────

def _score_lifecycle(
    account_id: str,
    logs: list[dict],
    now: int,
    created_ts: int = 0,
) -> tuple[float, dict]:
    """
    Detect mismatches between account lifecycle stage and observed behavior.

    Signals:
      - NEW/WARMUP acting like HARVESTER → hard mismatch
      - DECLINE account with very high intensity → suspicious recovery
      - Niche instability: too many different niches for the age
    """
    reasoning: dict[str, Any] = {}

    # Try to get lifecycle stage (lazy import, exception-safe)
    stage_str = "GROWTH"   # safe default
    try:
        from core.lifecycle_engine import get_lifecycle_stage, LifecycleStage
        stage = get_lifecycle_stage(account_id, created_ts, now)
        stage_str = stage.value
    except Exception:
        pass

    reasoning["lifecycle_stage"] = stage_str

    if len(logs) < 3:
        reasoning["skip"] = "insufficient data"
        return 0.0, reasoning

    roles   = [log.get("role", "") for log in logs]
    niches  = [log.get("niche", "") for log in logs]
    modifiers = [log.get("modifiers", {}) for log in logs]
    intensities = [
        float(m.get("strategy_intensity", 0.5))
        for m in modifiers if isinstance(m, dict)
    ]

    # Signal 1: role mismatch vs stage
    HIGH_RISK_ROLES = {"HARVESTER", "AMPLIFIER"}
    SAFE_STAGES     = {"NEW", "WARMUP", "DECLINE"}

    role_mismatch = 0.0
    if stage_str in SAFE_STAGES:
        risky_count  = sum(1 for r in roles if r in HIGH_RISK_ROLES)
        role_mismatch = min(1.0, risky_count / max(1, len(roles)) * 3)

    # Signal 2: niche instability (too many niches for a new/young account)
    unique_niches = len(set(n for n in niches if n))
    age_days      = max(1, (now - created_ts) // 86400) if created_ts else 30
    expected_niches = min(5, max(1, age_days // 7))   # 1 per week, max 5
    niche_instability = max(0.0, min(1.0, (unique_niches - expected_niches) / 4))

    # Signal 3: intensity vs stage expectation
    avg_intensity = sum(intensities) / len(intensities) if intensities else 0.5
    intensity_mismatch = 0.0
    if stage_str in ("DECLINE", "NEW") and avg_intensity > 0.70:
        intensity_mismatch = min(1.0, (avg_intensity - 0.70) * 5)
    elif stage_str == "MATURE" and avg_intensity < 0.30:
        intensity_mismatch = min(1.0, (0.30 - avg_intensity) * 5)

    score = 0.50 * role_mismatch + 0.30 * niche_instability + 0.20 * intensity_mismatch
    score = max(0.0, min(1.0, score))

    reasoning.update({
        "role_mismatch":      round(role_mismatch, 4),
        "niche_instability":  round(niche_instability, 4),
        "intensity_mismatch": round(intensity_mismatch, 4),
        "unique_niches":      unique_niches,
        "avg_intensity":      round(avg_intensity, 4),
        "age_days":           age_days,
    })
    return score, reasoning


# ── DetectorSimulator ─────────────────────────────────────────────────────────

class DetectorSimulator:
    """
    Platform-side detection AI simulation.

    Evaluates 5 independent signal groups, aggregates them with fixed weights,
    applies EMA temporal smoothing, and generates flags with explanations.

    Thread-safe: all per-account state is stored in module-level dicts.
    No cross-account state is read or written.
    """

    def evaluate(
        self,
        account_id:  str,
        now:         int | None = None,
        created_ts:  int = 0,
        logs:        list[dict] | None = None,
    ) -> DetectionResult:
        """
        Evaluate detection risk for account_id.

        Args:
            account_id: target account
            now:        current unix timestamp (default: time.time())
            created_ts: account creation timestamp (for lifecycle check)
            logs:       pre-fetched observer logs (fetched from observer if None)
        """
        if now is None:
            now = int(time.time())

        # Fetch logs from observer if not provided
        if logs is None:
            logs = _fetch_logs(account_id)

        # Use the last N events only
        window = logs[-_LOG_WINDOW:] if len(logs) > _LOG_WINDOW else logs

        # ── Run 5 signal groups ───────────────────────────────────────────────
        timing_score,    timing_r    = _score_timing(window)
        entropy_score,   entropy_r   = _score_entropy(window)
        session_score,   session_r   = _score_session(window, account_id)
        similarity_score, sim_r      = _score_similarity(account_id, window, now)
        lifecycle_score, lifecycle_r = _score_lifecycle(account_id, window, now, created_ts)

        sub_scores = {
            "timing":     timing_score,
            "entropy":    entropy_score,
            "session":    session_score,
            "similarity": similarity_score,
            "lifecycle":  lifecycle_score,
        }

        # ── Weighted aggregation ──────────────────────────────────────────────
        raw_score = sum(sub_scores[k] * _WEIGHTS[k] for k in sub_scores)
        raw_score = max(0.0, min(1.0, raw_score))

        # ── EMA smoothing ─────────────────────────────────────────────────────
        prev_score   = _RISK_MEMORY.get(account_id, raw_score)
        smooth_score = prev_score * _RISK_EMA_PREV + raw_score * _RISK_EMA_CURRENT
        smooth_score = max(0.0, min(1.0, smooth_score))

        # Hard clamp: no jump > MAX_RISK_JUMP per cycle
        delta = smooth_score - prev_score
        if abs(delta) > _MAX_RISK_JUMP:
            smooth_score = prev_score + math.copysign(_MAX_RISK_JUMP, delta)
        smooth_score = round(max(0.0, min(1.0, smooth_score)), 5)

        _RISK_MEMORY[account_id] = smooth_score

        # ── Flag generation ───────────────────────────────────────────────────
        flags: dict[str, float] = {}
        for key, (threshold, flag_name) in _FLAG_THRESHOLDS.items():
            if sub_scores[key] > threshold:
                flags[flag_name] = round(sub_scores[key], 4)

        # ── Build result ──────────────────────────────────────────────────────
        result = DetectionResult(
            account_id  = account_id,
            risk_score  = smooth_score,
            flags       = flags,
            sub_scores  = sub_scores,
            reasoning   = {
                "raw_score":    round(raw_score, 5),
                "prev_score":   round(prev_score, 5),
                "weights":      _WEIGHTS,
                "timing":       timing_r,
                "entropy":      entropy_r,
                "session":      session_r,
                "similarity":   sim_r,
                "lifecycle":    lifecycle_r,
            },
            ts = float(now),
        )

        LOGGER.info(
            "detector_eval account=%s risk=%.3f flags=%s timing=%.2f "
            "entropy=%.2f session=%.2f sim=%.2f lc=%.2f",
            account_id, smooth_score, list(flags.keys()),
            timing_score, entropy_score, session_score, similarity_score, lifecycle_score,
        )
        return result


# ── Observer log fetcher ──────────────────────────────────────────────────────

def _fetch_logs(account_id: str) -> list[dict]:
    """Fetch observer logs for account, exception-safe."""
    try:
        from core.observer import get_observer
        obs = get_observer()
        return obs.replay(account_id)
    except Exception as exc:
        LOGGER.debug("detector_fetch_logs_error account=%s error=%s", account_id, exc)
        return []


# ── Public API ────────────────────────────────────────────────────────────────

def get_risk_score(account_id: str) -> float:
    """Return smoothed risk score for account (0.0 = safe, 1.0 = bot-like)."""
    return _RISK_MEMORY.get(account_id, 0.0)


def evaluate_account(
    account_id:  str,
    now:         int | None = None,
    created_ts:  int = 0,
    logs:        list[dict] | None = None,
) -> DetectionResult:
    """Module-level convenience: evaluate using the singleton detector."""
    return get_detector().evaluate(account_id, now=now, created_ts=created_ts, logs=logs)


# ── Metrics integration ───────────────────────────────────────────────────────

def record_to_metrics(result: DetectionResult) -> None:
    """Push detection result into metrics_store as a ban_rate proxy."""
    try:
        from core.metrics_store import get_metrics_store
        store = get_metrics_store()
        # Treat risk_score > 0.80 as a "ban proxy" event
        ban_proxy = 1.0 if result.risk_score > 0.80 else 0.0
        store.update("ban_rate",     ban_proxy,            tag=result.account_id)
        store.update("anomaly_score", result.risk_score,   tag=result.account_id)
    except Exception as exc:
        LOGGER.debug("detector_metrics_error account=%s error=%s", result.account_id, exc)


# ── Singleton ─────────────────────────────────────────────────────────────────

_DETECTOR: DetectorSimulator | None = None


def get_detector() -> DetectorSimulator:
    global _DETECTOR
    if _DETECTOR is None:
        _DETECTOR = DetectorSimulator()
    return _DETECTOR


def reset_detector() -> None:
    """For testing only."""
    global _DETECTOR
    _DETECTOR = None
    _RISK_MEMORY.clear()
