from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
import uuid
from pathlib import Path


def _app_data_dir() -> Path:
    override = os.getenv("AUTOMATION_ECOSYSTEM_STATE_DIR", "").strip()
    if override:
        return Path(override)
    if sys.platform == "win32":
        appdata = os.getenv("APPDATA")
        if appdata:
            return Path(appdata) / "AutomationEcosystem"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AutomationEcosystem"
    return Path.home() / ".config" / "automation-ecosystem"


def _read_windows_machine_guid() -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            value, _type = winreg.QueryValueEx(key, "MachineGuid")
            return str(value).strip() or None
    except OSError:
        return None


def _read_macos_platform_uuid() -> str | None:
    if sys.platform != "darwin":
        return None
    try:
        output = subprocess.check_output(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    marker = '"IOPlatformUUID" = "'
    for line in output.splitlines():
        if marker in line:
            return line.split(marker, 1)[1].split('"', 1)[0].strip() or None
    return None


def _read_linux_machine_id() -> str | None:
    if not sys.platform.startswith("linux"):
        return None
    for path in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return None


def _fallback_machine_id() -> str:
    path = _app_data_dir() / "machine_id"
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    path.parent.mkdir(parents=True, exist_ok=True)
    value = f"fallback:{uuid.uuid4()}"
    path.write_text(value, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return value


def get_machine_id() -> str:
    machine_id = (
        _read_windows_machine_guid()
        or _read_macos_platform_uuid()
        or _read_linux_machine_id()
        or _fallback_machine_id()
    )
    return f"{platform.system().lower()}:{machine_id}"


def get_local_machine_fingerprint_hash() -> str:
    payload = get_machine_id().encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def get_platform_label() -> str:
    return f"{platform.system()} {platform.release()}".strip()


def get_license_state_dir() -> Path:
    return _app_data_dir()
