from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


def test_write_netscape_cookie_file_sanitizes_fields(tmp_path):
    from workers.handlers.tiktok.download_videos import _write_netscape_cookie_file

    cookie_file = tmp_path / "cookies.txt"
    _write_netscape_cookie_file(
        [
            {
                "domain": ".tiktok.com",
                "path": "/",
                "secure": True,
                "expires": 1893456000,
                "name": "sessionid",
                "value": "abc\t123\n456",
            },
            {
                "domain": "example.com",
                "path": "/",
                "secure": False,
                "expires": 0,
                "name": "ignored",
                "value": "secret",
            },
        ],
        cookie_file,
    )

    text = cookie_file.read_text(encoding="utf-8")
    lines = text.splitlines()

    assert lines[0] == "# Netscape HTTP Cookie File"
    assert len(lines) == 2
    assert lines[1].split("\t") == [
        ".tiktok.com",
        "TRUE",
        "/",
        "TRUE",
        "1893456000",
        "sessionid",
        "abc 123 456",
    ]
    assert "example.com" not in text


def test_write_netscape_cookie_file_clamps_negative_expires(tmp_path):
    from workers.handlers.tiktok.download_videos import _write_netscape_cookie_file

    cookie_file = tmp_path / "cookies.txt"
    _write_netscape_cookie_file(
        [
            {
                "domain": ".tiktok.com",
                "path": "/",
                "secure": True,
                "expires": -1,
                "name": "sessionid",
                "value": "abc",
            },
        ],
        cookie_file,
    )

    fields = cookie_file.read_text(encoding="utf-8").splitlines()[1].split("\t")
    assert fields[4] == "0"


def test_filter_tiktok_cookies_only_keeps_tiktok_domains():
    from workers.handlers.tiktok.download_videos import _filter_tiktok_cookies

    assert _filter_tiktok_cookies([]) == []
    assert _filter_tiktok_cookies([{"domain": ".google.com"}]) == []
    assert _filter_tiktok_cookies([{"domain": ".tiktok.com"}, {"domain": "www.tiktok.com"}]) == [
        {"domain": ".tiktok.com"},
        {"domain": "www.tiktok.com"},
    ]


def test_require_tiktok_cookies_raises_when_missing():
    from workers.handlers.tiktok.download_videos import _require_tiktok_cookies

    with pytest.raises(RuntimeError, match="No TikTok cookies exported"):
        _require_tiktok_cookies([{"domain": ".google.com"}], "account-1")


def test_require_tiktok_cookies_returns_tiktok_cookies():
    from workers.handlers.tiktok.download_videos import _require_tiktok_cookies

    cookies = [{"domain": ".tiktok.com", "name": "sessionid", "value": "dummy"}]

    assert _require_tiktok_cookies(cookies, "account-1") == cookies


def test_find_downloaded_file_ignores_temp_files(tmp_path):
    from workers.handlers.tiktok.download_videos import _find_downloaded_file

    part_file = tmp_path / "video_00.part"
    part_file.write_bytes(b"partial")

    assert _find_downloaded_file(tmp_path, 0) is None


def test_find_downloaded_file_prefers_valid_mp4(tmp_path):
    from workers.handlers.tiktok.download_videos import _find_downloaded_file

    part_file = tmp_path / "video_00.part"
    webm_file = tmp_path / "video_00.webm"
    mp4_file = tmp_path / "video_00.mp4"

    part_file.write_bytes(b"partial")
    webm_file.write_bytes(b"webm")
    mp4_file.write_bytes(b"mp4")

    assert _find_downloaded_file(tmp_path, 0) == mp4_file


def test_find_downloaded_file_ignores_empty_and_invalid_files(tmp_path):
    from workers.handlers.tiktok.download_videos import _find_downloaded_file

    (tmp_path / "video_00.mp4").write_bytes(b"")
    (tmp_path / "video_00.info.json").write_text("{}", encoding="utf-8")
    webm_file = tmp_path / "video_00.webm"
    webm_file.write_bytes(b"webm")

    assert _find_downloaded_file(tmp_path, 0) == webm_file


