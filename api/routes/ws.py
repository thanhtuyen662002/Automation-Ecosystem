"""
api/routes/ws.py — WebSocket live feed for Execution Brain events.

Endpoint: GET /api/v1/ws/brain

Broadcasts JSON messages of the form:
    { "event": "decision_made" | "publish_event" | "metric_update", "data": {...}, "ts": float }

Usage from content_brain.py:
    from api.routes.ws import broadcast
    await broadcast("decision_made", {...})
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

LOGGER = logging.getLogger("api.ws")
router = APIRouter(prefix="/ws", tags=["WebSocket"])

# ── Fan-out manager ───────────────────────────────────────────────────────────

class _ConnectionManager:
    def __init__(self) -> None:
        self._connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.append(ws)
        LOGGER.info("ws_connected total=%d", len(self._connections))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections = [c for c in self._connections if c is not ws]
        LOGGER.info("ws_disconnected total=%d", len(self._connections))

    async def broadcast(self, event: str, data: dict[str, Any]) -> None:
        msg = json.dumps({"event": event, "data": data, "ts": time.time()})
        dead: list[WebSocket] = []
        async with self._lock:
            targets = list(self._connections)
        for ws in targets:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                self._connections = [c for c in self._connections if c not in dead]

    @property
    def count(self) -> int:
        return len(self._connections)


_manager = _ConnectionManager()


async def broadcast(event: str, data: dict[str, Any]) -> None:
    """Public broadcast helper called from other routes."""
    await _manager.broadcast(event, data)


# ── WebSocket endpoint ────────────────────────────────────────────────────────

@router.websocket("/brain")
async def brain_ws(ws: WebSocket) -> None:
    """
    Live event stream for the Execution Brain.

    Connect with: ws://localhost:8000/api/v1/ws/brain

    Server will push:
        { "event": "decision_made",  "data": { content_id, decision, final_score, ... } }
        { "event": "publish_event",  "data": { content_id, action, ... } }
        { "event": "metric_update",  "data": { ... } }
        { "event": "ping",           "data": { "clients": N } }

    Client can send:
        { "type": "ping" }  → server echoes pong
    """
    await _manager.connect(ws)
    # Send welcome message with current client count
    await ws.send_text(json.dumps({
        "event": "connected",
        "data": {"clients": _manager.count},
        "ts": time.time(),
    }))
    try:
        while True:
            # Keep connection alive; handle client pings
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=30.0)
                msg = json.loads(raw)
                if msg.get("type") == "ping":
                    await ws.send_text(json.dumps({
                        "event": "pong",
                        "data": {"clients": _manager.count},
                        "ts": time.time(),
                    }))
            except asyncio.TimeoutError:
                # Server-side keepalive ping every 30s
                await ws.send_text(json.dumps({
                    "event": "ping",
                    "data": {"clients": _manager.count},
                    "ts": time.time(),
                }))
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        LOGGER.warning("ws_error %s", exc)
    finally:
        await _manager.disconnect(ws)
