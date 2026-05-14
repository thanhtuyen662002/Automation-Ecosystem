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


def _audit(headers: dict[str, str], license_id: str, device_id: str | None, reason: str) -> None:
    httpx.post(
        rest_url("license_audit_logs"),
        headers=headers,
        json={
            "license_id": license_id,
            "device_id": device_id,
            "event_type": "device_revoked",
            "severity": "warning",
            "detail": {"reason": reason},
        },
        timeout=20,
    )


def main() -> None:
    load_env_file()
    parser = argparse.ArgumentParser(description="Revoke license devices without deleting history.")
    parser.add_argument("--license-id")
    parser.add_argument("--license-key")
    parser.add_argument("--revoke-all-devices", action="store_true")
    parser.add_argument("--revoke-device")
    parser.add_argument("--reason", default="admin_reset_device")
    args = parser.parse_args()

    if args.revoke_all_devices == bool(args.revoke_device):
        raise SystemExit("Use exactly one of --revoke-all-devices or --revoke-device DEVICE_ID")

    headers = service_headers(get_service_key())
    license_id = _resolve_license_id(headers, args.license_id, args.license_key)
    now = datetime.now(UTC).isoformat()
    body = {"status": "revoked", "revoked_at": now, "revoke_reason": args.reason, "updated_at": now}
    params = {"license_id": f"eq.{license_id}", "status": "eq.active"} if args.revoke_all_devices else {"id": f"eq.{args.revoke_device}"}
    response = httpx.patch(
        rest_url("license_devices"),
        headers={**headers, "Prefer": "return=representation"},
        params=params,
        json=body,
        timeout=20,
    )
    if response.status_code >= 400:
        raise SystemExit(f"Failed to revoke devices: HTTP {response.status_code} {response.text}")
    rows = response.json()
    for row in rows:
        _audit(headers, license_id, row.get("id"), args.reason)
    print(f"Revoked devices: {len(rows)}")


if __name__ == "__main__":
    main()
