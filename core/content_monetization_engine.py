"""
core/content_monetization_engine.py — Content Monetization Engine

Philosophy: "Content FIRST, product SECOND."
CTAs feel natural. No spam. Never one-size-fits-all.

Public API:
    monetize(candidate)                          -> dict  ← primary entry
    resolve_mode(candidate)                      -> str
    resolve_cta_placement(mode, intent)          -> dict
    generate_structured_cta(mode, intent, niche, seed) -> dict
    generate_script(candidate, product, mode)    -> dict
    generate_cta(mode, intent, product, niche)   -> str
    inject_product(script, product, mode)        -> dict
    generate_variants(base_script, candidate, mode, n) -> list[dict]
"""
from __future__ import annotations

import hashlib
from typing import Any

# ── Anti-spam phrase blocklist ────────────────────────────────────────────────

_SPAM_PHRASES: frozenset[str] = frozenset({
    "buy now", "limited time", "act fast", "click here", "100% free",
    "guaranteed", "no risk", "order now", "don't miss", "special offer",
    "exclusive deal", "instant results", "make money fast",
})

# ── Thresholds ────────────────────────────────────────────────────────────────

_DIRECT_THRESHOLD  = 0.70   # product_match_score >= this → DIRECT
_VARIANT_MIN_EV    = 50.0   # expected_value floor to justify variation cost

# ─────────────────────────────────────────────────────────────────────────────
# Part 1 — Monetization Mode Resolution
# ─────────────────────────────────────────────────────────────────────────────

def resolve_mode(candidate: dict[str, Any]) -> str:
    """
    Returns one of: "direct" | "indirect" | "trust"

    Priority:
        1. product_match_score >= 0.70 AND product present → direct
        2. soft_match / suggest_soft_cta → indirect
        3. no_product_mode / nothing → trust
    """
    pms    = float(candidate.get("product_match_score", 0.0))
    has_p  = bool(candidate.get("product") or candidate.get("best_product"))
    soft   = bool(candidate.get("soft_match") or candidate.get("suggest_soft_cta"))
    no_p   = bool(candidate.get("no_product_mode", True))

    if pms >= _DIRECT_THRESHOLD and has_p:
        return "direct"
    if soft or (0.0 < pms < _DIRECT_THRESHOLD and not no_p):
        return "indirect"
    return "trust"


# ─────────────────────────────────────────────────────────────────────────────
# Part 2 — Hook Templates
# ─────────────────────────────────────────────────────────────────────────────

# Keyed by intent → list of hook templates (use {niche} placeholder)
_HOOK_TEMPLATES: dict[str, list[str]] = {
    "problem": [
        "If you have {niche} issues, stop doing this right now...",
        "This is why your {niche} routine never actually works...",
        "The biggest {niche} mistake nobody talks about...",
    ],
    "desire": [
        "Upgrade your {niche} game with this one thing...",
        "This is what the top {niche} people are actually using...",
        "Here's the {niche} upgrade you didn't know you needed...",
    ],
    "curiosity": [
        "I didn't expect this {niche} thing to actually work...",
        "This is actually crazy — {niche} edition...",
        "Nobody is talking about this {niche} secret...",
    ],
}

_BODY_TEMPLATES: dict[str, list[str]] = {
    "problem": [
        "Most people try the obvious fix — but it doesn't last.",
        "The real issue isn't what you think. Here's what's happening...",
        "There's a smarter approach that fixes the root cause.",
    ],
    "desire": [
        "The difference is in the details most people skip.",
        "Once you upgrade this one thing, everything else clicks.",
        "Top performers all share this one habit or tool.",
    ],
    "curiosity": [
        "I tested this for 30 days. The results were unexpected.",
        "It sounds simple — but the mechanism is fascinating.",
        "Most people overlook this completely. Here's why it works.",
    ],
}

_PAYOFF_TEMPLATES: dict[str, list[str]] = {
    "problem": [
        "After switching, the difference was immediate.",
        "This solved what nothing else could.",
        "Before: struggling. After: completely different.",
    ],
    "desire": [
        "The upgrade paid for itself in the first week.",
        "This is now a non-negotiable part of my routine.",
        "Results speak for themselves.",
    ],
    "curiosity": [
        "Now I can't imagine going back.",
        "Turned out to be one of the best things I tried.",
        "The experiment exceeded every expectation.",
    ],
}


