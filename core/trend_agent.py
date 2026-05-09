"""
Trend Agent — Layer 7.5: Simulated trend discovery + ContentEngine integration.

Simulates a Kalodata-style trending product discovery system.

Input:
    keyword:   str | None  — optional niche filter
    account_id: str        — drives per-account deterministic variation
    top_n:     int         — max results (default 10)
    day_seed:  int | None  — override daily seed (default: today UTC YYYYMMDD int)

Output:
    list[TrendResult] sorted by score desc:
        product:  str   — product name
        score:    float — 0.000–1.000
        reason:   str   — human-readable explanation
        niche:    str
        category: str
        day_seed: int   — seed used (for reproducibility audit)

Design contracts:
    - 100% deterministic: same (keyword, account_id, day_seed) → same output.
    - No HTTP calls — pure simulation.
    - Seed = hash(account_id + keyword + day_str).
    - Small daily variation only (single day_seed, no week/slot stacking).
    - Per-account variation via account_id in seed key (not a separate slot hash).
    - Score always in [0.0, 1.0] with 3 decimal precision.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.content_engine import ContentPlan

LOGGER = logging.getLogger("core.trend_agent")


# ── Stable deterministic hash helpers ────────────────────────────────────────

def stable_hash_int(*parts: str, mod: int = 10 ** 9) -> int:
    """Stable, process-invariant integer hash (SHA-256 based).

    Identical to mutation_controller.stable_hash_int. Duplicated here to keep
    this module self-contained without a circular import.
    Output is identical across Python processes, machines, and runs.
    """
    h = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return int(h[:16], 16) % mod


def build_time_seed(account_id: str, day_seed: int) -> int:
    """Account-decorrelated daily seed.

    FIX 2: raw day_seed is shared by ALL accounts (same date = same pattern).
    This function mixes account_id in so each account gets a unique base seed
    per day, preventing cross-account synchronisation.

    Still deterministic: same (account_id, day_seed) always gives same result.
    """
    return stable_hash_int(account_id, str(day_seed))


def _seed_float(key: str) -> float:
    """Deterministic float [0, 1) from a composite string key. Process-stable."""
    return stable_hash_int(key) / (10 ** 9 - 1)


def _seed_pick(key: str, pool: list) -> Any:
    """Pick deterministically from a list."""
    return pool[int(_seed_float(key) * len(pool))]


def _seed_float_range(key: str, lo: float, hi: float) -> float:
    return round(lo + _seed_float(key) * (hi - lo), 4)


def _seed_int_range(key: str, lo: int, hi: int) -> int:
    return lo + int(_seed_float(key) * (hi - lo + 1))


def _today_int() -> int:
    """Today's UTC date as YYYYMMDD integer. Changes once per day."""
    return int(datetime.now(timezone.utc).strftime("%Y%m%d"))


# ── Product catalog ────────────────────────────────────────────────────────────

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

_KEYWORD_NICHE_MAP: dict[str, str] = {
    "skincare": "skincare", "beauty": "skincare", "serum": "skincare", "moisturizer": "skincare",
    "fitness":  "fitness",  "workout": "fitness",  "gym": "fitness",    "protein": "fitness",
    "kitchen":  "kitchen",  "cooking": "kitchen",  "air fryer": "kitchen", "food": "kitchen",
    "tech":     "tech",     "gadget": "tech",      "camera": "tech",    "phone": "tech",
    "fashion":  "fashion",  "clothing": "fashion", "style": "fashion",  "outfit": "fashion",
    "pet":      "pet",      "dog": "pet",          "cat": "pet",        "animal": "pet",
    "home":     "home",     "decor": "home",       "smart": "home",     "light": "home",
    "baby":     "baby",     "infant": "baby",      "toddler": "baby",   "parenting": "baby",
}

_REASON_TEMPLATES: list[str] = [
    "{product} trending with {velocity}% weekly sales growth in the {category} niche",
    "High engagement rate ({engagement}%) on short-form content featuring {product}",
    "{product} has {saturation}% lower market saturation than comparable alternatives",
    "Search volume spike for {product} — up {velocity}% this week",
    "{product} in {category} with strong repeat-purchase signal ({repeat}%)",
    "Low competition + high demand: {product} opportunity score {score}",
    "{product} dominating {category} TikTok Shop — {velocity}% GMV increase",
    "Viral: {product} featured in {engagement} creator videos this week",
]

