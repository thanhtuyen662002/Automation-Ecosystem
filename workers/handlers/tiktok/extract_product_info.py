"""
Handler: tiktok.extract_product_info
─────────────────────────────────────
Input payload:
  product_url:         str | None  – public product page URL
  product_image_path:  str | None  – local path to a product image
  (At least one of the above must be provided.)

Output result:
  title:        str
  description:  str
  keywords:     list[str]
  ok:           bool
"""

from __future__ import annotations

import base64
import logging
import os
import re
from pathlib import Path
from typing import Any

from workers.handlers.tiktok._base import (
    check_already_processed,
    fetch_url_text,
    get_openai_api_key,
    get_openai_model,
    random_jitter,
)

LOGGER = logging.getLogger("workers.handlers.tiktok.extract_product_info")


async def extract_product_info_handler(payload: dict[str, Any]) -> dict[str, Any]:
    # ── Idempotency guard ─────────────────────────────────────────────────────
    if (cached := check_already_processed(payload)) is not None:
        return cached

    product_url: str | None = payload.get("product_url") or None
    product_image_path: str | None = payload.get("product_image_path") or None

    if not product_url and not product_image_path:
        raise ValueError("extract_product_info requires 'product_url' or 'product_image_path'")

    await random_jitter(0.5, 2.0)

    LOGGER.info(
        "extract_product_info_start",
        extra={
            "event": "extract_product_info_start",
            "has_url": bool(product_url),
            "has_image": bool(product_image_path),
        },
    )

    # ── Build OpenAI messages ─────────────────────────────────────────────────
    from openai import AsyncOpenAI  # imported lazily to keep startup fast

    client = AsyncOpenAI(api_key=get_openai_api_key())
    model = get_openai_model()

    system_prompt = (
        "You are a product analyst. Extract key information from the product page or image provided. "
        "Return ONLY a valid JSON object with exactly these keys:\n"
        "  title        (string, ≤80 chars)\n"
        "  description  (string, 2–4 sentences)\n"
        "  keywords     (array of 5–10 short marketing keywords)\n"
        "Do not include any other text outside the JSON object."
    )

    user_content: list[dict[str, Any]] = []

    if product_image_path:
        image_path = Path(product_image_path).expanduser().resolve()
        if not image_path.exists():
            raise FileNotFoundError(f"product_image_path does not exist: {image_path}")
        image_bytes = image_path.read_bytes()
        b64 = base64.standard_b64encode(image_bytes).decode()
        suffix = image_path.suffix.lower().lstrip(".")
        mime = f"image/{suffix if suffix in ('png', 'jpg', 'jpeg', 'webp', 'gif') else 'jpeg'}"
        user_content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "high"},
            }
        )

    if product_url:
        try:
            page_text = await fetch_url_text(product_url)
            # Trim to first 8000 chars to stay within context window
            page_text = page_text[:8000]
        except Exception as exc:
            LOGGER.warning(
                "fetch_url_failed",
                extra={"event": "fetch_url_failed", "url": product_url, "error": str(exc)},
            )
            page_text = f"Product URL: {product_url}"

        user_content.append({"type": "text", "text": f"Product page content:\n\n{page_text}"})

    if not user_content:
        user_content.append({"type": "text", "text": "Analyze the product and return the JSON."})

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    response = await client.chat.completions.create(
        model=model,
        messages=messages,  # type: ignore[arg-type]
        temperature=0.3,
        max_tokens=512,
    )

    raw_text = (response.choices[0].message.content or "").strip()
    parsed = _parse_json_response(raw_text)

    title: str = str(parsed.get("title", "")).strip() or "Unknown Product"
    description: str = str(parsed.get("description", "")).strip()
    keywords: list[str] = [str(k).strip() for k in parsed.get("keywords", []) if k]

    if not keywords:
        # Fallback: derive from title
        keywords = [w.lower() for w in title.split() if len(w) > 3][:8]

    result = {
        "title": title,
        "description": description,
        "keywords": keywords,
        "ok": True,
    }

    LOGGER.info(
        "extract_product_info_done",
        extra={
            "event": "extract_product_info_done",
            "title": title,
            "keyword_count": len(keywords),
        },
    )
    return result


def _parse_json_response(text: str) -> dict[str, Any]:
    """Extract and parse the first JSON object found in text."""
    import json

    # Strip markdown code fences if present
    text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()

    # Find first {...}
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # Last resort
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        LOGGER.warning(
            "json_parse_failed",
            extra={"event": "json_parse_failed", "raw_text_preview": text[:200]},
        )
        return {}