def _pick(templates: list[str], seed: str, niche: str = "") -> str:
    """Deterministic selection from a template list using a hash seed."""
    idx = int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) % len(templates)
    return templates[idx].replace("{niche}", niche or "this")


# ─────────────────────────────────────────────────────────────────────────────
# Part 3 — CTA Engine
# ─────────────────────────────────────────────────────────────────────────────

_CTA_DIRECT: dict[str, list[str]] = {
    "problem":   [
        "Get this before your {niche} gets worse — link below.",
        "Stop struggling with {niche}. What I used is linked.",
        "This fixed my {niche} — grab it while it's available.",
    ],
    "desire":    [
        "Upgrade your {niche} here — link in bio.",
        "Ready to level up your {niche}? It's linked.",
        "The upgrade is in my bio. Worth every penny.",
    ],
    "curiosity": [
        "Try it yourself — link is below.",
        "I linked the exact thing I tested.",
        "Curious? I left the link below.",
    ],
}

_CTA_INDIRECT: dict[str, list[str]] = {
    "problem":   [
        "Check the comments — I shared what actually helped me.",
        "Drop a comment if you want what fixed this.",
        "I shared the solution in the first comment.",
    ],
    "desire":    [
        "Link in bio if you want it.",
        "The full list is in my bio.",
        "Bio has everything I mentioned.",
    ],
    "curiosity": [
        "I dropped it below — you'll want to see it.",
        "Details are in the link below.",
        "Find it in the comments.",
    ],
}

_CTA_TRUST: list[str] = [
    "Follow for part 2.",
    "Save this — you'll need it later.",
    "Part 2 is coming. Follow so you don't miss it.",
    "Drop a question below — I read every comment.",
    "Share this with someone who needs it.",
]


def _sanitize_cta(text: str) -> str:
    """Remove any accidentally included spam phrases."""
    lower = text.lower()
    for phrase in _SPAM_PHRASES:
        if phrase in lower:
            text = text.replace(phrase, "").replace(phrase.title(), "").strip()
    return text


def generate_cta(mode: str, intent: str, product: dict[str, Any] | None,
                 niche: str = "", seed: str = "") -> str:
    """
    Returns a single, natural CTA string.
    Max 1 CTA per content (enforced at call site via monetize()).
    """
    seed = seed or f"{mode}:{intent}:{niche}"

    if mode == "direct":
        pool = _CTA_DIRECT.get(intent, _CTA_DIRECT["curiosity"])
        cta  = _pick(pool, seed, niche)
    elif mode == "indirect":
        pool = _CTA_INDIRECT.get(intent, _CTA_INDIRECT["curiosity"])
        cta  = _pick(pool, seed, niche)
    else:  # trust
        idx  = int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) % len(_CTA_TRUST)
        cta  = _CTA_TRUST[idx]

    return _sanitize_cta(cta)


# ─────────────────────────────────────────────────────────────────────────────
# Part 4 — Product Injection
# ─────────────────────────────────────────────────────────────────────────────

def inject_product(script: dict[str, str], product: dict[str, Any] | None,
                   mode: str) -> dict[str, str]:
    """
    Mutates script["body"] to incorporate product based on mode.

    DIRECT:  name product + benefit clearly.
    INDIRECT: hint only — no product name.
    TRUST:   no product mention.
    """
    if not product or mode == "trust":
        return script

    title = str(product.get("title", "")).strip()
    price = float(product.get("price", 0.0))

    if mode == "direct" and title:
        price_str = f" (~${price:.0f})" if price > 0 else ""
        injection = f" The specific thing I'm using: {title}{price_str}."
        script = dict(script)
        script["body"] = script.get("body", "") + injection

    elif mode == "indirect":
        # Hint without naming — tease only
        injection = " I found something that actually addresses this — more details below."
        script = dict(script)
        script["body"] = script.get("body", "") + injection

    return script


# ─────────────────────────────────────────────────────────────────────────────
# Part 2 — Script Generation
# ─────────────────────────────────────────────────────────────────────────────

