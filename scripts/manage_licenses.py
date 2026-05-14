from __future__ import annotations


DEPRECATED_MESSAGE = """
scripts/manage_licenses.py is deprecated and disabled.

It belonged to the old username/session license flow and wrote raw license keys
plus legacy license_sessions data. Use the new admin scripts instead:

  python scripts/create_license.py --label "Customer A" --plan standard
  python scripts/list_licenses.py
  python scripts/list_license_devices.py --license-id <uuid>
  python scripts/reset_license_devices.py --license-id <uuid> --revoke-all-devices --reason "customer changed machine"
  python scripts/renew_license.py --license-id <uuid> --expires-at lifetime
  python scripts/revoke_license.py --license-id <uuid> --reason "policy violation"
"""


def main() -> None:
    raise SystemExit(DEPRECATED_MESSAGE.strip())


if __name__ == "__main__":
    main()
