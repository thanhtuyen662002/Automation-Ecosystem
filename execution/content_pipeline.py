"""
execution/content_pipeline.py — Video processing pipeline (FFmpeg).

Modes:
    reup   — download → crop 9:16 → subtitle overlay → optional speed ±5%
    remark — same as reup + CTA text overlay

Requirements:
    - ffmpeg on PATH (or set FFMPEG_PATH env var)
    - yt-dlp on PATH (or set YTDLP_PATH env var) for URL downloads

Design contracts:
    - All processing is synchronous (run in executor if called from async context).
    - Output directory: CONTENT_OUTPUT_DIR env var (default: data/content_output).
    - Deterministic output path: {output_dir}/{content_id}.mp4.
    - Exception-safe: returns PipelineResult (never raises).
    - ffmpeg errors are captured and returned in PipelineResult.error.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("execution.content_pipeline")

# ── Config ────────────────────────────────────────────────────────────────────

_FFMPEG    = os.environ.get("FFMPEG_PATH", "ffmpeg")
_YTDLP     = os.environ.get("YTDLP_PATH",  "yt-dlp")
_OUTPUT_DIR = Path(os.environ.get("CONTENT_OUTPUT_DIR", "data/content_output"))

# Target aspect ratio for 9:16 short-form
_TARGET_W: int = 1080
_TARGET_H: int = 1920

# Subtitle/CTA font settings (bundled fallback uses Arial)
_FONT_COLOR = "white"
_FONT_SIZE  = 42
_CTA_SIZE   = 54

# Speed variation range ±5%
_SPEED_RANGE: tuple[float, float] = (0.95, 1.05)


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class PipelineResult:
    success:    bool
    content_id: str
    mode:       str
    video_path: str   = ""
    error:      str   = ""
    elapsed_s:  float = 0.0
    meta:       dict[str, Any] = field(default_factory=dict)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run_ffmpeg(*args: str, timeout: int = 300) -> tuple[bool, str]:
    """Run ffmpeg with given args. Returns (success, stderr_output)."""
    cmd = [_FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *args]
    LOGGER.debug("ffmpeg_cmd %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        ok = result.returncode == 0
        if not ok:
            LOGGER.warning("ffmpeg_error rc=%d stderr=%s", result.returncode, result.stderr[:500])
        return ok, result.stderr
    except subprocess.TimeoutExpired:
        return False, "ffmpeg timeout"
    except FileNotFoundError:
        return False, f"ffmpeg not found at '{_FFMPEG}' — install ffmpeg or set FFMPEG_PATH"


def _run_ytdlp(url: str, out_path: str, timeout: int = 120) -> tuple[bool, str]:
    """Download video via yt-dlp."""
    cmd = [
        _YTDLP,
        "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--output", out_path,
        "--no-playlist",
        url,
    ]
    LOGGER.debug("ytdlp_cmd %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
        )
        ok = result.returncode == 0
        return ok, result.stderr if not ok else ""
    except subprocess.TimeoutExpired:
        return False, "yt-dlp timeout"
    except FileNotFoundError:
        return False, f"yt-dlp not found at '{_YTDLP}' — install yt-dlp or set YTDLP_PATH"


def _download_video(source_url: str, dest: Path) -> tuple[bool, str]:
    """
    Download a video from URL to dest path.
    Tries yt-dlp first (handles most platforms), falls back to urllib.
    """
    if not source_url:
        return False, "source_url is empty"

    # Try yt-dlp for platform-aware download
    ok, err = _run_ytdlp(source_url, str(dest))
    if ok and dest.exists():
        return True, ""

    LOGGER.debug("ytdlp_failed error=%s — falling back to urllib", err)
    # Plain HTTP fallback (works for direct mp4 links)
    try:
        urllib.request.urlretrieve(source_url, dest)   # noqa: S310
        if dest.exists() and dest.stat().st_size > 0:
            return True, ""
        return False, "urllib download produced empty file"
    except Exception as exc:
        return False, f"download_failed: {exc}"


def _crop_9_16(input_path: Path, output_path: Path) -> tuple[bool, str]:
    """
    Crop and scale input to 1080×1920 (9:16).

    FFmpeg filter:
      scale to fit within 1080×1920 (pad to fill),
      then pad black bars if needed.
    """
    vf = (
        f"scale={_TARGET_W}:{_TARGET_H}:force_original_aspect_ratio=decrease,"
        f"pad={_TARGET_W}:{_TARGET_H}:(ow-iw)/2:(oh-ih)/2:black"
    )
    return _run_ffmpeg(
        "-i", str(input_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        str(output_path),
    )


def _add_subtitle(
    input_path: Path,
    output_path: Path,
    text: str,
    *,
    y_pos: str = "h-100",
    font_size: int = _FONT_SIZE,
    color: str = _FONT_COLOR,
) -> tuple[bool, str]:
    """
    Burn a text overlay into the video at the bottom.

    Uses ffmpeg drawtext filter. Text is sanitised (colons escaped).
    """
    safe_text = text.replace("'", "\\'").replace(":", "\\:")[:120]
    vf = (
        f"drawtext=text='{safe_text}':"
        f"fontsize={font_size}:fontcolor={color}:"
        f"x=(w-text_w)/2:y={y_pos}:"
        "box=1:boxcolor=black@0.5:boxborderw=8"
    )
    return _run_ffmpeg(
        "-i", str(input_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "copy",
        str(output_path),
    )


def _change_speed(
    input_path: Path,
    output_path: Path,
    speed_factor: float,
) -> tuple[bool, str]:
    """
    Adjust video and audio speed by speed_factor (e.g. 1.05 = 5% faster).
    Clamped to [0.5, 2.0] for safety.
    """
    speed_factor = max(0.5, min(2.0, speed_factor))
    # Audio atempo filter only accepts [0.5, 2.0]
    vf = f"setpts={1.0 / speed_factor:.4f}*PTS"
    af = f"atempo={speed_factor:.4f}"
    return _run_ffmpeg(
        "-i", str(input_path),
        "-vf", vf, "-af", af,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        str(output_path),
    )


# ── Main processing functions ─────────────────────────────────────────────────

def _process_reup(
    source_url: str,
    output_path: Path,
    subtitle: str,
    speed_factor: float | None,
    work_dir: Path,
) -> tuple[bool, str]:
    """
    Reup mode:
      1. Download source video
      2. Crop to 9:16
      3. Add subtitle overlay
      4. (Optional) Speed change ±5%
    """
    raw       = work_dir / "raw.mp4"
    cropped   = work_dir / "cropped.mp4"
    subtitled = work_dir / "subtitled.mp4"

    ok, err = _download_video(source_url, raw)
    if not ok:
        return False, f"download: {err}"

    ok, err = _crop_9_16(raw, cropped)
    if not ok:
        return False, f"crop: {err}"

    sub_text = subtitle or "Check link in bio!"
    ok, err = _add_subtitle(cropped, subtitled, sub_text)
    if not ok:
        return False, f"subtitle: {err}"

    if speed_factor is not None and speed_factor != 1.0:
        sped = work_dir / "sped.mp4"
        ok, err = _change_speed(subtitled, sped, speed_factor)
        if not ok:
            return False, f"speed: {err}"
        shutil.copy2(sped, output_path)
    else:
        shutil.copy2(subtitled, output_path)

    return True, ""


def _process_remark(
    source_url: str,
    output_path: Path,
    subtitle: str,
    cta_text: str,
    speed_factor: float | None,
    work_dir: Path,
) -> tuple[bool, str]:
    """
    Remark mode:
      Same as reup, then adds a CTA overlay at the top.
    """
    reup_out = work_dir / "reup.mp4"
    ok, err = _process_reup(source_url, reup_out, subtitle, speed_factor, work_dir)
    if not ok:
        return False, err

    # CTA overlay at top
    cta = cta_text or "Get yours now! Link in bio 👆"
    ok, err = _add_subtitle(
        reup_out, output_path, cta,
        y_pos="60", font_size=_CTA_SIZE, color="yellow",
    )
    return ok, err


# ── Public API ────────────────────────────────────────────────────────────────

def process(candidate: dict[str, Any]) -> PipelineResult:
    """
    Process a content candidate into a local .mp4 file.

    candidate keys:
        mode        : "reup" | "remark"
        content_id  : str  (used for output filename)
        source_url  : str  (video to download/reuse)
        niche       : str  (used in subtitle if no subtitle provided)
        product_id  : str  (used in CTA if no cta_text provided)
        subtitle    : str  (optional — override auto-generated)
        cta_text    : str  (optional — override auto-generated)
        speed_factor: float (optional — e.g. 1.03 for 3% faster)

    Returns PipelineResult.
    """
    t0         = time.monotonic()
    mode       = candidate.get("mode", "reup").lower()
    content_id = candidate.get("content_id") or str(uuid.uuid4())
    source_url = candidate.get("source_url", "")

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = _OUTPUT_DIR / f"{content_id.replace(':', '_')}.mp4"

    # Return cached result if already processed
    if output_path.exists() and output_path.stat().st_size > 0:
        LOGGER.info("content_pipeline_cache_hit content_id=%s", content_id)
        return PipelineResult(
            success=True, content_id=content_id, mode=mode,
            video_path=str(output_path), elapsed_s=0.0,
            meta={"cached": True},
        )

    subtitle     = candidate.get("subtitle") or f"#{candidate.get('niche', 'viral')} check link in bio!"
    cta_text     = candidate.get("cta_text") or f"Shop now! #{candidate.get('product_id', 'deal')}"
    speed_factor = candidate.get("speed_factor")

    with tempfile.TemporaryDirectory(prefix="exec_pipeline_") as tmpdir:
        work_dir = Path(tmpdir)

        if mode == "remark":
            ok, err = _process_remark(
                source_url, output_path, subtitle, cta_text, speed_factor, work_dir,
            )
        else:
            # Default: reup
            ok, err = _process_reup(
                source_url, output_path, subtitle, speed_factor, work_dir,
            )

    elapsed = round(time.monotonic() - t0, 2)

    if ok and output_path.exists():
        size_mb = round(output_path.stat().st_size / 1_048_576, 2)
        LOGGER.info(
            "content_pipeline_success content_id=%s mode=%s size_mb=%.1f elapsed=%.1fs",
            content_id, mode, size_mb, elapsed,
        )
        return PipelineResult(
            success=True, content_id=content_id, mode=mode,
            video_path=str(output_path), elapsed_s=elapsed,
            meta={"size_mb": size_mb},
        )
    else:
        LOGGER.warning("content_pipeline_failed content_id=%s error=%s", content_id, err)
        return PipelineResult(
            success=False, content_id=content_id, mode=mode,
            error=err, elapsed_s=elapsed,
        )
