"""
Swarm Dynamics -- Darwinian Intra-Swarm Competition, Resource Allocation,
and Genetic Inheritance with Genotype/Phenotype separation.

Genotype (DNA) = stable compressed traits that evolve slowly.
Phenotype      = runtime optimizer params decoded from genotype each cycle.

Design contracts:
  - Deterministic: no random.* usage; seeding through stable_hash_int.
  - Exception-safe: every public function must never raise.
  - No circular imports: swarm_dynamics does NOT import from optimizer.
  - reset_swarm() available for test resets.
"""
from __future__ import annotations

import logging
from typing import Any

from core.mutation_controller import stable_hash_int

LOGGER = logging.getLogger("core.swarm_dynamics")

# ---------------------------------------------------------------------------
# Tunable constants
# ---------------------------------------------------------------------------

FITNESS_ALPHA: float = 0.15
FITNESS_PREV:  float = 0.85
FITNESS_REWARD_SHIFT: float = 1.5

RESOURCE_PERF_WEIGHT:  float = 0.50
RESOURCE_FAIR_WEIGHT:  float = 0.50
RESOURCE_DOMINANCE_CAP: float = 0.40   # PART 5: max 40% per cluster

FITNESS_WEAK_THRESHOLD:  float = 0.40
FITNESS_MUTATE_THRESHOLD: float = 0.50
FITNESS_SUPPRESS_MULT:   float = 0.50

MUTATION_EXPLORATION_MULT: float = 1.50
MUTATION_STRENGTH_MULT:    float = 1.30

WEAK_STREAK_LIMIT: int = 10   # kept for compat
SOFT_EXTINCTION_STREAK: int = 12   # PART 7: enter soft-extinction after 12 weak cycles
HARD_EXTINCTION_STREAK: int = 20   # PART 9: fully delete after 20 weak cycles
SOFT_EXTINCTION_RESOURCE_MULT: float = 0.10  # PART 8: crush resource to 10%
SOFT_EXTINCTION_MUTATION_MULT: float = 2.00  # PART 8: 2x mutation (last chance)

NICHE_LOCK_STABILITY_THRESH: float = 0.70  # PART 3: stability EMA to lock niche
NICHE_LOCK_AGE_MIN:          int   = 5    # PART 3: minimum cycles before locking
NICHE_LOCK_MUTATION_SCALE:   float = 0.50  # PART 3: locked cluster mutates half as fast
NICHE_LOCK_EXPLORE_BIAS:     float = 0.70  # PART 3: locked cluster explores less
NICHE_DOMINANCE_BONUS:       float = 1.10  # PART 6: top-in-niche resource bonus

REBIRTH_CORE_MIX: float = 0.50  # PART 10: 50/50 genome mix on rebirth

REPRODUCTION_FITNESS_MIN: float = 1.20
REPRODUCTION_SIZE_MIN:    int   = 8
REPRODUCTION_MAX_DAUGHTERS_PER_CYCLE: int = 1

# ---------------------------------------------------------------------------
# PART 1 -- True Genome: 4 compressed semantic traits (genotype, not phenotype)
# ---------------------------------------------------------------------------

# Genome keys -- compressed traits, NOT raw optimizer param names.
GENOME_KEYS: tuple[str, ...] = (
    "aggressiveness_base",  # encoded from behavior_aggressiveness_mult
    "exploration_base",     # encoded from strategy_explorer_weight_mult
    "risk_tolerance",       # encoded as 1 / (1 + platform_delay_base_mult - 1)
    "diversity_bias",       # encoded from strategy_harvester_weight_mult
)

GENOME_NEUTRAL: float = 1.0
GENOME_CLAMP_MIN: float = 0.50   # wider than optimizer [0.6,1.4] -- genome is DNA
GENOME_CLAMP_MAX: float = 1.50

# Genome EMA update alpha (PART 3 -- slow evolution, anti-spike)
GENOME_EMA_ALPHA: float = 0.10   # 10% new observation, 90% history

# Per-cycle decay toward neutral (0.995 -> ~0.5%/cycle)
GENOME_DECAY: float = 0.995

# Mutation spread on reproduction (+-%)
GENOME_MUTATION_SPREAD: float = 0.05   # +-5% at DNA level (tighter than before)

# Mutation pressure thresholds
GENOME_PRESSURE_WEAK_THRESH:   float = 0.50
GENOME_PRESSURE_STRONG_THRESH: float = 1.20
GENOME_PRESSURE_WEAK_MULT:     float = 1.50
GENOME_PRESSURE_STRONG_MULT:   float = 0.80

# Delta contexts — "default" removed; core IS the shared default
GENOME_DELTA_CONTEXTS: tuple[str, ...] = ("high_risk", "low_risk")

# How much a context delta may deviate from core (PART 4)
GENOME_DELTA_CLAMP: float = 0.20   # ±20%

# Core EMA alpha (PART 3)
GENOME_CORE_ALPHA: float = 0.10
# Delta EMA alpha — lighter than core (PART 3)
GENOME_DELTA_ALPHA: float = 0.05

# Fitness feedback step sizes (PART 7)
GENOME_FEEDBACK_STRONG_AGGR_STEP:  float = 1.02
GENOME_FEEDBACK_WEAK_EXPLORE_STEP: float = 1.05

# Blend alpha: how strongly genotype biases phenotype each cycle (PART 2)
GENOME_DECODE_ALPHA: float = 0.30


def _small_zero() -> dict[str, float]:
    """Zero-initialised delta — context starts as no adjustment."""
    return {k: 0.0 for k in GENOME_KEYS}


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_CLUSTER_FITNESS:    dict[str, dict[str, float]] = {}
_CLUSTER_RESOURCE:   dict[str, float]            = {}
_CLUSTER_WEAK_STREAK: dict[str, int]             = {}
_INACTIVE_CLUSTERS:  set[str]                    = set()
_CLUSTER_DAUGHTERS:  dict[str, list[str]]        = {}
# Structure: {cid: {"core": {k: float}, "delta": {ctx: {k: float}}}}
_CLUSTER_GENOME:     dict[str, dict]             = {}
# Niche ecosystem state (Parts 2-10)
_CLUSTER_NICHE:        dict[str, tuple]          = {}   # (content_type, risk_bucket, lc_stage)
_CLUSTER_NICHE_LOCKED: dict[str, bool]           = {}
_CLUSTER_AGE:          dict[str, int]            = {}   # cycles alive
_CLUSTER_STABILITY:    dict[str, float]          = {}   # EWMA fitness-above-threshold
_SOFT_EXTINCT:         set[str]                  = set()
_HARD_EXTINCT:         set[str]                  = set()
_REBIRTH_COUNT:          int   = 0
_SYSTEM_CYCLE:           int   = 0   # PART 4: total cycles for adaptive pressure
_EXTINCTION_WAVE_DURATION: int = 0   # PART 5: remaining cycles of wave effect
_GLOBAL_MUTATION_MULT:   float = 1.0  # PART 5: temporary mutation boost during wave
# Lineage / ancestry tracking (multi-generation memory)
_CLUSTER_LINEAGE: dict[str, dict] = {}
# Rivalry: cid -> set of rival cids sharing same niche + similar resource band
_LINEAGE_RIVALS:  dict[str, set]  = {}
# Niche capital: accumulated territorial influence [0, 1]
_NICHE_CAPITAL:   dict[str, float] = {}

# ---------------------------------------------------------------------------
# Niche System  (Parts 2, 3, 6)
# ---------------------------------------------------------------------------

def compute_behavioral_niche(cluster_id: str) -> tuple:
    """
    PART 1 — Niche = HOW a cluster behaves, derived from genome core traits.

    Resolution: 0.05 buckets via _bucket() — finer than round(x,1) which
    collapsed clusters with differences of up to 0.09 into the same niche.
    Deterministic: int(x * 20) / 20.0 uses only integer division, no float drift.
    """
    def _bucket(x: float) -> float:
        """Snap to nearest 0.05 grid: int(x * 20) / 20.0."""
        return int(float(x) * 20) / 20.0

    g = _CLUSTER_GENOME.get(cluster_id)
    if not g:
        return (1.0, 1.0, 1.0)
    core = g.get("core", {})
    return (
        _bucket(core.get("aggressiveness_base", GENOME_NEUTRAL)),
        _bucket(core.get("risk_tolerance",      GENOME_NEUTRAL)),
        _bucket(core.get("exploration_base",    GENOME_NEUTRAL)),
    )


def update_cluster_niche(cluster_id: str) -> None:
    """
    PART 1/2 — Recompute behavioral niche from genome, tick age, update stability.
    Call once per Darwin cycle after the genome has been updated.
    """
    try:
        _CLUSTER_NICHE[cluster_id] = compute_behavioral_niche(cluster_id)
        _CLUSTER_AGE[cluster_id]   = _CLUSTER_AGE.get(cluster_id, 0) + 1
        # Stability: EWMA of whether fitness stays above weak threshold
        f    = _get_global_fitness(cluster_id)
        prev = _CLUSTER_STABILITY.get(cluster_id, 0.5)
        sig  = 1.0 if f >= FITNESS_WEAK_THRESHOLD else 0.0
        _CLUSTER_STABILITY[cluster_id] = round(prev * 0.90 + sig * 0.10, 6)
        check_niche_lock(cluster_id)
    except Exception as exc:
        LOGGER.debug("swarm_update_niche_error cluster=%s error=%s", cluster_id, exc)


