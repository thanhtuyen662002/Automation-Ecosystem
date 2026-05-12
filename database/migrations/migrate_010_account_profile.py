"""
Migration 010 — Add avatar_url and display_name to accounts table.

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

        if "avatar_url" not in cols:
            await conn.execute("ALTER TABLE accounts ADD COLUMN avatar_url TEXT")
            print("  + avatar_url column added")
        else:
            print("  ~ avatar_url already exists")

        if "display_name" not in cols:
            await conn.execute("ALTER TABLE accounts ADD COLUMN display_name TEXT")
            print("  + display_name column added")
        else:
            print("  ~ display_name already exists")

        await conn.commit()
        print("Migration 010 complete.")


if __name__ == "__main__":
    asyncio.run(migrate())
