"""
api/routes/auth.py — Hardened authentication with machine binding + sessions.

POST /api/v1/auth/login
  - Rate-limited: 10 attempts / 60s / IP
  - machine_id computed SERVER-SIDE (not trusted from client)
  - Validates license against DB (active + not expired)
  - Issues short-lived token (60 min default)
  - Enforces 1 active session per license (new login revokes old)
  - IP anomaly detection: flags license after 5 distinct IPs in 1 hour

POST /api/v1/auth/refresh
  - Exchanges a still-valid token for a fresh one
  - Rotates session (old token immediately revoked)

GET  /api/v1/auth/me
  - Returns current user info from token payload

POST /api/v1/auth/logout
  - Revokes current session
"""
from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.middleware.rate_limiter import check_login_rate_limit
from api.local_auth_cache import (
    DEFAULT_REFRESH_TOKEN_KEY,
    cache_allows_offline_use,
    clear_local_auth_cache,
    get_local_auth_cache,
    offline_grace_iso,
    upsert_local_auth_cache,
)
from api.security import (
    audit_event,
    compute_machine_fingerprint,
    create_session,
    get_client_ip,
    issue_token,
    lookup_session,
    revoke_sessions_for_license,
    verify_token,
)
from api.secure_store import delete_secret, load_secret, save_secret

LOGGER = logging.getLogger("api.auth")
router = APIRouter(prefix="/auth", tags=["Auth"])

# ── Schemas ────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    account: str
    license_key: str
    # machine_id from client is IGNORED — computed server-side
    machine_id: str = ""


class LoginResponse(BaseModel):
    token: str
    expires_in: int
    user: dict


class RefreshResponse(BaseModel):
    token: str
    expires_in: int


class BootstrapResponse(LoginResponse):
    pass


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _validate_license_and_bind(
    db,
    key: str,
    machine_fp: str,
    ip: str,
) -> dict:
    """
    Full license validation:
      1. Key exists + active + not expired
      2. Machine fingerprint matches (or first-time bind)
      3. IP tracking + anomaly detection
    Raises HTTPException on any failure.
    """
    async with db.connection() as conn:
        cur = await conn.execute(
            "SELECT * FROM licenses WHERE license_key = ?", (key,)
        )
        row = await cur.fetchone()

    if row is None:
        raise HTTPException(401, "License key không hợp lệ.")

    if not row["is_active"]:
        raise HTTPException(403, "License key đã bị thu hồi.")

    if row["expires_at"]:
        expires = datetime.fromisoformat(row["expires_at"])
        if datetime.now(UTC) > expires:
            raise HTTPException(403, "License key đã hết hạn.")

    if row["flagged"]:
        raise HTTPException(403, f"License bị khóa: {row['flagged_reason'] or 'hoạt động bất thường'}.")

    bound_fp = row["machine_id"]
    if bound_fp is None:
        # First activation — bind server-computed fingerprint
        async with db.connection() as conn:
            await conn.execute(
                """UPDATE licenses
                   SET machine_id = ?, activated_at = CURRENT_TIMESTAMP,
                       last_ip = ?, last_seen_at = CURRENT_TIMESTAMP
                   WHERE license_key = ?""",
                (machine_fp, ip, key),
            )
            await conn.commit()
        LOGGER.info("license_activated", extra={
            "event": "license_activated", "key": key, "fp": machine_fp[:8]
        })
    else:
        if bound_fp != machine_fp:
            LOGGER.warning("license_machine_mismatch", extra={
                "event": "license_machine_mismatch", "key": key
            })
            raise HTTPException(
                401,
                "License key này đã được kích hoạt trên một thiết bị khác. "
                "Liên hệ quản trị viên để reset nếu cần.",
            )
        # Update last seen
        async with db.connection() as conn:
            await conn.execute(
                """UPDATE licenses SET last_ip = ?, last_seen_at = CURRENT_TIMESTAMP
                   WHERE license_key = ?""",
                (ip, key),
            )
            await conn.commit()

    # IP anomaly detection: if IP changed, check how many distinct IPs in last hour
    last_ip = row["last_ip"]
    if last_ip and last_ip != ip:
        await _check_ip_anomaly(db, key, ip)

    return dict(row)


