"""
core/product_scanner.py — Intent-Aware Product Scanner

Fetches products TARGETED to each content candidate's intent + keywords.
Mock-safe: swap _fetch_from_source() for real Shopee/TikTok Shop API later.

Public API:
    get_product_pool_by_candidate(candidate, limit=50) -> list[dict]  ← primary
    get_product_pool(limit=50)                         -> list[dict]  ← backward compat
    extract_product_keywords(candidate)                -> list[str]
    fetch_products_by_keywords(keywords, limit=50)     -> list[dict]
    normalize_products(list)                           -> list[dict]
    filter_products(list)                              -> list[dict]
    score_product(product, keywords=None)              -> float
    tag_product_niche(product)                         -> dict
"""
from __future__ import annotations

import hashlib
import math
import os
import time
from typing import Any

# ── Constants ─────────────────────────────────────────────────────────────────

_FILTER_MIN_RATING   = 4.2
_FILTER_MIN_SOLD     = 500
_FILTER_MIN_PRICE    = 5.0
_FILTER_MAX_PRICE    = 150.0
_SCORE_SOLD_LOG_CEIL = 5.0
_MAX_POOL_SIZE       = 50
_FALLBACK_MIN_SCORE  = 0.5     # Part 5: return [] if top score < this
_CACHE_TTL_S         = 3600    # Part 7: 1-hour TTL per keyword set

# ── In-process cache: {cache_key: (expires_ts, results)} ─────────────────────
_POOL_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}

# ── Semantic expansion map ────────────────────────────────────────────────────

_SEMANTIC_EXPANSIONS: dict[str, list[str]] = {
    "back pain":    ["back pain", "posture", "spine support", "ergonomic"],
    "acne":         ["acne", "pimple", "skin clear", "salicylic"],
    "weight loss":  ["weight loss", "fat burn", "slimming", "metabolism"],
    "hair loss":    ["hair loss", "hair growth", "scalp", "biotin"],
    "sleep":        ["sleep", "insomnia", "melatonin", "relaxation"],
    "muscle":       ["muscle", "recovery", "protein", "bcaa"],
    "anxiety":      ["anxiety", "stress", "calm", "relaxation"],
    "energy":       ["energy", "caffeine", "vitamin b", "supplement"],
    "skin glow":    ["skin glow", "brightening", "vitamin c", "serum"],
    "pet care":     ["pet care", "dog", "cat", "grooming"],
}

_INTENT_KEYWORDS: dict[str, list[str]] = {
    "problem":   ["fix", "solution", "relief", "how to", "tool", "repair"],
    "desire":    ["upgrade", "premium", "best", "top", "must have"],
    "curiosity": ["weird", "viral", "trending", "secret", "discover"],
}

# Intent → product tag signals used for intent_match scoring (Problem 3)
_INTENT_PRODUCT_SIGNALS: dict[str, list[str]] = {
    "problem":   ["fix", "solution", "relief", "repair", "solve", "treatment",
                  "remove", "stop", "cure", "how to"],
    "desire":    ["upgrade", "premium", "best", "luxury", "glow", "aesthetic",
                  "style", "trending", "elite", "top"],
    "curiosity": ["viral", "weird", "trending", "unique", "new", "discover",
                  "gadget", "wow", "special", "limited"],
}

_NICHE_KEYWORDS: dict[str, list[str]] = {
    "beauty":  ["beauty", "skin", "skincare", "serum", "moisturizer",
                "lipstick", "makeup", "cosmetic", "glow", "facial"],
    "fitness": ["gym", "fitness", "workout", "protein", "supplement",
                "exercise", "training", "yoga", "dumbbell", "resistance"],
    "pet":     ["pet", "dog", "cat", "puppy", "kitten", "leash", "collar"],
    "tech":    ["phone", "cable", "charger", "earphone", "bluetooth",
                "laptop", "keyboard", "gadget", "usb"],
    "home":    ["home", "kitchen", "organizer", "storage", "cleaning",
                "decor", "lamp"],
    "fashion": ["shirt", "dress", "shoes", "bag", "wallet", "watch",
                "necklace", "sunglasses", "hoodie"],
    "food":    ["snack", "coffee", "tea", "supplement", "vitamin",
                "collagen", "organic", "herbal", "drink"],
}

