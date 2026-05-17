import sqlite3
import json
import os

db_path = "C:/Users/Vo Thanh Tuyen/AppData/Roaming/Automation-Ecosystem/data/app.db"
if not os.path.exists(db_path):
    print(f"Error: Database file does not exist at {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

print("=== Tasks created since 2026-05-17 15:30:00 ===")
rows = conn.execute(
    """
    SELECT t.id, t.task_type, t.status, t.retry_count,
           t.payload, t.result, t.error_message, t.created_at, t.updated_at
    FROM tasks t
    WHERE t.created_at >= '2026-05-17 15:30:00'
    ORDER BY t.created_at DESC
    """
).fetchall()

if not rows:
    print("No tasks found.")
else:
    for r in rows:
        print(f"\nTask: {r['task_type']} ({r['id']}) | Status: {r['status']}")
        print(f"  Created: {r['created_at']} | Updated: {r['updated_at']}")
        if r['error_message']:
            print(f"  Error: {r['error_message']}")

conn.close()