async def _check_ip_anomaly(db, license_key: str, current_ip: str) -> None:
    """
    Count distinct IPs used for this license in the last hour from license_events.
    If > 5 distinct IPs: flag the license as suspicious.
    """
    try:
        async with db.connection() as conn:
            cur = await conn.execute(
                """SELECT COUNT(DISTINCT ip) AS cnt
                   FROM license_events
                   WHERE license_key = ?
                     AND event_type = 'login_ok'
                     AND created_at > datetime('now', '-1 hour')""",
                (license_key,),
            )
            row = await cur.fetchone()
            distinct_ips = row["cnt"] if row else 0

        if distinct_ips >= 5:
            async with db.connection() as conn:
                await conn.execute(
                    """UPDATE licenses SET flagged = 1, flagged_reason = ?
                       WHERE license_key = ?""",
                    (f"IP anomaly: {distinct_ips} distinct IPs in 1 hour", license_key),
                )
                await conn.commit()
            LOGGER.warning("ip_anomaly_flagged", extra={
                "event": "ip_anomaly_flagged", "key": license_key, "distinct_ips": distinct_ips
            })
    except Exception as exc:
        LOGGER.warning("ip_anomaly_check_failed", extra={"err": str(exc)})


async def _has_any_license_in_db(db) -> bool:
    try:
        async with db.connection() as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) AS cnt FROM licenses WHERE is_active = 1"
            )
            row = await cur.fetchone()
            return bool(row and row["cnt"] > 0)
    except Exception:
        return False


async def _ensure_local_license_stub(
    db,
    key: str,
    role: str = "operator",
    max_accounts: int = 10,
    expires_at: str | None = None,
) -> None:
    """
    Upsert a minimal license row in local SQLite so the sessions→licenses
    JOIN in lookup_session() always has a matching row when using Supabase.
    """
    import uuid as _u
    async with db.connection() as conn:
        await conn.execute(
            """INSERT INTO licenses
                   (id, license_key, label, role, max_accounts, expires_at, is_active)
               VALUES (?, ?, 'Supabase-managed', ?, ?, ?, 1)
               ON CONFLICT(license_key) DO UPDATE SET
                   role = excluded.role,
                   max_accounts = excluded.max_accounts,
                   expires_at = excluded.expires_at,
                   is_active = 1,
                   last_seen_at = CURRENT_TIMESTAMP""",
            (str(_u.uuid4()), key, role, max_accounts, expires_at),
        )
        await conn.commit()


def _offline_grace_days() -> int:
    try:
        return max(0, int(os.getenv("LICENSE_OFFLINE_GRACE_DAYS", "7")))
    except ValueError:
        return 7


async def _cache_authority_result(db, result, *, fallback_account: str = "") -> dict:
    account = result.account or fallback_account
    if result.refresh_token:
        save_secret(DEFAULT_REFRESH_TOKEN_KEY, result.refresh_token)
    await _ensure_local_license_stub(
        db,
        result.license_key,
        result.role,
        result.max_accounts,
        expires_at=result.expires_at,
    )
    return await upsert_local_auth_cache(
        db,
        license_key=result.license_key,
        activation_id=result.activation_id,
        account=account,
        role=result.role,
        max_accounts=result.max_accounts,
        expires_at=result.expires_at,
        offline_grace_until=result.offline_grace_until or offline_grace_iso(_offline_grace_days()),
        app_config=result.app_config,
    )


async def _issue_local_login_response(db, request: Request, cache: dict) -> LoginResponse:
    import uuid as _uuid

    ip = get_client_ip(request)
    machine_fp = compute_machine_fingerprint(request)
    license_key = str(cache["license_key"])
    account = str(cache.get("account") or "")
    role = str(cache.get("role") or "operator")
    max_accounts = int(cache.get("max_accounts") or 10)
    session_id = str(_uuid.uuid4())
    token, exp = issue_token(license_key, machine_fp, session_id, role=role, account=account)
    await create_session(db, license_key, machine_fp, ip, token, exp, account=account, session_id=session_id)
    return LoginResponse(
        token=token,
        expires_in=exp - int(time.time()),
        user={"account": account, "role": role, "max_accounts": max_accounts},
    )


