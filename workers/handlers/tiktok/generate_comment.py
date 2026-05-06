"""
Handler: tiktok.generate_comment
──────────────────────────────────
Reads from parent generate_content result:
  caption (for tone alignment)
  hook    (for context)

Also reads from ancestor extract_product_info result:
  title, keywords

Input payload:
  count:  int = random 3–5 per run

Output result:
  comments: list[str]  – 3–5 natural, emoji-inclusive social-proof comments
  ok:       bool

COST OPTIMISATION: Zero AI calls.
Comments are generated from a 28-entry curated template bank, randomly
sampled and lightly personalised with the product title.
"""

from __future__ import annotations

import logging
import random
import string
from typing import Any

from workers.handlers.tiktok._base import (
    check_already_processed,
    resolve_parent_result,
)

LOGGER = logging.getLogger("workers.handlers.tiktok.generate_comment")


# ── Template bank (28 entries, 4 categories) ──────────────────────────────────
# {title} is substituted at runtime with the product name.
# Templates intentionally vary in length, capitalization, and emoji density
# to simulate organic comment diversity.

_TEMPLATES: dict[str, list[str]] = {
    "fake_user_experience": [
        "I bought this and it actually works 😭 why did i wait so long",
        "ordered {title} last week and omg the quality is insane 🔥",
        "been using {title} for 2 weeks, not going back 💯",
        "ngl i was skeptical but {title} delivered fr fr 🤌",
        "my friend got this first and now i had to get one too lol",
        "5 stars ✨ received it faster than expected and it's perfect",
        "okay {title} actually exceeded my expectations 😍",
        "i've tried so many similar products, this one is different 👏",
    ],
    "curiosity_trigger": [
        "wait this is legit?? 👀",
        "hold on i need to know more about this 😮",
        "okay but WHY am i just finding this now",
        "bro this showed up on my fyp at the perfect time 😭",
        "is this actually real or am i being pranked rn",
        "wait WHAT 🤩 adding to cart immediately",
        "someone explain why this isn't everywhere yet",
        "okay i've watched this 4 times now 💀",
    ],
    "soft_cta": [
        "sending this to my bestie 😭",
        "bookmarking this rn 📌",
        "tagging my sister in this immediately 😂",
        "saving for when i get paid lmaooo 💸",
        "link đâu vậy??",
        "mua ở đâu vậy bạn ơi",
        "đang tìm cái này mãi 🫶",
    ],
    "question": [
        "does this actually work for beginners?",
        "how long until you see results?",
        "is {title} worth the price tho?",
        "where do you even get this from??",
        "bao nhiêu tiền vậy?",
    ],
}

# Flat list for weighted random draw (categories stay balanced)
_ALL_TYPES: list[str] = list(_TEMPLATES.keys())

# Urgency / trend phrases injected post-selection for extra variety
_URGENCY_SUFFIXES = [
    "",   # no suffix — keeps ~50% of comments clean
    "",
    " 🔥",
    " ✨",
    " 💯",
]


def _render(template: str, title: str) -> str:
    """Substitute {title} placeholder and lightly randomise capitalisation."""
    text = template.replace("{title}", title)
    # Random chance to capitalise first letter differently (feels organic)
    if text and random.random() < 0.3:
        text = text[0].upper() + text[1:]
    return text + random.choice(_URGENCY_SUFFIXES)


# ── Handler ───────────────────────────────────────────────────────────────────

async def generate_comment_handler(payload: dict[str, Any]) -> dict[str, Any]:
    # ── Idempotency guard ─────────────────────────────────────────────────────
    if (cached := check_already_processed(payload)) is not None:
        return cached

    # ── Resolve inputs ────────────────────────────────────────────────────────
    count: int = max(3, min(5, int(payload.get("count", random.randint(3, 5)))))

    try:
        title: str = str(resolve_parent_result(payload, "title")).strip()
    except KeyError:
        title = str(payload.get("title", "this product")).strip()

    LOGGER.info(
        "generate_comment_start",
        extra={
            "event": "generate_comment_start",
            "count": count,
            "title": title,
            "source": "template",
        },
    )

    # ── Sample comment types (balanced across categories) ─────────────────────
    # Pick at least one from each primary category, then fill remaining slots
    primary = ["fake_user_experience", "curiosity_trigger", "soft_cta"]
    selected_types: list[str] = random.sample(primary, min(count, len(primary)))
    remaining = count - len(selected_types)
    if remaining > 0:
        selected_types += random.choices(_ALL_TYPES, k=remaining)
    random.shuffle(selected_types)

    # ── Render templates ──────────────────────────────────────────────────────
    comments: list[str] = []
    used_templates: set[str] = set()  # de-duplicate within a single run

    for ctype in selected_types:
        pool = _TEMPLATES[ctype]
        candidates = [t for t in pool if t not in used_templates]
        if not candidates:
            candidates = pool  # allow repeat if pool exhausted
        chosen = random.choice(candidates)
        used_templates.add(chosen)
        rendered = _render(chosen, title)[:150]
        comments.append(rendered)

    result = {
        "comments": comments,
        "ok": True,
    }

    LOGGER.info(
        "generate_comment_done",
        extra={
            "event": "generate_comment_done",
            "count": len(comments),
            "types": selected_types,
            "source": "template",
            "ai_calls_used": 0,
            "estimated_cost_usd": 0.0,
        },
    )
    return result
