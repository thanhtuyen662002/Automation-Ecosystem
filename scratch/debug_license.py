import os
import sys
import asyncio
from datetime import UTC, datetime

# Add the project root to sys.path
sys.path.append(os.getcwd())

async def debug_license():
    from api.supabase_license import is_supabase_configured, _fetch_license, _get_supabase
    
    print(f"SUPABASE_URL: {os.environ.get('SUPABASE_URL')}")
    print(f"SUPABASE_SERVICE_KEY: {os.environ.get('SUPABASE_SERVICE_KEY')[:10]}...")
    print(f"Is Supabase configured? {is_supabase_configured()}")
    
    key = "AE-6898BBEBA3322B7CF17025553B99CE37"
    print(f"Fetching license: {key}")
    
    try:
        row = _fetch_license(key)
        if row:
            print("FOUND LICENSE:")
            print(f"  ID: {row.get('id')}")
            print(f"  Key: {row.get('license_key')}")
            print(f"  Role: {row.get('role')}")
            print(f"  Active: {row.get('is_active')}")
            print(f"  Machine: {row.get('machine_id')}")
        else:
            print("LICENSE NOT FOUND in Supabase.")
            
            # Check all licenses in Supabase
            print("Listing all licenses in Supabase:")
            sb = _get_supabase()
            res = sb.table("licenses").select("*").execute()
            for r in res.data:
                print(f"  - {r.get('license_key')} (Role: {r.get('role')})")
                
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(debug_license())
