"""
Account Clustering v2 — Self-Organizing Swarm.

Upgrades over v1:
  - Dynamic K ∈ [3, 12] — grows/shrinks based on cohesion/distance
  - Cluster split  when size > 8 and cohesion > 0.40
  - Cluster merge  when inter-cluster distance < 0.10
  - Cluster age + stability tracking (no split/merge if age < 3)
  - Collapse prevention: force-split largest cluster if > 60% of fleet
  - Adaptive recluster: fires on embedding drift > 0.15 OR every N_MAX cycles
  - Cluster quality = reward / (1 + risk) — biases merge preference

Design contracts:
  - Deterministic: stable_hash_int for all seeding; no random.*
  - Bounded: all embedding dims clamped [0, 1]
  - No cross-account state mutation
  - reset_clustering() available for testing
"""
from __future__ import annotations

import logging
import math
from typing import Any

from core.mutation_controller import stable_hash_int

LOGGER = logging.getLogger("core.account_clustering")

# ── Constants ─────────────────────────────────────────────────────────────────

K_MIN: int        = 3
K_MAX: int        = 12
EMB_DIM: int      = 5
_EMB_ALPHA: float = 0.20
_KMEANS_ITERS: int = 4

# Adaptive recluster
_DRIFT_THRESHOLD: float = 0.15   # trigger recluster on embedding drift
_N_MAX_CYCLES:    int   = 10     # force recluster after at most N cycles

# Split / merge thresholds
_SPLIT_SIZE:      int   = 8      # cluster must have > this many members
_SPLIT_COHESION:  float = 0.40   # intra-cluster avg distance to trigger split
_MERGE_DIST:      float = 0.10   # inter-centroid distance to trigger merge
_COHESION_HIGH:   float = 0.35   # K grows when avg cohesion exceeds this
_DIST_LOW:        float = 0.12   # K shrinks when avg inter-dist drops below

# Age / stability
_AGE_MIN_OPS:     int   = 3      # no split/merge until cluster is this old
_STABILITY_LOW:   float = 0.50   # learning rate dampened below this

# Collapse prevention
_COLLAPSE_FRAC:   float = 0.60   # force split if largest cluster > 60% of fleet

# Lifecycle → float
_LIFECYCLE_FLOAT: dict[str, float] = {
    "NEW": 0.0, "WARMUP": 0.2, "GROWTH": 0.4,
    "MATURE": 0.6, "DECLINE": 0.8, "RECOVERY": 1.0,
    "unknown": 0.3,
}

# ── Mutable global state ───────────────────────────────────────────────────────

_K: int = 6   # current live cluster count

# account_id → 5D embedding
_EMBEDDINGS:  dict[str, list[float]] = {}
# account_id → "cN"
_CLUSTER_IDS: dict[str, str]         = {}
# list of K centroids (each is a list[float] of EMB_DIM)
_CENTROIDS:   list[list[float]]      = []

# Cluster metadata keyed by centroid index (int)
_CLUSTER_AGE:       dict[int, int]   = {}   # cycles since born
_CLUSTER_STABILITY: dict[int, float] = {}   # EMA of assignment stability (0-1)
_CLUSTER_QUALITY:   dict[int, float] = {}   # EMA of reward/(1+risk)

# Per-account previous embedding for drift tracking
_PREV_EMBEDDINGS: dict[str, list[float]] = {}
# EMA of fleet-level embedding drift
_DRIFT_EMA: float = 0.0

_CYCLE:          int = 0
_LAST_RECLUSTER: int = -999


def reset_clustering() -> None:
    """Full state reset — for testing only."""
    global _K, _CYCLE, _LAST_RECLUSTER, _DRIFT_EMA
    _K = 6
    _CYCLE = 0
    _LAST_RECLUSTER = -999
    _DRIFT_EMA = 0.0
    _EMBEDDINGS.clear()
    _CLUSTER_IDS.clear()
    _CENTROIDS.clear()
    _PREV_EMBEDDINGS.clear()
    _CLUSTER_AGE.clear()
    _CLUSTER_STABILITY.clear()
    _CLUSTER_QUALITY.clear()


# ── Embedding helpers ─────────────────────────────────────────────────────────

def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def _default_embedding() -> list[float]:
    return [0.5] * EMB_DIM


def _lifecycle_to_float(stage: str) -> float:
    return _LIFECYCLE_FLOAT.get(stage or "unknown", 0.3)


