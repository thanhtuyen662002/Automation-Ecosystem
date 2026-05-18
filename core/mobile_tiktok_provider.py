"""Mobile TikTok provider interface.

The first implementation uses ADB only. It can open TikTok URLs in an already
configured Android device/emulator, scroll, dump UIAutomator XML, and collect
diagnostics. It does not bypass login, CAPTCHA, checkpoints, or TikTok save
restrictions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import unicodedata
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
_DEFAULT_SAVE_SCAN_DIRS = ("/sdcard/DCIM", "/sdcard/Movies", "/sdcard/Download", "/sdcard/Pictures")
_DEFAULT_PULL_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm")
_SAVE_BUTTON_LABELS = {"save video", "save", "download", "luu video", "tai xuong"}
_PREFERRED_SAVE_BUTTON_LABELS = {"save video", "download", "luu video", "tai xuong"}
_SHARE_MORE_LABELS = {
    "share",
    "share video",
    "more",
    "more options",
    "chia se",
    "them",
}

LOGGER = logging.getLogger("core.mobile_tiktok_provider")


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


@dataclass(frozen=True)
class MobileMediaFile:
    remote_path: str
    size: int = 0
    modified_at: float = 0.0


class MobileTikTokProvider(Protocol):
    async def is_available(self) -> bool: ...
    async def open_url(self, url: str) -> MobileOpenResult: ...
    async def collect_visible_video_links(self, max_results: int) -> list[dict[str, Any]]: ...
    async def scroll_feed(self, rounds: int) -> dict[str, Any]: ...
    async def get_current_state(self) -> dict[str, Any]: ...
    async def screenshot(self, path: Path) -> Path | None: ...
    async def save_video_if_available(self, output_dir: Path, filename_prefix: str) -> Path | None: ...


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
        self.last_save_failure_kind = ""
        self.last_save_message = ""

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
                failure_kind="mobile_login_required",
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
        self.last_save_failure_kind = ""
        self.last_save_message = ""
        output_dir.mkdir(parents=True, exist_ok=True)
        scan_dirs = _env_list("TIKTOK_MOBILE_SAVE_SCAN_DIRS", _DEFAULT_SAVE_SCAN_DIRS)
        extensions = _normalize_extensions(_env_list("TIKTOK_MOBILE_PULL_EXTENSIONS", _DEFAULT_PULL_EXTENSIONS))
        timeout_seconds = _env_float("TIKTOK_MOBILE_SAVE_TIMEOUT_SECONDS", default=45.0, minimum=5.0, maximum=300.0)

        try:
            state = await self.get_current_state()
            if bool(state.get("verification_required")):
                self.last_save_failure_kind = "mobile_verification_required"
                self.last_save_message = "TikTok app requires manual verification; automation will not bypass it."
                LOGGER.warning(
                    "mobile_save_failed",
                    extra={"event": "mobile_save_failed", "failure_kind": self.last_save_failure_kind},
                )
                return None
            if bool(state.get("login_required")):
                self.last_save_failure_kind = "mobile_login_required"
                self.last_save_message = "TikTok app requires manual login before automation can continue."
                LOGGER.warning(
                    "mobile_save_failed",
                    extra={"event": "mobile_save_failed", "failure_kind": self.last_save_failure_kind},
                )
                return None

            before = await self._list_mobile_media_files(scan_dirs=scan_dirs, extensions=extensions)
            LOGGER.info(
                "mobile_save_scan_before",
                extra={
                    "event": "mobile_save_scan_before",
                    "file_count": len(before),
                    "scan_dirs": scan_dirs,
                    "extensions": extensions,
                },
            )

            if not await self._tap_save_button_if_visible():
                self.last_save_failure_kind = "mobile_download_not_available"
                self.last_save_message = "TikTok app opened the video, but Save video/Download was not available."
                return None

            deadline = asyncio.get_running_loop().time() + timeout_seconds
            after: dict[str, MobileMediaFile] = before
            new_file: MobileMediaFile | None = None
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(1.5)
                after = await self._list_mobile_media_files(scan_dirs=scan_dirs, extensions=extensions)
                candidate = _find_new_mobile_media_file(before, after)
                if candidate is None:
                    continue
                await asyncio.sleep(1.0)
                stable_after = await self._list_mobile_media_files(scan_dirs=scan_dirs, extensions=extensions)
                after = stable_after
                stable_candidate = stable_after.get(candidate.remote_path)
                if stable_candidate is not None and stable_candidate.size == candidate.size and stable_candidate.size > 0:
                    new_file = stable_candidate
                    break

            LOGGER.info(
                "mobile_save_scan_after",
                extra={"event": "mobile_save_scan_after", "file_count": len(after), "scan_dirs": scan_dirs},
            )
            if new_file is None:
                self.last_save_failure_kind = "mobile_save_no_new_file"
                self.last_save_message = "Save video was tapped, but no new media file was created on the Android device."
                LOGGER.warning(
                    "mobile_save_no_new_file",
                    extra={
                        "event": "mobile_save_no_new_file",
                        "before_count": len(before),
                        "after_count": len(after),
                        "timeout_seconds": timeout_seconds,
                    },
                )
                return None

            LOGGER.info(
                "mobile_save_new_file_detected",
                extra={
                    "event": "mobile_save_new_file_detected",
                    "remote_path": new_file.remote_path,
                    "size": new_file.size,
                    "modified_at": new_file.modified_at,
                },
            )
            suffix = Path(new_file.remote_path).suffix.lower()
            if suffix not in extensions:
                suffix = ".mp4"
            local_path = output_dir / f"{filename_prefix}{suffix}"
            await self._pull_mobile_file(new_file.remote_path, local_path)
            LOGGER.info(
                "mobile_save_pull_done",
                extra={
                    "event": "mobile_save_pull_done",
                    "remote_path": new_file.remote_path,
                    "local_path": str(local_path),
                    "size": local_path.stat().st_size if local_path.exists() else 0,
                },
            )

            if await self._validate_pulled_video(local_path):
                LOGGER.info(
                    "mobile_save_video_validated",
                    extra={"event": "mobile_save_video_validated", "local_path": str(local_path)},
                )
                return local_path

            self.last_save_failure_kind = "mobile_invalid_video_stream"
            self.last_save_message = "Pulled mobile file does not contain a video stream."
            _mark_invalid_file(local_path)
            LOGGER.warning(
                "mobile_save_invalid_video_stream",
                extra={"event": "mobile_save_invalid_video_stream", "local_path": str(local_path)},
            )
            return None
        except Exception as exc:
            self.last_save_failure_kind = self.last_save_failure_kind or "mobile_save_failed"
            self.last_save_message = str(exc)
            LOGGER.warning(
                "mobile_save_failed",
                extra={
                    "event": "mobile_save_failed",
                    "failure_kind": self.last_save_failure_kind,
                    "error": str(exc)[:300],
                },
            )
            return None

    async def _list_mobile_media_files(
        self,
        *,
        scan_dirs: list[str] | tuple[str, ...] | None = None,
        extensions: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, MobileMediaFile]:
        scan_dirs = list(scan_dirs or _env_list("TIKTOK_MOBILE_SAVE_SCAN_DIRS", _DEFAULT_SAVE_SCAN_DIRS))
        extensions = _normalize_extensions(list(extensions or _env_list("TIKTOK_MOBILE_PULL_EXTENSIONS", _DEFAULT_PULL_EXTENSIONS)))
        script = _build_find_media_script(scan_dirs, extensions)
        stdout, _stderr = await _run_text(self._adb_args("shell", script), timeout=45.0)
        files: dict[str, MobileMediaFile] = {}
        for line in stdout.splitlines():
            parts = line.split("|", 2)
            if len(parts) != 3:
                continue
            modified_raw, size_raw, remote_path = parts
            remote_path = remote_path.strip()
            if not remote_path or Path(remote_path).suffix.lower() not in extensions:
                continue
            try:
                modified_at = float(modified_raw)
            except ValueError:
                modified_at = 0.0
            try:
                size = int(size_raw)
            except ValueError:
                size = 0
            files[remote_path] = MobileMediaFile(remote_path=remote_path, size=size, modified_at=modified_at)
        return files

    async def _tap_save_button_if_visible(self) -> bool:
        xml_text = await self._dump_window_xml()
        lowered_ui = xml_text.lower()
        if _contains_any(lowered_ui, _VERIFICATION_MARKERS) or _contains_any(lowered_ui, _LOGIN_MARKERS):
            LOGGER.warning(
                "mobile_save_button_not_found",
                extra={"event": "mobile_save_button_not_found", "reason": "login_or_verification_required"},
            )
            return False

        button = _find_actionable_ui_node(xml_text, role="save")
        if button is None:
            share_or_more = _find_actionable_ui_node(xml_text, role="share_or_more")
            if share_or_more is not None:
                await self._tap_node(share_or_more)
                await asyncio.sleep(1.2)
                xml_text = await self._dump_window_xml()
                lowered_ui = xml_text.lower()
                if _contains_any(lowered_ui, _VERIFICATION_MARKERS) or _contains_any(lowered_ui, _LOGIN_MARKERS):
                    LOGGER.warning(
                        "mobile_save_button_not_found",
                        extra={"event": "mobile_save_button_not_found", "reason": "login_or_verification_required_after_menu"},
                    )
                    return False
                button = _find_actionable_ui_node(xml_text, role="save")

        if button is None:
            LOGGER.info(
                "mobile_save_button_not_found",
                extra={"event": "mobile_save_button_not_found", "reason": "no_visible_save_or_download_button"},
            )
            return False

        LOGGER.info(
            "mobile_save_button_found",
            extra={
                "event": "mobile_save_button_found",
                "text": button.get("text", ""),
                "content_desc": button.get("content-desc", ""),
                "resource_id": button.get("resource-id", ""),
            },
        )
        await self._tap_node(button)
        LOGGER.info("mobile_save_tap_done", extra={"event": "mobile_save_tap_done"})
        return True

    async def _tap_node(self, node: dict[str, str]) -> None:
        bounds = _parse_bounds(node.get("bounds", ""))
        if bounds is None:
            raise RuntimeError("UI node has no tappable bounds")
        x1, y1, x2, y2 = bounds
        await _run_text(
            self._adb_args("shell", "input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2)),
            timeout=10.0,
        )

    async def _pull_mobile_file(self, remote_path: str, local_path: Path) -> Path:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        if local_path.exists():
            local_path.unlink()
        await _run_text(self._adb_args("pull", remote_path, str(local_path)), timeout=90.0)
        if not local_path.is_file():
            raise RuntimeError("adb pull completed but local file was not created")
        return local_path

    async def _validate_pulled_video(self, path: Path) -> bool:
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        ffprobe = shutil.which("ffprobe") or shutil.which("ffprobe.exe") or "ffprobe"
        try:
            stdout, _stderr = await _run_text(
                [
                    ffprobe,
                    "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=codec_type",
                    "-of", "json",
                    str(path),
                ],
                timeout=30.0,
            )
            data = json.loads(stdout or "{}")
            streams = data.get("streams") or []
            return any(str(stream.get("codec_type") or "").lower() == "video" for stream in streams)
        except Exception:
            return False

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


def _find_new_mobile_media_file(
    before: dict[str, MobileMediaFile],
    after: dict[str, MobileMediaFile],
) -> MobileMediaFile | None:
    candidates: list[MobileMediaFile] = []
    for remote_path, file_info in after.items():
        if file_info.size <= 0:
            continue
        previous = before.get(remote_path)
        if previous is None:
            candidates.append(file_info)
            continue
        if file_info.modified_at > previous.modified_at or file_info.size != previous.size:
            candidates.append(file_info)
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item.modified_at, item.size, item.remote_path), reverse=True)
    return candidates[0]


def _build_find_media_script(scan_dirs: list[str] | tuple[str, ...], extensions: list[str] | tuple[str, ...]) -> str:
    safe_dirs = [_shell_quote(directory) for directory in scan_dirs if str(directory).strip()]
    safe_extensions = _normalize_extensions(extensions)
    if not safe_dirs:
        safe_dirs = [_shell_quote(directory) for directory in _DEFAULT_SAVE_SCAN_DIRS]
    if not safe_extensions:
        safe_extensions = list(_DEFAULT_PULL_EXTENSIONS)
    name_filter = " -o ".join(f"-iname {_shell_quote('*' + extension)}" for extension in safe_extensions)
    return (
        f"for d in {' '.join(safe_dirs)}; do "
        '[ -d "$d" ] || continue; '
        f"find \"$d\" -type f \\( {name_filter} \\) 2>/dev/null; "
        "done | while IFS= read -r f; do "
        "meta=$(stat -c '%Y|%s' \"$f\" 2>/dev/null || echo '0|0'); "
        "echo \"$meta|$f\"; "
        "done"
    )


def _shell_quote(value: str) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _ui_nodes(xml_text: str) -> list[dict[str, str]]:
    if not xml_text:
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        nodes: list[dict[str, str]] = []
        for match in re.finditer(r"<node\b([^>]*)/?>", xml_text):
            attrs = {
                key: value
                for key, value in re.findall(r'([\w:-]+)="([^"]*)"', match.group(1))
            }
            if attrs:
                nodes.append(attrs)
        return nodes
    return [{key: str(value) for key, value in node.attrib.items()} for node in root.iter()]


def _ui_text_values(xml_text: str) -> list[str]:
    values: list[str] = []
    for node in _ui_nodes(xml_text):
        for attr in ("text", "content-desc", "resource-id"):
            value = str(node.get(attr) or "").strip()
            if value:
                values.append(value)
    return values


def _find_actionable_ui_node(xml_text: str, *, role: str) -> dict[str, str] | None:
    candidates: list[tuple[int, dict[str, str]]] = []
    for node in _ui_nodes(xml_text):
        if str(node.get("visible-to-user", "true")).lower() == "false":
            continue
        if str(node.get("enabled", "true")).lower() == "false":
            continue
        if _parse_bounds(node.get("bounds", "")) is None:
            continue
        if role == "save" and _node_matches_save(node):
            candidates.append((_save_node_rank(node), node))
        elif role == "share_or_more" and _node_matches_share_or_more(node):
            candidates.append((0, node))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _node_matches_save(node: dict[str, str]) -> bool:
    text = _normalize_ui_text(node.get("text", ""))
    content_desc = _normalize_ui_text(node.get("content-desc", ""))
    resource_id = _normalize_ui_text(node.get("resource-id", ""))
    if text in _SAVE_BUTTON_LABELS or content_desc in _SAVE_BUTTON_LABELS:
        return True
    if "saved" in resource_id or "favorite" in resource_id or "bookmark" in resource_id:
        return False
    return "save_video" in resource_id or "download_video" in resource_id or resource_id.endswith("/download")


def _save_node_rank(node: dict[str, str]) -> int:
    text = _normalize_ui_text(node.get("text", ""))
    content_desc = _normalize_ui_text(node.get("content-desc", ""))
    if text in _PREFERRED_SAVE_BUTTON_LABELS or content_desc in _PREFERRED_SAVE_BUTTON_LABELS:
        return 10
    if text in _SAVE_BUTTON_LABELS or content_desc in _SAVE_BUTTON_LABELS:
        return 5
    return 1


def _node_matches_share_or_more(node: dict[str, str]) -> bool:
    text = _normalize_ui_text(node.get("text", ""))
    content_desc = _normalize_ui_text(node.get("content-desc", ""))
    resource_id = _normalize_ui_text(node.get("resource-id", ""))
    if text in _SHARE_MORE_LABELS or content_desc in _SHARE_MORE_LABELS:
        return True
    return any(marker in resource_id for marker in ("/share", "share_button", "/more", "more_button", "more_options"))


def _normalize_ui_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def _parse_bounds(value: str) -> tuple[int, int, int, int] | None:
    match = re.search(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", str(value or ""))
    if not match:
        return None
    x1, y1, x2, y2 = (int(part) for part in match.groups())
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


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


def _env_float(name: str, *, default: float, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value < minimum or value > maximum:
        return default
    return value


def _env_list(name: str, default: list[str] | tuple[str, ...]) -> list[str]:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return list(default)
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or list(default)


def _normalize_extensions(values: list[str] | tuple[str, ...]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        extension = str(value or "").strip().lower()
        if not extension:
            continue
        if not extension.startswith("."):
            extension = "." + extension
        if extension in seen:
            continue
        seen.add(extension)
        normalized.append(extension)
    return normalized or list(_DEFAULT_PULL_EXTENSIONS)


def _mark_invalid_file(path: Path) -> None:
    if not path.exists():
        return
    invalid_path = path.with_suffix(path.suffix + ".invalid")
    try:
        if invalid_path.exists():
            invalid_path.unlink()
        path.replace(invalid_path)
    except OSError:
        try:
            path.unlink()
        except OSError:
            return


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
