"""
Handler: tiktok.generate_comment
──────────────────────────────────
Reads from parent generate_content result:
  caption (for tone alignment)

Also reads from ancestor extract_product_info result:
  title, keywords

Input payload:
  count:  int = 3   – number of comments to generate (2–5)

Output result:
  comments: list[str]   – 2–5 natural, emoji-inclusive comments
  ok:       bool
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

LOGGER = logging.getLogger("workers.handlers.tiktok.generate_comment")

_COMMENT_PERSONAS = [
    "a curious shopper who just discovered this product",
    "a loyal customer who already bought and loves it",
    "a friend tagging another friend who might need this",
    "someone who's impressed and wants to try it",
    "a skeptic who was pleasantly surprised",
]

_EMOJI_SETS = [
    ["🔥", "😍", "💯", "👏"],
    ["✨", "💫", "🙌", "❤️"],
    ["😮", "👀", "🤩", "💪"],
    ["🛒", "😊", "💰", "⭐"],
]


async def generate_comment_handler(payload: dict[str, Any]) -> dict[str, Any]:
    # ── Idempotency guard ─────────────────────────────────────────────────────
    if (cached := check_already_processed(payload)) is not None:
        return cached

    # ── Resolve inputs ────────────────────────────────────────────────────────
    count: int = max(2, min(5, int(payload.get("count", 3))))

    try:
        caption: str = str(resolve_parent_result(payload, "caption")).strip()
    except KeyError:
        caption = str(payload.get("caption", "")).strip()

    try:
        title: str = str(resolve_parent_result(payload, "title")).strip()
    except KeyError:
        title = str(payload.get("title", "this product")).strip()

    try:
        keywords: list[str] = list(resolve_parent_result(payload, "keywords"))
    except KeyError:
        keywords = list(payload.get("keywords", []))

    # Anti-duplication: random personas + emojis for each run
    selected_personas = random.sample(_COMMENT_PERSONAS, min(count, len(_COMMENT_PERSONAS)))
    emoji_hints = random.choice(_EMOJI_SETS)
    seed = random.randint(0, 99999)

    LOGGER.info(
        "generate_comment_start",
        extra={"event": "generate_comment_start", "count": count, "seed": seed},
    )

    await random_jitter(0.5, 2.0)

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=get_openai_api_key())
    model = get_openai_model()

    personas_text = "\n".join(f"  {i+1}. Persona: {p}" for i, p in enumerate(selected_personas))
    emojis_text = " ".join(emoji_hints)

    system_prompt = (
        "You are a social media manager writing authentic, natural-sounding TikTok comments. "
        "Comments must feel like they're from real users — NOT marketing copy. "
        "Use casual language, typos occasionally, and emojis naturally. "
        "Return ONLY a valid JSON object with exactly one key:\n"
        '  "comments": [array of comment strings]\n'
        "Do not include any text outside the JSON object."
    )

    user_prompt = (
        f"Product: {title}\n"
        f"TikTok caption context: {caption[:300]}\n"
        f"Keywords: {', '.join(keywords[:6])}\n\n"
        f"Write {count} different comments. Each persona and suggested emojis:\n"
        f"{personas_text}\n"
        f"Suggested emojis to sprinkle naturally: {emojis_text}\n"
        f"Keep each comment under 120 characters. (seed={seed})"
    )

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=1.15,
        max_tokens=400,
    )

    raw_text = (response.choices[0].message.content or "").strip()
    parsed = _parse_json_response(raw_text)

    comments: list[str] = [str(c).strip() for c in parsed.get("comments", []) if c]

    # Clamp each comment
    comments = [c[:150] for c in comments if c]

    if not comments:
        raise RuntimeError("LLM returned empty comments — retry may resolve this")

    result = {
        "comments": comments[:5],
        "ok": True,
    }

    LOGGER.info(
        "generate_comment_done",
        extra={"event": "generate_comment_done", "count": len(comments)},
    )
    return result
