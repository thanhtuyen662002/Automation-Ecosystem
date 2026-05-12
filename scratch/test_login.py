import requests
import json
import sys

# Ensure stdout can handle UTF-8
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE_URL = "http://localhost:8000/api/v1"
LICENSE_KEY = "AE-6898BBEBA3322B7CF17025553B99CE37"
ACCOUNT = "admin"

def test_login():
    url = f"{BASE_URL}/auth/login"
    payload = {
        "account": ACCOUNT,
        "license_key": LICENSE_KEY
    }
    print(f"Testing login with {payload}...")
    try:
        response = requests.post(url, json=payload)
        print(f"Status Code: {response.status_code}")
        try:
            print(f"Response Body: {response.json()}")
        except:
            print(f"Response Body (raw): {response.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_login()
