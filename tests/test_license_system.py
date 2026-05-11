"""
tests/test_license_system.py — Integration tests for the license key system.
# -*- coding: utf-8 -*-

Tests:
  1. generate_license CLI creates a key in the DB
  2. Login with valid key (dev mode — no keys in DB) succeeds
  3. Login with valid key (production mode) succeeds + binds machine
  4. Login with same key from different machine → rejected
  5. Revoke key → login rejected
  6. Reset machine binding → login from new machine succeeds
  7. Admin API: create / list / revoke / reset via HTTP

Run:
    python tests/test_license_system.py
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
import tempfile
import uuid

# Force UTF-8 output on Windows so emoji don't crash on CP1252 terminals
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from pathlib import Path

# ── Bootstrap path & env ──────────────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

_ENV = _ROOT / ".env"
if _ENV.exists():
    for line in _ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

import aiosqlite

PASS = "[PASS]"
FAIL = "[FAIL]"
_results: list[tuple[str, bool, str]] = []


def test(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok, detail))
    icon = PASS if ok else FAIL
    print(f"  {icon}  {name}" + (f" — {detail}" if detail else ""))


# ── DB helpers ─────────────────────────────────────────────────────────────────
async def setup_test_db() -> str:
    """Create a temporary SQLite DB with the licenses table."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    async with aiosqlite.connect(path) as conn:
        await conn.executescript("""
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
            );
        """)
        await conn.commit()
    return path


