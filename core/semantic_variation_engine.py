"""
core/semantic_variation_engine.py — Performance-Aware Semantic Hook Variation Engine

Generates 3–5 structurally distinct, monetization-aligned hook variations.

Parts:
  1. Performance-aware angle scoring     — real CTR/CVR/EV data drives selection
  2. Anti-repetition memory             — per-account history, 20-item window
  3. Intent-CTA alignment               — angle must match funnel mode
  4. Revenue-aware hook boost           — high-revenue content → pain/contrarian lift
  5. Diversity enforcement              — batch must have ≥3 different angle types
  6. Cost-aware generation              — max_variants driven by expected_value
  7. Enriched output per variant        — angle_score, final_score, cta_alignment, etc.
  8. Distribution priority score        — 0.4*final + 0.3*angle + 0.3*(1-rep_penalty)

Public API:
    update_angle_performance(niche, intent, angle_type, ctr, cvr, ev)
    score_angle(niche, intent, angle_type) -> float
    record_hook(account_id, hook)
    is_repetitive(hook, account_id) -> bool
    generate_hook_variants(base_hook, niche, intent, seed, signals, monetization_mode,
                           expected_value) -> list[HookVariant]
    assign_variants_to_accounts(base_hook, niche, accounts, intent, seed,
                                signals, monetization_mode, expected_value) -> list[dict]
    similarity(text_a, text_b) -> float
"""
from __future__ import annotations

import hashlib
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

# ── Thresholds ────────────────────────────────────────────────────────────────

_MAX_SIMILARITY      = 0.70   # Jaccard gate between generated variants
_HISTORY_SIM_GATE    = 0.75   # gate vs account history
_HISTORY_WINDOW      = 20     # recent hooks per account to check
_MIN_VARIANTS        = 3
_MAX_VARIANTS        = 5
_MIN_ANGLE_DIVERSITY = 3      # Part 5: minimum distinct angle types per batch

# Part 4: Revenue boost
_REVENUE_BOOST_THRESHOLD   = 0.70
_REVENUE_BOOST_ANGLES      = {"pain", "contrarian"}
_REVENUE_BOOST_MULTIPLIER  = 1.15

# Part 6: Cost-aware caps
_EV_TIER_LOW   = 20.0   # expected_value < 20 → max 2
_EV_TIER_MID   = 50.0   # expected_value < 50 → max 3
                         # ≥ 50               → max 5

# ── Angle catalogue ───────────────────────────────────────────────────────────

_ANGLES: dict[str, dict[str, Any]] = {
    "pain": {
        "trigger":   "frustration",
        "structure": "problem_first",
        "templates": [
            "Stop ignoring the {topic} problem — it's costing you more than you think",
            "Why does {topic} keep failing you? Here's the real reason nobody says out loud",
            "The {topic} struggle is real — and it's not your fault. Here's what's broken",
            "If {topic} keeps draining you, this is the thing you've been missing",
            "Most people suffer through {topic} for years before they discover this",
        ],
    },
    "curiosity": {
        "trigger":   "intrigue",
        "structure": "open_question",
        "templates": [
            "What actually happens when you commit to {topic} for 90 days straight?",
            "The {topic} secret the top 1% quietly use — and never post about",
            "I tested every {topic} method out there. Only one actually worked",
            "Nobody's talking about this {topic} shift — but the results are wild",
            "There's a {topic} pattern hidden in plain sight. Here's what it is",
        ],
    },
    "contrarian": {
        "trigger":   "surprise",
        "structure": "myth_bust",
        "templates": [
            "Unpopular take: everything you've heard about {topic} is backwards",
            "I stopped following the standard {topic} advice — best decision I ever made",
            "The {topic} 'best practice' that's actually holding everyone back",
            "Conventional {topic} wisdom is designed for average results. Here's better",
            "Hot take: the {topic} 'rule' most people follow is completely made up",
        ],
    },
    "personal_story": {
        "trigger":   "empathy",
        "structure": "narrative_first",
        "templates": [
            "Six months ago I was completely lost with {topic}. Then this happened",
            "The {topic} moment that changed how I see everything — raw and honest",
            "I almost quit {topic} entirely. What brought me back was unexpected",
            "My {topic} journey looked nothing like the highlight reels online. Real talk",
            "Here's what a year of obsessing over {topic} actually taught me",
        ],
    },
    "list_tips": {
        "trigger":   "value",
        "structure": "numbered_value",
        "templates": [
            "5 {topic} things that took me years to learn — saving you the time right now",
            "3 {topic} moves that changed my results overnight (no fluff, just facts)",
            "The {topic} checklist nobody gave me when I started — use this",
            "Quick {topic} wins you can apply today: here's the shortlist",
            "7 things high performers do differently in {topic} — ranked by impact",
        ],
    },
}

