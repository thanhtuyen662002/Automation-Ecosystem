from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any


async def media_handler(payload: dict[str, Any]) -> dict[str, Any]:
    input_path_raw = str(payload.get("input_path") or "").strip()
    if not input_path_raw:
        raise ValueError("media task requires payload.input_path")
    input_path = Path(input_path_raw).expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"input_path does not exist: {input_path}")
    if not input_path.is_file():
        raise ValueError(f"input_path is not a file: {input_path}")

    stat_result = await asyncio.to_thread(input_path.stat)
    checksum = await asyncio.to_thread(_sha256_file, input_path)
    return {
        "handler": "media",
        "input_path": str(input_path),
        "file_name": input_path.name,
        "size_bytes": stat_result.st_size,
        "modified_at": stat_result.st_mtime,
        "sha256": checksum,
        "ok": True,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
