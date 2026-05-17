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
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any

from workers.handlers.tiktok._base import (
    check_already_processed,
    fetch_url_text,
    random_jitter,
)
from core.ai_key_store import get_enabled_candidates, mark_key_failure, mark_key_success
from workers.worker_runtime import FatalDependencyError

LOGGER = logging.getLogger("workers.handlers.tiktok.extract_product_info")

_SYSTEM_PROMPT = (
    "You are a product analyst. Extract key information from the product page or image provided. "
    "Return ONLY a valid JSON object with exactly these keys:\n"
    "  title        (string, full product title, <=220 chars)\n"
    "  description  (string, 2–4 sentences)\n"
    "  keywords     (array of 5–10 short marketing keywords)\n"
    "Do not include any other text outside the JSON object."
)

_JUNK_KEYWORDS = {"unknown", "product", "item", "shop", "tiktok", "tiktok shop"}
_TITLE_GENERIC_RE = re.compile(
    r"^(?:tiktok\s*shop|tiktok|shop|product|unknown\s*product|item|not\s*found|access\s*denied|just\s*a\s*moment)$",
    re.IGNORECASE,
)
_VIETNAMESE_STOPWORDS = {
    "và",
    "của",
    "cho",
    "với",
    "một",
    "các",
    "trong",
    "là",
    "tặng",
    "siêu",
    "xu",
    "hướng",
}
_PRODUCT_NOUN_HINTS = {
    "khăn",
    "giấy",
    "rút",
    "dây",
    "thùng",
    "bịch",
    "combo",
    "mềm",
    "mịn",
    "lớp",
    "tờ",
    "hộp",
    "gói",
    "bộ",
    "chai",
    "topgia",
    "tiểu",
    "hạ",
    "bột",
    "gỗ",
}

_TIKTOK_SHOP_DOM_EXTRACTOR = r"""() => {
    const normalize = (value) => String(value || "")
        .replace(/\u00a0/g, " ")
        .replace(/[ \t]+/g, " ")
        .trim();
    const bodyText = normalize(document.body ? document.body.innerText : "");
    const rawLines = bodyText
        .split(/\n+/)
        .map(normalize)
        .filter(Boolean);
    const priceMatches = [...bodyText.matchAll(/₫\s*[0-9][0-9.]*/g)].map((m) => normalize(m[0]));
    const soldMatch = bodyText.match(/([0-9.,]+[KkMm]?|[0-9]+)\s*(đã được bán|sold)/i);
    const ratingMatch = bodyText.match(/(?:^|\s)([0-5](?:[.,]\d)?)\s*(?:\/\s*5)?\s*(?:\n|\s|[0-9.,]+)*(?:đánh giá|rating|ratings)/i)
        || bodyText.match(/(?:đánh giá|rating|ratings)[^\d]{0,20}([0-5](?:[.,]\d)?)/i);
    const breadcrumbNodes = Array.from(document.querySelectorAll('a, [class*="breadcrumb"], [class*="Breadcrumb"]'));
    const breadcrumbs = [];
    for (const node of breadcrumbNodes) {
        const text = normalize(node.innerText || node.textContent || "");
        if (text && text.length >= 2 && text.length <= 90 && !breadcrumbs.includes(text)) {
            breadcrumbs.push(text);
        }
        if (breadcrumbs.length >= 12) break;
    }
    const priceLineIndexes = new Set();
    rawLines.forEach((line, index) => {
        if (/₫\s*[0-9][0-9.]*/.test(line)) priceLineIndexes.add(index);
    });
    const navOrButtonRe = /^(?:TikTok Shop|TikTok|Dành cho bạn|Đang theo dõi|Tải ứng dụng|Đăng nhập|Đăng ký|Giỏ hàng|Mua ngay|Thêm vào giỏ hàng|Chat|Theo dõi|Chia sẻ|Báo cáo|Trang chủ|Sản phẩm|Cửa hàng|Tìm kiếm)$/i;
    const badLineRe = /(mua ngay|thêm vào giỏ hàng|đăng nhập|đăng ký|chính sách|vận chuyển|trả hàng|xem thêm|đã được bán|sold|đánh giá|rating|₫\s*[0-9])/i;
    const productSignalRe = /(khăn|giấy|thùng|bịch|combo|tặng|mềm|mịn|lớp|tờ|hộp|gói|bộ|set|chai|sản phẩm|topgia|tiểu hạ)/i;
    const scored = [];
    rawLines.forEach((line, index) => {
        if (line.length < 9 || line.length > 260) return;
        if (navOrButtonRe.test(line)) return;
        if (/^₫\s*[0-9.]+$/.test(line)) return;
        if (/^[0-9.,]+\s*(đã được bán|sold)$/i.test(line)) return;
        if (badLineRe.test(line) && line.length < 35) return;
        let score = 0;
        if (line.length > 30) score += 45;
        if (line.length > 60) score += 20;
        if (productSignalRe.test(line)) score += 45;
        if (/[\[\]()]/.test(line)) score += 8;
        for (const priceIndex of priceLineIndexes) {
            const distance = Math.abs(index - priceIndex);
            if (distance <= 5) score += Math.max(6, 30 - distance * 4);
        }
        if (index <= 20 && priceLineIndexes.size) score += 8;
        if (badLineRe.test(line)) score -= 20;
        scored.push({ line, score, index });
    });
    scored.sort((a, b) => b.score - a.score || b.line.length - a.line.length);
    const title = scored.length ? scored[0].line : "";
    let shopName = "";
    const sellerMatch = bodyText.match(/Do\s+(.{2,80}?)\s+bán/i);
    if (sellerMatch) {
        shopName = normalize(sellerMatch[1]);
    } else if (title) {
        const titleIndex = rawLines.indexOf(title);
        const shopLineRe = /shop|store|official|mall|bán/i;
        for (let i = titleIndex + 1; i < Math.min(rawLines.length, titleIndex + 8); i += 1) {
            const line = rawLines[i];
            if (line && line.length >= 2 && line.length <= 80 && shopLineRe.test(line) && !/₫|đã được bán|đánh giá/i.test(line)) {
                shopName = line;
                break;
            }
        }
    }
    const descriptionLines = scored
        .map((item) => item.line)
        .filter((line) => line !== title && line.length > 20)
        .slice(0, 4);
    return {
        title,
        description: descriptionLines.join("\n"),
        price: priceMatches[0] || "",
        rating: ratingMatch ? normalize(ratingMatch[1] || ratingMatch[0]) : "",
        sold_count: soldMatch ? normalize(soldMatch[0]) : "",
        shop_name: shopName,
        breadcrumbs,
        candidate_lines: scored.slice(0, 20).map((item) => item.line),
        body_text_preview: bodyText.slice(0, 2500),
        body_text_length: bodyText.length,
    };
}"""