def generate_script(candidate: dict[str, Any],
                    product:   dict[str, Any] | None = None,
                    mode:      str = "trust") -> dict[str, str]:
    """
    Build hook / body / payoff / cta for this candidate.

    Uses deterministic template selection — same candidate always
    produces the same base script.
    """
    intent  = str(candidate.get("intent_type",  "curiosity"))
    niche   = str(candidate.get("niche",        "")).lower()
    hook_in = str(candidate.get("hook_text",    ""))
    cid     = str(candidate.get("content_id",   hook_in[:32]))

    hook_seed    = f"hook:{cid}:{intent}:{niche}"
    body_seed    = f"body:{cid}:{intent}:{niche}"
    payoff_seed  = f"payoff:{cid}:{intent}:{niche}"
    cta_seed     = f"cta:{cid}:{intent}:{niche}:{mode}"

    hook   = _pick(_HOOK_TEMPLATES.get(intent,  _HOOK_TEMPLATES["curiosity"]),  hook_seed,   niche)
    body   = _pick(_BODY_TEMPLATES.get(intent,  _BODY_TEMPLATES["curiosity"]),  body_seed,   niche)
    payoff = _pick(_PAYOFF_TEMPLATES.get(intent, _PAYOFF_TEMPLATES["curiosity"]), payoff_seed, niche)
    cta    = generate_cta(mode, intent, product, niche, cta_seed)

    script: dict[str, str] = {
        "hook":   hook,
        "body":   body,
        "payoff": payoff,
        "cta":    cta,
    }

    # Part 4: inject product into body
    script = inject_product(script, product, mode)

    return script


# ─────────────────────────────────────────────────────────────────────────────
# Part 5 — Variation Engine (A/B)
# ─────────────────────────────────────────────────────────────────────────────

def generate_variants(base_script:  dict[str, str],
                       candidate:   dict[str, Any],
                       mode:        str,
                       product:     dict[str, Any] | None = None,
                       n:           int = 2) -> list[dict[str, str]]:
    """
    Generate n alternative script variants with different hook + CTA phrasing.
    Only called when expected_value > _VARIANT_MIN_EV.
    """
    intent = str(candidate.get("intent_type", "curiosity"))
    niche  = str(candidate.get("niche",       ""))
    cid    = str(candidate.get("content_id",  ""))

    hook_pool = _HOOK_TEMPLATES.get(intent, _HOOK_TEMPLATES["curiosity"])
    cta_modes = [mode] * n   # keep same monetization mode, vary phrasing only

    variants: list[dict[str, str]] = []
    for i in range(1, n + 1):
        v_seed    = f"var{i}:{cid}:{intent}:{niche}"
        v_hook    = _pick(hook_pool, v_seed, niche)
        v_cta_s   = f"var{i}:cta:{cid}:{intent}:{niche}:{mode}"
        v_cta     = generate_cta(mode, intent, product, niche, v_cta_s)

        variant: dict[str, str] = {
            "hook":   v_hook,
            "body":   base_script.get("body",   ""),
            "payoff": base_script.get("payoff", ""),
            "cta":    v_cta,
        }
        variant = inject_product(variant, product, mode)

        # Skip if identical to base (all templates exhausted)
        if variant["hook"] != base_script.get("hook") or variant["cta"] != base_script.get("cta"):
            variants.append(variant)

    return variants[:n]


# ─────────────────────────────────────────────────────────────────────────────
# CTA Placement + Structured CTA (Parts 1 – 2 of upgrade)
# ─────────────────────────────────────────────────────────────────────────────

_CTA_PLACEMENT: dict[str, dict[str, Any]] = {
    "direct": {
        "primary":   "video",
        "secondary": "comment",
        "strategy":  "Mention product in video; drop link in pinned comment.",
    },
    "indirect": {
        "primary":   "comment",
        "secondary": "bio",
        "strategy":  "Tease in video; reveal in first comment; bio as backup.",
    },
    "trust": {
        "primary":   None,
        "secondary": "follow",
        "strategy":  "No sell. Build audience. Retarget later.",
    },
}


def resolve_cta_placement(mode: str, intent: str) -> dict[str, Any]:
    """
    Returns placement config for primary + secondary CTA channels.
    Safe default: trust placement.
    """
    base = dict(_CTA_PLACEMENT.get(mode, _CTA_PLACEMENT["trust"]))
    # intent-aware annotation for downstream content routing
    base["intent"] = intent
    return base


# ── Structured CTA pools ───────────────────────────────────────────────────────

