"""
core/ai_router.py
─────────────────────────────────────────────────────────────────────────────
Unified AI text generation with multi-provider fallback.

Priority order:
  1. Gemini Flash  (GEMINI_API_KEY)
  2. HuggingFace Serverless Inference  (HF_API_KEY)
  3. Pollinations AI  (no key required)

Each provider:
  - timeout : 10 s per attempt
  - retries : 2 retries → up to 3 total attempts

Public interface
────────────────
    from core.ai_router import generate_text

    text = await generate_text(prompt, max_tokens=512, temperature=0.7)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import urllib.parse
from typing import Callable, Awaitable

import httpx

LOGGER = logging.getLogger("core.ai_router")

# ── Constants ─────────────────────────────────────────────────────────────────

_PROVIDER_TIMEOUT_S: float = 10.0
_PROVIDER_RETRIES: int = 2          # retries after initial attempt → 3 total tries
_RETRY_BACKOFF_S: float = 1.0       # linear back-off between retries

# HuggingFace model — lightweight, instruction-tuned, free serverless tier
_HF_MODEL = "mistralai/Mistral-7B-Instruct-v0.3"
_HF_INFERENCE_URL = f"https://api-inference.huggingface.co/models/{_HF_MODEL}"

# Gemini model
_GEMINI_MODEL = "gemini-1.5-flash"


# ── Environment helpers ───────────────────────────────────────────────────────

def _get_env(key: str) -> str | None:
    return os.environ.get(key, "").strip() or None


# ── Provider: Gemini ──────────────────────────────────────────────────────────

async def _call_gemini(
    prompt: str,
    max_tokens: int,
    temperature: float,
) -> str:
    """Call Google Gemini via the google-generativeai SDK."""
    api_key = _get_env("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set — skipping Gemini provider")

    # Lazy import so the package is optional at startup
    try:
        import google.generativeai as genai  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "google-generativeai is not installed. "
            "Run: pip install google-generativeai"
        ) from exc

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(_GEMINI_MODEL)

    generation_config = genai.types.GenerationConfig(  # type: ignore[attr-defined]
        max_output_tokens=max_tokens,
        temperature=temperature,
    )

    # Gemini SDK is synchronous — run in thread pool to keep async
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None,
        lambda: model.generate_content(prompt, generation_config=generation_config),
    )

    text = response.text
    if not text or not text.strip():
        raise RuntimeError("Gemini returned empty response")
    return text.strip()


# ── Provider: HuggingFace Serverless Inference ────────────────────────────────

async def _call_huggingface(
    prompt: str,
    max_tokens: int,
    temperature: float,
) -> str:
    """Call HuggingFace Serverless Inference API."""
    api_key = _get_env("HF_API_KEY")
    if not api_key:
        raise RuntimeError("HF_API_KEY is not set — skipping HuggingFace provider")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "inputs": prompt,
        "parameters": {
            "max_new_tokens": max_tokens,
            "temperature": max(0.01, temperature),  # HF rejects 0.0
            "return_full_text": False,
        },
    }

    async with httpx.AsyncClient(timeout=_PROVIDER_TIMEOUT_S) as client:
        resp = await client.post(_HF_INFERENCE_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    # HF returns a list of generated texts
    if isinstance(data, list) and data:
        text = data[0].get("generated_text", "")
    elif isinstance(data, dict):
        text = data.get("generated_text", "")
    else:
        text = ""

    if not text or not str(text).strip():
        raise RuntimeError("HuggingFace returned empty response")
    return str(text).strip()


# ── Provider: Pollinations ────────────────────────────────────────────────────

async def _call_pollinations(
    prompt: str,
    max_tokens: int,
    temperature: float,
) -> str:
    """Call Pollinations text API — no API key required (last resort)."""
    encoded = urllib.parse.quote(prompt, safe="")
    url = f"https://text.pollinations.ai/{encoded}"

    params = {
        "model": "openai",
        "seed": int(time.time()) % 10000,
    }

    async with httpx.AsyncClient(timeout=_PROVIDER_TIMEOUT_S, follow_redirects=True) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        text = resp.text

    if not text or not text.strip():
        raise RuntimeError("Pollinations returned empty response")
    return text.strip()


# ── Retry wrapper ─────────────────────────────────────────────────────────────

async def _call_with_retry(
    provider_fn: Callable[..., Awaitable[str]],
    provider_name: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
) -> str:
    """
    Call a provider function with timeout and linear retry.

    Returns the provider's text on success.
    Raises the last exception if all attempts are exhausted.
    """
    last_exc: Exception = RuntimeError(f"{provider_name}: no attempts made")

    for attempt in range(1, _PROVIDER_RETRIES + 2):  # 1, 2, 3
        try:
            t0 = time.monotonic()
            text = await asyncio.wait_for(
                provider_fn(prompt, max_tokens, temperature),
                timeout=_PROVIDER_TIMEOUT_S,
            )
            latency_ms = int((time.monotonic() - t0) * 1000)
            LOGGER.info(
                "ai_router_provider_success",
                extra={
                    "event": "ai_router_provider_success",
                    "provider": provider_name,
                    "attempt": attempt,
                    "latency_ms": latency_ms,
                },
            )
            return text

        except asyncio.TimeoutError as exc:
            last_exc = exc
            LOGGER.warning(
                "ai_router_provider_timeout",
                extra={
                    "event": "ai_router_provider_timeout",
                    "provider": provider_name,
                    "attempt": attempt,
                    "timeout_s": _PROVIDER_TIMEOUT_S,
                },
            )
        except Exception as exc:
            last_exc = exc
            LOGGER.warning(
                "ai_router_provider_error",
                extra={
                    "event": "ai_router_provider_error",
                    "provider": provider_name,
                    "attempt": attempt,
                    "error": str(exc)[:200],
                },
            )

        if attempt <= _PROVIDER_RETRIES:
            await asyncio.sleep(_RETRY_BACKOFF_S * attempt)

    raise last_exc


# ── Public interface ──────────────────────────────────────────────────────────

async def generate_text(
    prompt: str,
    max_tokens: int = 512,
    temperature: float = 0.7,
) -> str:
    """
    Generate text using the best available AI provider.

    Tries each provider in priority order; falls back on any failure.

    Priority:
      1. Gemini Flash  (GEMINI_API_KEY)
      2. HuggingFace   (HF_API_KEY)
      3. Pollinations  (no key)

    Raises:
      RuntimeError: if all providers fail.
    """
    providers: list[tuple[str, Callable[..., Awaitable[str]]]] = [
        ("gemini", _call_gemini),
        ("huggingface", _call_huggingface),
        ("pollinations", _call_pollinations),
    ]

    errors: list[str] = []

    for idx, (name, fn) in enumerate(providers):
        if idx > 0:
            LOGGER.warning(
                "ai_router_fallback",
                extra={
                    "event": "ai_router_fallback",
                    "from_provider": providers[idx - 1][0],
                    "to_provider": name,
                    "reason": errors[-1] if errors else "unknown",
                },
            )

        try:
            return await _call_with_retry(fn, name, prompt, max_tokens, temperature)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            LOGGER.error(
                "ai_router_provider_exhausted",
                extra={
                    "event": "ai_router_provider_exhausted",
                    "provider": name,
                    "error": str(exc)[:300],
                },
            )

    raise RuntimeError(
        "All AI providers failed. Errors:\n" + "\n".join(f"  • {e}" for e in errors)
    )