_ANGLE_KEYS = ["pain", "curiosity", "contrarian", "personal_story", "list_tips"]

# ── Part 3: Intent → CTA alignment map ───────────────────────────────────────

ANGLE_TO_CTA: dict[str, str] = {
    "pain":           "direct",
    "contrarian":     "direct",
    "curiosity":      "indirect",
    "personal_story": "trust",
    "list_tips":      "indirect",
}
_CTA_MISMATCH_PENALTY = 0.80   # multiply final_score if misaligned

# ── Part 1: Angle performance memory ─────────────────────────────────────────
# (niche, intent, angle_type) → {ctr, cvr, ev, uses}

ANGLE_PERFORMANCE: dict[tuple[str, str, str], dict[str, float]] = {}

# Normalisation ceilings for score_angle
_NORM_CTR = 0.15
_NORM_CVR = 0.20
_NORM_EV  = 200.0


def _normalize(value: float, ceiling: float) -> float:
    if ceiling <= 0:
        return 0.0
    return min(1.0, max(0.0, value / ceiling))


def update_angle_performance(
    niche:      str,
    intent:     str,
    angle_type: str,
    ctr:        float,
    cvr:        float,
    ev:         float,
    alpha:      float = 0.20,
) -> None:
    """
    Part 1: EWMA-update angle performance memory.
    alpha=0.20 matches conversion_tracker pattern.
    """
    key = (niche, intent, angle_type)
    if key not in ANGLE_PERFORMANCE:
        ANGLE_PERFORMANCE[key] = {"ctr": ctr, "cvr": cvr, "ev": ev, "uses": 1}
    else:
        d = ANGLE_PERFORMANCE[key]
        d["ctr"]  = round((1 - alpha) * d["ctr"]  + alpha * ctr,  6)
        d["cvr"]  = round((1 - alpha) * d["cvr"]  + alpha * cvr,  6)
        d["ev"]   = round((1 - alpha) * d["ev"]   + alpha * ev,   4)
        d["uses"] += 1


def score_angle(niche: str, intent: str, angle_type: str) -> float:
    """
    Part 1: Revenue-centric angle score.
    Returns 0.6 (exploration bias) when no data.

    Formula:
        0.4 * normalize(ctr, 0.15)
      + 0.3 * normalize(cvr, 0.20)
      + 0.3 * normalize(ev,  200)
    """
    data = ANGLE_PERFORMANCE.get((niche, intent, angle_type))
    if not data:
        return 0.60   # exploration bias
    return round(
        0.4 * _normalize(data["ctr"], _NORM_CTR) +
        0.3 * _normalize(data["cvr"], _NORM_CVR) +
        0.3 * _normalize(data["ev"],  _NORM_EV),
        4
    )


# ── Part 2: Anti-repetition history ──────────────────────────────────────────
# account_id → list of {hook, timestamp}

HOOK_HISTORY: dict[str, list[dict[str, Any]]] = defaultdict(list)


def record_hook(account_id: str, hook: str) -> None:
    """Part 2: Save hook to account history after publish."""
    HOOK_HISTORY[account_id].append({"hook": hook, "timestamp": time.time()})
    # Trim to 2× window to avoid unbounded growth
    if len(HOOK_HISTORY[account_id]) > _HISTORY_WINDOW * 2:
        HOOK_HISTORY[account_id] = HOOK_HISTORY[account_id][-_HISTORY_WINDOW:]


