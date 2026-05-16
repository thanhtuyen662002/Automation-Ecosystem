"""Shared TikTok search DOM extraction and normalization helpers."""

from __future__ import annotations

import re
import time
from typing import Any


SEARCH_CARD_SELECTOR = (
    '[data-e2e="search_video-item"], '
    'div[class*="DivItemContainerForSearch"], '
    'div[class*="DivItemContainer"]'
)


JS_EXTRACT_SEARCH_CARDS = """
() => {
    const results = [];

    const containerSelectors = [
        '[data-e2e="search_video-item"]',
        'div[class*="DivItemContainerForSearch"]',
        'div[class*="DivItemContainer"]',
        'article[data-e2e]',
        'li[class*="VideoFeed"]',
    ];

    let containers = [];
    for (const sel of containerSelectors) {
        const found = [...document.querySelectorAll(sel)];
        if (found.length > 0) { containers = found; break; }
    }

    const seen = new WeakSet();
    const unique = containers.filter(el => {
        if (seen.has(el)) return false;
        seen.add(el);
        return true;
    });

    for (const el of unique) {
        try {
            const linkEl = el.querySelector('a[href*="/video/"]')
                        || el.querySelector('a[href*="/photo/"]')
                        || el.querySelector('a[href*="/@"]');
            const videoUrl = linkEl ? linkEl.href : '';
            if (!videoUrl || !videoUrl.includes('tiktok.com')) continue;

            const authorEl =
                el.querySelector('[data-e2e="video-author-uniqueid"]') ||
                el.querySelector('[data-e2e*="author-uniqueid"]')       ||
                el.querySelector('[data-e2e*="author"]')                ||
                el.querySelector('a[href*="/@"][class*="author"]')      ||
                el.querySelector('span[class*="AuthorTitle"]')          ||
                el.querySelector('p[class*="author"]')                  ||
                el.querySelector('h3[class*="author"]');
            const author = authorEl ? authorEl.textContent.trim().replace(/^@/, '') : '';

            const captionEl =
                el.querySelector('[data-e2e="video-desc"]')     ||
                el.querySelector('[data-e2e*="video-desc"]')    ||
                el.querySelector('div[class*="DivDesc"]')       ||
                el.querySelector('span[class*="SpanText"]')     ||
                el.querySelector('h1[class*="video-meta"]')     ||
                el.querySelector('div[class*="video-meta-title"]');
            const caption = captionEl ? captionEl.textContent.trim().slice(0, 600) : '';

            const titleEl =
                el.querySelector('[title]') ||
                el.querySelector('img[alt]');
            const title = titleEl
                ? (titleEl.getAttribute('title') || titleEl.getAttribute('alt') || '').trim().slice(0, 240)
                : '';

            const viewEl =
                el.querySelector('[data-e2e="video-views"]') ||
                el.querySelector('[data-e2e*="views"]')      ||
                el.querySelector('span[class*="SpanViews"]');

            const likeEl =
                el.querySelector('[data-e2e="like-count"]') ||
                el.querySelector('[data-e2e*="like-count"]') ||
                el.querySelector('[data-e2e*="like"]');

            const commentEl =
                el.querySelector('[data-e2e="comment-count"]') ||
                el.querySelector('[data-e2e*="comment-count"]') ||
                el.querySelector('[data-e2e*="comment"]');

            const strongs = [...el.querySelectorAll('strong')].map(s =>
                s.textContent.trim()
            ).filter(t => /[\\d.]+[KkMmBb]?/.test(t) && t.length < 12);

            const viewsText    = viewEl    ? viewEl.textContent.trim()    : (strongs[0] || '0');
            const likesText    = likeEl    ? likeEl.textContent.trim()    : (strongs[1] || '0');
            const commentsText = commentEl ? commentEl.textContent.trim() : (strongs[2] || '0');

            const durationEl =
                el.querySelector('[data-e2e*="duration"]') ||
                el.querySelector('span[class*="Duration"]') ||
                el.querySelector('div[class*="Duration"]');
            const durationText = durationEl ? durationEl.textContent.trim() : '';

            const imgEl =
                el.querySelector('img[src*="tiktokcdn"]') ||
                el.querySelector('img[class*="ImgPoster"]') ||
                el.querySelector('img[src*="p16"]') ||
                el.querySelector('img[src*="p19"]') ||
                el.querySelector('img');
            const thumbnail = imgEl ? (imgEl.src || imgEl.dataset.src || '') : '';

            results.push({
                video_url: videoUrl,
                author,
                caption,
                title,
                views_text: viewsText,
                likes_text: likesText,
                comments_text: commentsText,
                duration_text: durationText,
                thumbnail,
            });
        } catch(e) {
            // skip malformed card
        }
    }
    return results;
}
"""


