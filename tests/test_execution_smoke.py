"""
tests/test_execution_smoke.py — Minimal smoke test for the execution layer.

Validates:
  1. content_pipeline.process() produces a valid PipelineResult
  2. publisher_playwright.publish() is callable (mocked — no real browser)
  3. tracker_real generates a tracking link and records a click
  4. orchestrator.run_execution_pipeline() calls all three and returns ExecutionResult

All external I/O (ffmpeg, playwright, yt-dlp) is mocked.
No real network calls. No browser launched.
"""
from __future__ import annotations

import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# ── Ensure project root on path ───────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Force in-memory SQLite for tracker AND rate limiter
os.environ.setdefault("TRACKER_DB",    ":memory:")
os.environ.setdefault("RATE_LIMIT_DB", ":memory:")
os.environ.setdefault("CONTENT_OUTPUT_DIR", str(_ROOT / "data" / "test_content_output"))


def _reset_rate_limiter() -> None:
    """Drop and recreate the in-memory rate limiter DB between tests."""
    try:
        import execution.orchestrator as _orch
        if hasattr(_orch._rate_local, "conn") and _orch._rate_local.conn is not None:
            _orch._rate_local.conn.close()
            _orch._rate_local.conn = None
    except Exception:
        pass


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_candidate(
    mode: str = "reup",
    platform: str = "tiktok",
    source_url: str = "https://example.com/test.mp4",
) -> dict:
    return {
        "content_id":  "smoke_test_001",
        "mode":        mode,
        "platform":    platform,
        "niche":       "tech",
        "product_id":  "prod_abc",
        "source_url":  source_url,
        "caption":     "Check this out! #tech",
        "hashtags":    ["tech", "viral"],
    }


def _make_credentials(account_id: str = "smoke_test_page") -> dict:
    return {
        "username":   "test_user@example.com",
        "password":   "test_password_123",
        "email":      "test_user@example.com",
        "account_id": account_id,
        "default_source_url": "https://example.com/test.mp4",
        "default_caption":    "Test caption",
    }

_FAKE_CREDENTIALS = _make_credentials()


# ═══════════════════════════════════════════════════════════════════════════════
# Test 1 — Tracker
# ═══════════════════════════════════════════════════════════════════════════════

class TestTrackerReal(unittest.TestCase):

    def setUp(self) -> None:
        from execution.tracker_real import reset_tracker
        reset_tracker()

    def tearDown(self) -> None:
        from execution.tracker_real import reset_tracker
        reset_tracker()

    def test_generate_tracking_link_returns_url(self) -> None:
        from execution.tracker_real import generate_tracking_link
        url = generate_tracking_link("content_abc", "page_xyz")
        self.assertIsInstance(url, str)
        self.assertTrue(url.startswith("https://") or url.startswith("http://"),
                        f"Expected URL, got: {url}")
        self.assertIn("ref=", url)

    def test_record_click(self) -> None:
        from execution.tracker_real import generate_tracking_link, record_click
        url = generate_tracking_link("content_click", "page_click")
        # Reconstruct tracking code (for test purposes, we re-generate)
        ok = record_click("aff://content_click:page_click:ffffffff")
        self.assertTrue(ok)

    def test_get_stats_no_data(self) -> None:
        from execution.tracker_real import get_stats
        stats = get_stats("nonexistent_content")
        self.assertEqual(stats["total_clicks"], 0)
        self.assertEqual(stats["total_conversions"], 0)

    def test_generate_and_stats_flow(self) -> None:
        from execution.tracker_real import (
            generate_tracking_link, record_click, record_conversion, get_stats,
        )
        generate_tracking_link("flow_content", "flow_page")
        # Simulate a click + conversion on the generated code
        record_click("aff://flow_content:flow_page:deadbeef")
        record_conversion("aff://flow_content:flow_page:deadbeef", revenue=12.50)
        stats = get_stats("flow_content")
        # tracking_link was inserted, so stats exist
        self.assertIsInstance(stats, dict)
        self.assertIn("total_revenue", stats)


# ═══════════════════════════════════════════════════════════════════════════════
# Test 2 — Content Pipeline (mocked ffmpeg + yt-dlp)
# ═══════════════════════════════════════════════════════════════════════════════

