"""
scripts/manage_licenses.py
──────────────────────────────────────────────────────────────────────────────
CLI tool to manage license keys stored in Supabase.

Usage:
  python scripts/manage_licenses.py create --label "Admin" --role admin
  python scripts/manage_licenses.py create --label "User 1" --days 365
  python scripts/manage_licenses.py list
  python scripts/manage_licenses.py revoke <license_key>
  python scripts/manage_licenses.py reset-machine <license_key>
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
from datetime import UTC, datetime, timedelta

from dotenv import load_dotenv

load_dotenv()


def _get_client():
    try:
        from supabase import create_client  # type: ignore[import]
    except ImportError:
        print("ERROR: pip install supabase")
        sys.exit(1)

    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")
        sys.exit(1)

    return create_client(url, key)


def cmd_create(args: argparse.Namespace) -> None:
    sb = _get_client()
    key = "AE-" + secrets.token_hex(16).upper()

    row: dict = {
        "license_key":  key,
        "label":        args.label,
        "role":         args.role,
        "max_accounts": args.max_accounts,
        "is_active":    True,
    }
    if args.days:
        row["expires_at"] = (datetime.now(UTC) + timedelta(days=args.days)).isoformat()

    result = sb.table("licenses").insert(row).execute()
    if result.data:
        rec = result.data[0]
        print("\n[OK] License created")
        print(f"   Key:          {rec['license_key']}")
        print(f"   Label:        {rec['label']}")
        print(f"   Role:         {rec['role']}")
        print(f"   Max accounts: {rec['max_accounts']}")
        print(f"   Expires:      {rec.get('expires_at', 'never')}")
    else:
        print("ERROR: failed to create license", result)


def cmd_list(args: argparse.Namespace) -> None:
    sb = _get_client()
    result = sb.table("licenses").select("*").order("created_at", desc=True).execute()
    rows = result.data or []
    if not rows:
        print("No licenses found.")
        return

    print(f"\n{'Key':<42} {'Label':<20} {'Role':<10} {'Active':<7} {'Expires':<25} {'Machine'}")
    print("-" * 115)
    for r in rows:
        key     = r["license_key"]
        label   = (r.get("label") or "")[:19]
        role    = (r.get("role") or "operator")[:9]
        active  = "YES" if r.get("is_active") else "NO"
        expires = (r.get("expires_at") or "never")[:24]
        machine = "bound" if r.get("machine_id") else "unbound"
        print(f"{key:<42} {label:<20} {role:<10} {active:<7} {expires:<25} {machine}")


def cmd_revoke(args: argparse.Namespace) -> None:
    sb = _get_client()
    sb.table("licenses").update({"is_active": False}).eq("license_key", args.key).execute()
    print(f"[OK] License revoked: {args.key}")


def cmd_reset_machine(args: argparse.Namespace) -> None:
    sb = _get_client()
    sb.table("licenses").update({
        "machine_id":   None,
        "activated_at": None,
    }).eq("license_key", args.key).execute()
    print(f"[OK] Machine binding reset for: {args.key}")
    print("   User can now activate on a new machine.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Supabase license keys")
    sub = parser.add_subparsers(dest="command")

    # create
    p_create = sub.add_parser("create", help="Create a new license key")
    p_create.add_argument("--label", required=True, help="Human-readable label")
    p_create.add_argument("--role", default="operator", choices=["operator", "admin"])
    p_create.add_argument("--max-accounts", type=int, default=10)
    p_create.add_argument("--days", type=int, default=0, help="Expiry in days (0 = never)")

    # list
    sub.add_parser("list", help="List all license keys")

    # revoke
    p_revoke = sub.add_parser("revoke", help="Revoke a license key")
    p_revoke.add_argument("key", help="License key to revoke")

    # reset-machine
    p_reset = sub.add_parser("reset-machine", help="Clear machine binding (allow new device)")
    p_reset.add_argument("key", help="License key to reset")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    {"create": cmd_create, "list": cmd_list, "revoke": cmd_revoke, "reset-machine": cmd_reset_machine}[args.command](args)


if __name__ == "__main__":
    main()
