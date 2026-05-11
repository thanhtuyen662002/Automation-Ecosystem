"""
Migration 008: Add task_key column to tasks table (if missing).
Run: python scripts/migrate_008.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import aiosqlite

DB_PATH = os.environ.get(
    "DATABASE_URL",
    "sqlite+aiosqlite:///C:/Users/Admin/AppData/Roaming/Automation-Ecosystem/data/app.db",
)

def _resolve_path(url: str) -> str:
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if url.startswith(prefix):
            return url[len(prefix):]
    return url


MIGRATION_SQL = """
-- 008: add task_key to tasks (idempotent via PRAGMA check)
ALTER TABLE tasks ADD COLUMN task_key TEXT NOT NULL DEFAULT '';
"""

# Columns that schema.sql expects but may be missing from old DBs
EXPECTED_COLUMNS: dict[str, str] = {
    "task_key": "TEXT NOT NULL DEFAULT ''",
}


async def run() -> None:
    db_path = _resolve_path(DB_PATH)
    print(f"[migrate_008] Connecting to: {db_path}")

    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row

        # Read current columns
        cur = await conn.execute("PRAGMA table_info(tasks)")
        rows = await cur.fetchall()
        existing = {row["name"] for row in rows}
        print(f"[migrate_008] Existing tasks columns: {sorted(existing)}")

        added: list[str] = []
        for col, col_def in EXPECTED_COLUMNS.items():
            if col not in existing:
                sql = f"ALTER TABLE tasks ADD COLUMN {col} {col_def}"
                print(f"[migrate_008] Adding column: {col}")
                await conn.execute(sql)
                added.append(col)

        if added:
            await conn.commit()
            print(f"[migrate_008] Done. Added columns: {added}")
        else:
            print("[migrate_008] No changes needed — all columns already present.")

        # Verify
        cur2 = await conn.execute("PRAGMA table_info(tasks)")
        rows2 = await cur2.fetchall()
        final = [row["name"] for row in rows2]
        print(f"[migrate_008] Final tasks columns: {final}")


if __name__ == "__main__":
    asyncio.run(run())
