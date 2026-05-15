from __future__ import annotations

import json

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


def test_real_chrome_user_data_dir_stable(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    from core.browser_providers import get_real_chrome_user_data_dir

    first = get_real_chrome_user_data_dir("account-123")
    second = get_real_chrome_user_data_dir("account-123")

    assert first == second
    assert first.name == "account-123"
    assert "real_chrome_profiles" in str(first)
    assert first.exists()


def test_provider_resolver_reads_json_metadata():
    from core.browser_providers import BROWSER_PROVIDER_REAL_CHROME, resolve_browser_provider

    provider = resolve_browser_provider({"metadata": json.dumps({"browser_provider": "real_chrome"})})

    assert provider == BROWSER_PROVIDER_REAL_CHROME


def test_real_chrome_account_readiness_does_not_require_proxy():
    from api.schemas import AccountResponse

    response = AccountResponse.from_row(
        {
            "id": "account-1",
            "platform": "tiktok",
            "account_handle": "handle",
            "profile_url": None,
            "external_user_id": None,
            "status": "healthy",
            "proxy_url": None,
            "proxy_country": None,
            "metadata": json.dumps({"browser_provider": "real_chrome"}),
            "session_valid": 1,
            "last_login_at": "2026-05-15T00:00:00Z",
            "user_agent": "Chrome",
            "browser_data_dir": None,
            "avatar_url": None,
            "display_name": None,
            "risk_score": 0,
            "soft_ban_detected": 0,
            "warmup_sessions_completed": 0,
            "failed_publish_count": 0,
            "captcha_hit_count": 0,
            "created_at": None,
            "updated_at": None,
        }
    )

    assert response.browser_provider == "real_chrome"
    assert "proxy_missing" not in response.readiness_errors
    assert response.can_publish is True


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
async def test_real_chrome_provider_does_not_inject_scripts(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    from core.browser_providers import RealChromeProvider

    class FakePage:
        pass

    class FakeContext:
        def __init__(self) -> None:
            self.pages = [FakePage()]
            self.init_scripts: list[str] = []

        async def add_init_script(self, script: str) -> None:
            self.init_scripts.append(script)

        async def close(self) -> None:
            return None

    class FakeChromium:
        def __init__(self) -> None:
            self.kwargs: dict = {}
            self.context = FakeContext()

        async def launch_persistent_context(self, user_data_dir: str, **kwargs):
            self.user_data_dir = user_data_dir
            self.kwargs = kwargs
            return self.context

    class FakePlaywright:
        def __init__(self) -> None:
            self.chromium = FakeChromium()

    pw = FakePlaywright()
    provider = RealChromeProvider({"id": "account-real", "metadata": {"browser_provider": "real_chrome"}})

    async with provider.open_connect_context(pw) as (context, _page, opened_dir):
        assert context.init_scripts == []
        assert opened_dir.name == "account-real"

    assert pw.chromium.kwargs["channel"] == "chrome"
    assert "--disable-blink-features=AutomationControlled" not in pw.chromium.kwargs.get("args", [])
    assert "user_agent" not in pw.chromium.kwargs
    assert "timezone_id" not in pw.chromium.kwargs
    assert "locale" not in pw.chromium.kwargs


@pytest.mark.asyncio
async def test_connect_rate_limited_maps_to_http_429():
    from fastapi import HTTPException

    from api.routes.accounts import _raise_login_block
    from core.login_diagnostics import LoginBlockStatus

    class FakeDatabase:
        def __init__(self) -> None:
            self.recorded: tuple | None = None

        async def record_login_diagnostic(self, account_id: str, diagnostic: str, *, platform=None, status=None) -> None:
            self.recorded = (account_id, diagnostic, platform, status)

    db = FakeDatabase()

    with pytest.raises(HTTPException) as exc:
        await _raise_login_block("account-1", "tiktok", LoginBlockStatus.RATE_LIMITED, db)

    assert exc.value.status_code == 429
    assert db.recorded == ("account-1", "RATE_LIMITED", "tiktok", "limited")


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


@pytest.mark.asyncio
async def test_publisher_real_chrome_requires_connected_session(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    import execution.publisher_playwright as publisher
    from core.browser_providers import get_real_chrome_user_data_dir

    async def fail_login(*args, **kwargs):
        raise AssertionError("login_tiktok must not be called")

    monkeypatch.setattr(publisher, "login_tiktok", fail_login)

    profile_dir = get_real_chrome_user_data_dir("real-chrome-no-session")
    result = await publisher.publish_v2(
        content_id="content-1",
        platform="tiktok",
        video_path="missing.mp4",
        caption="caption",
        account={
            "account_id": "real-chrome-no-session",
            "platform": "tiktok",
            "metadata": {"browser_provider": "real_chrome", "real_chrome_user_data_dir": str(profile_dir)},
        },
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


def test_warmup_real_chrome_profile_dir_is_not_enough(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("WARMUP_DB", str(tmp_path / "warmup.db"))

    import execution.publisher_playwright as publisher
    from core.browser_providers import get_real_chrome_user_data_dir
    from execution.account_warmup import run_warmup_session

    async def fail_login(*args, **kwargs):
        raise AssertionError("login_tiktok must not be called")

    monkeypatch.setattr(publisher, "login_tiktok", fail_login)

    profile_dir = get_real_chrome_user_data_dir("warmup-real-chrome-no-session")
    result = run_warmup_session(
        {
            "account_id": "warmup-real-chrome-no-session",
            "platform": "tiktok",
            "metadata": {"browser_provider": "real_chrome", "real_chrome_user_data_dir": str(profile_dir)},
        },
        headless=True,
    )

    assert result.success is False
    assert result.error == "SESSION_NOT_CONNECTED"
