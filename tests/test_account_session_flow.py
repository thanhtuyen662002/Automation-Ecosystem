from __future__ import annotations

import pytest


def test_get_browser_data_dir_stable(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    from core.browser_context import get_browser_data_dir

    first = get_browser_data_dir("account-123")
    second = get_browser_data_dir("account-123")

    assert first == second
    assert first.name == "account-123"
    assert first.exists()


def test_identity_stable_and_local_defaults(monkeypatch):
    monkeypatch.delenv("AE_DEFAULT_TIMEZONE", raising=False)
    monkeypatch.delenv("AE_DEFAULT_LOCALE", raising=False)
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.delenv("LANG", raising=False)

    import core.identity_manager as identity_manager

    monkeypatch.setattr(identity_manager.pylocale, "getlocale", lambda: (None, None))

    p1 = identity_manager.generate_identity_profile("account-identity")
    p2 = identity_manager.generate_identity_profile("account-identity")

    assert p1.fingerprint_hash == p2.fingerprint_hash
    assert p1.timezone == "Asia/Ho_Chi_Minh"
    assert p1.locale == "vi-VN"
    assert p1.timezone != "America/New_York"


def test_identity_proxy_country_aligns_timezone_locale():
    from core.identity_manager import generate_identity_profile

    profile = generate_identity_profile("account-vn-proxy", proxy_country="VN")

    assert profile.proxy_country == "VN"
    assert profile.timezone == "Asia/Ho_Chi_Minh"
    assert profile.locale == "vi-VN"


def test_classify_login_block_rate_limited_text():
    from core.login_diagnostics import LoginBlockStatus, classify_login_text

    status = classify_login_text("Maximum number of attempts reached. Try again later.")

    assert status == LoginBlockStatus.RATE_LIMITED


@pytest.mark.asyncio
async def test_publisher_does_not_auto_login_without_connected_session(monkeypatch):
    import execution.publisher_playwright as publisher

    async def fail_login(*args, **kwargs):
        raise AssertionError("login_tiktok must not be called")

    monkeypatch.setattr(publisher, "login_tiktok", fail_login)

    result = await publisher.publish_v2(
        content_id="content-1",
        platform="tiktok",
        video_path="missing.mp4",
        caption="caption",
        account={"account_id": "account-no-session", "platform": "tiktok", "username": "u", "password": "p"},
    )

    assert result.success is False
    assert result.error == "SESSION_NOT_CONNECTED"
    assert result.meta["error_code"] == "SESSION_NOT_CONNECTED"


def test_warmup_does_not_auto_login_without_connected_session(monkeypatch, tmp_path):
    monkeypatch.setenv("WARMUP_DB", str(tmp_path / "warmup.db"))

    import execution.publisher_playwright as publisher
    from execution.account_warmup import run_warmup_session

    async def fail_login(*args, **kwargs):
        raise AssertionError("login_tiktok must not be called")

    monkeypatch.setattr(publisher, "login_tiktok", fail_login)

    result = run_warmup_session(
        {"account_id": "warmup-no-session", "platform": "tiktok", "username": "u", "password": "p"},
        headless=True,
    )

    assert result.success is False
    assert result.error == "SESSION_NOT_CONNECTED"