# ── Mock catalogue ────────────────────────────────────────────────────────────

_MOCK_CATALOGUE: list[dict[str, Any]] = [
    {"product_id": "SP001", "title": "Vitamin C Brightening Serum",       "category": "beauty",  "tags": ["skin", "serum", "glow", "beauty", "brightening", "vitamin c"]},
    {"product_id": "SP002", "title": "Hyaluronic Acid Moisturizer",        "category": "beauty",  "tags": ["moisturizer", "skincare", "hydration", "skin"]},
    {"product_id": "SP003", "title": "Retinol Anti-Aging Cream",           "category": "beauty",  "tags": ["skin", "anti-aging", "beauty", "cream", "repair"]},
    {"product_id": "SP004", "title": "Rose Water Toner Spray",             "category": "beauty",  "tags": ["toner", "skin", "facial", "beauty"]},
    {"product_id": "SP005", "title": "Collagen Face Mask Pack 10pcs",      "category": "beauty",  "tags": ["collagen", "mask", "skin", "beauty"]},
    {"product_id": "SP006", "title": "Sunscreen SPF50 PA++++",             "category": "beauty",  "tags": ["sunscreen", "skin", "beauty", "glow", "protection"]},
    {"product_id": "SP007", "title": "Lip Gloss Set 6 Colors",             "category": "beauty",  "tags": ["lipstick", "makeup", "beauty", "cosmetic"]},
    {"product_id": "SP008", "title": "Acne Spot Treatment Gel",            "category": "beauty",  "tags": ["acne", "pimple", "skin clear", "salicylic", "solution"]},
    {"product_id": "SP009", "title": "Whey Protein Chocolate 1kg",         "category": "fitness", "tags": ["protein", "supplement", "gym", "fitness", "muscle"]},
    {"product_id": "SP010", "title": "Resistance Bands Set 5pcs",          "category": "fitness", "tags": ["resistance", "exercise", "fitness", "gym", "tool"]},
    {"product_id": "SP011", "title": "Adjustable Dumbbell 20kg",           "category": "fitness", "tags": ["dumbbell", "gym", "fitness", "training"]},
    {"product_id": "SP012", "title": "Yoga Mat Non-Slip 6mm",              "category": "fitness", "tags": ["yoga", "exercise", "fitness", "workout"]},
    {"product_id": "SP013", "title": "Jump Rope Speed Cable",              "category": "fitness", "tags": ["workout", "fitness", "training", "tool"]},
    {"product_id": "SP014", "title": "BCAAs Recovery Powder 300g",         "category": "fitness", "tags": ["supplement", "protein", "gym", "fitness", "recovery", "muscle"]},
    {"product_id": "SP015", "title": "Ab Wheel Roller Core Trainer",       "category": "fitness", "tags": ["gym", "fitness", "exercise", "training", "tool"]},
    {"product_id": "SP016", "title": "Posture Corrector Belt",             "category": "fitness", "tags": ["posture", "back pain", "spine support", "ergonomic", "fix", "relief", "solution"]},
    {"product_id": "SP017", "title": "Interactive Cat Feather Toy",        "category": "pet",     "tags": ["cat", "pet", "toy"]},
    {"product_id": "SP018", "title": "Dog Dental Chew Treats 30pcs",       "category": "pet",     "tags": ["dog", "pet", "treat"]},
    {"product_id": "SP019", "title": "Self-Cleaning Pet Slicker Brush",    "category": "pet",     "tags": ["dog", "cat", "pet", "grooming", "pet care"]},
    {"product_id": "SP020", "title": "Automatic Pet Water Fountain 2L",    "category": "pet",     "tags": ["dog", "cat", "pet", "water"]},
    {"product_id": "SP021", "title": "Adjustable Dog Harness No-Pull",     "category": "pet",     "tags": ["dog", "pet", "leash", "collar"]},
    {"product_id": "SP022", "title": "Cat Litter Clumping Bentonite 5kg",  "category": "pet",     "tags": ["cat", "pet"]},
    {"product_id": "SP023", "title": "USB-C Fast Charge Cable 1m",         "category": "tech",    "tags": ["cable", "usb", "gadget", "phone", "upgrade"]},
    {"product_id": "SP024", "title": "Wireless Bluetooth Earbuds TWS",     "category": "tech",    "tags": ["earphone", "bluetooth", "gadget", "tech", "upgrade"]},
    {"product_id": "SP025", "title": "Phone Ring Stand Holder",            "category": "tech",    "tags": ["phone", "gadget", "tech", "tool"]},
    {"product_id": "SP026", "title": "20000mAh Power Bank Slim",           "category": "tech",    "tags": ["charger", "phone", "gadget", "usb", "upgrade"]},
    {"product_id": "SP027", "title": "Mechanical Keyboard TKL RGB",        "category": "tech",    "tags": ["keyboard", "laptop", "tech", "gadget", "upgrade"]},
    {"product_id": "SP028", "title": "Webcam 1080p Auto-Focus",            "category": "tech",    "tags": ["laptop", "tech", "gadget"]},
    {"product_id": "SP029", "title": "Laptop Cooling Pad 6-Fan",           "category": "tech",    "tags": ["laptop", "tech", "gadget", "fix"]},
    {"product_id": "SP030", "title": "Screen Cleaner Microfiber Kit",      "category": "tech",    "tags": ["gadget", "phone", "laptop", "tech"]},
    {"product_id": "SP031", "title": "Bamboo Kitchen Organizer Rack",      "category": "home",    "tags": ["kitchen", "organizer", "home"]},
    {"product_id": "SP032", "title": "Vacuum Storage Bags 8pcs",           "category": "home",    "tags": ["storage", "home", "organizer"]},
    {"product_id": "SP033", "title": "LED Strip Lights 5m RGB",            "category": "home",    "tags": ["lamp", "decor", "home", "upgrade"]},
    {"product_id": "SP034", "title": "Non-Stick Ceramic Frying Pan 28cm",  "category": "home",    "tags": ["kitchen", "home", "cooking"]},
    {"product_id": "SP035", "title": "Silicone Cleaning Brush Set",        "category": "home",    "tags": ["cleaning", "home", "kitchen", "tool"]},
    {"product_id": "SP036", "title": "Wall-Mount Key Holder 6 Hooks",      "category": "home",    "tags": ["home", "organizer", "decor"]},
    {"product_id": "SP037", "title": "Canvas Tote Bag Large",              "category": "fashion", "tags": ["bag", "fashion", "upgrade"]},
    {"product_id": "SP038", "title": "Minimalist Leather Wallet Slim",     "category": "fashion", "tags": ["wallet", "fashion", "upgrade"]},
    {"product_id": "SP039", "title": "Polarized Sunglasses UV400",         "category": "fashion", "tags": ["sunglasses", "fashion"]},
    {"product_id": "SP040", "title": "Stainless Steel Watch Classic",      "category": "fashion", "tags": ["watch", "fashion", "premium", "upgrade"]},
    {"product_id": "SP041", "title": "Silver Chain Necklace 45cm",         "category": "fashion", "tags": ["necklace", "fashion"]},
    {"product_id": "SP042", "title": "Oversized Graphic Hoodie",           "category": "fashion", "tags": ["hoodie", "shirt", "fashion"]},
    {"product_id": "SP043", "title": "Matcha Green Tea Powder 100g",       "category": "food",    "tags": ["tea", "organic", "food", "drink", "energy"]},
    {"product_id": "SP044", "title": "MCT Oil Powder Keto-Friendly 300g",  "category": "food",    "tags": ["supplement", "food", "organic", "energy", "fat burn"]},
    {"product_id": "SP045", "title": "Mixed Nuts Snack Pack 200g",         "category": "food",    "tags": ["snack", "organic", "food"]},
    {"product_id": "SP046", "title": "Marine Collagen Peptides 250g",      "category": "food",    "tags": ["collagen", "supplement", "food", "vitamin", "skin glow"]},
    {"product_id": "SP047", "title": "Vitamin D3+K2 Softgels 120 caps",   "category": "food",    "tags": ["vitamin", "supplement", "food", "immunity"]},
    {"product_id": "SP048", "title": "Cold Brew Coffee Bags 10pcs",        "category": "food",    "tags": ["coffee", "drink", "food", "energy"]},
    {"product_id": "SP049", "title": "Hair Growth Serum Biotin",           "category": "beauty",  "tags": ["hair loss", "hair growth", "scalp", "biotin", "solution", "fix"]},
    {"product_id": "SP050", "title": "Melatonin Sleep Aid Gummies",        "category": "food",    "tags": ["sleep", "melatonin", "relaxation", "insomnia", "solution"]},
]

