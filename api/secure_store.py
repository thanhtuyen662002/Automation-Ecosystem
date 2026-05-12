"""
Local secure storage helpers for desktop-only secrets.

The packaged app must not keep refresh tokens or signing secrets in .env files,
SQLite, localStorage, or sessionStorage. On Windows this module stores encrypted
payloads with DPAPI, bound to the current Windows user profile. Non-Windows
fallbacks are file-permission based and intended for developer use only.
"""
from __future__ import annotations

import base64
import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path


APP_NAME = "Automation-Ecosystem"


class SecureStoreError(RuntimeError):
    pass


def _appdata_dir() -> Path:
    override = os.getenv("AE_SECURE_STORE_DIR", "").strip()
    if override:
        return Path(override)
    if sys.platform == "win32":
        appdata = os.getenv("APPDATA")
        if appdata:
            return Path(appdata) / APP_NAME / "secrets"
    return Path.home() / ".automation-ecosystem" / "secrets"


def _path_for(name: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in name)
    return _appdata_dir() / f"{safe}.bin"


def save_secret(name: str, value: str) -> None:
    data = value.encode("utf-8")
    payload = _protect(data) if sys.platform == "win32" else base64.b64encode(data)
    path = _path_for(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_secret(name: str) -> str | None:
    path = _path_for(name)
    if not path.exists():
        return None
    payload = path.read_bytes()
    try:
        data = _unprotect(payload) if sys.platform == "win32" else base64.b64decode(payload)
    except Exception as exc:
        raise SecureStoreError(f"Failed to decrypt secure secret '{name}'") from exc
    return data.decode("utf-8")


def delete_secret(name: str) -> None:
    _path_for(name).unlink(missing_ok=True)


def get_or_create_secret(name: str, *, nbytes: int = 32) -> str:
    existing = load_secret(name)
    if existing:
        return existing
    import secrets

    value = secrets.token_urlsafe(nbytes)
    save_secret(name, value)
    return value


if sys.platform == "win32":

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32

    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL

    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p


def _make_blob(data: bytes) -> tuple[DATA_BLOB, ctypes.Array[ctypes.c_char]]:
    buf = ctypes.create_string_buffer(data)
    blob = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    return blob, buf


def _protect(data: bytes) -> bytes:
    in_blob, _buf = _make_blob(data)
    out_blob = DATA_BLOB()
    ok = _crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "Automation Ecosystem secret",
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise SecureStoreError("CryptProtectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        _kernel32.LocalFree(out_blob.pbData)


def _unprotect(data: bytes) -> bytes:
    in_blob, _buf = _make_blob(data)
    out_blob = DATA_BLOB()
    ok = _crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise SecureStoreError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        _kernel32.LocalFree(out_blob.pbData)
