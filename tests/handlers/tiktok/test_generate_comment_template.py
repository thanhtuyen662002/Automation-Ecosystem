"""
Unit tests for the template-based generate_comment handler.

Verifies:
  - No AI calls are made (generate_text is never imported/called)
  - 3–5 comments are returned
  - {title} placeholder is substituted in rendered templates
  - Result schema is correct
  - Idempotency guard works
"""

from __future__ import annotations

import pytest
from unittest.mock import patch


@pytest.mark.asyncio
async def test_no_ai_call_made():
    """generate_comment_handler must never import or call generate_text."""
    import workers.handlers.tiktok.generate_comment as mod
    # generate_text must not be an attribute of the module at all
    assert not hasattr(mod, "generate_text"), (
        "generate_text must not be imported in generate_comment module"
    )


@pytest.mark.asyncio
async def test_returns_correct_count():
    """Handler returns 3–5 comments."""
    from workers.handlers.tiktok.generate_comment import generate_comment_handler

    for requested_count in [3, 4, 5]:
        result = await generate_comment_handler({
            "title": "Magic Serum",
            "count": requested_count,
        })
        assert result["ok"] is True
        assert len(result["comments"]) == requested_count


@pytest.mark.asyncio
async def test_title_substitution():
    """Product title should appear in at least some rendered comments."""
    from workers.handlers.tiktok.generate_comment import generate_comment_handler

    title = "UltraGlow Cream"
    # Run several times to get varied template selection
    found_title = False
    for _ in range(10):
        result = await generate_comment_handler({"title": title, "count": 5})
        if any(title in c for c in result["comments"]):
            found_title = True
            break
    assert found_title, "Product title was never substituted in any comment across 10 runs"


@pytest.mark.asyncio
async def test_comment_max_length():
    """All comments must be ≤ 150 characters."""
    from workers.handlers.tiktok.generate_comment import generate_comment_handler

    result = await generate_comment_handler({"title": "SomeProduct", "count": 5})
    for c in result["comments"]:
        assert len(c) <= 150, f"Comment too long ({len(c)}): {c!r}"


@pytest.mark.asyncio
async def test_idempotency_guard():
    """If _idempotent_result is present, handler returns it immediately."""
    from workers.handlers.tiktok.generate_comment import generate_comment_handler

    cached = {"comments": ["cached comment"], "ok": True}
    result = await generate_comment_handler({"_idempotent_result": cached})
    assert result == cached


@pytest.mark.asyncio
async def test_result_schema():
    """Result must contain 'comments' (list[str]) and 'ok' (True)."""
    from workers.handlers.tiktok.generate_comment import generate_comment_handler

    result = await generate_comment_handler({"title": "Test Product"})
    assert isinstance(result["comments"], list)
    assert all(isinstance(c, str) for c in result["comments"])
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_cost_is_zero():
    """Logged cost must be 0 (no AI calls)."""
    import logging
    from workers.handlers.tiktok.generate_comment import generate_comment_handler

    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Capture()
    logger = logging.getLogger("workers.handlers.tiktok.generate_comment")
    orig_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        await generate_comment_handler({"title": "Widget"})
    finally:
        logger.removeHandler(handler)
        logger.setLevel(orig_level)

    done_records = [r for r in records if getattr(r, "event", None) == "generate_comment_done"]
    assert done_records, "generate_comment_done event not logged"
    rec = done_records[0]
    assert rec.__dict__.get("ai_calls_used") == 0
    assert rec.__dict__.get("estimated_cost_usd") == 0.0
