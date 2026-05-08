"""
Trend Agent — Layer 7.5: Market signal simulation + ContentEngine integration.

Simulates a Kalodata-style trending product discovery system.

Input:
    keyword: str | None          — optional filter keyword
    account_id: str              — drives deterministic variation
    top_n: int                   — max results to return (default 10)

Output:
    list[TrendResult]:
        product: str             — product name
        score:   float           — trending score 0.0–1.0
        reason:  str             — human-readable explanation

Internal pipeline:
    1. Keyword expansion        — expand keyword into search variants
    2. Product catalog scan     — score products from simulated catalog
    3. Signal computation       — velocity + engagement + saturation scores
    4. Rank + filter            — top_n by score, deduped
    5. ContentEngine push       — convert top results → ContentPlan inputs

Design contracts:
    - 100% deterministic per (keyword, account_id) pair
    - No HTTP calls — pure simulation with realistic distributions
    - ContentEngine receives TrendResult as a build_plan() source
    - Score always in [0.0, 1.0] with 3 decimal precision
    - reason always a non-empty string
    - product always a non-empty string

Usage:
    agent = get_trend_agent()
    results = agent.scan(keyword="skincare", account_id="acct-01")
    plans   = agent.push_to_content_engine(results, account_id="acct-01")
"""
from __future__ import annotations

import hashlib
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.content_engine import ContentPlan

LOGGER = logging.getLogger("core.trend_agent")


# ── PRNG helpers ──────────────────────────────────────────────────────────────