async def _refresh_from_authority(request: Request, *, allow_offline: bool) -> LoginResponse:
    from api.supabase_license import LicenseAuthorityUnavailable, refresh as authority_refresh

    db = request.app.state.database
    cache = await get_local_auth_cache(db)
    refresh_token = load_secret(DEFAULT_REFRESH_TOKEN_KEY)
    if not refresh_token or not cache:
        raise HTTPException(401, "Thiết bị này chưa được kích hoạt license.")

    machine_fp = compute_machine_fingerprint(request)
    try:
        result = await authority_refresh(
            refresh_token=refresh_token,
            machine_fp=machine_fp,
            activation_id=cache.get("activation_id"),
        )
        cached = await _cache_authority_result(db, result, fallback_account=cache.get("account") or "")
        await audit_event(
            db,
            "refresh_ok",
            license_key=cached["license_key"],
            ip=get_client_ip(request),
            machine_fp=machine_fp,
            detail={"via": "license_authority"},
        )
        return await _issue_local_login_response(db, request, cached)
    except LicenseAuthorityUnavailable as exc:
        if allow_offline and cache_allows_offline_use(cache):
            await audit_event(
                db,
                "refresh_offline_grace",
                license_key=cache["license_key"],
                ip=get_client_ip(request),
                machine_fp=machine_fp,
                detail={"error": str(exc)},
            )
            return await _issue_local_login_response(db, request, cache)
        raise HTTPException(
            503,
            "Không thể kết nối máy chủ license. Vui lòng kiểm tra mạng để xác thực lại.",
        ) from exc
    except HTTPException:
        delete_secret(DEFAULT_REFRESH_TOKEN_KEY)
        await clear_local_auth_cache(db)
        await revoke_sessions_for_license(db, str(cache["license_key"]), reason="license_authority_rejected")
        raise


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, request: Request) -> LoginResponse:
    """
    Authenticate with license key.
    machine_id from client payload is IGNORED — fingerprint is computed server-side.

    Validation priority:
      1. Supabase Edge Function license authority
      2. Local SQLite licenses table
      3. Debug mode (DEBUG=true, DB empty)
    """
    # Step 1: Rate limit
    await check_login_rate_limit(request)

    account = req.account.strip().lstrip("@")
    key     = req.license_key.strip()

    if not account:
        raise HTTPException(400, "Tên tài khoản không được để trống.")
    if not key:
        raise HTTPException(400, "License key không được để trống.")

    db         = request.app.state.database
    ip         = get_client_ip(request)
    machine_fp = compute_machine_fingerprint(request)

    from api.supabase_license import is_supabase_configured
    role: str = "operator"
    max_accounts = 10

    if is_supabase_configured():
        # ── Supabase path (cloud-managed license store) ────────────────────────
        from api.supabase_license import LicenseAuthorityUnavailable, activate as authority_activate

        try:
            result = await authority_activate(
                account=account,
                license_key=key,
                machine_fp=machine_fp,
                ip=ip,
            )
        except LicenseAuthorityUnavailable as exc:
            raise HTTPException(503, "Không thể kết nối máy chủ license. Vui lòng thử lại.") from exc

        cached = await _cache_authority_result(db, result, fallback_account=account)
        role = str(cached.get("role") or "operator")
        max_accounts = int(cached.get("max_accounts") or 10)
        key = str(cached["license_key"])
        await audit_event(db, "login_ok", license_key=key, ip=ip, machine_fp=machine_fp,
                          detail={"account": account, "via": "license_authority"})

    elif await _has_any_license_in_db(db):
        # ── Local SQLite path (production, DB-seeded licenses) ─────────────────
        await _validate_license_and_bind(db, key, machine_fp, ip)
        await audit_event(db, "login_ok", license_key=key, ip=ip, machine_fp=machine_fp,
                          detail={"account": account})
        # Fetch role from local DB
        try:
            async with db.connection() as conn:
                cur = await conn.execute(
                    "SELECT role, max_accounts FROM licenses WHERE license_key = ?", (key,)
                )
                row_r = await cur.fetchone()
                if row_r and row_r["role"]:
                    role = row_r["role"]
                if row_r and row_r["max_accounts"]:
                    max_accounts = int(row_r["max_accounts"])
        except Exception:
            pass  # column may not exist on old DBs

    else:
        # ── Dev mode: DB empty, no Supabase — accept any key >= 16 chars ───────
        import os as _os
        if _os.getenv("DEBUG", "false").lower() not in ("1", "true", "yes"):
            raise HTTPException(
                503,
                "No licenses configured. "
                "Either set LICENSE_AUTHORITY_URL/SUPABASE_URL for the Edge Function, "
                "or run: python scripts/generate_license.py create --label 'Admin'"
            )
        if len(key) < 16:
            raise HTTPException(401, "License key không hợp lệ (debug mode: >= 16 ký tự).")
        LOGGER.warning("auth_debug_mode", extra={"event": "auth_debug_mode", "account": account})
        max_accounts = 100

        import uuid as _uuid_debug
        async with db.connection() as conn:
            await conn.execute(
                """INSERT OR IGNORE INTO licenses
                       (id, license_key, label, role, max_accounts, is_active)
                   VALUES (?, ?, 'DEBUG — auto-created', 'operator', 100, 1)""",
                (str(_uuid_debug.uuid4()), key),
            )
            await conn.commit()

    # ── Issue token + create session ───────────────────────────────────────────
    import uuid as _uuid
    session_id = str(_uuid.uuid4())
    token, exp  = issue_token(key, machine_fp, session_id, role=role, account=account)
    await create_session(db, key, machine_fp, ip, token, exp, account=account,
                         session_id=session_id)

    expires_in = exp - int(time.time())
    LOGGER.info("login_success", extra={"event": "login_success", "account": account, "ip": ip})

    return LoginResponse(
        token=token,
        expires_in=expires_in,
        user={"account": account, "role": role, "max_accounts": max_accounts},
    )