def test_ytdlp_format_default_and_env_override(monkeypatch):
    from workers.handlers.tiktok import download_videos as module

    monkeypatch.delenv("TIKTOK_YTDLP_FORMAT", raising=False)
    assert module._ytdlp_format() == "bestvideo*+bestaudio/best[ext=mp4]/best"

    monkeypatch.setenv("TIKTOK_YTDLP_FORMAT", "best")
    assert module._ytdlp_format() == "best"


def test_download_provider_allowed_values(monkeypatch):
    from workers.handlers.tiktok import download_videos as module

    for provider in ("auto", "web_only", "browser_first", "ytdlp_only", "mobile_first"):
        monkeypatch.setenv("TIKTOK_DOWNLOAD_PROVIDER", provider)
        assert module._download_provider_from_env() == provider

    monkeypatch.setenv("TIKTOK_DOWNLOAD_PROVIDER", "browser_ytdlp_mobile")
    assert module._download_provider_from_env() == "auto"


def test_browser_media_candidate_filter_prioritizes_real_video():
    from workers.handlers.tiktok import download_videos as module

    candidates = module._dedupe_browser_candidates([
        {"url": "https://example.com/cover.jpg", "content_type": "image/jpeg", "source": "network"},
        {"url": "https://v16-webapp-prime.tiktok.com/video?a=1", "content_type": "video/mp4", "source": "network"},
        {"url": "https://example.com/play?mime_type=video_mp4", "content_type": "", "source": "dom"},
    ])

    assert len(candidates) == 2
    assert candidates[0]["content_type"] == "video/mp4"
    assert all("image" not in str(candidate.get("content_type")) for candidate in candidates)


def test_parse_ytdlp_impersonate_targets_skips_unavailable():
    from workers.handlers.tiktok import download_videos as module

    output = """[info] Available impersonate targets
Client    OS   Source
--------------------------------------------
Edge      -    curl_cffi
Chrome    -    curl_cffi (unavailable)
Safari    -    curl_cffi
"""

    assert module._parse_ytdlp_impersonate_targets(output) == {"edge", "safari"}


def test_auto_provider_routes_tiktok_shop_to_mobile_first():
    from workers.handlers.tiktok import download_videos as module

    assert module._effective_download_provider(
        "auto",
        {"source": "mobile_tiktok_shop", "requires_mobile_app": True},
        "https://www.tiktok.com/@shop/video/1",
    ) == "mobile_first"
    assert module._effective_download_provider(
        "browser_first",
        {"source": "mobile_tiktok_shop", "requires_mobile_app": True},
        "https://www.tiktok.com/@shop/video/1",
    ) == "mobile_first"
    assert module._effective_download_provider(
        "auto",
        {},
        "https://www.tiktok.com/@public/video/2",
    ) == "browser_first"


@pytest.mark.asyncio
async def test_detects_tiktok_shop_app_only_gate():
    from workers.handlers.tiktok import download_videos as module

    class FakePage:
        url = "https://www.tiktok.com/@shop/video/1"

        async def evaluate(self, _script):
            return "Xem video TikTok Shop trong ứng dụng TikTok"

        async def title(self):
            return "TikTok Shop"

    result = await module._detect_tiktok_app_only_gate(FakePage())

    assert result is not None
    assert result["gate_text"] == "Xem video TikTok Shop trong ứng dụng TikTok"
    assert result["current_url"] == "https://www.tiktok.com/@shop/video/1"