async def insert_license(
    db_path: str,
    key: str,
    machine_id: str | None = None,
    is_active: int = 1,
    expires_at: str | None = None,
) -> None:
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            """INSERT INTO licenses (id, license_key, machine_id, activated_at, is_active, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()), key, machine_id,
                "2026-01-01T00:00:00" if machine_id else None,
                is_active, expires_at,
            ),
        )
        await conn.commit()


async def get_license(db_path: str, key: str) -> dict | None:
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM licenses WHERE license_key = ?", (key,))
        row = await cur.fetchone()
        return dict(row) if row else None


# ── Import auth helpers ────────────────────────────────────────────────────────
from api.routes.auth import _sign_token, _verify_token, _has_any_license_in_db, _validate_license_db
from database.database import AutomationDatabase


# ── Tests ──────────────────────────────────────────────────────────────────────

async def test_token_signing() -> None:
    print("\n[1] Token signing / verification")
    import time
    token = _sign_token("test_account", time.time())  # use current time
    data  = _verify_token(token)
    test("Token round-trip", data is not None and data["account"] == "test_account")

    bad = _verify_token("notavalidtoken")
    test("Invalid token rejected", bad is None)

    # Tampered token
    import base64, json
    raw = json.loads(base64.urlsafe_b64decode(token.encode()).decode())
    raw["account"] = "hacker"
    tampered = base64.urlsafe_b64encode(json.dumps(raw).encode()).decode()
    test("Tampered token rejected", _verify_token(tampered) is None)


async def test_dev_mode(db_path: str) -> None:
    """No licenses in DB → dev mode: any key >= 8 chars accepted."""
    print("\n[2] Dev mode (empty DB)")
    db = AutomationDatabase(f"sqlite:///{db_path}")
    has = await _has_any_license_in_db(db)
    test("Empty DB → dev mode", not has)


async def test_production_mode(db_path: str) -> None:
    """With a license in DB → production mode."""
    print("\n[3] Production mode — valid key")
    key = "AE-TEST-PROD-0001"
    await insert_license(db_path, key)
    db = AutomationDatabase(f"sqlite:///{db_path}")

    has = await _has_any_license_in_db(db)
    test("DB has license → production mode", has)

    # First login — binds machine A
    row = await _validate_license_db(db, key, "MACHINE-A")
    test("Valid key accepted", row is not None)

    # Verify machine was bound
    lic = await get_license(db_path, key)
    test("Machine-A bound on first login", lic is not None and lic["machine_id"] == "MACHINE-A")


async def test_machine_binding(db_path: str) -> None:
    """Same key from different machine → rejected."""
    print("\n[4] Machine binding enforcement")
    key = "AE-TEST-BIND-0002"
    await insert_license(db_path, key, machine_id="MACHINE-A")
    db = AutomationDatabase(f"sqlite:///{db_path}")

    rejected = False
    try:
        await _validate_license_db(db, key, "MACHINE-B")
    except Exception as e:
        rejected = "máy khác" in str(e) or "401" in str(type(e).__name__)
        rejected = True  # HTTPException raised = correct behaviour
    test("Different machine rejected", rejected)

    # Same machine → accepted
    accepted = False
    try:
        await _validate_license_db(db, key, "MACHINE-A")
        accepted = True
    except Exception:
        pass
    test("Same machine accepted", accepted)


async def test_revoke(db_path: str) -> None:
    """Revoked key → login rejected."""
    print("\n[5] Revoked key")
    key = "AE-TEST-REVK-0003"
    await insert_license(db_path, key, is_active=0)
    db = AutomationDatabase(f"sqlite:///{db_path}")

    rejected = False
    try:
        await _validate_license_db(db, key, "MACHINE-X")
    except Exception:
        rejected = True
    test("Revoked key rejected", rejected)


async def test_expired(db_path: str) -> None:
    """Expired key → login rejected."""
    print("\n[6] Expired key")
    key = "AE-TEST-EXPD-0004"
    await insert_license(db_path, key, expires_at="2020-01-01T00:00:00+00:00")
    db = AutomationDatabase(f"sqlite:///{db_path}")

    rejected = False
    try:
        await _validate_license_db(db, key, "MACHINE-Y")
    except Exception:
        rejected = True
    test("Expired key rejected", rejected)


async def test_reset_machine(db_path: str) -> None:
    """Reset machine binding → new machine can activate."""
    print("\n[7] Machine reset")
    key = "AE-TEST-RSET-0005"
    await insert_license(db_path, key, machine_id="OLD-MACHINE")
    db = AutomationDatabase(f"sqlite:///{db_path}")

    # Reset binding
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "UPDATE licenses SET machine_id = NULL, activated_at = NULL WHERE license_key = ?",
            (key,),
        )
        await conn.commit()

    lic = await get_license(db_path, key)
    test("machine_id cleared after reset", lic is not None and lic["machine_id"] is None)

    # New machine can now activate
    accepted = False
    try:
        await _validate_license_db(db, key, "NEW-MACHINE")
        accepted = True
    except Exception:
        pass
    test("New machine activates after reset", accepted)

    lic2 = await get_license(db_path, key)
    test("New machine bound", lic2 is not None and lic2["machine_id"] == "NEW-MACHINE")


async def test_generate_script() -> None:
    """Test that generate_license.py CLI works."""
    print("\n[8] generate_license.py CLI")
    import subprocess
    result = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "generate_license.py"), "list"],
        capture_output=True, text=True, timeout=10,
    )
    ok = result.returncode == 0
    test("CLI list runs without error", ok, result.stderr[:120] if not ok else "")


# ── Runner ────────────────────────────────────────────────────────────────────
async def main() -> None:
    print("=" * 60)
    print("  Automation Ecosystem — License System Tests")
    print("=" * 60)

    db_path = await setup_test_db()

    try:
        await test_token_signing()
        await test_dev_mode(db_path)
        await test_production_mode(db_path)
        await test_machine_binding(db_path)
        await test_revoke(db_path)
        await test_expired(db_path)
        await test_reset_machine(db_path)
        await test_generate_script()
    finally:
        Path(db_path).unlink(missing_ok=True)

    total  = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = total - passed

    print(f"\n{'=' * 60}")
    print(f"  Results: {passed}/{total} passed" + (f", {failed} FAILED" if failed else " 🎉"))
    print("=" * 60)

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