@router.post("/bootstrap", response_model=BootstrapResponse)
async def bootstrap(request: Request) -> BootstrapResponse:
    """
    Restore a local session from the secure refresh token created at activation.
    This lets packaged users open the app again without re-entering the license.
    """
    from api.supabase_license import is_supabase_configured

    if not is_supabase_configured():
        raise HTTPException(401, "Remote license authority is not configured.")
    response = await _refresh_from_authority(request, allow_offline=True)
    return BootstrapResponse(**response.model_dump())


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(request: Request) -> RefreshResponse:
    """
    Refresh the local access token.

    With the Supabase license authority enabled, this uses the securely stored
    remote refresh token and therefore still works after the local access token
    has expired. Local-only mode keeps the old valid-token rotation behavior.
    """
    from api.supabase_license import is_supabase_configured

    if is_supabase_configured():
        response = await _refresh_from_authority(request, allow_offline=True)
        return RefreshResponse(token=response.token, expires_in=response.expires_in)

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Token không hợp lệ.")

    old_token = auth_header[len("Bearer "):]
    payload   = verify_token(old_token)
    if payload is None:
        raise HTTPException(401, "Token không hợp lệ hoặc đã hết hạn.")

    db = request.app.state.database
    session = await lookup_session(db, old_token)
    if session is None:
        raise HTTPException(401, "Phiên làm việc đã hết hạn. Vui lòng đăng nhập lại.")

    ip = get_client_ip(request)
    machine_fp = payload["fp"]
    license_key = payload["lid"]

    # Revoke old session + issue new
    from api.security import token_sha256
    async with db.connection() as conn:
        await conn.execute(
            "UPDATE sessions SET revoked = 1, revoke_reason = 'refreshed' WHERE token_hash = ?",
            (token_sha256(old_token),),
        )
        await conn.commit()

    # Carry forward role + account from old payload
    old_role    = payload.get("role", "operator")
    old_account = payload.get("acc", "")

    new_token, exp = issue_token(license_key, machine_fp, "pending", role=old_role, account=old_account)
    session_id = await create_session(
        db, license_key, machine_fp, ip, new_token, exp, account=old_account
    )
    new_token, exp = issue_token(license_key, machine_fp, session_id, role=old_role, account=old_account)
    async with db.connection() as conn:
        await conn.execute(
            "UPDATE sessions SET token_hash = ? WHERE id = ?",
            (token_sha256(new_token), session_id),
        )
        await conn.commit()

    return RefreshResponse(token=new_token, expires_in=exp - int(time.time()))


@router.post("/logout")
async def logout(request: Request) -> dict:
    """Revoke the current session."""
    db = request.app.state.database
    refresh_token = load_secret(DEFAULT_REFRESH_TOKEN_KEY)
    if refresh_token:
        from api.supabase_license import logout as authority_logout

        await authority_logout(refresh_token)
    delete_secret(DEFAULT_REFRESH_TOKEN_KEY)
    await clear_local_auth_cache(db)

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return {"logged_out": True}

    token = auth_header[len("Bearer "):]
    from api.security import token_sha256
    async with db.connection() as conn:
        await conn.execute(
            "UPDATE sessions SET revoked = 1, revoke_reason = 'logout' WHERE token_hash = ?",
            (token_sha256(token),),
        )
        await conn.commit()
    return {"logged_out": True}


@router.get("/me")
async def me(request: Request) -> dict:
    """Return current session info (requires valid Bearer token)."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Token không hợp lệ.")
    token = auth_header[len("Bearer "):]
    payload = verify_token(token)
    if payload is None:
        raise HTTPException(401, "Token không hợp lệ hoặc đã hết hạn.")
    db = request.app.state.database
    session = await lookup_session(db, token)
    if session is None:
        raise HTTPException(401, "Phiên làm việc đã hết hạn.")
    return {
        "license_key":   payload["lid"],
        "session_id":    payload["sid"],
        "account":       payload.get("acc") or session.get("account", ""),
        "role":          payload.get("role") or session.get("license_role", "operator"),
        "max_accounts":  session.get("license_max_accounts", 10),
        "valid": True,
    }
