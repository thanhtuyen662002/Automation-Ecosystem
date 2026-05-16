from __future__ import annotations

import sqlite3
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from core import ai_key_store


router = APIRouter(prefix="/admin/ai", tags=["Admin AI Settings"])


def require_admin(request: Request) -> None:
    """
    Local admin guard.

    TODO: replace this with the app's canonical RBAC dependency when the UI
    grows a first-class role/session system. Today the LicenseGuard has already
    validated local activation; if it exposes a role, enforce admin.
    """
    role = getattr(request.state, "license_role", None)
    if role and role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")


AdminDependency = Depends(require_admin)


class ProviderCreateRequest(BaseModel):
    provider: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    base_url: str | None = None
    enabled: bool = True
    priority: int = 100


class ProviderUpdateRequest(BaseModel):
    provider: str | None = None
    display_name: str | None = None
    base_url: str | None = None
    enabled: bool | None = None
    priority: int | None = None


class KeyCreateRequest(BaseModel):
    label: str = Field(min_length=1)
    raw_key: str = Field(min_length=1)
    enabled: bool = True
    priority: int = 100


class KeyUpdateRequest(BaseModel):
    label: str | None = None
    raw_key: str | None = None
    enabled: bool | None = None
    priority: int | None = None


class ModelCreateRequest(BaseModel):
    model_name: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    enabled: bool = True
    is_default: bool = False
    max_tokens: int | None = Field(default=None, ge=1)
    temperature_default: float | None = Field(default=None, ge=0.0, le=2.0)
    priority: int = 100


class ModelUpdateRequest(BaseModel):
    model_name: str | None = None
    display_name: str | None = None
    enabled: bool | None = None
    is_default: bool | None = None
    max_tokens: int | None = Field(default=None, ge=1)
    temperature_default: float | None = Field(default=None, ge=0.0, le=2.0)
    priority: int | None = None


class TestAiRequest(BaseModel):
    provider_id: str | None = None
    provider: str | None = None
    model_id: str | None = None
    model_name: str | None = None
    key_id: str | None = None
    prompt: str = "Reply with a short confirmation that the AI provider is working."
    max_tokens: int = Field(default=128, ge=1, le=4096)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)


def _patch_fields(model: BaseModel) -> dict[str, Any]:
    data = model.model_dump()
    return {key: data[key] for key in model.model_fields_set}


@router.get("/providers", dependencies=[AdminDependency])
async def list_ai_providers() -> dict[str, Any]:
    return {"items": ai_key_store.list_providers()}


@router.post("/providers", status_code=status.HTTP_201_CREATED, dependencies=[AdminDependency])
async def create_ai_provider(request: ProviderCreateRequest) -> dict[str, Any]:
    return ai_key_store.create_provider(**request.model_dump())


@router.patch("/providers/{provider_id}", dependencies=[AdminDependency])
async def update_ai_provider(provider_id: str, request: ProviderUpdateRequest) -> dict[str, Any]:
    provider = ai_key_store.update_provider(provider_id, **_patch_fields(request))
    if provider is None:
        raise HTTPException(status_code=404, detail="AI provider not found")
    return provider


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[AdminDependency])
async def delete_ai_provider(provider_id: str) -> None:
    if not ai_key_store.delete_provider(provider_id):
        raise HTTPException(status_code=404, detail="AI provider not found")


@router.post("/providers/{provider_id}/keys", status_code=status.HTTP_201_CREATED, dependencies=[AdminDependency])
async def create_ai_key(provider_id: str, request: KeyCreateRequest) -> dict[str, Any]:
    try:
        return ai_key_store.create_key(provider_id=provider_id, **request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=404, detail="AI provider not found") from exc


@router.patch("/keys/{key_id}", dependencies=[AdminDependency])
async def update_ai_key(key_id: str, request: KeyUpdateRequest) -> dict[str, Any]:
    patch = _patch_fields(request)
    if isinstance(patch.get("raw_key"), str) and not patch["raw_key"].strip():
        patch.pop("raw_key")
    try:
        key = ai_key_store.update_key(key_id, **patch)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if key is None:
        raise HTTPException(status_code=404, detail="AI API key not found")
    return key


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[AdminDependency])
async def delete_ai_key(key_id: str) -> None:
    if not ai_key_store.delete_key(key_id):
        raise HTTPException(status_code=404, detail="AI API key not found")


@router.post("/providers/{provider_id}/models", status_code=status.HTTP_201_CREATED, dependencies=[AdminDependency])
async def create_ai_model(provider_id: str, request: ModelCreateRequest) -> dict[str, Any]:
    try:
        return ai_key_store.create_model(provider_id=provider_id, **request.model_dump())
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=404, detail="AI provider not found") from exc


@router.patch("/models/{model_id}", dependencies=[AdminDependency])
async def update_ai_model(model_id: str, request: ModelUpdateRequest) -> dict[str, Any]:
    model = ai_key_store.update_model(model_id, **_patch_fields(request))
    if model is None:
        raise HTTPException(status_code=404, detail="AI model not found")
    return model


@router.delete("/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[AdminDependency])
async def delete_ai_model(model_id: str) -> None:
    if not ai_key_store.delete_model(model_id):
        raise HTTPException(status_code=404, detail="AI model not found")


@router.post("/test", dependencies=[AdminDependency])
async def test_ai_provider(request: TestAiRequest) -> dict[str, Any]:
    from core.ai_router import generate_text

    preferred_provider = request.provider_id or request.provider
    preferred_model = request.model_id or request.model_name
    started_at = time.monotonic()
    try:
        text = await generate_text(
            request.prompt,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
            preferred_key_id=request.key_id,
        )
    except RuntimeError as exc:
        status_code = 400 if "No usable AI provider key configured" in str(exc) else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return {
        "ok": True,
        "text": text,
        "elapsed_ms": int((time.monotonic() - started_at) * 1000),
    }
