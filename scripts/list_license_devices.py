from __future__ import annotations

import argparse

import httpx

from license_admin_common import get_service_key, load_env_file, rest_url, service_headers


def _resolve_license_id(headers: dict[str, str], value: str) -> str:
    if len(value) == 36 and value.count("-") == 4:
        return value
    response = httpx.get(
        rest_url("licenses"),
        headers=headers,
        params={"select": "id", "license_key_prefix": f"eq.{value}", "limit": "1"},
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
    parser = argparse.ArgumentParser(description="List devices for a license.")
    parser.add_argument("license", help="License id or license prefix")
    args = parser.parse_args()

    headers = service_headers(get_service_key())
    license_id = _resolve_license_id(headers, args.license)
    response = httpx.get(
        rest_url("license_devices"),
        headers=headers,
        params={
            "select": "id,device_name,platform,app_version,status,activated_at,last_seen_at,machine_id_hash",
            "license_id": f"eq.{license_id}",
            "order": "created_at.desc",
        },
        timeout=20,
    )
    if response.status_code >= 400:
        raise SystemExit(f"Failed to list devices: HTTP {response.status_code} {response.text}")
    rows = response.json()
    print(f"License id: {license_id}")
    print(f"{'id':36} {'device':22} {'platform':18} {'app':10} {'status':10} {'machine':12} {'activated_at':24} {'last_seen_at'}")
    print("-" * 160)
    for row in rows:
        machine_prefix = str(row.get("machine_id_hash") or "")[:12]
        print(
            f"{row.get('id',''):<36} "
            f"{(row.get('device_name') or '')[:22]:<22} "
            f"{(row.get('platform') or '')[:18]:<18} "
            f"{(row.get('app_version') or '')[:10]:<10} "
            f"{row.get('status',''):<10} "
            f"{machine_prefix:<12} "
            f"{(row.get('activated_at') or '')[:24]:<24} "
            f"{row.get('last_seen_at') or ''}"
        )


if __name__ == "__main__":
    main()