def update_embedding(
    account_id:      str,
    detection_risk:  float,
    reward:          float,
    success:         bool,
    lifecycle_stage: str,
    is_suppressed:   bool,
) -> list[float]:
    """EMA-update 5D embedding. Returns updated embedding. Exception-safe."""
    global _DRIFT_EMA
    try:
        emb  = _EMBEDDINGS.setdefault(account_id, _default_embedding())
        prev = list(emb)   # snapshot before update

        obs = [
            _clamp(float(detection_risk) if detection_risk == detection_risk else 0.5),
            _clamp(((float(reward) if reward == reward else 0.0) + 1.5) / 2.5),
            1.0 if success else 0.0,
            _lifecycle_to_float(lifecycle_stage),
            0.0 if is_suppressed else 1.0,
        ]
        for i in range(EMB_DIM):
            emb[i] = round(_clamp(emb[i] * (1 - _EMB_ALPHA) + obs[i] * _EMB_ALPHA), 5)

        # Track drift for adaptive recluster
        drift = math.sqrt(sum((emb[i] - prev[i]) ** 2 for i in range(EMB_DIM)))
        _DRIFT_EMA = _DRIFT_EMA * 0.80 + drift * 0.20
        _PREV_EMBEDDINGS[account_id] = prev

        return emb
    except Exception as exc:
        LOGGER.debug("embedding_update_error account=%s error=%s", account_id, exc)
        return _EMBEDDINGS.get(account_id, _default_embedding())


# ── Distance helpers ──────────────────────────────────────────────────────────

def _l2_sq(a: list[float], b: list[float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b))


def _l2(a: list[float], b: list[float]) -> float:
    return math.sqrt(_l2_sq(a, b))


def _nearest_centroid_idx(emb: list[float], centroids: list[list[float]]) -> int:
    best_i, best_d = 0, float("inf")
    for i, c in enumerate(centroids):
        d = _l2_sq(emb, c)
        if d < best_d:
            best_d, best_i = d, i
    return best_i


def _centroid_mean(members: list[list[float]]) -> list[float]:
    n = len(members)
    return [sum(m[d] for m in members) / n for d in range(EMB_DIM)]


# ── Centroid initialization ───────────────────────────────────────────────────

def _init_centroids(k: int) -> list[list[float]]:
    """Deterministic centroid init for exactly k clusters."""
    centroids = []
    for ki in range(k):
        c = [stable_hash_int("centroid_init", str(ki), str(d)) % 1000 / 1000.0
             for d in range(EMB_DIM)]
        centroids.append(c)
    return centroids


# ── Cluster quality & metrics ─────────────────────────────────────────────────

def record_cluster_quality(
    cluster_idx:    int,
    detection_risk: float,
    reward:         float,
) -> None:
    """EMA-update quality = reward / (1 + risk) for a cluster."""
    q = (reward + 1.5) / (1.0 + max(0.0, detection_risk))   # shift reward ≥ 0
    prev = _CLUSTER_QUALITY.get(cluster_idx, 0.5)
    _CLUSTER_QUALITY[cluster_idx] = round(prev * 0.85 + q * 0.15, 5)


# ── Intra/inter cluster metrics ───────────────────────────────────────────────

def _intra_cohesion(members: list[list[float]], centroid: list[float]) -> float:
    """Average L2 distance from centroid (intra-cluster spread)."""
    if not members:
        return 0.0
    return sum(_l2(m, centroid) for m in members) / len(members)


def _inter_distance(c1: list[float], c2: list[float]) -> float:
    return _l2(c1, c2)


# ── Split operation ───────────────────────────────────────────────────────────

def _split_cluster(
    members:  list[list[float]],
    cycle_id: int,
) -> tuple[list[float], list[float]]:
    """
    Pick 2 farthest points as seeds for a split.
    Deterministic: sorts members lexicographically first (stable order),
    then scans all pairs — O(n²) but members list is small (≤ ~20).
    """
    if len(members) < 2:
        return members[0], members[0]
    # Sort for determinism
    sorted_m = sorted(members)
    best_d, seed_a, seed_b = -1.0, sorted_m[0], sorted_m[-1]
    for i in range(len(sorted_m)):
        for j in range(i + 1, len(sorted_m)):
            d = _l2_sq(sorted_m[i], sorted_m[j])
            if d > best_d:
                best_d = d
                seed_a, seed_b = sorted_m[i], sorted_m[j]
    return seed_a, seed_b


# ── Core clustering engine ────────────────────────────────────────────────────

