from __future__ import annotations

from io import BytesIO

import pytest
from starlette.datastructures import Headers, UploadFile


@pytest.mark.asyncio
async def test_upload_product_image_writes_to_media_output(tmp_path, monkeypatch):
    from api.routes.uploads import upload_product_image

    monkeypatch.setenv("MEDIA_OUTPUT_DIR", str(tmp_path))
    upload = UploadFile(
        file=BytesIO(b"\x89PNG\r\n\x1a\nimage"),
        filename="product.png",
        headers=Headers({"content-type": "image/png"}),
    )

    result = await upload_product_image(upload)

    assert result["filename"].endswith(".png")
    assert result["url"].startswith("/api/v1/uploads/product-image/")
    assert result["path"].startswith(str(tmp_path))