# video_cta: what appears / is said in the video itself
_VIDEO_CTA_DIRECT: dict[str, list[str]] = {
    "problem": [
        "This is what finally fixed it for me — link below.",
        "If you’re dealing with {niche} issues, what I used is linked.",
        "Stopped struggling the moment I switched. Link’s in the comments.",
    ],
    "desire": [
        "This is the {niche} upgrade I’ve been using. Link below.",
        "If you want the same results, it’s linked in my bio.",
        "Exactly what I use — link is below.",
    ],
    "curiosity": [
        "I linked exactly what I tested. Check below.",
        "Find it yourself — link is right below.",
        "Curious? It’s linked.",
    ],
}

_VIDEO_CTA_INDIRECT: dict[str, list[str]] = {
    "problem":   [
        "Drop a comment if you want what I used.",
        "I’ll share what helped me — comment below.",
        "Ask in comments and I’ll reply with what worked.",
    ],
    "desire":    [
        "Details are in my bio if you’re interested.",
        "I put together a list — it’s in my bio.",
        "Check my bio if you want the full breakdown.",
    ],
    "curiosity": [
        "I dropped the details below. Go check.",
        "More info in the link below.",
        "Check the first comment for what I found.",
    ],
}

# comment_cta: pinned / first comment content
_COMMENT_CTA_DIRECT: dict[str, list[str]] = {
    "problem":   ["👇 Link to what I used to fix this.",
                  "🔗 Grab it here before it sells out.",
                  "⬇️ What helped me: [link]"],
    "desire":    ["👇 The upgrade I mentioned: [link]",
                  "⬇️ Here’s the one I use: [link]",
                  "🔗 Link to the exact product."],
    "curiosity": ["👇 What I tested: [link]",
                  "⬇️ The thing I mentioned: [link]",
                  "🔗 Exact link here."],
}

_COMMENT_CTA_INDIRECT: dict[str, list[str]] = {
    "problem":   ["This is what actually helped me — [link]",
                  "For anyone asking — here’s what I found: [link]",
                  "Several people asked — here it is: [link]"],
    "desire":    ["Full list of what I use is here: [link]",
                  "Everything I mentioned is linked: [link]",
                  "Here’s the breakdown: [link]"],
    "curiosity": ["Here’s what I was talking about: [link]",
                  "More context here: [link]",
                  "The thing I hinted at: [link]"],
}

_BIO_CTA: list[str] = [
    "🔗 Everything I use and recommend.",
    "👇 My go-to tools and products.",
    "⬇️ Find everything I mention here.",
]


