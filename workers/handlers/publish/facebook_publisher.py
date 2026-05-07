"""
Facebook video publisher — STUB (not yet implemented).
"""
from __future__ import annotations

from typing import Any


async def publish_facebook_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Publish a video to Facebook using stored account session.

    NOT YET IMPLEMENTED — raises FatalDependencyError with clear message.
    """
    from workers.worker_runtime import FatalDependencyError
    raise FatalDependencyError(
        "Facebook publishing is not yet implemented. "
        "Implement workers/handlers/publish/facebook_publisher.py "
        "targeting https://www.facebook.com/"
    )
