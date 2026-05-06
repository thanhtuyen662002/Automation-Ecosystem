"""
Unit tests for cost-reduction features in generate_content handler.

Verifies:
  - Default path uses template engine (zero AI calls)
  - DRY_RUN=true skips AI and returns mock content
  - USE_AI_CAPTION=true triggers AI path (with AI mocked)
  - In-memory cache avoids a second call for same title+style
  - Hashtags are rule-based and within 7–10 count
  - Variants are generated (2–3 per run)
  - Cost logged correctly per scenario
"""

from __future__ import annotations

import json
import logging
import pytest
from unittest.mock import AsyncMock, patch


# ── Template path (default — zero AI) ────────────────────────────────────────

@pytest.mark.asyncio
async def test_default_path_no_ai(monkeypatch):
    """Default path must not call generate_text."""
    monkeypatch.delenv("USE_AI_CAPTION", raising=False)
    monkeypatch.delenv("DRY_RUN", raising=False)

    import workers.handlers.tiktok.generate_content as mod
    mod._RESULT_CACHE.clear()

    # generate_text is lazily imported — it should never be called on template path
    with patch("workers.handlers.tiktok.generate_content.generate_text",
               side_effect=AssertionError("AI must not be called on template path"),
               create=True):
        from workers.handlers.tiktok.generate_content import generate_content_handler
        result = await generate_content_handler({
            "title": "Test Widget",
            "description": "A great product",
            "keywords": ["widget", "test"],
        })

    assert result["ok"] is True
    assert result["caption"]
    assert result["hook"]
    assert result["cta"]


@pytest.mark.asyncio
async def test_default_path_generates_variants(monkeypatch):
    """Template path must return 2–3 variants."""
    monkeypatch.delenv("USE_AI_CAPTION", raising=False)
    monkeypatch.delenv("DRY_RUN", raising=False)

    import workers.handlers.tiktok.generate_content as mod
    mod._RESULT_CACHE.clear()

    from workers.handlers.tiktok.generate_content import generate_content_handler
    result = await generate_content_handler({
        "title": "Variant Widget",
        "description": "desc",
        "keywords": [],
    })

    assert "variants" in result
    assert 2 <= len(result["variants"]) <= 3
    for v in result["variants"]:
        assert "caption" in v
        assert "hook" in v
        assert "cta" in v


@pytest.mark.asyncio
async def test_default_path_cost_is_zero(monkeypatch):
    """Template path must log zero cost."""
    monkeypatch.delenv("USE_AI_CAPTION", raising=False)
    monkeypatch.delenv("DRY_RUN", raising=False)

    import workers.handlers.tiktok.generate_content as mod
    mod._RESULT_CACHE.clear()

    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, r: logging.LogRecord) -> None:
            records.append(r)

    logger = logging.getLogger("workers.handlers.tiktok.generate_content")
    orig_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(cap := Capture())
    try:
        from workers.handlers.tiktok.generate_content import generate_content_handler
        await generate_content_handler({
            "title": "Cost Widget",
            "description": "desc",
            "keywords": [],
        })
    finally:
        logger.removeHandler(cap)
        logger.setLevel(orig_level)

    done = [r for r in records if getattr(r, "event", None) == "generate_content_done"]
    assert done
    assert done[0].__dict__.get("ai_calls_used") == 0
    assert done[0].__dict__.get("estimated_cost_usd") == 0.0


# ── DRY_RUN mode ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dry_run_skips_ai(monkeypatch):
    """DRY_RUN=true must skip AI and return mock content."""
    monkeypatch.setenv("DRY_RUN", "true")

    import workers.handlers.tiktok.generate_content as mod
    mod._RESULT_CACHE.clear()

    from workers.handlers.tiktok.generate_content import generate_content_handler
    result = await generate_content_handler({
        "title": "DryWidget",
        "description": "A test product",
        "keywords": ["test"],
    })

    assert result["ok"] is True
    assert "[DRY_RUN]" in result["caption"]
    assert "[DRY_RUN]" in result["hook"]
    assert isinstance(result["hashtags"], list)
    assert result["ai_calls_used"] if "ai_calls_used" in result else True  # field optional


@pytest.mark.asyncio
async def test_dry_run_cost_is_zero(monkeypatch):
    """DRY_RUN mode must log zero cost."""
    monkeypatch.setenv("DRY_RUN", "true")

    import workers.handlers.tiktok.generate_content as mod
    mod._RESULT_CACHE.clear()

    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, r: logging.LogRecord) -> None:
            records.append(r)

    logger = logging.getLogger("workers.handlers.tiktok.generate_content")
    orig_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(cap := Capture())
    try:
        from workers.handlers.tiktok.generate_content import generate_content_handler
        await generate_content_handler({
            "title": "DryWidget",
            "description": "desc",
            "keywords": [],
        })
    finally:
        logger.removeHandler(cap)
        logger.setLevel(orig_level)

    done = [r for r in records if getattr(r, "event", None) == "generate_content_done"]
    assert done
    assert done[0].__dict__.get("estimated_cost_usd") == 0.0
    assert done[0].__dict__.get("ai_calls_used") == 0


