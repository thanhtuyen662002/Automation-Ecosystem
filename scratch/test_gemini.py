import os
import sys
import asyncio
from pathlib import Path

# Add project root to path
sys.path.append(os.getcwd())

async def test_gemini():
    print("--- Gemini Connection Test (v3) ---")
    from scripts.start_backend import _get_appdata_dir, _load_env
    app_data_dir = _get_appdata_dir()
    env_path = app_data_dir / ".env.production"
    _load_env(env_path, app_data_dir)
    
    api_key = os.environ.get("GEMINI_API_KEY")
    model_name = os.environ.get("GEMINI_MODEL")
    
    print(f"Testing with Model: {model_name}")

    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
        
        response = await asyncio.to_thread(model.generate_content, "Hello! Please confirm you are working.")
        print(f"SUCCESS! Gemini says: {response.text.strip()}")
            
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(test_gemini())
