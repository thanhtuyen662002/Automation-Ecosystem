"""
Shared utilities for TikTok pipeline handlers.

All helpers here are intentionally dependency-light (stdlib + httpx only) so
every handler module can import them without pulling in heavy optional deps.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Any

import httpx

LOGGER = logging.getLogger("workers.handlers.tiktok")


# ── Environment helpers ───────────────────────────────────────────────────────

def _env(key: str, default: str | None = None) -> str:
    value = os.environ.get(key, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return value


def get_openai_api_key() -> str:
    return _env("OPENAI_API_KEY")


def get_openai_model() -> str:
    return _env("OPENAI_MODEL", "gpt-4o")


def get_media_output_dir() -> Path:
    return Path(_env("MEDIA_OUTPUT_DIR", "./media_output")).expanduser().resolve()


def get_bgm_dir() -> Path | None:
    raw = os.environ.get("BGM_DIR", "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def get_flip_chance() -> float:
    """Return the per-clip horizontal-flip probability (env TIKTOK_FLIP_CHANCE, default 0.30)."""
    raw = os.environ.get("TIKTOK_FLIP_CHANCE", "0.30").strip()
    try:
        val = float(raw)
    except ValueError:
        val = 0.30
    return max(0.0, min(1.0, val))


# ── Anti-abuse jitter ─────────────────────────────────────────────────────────

async def random_jitter(min_seconds: float = 1.0, max_seconds: float = 5.0) -> None:
    """Sleep for a random duration to avoid bot-detection rate limits."""
    delay = random.uniform(min_seconds, max_seconds)
    LOGGER.debug("anti_abuse_jitter", extra={"event": "jitter_sleep", "delay_seconds": round(delay, 2)})
    await asyncio.sleep(delay)


# ── Per-job random seed ───────────────────────────────────────────────────────

def random_seed() -> int:
    """
    Generate a unique per-job seed that combines system randomness with the
    current millisecond timestamp to guarantee varied outputs across rapid
    successive runs.
    """
    ts_ms = int(time.monotonic() * 1000) & 0xFFFFFF  # 24-bit monotonic slice
    rand_part = random.randint(0, 0xFFFFFF)
    seed = ts_ms ^ rand_part
    LOGGER.debug("random_seed_generated", extra={"event": "random_seed_generated", "seed": seed})
    return seed


# ── Subprocess helpers ────────────────────────────────────────────────────────

class SubprocessError(RuntimeError):
    """Raised when a subprocess exits with a non-zero code."""


async def run_subprocess(
    *args: str,
    timeout: float = 300.0,
    cwd: str | None = None,
) -> tuple[str, str]:
    """
    Run an external command asynchronously.

    Returns (stdout, stderr). Raises SubprocessError on non-zero exit.
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        raise SubprocessError(f"Subprocess timed out after {timeout}s: {' '.join(args[:3])}") from exc

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")

    if proc.returncode != 0:
        raise SubprocessError(
            f"Subprocess exited with code {proc.returncode}: {' '.join(args[:3])}\nstderr: {stderr[:500]}"
        )
    return stdout, stderr


# ── Idempotency guard ─────────────────────────────────────────────────────────

def check_already_processed(payload: dict[str, Any]) -> dict[str, Any] | None:
    """
    If the payload carries '_idempotent_result', return it immediately.

    The workflow_manager may inject this field when re-queuing a task that
    completed successfully but whose downstream job was paused.
    """
    result = payload.get("_idempotent_result")
    if isinstance(result, dict):
        LOGGER.info(
            "task_skipped_idempotent",
            extra={"event": "task_skipped_idempotent"},
        )
        return result
    return None


# ── Parent-task result resolution ─────────────────────────────────────────────

def resolve_parent_result(payload: dict[str, Any], key: str) -> Any:
    """
    Pull a value from the payload that was injected from a parent task result.

    By convention the workflow_manager copies parent results into the child
    payload under the key ``parent_results.<task_type>`` when building the
    job graph via POST /pipelines/tiktok. Handlers may also receive the
    result directly embedded at the top level of the payload.
    """
    # Direct embed (preferred path set by the pipeline creator)
    if key in payload:
        return payload[key]
    # Nested under parent_results
    parent_results: dict[str, Any] = payload.get("parent_results") or {}
    if key in parent_results:
        return parent_results[key]
    raise KeyError(f"Required key '{key}' not found in payload or parent_results")


# ── HTTP helper ───────────────────────────────────────────────────────────────

async def fetch_url_text(url: str, timeout: float = 20.0) -> str:
    """Fetch a URL and return the response body as text."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout, headers=headers) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


# ── BGM picker ────────────────────────────────────────────────────────────────

def pick_random_bgm(bgm_dir: Path | None) -> str | None:
    """Return a random .mp3/.m4a file from bgm_dir, or None if unavailable."""
    if bgm_dir is None or not bgm_dir.is_dir():
        return None
    candidates = list(bgm_dir.glob("*.mp3")) + list(bgm_dir.glob("*.m4a"))
    if not candidates:
        return None
    chosen = random.choice(candidates)
    return str(chosen)


# ── ffprobe duration ─────────────────────────────────────────────────────────

async def get_video_duration(video_path: str) -> float:
    """Return video duration in seconds via ffprobe."""
    stdout, _ = await run_subprocess(
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        video_path,
        timeout=30.0,
    )
    data = json.loads(stdout)
    return float(data["format"]["duration"])
