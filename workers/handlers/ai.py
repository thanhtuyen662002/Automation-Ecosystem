from __future__ import annotations

import hashlib
from typing import Any


async def ai_handler(payload: dict[str, Any]) -> dict[str, Any]:
    prompt = str(payload.get("prompt") or payload.get("input") or "").strip()
    if not prompt:
        raise ValueError("ai task requires payload.prompt or payload.input")
    max_chars = int(payload.get("max_chars", 280))
    if max_chars < 1:
        raise ValueError("payload.max_chars must be >= 1")

    normalized = " ".join(prompt.split())
    generated = _generate_text(normalized, max_chars)
    return {
        "handler": "ai",
        "input_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "text": generated,
        "token_estimate": max(1, len(generated.split())),
        "ok": True,
    }


def _generate_text(prompt: str, max_chars: int) -> str:
    prefix = "Generated response:"
    text = f"{prefix} {prompt}"
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip()