def get_cluster_niche(cluster_id: str) -> tuple | None:
    """Return stored niche tuple (content_type, risk_bucket, lc_stage) or None."""
    return _CLUSTER_NICHE.get(cluster_id)


def check_niche_lock(cluster_id: str) -> bool:
    """
    PART 3 — Lock niche when cluster is stable and mature.
    Once locked, mutation and exploration are dampened (specialist behaviour).
    """
    try:
        stability = _CLUSTER_STABILITY.get(cluster_id, 0.0)
        age       = _CLUSTER_AGE.get(cluster_id, 0)
        should_lock = stability > NICHE_LOCK_STABILITY_THRESH and age > NICHE_LOCK_AGE_MIN
        if should_lock and not _CLUSTER_NICHE_LOCKED.get(cluster_id):
            _CLUSTER_NICHE_LOCKED[cluster_id] = True
            LOGGER.info(
                "swarm_niche_lock cluster=%s niche=%s stability=%.3f age=%d",
                cluster_id, _CLUSTER_NICHE.get(cluster_id), stability, age,
            )
        return should_lock
    except Exception:
        return False


def is_niche_locked(cluster_id: str) -> bool:
    """True if the cluster has locked into a specialist niche."""
    return bool(_CLUSTER_NICHE_LOCKED.get(cluster_id))


# ---------------------------------------------------------------------------
# Context Helper
# ---------------------------------------------------------------------------

def get_context(risk: float) -> str:
    """Map risk score to a delta context key."""
    if risk > 0.6:
        return "high_risk"
    elif risk < 0.3:
        return "low_risk"
    return "high_risk"   # mid-range falls to high_risk; core still dominates


def get_context_strength(risk: float) -> float:
    """
    PART 2 — How strongly the current risk reading belongs to its context.

    high_risk zone (>0.6): strength rises linearly from 0.0 at 0.6 to 1.0 at 1.0
    low_risk  zone (<0.3): strength rises linearly from 0.0 at 0.3 to 1.0 at 0.0
    middle    zone (0.3-0.6): returns 0.0 → no delta learning at all
    """
    if risk > 0.6:
        x = (risk - 0.6) / 0.4
        return x * x   # quadratic boost
    elif risk < 0.3:
        x = (0.3 - risk) / 0.3
        return x * x   # quadratic boost
    return 0.0   # dead middle zone — core already handles this


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

def reset_swarm() -> None:
    """Full state reset -- for testing only."""
    global _REBIRTH_COUNT, _SYSTEM_CYCLE, _EXTINCTION_WAVE_DURATION, _GLOBAL_MUTATION_MULT
    _CLUSTER_FITNESS.clear()
    _CLUSTER_RESOURCE.clear()
    _CLUSTER_WEAK_STREAK.clear()
    _INACTIVE_CLUSTERS.clear()
    _CLUSTER_DAUGHTERS.clear()
    _CLUSTER_GENOME.clear()
    _CLUSTER_NICHE.clear()
    _CLUSTER_NICHE_LOCKED.clear()
    _CLUSTER_AGE.clear()
    _CLUSTER_STABILITY.clear()
    _SOFT_EXTINCT.clear()
    _HARD_EXTINCT.clear()
    _REBIRTH_COUNT = 0
    _SYSTEM_CYCLE  = 0
    _EXTINCTION_WAVE_DURATION = 0
    _GLOBAL_MUTATION_MULT     = 1.0
    _CLUSTER_LINEAGE.clear()
    _LINEAGE_RIVALS.clear()
    _NICHE_CAPITAL.clear()


# ---------------------------------------------------------------------------
# Lineage System (Parts 1, 2, 3, 4)
# ---------------------------------------------------------------------------

def _init_lineage(cid: str, parent: str | None) -> dict:
    """PART 1 -- Create a fresh lineage record. Inherits generation + lineage_id from parent."""
    if parent is None or parent not in _CLUSTER_LINEAGE:
        return {
            "parent":          None,
            "generation":      0,
            "lineage_id":      cid,   # root of its own lineage
            "fitness_ema":     1.0,
            "survival_cycles": 0,
        }
    p = _CLUSTER_LINEAGE[parent]
    return {
        "parent":          parent,
        "generation":      p["generation"] + 1,
        "lineage_id":      p["lineage_id"],   # inherit root ancestor
        "fitness_ema":     p["fitness_ema"],   # inherit parent's learned fitness
        "survival_cycles": 0,
    }


def update_lineage_fitness(cid: str, fitness: float) -> None:
    """PART 3 -- EMA-update lineage fitness memory and increment survival counter."""
    try:
        ln = _CLUSTER_LINEAGE.get(cid)
        if ln is None:
            _CLUSTER_LINEAGE[cid] = _init_lineage(cid, None)
            ln = _CLUSTER_LINEAGE[cid]
        ln["fitness_ema"]     = round(ln["fitness_ema"] * 0.9 + fitness * 0.1, 6)
        ln["survival_cycles"] += 1
    except Exception as exc:
        LOGGER.debug("swarm_lineage_update_error cid=%s error=%s", cid, exc)


def get_ancestry_pressure(cid: str) -> dict[str, float]:
    """
    PART 4 -- Derive three pressure scalars from lineage history,
    with Lineage Fatigue applied for old generations (gen > 6).

    stability_boost : peaks mid-life, then erodes via fatigue
    mutation_scale  : drops for proven lineages, then recovers via fatigue
    extinction_bias : poor-fitness old lineages die faster

    Bounds enforced:
        stability_boost ∈ [0.70, 1.50]
        mutation_scale  ∈ [0.50, 1.50]
    """
    try:
        ln = _CLUSTER_LINEAGE.get(cid)
        if ln is None:
            return {"stability_boost": 1.0, "mutation_scale": 1.0, "extinction_bias": 1.0}
        gen = ln["generation"]
        fit = ln["fitness_ema"]
        age = ln["survival_cycles"]

        gen_factor = min(gen / 10.0, 1.0)    # saturates at gen=10
        age_factor = min(age / 20.0, 1.0)    # saturates at 20 cycles

        # Base pressures (same as before)
        stability_boost = 1.0 + 0.10 * gen_factor * age_factor
        mutation_scale  = 1.0 - 0.30 * gen_factor
        extinction_bias = 1.0 - 0.20 * fit

        # PART 1 -- Fatigue factor: zero for gen <= 6, ramps to 1.0 by gen ~16
        fatigue = max(0.0, min(1.0, (gen - 6) / 10.0))

        # PART 2 -- Apply fatigue
        stability_boost *= (1.0 - 0.30 * fatigue)   # erode dominance advantage
        mutation_scale  *= (1.0 + 0.50 * fatigue)   # regain exploratory pressure

        # PART 3 -- Weak old lineages become more extinction-vulnerable
        if fit < 0.6:
            extinction_bias *= (1.0 + 0.20 * fatigue)

        # RIVALRY (Parts 3/4/6): affect only stability_boost + mutation_scale
        stability_boost, mutation_scale = apply_rivalry_effects(
            cid, stability_boost, mutation_scale, fatigue
        )

        # PART 2: conditional soft saturation -- shock absorber, not always-on flattener
        deviation = stability_boost - 1.0
        if abs(deviation) > 0.2:
            # PART 2: compression strength tied to ecosystem health
            # health = avg fitness of active non-extinct clusters (inline, no circular dep)
            active_fits = [
                _get_global_fitness(k)
                for k in _CLUSTER_FITNESS
                if k not in _INACTIVE_CLUSTERS and k not in _HARD_EXTINCT
            ]
            ecosystem_health = sum(active_fits) / max(1, len(active_fits))
            compress = 0.80 if ecosystem_health < 0.70 else 0.90

            # PART 3: protect top-20% performers -- preserve dominance incentive
            all_fits = sorted(active_fits, reverse=True)
            top_threshold = all_fits[max(0, int(len(all_fits) * 0.20) - 1)] if all_fits else 1.0
            if _get_global_fitness(cid) >= top_threshold:
                compress = max(compress, 0.90)   # top cluster: at most light compression

            stability_boost = 1.0 + deviation * compress

        # Clamp to final spec bounds (PART 8)
        stability_boost = round(max(0.50, min(1.50, stability_boost)), 6)
        mutation_scale  = round(max(0.50, min(2.00, mutation_scale)),  6)
        extinction_bias = round(extinction_bias, 6)

        return {
            "stability_boost": stability_boost,
            "mutation_scale":  mutation_scale,
            "extinction_bias": extinction_bias,
        }
    except Exception:
        return {"stability_boost": 1.0, "mutation_scale": 1.0, "extinction_bias": 1.0}


# ---------------------------------------------------------------------------
# Niche Capital System (Parts 1-6)
# ---------------------------------------------------------------------------

