"""
core/mutation_engine.py — Content Mutation Engine

Transforms scraped/reused videos into high-novelty, non-detectable,
high-performance content through structural mutation, hook rewriting,
and visual anti-detect transformations.

Pipeline integration:
    raw_candidate → angle_engine → mutation_engine → execution_brain
"""

import hashlib
import random
import time
import os
import sqlite3
from pathlib import Path
from typing import Any

# ── Part 2 — Extract Core Signal ──────────────────────────────────────────────

def extract_core_signal(candidate: dict[str, Any]) -> dict[str, str]:
    """Extract semantic and emotional core from the raw candidate."""
    caption    = candidate.get("caption", "").lower()
    transcript = candidate.get("transcript", "").lower()
    hook_text  = candidate.get("hook_text", "").lower()
    niche      = candidate.get("niche", "general")
    
    text_corpus = f"{hook_text} {caption} {transcript}"
    
    # Heuristics for value_type
    value_type = "education"
    if "story" in text_corpus or "i remember" in text_corpus or "years ago" in text_corpus:
        value_type = "story"
    elif "wtf" in text_corpus or "crazy" in text_corpus or "insane" in text_corpus:
        value_type = "shock"
    elif "secret" in text_corpus or "what happens" in text_corpus or "why" in text_corpus:
        value_type = "curiosity"
        
    # Heuristics for emotional_trigger
    emotional_trigger = "neutral"
    if "hate" in text_corpus or "stop" in text_corpus or "worst" in text_corpus:
        emotional_trigger = "frustration"
    elif "love" in text_corpus or "beautiful" in text_corpus or "dream" in text_corpus:
        emotional_trigger = "desire"
    elif "fear" in text_corpus or "danger" in text_corpus or "warning" in text_corpus:
        emotional_trigger = "fear"
        
    return {
        "main_topic":        niche,
        "emotional_trigger": emotional_trigger,
        "value_type":        value_type,
        "target_audience":   f"{niche} enthusiasts",
        "key_moment":        "00:05",  # Assumed peak moment fallback
        "original_hook":     hook_text,
    }

# ── Part 3 — Hook Rewrite Engine ──────────────────────────────────────────────