# ── Seed helpers (deterministic, no random) ───────────────────────────────────

def _seed_float(pid: str, slot: int, lo: float, hi: float) -> float:
    h = hashlib.sha256(f"ps:{pid}:{slot}".encode()).hexdigest()
    return round(lo + (int(h[:8], 16) / 0xFFFFFFFF) * (hi - lo), 2)

def _seed_int(pid: str, slot: int, lo: int, hi: int) -> int:
    h = hashlib.sha256(f"ps:{pid}:{slot}".encode()).hexdigest()
    return lo + int((int(h[:8], 16) / 0xFFFFFFFF) * (hi - lo + 1))


# ─────────────────────────────────────────────────────────────────────────────
# Part 1 — Keyword Extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_product_keywords(candidate: dict[str, Any]) -> list[str]:
    """
    Extract 3–6 intent-aware keywords from candidate signals.
    Deterministic: same candidate always produces same keywords.
    """
    from core.product_matcher import detect_intent  # local import — no circular dep

    hook    = str(candidate.get("hook_text", "")).lower()
    caption = str(candidate.get("caption",   "")).lower()
    niche   = str(candidate.get("niche",     "")).lower()
    text    = f"{hook} {caption}"

    keywords: list[str] = []

    # 1. Add niche as base signal
    if niche and niche != "general":
        keywords.append(niche)

    # 2. Intent-driven keywords (Problem 3: full intent phrase expansion)
    intent = detect_intent(candidate)
    intent_phrases = _INTENT_KEYWORDS.get(intent, [])  # use full list, not just [:2]
    keywords.extend(intent_phrases)

    # 3. Semantic expansion — check text against known phrases
    for phrase, expansions in _SEMANTIC_EXPANSIONS.items():
        if phrase in text:
            keywords.extend(expansions[:3])
            break

    # 4. Extract meaningful words from hook (len > 3, skip stopwords)
    _STOPWORDS = {"this", "that", "with", "your", "from", "have", "will",
                  "just", "like", "they", "when", "than", "then", "them"}
    for word in hook.split():
        w = word.strip(".,!?\"'")
        if len(w) > 3 and w not in _STOPWORDS:
            keywords.append(w)

    # 5. Clean: lowercase, deduplicate, max 8 (raised from 6 for intent coverage)
    seen: set[str] = set()
    clean: list[str] = []
    for kw in keywords:
        kw = kw.lower().strip()
        if kw and kw not in seen:
            seen.add(kw)
            clean.append(kw)
        if len(clean) >= 8:
            break

    return clean if clean else [niche or "general"]