def clean_product_title(raw_title: Any, *, max_length: int = 220) -> str:
    title = re.sub(r"\s+", " ", str(raw_title or "")).strip()
    if not title:
        return ""
    title = re.sub(r"(?i)\s*\|\s*tiktok\s*shop.*$", "", title)
    title = re.sub(r"(?i)^tiktok\s*shop\s*[:|\-–—]*\s*", "", title)
    title = re.sub(r"(?i)\s*[:|\-–—]*\s*tiktok\s*shop\s*$", "", title)
    title = re.sub(r"₫\s*[0-9][0-9.]*", " ", title)
    title = re.sub(r"(?i)([0-9.,]+[KkMm]?|[0-9]+)\s*(đã được bán|sold)\b", " ", title)
    title = re.sub(
        r"(?i)\b[0-5](?:[.,]\d)?\s*(?:/\s*5)?(?:\s+[0-9.,]+)?\s*(?:đánh giá|rating|ratings)\b",
        " ",
        title,
    )
    title = re.sub(r"\s+", " ", title).strip(" -|•·,.;:")
    if len(title) <= max_length:
        return title
    shortened = title[:max_length].rstrip()
    space_index = shortened.rfind(" ")
    if space_index >= 160:
        shortened = shortened[:space_index]
    return shortened.rstrip(" -|•·,.;:")


def _is_meaningful_product_title(raw_title: Any) -> bool:
    title = clean_product_title(raw_title)
    if len(title) <= 8:
        return False
    if title.startswith(("http://", "https://")):
        return False
    if _TITLE_GENERIC_RE.fullmatch(title):
        return False
    if not re.search(r"[0-9A-Za-zÀ-ỹ]", title):
        return False
    if re.fullmatch(r"[\d\s.,₫đĐ/-]+", title):
        return False
    return True


