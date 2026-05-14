from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from core.machine_id import get_license_state_dir


def license_state_path() -> Path:
    return get_license_state_dir() / "license_state.json"


def read_license_state() -> dict[str, Any] | None:
    path = license_state_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def write_license_state(state: dict[str, Any]) -> None:
    path = license_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2, sort_keys=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(payload, encoding="utf-8")
    try:
        os.chmod(temp_path, 0o600)
    except OSError:
        pass
    temp_path.replace(path)


def clear_license_state() -> None:
    license_state_path().unlink(missing_ok=True)


def has_license_state() -> bool:
    return read_license_state() is not None