def update_niche_capital(cid: str, resource_share: float) -> None:
    """
    PART 2 -- Accumulate capital from resource share, then apply anti-monopoly bleed.

    capital += 0.1 * resource_share   (winner builds territory)
    if capital > 0.7: bleed excess    (PART 6: anti-monopoly)
    """
    try:
        cap = _NICHE_CAPITAL.get(cid, 0.0)
        cap += 0.10 * resource_share
        cap  = min(cap, 1.0)
        # PART 6: anti-monopoly bleed above 0.7
        if cap > 0.7:
            extra = cap - 0.7
            cap  -= extra * 0.5
        _NICHE_CAPITAL[cid] = round(max(0.0, cap), 6)
    except Exception as exc:
        LOGGER.debug("swarm_capital_update_error cid=%s error=%s", cid, exc)


def decay_niche_capital() -> None:
    """
    PART 4 -- Apply 3% decay to all capitals each cycle.
    Clusters that stop performing gradually lose territorial advantage.
    """
    for cid in list(_NICHE_CAPITAL):
        _NICHE_CAPITAL[cid] = round(_NICHE_CAPITAL[cid] * 0.97, 6)


def contest_niche_capital(winner: str, loser: str, amount: float = 0.05) -> None:
    """
    PART 5 -- Transfer capital from loser to winner within same rivalry.
    Creates stateful territorial impact from each rivalry contest.
    amount is clamped so loser cannot go below 0.
    """
    try:
        w = _NICHE_CAPITAL.get(winner, 0.0)
        l = _NICHE_CAPITAL.get(loser,  0.0)
        transfer = min(amount, l)
        _NICHE_CAPITAL[winner] = round(min(1.0, w + transfer), 6)
        _NICHE_CAPITAL[loser]  = round(max(0.0, l - transfer), 6)
    except Exception as exc:
        LOGGER.debug("swarm_capital_contest_error winner=%s loser=%s error=%s", winner, loser, exc)


# ---------------------------------------------------------------------------
# Rivalry System (Parts 1-6)
# ---------------------------------------------------------------------------

def update_rivalry_groups() -> None:
    """
    PART 1 -- Rebuild _LINEAGE_RIVALS from current niche + resource state.

    Rivalry condition (BOTH must hold):
      1. Same behavioral niche tuple
      2. |resource_i - resource_j| < 0.15

    Pool cap: max 2 rivals per cluster to prevent global entanglement.
    Deterministic: sorted IDs ensure stable grouping order.
    """
    _LINEAGE_RIVALS.clear()
    try:
        active = sorted(
            cid for cid in _CLUSTER_FITNESS
            if cid not in _INACTIVE_CLUSTERS and cid not in _HARD_EXTINCT
        )
        niche_groups: dict[tuple, list[str]] = {}
        for cid in active:
            n = _CLUSTER_NICHE.get(cid)
            if n:
                niche_groups.setdefault(n, []).append(cid)

        for members in niche_groups.values():
            if len(members) < 2:
                continue
            for i, a in enumerate(members):
                for b in members[i + 1:]:
                    ra = _CLUSTER_RESOURCE.get(a, 0.0)
                    rb = _CLUSTER_RESOURCE.get(b, 0.0)
                    if abs(ra - rb) < 0.15:
                        if len(_LINEAGE_RIVALS.get(a, set())) < 2:
                            _LINEAGE_RIVALS.setdefault(a, set()).add(b)
                        if len(_LINEAGE_RIVALS.get(b, set())) < 2:
                            _LINEAGE_RIVALS.setdefault(b, set()).add(a)
    except Exception as exc:
        LOGGER.debug("swarm_rivalry_update_error error=%s", exc)


def get_rivalry_pressure(cid: str) -> float:
    """
    PART 2 -- Rivalry pressure in [0, 1].

    pressure = clamp((f_rival_max - f_self) / 1.5, 0, 1)
      0 -> dominant or no rivals
      1 -> heavily outcompeted
    """
    try:
        rivals = _LINEAGE_RIVALS.get(cid)
        if not rivals:
            return 0.0
        f_self      = _get_global_fitness(cid)
        f_rival_max = max(_get_global_fitness(r) for r in rivals)
        return round(max(0.0, min(1.0, (f_rival_max - f_self) / 1.5)), 6)
    except Exception:
        return 0.0


def apply_rivalry_effects(
    cid: str,
    stability_boost: float,
    mutation_scale: float,
    fatigue: float,
) -> tuple[float, float]:
    """
    PARTS 3/4/6 -- Apply rivalry pressure to stability_boost and mutation_scale ONLY.

    Resource is NOT directly modified. Instead, stability_boost feeds into
    resource allocation naturally (resource oc fitness * stability_boost),
    creating smooth indirect pressure without hard overrides.

    PART 4: shield gives stability_boost *= 1.05 (not resource *= 1.05).
    PART 6: effective_fatigue = fatigue * (1 - 0.5*pressure) for comeback mechanic.
    """
    try:
        pressure = get_rivalry_pressure(cid)

        # PART 6: modulate effective fatigue based on rivalry pressure
        effective_fatigue = fatigue * (1.0 - 0.5 * pressure)
        fatigue_delta     = effective_fatigue - fatigue   # <= 0 when under pressure

        # Fatigue correction for struggling clusters
        stability_boost *= (1.0 - 0.30 * fatigue_delta)
        mutation_scale  *= (1.0 + 0.50 * fatigue_delta)

        # PART 3: rivalry pressure -> stability erosion + mutation boost (PARTS 5/6)
        stability_boost *= (1.0 - 0.20 * pressure)   # indirect resource via stability
        mutation_scale  *= (1.0 + 0.60 * pressure)   # losing -> explore more

        # PART 4: shield for dominant low-pressure winner -> stability bonus only
        rivals = _LINEAGE_RIVALS.get(cid)
        if rivals:
            f_self      = _get_global_fitness(cid)
            f_rival_max = max(_get_global_fitness(r) for r in rivals)
            if f_self >= f_rival_max and pressure < 0.2:
                # PART 1: diminishing shield -- near-zero boost when already high
                shield = 0.05 * (1.0 - stability_boost)
                stability_boost *= (1.0 + shield)
                mutation_scale  *= 0.90   # stabilize winner

        return stability_boost, mutation_scale
    except Exception:
        return stability_boost, mutation_scale


def get_rivalry_shield_survivor(group: list[str]) -> str | None:
    """
    PART 5 -- Anti-collapse guard.

    When ALL members of a rivalry group are weak, return the fittest one
    so extinction logic can spare it (last lineage standing).
    """
    try:
        alive = [c for c in group
                 if c not in _INACTIVE_CLUSTERS and c not in _HARD_EXTINCT]
        if not alive:
            return None
        all_weak = all(_get_global_fitness(c) < FITNESS_WEAK_THRESHOLD for c in alive)
        if all_weak:
            return max(alive, key=_get_global_fitness)
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# PART 1 -- encode_genome: optimizer state -> compressed genotype
# ---------------------------------------------------------------------------

def encode_genome(state: dict[str, float]) -> dict[str, float]:
    """
    Compress an optimizer param state dict into 4 semantic genome traits.

    Mapping:
        aggressiveness_base = behavior_aggressiveness_mult
        exploration_base    = strategy_explorer_weight_mult
        risk_tolerance      = 1.0 / (1.0 + delay_excess)
                              where delay_excess = max(0, platform_delay_base_mult - 1)
        diversity_bias      = strategy_harvester_weight_mult

    All results clamped to [GENOME_CLAMP_MIN, GENOME_CLAMP_MAX].
    Exception-safe -- returns neutral genome on any error.
    """
    try:
        def _g(key: str, default: float = GENOME_NEUTRAL) -> float:
            return float(state.get(key, default))

        delay    = _g("platform_delay_base_mult")
        delay_excess = max(0.0, delay - 1.0)
        risk_tol = 1.0 / (1.0 + delay_excess)

        raw = {
            "aggressiveness_base": _g("behavior_aggressiveness_mult"),
            "exploration_base":    _g("strategy_explorer_weight_mult"),
            "risk_tolerance":      risk_tol,
            "diversity_bias":      _g("strategy_harvester_weight_mult"),
        }
        return _clamp_genome(raw)
    except Exception as exc:
        LOGGER.debug("encode_genome_error error=%s", exc)
        return {k: GENOME_NEUTRAL for k in GENOME_KEYS}


# ---------------------------------------------------------------------------
# PART 2 -- decode_genome: genotype -> multiplicative phenotype deltas
# ---------------------------------------------------------------------------

def decode_genome(genome: dict[str, float]) -> dict[str, float]:
    """
    Expand genome traits into optimizer param multipliers.

    The result is used by blend_genome_into_state() to nudge the optimizer
    state toward the cluster's genetic baseline BEFORE metric-driven changes.

    Mapping (inverse of encode_genome):
        behavior_aggressiveness_mult  *= aggressiveness_base
        strategy_explorer_weight_mult *= exploration_base
        platform_delay_base_mult      *= 1.0 / risk_tolerance
        strategy_harvester_weight_mult *= diversity_bias

    Returns a dict of {optimizer_key: multiplier}.  Multipliers of 1.0 are
    no-ops.  All values clamped to [0.60, 1.40] (optimizer bounds).
    Exception-safe.
    """
    try:
        def _gv(k: str) -> float:
            return float(genome.get(k, GENOME_NEUTRAL))

        risk_tol = _gv("risk_tolerance")
        delay_mult = 1.0 / risk_tol if risk_tol > 0 else 1.0

        raw = {
            "behavior_aggressiveness_mult":   _gv("aggressiveness_base"),
            "strategy_explorer_weight_mult":  _gv("exploration_base"),
            "platform_delay_base_mult":       delay_mult,
            "strategy_harvester_weight_mult": _gv("diversity_bias"),
        }
        # Clamp to optimizer bounds
        return {k: round(max(0.60, min(1.40, v)), 6) for k, v in raw.items()}
    except Exception as exc:
        LOGGER.debug("decode_genome_error error=%s", exc)
        return {}