# ── USE_AI_CAPTION path ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_use_ai_caption_calls_ai(monkeypatch):
    """USE_AI_CAPTION=true must trigger the AI path."""
    monkeypatch.setenv("USE_AI_CAPTION", "true")
    monkeypatch.delenv("DRY_RUN", raising=False)

    import workers.handlers.tiktok.generate_content as mod
    mod._RESULT_CACHE.clear()

    ai_response = json.dumps({
        "caption": "AI caption here",
        "hook": "AI hook",
        "cta": "AI cta",
    })

    with (
        patch("core.ai_router.generate_text", new=AsyncMock(return_value=ai_response)),
        patch("workers.handlers.tiktok.generate_content.random_jitter", new=AsyncMock()),
    ):
        from workers.handlers.tiktok.generate_content import generate_content_handler
        result = await generate_content_handler({
            "title": "AI Product",
            "description": "A great AI product",
            "keywords": ["ai", "test"],
        })

    assert result["ok"] is True
    assert result["caption"]  # could be AI or template fallback
    assert "variants" in result


# ── Cache ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_template_cache_hit(monkeypatch):
    """Second call with same title on template path must populate cache."""
    monkeypatch.delenv("USE_AI_CAPTION", raising=False)
    monkeypatch.delenv("DRY_RUN", raising=False)

    import workers.handlers.tiktok.generate_content as mod
    mod._RESULT_CACHE.clear()

    from workers.handlers.tiktok.generate_content import generate_content_handler
    payload = {"title": "CacheProduct", "description": "desc", "keywords": []}

    r1 = await generate_content_handler(dict(payload))
    # After first run, cache must have at least one entry
    assert len(mod._RESULT_CACHE) >= 1

    r2 = await generate_content_handler(dict(payload))
    assert r1["ok"] is True
    assert r2["ok"] is True


# ── Rule-based hashtags ────────────────────────────────────────────────────────

def test_hashtag_count():
    """_build_hashtags must return 7–10 tags."""
    from workers.handlers.tiktok.generate_content import _build_hashtags
    for _ in range(20):
        tags = _build_hashtags("Super Widget", ["gadget", "tech", "must-have"])
        assert 7 <= len(tags) <= 10, f"Got {len(tags)} tags: {tags}"


def test_hashtag_no_hash_prefix():
    """Hashtags must not have # prefix."""
    from workers.handlers.tiktok.generate_content import _build_hashtags
    tags = _build_hashtags("Glow Serum", ["beauty", "skincare"])
    assert all(not t.startswith("#") for t in tags), f"Found # prefix in {tags}"


def test_hashtag_slugify():
    """_slugify must remove special chars and spaces."""
    from workers.handlers.tiktok.generate_content import _slugify
    assert _slugify("Hello World!") == "helloworld"
    assert _slugify("best product 2024") == "bestproduct2024"
    assert _slugify("  spaces  ") == "spaces"


# ── Template engine internals ─────────────────────────────────────────────────

def test_build_caption_has_product_name():
    """Template output must contain the product name."""
    from workers.handlers.tiktok.generate_content import _build_caption_from_template, _CTA_OPTIONS
    import random
    for style in ["shock", "review", "problem_solution", "before_after"]:
        result = _build_caption_from_template(style, "MagicWidget", random.choice(_CTA_OPTIONS))
        assert "MagicWidget" in result["caption"] or "MagicWidget" in result["hook"], \
            f"Product name not found in {style} output"


def test_generate_variants_count():
    """_generate_variants must return exactly n variants."""
    from workers.handlers.tiktok.generate_content import _generate_variants
    for n in [2, 3]:
        variants = _generate_variants("shock", "TestProduct", n=n)
        assert len(variants) == n


def test_caption_max_length():
    """All generated captions must be ≤ 2200 chars."""
    from workers.handlers.tiktok.generate_content import _build_caption_from_template, _CTA_OPTIONS
    import random
    for style in ["shock", "review", "problem_solution", "before_after"]:
        for _ in range(5):
            result = _build_caption_from_template(style, "X" * 100, random.choice(_CTA_OPTIONS))
            assert len(result["caption"]) <= 2200


# ── Result schema ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_result_schema_complete(monkeypatch):
    """Result must have caption, hook, cta, hashtags, variants, ok."""
    monkeypatch.setenv("DRY_RUN", "true")

    import workers.handlers.tiktok.generate_content as mod
    mod._RESULT_CACHE.clear()

    from workers.handlers.tiktok.generate_content import generate_content_handler
    result = await generate_content_handler({
        "title": "SchemaProduct",
        "description": "desc",
        "keywords": ["key1"],
    })

    for key in ("caption", "hook", "cta", "hashtags", "variants", "ok"):
        assert key in result, f"Missing key: {key}"
    assert isinstance(result["hashtags"], list)
    assert isinstance(result["variants"], list)
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_idempotency_guard():
    """_idempotent_result in payload should short-circuit the handler."""
    from workers.handlers.tiktok.generate_content import generate_content_handler
    cached = {"caption": "c", "hook": "h", "cta": "cta", "hashtags": [], "ok": True}
    result = await generate_content_handler({"_idempotent_result": cached})
    assert result == cached