# Signal weights for composite score
_SIGNAL_WEIGHTS: dict[str, float] = {
    "velocity":    0.35,
    "engagement":  0.25,
    "saturation":  0.20,
    "repeat_rate": 0.10,
    "recency":     0.10,
}


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class TrendSignals:
    """Internal scoring signals for a single product."""
    velocity:    float
    engagement:  float
    saturation:  float   # inverse saturation — higher = less crowded
    repeat_rate: float
    recency:     float


@dataclass
class TrendResult:
    """Single trending product result."""
    product:  str
    score:    float       # 0.000–1.000
    reason:   str
    niche:    str         = ""
    category: str         = ""
    signals:  TrendSignals | None = None
    day_seed: int         = 0    # temporal seed used — for audit/reproducibility

    def to_dict(self) -> dict[str, Any]:
        return {"product": self.product, "score": self.score, "reason": self.reason}

    def to_full_dict(self) -> dict[str, Any]:
        d = self.to_dict()
        d["niche"]    = self.niche
        d["category"] = self.category
        d["day_seed"] = self.day_seed
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
    Simulated trend discovery engine.

    Fully deterministic: seed = hash(account_id + keyword_or_niche + product_index + day).
    Daily variation: day_seed changes once per UTC day.
    Per-account variation: account_id is part of the seed key.
    No week seeds, no slot hashing — one clear seed per (account, product, day).
    """

    def __init__(self, content_engine: Any | None = None) -> None:
        from core.content_engine import get_content_engine
        self._engine = content_engine or get_content_engine()

    # ── Public API ────────────────────────────────────────────────────────────

    def scan(
        self,
        account_id: str,
        keyword:    str | None = None,
        top_n:      int        = 10,
        mode_hint:  str        = "create",
        day_seed:   int | None = None,
    ) -> list[TrendResult]:
        """Scan trending products, optionally filtered by keyword.

        Args:
            account_id: Drives per-account seed variation.
            keyword:    Optional niche/keyword filter.
            top_n:      Max results returned.
            mode_hint:  Passed to ContentEngine (not used in scoring).
            day_seed:   Override for daily seed. Default: today's YYYYMMDD int.
                        Pass a fixed int in tests to pin the date.

        Returns:
            list[TrendResult] sorted by score desc, length <= top_n.
            Same inputs always produce the same output.
        """
        ds = day_seed if day_seed is not None else _today_int()
        # FIX 2: decorrelate accounts — each (account, day) gets a unique base seed.
        base_seed = build_time_seed(account_id, ds)
        niches = self._resolve_niches(keyword, account_id, base_seed)
        candidates: list[TrendResult] = []

        for niche in niches:
            for i, (product, base, category) in enumerate(_CATALOG.get(niche, [])):
                # FIX 1 + FIX 2: seed is now account-decorrelated and process-stable.
                # key = stable_hash of (account-decorated base_seed, niche, product index).
                key = f"{base_seed}:{niche}:{i}"
                sig   = self._compute_signals(key, base)
                score = self._composite_score(sig, base)
                reason = self._generate_reason(product, category, sig, score, key)
                candidates.append(TrendResult(
                    product  = product,
                    score    = round(score, 3),
                    reason   = reason,
                    niche    = niche,
                    category = category,
                    signals  = sig,
                    day_seed = ds,
                ))

        # Deduplicate by product name, sort by score desc, take top_n
        seen: set[str] = set()
        ranked: list[TrendResult] = []
        for r in sorted(candidates, key=lambda x: x.score, reverse=True):
            if r.product not in seen:
                seen.add(r.product)
                ranked.append(r)
            if len(ranked) >= top_n:
                break

        LOGGER.info(
            "trend_scan account=%s keyword=%s niches=%s returned=%d day_seed=%d",
            account_id, keyword, niches, len(ranked), ds,
        )
        return ranked

    def push_to_content_engine(
        self,
        results:    list[TrendResult],
        account_id: str,
        mode:       str        = "create",
        profile:    Any | None = None,
    ) -> list["ContentPlan"]:
        """Convert TrendResults → ContentPlans via ContentEngine."""
        plans = []
        for result in results:
            source = f"trend://{result.niche}/{result.product.lower().replace(' ', '-')}"
            try:
                plan = self._engine.build_plan(
                    {"account_id": account_id, "type": "product", "source": source, "mode": mode},
                    profile=profile,
                )
                plans.append(plan)
                LOGGER.debug(
                    "trend_plan_built account=%s product=%s template=%s",
                    account_id, result.product, plan.template_id,
                )
            except Exception as exc:
                LOGGER.error("trend_plan_error account=%s product=%s error=%s",
                             account_id, result.product, exc)
        return plans

    def scan_and_plan(
        self,
        account_id: str,
        keyword:    str | None = None,
        top_n:      int        = 5,
        mode:       str        = "create",
        profile:    Any | None = None,
        day_seed:   int | None = None,
    ) -> tuple[list[TrendResult], list["ContentPlan"]]:
        """Convenience wrapper: scan() then push_to_content_engine()."""
        results = self.scan(account_id=account_id, keyword=keyword,
                            top_n=top_n, mode_hint=mode, day_seed=day_seed)
        plans   = self.push_to_content_engine(results, account_id=account_id,
                                               mode=mode, profile=profile)
        return results, plans

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _resolve_niches(keyword: str | None, account_id: str, base_seed: int) -> list[str]:
        """Map keyword to niche list. If no keyword, return all niches in seeded order.

        base_seed is already account-decorrelated via build_time_seed().
        """
        if keyword is None:
            niches = list(_CATALOG.keys())
            offset = _seed_int_range(str(base_seed), 0, len(niches) - 1)
            return niches[offset:] + niches[:offset]

        kw = keyword.lower().strip()
        if kw in _KEYWORD_NICHE_MAP:
            return [_KEYWORD_NICHE_MAP[kw]]

        matches = [n for k, n in _KEYWORD_NICHE_MAP.items() if k in kw or kw in k]
        if matches:
            seen: set[str] = set()
            return [m for m in matches if not (m in seen or seen.add(m))]  # type: ignore

        # Fallback: seeded pick from full catalog using account-decorrelated seed.
        return [_seed_pick(str(base_seed), list(_CATALOG.keys()))]

    @staticmethod
    def _compute_signals(key: str, base: float) -> TrendSignals:
        """Compute per-product signals. base biases the distribution."""
        return TrendSignals(
            velocity    = _seed_float_range(f"{key}:v",  base * 0.6, min(1.0, base * 1.25)),
            engagement  = _seed_float_range(f"{key}:e",  base * 0.5, min(1.0, base * 1.3)),
            saturation  = max(0.0, min(1.0, _seed_float_range(f"{key}:s", 0.1, 0.9) * (1.2 - base * 0.3))),
            repeat_rate = _seed_float_range(f"{key}:r",  0.2, 0.85),
            recency     = _seed_float_range(f"{key}:rc", 0.3, 1.0),
        )

    @staticmethod
    def _composite_score(s: TrendSignals, base: float) -> float:
        """Weighted composite of signals + base anchor → [0.0, 1.0]."""
        w = _SIGNAL_WEIGHTS
        raw = (s.velocity * w["velocity"] + s.engagement * w["engagement"]
               + s.saturation * w["saturation"] + s.repeat_rate * w["repeat_rate"]
               + s.recency * w["recency"])
        return max(0.0, min(1.0, round(raw * 0.70 + base * 0.30, 4)))

    @staticmethod
    def _generate_reason(product: str, category: str, s: TrendSignals, score: float, key: str) -> str:
        template = _seed_pick(f"{key}:tmpl", _REASON_TEMPLATES)
        return template.format(
            product    = product,
            category   = category,
            velocity   = int(s.velocity   * 120),
            engagement = int(s.engagement * 100),
            saturation = int((1 - s.saturation) * 80) + 10,
            repeat     = int(s.repeat_rate * 100),
            score      = score,
        )


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
