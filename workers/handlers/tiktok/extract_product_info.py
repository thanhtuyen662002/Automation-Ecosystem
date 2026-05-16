"""
Handler: tiktok.extract_product_info
─────────────────────────────────────
Input payload:
  product_url:         str | None  – public product page URL
  product_image_path:  str | None  – local path to a product image
  (At least one of the above must be provided.)

Output result:
  title:        str
  description:  str
  keywords:     list[str]
  ok:           bool

AI provider: Google Gemini configured in Settings -> AI Providers.
Vision supported: passes PIL.Image for product_image_path inputs.
Runs Gemini SDK in thread executor to stay non-blocking.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from workers.handlers.tiktok._base import (
    check_already_processed,
    fetch_url_text,
    random_jitter,
)
from core.ai_key_store import get_enabled_candidates, mark_key_failure, mark_key_success

LOGGER = logging.getLogger("workers.handlers.tiktok.extract_product_info")

_SYSTEM_PROMPT = (
    "You are a product analyst. Extract key information from the product page or image provided. "
    "Return ONLY a valid JSON object with exactly these keys:\n"
    "  title        (string, ≤80 chars)\n"
    "  description  (string, 2–4 sentences)\n"
    "  keywords     (array of 5–10 short marketing keywords)\n"
    "Do not include any other text outside the JSON object."
)


async def extract_tiktok_shop_product_info(product_url: str, account_id: str | None = None) -> dict[str, Any]:
    from core.browser_providers import make_browser_provider
    from database.database import AutomationDatabase, RetryConfig
    from playwright.async_api import async_playwright
    import json
    import os

    result = {
        "title": "",
        "description": "",
        "keywords": [],
        "ld_json": None,
        "og_title": "",
        "meta_description": "",
        "page_title": "",
        "ok": False,
        "error": None
    }

    if not account_id:
        result["error"] = "No account_id provided for TikTok Shop extraction"
        return result

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        result["error"] = "DATABASE_URL not set"
        return result

    database = AutomationDatabase(db_url, retry_config=RetryConfig())
    await database.open()

    try:
        account = await database.get_account(account_id)
        if not account:
            result["error"] = "Account not found"
            return result
        
        metadata = account.get("metadata")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {}
        elif not isinstance(metadata, dict):
            metadata = {}
            
        session = await database.get_account_session(account_id) or {}
        
        provider = make_browser_provider(
            {**account, "account_id": account_id, "metadata": metadata},
            session=session,
        )

        async with async_playwright() as pw:
            async with provider.open_publisher_context(pw, headless=False) as (context, page, _):
                try:
                    await page.goto(product_url, wait_until="domcontentloaded", timeout=30_000)
                    await asyncio.sleep(2)
                    
                    try:
                        ld_json_text = await page.evaluate('''() => {
                            const scripts = document.querySelectorAll('script[type="application/ld+json"]');
                            for (const s of scripts) {
                                if (s.innerText.includes('Product')) return s.innerText;
                            }
                            return null;
                        }''')
                        if ld_json_text:
                            result["ld_json"] = json.loads(ld_json_text)
                    except Exception:
                        pass
                        
                    try:
                        result["og_title"] = await page.evaluate('''() => {
                            const og = document.querySelector('meta[property="og:title"]') || document.querySelector('meta[name="twitter:title"]');
                            return og ? og.content : "";
                        }''')
                    except Exception:
                        pass
                        
                    try:
                        result["meta_description"] = await page.evaluate('''() => {
                            const desc = document.querySelector('meta[name="description"]') || document.querySelector('meta[property="og:description"]');
                            return desc ? desc.content : "";
                        }''')
                    except Exception:
                        pass
                        
                    try:
                        result["page_title"] = await page.title()
                    except Exception:
                        pass
                        
                    try:
                        title_from_dom = await page.evaluate('''() => {
                            const el = document.querySelector('h1') || document.querySelector('[class*="title"]') || document.querySelector('[class*="name"]');
                            return el ? el.innerText : "";
                        }''')
                        if title_from_dom:
                            result["title"] = title_from_dom
                    except Exception:
                        pass
                        
                    result["ok"] = True
                except Exception as exc:
                    result["error"] = str(exc)
    finally:
        await database.close()

    return result


async def extract_product_info_handler(payload: dict[str, Any]) -> dict[str, Any]:
    # ── Idempotency guard ─────────────────────────────────────────────────────
    if (cached := check_already_processed(payload)) is not None:
        return cached

    product_url: str | None = payload.get("product_url") or None
    product_image_path: str | None = payload.get("product_image_path") or None
    account_id: str | None = payload.get("account_id") or None

    if not product_url and not product_image_path:
        raise ValueError("extract_product_info requires 'product_url' or 'product_image_path'")

    await random_jitter(0.5, 2.0)

    is_tiktok_shop = False
    if product_url and ("shop.tiktok.com/view/product" in product_url or "www.tiktok.com/shop/product" in product_url):
        is_tiktok_shop = True

    LOGGER.info(
        "extract_product_info_start",
        extra={
            "event": "extract_product_info_start",
            "has_url": bool(product_url),
            "has_image": bool(product_image_path),
            "is_tiktok_shop": is_tiktok_shop,
        },
    )

    # ── Lazy imports (keep startup fast) ─────────────────────────────────────
    import google.generativeai as genai  # type: ignore[import]

    generation_config = genai.types.GenerationConfig(  # type: ignore[attr-defined]
        temperature=0.3,
        max_output_tokens=512,
    )

    # ── Build content list ────────────────────────────────────────────────────
    content: list[Any] = []

    # Vision: attach PIL image when a local file path is given
    if product_image_path:
        from PIL import Image  # type: ignore[import]

        image_path = Path(product_image_path).expanduser().resolve()
        if not image_path.exists():
            raise FileNotFoundError(f"product_image_path does not exist: {image_path}")
        pil_img = Image.open(image_path)
        content.append(pil_img)

    import json
    fetch_url_success = False
    fetched_text_length = 0
    shop_data = None

    if product_url:
        if is_tiktok_shop and account_id:
            shop_data = await extract_tiktok_shop_product_info(product_url, account_id)
            if shop_data.get("ok"):
                fetch_url_success = True
                page_text = f"URL: {product_url}\n"
                if shop_data.get("page_title"): page_text += f"Page Title: {shop_data['page_title']}\n"
                if shop_data.get("og_title"): page_text += f"Meta Title: {shop_data['og_title']}\n"
                if shop_data.get("title"): page_text += f"DOM Title: {shop_data['title']}\n"
                if shop_data.get("meta_description"): page_text += f"Meta Desc: {shop_data['meta_description']}\n"
                if shop_data.get("ld_json"): page_text += f"JSON-LD: {json.dumps(shop_data['ld_json'])}\n"
                fetched_text_length = len(page_text)
                content.append(f"Product page data:\n\n{page_text}")
            else:
                LOGGER.warning(
                    "tiktok_shop_extract_failed",
                    extra={"event": "tiktok_shop_extract_failed", "url": product_url, "error": shop_data.get("error")}
                )

        if not fetch_url_success:
            try:
                page_text = await fetch_url_text(product_url)
                page_text = page_text[:8000]
                fetch_url_success = True
                fetched_text_length = len(page_text)
                content.append(f"Product page content:\n\n{page_text}")
            except Exception as exc:
                LOGGER.warning(
                    "fetch_url_failed",
                    extra={"event": "fetch_url_failed", "url": product_url, "error": str(exc)},
                )
                page_text = f"Product URL: {product_url}"
                content.append(f"Product page content:\n\n{page_text}")

    if not content:
        content.append("Analyze the product and return the JSON.")

    # ── Call Gemini in thread executor (SDK is synchronous) ───────────────────
    candidates = get_enabled_candidates(preferred_provider="gemini")
    if not candidates:
        raise RuntimeError(
            "No usable AI provider key configured. Open Settings -> AI Providers and add an enabled Gemini key/model."
        )

    loop = asyncio.get_event_loop()
    raw_text = ""
    errors: list[str] = []
    for candidate in candidates:
        try:
            genai.configure(api_key=candidate.raw_key)
            model = genai.GenerativeModel(
                model_name=candidate.model_name,
                system_instruction=_SYSTEM_PROMPT,
            )
            response = await loop.run_in_executor(
                None,
                lambda: model.generate_content(content, generation_config=generation_config),
            )
            raw_text = (response.text or "").strip()
            if candidate.key_id:
                mark_key_success(candidate.key_id)
            break
        except Exception as exc:
            if candidate.key_id:
                mark_key_failure(candidate.key_id, exc)
            errors.append(f"{candidate.provider}/{candidate.model_name}: {exc}")
            LOGGER.warning(
                "extract_product_info_ai_candidate_failed",
                extra={
                    "event": "extract_product_info_ai_candidate_failed",
                    "provider": candidate.provider,
                    "model": candidate.model_name,
                    "key_preview": candidate.key_preview,
                    "error": str(exc)[:300],
                },
            )
    else:
        raise RuntimeError(
            "All configured AI providers failed. Errors:\n" + "\n".join(f"  - {error}" for error in errors)
        )
    parsed = _parse_json_response(raw_text)

    title: str = str(parsed.get("title", "")).strip() or "Unknown Product"
    description: str = str(parsed.get("description", "")).strip()
    keywords: list[str] = [str(k).strip() for k in parsed.get("keywords", []) if k]

    if not keywords:
        keywords = [w.lower() for w in title.split() if len(w) > 3][:8]

    is_fallback_unknown = title.lower() == "unknown product" or title.strip() == ""
    
    junk_set = {"unknown", "product", "item", "shop", "tiktok", "tiktok shop"}
    meaningful_keywords = [k for k in keywords if k.lower() not in junk_set and len(k) > 2]
    
    LOGGER.info(
        "extract_product_info_done",
        extra={
            "event": "extract_product_info_done",
            "product_url": product_url,
            "fetch_url_success": fetch_url_success,
            "fetched_text_length": fetched_text_length,
            "parsed_title": title,
            "parsed_keywords": keywords,
            "is_fallback_unknown": is_fallback_unknown,
            "raw_ai_text_preview": raw_text[:200] if raw_text else "",
        },
    )

    if is_fallback_unknown or len(meaningful_keywords) < 3:
        raise RuntimeError(
            f"Could not extract meaningful product title/keywords from product_url: {product_url}. "
            f"Got title='{title}', keywords={keywords}"
        )

    result = {
        "title": title if not is_fallback_unknown else " ".join(meaningful_keywords),
        "description": description,
        "keywords": meaningful_keywords if len(meaningful_keywords) >= 3 else keywords,
        "ok": True,
    }

    return result


def _parse_json_response(text: str) -> dict[str, Any]:
    """Extract and parse the first JSON object found in the model response."""
    import json

    # Strip markdown code fences if present
    text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()

    # Find first {...}
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # Last resort — attempt full parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        LOGGER.warning(
            "json_parse_failed",
            extra={"event": "json_parse_failed", "raw_text_preview": text[:200]},
        )
        return {}
