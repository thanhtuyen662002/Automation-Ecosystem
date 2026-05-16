"""
Handler: tiktok.generate_content
──────────────────────────────────
Reads from parent extract_product_info result:
  title, description, keywords

Output result:
  caption:   str        – full post body (hook + body + CTA), ≤ 2200 chars
  hook:      str        – first 1–2 lines (for video overlay / preview)
  cta:       str        – the call-to-action phrase
  hashtags:  list[str]  – 7–10 rule-based tags
  variants:  list[dict] – 2–3 alternative caption variants for A/B selection
  ok:        bool

COST OPTIMISATION (near-zero AI):
  • Default path: pure template engine — ZERO AI calls
  • AI is OPTIONAL fallback, enabled only when:
      – template engine fails (empty result), OR
      – USE_AI_CAPTION=true in environment
  • Module-level cache keyed on hash(title + template_type)
  • DRY_RUN=true forces template path regardless of USE_AI_CAPTION
  • Per-job cost logged; template path always logs $0.00
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
import re
import time
from typing import Any

from workers.handlers.tiktok._base import (
    check_already_processed,
    random_jitter,
    random_seed,
    resolve_parent_result,
)
from workers.handlers.tiktok.extract_product_info import _parse_json_response

LOGGER = logging.getLogger("workers.handlers.tiktok.generate_content")


# ── Cost estimation constants ─────────────────────────────────────────────────
_DEFAULT_COST_PER_1K = 0.00015   # USD / 1 000 tokens (Gemini Flash estimate)
_APPROX_TOKENS_PER_JOB = 700     # prompt (~500) + response (~200)

# ── Module-level result cache ─────────────────────────────────────────────────
# key  : SHA-256(title + "|" + template_type)[:16]
# value: {"caption": ..., "hook": ..., "cta": ...}
_RESULT_CACHE: dict[str, dict[str, str]] = {}


def _cache_key(title: str, template_type: str) -> str:
    raw = f"{title.lower().strip()}|{template_type}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════════════════════
# TEMPLATE ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

# ── Hook templates ────────────────────────────────────────────────────────────
# {p} = product name.  Each hook is ≤ 100 chars.

_HOOKS: dict[str, list[str]] = {
    "shock": [
        "{p} đang bị bán rẻ một cách vô lý 😳",
        "Cái này mà không viral thì hơi phí 🤯",
        "Sao mình không biết đến {p} sớm hơn??? 😭",
        "Giá của {p} mà rẻ thế này thật không??? 😱",
        "Đây là thứ mà mình đã tìm kiếm cả năm 😤",
        "TikTok đã thay đổi cuộc đời mình nhờ {p} 🤯",
        "Không ai nói với mình về {p} cả — vì sao vậy??",
        "Cảnh báo: {p} sẽ khiến bạn nghiện ngay lập tức 🚨",
        "Mua {p} xong mình chỉ muốn mua thêm 10 cái nữa",
        "POV: bạn vừa tìm ra {p} và không thể ngừng nhìn",
    ],
    "review": [
        "Mình test thử {p} và cái kết... 😍",
        "Không nghĩ {p} lại ổn vậy luôn 😭",
        "Thành thật mà nói về {p} sau 1 tuần dùng:",
        "Mình đã hoài nghi về {p} — đây là kết quả thật:",
        "Unbox {p} cùng mình và xem có đáng không 🎁",
        "Review thật không quảng cáo: {p} có ngon không?",
        "Mình đã dùng {p} được 2 tuần rồi, đây là review:",
        "Soleil moment: lần đầu thử {p} và phản ứng của mình 😂",
        "{p} có thật sự tốt như mọi người nói không? Mình test:",
        "Đã test {p} trong 30 ngày — kết quả bất ngờ lắm 👀",
    ],
    "problem_solution": [
        "Mệt mỏi với vấn đề này chưa? {p} giải quyết hết 💯",
        "Trước khi biết {p}, mình cũng từng như bạn 😔",
        "Vấn đề của bạn + {p} = giải pháp hoàn hảo ✅",
        "Bao lâu rồi bạn chịu đựng vấn đề này? Thử {p} đi:",
        "Nếu bạn đang tìm cách giải quyết điều này — đây rồi 👇",
        "Hint: vấn đề bạn đang gặp có thể fix bằng {p}",
        "Đây là lý do mọi người cần {p} trong cuộc sống:",
        "Stop chịu đựng — {p} đang chờ bạn 🙌",
        "{p} đã thay đổi cách mình xử lý vấn đề này mãi mãi",
        "Ai cũng có vấn đề này nhưng không ai biết {p} 😤",
    ],
    "before_after": [
        "Trước khi dùng {p}: ❌  Sau khi dùng: ✅",
        "Life before {p} vs life after — sự khác biệt WOW 🤩",
        "Mình trước đây vs mình sau khi có {p} 😭✨",
        "Bạn sẽ không tin sự khác biệt mà {p} tạo ra đâu",
        "Before: khổ sở / After: {p} cứu mình rồi 🙌",
        "Transformation thật sự nhờ {p} — không ai ngờ được",
        "6 tháng trước vs bây giờ khi có {p} trong tay:",
        "Cái mình CẦN lúc trước chính là {p} — ước gì biết sớm",
        "Nếu mình dùng {p} sớm hơn, đã không mất nhiều thời gian",
        "{p}: trước và sau khi thử — bạn sẽ thấy sự khác biệt",
    ],
}

# ── Body templates ────────────────────────────────────────────────────────────
# Short connective body (1–3 lines) that bridges hook → CTA.
# {p} = product name.

_BODIES: dict[str, list[str]] = {
    "shock": [
        "Chất lượng cao cấp, giá thì không thể tin được.\nMình đã thử và kết quả vượt xa kỳ vọng.\n{p} đang được săn đón cực kỳ nhiều mà ít ai biết đến.",
        "Đây không phải quảng cáo — mình thật sự sốc với {p}.\nGiá hời vậy mà chất lượng lại không thua kém gì hàng đắt tiền.",
        "Cả hội bạn mình đang dùng {p} và không ai muốn chia sẻ vì sợ hết hàng 😂\nMình quyết định chia sẻ vì các bạn deserves to know.",
    ],
    "review": [
        "Thật ra mình khá nghi ngờ ban đầu.\nNhưng sau khi dùng {p} được 1 tuần, mình đã đổi ý hoàn toàn.\nChất lượng thật, không có filter không có chỉnh sửa.",
        "Mình đã thử nhiều sản phẩm tương tự và {p} thật sự nổi bật.\nKhông cần quảng cáo hoành tráng, kết quả tự nói lên tất cả.",
        "Điều mình thích nhất ở {p} là sự đơn giản và hiệu quả.\nDùng ngay là thấy kết quả, không cần chờ lâu.",
    ],
    "problem_solution": [
        "Mình đã tìm kiếm giải pháp cho vấn đề này trong bao lâu.\nRồi {p} xuất hiện và thay đổi mọi thứ chỉ trong vài ngày.\nGiờ không thể tưởng tượng cuộc sống thiếu nó.",
        "{p} không chỉ giải quyết vấn đề, nó còn tiết kiệm thời gian và công sức.\nĐây là đầu tư thông minh nhất mình từng làm.",
        "Bí quyết: thay vì chịu đựng vấn đề, hãy để {p} lo.\nĐơn giản, hiệu quả, và giá cực kỳ hợp lý.",
    ],
    "before_after": [
        "Trước: mình không nghĩ có giải pháp nào hiệu quả.\nSau khi thử {p}: mọi thứ thay đổi hoàn toàn.\nKết quả thật, không có chỉnh sửa.",
        "Hành trình với {p}:\n→ Ngày 1: nghi ngờ\n→ Ngày 7: bắt đầu thấy khác biệt\n→ Ngày 30: không thể thiếu nó trong cuộc sống",
        "Bạn bè mình đã hỏi mình làm thế nào để thay đổi như vậy.\nBí mật của mình chỉ là {p} thôi — đơn giản vậy thôi.",
    ],
}

# ── CTA options ───────────────────────────────────────────────────────────────

_CTA_OPTIONS: list[str] = [
    "Link ở bio — xem ngay trước khi hết hàng 🛒",
    "Đang sale — đừng bỏ lỡ cơ hội này 🔥",
    "Comment 'INFO' để mình gửi link cho bạn 💬",
    "Follow để không bỏ lỡ deal tiếp theo 👀",
    "Save lại để dùng khi cần 📌",
    "Tag người bạn cần thấy cái này! 🙌",
    "Xem ngay trước khi giá tăng trở lại ⚡",
    "Drop a comment below 👇",
    "Link in bio for more info ✨",
    "Share this with someone who needs it 🤍",
]

# ── Urgency prefixes (40% injection rate) ─────────────────────────────────────

_URGENCY_PHRASES: list[str] = [
    "🔥 HOT:",
    "⚡ TRENDING:",
    "🛒 đang sale:",
    "👀 đừng bỏ lỡ:",
    "💥 OMG:",
    "✨ viral rn:",
    "🚨 must-have:",
]

# ── Emoji variation sets (injected randomly into body) ───────────────────────

_INLINE_EMOJIS: list[str] = [
    "🔥", "✨", "💯", "👀", "😭", "🤩", "💪", "🙌",
    "⭐", "💫", "🎯", "🛒", "❤️", "😍", "💥", "🤌",
]

# ── Trending + generic TikTok hashtags ───────────────────────────────────────

_TRENDING_TAGS: list[str] = [
    "fyp", "foryou", "foryoupage", "viral", "trending",
    "tiktokfinds", "tiktokmademebuyit", "explore",
]

_NICHE_TAG_POOL: list[str] = [
    "productreview", "musthave", "lifehack", "unboxing", "recommendation",
    "bestproduct", "review", "honest", "dailyfinds", "shopnow",
    "reviewsanpham", "muasam", "sanphamhot", "tiktokshop",
]

# ── Style keys ────────────────────────────────────────────────────────────────

_STYLE_KEYS: list[str] = list(_HOOKS.keys())

# ── AI style prompts (fallback only) ─────────────────────────────────────────

_AI_STYLE_PROMPTS: dict[str, str] = {
    "shock": (
        "Write a SHOCK-style TikTok caption. Open with a surprising stat, bold claim, or "
        "controversial statement. Follow with 2–3 sentences explaining why this product "
        "changes everything. Keep energy high."
    ),
    "review": (
        "Write a first-person REVIEW-style TikTok caption as if you just tried the product. "
        "Start with an honest, relatable reaction. Sound authentic — not salesy."
    ),
    "problem_solution": (
        "Write a PROBLEM/SOLUTION TikTok caption. Open by calling out a specific pain point. "
        "Then present this product as the clear, simple fix."
    ),
    "before_after": (
        "Write a BEFORE/AFTER transformation TikTok caption using contrast language: "
        "'used to… now I…' or 'before vs. after'. Keep it punchy."
    ),
}


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _slugify(text: str) -> str:
    """Convert text to a lowercase alphanumeric slug for hashtag use."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", "", text.strip())
    return text[:30]