def is_repetitive(hook: str, account_id: str) -> bool:
    """
    Part 2: Returns True if hook is too similar to any of the last 20 hooks
    for this account (Jaccard ≥ _HISTORY_SIM_GATE = 0.75).
    """
    history = HOOK_HISTORY.get(account_id, [])[-_HISTORY_WINDOW:]
    return any(similarity(hook, h["hook"]) >= _HISTORY_SIM_GATE for h in history)


def repetition_penalty(hook: str, account_id: str) -> float:
    """
    Part 2: Returns penalty multiplier.
    1.0 = no penalty; 0.6 = repetitive.
    """
    return 0.60 if is_repetitive(hook, account_id) else 1.0


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class HookVariant:
    angle_type:         str
    trigger:            str
    structure:          str
    hook:               str
    score:              float   # distinctiveness [0,1]
    # Part 7 enriched fields
    angle_score:        float = 0.60
    template_score:     float = 0.95
    final_score:        float = 0.0
    cta_alignment:      float = 1.0   # 1.0 = aligned, 0.8 = misaligned
    repetition_penalty: float = 1.0   # 1.0 = no penalty
    diversity_score:    float = 1.0
    priority:           float = 0.0   # Part 8 distribution priority


# ── Similarity engine (trigram Jaccard) ───────────────────────────────────────

def _trigrams(text: str) -> set[str]:
    clean = re.sub(r"[^a-z0-9 ]", "", text.lower())
    words = clean.split()
    wbigrams = {f"{words[i]} {words[i+1]}" for i in range(len(words)-1)}
    ctri     = {clean[i:i+3] for i in range(len(clean)-2)}
    return wbigrams | ctri


def similarity(text_a: str, text_b: str) -> float:
    """Jaccard on word-bigrams + char-trigrams. [0,1]"""
    if not text_a or not text_b:
        return 0.0
    a, b  = _trigrams(text_a), _trigrams(text_b)
    inter = len(a & b)
    union = len(a | b)
    return round(inter / union, 4) if union > 0 else 0.0


# ── Keyword extractor ─────────────────────────────────────────────────────────

_STOPWORDS = {
    "the","a","an","and","or","but","if","in","on","at","to","for",
    "of","with","this","that","is","are","was","were","be","been",
    "have","has","had","do","does","did","will","would","could","should",
    "i","you","we","they","he","she","it","my","your","their","our",
    "very","just","so","up","out","about","what","how","why","who",
}


def _extract_topic_keyword(base_hook: str, niche: str) -> str:
    combined  = f"{niche} {base_hook}".lower()
    words     = re.sub(r"[^a-z0-9 ]", " ", combined).split()
    keywords  = [w for w in words if w not in _STOPWORDS and len(w) > 3]
    niche_kws = [w for w in niche.lower().split() if w not in _STOPWORDS]
    ordered   = niche_kws + [w for w in keywords if w not in niche_kws]
    if len(ordered) >= 2:
        return " ".join(ordered[:2])
    return ordered[0] if ordered else niche


# ── Deterministic template selector ──────────────────────────────────────────

def _pick_template(angle_key: str, seed: str, exclude_indices: set[int]) -> str:
    templates = _ANGLES[angle_key]["templates"]
    h = int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16)
    for offset in range(len(templates)):
        idx = (h + offset) % len(templates)
        if idx not in exclude_indices:
            return templates[idx]
    return templates[h % len(templates)]


# ── Part 6: Cost-aware max_variants ──────────────────────────────────────────

def _cost_aware_max(expected_value: float) -> int:
    """Part 6: Cap number of variants by expected revenue value."""
    if expected_value < _EV_TIER_LOW:
        return 2
    if expected_value < _EV_TIER_MID:
        return 3
    return _MAX_VARIANTS


# ── Core generator ────────────────────────────────────────────────────────────