def run_clustering() -> dict[str, str]:
    """
    Full swarm clustering pass:
      1. Mini k-means (_K iterations)
      2. Dynamic K adjustment via cohesion/distance
      3. Split large high-cohesion clusters
      4. Merge close clusters
      5. Collapse prevention
      6. Update cluster age/stability metadata
    Returns {account_id → cluster_id}.
    Exception-safe.
    """
    global _K, _CENTROIDS, _LAST_RECLUSTER

    if not _EMBEDDINGS:
        return dict(_CLUSTER_IDS)

    try:
        account_ids = list(_EMBEDDINGS.keys())
        embs        = [_EMBEDDINGS[a] for a in account_ids]
        n           = len(embs)

        # ── Step 1: Init centroids ─────────────────────────────────────────────
        if not _CENTROIDS or len(_CENTROIDS) != _K:
            _CENTROIDS = _init_centroids(_K)

        centroids = [list(c) for c in _CENTROIDS]
        assignments: list[int] = [0] * n

        # ── Step 2: Mini k-means ───────────────────────────────────────────────
        for _it in range(_KMEANS_ITERS):
            for i, emb in enumerate(embs):
                assignments[i] = _nearest_centroid_idx(emb, centroids)
            new_centroids = []
            for k in range(len(centroids)):
                members = [embs[i] for i in range(n) if assignments[i] == k]
                new_centroids.append(_centroid_mean(members) if members else list(centroids[k]))
            centroids = new_centroids

        # ── Step 3: Cohesion / distance metrics ───────────────────────────────
        cluster_members: dict[int, list[list[float]]] = {k: [] for k in range(len(centroids))}
        for i, a in enumerate(assignments):
            cluster_members[a].append(embs[i])

        cohesions  = [_intra_cohesion(cluster_members[k], centroids[k])
                      for k in range(len(centroids))]
        avg_cohesion = sum(cohesions) / max(1, len(cohesions))

        inter_dists = []
        nc = len(centroids)
        for i in range(nc):
            for j in range(i + 1, nc):
                inter_dists.append(_inter_distance(centroids[i], centroids[j]))
        avg_inter = sum(inter_dists) / max(1, len(inter_dists))

        # ── Step 4: Dynamic K adjustment ──────────────────────────────────────
        new_k = _K
        if avg_cohesion > _COHESION_HIGH and _K < K_MAX:
            new_k = min(K_MAX, _K + 1)
        elif avg_inter < _DIST_LOW and _K > K_MIN:
            new_k = max(K_MIN, _K - 1)

        # ── Step 5: Split ──────────────────────────────────────────────────────
        split_happened = False
        new_centroids_list = list(centroids)
        for k in range(len(centroids)):
            age = _CLUSTER_AGE.get(k, 0)
            if age < _AGE_MIN_OPS:
                continue
            size = len(cluster_members[k])
            coh  = cohesions[k]
            if size > _SPLIT_SIZE and coh > _SPLIT_COHESION and len(new_centroids_list) < K_MAX:
                seed_a, seed_b = _split_cluster(cluster_members[k], _CYCLE)
                new_centroids_list[k] = list(seed_a)
                new_centroids_list.append(list(seed_b))
                split_happened = True
                LOGGER.info("cluster_split k=%d size=%d cohesion=%.3f", k, size, coh)

        if split_happened:
            centroids = new_centroids_list

        # ── Step 6: Merge ──────────────────────────────────────────────────────
        merged = True
        while merged and len(centroids) > K_MIN:
            merged = False
            nc = len(centroids)
            for i in range(nc):
                if merged:
                    break
                for j in range(i + 1, nc):
                    age_i = _CLUSTER_AGE.get(i, 0)
                    age_j = _CLUSTER_AGE.get(j, 0)
                    if age_i < _AGE_MIN_OPS or age_j < _AGE_MIN_OPS:
                        continue
                    if _inter_distance(centroids[i], centroids[j]) < _MERGE_DIST:
                        # Weighted merge by quality (higher quality cluster dominates)
                        q_i = _CLUSTER_QUALITY.get(i, 0.5)
                        q_j = _CLUSTER_QUALITY.get(j, 0.5)
                        total_q = q_i + q_j if (q_i + q_j) > 0 else 1.0
                        w_i, w_j = q_i / total_q, q_j / total_q
                        merged_centroid = [
                            w_i * centroids[i][d] + w_j * centroids[j][d]
                            for d in range(EMB_DIM)
                        ]
                        centroids[i] = merged_centroid
                        centroids.pop(j)
                        merged = True
                        LOGGER.info("cluster_merge i=%d j=%d dist=%.3f", i, j,
                                    _inter_distance(centroids[i], merged_centroid))
                        break

        # ── Step 7: Collapse prevention ────────────────────────────────────────
        if n > 0:
            final_assign = [_nearest_centroid_idx(embs[i], centroids) for i in range(n)]
            sizes = {}
            for a in final_assign:
                sizes[a] = sizes.get(a, 0) + 1
            largest_k, largest_sz = max(sizes.items(), key=lambda x: x[1])
            if largest_sz > _COLLAPSE_FRAC * n and len(centroids) < K_MAX:
                mems = [embs[i] for i in range(n) if final_assign[i] == largest_k]
                if len(mems) >= 2:
                    seed_a, seed_b = _split_cluster(mems, _CYCLE)
                    centroids[largest_k] = list(seed_a)
                    centroids.append(list(seed_b))
                    LOGGER.info("collapse_prevention k=%d sz=%d n=%d", largest_k, largest_sz, n)

        # ── Step 8: Final assignment + update metadata ─────────────────────────
        _K = max(K_MIN, min(K_MAX, len(centroids)))
        centroids = centroids[:_K]   # hard clamp

        final_assign = [_nearest_centroid_idx(embs[i], centroids) for i in range(n)]
        prev_ids     = {a: _CLUSTER_IDS.get(a) for a in account_ids}

        for i, a in enumerate(account_ids):
            _CLUSTER_IDS[a] = f"c{final_assign[i]}"

        # Update age (increment all live clusters)
        live_indices = set(final_assign)
        for k in range(len(centroids)):
            if k in live_indices:
                _CLUSTER_AGE[k]  = _CLUSTER_AGE.get(k, 0) + 1
            else:
                _CLUSTER_AGE[k]  = 0
            # Stability: fraction of accounts that kept same cluster
            same = sum(
                1 for i, a in enumerate(account_ids)
                if prev_ids.get(a) == f"c{final_assign[i]}"
            )
            prev_stab = _CLUSTER_STABILITY.get(k, 1.0)
            ratio = same / n if n else 1.0
            _CLUSTER_STABILITY[k] = round(prev_stab * 0.75 + ratio * 0.25, 4)

        _CENTROIDS[:] = centroids
        _LAST_RECLUSTER = _CYCLE

        LOGGER.debug(
            "swarm_cluster K=%d drift=%.3f cohesion=%.3f inter=%.3f",
            _K, _DRIFT_EMA, avg_cohesion, avg_inter,
        )
        return dict(_CLUSTER_IDS)

    except Exception as exc:
        LOGGER.debug("clustering_error error=%s", exc)
        return dict(_CLUSTER_IDS)


