"""
Unit tests for tiktok.remake_video handler.

FFmpeg and ffprobe calls are mocked — no real media files required.
Updated for the advanced remake engine:
  - 720×1280 output
  - get_flip_chance / random_seed patched
  - vignette flag support
"""

from __future__ import annotations

import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_remake_video_missing_video_paths():
    from workers.handlers.tiktok.remake_video import remake_video_handler

    with pytest.raises(ValueError, match="video_paths"):
        await remake_video_handler({"job_id": "test-job"})


@pytest.mark.asyncio
async def test_remake_video_skips_decision_gate_without_signals(monkeypatch):
    from workers.handlers.tiktok import remake_video as module

    def fail_gate(*_args, **_kwargs):
        raise AssertionError("decision gate should not run without decision_signals")

    monkeypatch.setattr(module, "_content_decision_gate", fail_gate)

    with pytest.raises(ValueError, match="video_paths"):
        await module.remake_video_handler({"job_id": "test-job"})


@pytest.mark.asyncio
async def test_remake_video_idempotency():
    from workers.handlers.tiktok.remake_video import remake_video_handler

    cached = {"output_path": "/tmp/x.mp4", "duration": 12.5, "segment_count": 3, "ok": True}
    result = await remake_video_handler({"_idempotent_result": cached})
    assert result == cached


@pytest.mark.asyncio
async def test_escape_drawtext():
    from workers.handlers.tiktok.remake_video import _escape_drawtext

    text = "It's 50% off: Buy now!"
    escaped = _escape_drawtext(text)
    assert "\\'" in escaped   # apostrophe escaped
    assert "\\:" in escaped   # colon escaped
    assert "\\%" in escaped   # percent escaped


@pytest.mark.asyncio
async def test_remake_video_calls_ffmpeg(tmp_path, monkeypatch):
    """Smoke test: handler creates output file when mocked subprocess returns success."""
    from workers.handlers.tiktok import remake_video as module

    # Create fake input video files
    fake_video = tmp_path / "video_00.mp4"
    fake_video.write_bytes(b"fake")

    call_log: list[tuple] = []

    async def fake_run_subprocess(*args, **kwargs):
        call_log.append(args)
        if "ffprobe" in args[0]:
            import json
            return json.dumps({"format": {"duration": "15.0"}}), ""
        # Create stub segment output files
        for arg in args:
            if isinstance(arg, str) and arg.endswith(".mp4") and "seg_" in arg:
                Path(arg).write_bytes(b"fake_segment")
            if isinstance(arg, str) and "concat.mp4" in arg:
                Path(arg).write_bytes(b"fake_concat")
        return "", ""

    async def fake_get_duration(path):
        return 15.0

    monkeypatch.setattr(module, "run_subprocess", fake_run_subprocess)
    monkeypatch.setattr(module, "get_video_duration", fake_get_duration)
    monkeypatch.setattr(module, "get_media_output_dir", lambda: tmp_path)
    monkeypatch.setattr(module, "get_bgm_dir", lambda: None)
    monkeypatch.setattr(module, "pick_random_bgm", lambda _: None)
    monkeypatch.setattr(module, "get_flip_chance", lambda: 0.30)
    monkeypatch.setattr(module, "random_seed", lambda: 12345)

    async def fake_apply(input_path, output_path, **kwargs):
        output_path.write_bytes(b"fake_final_video")

    monkeypatch.setattr(module, "_apply_final_effects", fake_apply)

    payload = {
        "job_id": "test-job-1234",
        "video_paths": [str(fake_video)],
        "hook_text": "Amazing product!",
    }

    result = await module.remake_video_handler(payload)
    assert result["ok"] is True
    assert "output_path" in result
    assert result["duration"] == 15.0


@pytest.mark.asyncio
async def test_remake_video_vignette_flag(tmp_path, monkeypatch):
    """Handler should accept add_vignette flag without error."""
    from workers.handlers.tiktok import remake_video as module

    fake_video = tmp_path / "video_00.mp4"
    fake_video.write_bytes(b"fake")

    async def fake_run_subprocess(*args, **kwargs):
        for arg in args:
            if isinstance(arg, str) and arg.endswith(".mp4") and "seg_" in arg:
                Path(arg).write_bytes(b"fake_segment")
            if isinstance(arg, str) and "concat.mp4" in arg:
                Path(arg).write_bytes(b"fake_concat")
        return "", ""

    async def fake_get_duration(path):
        return 10.0

    monkeypatch.setattr(module, "run_subprocess", fake_run_subprocess)
    monkeypatch.setattr(module, "get_video_duration", fake_get_duration)
    monkeypatch.setattr(module, "get_media_output_dir", lambda: tmp_path)
    monkeypatch.setattr(module, "get_bgm_dir", lambda: None)
    monkeypatch.setattr(module, "pick_random_bgm", lambda _: None)
    monkeypatch.setattr(module, "get_flip_chance", lambda: 0.0)
    monkeypatch.setattr(module, "random_seed", lambda: 99999)

    async def fake_apply(input_path, output_path, **kwargs):
        output_path.write_bytes(b"fake_final_video")

    monkeypatch.setattr(module, "_apply_final_effects", fake_apply)

    payload = {
        "job_id": "test-vignette",
        "video_paths": [str(fake_video)],
        "hook_text": "Cool!",
        "add_grain": False,
        "add_vignette": True,
    }

    result = await module.remake_video_handler(payload)
    assert result["ok"] is True
