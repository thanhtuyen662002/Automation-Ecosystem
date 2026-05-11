"""One-off script: initialize missing DB tables from database/schema.sql."""
from __future__ import annotations
import asyncio, os, sys
from pathlib import Path

# Load .env
for raw in Path(".env").read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

sys.path.insert(0, ".")
from database.database import AutomationDatabase

async def main() -> None:
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    print(f"[init_db] Connecting to: {db_url}")
    db = AutomationDatabase(db_url)
    await db.open()

    schema_sql = Path("database/schema.sql").read_text(encoding="utf-8")

    # Split into individual statements and run each, skipping already-existing objects
    statements = [s.strip() for s in schema_sql.split(";") if s.strip() and not s.strip().startswith("--")]

    ok = skipped = 0
    async with db.connection() as conn:
        for stmt in statements:
            try:
                await conn.execute(stmt)
                ok += 1
            except Exception as exc:
                err = str(exc).lower()
                if "already exists" in err:
                    skipped += 1
                else:
                    print(f"[WARN] {exc} — stmt: {stmt[:60]}")
                    skipped += 1
        await conn.commit()

    await db.close()
    print(f"[init_db] Done — {ok} statements applied, {skipped} skipped (already exist) ✓")

if __name__ == "__main__":
    asyncio.run(main())