def _build_hashtags(title: str, keywords: list[str]) -> list[str]:
    """Rule-based hashtag builder. Zero AI cost."""
    trending = random.sample(_TRENDING_TAGS, k=random.randint(2, 3))
    niche = random.sample(_NICHE_TAG_POOL, k=random.randint(2, 3))
    title_words = [w for w in title.split() if len(w) > 3]
    kw_candidates = [_slugify(k) for k in keywords[:5] if k]
    title_candidates = [_slugify(w) for w in title_words[:3]]
    product_tags = list(dict.fromkeys(title_candidates + kw_candidates))
    product_tags = [t for t in product_tags if t][:4]
    all_tags = trending + niche + product_tags
    seen: set[str] = set()
    unique: list[str] = []
    for t in all_tags:
        if t and t not in seen:
            seen.add(t)
            unique.append(t)
    random.shuffle(unique)
    return unique[:10]


def _inject_emoji(text: str) -> str:
    """Randomly inject 0–2 emojis at end of lines for variety."""
    lines = text.splitlines()
    out: list[str] = []
    for line in lines:
        if line.strip() and random.random() < 0.3:
            emoji = random.choice(_INLINE_EMOJIS)
            if not line.rstrip().endswith(emoji):
                line = line.rstrip() + f" {emoji}"
        out.append(line)
    return "\n".join(out)


