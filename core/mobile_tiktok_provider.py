"""Mobile TikTok provider interface.

The first implementation uses ADB only. It can open TikTok URLs in an already
configured Android device/emulator, scroll, dump UIAutomator XML, and collect
diagnostics. It does not bypass login, CAPTCHA, checkpoints, or TikTok save
restrictions.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit


_TIKTOK_VIDEO_URL_RE = re.compile(r"https?://(?:www\.)?tiktok\.com/@[^/\s?#]+/video/\d+", re.IGNORECASE)
_VERIFICATION_MARKERS = (
    "captcha",
    "verify",
    "verification",
    "checkpoint",
    "security check",
    "xac minh",
    "x\u00e1c minh",
    "ki\u1ec3m tra b\u1ea3o m\u1eadt",
)
_LOGIN_MARKERS = ("log in", "login", "sign up", "\u0111\u0103ng nh\u1eadp")


@dataclass
class MobileOpenResult:
    ok: bool
    status: str
    failure_kind: str = ""
    message: str = ""
    stdout_preview: str = ""
    stderr_preview: str = ""
    current_package: str = ""
    state: dict[str, Any] | None = None


class MobileTikTokProvider(Protocol):
    async def is_available(self) -> bool: ...
    async def open_url(self, url: str) -> MobileOpenResult: ...
    async def collect_visible_video_links(self, max_results: int) -> list[dict[str, Any]]: ...
    async def scroll_feed(self, rounds: int) -> dict[str, Any]: ...
    async def get_current_state(self) -> dict[str, Any]: ...
    async def screenshot(self, path: Path) -> Path | None: ...


class AdbTikTokProvider:
    def __init__(
        self,
        *,
        device_id: str | None = None,
        package_name: str = "com.zhiliaoapp.musically",
        adb_path: str | None = None,
    ) -> None:
        self.device_id = (device_id or os.environ.get("TIKTOK_MOBILE_DEVICE_ID") or "").strip()
        self.package_name = (
            package_name
            or os.environ.get("TIKTOK_ANDROID_TIKTOK_PACKAGE")
            or "com.zhiliaoapp.musically"
        ).strip()
        self.adb_path = adb_path or shutil.which("adb") or shutil.which("adb.exe") or "adb"

    def _adb_args(self, *args: str) -> list[str]:
        base = [self.adb_path]
        if self.device_id:
            base.extend(["-s", self.device_id])
        base.extend(args)
        return base

    async def is_available(self) -> bool:
        if not _command_available(self.adb_path):
            return False
        try:
            stdout, _stderr = await _run_text([self.adb_path, "devices"], timeout=10.0)
        except Exception:
            return False
        return _adb_devices_has_available_device(stdout, self.device_id)

    async def open_url(self, url: str) -> MobileOpenResult:
        if not await self.is_available():
            return MobileOpenResult(
                ok=False,
                status="device_unavailable",
                failure_kind="mobile_device_unavailable",
                message="Mobile fallback enabled but no Android device/emulator detected.",
            )

        try:
            stdout, stderr = await _run_text(
                self._adb_args(
                    "shell",
                    "am",
                    "start",
                    "-a",
                    "android.intent.action.VIEW",
                    "-d",
                    url,
                ),
                timeout=20.0,
            )
        except Exception as exc:
            return MobileOpenResult(
                ok=False,
                status="open_failed",
                failure_kind="mobile_open_failed",
                message=str(exc),
            )

        await asyncio.sleep(3.0)
        state = await self.get_current_state()
        current_package = str(state.get("current_package") or "")

        if bool(state.get("verification_required")):
            return MobileOpenResult(
                ok=False,
                status="verification_required",
                failure_kind="mobile_verification_required",
                message="TikTok app requires manual verification; automation will not bypass it.",
                stdout_preview=_preview(stdout),
                stderr_preview=_preview(stderr),
                current_package=current_package,
                state=state,
            )
        if bool(state.get("login_required")):
            return MobileOpenResult(
                ok=False,
                status="login_required",
                failure_kind="mobile_verification_required",
                message="TikTok app requires manual login before automation can continue.",
                stdout_preview=_preview(stdout),
                stderr_preview=_preview(stderr),
                current_package=current_package,
                state=state,
            )

        started_tiktok = self.package_name in f"{stdout}\n{current_package}"
        if not started_tiktok:
            return MobileOpenResult(
                ok=False,
                status="open_failed",
                failure_kind="mobile_open_failed",
                message="TikTok app did not become active after opening the URL.",
                stdout_preview=_preview(stdout),
                stderr_preview=_preview(stderr),
                current_package=current_package,
                state=state,
            )

        return MobileOpenResult(
            ok=True,
            status="opened",
            message="TikTok URL opened in the Android app.",
            stdout_preview=_preview(stdout),
            stderr_preview=_preview(stderr),
            current_package=current_package,
            state=state,
        )

    async def collect_visible_video_links(self, max_results: int) -> list[dict[str, Any]]:
        max_results = max(1, int(max_results or 1))
        xml_text = await self._dump_window_xml()
        values = _ui_text_values(xml_text)
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, value in enumerate(values):
            for match in _TIKTOK_VIDEO_URL_RE.finditer(value):
                url = _canonical_tiktok_url(match.group(0))
                if not url or url in seen:
                    continue
                seen.add(url)
                title = _nearby_title(values, index)
                candidates.append({
                    "url": url,
                    "title": title,
                    "source": "mobile_tiktok_shop",
                    "requires_mobile_app": True,
                })
                if len(candidates) >= max_results:
                    return candidates
        return candidates

    async def scroll_feed(self, rounds: int) -> dict[str, Any]:
        rounds = max(1, int(rounds or 1))
        swipes = 0
        errors: list[str] = []
        for _round in range(rounds):
            try:
                await _run_text(
                    self._adb_args("shell", "input", "swipe", "540", "1700", "540", "500", "450"),
                    timeout=10.0,
                )
                swipes += 1
                await asyncio.sleep(1.2)
            except Exception as exc:
                errors.append(str(exc)[:300])
                break
        return {"rounds_requested": rounds, "swipes": swipes, "errors": errors}

    async def get_current_state(self) -> dict[str, Any]:
        devices_stdout = ""
        adb_available = _command_available(self.adb_path)
        if adb_available:
            try:
                devices_stdout, _stderr = await _run_text([self.adb_path, "devices"], timeout=10.0)
            except Exception:
                devices_stdout = ""

        device_available = _adb_devices_has_available_device(devices_stdout, self.device_id)
        resolved_device_id = self.device_id or _first_available_device_id(devices_stdout)
        tiktok_app_installed = False
        if device_available:
            try:
                stdout, _stderr = await _run_text(self._adb_args("shell", "pm", "path", self.package_name), timeout=10.0)
                tiktok_app_installed = bool(stdout.strip())
            except Exception:
                tiktok_app_installed = False

        current_package = await self._current_package() if device_available else ""
        xml_text = await self._dump_window_xml() if device_available else ""
        lowered_ui = xml_text.lower()
        return {
            "provider": "adb",
            "adb_available": adb_available,
            "device_available": device_available,
            "device_id": resolved_device_id,
            "package_name": self.package_name,
            "tiktok_app_installed": tiktok_app_installed,
            "current_package": current_package,
            "tiktok_app_active": current_package == self.package_name,
            "verification_required": _contains_any(lowered_ui, _VERIFICATION_MARKERS),
            "login_required": _contains_any(lowered_ui, _LOGIN_MARKERS),
            "manual_login_required": _env_bool("TIKTOK_MOBILE_REQUIRE_MANUAL_LOGIN", default=True),
            "xml_text_preview": _preview(" ".join(_ui_text_values(xml_text)), max_chars=1200),
        }

    async def screenshot(self, path: Path) -> Path | None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = await _run_bytes(self._adb_args("exec-out", "screencap", "-p"), timeout=20.0)
            if not data:
                return None
            path.write_bytes(data)
            return path
        except Exception:
            return None

    async def open_tiktok_url(self, url: str) -> MobileOpenResult:
        return await self.open_url(url)

    async def get_share_link(self) -> str | None:
        return None

    async def save_video_if_available(self, output_dir: Path, filename_prefix: str) -> Path | None:
        return await self._pull_new_saved_video(output_dir, filename_prefix)

    async def _pull_new_saved_video(self, output_dir: Path, filename_prefix: str) -> Path | None:
        # Placeholder for a legitimate "Save video" workflow. We intentionally
        # do not tap hidden buttons or bypass TikTok restrictions.
        return None

    async def _current_package(self) -> str:
        commands = (
            self._adb_args("shell", "dumpsys", "window"),
            self._adb_args("shell", "dumpsys", "activity", "top"),
        )
        for args in commands:
            try:
                stdout, _stderr = await _run_text(args, timeout=10.0)
            except Exception:
                continue
            if self.package_name in stdout:
                return self.package_name
        return ""

    async def _dump_window_xml(self) -> str:
        with tempfile.TemporaryDirectory(prefix="ae_tiktok_ui_") as tmp_dir:
            local_path = Path(tmp_dir) / "window.xml"
            try:
                await _run_text(self._adb_args("shell", "uiautomator", "dump", "/sdcard/window.xml"), timeout=15.0)
                await _run_text(self._adb_args("pull", "/sdcard/window.xml", str(local_path)), timeout=15.0)
                return local_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return ""


def make_mobile_tiktok_provider() -> MobileTikTokProvider:
    provider = os.environ.get("TIKTOK_MOBILE_PROVIDER", "adb").strip().lower()
    if provider != "adb":
        raise ValueError(f"Unsupported TIKTOK_MOBILE_PROVIDER={provider!r}; only 'adb' is available")
    return AdbTikTokProvider(
        device_id=os.environ.get("TIKTOK_MOBILE_DEVICE_ID", ""),
        package_name=os.environ.get("TIKTOK_ANDROID_TIKTOK_PACKAGE", "com.zhiliaoapp.musically"),
    )


def _command_available(command: str) -> bool:
    return bool(shutil.which(command) or Path(command).is_file())


def _adb_devices_has_available_device(stdout: str, device_id: str = "") -> bool:
    wanted = str(device_id or "").strip()
    for line in str(stdout or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2 or parts[1] != "device":
            continue
        if wanted and parts[0] != wanted:
            continue
        return True
    return False


def _first_available_device_id(stdout: str) -> str:
    for line in str(stdout or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return ""


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _ui_text_values(xml_text: str) -> list[str]:
    values: list[str] = []
    if not xml_text:
        return values
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return re.findall(r"(?:text|content-desc)=\"([^\"]+)\"", xml_text)
    for node in root.iter():
        for attr in ("text", "content-desc", "resource-id"):
            value = str(node.attrib.get(attr) or "").strip()
            if value:
                values.append(value)
    return values


def _nearby_title(values: list[str], index: int) -> str:
    for offset in range(1, 5):
        candidate_index = index - offset
        if candidate_index < 0:
            break
        candidate = values[candidate_index].strip()
        if candidate and not _TIKTOK_VIDEO_URL_RE.search(candidate) and len(candidate) <= 180:
            return candidate
    return ""


def _canonical_tiktok_url(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    if value.startswith("//"):
        value = "https:" + value
    elif value.startswith("www.tiktok.com"):
        value = "https://" + value
    parts = urlsplit(value)
    if not parts.netloc:
        return ""
    netloc = "www.tiktok.com" if parts.netloc.lower().endswith("tiktok.com") else parts.netloc
    return urlunsplit(("https", netloc, parts.path.rstrip("/"), "", ""))


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _preview(text: str, max_chars: int = 800) -> str:
    return str(text or "").replace("\r", "\n").strip()[:max_chars]


async def _run_text(args: list[str], *, timeout: float) -> tuple[str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:4])} failed with code {proc.returncode}: {stderr[:500]}")
    return stdout, stderr


async def _run_bytes(args: list[str], *, timeout: float) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    if proc.returncode != 0:
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        raise RuntimeError(f"{' '.join(args[:4])} failed with code {proc.returncode}: {stderr[:500]}")
    return stdout_bytes
