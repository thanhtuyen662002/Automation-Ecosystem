import sqlite3
import json
import subprocess

db_path = "C:/Users/Admin/AppData/Roaming/Automation-Ecosystem/data/app.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

print("=== Recent search_tiktok & select_videos tasks ===")
rows = conn.execute(
    """
    SELECT t.task_type, t.status, t.retry_count,
           substr(t.result, 1, 800) as result_snippet,
           t.error_message, t.error_type,
           t.created_at
    FROM tasks t
    WHERE t.task_type IN ('tiktok.search_tiktok', 'tiktok.select_videos')
    ORDER BY t.created_at DESC
    LIMIT 10
    """
).fetchall()

for r in rows:
    print(f"\n--- {r['task_type']} | status={r['status']} | retries={r['retry_count']} | created={r['created_at']}")
    if r["result_snippet"]:
        try:
            result = json.loads(r["result_snippet"])
            if "videos" in result:
                print(f"    videos count: {len(result['videos'])}")
                if result["videos"]:
                    print(f"    first video keys: {list(result['videos'][0].keys())}")
            else:
                print(f"    result keys: {list(result.keys())}")
        except Exception:
            print(f"    result (raw): {r['result_snippet'][:200]}")
    if r["error_message"]:
        print(f"    ERROR [{r['error_type']}]: {r['error_message'][:300]}")

conn.close()

print("\n\n=== Checking if yt-dlp is available ===")
try:
    result = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True, timeout=10)
    print(f"yt-dlp version: {result.stdout.strip()}")
    if result.stderr.strip():
        print(f"stderr: {result.stderr.strip()}")
except FileNotFoundError:
    print("ERROR: yt-dlp NOT FOUND in PATH!")
except Exception as e:
    print(f"ERROR running yt-dlp: {e}")

print("\n=== Quick test: yt-dlp ttsearch3:phone case ===")
try:
    result = subprocess.run(
        ["yt-dlp", "--dump-json", "--no-playlist", "--skip-download", "--quiet", "ttsearch3:phone case"],
        capture_output=True, text=True, timeout=30
    )
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    print(f"stdout lines returned: {len(lines)}")
    if lines:
        try:
            data = json.loads(lines[0])
            print(f"First video: url={data.get('webpage_url','N/A')} views={data.get('view_count')} duration={data.get('duration')}")
        except Exception:
            print(f"First line (raw): {lines[0][:200]}")
    else:
        print("No video output from yt-dlp - scraping FAILED!")
    if result.stderr.strip():
        print(f"stderr: {result.stderr[:600]}")
    print(f"Return code: {result.returncode}")
except subprocess.TimeoutExpired:
    print("TIMEOUT after 30s - yt-dlp hung or network blocked!")
except FileNotFoundError:
    print("ERROR: yt-dlp NOT FOUND in PATH!")
except Exception as e:
    print(f"ERROR: {e}")
