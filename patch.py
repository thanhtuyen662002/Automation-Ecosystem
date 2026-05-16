import re
import os

fpath = r"d:\Projects\Automation-Ecosystem\workers\handlers\tiktok\extract_product_info.py"
with open(fpath, "r", encoding="utf-8") as f:
    text = f.read()

# 1. Add account_id to payload extraction
text = re.sub(
    r'(product_image_path: str \| None = payload\.get\("product_image_path"\) or None)',
    r'\1\n    account_id: str | None = payload.get("account_id") or None',
    text
)

# 2. Add is_tiktok_shop check
text = re.sub(
    r'(await random_jitter\(0\.5, 2\.0\))',
    r'\1\n\n    is_tiktok_shop = False\n    if product_url and ("shop.tiktok.com/view/product" in product_url or "www.tiktok.com/shop/product" in product_url):\n        is_tiktok_shop = True',
    text
)

# 3. Update log
text = re.sub(
    r'("has_image": bool\(product_image_path\),)',
    r'\1\n            "is_tiktok_shop": is_tiktok_shop,',
    text
)

# 4. Update the extraction logic for text
old_text_extract = """    # Text: scrape product URL (first 8 000 chars to stay within context window)
    if product_url:
        try:
            page_text = await fetch_url_text(product_url)
            page_text = page_text[:8000]
        except Exception as exc:
            LOGGER.warning(
                "fetch_url_failed",
                extra={"event": "fetch_url_failed", "url": product_url, "error": str(exc)},
            )
            page_text = f"Product URL: {product_url}"

        content.append(f"Product page content:\\n\\n{page_text}")"""

new_text_extract = """    import json
    fetch_url_success = False
    fetched_text_length = 0
    shop_data = None

    if product_url:
        if is_tiktok_shop and account_id:
            shop_data = await extract_tiktok_shop_product_info(product_url, account_id)
            if shop_data.get("ok"):
                fetch_url_success = True
                page_text = f"URL: {product_url}\\n"
                if shop_data.get("page_title"): page_text += f"Page Title: {shop_data['page_title']}\\n"
                if shop_data.get("og_title"): page_text += f"Meta Title: {shop_data['og_title']}\\n"
                if shop_data.get("title"): page_text += f"DOM Title: {shop_data['title']}\\n"
                if shop_data.get("meta_description"): page_text += f"Meta Desc: {shop_data['meta_description']}\\n"
                if shop_data.get("ld_json"): page_text += f"JSON-LD: {json.dumps(shop_data['ld_json'])}\\n"
                fetched_text_length = len(page_text)
                content.append(f"Product page data:\\n\\n{page_text}")
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
                content.append(f"Product page content:\\n\\n{page_text}")
            except Exception as exc:
                LOGGER.warning(
                    "fetch_url_failed",
                    extra={"event": "fetch_url_failed", "url": product_url, "error": str(exc)},
                )
                page_text = f"Product URL: {product_url}"
                content.append(f"Product page content:\\n\\n{page_text}")"""

if old_text_extract in text:
    text = text.replace(old_text_extract, new_text_extract)
else:
    print("WARNING: Could not find text extraction block")

# 5. Validation part
old_val = """    if not keywords:
        # Fallback: derive from title words
        keywords = [w.lower() for w in title.split() if len(w) > 3][:8]

    result = {
        "title": title,
        "description": description,
        "keywords": keywords,
        "ok": True,
    }

    LOGGER.info(
        "extract_product_info_done",
        extra={
            "event": "extract_product_info_done",
            "title": title,
            "keyword_count": len(keywords),
        },
    )
    return result"""

new_val = """    if not keywords:
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

    return result"""

if old_val in text:
    text = text.replace(old_val, new_val)
else:
    print("WARNING: Could not find validation block")

with open(fpath, "w", encoding="utf-8") as f:
    f.write(text)
print("extract_product_info updated")
