import sqlite3
import os

db_path = "C:/Users/Admin/AppData/Roaming/Automation-Ecosystem/data/app.db"
print(f"Testing connection to: {db_path}")

try:
    # Ensure dir exists
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    conn = sqlite3.connect(db_path)
    print("Success!")
    conn.close()
except Exception as e:
    print(f"FAILED: {e}")

db_path_2 = "/C:/Users/Admin/AppData/Roaming/Automation-Ecosystem/data/app.db"
print(f"Testing connection to: {db_path_2}")
try:
    conn = sqlite3.connect(db_path_2)
    print("Success!")
    conn.close()
except Exception as e:
    print(f"FAILED: {e}")
