"""
scripts/generate_license.py — Admin CLI to generate + store license keys.

Usage:
    python scripts/generate_license.py [--label "Khách hàng A"] [--expires 365] [--notes "..."]
    python scripts/generate_license.py --list
    python scripts/generate_license.py --revoke AE-XXXX-XXXX-XXXX
    python scripts/generate_license.py --reset-machine AE-XXXX-XXXX-XXXX

Requires the DATABASE_URL env var (or reads from .env).
"""
from __future__ import annotations

import argparse
import asyncio
import io
import os
import secrets
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Force UTF-8 on Windows CP1252 terminals
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ── Load .env ─────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent
_ENV  = _ROOT / ".env"
if _ENV.exists():
    for line in _ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

import aiosqlite

# ── Config ────────────────────────────────────────────────────────────────────
_RAW_URL   = os.getenv("DATABASE_URL", "sqlite:///app.db")
_DB_PATH   = _RAW_URL.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")


def _generate_key() -> str:
    """Generate a license key in AE-XXXX-XXXX-XXXX format (deterministic via secrets)."""
    parts = [secrets.token_hex(2).upper() for _ in range(3)]
    return f"AE-{parts[0]}-{parts[1]}-{parts[2]}"


async def _ensure_table(conn: aiosqlite.Connection) -> None:
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            id           TEXT PRIMARY KEY,
            license_key  TEXT UNIQUE NOT NULL,
            label        TEXT,
            machine_id   TEXT,
            activated_at DATETIME,
            expires_at   DATETIME,
            is_active    INTEGER NOT NULL DEFAULT 1,
            notes        TEXT,
            created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await conn.commit()


async def cmd_create(label: str | None, expires_days: int | None, notes: str | None) -> None:
    key = _generate_key()
    lid = str(uuid.uuid4())
    expires_at = None
    if expires_days:
        expires_at = (datetime.now(UTC) + timedelta(days=expires_days)).isoformat()

    async with aiosqlite.connect(_DB_PATH) as conn:
        await _ensure_table(conn)
        await conn.execute(
            """INSERT INTO licenses (id, license_key, label, expires_at, notes)
               VALUES (?, ?, ?, ?, ?)""",
            (lid, key, label, expires_at, notes),
        )
        await conn.commit()

    print(f"\n✅ License key created successfully!")
    print(f"   Key    : {key}")
    print(f"   Label  : {label or '—'}")
    print(f"   Expires: {expires_at or 'Never'}")
    print(f"   ID     : {lid}")
    print(f"\n   → Gửi key này cho người dùng: {key}\n")


async def cmd_list() -> None:
    async with aiosqlite.connect(_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await _ensure_table(conn)
        cur = await conn.execute(
            "SELECT * FROM licenses ORDER BY created_at DESC"
        )
        rows = await cur.fetchall()

    if not rows:
        print("Chưa có license key nào.")
        return

    print(f"\n{'KEY':<22} {'LABEL':<20} {'STATUS':<10} {'MACHINE':<12} {'EXPIRES':<20} {'CREATED'}")
    print("─" * 110)
    for r in rows:
        status  = "✅ Active" if r["is_active"] else "❌ Revoked"
        machine = r["machine_id"][:10] + "…" if r["machine_id"] else "Not bound"
        expires = r["expires_at"] or "Never"
        label   = (r["label"] or "—")[:18]
        print(f"{r['license_key']:<22} {label:<20} {status:<10} {machine:<12} {expires:<20} {r['created_at'][:19]}")
    print()


async def cmd_revoke(key: str) -> None:
    async with aiosqlite.connect(_DB_PATH) as conn:
        await _ensure_table(conn)
        cur = await conn.execute(
            "UPDATE licenses SET is_active = 0 WHERE license_key = ?", (key,)
        )
        await conn.commit()
        if cur.rowcount == 0:
            print(f"❌ Key không tồn tại: {key}")
        else:
            print(f"✅ Đã thu hồi: {key}")


async def cmd_reset_machine(key: str) -> None:
    async with aiosqlite.connect(_DB_PATH) as conn:
        await _ensure_table(conn)
        cur = await conn.execute(
            "UPDATE licenses SET machine_id = NULL, activated_at = NULL WHERE license_key = ?",
            (key,),
        )
        await conn.commit()
        if cur.rowcount == 0:
            print(f"❌ Key không tồn tại: {key}")
        else:
            print(f"✅ Đã reset machine binding cho: {key}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Automation Ecosystem — License Manager CLI")
    sub = parser.add_subparsers(dest="cmd")

    # create (default)
    create_p = sub.add_parser("create", help="Tạo license key mới")
    create_p.add_argument("--label",   default=None, help="Tên khách hàng")
    create_p.add_argument("--expires", type=int, default=None, metavar="DAYS", help="Số ngày (mặc định: không hết hạn)")
    create_p.add_argument("--notes",   default=None, help="Ghi chú thêm")

    sub.add_parser("list",    help="Liệt kê tất cả license keys")
    revoke_p = sub.add_parser("revoke",  help="Thu hồi license key")
    revoke_p.add_argument("key", help="License key cần thu hồi")

    reset_p = sub.add_parser("reset-machine", help="Reset machine binding")
    reset_p.add_argument("key", help="License key cần reset")

    args = parser.parse_args()

    # Default to create if no subcommand (legacy: --label/--expires at top level)
    if args.cmd is None:
        # Legacy positional mode
        parser2 = argparse.ArgumentParser()
        parser2.add_argument("--label",   default=None)
        parser2.add_argument("--expires", type=int, default=None)
        parser2.add_argument("--notes",   default=None)
        parser2.add_argument("--list",    action="store_true")
        parser2.add_argument("--revoke",  default=None)
        parser2.add_argument("--reset-machine", default=None, dest="reset_machine")
        a2 = parser2.parse_args()
        if a2.list:
            asyncio.run(cmd_list())
        elif a2.revoke:
            asyncio.run(cmd_revoke(a2.revoke))
        elif a2.reset_machine:
            asyncio.run(cmd_reset_machine(a2.reset_machine))
        else:
            asyncio.run(cmd_create(a2.label, a2.expires, a2.notes))
        return

    if args.cmd == "create":
        asyncio.run(cmd_create(args.label, args.expires, args.notes))
    elif args.cmd == "list":
        asyncio.run(cmd_list())
    elif args.cmd == "revoke":
        asyncio.run(cmd_revoke(args.key))
    elif args.cmd == "reset-machine":
        asyncio.run(cmd_reset_machine(args.key))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