def rewrite_hook(core_signal: dict[str, str]) -> list[str]:
    """Generate 3-5 structurally different hook variants."""
    topic   = core_signal.get("main_topic", "this")
    trigger = core_signal.get("emotional_trigger", "neutral")
    orig    = core_signal.get("original_hook", "")
    
    # Templates mapped to types
    variants = []
    
    # 1. Curiosity Gap
    variants.append(f"The real reason your {topic} strategy is failing.")
    # 2. Contrarian
    variants.append(f"Unpopular opinion: {topic} advice is mostly a scam.")
    # 3. Direct Benefit
    variants.append(f"How to finally master {topic} without the headache.")
    # 4. Negative Hook (Warning)
    variants.append(f"Stop doing {topic} like this immediately.")
    # 5. Story Hook
    variants.append(f"I tried every {topic} hack so you don't have to.")
    
    # Filter out anything too similar to the original (simple length/word check)
    orig_words = set(orig.split())
    filtered = []
    for v in variants:
        v_words = set(v.lower().split())
        overlap = len(orig_words.intersection(v_words))
        if overlap < max(2, len(v_words) // 2):  # Ensure NOT similar wording
            filtered.append(v)
            
    return filtered[:5] if filtered else variants[:3]

# ── Part 4 — Structure Mutation ───────────────────────────────────────────────

def mutate_structure(candidate: dict[str, Any]) -> dict[str, Any]:
    """Plan timeline and pacing transformations."""
    options = [
        "cut_intro_start_at_peak",
        "reorder_scenes",
        "add_loop_ending",
        "split_part1_part2",
        "inject_commentary"
    ]
    
    # Deterministic choice based on candidate ID to ensure consistency
    cid_hash = int(hashlib.md5(candidate.get("content_id", "0").encode()).hexdigest()[:8], 16)
    trans_type = options[cid_hash % len(options)]
    
    new_timeline = []
    if trans_type == "cut_intro_start_at_peak":
        new_timeline = [{"action": "trim", "start": "00:03", "end": "EOF"}]
    elif trans_type == "add_loop_ending":
        new_timeline = [{"action": "keep_all"}, {"action": "duplicate_first_1s_at_end"}]
    elif trans_type == "split_part1_part2":
        new_timeline = [{"action": "split_half", "keep": "part_1"}]
    else:
        new_timeline = [{"action": "shuffle_b_roll"}]
        
    return {
        "new_timeline": new_timeline,
        "transformation_type": trans_type
    }

# ── Part 5 — Visual Transformation (Anti-Detect) ──────────────────────────────

def plan_visual_transformation() -> list[str]:
    """Select at least 3 visual transformations to guarantee non-identical frames."""
    available = [
        "zoom_pattern_changes",
        "dynamic_crop",
        "speed_variation_non_uniform",
        "overlay_repositioning",
        "color_grade_shift",
        "mirror_flip"
    ]
    # Always apply at least 3
    selected = random.sample(available, 3)
    # Ensure constraint: NEVER keep first 3 seconds unchanged
    if "speed_variation_non_uniform" not in selected:
        selected.append("speed_variation_non_uniform")
    return list(set(selected))

# ── Part 6 — Comment-Driven Augmentation ──────────────────────────────────────

def extract_comment_signals(comments: list[str]) -> dict[str, str]:
    """Extract objections or emotional reactions from top comments to drive CTA/Hooks."""
    objection = "Wait, does this actually work?"
    reaction  = "This changed everything for me!"
    
    for c in comments:
        cl = c.lower()
        if "?" in cl or "but" in cl or "expensive" in cl:
            objection = c[:50]
        if "!" in cl or "omg" in cl or "love" in cl:
            reaction = c[:50]
            
    return {
        "objection": objection,
        "emotional_reaction": reaction,
        "generated_cta": f"Addressing the biggest question: {objection} 👇 Link in bio."
    }

# ── Part 7 — Novelty Scoring ──────────────────────────────────────────────────

def text_similarity(text1: str, text2: str) -> float:
    """Simple Jaccard similarity for text."""
    set1 = set(text1.lower().split())
    set2 = set(text2.lower().split())
    if not set1 or not set2:
        return 0.0
    return len(set1.intersection(set2)) / len(set1.union(set2))

def compute_similarity(original: dict[str, Any], mutated: dict[str, Any]) -> float:
    """Return similarity score (0-1)."""
    hook_sim = text_similarity(original.get("hook_text", ""), mutated.get("hook", ""))
    caption_sim = text_similarity(original.get("caption", ""), mutated.get("caption", ""))

    # structure similarity proxy
    struct_sim = 1.0 if mutated.get("transformation_type") == "keep_all" else 0.5

    return 0.5 * hook_sim + 0.3 * caption_sim + 0.2 * struct_sim

# ── Global Memory (Part 4 & 6) ────────────────────────────────────────────────

_DB_PATH = Path(os.environ.get("MUTATION_ENGINE_DB", "data/mutation_engine.db"))

def _get_db():
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('''
        CREATE TABLE IF NOT EXISTS pattern_performance (
            pattern_key TEXT PRIMARY KEY,
            avg_ev REAL,
            success_rate REAL,
            usage_count INTEGER,
            pattern_early_score REAL DEFAULT 0.0,
            avg_conversion_value REAL DEFAULT 0.0,
            avg_ctr REAL DEFAULT 0.0,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Migrate existing DBs that lack the new columns (safe no-op if already present)
    for col, typedef in [("avg_conversion_value", "REAL DEFAULT 0.0"),
                          ("avg_ctr",              "REAL DEFAULT 0.0")]:
        try:
            conn.execute(f"ALTER TABLE pattern_performance ADD COLUMN {col} {typedef}")
        except Exception:
            pass   # column already exists
    return conn

def get_pattern_key(value_type: str, emotional_trigger: str, transformation_type: str) -> str:
    return f"{value_type}_{emotional_trigger}_{transformation_type}"

def get_pattern_score(value_type: str, emotional_trigger: str, transformation_type: str) -> float:
    key = get_pattern_key(value_type, emotional_trigger, transformation_type)
    with _get_db() as conn:
        row = conn.execute("SELECT * FROM pattern_performance WHERE pattern_key = ?", (key,)).fetchone()
        if not row:
            return 0.5

        success_rate = float(row["success_rate"] or 0.0)
        avg_ev       = float(row["avg_ev"]       or 0.0)
        norm_ev      = max(0.0, min(1.0, avg_ev / 1000.0))

        # Conversion signals (default 0.0 when column absent / NULL)
        norm_cv  = max(0.0, min(1.0, float(row["avg_conversion_value"] or 0.0) / 100.0))
        avg_ctr  = max(0.0, min(1.0, float(row["avg_ctr"]              or 0.0)))

        score = (
            0.35 * success_rate +
            0.25 * norm_ev      +
            0.20 * norm_cv      +
            0.20 * avg_ctr
        )
        return max(0.0, min(1.0, round(score, 4)))

def update_pattern_memory(
    value_type: str,
    emotional_trigger: str,
    transformation_type: str,
    ev: float,
    success: bool,
    v_t1: float = 0.0,
    v_t2: float = 0.0,
    save_rate_trend: float = 0.0,
    share_rate_trend: float = 0.0,
    adoption_speed: float = 0.0,
    conversion_value: float = 0.0,
    click_through_rate: float = 0.0,
):
    key = get_pattern_key(value_type, emotional_trigger, transformation_type)

    # PART 1 FIX: compute new score first, then blend with existing to preserve trend memory
    new_score = compute_pattern_early_score(
        v_t1, v_t2, save_rate_trend, share_rate_trend, adoption_speed
    )

    with _get_db() as conn:
        row = conn.execute("SELECT * FROM pattern_performance WHERE pattern_key = ?", (key,)).fetchone()
        current_success = 1.0 if success else 0.0
        if not row:
            # First observation — use new score directly
            pattern_early_score = max(0.0, min(1.0, new_score))
            conn.execute('''
                INSERT INTO pattern_performance
                    (pattern_key, avg_ev, success_rate, usage_count, pattern_early_score,
                     avg_conversion_value, avg_ctr)
                VALUES (?, ?, ?, 1, ?, ?, ?)
            ''', (key, ev, current_success, pattern_early_score,
                  max(0.0, float(conversion_value)),
                  max(0.0, min(1.0, float(click_through_rate)))))
        else:
            # EWMA blend: preserve 70% of historical trend memory
            old_score = float(row["pattern_early_score"] or 0.0)
            pattern_early_score = max(0.0, min(1.0, 0.7 * old_score + 0.3 * new_score))
            usage_count = int(row["usage_count"]) + 1
            avg_ev      = 0.8 * float(row["avg_ev"])       + 0.2 * ev
            success_rate = 0.8 * float(row["success_rate"]) + 0.2 * current_success
            # EWMA for new conversion signals
            old_cv  = float(row["avg_conversion_value"] or 0.0)
            old_ctr = float(row["avg_ctr"]              or 0.0)
            new_cv  = max(0.0, float(conversion_value))
            new_ctr = max(0.0, min(1.0, float(click_through_rate)))
            avg_cv  = 0.8 * old_cv  + 0.2 * new_cv
            avg_ctr = 0.8 * old_ctr + 0.2 * new_ctr
            conn.execute('''
                UPDATE pattern_performance
                SET avg_ev = ?, success_rate = ?, usage_count = ?,
                    pattern_early_score = ?, avg_conversion_value = ?,
                    avg_ctr = ?, last_updated = CURRENT_TIMESTAMP
                WHERE pattern_key = ?
            ''', (avg_ev, success_rate, usage_count, pattern_early_score,
                  avg_cv, avg_ctr, key))

def compute_pattern_adoption_speed(pattern_key: str) -> float:
    """
    Measures how fast this pattern is appearing in new inputs.
    Compares appearances in the last 1h vs last 24h as a velocity ratio.
    Falls back safely when DB is empty or pattern is unknown.
    """
    import time as _time

    try:
        with _get_db() as conn:
            now = _time.time()
            one_hour_ago  = now - 3600
            one_day_ago   = now - 86400

            # Count pattern appearances per time window using last_updated
            row_recent = conn.execute(
                "SELECT COUNT(*) FROM pattern_performance "
                "WHERE pattern_key = ? AND last_updated >= datetime(?, 'unixepoch')",
                (pattern_key, one_hour_ago)
            ).fetchone()
            row_daily = conn.execute(
                "SELECT COUNT(*) FROM pattern_performance "
                "WHERE pattern_key = ? AND last_updated >= datetime(?, 'unixepoch')",
                (pattern_key, one_day_ago)
            ).fetchone()

            recent = int(row_recent[0]) if row_recent else 1
            daily  = int(row_daily[0])  if row_daily  else 5
    except Exception:
        recent = 1
        daily  = 5

    return min(1.0, recent / max(1, daily))


def compute_pattern_early_score(v_t1: float, v_t2: float, save_rate_trend: float, share_rate_trend: float, adoption_speed: float) -> float:
    acceleration = max(0.0, v_t1 - v_t2)
    n_acc = min(1.0, acceleration / 100.0)
    n_save = min(1.0, save_rate_trend / 0.1)
    n_share = min(1.0, share_rate_trend / 0.1)
    # PART 3: adoption_speed is active (0.20 weight)
    n_adopt = min(1.0, adoption_speed)

    score = (
        0.30 * n_acc +
        0.25 * n_save +
        0.25 * n_share +
        0.20 * n_adopt
    )
    return max(0.0, min(1.0, score))

def get_pattern_early_score(value_type: str, emotional_trigger: str, transformation_type: str) -> float:
    key = get_pattern_key(value_type, emotional_trigger, transformation_type)
    with _get_db() as conn:
        row = conn.execute("SELECT pattern_early_score FROM pattern_performance WHERE pattern_key = ?", (key,)).fetchone()
        if not row:
            return 0.0
        return float(row["pattern_early_score"])

# ── Part 9 — Cost Control ─────────────────────────────────────────────────────

def estimate_cost_and_ev(candidate: dict[str, Any], variant_type: str, visual_transforms: list[str]) -> tuple[float, float]:
    """Estimate compute/API cost vs Expected Value using angle signals."""
    base_views = float(candidate.get("view_count", 5000))
    eng_rate   = float(candidate.get("engagement_metrics", {}).get("engagement_rate", 0.05))
    
    angle_strength = float(candidate.get("angle_amplification_score", 0.5))
    early_trend    = float(candidate.get("early_trend_score", 0.0))
    
    platform_multiplier = 1.0
    platform = candidate.get("platform", "tiktok").lower()
    if platform == "reels":
        platform_multiplier = 1.1
    elif platform == "shorts":
        platform_multiplier = 0.9
        
    competition_penalty = float(candidate.get("competition_penalty", 0.2))
    
    expected_value = (
        base_views
        * eng_rate
        * angle_strength
        * platform_multiplier
        * (1.0 - competition_penalty)
    )
    
    if early_trend > 0.55:
        expected_value *= 1.15
        
    base_compute = 0.50
    transformation_complexity_cost = 0.15 if variant_type != "keep_all" else 0.05
    cost = base_compute + transformation_complexity_cost
    
    if variant_type == "inject_commentary":
        cost += 0.20
    if len(visual_transforms) > 3:
        cost += 0.10
        
    return cost, expected_value

# ── Part 8 & 10 — Multi-Variant Generation & Output ───────────────────────────

def process_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """
    Main entry point. Takes raw candidate and produces mutated versions.
    Rejects variants where EV < Cost or Novelty < 0.6.
    """
    core_signal = extract_core_signal(candidate)
    hooks       = rewrite_hook(core_signal)

    # PART 4: compute adoption_speed once per candidate before variant loop
    _pattern_key_base = get_pattern_key(
        core_signal["value_type"],
        core_signal["emotional_trigger"],
        "_",  # placeholder; per-variant key computed inside loop
    )
    _adoption_speed = compute_pattern_adoption_speed(_pattern_key_base)
    
    comments    = candidate.get("top_comments", [])
    comm_aug    = extract_comment_signals(comments)
    
    mutated_versions = []
    
    # Generate variants based on angle strength
    angle_strength = float(candidate.get("angle_amplification_score", 0.5))
    if angle_strength < 0.4:
        max_variants = 1
    elif angle_strength < 0.7:
        max_variants = 2
    else:
        max_variants = 3
        
    num_variants = min(max_variants, len(hooks))
    
    for i in range(num_variants):
        struct = mutate_structure(candidate)
        vis_tf = plan_visual_transformation()

        # PART 4: refine adoption_speed using actual transformation_type key
        _variant_pattern_key = get_pattern_key(
            core_signal["value_type"],
            core_signal["emotional_trigger"],
            struct["transformation_type"],
        )
        adoption_speed = compute_pattern_adoption_speed(_variant_pattern_key)
        
        # Multi-layer True Novelty (Part 1)
    
        structure_novelty = 1.0 if struct["transformation_type"] != "keep_all" else 0.3
        visual_novelty    = min(1.0, len(vis_tf) * 0.25)
        semantic_novelty  = 1.0 - text_similarity(core_signal["original_hook"], hooks[i])
        
        novelty_score = (
            0.4 * semantic_novelty +
            0.3 * structure_novelty +
            0.3 * visual_novelty
        )
        
        # Rule: Reject if novelty < 0.65
        if novelty_score < 0.65:
            continue
            
        novelty = novelty_score  # for downstream use
            
        cost, ev = estimate_cost_and_ev(candidate, struct["transformation_type"], vis_tf)
        
        # Rule: If expected_value < cost → do NOT generate
        if ev < cost:
            continue
            
        mutation_quality_score = (
            0.4 * novelty +
            0.3 * (ev / (cost + 1e-6)) +
            0.3 * angle_strength
        )
            
        version = {
            "hook":                   hooks[i],
            "caption":                comm_aug["generated_cta"],
            "structure_plan":         struct["new_timeline"],
            "visual_transforms":      vis_tf,
            "transformation_type":    struct["transformation_type"],
            "novelty_score":          round(novelty, 4),
            "expected_value":         round(ev, 2),
            "cost":                   round(cost, 2),
            "mutation_quality_score": round(mutation_quality_score, 4),
            "pattern_score":          round(get_pattern_score(core_signal["value_type"], core_signal["emotional_trigger"], struct["transformation_type"]), 4),
            "pattern_early_score":    round(get_pattern_early_score(core_signal["value_type"], core_signal["emotional_trigger"], struct["transformation_type"]), 4)
        }
        mutated_versions.append(version)

        # PART 4: persist pattern memory with adoption_speed signal
        update_pattern_memory(
            value_type          = core_signal["value_type"],
            emotional_trigger   = core_signal["emotional_trigger"],
            transformation_type = struct["transformation_type"],
            ev                  = ev,
            success             = ev > cost,
            v_t1                = float(candidate.get("view_velocity", 0.0)),
            v_t2                = float(candidate.get("avg_view_velocity", 0.0)),
            save_rate_trend     = float(candidate.get("save_rate", 0.0)),
            share_rate_trend    = float(candidate.get("share_rate", 0.0)),
            adoption_speed      = adoption_speed,
        )
        
    return {
        "original_content_id": candidate.get("content_id"),
        "mutated_versions": mutated_versions
    }

# ── Part 11 — Integration Stub ────────────────────────────────────────────────

def inject_into_pipeline(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Hooks into: input → angle_engine → mutation_engine → execution_brain.
    Takes scraped raw reups, runs them through mutation engine, and returns 
    the highly-novel variants for execution_brain to score.
    """
    mutated_candidates = []
    for cand in candidates:
        result = process_candidate(cand)
        for i, mv in enumerate(result.get("mutated_versions", [])):
            # Create a new candidate dictionary mapping to execution_brain requirements
            new_cand = cand.copy()
            new_cand["content_id"]    = f"{cand.get('content_id')}_mut_{i}"
            new_cand["mode"]          = "remark" # Upgraded from raw reup
            new_cand["hook_text"]     = mv["hook"]
            new_cand["caption"]       = mv["caption"]
            new_cand["mutation_novelty"]       = mv["novelty_score"]
            new_cand["mutation_cost"]          = mv["cost"]
            new_cand["mutation_ev"]            = mv["expected_value"]
            new_cand["mutation_quality_score"] = mv["mutation_quality_score"]
            new_cand["pattern_score"]          = mv.get("pattern_score", 0.5)
            new_cand["pattern_early_score"]    = mv.get("pattern_early_score", 0.0)
            new_cand["mutation_plan"] = {
                "structure":  mv["structure_plan"],
                "visual":     mv["visual_transforms"],
                "trans_type": mv["transformation_type"]
            }
            mutated_candidates.append(new_cand)
            
    return mutated_candidates
