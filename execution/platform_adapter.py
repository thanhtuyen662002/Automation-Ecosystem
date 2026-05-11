"""
execution/platform_adapter.py — Platform Content Adapter.

Transforms a generic content candidate into platform-specific format.
Different platforms need radically different content treatment:

TikTok:
  - Short punchy caption (≤ 150 chars)
  - Aggressive hook as first line
  - Trend hashtags (5–8 tags, prioritise #fyp)
  - Emoji-heavy to boost CTR
  - Short CTA: "Link in bio"

Facebook Reels:
  - Longer storytelling caption (150–400 chars)
  - Curiosity hook, then 2-sentence story
  - Softer CTA: "Comment below", "Save this"
  - 3–5 relevant hashtags (not spammy)
  - Emojis used sparingly

Public API:
    adapt(candidate, platform, niche)         → AdaptedContent
    adapt_batch(candidates, platform, niche)  → list[AdaptedContent]
    get_platform_rules(platform)              → PlatformRules

Config:
    ADAPTER_MAX_TIKTOK_CAPTION  : default 150
    ADAPTER_MAX_FB_CAPTION      : default 400
"""
from __future__ import annotations

import logging
import os
import random
import re
from dataclasses import dataclass, field
from typing import Any

LOGGER = logging.getLogger("execution.platform_adapter")

_MAX_TT_CAPTION = int(os.environ.get("ADAPTER_MAX_TIKTOK_CAPTION", "150"))
_MAX_FB_CAPTION = int(os.environ.get("ADAPTER_MAX_FB_CAPTION",     "400"))

# ── Platform rules ────────────────────────────────────────────────────────────

@dataclass
class PlatformRules:
    platform:         str
    max_caption_len:  int
    min_hashtags:     int
    max_hashtags:     int
    hook_style:       str     # "aggressive" | "storytelling"
    cta_style:        str     # "short" | "conversational"
    emoji_density:    str     # "high" | "medium" | "low"
    requires_story:   bool    = False
    fyp_boost:        bool    = False

_RULES: dict[str, PlatformRules] = {
    "tiktok": PlatformRules(
        platform="tiktok", max_caption_len=_MAX_TT_CAPTION,
        min_hashtags=5, max_hashtags=8, hook_style="aggressive",
        cta_style="short", emoji_density="high",
        fyp_boost=True, requires_story=False,
    ),
    "facebook": PlatformRules(
        platform="facebook", max_caption_len=_MAX_FB_CAPTION,
        min_hashtags=3, max_hashtags=5, hook_style="storytelling",
        cta_style="conversational", emoji_density="medium",
        fyp_boost=False, requires_story=True,
    ),
    "instagram": PlatformRules(
        platform="instagram", max_caption_len=220,
        min_hashtags=8, max_hashtags=15, hook_style="aggressive",
        cta_style="short", emoji_density="high",
        fyp_boost=False, requires_story=False,
    ),
}

# ── Hashtag libraries ─────────────────────────────────────────────────────────

_BOOST_TAGS: dict[str, list[str]] = {
    "tiktok":   ["fyp", "foryou", "foryoupage", "viral", "trending", "explore"],
    "facebook": ["reels", "trending", "viral", "explore"],
    "instagram":["explore", "reels", "viral", "trending", "instagood"],
}

_NICHE_TAGS: dict[str, list[str]] = {
    "tech":          ["tech", "coding", "programming", "software", "ai", "gadgets"],
    "fitness":       ["fitness", "workout", "gym", "healthylifestyle", "motivation", "exercise"],
    "finance":       ["money", "investing", "finance", "wealth", "passiveincome", "crypto"],
    "entertainment": ["entertainment", "funny", "comedy", "meme", "viralvideo", "lol"],
    "food":          ["food", "recipe", "cooking", "foodie", "delicious", "homecooking"],
    "travel":        ["travel", "adventure", "wanderlust", "explore", "vacation", "trip"],
}

# ── CTAs ──────────────────────────────────────────────────────────────────────

