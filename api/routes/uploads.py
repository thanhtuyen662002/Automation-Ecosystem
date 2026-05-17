from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from workers.handlers.tiktok._base import get_media_output_dir


router = APIRouter(prefix="/api/v1/uploads", tags=["uploads"])

_ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_MAX_PRODUCT_IMAGE_BYTES = 10 * 1024 * 1024


def _product_image_dir() -> Path:
    path = get_media_output_dir() / "uploads" / "product_images"
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.post("/product-image", status_code=status.HTTP_201_CREATED)
async def upload_product_image(file: UploadFile = File(...)) -> dict[str, str]:
    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported product image extension")
    if not str(file.content_type or "").lower().startswith("image/"):
        raise HTTPException(status_code=400, detail="Uploaded file must be an image")

    content = await file.read(_MAX_PRODUCT_IMAGE_BYTES + 1)
    if len(content) > _MAX_PRODUCT_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Product image must be 10MB or smaller")
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded image is empty")

    stored_name = f"{uuid4().hex}{suffix}"
    path = (_product_image_dir() / stored_name).resolve()
    path.write_bytes(content)

    return {
        "path": str(path),
        "url": f"/api/v1/uploads/product-image/{stored_name}",
        "filename": stored_name,
    }


@router.get("/product-image/{filename}")
async def get_product_image(filename: str) -> FileResponse:
    safe_name = Path(filename).name
    path = (_product_image_dir() / safe_name).resolve()
    upload_dir = _product_image_dir().resolve()
    if upload_dir not in path.parents or path.suffix.lower() not in _ALLOWED_IMAGE_EXTENSIONS or not path.is_file():
        raise HTTPException(status_code=404, detail="Product image not found")
    return FileResponse(path)
