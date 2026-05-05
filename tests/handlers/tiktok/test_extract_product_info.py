"""
Unit tests for tiktok.extract_product_info handler.

LLM calls are mocked — no real API calls are made.
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_openai_response(content: str):
    """Create a minimal mock OpenAI chat completion response."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


@pytest.mark.asyncio
async def test_extract_product_info_url(monkeypatch):
    from workers.handlers.tiktok.extract_product_info import extract_product_info_handler

    good_json = json.dumps({
        "title": "Awesome Widget",
        "description": "A great product for everyone.",
        "keywords": ["widget", "awesome", "gadget"],
    })

    mock_response = _make_openai_response(good_json)

    with (
        patch("workers.handlers.tiktok.extract_product_info.fetch_url_text", new=AsyncMock(return_value="<html>product page</html>")),
        patch("workers.handlers.tiktok.extract_product_info.get_openai_api_key", return_value="sk-test"),
        patch("workers.handlers.tiktok.extract_product_info.get_openai_model", return_value="gpt-4o"),
        patch("workers.handlers.tiktok.extract_product_info.random_jitter", new=AsyncMock()),
        patch("openai.AsyncOpenAI") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        payload = {"product_url": "https://example.com/product"}
        result = await extract_product_info_handler(payload)

    assert result["ok"] is True
    assert result["title"] == "Awesome Widget"
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

    # LLM returns garbage — handler should still produce a result with fallback title
    mock_response = _make_openai_response("sorry I cannot help with that")

    with (
        patch("workers.handlers.tiktok.extract_product_info.fetch_url_text", new=AsyncMock(return_value="page")),
        patch("workers.handlers.tiktok.extract_product_info.get_openai_api_key", return_value="sk-test"),
        patch("workers.handlers.tiktok.extract_product_info.get_openai_model", return_value="gpt-4o"),
        patch("workers.handlers.tiktok.extract_product_info.random_jitter", new=AsyncMock()),
        patch("openai.AsyncOpenAI") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await extract_product_info_handler({"product_url": "https://example.com"})

    assert result["ok"] is True
    assert result["title"] == "Unknown Product"
