from __future__ import annotations

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
