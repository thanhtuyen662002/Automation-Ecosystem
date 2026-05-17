import sqlite3
import json
import os

db_path = "C:/Users/Vo Thanh Tuyen/AppData/Roaming/Automation-Ecosystem/data/app.db"
if not os.path.exists(db_path):
    print(f"Error: Database file does not exist at {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

print("=== 20 Recent Tasks ===")
rows = conn.execute(
    """
    SELECT t.id, t.task_type, t.status, t.retry_count,
           t.payload, t.result, t.error_message, t.error_type,
           t.created_at, t.updated_at
    FROM tasks t
    ORDER BY t.created_at DESC
    LIMIT 20
    """
).fetchall()

for r in rows:
    print(f"\n--- [{r['id']}] {r['task_type']} ---")
    print(f"    Status:      {r['status']}")
    print(f"    Retries:     {r['retry_count']}")
    print(f"    Created:     {r['created_at']}")
    print(f"    Updated:     {r['updated_at']}")
    
    if r["payload"]:
        try:
            payload = json.loads(r["payload"])
            # Print simple summary of payload
            print(f"    Payload:     job_id={payload.get('job_id')}, account_id={payload.get('account_id')}")
            if "selected_videos" in payload:
                print(f"                 selected_videos count: {len(payload['selected_videos'])}")
            elif "keywords" in payload:
                print(f"                 keywords: {payload.get('keywords')}")
        except Exception:
            print(f"    Payload (raw): {r['payload'][:200]}")
            
    if r["result"]:
        try:
            result = json.loads(r["result"])
            print(f"    Result keys: {list(result.keys())}")
            if "video_paths" in result:
                print(f"                 video_paths count: {len(result['video_paths'])}")
                for p in result['video_paths']:
                    print(f"                 - {p}")
            if "failed_urls" in result and result["failed_urls"]:
                print(f"                 failed_urls count: {len(result['failed_urls'])}")
                for u in result['failed_urls']:
                    print(f"                 - {u}")
        except Exception:
            print(f"    Result (raw): {r['result'][:200]}")
            
    if r["error_message"]:
        print(f"    ERROR [{r['error_type']}]: {r['error_message']}")

conn.close()