# ---------------------------------------------------------------------------
# PART 3 -- record_cluster_genome: EMA-smooth update (anti-noise)
# ---------------------------------------------------------------------------

def _init_genome(encoded: dict[str, float]) -> dict:
    """Create a fresh core+delta genome structure from an encoded base."""
    return {
        "core":  dict(encoded),
        "delta": {ctx: _small_zero() for ctx in GENOME_DELTA_CONTEXTS},
    }


def record_cluster_genome(cluster_id: str, optimizer_state: dict[str, float], risk: float = 0.5) -> None:
    """
    Core+Delta EMA update with context-filtered delta signal.

    core[k]  always updated: EWMA α=0.10 (stable shared knowledge)
    delta[k] gated by context_strength and dead-zone:
        residual       = observed[k] - new_core[k]
        dead-zone      : |residual| < 0.02 → skip (PART 3)
        clip           : residual clamped to [-0.10, +0.10] (PART 5)
        context_weight : residual * context_strength (PART 1/2)
        inactive decay : other contexts *= 0.995/cycle (PART 4)
        final clamp    : delta[k] in [-GENOME_DELTA_CLAMP, +GENOME_DELTA_CLAMP]
    """
    try:
        observed = encode_genome(optimizer_state)
        ctx = get_context(risk)
        ctx_strength = get_context_strength(risk)   # PART 1/2

        if cluster_id not in _CLUSTER_GENOME:
            _CLUSTER_GENOME[cluster_id] = _init_genome(observed)
            return

        g = _CLUSTER_GENOME[cluster_id]
        core  = g["core"]
        delta = g["delta"]

        # ── Update core (always) ──────────────────────────────────────────────
        new_core: dict[str, float] = {}
        for k in GENOME_KEYS:
            cv = core.get(k, GENOME_NEUTRAL)
            ov = observed.get(k, GENOME_NEUTRAL)
            new_core[k] = round(
                cv * (1.0 - GENOME_CORE_ALPHA) + ov * GENOME_CORE_ALPHA, 6)
        new_core = _clamp_genome(new_core)
        g["core"] = new_core

        # ── Update active delta (context-filtered) ────────────────────────────
        if ctx not in delta:
            delta[ctx] = _small_zero()
        d = delta[ctx]

        for k in GENOME_KEYS:
            dv = d.get(k, 0.0)
            raw_residual = observed.get(k, GENOME_NEUTRAL) - new_core[k]

            # PART 3: dead-zone — ignore residuals smaller than noise threshold
            if abs(raw_residual) < 0.02:
                raw_residual = 0.0

            # PART 5: clip extreme update to prevent single-cycle spikes
            residual = max(-0.10, min(0.10, raw_residual))

            # PART 1: weight by context strength (middle zone → 0.0, no learning)
            weighted = residual * ctx_strength

            new_dv = dv * 0.95 + weighted * GENOME_DELTA_ALPHA
            new_dv = max(-GENOME_DELTA_CLAMP, min(GENOME_DELTA_CLAMP, new_dv))
            d[k] = round(new_dv, 6)

        # PART 4: passive decay on inactive contexts (not updated this cycle)
        for other_ctx, od in delta.items():
            if other_ctx == ctx:
                continue
            for k in list(od):
                od[k] = round(od[k] * 0.995, 6)

        LOGGER.debug(
            "swarm_genome_record cluster=%s ctx=%s strength=%.3f",
            cluster_id, ctx, ctx_strength,
        )
    except Exception as exc:
        LOGGER.debug("swarm_genome_record_error cluster=%s error=%s", cluster_id, exc)


def resolve_genome(cluster_id: str, ctx: str) -> dict[str, float]:
    """
    PART 2 — Resolve the effective genome for a context.

    effective[k] = core[k] * (1 + delta[ctx][k])
    Context is an "adjustment", not a separate genome.
    """
    try:
        g = _CLUSTER_GENOME.get(cluster_id)
        if not g:
            return {k: GENOME_NEUTRAL for k in GENOME_KEYS}
        core  = g.get("core",  {k: GENOME_NEUTRAL for k in GENOME_KEYS})
        delta = g.get("delta", {}).get(ctx, {})
        return _clamp_genome({
            k: round(core.get(k, GENOME_NEUTRAL) * (1.0 + delta.get(k, 0.0)), 6)
            for k in GENOME_KEYS
        })
    except Exception as exc:
        LOGGER.debug("resolve_genome_error cluster=%s ctx=%s error=%s", cluster_id, ctx, exc)
        return {k: GENOME_NEUTRAL for k in GENOME_KEYS}


def get_genome(cluster_id: str, risk: float = 0.5) -> dict[str, float]:
    """Public accessor — resolves the effective genome for the given risk level."""
    ctx = get_context(risk)
    return resolve_genome(cluster_id, ctx)


# ---------------------------------------------------------------------------
# PART 4 -- _mutate_genome: mutation at DNA level, not runtime state
# ---------------------------------------------------------------------------

def _mutate_genome(
    parent_g:     dict,
    child_id:     str,
    parent_id:    str,
    daughter_idx: int,
) -> dict:
    """
    PART 5 — Produce a mutated child genome (core+delta structure).

    core  mutates strongly: factor in [0.95, 1.05]
    delta mutates lightly:  factor in [0.98, 1.02]
    """
    pressure_mult = get_mutation_pressure(parent_id)

    parent_core  = parent_g.get("core",  {k: GENOME_NEUTRAL for k in GENOME_KEYS})
    parent_delta = parent_g.get("delta", {ctx: _small_zero() for ctx in GENOME_DELTA_CONTEXTS})

    # Mutate core strongly
    child_core: dict[str, float] = {}
    core_spread = GENOME_MUTATION_SPREAD * pressure_mult   # +-5% base
    for k, v in parent_core.items():
        seed = stable_hash_int(child_id, "core_inherit", k, str(daughter_idx)) % 1000
        n01  = seed / 999.0
        # 0.95 + 0.10*rand → range [0.95, 1.05]
        noise = 0.95 + 0.10 * n01 * (1.0 + core_spread)
        child_core[k] = round(
            max(GENOME_CLAMP_MIN, min(GENOME_CLAMP_MAX, v * noise)), 6)

    # Mutate delta lightly
    child_delta: dict[str, dict[str, float]] = {}
    for ctx in GENOME_DELTA_CONTEXTS:
        parent_ctx_delta = parent_delta.get(ctx, _small_zero())
        child_ctx: dict[str, float] = {}
        for k, dv in parent_ctx_delta.items():
            seed = stable_hash_int(child_id, "delta_inherit", ctx, k, str(daughter_idx)) % 1000
            n01  = seed / 999.0
            # 0.98 + 0.04*rand → range [0.98, 1.02]
            noise = 0.98 + 0.04 * n01
            new_dv = dv * noise
            child_ctx[k] = round(
                max(-GENOME_DELTA_CLAMP, min(GENOME_DELTA_CLAMP, new_dv)), 6)
        child_delta[ctx] = child_ctx

    return {"core": child_core, "delta": child_delta}


# ---------------------------------------------------------------------------
# PART 5 -- apply_fitness_feedback_to_genome: gene self-adjusts on survival
# ---------------------------------------------------------------------------

def apply_fitness_feedback_to_genome(cluster_id: str) -> None:
    """
    PART 7 — Fitness only adjusts core; delta stays context-specific.

    Strong fitness (>1.2): core aggressiveness_base *=1.02
    Weak   fitness (<0.5): core exploration_base    *=1.05
    """
    try:
        fitness = _get_global_fitness(cluster_id)
        g = _CLUSTER_GENOME.get(cluster_id)
        if not g:
            return
        core = g.get("core")
        if not core:
            return

        if fitness > GENOME_PRESSURE_STRONG_THRESH:
            core["aggressiveness_base"] = round(
                max(GENOME_CLAMP_MIN, min(GENOME_CLAMP_MAX,
                    core.get("aggressiveness_base", GENOME_NEUTRAL)
                    * GENOME_FEEDBACK_STRONG_AGGR_STEP)), 6)

        if fitness < GENOME_PRESSURE_WEAK_THRESH:
            core["exploration_base"] = round(
                max(GENOME_CLAMP_MIN, min(GENOME_CLAMP_MAX,
                    core.get("exploration_base", GENOME_NEUTRAL)
                    * GENOME_FEEDBACK_WEAK_EXPLORE_STEP)), 6)

        LOGGER.debug("swarm_genome_feedback cluster=%s fitness=%.3f", cluster_id, fitness)
    except Exception as exc:
        LOGGER.debug("swarm_genome_feedback_error cluster=%s error=%s", cluster_id, exc)


