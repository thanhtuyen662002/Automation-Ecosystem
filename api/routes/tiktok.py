"""
API route: POST /pipelines/tiktok
───────────────────────────────────
Creates a full TikTok content automation job (7 tasks) in a single atomic call.
Returns the standard JobResponse with all task IDs for client polling.

DAG:
  extract_product_info  (PENDING, no deps)
    └── search_tiktok   (depends_on: extract_product_info)
          └── select_videos (depends_on: search_tiktok)
                └── download_videos (depends_on: select_videos)
                      └── remake_video (depends_on: download_videos + extract_product_info)

  extract_product_info ──→ generate_content (depends_on: extract_product_info)
                                 └── generate_comment (depends_on: generate_content)

Parent results are forwarded via task payload using task_key references so handlers
can resolve them via resolve_parent_result(payload, key).
The pipeline endpoint embeds parent task results into child payloads by injecting
placeholder keys; the actual values are resolved by each handler from the DB result
at runtime — DB remains the single source of truth.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from api.dependencies import DatabaseDependency
from api.schemas import JobResponse, TikTokPipelineRequest

LOGGER = logging.getLogger("api.tiktok")

router = APIRouter(prefix="/pipelines/tiktok", tags=["tiktok-pipeline"])

# Task key constants for DAG wiring
_KEY_EXTRACT = "tiktok_extract_product_info"
_KEY_SEARCH = "tiktok_search"
_KEY_SELECT = "tiktok_select"
_KEY_DOWNLOAD = "tiktok_download"
_KEY_REMAKE = "tiktok_remake"
_KEY_CONTENT = "tiktok_content"
_KEY_COMMENT = "tiktok_comment"


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_tiktok_pipeline(
    request: TikTokPipelineRequest,
    database: DatabaseDependency,
) -> JobResponse:
    """
    Submit a TikTok content automation job.

    The system will:
    1. Extract product info (URL or image)
    2. Search TikTok for relevant videos
    3. Filter/rank videos
    4. Download top N videos
    5. Remix into a new video (FFmpeg)
    6. Generate caption + hashtags
    7. Generate natural comments

    Poll GET /jobs/{job_id} to track progress.
    All task results are stored in the 'result' JSON field on each task.
    """
    media_output_dir = os.environ.get("MEDIA_OUTPUT_DIR", "./media_output")
    min_views = request.min_views or int(os.environ.get("TIKTOK_MIN_VIEWS", "10000"))
    min_likes = request.min_likes or int(os.environ.get("TIKTOK_MIN_LIKES", "500"))
    top_n = request.top_n or int(os.environ.get("TIKTOK_TOP_N", "5"))

    # ── Build task list ───────────────────────────────────────────────────────
    # NOTE: 'depends_on' uses task_key strings; the database layer resolves them
    # to task UUIDs within the same job creation transaction.
    tasks: list[dict[str, Any]] = [
        # 1. Extract product info
        {
            "task_type": "tiktok.extract_product_info",
            "task_key": _KEY_EXTRACT,
            "payload": {
                "product_url": request.product_url,
                "product_image_path": request.product_image_path,
            },
            "metadata": {"pipeline": "tiktok", "step": 1},
            "max_retries": 2,
            "depends_on": [],
        },
        # 2. Search TikTok — keywords resolved at runtime from DB result
        {
            "task_type": "tiktok.search_tiktok",
            "task_key": _KEY_SEARCH,
            "payload": {
                # Handler resolves keywords from parent result in DB
                "max_results": int(os.environ.get("TIKTOK_SEARCH_MAX_RESULTS", "50")),
                # parent_result_key tells the handler where to look in parent result
                "_parent_task_key": _KEY_EXTRACT,
                "_parent_result_fields": ["keywords"],
            },
            "metadata": {"pipeline": "tiktok", "step": 2},
            "max_retries": 3,
            "depends_on": [_KEY_EXTRACT],
        },
        # 3. Select best videos
        {
            "task_type": "tiktok.select_videos",
            "task_key": _KEY_SELECT,
            "payload": {
                "min_views": min_views,
                "min_likes": min_likes,
                "min_duration": request.min_duration,
                "max_duration": request.max_duration,
                "top_n": top_n,
                "_parent_task_key": _KEY_SEARCH,
                "_parent_result_fields": ["videos"],
            },
            "metadata": {"pipeline": "tiktok", "step": 3},
            "max_retries": 1,
            "depends_on": [_KEY_SEARCH],
        },
        # 4. Download videos
        {
            "task_type": "tiktok.download_videos",
            "task_key": _KEY_DOWNLOAD,
            "payload": {
                "_parent_task_key": _KEY_SELECT,
                "_parent_result_fields": ["selected_videos"],
                # job_id injected post-creation — handler reads from payload or falls back
            },
            "metadata": {"pipeline": "tiktok", "step": 4},
            "max_retries": 2,
            "depends_on": [_KEY_SELECT],
        },
        # 5. Remake video (depends on download + extract for hook_text)
        {
            "task_type": "tiktok.remake_video",
            "task_key": _KEY_REMAKE,
            "payload": {
                "add_grain": request.add_grain,
                "bgm_path": request.bgm_path,
                "_parent_task_key": _KEY_DOWNLOAD,
                "_parent_result_fields": ["video_paths"],
                "_parent_task_key_2": _KEY_EXTRACT,
                "_parent_result_fields_2": ["title"],
            },
            "metadata": {"pipeline": "tiktok", "step": 5},
            "max_retries": 1,
            # Long timeout hint (picked up by worker if it checks metadata)
            "depends_on": [_KEY_DOWNLOAD, _KEY_EXTRACT],
        },
        # 6. Generate caption + hashtags
        {
            "task_type": "tiktok.generate_content",
            "task_key": _KEY_CONTENT,
            "payload": {
                "_parent_task_key": _KEY_EXTRACT,
                "_parent_result_fields": ["title", "description", "keywords"],
            },
            "metadata": {"pipeline": "tiktok", "step": 6},
            "max_retries": 3,
            "depends_on": [_KEY_EXTRACT],
        },
        # 7. Generate comments
        {
            "task_type": "tiktok.generate_comment",
            "task_key": _KEY_COMMENT,
            "payload": {
                "count": request.comment_count,
                "_parent_task_key": _KEY_CONTENT,
                "_parent_result_fields": ["caption"],
                "_parent_task_key_2": _KEY_EXTRACT,
                "_parent_result_fields_2": ["title", "keywords"],
            },
            "metadata": {"pipeline": "tiktok", "step": 7},
            "max_retries": 3,
            "depends_on": [_KEY_CONTENT, _KEY_EXTRACT],
        },
    ]

    try:
        detail = await database.create_job(
            workflow_name="tiktok_content_pipeline",
            tasks=tasks,
            job_key=request.job_key,
            priority=request.priority,
            input_data={
                "product_url": request.product_url,
                "product_image_path": request.product_image_path,
            },
            metadata={
                "pipeline": "tiktok",
                "top_n": top_n,
                "min_views": min_views,
                "min_likes": min_likes,
            },
        )
    except Exception as exc:
        LOGGER.error(
            "tiktok_pipeline_create_failed",
            extra={
                "event": "tiktok_pipeline_create_failed",
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
        )
        raise

    LOGGER.info(
        "tiktok_pipeline_created",
        extra={
            "event": "tiktok_pipeline_created",
            "job_id": str(detail.job.id),
            "task_count": len(detail.tasks),
        },
    )
    return JobResponse.from_detail(detail)
