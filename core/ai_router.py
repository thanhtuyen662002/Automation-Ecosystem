"""
Unified AI text generation with admin-managed provider keys.

API keys and model choices are loaded from the local encrypted AI key store.
Environment variables are not used for provider credentials.
"""

from __future__ import annotations

import asyncio
import logging
import time
import urllib.parse
from collections.abc import Awaitable, Callable

import httpx

from core.ai_key_store import (
    AICandidate,
    get_enabled_candidates,
    mark_key_failure,
    mark_key_success,
)


LOGGER = logging.getLogger("core.ai_router")

_PROVIDER_TIMEOUT_S: float = 10.0
_PROVIDER_RETRIES: int = 2
_RETRY_BACKOFF_S: float = 1.0

_ProviderFn = Callable[[AICandidate, str, int, float], Awaitable[str]]


async def _call_openai(
    candidate: AICandidate,
    prompt: str,
    max_tokens: int,
    temperature: float,
) -> str:
    if not candidate.raw_key:
        raise RuntimeError("OpenAI provider has no usable key")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("openai package is not installed") from exc

    def _run() -> str:
        kwargs = {"api_key": candidate.raw_key}
        if candidate.base_url:
            kwargs["base_url"] = candidate.base_url
        client = OpenAI(**kwargs)
        response = client.chat.completions.create(
            model=candidate.model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text = response.choices[0].message.content if response.choices else ""
        return str(text or "").strip()

    loop = asyncio.get_running_loop()
    text = await loop.run_in_executor(None, _run)
    if not text:
        raise RuntimeError("OpenAI returned empty response")
    return text


async def _call_gemini(
    candidate: AICandidate,
    prompt: str,
    max_tokens: int,
    temperature: float,
) -> str:
    if not candidate.raw_key:
        raise RuntimeError("Gemini provider has no usable key")
    try:
        import google.generativeai as genai  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError("google-generativeai package is not installed") from exc

    genai.configure(api_key=candidate.raw_key)
    model = genai.GenerativeModel(candidate.model_name)
    generation_config = genai.types.GenerationConfig(  # type: ignore[attr-defined]
        max_output_tokens=max_tokens,
        temperature=temperature,
    )

    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(
        None,
        lambda: model.generate_content(prompt, generation_config=generation_config),
    )
    text = str(getattr(response, "text", "") or "").strip()
    if not text:
        raise RuntimeError("Gemini returned empty response")
    return text


async def _call_huggingface(
    candidate: AICandidate,
    prompt: str,
    max_tokens: int,
    temperature: float,
) -> str:
    if not candidate.raw_key:
        raise RuntimeError("HuggingFace provider has no usable key")
    url = candidate.base_url or f"https://api-inference.huggingface.co/models/{candidate.model_name}"
    headers = {
        "Authorization": f"Bearer {candidate.raw_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "inputs": prompt,
        "parameters": {
            "max_new_tokens": max_tokens,
            "temperature": max(0.01, temperature),
            "return_full_text": False,
        },
    }

    async with httpx.AsyncClient(timeout=_PROVIDER_TIMEOUT_S) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    if isinstance(data, list) and data:
        first = data[0]
        text = first.get("generated_text", "") if isinstance(first, dict) else ""
    elif isinstance(data, dict):
        text = data.get("generated_text", "")
    else:
        text = ""
    text = str(text or "").strip()
    if not text:
        raise RuntimeError("HuggingFace returned empty response")
    return text


async def _call_pollinations(
    candidate: AICandidate,
    prompt: str,
    max_tokens: int,
    temperature: float,
) -> str:
    encoded = urllib.parse.quote(prompt, safe="")
    url = f"https://text.pollinations.ai/{encoded}"
    params = {
        "model": candidate.model_name,
        "seed": int(time.time()) % 10000,
    }

    async with httpx.AsyncClient(timeout=_PROVIDER_TIMEOUT_S, follow_redirects=True) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        text = resp.text

    text = str(text or "").strip()
    if not text:
        raise RuntimeError("Pollinations returned empty response")
    return text


_PROVIDER_CALLS: dict[str, _ProviderFn] = {
    "openai": _call_openai,
    "gemini": _call_gemini,
    "huggingface": _call_huggingface,
    "pollinations": _call_pollinations,
}


async def _call_with_retry(
    candidate: AICandidate,
    prompt: str,
    max_tokens: int,
    temperature: float,
) -> str:
    provider_fn = _PROVIDER_CALLS.get(candidate.provider)
    if provider_fn is None:
        raise RuntimeError(f"Unsupported AI provider: {candidate.provider}")

    actual_max_tokens = min(max_tokens, candidate.max_tokens) if candidate.max_tokens else max_tokens
    actual_temperature = candidate.temperature_default if candidate.temperature_default is not None else temperature
    last_exc: Exception = RuntimeError(f"{candidate.provider}: no attempts made")

    for attempt in range(1, _PROVIDER_RETRIES + 2):
        try:
            t0 = time.monotonic()
            text = await asyncio.wait_for(
                provider_fn(candidate, prompt, actual_max_tokens, actual_temperature),
                timeout=_PROVIDER_TIMEOUT_S,
            )
            latency_ms = int((time.monotonic() - t0) * 1000)
            LOGGER.info(
                "ai_router_provider_success",
                extra={
                    "event": "ai_router_provider_success",
                    "provider": candidate.provider,
                    "model": candidate.model_name,
                    "key_preview": candidate.key_preview,
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
                    "provider": candidate.provider,
                    "model": candidate.model_name,
                    "key_preview": candidate.key_preview,
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
                    "provider": candidate.provider,
                    "model": candidate.model_name,
                    "key_preview": candidate.key_preview,
                    "attempt": attempt,
                    "error": str(exc)[:300],
                },
            )

        if attempt <= _PROVIDER_RETRIES:
            await asyncio.sleep(_RETRY_BACKOFF_S * attempt)

    raise last_exc


async def generate_text(
    prompt: str,
    max_tokens: int = 512,
    temperature: float = 0.7,
    preferred_provider: str | None = None,
    preferred_model: str | None = None,
    preferred_key_id: str | None = None,
) -> str:
    """
    Generate text using admin-configured AI providers.

    Provider/key/model candidates are tried in store priority order. If one key
    or provider fails, the router records the failure and tries the next
    candidate. Raw keys are never logged.
    """
    candidates = get_enabled_candidates(
        preferred_provider=preferred_provider,
        preferred_model=preferred_model,
        preferred_key=preferred_key_id,
    )
    if not candidates:
        raise RuntimeError("No usable AI provider key configured")

    errors: list[str] = []
    for idx, candidate in enumerate(candidates):
        if idx > 0:
            LOGGER.warning(
                "ai_router_fallback",
                extra={
                    "event": "ai_router_fallback",
                    "to_provider": candidate.provider,
                    "to_model": candidate.model_name,
                    "key_preview": candidate.key_preview,
                    "reason": errors[-1] if errors else "unknown",
                },
            )
        try:
            text = await _call_with_retry(candidate, prompt, max_tokens, temperature)
            if candidate.key_id:
                mark_key_success(candidate.key_id)
            return text
        except Exception as exc:
            if candidate.key_id:
                mark_key_failure(candidate.key_id, exc)
            errors.append(f"{candidate.provider}/{candidate.model_name}: {exc}")
            LOGGER.error(
                "ai_router_provider_exhausted",
                extra={
                    "event": "ai_router_provider_exhausted",
                    "provider": candidate.provider,
                    "model": candidate.model_name,
                    "key_preview": candidate.key_preview,
                    "error": str(exc)[:300],
                },
            )

    raise RuntimeError(
        "All configured AI providers failed. Errors:\n" + "\n".join(f"  - {error}" for error in errors)
    )