# ---------------------------------------------------------------------------
# PART 6 -- _normalize_genome: clamp to prevent runaway evolution
# ---------------------------------------------------------------------------

def _clamp_genome(genome: dict[str, float]) -> dict[str, float]:
    """Clamp all genome values to [GENOME_CLAMP_MIN, GENOME_CLAMP_MAX]."""
    return {
        k: round(max(GENOME_CLAMP_MIN, min(GENOME_CLAMP_MAX, float(v))), 6)
        for k, v in genome.items()
    }


def normalize_genome(cluster_id: str) -> None:
    """PART 6 — Clamp core in-place; clamp delta to [-GENOME_DELTA_CLAMP, +GENOME_DELTA_CLAMP]."""
    try:
        g = _CLUSTER_GENOME.get(cluster_id)
        if not g:
            return
        if "core" in g:
            g["core"] = _clamp_genome(g["core"])
        for ctx, d in g.get("delta", {}).items():
            g["delta"][ctx] = {
                k: round(max(-GENOME_DELTA_CLAMP, min(GENOME_DELTA_CLAMP, float(v))), 6)
                for k, v in d.items()
            }
    except Exception as exc:
        LOGGER.debug("swarm_normalize_genome_error cluster=%s error=%s", cluster_id, exc)


# ---------------------------------------------------------------------------
# Genome decay (toward neutral each cycle)
# ---------------------------------------------------------------------------

def apply_genome_decay() -> None:
    """Decay core toward GENOME_NEUTRAL each cycle; deltas decay faster toward 0."""
    try:
        neutral_weight = 1.0 - GENOME_DECAY
        for cid, g in _CLUSTER_GENOME.items():
            core = g.get("core", {})
            for k in list(core):
                decayed   = core[k] * GENOME_DECAY + GENOME_NEUTRAL * neutral_weight
                core[k]   = round(max(GENOME_CLAMP_MIN, min(GENOME_CLAMP_MAX, decayed)), 6)
            for ctx, d in g.get("delta", {}).items():
                for k in list(d):
                    # Delta decays faster toward 0
                    d[k] = round(
                        max(-GENOME_DELTA_CLAMP, min(GENOME_DELTA_CLAMP,
                            d[k] * (GENOME_DECAY ** 2))), 6)
        LOGGER.debug("swarm_genome_decay n_clusters=%d", len(_CLUSTER_GENOME))
    except Exception as exc:
        LOGGER.debug("swarm_genome_decay_error error=%s", exc)


# ---------------------------------------------------------------------------
# Genome -> optimizer state bridge (replaces old blend_genome_into_state)
# ---------------------------------------------------------------------------

def blend_genome_into_state(
    cluster_id: str,
    state:      dict[str, float],
    risk:       float = 0.5,
    alpha:      float = GENOME_DECODE_ALPHA,
) -> dict[str, float]:
    """Decode resolved genome and blend phenotype multipliers into state."""
    try:
        ctx = get_context(risk)
        effective = resolve_genome(cluster_id, ctx)
        if not effective:
            return state
        decoded = decode_genome(effective)
        for k, decoded_val in decoded.items():
            if k in state:
                blended  = (1.0 - alpha) * state[k] + alpha * decoded_val
                state[k] = round(max(0.60, min(1.40, blended)), 6)
    except Exception as exc:
        LOGGER.debug("swarm_blend_genome_error cluster=%s error=%s", cluster_id, exc)
    return state


# ---------------------------------------------------------------------------
# Mutation pressure
# ---------------------------------------------------------------------------

def get_mutation_pressure(cluster_id: str, ctx: str = "high_risk") -> float:
    """
    Return genome mutation noise multiplier based on global (core) fitness.
    Weak (<0.5) -> x1.5;  Strong (>1.2) -> x0.8;  else 1.0.
    ctx param kept for backwards-compat but global fitness is used.
    """
    try:
        fitness = _get_global_fitness(cluster_id)
        if fitness < GENOME_PRESSURE_WEAK_THRESH:
            return GENOME_PRESSURE_WEAK_MULT
        if fitness > GENOME_PRESSURE_STRONG_THRESH:
            return GENOME_PRESSURE_STRONG_MULT
        return 1.0
    except Exception:
        return 1.0


# ---------------------------------------------------------------------------
# Fitness scoring
# ---------------------------------------------------------------------------

def _get_global_fitness(cluster_id: str) -> float:
    ctx_fitness = _CLUSTER_FITNESS.get(cluster_id)
    if not ctx_fitness:
        return 1.0
    # Use average fitness across contexts for global ops
    return sum(ctx_fitness.values()) / max(1, len(ctx_fitness))

def update_cluster_fitness(cluster_id: str, reward: float, risk: float) -> float:
    """EMA-update core fitness. Delta contexts don't track separate fitness. Exception-safe."""
    try:
        ctx = get_context(risk)
        risk   = max(0.0, float(risk)   if risk   == risk   else 0.0)
        reward = float(reward) if reward == reward else 0.0
        raw    = (reward + FITNESS_REWARD_SHIFT) / (1.0 + risk)

        if cluster_id not in _CLUSTER_FITNESS:
            _CLUSTER_FITNESS[cluster_id] = {c: 1.0 for c in GENOME_DELTA_CONTEXTS}

        prev   = _CLUSTER_FITNESS[cluster_id].get(ctx, 1.0)
        new    = round(prev * FITNESS_PREV + raw * FITNESS_ALPHA, 6)
        _CLUSTER_FITNESS[cluster_id][ctx] = new
        return new
    except Exception as exc:
        LOGGER.debug("swarm_fitness_error cluster=%s error=%s", cluster_id, exc)
        return 0.5


# ---------------------------------------------------------------------------
# Resource allocation
# ---------------------------------------------------------------------------

def allocate_resources() -> dict[str, float]:
    """
    PARTS 4/5/6 — Niche-aware resource allocation.

    Within each niche group resources are proportional to fitness (Part 4).
    The niche leader gets a 1.1x bonus (Part 6).
    No single cluster can hold >40% globally (Part 5).
    Soft-extinct clusters are crushed to 10% of their share (Part 8).
    """
    try:
        if not _CLUSTER_FITNESS:
            return dict(_CLUSTER_RESOURCE)

        active = {
            cid: _get_global_fitness(cid)
            for cid in _CLUSTER_FITNESS
            if cid not in _INACTIVE_CLUSTERS and cid not in _HARD_EXTINCT
        }
        if not active:
            return dict(_CLUSTER_RESOURCE)

        n    = len(active)
        fair = 1.0 / max(1, n)

        # ── PART 4: group by niche, compute intra-niche relative shares ────────
        niche_groups: dict[tuple, list[str]] = {}
        for cid in active:
            niche = _CLUSTER_NICHE.get(cid)
            niche_groups.setdefault(niche or ("__none__",), []).append(cid)

        niche_score: dict[str, float] = {}
        for niche, members in niche_groups.items():
            # NICHE CAPITAL PARTS 2/3: capital boosts effective_fitness within niche only
            # cap_effect = 1 + 0.20 * sqrt(capital) * min(1, fitness)
            # -> capital is an amplifier, not a crutch: weak fitness kills the effect
            f_map   = {
                cid: max(active[cid], 0.01) * (
                    1.0 + 0.20
                    * (_NICHE_CAPITAL.get(cid, 0.0) ** 0.5)
                    * min(1.0, active[cid])   # fitness gate
                )
                for cid in members
            }
            total_f = sum(f_map.values()) or 1e-9
            ranked  = sorted(f_map.items(), key=lambda x: x[1], reverse=True)
            for rank, (cid, f) in enumerate(ranked):
                share = f / total_f
                # PART 6: niche leader bonus (capped so blending stays sane)
                if rank == 0:
                    share = min(share * NICHE_DOMINANCE_BONUS, 1.0)
                niche_score[cid] = share

        # ── PART 4: adaptive blend weights based on system maturity ────────
        if _SYSTEM_CYCLE > 50:
            w_global, w_niche, w_fair = 0.60, 0.30, 0.10
        elif _SYSTEM_CYCLE > 20:
            w_global, w_niche, w_fair = 0.50, 0.30, 0.20
        else:
            w_global, w_niche, w_fair = 0.40, 0.40, 0.20

        global_total = sum(max(f, 0.01) for f in active.values()) or 1e-9
        raw: dict[str, float] = {}
        for cid, f in active.items():
            global_share = max(f, 0.01) / global_total
            niche_rel    = niche_score.get(cid, fair)
            # PART 5.2: ancestry stability_boost scales resource share (rivalry already baked in)
            pressure = get_ancestry_pressure(cid)
            blend    = w_global * global_share + w_niche * niche_rel + w_fair * fair
            blend   *= pressure["stability_boost"]
            if f < FITNESS_WEAK_THRESHOLD:
                blend *= FITNESS_SUPPRESS_MULT
            # PART 8: soft-extinct -> crush resource
            if cid in _SOFT_EXTINCT:
                blend *= SOFT_EXTINCTION_RESOURCE_MULT
            raw[cid] = max(blend, 0.0)

        # ── PART 5: iterative 40% cap ─────────────────────────────────────
        cap = RESOURCE_DOMINANCE_CAP
        if n == 1:
            _CLUSTER_RESOURCE[next(iter(raw))] = 1.0
        else:
            for _ in range(10):
                raw_total  = sum(raw.values()) or 1.0
                norm       = {cid: v / raw_total for cid, v in raw.items()}
                over_cap   = {cid for cid, v in norm.items() if v > cap}
                if not over_cap:
                    for cid, v in norm.items():
                        _CLUSTER_RESOURCE[cid] = round(v, 6)
                    break
                surplus  = 0.0
                uncapped = {}
                for cid, v in norm.items():
                    if cid in over_cap:
                        raw[cid]  = cap * raw_total
                        surplus  += (v - cap) * raw_total
                    else:
                        uncapped[cid] = v
                ut = sum(uncapped.values()) or 1e-9
                for cid, v in uncapped.items():
                    raw[cid] += surplus * (v / ut)
            else:
                raw_total = sum(raw.values()) or 1.0
                for cid, v in raw.items():
                    _CLUSTER_RESOURCE[cid] = round(min(v / raw_total, cap), 6)

        for cid in _INACTIVE_CLUSTERS | _HARD_EXTINCT:
            _CLUSTER_RESOURCE[cid] = 0.0
        return dict(_CLUSTER_RESOURCE)
    except Exception as exc:
        LOGGER.debug("swarm_allocate_error error=%s", exc)
        return dict(_CLUSTER_RESOURCE)


