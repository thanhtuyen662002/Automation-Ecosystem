from __future__ import annotations

import os
import sys

import httpx

from license_admin_common import hash_machine_for_security_test, load_env_file, require_env, rest_url, rpc_url, service_headers


TABLES = ("licenses", "license_devices", "license_audit_logs", "app_license_config")


def _anon_headers() -> dict[str, str]:
    key = os.getenv("SUPABASE_ANON_KEY", "").strip() or os.getenv("SUPABASE_KEY", "").strip()
    if not key:
        raise SystemExit("Missing SUPABASE_ANON_KEY for security verification")
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _service_headers() -> dict[str, str]:
    return service_headers(require_env("SUPABASE_SERVICE_ROLE_KEY"))


def _expect_denied(response: httpx.Response, label: str, failures: list[str]) -> None:
    if response.status_code < 400:
        failures.append(f"{label}: expected denied, got HTTP {response.status_code}")


def _expect_allowed(response: httpx.Response, label: str, failures: list[str]) -> None:
    if response.status_code >= 400:
        failures.append(f"{label}: expected allowed, got HTTP {response.status_code} {response.text[:300]}")


def _check_rls(failures: list[str]) -> None:
    db_url = os.getenv("SUPABASE_DB_URL", "").strip() or os.getenv("POSTGRES_URL", "").strip()
    if not db_url:
        database_url = os.getenv("DATABASE_URL", "").strip()
        if database_url.startswith(("postgres://", "postgresql://")):
            db_url = database_url
    if not db_url:
        failures.append("RLS check skipped: missing SUPABASE_DB_URL or POSTGRES_URL")
        return
    try:
        import psycopg
    except ImportError:
        failures.append("RLS check skipped: missing psycopg dependency")
        return
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.relname, c.relrowsecurity
                  FROM pg_class c
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = 'public'
                   AND c.relname = ANY(%s)
                """,
                (list(TABLES),),
            )
            rows = dict(cur.fetchall())
    for table in TABLES:
        if rows.get(table) is not True:
            failures.append(f"RLS is not enabled on public.{table}")


def main() -> None:
    load_env_file()
    anon_headers = _anon_headers()
    service_headers_ = _service_headers()
    failures: list[str] = []

    for table in TABLES:
        denied = httpx.get(
            rest_url(table),
            headers=anon_headers,
            params={"select": "*", "limit": "1"},
            timeout=20,
        )
        _expect_denied(denied, f"anon select public.{table}", failures)

        allowed = httpx.get(
            rest_url(table),
            headers=service_headers_,
            params={"select": "*", "limit": "1"},
            timeout=20,
        )
        _expect_allowed(allowed, f"service_role select public.{table}", failures)

    rpc_body = {
        "p_license_key_hash": "security-check-invalid-key",
        "p_machine_id_hash": hash_machine_for_security_test("security-check-machine"),
        "p_device_name": "security-check",
        "p_platform": "script",
        "p_app_version": "security-check",
        "p_metadata": {"script": "check_license_security.py"},
    }
    anon_rpc = httpx.post(rpc_url("activate_license_device"), headers=anon_headers, json=rpc_body, timeout=20)
    _expect_denied(anon_rpc, "anon execute public.activate_license_device", failures)

    service_rpc = httpx.post(rpc_url("activate_license_device"), headers=service_headers_, json=rpc_body, timeout=20)
    _expect_allowed(service_rpc, "service_role execute public.activate_license_device", failures)
    if service_rpc.status_code < 400:
        try:
            body = service_rpc.json()
        except ValueError:
            failures.append("service_role RPC returned non-JSON response")
        else:
            if body.get("status") != "invalid_key":
                failures.append(f"service_role RPC expected invalid_key for test hash, got {body!r}")

    _check_rls(failures)

    if failures:
        print("License security check failed:")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print("License security check passed.")


if __name__ == "__main__":
    main()
