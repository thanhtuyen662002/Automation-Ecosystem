"""
Integration helper: push a ContentPlan + MediaResult into the Review UI queue.

Usage:
    from api.review_ui.enqueue import enqueue_to_review
    enqueue_to_review(plan, media_result)

Or call the HTTP endpoint POST /enqueue directly.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.content_engine import ContentPlan
    from core.media_generator import MediaResult

LOGGER = logging.getLogger("review_ui.enqueue")


def enqueue_to_review(
    plan:   "ContentPlan",
    result: "MediaResult",
) -> dict:
    """
    Push a finished ContentPlan + MediaResult directly into the JSON queue.

    Imports the queue store directly (no HTTP round-trip needed when running
    in the same process as the review UI).
    """
    from api.review_ui.app import _load_queue, _save_queue
    import time
    import uuid

    # Build caption from hook step
    hook_text = next(
        (s.text for s in plan.script if s.role == "hook"),
        plan.script[0].text if plan.script else "",
    )

    # Build overlay from CTA step
    cta_text = next(
        (s.text for s in plan.script if s.role == "cta"),
        "",
    )

    plan_summary = {
        "mode":        plan.mode.value,
        "type":        plan.content_type.value,
        "template_id": plan.template_id,
        "duration":    plan.duration,
        "pattern_key": plan.pattern_key,
        "source":      plan.source[:80],
        "script_steps":len(plan.script),
    }

    item = {
        "id":           str(uuid.uuid4()),
        "status":       "pending",
        "account_id":   plan.account_id,
        "caption":      hook_text,
        "text_overlay": cta_text,
        "video_path":   result.video_path,
        "images":       result.images,
        "template_id":  plan.template_id,
        "mode":         plan.mode.value,
        "content_type": plan.content_type.value,
        "duration":     plan.duration,
        "plan_summary": plan_summary,
        "created_at":   time.time(),
        "updated_at":   time.time(),
    }

    items = _load_queue()
    items.append(item)
    _save_queue(items)

    LOGGER.info("enqueued_to_review", extra={
        "id":         item["id"],
        "account_id": plan.account_id,
        "video_path": result.video_path,
    })
    return item