def get_cluster_resource(cluster_id: str) -> float:
    if cluster_id in _INACTIVE_CLUSTERS:
        return 0.0
    return _CLUSTER_RESOURCE.get(cluster_id, 1.0)


def apply_resource_to_intensity(cluster_id: str, intensity: float) -> float:
    try:
        return round(max(0.0, min(1.0, float(intensity) * get_cluster_resource(cluster_id))), 4)
    except Exception:
        return intensity


def apply_resource_to_reach(cluster_id: str, reach: float) -> float:
    try:
        return round(max(0.0, min(1.0, float(reach) * get_cluster_resource(cluster_id))), 4)
    except Exception:
        return reach


# ---------------------------------------------------------------------------
# Weak streak + elimination
# ---------------------------------------------------------------------------

def tick_weak_streak(cluster_id: str) -> int:
    try:
        fitness = _get_global_fitness(cluster_id)
        if fitness < FITNESS_WEAK_THRESHOLD:
            streak = _CLUSTER_WEAK_STREAK.get(cluster_id, 0) + 1
            _CLUSTER_WEAK_STREAK[cluster_id] = streak
            # NICHE CAPITAL PART 4: faster decay when losing > 3 cycles
            if streak > 3 and cluster_id in _NICHE_CAPITAL:
                _NICHE_CAPITAL[cluster_id] = round(
                    _NICHE_CAPITAL[cluster_id] * 0.90, 6
                )
        else:
            _CLUSTER_WEAK_STREAK[cluster_id] = 0
            # Recover: remove from soft-extinct if fitness bounces back
            _SOFT_EXTINCT.discard(cluster_id)
        return _CLUSTER_WEAK_STREAK[cluster_id]
    except Exception as exc:
        LOGGER.debug("swarm_tick_weak_error cluster=%s error=%s", cluster_id, exc)
        return 0


def mark_cluster_inactive(cluster_id: str) -> None:
    _INACTIVE_CLUSTERS.add(cluster_id)
    _CLUSTER_RESOURCE[cluster_id] = 0.0
    LOGGER.warning("swarm_soft_death cluster=%s streak=%d",
                   cluster_id, _CLUSTER_WEAK_STREAK.get(cluster_id, 0))


def maybe_soft_extinct(cluster_id: str) -> bool:
    """PART 7/8 — Enter soft-extinction: minimal resource + 2x mutation (last chance)."""
    streak = _CLUSTER_WEAK_STREAK.get(cluster_id, 0)
    if (streak > SOFT_EXTINCTION_STREAK
            and cluster_id not in _SOFT_EXTINCT
            and cluster_id not in _HARD_EXTINCT
            and cluster_id not in _INACTIVE_CLUSTERS):
        _SOFT_EXTINCT.add(cluster_id)
        LOGGER.warning("swarm_soft_extinct cluster=%s streak=%d", cluster_id, streak)
        return True
    return False


def maybe_hard_extinct(cluster_id: str) -> bool:
    """PART 9 -- Full deletion: remove genome, zero resource, mark dead.
    RIVALRY PART 5: spares cluster if it is the last survivor in its rivalry group.
    """
    streak = _CLUSTER_WEAK_STREAK.get(cluster_id, 0)
    # PART 5.3: ancestry extinction_bias adjusts the threshold
    pressure  = get_ancestry_pressure(cluster_id)
    threshold = int(HARD_EXTINCTION_STREAK * pressure["extinction_bias"])
    if streak > threshold and cluster_id not in _HARD_EXTINCT:
        # RIVALRY PART 5: anti-collapse -- spare if best of an all-weak group
        rivals = list(_LINEAGE_RIVALS.get(cluster_id, set()))
        if rivals:
            group   = [cluster_id] + rivals
            survivor = get_rivalry_shield_survivor(group)
            if survivor == cluster_id:
                LOGGER.info("swarm_rivalry_shield cluster=%s spared", cluster_id)
                return False   # skip extinction; cluster is last viable member
        _HARD_EXTINCT.add(cluster_id)
        _INACTIVE_CLUSTERS.add(cluster_id)
        _SOFT_EXTINCT.discard(cluster_id)
        _CLUSTER_GENOME.pop(cluster_id, None)
        _CLUSTER_RESOURCE[cluster_id] = 0.0
        _CLUSTER_NICHE_LOCKED.pop(cluster_id, None)
        _CLUSTER_LINEAGE.pop(cluster_id, None)
        LOGGER.warning("swarm_hard_extinct cluster=%s threshold=%d", cluster_id, threshold)
        return True
    return False


def maybe_eliminate(cluster_id: str) -> bool:
    """Unified elimination gate: soft → hard extinction pipeline."""
    try:
        hard = maybe_hard_extinct(cluster_id)
        if not hard:
            maybe_soft_extinct(cluster_id)
        # Legacy path: old WEAK_STREAK_LIMIT still marks inactive if not already
        if (_CLUSTER_WEAK_STREAK.get(cluster_id, 0) > WEAK_STREAK_LIMIT
                and cluster_id not in _INACTIVE_CLUSTERS):
            mark_cluster_inactive(cluster_id)
        return hard   # only hard extinction is a true "elimination" event
    except Exception:
        return False


def is_cluster_inactive(cluster_id: str) -> bool:
    return cluster_id in _INACTIVE_CLUSTERS


# ---------------------------------------------------------------------------
# Rebirth (Part 10)
# ---------------------------------------------------------------------------

def _mix_genomes(g1: dict, g2: dict, f1: float, f2: float, child_id: str, idx: int) -> dict:
    """
    PART 3 — Asymmetric 70/30 mix biased toward fitter parent + mutation spike.

    dominant parent (higher fitness) contributes 70% of each gene;
    weak parent contributes 30% for diversity.
    mutation_scale = 1.2 gives offspring slight innovation beyond both parents.
    """
    if f1 >= f2:
        dominant_core = g1.get("core", {k: GENOME_NEUTRAL for k in GENOME_KEYS})
        weak_core     = g2.get("core", {k: GENOME_NEUTRAL for k in GENOME_KEYS})
    else:
        dominant_core = g2.get("core", {k: GENOME_NEUTRAL for k in GENOME_KEYS})
        weak_core     = g1.get("core", {k: GENOME_NEUTRAL for k in GENOME_KEYS})

    mutation_scale = 1.2   # PART 3: innovation spike vs pure averaging
    mixed: dict[str, float] = {}
    for k in GENOME_KEYS:
        base = dominant_core.get(k, GENOME_NEUTRAL) * 0.70 + weak_core.get(k, GENOME_NEUTRAL) * 0.30
        seed  = stable_hash_int(child_id, "rebirth", k, str(idx)) % 1000
        n01   = seed / 999.0
        noise = 0.95 + 0.10 * n01 * mutation_scale   # range [0.95, 1.17]
        mixed[k] = round(max(GENOME_CLAMP_MIN, min(GENOME_CLAMP_MAX, base * noise)), 6)
    return {
        "core":  mixed,
        "delta": {ctx: _small_zero() for ctx in GENOME_DELTA_CONTEXTS},
    }


def maybe_rebirth(all_cids: list[str]) -> str | None:
    """PART 10 — Spawn new cluster from asymmetric top-2 genome mix after hard extinction."""
    global _REBIRTH_COUNT
    try:
        if not any(cid in _HARD_EXTINCT for cid in all_cids):
            return None
        active_f = [
            (cid, _get_global_fitness(cid))
            for cid in all_cids
            if cid not in _INACTIVE_CLUSTERS
            and cid not in _HARD_EXTINCT
            and cid in _CLUSTER_GENOME
        ]
        if len(active_f) < 2:
            return None
        active_f.sort(key=lambda x: x[1], reverse=True)
        p1, f1 = active_f[0]
        p2, f2 = active_f[1]
        _REBIRTH_COUNT += 1
        new_id  = f"rebirth_{_REBIRTH_COUNT}"
        # PART 3: asymmetric mix using fitness-weighted parents
        child_g = _mix_genomes(_CLUSTER_GENOME[p1], _CLUSTER_GENOME[p2], f1, f2,
                                new_id, _REBIRTH_COUNT)
        _CLUSTER_GENOME[new_id] = child_g
        avg_f = (f1 + f2) / 2.0 * 0.75
        _CLUSTER_FITNESS[new_id] = {c: round(avg_f, 6) for c in GENOME_DELTA_CONTEXTS}
        # PART 2: rebirth lineage — root of new lineage, not child of parents
        _CLUSTER_LINEAGE[new_id] = _init_lineage(new_id, None)
        LOGGER.info("swarm_rebirth new=%s parents=(%s f=%.3f,%s f=%.3f)",
                    new_id, p1, f1, p2, f2)
        return new_id
    except Exception as exc:
        LOGGER.debug("swarm_rebirth_error error=%s", exc)
        return None