_CTAS_SHORT = [
    "Link in bio 🔗",
    "Tap the link above ⬆️",
    "Check link in bio",
    "Get it — link above",
    "Bio link = instant access",
]
_CTAS_CONVERSATIONAL = [
    "Drop a comment if this helped you 👇",
    "Save this for later — you'll need it!",
    "Share with someone who needs to see this",
    "Tell me in the comments which tip surprised you most",
    "Follow for more content like this every week",
    "Tag a friend who needs to hear this",
    "What's your take? Comment below 👇",
]
_CTAS_QUESTION = [
    "Which tip resonated most with you?",
    "Have you tried this before?",
    "What's your biggest struggle with this?",
    "Did this help? Let me know below!",
]

# ── Story bridges ─────────────────────────────────────────────────────────────

_STORY_BRIDGES = [
    "Here's what I learned the hard way:",
    "Nobody talks about this, but:",
    "I spent months figuring this out so you don't have to:",
    "The truth most people get wrong:",
    "What changed everything for me:",
    "The moment I understood this, everything clicked:",
]

# ── Emoji packs by density ────────────────────────────────────────────────────

_EMOJI_HIGH   = ["🔥", "💯", "⚡", "👀", "🚀", "💥", "✨", "🎯"]
_EMOJI_MEDIUM = ["✅", "💡", "🙌", "📌", "💬", "🔑"]
_EMOJI_LOW    = ["→", "•", "✓"]


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class AdaptedContent:
    content_id:    str
    platform:      str
    caption:       str
    hook:          str
    hashtags:      list[str]
    cta:           str
    niche:         str
    full_text:     str        = ""   # hook + caption + cta combined
    char_count:    int        = 0
    hashtag_count: int        = 0
    meta:          dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        parts = [p for p in [self.hook, self.caption, self.cta] if p]
        self.full_text     = "\n\n".join(parts)
        self.char_count    = len(self.full_text)
        self.hashtag_count = len(self.hashtags)


# ── Core adaptation ───────────────────────────────────────────────────────────

def _select_hashtags(
    platform: str, niche: str,
    existing_tags: list[str],
    rules: PlatformRules,
) -> list[str]:
    boost  = _BOOST_TAGS.get(platform, [])
    niche_ = _NICHE_TAGS.get(niche, [])
    pool   = (boost + niche_ + existing_tags)[:30]
    seen: set[str] = set()
    tags: list[str] = []
    for t in pool:
        clean = re.sub(r"[^a-zA-Z0-9_]", "", t).lower()
        if clean and clean not in seen:
            tags.append(clean)
            seen.add(clean)
    # Ensure FYP tag for TikTok
    if rules.fyp_boost and "fyp" not in seen:
        tags.insert(0, "fyp")
    return tags[:rules.max_hashtags]


def _build_tiktok_caption(
    hook:     str,
    caption:  str,
    niche:    str,
    rules:    PlatformRules,
) -> tuple[str, str, str]:
    """Returns (hook, body, cta)."""
    # TikTok: hook is first line, body is trimmed
    emoji = random.choice(_EMOJI_HIGH)
    clean_hook = hook.strip()
    if not clean_hook.endswith(tuple("!?🔥💀😂👀⚡")):
        clean_hook = f"{clean_hook} {emoji}"

    # Body: first sentence of caption, max 80 chars
    body = caption.split(".")[0].strip()
    if len(body) > 80:
        body = body[:77] + "..."

    cta = random.choice(_CTAS_SHORT)
    return clean_hook, body, cta


def _build_facebook_caption(
    hook:    str,
    caption: str,
    niche:   str,
    rules:   PlatformRules,
) -> tuple[str, str, str]:
    """Returns (hook, story_body, cta)."""
    # Facebook: hook → story bridge → 2-sentence body → CTA
    bridge = random.choice(_STORY_BRIDGES)
    emoji  = random.choice(_EMOJI_MEDIUM)

    # Clean hook: no aggressive all-caps, softer tone
    clean_hook = hook.strip().rstrip("!").rstrip(emoji) if emoji in hook else hook.strip()

    # Build story body from caption
    sentences = [s.strip() for s in re.split(r"[.!?]", caption) if len(s.strip()) > 15][:3]
    story     = " ".join(sentences)[:300]
    if not story:
        story = caption[:300]

    body = f"{bridge}\n\n{story}"
    cta  = random.choice(_CTAS_CONVERSATIONAL)
    return clean_hook, body, cta