def generate_structured_cta(mode: str, intent: str,
                             niche: str = "", seed: str = "") -> dict[str, str | None]:
    """
    Returns per-channel CTA strings: video_cta, comment_cta, bio_cta.
    Deterministic. Sanitized. Never repeats across channels for same seed.
    """
    seed = seed or f"scta:{mode}:{intent}:{niche}"

    def _s(pool: list[str], salt: str) -> str:
        return _sanitize_cta(_pick(pool, seed + salt, niche))

    if mode == "direct":
        vpool = _VIDEO_CTA_DIRECT.get(intent, _VIDEO_CTA_DIRECT["curiosity"])
        cpool = _COMMENT_CTA_DIRECT.get(intent, _COMMENT_CTA_DIRECT["curiosity"])
        return {
            "video_cta":   _s(vpool,  ":v"),
            "comment_cta": _s(cpool,  ":c"),
            "bio_cta":     None,
        }
    elif mode == "indirect":
        vpool = _VIDEO_CTA_INDIRECT.get(intent, _VIDEO_CTA_INDIRECT["curiosity"])
        cpool = _COMMENT_CTA_INDIRECT.get(intent, _COMMENT_CTA_INDIRECT["curiosity"])
        bidx  = int(hashlib.sha256((seed + ":b").encode()).hexdigest()[:8], 16) % len(_BIO_CTA)
        return {
            "video_cta":   _s(vpool,  ":v"),
            "comment_cta": _s(cpool,  ":c"),
            "bio_cta":     _BIO_CTA[bidx],
        }
    else:  # trust
        tidx = int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) % len(_CTA_TRUST)
        return {
            "video_cta":   _CTA_TRUST[tidx],
            "comment_cta": None,
            "bio_cta":     None,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Funnel + Conversion Boost (Parts 3 – 4 of upgrade)
# ─────────────────────────────────────────────────────────────────────────────

_FUNNEL_TYPES: dict[str, list[str]] = {
    "direct":   ["video", "click", "buy"],
    "indirect": ["video", "comment", "profile", "click", "buy"],
    "trust":    ["video", "follow", "retarget"],
}


def _resolve_funnel_type(mode: str) -> str:
    """Returns the mode name; steps are stored in _FUNNEL_TYPES for routing."""
    return mode if mode in _FUNNEL_TYPES else "trust"


# Part 4: conversion boost suffixes keyed by intent
_BOOST_SUFFIX: dict[str, str] = {
    "problem":   " Don’t wait until it gets worse.",        # urgency
    "desire":    " This is the standard worth having.",      # aspiration
    "curiosity": " Most people haven’t seen this yet.",      # intrigue
}


def _apply_conversion_boost(cta: str, intent: str) -> str:
    """
    Appends a short intent-aligned micro-persuasion to video_cta only.
    Sanitized. No spam phrases.
    """
    suffix = _BOOST_SUFFIX.get(intent, "")
    if not suffix or suffix.lower().strip('. ') in cta.lower():
        return cta
    return _sanitize_cta(cta.rstrip() + suffix)


# ─────────────────────────────────────────────────────────────────────────────
# Primary Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def monetize(candidate: dict[str, Any]) -> dict[str, Any]:
    """
    Primary entry point. Returns full monetization package.

    Extended output (v2):
    {
        "script":            {hook, body, payoff, cta},
        "cta":               {video_cta, comment_cta, bio_cta},
        "cta_placement":     {primary, secondary, strategy, intent},
        "funnel_type":       "direct" | "indirect" | "trust",
        "monetization_mode": str,
        "product_used":      bool,
        "cta_type":          str,
        "variants":          list[dict],
    }
    """
    try:
        intent = str(candidate.get("intent_type", "curiosity"))
        niche  = str(candidate.get("niche", "")).lower()
        cid    = str(candidate.get("content_id",
                                   str(candidate.get("hook_text", ""))[:32]))
        mode   = resolve_mode(candidate)

        # Resolve product
        if mode == "direct":
            product = candidate.get("product") or candidate.get("best_product")
        elif mode == "indirect":
            product = candidate.get("recommended_product")
        else:
            product = None
        if not isinstance(product, dict):
            product = None

        # Base script (unchanged)
        script = generate_script(candidate, product, mode)

        # Part 1: CTA placement
        placement = resolve_cta_placement(mode, intent)

        # Part 2: Structured CTA (per-channel)
        scta_seed = f"scta:{cid}:{mode}:{intent}:{niche}"
        structured = generate_structured_cta(mode, intent, niche, scta_seed)

        # Part 4: conversion boost on video_cta only
        if structured.get("video_cta"):
            structured["video_cta"] = _apply_conversion_boost(
                structured["video_cta"], intent
            )

        # Part 3: funnel type
        funnel = _resolve_funnel_type(mode)

        # Part 5: variants only when EV justifies cost
        ev = float(candidate.get("expected_value", 0.0))
        variants: list[dict[str, str]] = []
        if ev > _VARIANT_MIN_EV:
            base_variants = generate_variants(script, candidate, mode, product, n=2)
            # Anti-repeat: drop any variant whose hook+cta matches base exactly
            used_hooks: set[str] = {script.get("hook", "")}
            used_ctas:  set[str] = {script.get("cta",  "")}
            for v in base_variants:
                if v.get("hook") not in used_hooks or v.get("cta") not in used_ctas:
                    variants.append(v)
                    used_hooks.add(v.get("hook", ""))
                    used_ctas.add(v.get("cta",  ""))

        return {
            "script":            script,
            "cta":               structured,
            "cta_placement":     placement,
            "funnel_type":       funnel,
            "monetization_mode": mode,
            "product_used":      product is not None,
            "cta_type":          mode,
            "variants":          variants,
        }

    except Exception:
        return {
            "script": {
                "hook":   "You need to see this...",
                "body":   "Here's what most people miss.",
                "payoff": "The difference is real.",
                "cta":    "Follow for more.",
            },
            "cta":               {"video_cta": "Follow for more.", "comment_cta": None, "bio_cta": None},
            "cta_placement":     _CTA_PLACEMENT["trust"],
            "funnel_type":       "trust",
            "monetization_mode": "trust",
            "product_used":      False,
            "cta_type":          "trust",
            "variants":          [],
        }
