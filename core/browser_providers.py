from __future__ import annotations

import json
import logging
import os
import platform
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

LOGGER = logging.getLogger("core.browser_providers")

BROWSER_PROVIDER_PLAYWRIGHT = "playwright"
BROWSER_PROVIDER_REAL_CHROME = "real_chrome"
BROWSER_PROVIDER_ADSPOWER = "adspower"
VALID_BROWSER_PROVIDERS = {
    BROWSER_PROVIDER_PLAYWRIGHT,
    BROWSER_PROVIDER_REAL_CHROME,
    BROWSER_PROVIDER_ADSPOWER,
}


def account_metadata(account: dict[str, Any] | None) -> dict[str, Any]:
    raw = (account or {}).get("metadata")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def resolve_browser_provider(account: dict[str, Any] | None) -> str:
    """Resolve the account browser provider from top-level fields or metadata."""
    metadata = account_metadata(account)
    provider = (
        (account or {}).get("browser_provider")
        or metadata.get("browser_provider")
        or BROWSER_PROVIDER_PLAYWRIGHT
    )
    provider = str(provider).strip().lower()
    return provider if provider in VALID_BROWSER_PROVIDERS else BROWSER_PROVIDER_PLAYWRIGHT


def _provider_base_dir(folder_name: str) -> Path:
    sys_name = platform.system()
    if sys_name == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "Automation-Ecosystem" / folder_name
    if sys_name == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Automation-Ecosystem" / folder_name
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "automation-ecosystem" / folder_name


def get_real_chrome_user_data_dir(
    account_id: str,
    account: dict[str, Any] | None = None,
    *,
    create: bool = True,
) -> Path:
    """Return the stable per-account Chrome Stable profile path."""
    metadata = account_metadata(account)
    configured = (account or {}).get("real_chrome_user_data_dir") or metadata.get("real_chrome_user_data_dir")
    data_dir = Path(str(configured)) if configured else _provider_base_dir("real_chrome_profiles") / account_id
    if create:
        data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


class PlaywrightPersistentProvider:
    """Backward-compatible provider that keeps the current fingerprint path."""

    def __init__(
        self,
        account: dict[str, Any],
        session: dict[str, Any] | None = None,
        identity_profile: Any | None = None,
    ) -> None:
        self.account = account
        self.session = session or {}
        self.identity_profile = identity_profile
        self.account_id = str(account.get("account_id") or account.get("id") or self.session.get("id") or "default")

    @asynccontextmanager
    async def open_connect_context(self, pw: Any) -> AsyncGenerator[tuple[Any, Any, Path], None]:
        from core.browser_context import create_connect_context, get_browser_data_dir

        data_dir = Path(self.account.get("browser_data_dir") or self.session.get("browser_data_dir") or get_browser_data_dir(self.account_id))
        async with create_connect_context(
            pw,
            self.account_id,
            proxy_url=self.account.get("proxy_url") or self.session.get("proxy_url"),
            session=self.session,
            identity_profile=self.identity_profile,
            browser_data_dir=data_dir,
        ) as (context, page):
            yield context, page, data_dir

    @asynccontextmanager
    async def open_publisher_context(
        self,
        pw: Any,
        *,
        headless: bool = True,
    ) -> AsyncGenerator[tuple[Any, Any, Path], None]:
        from core.browser_context import create_publisher_context, get_browser_data_dir

        data_dir = Path(self.session.get("browser_data_dir") or self.account.get("browser_data_dir") or get_browser_data_dir(self.account_id))
        session = dict(self.session)
        session.setdefault("browser_data_dir", str(data_dir))
        async with create_publisher_context(pw, session, self.account_id, headless=headless) as (context, page):
            yield context, page, data_dir