# ---------------------------------------------------------------------------
# Mutation boost
# ---------------------------------------------------------------------------

def get_mutation_boost(cluster_id: str) -> tuple[float, float]:
    """
    Return (exploration_mult, strength_mult) factoring in:
      - extinction wave global multiplier (Part 5)
      - soft extinction 2x last-chance (Part 8)
      - weak fitness standard boost
      - niche lock specialist dampening (Part 7): exploration*0.7, strength*0.5
    """
    try:
        # PART 5: active extinction wave boosts all mutation
        wave_mult = _GLOBAL_MUTATION_MULT

        # PART 8: soft-extinct → maximum mutation pressure
        if cluster_id in _SOFT_EXTINCT:
            return (
                MUTATION_EXPLORATION_MULT * SOFT_EXTINCTION_MUTATION_MULT * wave_mult,
                MUTATION_STRENGTH_MULT    * SOFT_EXTINCTION_MUTATION_MULT * wave_mult,
            )
        fitness = _get_global_fitness(cluster_id)
        # PART 5.1: ancestry mutation_scale + rivalry boost for losing clusters
        anc_pressure = get_ancestry_pressure(cluster_id)
        anc_scale    = anc_pressure["mutation_scale"]   # already includes rivalry via get_ancestry_pressure
        if fitness < FITNESS_MUTATE_THRESHOLD and cluster_id not in _INACTIVE_CLUSTERS:
            e_mult = MUTATION_EXPLORATION_MULT * wave_mult * anc_scale
            s_mult = MUTATION_STRENGTH_MULT    * wave_mult * anc_scale
        else:
            e_mult = 1.0 * wave_mult * anc_scale
            s_mult = 1.0 * wave_mult * anc_scale

        # PART 7: niche-locked → specialist dampening
        if is_niche_locked(cluster_id):
            e_mult *= NICHE_LOCK_EXPLORE_BIAS     # 0.70x exploration
            s_mult *= NICHE_LOCK_MUTATION_SCALE   # 0.50x strength
        return (e_mult, s_mult)
    except Exception:
        return (1.0, 1.0)


# ---------------------------------------------------------------------------
# Extinction Wave (Part 5)
# ---------------------------------------------------------------------------

def trigger_extinction_wave(all_cids: list[str]) -> list[str]:
    """
    PART 5 -- Break local optima: kill bottom-30% + contract resources.

    Triggered when avg_fitness < 0.6 and _SYSTEM_CYCLE > 30.
    Guarantees minimum 3 active clusters survive (Part 8).
    Sets GLOBAL_MUTATION_MULT=1.5 for 3 cycles.
    """
    global _EXTINCTION_WAVE_DURATION, _GLOBAL_MUTATION_MULT
    try:
        active = [
            (cid, _get_global_fitness(cid))
            for cid in all_cids
            if cid not in _INACTIVE_CLUSTERS and cid not in _HARD_EXTINCT
        ]
        active.sort(key=lambda x: x[1])   # weakest first
        kill_n = max(0, int(len(active) * 0.30))
        kill_n = min(kill_n, max(0, len(active) - 3))  # min 3 survivors
        killed: list[str] = []
        for cid, _ in active[:kill_n]:
            _HARD_EXTINCT.add(cid)
            _INACTIVE_CLUSTERS.add(cid)
            _SOFT_EXTINCT.discard(cid)
            _CLUSTER_GENOME.pop(cid, None)
            _CLUSTER_RESOURCE[cid] = 0.0
            killed.append(cid)
        # Global resource contraction
        for cid in list(_CLUSTER_RESOURCE):
            if cid not in _INACTIVE_CLUSTERS:
                _CLUSTER_RESOURCE[cid] = round(_CLUSTER_RESOURCE.get(cid, 0.0) * 0.80, 6)
        # Temporary mutation boost
        _GLOBAL_MUTATION_MULT     = 1.5
        _EXTINCTION_WAVE_DURATION = 3
        LOGGER.warning("swarm_extinction_wave killed=%s", killed)
        return killed
    except Exception as exc:
        LOGGER.debug("swarm_extinction_wave_error error=%s", exc)
        return []


# ---------------------------------------------------------------------------
# Ecosystem Validation (Part 9)
# ---------------------------------------------------------------------------

def validate_ecosystem(all_cids: list[str]) -> dict[str, Any]:
    """
    PART 9 -- Compute diagnostic metrics: convergence, dominance, niche homogeneity.

    Returns a dict with param_std, max_resource, n_active,
    niche_diversity, dominant_niche_pct, warnings.
    """
    try:
        active = [cid for cid in all_cids
                  if cid not in _INACTIVE_CLUSTERS and cid not in _HARD_EXTINCT]
        n = len(active)
        if n == 0:
            return {"warnings": ["no_active_clusters"]}

        vals = [
            _CLUSTER_GENOME[cid]["core"].get("aggressiveness_base", GENOME_NEUTRAL)
            for cid in active if cid in _CLUSTER_GENOME
        ]
        mean_v = sum(vals) / len(vals) if vals else 1.0
        std_v  = (sum((v - mean_v) ** 2 for v in vals) / max(1, len(vals))) ** 0.5 if vals else 0.0

        max_res = max((_CLUSTER_RESOURCE.get(cid, 0.0) for cid in active), default=0.0)

        niches = [_CLUSTER_NICHE.get(cid) for cid in active if _CLUSTER_NICHE.get(cid)]
        distinct_n = len(set(niches))
        niche_div  = distinct_n / max(1, n)
        dom_pct    = 0.0
        if niches:
            counts   = {}
            for nn in niches:
                counts[nn] = counts.get(nn, 0) + 1
            dom_pct  = max(counts.values()) / n

        warnings: list[str] = []
        if std_v < 0.02:        warnings.append("convergence_risk")
        if max_res > 0.40:      warnings.append("dominance_violation")
        if n < 3:               warnings.append("min_cluster_breach")
        if dom_pct > 0.30:      warnings.append("niche_homogeneity")

        return {
            "param_std":          round(std_v, 4),
            "max_resource":       round(max_res, 4),
            "n_active":           n,
            "niche_diversity":    round(niche_div, 4),
            "dominant_niche_pct": round(dom_pct, 4),
            "warnings":           warnings,
        }
    except Exception as exc:
        LOGGER.debug("swarm_validate_error error=%s", exc)
        return {"warnings": ["validation_error"]}


# ---------------------------------------------------------------------------
# Reproduction (with DNA-level inheritance)
# ---------------------------------------------------------------------------

def maybe_reproduce(
    cluster_id:      str,
    cluster_size:    int,
    all_cluster_ids: list[str],
) -> str | None:
    """Spawn a daughter cluster with mutated genotype.  Exception-safe."""
    try:
        fitness = _get_global_fitness(cluster_id)
        if cluster_id in _INACTIVE_CLUSTERS:
            return None
        if fitness < REPRODUCTION_FITNESS_MIN:
            return None
        if cluster_size < REPRODUCTION_SIZE_MIN:
            return None

        daughters    = _CLUSTER_DAUGHTERS.setdefault(cluster_id, [])
        daughter_idx = len(daughters)
        daughter_id  = f"{cluster_id}_d{daughter_idx}"
        all_known    = set(all_cluster_ids) | set(daughters)
        if daughter_id in all_known:
            return None

        daughters.append(daughter_id)

        # PART 2: init lineage record, inheriting from parent
        _CLUSTER_LINEAGE[daughter_id] = _init_lineage(daughter_id, cluster_id)
        # Seed daughter fitness for active contexts
        _CLUSTER_FITNESS[daughter_id] = {
            c: round(_CLUSTER_FITNESS[cluster_id].get(c, 1.0) * 0.80, 6)
            for c in GENOME_DELTA_CONTEXTS
        }

        # Mutate at DNA level — core+delta structure (PART 5)
        parent_g = _CLUSTER_GENOME.get(
            cluster_id,
            {"core": {k: GENOME_NEUTRAL for k in GENOME_KEYS},
             "delta": {ctx: _small_zero() for ctx in GENOME_DELTA_CONTEXTS}}
        )
        child_g = _mutate_genome(parent_g, daughter_id, cluster_id, daughter_idx)
        _CLUSTER_GENOME[daughter_id] = child_g

        LOGGER.info("swarm_reproduction parent=%s daughter=%s fitness=%.3f",
                    cluster_id, daughter_id, fitness)
        return daughter_id
    except Exception as exc:
        LOGGER.debug("swarm_reproduce_error cluster=%s error=%s", cluster_id, exc)
        return None