def _render_template(template: str, product_name: str) -> str:
    """Substitute {p} placeholder and apply light randomisation."""
    return template.replace("{p}", product_name)


def _build_caption_from_template(
    style_key: str,
    product_name: str,
    cta: str,
) -> dict[str, str]:
    """
    Assemble one caption variant from template pools.
    Returns {"caption": ..., "hook": ..., "cta": ...}.
    """
    hook_raw = random.choice(_HOOKS[style_key])
    body_raw = random.choice(_BODIES[style_key])

    hook = _render_template(hook_raw, product_name)
    body = _render_template(body_raw, product_name)
    body = _inject_emoji(body)

    # Optionally prepend urgency phrase to hook (~40% of runs)
    if random.random() < 0.4:
        phrase = random.choice(_URGENCY_PHRASES)
        if not any(hook.startswith(p.split(":")[0]) for p in _URGENCY_PHRASES):
            hook = f"{phrase} {hook}"

    caption = f"{hook}\n\n{body}\n\n{cta}"

    # Clamp to TikTok 2200-char limit
    if len(caption) > 2200:
        caption = caption[:2197] + "..."

    return {
        "caption": caption,
        "hook": hook[:120],
        "cta": cta,
    }


def _generate_variants(
    style_key: str,
    product_name: str,
    n: int = 3,
) -> list[dict[str, str]]:
    """
    Generate n caption variants using different hook + body + CTA combos.
    De-duplicates hooks across variants.
    """
    variants: list[dict[str, str]] = []
    used_hooks: set[str] = set()

    for _ in range(n * 3):  # extra attempts for de-dup
        if len(variants) >= n:
            break
        cta = random.choice(_CTA_OPTIONS)
        hook_template = random.choice(_HOOKS[style_key])
        if hook_template in used_hooks:
            continue
        used_hooks.add(hook_template)
        v = _build_caption_from_template(style_key, product_name, cta)
        variants.append(v)

    # Fill remaining slots if de-dup couldn't satisfy n variants
    while len(variants) < n:
        cta = random.choice(_CTA_OPTIONS)
        variants.append(_build_caption_from_template(style_key, product_name, cta))

    return variants[:n]