def _tseed(key: str, slot: int) -> float:
    """Deterministic float [0,1) from composite key + slot."""
    h = hashlib.sha256(f"ta:{key}:{slot}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _tpick(key: str, slot: int, pool: list) -> Any:
    return pool[int(_tseed(key, slot) * len(pool))]


def _tfloat(key: str, slot: int, lo: float, hi: float) -> float:
    return round(lo + _tseed(key, slot) * (hi - lo), 4)


def _tint(key: str, slot: int, lo: int, hi: int) -> int:
    return lo + int(_tseed(key, slot) * (hi - lo + 1))


# ── Product catalog ────────────────────────────────────────────────────────────
# Simulates a Kalodata-style product database.
# Structured as niche → list of (product_name, base_score, category).
# base_score represents inherent popularity of the niche (before signal modifiers).

_CATALOG: dict[str, list[tuple[str, float, str]]] = {
    "skincare": [
        ("Vitamin C Serum 30ml",            0.82, "beauty"),
        ("Retinol Night Cream SPF50",        0.75, "beauty"),
        ("Hyaluronic Acid Toner 200ml",      0.78, "beauty"),
        ("Collagen Face Mask 5-Pack",        0.70, "beauty"),
        ("Niacinamide Pore Minimizer",       0.72, "beauty"),
        ("Sunscreen SPF60 UVA/UVB",          0.68, "beauty"),
        ("AHA/BHA Exfoliant Pads",           0.65, "beauty"),
        ("Rose Water Toner",                 0.60, "beauty"),
    ],
    "fitness": [
        ("Resistance Band Set 5-Level",      0.85, "sports"),
        ("Adjustable Dumbbells 20kg",        0.80, "sports"),
        ("Yoga Mat Anti-Slip 6mm",           0.74, "sports"),
        ("Jump Rope Speed Cable",            0.68, "sports"),
        ("Ab Roller Wheel w/ Knee Pad",      0.72, "sports"),
        ("Foam Roller Deep Tissue",          0.66, "sports"),
        ("Whey Protein 1kg Chocolate",       0.78, "sports"),
        ("Workout Gloves Anti-Slip",         0.62, "sports"),
    ],
    "kitchen": [
        ("Air Fryer 5L Digital",             0.88, "home"),
        ("Silicone Spatula Set 6-Piece",     0.64, "home"),
        ("Cast Iron Skillet 25cm",           0.72, "home"),
        ("Instant Pot 7-in-1 Cooker",        0.83, "home"),
        ("Glass Meal Prep Containers 10-Set",0.70, "home"),
        ("Electric Milk Frother",            0.67, "home"),
        ("Bamboo Cutting Board XL",          0.61, "home"),
        ("Digital Kitchen Scale 5kg",        0.65, "home"),
    ],
    "tech": [
        ("LED Ring Light 18 inch",           0.87, "electronics"),
        ("Wireless Earbuds ANC",             0.84, "electronics"),
        ("Phone Stand Adjustable Gooseneck", 0.76, "electronics"),
        ("USB-C Hub 7-in-1",                 0.79, "electronics"),
        ("Mechanical Keyboard RGB TKL",      0.73, "electronics"),
        ("Screen Cleaning Kit Pro",          0.60, "electronics"),
        ("Cable Management Box Large",       0.63, "electronics"),
        ("Webcam 1080p Autofocus",           0.77, "electronics"),
    ],
    "fashion": [
        ("Oversized Hoodie Unisex",          0.80, "apparel"),
        ("High-Waist Yoga Leggings",         0.85, "apparel"),
        ("Canvas Tote Bag Heavyweight",      0.68, "apparel"),
        ("Compression Socks 3-Pair",         0.63, "apparel"),
        ("Stainless Steel Watch Minimalist", 0.74, "apparel"),
        ("Bamboo Socks Eco 5-Pack",          0.60, "apparel"),
        ("Baseball Cap Structured",          0.65, "apparel"),
        ("Crossbody Bag Water-Resistant",    0.71, "apparel"),
    ],
    "pet": [
        ("Cat Scratcher Cardboard XL",       0.76, "pets"),
        ("Dog Harness No-Pull Reflective",   0.82, "pets"),
        ("Interactive Puzzle Feeder",        0.72, "pets"),
        ("Pet GPS Tracker Waterproof",       0.78, "pets"),
        ("Self-Cleaning Slicker Brush",      0.70, "pets"),
        ("Orthopedic Dog Bed Memory Foam",   0.75, "pets"),
        ("Automatic Water Fountain 2L",      0.68, "pets"),
        ("Freeze-Dried Chicken Treats 100g", 0.65, "pets"),
    ],
    "home": [
        ("LED Strip Lights 5m Smart",        0.84, "home"),
        ("Wall-Mount Organizer Bamboo",      0.67, "home"),
        ("Weighted Blanket 7kg",             0.80, "home"),
        ("Air Purifier HEPA H13",            0.86, "home"),
        ("Sunrise Alarm Clock LED",          0.73, "home"),
        ("Vacuum Storage Bags 12-Set",       0.65, "home"),
        ("Smart Plug Wi-Fi 4-Pack",          0.79, "home"),
        ("Essential Oil Diffuser 500ml",     0.71, "home"),
    ],
    "baby": [
        ("Baby Monitor 1080p Night Vision",  0.83, "parenting"),
        ("Silicone Feeding Set BPA-Free",    0.78, "parenting"),
        ("Portable Baby Bouncer",            0.75, "parenting"),
        ("Organic Cotton Swaddle Blanket",   0.70, "parenting"),
        ("Teething Toy Set Chilled",         0.68, "parenting"),
        ("White Noise Machine Portable",     0.80, "parenting"),
        ("Baby Food Maker Steamer Blender",  0.72, "parenting"),
        ("Diaper Backpack Waterproof",       0.76, "parenting"),
    ],
}

# Keyword → niche mapping (also covers partial matches)
_KEYWORD_NICHE_MAP: dict[str, str] = {
    "skincare":   "skincare",
    "beauty":     "skincare",
    "serum":      "skincare",
    "moisturizer":"skincare",
    "fitness":    "fitness",
    "workout":    "fitness",
    "gym":        "fitness",
    "protein":    "fitness",
    "kitchen":    "kitchen",
    "cooking":    "kitchen",
    "air fryer":  "kitchen",
    "food":       "kitchen",
    "tech":       "tech",
    "gadget":     "tech",
    "camera":     "tech",
    "phone":      "tech",
    "fashion":    "fashion",
    "clothing":   "fashion",
    "style":      "fashion",
    "outfit":     "fashion",
    "pet":        "pet",
    "dog":        "pet",
    "cat":        "pet",
    "animal":     "pet",
    "home":       "home",
    "decor":      "home",
    "smart":      "home",
    "light":      "home",
    "baby":       "baby",
    "infant":     "baby",
    "toddler":    "baby",
    "parenting":  "baby",
}

# Trending reason templates (seeded per product)
_REASON_TEMPLATES: list[str] = [
    "{product} is trending with {velocity}% weekly sales growth in the {category} niche",
    "High engagement rate ({engagement}%) on short-form content featuring {product}",
    "{product} has {saturation}% lower market saturation than comparable alternatives",
    "Search volume spike detected for {product} — up {velocity}% this week",
    "{product} trending in {category} with strong repeat-purchase signal ({repeat}%)",
    "Low competition + high demand: {product} has a {score} opportunity score",
    "{product} dominating the {category} TikTok Shop — {velocity}% GMV increase",
    "Viral potential: {product} featured in {engagement} creator videos this week",
]


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class TrendSignals:
    """Internal scoring breakdown for a single product."""
    velocity:    float    # sales growth rate 0–1
    engagement:  float    # content engagement rate 0–1
    saturation:  float    # inverse saturation (higher = less crowded) 0–1
    repeat_rate: float    # repeat purchase probability 0–1
    recency:     float    # how recently it started trending 0–1


@dataclass
class TrendResult:
    """Single trending product result (matches required output schema exactly)."""
    product:  str
    score:    float    # 0.000–1.000
    reason:   str
    # Internal fields (not in required schema but useful for integration)
    niche:    str      = ""
    category: str      = ""
    signals:  TrendSignals | None = None

    def to_dict(self) -> dict[str, Any]:
        """Returns the required output schema {product, score, reason}."""
        return {
            "product": self.product,
            "score":   self.score,
            "reason":  self.reason,
        }

    def to_full_dict(self) -> dict[str, Any]:
        """Extended dict including niche, category and signal breakdown."""
        d = self.to_dict()
        d["niche"]    = self.niche
        d["category"] = self.category
        if self.signals:
            d["signals"] = {
                "velocity":    self.signals.velocity,
                "engagement":  self.signals.engagement,
                "saturation":  self.signals.saturation,
                "repeat_rate": self.signals.repeat_rate,
                "recency":     self.signals.recency,
            }
        return d


# ── TrendAgent ────────────────────────────────────────────────────────────────

class TrendAgent:
    """
    Layer 7.5: Simulated trend discovery engine.

    Produces deterministic, varied trending product signals per
    (keyword, account_id) pair. Integrates with ContentEngine to
    convert top trends into ready-to-use ContentPlan inputs.

    Public API:
        scan()                 → list[TrendResult]
        push_to_content_engine() → list[ContentPlan]
        scan_and_plan()        → tuple[list[TrendResult], list[ContentPlan]]
    """

    # Weights for final composite score
    _SIGNAL_WEIGHTS: dict[str, float] = {
        "velocity":    0.35,
        "engagement":  0.25,
        "saturation":  0.20,
        "repeat_rate": 0.10,
        "recency":     0.10,
    }

    def __init__(
        self,
        content_engine: Any | None = None,   # ContentEngine (optional injection)
    ) -> None:
        from core.content_engine import get_content_engine
        self._engine = content_engine or get_content_engine()

    # ── Public API ────────────────────────────────────────────────────────────

    def scan(
        self,
        account_id: str,
        keyword:    str | None = None,
        top_n:      int        = 10,
        mode_hint:  str        = "create",   # content mode to tag results with
    ) -> list[TrendResult]:
        """
        Scan trending products, optionally filtered by keyword.

        Args:
            account_id: Drives deterministic variation across accounts.
            keyword:    Optional filter; matches niche or partial product name.
            top_n:      Max results to return.
            mode_hint:  Hint for downstream ContentEngine (not used in scoring).

        Returns:
            list[TrendResult] sorted by score descending, length <= top_n.
        """
        niches = self._resolve_niches(keyword, account_id)
        candidates: list[TrendResult] = []

        for niche in niches:
            products = _CATALOG.get(niche, [])
            for i, (product, base, category) in enumerate(products):
                # Composite seed: account + niche + product position
                seed_key = f"{account_id}:{niche}:{i}"
                signals  = self._compute_signals(seed_key, base)
                score    = self._composite_score(signals, base)
                reason   = self._generate_reason(
                    product, category, signals, score, seed_key
                )
                candidates.append(TrendResult(
                    product  = product,
                    score    = round(score, 3),
                    reason   = reason,
                    niche    = niche,
                    category = category,
                    signals  = signals,
                ))

        # Sort by score desc, deduplicate by product name
        seen: set[str] = set()
        ranked: list[TrendResult] = []
        for r in sorted(candidates, key=lambda x: x.score, reverse=True):
            if r.product not in seen:
                seen.add(r.product)
                ranked.append(r)
            if len(ranked) >= top_n:
                break

        LOGGER.info("trend_scan_complete", extra={
            "account_id": account_id,
            "keyword":    keyword,
            "niches":     niches,
            "candidates": len(candidates),
            "returned":   len(ranked),
        })

        return ranked

    def push_to_content_engine(
        self,
        results:    list[TrendResult],
        account_id: str,
        mode:       str = "create",
        profile:    Any | None = None,   # IdentityProfile (optional)
    ) -> list["ContentPlan"]:
        """
        Convert TrendResults into ContentPlans via ContentEngine.

        Each TrendResult becomes one ContentPlan with:
            source = "trend://{niche}/{product}" (deterministic URI)
            type   = "product"
            mode   = mode param

        Args:
            results:    Output of scan().
            account_id: Account to build plans for.
            mode:       ContentEngine mode (create/remake/reup).
            profile:    Optional IdentityProfile for style hints.

        Returns:
            list[ContentPlan], same length as results.
        """
        plans = []
        for result in results:
            source = f"trend://{result.niche}/{result.product.lower().replace(' ', '-')}"
            input_data = {
                "account_id": account_id,
                "type":       "product",
                "source":     source,
                "mode":       mode,
            }
            try:
                plan = self._engine.build_plan(input_data, profile=profile)
                plans.append(plan)
                LOGGER.debug("trend_plan_built", extra={
                    "account_id":  account_id,
                    "product":     result.product,
                    "template_id": plan.template_id,
                })
            except Exception as exc:
                LOGGER.error("trend_plan_error", extra={
                    "account_id": account_id,
                    "product":    result.product,
                    "error":      str(exc),
                })
        return plans

    def scan_and_plan(
        self,
        account_id: str,
        keyword:    str | None = None,
        top_n:      int        = 5,
        mode:       str        = "create",
        profile:    Any | None = None,
    ) -> tuple[list[TrendResult], list["ContentPlan"]]:
        """
        Convenience: scan() + push_to_content_engine() in one call.

        Returns:
            (trend_results, content_plans) — parallel lists.
        """
        results = self.scan(account_id=account_id, keyword=keyword, top_n=top_n, mode_hint=mode)
        plans   = self.push_to_content_engine(results, account_id=account_id, mode=mode, profile=profile)
        return results, plans

    # ── Internal pipeline ─────────────────────────────────────────────────────

    def _resolve_niches(self, keyword: str | None, account_id: str) -> list[str]:
        """
        Map keyword to one or more catalog niches.
        If keyword is None, return all niches in a seeded order (account-specific).
        """
        if keyword is None:
            # Return all niches; seeded shuffle so each account gets different order
            niches = list(_CATALOG.keys())
            # Seeded rotation: pick a start offset
            offset = _tint(account_id, 0, 0, len(niches) - 1)
            return niches[offset:] + niches[:offset]

        kw_lower = keyword.lower().strip()

        # Exact match
        if kw_lower in _KEYWORD_NICHE_MAP:
            return [_KEYWORD_NICHE_MAP[kw_lower]]

        # Partial match
        matches = [
            niche for kw, niche in _KEYWORD_NICHE_MAP.items()
            if kw in kw_lower or kw_lower in kw
        ]
        if matches:
            # Deduplicate preserving order
            seen: set[str] = set()
            return [m for m in matches if not (m in seen or seen.add(m))]  # type: ignore[func-returns-value]

        # No match → fall back to a seeded niche selection (avoid ignoring keyword)
        LOGGER.debug("keyword_no_match", extra={"keyword": keyword, "account_id": account_id})
        all_niches = list(_CATALOG.keys())
        picked = _tpick(account_id, 5, all_niches)
        return [picked]

    def _compute_signals(self, seed_key: str, base_score: float) -> TrendSignals:
        """
        Compute per-product trend signals.
        base_score biases the central tendency so popular niches score higher.
        Each signal is independently seeded for variety.
        """
        # Velocity: sales growth — biased upward for high base products
        velocity = _tfloat(seed_key, 10, base_score * 0.6, min(1.0, base_score * 1.25))

        # Engagement: content interaction rate
        engagement = _tfloat(seed_key, 20, base_score * 0.5, min(1.0, base_score * 1.3))

        # Saturation: inverse of market crowding (higher = less saturated = better)
        raw_sat = _tfloat(seed_key, 30, 0.1, 0.9)
        # Lower base_score → potentially more room (less crowded niches)
        saturation = round(raw_sat * (1.2 - base_score * 0.3), 4)
        saturation = max(0.0, min(1.0, saturation))

        # Repeat purchase rate
        repeat_rate = _tfloat(seed_key, 40, 0.2, 0.85)

        # Recency: how fresh the trend is (0 = old, 1 = just started)
        recency = _tfloat(seed_key, 50, 0.3, 1.0)

        return TrendSignals(
            velocity    = velocity,
            engagement  = engagement,
            saturation  = saturation,
            repeat_rate = repeat_rate,
            recency     = recency,
        )

    def _composite_score(self, s: TrendSignals, base: float) -> float:
        """
        Weighted composite of all signals plus base_score anchor.
        Score is in [0.0, 1.0].
        """
        w = self._SIGNAL_WEIGHTS
        raw = (
            s.velocity    * w["velocity"]
            + s.engagement  * w["engagement"]
            + s.saturation  * w["saturation"]
            + s.repeat_rate * w["repeat_rate"]
            + s.recency     * w["recency"]
        )
        # Blend with base_score (30% anchor so catalog quality matters)
        blended = raw * 0.70 + base * 0.30
        return max(0.0, min(1.0, round(blended, 4)))

    def _generate_reason(
        self,
        product:  str,
        category: str,
        signals:  TrendSignals,
        score:    float,
        seed_key: str,
    ) -> str:
        """Generate a seeded, human-readable reason string."""
        template = _tpick(seed_key, 60, _REASON_TEMPLATES)

        velocity_pct    = int(signals.velocity    * 120)   # up to 120%
        engagement_pct  = int(signals.engagement  * 100)
        saturation_pct  = int((1 - signals.saturation) * 80) + 10  # lower is better
        repeat_pct      = int(signals.repeat_rate * 100)
        creator_count   = _tint(seed_key, 70, 120, 4800)

        return template.format(
            product     = product,
            category    = category,
            velocity    = velocity_pct,
            engagement  = engagement_pct,
            saturation  = saturation_pct,
            repeat      = repeat_pct,
            score       = score,
        ).replace("{engagement}", str(creator_count))   # if template uses raw count


# ── Singleton ─────────────────────────────────────────────────────────────────

_TREND_AGENT: TrendAgent | None = None


def get_trend_agent(content_engine: Any | None = None) -> TrendAgent:
    """Return the process-level TrendAgent singleton."""
    global _TREND_AGENT
    if _TREND_AGENT is None:
        _TREND_AGENT = TrendAgent(content_engine=content_engine)
    return _TREND_AGENT


def reset_trend_agent() -> None:
    """Reset singleton (for testing)."""
    global _TREND_AGENT
    _TREND_AGENT = None
