"""
fix_db_state.py — One-time repair for orphaned task_executions.

Run after a backend crash to clear stale 'running' execution records
that block re-acquisition of READY/PENDING/RETRY tasks.
"""
import os
import sqlite3
from pathlib import Path

DB_PATH = os.environ.get(
    "DATABASE_URL",
    str(Path.home() / "AppData/Roaming/Automation-Ecosystem/data/app.db"),
).replace("sqlite:///", "")

print(f"DB: {DB_PATH}")
con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row

# ── Current state ──────────────────────────────────────────────────────────────
print("\n=== TASK STATUS ===")
for r in con.execute("SELECT status, count(*) cnt FROM tasks GROUP BY status ORDER BY cnt DESC"):
    print(f"  {r['status']}: {r['cnt']}")

print("\n=== EXECUTION STATUS ===")
for r in con.execute("SELECT status, count(*) cnt FROM task_executions GROUP BY status ORDER BY cnt DESC"):
    print(f"  {r['status']}: {r['cnt']}")

# ── Fix 1: orphaned 'running' executions for non-RUNNING tasks ─────────────────
orphan_sql = """
    SELECT ex.id, ex.task_id, ex.attempt_number, t.status as task_status, t.task_type
    FROM task_executions ex
    JOIN tasks t ON t.id = ex.task_id
    WHERE ex.status = 'running'
      AND t.status IN ('READY', 'PENDING', 'RETRY', 'FAILED')
"""
orphans = con.execute(orphan_sql).fetchall()
print(f"\n=== ORPHAN RUNNING EXECUTIONS: {len(orphans)} ===")
for r in orphans:
    print(f"  task_type={r['task_type']} task_status={r['task_status']} attempt={r['attempt_number']}")

if orphans:
    orphan_ids = [r["id"] for r in orphans]
    ph = ",".join("?" * len(orphan_ids))
    con.execute(
        f"UPDATE task_executions SET status='failed', error='orphaned_by_backend_restart',"
        f" completed_at=CURRENT_TIMESTAMP WHERE id IN ({ph})",
        orphan_ids,
    )
    con.commit()
    print(f"  -> Fixed {len(orphan_ids)} orphan executions.")

# ── Fix 2: tasks stuck in RUNNING with expired lease ──────────────────────────
stale_running = con.execute("""
    SELECT t.id, t.task_type, t.retry_count
    FROM tasks t
    WHERE t.status = 'RUNNING'
      AND NOT EXISTS (
          SELECT 1 FROM task_executions ex
          WHERE ex.task_id = t.id AND ex.status = 'running'
      )
""").fetchall()
print(f"\n=== RUNNING TASKS WITH NO ACTIVE EXECUTION: {len(stale_running)} ===")
for r in stale_running:
    print(f"  task_type={r['task_type']} retry_count={r['retry_count']}")

if stale_running:
    stale_ids = [r["id"] for r in stale_running]
    ph = ",".join("?" * len(stale_ids))
    # Reset to RETRY so scheduler can re-promote them
    con.execute(
        f"UPDATE tasks SET status='RETRY', next_retry_at=CURRENT_TIMESTAMP,"
        f" retry_count=retry_count+1, updated_at=CURRENT_TIMESTAMP"
        f" WHERE id IN ({ph})",
        stale_ids,
    )
    con.commit()
    print(f"  -> Reset {len(stale_ids)} stale RUNNING tasks to RETRY.")

# ── Final state ────────────────────────────────────────────────────────────────
print("\n=== FINAL TASK STATUS ===")
for r in con.execute("SELECT status, count(*) cnt FROM tasks GROUP BY status ORDER BY cnt DESC"):
    print(f"  {r['status']}: {r['cnt']}")

print("\n=== READY TASKS attempt_number check ===")
for r in con.execute("""
    SELECT t.task_type, t.retry_count,
           COUNT(ex.id) exec_cnt, MAX(ex.attempt_number) max_att
    FROM tasks t
    LEFT JOIN task_executions ex ON ex.task_id = t.id
    WHERE t.status = 'READY'
    GROUP BY t.id
    LIMIT 10
"""):
    next_att = (r["exec_cnt"] or 0) + 1
    clash = " <-- NO CLASH" if next_att != (r["retry_count"] or 0) + 1 else ""
    print(f"  type={r['task_type']} retry={r['retry_count']} exec_cnt={r['exec_cnt']} max_att={r['max_att']} next_attempt={next_att}{clash}")

con.close()
print("\nDone.")
