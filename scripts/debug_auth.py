"""
Debug script — check auth DB state and simulate lookup_session.
Run: python scripts/debug_auth.py
"""
import asyncio
import sys
import os

sys.stdout.reconfigure(encoding="utf-8")

DB = os.environ.get(
    "DATABASE_URL",
    "sqlite+aiosqlite:///C:/Users/Admin/AppData/Roaming/Automation-Ecosystem/data/app.db",
).replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")

import aiosqlite


async def main() -> None:
    print(f"DB: {DB}\n")
    async with aiosqlite.connect(DB) as conn:
        conn.row_factory = aiosqlite.Row

        # -- Sessions (latest 5) ---------------------------------------------------
        cur = await conn.execute(
            "SELECT id, license_key, revoked, expires_at, token_hash, account "
            "FROM sessions ORDER BY created_at DESC LIMIT 5"
        )
        rows = await cur.fetchall()
        print("=== SESSIONS (latest 5) ===")
        for r in rows:
            sid = str(r["id"])[:12] + "..."
            th  = str(r["token_hash"])[:16] + "..."
            print(
                f"  id={sid} | key={r['license_key']} | "
                f"revoked={r['revoked']} | expires={r['expires_at']} | "
                f"account={r['account']} | token_hash_prefix={th}"
            )

        # -- Licenses ---------------------------------------------------------------
        cur2 = await conn.execute(
            "SELECT license_key, is_active, machine_id, role, max_accounts, flagged "
            "FROM licenses"
        )
        rows2 = await cur2.fetchall()
        print("\n=== LICENSES ===")
        for r in rows2:
            mid = str(r["machine_id"] or "")[:12]
            print(
                f"  key={r['license_key']} | active={r['is_active']} | "
                f"machine_prefix={mid} | role={r['role']} | "
                f"max_accounts={r['max_accounts']} | flagged={r['flagged']}"
            )

        # -- Simulate lookup_session for the latest non-revoked session ------------
        print("\n=== LOOKUP_SESSION SIMULATION ===")
        cur3 = await conn.execute(
            "SELECT s.*, "
            "l.is_active AS license_active, "
            "l.expires_at AS license_expires, "
            "l.role AS license_role, "
            "l.max_accounts AS license_max_accounts "
            "FROM sessions s "
            "JOIN licenses l ON l.license_key = s.license_key "
            "WHERE s.revoked = 0 AND s.expires_at > CURRENT_TIMESTAMP "
            "ORDER BY s.created_at DESC LIMIT 1"
        )
        row3 = await cur3.fetchone()
        if row3:
            print("  [OK] Found active session via JOIN:")
            print(f"       license_active={row3['license_active']}")
            print(f"       license_role={row3['license_role']}")
            print(f"       license_max_accounts={row3['license_max_accounts']}")
            print(f"       session expires_at={row3['expires_at']}")
        else:
            print("  [FAIL] No active session found via JOIN!")
            # Diagnose why
            cur4 = await conn.execute(
                "SELECT COUNT(*) AS cnt FROM sessions WHERE revoked = 0"
            )
            r4 = await cur4.fetchone()
            print(f"       Non-revoked sessions: {r4['cnt']}")

            cur5 = await conn.execute(
                "SELECT COUNT(*) AS cnt FROM sessions s "
                "JOIN licenses l ON l.license_key = s.license_key "
                "WHERE s.revoked = 0"
            )
            r5 = await cur5.fetchone()
            print(f"       Non-revoked sessions WITH valid license JOIN: {r5['cnt']}")

            cur6 = await conn.execute(
                "SELECT COUNT(*) AS cnt FROM sessions WHERE expires_at > CURRENT_TIMESTAMP"
            )
            r6 = await cur6.fetchone()
            print(f"       Non-expired sessions: {r6['cnt']}")

        # -- login_attempts count --------------------------------------------------
        cur7 = await conn.execute("SELECT COUNT(*) AS cnt FROM login_attempts")
        r7 = await cur7.fetchone()
        print(f"\n=== login_attempts count: {r7['cnt']}")


if __name__ == "__main__":
    asyncio.run(main())
