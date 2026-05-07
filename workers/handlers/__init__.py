from workers.handlers.ai import ai_handler
from workers.handlers.browser import browser_handler
from workers.handlers.media import media_handler
from workers.handlers.tiktok import register_tiktok_handlers
from workers.handlers.publish import (
    publish_tiktok_handler,
    publish_youtube_handler,
    publish_facebook_handler,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from workers.worker_runtime import TaskRegistry


def register_default_handlers(registry: "TaskRegistry") -> None:
    # Generic handlers
    registry.register("ai", ai_handler)
    registry.register("browser", browser_handler)
    registry.register("media", media_handler)

    # TikTok content pipeline handlers
    register_tiktok_handlers(registry)

    # Platform publish handlers (Playwright-based)
    registry.register("publish_tiktok", publish_tiktok_handler)
    registry.register("publish_youtube", publish_youtube_handler)
    registry.register("publish_facebook", publish_facebook_handler)


__all__ = [
    "ai_handler",
    "browser_handler",
    "media_handler",
    "register_tiktok_handlers",
    "publish_tiktok_handler",
    "publish_youtube_handler",
    "publish_facebook_handler",
    "register_default_handlers",
]
