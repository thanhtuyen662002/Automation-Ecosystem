from __future__ import annotations

import argparse

import httpx

from license_admin_common import get_service_key, hash_raw_license_key, load_env_file, parse_expires_at, rest_url, service_headers


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
    parser = argparse.ArgumentParser(description="Renew a license.")
    parser.add_argument("--license-id")
    parser.add_argument("--license-key")
    parser.add_argument("--expires-at", required=True, help="ISO-8601 timestamp or lifetime")
    parser.add_argument("--set-active", action="store_true", help="Set status=active while renewing")
    args = parser.parse_args()

    headers = service_headers(get_service_key())
    license_id = _resolve_license_id(headers, args.license_id, args.license_key)
    body = {"expires_at": parse_expires_at(args.expires_at)}
    if args.set_active:
        body["status"] = "active"
        body["revoked_at"] = None
        body["revoke_reason"] = None
    response = httpx.patch(
        rest_url("licenses"),
        headers={**headers, "Prefer": "return=representation"},
        params={"id": f"eq.{license_id}"},
        json=body,
        timeout=20,
    )
    if response.status_code >= 400:
        raise SystemExit(f"Failed to renew license: HTTP {response.status_code} {response.text}")
    httpx.post(
        rest_url("license_audit_logs"),
        headers=headers,
        json={
            "license_id": license_id,
            "event_type": "license_renewed",
            "severity": "info",
            "detail": {"expires_at": body["expires_at"], "set_active": args.set_active},
        },
        timeout=20,
    )
    print(f"License renewed: {license_id}")


if __name__ == "__main__":
    main()
