"""
api/security.py — Core security primitives.

Responsibilities:
  - Server-side machine fingerprint (hash of IP + UA + AcceptLang)
  - Signed session token (HMAC-SHA256, 60-min expiry)
  - Token verification + DB session lookup
  - Client IP extraction (X-Forwarded-For aware)
  - Session creation + revocation helpers

SECURITY DESIGN:
  - machine_id is NEVER trusted from client; always re-computed from request headers
  - Tokens are short-lived (60 min); each token is tracked in DB
  - Revoking a license immediately invalidates all DB sessions (checked on every request)
  - No secrets are embedded in the frontend bundle
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Request

LOGGER = logging.getLogger("api.security")

# ── Secrets ───────────────────────────────────────────────────────────────────
_TOKEN_SECRET = os.getenv("LICENSE_SECRET", "automationecosystem-dev-secret-2025").encode()

# Token lifetime: 60 minutes. Adjust via SESSION_TTL_MINUTES env var.
_TOKEN_TTL_SECONDS = int(os.getenv("SESSION_TTL_MINUTES", "60")) * 60


# ── Client IP ─────────────────────────────────────────────────────────────────

def get_client_ip(request: Request) -> str:
    """
    Extract the real client IP.
    Trusts X-Forwarded-For only for the first hop (reverse-proxy scenario).
    For a direct desktop app, request.client.host is used.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        # Take only the first IP (leftmost = client, rightmost = proxy)
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


# ── Machine Fingerprint ───────────────────────────────────────────────────────

def compute_machine_fingerprint(request: Request) -> str:
    """
    Compute a server-side machine fingerprint.

    DESIGN: This is NOT hardware-bound. It binds to the network signature
    (IP + browser/client identity). This prevents sharing between different
    physical machines that have different IPs + User-Agents.

    For Electron: window.__MACHINE_ID__ should be passed as X-Machine-ID header
    (set by Electron main.js from wmic/cpuid). If present, it is included in
    the hash for stronger binding.

    The resulting hash is deterministic for the same client and unguessable
    from outside (includes secret salt).
    """
    ip        = get_client_ip(request)
    ua        = request.headers.get("User-Agent", "")
    lang      = request.headers.get("Accept-Language", "")
    # Electron injects this from OS hardware UUID; web fallback is empty
    hw_id     = request.headers.get("X-Machine-ID", "")

    raw = f"{ip}|{ua}|{lang}|{hw_id}"
    # HMAC-salt with token secret so clients cannot pre-compute fingerprints
    fp = hmac.new(_TOKEN_SECRET, raw.encode(), hashlib.sha256).hexdigest()[:32]
    LOGGER.debug("machine_fingerprint_computed", extra={"ip": ip, "fp": fp[:8] + "…"})
    return fp


# ── Token Issuance ────────────────────────────────────────────────────────────

def issue_token(
    license_key: str,
    machine_fp: str,
    session_id: str,
    role: str = "operator",
    account: str = "",
) -> tuple[str, int]:
    """
    Issue a signed session token.

    Payload: {lid, fp, sid, role, acc, iat, exp}
    Signature: HMAC-SHA256 over sorted JSON payload

    Returns: (token_string, expires_at_unix_ts)
    """
    now = int(time.time())
    exp = now + _TOKEN_TTL_SECONDS
    payload = {
        "lid": license_key,
        "fp":  machine_fp,
        "sid": session_id,
        "role": role,
        "acc":  account,
        "iat": now,
        "exp": exp,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    sig = hmac.new(_TOKEN_SECRET, raw.encode(), hashlib.sha256).hexdigest()
    token_bytes = (raw + "." + sig).encode()
    return base64.urlsafe_b64encode(token_bytes).decode(), exp


def verify_token(token: str) -> dict | None:
    """
    Verify a session token.

    Returns the payload dict on success, None on any failure.
    Does NOT check DB session state — that is done by the middleware.
    """
    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        raw, sig = decoded.rsplit(".", 1)
        expected = hmac.new(_TOKEN_SECRET, raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(raw)
        if int(time.time()) > payload["exp"]:
            return None
        return payload
    except Exception:
        return None


def token_sha256(token: str) -> str:
    """Return SHA-256 hex of the token string (used as DB lookup key)."""
    return hashlib.sha256(token.encode()).hexdigest()


# ── Session Management (DB side) ──────────────────────────────────────────────

async def create_session(
    db,
    license_key: str,
    machine_fp: str,
    ip: str,
    token: str,
    expires_at_unix: int,
    account: str = "",
    session_id: str | None = None,
) -> str:
    """
    Create a new session record in the DB.
    Revokes any existing active sessions for the same license_key first
    (enforces 1 active session per license).
    Returns the session_id.
    """
    session_id = session_id or str(uuid.uuid4())
    th = token_sha256(token)
    exp_dt = datetime.fromtimestamp(expires_at_unix, UTC).isoformat()

    async with db.connection() as conn:
        # Revoke old sessions for this license (single active session policy)
        await conn.execute(
            """UPDATE sessions
               SET revoked = 1, revoke_reason = 'new_login'
               WHERE license_key = ? AND revoked = 0""",
            (license_key,),
        )
        # Create new session (account column added in migration 009)
        await conn.execute(
            """INSERT INTO sessions (id, license_key, machine_fp, ip, account, token_hash, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, license_key, machine_fp, ip, account or None, th, exp_dt),
        )
        await conn.commit()
    return session_id


async def lookup_session(db, token: str) -> dict | None:
    """
    Look up a session by token hash.
    Returns session row (including license role + account) or None if not found/revoked/expired.
    """
    th = token_sha256(token)
    async with db.connection() as conn:
        cur = await conn.execute(
            """SELECT s.*,
                      l.is_active  AS license_active,
                      l.expires_at AS license_expires,
                      l.role       AS license_role,
                      l.max_accounts AS license_max_accounts
               FROM sessions s
               JOIN licenses l ON l.license_key = s.license_key
               WHERE s.token_hash = ?
                 AND s.revoked = 0
                 AND s.expires_at > CURRENT_TIMESTAMP""",
            (th,),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def revoke_sessions_for_license(db, license_key: str, reason: str = "license_revoked") -> None:
    """Revoke ALL active sessions for a license key (used when license is revoked/expired)."""
    async with db.connection() as conn:
        await conn.execute(
            """UPDATE sessions
               SET revoked = 1, revoke_reason = ?
               WHERE license_key = ? AND revoked = 0""",
            (reason, license_key),
        )
        await conn.commit()


# ── Audit Logging ─────────────────────────────────────────────────────────────

async def audit_event(
    db,
    event_type: str,
    license_key: str | None = None,
    ip: str | None = None,
    machine_fp: str | None = None,
    detail: dict | None = None,
) -> None:
    """Record a security-relevant event to the license_events table."""
    eid = str(uuid.uuid4())
    detail_json = json.dumps(detail or {})
    try:
        async with db.connection() as conn:
            await conn.execute(
                """INSERT INTO license_events (id, license_key, event_type, ip, machine_fp, detail)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (eid, license_key, event_type, ip, machine_fp, detail_json),
            )
            await conn.commit()
    except Exception as exc:
        LOGGER.warning("audit_write_failed", extra={"event": "audit_write_failed", "err": str(exc)})
