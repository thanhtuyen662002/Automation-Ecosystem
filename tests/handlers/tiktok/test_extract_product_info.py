"""
Unit tests for tiktok.extract_product_info handler.

LLM calls are mocked — no real API calls are made.
Gemini SDK is patched via google.generativeai module-level mocks.
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_gemini_response(text: str) -> MagicMock:
    """Create a minimal mock Gemini generate_content response."""
    response = MagicMock()
    response.text = text
    return response


def _gemini_patches(mock_response: MagicMock, page_text: str = "<html>product page</html>"):
    """
    Return a context-manager stack that patches:
      - GEMINI_API_KEY / GEMINI_MODEL helpers
      - google.generativeai.configure / GenerativeModel
      - fetch_url_text and random_jitter (async helpers)
    """
    mock_model = MagicMock()
    mock_model.generate_content.return_value = mock_response

    mock_types = MagicMock()
    mock_types.GenerationConfig.return_value = MagicMock()

    from unittest.mock import patch as _patch
    return (
        _patch("workers.handlers.tiktok.extract_product_info.get_gemini_api_key", return_value="test-gemini-key"),
        _patch("workers.handlers.tiktok.extract_product_info.get_gemini_model", return_value="gemini-1.5-flash"),
        _patch("workers.handlers.tiktok.extract_product_info.random_jitter", new=AsyncMock()),
        _patch("workers.handlers.tiktok.extract_product_info.fetch_url_text", new=AsyncMock(return_value=page_text)),
        _patch("google.generativeai.configure"),
        _patch("google.generativeai.GenerativeModel", return_value=mock_model),
        _patch("google.generativeai.types", mock_types),
    )


@pytest.mark.asyncio
async def test_extract_product_info_url():
    from workers.handlers.tiktok.extract_product_info import extract_product_info_handler

    good_json = json.dumps({
        "title": "Awesome Widget",
        "description": "A great product for everyone.",
        "keywords": ["widget", "awesome", "gadget"],
    })
    mock_response = _make_gemini_response(good_json)

    patches = _gemini_patches(mock_response)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        payload = {"product_url": "https://example.com/product"}
        result = await extract_product_info_handler(payload)

    assert result["ok"] is True
    assert result["title"] == "Awesome Widget"
    assert result["description"] == "A great product for everyone."
    assert isinstance(result["keywords"], list)
    assert len(result["keywords"]) > 0


@pytest.mark.asyncio
async def test_extract_product_info_missing_source():
    from workers.handlers.tiktok.extract_product_info import extract_product_info_handler

    with pytest.raises(ValueError, match="product_url.*product_image_path"):
        await extract_product_info_handler({})


@pytest.mark.asyncio
async def test_extract_product_info_idempotency():
    from workers.handlers.tiktok.extract_product_info import extract_product_info_handler

    cached = {"title": "T", "description": "D", "keywords": ["k"], "ok": True}
    result = await extract_product_info_handler({"_idempotent_result": cached})
    assert result == cached


@pytest.mark.asyncio
async def test_extract_product_info_malformed_llm_response():
    from workers.handlers.tiktok.extract_product_info import extract_product_info_handler

    # Gemini returns garbage — handler should produce fallback title "Unknown Product"
    mock_response = _make_gemini_response("sorry I cannot help with that")

    patches = _gemini_patches(mock_response, page_text="some page content")
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        result = await extract_product_info_handler({"product_url": "https://example.com"})

    assert result["ok"] is True
    assert result["title"] == "Unknown Product"


@pytest.mark.asyncio
async def test_extract_product_info_keywords_fallback():
    """When Gemini returns empty keywords, they are derived from the title."""
    from workers.handlers.tiktok.extract_product_info import extract_product_info_handler

    partial_json = json.dumps({
        "title": "Super Powerful Blender",
        "description": "The best blender.",
        "keywords": [],  # empty → fallback
    })
    mock_response = _make_gemini_response(partial_json)

    patches = _gemini_patches(mock_response)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        result = await extract_product_info_handler({"product_url": "https://example.com"})

    assert result["ok"] is True
    assert len(result["keywords"]) > 0  # derived from "Super Powerful Blender"