def _extract_json_ld_product_name(ld_json: Any) -> str:
    def iter_nodes(node: Any):
        if isinstance(node, dict):
            yield node
            graph = node.get("@graph")
            if isinstance(graph, list):
                for child in graph:
                    yield from iter_nodes(child)
        elif isinstance(node, list):
            for child in node:
                yield from iter_nodes(child)

    for node in iter_nodes(ld_json):
        raw_type = node.get("@type")
        types = raw_type if isinstance(raw_type, list) else [raw_type]
        if any("product" in str(item).lower() for item in types):
            name = clean_product_title(node.get("name"))
            if _is_meaningful_product_title(name):
                return name
    return ""


def _clean_keyword(raw_keyword: Any) -> str:
    keyword = re.sub(r"\s+", " ", str(raw_keyword or "")).strip(" -|•·,.;:").strip()
    keyword = re.sub(r"₫\s*[0-9][0-9.]*", "", keyword)
    keyword = re.sub(r"[^\wÀ-ỹ\s-]", " ", keyword, flags=re.UNICODE)
    keyword = re.sub(r"\s+", " ", keyword).strip(" -_")
    if keyword.lower() in _JUNK_KEYWORDS:
        return ""
    if len(keyword) <= 2:
        return ""
    return keyword


def _add_keyword(keywords: list[str], keyword: Any) -> None:
    cleaned = _clean_keyword(keyword)
    if not cleaned:
        return
    if cleaned.lower() in {item.lower() for item in keywords}:
        return
    keywords.append(cleaned)


def derive_keywords_from_title(title: str) -> list[str]:
    normalized_title = unicodedata.normalize("NFC", clean_product_title(title, max_length=260))
    lowered = normalized_title.lower()
    lowered = re.sub(r"₫\s*[0-9][0-9.]*", " ", lowered)
    lowered = re.sub(r"[^\wÀ-ỹ\s]", " ", lowered, flags=re.UNICODE)
    lowered = re.sub(r"\s+", " ", lowered).strip()

    keywords: list[str] = []
    priority_patterns = [
        ("khăn giấy", r"\bkhăn\s+giấy\b"),
        ("giấy rút dây", r"\bgiấy\s+rút\s+dây\b"),
        ("khăn giấy rút", r"\bkhăn\s+giấy\s+rút\b"),
        ("thùng 6 bịch", r"\bthùng\s+6\s+bịch\b"),
        ("tiểu hạ", r"\btiểu\s+hạ\b"),
        ("TOPGIA", r"\btopgia\b"),
        ("4 lớp", r"\b4\s+lớp\b"),
        ("1000 tờ", r"\b1000\s*tờ\b"),
    ]
    for keyword, pattern in priority_patterns:
        if re.search(pattern, lowered):
            _add_keyword(keywords, keyword)

    tokens = [token for token in lowered.split() if token and token not in _VIETNAMESE_STOPWORDS]
    for size in range(5, 1, -1):
        for index in range(0, max(0, len(tokens) - size + 1)):
            phrase_tokens = tokens[index:index + size]
            if not any(token in _PRODUCT_NOUN_HINTS or token.isdigit() for token in phrase_tokens):
                continue
            phrase = " ".join(phrase_tokens)
            if 4 <= len(phrase) <= 60:
                _add_keyword(keywords, phrase)
            if len(keywords) >= 10:
                return keywords

    if len(keywords) < 5:
        for token in tokens:
            if token in _PRODUCT_NOUN_HINTS or len(token) >= 4 or token.isdigit():
                _add_keyword(keywords, token)
            if len(keywords) >= 10:
                break

    if len(keywords) < 3:
        for size in range(min(3, len(tokens)), 1, -1):
            for index in range(0, max(0, len(tokens) - size + 1)):
                _add_keyword(keywords, " ".join(tokens[index:index + size]))
                if len(keywords) >= 5:
                    break
            if len(keywords) >= 5:
                break
    if len(keywords) < 3:
        for token in tokens:
            if len(token) >= 3:
                _add_keyword(keywords, token)
            if len(keywords) >= 5:
                break
    return keywords[:10]


def _meaningful_keywords(raw_keywords: Any) -> list[str]:
    if not isinstance(raw_keywords, list):
        return []
    keywords: list[str] = []
    for keyword in raw_keywords:
        _add_keyword(keywords, keyword)
    return keywords


def _merge_keywords(primary: list[str], secondary: list[str], *, limit: int = 10) -> list[str]:
    merged: list[str] = []
    for keyword in [*primary, *secondary]:
        _add_keyword(merged, keyword)
        if len(merged) >= limit:
            break
    return merged