@pytest.mark.asyncio
async def test_download_uses_cookie_fallback_when_primary_creates_no_file(tmp_path, monkeypatch):
    from workers.handlers.tiktok import download_videos as module

    monkeypatch.setattr(module, "random_jitter", AsyncMock())
    monkeypatch.setattr(module, "get_media_output_dir", lambda: tmp_path)

    async def fake_probe(_path):
        return {"ok": True, "has_video": True, "stream_count": 1}

    monkeypatch.setattr(module, "_probe_video_stream", fake_probe)

    async def fake_export_cookies(_account_id, cookie_file):
        cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    monkeypatch.setattr(module, "_export_adspower_tiktok_cookies", fake_export_cookies)

    attempts: list[tuple[str, bool]] = []

    async def fake_download(_url, output_template, *, mode, cookies_file=None, user_agent=None):
        attempts.append((mode, cookies_file is not None))
        if mode == "cookie_fallback":
            Path(output_template.replace("%(ext)s", "mp4")).write_bytes(b"video")
        return {"stdout_preview": "", "stderr_preview": "", "printed_filepaths": []}

    monkeypatch.setattr(module, "_download_with_ytdlp", fake_download)

    result = await module.download_videos_handler({
        "job_id": "job-1",
        "account_id": "account-1",
        "selected_videos": [{"url": "https://www.tiktok.com/@a/video/1"}],
    })

    assert attempts == [("primary", False), ("cookie_fallback", True)]
    assert result["ok"] is True
    assert len(result["video_paths"]) == 1
    assert Path(result["video_paths"][0]).is_file()


@pytest.mark.asyncio
async def test_download_continues_after_app_only_video(tmp_path, monkeypatch):
    from workers.handlers.tiktok import download_videos as module

    monkeypatch.delenv("TIKTOK_MOBILE_FALLBACK_ENABLED", raising=False)
    monkeypatch.setenv("TIKTOK_DOWNLOAD_PROVIDER", "browser_first")
    monkeypatch.setattr(module, "random_jitter", AsyncMock())
    monkeypatch.setattr(module, "get_media_output_dir", lambda: tmp_path)

    async def fake_browser(*, account_id, url, output_path, index):
        if index == 0:
            return {
                "ok": False,
                "mode": "browser_capture",
                "error": "TikTok web shows an app-only gate for this video.",
                "failure_kind": "app_only_gate",
                "gate_text": "Xem video TikTok Shop trong ứng dụng TikTok",
            }
        output_path.write_bytes(b"video")
        return {"ok": True, "mode": "browser_capture", "path": output_path}

    async def fake_ytdlp(**_kwargs):
        return {
            "ok": False,
            "mode": "primary",
            "error": "TikTok blocked yt-dlp with HTTP 403 even with cookies/impersonation.",
            "failure_kind": "tiktok_web_403",
            "stderr_preview": "HTTP Error 403: Forbidden",
        }

    monkeypatch.setattr(module, "_attempt_browser_download", fake_browser)
    monkeypatch.setattr(module, "_attempt_ytdlp_download", fake_ytdlp)

    result = await module.download_videos_handler({
        "job_id": "job-1",
        "account_id": "account-1",
        "selected_videos": [
            {"url": "https://www.tiktok.com/@shop/video/1"},
            {"url": "https://www.tiktok.com/@ok/video/2"},
        ],
    })

    assert result["ok"] is True
    assert len(result["video_paths"]) == 1
    assert result["download_stats"]["downloaded_count"] == 1
    assert result["download_stats"]["app_only_count"] == 1
    assert result["download_stats"]["http_403_count"] == 0
    assert result["failed_downloads"][0]["failure_kind"] == "mobile_fallback_disabled"
    assert result["failed_downloads"][0]["provider_attempts"]["browser_capture"]["failure_kind"] == "app_only_gate"
    assert result["failed_downloads"][0]["provider_attempts"]["mobile"]["failure_kind"] == "mobile_fallback_disabled"


