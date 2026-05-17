"""
Handler: tiktok.download_videos
─────────────────────────────────
Input payload:
  job_id:      str  – used to namespace the output directory

Reads 'selected_videos' from parent select_videos result (via parent_results or payload).

Output result:
  video_paths:  list[str]   – local file paths of successfully downloaded videos
  failed_urls:  list[str]   – URLs that could not be downloaded
  output_dir:   str
  ok:           bool
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import random
import tempfile
from pathlib import Path
from typing import Any

from workers.handlers.tiktok._base import (
    SubprocessError,
    check_already_processed,
    get_media_output_dir,
    get_ytdlp_path,
    random_jitter,
    resolve_parent_result,
    run_subprocess,
)
from workers.worker_runtime import FatalDependencyError

LOGGER = logging.getLogger("workers.handlers.tiktok.download_videos")

_DEFAULT_YTDLP_FORMAT = "bestvideo*+bestaudio/best[ext=mp4]/best"
_TEMP_SUFFIXES = {".part", ".ytdl", ".temp", ".tmp", ".frag"}
_VALID_MEDIA_SUFFIXES = {".mp4", ".m4v", ".mov", ".webm", ".mkv"}
_AUDIO_ONLY_SUFFIXES = {".mp3", ".m4a", ".aac", ".opus", ".ogg", ".wav", ".flac"}
_TRACKED_MEDIA_SUFFIXES = _VALID_MEDIA_SUFFIXES | _AUDIO_ONLY_SUFFIXES
_PREVIEW_CHARS = 1200
_IMPERSONATION_DEPENDENCY_WARNED = False


async def download_videos_handler(payload: dict[str, Any]) -> dict[str, Any]:
    # ── Idempotency guard ─────────────────────────────────────────────────────
    if (cached := check_already_processed(payload)) is not None:
        return cached

    # ── Resolve selected videos from parent result ────────────────────────────
    try:
        selected_videos: list[dict[str, Any]] = list(resolve_parent_result(payload, "selected_videos"))
    except KeyError:
        selected_videos_raw = payload.get("selected_videos")
        if not selected_videos_raw:
            raise ValueError("download_videos requires 'selected_videos' in payload or parent_results")
        selected_videos = list(selected_videos_raw)

    if not selected_videos:
        raise ValueError("selected_videos is empty — nothing to download")

    # ── Output directory ──────────────────────────────────────────────────────
    job_id: str = str(payload.get("job_id", "unknown_job"))
    account_id: str = str(payload.get("account_id") or "").strip()
    base_output_dir = get_media_output_dir()
    output_dir = base_output_dir / job_id / "downloads"
    output_dir.mkdir(parents=True, exist_ok=True)
    _warn_if_impersonation_dependency_missing()

    LOGGER.info(
        "download_videos_start",
        extra={
            "event": "download_videos_start",
            "video_count": len(selected_videos),
            "has_account_id": bool(account_id),
            "output_dir": str(output_dir),
        },
    )

    await random_jitter(1.0, 3.0)

    video_paths: list[str] = []
    failed_urls: list[str] = []
    failed_downloads: list[dict[str, Any]] = []
    cookie_tmpdir: tempfile.TemporaryDirectory[str] | None = None
    cookie_file: Path | None = None
    cookie_user_agent: str | None = None
    cookie_export_attempted = False

    try:
        for idx, video in enumerate(selected_videos):
            url: str = video.get("url", "")
            if not url:
                LOGGER.warning(
                    "download_skip_empty_url",
                    extra={"event": "download_skip_empty_url", "index": idx},
                )
                continue

            output_template = str(output_dir / f"video_{idx:02d}.%(ext)s")
            LOGGER.info(
                "download_video_start",
                extra={"event": "download_video_start", "url": url, "index": idx},
            )

            _remove_existing_attempt_files(output_dir, idx)

            attempt_errors: list[dict[str, Any]] = []
            primary_attempt = await _attempt_ytdlp_download(
                url=url,
                output_template=output_template,
                output_dir=output_dir,
                index=idx,
                mode="primary",
            )
            if primary_attempt["ok"]:
                downloaded = primary_attempt["path"]
            else:
                downloaded = None
                attempt_errors.append(primary_attempt)
                LOGGER.warning(
                    "download_video_primary_failed",
                    extra={
                        "event": "download_video_primary_failed",
                        "url": url,
                        "error": primary_attempt.get("error", "")[:500],
                        "stdout_preview": primary_attempt.get("stdout_preview", ""),
                        "stderr_preview": primary_attempt.get("stderr_preview", ""),
                    },
                )
                if not account_id:
                    LOGGER.info(
                        "download_cookie_fallback_skipped_no_account_id",
                        extra={"event": "download_cookie_fallback_skipped_no_account_id", "url": url},
                    )
                    failed_urls.append(url)
                    failed_downloads.append({
                        "url": url,
                        "index": idx,
                        "attempt_errors": attempt_errors,
                        "matching_files": _matching_download_files(output_dir, idx),
                    })
                    continue

                if not cookie_export_attempted:
                    cookie_export_attempted = True
                    cookie_tmpdir = tempfile.TemporaryDirectory(prefix="ae_tiktok_cookies_")
                    cookie_file = Path(cookie_tmpdir.name) / "cookies.txt"
                    try:
                        cookie_user_agent = await _export_adspower_tiktok_cookies(account_id, cookie_file)
                    except Exception as cookie_exc:
                        attempt_errors.append({
                            "ok": False,
                            "mode": "cookie_export",
                            "error": str(cookie_exc),
                            "stdout_preview": "",
                            "stderr_preview": "",
                            "matching_files": _matching_download_files(output_dir, idx),
                        })
                        LOGGER.warning(
                            "download_video_cookie_fallback_failed",
                            extra={
                                "event": "download_video_cookie_fallback_failed",
                                "url": url,
                                "error": str(cookie_exc)[:300],
                            },
                        )
                        cookie_file = None

                if cookie_file is None:
                    failed_urls.append(url)
                    failed_downloads.append({
                        "url": url,
                        "index": idx,
                        "attempt_errors": attempt_errors,
                        "matching_files": _matching_download_files(output_dir, idx),
                    })
                    continue

                LOGGER.info(
                    "download_video_cookie_fallback_start",
                    extra={"event": "download_video_cookie_fallback_start", "url": url, "account_id": account_id},
                )
                fallback_attempt = await _attempt_ytdlp_download(
                    url=url,
                    output_template=output_template,
                    output_dir=output_dir,
                    index=idx,
                    mode="cookie_fallback",
                    cookies_file=cookie_file,
                    user_agent=cookie_user_agent,
                )
                if fallback_attempt["ok"]:
                    downloaded = fallback_attempt["path"]
                    LOGGER.info(
                        "download_video_cookie_fallback_done",
                        extra={"event": "download_video_cookie_fallback_done", "url": url, "account_id": account_id},
                    )
                else:
                    attempt_errors.append(fallback_attempt)
                    LOGGER.warning(
                        "download_video_cookie_fallback_failed",
                        extra={
                            "event": "download_video_cookie_fallback_failed",
                            "url": url,
                            "error": fallback_attempt.get("error", "")[:500],
                            "stdout_preview": fallback_attempt.get("stdout_preview", ""),
                            "stderr_preview": fallback_attempt.get("stderr_preview", ""),
                        },
                    )
                    failed_urls.append(url)
                    failed_downloads.append({
                        "url": url,
                        "index": idx,
                        "attempt_errors": attempt_errors,
                        "matching_files": _matching_download_files(output_dir, idx),
                    })
                    continue

            if downloaded:
                video_path = str(downloaded)
                video_paths.append(video_path)
                LOGGER.info(
                    "download_video_done",
                    extra={"event": "download_video_done", "url": url, "path": video_path},
                )
            else:
                LOGGER.warning(
                    "download_video_missing_file",
                    extra={
                        "event": "download_video_missing_file",
                        "url": url,
                        "attempt_errors": attempt_errors,
                        "matching_files": _matching_download_files(output_dir, idx),
                    },
                )
                failed_urls.append(url)
                failed_downloads.append({
                    "url": url,
                    "index": idx,
                    "attempt_errors": attempt_errors,
                    "matching_files": _matching_download_files(output_dir, idx),
                })

            # Anti-abuse delay between downloads
            if idx < len(selected_videos) - 1:
                delay = random.uniform(3.0, 10.0)
                await asyncio.sleep(delay)
    finally:
        if cookie_tmpdir is not None:
            cookie_tmpdir.cleanup()

    if not video_paths:
        diagnostics = _format_download_failures(failed_downloads)
        if _all_failures_are_permanent_download_failures(failed_downloads):
            raise FatalDependencyError(
                f"All {len(selected_videos)} videos failed permanently during download. "
                f"URLs: {failed_urls[:3]}. {diagnostics}"
            )
        raise RuntimeError(
            f"All {len(selected_videos)} videos failed to download. URLs: {failed_urls[:3]}. {diagnostics}"
        )

    LOGGER.info(
        "download_videos_done",
        extra={
            "event": "download_videos_done",
            "downloaded": len(video_paths),
            "failed": len(failed_urls),
        },
    )

    return {
        "video_paths": video_paths,
        "failed_urls": failed_urls,
        "failed_downloads": failed_downloads,
        "output_dir": str(output_dir),
        "ok": True,
    }


async def _attempt_ytdlp_download(
    *,
    url: str,
    output_template: str,
    output_dir: Path,
    index: int,
    mode: str,
    cookies_file: Path | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    try:
        output = await _download_with_ytdlp(
            url,
            output_template,
            mode=mode,
            cookies_file=cookies_file,
            user_agent=user_agent,
        )
    except SubprocessError as exc:
        matching_files = _matching_download_files(output_dir, index)
        LOGGER.info(
            "download_video_attempt_files",
            extra={
                "event": "download_video_attempt_files",
                "url": url,
                "index": index,
                "mode": mode,
                "matching_files": matching_files,
            },
        )
        return {
            "ok": False,
            "mode": mode,
            "error": str(exc),
            "failure_kind": "http_403" if _is_http_403_error(str(exc), exc.stderr) else "yt_dlp_error",
            "returncode": exc.returncode,
            "stdout_preview": _preview(exc.stdout),
            "stderr_preview": _preview(exc.stderr),
            "matching_files": matching_files,
        }

    matching_files = _matching_download_files(output_dir, index)
    LOGGER.info(
        "download_video_attempt_files",
        extra={
            "event": "download_video_attempt_files",
            "url": url,
            "index": index,
            "mode": mode,
            "matching_files": matching_files,
        },
    )

    downloaded = _find_downloaded_file(output_dir, index)
    if downloaded is None:
        audio_files = _audio_only_download_files(matching_files)
        if audio_files:
            error = "yt-dlp produced audio-only file(s), no video file"
            LOGGER.warning(
                "download_video_audio_only",
                extra={
                    "event": "download_video_audio_only",
                    "url": url,
                    "index": index,
                    "mode": mode,
                    "output_template": output_template,
                    "audio_files": audio_files,
                    "stdout_preview": output["stdout_preview"],
                    "stderr_preview": output["stderr_preview"],
                    "printed_filepaths": output["printed_filepaths"],
                    "matching_files": matching_files,
                },
            )
        else:
            error = "yt-dlp completed but output file was not found"
        LOGGER.warning(
            "download_video_missing_file_after_ytdlp",
            extra={
                "event": "download_video_missing_file_after_ytdlp",
                "url": url,
                "index": index,
                "mode": mode,
                "output_template": output_template,
                "stdout_preview": output["stdout_preview"],
                "stderr_preview": output["stderr_preview"],
                "printed_filepaths": output["printed_filepaths"],
                "matching_files": matching_files,
                "audio_files": audio_files,
            },
        )
        return {
            "ok": False,
            "mode": mode,
            "error": error,
            "failure_kind": "audio_only" if audio_files else "missing_output",
            "stdout_preview": output["stdout_preview"],
            "stderr_preview": output["stderr_preview"],
            "printed_filepaths": output["printed_filepaths"],
            "matching_files": matching_files,
            "audio_files": audio_files,
        }

    probe = await _probe_video_stream(downloaded)
    if not probe.get("has_video"):
        error = "Downloaded media has no video stream"
        LOGGER.warning(
            "download_video_audio_only",
            extra={
                "event": "download_video_audio_only",
                "url": url,
                "index": index,
                "mode": mode,
                "path": str(downloaded),
                "probe": probe,
                "stdout_preview": output["stdout_preview"],
                "stderr_preview": output["stderr_preview"],
                "printed_filepaths": output["printed_filepaths"],
                "matching_files": matching_files,
            },
        )
        return {
            "ok": False,
            "mode": mode,
            "error": error,
            "failure_kind": "audio_only" if probe.get("ok") else "probe_failed",
            "path": downloaded,
            "stdout_preview": output["stdout_preview"],
            "stderr_preview": output["stderr_preview"],
            "printed_filepaths": output["printed_filepaths"],
            "matching_files": matching_files,
            "probe": probe,
        }

    return {
        "ok": True,
        "mode": mode,
        "path": downloaded,
        "stdout_preview": output["stdout_preview"],
        "stderr_preview": output["stderr_preview"],
        "printed_filepaths": output["printed_filepaths"],
        "matching_files": matching_files,
    }


async def _download_with_ytdlp(
    url: str,
    output_template: str,
    *,
    mode: str,
    cookies_file: Path | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    try:
        ytdlp_path = get_ytdlp_path()
    except FileNotFoundError as exc:
        raise SubprocessError(str(exc)) from exc

    timeout = _download_timeout_seconds()
    impersonate_target = _ytdlp_impersonate_target()
    ytdlp_format = _ytdlp_format()
    _warn_if_impersonation_dependency_missing()
    args = [
        ytdlp_path,
        "--format", ytdlp_format,
        "--merge-output-format", "mp4",
        "--no-playlist",
        "--restrict-filenames",
        "--windows-filenames",
        "--no-mtime",
        "--force-overwrites",
        "--retries", "2",
        "--fragment-retries", "2",
        "--print", "after_move:filepath",
        "--output", output_template,
    ]
    if impersonate_target:
        args.extend(["--impersonate", impersonate_target])
    if user_agent:
        args.extend(["--add-header", f"User-Agent:{user_agent}"])
    if cookies_file is not None:
        args.extend(["--cookies", str(cookies_file)])
    args.append(url)
    LOGGER.info(
        "download_video_ytdlp_start",
        extra={
            "event": "download_video_ytdlp_start",
            "url": url,
            "command_mode": mode,
            "output_template": output_template,
            "format": ytdlp_format,
            "cookies_used": cookies_file is not None,
            "user_agent_used": bool(user_agent),
            "impersonate_target": impersonate_target,
            "timeout_seconds": timeout,
            "yt_dlp_path": ytdlp_path,
        },
    )
    try:
        stdout, stderr = await run_subprocess(*args, timeout=timeout)
    except SubprocessError as exc:
        LOGGER.warning(
            "download_video_ytdlp_failed",
            extra={
                "event": "download_video_ytdlp_failed",
                "url": url,
                "command_mode": mode,
                "output_template": output_template,
                "format": ytdlp_format,
                "cookies_used": cookies_file is not None,
                "user_agent_used": bool(user_agent),
                "impersonate_target": impersonate_target,
                "returncode": exc.returncode,
                "error": str(exc)[:500],
                "stdout_preview": _preview(exc.stdout),
                "stderr_preview": _preview(exc.stderr),
            },
        )
        raise

    printed_filepaths = _extract_printed_filepaths(stdout)
    LOGGER.info(
        "download_video_ytdlp_done",
        extra={
            "event": "download_video_ytdlp_done",
            "url": url,
            "command_mode": mode,
            "output_template": output_template,
            "format": ytdlp_format,
            "cookies_used": cookies_file is not None,
            "user_agent_used": bool(user_agent),
            "impersonate_target": impersonate_target,
            "stdout_preview": _preview(stdout),
            "stderr_preview": _preview(stderr),
            "printed_filepaths": printed_filepaths,
        },
    )
    return {
        "stdout_preview": _preview(stdout),
        "stderr_preview": _preview(stderr),
        "printed_filepaths": printed_filepaths,
    }


def _find_downloaded_file(output_dir: Path, idx: int) -> Path | None:
    candidates: list[tuple[bool, float, Path]] = []
    for path in output_dir.glob(f"video_{idx:02d}.*"):
        try:
            if not path.is_file():
                continue
            if _is_temp_download_file(path):
                continue
            suffix = path.suffix.lower()
            if suffix not in _VALID_MEDIA_SUFFIXES:
                continue
            stat = path.stat()
            if stat.st_size <= 0:
                continue
            candidates.append((suffix == ".mp4", stat.st_mtime, path))
        except OSError:
            continue

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


async def _probe_video_stream(path: Path) -> dict[str, Any]:
    try:
        stdout, stderr = await run_subprocess(
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_type",
            "-of", "json",
            str(path),
            timeout=30.0,
        )
        data = json.loads(stdout or "{}")
        streams = data.get("streams") or []
        has_video = any(str(stream.get("codec_type") or "").lower() == "video" for stream in streams)
        return {
            "ok": True,
            "has_video": has_video,
            "stream_count": len(streams),
            "stdout_preview": _preview(stdout),
            "stderr_preview": _preview(stderr),
        }
    except SubprocessError as exc:
        return {
            "ok": False,
            "has_video": False,
            "error": str(exc),
            "stdout_preview": _preview(exc.stdout),
            "stderr_preview": _preview(exc.stderr),
        }
    except Exception as exc:
        return {
            "ok": False,
            "has_video": False,
            "error": str(exc),
            "stdout_preview": "",
            "stderr_preview": "",
        }


def _download_timeout_seconds() -> float:
    raw = os.environ.get("TIKTOK_DOWNLOAD_TIMEOUT_SECONDS", "90").strip()
    try:
        timeout = float(raw)
    except ValueError:
        timeout = 90.0
    return max(10.0, timeout)


def _ytdlp_format() -> str:
    return os.environ.get("TIKTOK_YTDLP_FORMAT", _DEFAULT_YTDLP_FORMAT).strip() or _DEFAULT_YTDLP_FORMAT


def _ytdlp_impersonate_target() -> str:
    return os.environ.get("TIKTOK_YTDLP_IMPERSONATE", "").strip()


def _warn_if_impersonation_dependency_missing() -> None:
    global _IMPERSONATION_DEPENDENCY_WARNED

    impersonate_target = _ytdlp_impersonate_target()
    if not impersonate_target or _IMPERSONATION_DEPENDENCY_WARNED:
        return
    if importlib.util.find_spec("curl_cffi") is not None:
        return
    _IMPERSONATION_DEPENDENCY_WARNED = True
    LOGGER.warning(
        "download_ytdlp_impersonate_dependency_missing",
        extra={
            "event": "download_ytdlp_impersonate_dependency_missing",
            "impersonate_target": impersonate_target,
            "install_command": "pip install -U yt-dlp curl-cffi",
        },
    )


def _preview(text: str, max_chars: int = _PREVIEW_CHARS) -> str:
    return str(text or "").replace("\r", "\n").strip()[:max_chars]


def _extract_printed_filepaths(stdout: str) -> list[str]:
    paths: list[str] = []
    for line in str(stdout or "").splitlines():
        value = line.strip()
        if not value:
            continue
        lower = value.lower()
        if lower.startswith("[") and "]" in lower:
            continue
        if Path(value).suffix.lower() in _TRACKED_MEDIA_SUFFIXES:
            paths.append(value)
    return paths[:10]


def _matching_download_files(output_dir: Path, idx: int) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for path in sorted(output_dir.glob(f"video_{idx:02d}*")):
        try:
            stat = path.stat()
            matches.append({
                "name": path.name,
                "path": str(path),
                "size": stat.st_size,
                "is_file": path.is_file(),
                "is_temp": _is_temp_download_file(path),
                "suffix": path.suffix.lower(),
                "media_kind": "audio" if path.suffix.lower() in _AUDIO_ONLY_SUFFIXES else "video",
            })
        except OSError as exc:
            matches.append({
                "name": path.name,
                "path": str(path),
                "error": str(exc)[:200],
            })
    return matches


def _audio_only_download_files(matching_files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        file_info
        for file_info in matching_files
        if str(file_info.get("suffix") or "").lower() in _AUDIO_ONLY_SUFFIXES
        and bool(file_info.get("is_file", True))
        and not bool(file_info.get("is_temp", False))
        and int(file_info.get("size") or 0) > 0
    ]


def _is_http_403_error(*parts: str) -> bool:
    text = "\n".join(str(part or "") for part in parts).lower()
    return "403" in text and ("forbidden" in text or "http error 403" in text)


def _all_failures_are_permanent_download_failures(failed_downloads: list[dict[str, Any]]) -> bool:
    if not failed_downloads:
        return False
    permanent_kinds = {"audio_only", "http_403"}
    for item in failed_downloads:
        errors = item.get("attempt_errors") or []
        if not errors:
            return False
        if not any(str(error.get("failure_kind") or "") in permanent_kinds for error in errors):
            return False
    return True


def _remove_existing_attempt_files(output_dir: Path, idx: int) -> None:
    removed: list[str] = []
    for path in output_dir.glob(f"video_{idx:02d}*"):
        try:
            if path.is_file():
                path.unlink()
                removed.append(path.name)
        except OSError as exc:
            LOGGER.warning(
                "download_video_cleanup_failed",
                extra={
                    "event": "download_video_cleanup_failed",
                    "index": idx,
                    "path": str(path),
                    "error": str(exc)[:200],
                },
            )
    if removed:
        LOGGER.info(
            "download_video_cleanup_existing_files",
            extra={
                "event": "download_video_cleanup_existing_files",
                "index": idx,
                "output_dir": str(output_dir),
                "removed_files": removed,
            },
        )


def _format_download_failures(failed_downloads: list[dict[str, Any]]) -> str:
    summaries: list[str] = []
    for item in failed_downloads[:3]:
        errors = item.get("attempt_errors") or []
        last_error = errors[-1] if errors else {}
        stderr = last_error.get("stderr_preview") or ""
        stdout = last_error.get("stdout_preview") or ""
        summaries.append(
            "download_error("
            f"index={item.get('index')}, "
            f"url={item.get('url')}, "
            f"error={last_error.get('error', '')[:500]}, "
            f"stderr={stderr[:800]}, "
            f"stdout={stdout[:400]}"
            ")"
        )
    return " | ".join(summaries)


def _is_temp_download_file(path: Path) -> bool:
    name = path.name.lower()
    suffix = path.suffix.lower()
    if suffix in _TEMP_SUFFIXES:
        return True
    if name.endswith(".part") or ".part-" in name:
        return True
    if name.endswith(".ytdl") or name.endswith(".temp") or name.endswith(".tmp"):
        return True
    return False


async def _export_adspower_tiktok_cookies(account_id: str, cookie_file: Path) -> str | None:
    from core.browser_providers import (
        BROWSER_PROVIDER_ADSPOWER_MANUAL,
        account_metadata,
        make_browser_provider,
        resolve_browser_provider,
    )
    from database.database import AutomationDatabase, RetryConfig
    from playwright.async_api import async_playwright

    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL is required for TikTok cookie fallback")

    database = AutomationDatabase(db_url, retry_config=RetryConfig())
    await database.open()
    try:
        account = await database.get_account(account_id)
        if account is None:
            raise RuntimeError(f"Account {account_id} not found")
        if str(account.get("platform") or "").strip().lower() != "tiktok":
            raise RuntimeError(f"Account {account_id} must be a TikTok account")

        metadata = account_metadata(account)
        account_for_provider = {**account, "account_id": account_id, "metadata": metadata}
        browser_provider = resolve_browser_provider(account_for_provider)
        if browser_provider != BROWSER_PROVIDER_ADSPOWER_MANUAL:
            raise RuntimeError("TikTok cookie fallback requires AdsPower manual provider")

        session = await database.get_account_session(account_id) or {}
        provider = make_browser_provider(account_for_provider, session=session, identity_profile=None)

        async with async_playwright() as pw:
            async with provider.open_publisher_context(pw, headless=False) as (context, page, _opened_profile):
                await page.goto("https://www.tiktok.com/", wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(random.uniform(1.5, 3.0))
                try:
                    user_agent = str(await page.evaluate("() => navigator.userAgent") or "").strip()
                except Exception:
                    user_agent = None
                cookies = await context.cookies()

        tiktok_cookies = _require_tiktok_cookies(cookies, account_id)
        _write_netscape_cookie_file(tiktok_cookies, cookie_file)
        LOGGER.info(
            "download_cookie_file_exported",
            extra={
                "event": "download_cookie_file_exported",
                "account_id": account_id,
                "cookie_count": len(tiktok_cookies),
                "user_agent_exported": bool(user_agent),
            },
        )
        return user_agent
    finally:
        await database.close()


def _filter_tiktok_cookies(cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        cookie
        for cookie in cookies
        if "tiktok.com" in str(cookie.get("domain", "")).lower()
    ]


def _require_tiktok_cookies(cookies: list[dict[str, Any]], account_id: str) -> list[dict[str, Any]]:
    tiktok_cookies = _filter_tiktok_cookies(cookies)
    if tiktok_cookies:
        return tiktok_cookies
    LOGGER.warning(
        "download_cookie_export_no_tiktok_cookies",
        extra={
            "event": "download_cookie_export_no_tiktok_cookies",
            "account_id": account_id,
        },
    )
    raise RuntimeError("No TikTok cookies exported from AdsPower profile")


def _write_netscape_cookie_file(cookies: list[dict[str, Any]], path: Path) -> None:
    lines = ["# Netscape HTTP Cookie File\n"]
    for cookie in cookies:
        domain = _sanitize_cookie_field(cookie.get("domain", ""))
        if not domain or "tiktok.com" not in domain:
            continue
        name = _sanitize_cookie_field(cookie.get("name", ""))
        if not name:
            continue
        value = _sanitize_cookie_field(cookie.get("value", ""))
        include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
        cookie_path = _sanitize_cookie_field(cookie.get("path", "/")) or "/"
        secure = "TRUE" if bool(cookie.get("secure")) else "FALSE"
        expires = _normalize_cookie_expires(cookie.get("expires"))
        lines.append(
            "\t".join(
                [
                    domain,
                    include_subdomains,
                    cookie_path,
                    secure,
                    str(expires),
                    name,
                    value,
                ]
            )
            + "\n"
        )
    path.write_text("".join(lines), encoding="utf-8")


def _sanitize_cookie_field(value: Any) -> str:
    return str(value or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")


def _normalize_cookie_expires(value: Any) -> int:
    try:
        expires = int(float(value or 0))
    except (TypeError, ValueError):
        return 0
    return max(0, expires)
