from core.adspower_client import _extract_debug_endpoint

def test_extract_debug_endpoint_prefers_puppeteer_over_playwright_session():
    data = {
        "ws": {
            "playwright": "ws://127.0.0.1:49692/session",
            "puppeteer": "ws://127.0.0.1:49692/devtools/browser/abc"
        }
    }
    endpoint, source = _extract_debug_endpoint(data)
    assert endpoint == "ws://127.0.0.1:49692/devtools/browser/abc"
    assert source == "ws.puppeteer"

def test_extract_debug_endpoint_uses_debug_port_fallback():
    data = {
        "debug_port": "49692"
    }
    endpoint, source = _extract_debug_endpoint(data)
    assert endpoint == "http://127.0.0.1:49692"
    assert source == "debug_port"

def test_extract_debug_endpoint_ignores_playwright_session():
    data = {
        "ws": {
            "playwright": "ws://127.0.0.1:49692/session"
        }
    }
    endpoint, source = _extract_debug_endpoint(data)
    assert endpoint is None
    assert source == "none"

def test_extract_debug_endpoint_from_ws_string():
    data = {
        "ws": "ws://127.0.0.1:12345/devtools/browser/xyz"
    }
    endpoint, source = _extract_debug_endpoint(data)
    assert endpoint == "ws://127.0.0.1:12345/devtools/browser/xyz"
    assert source == "ws"