# ── Adaptive recluster trigger ────────────────────────────────────────────────

def _should_recluster(cycle: int) -> bool:
    """
    Trigger recluster if:
      - embedding drift EMA > threshold, OR
      - N_MAX cycles have passed since last recluster
    """
    cycles_since = cycle - _LAST_RECLUSTER
    if _DRIFT_EMA > _DRIFT_THRESHOLD:
        return True
    if cycles_since >= _N_MAX_CYCLES:
        return True
    return False


# ── Public API ────────────────────────────────────────────────────────────────

def notify_cycle(cycle: int) -> bool:
    """
    Called once per pipeline cycle. Triggers adaptive recluster.
    Returns True if recluster fired.
    """
    global _CYCLE
    _CYCLE = cycle
    if _should_recluster(cycle):
        run_clustering()
        return True
    return False


def get_cluster_id(account_id: str) -> str:
    """
    Return current cluster_id for account.
    Fallback: nearest centroid lookup, or 'c0' if no centroids yet.
    """
    if account_id in _CLUSTER_IDS:
        return _CLUSTER_IDS[account_id]
    emb = _EMBEDDINGS.get(account_id, _default_embedding())
    if _CENTROIDS:
        idx = _nearest_centroid_idx(emb, _CENTROIDS)
        cid = f"c{idx}"
    else:
        cid = "c0"
    _CLUSTER_IDS[account_id] = cid
    return cid


def get_cluster_learning_rate(cluster_id: str, base_lr: float = 1.0) -> float:
    """
    Returns learning rate multiplier for a cluster.
    Dampened when stability < _STABILITY_LOW.
    """
    try:
        idx = int(cluster_id[1:])
        stab = _CLUSTER_STABILITY.get(idx, 1.0)
        if stab < _STABILITY_LOW:
            return round(base_lr * (0.5 + stab), 4)
        return base_lr
    except Exception:
        return base_lr


def snapshot() -> dict[str, Any]:
    """Observability snapshot."""
    cluster_counts: dict[str, int] = {}
    for cid in _CLUSTER_IDS.values():
        cluster_counts[cid] = cluster_counts.get(cid, 0) + 1

    return {
        "K":               _K,
        "n_accounts":      len(_EMBEDDINGS),
        "n_assigned":      len(_CLUSTER_IDS),
        "cycle":           _CYCLE,
        "last_recluster":  _LAST_RECLUSTER,
        "drift_ema":       round(_DRIFT_EMA, 4),
        "cluster_counts":  cluster_counts,
        "cluster_age":     dict(_CLUSTER_AGE),
        "cluster_stability": dict(_CLUSTER_STABILITY),
        "cluster_quality": dict(_CLUSTER_QUALITY),
        "centroids":       [[round(v, 3) for v in c] for c in _CENTROIDS],
    }