@pytest.mark.asyncio
async def test_download_fails_when_ytdlp_exits_zero_without_output(tmp_path, monkeypatch):
    from workers.handlers.tiktok import download_videos as module

    monkeypatch.setattr(module, "random_jitter", AsyncMock())
    monkeypatch.setattr(module, "get_media_output_dir", lambda: tmp_path)

    async def fake_download(*_args, **_kwargs):
        return {
            "stdout_preview": "no filepath printed",
            "stderr_preview": "WARNING: video unavailable",
            "printed_filepaths": [],
        }

    monkeypatch.setattr(module, "_download_with_ytdlp", fake_download)

    with pytest.raises(RuntimeError, match="yt-dlp completed but output file was not found"):
        await module.download_videos_handler({
            "job_id": "job-1",
            "selected_videos": [{"url": "https://www.tiktok.com/@a/video/1"}],
        })


@pytest.mark.asyncio
async def test_download_all_app_only_blocked_is_fatal(tmp_path, monkeypatch):
    from workers.handlers.tiktok import download_videos as module
    from workers.worker_runtime import FatalDependencyError

    monkeypatch.delenv("TIKTOK_MOBILE_FALLBACK_ENABLED", raising=False)
    monkeypatch.setenv("TIKTOK_DOWNLOAD_PROVIDER", "browser_first")
    monkeypatch.setattr(module, "random_jitter", AsyncMock())
    monkeypatch.setattr(module, "get_media_output_dir", lambda: tmp_path)

    async def fake_browser(**_kwargs):
        return {
            "ok": False,
            "mode": "browser_capture",
            "error": "TikTok web shows an app-only gate for this video.",
            "failure_kind": "app_only_gate",
        }

    async def fake_ytdlp(**_kwargs):
        return {
            "ok": False,
            "mode": "primary",
            "error": "TikTok blocked yt-dlp with HTTP 403 even with cookies/impersonation.",
            "failure_kind": "tiktok_web_403",
        }

    monkeypatch.setattr(module, "_attempt_browser_download", fake_browser)
    monkeypatch.setattr(module, "_attempt_ytdlp_download", fake_ytdlp)

    with pytest.raises(FatalDependencyError, match="All selected videos require TikTok app or were blocked by TikTok web"):
        await module.download_videos_handler({
            "job_id": "job-1",
            "account_id": "account-1",
            "selected_videos": [{"url": "https://www.tiktok.com/@shop/video/1"}],
        })


