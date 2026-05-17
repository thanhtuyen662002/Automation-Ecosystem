import sqlite3
import json
import os

db_path = "C:/Users/Vo Thanh Tuyen/AppData/Roaming/Automation-Ecosystem/data/app.db"
if not os.path.exists(db_path):
    print(f"Error: Database file does not exist at {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

print("=== Recent Jobs ===")
rows = conn.execute(
    """
    SELECT j.id, j.workflow_name, j.status, j.started_at, j.completed_at, j.created_at, j.error_message
    FROM jobs j
    ORDER BY j.created_at DESC
    LIMIT 10
    """
).fetchall()

for r in rows:
    print(f"\nJob ID: {r['id']} | Workflow: {r['workflow_name']} | Status: {r['status']}")
    print(f"  Created:   {r['created_at']}")
    print(f"  Started:   {r['started_at']}")
    print(f"  Completed: {r['completed_at']}")
    if r['error_message']:
        print(f"  Error:     {r['error_message']}")

conn.close()
