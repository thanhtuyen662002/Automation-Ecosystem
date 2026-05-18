"""Runtime environment bootstrap for the local backend.

This module intentionally centralizes the small amount of startup mutation we
need for local/dev runs: load .env without overriding real OS variables, apply
safe defaults, normalize typed values, and expose non-secret readiness checks.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger("core.runtime_env")
_YTDLP_IMPERSONATE_TARGETS_CACHE: set[str] | None = None

_DEFAULT_ENV: dict[str, str] = {
    "ADSPOWER_API_BASE": "http://local.adspower.net:50325",
    "ADSPOWER_OPEN_TIMEOUT_SECONDS": "60",
    "ADSPOWER_VERIFY_AFTER_LOGIN": "true",
    "BROWSER_DIAGNOSTICS_ENABLED": "true",
    "TIKTOK_SEARCH_PROVIDER": "adspower",
    "TIKTOK_SEARCH_MIN_VIEWS": "0",
    "TIKTOK_MIN_VIEWS": "0",
    "TIKTOK_SEARCH_MIN_RELEVANCE_SCORE": "0",
    "TIKTOK_SEARCH_SCROLL_MAX": "20",
    "TIKTOK_SEARCH_STAGNANT_LIMIT": "5",
    "TIKTOK_SEARCH_APPLY_MIN_VIEWS": "false",
    "TIKTOK_DOWNLOAD_PROVIDER": "auto",
    "TIKTOK_DOWNLOAD_TIMEOUT_SECONDS": "120",
    "TIKTOK_YTDLP_IMPERSONATE": "",
    "TIKTOK_YTDLP_FORMAT": "bestvideo*+bestaudio/best[ext=mp4]/best",
    "TIKTOK_MOBILE_FALLBACK_ENABLED": "false",
    "TIKTOK_MOBILE_PROVIDER": "adb",
    "TIKTOK_MOBILE_DEVICE_ID": "",
    "TIKTOK_ANDROID_TIKTOK_PACKAGE": "com.zhiliaoapp.musically",
    "TIKTOK_MOBILE_REQUIRE_MANUAL_LOGIN": "true",
    "TIKTOK_MOBILE_SCROLL_ROUNDS": "10",
    "TIKTOK_MOBILE_SAVE_TIMEOUT_SECONDS": "45",
    "TIKTOK_MOBILE_SAVE_SCAN_DIRS": "/sdcard/DCIM,/sdcard/Movies,/sdcard/Download,/sdcard/Pictures",
    "TIKTOK_MOBILE_PULL_EXTENSIONS": ".mp4,.mov,.mkv,.webm",
}

_BOOL_VARS = {
    "ADSPOWER_VERIFY_AFTER_LOGIN",
    "BROWSER_DIAGNOSTICS_ENABLED",
    "TIKTOK_SEARCH_APPLY_MIN_VIEWS",
    "TIKTOK_MOBILE_FALLBACK_ENABLED",
    "TIKTOK_MOBILE_REQUIRE_MANUAL_LOGIN",
}

_FLOAT_VARS: dict[str, tuple[float, float]] = {
    "ADSPOWER_OPEN_TIMEOUT_SECONDS": (1.0, 300.0),
    "TIKTOK_SEARCH_MIN_RELEVANCE_SCORE": (0.0, 1.0),
    "TIKTOK_DOWNLOAD_TIMEOUT_SECONDS": (10.0, 600.0),
    "TIKTOK_MOBILE_SAVE_TIMEOUT_SECONDS": (5.0, 300.0),
}

_INT_VARS: dict[str, tuple[int, int]] = {
    "TIKTOK_SEARCH_MIN_VIEWS": (0, 1_000_000_000),
    "TIKTOK_MIN_VIEWS": (0, 1_000_000_000),
    "TIKTOK_SEARCH_SCROLL_MAX": (1, 200),
    "TIKTOK_SEARCH_STAGNANT_LIMIT": (1, 50),
    "TIKTOK_MOBILE_SCROLL_ROUNDS": (1, 200),
}

_LIST_VARS = {
    "TIKTOK_MOBILE_SAVE_SCAN_DIRS",
    "TIKTOK_MOBILE_PULL_EXTENSIONS",
}

_DOWNLOAD_PROVIDERS = {"auto", "web_only", "ytdlp_only", "browser_first", "mobile_first"}
_BOOL_TRUE = {"1", "true", "yes", "on"}
_BOOL_FALSE = {"0", "false", "no", "off"}


def bootstrap_runtime_env() -> dict[str, Any]:
    """Load, normalize, validate, and set backend runtime environment values.

    OS-provided values win over .env values. Defaults are only applied when a
    variable is absent. Typed values are normalized back into ``os.environ`` so
    older modules that read raw strings get the same effective configuration.
    """

    loaded_env_file = _load_dotenv_no_override()
    warnings: list[dict[str, str]] = []

    for key, default in _DEFAULT_ENV.items():
        os.environ.setdefault(key, default)

    effective: dict[str, Any] = {}
    for key, default in _DEFAULT_ENV.items():
        if key in _BOOL_VARS:
            value, warning = _parse_bool_env(key, default=_parse_bool(default))
            os.environ[key] = "true" if value else "false"
            effective[key] = value
        elif key in _INT_VARS:
            minimum, maximum = _INT_VARS[key]
            value, warning = _parse_int_env(
                key,
                default=int(_DEFAULT_ENV[key]),
                minimum=minimum,
                maximum=maximum,
            )
            os.environ[key] = str(value)
            effective[key] = value
        elif key in _FLOAT_VARS:
            minimum, maximum = _FLOAT_VARS[key]
            value, warning = _parse_float_env(
                key,
                default=float(_DEFAULT_ENV[key]),
                minimum=minimum,
                maximum=maximum,
            )
            os.environ[key] = _format_number(value)
            effective[key] = value
        elif key in _LIST_VARS:
            value, warning = _parse_list_env(key, default=_DEFAULT_ENV[key])
            if key == "TIKTOK_MOBILE_PULL_EXTENSIONS":
                value = _normalize_extensions(value)
                if not value:
                    value = _normalize_extensions(_DEFAULT_ENV[key].split(","))
            os.environ[key] = ",".join(value)
            effective[key] = value
        else:
            value = str(os.environ.get(key, default)).strip()
            if key == "TIKTOK_DOWNLOAD_PROVIDER" and value.lower() not in _DOWNLOAD_PROVIDERS:
                warning = _invalid_warning(key, value, default)
                value = default
            elif key == "TIKTOK_MOBILE_PROVIDER" and value.lower() != "adb":
                warning = _invalid_warning(key, value, default)
                value = default
            else:
                warning = None
            if key in {"TIKTOK_DOWNLOAD_PROVIDER", "TIKTOK_MOBILE_PROVIDER", "TIKTOK_SEARCH_PROVIDER"}:
                value = value.lower()
            os.environ[key] = value
            effective[key] = value
        if warning is not None:
            warnings.append(warning)
            LOGGER.warning("runtime_env_invalid_value", extra={"event": "runtime_env_invalid_value", **warning})

    tools = runtime_tool_status()
    if target_warning := ytdlp_impersonate_target_warning():
        warnings.append(target_warning)
        LOGGER.warning("download_ytdlp_impersonate_target_unavailable", extra={"event": "download_ytdlp_impersonate_target_unavailable", **target_warning})
    effective.update(tools)
    effective["loaded_env_file"] = str(loaded_env_file) if loaded_env_file else ""
    effective["warnings"] = warnings

    LOGGER.info(
        "runtime_env_bootstrapped",
        extra={
            "event": "runtime_env_bootstrapped",
            "ADSPOWER_API_BASE": os.environ.get("ADSPOWER_API_BASE", ""),
            "TIKTOK_SEARCH_PROVIDER": os.environ.get("TIKTOK_SEARCH_PROVIDER", ""),
            "TIKTOK_DOWNLOAD_PROVIDER": os.environ.get("TIKTOK_DOWNLOAD_PROVIDER", ""),
            "TIKTOK_YTDLP_IMPERSONATE": os.environ.get("TIKTOK_YTDLP_IMPERSONATE", ""),
            "TIKTOK_YTDLP_FORMAT": os.environ.get("TIKTOK_YTDLP_FORMAT", ""),
            "TIKTOK_MOBILE_FALLBACK_ENABLED": effective["TIKTOK_MOBILE_FALLBACK_ENABLED"],
            "TIKTOK_MOBILE_PROVIDER": os.environ.get("TIKTOK_MOBILE_PROVIDER", ""),
            "TIKTOK_MOBILE_DEVICE_ID": os.environ.get("TIKTOK_MOBILE_DEVICE_ID", ""),
            "TIKTOK_ANDROID_TIKTOK_PACKAGE": os.environ.get("TIKTOK_ANDROID_TIKTOK_PACKAGE", ""),
            "TIKTOK_MOBILE_SAVE_SCAN_DIRS": os.environ.get("TIKTOK_MOBILE_SAVE_SCAN_DIRS", ""),
            "ffmpeg_available": tools["ffmpeg_available"],
            "ffprobe_available": tools["ffprobe_available"],
            "adb_available": tools["adb_available"],
            "curl_cffi_available": tools["curl_cffi_available"],
            "yt_dlp_available": tools["yt_dlp_available"],
            "TIKTOK_YTDLP_IMPERSONATE_AVAILABLE": not bool(target_warning),
            "TIKTOK_YTDLP_IMPERSONATE_FALLBACK": (target_warning or {}).get("fallback_target", ""),
        },
    )
    return effective


def runtime_tool_status() -> dict[str, bool]:
    return {
        "ffmpeg_available": _command_available("ffmpeg"),
        "ffprobe_available": _command_available("ffprobe"),
        "adb_available": _command_available("adb"),
        "curl_cffi_available": importlib.util.find_spec("curl_cffi") is not None,
        "yt_dlp_available": _yt_dlp_command() != "",
    }


def runtime_dependency_warnings(mobile_status: dict[str, Any] | None = None) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    tools = runtime_tool_status()
    impersonate_target = os.environ.get("TIKTOK_YTDLP_IMPERSONATE", "").strip()
    if impersonate_target and not tools["curl_cffi_available"]:
        warnings.append({
            "code": "download_ytdlp_impersonate_dependency_missing",
            "message": "TIKTOK_YTDLP_IMPERSONATE is enabled but curl_cffi is not installed.",
            "impersonate_target": impersonate_target,
            "install_command": "pip install -U yt-dlp curl-cffi",
        })
    if warning := ytdlp_impersonate_target_warning():
        warnings.append(warning)
    if not tools["ffmpeg_available"]:
        warnings.append({
            "code": "ffmpeg_missing",
            "message": "ffmpeg is not available on PATH; video remake tasks may fail.",
        })
    if not tools["ffprobe_available"]:
        warnings.append({
            "code": "ffprobe_missing",
            "message": "ffprobe is not available on PATH; downloaded videos cannot be validated.",
        })

    mobile_enabled = env_bool("TIKTOK_MOBILE_FALLBACK_ENABLED", default=False)
    if mobile_enabled:
        if not tools["adb_available"]:
            warnings.append({
                "code": "mobile_adb_missing",
                "message": "TIKTOK_MOBILE_FALLBACK_ENABLED=true but adb is not available on PATH.",
            })
        if mobile_status is not None:
            if not bool(mobile_status.get("device_available")):
                warnings.append({
                    "code": "mobile_device_unavailable",
                    "message": "TIKTOK_MOBILE_FALLBACK_ENABLED=true but no Android device/emulator is available.",
                })
            if bool(mobile_status.get("device_available")) and not bool(mobile_status.get("tiktok_app_installed")):
                warnings.append({
                    "code": "mobile_tiktok_app_missing",
                    "message": "Android device is available, but the configured TikTok app package is not installed.",
                })
    return _dedupe_warnings(warnings)


def env_bool(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return _parse_bool(raw)
    except ValueError:
        return default


def ytdlp_impersonate_target_warning() -> dict[str, str] | None:
    requested = os.environ.get("TIKTOK_YTDLP_IMPERSONATE", "").strip()
    if not requested:
        return None
    targets = available_ytdlp_impersonate_targets()
    if requested.lower() in targets:
        return None
    fallback = next((target for target in ("chrome", "chrome-120", "chrome-110", "edge", "safari") if target in targets), "")
    return {
        "code": "download_ytdlp_impersonate_target_unavailable",
        "message": "TIKTOK_YTDLP_IMPERSONATE is set, but the requested yt-dlp impersonate target is not available.",
        "requested_target": requested,
        "fallback_target": fallback,
        "available_targets": ",".join(sorted(targets)),
        "check_command": "yt-dlp --list-impersonate-targets",
    }


def available_ytdlp_impersonate_targets() -> set[str]:
    global _YTDLP_IMPERSONATE_TARGETS_CACHE

    if _YTDLP_IMPERSONATE_TARGETS_CACHE is not None:
        return set(_YTDLP_IMPERSONATE_TARGETS_CACHE)
    command = _yt_dlp_command()
    if not command:
        _YTDLP_IMPERSONATE_TARGETS_CACHE = set()
        return set()
    try:
        result = subprocess.run(
            [command, "--list-impersonate-targets"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        _YTDLP_IMPERSONATE_TARGETS_CACHE = set()
        return set()
    if result.returncode != 0:
        _YTDLP_IMPERSONATE_TARGETS_CACHE = set()
        return set()
    _YTDLP_IMPERSONATE_TARGETS_CACHE = _parse_ytdlp_impersonate_targets(result.stdout)
    return set(_YTDLP_IMPERSONATE_TARGETS_CACHE)


def _load_dotenv_no_override() -> Path | None:
    env_file = _find_env_file()
    if env_file is None:
        return None
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=env_file, override=False)
        return env_file
    except ModuleNotFoundError:
        _manual_load_env(env_file)
        return env_file


def _find_env_file() -> Path | None:
    candidates: list[Path] = []
    if raw := os.environ.get("AE_ENV_FILE", "").strip():
        candidates.append(Path(raw))
    candidates.append(Path.cwd() / ".env")
    candidates.append(Path(__file__).resolve().parents[1] / ".env")
    for candidate in candidates:
        try:
            path = candidate.expanduser().resolve()
        except OSError:
            continue
        if path.is_file():
            return path
    return None


def _manual_load_env(path: Path) -> None:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = _strip_quotes(value.strip())


def _parse_bool_env(name: str, *, default: bool) -> tuple[bool, dict[str, str] | None]:
    raw = os.environ.get(name, "")
    try:
        return _parse_bool(raw), None
    except ValueError:
        return default, _invalid_warning(name, raw, "true" if default else "false")


def _parse_int_env(
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> tuple[int, dict[str, str] | None]:
    raw = os.environ.get(name, "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default, _invalid_warning(name, raw, str(default))
    if value < minimum or value > maximum:
        return default, _invalid_warning(name, raw, str(default))
    return value, None


def _parse_float_env(
    name: str,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> tuple[float, dict[str, str] | None]:
    raw = os.environ.get(name, "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default, _invalid_warning(name, raw, _format_number(default))
    if value < minimum or value > maximum:
        return default, _invalid_warning(name, raw, _format_number(default))
    return value, None


def _parse_list_env(name: str, *, default: str) -> tuple[list[str], dict[str, str] | None]:
    raw = os.environ.get(name, "")
    values = [item.strip() for item in str(raw).split(",") if item.strip()]
    if values:
        return values, None
    default_values = [item.strip() for item in default.split(",") if item.strip()]
    return default_values, _invalid_warning(name, raw, default)


def _parse_bool(raw: str) -> bool:
    value = str(raw).strip().lower()
    if value in _BOOL_TRUE:
        return True
    if value in _BOOL_FALSE:
        return False
    raise ValueError(f"Invalid boolean value: {raw!r}")


def _normalize_extensions(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        ext = item.strip().lower()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = "." + ext
        if ext in seen:
            continue
        seen.add(ext)
        normalized.append(ext)
    return normalized


def _parse_ytdlp_impersonate_targets(stdout: str) -> set[str]:
    targets: set[str] = set()
    for raw_line in str(stdout or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("[") or line.startswith("-") or line.lower().startswith("client"):
            continue
        if "unavailable" in line.lower():
            continue
        parts = line.split()
        if not parts:
            continue
        target = parts[0].strip().lower()
        if target and target not in {"-", "client"}:
            targets.add(target)
    return targets


def _invalid_warning(name: str, raw: object, fallback: str) -> dict[str, str]:
    return {
        "variable": name,
        "value_preview": str(raw)[:80],
        "fallback": fallback,
    }


def _format_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return str(value)


def _command_available(command: str) -> bool:
    if shutil.which(command) or shutil.which(f"{command}.exe"):
        return True
    return Path(command).is_file()


def _yt_dlp_command() -> str:
    found = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    return found or ""


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _dedupe_warnings(warnings: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for warning in warnings:
        code = warning.get("code", "")
        if code in seen:
            continue
        seen.add(code)
        result.append(warning)
    return result