def generate_hook_variants(
    base_hook:         str,
    niche:             str,
    intent:            str = "",
    seed:              str = "",
    signals:           dict[str, Any] | None = None,
    monetization_mode: str = "",
    expected_value:    float = 100.0,
    account_id:        str = "",      # for repetition check
    n_min:             int = _MIN_VARIANTS,
) -> list[HookVariant]:
    """
    Generate semantically distinct, performance-aware, monetization-aligned
    hook variants.

    Parts applied per variant:
      1. angle_score from ANGLE_PERFORMANCE
      2. repetition_penalty vs account HOOK_HISTORY
      3. CTA alignment check vs ANGLE_TO_CTA
      4. Revenue boost for pain/contrarian when revenue_score > 0.70
      5. Diversity penalty (0.7x) for repeated angle types
      6. n_max capped by expected_value tier
      7. Enriched HookVariant fields
    """
    signals        = signals or {}
    revenue_score  = float(signals.get("revenue_score", 0.0))
    topic          = _extract_topic_keyword(base_hook, niche)
    _seed          = seed or f"{base_hook[:30]}:{niche}"

    # Part 6: cost-aware cap
    n_max = _cost_aware_max(expected_value)
    n_max = max(n_min, n_max)   # never go below n_min

    # Intent → angle order
    _INTENT_ORDER: dict[str, list[str]] = {
        "problem":   ["pain",      "contrarian",     "personal_story", "curiosity", "list_tips"],
        "desire":    ["list_tips", "personal_story",  "curiosity",      "contrarian","pain"],
        "curiosity": ["curiosity", "contrarian",      "pain",           "list_tips", "personal_story"],
    }
    angle_order = _INTENT_ORDER.get(intent, _ANGLE_KEYS)

    accepted:    list[HookVariant] = []
    used_angles: set[str]          = set()   # Part 5 diversity tracking

    for angle_key in angle_order:
        if len(accepted) >= n_max:
            break

        angle_meta = _ANGLES[angle_key]
        tseed      = f"{_seed}:{angle_key}:{len(accepted)}"
        raw        = _pick_template(angle_key, tseed, set())
        hook       = raw.replace("{topic}", topic)

        # ── Jaccard gate vs already accepted ─────────────────────────────────
        too_similar = any(similarity(hook, v.hook) >= _MAX_SIMILARITY for v in accepted)
        if too_similar:
            for extra in range(1, len(_ANGLES[angle_key]["templates"])):
                raw2  = _ANGLES[angle_key]["templates"][
                    (int(hashlib.sha256(tseed.encode()).hexdigest()[:8], 16) + extra)
                    % len(_ANGLES[angle_key]["templates"])
                ]
                hook2 = raw2.replace("{topic}", topic)
                if not any(similarity(hook2, v.hook) >= _MAX_SIMILARITY for v in accepted):
                    hook = hook2
                    too_similar = False
                    break
            if too_similar:
                continue

        # ── Part 1: angle_score ───────────────────────────────────────────────
        a_score = score_angle(niche, intent, angle_key)

        # ── Distinctiveness (template_score) ──────────────────────────────────
        if accepted:
            max_sim      = max(similarity(hook, v.hook) for v in accepted)
            t_score      = round(1.0 - max_sim, 4)
        else:
            t_score      = 0.95

        # ── Part 7: final_score base ──────────────────────────────────────────
        final = round(0.6 * t_score + 0.4 * a_score, 4)

        # ── Part 2: repetition penalty ────────────────────────────────────────
        rep_pen = repetition_penalty(hook, account_id) if account_id else 1.0
        final   = round(final * rep_pen, 4)

        # ── Part 3: CTA alignment ─────────────────────────────────────────────
        expected_cta = ANGLE_TO_CTA.get(angle_key, "")
        if monetization_mode and expected_cta and expected_cta != monetization_mode:
            cta_align = _CTA_MISMATCH_PENALTY
            final     = round(final * cta_align, 4)
        else:
            cta_align = 1.0

        # ── Part 4: revenue boost ─────────────────────────────────────────────
        if revenue_score > _REVENUE_BOOST_THRESHOLD and angle_key in _REVENUE_BOOST_ANGLES:
            final = round(min(1.0, final * _REVENUE_BOOST_MULTIPLIER), 4)

        # ── Part 5: diversity enforcement ────────────────────────────────────
        if angle_key in used_angles:
            div_score = 0.70
            final     = round(final * div_score, 4)
        else:
            div_score = 1.0
        used_angles.add(angle_key)

        # ── Part 8: distribution priority ────────────────────────────────────
        priority = round(
            0.4 * final +
            0.3 * a_score +
            0.3 * (1.0 - (1.0 - rep_pen)),   # 1 - rep_penalty (1.0=good, 0.6=bad)
            4
        )

        accepted.append(HookVariant(
            angle_type         = angle_key,
            trigger            = angle_meta["trigger"],
            structure          = angle_meta["structure"],
            hook               = hook,
            score              = t_score,
            angle_score        = a_score,
            template_score     = t_score,
            final_score        = final,
            cta_alignment      = cta_align,
            repetition_penalty = rep_pen,
            diversity_score    = div_score,
            priority           = priority,
        ))

    # ── Part 5: ensure ≥ _MIN_ANGLE_DIVERSITY angle types ─────────────────────
    if len({v.angle_type for v in accepted}) < min(_MIN_ANGLE_DIVERSITY, n_max):
        missing = [k for k in _ANGLE_KEYS if k not in {v.angle_type for v in accepted}]
        for fkey in missing:
            if len(accepted) >= n_max:
                break
            meta  = _ANGLES[fkey]
            hook  = meta["templates"][0].replace("{topic}", topic)
            a_sc  = score_angle(niche, intent, fkey)
            accepted.append(HookVariant(
                angle_type         = fkey,
                trigger            = meta["trigger"],
                structure          = meta["structure"],
                hook               = hook,
                score              = 0.50,
                angle_score        = a_sc,
                template_score     = 0.50,
                final_score        = round(0.6 * 0.50 + 0.4 * a_sc, 4),
                cta_alignment      = 1.0,
                repetition_penalty = 1.0,
                diversity_score    = 1.0,
                priority           = round(0.4 * (0.6*0.50+0.4*a_sc) + 0.3*a_sc + 0.3, 4),
            ))

    # Sort by Part 8 priority
    accepted.sort(key=lambda v: -v.priority)
    return accepted[:n_max]