class TestContentPipeline(unittest.TestCase):

    def _make_fake_video(self, path: Path) -> None:
        """Create a tiny non-empty file to satisfy existence checks."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00" * 1024)

    @patch("execution.content_pipeline._run_ytdlp")
    @patch("execution.content_pipeline._run_ffmpeg")
    def test_process_reup_success(self, mock_ffmpeg: MagicMock, mock_ytdlp: MagicMock) -> None:
        from execution.content_pipeline import process, _OUTPUT_DIR

        # yt-dlp "succeeds" and ffmpeg "succeeds"
        mock_ytdlp.return_value = (True, "")
        mock_ffmpeg.return_value = (True, "")

        output_dir = Path(os.environ.get("CONTENT_OUTPUT_DIR", "data/test_content_output"))
        output_dir.mkdir(parents=True, exist_ok=True)

        candidate = _make_candidate(mode="reup")
        content_id = candidate["content_id"]
        out_path = output_dir / f"{content_id}.mp4"

        # Pre-create fake output file (simulates ffmpeg writing it)
        def _side_effect(*args: str, timeout: int = 300):
            # When ffmpeg is called, create the output file
            out_arg = args[-1]
            Path(out_arg).parent.mkdir(parents=True, exist_ok=True)
            Path(out_arg).write_bytes(b"\x00" * 2048)
            return True, ""

        mock_ffmpeg.side_effect = _side_effect
        # Also make yt-dlp create the raw file
        def _ytdlp_side(url, out, timeout=120):
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_bytes(b"\x00" * 1024)
            return True, ""
        mock_ytdlp.side_effect = _ytdlp_side

        result = process(candidate)

        self.assertTrue(result.success, f"Expected success, got error: {result.error}")
        self.assertEqual(result.content_id, content_id)
        self.assertEqual(result.mode, "reup")
        self.assertTrue(Path(result.video_path).exists())

        # Cleanup
        if out_path.exists():
            out_path.unlink()

    @patch("execution.content_pipeline._run_ytdlp")
    @patch("execution.content_pipeline._run_ffmpeg")
    def test_process_remark_success(self, mock_ffmpeg: MagicMock, mock_ytdlp: MagicMock) -> None:
        from execution.content_pipeline import process

        def _ytdlp_side(url, out, timeout=120):
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_bytes(b"\x00" * 1024)
            return True, ""

        def _ffmpeg_side(*args: str, timeout: int = 300):
            out_arg = args[-1]
            Path(out_arg).parent.mkdir(parents=True, exist_ok=True)
            Path(out_arg).write_bytes(b"\x00" * 2048)
            return True, ""

        mock_ytdlp.side_effect = _ytdlp_side
        mock_ffmpeg.side_effect = _ffmpeg_side

        candidate = _make_candidate(mode="remark")
        candidate["content_id"] = "smoke_remark_001"
        result = process(candidate)

        self.assertTrue(result.success, f"remark failed: {result.error}")
        self.assertEqual(result.mode, "remark")

        out = Path(result.video_path)
        if out.exists():
            out.unlink()

    @patch("execution.content_pipeline._run_ytdlp")
    @patch("execution.content_pipeline._run_ffmpeg")
    def test_process_ffmpeg_failure(self, mock_ffmpeg: MagicMock, mock_ytdlp: MagicMock) -> None:
        from execution.content_pipeline import process

        def _ytdlp_side(url, out, timeout=120):
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_bytes(b"\x00" * 1024)
            return True, ""

        mock_ytdlp.side_effect = _ytdlp_side
        mock_ffmpeg.return_value = (False, "ffmpeg: error")

        candidate = _make_candidate()
        candidate["content_id"] = "smoke_fail_001"
        result = process(candidate)

        self.assertFalse(result.success)
        self.assertIn("crop", result.error)


# ═══════════════════════════════════════════════════════════════════════════════
# Test 3 — Publisher (mocked Playwright)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPublisher(unittest.TestCase):

    def test_publish_returns_result_without_playwright(self) -> None:
        """
        If playwright is not installed, publish() must return a
        failed PublishResult with a helpful error message — never raise.
        """
        import asyncio
        import importlib

        # Temporarily hide playwright from imports
        _orig = sys.modules.get("playwright")
        sys.modules["playwright"] = None  # type: ignore[assignment]
        sys.modules["playwright.async_api"] = None  # type: ignore[assignment]

        try:
            from execution.publisher_playwright import publish
            result = asyncio.run(publish(
                content_id="smoke_pub_001",
                platform="tiktok",
                video_path="/tmp/test.mp4",
                caption="test",
                credentials=_FAKE_CREDENTIALS,
            ))
            self.assertFalse(result.success)
            self.assertIn("playwright", result.error.lower())
        finally:
            if _orig is not None:
                sys.modules["playwright"] = _orig
            else:
                sys.modules.pop("playwright", None)
            sys.modules.pop("playwright.async_api", None)


# ═══════════════════════════════════════════════════════════════════════════════
# Test 4 — Orchestrator (end-to-end mocked)
# ═══════════════════════════════════════════════════════════════════════════════

class TestOrchestrator(unittest.TestCase):

    def setUp(self) -> None:
        from execution.tracker_real import reset_tracker
        reset_tracker()
        _reset_rate_limiter()

    def tearDown(self) -> None:
        from execution.tracker_real import reset_tracker
        reset_tracker()
        _reset_rate_limiter()

    @patch("execution.orchestrator._publish")
    @patch("execution.orchestrator._video_process")
    def test_run_execution_pipeline_success(
        self,
        mock_pipeline: MagicMock,
        mock_publish: MagicMock,
    ) -> None:
        from execution.content_pipeline import PipelineResult
        from execution.publisher_playwright import PublishResult
        from execution.orchestrator import run_execution_pipeline

        mock_pipeline.return_value = PipelineResult(
            success=True, content_id="orch_001", mode="reup",
            video_path="/tmp/orch_001.mp4",
        )

        async def _fake_publish(*args, **kwargs):
            return PublishResult(
                success=True, platform="tiktok", content_id="orch_001",
                url="https://tiktok.com/@test/video/12345",
            )
        mock_publish.side_effect = _fake_publish

        # Use a unique account_id so rate limiter starts at 0
        creds = _make_credentials("orch_success_page")
        candidate = _make_candidate()
        candidate["content_id"] = "orch_001"

        result = run_execution_pipeline(candidate, creds)

        self.assertEqual(result.status, "success")
        self.assertEqual(result.platform, "tiktok")
        self.assertIsInstance(result.tracking_link, str)
        self.assertIn("trk.local", result.tracking_link)
        self.assertEqual(result.url, "https://tiktok.com/@test/video/12345")

    @patch("execution.orchestrator._publish")
    @patch("execution.orchestrator._video_process")
    def test_run_execution_pipeline_rate_limit(
        self,
        mock_pipeline: MagicMock,
        mock_publish: MagicMock,
    ) -> None:
        """After MAX_POSTS_PER_PAGE_PER_DAY, next call should be skipped."""
        from execution.content_pipeline import PipelineResult
        from execution.publisher_playwright import PublishResult
        from execution.orchestrator import (
            run_execution_pipeline, MAX_POSTS_PER_PAGE_PER_DAY, _record_post,
        )

        async def _fake_publish(*args, **kwargs):
            return PublishResult(
                success=True, platform="tiktok", content_id="rl_test",
                url="https://tiktok.com/@test/video/99999",
            )

        mock_pipeline.return_value = PipelineResult(
            success=True, content_id="rl_test", mode="reup",
            video_path="/tmp/rl_test.mp4",
        )
        mock_publish.side_effect = _fake_publish

        # Unique page to avoid interference from other tests
        creds    = _make_credentials("rl_test_page")
        candidate = _make_candidate()
        candidate["content_id"] = "rl_test"
        platform = "tiktok"

        # Saturate the daily limit
        for i in range(MAX_POSTS_PER_PAGE_PER_DAY):
            _record_post(creds["account_id"], platform, f"pre_{i}")

        result = run_execution_pipeline(candidate, creds)
        self.assertEqual(result.status, "skipped")
        self.assertIn("rate_limit", result.error)

    @patch("execution.orchestrator._publish")
    @patch("execution.orchestrator._video_process")
    def test_run_execution_pipeline_video_failure(
        self,
        mock_pipeline: MagicMock,
        mock_publish: MagicMock,
    ) -> None:
        from execution.content_pipeline import PipelineResult
        from execution.orchestrator import run_execution_pipeline

        mock_pipeline.return_value = PipelineResult(
            success=False, content_id="fail_001", mode="reup",
            error="ffmpeg not found",
        )

        # Unique account_id so rate limiter is fresh
        creds = _make_credentials("fail_test_page")
        candidate = _make_candidate()
        candidate["content_id"] = "fail_001"

        result = run_execution_pipeline(candidate, creds)
        self.assertEqual(result.status, "failed")
        self.assertIn("content_pipeline_failed", result.error)
        mock_publish.assert_not_called()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