# ---------------------------------------------------------------------------
# Full Darwin cycle
# ---------------------------------------------------------------------------

def run_darwin_cycle(
    cluster_rewards: dict[str, float],
    cluster_risks:   dict[str, float],
    cluster_sizes:   dict[str, int],
) -> dict[str, Any]:
    """
    Full ecosystem cycle (Parts 4/5/7/8/9 integrated):
      fitness -> streaks -> extinction wave -> soft/hard extinction
      -> behavioral niche update -> resources (adaptive niche-aware)
      -> reproduction -> rebirth -> niche-lock -> genome feedback -> decay
      -> wave decay -> validation
    """
    global _SYSTEM_CYCLE, _EXTINCTION_WAVE_DURATION, _GLOBAL_MUTATION_MULT
    summary: dict[str, Any] = {
        "eliminated": [], "reproduced": [], "reborn": [],
        "soft_extinct": [], "niche_locked": [], "weak_clusters": [],
        "extinction_wave": [], "resource_map": {}, "fitness_map": {},
        "validation": {},
    }
    try:
        _SYSTEM_CYCLE += 1
        all_cids = list(set(list(cluster_rewards) + list(_CLUSTER_FITNESS)))

        # -- 1. Fitness update + lineage memory (Part 3) --------------------------
        for cid in all_cids:
            update_cluster_fitness(
                cid,
                cluster_rewards.get(cid, 0.0),
                cluster_risks.get(cid, 0.5),
            )
            if cid not in _INACTIVE_CLUSTERS and cid not in _HARD_EXTINCT:
                update_lineage_fitness(cid, _get_global_fitness(cid))

        # -- 2. Extinction wave check (Part 5) -----------------------------------
        active_fitness = [
            _get_global_fitness(cid)
            for cid in all_cids
            if cid not in _INACTIVE_CLUSTERS and cid not in _HARD_EXTINCT
        ]
        avg_fitness = sum(active_fitness) / max(1, len(active_fitness))
        if avg_fitness < 0.6 and _SYSTEM_CYCLE > 30 and _EXTINCTION_WAVE_DURATION == 0:
            wave_killed = trigger_extinction_wave(all_cids)
            summary["extinction_wave"] = wave_killed

        # -- 3. Weak streaks + per-cluster extinction pipeline -------------------
        for cid in all_cids:
            tick_weak_streak(cid)
            if _get_global_fitness(cid) < FITNESS_WEAK_THRESHOLD:
                summary["weak_clusters"].append(cid)
            if maybe_eliminate(cid):
                summary["eliminated"].append(cid)
            elif cid in _SOFT_EXTINCT:
                summary["soft_extinct"].append(cid)

        # -- 4. Behavioral niche update + rivalry groups (Parts 1/R1) -------------
        for cid in all_cids:
            if cid not in _INACTIVE_CLUSTERS and cid not in _HARD_EXTINCT:
                update_cluster_niche(cid)
        update_rivalry_groups()   # RIVALRY PART 1: rebuild after niche refresh

        # -- 5. Niche-aware + adaptive resource allocation (Parts 4/6) -----------
        resource_map = allocate_resources()
        summary["resource_map"] = {k: round(v, 4) for k, v in resource_map.items()}
        summary["fitness_map"]  = {k: round(_get_global_fitness(k), 4) for k in _CLUSTER_FITNESS}

        # -- 5b. Niche capital: accumulate + contest (Parts 2/5) -----------------
        for cid, res in resource_map.items():
            if cid not in _INACTIVE_CLUSTERS and cid not in _HARD_EXTINCT:
                update_niche_capital(cid, res)
        # Contest: for each rivalry pair, winner takes from loser
        for cid, rivals in _LINEAGE_RIVALS.items():
            if cid in _INACTIVE_CLUSTERS or cid in _HARD_EXTINCT:
                continue
            f_self = _get_global_fitness(cid)
            for rival in rivals:
                if rival in _INACTIVE_CLUSTERS or rival in _HARD_EXTINCT:
                    continue
                if f_self > _get_global_fitness(rival):
                    contest_niche_capital(winner=cid, loser=rival)

        # -- 6. Reproduction (fit + large clusters) ------------------------------
        existing = list(_CLUSTER_FITNESS.keys())
        spawned  = 0
        for cid, size in cluster_sizes.items():
            if spawned >= REPRODUCTION_MAX_DAUGHTERS_PER_CYCLE:
                break
            d = maybe_reproduce(cid, size, existing)
            if d:
                summary["reproduced"].append(d)
                spawned += 1

        # -- 7. Rebirth from asymmetric top-2 mix (Part 3/10) -------------------
        rb = maybe_rebirth(all_cids)
        if rb:
            summary["reborn"].append(rb)

        # -- 8. Niche lock check (Part 7) ----------------------------------------
        for cid in all_cids:
            if cid not in _INACTIVE_CLUSTERS and cid not in _HARD_EXTINCT:
                if check_niche_lock(cid):
                    summary["niche_locked"].append(cid)

        # -- 9. Genome feedback + decay + capital decay (Parts 9/C4) ------------
        for cid in all_cids:
            if cid not in _INACTIVE_CLUSTERS:
                apply_fitness_feedback_to_genome(cid)
                normalize_genome(cid)
        apply_genome_decay()
        decay_niche_capital()   # NICHE CAPITAL PART 4: 3% decay each cycle

        # -- 10. Wave decay (Part 5) ----------------------------------------------
        if _EXTINCTION_WAVE_DURATION > 0:
            _EXTINCTION_WAVE_DURATION -= 1
            if _EXTINCTION_WAVE_DURATION == 0:
                _GLOBAL_MUTATION_MULT = 1.0
                LOGGER.info("swarm_extinction_wave_ended")

        # -- 11. Ecosystem validation (Part 9) ------------------------------------
        summary["validation"] = validate_ecosystem(all_cids)

        LOGGER.info(
            "swarm_darwin_cycle=%d clusters=%d weak=%d soft_ext=%d "
            "eliminated=%d reproduced=%d reborn=%d wave=%d locked=%d",
            _SYSTEM_CYCLE, len(all_cids), len(summary["weak_clusters"]),
            len(summary["soft_extinct"]), len(summary["eliminated"]),
            len(summary["reproduced"]),  len(summary["reborn"]),
            len(summary["extinction_wave"]), len(summary["niche_locked"]),
        )
    except Exception as exc:
        LOGGER.warning("swarm_darwin_cycle_error error=%s", exc)

    return summary


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------

def snapshot() -> dict[str, Any]:
    """Full swarm ecosystem state for monitoring."""
    genomes_out: dict = {}
    for cid, g in _CLUSTER_GENOME.items():
        core = {gk: round(gv, 4) for gk, gv in g.get("core", {}).items()}
        delta = {
            ctx: {gk: round(gv, 4) for gk, gv in d.items()}
            for ctx, d in g.get("delta", {}).items()
        }
        genomes_out[cid] = {"core": core, "delta": delta}

    # PART 8: lineage observability
    lineage_out = {
        cid: {
            "gen":    ln["generation"],
            "fit":    round(ln["fitness_ema"], 3),
            "age":    ln["survival_cycles"],
            "root":   ln["lineage_id"],
            "parent": ln["parent"],
        }
        for cid, ln in _CLUSTER_LINEAGE.items()
    }
    # RIVALRY PART 7: rivalry observability
    rivalry_out = {
        cid: {
            "rivals":   sorted(rivals),
            "pressure": get_rivalry_pressure(cid),
        }
        for cid, rivals in _LINEAGE_RIVALS.items()
    }
    capital_out = {k: round(v, 4) for k, v in _NICHE_CAPITAL.items()}

    return {
        "fitness":        {k: {ctx: round(v, 4) for ctx, v in f.items()} for k, f in _CLUSTER_FITNESS.items()},
        "resources":      {k: round(v, 4) for k, v in _CLUSTER_RESOURCE.items()},
        "weak_streaks":   dict(_CLUSTER_WEAK_STREAK),
        "inactive":       list(_INACTIVE_CLUSTERS),
        "soft_extinct":   list(_SOFT_EXTINCT),
        "hard_extinct":   list(_HARD_EXTINCT),
        "daughters":      {k: list(v) for k, v in _CLUSTER_DAUGHTERS.items()},
        "genomes":        genomes_out,
        "niches":         {k: list(v) for k, v in _CLUSTER_NICHE.items()},
        "niche_locked":   [k for k, v in _CLUSTER_NICHE_LOCKED.items() if v],
        "stability":      {k: round(v, 4) for k, v in _CLUSTER_STABILITY.items()},
        "ages":           dict(_CLUSTER_AGE),
        "rebirth_count":  _REBIRTH_COUNT,
        "lineage":        lineage_out,
        "rivalry":        rivalry_out,
        "niche_capital":  capital_out,
        "n_active":       len(_CLUSTER_FITNESS) - len(_INACTIVE_CLUSTERS),
        "resource_total": round(
            sum(v for k, v in _CLUSTER_RESOURCE.items()
                if k not in _INACTIVE_CLUSTERS), 4),
    }
