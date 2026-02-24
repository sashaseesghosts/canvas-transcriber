"""
Canvas page link extraction.

Provides two entry points used by the CLI:

* ``extract_links_from_page`` — scrape anchors and iframes from a single page.
* ``extract_links_from_modules_page`` — crawl every module item in a Canvas
  /modules page and collect video links from the destination pages.
"""

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Video provider detection
# ---------------------------------------------------------------------------

_VIDEO_PROVIDERS: dict[str, list[str]] = {
    "panopto":      ["panopto"],
    "kaltura":      ["kaltura", "kaf"],
    "yuja":         ["yuja"],
    "zoom":         ["zoom.us"],
    "youtube":      ["youtube.com", "youtu.be"],
    "canvas_media": ["instructuremedia", "mediaobjects"],
    "vimeo":        ["vimeo.com"],
}


def detect_video_provider(href: str) -> Optional[str]:
    """Return the detected video provider name for *href*, or ``None``."""
    href_lower = href.lower()
    for provider, patterns in _VIDEO_PROVIDERS.items():
        for pattern in patterns:
            if pattern in href_lower:
                return provider
    return None


# ---------------------------------------------------------------------------
# Single-page extraction
# ---------------------------------------------------------------------------

def extract_links_from_page(page) -> list[dict]:
    """Return deduplicated link records from the current page.

    Each record contains:
    ``text``, ``href``, ``link_type`` (``"anchor"`` | ``"iframe"``),
    ``video_provider`` (provider name or ``None``).
    """
    raw = page.evaluate("""() => {
        const results = [];

        document.querySelectorAll('a[href]').forEach(a => {
            results.push({
                type: 'anchor',
                text: a.textContent.trim().substring(0, 200),
                href: a.href,
            });
        });

        document.querySelectorAll('iframe[src]').forEach(f => {
            const src = f.src;
            if (src && !src.startsWith('about:') && !src.startsWith('javascript:'))
                results.push({
                    type: 'iframe',
                    text: f.title || f.getAttribute('aria-label') || 'Embedded iframe',
                    href: src,
                });
        });

        return results;
    }""")

    unique: dict[str, dict] = {}
    for item in raw:
        href = item.get("href", "")
        if not href or href.startswith(("javascript:", "about:", "#")):
            continue
        if href in unique:
            continue
        unique[href] = {
            "text": item["text"],
            "href": href,
            "link_type": item.get("type", "anchor"),
            "video_provider": detect_video_provider(href),
        }
    return list(unique.values())


# ---------------------------------------------------------------------------
# Modules-page crawl
# ---------------------------------------------------------------------------

def _extract_module_item_links(page) -> list[dict]:
    """Return all module item links with their parent module name."""
    return page.evaluate("""() => {
        const results = [];
        document.querySelectorAll('.context_module').forEach(mod => {
            const nameEl = mod.querySelector('.ig-header .name, .ig-header strong');
            const moduleName = nameEl
                ? nameEl.textContent.trim()
                : (mod.getAttribute('aria-label') || 'Unknown Module');
            mod.querySelectorAll('a.ig-title[href*="/modules/items/"]').forEach(a => {
                results.push({
                    module_name: moduleName,
                    text: a.textContent.trim().substring(0, 200),
                    href: a.href,
                });
            });
        });
        return results;
    }""")


def extract_links_from_modules_page(page, context) -> list[dict]:
    """Crawl every module item on a Canvas */modules* page.

    Navigates each ``/modules/items/{id}`` URL, collects video links from the
    destination page, and attaches ``module_name`` + ``canvas_item_text`` to
    each link record for downstream organisation.
    """
    print(f"    Reading module structure...")
    items = _extract_module_item_links(page)
    print(f"    Found {len(items)} module items to check")

    all_video_links: dict[str, dict] = {}
    check_page = context.new_page()

    for i, item in enumerate(items, 1):
        href = item["href"]
        text = item["text"]
        module_name = item["module_name"]
        try:
            check_page.goto(href, timeout=20000)
            check_page.wait_for_load_state("domcontentloaded", timeout=20000)

            for link in extract_links_from_page(check_page):
                if not link.get("video_provider"):
                    continue
                lhref = link["href"]
                if lhref not in all_video_links:
                    link["module_name"] = module_name
                    link["canvas_item_text"] = text
                    all_video_links[lhref] = link
                    print(f"  [{i:3d}/{len(items)}] [{link['video_provider']}] "
                          f"{module_name[:30]} / {text[:40]}")

            if not any(l["href"] == href for l in all_video_links.values()):
                # Only print "no video" once per item to avoid repetition
                print(f"  [{i:3d}/{len(items)}] (no video) {text[:60]}")

        except Exception as e:
            print(f"  [{i:3d}/{len(items)}] Error — {text[:40]}: {e}")

    check_page.close()
    return list(all_video_links.values())