def _choose_tiktok_shop_title(shop_data: dict[str, Any]) -> str:
    for candidate in (
        _extract_json_ld_product_name(shop_data.get("ld_json")),
        shop_data.get("og_title"),
        shop_data.get("title"),
        shop_data.get("page_title"),
    ):
        cleaned = clean_product_title(candidate)
        if _is_meaningful_product_title(cleaned):
            return cleaned
    return ""


def _build_tiktok_shop_diagnostic(product_url: str, shop_data: dict[str, Any] | None) -> dict[str, Any]:
    shop_data = shop_data or {}
    return {
        "product_url": product_url,
        "current_url": shop_data.get("current_url") or "",
        "body_text_preview": shop_data.get("body_text_preview") or "",
        "candidate_lines": shop_data.get("candidate_lines") or [],
        "page_title": shop_data.get("page_title") or "",
        "og_title": shop_data.get("og_title") or "",
        "meta_description": shop_data.get("meta_description") or "",
        "shop_data_error": shop_data.get("error"),
    }


def _raise_tiktok_shop_title_failure(product_url: str, shop_data: dict[str, Any] | None) -> None:
    diagnostic = _build_tiktok_shop_diagnostic(product_url, shop_data)
    LOGGER.error(
        "extract_product_info_validation_failed",
        extra={
            "event": "extract_product_info_validation_failed",
            **diagnostic,
        },
    )
    message = (
        "TikTok Shop page loaded but product title could not be extracted. "
        f"diagnostic={json.dumps(diagnostic, ensure_ascii=False)[:4000]}"
    )
    raise FatalDependencyError(message)


async def _call_gemini_product_info(content: list[Any]) -> str:
    candidates = get_enabled_candidates(preferred_provider="gemini")
    if not candidates:
        raise RuntimeError(
            "No usable AI provider key configured. Open Settings -> AI Providers and add an enabled Gemini key/model."
        )

    import google.generativeai as genai  # type: ignore[import]

    generation_config = genai.types.GenerationConfig(  # type: ignore[attr-defined]
        temperature=0.3,
        max_output_tokens=512,
    )
    loop = asyncio.get_running_loop()
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
            return raw_text
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
    raise RuntimeError(
        "All configured AI providers failed. Errors:\n" + "\n".join(f"  - {error}" for error in errors)
    )


async def _wait_for_tiktok_product_signal(page: Any) -> str:
    selectors = [
        "text=₫",
        "text=Đã được bán",
        "img",
        '[data-e2e*="product"], [class*="product"], [class*="Product"], h1, h2, [class*="title"], [class*="Title"]',
    ]
    for selector in selectors:
        try:
            await page.wait_for_selector(selector, timeout=2_500)
            return selector
        except Exception:
            continue
    try:
        await page.wait_for_function(
            """() => {
                const text = document.body ? document.body.innerText : "";
                return text.length > 1000 || /₫|Đã được bán|sold/i.test(text);
            }""",
            timeout=5_000,
        )
        return "body_text"
    except Exception:
        return ""


