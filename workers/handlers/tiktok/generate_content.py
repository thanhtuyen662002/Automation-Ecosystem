"""
Handler: tiktok.generate_content
──────────────────────────────────
Reads from parent extract_product_info result:
  title, description, keywords

Output result:
  caption:    str   – hook sentence + body + CTA (≤ 2200 chars for TikTok)
  hashtags:   list[str]   – 8–15 tags without leading #
  ok:         bool
"""

from __future__ import annotations

import logging
import random
from typing import Any

from workers.handlers.tiktok._base import (
    check_already_processed,
    get_openai_api_key,
    get_openai_model,
    random_jitter,
    resolve_parent_result,
)
from workers.handlers.tiktok.extract_product_info import _parse_json_response

LOGGER = logging.getLogger("workers.handlers.tiktok.generate_content")

_CAPTION_STYLES = [
    "casual and energetic",
    "professional and persuasive",
    "witty and playful",
    "storytelling and emotional",
]

_CTA_OPTIONS = [
    "Drop a comment below 👇",
    "Follow for more! 🔥",
    "Save this for later 📌",
    "Tag a friend who needs this! 👀",
    "Link in bio for more info ✨",
]


async def generate_content_handler(payload: dict[str, Any]) -> dict[str, Any]:
    # ── Idempotency guard ─────────────────────────────────────────────────────
    if (cached := check_already_processed(payload)) is not None:
        return cached

    # ── Resolve product info from parent result ───────────────────────────────
    try:
        title: str = str(resolve_parent_result(payload, "title")).strip()
        description: str = str(resolve_parent_result(payload, "description")).strip()
        keywords: list[str] = list(resolve_parent_result(payload, "keywords"))
    except KeyError:
        title = str(payload.get("title", "")).strip()
        description = str(payload.get("description", "")).strip()
        keywords = list(payload.get("keywords", []))

    if not title:
        raise ValueError("generate_content requires 'title' from parent extract_product_info result")

    # Anti-duplication: randomly pick style + CTA
    style = random.choice(_CAPTION_STYLES)
    cta = random.choice(_CTA_OPTIONS)
    seed = random.randint(0, 99999)

    LOGGER.info(
        "generate_content_start",
        extra={"event": "generate_content_start", "style": style, "seed": seed},
    )

    await random_jitter(0.5, 2.0)

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=get_openai_api_key())
    model = get_openai_model()

    system_prompt = (
        "You are a viral TikTok copywriter specializing in product promotions. "
        f"Your writing style today is: {style}. "
        "Return ONLY a valid JSON object with exactly these keys:\n"
        "  caption    (string, ≤2000 chars — hook + 2–3 body sentences + CTA)\n"
        "  hashtags   (array of 8–15 strings WITHOUT the # prefix)\n"
        "The caption MUST start with a compelling hook (question or bold statement), "
        "and end with this exact CTA: " + cta + "\n"
        "Do not include any text outside the JSON object."
    )

    user_prompt = (
        f"Product: {title}\n"
        f"Description: {description}\n"
        f"Keywords: {', '.join(keywords[:10])}\n\n"
        f"Generate an engaging TikTok caption and hashtag set for this product. "
        f"(seed={seed})"  # Forces model to vary output
    )

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=1.1,
        max_tokens=800,
    )

    raw_text = (response.choices[0].message.content or "").strip()
    parsed = _parse_json_response(raw_text)

    caption: str = str(parsed.get("caption", "")).strip()
    hashtags: list[str] = [
        str(h).strip().lstrip("#") for h in parsed.get("hashtags", []) if h
    ]

    if not caption:
        raise RuntimeError("LLM returned empty caption — retry may resolve this")

    # Clamp caption length (TikTok max is ~2200 chars)
    if len(caption) > 2200:
        caption = caption[:2197] + "..."

    # Ensure CTA is present
    if cta not in caption:
        caption = caption.rstrip() + f"\n\n{cta}"

    result = {
        "caption": caption,
        "hashtags": hashtags[:15],
        "ok": True,
    }

    LOGGER.info(
        "generate_content_done",
        extra={
            "event": "generate_content_done",
            "caption_length": len(caption),
            "hashtag_count": len(hashtags),
        },
    )
    return result
