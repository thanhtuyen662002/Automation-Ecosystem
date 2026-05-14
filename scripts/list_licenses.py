from __future__ import annotations

import argparse

import httpx

from license_admin_common import get_service_key, load_env_file, rest_url, service_headers


def main() -> None:
    load_env_file()
    parser = argparse.ArgumentParser(description="List Supabase licenses without raw keys.")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    headers = service_headers(get_service_key())
    response = httpx.get(
        rest_url("licenses"),
        headers=headers,
        params={
            "select": "id,license_key_prefix,label,plan,status,expires_at,max_devices,last_seen_at,license_devices(status)",
            "order": "created_at.desc",
            "limit": str(args.limit),
        },
        timeout=20,
    )
    if response.status_code >= 400:
        raise SystemExit(f"Failed to list licenses: HTTP {response.status_code} {response.text}")
    rows = response.json()
    print(f"{'id':36} {'prefix':12} {'label':24} {'plan':10} {'status':10} {'exp':22} {'max':>3} {'active':>6} {'last_seen'}")
    print("-" * 140)
    for row in rows:
        devices = row.get("license_devices") or []
        active_count = sum(1 for device in devices if device.get("status") == "active")
        print(
            f"{row.get('id',''):<36} "
            f"{row.get('license_key_prefix',''):<12} "
            f"{(row.get('label') or '')[:24]:<24} "
            f"{row.get('plan',''):<10} "
            f"{row.get('status',''):<10} "
            f"{(row.get('expires_at') or 'lifetime')[:22]:<22} "
            f"{row.get('max_devices',''):>3} "
            f"{active_count:>6} "
            f"{row.get('last_seen_at') or ''}"
        )


if __name__ == "__main__":
    main()
