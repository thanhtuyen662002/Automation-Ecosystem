"""
YouTube video publisher — STUB (not yet implemented).

Architecture is in place. Implement upload flow targeting YouTube Studio.
"""
from __future__ import annotations

from typing import Any


async def publish_youtube_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Publish a video to YouTube using stored account session.

    NOT YET IMPLEMENTED — raises FatalDependencyError with clear message.
    """
    from workers.worker_runtime import FatalDependencyError
    raise FatalDependencyError(
        "YouTube publishing is not yet implemented. "
        "Implement workers/handlers/publish/youtube_publisher.py "
        "targeting https://studio.youtube.com/"
    )
