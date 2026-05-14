from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

import httpx

from license_admin_common import (
    generate_license_key,
    get_service_key,
    hash_raw_license_key,
    is_unique_violation,
    load_env_file,
    parse_expires_at,
    rest_url,
    service_headers,
)


def main() -> None:
    load_env_file()
    parser = argparse.ArgumentParser(description="Create a Supabase license without storing the raw key.")
    parser.add_argument("--label", required=True)
    parser.add_argument("--plan", default="standard")
    parser.add_argument("--expires-at", default=None, help="ISO-8601 timestamp or lifetime")
    parser.add_argument("--max-devices", type=int, default=1)
    args = parser.parse_args()

    if args.max_devices < 1:
        raise SystemExit("--max-devices must be >= 1")

    expires_at = parse_expires_at(args.expires_at)
    key = get_service_key()
    headers = service_headers(key)
    created = None
    raw_key = ""
    for attempt in range(1, 6):
        raw_key = generate_license_key()
        normalized, key_hash, prefix = hash_raw_license_key(raw_key)
        payload = {
            "license_key_hash": key_hash,
            "license_key_prefix": prefix,
            "label": args.label,
            "plan": args.plan,
            "status": "active",
            "max_devices": args.max_devices,
            "expires_at": expires_at,
            "metadata": {"created_by": "scripts/create_license.py"},
        }
        response = httpx.post(
            rest_url("licenses"),
            headers={**headers, "Prefer": "return=representation"},
            json=payload,
            timeout=20,
        )
        if 200 <= response.status_code < 300:
            data = response.json()
            created = data[0] if isinstance(data, list) and data else payload
            break
        if is_unique_violation(response) and attempt < 5:
            continue
        print(response.text, file=sys.stderr)
        raise SystemExit(f"Failed to create license: HTTP {response.status_code}")

    if created is None:
        raise SystemExit("Failed to create license after 5 attempts")

    print("License created:")
    print(f"Label: {args.label}")
    print(f"Plan: {args.plan}")
    print(f"Expires at: {expires_at or 'lifetime'}")
    print(f"Max devices: {args.max_devices}")
    print(f"License id: {created.get('id')}")
    print(f"License key: {raw_key}")


if __name__ == "__main__":
    main()