def _build_instagram_caption(
    hook:    str,
    caption: str,
    niche:   str,
    rules:   PlatformRules,
) -> tuple[str, str, str]:
    emoji     = random.choice(_EMOJI_HIGH)
    clean_hook = f"{hook.strip()} {emoji}" if emoji not in hook else hook.strip()
    body       = caption[:220].rsplit(" ", 1)[0]
    cta        = random.choice(_CTAS_SHORT)
    return clean_hook, body, cta


def adapt(
    candidate: dict[str, Any],
    platform:  str,
    niche:     str = "",
) -> AdaptedContent:
    """
    Transform a content candidate dict into platform-optimised AdaptedContent.

    candidate must have at minimum: content_id, caption (or hook).
    Tries hook_optimizer for hook if not already present.
    Never raises.
    """
    try:
        rules      = _RULES.get(platform, _RULES["tiktok"])
        _niche     = niche or candidate.get("niche", "entertainment")
        content_id = candidate.get("content_id", "")
        raw_caption = candidate.get("caption", "") or candidate.get("source_url", "")
        raw_hook    = candidate.get("hook", "") or candidate.get("best_hook", "")

        # Generate hook if not provided
        if not raw_hook:
            try:
                from execution.hook_optimizer import optimize_hook
                hr       = optimize_hook(candidate, niche=_niche, platform=platform)
                raw_hook = hr.best_hook
            except Exception:
                raw_hook = raw_caption[:80]

        # Platform-specific formatting
        if platform == "tiktok":
            hook, body, cta = _build_tiktok_caption(raw_hook, raw_caption, _niche, rules)
        elif platform == "facebook":
            hook, body, cta = _build_facebook_caption(raw_hook, raw_caption, _niche, rules)
        else:
            hook, body, cta = _build_instagram_caption(raw_hook, raw_caption, _niche, rules)

        # Hashtags
        existing = candidate.get("hashtags", []) or []
        tags     = _select_hashtags(platform, _niche, existing, rules)
        tag_str  = " ".join(f"#{t}" for t in tags)

        # Final caption (body only — hook + cta go in AdaptedContent separately)
        caption_body = f"{body}\n\n{tag_str}"[:rules.max_caption_len]

        result = AdaptedContent(
            content_id = content_id,
            platform   = platform,
            caption    = caption_body,
            hook       = hook,
            hashtags   = tags,
            cta        = cta,
            niche      = _niche,
            meta       = {
                "rules":       rules.__dict__,
                "hook_source": "hook_optimizer" if not candidate.get("hook") else "candidate",
            },
        )
        LOGGER.debug(
            "adapted content_id=%s platform=%s chars=%d tags=%d",
            content_id, platform, result.char_count, result.hashtag_count,
        )
        return result

    except Exception as exc:
        LOGGER.warning("adapt_error content_id=%s error=%s", candidate.get("content_id"), exc)
        return AdaptedContent(
            content_id = candidate.get("content_id", ""),
            platform   = platform, niche=niche,
            caption    = candidate.get("caption", "")[:150],
            hook       = candidate.get("caption", "")[:80],
            hashtags   = [], cta = "Link in bio",
            meta       = {"error": str(exc)},
        )


def adapt_batch(
    candidates: list[dict[str, Any]],
    platform:   str,
    niche:      str = "",
) -> list[AdaptedContent]:
    """Adapt multiple candidates. Never raises."""
    return [adapt(c, platform, niche) for c in candidates]


def get_platform_rules(platform: str) -> PlatformRules:
    """Return platform constraint rules."""
    return _RULES.get(platform, _RULES["tiktok"])