# ─────────────────────────────────────────────────────────────────────────────
# Part 2 — Fetch by Keywords
# ─────────────────────────────────────────────────────────────────────────────

def _compute_keyword_match_score(product: dict[str, Any],
                                  keywords: list[str]) -> float:
    """Fraction of keywords found in product title + tags. Returns [0, 1]."""
    if not keywords:
        return 0.0
    title_lower = str(product.get("title", "")).lower()
    tag_set     = {str(t).lower() for t in product.get("tags", [])}
    product_text = title_lower + " " + " ".join(tag_set)

    matches = sum(1 for kw in keywords if kw in product_text)
    return round(matches / len(keywords), 4)


def _fetch_from_source(limit: int) -> list[dict[str, Any]]:
    """Pluggable source — swap body for real API when ready."""
    backend = os.environ.get("PRODUCT_SCANNER_BACKEND", "mock").lower()
    if backend != "mock":
        return []  # future: call real API

    raw: list[dict[str, Any]] = []
    for tpl in _MOCK_CATALOGUE[:limit]:
        pid = tpl["product_id"]
        raw.append({
            "product_id": pid,
            "title":      tpl["title"],
            "category":   tpl["category"],
            "price":      _seed_float(pid, 1, 5.0,    200.0),
            "rating":     _seed_float(pid, 2, 3.5,    5.0),
            "sold_count": _seed_int(  pid, 3, 10,     100_000),
            "tags":       list(tpl["tags"]),
        })
    return raw


