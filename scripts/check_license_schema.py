from __future__ import annotations

import os
import sys

from license_admin_common import load_env_file


REQUIRED_TABLES = {"licenses", "license_devices", "license_audit_logs", "app_license_config"}
REQUIRED_LICENSE_COLUMNS = {"license_key_hash", "license_key_prefix", "status", "max_devices", "expires_at", "metadata"}
REQUIRED_CONFIG_KEYS = {"license_offline_grace_days", "default_max_devices", "require_online_activation"}


def _db_url() -> str:
    value = os.getenv("SUPABASE_DB_URL", "").strip() or os.getenv("POSTGRES_URL", "").strip()
    if not value:
        database_url = os.getenv("DATABASE_URL", "").strip()
        if database_url.startswith(("postgres://", "postgresql://")):
            value = database_url
    if not value:
        raise SystemExit("Missing SUPABASE_DB_URL or POSTGRES_URL for schema verification")
    return value


def main() -> None:
    load_env_file()
    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit("Missing dependency psycopg. Install project dependencies before running this script.") from exc

    failures: list[str] = []
    with psycopg.connect(_db_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name
                  FROM information_schema.tables
                 WHERE table_schema = 'public'
                   AND table_name = ANY(%s)
                """,
                (list(REQUIRED_TABLES),),
            )
            tables = {row[0] for row in cur.fetchall()}
            failures.extend(f"missing table: {table}" for table in sorted(REQUIRED_TABLES - tables))

            cur.execute(
                """
                SELECT column_name
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'licenses'
                """
            )
            license_columns = {row[0] for row in cur.fetchall()}
            failures.extend(f"missing licenses column: {col}" for col in sorted(REQUIRED_LICENSE_COLUMNS - license_columns))

            cur.execute(
                """
                SELECT tc.constraint_name
                  FROM information_schema.table_constraints tc
                  JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                   AND tc.table_schema = kcu.table_schema
                 WHERE tc.table_schema = 'public'
                   AND tc.table_name = 'license_devices'
                   AND tc.constraint_type = 'UNIQUE'
                 GROUP BY tc.constraint_name
                HAVING array_agg(kcu.column_name ORDER BY kcu.ordinal_position) = ARRAY['license_id','machine_id_hash']
                """
            )
            if cur.fetchone() is None:
                failures.append("missing unique constraint license_devices(license_id, machine_id_hash)")

            cur.execute(
                """
                SELECT key
                  FROM public.app_license_config
                 WHERE key = ANY(%s)
                """,
                (list(REQUIRED_CONFIG_KEYS),),
            )
            config_keys = {row[0] for row in cur.fetchall()}
            failures.extend(f"missing app_license_config key: {key}" for key in sorted(REQUIRED_CONFIG_KEYS - config_keys))

            cur.execute(
                """
                SELECT p.proname, p.prosecdef, pg_get_function_identity_arguments(p.oid)
                  FROM pg_proc p
                  JOIN pg_namespace n ON n.oid = p.pronamespace
                 WHERE n.nspname = 'public'
                   AND p.proname = 'activate_license_device'
                """
            )
            rpc = cur.fetchone()
            if rpc is None:
                failures.append("missing RPC function public.activate_license_device")
            elif rpc[1] is not True:
                failures.append("public.activate_license_device is not SECURITY DEFINER")

    if failures:
        print("License schema check failed:")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print("License schema check passed.")


if __name__ == "__main__":
    main()