def _dry_run_content(title: str, cta: str, style_key: str) -> dict[str, str]:
    return {
        "caption": f"[DRY_RUN] Mock caption for {title}. Style: {style_key}. {cta}",
        "hook": f"[DRY_RUN] {title} — mock hook",
        "cta": cta,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CONTENT DECISION GATE
# ═══════════════════════════════════════════════════════════════════════════════

def _content_decision_gate(
    payload: dict[str, Any],
    title:   str  = "",
    mode:    str  = "generate",
) -> None:
    """
    Mandatory EV gate before expensive generation.

    Reads payload["decision_signals"] for context.
    Falls back to title-based curiosity signal for hook estimation.
    Raises ValueError if should_produce() returns False.
    Fail-open on import errors (non-fatal).
    """
    try:
        from core.content_decision import ContentCandidate, should_produce
        signals = payload.get("decision_signals") or {}
        item_id = str(payload.get("job_id") or payload.get("item_id") or "gc_job")
        candidate = ContentCandidate(
            item_id         = item_id,
            trend_score     = float(signals.get("trend_score",    0.5)),
            product_intent  = float(signals.get("product_intent", 0.5)),
            hook_potential  = float(signals.get("hook_potential", -1.0)),  # auto-estimate
            match_score     = float(signals.get("match_score",    0.5)),
            novelty_score   = float(signals.get("novelty_score",  0.5)),
            production_cost = float(signals.get("production_cost", 0.8)),
            metadata        = {"text": title, **(signals.get("metadata") or {})},
        )
        niche = str(signals.get("niche", ""))
        allowed, reason = should_produce(candidate, mode=mode, niche=niche)
        if not allowed:
            LOGGER.info(
                "generate_content_decision_blocked item=%s mode=%s reason=%s",
                item_id, mode, reason,
            )
            raise ValueError(f"content_decision BLOCKED [{mode}]: {reason}")
    except ValueError:
        raise
    except Exception as exc:
        LOGGER.debug("generate_content_gate_error (non-fatal): %s", exc)


# ═══════════════════════════════════════════════════════════════════════════════
# HANDLER
# ═══════════════════════════════════════════════════════════════════════════════

async def generate_content_handler(payload: dict[str, Any]) -> dict[str, Any]:
    # ── Idempotency guard ─────────────────────────────────────────────────────
    if (cached := check_already_processed(payload)) is not None:
        return cached

    # ── Resolve product info ──────────────────────────────────────────────────
    try:
        title: str = str(resolve_parent_result(payload, "title")).strip()
        description: str = str(resolve_parent_result(payload, "description")).strip()
        keywords: list[str] = list(resolve_parent_result(payload, "keywords"))
    except KeyError:
        title = str(payload.get("title", "")).strip()
        description = str(payload.get("description", "")).strip()
        keywords = list(payload.get("keywords", []))

    if not title:
        raise ValueError("generate_content requires 'title' from parent extract_product_info result")

    # ── Content Decision Gate (MANDATORY — before any AI API call) ────────────
    # generate mode: EV < 0.1 or final_score < 0 → skip entirely.
    signals = payload.get("decision_signals")
    if isinstance(signals, dict) and signals:
        _content_decision_gate(payload, title=title, mode="generate")
    else:
        LOGGER.info(
            "generate_content_decision_gate_skipped",
            extra={
                "event": "generate_content_decision_gate_skipped",
                "reason": "missing_decision_signals",
            },
        )

    # ── Config flags ──────────────────────────────────────────────────────────
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    use_ai = (not dry_run) and (os.environ.get("USE_AI_CAPTION", "false").lower() == "true")

    # Random style for this run
    style_key: str = random.choice(_STYLE_KEYS)
    cta: str = random.choice(_CTA_OPTIONS)
    seed: int = random_seed()

    # Rule-based hashtags (always — regardless of AI/template path)
    hashtags = _build_hashtags(title, keywords)

    LOGGER.info(
        "generate_content_start",
        extra={
            "event": "generate_content_start",
            "style": style_key,
            "seed": seed,
            "use_ai": use_ai,
            "dry_run": dry_run,
        },
    )

    ai_calls_used = 0
    estimated_cost = 0.0
    source = "template"

    # ── DRY_RUN path ──────────────────────────────────────────────────────────
    if dry_run:
        LOGGER.info(
            "generate_content_dry_run",
            extra={"event": "generate_content_dry_run", "title": title},
        )
        primary = _dry_run_content(title, cta, style_key)
        variants = [primary]

    # ── AI path (optional, opt-in via USE_AI_CAPTION=true) ───────────────────
    elif use_ai:
        ckey = _cache_key(title, f"ai_{style_key}")
        cached_ai = _RESULT_CACHE.get(ckey)

        if cached_ai is not None:
            LOGGER.info(
                "generate_content_cache_hit",
                extra={"event": "generate_content_cache_hit", "cache_key": ckey},
            )
            primary = cached_ai
        else:
            LOGGER.info(
                "generate_content_cache_miss",
                extra={"event": "generate_content_cache_miss", "cache_key": ckey},
            )
            await random_jitter(0.5, 2.0)

            from core.ai_router import generate_text  # lazy import — not loaded on default path

            style_prompt = _AI_STYLE_PROMPTS[style_key]
            prompt = (
                "You are a viral TikTok copywriter specializing in product promotions.\n\n"
                f"STYLE DIRECTIVE:\n{style_prompt}\n\n"
                "STRUCTURE your caption as:\n"
                "  Line 1–2: Hook (curiosity, pain point, or bold claim)\n"
                "  Lines 3–5: Problem → Solution body\n"
                f"  Last line: End with this exact CTA → {cta}\n\n"
                "Return ONLY a valid JSON object with exactly these keys:\n"
                "  caption   (string, ≤2000 chars)\n"
                "  hook      (string, ≤120 chars)\n"
                "  cta       (string — verbatim CTA)\n"
                "Do not include any text outside the JSON object.\n\n"
                f"Product: {title}\n"
                f"Description: {description}\n"
                f"Keywords: {', '.join(keywords[:10])}\n\n"
                f"(seed={seed})"
            )

            t0 = time.monotonic()
            raw_text = await generate_text(prompt, max_tokens=600, temperature=1.1)
            elapsed_s = time.monotonic() - t0

            parsed = _parse_json_response(raw_text)
            if not parsed:
                LOGGER.warning(
                    "generate_content_ai_fallback_to_template",
                    extra={"event": "generate_content_ai_fallback_to_template"},
                )
                # AI returned unparseable output → fall through to template
                primary = _build_caption_from_template(style_key, title, cta)
                source = "template_ai_fallback"
            else:
                primary = {
                    "caption": str(parsed.get("caption", "")).strip(),
                    "hook": str(parsed.get("hook", "")).strip(),
                    "cta": str(parsed.get("cta", cta)).strip(),
                }
                source = "ai"

            _RESULT_CACHE[f"ai_{ckey}"] = primary

            cost_per_1k = float(os.environ.get("AI_COST_PER_1K_TOKENS", _DEFAULT_COST_PER_1K))
            estimated_cost = (_APPROX_TOKENS_PER_JOB / 1000) * cost_per_1k
            ai_calls_used = 1

            LOGGER.info(
                "generate_content_ai_complete",
                extra={
                    "event": "generate_content_ai_complete",
                    "elapsed_s": round(elapsed_s, 2),
                    "estimated_cost_usd": round(estimated_cost, 6),
                },
            )

        # Generate template-based variants alongside the AI primary
        variants = [primary] + _generate_variants(style_key, title, n=2)

    # ── Template path (DEFAULT — zero AI cost) ────────────────────────────────
    else:
        ckey = _cache_key(title, style_key)
        cached_tmpl = _RESULT_CACHE.get(ckey)

        if cached_tmpl is not None:
            LOGGER.info(
                "generate_content_cache_hit",
                extra={"event": "generate_content_cache_hit", "cache_key": ckey},
            )
            primary = cached_tmpl
        else:
            primary = _build_caption_from_template(style_key, title, cta)
            _RESULT_CACHE[ckey] = primary

        # Always generate fresh variants (different hooks/CTAs per variant)
        variants = [primary] + _generate_variants(style_key, title, n=2)

    # ── Post-process primary result ───────────────────────────────────────────
    caption: str = primary.get("caption", "").strip()
    hook: str = primary.get("hook", "").strip()
    returned_cta: str = primary.get("cta", cta).strip()

    if not caption:
        raise RuntimeError("generate_content: empty caption — template or AI produced nothing")

    if len(caption) > 2200:
        caption = caption[:2197] + "..."

    if returned_cta not in caption:
        caption = caption.rstrip() + f"\n\n{returned_cta}"

    if not hook:
        first_lines = [ln.strip() for ln in caption.splitlines() if ln.strip()]
        hook = " ".join(first_lines[:2])[:120]

    result = {
        "caption": caption,
        "hook": hook,
        "cta": returned_cta,
        "hashtags": hashtags,
        "variants": variants,   # list[{"caption":..., "hook":..., "cta":...}]
        "ok": True,
    }

    LOGGER.info(
        "generate_content_done",
        extra={
            "event": "generate_content_done",
            "style": style_key,
            "source": source,
            "caption_length": len(caption),
            "hashtag_count": len(hashtags),
            "hook_length": len(hook),
            "variant_count": len(variants),
            "ai_calls_used": ai_calls_used,
            "estimated_cost_usd": estimated_cost,
            "dry_run": dry_run,
        },
    )
    return result