def fetch_products_by_keywords(keywords: list[str],
                                limit: int = 50) -> list[dict[str, Any]]:
    """
    Fetch products that match at least 1 keyword.
    Scores keyword overlap; returns sorted results.
    Pluggable: replace _fetch_from_source for real API.
    """
    try:
        all_raw = _fetch_from_source(len(_MOCK_CATALOGUE))
        matched: list[dict[str, Any]] = []
        for p in all_raw:
            kms = _compute_keyword_match_score(p, keywords)
            if kms > 0.0:
                p["keyword_match_score"] = kms
                matched.append(p)
        # Sort by keyword match DESC for early truncation
        matched.sort(key=lambda x: -x.get("keyword_match_score", 0))
        return matched[:limit]
    except Exception:
        return []


def fetch_products(limit: int = 200) -> list[dict[str, Any]]:
    """Backward-compat: fetch all products with no keyword filter."""
    try:
        return _fetch_from_source(limit)
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Normalize / Filter / Tag  (unchanged signatures)
# ─────────────────────────────────────────────────────────────────────────────

def normalize_products(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in products:
        try:
            norm = {
                "product_id":          str(p.get("product_id", "")),
                "title":               str(p.get("title", "Unknown Product")),
                "category":            str(p.get("category", "general")),
                "price":               max(0.0, float(p.get("price", 0.0))),
                "rating":              max(0.0, min(5.0, float(p.get("rating", 0.0)))),
                "sold_count":          max(0, int(p.get("sold_count", 0))),
                "tags":                [str(t) for t in p.get("tags", []) if t],
                "niche":               str(p.get("niche", "general")),
                "keyword_match_score": float(p.get("keyword_match_score", 0.0)),
            }
            if norm["product_id"]:
                out.append(norm)
        except Exception:
            continue
    return out


def filter_products(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        p for p in products
        if (p["rating"]     >= _FILTER_MIN_RATING
            and p["sold_count"] >= _FILTER_MIN_SOLD
            and _FILTER_MIN_PRICE <= p["price"] <= _FILTER_MAX_PRICE)
    ]


def tag_product_niche(product: dict[str, Any]) -> dict[str, Any]:
    text = (str(product.get("title", "")).lower() + " "
            + " ".join(str(t).lower() for t in product.get("tags", [])))
    for niche, kws in _NICHE_KEYWORDS.items():
        if any(kw in text for kw in kws):
            product["niche"] = niche
            return product
    product["niche"] = "general"
    return product


# ─────────────────────────────────────────────────────────────────────────────
# Part 3 — Updated Scoring (keyword-aware)
# ─────────────────────────────────────────────────────────────────────────────

def _compute_intent_match_score(product: dict[str, Any], intent: str) -> float:
    """
    Overlap between product tags/title and intent-specific signal list.
    Returns [0, 1].  0.0 if intent unknown.
    """
    signals = _INTENT_PRODUCT_SIGNALS.get(intent, [])
    if not signals:
        return 0.0
    title_lower = str(product.get("title", "")).lower()
    tag_set     = {str(t).lower() for t in product.get("tags", [])}
    product_text = title_lower + " " + " ".join(tag_set)
    matches = sum(1 for s in signals if s in product_text)
    return round(min(1.0, matches / max(1, len(signals) // 2)), 4)


def score_product(product: dict[str, Any],
                  keywords: list[str] | None = None,
                  intent: str | None = None) -> float:
    """
    score = 0.25 * rating_score
          + 0.20 * log_sold_score
          + 0.20 * keyword_match_score
          + 0.15 * intent_match_score
          + 0.10 * price_score
          + 0.10 * review_signal

    Returns None if intent_match_score < 0.20 (hard reject gate).
    Callers must handle None.
    """
    rating     = float(product.get("rating",     0.0))
    sold_count = float(product.get("sold_count", 0.0))
    price      = float(product.get("price",      0.0))

    rating_score   = max(0.0, min(1.0, rating / 5.0))
    log_sold_score = min(1.0, math.log10(sold_count + 1) / _SCORE_SOLD_LOG_CEIL)

    kms = (product.get("keyword_match_score")
           or (_compute_keyword_match_score(product, keywords) if keywords else 0.0))
    keyword_match_score = float(kms)

    if price < 15.0:
        price_score = 1.0
    elif price < 50.0:
        price_score = 0.8
    elif price < 150.0:
        price_score = 0.6
    else:
        price_score = 0.4

    review_signal      = float(product.get("review_signal", 0.5))
    intent_match_score = _compute_intent_match_score(product, intent or "curiosity")

    # Problem 3 hard reject gate
    if intent_match_score < 0.20:
        return None  # type: ignore[return-value]  # caller must handle

    raw = (0.25 * rating_score
           + 0.20 * log_sold_score
           + 0.20 * keyword_match_score
           + 0.15 * intent_match_score
           + 0.10 * price_score
           + 0.10 * review_signal)
    return max(0.0, min(1.0, round(raw, 4)))


# ─────────────────────────────────────────────────────────────────────────────
# Part 4 + 5 + 7 — Candidate-aware pool with cache
# ─────────────────────────────────────────────────────────────────────────────

def _cache_key(keywords: list[str]) -> str:
    joined = "|".join(sorted(keywords))
    return hashlib.md5(joined.encode()).hexdigest()


def get_product_pool_by_candidate(candidate: dict[str, Any],
                                   limit: int = _MAX_POOL_SIZE) -> list[dict[str, Any]]:
    """
    Primary entry point. Returns targeted product pool for this candidate.

    Pipeline:
        keywords → fetch_by_keywords → normalize → dedup → filter →
        tag niche → score → sort DESC → fallback guard → top N

    Returns [] if no products meet quality threshold (Part 5).
    Caches per keyword set with TTL (Part 7).
    """
    try:
        # Part 1: extract keywords
        keywords = extract_product_keywords(candidate)

        # Part 7: cache lookup
        ck  = _cache_key(keywords)
        now = time.time()
        if ck in _POOL_CACHE:
            exp, cached = _POOL_CACHE[ck]
            if now < exp:
                return cached[:limit]

        # Part 2: keyword-filtered fetch
        raw = fetch_products_by_keywords(keywords, limit=len(_MOCK_CATALOGUE))

        # Normalize + dedup
        normed = normalize_products(raw)
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for p in normed:
            if p["product_id"] not in seen:
                seen.add(p["product_id"])
                deduped.append(p)

        # Part 4: filter quality gates
        filtered = filter_products(deduped)

        # Tag niche
        for p in filtered:
            tag_product_niche(p)

        # Part 3: score with keywords + intent (Problem 3)
        from core.product_matcher import detect_intent as _detect_intent
        _intent = _detect_intent(candidate)
        intent_passed: list[dict[str, Any]] = []
        for p in filtered:
            s = score_product(p, keywords, intent=_intent)
            if s is not None:   # None = failed intent_match < 0.20 gate
                p["product_score"] = s
                intent_passed.append(p)

        # Sort DESC by score, then product_id for determinism
        intent_passed.sort(key=lambda p: (-p["product_score"], p["product_id"]))

        # Part 5: fallback — return [] if no products or top score too low
        if not intent_passed or intent_passed[0]["product_score"] < _FALLBACK_MIN_SCORE:
            _POOL_CACHE[ck] = (now + _CACHE_TTL_S, [])
            return []

        # Part 8: ensure output format fields present
        result = intent_passed[:limit]

        # Part 7: store in cache
        _POOL_CACHE[ck] = (now + _CACHE_TTL_S, result)

        return result

    except Exception:
        return []


def get_product_pool(limit: int = _MAX_POOL_SIZE) -> list[dict[str, Any]]:
    """
    Backward-compatible: non-targeted pool (used as last-resort fallback).
    Prefer get_product_pool_by_candidate() for targeted retrieval.
    """
    try:
        raw      = fetch_products(limit=len(_MOCK_CATALOGUE))
        normed   = normalize_products(raw)
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for p in normed:
            if p["product_id"] not in seen:
                seen.add(p["product_id"])
                deduped.append(p)
        filtered = filter_products(deduped)
        for p in filtered:
            tag_product_niche(p)
            p["keyword_match_score"] = 0.0
            p["product_score"]       = score_product(p)
        filtered.sort(key=lambda p: (-p["product_score"], p["product_id"]))
        return filtered[:limit]
    except Exception:
        return []
