"""
TikTok content automation pipeline — handler package.

Task types registered:
  tiktok.extract_product_info
  tiktok.search_tiktok
  tiktok.select_videos
  tiktok.download_videos
  tiktok.remake_video
  tiktok.generate_content
  tiktok.generate_comment
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from workers.handlers.tiktok.download_videos import download_videos_handler
from workers.handlers.tiktok.extract_product_info import extract_product_info_handler
from workers.handlers.tiktok.generate_comment import generate_comment_handler
from workers.handlers.tiktok.generate_content import generate_content_handler
from workers.handlers.tiktok.remake_video import remake_video_handler
from workers.handlers.tiktok.search_tiktok import search_tiktok_handler
from workers.handlers.tiktok.select_videos import select_videos_handler

if TYPE_CHECKING:
    from workers.worker_runtime import TaskRegistry

_TIKTOK_HANDLERS: dict[str, object] = {
    "tiktok.extract_product_info": extract_product_info_handler,
    "tiktok.search_tiktok": search_tiktok_handler,
    "tiktok.select_videos": select_videos_handler,
    "tiktok.download_videos": download_videos_handler,
    "tiktok.remake_video": remake_video_handler,
    "tiktok.generate_content": generate_content_handler,
    "tiktok.generate_comment": generate_comment_handler,
}


def register_tiktok_handlers(registry: "TaskRegistry") -> None:
    """Register all TikTok pipeline task handlers into the given registry."""
    for task_type, handler in _TIKTOK_HANDLERS.items():
        registry.register(task_type, handler)  # type: ignore[arg-type]


__all__ = [
    "register_tiktok_handlers",
    "extract_product_info_handler",
    "search_tiktok_handler",
    "select_videos_handler",
    "download_videos_handler",
    "remake_video_handler",
    "generate_content_handler",
    "generate_comment_handler",
]
