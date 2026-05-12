# -*- coding: utf-8 -*-
"""
Inspect TikTok search page DOM - dumps all data-e2e attrs, class names,
and attempts to extract author/views/likes with multiple selector strategies.
Run: .venv/Scripts/python.exe scratch/inspect_tiktok_dom.py
"""
import asyncio, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.browser_context import get_browser_data_dir
from core.stealth import get_stealth_scripts


JS_DUMP_E2E = """
() => {
    const els = [...document.querySelectorAll('[data-e2e]')];
    return els.map(el => ({
        e2e:  el.getAttribute('data-e2e'),
        tag:  el.tagName,
        text: el.innerText?.trim().slice(0, 40) || '',
        cls:  el.className?.toString().slice(0, 60) || '',
    }));
}
"""

JS_DUMP_VIDEO_CARDS = """
() => {
    // Try each container selector
    const selectors = [
        '[data-e2e="search_video-item"]',
        'div[class*="DivItemContainerForSearch"]',
        'div[class*="DivItemContainer"]',
        'article',
    ];
    let containers = [];
    let usedSel = '';
    for (const sel of selectors) {
        const found = [...document.querySelectorAll(sel)];
        if (found.length > 0) { containers = found; usedSel = sel; break; }
    }

    const cards = containers.slice(0, 3).map((el, idx) => {
        // Dump ALL descendant elements with data-e2e or interesting class
        const descendants = [...el.querySelectorAll('*')].map(d => ({
            tag:  d.tagName,
            e2e:  d.getAttribute('data-e2e') || '',
            cls:  (d.className?.toString() || '').slice(0, 80),
            text: (d.innerText?.trim() || '').slice(0, 50),
            href: d.getAttribute('href') || '',
        })).filter(d => d.e2e || d.href.includes('tiktok') ||
                        d.cls.match(/[Aa]uthor|[Ll]ike|[Vv]iew|[Cc]ount|[Uu]ser|[Nn]ick|[Ss]trong/));

        return { card_idx: idx, container_selector: usedSel, descendants };
    });
    return { used_selector: usedSel, total_cards: containers.length, cards };
}
"""

JS_STRONG_TAGS = """
() => {
    // TikTok puts counts in <strong> tags
    const strongs = [...document.querySelectorAll('strong')];
    return strongs.slice(0, 30).map(s => ({
        text: s.innerText?.trim() || '',
        e2e:  s.getAttribute('data-e2e') || '',
        cls:  (s.className?.toString() || '').slice(0, 60),
        parentE2e: s.parentElement?.getAttribute('data-e2e') || '',
        parentCls: (s.parentElement?.className?.toString() || '').slice(0, 60),
    }));
}
"""

JS_AUTHOR_CANDIDATES = """
() => {
    // Find all elements that look like @username
    const all = [...document.querySelectorAll('a, p, span, h3')];
    return all.filter(el => {
        const t = el.innerText?.trim() || '';
        return t.startsWith('@') || el.getAttribute('data-e2e')?.includes('author');
    }).slice(0, 10).map(el => ({
        tag:  el.tagName,
        text: el.innerText?.trim().slice(0, 40) || '',
        e2e:  el.getAttribute('data-e2e') || '',
        cls:  (el.className?.toString() || '').slice(0, 80),
        href: el.getAttribute('href') || '',
    }));
}
"""


async def main():
    from playwright.async_api import async_playwright

    data_dir = get_browser_data_dir("_tiktok_scraper_")
    url = "https://www.tiktok.com/search?q=skincare+routine"

    print(f"Launching browser -> {url}")
    print("(headless=False so you can see what's happening)\n")

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            str(data_dir),
            headless   = True,   # set False to watch
            viewport   = {"width": 1280, "height": 900},
            locale     = "en-US",
            timezone_id= "America/New_York",
            args       = ["--disable-blink-features=AutomationControlled"],
        )
        for script in get_stealth_scripts("_tiktok_scraper_"):
            await ctx.add_init_script(script)

        pages = ctx.pages
        page  = pages[0] if pages else await ctx.new_page()

        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(4)

        # Dismiss overlays
        for sel in ['[data-e2e="modal-close-inner-button"]', '[aria-label="Close"]']:
            try:
                btn = await page.query_selector(sel)
                if btn:
                    await btn.click()
                    await asyncio.sleep(0.5)
            except Exception:
                pass

        # Wait for video cards
        try:
            await page.wait_for_selector(
                '[data-e2e="search_video-item"], div[class*="DivItemContainer"]',
                timeout=12_000
            )
        except Exception:
            print("[WARN] Video card selector timed out — page may need login")

        await asyncio.sleep(3)

        # Scroll twice to trigger lazy load
        for _ in range(3):
            await page.mouse.wheel(0, 600)
            await asyncio.sleep(1.5)

        await asyncio.sleep(2)

        # ── Dump 1: all data-e2e attributes ───────────────────────────────────
        print("=" * 60)
        print("ALL data-e2e ATTRIBUTES ON PAGE:")
        print("=" * 60)
        e2e_items = await page.evaluate(JS_DUMP_E2E)
        seen_e2e = set()
        for item in e2e_items:
            key = item["e2e"]
            if key and key not in seen_e2e:
                seen_e2e.add(key)
                print(f"  [{item['tag']:10}] data-e2e={key:<45} text={item['text']!r}")

        # ── Dump 2: video card descendants ────────────────────────────────────
        print()
        print("=" * 60)
        print("VIDEO CARD DESCENDANT ELEMENTS (first 3 cards):")
        print("=" * 60)
        card_data = await page.evaluate(JS_DUMP_VIDEO_CARDS)
        print(f"Container selector used: {card_data['used_selector']!r}")
        print(f"Total cards found: {card_data['total_cards']}")
        for card in card_data["cards"]:
            print(f"\n  Card #{card['card_idx']}:")
            for d in card["descendants"]:
                if d["e2e"] or d["href"]:
                    print(f"    <{d['tag']}> e2e={d['e2e']!r:40} text={d['text']!r:30} href={d['href'][:50]!r}")
                else:
                    print(f"    <{d['tag']}> cls={d['cls']!r:60} text={d['text']!r}")

        # ── Dump 3: <strong> tags (likely counts) ─────────────────────────────
        print()
        print("=" * 60)
        print("<strong> TAGS (view/like counts live here on TikTok):")
        print("=" * 60)
        strongs = await page.evaluate(JS_STRONG_TAGS)
        for s in strongs:
            print(f"  text={s['text']!r:12} e2e={s['e2e']!r:35} "
                  f"parent_e2e={s['parentE2e']!r:30} cls={s['cls'][:40]!r}")

        # ── Dump 4: author candidates ─────────────────────────────────────────
        print()
        print("=" * 60)
        print("AUTHOR / USERNAME CANDIDATES:")
        print("=" * 60)
        authors = await page.evaluate(JS_AUTHOR_CANDIDATES)
        for a in authors:
            print(f"  <{a['tag']}> text={a['text']!r:30} e2e={a['e2e']!r:35} "
                  f"href={a['href'][:40]!r}")

        await ctx.close()

    print()
    print("DOM inspection complete.")


asyncio.run(main())
