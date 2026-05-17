import sqlite3
import json
import os

db_path = "C:/Users/Vo Thanh Tuyen/AppData/Roaming/Automation-Ecosystem/data/app.db"
if not os.path.exists(db_path):
    print(f"Error: Database file does not exist at {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

job_id = "563fc570-e454-4fa3-ab90-8a948ed293ab"
print(f"=== Tasks for Job {job_id} ===")
rows = conn.execute(
    """
    SELECT t.id, t.task_type, t.status, t.retry_count,
           t.payload, t.result, t.error_message, t.error_type,
           t.created_at, t.updated_at
    FROM tasks t
    WHERE t.job_id = ?
    ORDER BY t.created_at ASC
    """,
    (job_id,)
).fetchall()

for r in rows:
    print(f"\nTask: {r['task_type']} ({r['id']}) | Status: {r['status']} | Retries: {r['retry_count']}")
    print(f"  Created: {r['created_at']} | Updated: {r['updated_at']}")
    if r['error_message']:
        print(f"  Error [{r['error_type']}]: {r['error_message']}")
    if r['result']:
        try:
            res = json.loads(r['result'])
            if 'video_paths' in res:
                print(f"  Result video_paths: {res['video_paths']}")
            if 'failed_urls' in res:
                print(f"  Result failed_urls: {res['failed_urls']}")
        except Exception:
            pass

conn.close()
