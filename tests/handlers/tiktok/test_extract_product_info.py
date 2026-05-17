"""
Unit tests for tiktok.extract_product_info handler.

LLM calls are mocked — no real API calls are made.
Gemini SDK is patched via google.generativeai module-level mocks.
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch as _patch


def _make_gemini_response(text: str) -> MagicMock:
    """Create a minimal mock Gemini generate_content response."""
    response = MagicMock()
    response.text = text
    return response


def _gemini_patches(mock_response: MagicMock, page_text: str = "<html>product page</html>"):
    """
    Return a context-manager stack that patches:
      - AI key store Gemini candidates
      - google.generativeai.configure / GenerativeModel
      - fetch_url_text and random_jitter (async helpers)
    """
    from core.ai_key_store import AICandidate

    candidate = AICandidate(
        provider_id="provider-gemini",
        provider="gemini",
        display_name="Google Gemini",
        base_url=None,
        model_id="model-gemini",
        model_name="gemini-1.5-flash",
        model_display_name="Gemini Flash",
        max_tokens=None,
        temperature_default=None,
        key_id="key-gemini",
        key_preview="tes...key",
        raw_key="test-gemini-key",
    )
    mock_model = MagicMock()
    mock_model.generate_content.return_value = mock_response

    mock_types = MagicMock()
    mock_types.GenerationConfig.return_value = MagicMock()

    from unittest.mock import patch as _patch
    return (
        _patch("workers.handlers.tiktok.extract_product_info.get_enabled_candidates", return_value=[candidate]),
        _patch("workers.handlers.tiktok.extract_product_info.mark_key_success"),
        _patch("workers.handlers.tiktok.extract_product_info.mark_key_failure"),
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
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
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

    # Gemini returns garbage — handler should fail validation instead of passing junk downstream.
    mock_response = _make_gemini_response("sorry I cannot help with that")

    patches = _gemini_patches(mock_response, page_text="some page content")
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        with pytest.raises(RuntimeError, match="Could not extract meaningful product title/keywords"):
            await extract_product_info_handler({"product_url": "https://example.com"})


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
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        result = await extract_product_info_handler({"product_url": "https://example.com"})

    assert result["ok"] is True
    assert len(result["keywords"]) > 0  # derived from "Super Powerful Blender"


def test_derive_keywords_from_tiktok_shop_title():
    from workers.handlers.tiktok.extract_product_info import derive_keywords_from_title

    title = (
        "Xu Hướng [SIÊU TIẾT KIỆM - SIÊU MỀM MỊN] Thùng 6 Bịch Khăn Giấy Rút Dây "
        "Tiểu Hạ TOPGIA 4 Lớp Mềm Mịn, Khăn Giấy Vệ Sinh 1000 tờ"
    )

    keywords = derive_keywords_from_title(title)
    lowered = {keyword.lower() for keyword in keywords}

    assert "khăn giấy" in lowered
    assert "giấy rút dây" in lowered
    assert "thùng 6 bịch" in lowered
    assert "topgia" in lowered
    assert "4 lớp" in lowered
    assert "1000 tờ" in lowered
    assert lowered.isdisjoint({"unknown", "product", "item", "shop"})


@pytest.mark.asyncio
async def test_extract_product_info_tiktok_shop_dom_fallback_without_ai():
    from workers.handlers.tiktok.extract_product_info import extract_product_info_handler

    title = (
        "Xu Hướng [SIÊU TIẾT KIỆM - SIÊU MỀM MỊN] Thùng 6 Bịch Khăn Giấy Rút Dây "
        "Tiểu Hạ TOPGIA 4 Lớp Mềm Mịn, Thành Phần Bột Gỗ Tự Nhiên Thân Thiện An Toàn, "
        "Khăn Giấy Vệ Sinh 1000 tờ,4 lớp Tặng 2 móc treo tiện lợi"
    )
    shop_data = {
        "ok": True,
        "title": title,
        "description": "Khăn giấy rút dây 4 lớp, thùng 6 bịch.",
        "meta_description": "",
        "page_title": "TikTok Shop",
        "og_title": "",
        "price": "₫99.000",
        "sold_count": "1.2K đã được bán",
        "rating": "4.7",
        "shop_name": "TOPGIA",
        "candidate_lines": [title],
        "ld_json": None,
    }

    with (
        _patch("workers.handlers.tiktok.extract_product_info.random_jitter", new=AsyncMock()),
        _patch("workers.handlers.tiktok.extract_product_info.extract_tiktok_shop_product_info", new=AsyncMock(return_value=shop_data)),
        _patch("workers.handlers.tiktok.extract_product_info.get_enabled_candidates", return_value=[]),
        _patch("workers.handlers.tiktok.extract_product_info.fetch_url_text", new=AsyncMock(side_effect=AssertionError("should not fetch"))),
    ):
        result = await extract_product_info_handler({
            "product_url": "https://shop.tiktok.com/view/product/1731773686751724630",
            "account_id": "account-1",
        })

    lowered = {keyword.lower() for keyword in result["keywords"]}
    assert result["ok"] is True
    assert result["source"] == "tiktok_shop_dom"
    assert "Thùng 6 Bịch Khăn Giấy Rút Dây" in result["title"]
    assert {"khăn giấy", "giấy rút dây", "thùng 6 bịch", "topgia", "4 lớp", "1000 tờ"} <= lowered


@pytest.mark.asyncio
async def test_extract_product_info_tiktok_shop_missing_title_is_fatal():
    from workers.handlers.tiktok.extract_product_info import extract_product_info_handler
    from workers.worker_runtime import FatalDependencyError

    shop_data = {
        "ok": False,
        "title": "",
        "page_title": "TikTok Shop",
        "og_title": "",
        "meta_description": "generic",
        "body_text_preview": "₫99.000\n1.2K đã được bán",
        "candidate_lines": ["₫99.000", "1.2K đã được bán"],
        "error": None,
    }

    with (
        _patch("workers.handlers.tiktok.extract_product_info.random_jitter", new=AsyncMock()),
        _patch("workers.handlers.tiktok.extract_product_info.extract_tiktok_shop_product_info", new=AsyncMock(return_value=shop_data)),
        _patch("workers.handlers.tiktok.extract_product_info.fetch_url_text", new=AsyncMock(side_effect=AssertionError("should not fetch"))),
    ):
        with pytest.raises(FatalDependencyError, match="TikTok Shop page loaded but product title could not be extracted"):
            await extract_product_info_handler({
                "product_url": "https://shop.tiktok.com/view/product/1731773686751724630",
                "account_id": "account-1",
            })
