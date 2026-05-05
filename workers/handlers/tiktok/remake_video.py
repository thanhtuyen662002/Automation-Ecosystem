"""
Handler: tiktok.remake_video
──────────────────────────────
Input payload:
  job_id:      str
  hook_text:   str   – overlay text (from extract_product_info title / custom)
  add_grain:   bool  = True
  bgm_path:    str | None  – optional local music file path

Reads from parent results:
  video_paths  ← download_videos.video_paths
  title        ← extract_product_info.title  (used as hook_text fallback)

FFmpeg pipeline per segment:
  1. Random 1–3s trim
  2. Slight zoom (scale=1.05–1.2, crop to original size)
  3. Random speed (setpts=PTS/factor where factor ∈ [0.9, 1.15])

Final composite:
  4. Concat all segments
  5. drawtext hook overlay (top-center, fade in/out)
  6. Optional noise/grain filter
  7. Optional amix background music

Output result:
  output_path:   str
  duration:      float  (seconds)
  segment_count: int
  ok:            bool
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import shutil
import tempfile
from pathlib import Path
from typing import Any

from workers.handlers.tiktok._base import (
    SubprocessError,
    check_already_processed,
    get_bgm_dir,
    get_media_output_dir,
    get_video_duration,
    pick_random_bgm,
    resolve_parent_result,
    run_subprocess,
)

LOGGER = logging.getLogger("workers.handlers.tiktok.remake_video")

# Segment parameters
_MIN_SEGMENT_SECONDS = 1.5
_MAX_SEGMENT_SECONDS = 3.0
_ZOOM_MIN = 1.05
_ZOOM_MAX = 1.20
_SPEED_MIN = 0.90
_SPEED_MAX = 1.15

# Output video spec
_OUTPUT_WIDTH = 1080
_OUTPUT_HEIGHT = 1920  # TikTok portrait
_OUTPUT_FPS = 30


async def remake_video_handler(payload: dict[str, Any]) -> dict[str, Any]:
    # ── Idempotency guard ─────────────────────────────────────────────────────
    if (cached := check_already_processed(payload)) is not None:
        return cached

    # ── Resolve inputs ────────────────────────────────────────────────────────
    try:
        video_paths: list[str] = list(resolve_parent_result(payload, "video_paths"))
    except KeyError:
        video_paths_raw = payload.get("video_paths")
        if not video_paths_raw:
            raise ValueError("remake_video requires 'video_paths' in payload or parent_results")
        video_paths = list(video_paths_raw)

    # hook_text: prefer explicit payload, then fall back to parent title
    hook_text: str = str(payload.get("hook_text", "")).strip()
    if not hook_text:
        try:
            hook_text = str(resolve_parent_result(payload, "title")).strip()
        except KeyError:
            hook_text = "Check this out!"

    add_grain: bool = bool(payload.get("add_grain", True))
    bgm_path_override: str | None = payload.get("bgm_path") or None

    job_id: str = str(payload.get("job_id", "unknown_job"))
    base_output_dir = get_media_output_dir()
    output_dir = base_output_dir / job_id / "remixed"
    output_dir.mkdir(parents=True, exist_ok=True)

    # BGM
    bgm_dir = get_bgm_dir()
    bgm_path = bgm_path_override or pick_random_bgm(bgm_dir)

    LOGGER.info(
        "remake_video_start",
        extra={
            "event": "remake_video_start",
            "video_count": len(video_paths),
            "hook_text": hook_text[:60],
            "add_grain": add_grain,
            "has_bgm": bool(bgm_path),
        },
    )

    with tempfile.TemporaryDirectory(prefix="ae_remake_") as tmpdir:
        tmp = Path(tmpdir)
        segment_files = await _cut_segments(video_paths, tmp)

        if not segment_files:
            raise RuntimeError("Failed to produce any video segments from input videos")

        random.shuffle(segment_files)

        concat_path = await _concat_segments(segment_files, tmp)
        output_path = output_dir / f"remixed_{job_id[:8]}.mp4"

        await _apply_final_effects(
            input_path=concat_path,
            output_path=output_path,
            hook_text=hook_text,
            add_grain=add_grain,
            bgm_path=bgm_path,
        )

    duration = await get_video_duration(str(output_path))

    LOGGER.info(
        "remake_video_done",
        extra={
            "event": "remake_video_done",
            "output_path": str(output_path),
            "duration": round(duration, 2),
            "segment_count": len(segment_files),
        },
    )

    return {
        "output_path": str(output_path),
        "duration": round(duration, 2),
        "segment_count": len(segment_files),
        "ok": True,
    }


# ── Segment cutting ───────────────────────────────────────────────────────────

async def _cut_segments(video_paths: list[str], tmpdir: Path) -> list[Path]:
    """Cut 1–3 random short segments from each source video."""
    segments: list[Path] = []
    for vid_idx, video_path in enumerate(video_paths):
        if not Path(video_path).exists():
            LOGGER.warning(
                "segment_source_missing",
                extra={"event": "segment_source_missing", "path": video_path},
            )
            continue

        try:
            total_duration = await get_video_duration(video_path)
        except Exception as exc:
            LOGGER.warning(
                "probe_failed",
                extra={"event": "probe_failed", "path": video_path, "error": str(exc)[:200]},
            )
            continue

        if total_duration < _MIN_SEGMENT_SECONDS + 1:
            LOGGER.warning(
                "video_too_short",
                extra={"event": "video_too_short", "path": video_path, "duration": total_duration},
            )
            continue

        # Number of segments to cut from this video (1–3)
        num_segments = random.randint(1, min(3, max(1, int(total_duration / 4))))

        for seg_idx in range(num_segments):
            seg_duration = random.uniform(_MIN_SEGMENT_SECONDS, _MAX_SEGMENT_SECONDS)
            max_start = max(0.0, total_duration - seg_duration - 0.5)
            start_time = random.uniform(0.0, max_start)

            seg_file = tmpdir / f"seg_{vid_idx:02d}_{seg_idx:02d}.mp4"
            zoom_factor = random.uniform(_ZOOM_MIN, _ZOOM_MAX)
            speed_factor = random.uniform(_SPEED_MIN, _SPEED_MAX)

            # Build scale+crop filter for zoom-in effect
            # Scale up by zoom_factor, then crop back to target dimensions
            scaled_w = int(_OUTPUT_WIDTH * zoom_factor)
            scaled_h = int(_OUTPUT_HEIGHT * zoom_factor)
            x_offset = (scaled_w - _OUTPUT_WIDTH) // 2
            y_offset = (scaled_h - _OUTPUT_HEIGHT) // 2

            vf = (
                f"scale={scaled_w}:{scaled_h}:force_original_aspect_ratio=increase,"
                f"crop={_OUTPUT_WIDTH}:{_OUTPUT_HEIGHT}:{x_offset}:{y_offset},"
                f"setpts={1.0 / speed_factor:.6f}*PTS,"
                f"fps={_OUTPUT_FPS}"
            )

            try:
                await run_subprocess(
                    "ffmpeg", "-y",
                    "-ss", str(round(start_time, 3)),
                    "-i", video_path,
                    "-t", str(round(seg_duration, 3)),
                    "-vf", vf,
                    "-af", f"atempo={min(max(speed_factor, 0.5), 2.0):.4f}",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    "-c:a", "aac", "-b:a", "128k",
                    "-movflags", "+faststart",
                    str(seg_file),
                    timeout=120.0,
                )
                if seg_file.exists() and seg_file.stat().st_size > 0:
                    segments.append(seg_file)
                    LOGGER.debug(
                        "segment_cut",
                        extra={
                            "event": "segment_cut",
                            "file": seg_file.name,
                            "start": round(start_time, 2),
                            "duration": round(seg_duration, 2),
                            "zoom": round(zoom_factor, 3),
                            "speed": round(speed_factor, 3),
                        },
                    )
            except SubprocessError as exc:
                LOGGER.warning(
                    "segment_cut_failed",
                    extra={"event": "segment_cut_failed", "error": str(exc)[:300]},
                )

    return segments


# ── Concat ────────────────────────────────────────────────────────────────────

async def _concat_segments(segment_files: list[Path], tmpdir: Path) -> Path:
    """Concatenate segments using ffmpeg concat demuxer."""
    concat_list = tmpdir / "concat_list.txt"
    lines = [f"file '{str(f).replace(chr(39), chr(92) + chr(39))}'\n" for f in segment_files]
    concat_list.write_text("".join(lines), encoding="utf-8")

    concat_out = tmpdir / "concat.mp4"
    await run_subprocess(
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        str(concat_out),
        timeout=300.0,
    )
    return concat_out


# ── Final effects ─────────────────────────────────────────────────────────────

async def _apply_final_effects(
    input_path: Path,
    output_path: Path,
    hook_text: str,
    add_grain: bool,
    bgm_path: str | None,
) -> None:
    """Apply text overlay, grain noise, and optional BGM to the concatenated clip."""
    # Escape special chars for FFmpeg drawtext
    safe_text = _escape_drawtext(hook_text[:60])

    # Font size relative to output width
    fontsize = max(48, _OUTPUT_WIDTH // 18)

    # Build video filter chain
    vf_parts: list[str] = [
        # Text overlay: top-center with fade in over first 0.5s, fade out over last 0.5s
        (
            f"drawtext=text='{safe_text}':"
            f"fontsize={fontsize}:fontcolor=white:"
            f"borderw=3:bordercolor=black:"
            f"x=(w-text_w)/2:y=h*0.08:"
            f"alpha='if(lt(t,0.5),t/0.5,if(lt(t,2.5),1,if(lt(t,3),(3-t)/0.5,0)))'"
        )
    ]

    if add_grain:
        vf_parts.append(
            # Subtle film grain — noise amplitude 8, independent per frame
            "noise=alls=8:allf=t"
        )

    vf_chain = ",".join(vf_parts)

    # Build input/output args
    ffmpeg_args = [
        "ffmpeg", "-y",
        "-i", str(input_path),
    ]

    if bgm_path and Path(bgm_path).exists():
        # Mix background music at -18dB under the original audio
        ffmpeg_args += [
            "-i", bgm_path,
            "-filter_complex",
            f"[0:v]{vf_chain}[vout];[1:a]volume=-18dB[bgm];[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=3[aout]",
            "-map", "[vout]",
            "-map", "[aout]",
        ]
    else:
        ffmpeg_args += [
            "-vf", vf_chain,
        ]

    ffmpeg_args += [
        "-c:v", "libx264", "-preset", "medium", "-crf", "21",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(output_path),
    ]

    await run_subprocess(*ffmpeg_args, timeout=600.0)


def _escape_drawtext(text: str) -> str:
    """Escape characters that FFmpeg drawtext filter treats as special."""
    replacements = [
        ("\\", "\\\\"),
        ("'", "\\'"),
        (":", "\\:"),
        ("%", "\\%"),
    ]
    for src, dst in replacements:
        text = text.replace(src, dst)
    return text
