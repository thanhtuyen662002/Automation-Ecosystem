from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("workers.handlers.media")


async def media_handler(payload: dict[str, Any]) -> dict[str, Any]:
    # ── Content Decision Gate (MANDATORY before any render) ───────────────────
    _decision_guard(payload, mode="reup")

    input_path_raw = str(payload.get("input_path") or "").strip()
    if not input_path_raw:
        raise ValueError("media task requires payload.input_path")
    input_path = Path(input_path_raw).expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"input_path does not exist: {input_path}")
    if not input_path.is_file():
        raise ValueError(f"input_path is not a file: {input_path}")

    stat_result = await asyncio.to_thread(input_path.stat)
    checksum = await asyncio.to_thread(_sha256_file, input_path)
    return {
        "handler": "media",
        "input_path": str(input_path),
        "file_name": input_path.name,
        "size_bytes": stat_result.st_size,
        "modified_at": stat_result.st_mtime,
        "sha256": checksum,
        "ok": True,
    }


def _decision_guard(payload: dict[str, Any], mode: str) -> None:
    """
    Worker-level content decision gate.

    Reads optional decision signals from payload["decision_signals"].
    Calls should_produce() and raises ValueError if blocked.

    Non-fatal if content_decision import fails — gate errors are logged and
    execution continues (fail-open to preserve existing behaviour).
    """
    try:
        from core.content_decision import ContentCandidate, should_produce
        signals = payload.get("decision_signals") or {}
        item_id = str(payload.get("job_id") or payload.get("item_id") or "media_job")
        candidate = ContentCandidate(
            item_id         = item_id,
            trend_score     = float(signals.get("trend_score",    0.5)),
            product_intent  = float(signals.get("product_intent", 0.5)),
            hook_potential  = float(signals.get("hook_potential", -1.0)),
            match_score     = float(signals.get("match_score",    0.5)),
            novelty_score   = float(signals.get("novelty_score",  0.5)),
            production_cost = float(signals.get("production_cost", 0.5)),
            metadata        = dict(signals.get("metadata") or {}),
        )
        niche = str(signals.get("niche", ""))
        allowed, reason = should_produce(candidate, mode=mode, niche=niche)
        if not allowed:
            LOGGER.info(
                "media_handler_skipped item=%s mode=%s reason=%s",
                item_id, mode, reason,
            )
            raise ValueError(f"content_decision BLOCKED [{mode}]: {reason}")
    except ValueError:
        raise
    except Exception as exc:
        LOGGER.debug("media_decision_gate_error (non-fatal): %s", exc)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
