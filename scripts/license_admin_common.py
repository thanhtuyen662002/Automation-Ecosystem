from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.license_key import get_license_key_prefix, normalize_license_key


ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def load_env_file(path: Path | None = None) -> None:
    env_path = path or (ROOT / ".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def service_headers(key: str) -> dict[str, str]:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def rest_url(path: str) -> str:
    return f"{require_env('SUPABASE_URL').rstrip('/')}/rest/v1/{path.lstrip('/')}"


def rpc_url(name: str) -> str:
    return rest_url(f"rpc/{name}")


def get_service_key() -> str:
    return require_env("SUPABASE_SERVICE_ROLE_KEY")


def generate_license_key() -> str:
    groups = ["".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(4)]
    return "AECO-" + "-".join(groups)


def hash_raw_license_key(raw_key: str) -> tuple[str, str, str]:
    normalized = normalize_license_key(raw_key)
    pepper = require_env("LICENSE_KEY_PEPPER")
    if not normalized:
        raise SystemExit("License key cannot be empty")
    digest = hashlib.sha256(f"{normalized}{pepper}".encode("utf-8")).hexdigest()
    return normalized, digest, get_license_key_prefix(normalized)


def safe_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def parse_expires_at(value: str | None) -> str | None:
    if not value or value.lower() in {"lifetime", "never", "none", "null"}:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expires_at must be ISO-8601 or 'lifetime'") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def is_unique_violation(response: httpx.Response) -> bool:
    if response.status_code != 409:
        return False
    try:
        body = response.json()
    except ValueError:
        return "duplicate" in response.text.lower()
    text = json.dumps(body).lower()
    return "duplicate" in text or "unique" in text or "23505" in text


def hash_machine_for_security_test(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()