# ── Account assignment ────────────────────────────────────────────────────────

def assign_variants_to_accounts(
    base_hook:         str,
    niche:             str,
    accounts:          list[dict[str, Any]],
    intent:            str = "",
    seed:              str = "",
    signals:           dict[str, Any] | None = None,
    monetization_mode: str = "",
    expected_value:    float = 100.0,
) -> list[dict[str, Any]]:
    """
    Generate variants and assign one per account deterministically.

    Each account gets:
      - Its own hook (angle-diverse across accounts)
      - Repetition-checked against that account's history
      - Full Part 7 metadata

    Returns:
        [{account_id, hook, angle_type, trigger, angle_score,
          final_score, cta_alignment, repetition_penalty,
          diversity_score, priority}, ...]
    """
    if not accounts:
        return []

    result: list[dict[str, Any]] = []

    for i, acct in enumerate(accounts):
        aid = str(acct.get("account_id", f"acct_{i}"))
        acct_seed = f"{seed}:{aid}:{i}"

        # Generate with per-account repetition context
        variants = generate_hook_variants(
            base_hook         = base_hook,
            niche             = niche,
            intent            = intent,
            seed              = acct_seed,
            signals           = signals,
            monetization_mode = monetization_mode,
            expected_value    = expected_value,
            account_id        = aid,
        )

        if not variants:
            continue

        # Deterministic selection offset per account
        h       = int(hashlib.sha256(f"{seed}:{aid}".encode()).hexdigest()[:4], 16)
        var_idx = (h + i) % len(variants)
        var     = variants[var_idx]

        result.append({
            "account_id":        aid,
            "hook":              var.hook,
            "angle_type":        var.angle_type,
            "trigger":           var.trigger,
            "structure":         var.structure,
            "angle_score":       var.angle_score,
            "final_score":       var.final_score,
            "cta_alignment":     var.cta_alignment,
            "repetition_penalty":var.repetition_penalty,
            "diversity_score":   var.diversity_score,
            "priority":          var.priority,
        })

    return result
