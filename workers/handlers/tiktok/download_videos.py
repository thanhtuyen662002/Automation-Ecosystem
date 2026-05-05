"""
Handler: tiktok.download_videos
─────────────────────────────────
Input payload:
  job_id:      str  – used to namespace the output directory

Reads 'selected_videos' from parent select_videos result (via parent_results or payload).

Output result:
  video_paths:  list[str]   – local file paths of successfully downloaded videos
  failed_urls:  list[str]   – URLs that could not be downloaded
  output_dir:   str
  ok:           bool
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from pathlib import Path
from typing import Any

from workers.handlers.tiktok._base import (
    SubprocessError,
    check_already_processed,
    get_media_output_dir,
    random_jitter,
    resolve_parent_result,
    run_subprocess,
)

LOGGER = logging.getLogger("workers.handlers.tiktok.download_videos")


async def download_videos_handler(payload: dict[str, Any]) -> dict[str, Any]:
    # ── Idempotency guard ─────────────────────────────────────────────────────
    if (cached := check_already_processed(payload)) is not None:
        return cached

    # ── Resolve selected videos from parent result ────────────────────────────
    try:
        selected_videos: list[dict[str, Any]] = list(resolve_parent_result(payload, "selected_videos"))
    except KeyError:
        selected_videos_raw = payload.get("selected_videos")
        if not selected_videos_raw:
            raise ValueError("download_videos requires 'selected_videos' in payload or parent_results")
        selected_videos = list(selected_videos_raw)

    if not selected_videos:
        raise ValueError("selected_videos is empty — nothing to download")

    # ── Output directory ──────────────────────────────────────────────────────
    job_id: str = str(payload.get("job_id", "unknown_job"))
    base_output_dir = get_media_output_dir()
    output_dir = base_output_dir / job_id / "downloads"
    output_dir.mkdir(parents=True, exist_ok=True)

    LOGGER.info(
        "download_videos_start",
        extra={
            "event": "download_videos_start",
            "video_count": len(selected_videos),
            "output_dir": str(output_dir),
        },
    )

    await random_jitter(1.0, 3.0)

    video_paths: list[str] = []
    failed_urls: list[str] = []

    for idx, video in enumerate(selected_videos):
        url: str = video.get("url", "")
        if not url:
            LOGGER.warning(
                "download_skip_empty_url",
                extra={"event": "download_skip_empty_url", "index": idx},
            )
            continue

        output_template = str(output_dir / f"video_{idx:02d}.%(ext)s")
        try:
            LOGGER.info(
                "download_video_start",
                extra={"event": "download_video_start", "url": url, "index": idx},
            )
            await run_subprocess(
                "yt-dlp",
                "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "--merge-output-format", "mp4",
                "--no-playlist",
                "--quiet",
                "--output", output_template,
                url,
                timeout=180.0,
            )

            # Find the downloaded file (yt-dlp picks the extension)
            downloaded = list(output_dir.glob(f"video_{idx:02d}.*"))
            if downloaded:
                video_path = str(downloaded[0])
                video_paths.append(video_path)
                LOGGER.info(
                    "download_video_done",
                    extra={"event": "download_video_done", "url": url, "path": video_path},
                )
            else:
                LOGGER.warning(
                    "download_video_missing_file",
                    extra={"event": "download_video_missing_file", "url": url},
                )
                failed_urls.append(url)

        except SubprocessError as exc:
            LOGGER.error(
                "download_video_failed",
                extra={"event": "download_video_failed", "url": url, "error": str(exc)[:300]},
            )
            failed_urls.append(url)

        # Anti-abuse delay between downloads
        if idx < len(selected_videos) - 1:
            delay = random.uniform(3.0, 10.0)
            await asyncio.sleep(delay)

    if not video_paths:
        raise RuntimeError(
            f"All {len(selected_videos)} videos failed to download. URLs: {failed_urls[:3]}"
        )

    LOGGER.info(
        "download_videos_done",
        extra={
            "event": "download_videos_done",
            "downloaded": len(video_paths),
            "failed": len(failed_urls),
        },
    )

    return {
        "video_paths": video_paths,
        "failed_urls": failed_urls,
        "output_dir": str(output_dir),
        "ok": True,
    }
