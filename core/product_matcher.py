"""
core/product_matcher.py — Product Matching Engine

Connects trend/pattern/angle signals to the correct product to maximise
conversion.  All scoring is deterministic and numeric.  Safe defaults
prevent crashes when data is missing or the product pool is empty.

Public API:
    match_product(candidate, products)      → dict
    detect_intent(candidate)                → str
    compute_match_score(candidate, product) → float
    compute_conversion_proxy(product)       → float

Output contract (success):
    {
        "best_product":           dict,
        "product_match_score":    float,   # [0, 1]
        "top_products":           list[dict],
        "intent_type":            str,
        "conversion_proxy_score": float,   # [0, 1]
    }
Output contract (no products provided):
    {
        "best_product":           None,
        "product_match_score":    0.5,
        "top_products":           [],
        "intent_type":            str,
        "conversion_proxy_score": 0.5,
        "no_product_attached":    True,
    }
Output contract (reject — score < 0.55):
    {"reject": True, "reason": "low_product_match"}
"""
from __future__ import annotations

import math
from typing import Any

# ── Intent keyword maps ───────────────────────────────────────────────────────

_CURIOSITY_KEYWORDS: frozenset[str] = frozenset({
    "why", "secret", "what happens", "hidden", "truth", "real reason",
    "nobody knows", "shocking", "unbelievable",
})

_PROBLEM_KEYWORDS: frozenset[str] = frozenset({
    "how to", "fix", "stop", "avoid", "solve", "prevent", "repair",
    "troubleshoot", "mistake", "problem", "issue", "broken",
})

_DESIRE_KEYWORDS: frozenset[str] = frozenset({
    "best", "top", "must have", "upgrade", "glow up", "level up",
    "dream", "premium", "ultimate", "perfect", "amazing",
})

# ── Price bucket thresholds ───────────────────────────────────────────────────

_PRICE_LOW_MAX  = 25.0   # ≤ $25 → "low"
_PRICE_MID_MAX  = 75.0   # ≤ $75 → "mid"
                          # > $75 → "high"

# ── Normalisation ceiling for sold_count ─────────────────────────────────────

_SOLD_COUNT_CEIL = 50_000.0

# ── Match thresholds ─────────────────────────────────────────────────────────

_MATCH_THRESHOLD     = 0.60   # < 0.60 → reject (raised from 0.55)
_MATCH_STRONG        = 0.70   # ≥ 0.70 → strong (eligible for boost)
_HARD_KMS_THRESHOLD  = 0.30   # keyword_match_score gate (Part 1 scanner fix)
_HARD_CP_THRESHOLD   = 0.50   # conversion_proxy gate (Part 2 final safety)
_HARD_KMS_FINAL      = 0.40   # final safety keyword gate (Part 2)
_INTENT_MISMATCH_PEN = 0.20   # penalty for intent→product-type mismatch
_NICHE_MISMATCH_PEN  = 0.25   # penalty for niche mismatch

# ── Top-N to return ───────────────────────────────────────────────────────────

_TOP_N = 3


# ─────────────────────────────────────────────────────────────────────────────
# Part 3 — Intent Detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_intent(candidate: dict[str, Any]) -> str:
    """
    Classify content intent from hook_text + caption signals.

    Returns one of: "curiosity" | "problem" | "desire"
    Default: "curiosity"
    """
    hook    = str(candidate.get("hook_text", "")).lower()
    caption = str(candidate.get("caption",   "")).lower()
    text    = f"{hook} {caption}"

    # Problem check first — most actionable, highest purchase intent
    for kw in _PROBLEM_KEYWORDS:
        if kw in text:
            return "problem"

    # Desire check — aspirational / aesthetic content
    for kw in _DESIRE_KEYWORDS:
        if kw in text:
            return "desire"

    # Curiosity check
    for kw in _CURIOSITY_KEYWORDS:
        if kw in text:
            return "curiosity"

    return "curiosity"   # safe default


