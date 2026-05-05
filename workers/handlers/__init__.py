from workers.handlers.ai import ai_handler
from workers.handlers.browser import browser_handler
from workers.handlers.media import media_handler
from workers.handlers.tiktok import register_tiktok_handlers
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from workers.worker_runtime import TaskRegistry


def register_default_handlers(registry: "TaskRegistry") -> None:
    registry.register("ai", ai_handler)
    registry.register("browser", browser_handler)
    registry.register("media", media_handler)
    register_tiktok_handlers(registry)


__all__ = [
    "ai_handler",
    "browser_handler",
    "media_handler",
    "register_tiktok_handlers",
    "register_default_handlers",
]