async def extract_tiktok_shop_product_info(product_url: str, account_id: str | None = None) -> dict[str, Any]:
    from core.browser_providers import make_browser_provider
    from database.database import AutomationDatabase, RetryConfig
    from playwright.async_api import async_playwright
    import os

    result = {
        "title": "",
        "description": "",
        "keywords": [],
        "ld_json": None,
        "og_title": "",
        "meta_description": "",
        "page_title": "",
        "price": "",
        "rating": "",
        "sold_count": "",
        "shop_name": "",
        "breadcrumbs": [],
        "candidate_lines": [],
        "body_text_preview": "",
        "body_text_length": 0,
        "current_url": "",
        "page_loaded": False,
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
                    await page.wait_for_load_state("domcontentloaded")
                    try:
                        await page.wait_for_load_state("networkidle", timeout=10_000)
                    except Exception:
                        pass
                    await _wait_for_tiktok_product_signal(page)
                    await asyncio.sleep(2.5)
                    result["page_loaded"] = True
                    result["current_url"] = page.url

                    try:
                        ld_json_texts = await page.evaluate('''() => {
                            return Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
                                .map((script) => script.textContent || script.innerText || "")
                                .filter(Boolean);
                        }''')
                        parsed_ld_json: list[Any] = []
                        for ld_json_text in ld_json_texts or []:
                            try:
                                parsed = json.loads(ld_json_text)
                            except Exception:
                                continue
                            parsed_ld_json.append(parsed)
                        if parsed_ld_json:
                            result["ld_json"] = parsed_ld_json[0] if len(parsed_ld_json) == 1 else parsed_ld_json
                    except Exception:
                        pass

                    try:
                        result["og_title"] = await page.evaluate('''() => {
                            const og = document.querySelector('meta[property="og:title"]')
                                || document.querySelector('meta[name="twitter:title"]')
                                || document.querySelector('meta[name="og:title"]');
                            return og ? (og.content || "") : "";
                        }''')
                    except Exception:
                        pass

                    try:
                        result["meta_description"] = await page.evaluate('''() => {
                            const desc = document.querySelector('meta[name="description"]')
                                || document.querySelector('meta[property="og:description"]')
                                || document.querySelector('meta[name="twitter:description"]');
                            return desc ? (desc.content || "") : "";
                        }''')
                    except Exception:
                        pass

                    try:
                        result["page_title"] = await page.title()
                    except Exception:
                        pass

                    try:
                        dom_data = await page.evaluate(_TIKTOK_SHOP_DOM_EXTRACTOR)
                        if isinstance(dom_data, dict):
                            result.update({
                                "title": dom_data.get("title") or "",
                                "description": dom_data.get("description") or "",
                                "price": dom_data.get("price") or "",
                                "rating": dom_data.get("rating") or "",
                                "sold_count": dom_data.get("sold_count") or "",
                                "shop_name": dom_data.get("shop_name") or "",
                                "breadcrumbs": dom_data.get("breadcrumbs") or [],
                                "candidate_lines": dom_data.get("candidate_lines") or [],
                                "body_text_preview": dom_data.get("body_text_preview") or "",
                                "body_text_length": int(dom_data.get("body_text_length") or 0),
                            })
                    except Exception:
                        pass

                    if not result["body_text_length"]:
                        try:
                            result["body_text_length"] = int(await page.evaluate('''() => {
                                return document.body && document.body.innerText ? document.body.innerText.length : 0;
                            }''') or 0)
                        except Exception:
                            pass

                    chosen_title = _choose_tiktok_shop_title(result)
                    if chosen_title:
                        result["title"] = chosen_title
                        result["ok"] = True

                    LOGGER.info(
                        "tiktok_shop_page_loaded",
                        extra={
                            "event": "tiktok_shop_page_loaded",
                            "current_url": result["current_url"],
                            "title": result["page_title"],
                            "body_text_length": result["body_text_length"],
                        },
                    )
                    LOGGER.info(
                        "tiktok_shop_dom_extracted",
                        extra={
                            "event": "tiktok_shop_dom_extracted",
                            "title": result["title"],
                            "price": result["price"],
                            "sold_count": result["sold_count"],
                            "rating": result["rating"],
                            "candidate_count": len(result["candidate_lines"]),
                        },
                    )
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

    fetch_url_success = False
    fetched_text_length = 0
    shop_data: dict[str, Any] | None = None
    dom_result: dict[str, Any] | None = None

    if product_url:
        if is_tiktok_shop and account_id:
            shop_data = await extract_tiktok_shop_product_info(product_url, account_id)
            shop_title = _choose_tiktok_shop_title(shop_data)
            if shop_data.get("ok") and shop_title:
                fetch_url_success = True
                description = str(shop_data.get("description") or shop_data.get("meta_description") or "").strip()
                deterministic_keywords = derive_keywords_from_title(shop_title)
                dom_result = {
                    "title": shop_title,
                    "description": description,
                    "keywords": deterministic_keywords,
                    "ok": True,
                    "source": "tiktok_shop_dom",
                }
                LOGGER.info(
                    "extract_product_info_using_dom_fallback",
                    extra={
                        "event": "extract_product_info_using_dom_fallback",
                        "title": shop_title,
                        "keywords": deterministic_keywords,
                    },
                )

                page_text = f"URL: {product_url}\n"
                if shop_data.get("page_title"): page_text += f"Page Title: {shop_data['page_title']}\n"
                if shop_data.get("og_title"): page_text += f"Meta Title: {shop_data['og_title']}\n"
                if shop_data.get("title"): page_text += f"DOM Title: {shop_title}\n"
                if shop_data.get("meta_description"): page_text += f"Meta Desc: {shop_data['meta_description']}\n"
                if shop_data.get("price"): page_text += f"Price: {shop_data['price']}\n"
                if shop_data.get("sold_count"): page_text += f"Sold: {shop_data['sold_count']}\n"
                if shop_data.get("rating"): page_text += f"Rating: {shop_data['rating']}\n"
                if shop_data.get("shop_name"): page_text += f"Shop: {shop_data['shop_name']}\n"
                if shop_data.get("candidate_lines"):
                    page_text += "Candidate Lines:\n" + "\n".join(str(line) for line in shop_data["candidate_lines"][:8]) + "\n"
                if shop_data.get("ld_json"): page_text += f"JSON-LD: {json.dumps(shop_data['ld_json'])}\n"
                fetched_text_length = len(page_text)
                content.append(f"Product page data:\n\n{page_text}")
            else:
                has_page_diagnostics = bool(
                    shop_data.get("page_loaded")
                    or shop_data.get("body_text_preview")
                    or shop_data.get("candidate_lines")
                    or shop_data.get("page_title")
                    or shop_data.get("og_title")
                    or shop_data.get("ld_json")
                )
                if has_page_diagnostics:
                    _raise_tiktok_shop_title_failure(product_url, shop_data)
                LOGGER.warning(
                    "tiktok_shop_extract_failed",
                    extra={
                        "event": "tiktok_shop_extract_failed",
                        "url": product_url,
                        "error": shop_data.get("error"),
                    },
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

    raw_text = ""
    if dom_result:
        try:
            raw_text = await _call_gemini_product_info(content)
            parsed = _parse_json_response(raw_text)
        except Exception as exc:
            LOGGER.warning(
                "extract_product_info_using_dom_fallback",
                extra={
                    "event": "extract_product_info_using_dom_fallback",
                    "title": dom_result["title"],
                    "keywords": dom_result["keywords"],
                    "error": str(exc)[:300],
                },
            )
            LOGGER.info(
                "extract_product_info_done",
                extra={
                    "event": "extract_product_info_done",
                    "product_url": product_url,
                    "fetch_url_success": fetch_url_success,
                    "fetched_text_length": fetched_text_length,
                    "parsed_title": dom_result["title"],
                    "parsed_keywords": dom_result["keywords"],
                    "source": dom_result["source"],
                    "raw_ai_text_preview": raw_text[:200] if raw_text else "",
                },
            )
            return dom_result

        refined_result = dict(dom_result)
        ai_description = str(parsed.get("description", "")).strip()
        ai_keywords = _meaningful_keywords(parsed.get("keywords"))
        changed = False
        if ai_description:
            refined_result["description"] = ai_description
            changed = True
        if ai_keywords:
            refined_result["keywords"] = _merge_keywords(dom_result["keywords"], ai_keywords)
            changed = True
        refined_result["source"] = "tiktok_shop_dom_refined" if changed else dom_result["source"]
        LOGGER.info(
            "extract_product_info_done",
            extra={
                "event": "extract_product_info_done",
                "product_url": product_url,
                "fetch_url_success": fetch_url_success,
                "fetched_text_length": fetched_text_length,
                "parsed_title": refined_result["title"],
                "parsed_keywords": refined_result["keywords"],
                "source": refined_result["source"],
                "raw_ai_text_preview": raw_text[:200] if raw_text else "",
            },
        )
        return refined_result

    raw_text = await _call_gemini_product_info(content)
    parsed = _parse_json_response(raw_text)

    title: str = clean_product_title(parsed.get("title")) or "Unknown Product"
    description: str = str(parsed.get("description", "")).strip()
    keywords: list[str] = _meaningful_keywords(parsed.get("keywords"))

    if not keywords:
        keywords = derive_keywords_from_title(title)
    if not keywords:
        keywords = [w.lower() for w in title.split() if len(w) > 3 and w.lower() not in _JUNK_KEYWORDS][:8]

    is_fallback_unknown = title.lower() == "unknown product" or not _is_meaningful_product_title(title)
    meaningful_keywords = _merge_keywords(keywords, [], limit=10)
    
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
        LOGGER.error(
            "extract_product_info_validation_failed",
            extra={
                "event": "extract_product_info_validation_failed",
                "product_url": product_url,
                "current_url": product_url or "",
                "body_text_preview": "",
                "candidate_lines": [],
                "page_title": "",
                "og_title": "",
                "meta_description": "",
                "shop_data_error": shop_data.get("error") if shop_data else None,
                "parsed_title": title,
                "parsed_keywords": keywords,
                "raw_ai_text_preview": raw_text[:500] if raw_text else "",
            },
        )
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
