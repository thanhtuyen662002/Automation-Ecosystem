"""
BE + DB health audit script.
Run: python scripts/audit_be_db.py
"""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
import base64
import importlib
from pathlib import Path
from datetime import UTC, datetime

sys.stdout.reconfigure(encoding="utf-8")

# Add project root to path so 'api' package is importable
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = os.environ.get(
    "DATABASE_URL",
    "sqlite+aiosqlite:///C:/Users/Admin/AppData/Roaming/Automation-Ecosystem/data/app.db",
).replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")

try:
    import aiosqlite
except ImportError:
    print("[FAIL] aiosqlite not installed")
    sys.exit(1)

# ── Replicate token logic locally ─────────────────────────────────────────────
SECRET = os.getenv("LICENSE_SECRET", "automationecosystem-dev-secret-2025").encode()
TTL    = int(os.getenv("SESSION_TTL_MINUTES", "60")) * 60

def issue_token(license_key: str, machine_fp: str, session_id: str,
                role: str = "operator", account: str = "") -> tuple[str, int]:
    now = int(time.time())
    exp = now + TTL
    payload = {"lid": license_key, "fp": machine_fp, "sid": session_id,
               "role": role, "acc": account, "iat": now, "exp": exp}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    sig = hmac.new(SECRET, raw.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode((raw + "." + sig).encode()).decode(), exp

def token_sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def verify_token(token: str) -> dict | None:
    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        raw, sig = decoded.rsplit(".", 1)
        expected = hmac.new(SECRET, raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(raw)
        if int(time.time()) > payload["exp"]:
            return None
        return payload
    except Exception:
        return None


PASS = "[PASS]"
FAIL = "[FAIL]"
WARN = "[WARN]"


async def run_audit():
    issues: list[str] = []
    warnings: list[str] = []

    print(f"\n{'='*60}")
    print(f"  BE + DB Audit — {datetime.now().isoformat()}")
    print(f"  DB: {DB_PATH}")
    print(f"{'='*60}\n")

    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row

        # ── 1. Table schemas ───────────────────────────────────────────────────
        print("── 1. Table Schemas ─────────────────────────────────────────────")
        expected = {
            "licenses":       {"license_key", "is_active", "machine_id", "role",
                                "max_accounts", "expires_at", "flagged", "flagged_reason"},
            "sessions":       {"id", "license_key", "machine_fp", "ip", "account",
                                "token_hash", "revoked", "revoke_reason", "expires_at"},
            "login_attempts": {"id", "ip", "license_key", "attempted_at"},
            "license_events": {"id", "license_key", "event_type", "ip", "machine_fp"},
        }
        for tbl, req_cols in expected.items():
            cur = await conn.execute(f"PRAGMA table_info({tbl})")
            cols = {r[1] for r in await cur.fetchall()}
            missing = req_cols - cols
            if missing:
                print(f"  {FAIL} {tbl}: missing columns {missing}")
                issues.append(f"{tbl} missing: {missing}")
            else:
                print(f"  {PASS} {tbl}: all required columns ({len(cols)} total)")

        # ── 2. License data ───────────────────────────────────────────────────
        print("\n── 2. License Data ──────────────────────────────────────────────")
        cur = await conn.execute(
            "SELECT license_key, is_active, machine_id, role, max_accounts, "
            "expires_at, flagged, flagged_reason FROM licenses"
        )
        licenses = await cur.fetchall()
        if not licenses:
            print(f"  {WARN} No licenses in DB")
            warnings.append("No licenses configured")
        for lic in licenses:
            problems = []
            if not lic["is_active"]:
                problems.append("INACTIVE")
            if lic["flagged"]:
                problems.append(f"FLAGGED({lic['flagged_reason']})")
            if lic["expires_at"]:
                try:
                    exp = datetime.fromisoformat(lic["expires_at"])
                    if datetime.now(UTC) > exp:
                        problems.append("EXPIRED")
                except Exception:
                    problems.append("BAD_EXPIRY")
            mid = (str(lic["machine_id"] or "")[:16] + "…") if lic["machine_id"] else "unbound"
            tag = PASS if not problems else FAIL
            label = ",".join(problems) if problems else "active"
            print(f"  {tag} key={lic['license_key']} status={label} "
                  f"role={lic['role']} max_acc={lic['max_accounts']} machine={mid}")
            if problems:
                issues.append(f"License {lic['license_key']}: {label}")

        # ── 3. Session state ──────────────────────────────────────────────────
        print("\n── 3. Session State ─────────────────────────────────────────────")
        cur = await conn.execute(
            "SELECT id, license_key, revoked, expires_at, account, token_hash "
            "FROM sessions ORDER BY issued_at DESC LIMIT 5"
        )
        sessions = await cur.fetchall()
        active_count = 0
        now_utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M")
        for s in sessions:
            if not s["revoked"]:
                active_count += 1
                # Check if really not expired (rough string compare)
                exp_str = str(s["expires_at"])
                still_valid = exp_str > now_utc
                extra = "" if still_valid else " ⚠️ EXPIRED"
            th_prefix = str(s["token_hash"] or "")[:20]
            mark = "✓" if not s["revoked"] else "✗"
            status = "ACTIVE" if not s["revoked"] else "REVOKED"
            print(f"  {mark} {str(s['id'])[:12]}… key={s['license_key']} "
                  f"{status} expires={s['expires_at']} acc={s['account']} "
                  f"hash={th_prefix}…")
        if active_count == 0:
            print(f"  {WARN} No active sessions — user must log in fresh")
            warnings.append("No active sessions")

        # ── 4. Simulate lookup_session JOIN ───────────────────────────────────
        print("\n── 4. lookup_session JOIN ───────────────────────────────────────")
        cur = await conn.execute("""
            SELECT s.token_hash, s.expires_at,
                   l.is_active  AS license_active,
                   l.role       AS license_role,
                   l.max_accounts AS license_max_accounts
            FROM sessions s
            JOIN licenses l ON l.license_key = s.license_key
            WHERE s.revoked = 0 AND s.expires_at > CURRENT_TIMESTAMP
            ORDER BY s.issued_at DESC LIMIT 1
        """)
        row = await cur.fetchone()
        if row:
            print(f"  {PASS} JOIN works: license_active={row['license_active']} "
                  f"role={row['license_role']} max_acc={row['license_max_accounts']}")
            print(f"         session expires: {row['expires_at']}")
        else:
            print(f"  {WARN} No active+non-expired session for JOIN (need fresh login)")
            warnings.append("No joinable active session")

        # ── 5. Token round-trip ───────────────────────────────────────────────
        print("\n── 5. Token Round-Trip ──────────────────────────────────────────")
        tok, exp = issue_token("TEST-KEY", "fp-1234", "sid-abcd",
                               role="operator", account="tester")
        payload = verify_token(tok)
        if payload and payload["lid"] == "TEST-KEY" and payload["role"] == "operator":
            print(f"  {PASS} issue+verify: role={payload['role']} acc={payload['acc']}")
        else:
            print(f"  {FAIL} Token round-trip FAILED")
            issues.append("Token issue/verify broken")

        th = token_sha256(tok)
        if len(th) == 64:
            print(f"  {PASS} token_sha256: {th[:24]}…")
        else:
            print(f"  {FAIL} token_sha256 wrong length: {len(th)}")
            issues.append("token_sha256 broken")

        # ── 6. login_attempts INSERT ──────────────────────────────────────────
        print("\n── 6. Rate Limiter Table ────────────────────────────────────────")
        cur = await conn.execute("SELECT COUNT(*) AS cnt FROM login_attempts")
        r = await cur.fetchone()
        print(f"  {PASS} existing rows: {r['cnt']}")

        try:
            await conn.execute("INSERT INTO login_attempts (ip) VALUES (?)", ("audit-test",))
            cur2 = await conn.execute(
                "SELECT id FROM login_attempts WHERE ip='audit-test' "
                "ORDER BY attempted_at DESC LIMIT 1"
            )
            r2 = await cur2.fetchone()
            if r2 and r2["id"]:
                print(f"  {PASS} INSERT + DEFAULT id: {str(r2['id'])[:20]}…")
                await conn.execute(
                    "DELETE FROM login_attempts WHERE id=?", (r2["id"],)
                )
            else:
                print(f"  {WARN} INSERT succeeded but id is NULL")
                warnings.append("login_attempts DEFAULT id may not work")
        except Exception as e:
            print(f"  {WARN} login_attempts INSERT: {e}")
            warnings.append(f"login_attempts: {e}")

    # ── 7. Backend module imports ──────────────────────────────────────────────
    print("\n── 7. Backend Imports ───────────────────────────────────────────────")
    import_targets = [
        "api.security",
        "api.middleware.license_guard",
        "api.middleware.rate_limiter",
        "api.routes.auth",
        "api.routes.jobs",
        "api.dependencies",
        "database.database",
    ]
    for mod in import_targets:
        try:
            importlib.import_module(mod)
            print(f"  {PASS} {mod}")
        except Exception as e:
            short = str(e)[:80]
            print(f"  {FAIL} {mod}: {short}")
            issues.append(f"import {mod}: {short}")

    # ── 8. auth.py: single-token issuance check ────────────────────────────────
    print("\n── 8. auth.py Token Issuance Pattern ────────────────────────────────")
    auth_path = PROJECT_ROOT / "api" / "routes" / "auth.py"
    with open(auth_path, encoding="utf-8") as f:
        auth_src = f.read()
    if "pending" in auth_src and 'issue_token(key, machine_fp, "pending")' in auth_src:
        print(f"  {FAIL} Old two-step 'pending' token pattern still present!")
        issues.append("auth.py still has pending-token pattern")
    else:
        print(f"  {PASS} Single-step token issuance (no pending pattern)")
    if "session_id=session_id" in auth_src:
        print(f"  {PASS} create_session called with session_id kwarg")
    else:
        print(f"  {WARN} create_session may not pass session_id — check auth.py")
        warnings.append("auth.py: session_id kwarg might be missing")

    # ── Summary ────────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}")
    if issues:
        print(f"\n  ❌ ISSUES ({len(issues)}):")
        for i in issues:
            print(f"     - {i}")
    else:
        print(f"\n  ✅ No blocking issues found")
    if warnings:
        print(f"\n  ⚠️  WARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"     - {w}")
    if not issues and not warnings:
        print("  🟢 BE + DB fully healthy")
    print()


if __name__ == "__main__":
    asyncio.run(run_audit())
