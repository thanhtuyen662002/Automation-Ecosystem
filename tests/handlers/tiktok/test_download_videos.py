from __future__ import annotations


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

