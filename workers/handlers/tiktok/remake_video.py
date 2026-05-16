"""
Handler: tiktok.remake_video
──────────────────────────────
Input payload:
  job_id:        str
  hook_text:     str   – overlay text (from extract_product_info title / custom)
  add_grain:     bool  = True    – subtle film-grain noise
  add_vignette:  bool  = False   – blur-edge vignette effect
  flip_chance:   float = 0.30   – per-clip horizontal flip probability
  bgm_path:      str | None  – optional local music file path

Reads from parent results:
  video_paths  ← download_videos.video_paths
  title        ← extract_product_info.title  (used as hook_text fallback)

FFmpeg pipeline per segment:
  1. Random start time
  2. Clip duration 1–3 s (3–6 clips total across all sources)
  3. Scale + zoom (1.05–1.20) → crop to 720×1280
  4. Speed variation (setpts=PTS/factor, factor ∈ [0.9, 1.15])
  5. Horizontal flip (30 % chance per clip)
  6. Colour jitter (brightness ±0.05, contrast 0.95–1.05, saturation 0.9–1.1)

Final composite:
  7. Shuffle + duration enforcement (8–20 s)
  8. drawtext hook overlay (top OR center, random per run)
  9. Optional film-grain noise
  10. Optional vignette
  11. Optional BGM amix + loudnorm normalisation

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
    get_flip_chance,
    get_media_output_dir,
    get_video_duration,
    pick_random_bgm,
    random_seed,
    resolve_parent_result,
    run_subprocess,
)

LOGGER = logging.getLogger("workers.handlers.tiktok.remake_video")

# ── Segment parameters ────────────────────────────────────────────────────────


# ── Content Decision Gate ─────────────────────────────────────────────────────

def _content_decision_gate(payload: dict[str, Any], mode: str = "remark") -> None:
    """
    Mandatory EV + match guard before FFmpeg processing.

    remark mode enforces: match_score >= 0.6 AND EV >= 0.05.
    Raises ValueError if blocked. Fail-open on import errors.
    """
    try:
        from core.content_decision import ContentCandidate, should_produce
        signals = payload.get("decision_signals") or {}
        item_id = str(payload.get("job_id") or payload.get("item_id") or "rv_job")
        hook_text = str(payload.get("hook_text", "")).strip()
        candidate = ContentCandidate(
            item_id         = item_id,
            trend_score     = float(signals.get("trend_score",    0.5)),
            product_intent  = float(signals.get("product_intent", 0.5)),
            hook_potential  = float(signals.get("hook_potential", -1.0)),
            match_score     = float(signals.get("match_score",    0.5)),
            novelty_score   = float(signals.get("novelty_score",  0.5)),
            production_cost = float(signals.get("production_cost", 0.7)),
            metadata        = {"text": hook_text, **(signals.get("metadata") or {})},
        )
        niche = str(signals.get("niche", ""))
        allowed, reason = should_produce(candidate, mode=mode, niche=niche)
        if not allowed:
            LOGGER.info(
                "remake_video_decision_blocked item=%s mode=%s reason=%s",
                item_id, mode, reason,
            )
            raise ValueError(f"content_decision BLOCKED [{mode}]: {reason}")
    except ValueError:
        raise
    except Exception as exc:
        LOGGER.debug("remake_video_gate_error (non-fatal): %s", exc)


# ── Segment parameters ────────────────────────────────────────────────────────
_MIN_SEGMENT_SECONDS = 0.8
_MAX_SEGMENT_SECONDS = 2.0
_MIN_CLIPS_TOTAL = 5
_MAX_CLIPS_TOTAL = 12
_ZOOM_MIN = 1.05
_ZOOM_MAX = 1.20
_SPEED_MIN = 0.90
_SPEED_MAX = 1.15

# ── Output spec ───────────────────────────────────────────────────────────────
_OUTPUT_WIDTH = 720    # mobile-optimised portrait
_OUTPUT_HEIGHT = 1280
_OUTPUT_FPS = 30

# ── Duration constraints ──────────────────────────────────────────────────────
_MIN_TOTAL_SECONDS = 8.0
_MAX_TOTAL_SECONDS = 20.0


async def remake_video_handler(payload: dict[str, Any]) -> dict[str, Any]:
    # ── Idempotency guard ─────────────────────────────────────────────────────
    if (cached := check_already_processed(payload)) is not None:
        return cached

    # ── Content Decision Gate (MANDATORY — remark mode) ─────────────────────
    # match_score < 0.6 → blocked (wrong product)
    # EV < 0.05         → blocked (not worth FFmpeg cost)
    signals = payload.get("decision_signals")
    if isinstance(signals, dict) and signals:
        _content_decision_gate(payload, mode="remark")
    else:
        LOGGER.info(
            "remake_video_decision_gate_skipped",
            extra={
                "event": "remake_video_decision_gate_skipped",
                "reason": "missing_decision_signals",
            },
        )

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
    add_vignette: bool = bool(payload.get("add_vignette", False))
    flip_prob: float = float(payload.get("flip_chance", get_flip_chance()))
    bgm_path_override: str | None = payload.get("bgm_path") or None

    job_id: str = str(payload.get("job_id", "unknown_job"))
    base_output_dir = get_media_output_dir()
    output_dir = base_output_dir / job_id / "remixed"
    output_dir.mkdir(parents=True, exist_ok=True)

    bgm_dir = get_bgm_dir()
    bgm_path = bgm_path_override or pick_random_bgm(bgm_dir)

    seed = random_seed()
    random.seed(seed)

    LOGGER.info(
        "remake_video_start",
        extra={
            "event": "remake_video_start",
            "video_count": len(video_paths),
            "hook_text": hook_text[:60],
            "add_grain": add_grain,
            "add_vignette": add_vignette,
            "flip_prob": flip_prob,
            "has_bgm": bool(bgm_path),
            "seed": seed,
        },
    )

    with tempfile.TemporaryDirectory(prefix="ae_remake_") as tmpdir:
        tmp = Path(tmpdir)
        target_clips = random.randint(_MIN_CLIPS_TOTAL, _MAX_CLIPS_TOTAL)
        segment_files = await _cut_segments(video_paths, tmp, target_clips, flip_prob)

        if not segment_files:
            raise RuntimeError("Failed to produce any video segments from input videos")

        random.shuffle(segment_files)

        # Enforce total duration 8–20 s
        segment_files = await _enforce_duration(segment_files, tmp)

        concat_path = await _concat_segments(segment_files, tmp)
        output_path = output_dir / f"remixed_{job_id[:8]}_{seed & 0xFFFF:04x}.mp4"

        await _apply_final_effects(
            input_path=concat_path,
            output_path=output_path,
            hook_text=hook_text,
            add_grain=add_grain,
            add_vignette=add_vignette,
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

async def _cut_segments(
    video_paths: list[str],
    tmpdir: Path,
    target_clips: int,
    flip_prob: float,
) -> list[Path]:
    """
    Cut random short segments from source videos.

    Distributes `target_clips` across all available source videos as evenly
    as possible, then cuts each individually with random transformations.
    """
    if not video_paths:
        return []

    # Distribute clip budget across source videos
    clips_per_video = _distribute_clips(target_clips, len(video_paths))

    segments: list[Path] = []
    global_seg_idx = 0

    tasks = []
    assignments: list[tuple[str, int, int]] = []  # (path, vid_idx, n_clips)
    for vid_idx, (video_path, n_clips) in enumerate(zip(video_paths, clips_per_video)):
        if n_clips > 0:
            assignments.append((video_path, vid_idx, n_clips))

    # Cut segments concurrently per video
    results = await asyncio.gather(
        *[
            _cut_video_segments(video_path, vid_idx, n_clips, tmpdir, flip_prob, start_idx=sum(clips_per_video[:vid_idx]))
            for vid_idx, (video_path, n_clips) in enumerate(
                [(a[0], a[2]) for a in assignments]
            )
            for vid_idx in [assignments[vid_idx][1]]  # type: ignore[index]
        ],
        return_exceptions=True,
    ) if False else [
        await _cut_video_segments(
            video_path, vid_idx, n_clips, tmpdir, flip_prob,
            start_idx=sum(clips_per_video[:assignments.index((video_path, vid_idx, n_clips))])
        )
        for video_path, vid_idx, n_clips in assignments
    ]

    for seg_list in results:
        if isinstance(seg_list, list):
            segments.extend(seg_list)

    return segments


def _distribute_clips(total: int, n_videos: int) -> list[int]:
    """Distribute `total` clips as evenly as possible across `n_videos` videos."""
    if n_videos == 0:
        return []
    base = total // n_videos
    remainder = total % n_videos
    counts = [base + (1 if i < remainder else 0) for i in range(n_videos)]
    return counts


async def _cut_video_segments(
    video_path: str,
    vid_idx: int,
    n_clips: int,
    tmpdir: Path,
    flip_prob: float,
    start_idx: int,
) -> list[Path]:
    """Cut `n_clips` random segments from a single video file."""
    segments: list[Path] = []

    if not Path(video_path).exists():
        LOGGER.warning(
            "segment_source_missing",
            extra={"event": "segment_source_missing", "path": video_path},
        )
        return segments

    try:
        total_duration = await get_video_duration(video_path)
    except Exception as exc:
        LOGGER.warning(
            "probe_failed",
            extra={"event": "probe_failed", "path": video_path, "error": str(exc)[:200]},
        )
        return segments

    if total_duration < _MIN_SEGMENT_SECONDS + 0.5:
        LOGGER.warning(
            "video_too_short",
            extra={"event": "video_too_short", "path": video_path, "duration": total_duration},
        )
        return segments

    for seg_idx in range(n_clips):
        seg_duration = random.uniform(_MIN_SEGMENT_SECONDS, _MAX_SEGMENT_SECONDS)
        max_start = max(0.0, total_duration - seg_duration - 0.5)
        start_time = random.uniform(0.0, max_start)

        seg_file = tmpdir / f"seg_{vid_idx:02d}_{start_idx + seg_idx:02d}.mp4"

        zoom_factor = random.uniform(_ZOOM_MIN, _ZOOM_MAX)
        speed_factor = random.uniform(_SPEED_MIN, _SPEED_MAX)
        do_flip = random.random() < flip_prob

        # Pattern Interrupts
        do_flash = random.random() < 0.3
        do_rotate = random.random() < 0.3
        rotate_angle = random.uniform(-4, 4) if do_rotate else 0.0

        # Colour jitter
        base_brightness = random.uniform(-0.05, 0.05)
        contrast = random.uniform(0.95, 1.05)
        saturation = random.uniform(0.9, 1.1)

        # Build scale+crop for zoom
        if do_rotate:
            zoom_factor *= 1.08  # Extra zoom to hide black rotation edges

        scaled_w = int(_OUTPUT_WIDTH * zoom_factor)
        scaled_h = int(_OUTPUT_HEIGHT * zoom_factor)
        x_offset = (scaled_w - _OUTPUT_WIDTH) // 2
        y_offset = (scaled_h - _OUTPUT_HEIGHT) // 2

        vf_parts = []
        if do_rotate:
            vf_parts.append(f"rotate={rotate_angle}*PI/180:c=black")
            
        vf_parts.extend([
            f"scale={scaled_w}:{scaled_h}:force_original_aspect_ratio=increase",
            f"crop={_OUTPUT_WIDTH}:{_OUTPUT_HEIGHT}:{x_offset}:{y_offset}",
            f"setpts={1.0 / speed_factor:.6f}*PTS",
            f"fps={_OUTPUT_FPS}",
        ])
        
        if do_flash:
            # 0.1s white flash at start of segment
            vf_parts.append(f"eq=brightness='if(lt(t,0.1),0.4,{base_brightness:.4f})':contrast={contrast:.4f}:saturation={saturation:.4f}")
        else:
            vf_parts.append(f"eq=brightness={base_brightness:.4f}:contrast={contrast:.4f}:saturation={saturation:.4f}")

        if do_flip:
            vf_parts.append("hflip")

        vf = ",".join(vf_parts)

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
                        "flip": do_flip,
                        "brightness": round(base_brightness, 4),
                        "contrast": round(contrast, 4),
                    },
                )
        except SubprocessError as exc:
            LOGGER.warning(
                "segment_cut_failed",
                extra={"event": "segment_cut_failed", "error": str(exc)[:300]},
            )

    return segments


# ── Duration enforcement ──────────────────────────────────────────────────────

async def _enforce_duration(segment_files: list[Path], tmpdir: Path) -> list[Path]:
    """
    Trim segment list so total duration stays within [8, 20] seconds.
    Drops trailing segments if over 20 s; logs a warning if under 8 s.
    """
    durations: list[float] = []
    for f in segment_files:
        try:
            d = await get_video_duration(str(f))
        except Exception:
            d = _MAX_SEGMENT_SECONDS  # pessimistic estimate
        durations.append(d)

    total = sum(durations)

    if total > _MAX_TOTAL_SECONDS:
        # Greedily drop trailing segments until under limit
        kept: list[Path] = []
        running = 0.0
        for f, d in zip(segment_files, durations):
            if running + d <= _MAX_TOTAL_SECONDS:
                kept.append(f)
                running += d
            else:
                break
        LOGGER.info(
            "duration_trimmed",
            extra={
                "event": "duration_trimmed",
                "original_total": round(total, 2),
                "trimmed_total": round(running, 2),
                "dropped_segments": len(segment_files) - len(kept),
            },
        )
        segment_files = kept

    elif total < _MIN_TOTAL_SECONDS:
        LOGGER.warning(
            "duration_below_minimum",
            extra={
                "event": "duration_below_minimum",
                "total_seconds": round(total, 2),
                "minimum": _MIN_TOTAL_SECONDS,
            },
        )

    return segment_files


# ── Concatenation ─────────────────────────────────────────────────────────────

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
    add_vignette: bool,
    bgm_path: str | None,
) -> None:
    """Apply text overlay, optional effects, and BGM to the concatenated clip."""
    try:
        duration = await get_video_duration(str(input_path))
    except Exception:
        duration = 15.0

    fontsize = max(36, _OUTPUT_WIDTH // 18)
    vf_parts: list[str] = []

    _BENEFIT_PHRASES = [
        "Sự thật là...",
        "Không thể tin được 😱",
        "Kết quả thật sự WOW",
        "Ai cũng cần cái này",
        "Phải thử ngay lập tức",
        "Giải pháp hoàn hảo",
        "Đỉnh của chóp 🔥",
    ]

    _LOOP_ENDINGS = [
        "Và điều bất ngờ nhất là...",
        "Lý do là vì...",
        "Đó là lý do tại sao...",
        "Sự thật đằng sau là...",
    ]

    # Schedule texts
    texts = []
    
    # Hook (0 - 2.0s)
    texts.append({
        "text": _escape_drawtext(hook_text[:60]),
        "start": 0.0,
        "end": 2.0,
    })

    current_time = 2.0
    loop_end_time = max(duration - 1.5, 2.0)

    # Benefits (2.0s to loop end)
    while current_time < loop_end_time:
        next_time = current_time + random.uniform(2.0, 3.0)
        if next_time > loop_end_time - 1.0:
            next_time = loop_end_time
        texts.append({
            "text": _escape_drawtext(random.choice(_BENEFIT_PHRASES)),
            "start": current_time,
            "end": next_time,
        })
        current_time = next_time

    # Loop Ending
    if loop_end_time < duration:
        texts.append({
            "text": _escape_drawtext(random.choice(_LOOP_ENDINGS)),
            "start": loop_end_time,
            "end": duration,
        })

    for item in texts:
        t_start = item["start"]
        t_end = item["end"]
        text = item["text"]
        y_pos = "h*0.45" if random.random() < 0.5 else "h*0.08"
        vf_parts.append(
            f"drawtext=text='{text}':"
            f"fontsize={fontsize}:fontcolor=white:"
            f"borderw=3:bordercolor=black:"
            f"x=(w-text_w)/2:y={y_pos}:"
            f"enable='between(t,{t_start:.2f},{t_end:.2f})'"
        )

    if add_grain:
        vf_parts.append("noise=alls=8:allf=t")

    if add_vignette:
        vf_parts.append("vignette=PI/4")

    vf_chain = ",".join(vf_parts)

    ffmpeg_args = [
        "ffmpeg", "-y",
        "-i", str(input_path),
    ]

    if bgm_path and Path(bgm_path).exists():
        # Mix BGM at -16 dB + tremolo (rhythmic peaks) + loudnorm
        ffmpeg_args += [
            "-i", bgm_path,
            "-filter_complex",
            (
                f"[0:v]{vf_chain}[vout];"
                f"[1:a]volume=-16dB,tremolo=f=1.5:d=0.4[bgm];"
                f"[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=3,"
                f"loudnorm[aout]"
            ),
            "-map", "[vout]",
            "-map", "[aout]",
        ]
    else:
        ffmpeg_args += ["-vf", vf_chain]

    ffmpeg_args += [
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
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