JS_SEARCH_PAGE_STATE = """
() => {
    const errEl = document.querySelector('[data-e2e="search-error-title"]');
    const loginBtn = document.querySelector('[data-e2e="top-login-button"]');
    return {
        has_error: !!errEl,
        needs_login: !!loginBtn,
        error_text: errEl ? errEl.textContent.trim() : '',
        card_count: document.querySelectorAll(
            '[data-e2e="search_video-item"], div[class*="DivItemContainer"]'
        ).length,
        title: document.title || '',
        url: location.href,
    };
}
"""


def parse_count(text: str | None) -> int:
    """Convert TikTok compact counts such as 1.2M or 45K into an int."""
    if not text:
        return 0
    clean = str(text).strip().replace(",", "").replace(" ", "").replace("\u00a0", "")
    try:
        match = re.match(r"([\d.]+)([KkMmBb]?)", clean)
        if not match:
            return 0
        number = float(match.group(1))
        suffix = match.group(2).upper()
        if suffix == "K":
            return int(number * 1_000)
        if suffix == "M":
            return int(number * 1_000_000)
        if suffix == "B":
            return int(number * 1_000_000_000)
        return int(number)
    except Exception:
        return 0


def parse_duration_seconds(text: str | None) -> float:
    """Parse duration strings like 00:31, 1:02, or 1:02:03. Unknown returns 0."""
    if not text:
        return 0.0
    clean = str(text).strip()
    if not clean:
        return 0.0
    parts = clean.split(":")
    if not all(part.isdigit() for part in parts):
        return 0.0
    try:
        values = [int(part) for part in parts]
        if len(values) == 2:
            minutes, seconds = values
            return float(minutes * 60 + seconds)
        if len(values) == 3:
            hours, minutes, seconds = values
            return float(hours * 3600 + minutes * 60 + seconds)
    except Exception:
        return 0.0
    return 0.0


def author_from_url(url: str) -> str:
    """Extract the TikTok @username from a URL."""
    match = re.search(r"tiktok\.com/@([^/?#]+)", url)
    return match.group(1) if match else ""


def normalize_tiktok_search_items(
    raw_items: list[dict[str, Any]],
    keyword: str,
    *,
    source: str,
    allow_photo: bool = False,
    scraped_at: int | None = None,
) -> list[dict[str, Any]]:
    """Normalize DOM-extracted TikTok search cards for downstream selection."""
    timestamp = int(scraped_at if scraped_at is not None else time.time())
    videos: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for item in raw_items:
        url = str(item.get("video_url") or item.get("url") or "").strip()
        if not url or "tiktok.com" not in url:
            continue
        is_video = "/video/" in url
        is_photo = "/photo/" in url
        if not is_video and not (allow_photo and is_photo):
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)

        author = str(item.get("author") or "").strip().lstrip("@")
        if not author:
            author = author_from_url(url)

        caption = str(item.get("caption") or "").strip()
        title = str(item.get("title") or "").strip()
        if not title:
            title = caption[:160] if caption else keyword

        videos.append(
            {
                "url": url,
                "title": title,
                "description": caption,
                "author": author,
                "uploader": author,
                "uploader_id": author,
                "views": parse_count(item.get("views_text")),
                "likes": parse_count(item.get("likes_text")),
                "comments": parse_count(item.get("comments_text")),
                "duration": parse_duration_seconds(item.get("duration_text")),
                "thumbnail": str(item.get("thumbnail") or "").strip(),
                "keyword": keyword,
                "source": source,
                "scraped_at": timestamp,
            }
        )

    return videos