# ─────────────────────────────────────────────────────────────────────────────
# Part 4 — Product Feature Extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_product_features(product: dict[str, Any]) -> dict[str, Any]:
    """
    Normalise raw product dict into scoring-ready features.

    Returns:
        {
            "price_bucket":  "low" | "mid" | "high",
            "social_proof":  float [0, 1],
            "quality_score": float [0, 1],
            "tag_vector":    frozenset[str],
        }
    """
    price       = float(product.get("price",      0.0))
    rating      = float(product.get("rating",     0.0))
    sold_count  = float(product.get("sold_count", 0.0))
    tags: list  = list(product.get("tags",        []))
    title       = str(product.get("title",        "")).lower()

    # Price bucket
    if price <= _PRICE_LOW_MAX:
        price_bucket = "low"
    elif price <= _PRICE_MID_MAX:
        price_bucket = "mid"
    else:
        price_bucket = "high"

    # Social proof: sold_count normalised to [0, 1]
    social_proof = min(1.0, sold_count / max(1.0, _SOLD_COUNT_CEIL))

    # Quality score: rating / 5, clamped
    quality_score = max(0.0, min(1.0, rating / 5.0))

    # Tag vector: union of explicit tags + title unigrams
    title_tokens: set[str] = {
        w for w in title.split()
        if len(w) > 2   # ignore stop-word-length tokens
    }
    tag_vector: frozenset[str] = frozenset(
        {str(t).lower() for t in tags} | title_tokens
    )

    return {
        "price_bucket":  price_bucket,
        "social_proof":  round(social_proof,  4),
        "quality_score": round(quality_score, 4),
        "tag_vector":    tag_vector,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Part 5 — Match Scoring
# ─────────────────────────────────────────────────────────────────────────────

def _compute_intent_score(intent: str, product: dict[str, Any]) -> float:
    """
    Score how well the product fits the detected content intent.

    problem  → product should solve something (tags: fix, solution, repair …)
    desire   → aesthetic / trending (tags: premium, style, luxury, trending …)
    curiosity→ novelty / wow factor (tags: new, unique, innovative, rare …)
    """
    tags_lower: frozenset[str] = frozenset(
        str(t).lower() for t in product.get("tags", [])
    )
    title_lower = str(product.get("title", "")).lower()
    combined    = tags_lower | frozenset(title_lower.split())

    _PROBLEM_SIGNALS  = frozenset({"fix", "solution", "repair", "solve",
                                    "relief", "treatment", "cleanse", "cure"})
    _DESIRE_SIGNALS   = frozenset({"premium", "luxury", "style", "trending",
                                    "aesthetic", "glow", "upgrade", "elite"})
    _CURIOSITY_SIGNALS= frozenset({"new", "unique", "innovative", "rare",
                                    "discover", "secret", "special", "limited"})

    signal_map = {
        "problem":   _PROBLEM_SIGNALS,
        "desire":    _DESIRE_SIGNALS,
        "curiosity": _CURIOSITY_SIGNALS,
    }

    target_signals = signal_map.get(intent, _CURIOSITY_SIGNALS)
    matches        = len(combined & target_signals)
    # Normalise: up to 3 matches = full score; beyond = capped
    return min(1.0, matches / 3.0)


def _compute_niche_score(candidate: dict[str, Any],
                          features: dict[str, Any]) -> float:
    """
    Keyword overlap between candidate text corpus and product tag_vector.
    Uses Jaccard-like ratio normalised to [0, 1].
    """
    niche      = str(candidate.get("niche", "")).lower()
    hook       = str(candidate.get("hook_text", "")).lower()
    caption    = str(candidate.get("caption",   "")).lower()

    candidate_words: frozenset[str] = frozenset(
        w for w in f"{niche} {hook} {caption}".split()
        if len(w) > 2
    )

    product_tags: frozenset[str] = features["tag_vector"]

    if not candidate_words or not product_tags:
        return 0.5   # neutral when no data

    overlap = len(candidate_words & product_tags)
    # Normalise against candidate vocabulary size (prevents long texts dominating)
    return min(1.0, overlap / max(1, len(candidate_words) // 3))


def _compute_price_score(features: dict[str, Any]) -> float:
    """
    TikTok impulse-buy dynamics: low/mid price → higher score.
    high-price → lower score (longer consideration cycle).
    """
    bucket = features["price_bucket"]
    return {"low": 1.0, "mid": 0.75, "high": 0.35}.get(bucket, 0.5)


def compute_buyability(product: dict[str, Any]) -> float:
    """
    Estimate raw purchase likelihood independent of content signals.

    Components (Part 1 scanner hardening):
        0.4 × rating_norm
        0.3 × log_sold
        0.3 × price_affordability

    Returns [0, 1].  Used as a pre-filter gate (< 0.5 → drop).
    """
    rating     = float(product.get("rating",     0.0))
    sold_count = float(product.get("sold_count", 0.0))
    price      = float(product.get("price",      0.0))

    rating_norm = max(0.0, min(1.0, rating / 5.0))
    log_sold    = min(1.0, math.log10(sold_count + 1) / 5.0)

    if price < 15.0:
        affordability = 1.0
    elif price < 50.0:
        affordability = 0.8
    elif price < 150.0:
        affordability = 0.6
    else:
        affordability = 0.4

    return max(0.0, min(1.0, round(
        0.4 * rating_norm + 0.3 * log_sold + 0.3 * affordability, 4
    )))


_INTENT_PRODUCT_MAP: dict[str, frozenset[str]] = {
    "problem":   frozenset({"fix", "solution", "repair", "solve", "stop",
                             "cure", "relief", "remove", "treatment"}),
    "desire":    frozenset({"premium", "luxury", "glow", "upgrade", "elite",
                             "best", "aesthetic", "style", "trending"}),
    "curiosity": frozenset({"new", "unique", "innovative", "rare", "limited",
                             "viral", "wow", "discover", "gadget", "special"}),
}


def _intent_alignment_penalty(intent: str, product: dict[str, Any]) -> float:
    """
    Returns a penalty [0, _INTENT_MISMATCH_PEN] when the product's tag signals
    clearly belong to a DIFFERENT intent bucket than the content's intent.
    0.0 = aligned or ambiguous; _INTENT_MISMATCH_PEN = clear mismatch.
    """
    tags_lower = frozenset(str(t).lower() for t in product.get("tags", []))
    title_toks = frozenset(str(product.get("title", "")).lower().split())
    combined   = tags_lower | title_toks

    own_signals = _INTENT_PRODUCT_MAP.get(intent, frozenset())
    if own_signals & combined:
        return 0.0   # product aligns with intent — no penalty

    # Check if product clearly belongs to a competing intent
    for other_intent, signals in _INTENT_PRODUCT_MAP.items():
        if other_intent != intent and signals & combined:
            return _INTENT_MISMATCH_PEN

    return 0.0   # ambiguous → no penalty


# ─────────────────────────────────────────────────────────────────────────────
# Part 3 (new) — Conversion Proxy Score
# ─────────────────────────────────────────────────────────────────────────────

def compute_conversion_proxy(product: dict[str, Any]) -> float:
    """
    Estimate purchase likelihood from observable product signals.

    Components:
        0.4 × norm_rating  (rating / 5)
        0.3 × norm_sold    (log10(sold_count + 1) / 5, capped at 1.0)
        0.3 × price_score  (tiered: <15→1.0, <50→0.8, <150→0.6, else→0.4)

    Returns float [0, 1].
    """
    rating     = float(product.get("rating",     0.0))
    sold_count = float(product.get("sold_count", 0.0))
    price      = float(product.get("price",      0.0))

    norm_rating = max(0.0, min(1.0, rating / 5.0))
    norm_sold   = min(1.0, math.log10(sold_count + 1) / 5.0)

    if price < 15.0:
        cp_price = 1.0
    elif price < 50.0:
        cp_price = 0.8
    elif price < 150.0:
        cp_price = 0.6
    else:
        cp_price = 0.4

    score = (
        0.4 * norm_rating +
        0.3 * norm_sold   +
        0.3 * cp_price
    )
    return max(0.0, min(1.0, round(score, 4)))


def compute_match_score(candidate: dict[str, Any],
                         product:   dict[str, Any]) -> float:
    """
    Compute a single [0, 1] match score between a content candidate and
    a product.

    Weights (Part 4 updated formula):
        0.30 × intent_score
        0.25 × niche_score
        0.20 × conversion_proxy
        0.15 × social_proof
        0.10 × price_score
    """
    intent   = detect_intent(candidate)
    features = extract_product_features(product)

    intent_score      = _compute_intent_score(intent, product)
    niche_score       = _compute_niche_score(candidate, features)
    conversion_proxy  = compute_conversion_proxy(product)
    social_proof      = features["social_proof"]
    price_score       = _compute_price_score(features)

    score = (
        0.30 * intent_score     +
        0.25 * niche_score      +
        0.20 * conversion_proxy +
        0.15 * social_proof     +
        0.10 * price_score
    )
    return max(0.0, min(1.0, round(score, 4)))


# ─────────────────────────────────────────────────────────────────────────────
# Part 6 + 7 — Select Best Product + Hard Filter
# ─────────────────────────────────────────────────────────────────────────────

def match_product(candidate: dict[str, Any],
                  products:  list[dict[str, Any]]) -> dict[str, Any]:
    """
    Main entry point. Scores every product, applies hard gates, and returns
    the best match or an explicit reject dict.

    Hard gates (applied per-product before scoring):
        1. keyword_match_score < 0.30 → drop
        2. buyability            < 0.50 → drop
        3. intent alignment penalty   (−0.20 if mismatch)
        4. niche mismatch penalty     (−0.25 if candidate.niche ≠ product.niche)

    Final safety check (applied to best candidate after scoring):
        5. product_match_score   < 0.60 → reject
        6. conversion_proxy      < 0.50 → reject
        7. keyword_match_score   < 0.40 → reject

    Philosophy: "Content leads. Product follows."
    """
    intent           = detect_intent(candidate)
    candidate_niche  = str(candidate.get("niche", "")).lower()

    # ── Empty pool ─────────────────────────────────────────────────────────────
    if not products:
        return {
            "best_product":           None,
            "product_match_score":    0.5,
            "top_products":           [],
            "intent_type":            intent,
            "conversion_proxy_score": 0.5,
            "keyword_match_score":    0.0,
            "intent_alignment":       "no_product",
            "no_product_attached":    True,
        }

    # ── Score each product with hard gates ────────────────────────────────────
    scored: list[tuple[float, dict[str, Any], str]] = []  # (score, product, drop_reason)

    for product in products:
        try:
            kms = float(product.get("keyword_match_score", 0.0))

            # Gate 1: keyword relevance
            if kms < _HARD_KMS_THRESHOLD:
                continue

            # Gate 2: buyability
            if compute_buyability(product) < 0.50:
                continue

            raw_score = compute_match_score(candidate, product)

            # Gate 3: intent alignment penalty
            raw_score = max(0.0, raw_score - _intent_alignment_penalty(intent, product))

            # Gate 4: niche mismatch penalty
            product_niche = str(product.get("niche", "")).lower()
            if candidate_niche and product_niche and candidate_niche != product_niche:
                raw_score = max(0.0, raw_score - _NICHE_MISMATCH_PEN)

            scored.append((round(raw_score, 4), product))
        except Exception:
            continue

    if not scored:
        return {
            "reject":         True,
            "reason":         "no_qualifying_products",
            "intent_type":    intent,
            "reject_reason":  "all_products_failed_hard_gates",
        }

    # Sort descending
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_product = scored[0]

    # ── Compute explainability fields ─────────────────────────────────────────
    try:
        best_cp  = compute_conversion_proxy(best_product)
    except Exception:
        best_cp  = 0.5

    best_kms = float(best_product.get("keyword_match_score", 0.0))
    align    = "aligned" if _intent_alignment_penalty(intent, best_product) == 0.0 else "misaligned"

    # ── Final safety checks ───────────────────────────────────────────────────
    # Soft match tier: 0.45 ≤ score < 0.60 with adequate keyword + proxy signals
    _SOFT_MATCH_MIN   = 0.45
    _SOFT_KMS_MIN     = 0.30
    _SOFT_CP_MIN      = 0.50

    if best_score < _MATCH_THRESHOLD:
        if (best_score >= _SOFT_MATCH_MIN
                and best_kms  >= _SOFT_KMS_MIN
                and best_cp   >= _SOFT_CP_MIN):
            # Near-miss → indirect monetization, DO NOT attach product directly
            return {
                "soft_match":          True,
                "product_match_score": best_score,
                "recommended_product": best_product,
                "monetization_mode":   "indirect",
                "conversion_proxy_score": round(best_cp, 4),
                "keyword_match_score": best_kms,
                "intent_alignment":    align,
                "intent_type":         intent,
                "reject_reason":       None,
            }
        return {
            "reject":                 True,
            "reason":                 "low_product_match",
            "product_match_score":    best_score,
            "conversion_proxy_score": best_cp,
            "keyword_match_score":    best_kms,
            "intent_alignment":       align,
            "intent_type":            intent,
            "reject_reason":          "score_below_threshold_0.60",
        }

    if best_cp < _HARD_CP_THRESHOLD:
        return {
            "reject":                 True,
            "reason":                 "low_conversion_proxy",
            "product_match_score":    best_score,
            "conversion_proxy_score": best_cp,
            "keyword_match_score":    best_kms,
            "intent_alignment":       align,
            "intent_type":            intent,
            "reject_reason":          "conversion_proxy_below_0.50",
        }

    if best_kms < _HARD_KMS_FINAL:
        return {
            "reject":                 True,
            "reason":                 "low_keyword_match",
            "product_match_score":    best_score,
            "conversion_proxy_score": best_cp,
            "keyword_match_score":    best_kms,
            "intent_alignment":       align,
            "intent_type":            intent,
            "reject_reason":          "keyword_match_below_0.40",
        }

    top_products = [
        {"product": p, "score": round(s, 4)}
        for s, p in scored[:_TOP_N]
    ]

    return {
        "best_product":           best_product,
        "product_match_score":    best_score,
        "top_products":           top_products,
        "intent_type":            intent,
        "conversion_proxy_score": round(best_cp, 4),
        "keyword_match_score":    best_kms,
        "intent_alignment":       align,
        "reject_reason":          None,
    }
