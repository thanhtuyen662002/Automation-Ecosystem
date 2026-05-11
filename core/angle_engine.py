"""
core/angle_engine.py — Content Alpha Engine

Generates high-performing content angles BEFORE they appear in the market.

Pipeline:
    generate_angles(niche)           → list[Angle]
    score_angles(angles)             → list[Angle]  (top 30%)
    record_micro_test(angle_id, ...) → None
    validate_angle(angle_id)         → bool
    get_best_angles(niche, n)        → list[Angle]   (validated first)
    record_performance(angle_id, ...) → None
    enrich_candidate(candidate, niche) → dict        ← Part 4 integration

Public selectors (Part 4 priority):
    validated_angle > scraped content

Parts 5 + 6:
    - feedback loop updates angle_success_rate / avg_views / conversion_rate
    - reuse > 5 times → score penalty + variation flag
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import random
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("core.angle_engine")

# ── DB path ───────────────────────────────────────────────────────────────────
_DB_PATH = Path(os.environ.get("ANGLE_ENGINE_DB", "data/angle_engine.db"))

# ── Scoring weights (Part 2) ──────────────────────────────────────────────────
W_NOVELTY              = 0.30
W_EMOTIONAL_STRENGTH   = 0.25
W_CLARITY              = 0.20
W_VIRALITY_MATCH       = 0.15
W_COST_EFFICIENCY      = 0.10

# ── Thresholds ────────────────────────────────────────────────────────────────
_TOP_PCT               = 0.30    # keep top 30% by score
_VALIDATION_THRESHOLD  = 2 / 3   # 2-out-of-3 micro-test metrics above threshold
_MICRO_METRIC_GATE     = 0.50    # per-metric threshold for micro-test pass
_REUSE_PENALTY_COUNT   = 5       # Part 6: reuse > 5 → 20% score penalty
_REUSE_PENALTY_FACTOR  = 0.80

# ── Angle archetypes (used to seed generation) ────────────────────────────────
_ARCHETYPES: dict[str, dict[str, Any]] = {
    "pain_point": {
        "emotion":  "frustration",
        "format":   "problem_solution",
        "virality": 0.75,
        "clarity":  0.80,
    },
    "desire": {
        "emotion":  "aspiration",
        "format":   "transformation",
        "virality": 0.80,
        "clarity":  0.75,
    },
    "curiosity_gap": {
        "emotion":  "curiosity",
        "format":   "reveal",
        "virality": 0.85,
        "clarity":  0.70,
    },
    "contrarian": {
        "emotion":  "surprise",
        "format":   "myth_bust",
        "virality": 0.90,
        "clarity":  0.65,
    },
    "emotional_trigger": {
        "emotion":  "empathy",
        "format":   "story",
        "virality": 0.70,
        "clarity":  0.85,
    },
}

# ── Hook templates per archetype ──────────────────────────────────────────────
_HOOK_TEMPLATES: dict[str, list[str]] = {
    "pain_point": [
        "Why you keep failing at {niche} (and the real fix)",
        "Stop doing this in {niche} — it's killing your results",
        "The {niche} mistake 90% of people make",
        "This {niche} habit is destroying your progress",
        "Everyone in {niche} gets this wrong",
    ],
    "desire": [
        "How I achieved {niche} results in 30 days",
        "The {niche} formula no one talks about",
        "What high performers in {niche} do differently",
        "This one {niche} habit changed everything",
        "The fastest path to {niche} success",
    ],
    "curiosity_gap": [
        "The {niche} secret the pros don't share",
        "What happens when you do {niche} for 90 days",
        "I tried every {niche} strategy — here's what worked",
        "The counterintuitive truth about {niche}",
        "What nobody tells you about {niche}",
    ],
    "contrarian": [
        "Unpopular opinion: {niche} advice is backwards",
        "Why conventional {niche} wisdom is wrong",
        "I stopped following {niche} rules and this happened",
        "The {niche} 'best practice' that actually hurts you",
        "What {niche} gurus won't admit",
    ],
    "emotional_trigger": [
        "My honest {niche} journey (raw + real)",
        "The {niche} moment that changed my life",
        "Why I almost gave up on {niche}",
        "This {niche} story hit different",
        "The emotional side of {niche} no one discusses",
    ],
}


# ── Pre-trend weights (Part 5) ───────────────────────────────────────────────
_W_VIEW_VELOCITY    = 0.35
_W_SHARE_RATE       = 0.25
_W_SAVE_RATE        = 0.20
_W_COMMENT_VELOCITY = 0.20
_PRE_TREND_THRESHOLD = 0.55   # early_trend_score above this triggers boost in execution_brain

# ── Pattern similarity weights (Parts 1-2) ────────────────────────────────────
_W_SIM_EMOTION  = 0.25
_W_SIM_HOOK     = 0.25
_W_SIM_FORMAT   = 0.20
_W_SIM_TOPIC    = 0.15
_W_SIM_PACING   = 0.15

# ── Recent winner memory (Part 3) ─────────────────────────────────────────────
# Maintained as a module-level ring-buffer of (angle_id, pattern_signature, score)
_WINNER_MEMORY_N  = 20
_WIN_AMP_THRESH   = 0.5   # amplification_score threshold to enter winner memory
_WIN_GROW_THRESH  = 0.3   # growth_trend threshold (alternative gate)
# {angle_id: {"signature": dict, "amp": float, "growth": float}}
_recent_top_angles: dict[str, dict[str, Any]] = {}

# ── Pacing style map per archetype ────────────────────────────────────────────
_ARCHETYPE_PACING: dict[str, str] = {
    "pain_point":       "slow_build",
    "desire":           "fast_hook",
    "curiosity_gap":    "reveal",
    "contrarian":       "shock",
    "emotional_trigger": "story",
}

# ── Schema ────────────────────────────────────────────────────────────────────
_DDL = """
CREATE TABLE IF NOT EXISTS angles (
    angle_id            TEXT PRIMARY KEY,
    niche               TEXT NOT NULL,
    archetype           TEXT NOT NULL,
    hook_idea           TEXT NOT NULL,
    target_emotion      TEXT NOT NULL,
    content_format      TEXT NOT NULL,
    angle_score         REAL NOT NULL DEFAULT 0.0,
    novelty             REAL NOT NULL DEFAULT 0.5,
    emotional_strength  REAL NOT NULL DEFAULT 0.5,
    clarity             REAL NOT NULL DEFAULT 0.5,
    virality_match      REAL NOT NULL DEFAULT 0.5,
    cost_efficiency     REAL NOT NULL DEFAULT 0.8,
    is_validated        INTEGER NOT NULL DEFAULT 0,
    lifecycle           TEXT NOT NULL DEFAULT 'new',
    dominance_score     REAL NOT NULL DEFAULT 0.0,
    amplification_score REAL NOT NULL DEFAULT 0.0,
    early_trend_score   REAL NOT NULL DEFAULT 0.0,
    pattern_match_score REAL NOT NULL DEFAULT 0.0,
    hybrid_pre_trend    REAL NOT NULL DEFAULT 0.0,
    pacing_style        TEXT NOT NULL DEFAULT '',
    hook_structure      TEXT NOT NULL DEFAULT '',
    reuse_count         INTEGER NOT NULL DEFAULT 0,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS angle_early_signals (
    angle_id          TEXT NOT NULL,
    view_velocity     REAL NOT NULL DEFAULT 0.0,
    share_rate        REAL NOT NULL DEFAULT 0.0,
    save_rate         REAL NOT NULL DEFAULT 0.0,
    comment_velocity  REAL NOT NULL DEFAULT 0.0,
    early_trend_score REAL NOT NULL DEFAULT 0.0,
    total_views       REAL NOT NULL DEFAULT 0.0,
    updated_at        REAL NOT NULL,
    PRIMARY KEY (angle_id)
);

CREATE TABLE IF NOT EXISTS angle_variations (
    variation_id      TEXT PRIMARY KEY,
    angle_id          TEXT NOT NULL,
    hook_variation    TEXT NOT NULL,
    frame_variation   TEXT NOT NULL,
    caption_variation TEXT NOT NULL,
    score             REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS micro_tests (
    test_id           TEXT PRIMARY KEY,
    angle_id          TEXT NOT NULL,
    account_id        TEXT NOT NULL,
    hook_retention    REAL,
    scroll_stop_rate  REAL,
    engagement_velocity REAL,
    passed            INTEGER NOT NULL DEFAULT 0,
    created_at        REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS angle_performance (
    angle_id          TEXT PRIMARY KEY,
    success_rate      REAL NOT NULL DEFAULT 0.0,
    avg_views         REAL NOT NULL DEFAULT 0.0,
    conversion_rate   REAL NOT NULL DEFAULT 0.0,
    cross_platform_success REAL NOT NULL DEFAULT 0.0,
    consecutive_wins  INTEGER NOT NULL DEFAULT 0,
    last_5_perf       TEXT NOT NULL DEFAULT '[]',
    growth_trend      REAL NOT NULL DEFAULT 0.0,
    publish_count     INTEGER NOT NULL DEFAULT 0,
    updated_at        REAL NOT NULL
);
"""


def _db() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(_DB_PATH), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_DDL)
    con.commit()
    return con


def _angle_id(niche: str, archetype: str, hook: str) -> str:
    key = f"{niche}:{archetype}:{hook[:40]}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


# ── Part 1 — Angle generation ─────────────────────────────────────────────────

@dataclass
class Angle:
    angle_id:           str
    niche:              str
    archetype:          str
    hook_idea:          str
    target_emotion:     str
    content_format:     str
    angle_score:        float = 0.0
    novelty:            float = 0.5
    emotional_strength: float = 0.5
    clarity:            float = 0.5
    virality_match:     float = 0.5
    cost_efficiency:    float = 0.8
    is_validated:       bool  = False
    lifecycle:          str   = "new"
    dominance_score:    float = 0.0
    amplification_score: float = 0.0
    early_trend_score:  float = 0.0   # reactive signal
    pattern_match_score: float = 0.0  # Part 4: predictive signal
    hybrid_pre_trend:   float = 0.0   # Part 5: blended score
    pacing_style:       str   = ""    # Part 1: pattern signature field
    hook_structure:     str   = ""    # Part 1: pattern signature field
    reuse_count:        int   = 0
    # Part 6: variation flag
    needs_variation:    bool  = False

    # ── Part 1: pattern signature ─────────────────────────────────────────────
    def pattern_signature(self) -> dict[str, str]:
        """Returns a 5-field structural fingerprint for similarity matching."""
        return {
            "emotion_type":    self.target_emotion,
            "hook_structure":  self.hook_structure or _derive_hook_structure(self.hook_idea),
            "content_format":  self.content_format,
            "niche_topic":     self.niche,
            "pacing_style":    self.pacing_style or _ARCHETYPE_PACING.get(self.archetype, "neutral"),
        }

    def to_candidate_fields(self) -> dict[str, Any]:
        """Part 4: fields injected into execution_brain candidate dict."""
        return {
            "angle_id":                  self.angle_id,
            "angle_archetype":           self.archetype,
            "angle_hook":                self.hook_idea,
            "angle_emotion":             self.target_emotion,
            "angle_format":              self.content_format,
            "angle_score":               self.angle_score,
            "angle_validated":           self.is_validated,
            "angle_lifecycle":           self.lifecycle,
            "angle_amplification_score": self.amplification_score,
            "early_trend_score":         self.early_trend_score,
            "pattern_match_score":       self.pattern_match_score,
            "hybrid_pre_trend":          self.hybrid_pre_trend,
            "angle_needs_variation":     self.needs_variation,
            # Pass angle's novelty + emotional strength as content signals
            "novelty_score":             self.novelty,
            "hook_score":                self.emotional_strength * 0.9 + self.virality_match * 0.1,
        }


# ── Part 1: Hook structure derivation ────────────────────────────────────────

_HOOK_STRUCTURE_PATTERNS: list[tuple[str, str]] = [
    ("why you",           "question_why"),
    ("how i",             "personal_story"),
    ("stop doing",        "command_stop"),
    ("what happens",      "curiosity_what"),
    ("the secret",        "reveal_secret"),
    ("unpopular opinion", "contrarian"),
    ("i tried",           "experiment"),
    ("this one",          "singular_tip"),
]


def _derive_hook_structure(hook_idea: str) -> str:
    """Classify hook into one of ~8 structural buckets based on leading phrase."""
    lower = hook_idea.lower()
    for phrase, label in _HOOK_STRUCTURE_PATTERNS:
        if lower.startswith(phrase) or phrase in lower[:40]:
            return label
    return "generic"


# ── Part 2: Pattern similarity engine ────────────────────────────────────────

def compute_pattern_similarity(
    sig_a: dict[str, str],
    sig_b: dict[str, str],
) -> float:
    """
    Part 2: Weighted structural similarity between two pattern signatures.
    Returns normalised score in [0, 1].
    """
    emotion_match = 1.0 if sig_a.get("emotion_type")    == sig_b.get("emotion_type")    else 0.0
    hook_match    = 1.0 if sig_a.get("hook_structure")  == sig_b.get("hook_structure")  else 0.0
    format_match  = 1.0 if sig_a.get("content_format")  == sig_b.get("content_format")  else 0.0
    topic_match   = 1.0 if sig_a.get("niche_topic")     == sig_b.get("niche_topic")     else 0.0
    pacing_match  = 1.0 if sig_a.get("pacing_style")    == sig_b.get("pacing_style")    else 0.0
    raw = (
        _W_SIM_EMOTION  * emotion_match
        + _W_SIM_HOOK   * hook_match
        + _W_SIM_FORMAT * format_match
        + _W_SIM_TOPIC  * topic_match
        + _W_SIM_PACING * pacing_match
    )
    return round(max(0.0, min(1.0, raw)), 4)


# ── Part 3: Recent winner memory management ───────────────────────────────────

def update_winner_memory(
    angle_id:            str,
    signature:           dict[str, str],
    amplification_score: float,
    growth_trend:        float,
) -> None:
    """
    Part 3: Add angle to recent_top_angles memory if it qualifies.
    Criteria: high amplification_score OR high growth_trend.
    Evicts oldest entry when memory exceeds _WINNER_MEMORY_N.
    """
    qualifies = (
        amplification_score >= _WIN_AMP_THRESH
        or growth_trend >= _WIN_GROW_THRESH
    )
    if not qualifies:
        return
    _recent_top_angles[angle_id] = {
        "signature": signature,
        "amp":       amplification_score,
        "growth":    growth_trend,
    }
    # Evict oldest if over capacity (dict preserves insertion order in Py3.7+)
    while len(_recent_top_angles) > _WINNER_MEMORY_N:
        oldest_key = next(iter(_recent_top_angles))
        del _recent_top_angles[oldest_key]


def get_winner_memory() -> dict[str, dict[str, Any]]:
    """Part 3: Return a snapshot of the current recent_top_angles memory."""
    return dict(_recent_top_angles)


# ── Part 4: Pattern-match score ───────────────────────────────────────────────

def compute_pattern_match_score(candidate_sig: dict[str, str]) -> float:
    """
    Part 4: Max similarity of candidate signature against recent winner memory.
    Returns 0.0 when memory is empty (cold start — safe neutral).
    """
    if not _recent_top_angles:
        return 0.0
    return round(max(
        compute_pattern_similarity(candidate_sig, entry["signature"])
        for entry in _recent_top_angles.values()
    ), 4)


# ── Part 5: Hybrid pre-trend score ───────────────────────────────────────────

def compute_hybrid_pre_trend(
    early_trend_score:   float,
    pattern_match_score: float,
) -> float:
    """
    Part 5: Blend reactive (early_trend_score) with predictive (pattern_match_score).
    hybrid = 0.6 * reactive + 0.4 * predictive
    """
    return round(max(0.0, min(1.0,
        0.6 * early_trend_score + 0.4 * pattern_match_score
    )), 4)


def generate_angles(
    niche:   str,
    n:       int = 30,
    rng:     random.Random | None = None,
) -> list[Angle]:
    """
    Part 1: Generate n content angles for a given niche.
    Uses 5 archetypes × hook templates + noise variation.
    Each angle includes hook_idea, target_emotion, content_format.
    """
    rng = rng or random.Random(int(time.time() * 1000) % 2**31)
    angles: list[Angle] = []
    archetypes  = list(_ARCHETYPES.keys())
    per_type    = max(1, n // len(archetypes))
    extra       = n - per_type * len(archetypes)

    for i, arch in enumerate(archetypes):
        meta     = _ARCHETYPES[arch]
        hooks    = _HOOK_TEMPLATES[arch]
        count    = per_type + (1 if i < extra else 0)
        for j in range(count):
            hook_tpl = hooks[j % len(hooks)]
            hook     = hook_tpl.replace("{niche}", niche)
            # Add variation suffix to avoid exact duplicates
            if j >= len(hooks):
                hook = hook + f" (angle {j + 1})"

            # Intrinsic scores vary slightly per angle (deterministic noise)
            _seed  = abs(hash(f"{niche}:{arch}:{j}")) % 10000
            _r     = random.Random(_seed)
            novelty            = round(min(1.0, max(0.2, meta["clarity"] + _r.uniform(-0.15, 0.15))), 3)
            emotional_strength = round(min(1.0, max(0.2, 0.7 + _r.uniform(-0.2, 0.2))), 3)
            clarity            = round(min(1.0, max(0.2, meta["clarity"] + _r.uniform(-0.10, 0.10))), 3)
            virality_match     = round(min(1.0, max(0.2, meta["virality"] + _r.uniform(-0.15, 0.15))), 3)
            cost_efficiency    = round(min(1.0, max(0.4, 0.80 + _r.uniform(-0.10, 0.10))), 3)

            aid = _angle_id(niche, arch, hook)
            angles.append(Angle(
                angle_id         = aid,
                niche            = niche,
                archetype        = arch,
                hook_idea        = hook,
                target_emotion   = meta["emotion"],
                content_format   = meta["format"],
                novelty          = novelty,
                emotional_strength = emotional_strength,
                clarity          = clarity,
                virality_match   = virality_match,
                cost_efficiency  = cost_efficiency,
            ))

    return angles


# ── Part 2 — Angle scoring ────────────────────────────────────────────────────

@dataclass
class AngleVariation:
    variation_id:      str
    angle_id:          str
    hook_variation:    str
    frame_variation:   str
    caption_variation: str
    score:             float

def expand_variations(angle: Angle, rng: random.Random | None = None) -> list[AngleVariation]:
    """Part 2: Generate dynamic variations based on dominance score."""
    rng = rng or random.Random(int(time.time() * 1000) % 2**31)
    
    if angle.dominance_score > 0.7:
        n = rng.randint(8, 10)
    elif angle.dominance_score > 0.5:
        n = rng.randint(5, 7)
    else:
        n = rng.randint(3, 5)
        
    variations = []
    
    hook_mods = ["(Warning)", "Must watch", "Listen up", "Truth bomb", "Wait for it", "Stop scrolling"]
    frame_mods = ["Zoom in", "Quick cut", "Text pop", "Glitch intro", "Sudden motion"]
    cap_mods = ["👇 Drop a comment", "Link in bio", "Save this", "Share with a friend"]
    
    con = _db()
    try:
        for i in range(n):
            vid = _angle_id(angle.angle_id, "var", str(i))
            hv = f"{rng.choice(hook_mods)}: {angle.hook_idea}"
            fv = rng.choice(frame_mods)
            cv = f"{angle.hook_idea} {rng.choice(cap_mods)}"
            score = round(rng.uniform(0.5, 1.0) * angle.angle_score, 4)
            
            var = AngleVariation(vid, angle.angle_id, hv, fv, cv, score)
            variations.append(var)
            
            con.execute("""
                INSERT OR IGNORE INTO angle_variations 
                (variation_id, angle_id, hook_variation, frame_variation, caption_variation, score)
                VALUES (?,?,?,?,?,?)
            """, (vid, angle.angle_id, hv, fv, cv, score))
        con.commit()
    finally:
        con.close()
    return variations

def _raw_angle_score(angle: Angle) -> float:
    """Part 2: compute raw score (pre-penalty)."""
    return round(
        W_NOVELTY            * angle.novelty
        + W_EMOTIONAL_STRENGTH * angle.emotional_strength
        + W_CLARITY            * angle.clarity
        + W_VIRALITY_MATCH     * angle.virality_match
        + W_COST_EFFICIENCY    * angle.cost_efficiency,
        4,
    )


def score_angles(angles: list[Angle], top_pct: float = _TOP_PCT) -> list[Angle]:
    """
    Part 2: Score all angles, apply Part 6 reuse penalty, keep top 30%.
    Returns list sorted descending by angle_score.
    """
    if not angles:
        return []

    # Fetch reuse counts from DB
    try:
        con = _db()
        rows = con.execute(
            "SELECT angle_id, reuse_count FROM angles"
        ).fetchall()
        con.close()
        reuse_map = {r["angle_id"]: r["reuse_count"] for r in rows}
    except Exception:
        reuse_map = {}

    for a in angles:
        raw   = _raw_angle_score(a)
        reuse = reuse_map.get(a.angle_id, a.reuse_count)
        # Part 6: anti-commodity rule
        if reuse > _REUSE_PENALTY_COUNT:
            raw           = round(raw * _REUSE_PENALTY_FACTOR, 4)
            a.needs_variation = True
        a.reuse_count = reuse
        a.angle_score = raw

    angles.sort(key=lambda x: x.angle_score, reverse=True)
    keep_n = max(1, round(len(angles) * top_pct))
    return angles[:keep_n]


def persist_angles(angles: list[Angle]) -> None:
    """Upsert angles into DB (idempotent)."""
    now = time.time()
    con = _db()
    try:
        for a in angles:
            _pacing = a.pacing_style or _ARCHETYPE_PACING.get(a.archetype, "neutral")
            _hook_s = a.hook_structure or _derive_hook_structure(a.hook_idea)
            con.execute("""
                INSERT INTO angles
                    (angle_id, niche, archetype, hook_idea, target_emotion, content_format,
                     angle_score, novelty, emotional_strength, clarity, virality_match,
                     cost_efficiency, is_validated, lifecycle, dominance_score,
                     amplification_score, early_trend_score,
                     pattern_match_score, hybrid_pre_trend,
                     pacing_style, hook_structure,
                     reuse_count, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(angle_id) DO UPDATE SET
                    angle_score         = excluded.angle_score,
                    reuse_count         = excluded.reuse_count,
                    is_validated        = MAX(is_validated, excluded.is_validated),
                    lifecycle           = excluded.lifecycle,
                    dominance_score     = excluded.dominance_score,
                    amplification_score = excluded.amplification_score,
                    early_trend_score   = excluded.early_trend_score,
                    pattern_match_score = excluded.pattern_match_score,
                    hybrid_pre_trend    = excluded.hybrid_pre_trend,
                    pacing_style        = excluded.pacing_style,
                    hook_structure      = excluded.hook_structure,
                    updated_at          = excluded.updated_at
            """, (
                a.angle_id, a.niche, a.archetype, a.hook_idea,
                a.target_emotion, a.content_format,
                a.angle_score, a.novelty, a.emotional_strength,
                a.clarity, a.virality_match, a.cost_efficiency,
                int(a.is_validated), a.lifecycle, a.dominance_score,
                a.amplification_score, a.early_trend_score,
                a.pattern_match_score, a.hybrid_pre_trend,
                _pacing, _hook_s,
                a.reuse_count, now, now,
            ))
        con.commit()
    finally:
        con.close()


# ── Part 3 — Micro-testing ────────────────────────────────────────────────────

def record_micro_test(
    angle_id:            str,
    account_id:          str,
    hook_retention:      float,
    scroll_stop_rate:    float,
    engagement_velocity: float,
) -> bool:
    """
    Part 3: Record 1 micro-test result.
    Returns True if this test passes (2/3 metrics above threshold).
    """
    metrics  = [hook_retention, scroll_stop_rate, engagement_velocity]
    passed_n = sum(1 for m in metrics if m >= _MICRO_METRIC_GATE)
    passed   = passed_n >= 2   # 2/3 rule

    test_id = _angle_id(angle_id, account_id, str(time.time()))
    now     = time.time()
    con     = _db()
    try:
        con.execute("""
            INSERT OR IGNORE INTO micro_tests
            (test_id, angle_id, account_id, hook_retention, scroll_stop_rate,
             engagement_velocity, passed, created_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (test_id, angle_id, account_id,
              hook_retention, scroll_stop_rate, engagement_velocity,
              int(passed), now))
        con.commit()

        # Validate angle if ≥2 passing tests exist
        passing = con.execute(
            "SELECT COUNT(*) FROM micro_tests WHERE angle_id=? AND passed=1",
            (angle_id,)
        ).fetchone()[0]
        if passing >= 2:
            con.execute(
                "UPDATE angles SET is_validated=1, lifecycle='validated', updated_at=? WHERE angle_id=?",
                (now, angle_id)
            )
            con.commit()
            LOGGER.info("angle_validated angle_id=%s", angle_id)
    finally:
        con.close()

    return passed


def validate_angle(angle_id: str) -> bool:
    """Returns True if angle is currently marked validated."""
    try:
        con = _db()
        row = con.execute(
            "SELECT is_validated FROM angles WHERE angle_id=?", (angle_id,)
        ).fetchone()
        con.close()
        return bool(row and row["is_validated"])
    except Exception:
        return False


# ── Part 4 — Integration helpers ──────────────────────────────────────────────

def get_best_angles(
    niche: str,
    n:     int = 5,
) -> list[Angle]:
    """
    Part 4: Return top-n angles for a niche.
    Priority: validated_angle > high_score > rest.
    Falls back to generating + scoring if DB is empty.
    """
    try:
        con  = _db()
        rows = con.execute("""
            SELECT * FROM angles
            WHERE niche = ?
            ORDER BY is_validated DESC, angle_score DESC
            LIMIT ?
        """, (niche, n)).fetchall()
        con.close()

        if rows:
            return [_row_to_angle(r) for r in rows]
    except Exception:
        pass

    # Fallback: generate + score on-the-fly
    raw     = generate_angles(niche, n=30)
    scored  = score_angles(raw)
    persist_angles(scored)
    return scored[:n]


def enrich_candidate(
    candidate: dict[str, Any],
    niche:     str,
) -> dict[str, Any]:
    """
    Part 4: Merge best angle fields INTO the candidate dict.

    Priority rule:
      - If candidate already has angle_id (pre-selected) → validate + inject.
      - Else → pick best validated angle for this niche.
      - validated_angle fields override scraped defaults for hook_score/novelty_score.

    Returns enriched candidate (dict copy).
    """
    candidate = dict(candidate)   # non-destructive

    # If the candidate was already built from an angle, respect it
    if candidate.get("angle_id") and validate_angle(candidate["angle_id"]):
        candidate["is_validated_angle"] = True
        candidate.setdefault("angle_score", 0.6)
        return candidate

    # Pick best available angle
    angles = get_best_angles(niche, n=1)
    if not angles:
        candidate["is_validated_angle"] = False
        return candidate

    best = angles[0]
    candidate.update(best.to_candidate_fields())
    candidate["is_validated_angle"] = best.is_validated
    candidate["angle_lifecycle"]    = best.lifecycle
    
    # Check variations expansion
    con = _db()
    var_count = con.execute("SELECT COUNT(*) FROM angle_variations WHERE angle_id=?", (best.angle_id,)).fetchone()[0]
    con.close()
    if var_count == 0 and best.is_validated:
        expand_variations(best)

    # Increment reuse counter
    _increment_reuse(best.angle_id)
    return candidate


def _row_to_angle(row: sqlite3.Row) -> Angle:
    _keys = row.keys()
    return Angle(
        angle_id             = row["angle_id"],
        niche                = row["niche"],
        archetype            = row["archetype"],
        hook_idea            = row["hook_idea"],
        target_emotion       = row["target_emotion"],
        content_format       = row["content_format"],
        angle_score          = float(row["angle_score"]),
        novelty              = float(row["novelty"]),
        emotional_strength   = float(row["emotional_strength"]),
        clarity              = float(row["clarity"]),
        virality_match       = float(row["virality_match"]),
        cost_efficiency      = float(row["cost_efficiency"]),
        is_validated         = bool(row["is_validated"]),
        lifecycle            = row["lifecycle"],
        dominance_score      = float(row["dominance_score"]),
        amplification_score  = float(row["amplification_score"]),
        early_trend_score    = float(row["early_trend_score"])    if "early_trend_score"    in _keys else 0.0,
        pattern_match_score  = float(row["pattern_match_score"])  if "pattern_match_score"  in _keys else 0.0,
        hybrid_pre_trend     = float(row["hybrid_pre_trend"])     if "hybrid_pre_trend"     in _keys else 0.0,
        pacing_style         = str(row["pacing_style"])           if "pacing_style"         in _keys else "",
        hook_structure       = str(row["hook_structure"])         if "hook_structure"        in _keys else "",
        reuse_count          = int(row["reuse_count"]),
        needs_variation      = int(row["reuse_count"]) > _REUSE_PENALTY_COUNT,
    )


# ── Part 5 — Feedback loop ────────────────────────────────────────────────────

def record_performance(
    angle_id:        str,
    views:           float,
    conversions:     float,
    was_successful:  bool,
) -> None:
    """
    Part 5: Update angle performance after publish.
    Updates success_rate, avg_views, conversion_rate.
    Boosts angle_score for winners; decays for losers.
    """
    now = time.time()
    con = _db()
    import json
    try:
        row = con.execute(
            "SELECT * FROM angle_performance WHERE angle_id=?", (angle_id,)
        ).fetchone()

        if row:
            n  = row["publish_count"] + 1
            sr = round((row["success_rate"] * (n - 1) + int(was_successful)) / n, 4)
            av = round((row["avg_views"]    * (n - 1) + views)               / n, 4)
            cr = round((row["conversion_rate"] * (n - 1) + conversions)      / n, 4)
            cp = round((row["cross_platform_success"] * (n - 1) + int(views > 10000)) / n, 4)
            
            # Winner memory computation
            last_5 = json.loads(row["last_5_perf"])
            last_5.append(float(views))
            if len(last_5) > 5:
                last_5.pop(0)
            
            if was_successful and views >= av:
                c_wins = int(row["consecutive_wins"]) + 1
            else:
                c_wins = 0 # Part 6: Auto-decay - reset wins
                
            last_5_avg = sum(last_5) / len(last_5) if last_5 else 0.0
            g_trend = round(min(1.0, max(-1.0, (views - last_5_avg) / max(1.0, last_5_avg))), 4)
            
            con.execute("""
                UPDATE angle_performance
                SET success_rate=?, avg_views=?, conversion_rate=?, cross_platform_success=?, 
                    consecutive_wins=?, last_5_perf=?, growth_trend=?,
                    publish_count=?, updated_at=?
                WHERE angle_id=?
            """, (sr, av, cr, cp, c_wins, json.dumps(last_5), g_trend, n, now, angle_id))
        else:
            n  = 1   # first publish — matches the INSERT publish_count=1 below
            sr = 1.0 if was_successful else 0.0
            av = views
            cr = conversions
            cp = 1.0 if views > 10000 else 0.0
            c_wins = 1 if was_successful else 0
            last_5 = [float(views)]
            g_trend = 0.0
            
            con.execute("""
                INSERT INTO angle_performance
                (angle_id, success_rate, avg_views, conversion_rate, cross_platform_success, 
                 consecutive_wins, last_5_perf, growth_trend, publish_count, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (angle_id, sr, av, cr, cp, c_wins, json.dumps(last_5), g_trend, 1, now))

        # Fetch current angles row to update amplification_score
        angle_row = con.execute("SELECT amplification_score FROM angles WHERE angle_id=?", (angle_id,)).fetchone()
        current_amp = float(angle_row["amplification_score"]) if angle_row else 0.0
        
        # Part 2: Amplification score
        consistency = sr
        norm_wins = min(1.0, c_wins / 5.0)
        norm_growth = max(0.0, g_trend)
        new_amp = round(0.4 * norm_wins + 0.3 * norm_growth + 0.3 * consistency, 4)
        
        # Part 6: Auto decay
        if not was_successful or views < av:
            new_amp = round(current_amp * 0.70, 4) # decay by 30% per cycle
            
        # Part 3: Angle Dominance Signal
        dom_score = round(0.4 * sr + 0.4 * min(1.0, av / 50000) + 0.2 * cp, 4)

        # Part 5: Update lifecycle
        lifecycle_update = ""
        new_lifecycle = "testing"
        if dom_score > 0.8 and n > 5:
            new_lifecycle = "scaling"
            lifecycle_update = ", lifecycle='scaling'"
        elif dom_score < 0.3 and n > 10:
            new_lifecycle = "saturated"
            lifecycle_update = ", lifecycle='saturated'"

        con.execute(f"""
            UPDATE angles
            SET dominance_score = ?, amplification_score = ? {lifecycle_update}
            WHERE angle_id = ?
        """, (dom_score, new_amp, angle_id))

        # Retrain angle_score using performance feedback (Part 5)
        perf_boost = 0.0
        if sr > 0.70:
            perf_boost = +0.05   # boost winners
        elif sr < 0.30:
            perf_boost = -0.05   # decay losers

        if perf_boost != 0.0:
            con.execute("""
                UPDATE angles
                SET angle_score = MIN(1.0, MAX(0.0, angle_score + ?)),
                    updated_at  = ?
                WHERE angle_id = ?
            """, (perf_boost, now, angle_id))

        con.commit()
        LOGGER.debug("angle_performance angle_id=%s sr=%.3f av=%.0f cr=%.4f",
                     angle_id, sr, av, cr)

        # Part 3: Update winner memory when performance qualifies
        angle_meta = con.execute(
            "SELECT archetype, target_emotion, content_format, niche, pacing_style, hook_structure, hook_idea"
            " FROM angles WHERE angle_id=?", (angle_id,)
        ).fetchone()
        if angle_meta:
            _sig = {
                "emotion_type":   angle_meta["target_emotion"],
                "hook_structure": angle_meta["hook_structure"] or _derive_hook_structure(angle_meta["hook_idea"]),
                "content_format": angle_meta["content_format"],
                "niche_topic":    angle_meta["niche"],
                "pacing_style":   angle_meta["pacing_style"] or _ARCHETYPE_PACING.get(angle_meta["archetype"], "neutral"),
            }
            update_winner_memory(angle_id, _sig, new_amp, g_trend)
    finally:
        con.close()


def _increment_reuse(angle_id: str) -> None:
    """Increment reuse counter + apply Part 6 score penalty if needed."""
    now = time.time()
    con = _db()
    try:
        # Part 6: Anti-commodity HARD MODE. usage > 10
        row = con.execute("SELECT reuse_count FROM angles WHERE angle_id=?", (angle_id,)).fetchone()
        current_reuse = row[0] if row else 0
        
        penalty_factor = 1.0
        if current_reuse + 1 > 10:
            penalty_factor = 0.5 # Force mutation by crippling score
            LOGGER.info("angle_mutation_forced usage=%d angle=%s", current_reuse + 1, angle_id)
        elif current_reuse + 1 > _REUSE_PENALTY_COUNT:
            penalty_factor = _REUSE_PENALTY_FACTOR
            
        con.execute("""
            UPDATE angles
            SET reuse_count = reuse_count + 1,
                angle_score = MIN(1.0, MAX(0.0, angle_score * ?)),
                updated_at = ?
            WHERE angle_id = ?
        """, (penalty_factor, now, angle_id))
        con.commit()
    finally:
        con.close()


# ── Part 6 — Force variation generation ───────────────────────────────────────

def generate_variation(angle: Angle) -> Angle:
    """
    Part 6: When reuse > 5, generate a variation with modified hook.
    Returns a new Angle with a fresh angle_id and reset reuse_count.
    """
    variation_suffixes = [
        "— a different perspective",
        "(what actually works)",
        "the real truth",
        "— inside story",
        "you've never heard this",
    ]
    suffix = variation_suffixes[angle.reuse_count % len(variation_suffixes)]
    
    # Part 6: Force mutation (new emotion, new format, new hook)
    new_emotion = random.choice([e for e in ["frustration", "aspiration", "curiosity", "surprise", "empathy"] if e != angle.target_emotion])
    new_format = random.choice([f for f in ["problem_solution", "transformation", "reveal", "myth_bust", "story"] if f != angle.content_format])

    new_hook = f"{angle.hook_idea} | {suffix}"
    new_aid  = _angle_id(angle.niche, angle.archetype, new_hook)
    return Angle(
        angle_id           = new_aid,
        niche              = angle.niche,
        archetype          = angle.archetype,
        hook_idea          = new_hook,
        target_emotion     = new_emotion,
        content_format     = new_format,
        novelty            = round(min(1.0, angle.novelty + 0.10), 3),   # forced novelty bump
        emotional_strength = angle.emotional_strength,
        clarity            = angle.clarity,
        virality_match     = angle.virality_match,
        cost_efficiency    = angle.cost_efficiency,
        is_validated       = False,   # new variation starts unvalidated
        reuse_count        = 0,
        needs_variation    = False,
    )


# ── Public convenience helpers ────────────────────────────────────────────────

def get_angle_bank(niche: str) -> list[Angle]:
    """
    Part 1: Return all stored angles for a niche (the angle_bank).
    If empty, generates and persists a fresh set.
    """
    try:
        con  = _db()
        rows = con.execute(
            "SELECT * FROM angles WHERE niche=? ORDER BY angle_score DESC", (niche,)
        ).fetchall()
        con.close()
        if rows:
            return [_row_to_angle(r) for r in rows]
    except Exception:
        pass

    angles  = generate_angles(niche, n=40)
    scored  = score_angles(angles)
    persist_angles(scored)
    return scored


def refresh_angle_bank(niche: str, n: int = 40) -> list[Angle]:
    """Generate a fresh batch, score it, persist, and return top 30%."""
    raw    = generate_angles(niche, n=n)
    scored = score_angles(raw)
    persist_angles(scored)
    return scored


# ── Part 5 — Pre-trend detection ──────────────────────────────────────────────

def compute_early_trend_score(
    view_velocity:    float,
    share_rate:       float,
    save_rate:        float,
    comment_velocity: float,
) -> float:
    """
    Part 5: Weighted early-trend signal.
    All inputs should be normalised to [0, 1] by the caller.
    """
    raw = (
        _W_VIEW_VELOCITY    * view_velocity
        + _W_SHARE_RATE       * share_rate
        + _W_SAVE_RATE        * save_rate
        + _W_COMMENT_VELOCITY * comment_velocity
    )
    return round(max(0.0, min(1.0, raw)), 4)


def record_early_signals(
    angle_id:         str,
    view_velocity:    float,
    share_rate:       float,
    save_rate:        float,
    comment_velocity: float,
    total_views:      float = 0.0,
) -> float:
    """
    Part 5: Persist early trend signals for an angle and return the
    computed early_trend_score.  Also updates the angles table so the
    score is available via get_best_angles / enrich_candidate.
    """
    score = compute_early_trend_score(
        view_velocity, share_rate, save_rate, comment_velocity
    )
    now = time.time()
    con = _db()
    try:
        con.execute("""
            INSERT INTO angle_early_signals
                (angle_id, view_velocity, share_rate, save_rate,
                 comment_velocity, early_trend_score, total_views, updated_at)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(angle_id) DO UPDATE SET
                view_velocity     = excluded.view_velocity,
                share_rate        = excluded.share_rate,
                save_rate         = excluded.save_rate,
                comment_velocity  = excluded.comment_velocity,
                early_trend_score = excluded.early_trend_score,
                total_views       = excluded.total_views,
                updated_at        = excluded.updated_at
        """, (angle_id, view_velocity, share_rate, save_rate,
              comment_velocity, score, total_views, now))
        # Sync to the angles table for ranking purposes
        con.execute(
            "UPDATE angles SET early_trend_score=?, updated_at=? WHERE angle_id=?",
            (score, now, angle_id),
        )
        con.commit()
        LOGGER.debug("early_signals angle_id=%s ets=%.4f views=%.0f",
                     angle_id, score, total_views)
    finally:
        con.close()
    return score


def get_early_signals(angle_id: str) -> dict[str, float]:
    """Return the latest early-signal snapshot for an angle (or empty dict)."""
    try:
        con = _db()
        row = con.execute(
            "SELECT * FROM angle_early_signals WHERE angle_id=?", (angle_id,)
        ).fetchone()
        con.close()
        if row:
            return {
                "view_velocity":     float(row["view_velocity"]),
                "share_rate":        float(row["share_rate"]),
                "save_rate":         float(row["save_rate"]),
                "comment_velocity":  float(row["comment_velocity"]),
                "early_trend_score": float(row["early_trend_score"]),
                "total_views":       float(row["total_views"]),
            }
    except Exception:
        pass
    return {}
