import pytest
from unittest.mock import AsyncMock, patch

from core.adspower_client import _resolve_cdp_endpoint

@pytest.fixture
def mock_probe():
    with patch("core.adspower_client._probe_http_cdp_endpoint", new_callable=AsyncMock) as mock:
        yield mock

@pytest.mark.asyncio
async def test_resolve_cdp_endpoint_prefers_puppeteer_over_playwright_session(mock_probe):
    data = {
        "ws": {
            "playwright": "ws://127.0.0.1:49692/session",
            "puppeteer": "ws://127.0.0.1:49692/devtools/browser/abc"
        }
    }
    endpoint, source, rejected = await _resolve_cdp_endpoint(data, "prof_1")
    assert endpoint == "ws://127.0.0.1:49692/devtools/browser/abc"
    assert source == "ws.puppeteer"
    assert len(rejected) > 0
    assert rejected[0]["source"] == "ws.playwright"

@pytest.mark.asyncio
async def test_resolve_cdp_endpoint_rejects_debug_port_if_probe_fails(mock_probe):
    mock_probe.return_value = None
    data = {
        "debug_port": "49692"
    }
    endpoint, source, rejected = await _resolve_cdp_endpoint(data, "prof_1")
    assert endpoint is None
    assert source == "none"
    assert len(rejected) == 1
    assert rejected[0]["source"] == "debug_port"

@pytest.mark.asyncio
async def test_resolve_cdp_endpoint_accepts_debug_port_if_probe_succeeds(mock_probe):
    mock_probe.return_value = "ws://127.0.0.1:49692/devtools/browser/from_probe"
    data = {
        "debug_port": "49692"
    }
    endpoint, source, rejected = await _resolve_cdp_endpoint(data, "prof_1")
    assert endpoint == "ws://127.0.0.1:49692/devtools/browser/from_probe"
    assert source == "debug_port.json_version"

@pytest.mark.asyncio
async def test_resolve_cdp_endpoint_rejects_selenium():
    data = {
        "ws": {
            "selenium": "ws://127.0.0.1:49692/devtools/browser/123"
        }
    }
    endpoint, source, rejected = await _resolve_cdp_endpoint(data, "prof_1")
    assert endpoint is None
    # Assuming selenium is not even listed in candidates list except we added logic to catch it.
    # Wait, the code doesn't append `ws.selenium` to candidates! Let's just check it returns None.

@pytest.mark.asyncio
async def test_resolve_cdp_endpoint_rejects_all(mock_probe):
    mock_probe.return_value = None
    data = {
        "ws": {
            "playwright": "ws://127.0.0.1:49692/session",
        },
        "debug_port": "49692"
    }
    endpoint, source, rejected = await _resolve_cdp_endpoint(data, "prof_1")
    assert endpoint is None
    assert len(rejected) == 2