@pytest.mark.asyncio
async def test_download_mobile_enabled_no_device_is_fatal(tmp_path, monkeypatch):
    from workers.handlers.tiktok import download_videos as module
    from workers.worker_runtime import FatalDependencyError

    monkeypatch.setenv("TIKTOK_MOBILE_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("TIKTOK_DOWNLOAD_PROVIDER", "auto")
    monkeypatch.setattr(module, "random_jitter", AsyncMock())
    monkeypatch.setattr(module, "get_media_output_dir", lambda: tmp_path)

    async def fake_browser(**_kwargs):
        return {
            "ok": False,
            "mode": "browser_capture",
            "error": "TikTok web shows an app-only gate for this video.",
            "failure_kind": "app_only_gate",
        }

    async def fake_ytdlp(**_kwargs):
        return {
            "ok": False,
            "mode": "primary",
            "error": "TikTok blocked yt-dlp with HTTP 403 even with cookies/impersonation.",
            "failure_kind": "tiktok_web_403",
        }

    async def fake_mobile(**_kwargs):
        return {
            "ok": False,
            "mode": "mobile",
            "error": "Mobile fallback enabled but no Android device/emulator detected.",
            "failure_kind": "mobile_device_unavailable",
        }

    monkeypatch.setattr(module, "_attempt_browser_download", fake_browser)
    monkeypatch.setattr(module, "_attempt_ytdlp_download", fake_ytdlp)
    monkeypatch.setattr(module, "_attempt_mobile_download", fake_mobile)

    with pytest.raises(FatalDependencyError, match="Mobile fallback enabled but no Android device/emulator detected"):
        await module.download_videos_handler({
            "job_id": "job-1",
            "account_id": "account-1",
            "selected_videos": [{"url": "https://www.tiktok.com/@shop/video/1"}],
        })


@pytest.mark.asyncio
async def test_attempt_ytdlp_download_reports_audio_only(tmp_path, monkeypatch):
    from workers.handlers.tiktok import download_videos as module

    async def fake_download(_url, output_template, **_kwargs):
        Path(output_template.replace("%(ext)s", "mp3")).write_bytes(b"audio")
        return {
            "stdout_preview": str(tmp_path / "video_00.mp3"),
            "stderr_preview": "",
            "printed_filepaths": [str(tmp_path / "video_00.mp3")],
        }

    monkeypatch.setattr(module, "_download_with_ytdlp", fake_download)

    result = await module._attempt_ytdlp_download(
        url="https://www.tiktok.com/@a/video/1",
        output_template=str(tmp_path / "video_00.%(ext)s"),
        output_dir=tmp_path,
        index=0,
        mode="primary",
    )

    assert result["ok"] is False
    assert result["failure_kind"] == "audio_only"
    assert "audio-only" in result["error"]
    assert result["audio_files"][0]["name"] == "video_00.mp3"


@pytest.mark.asyncio
async def test_attempt_ytdlp_download_rejects_mp4_without_video_stream(tmp_path, monkeypatch):
    from workers.handlers.tiktok import download_videos as module

    async def fake_download(_url, output_template, **_kwargs):
        Path(output_template.replace("%(ext)s", "mp4")).write_bytes(b"audio in mp4")
        return {"stdout_preview": "", "stderr_preview": "", "printed_filepaths": []}

    async def fake_probe(_path):
        return {"ok": True, "has_video": False, "stream_count": 0}

    monkeypatch.setattr(module, "_download_with_ytdlp", fake_download)
    monkeypatch.setattr(module, "_probe_video_stream", fake_probe)

    result = await module._attempt_ytdlp_download(
        url="https://www.tiktok.com/@a/video/1",
        output_template=str(tmp_path / "video_00.%(ext)s"),
        output_dir=tmp_path,
        index=0,
        mode="primary",
    )

    assert result["ok"] is False
    assert result["failure_kind"] == "audio_only"
    assert result["error"] == "Downloaded media has no video stream"


@pytest.mark.asyncio
async def test_download_all_audio_only_is_fatal(tmp_path, monkeypatch):
    from workers.handlers.tiktok import download_videos as module
    from workers.worker_runtime import FatalDependencyError

    monkeypatch.setattr(module, "random_jitter", AsyncMock())
    monkeypatch.setattr(module, "get_media_output_dir", lambda: tmp_path)

    async def fake_download(_url, output_template, **_kwargs):
        Path(output_template.replace("%(ext)s", "mp3")).write_bytes(b"audio")
        return {"stdout_preview": "", "stderr_preview": "", "printed_filepaths": []}

    monkeypatch.setattr(module, "_download_with_ytdlp", fake_download)

    with pytest.raises(FatalDependencyError, match="audio-only"):
        await module.download_videos_handler({
            "job_id": "job-1",
            "selected_videos": [{"url": "https://www.tiktok.com/@a/video/1"}],
        })


@pytest.mark.asyncio
async def test_download_all_http_403_is_fatal(tmp_path, monkeypatch):
    from workers.handlers.tiktok import download_videos as module
    from workers.handlers.tiktok._base import SubprocessError
    from workers.worker_runtime import FatalDependencyError

    monkeypatch.setattr(module, "random_jitter", AsyncMock())
    monkeypatch.setattr(module, "get_media_output_dir", lambda: tmp_path)

    async def fake_download(*_args, **_kwargs):
        raise SubprocessError(
            "Subprocess exited with code 1",
            stderr="ERROR: HTTP Error 403: Forbidden",
            returncode=1,
        )

    monkeypatch.setattr(module, "_download_with_ytdlp", fake_download)

    with pytest.raises(
        FatalDependencyError,
        match="TikTok blocked yt-dlp with HTTP 403 even with cookies/impersonation",
    ):
        await module.download_videos_handler({
            "job_id": "job-1",
            "selected_videos": [{"url": "https://www.tiktok.com/@a/video/1"}],
        })


@pytest.mark.asyncio
async def test_download_with_ytdlp_adds_user_agent_and_impersonate(monkeypatch):
    from workers.handlers.tiktok import download_videos as module

    monkeypatch.setenv("TIKTOK_YTDLP_IMPERSONATE", "chrome")
    monkeypatch.setenv("TIKTOK_YTDLP_FORMAT", "best")
    monkeypatch.setattr(module, "get_ytdlp_path", lambda: "yt-dlp")
    monkeypatch.setattr(module, "_available_ytdlp_impersonate_targets", AsyncMock(return_value={"chrome"}))

    captured_args: tuple[str, ...] = ()

    async def fake_run_subprocess(*args, **_kwargs):
        nonlocal captured_args
        captured_args = args
        return "C:/tmp/video_00.mp4\n", ""

    monkeypatch.setattr(module, "run_subprocess", fake_run_subprocess)

    await module._download_with_ytdlp(
        "https://www.tiktok.com/@a/video/1",
        "C:/tmp/video_00.%(ext)s",
        mode="cookie_fallback",
        user_agent="Mozilla/5.0 AdsPower",
    )

    assert "--impersonate" in captured_args
    assert "chrome" in captured_args
    assert "--format" in captured_args
    assert "best" in captured_args
    assert "--add-header" in captured_args
    assert "User-Agent:Mozilla/5.0 AdsPower" in captured_args


@pytest.mark.asyncio
async def test_download_with_ytdlp_omits_unavailable_impersonate(monkeypatch):
    from workers.handlers.tiktok import download_videos as module

    monkeypatch.setenv("TIKTOK_YTDLP_IMPERSONATE", "chrome")
    monkeypatch.setattr(module, "get_ytdlp_path", lambda: "yt-dlp")
    monkeypatch.setattr(module, "_available_ytdlp_impersonate_targets", AsyncMock(return_value=set()))

    captured_args: tuple[str, ...] = ()

    async def fake_run_subprocess(*args, **_kwargs):
        nonlocal captured_args
        captured_args = args
        return "C:/tmp/video_00.mp4\n", ""

    monkeypatch.setattr(module, "run_subprocess", fake_run_subprocess)

    await module._download_with_ytdlp(
        "https://www.tiktok.com/@a/video/1",
        "C:/tmp/video_00.%(ext)s",
        mode="primary",
    )

    assert "--impersonate" not in captured_args


@pytest.mark.asyncio
async def test_download_with_ytdlp_falls_back_to_available_impersonate(monkeypatch):
    from workers.handlers.tiktok import download_videos as module

    monkeypatch.setenv("TIKTOK_YTDLP_IMPERSONATE", "chrome")
    monkeypatch.setattr(module, "get_ytdlp_path", lambda: "yt-dlp")
    monkeypatch.setattr(module, "_available_ytdlp_impersonate_targets", AsyncMock(return_value={"edge"}))

    captured_args: tuple[str, ...] = ()

    async def fake_run_subprocess(*args, **_kwargs):
        nonlocal captured_args
        captured_args = args
        return "C:/tmp/video_00.mp4\n", ""

    monkeypatch.setattr(module, "run_subprocess", fake_run_subprocess)

    await module._download_with_ytdlp(
        "https://www.tiktok.com/@a/video/1",
        "C:/tmp/video_00.%(ext)s",
        mode="primary",
    )

    assert "--impersonate" in captured_args
    assert "edge" in captured_args


def test_warns_when_impersonate_enabled_without_curl_cffi(monkeypatch, caplog):
    from workers.handlers.tiktok import download_videos as module

    monkeypatch.setenv("TIKTOK_YTDLP_IMPERSONATE", "chrome")
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(module, "_IMPERSONATION_DEPENDENCY_WARNED", False)

    with caplog.at_level(logging.WARNING):
        module._warn_if_impersonation_dependency_missing()

    assert "download_ytdlp_impersonate_dependency_missing" in caplog.text
