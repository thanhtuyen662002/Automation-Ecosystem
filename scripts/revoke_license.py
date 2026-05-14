from __future__ import annotations

import argparse
from datetime import UTC, datetime

import httpx

from license_admin_common import get_service_key, hash_raw_license_key, load_env_file, rest_url, service_headers


def _resolve_license_id(headers: dict[str, str], license_id: str | None, raw_key: str | None) -> str:
    if license_id:
        return license_id
    if not raw_key:
        raise SystemExit("Provide --license-id or --license-key")
    _normalized, key_hash, _prefix = hash_raw_license_key(raw_key)
    response = httpx.get(
        rest_url("licenses"),
        headers=headers,
        params={"select": "id", "license_key_hash": f"eq.{key_hash}", "limit": "1"},
        timeout=20,
    )
    if response.status_code >= 400:
        raise SystemExit(f"Failed to resolve license: HTTP {response.status_code} {response.text}")
    rows = response.json()
    if not rows:
        raise SystemExit("License not found")
    return rows[0]["id"]


def main() -> None:
    load_env_file()
    parser = argparse.ArgumentParser(description="Revoke a license.")
    parser.add_argument("--license-id")
    parser.add_argument("--license-key")
    parser.add_argument("--reason", default="admin_revoked")
    args = parser.parse_args()

    headers = service_headers(get_service_key())
    license_id = _resolve_license_id(headers, args.license_id, args.license_key)
    now = datetime.now(UTC).isoformat()
    response = httpx.patch(
        rest_url("licenses"),
        headers={**headers, "Prefer": "return=representation"},
        params={"id": f"eq.{license_id}"},
        json={"status": "revoked", "revoked_at": now, "revoke_reason": args.reason, "updated_at": now},
        timeout=20,
    )
    if response.status_code >= 400:
        raise SystemExit(f"Failed to revoke license: HTTP {response.status_code} {response.text}")
    httpx.post(
        rest_url("license_audit_logs"),
        headers=headers,
        json={
            "license_id": license_id,
            "event_type": "license_revoked",
            "severity": "warning",
            "detail": {"reason": args.reason},
        },
        timeout=20,
    )
    print(f"License revoked: {license_id}")


if __name__ == "__main__":
    main()
