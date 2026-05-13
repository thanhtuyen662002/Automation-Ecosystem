"""
Migration 010 — Add account profile fields to accounts table.

Run from the project root:
    python database/migrations/migrate_010_account_profile.py
"""
import asyncio
import os
import sys
from pathlib import Path

# Allow running from project root
ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

# Load .env from project root so DATABASE_URL is populated
_env_file = ROOT / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "sqlite+aiosqlite:///C:/Users/Admin/AppData/Roaming/Automation-Ecosystem/data/app.db",
)


async def migrate() -> None:
    import aiosqlite

    db_path = DATABASE_URL.removeprefix("sqlite:///").removeprefix("sqlite+aiosqlite:///")
    print(f"Migrating: {db_path}")

    async with aiosqlite.connect(db_path) as conn:
        # Check existing columns
        cur = await conn.execute("PRAGMA table_info(accounts)")
        cols = {row[1] for row in await cur.fetchall()}

        for column in ("avatar_url", "display_name", "profile_url", "external_user_id"):
            if column not in cols:
                await conn.execute(f"ALTER TABLE accounts ADD COLUMN {column} TEXT")
                print(f"  + {column} column added")
            else:
                print(f"  ~ {column} already exists")

        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS accounts_profile_url_idx
                ON accounts (profile_url)
                WHERE profile_url IS NOT NULL
            """
        )

        await conn.commit()
        print("Migration 010 complete.")


if __name__ == "__main__":
    asyncio.run(migrate())
