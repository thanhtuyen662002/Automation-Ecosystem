"""
tests/test_ai_router.py
────────────────────────
Unit tests for core.ai_router.generate_text.

All external I/O is mocked — no real API calls are made.
Tests verify:
  1. Gemini succeeds on first try → returned immediately
  2. Gemini fails → HuggingFace succeeds → fallback logged
  3. Gemini + HF fail → Pollinations succeeds → full fallback chain
  4. All providers fail → RuntimeError raised
  5. Gemini times out → fallback to HuggingFace
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


# ── Helpers ───────────────────────────────────────────────────────────────────

GOOD_TEXT = '{"caption": "test", "hashtags": [], "hook": "h", "cta": "c"}'


def _make_hf_response(text: str):
    """Minimal mock for an httpx Response returning HF JSON."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = [{"generated_text": text}]
    return mock_resp


def _make_pollinations_response(text: str):
    """Minimal mock for an httpx Response returning plain text."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.text = text
    return mock_resp


# ── Test 1: Gemini succeeds ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gemini_success():
    """Router returns Gemini output without touching other providers."""
    with (
        patch("core.ai_router._get_env", return_value="fake-gemini-key"),
        patch("core.ai_router._call_gemini", new=AsyncMock(return_value=GOOD_TEXT)) as mock_gemini,
        patch("core.ai_router._call_huggingface", new=AsyncMock()) as mock_hf,
        patch("core.ai_router._call_pollinations", new=AsyncMock()) as mock_poll,
    ):
        from core.ai_router import generate_text
        result = await generate_text("test prompt")

    assert result == GOOD_TEXT
    mock_gemini.assert_awaited_once()
    mock_hf.assert_not_awaited()
    mock_poll.assert_not_awaited()


# ── Test 2: Gemini fails → HuggingFace succeeds ───────────────────────────────

@pytest.mark.asyncio
async def test_gemini_fail_hf_success(caplog):
    """Fallback from Gemini to HuggingFace is triggered and logged."""
    import logging

    async def _fail(*_a, **_kw):
        raise RuntimeError("Gemini API error")

    with (
        patch("core.ai_router._call_gemini", new=_fail),
        patch("core.ai_router._call_huggingface", new=AsyncMock(return_value=GOOD_TEXT)) as mock_hf,
        patch("core.ai_router._call_pollinations", new=AsyncMock()) as mock_poll,
        patch("core.ai_router.asyncio.sleep", new=AsyncMock()),  # skip retry back-off
    ):
        with caplog.at_level(logging.WARNING, logger="core.ai_router"):
            from core.ai_router import generate_text
            result = await generate_text("test prompt")

    assert result == GOOD_TEXT
    mock_hf.assert_awaited()
    mock_poll.assert_not_awaited()
    # Fallback event must be logged
    assert any("ai_router_fallback" in r.message or "fallback" in r.message.lower()
               for r in caplog.records)


# ── Test 3: Gemini + HF fail → Pollinations succeeds ─────────────────────────

@pytest.mark.asyncio
async def test_full_fallback_chain():
    """Full cascade: Gemini fail → HF fail → Pollinations success."""

    async def _fail(*_a, **_kw):
        raise RuntimeError("provider down")

    with (
        patch("core.ai_router._call_gemini", new=_fail),
        patch("core.ai_router._call_huggingface", new=_fail),
        patch("core.ai_router._call_pollinations", new=AsyncMock(return_value=GOOD_TEXT)) as mock_poll,
        patch("core.ai_router.asyncio.sleep", new=AsyncMock()),
    ):
        from core.ai_router import generate_text
        result = await generate_text("test prompt")

    assert result == GOOD_TEXT
    mock_poll.assert_awaited()


# ── Test 4: All providers fail → RuntimeError ─────────────────────────────────

@pytest.mark.asyncio
async def test_all_providers_fail():
    """RuntimeError is raised when every provider is exhausted."""

    async def _fail(*_a, **_kw):
        raise RuntimeError("total failure")

    with (
        patch("core.ai_router._call_gemini", new=_fail),
        patch("core.ai_router._call_huggingface", new=_fail),
        patch("core.ai_router._call_pollinations", new=_fail),
        patch("core.ai_router.asyncio.sleep", new=AsyncMock()),
    ):
        from core.ai_router import generate_text
        with pytest.raises(RuntimeError, match="All AI providers failed"):
            await generate_text("test prompt")


# ── Test 5: Gemini timeout → fallback to HuggingFace ─────────────────────────

@pytest.mark.asyncio
async def test_gemini_timeout_triggers_fallback():
    """A provider that exceeds the timeout is treated as a failure and triggers fallback."""

    # Simulate the TimeoutError that asyncio.wait_for raises when Gemini is too slow.
    # We patch _call_with_retry to raise TimeoutError for gemini only.
    original_call_with_retry = None

    async def _selective_retry(provider_fn, provider_name, prompt, max_tokens, temperature):
        """Let Gemini time out; let others succeed normally."""
        if provider_name == "gemini":
            raise asyncio.TimeoutError()
        return await original_call_with_retry(provider_fn, provider_name, prompt, max_tokens, temperature)

    import core.ai_router as router_module
    original_call_with_retry = router_module._call_with_retry

    with (
        patch("core.ai_router._call_huggingface", new=AsyncMock(return_value=GOOD_TEXT)) as mock_hf,
        patch("core.ai_router._call_pollinations", new=AsyncMock()) as mock_poll,
        patch("core.ai_router._call_with_retry", side_effect=_selective_retry),
        patch("core.ai_router.asyncio.sleep", new=AsyncMock()),
    ):
        from core.ai_router import generate_text
        result = await generate_text("test prompt")

    assert result == GOOD_TEXT
    mock_poll.assert_not_awaited()
