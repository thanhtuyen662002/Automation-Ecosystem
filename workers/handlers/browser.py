from __future__ import annotations

import hashlib
from typing import Any

import httpx


async def browser_handler(payload: dict[str, Any]) -> dict[str, Any]:
    url = str(payload.get("url") or "").strip()
    if not url:
        raise ValueError("browser task requires payload.url")
    if not url.startswith(("http://", "https://")):
        raise ValueError("payload.url must start with http:// or https://")

    timeout_seconds = float(payload.get("timeout_seconds", 15))
    if timeout_seconds <= 0:
        raise ValueError("payload.timeout_seconds must be > 0")

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout_seconds) as client:
        response = await client.get(url)

    body = response.content
    return {
        "handler": "browser",
        "url": str(response.url),
        "status_code": response.status_code,
        "content_type": response.headers.get("content-type"),
        "content_length": len(body),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "ok": 200 <= response.status_code < 400,
    }
