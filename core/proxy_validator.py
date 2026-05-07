"""
Proxy health checking — pure asyncio, no external dependencies.

Validates proxy connectivity via TCP socket before launching Playwright.
Fast-fails on dead proxies to avoid wasting a publishing slot.

Geo resolution:
  - Tries to detect country from the proxy URL hostname hints
    (e.g. "us-dc1.proxy.example.com" → "US")
  - Falls back to UNKNOWN (geo can be set manually via the UI)

Usage:
    reachable, latency_ms = await check_proxy_connectivity("http://user:pass@host:port")
    country = guess_country_from_url("http://vn.proxy.example.com:8080")
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from urllib.parse import urlparse

LOGGER = logging.getLogger("core.proxy_validator")

# Max latency before a proxy is considered "slow" (warning, not blocked)
SLOW_PROXY_THRESHOLD_MS: int = 3000

# ── Country hint patterns in proxy hostnames ─────────────────────────────────
# Ordered by length (longer = more specific) to avoid partial false matches.
_HOSTNAME_COUNTRY_HINTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bus[\-_\.]", re.I), "US"),
    (re.compile(r"\bgb[\-_\.]|uk[\-_\.]|unitedkingdom", re.I), "GB"),
    (re.compile(r"\bvn[\-_\.]|vietnam|viet", re.I), "VN"),
    (re.compile(r"\bde[\-_\.]|germany|deutsch", re.I), "DE"),
    (re.compile(r"\bfr[\-_\.]|france", re.I), "FR"),
    (re.compile(r"\bjp[\-_\.]|japan", re.I), "JP"),
    (re.compile(r"\bkr[\-_\.]|korea", re.I), "KR"),
    (re.compile(r"\bsg[\-_\.]|singapore|sing", re.I), "SG"),
    (re.compile(r"\bth[\-_\.]|thai", re.I), "TH"),
    (re.compile(r"\bid[\-_\.]|indo", re.I), "ID"),
    (re.compile(r"\bcn[\-_\.]|china|chinese", re.I), "CN"),
    (re.compile(r"\bau[\-_\.]|australia|aussie", re.I), "AU"),
    (re.compile(r"\bca[\-_\.]|canada", re.I), "CA"),
    (re.compile(r"\bbr[\-_\.]|brazil", re.I), "BR"),
    (re.compile(r"\bin[\-_\.]|india", re.I), "IN"),
    (re.compile(r"\bnl[\-_\.]|netherlands|dutch", re.I), "NL"),
    (re.compile(r"\bpl[\-_\.]|poland|polish", re.I), "PL"),
    (re.compile(r"\bua[\-_\.]|ukraine", re.I), "UA"),
    (re.compile(r"\bru[\-_\.]|russia|russian", re.I), "RU"),
    (re.compile(r"\bmx[\-_\.]|mexico", re.I), "MX"),
    (re.compile(r"\bph[\-_\.]|philippine", re.I), "PH"),
    (re.compile(r"\bmy[\-_\.]|malaysia|malay", re.I), "MY"),
]


def guess_country_from_proxy_url(proxy_url: str) -> str | None:
    """Try to extract ISO country code from proxy hostname/path hints.

    Returns 2-letter country code (e.g. "VN") or None if undetectable.
    Matches are case-insensitive against the full proxy URL string.
    """
    for pattern, country in _HOSTNAME_COUNTRY_HINTS:
        if pattern.search(proxy_url):
            return country
    return None


def parse_proxy_address(proxy_url: str) -> tuple[str, int]:
    """Extract (host, port) from proxy URL.

    Supports: http://, https://, socks4://, socks5://
    Raises ValueError if URL is malformed or port cannot be determined.
    """
    parsed = urlparse(proxy_url)
    host = parsed.hostname
    if not host:
        raise ValueError(f"Cannot parse proxy host from: {proxy_url!r}")

    port = parsed.port
    if port is None:
        scheme = (parsed.scheme or "http").lower()
        defaults = {
            "http": 8080,
            "https": 8080,
            "socks4": 1080,
            "socks5": 1080,
        }
        port = defaults.get(scheme, 8080)

    return host, port


async def check_proxy_connectivity(
    proxy_url: str,
    timeout_seconds: float = 5.0,
) -> tuple[bool, int]:
    """Test proxy reachability via TCP socket connection.

    Args:
        proxy_url: Full proxy URL, e.g. "http://user:pass@1.2.3.4:8080"
        timeout_seconds: Max time to wait for connection

    Returns:
        (reachable: bool, latency_ms: int)
        latency_ms is -1 if unreachable.
    """
    try:
        host, port = parse_proxy_address(proxy_url)
    except ValueError as exc:
        LOGGER.warning("proxy_parse_error", extra={"proxy": proxy_url, "error": str(exc)})
        return False, -1

    t0 = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout_seconds,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

        if latency_ms > SLOW_PROXY_THRESHOLD_MS:
            LOGGER.warning(
                "proxy_slow",
                extra={
                    "event": "proxy_slow",
                    "proxy_host": host,
                    "latency_ms": latency_ms,
                    "threshold_ms": SLOW_PROXY_THRESHOLD_MS,
                },
            )

        LOGGER.debug(
            "proxy_reachable",
            extra={"event": "proxy_reachable", "proxy_host": host, "port": port, "latency_ms": latency_ms},
        )
        return True, latency_ms

    except asyncio.TimeoutError:
        LOGGER.warning(
            "proxy_timeout",
            extra={
                "event": "proxy_timeout",
                "proxy_host": host,
                "port": port,
                "timeout_seconds": timeout_seconds,
            },
        )
        return False, -1
    except Exception as exc:
        LOGGER.warning(
            "proxy_connection_error",
            extra={"event": "proxy_connection_error", "proxy_host": host, "port": port, "error": str(exc)},
        )
        return False, -1
