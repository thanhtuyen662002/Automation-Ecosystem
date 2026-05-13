"""Verify yt-dlp fix and test live scraping."""
import sys
import shutil
from pathlib import Path

# Test get_ytdlp_path() logic
scripts_dir = Path(sys.executable).parent
print(f"Python: {sys.executable}")
print(f"Scripts dir: {scripts_dir}")

ytdlp_path = None
for name in ("yt-dlp.exe", "yt-dlp"):
    candidate = scripts_dir / name
    if candidate.is_file():
        ytdlp_path = str(candidate)
        print(f"Found yt-dlp at: {ytdlp_path}")
        break

if not ytdlp_path:
    found = shutil.which("yt-dlp")
    if found:
        ytdlp_path = found
        print(f"Found yt-dlp via PATH: {ytdlp_path}")
    else:
        print("ERROR: yt-dlp NOT found anywhere!")
        sys.exit(1)

# Quick live test
import subprocess, json

print(f"\n=== Testing yt-dlp scrape (ttsearch3:phone case) ===")
result = subprocess.run(
    [ytdlp_path, "--dump-json", "--no-playlist", "--skip-download", "--quiet", "ttsearch3:phone case"],
    capture_output=True, text=True, timeout=60
)
lines = [l for l in result.stdout.splitlines() if l.strip()]
print(f"Videos returned: {len(lines)}")
for i, line in enumerate(lines[:3]):
    try:
        data = json.loads(line)
        print(f"  [{i+1}] url={data.get('webpage_url','N/A')} views={data.get('view_count')} duration={data.get('duration')}s")
    except Exception:
        print(f"  [{i+1}] parse error: {line[:100]}")
if result.stderr.strip():
    print(f"stderr: {result.stderr[:400]}")
print(f"Return code: {result.returncode}")

# Test import of the fixed module
print("\n=== Testing import of fixed module ===")
try:
    from workers.handlers.tiktok._base import get_ytdlp_path
    path = get_ytdlp_path()
    print(f"get_ytdlp_path() => {path}")
except Exception as e:
    print(f"Import error: {e}")