class RealChromeProvider:
    """Use installed Chrome Stable with an isolated per-account user-data-dir.

    This provider intentionally does not inject stealth or fingerprint scripts.
    It also avoids overriding user-agent, timezone, locale, geolocation, and
    viewport unless Chrome itself decides those values.
    """

    def __init__(
        self,
        account: dict[str, Any],
        session: dict[str, Any] | None = None,
        identity_profile: Any | None = None,
    ) -> None:
        self.account = account
        self.session = session or {}
        self.identity_profile = identity_profile
        self.account_id = str(account.get("account_id") or account.get("id") or self.session.get("id") or "default")
        self.user_data_dir = get_real_chrome_user_data_dir(self.account_id, account)

    @asynccontextmanager
    async def open_connect_context(self, pw: Any) -> AsyncGenerator[tuple[Any, Any, Path], None]:
        async with self._open_context(pw, headless=False) as opened:
            yield opened

    @asynccontextmanager
    async def open_publisher_context(
        self,
        pw: Any,
        *,
        headless: bool = False,
    ) -> AsyncGenerator[tuple[Any, Any, Path], None]:
        async with self._open_context(pw, headless=False) as opened:
            yield opened

    @asynccontextmanager
    async def _open_context(
        self,
        pw: Any,
        *,
        headless: bool,
    ) -> AsyncGenerator[tuple[Any, Any, Path], None]:
        metadata = account_metadata(self.account)
        launch_kwargs: dict[str, Any] = {
            "headless": headless,
            "channel": "chrome",
            "args": [
                "--no-first-run",
                "--no-default-browser-check",
            ],
        }

        executable_path = metadata.get("chrome_executable_path") or self.account.get("chrome_executable_path") or os.environ.get("CHROME_EXECUTABLE_PATH")
        if executable_path:
            launch_kwargs.pop("channel", None)
            launch_kwargs["executable_path"] = str(executable_path)

        debug_port = metadata.get("real_chrome_debug_port") or self.account.get("real_chrome_debug_port")
        if debug_port:
            launch_kwargs["args"].append(f"--remote-debugging-port={int(debug_port)}")

        proxy_url = self.account.get("proxy_url") or self.session.get("proxy_url")
        if proxy_url:
            launch_kwargs["proxy"] = {"server": proxy_url}

        LOGGER.info(
            "real_chrome_launch",
            extra={
                "event": "real_chrome_launch",
                "account_id": self.account_id,
                "browser_provider": BROWSER_PROVIDER_REAL_CHROME,
                "user_data_dir": str(self.user_data_dir),
                "has_proxy": bool(proxy_url),
                "headless": headless,
                "uses_executable_path": bool(executable_path),
                "has_debug_port": bool(debug_port),
            },
        )

        context = await pw.chromium.launch_persistent_context(str(self.user_data_dir), **launch_kwargs)
        pages = context.pages
        page = pages[0] if pages else await context.new_page()

        try:
            yield context, page, self.user_data_dir
        finally:
            try:
                await context.close()
            except Exception:
                pass


def make_browser_provider(
    account: dict[str, Any],
    session: dict[str, Any] | None = None,
    identity_profile: Any | None = None,
) -> PlaywrightPersistentProvider | RealChromeProvider:
    provider = resolve_browser_provider(account)
    if provider == BROWSER_PROVIDER_REAL_CHROME:
        return RealChromeProvider(account, session=session, identity_profile=identity_profile)
    if provider == BROWSER_PROVIDER_ADSPOWER:
        raise NotImplementedError("AdsPower provider is not implemented yet")
    return PlaywrightPersistentProvider(account, session=session, identity_profile=identity_profile)


async def collect_runtime_diagnostics(page: Any, context: Any | None = None) -> dict[str, Any]:
    """Collect non-secret browser runtime data for fingerprint comparisons."""
    js = """
        () => {
            const glCanvas = document.createElement('canvas');
            const gl = glCanvas.getContext('webgl') || glCanvas.getContext('experimental-webgl');
            let webglVendor = '';
            let webglRenderer = '';
            if (gl) {
                const debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
                if (debugInfo) {
                    webglVendor = gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL) || '';
                    webglRenderer = gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL) || '';
                }
            }
            return {
                userAgent: navigator.userAgent,
                webdriver: navigator.webdriver,
                language: navigator.language,
                languages: Array.from(navigator.languages || []),
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                screen: { width: window.screen.width, height: window.screen.height },
                devicePixelRatio: window.devicePixelRatio,
                webglVendor,
                webglRenderer,
                currentUrl: location.href,
            };
        }
    """
    diagnostics: dict[str, Any]
    try:
        diagnostics = await page.evaluate(js)
    except Exception as exc:
        diagnostics = {"runtime_error": str(exc), "currentUrl": getattr(page, "url", "")}

    if context is not None:
        try:
            cookies = await context.cookies()
            diagnostics["cookie_names"] = sorted({str(cookie.get("name", "")) for cookie in cookies if cookie.get("name")})
        except Exception as exc:
            diagnostics["cookie_names_error"] = str(exc)
    return diagnostics
