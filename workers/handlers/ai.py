from __future__ import annotations

import hashlib
import logging
from typing import Any

LOGGER = logging.getLogger("workers.handlers.ai")


async def ai_handler(payload: dict[str, Any]) -> dict[str, Any]:
    # ── Content Decision Gate (MANDATORY before AI generation) ────────────────
    _decision_guard(payload, mode="generate")

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


def _decision_guard(payload: dict[str, Any], mode: str) -> None:
    """
    Worker-level EV gate before any AI API call.

    Reads payload["decision_signals"] for scoring context.
    Raises ValueError if the candidate fails should_produce().
    Fail-open on import errors — never blocks on infrastructure issues.
    """
    try:
        from core.content_decision import ContentCandidate, should_produce
        signals = payload.get("decision_signals") or {}
        item_id = str(payload.get("job_id") or payload.get("item_id") or "ai_job")
        candidate = ContentCandidate(
            item_id         = item_id,
            trend_score     = float(signals.get("trend_score",    0.5)),
            product_intent  = float(signals.get("product_intent", 0.5)),
            hook_potential  = float(signals.get("hook_potential", -1.0)),
            match_score     = float(signals.get("match_score",    0.5)),
            novelty_score   = float(signals.get("novelty_score",  0.5)),
            production_cost = float(signals.get("production_cost", 0.8)),  # AI = expensive
            metadata        = dict(signals.get("metadata") or {}),
        )
        niche = str(signals.get("niche", ""))
        allowed, reason = should_produce(candidate, mode=mode, niche=niche)
        if not allowed:
            LOGGER.info(
                "ai_handler_skipped item=%s mode=%s reason=%s",
                item_id, mode, reason,
            )
            raise ValueError(f"content_decision BLOCKED [{mode}]: {reason}")
    except ValueError:
        raise
    except Exception as exc:
        LOGGER.debug("ai_decision_gate_error (non-fatal): %s", exc)


def _generate_text(prompt: str, max_chars: int) -> str:
    prefix = "Generated response:"
    text = f"{prefix} {prompt}"
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip()
