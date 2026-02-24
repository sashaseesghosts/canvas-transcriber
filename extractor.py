import re
from typing import List, Dict, Optional


VIDEO_PROVIDERS = {
    "panopto": ["panopto", "psu"],
    "kaltura": ["kaltura", "kaf"],
    "yuja": ["yuja", "yuja"],
    "zoom": ["zoom", "zoom.us"],
    "youtube": ["youtube", "youtu.be"],
    "canvas_media": ["<school>.instructure.com/media", "instructuremedia", "mediaobjects"],
    "vimeo": ["vimeo"],
}


def detect_video_provider(href: str) -> Optional[str]:
    """Detect video provider from URL."""
    href_lower = href.lower()

    for provider, patterns in VIDEO_PROVIDERS.items():
        for pattern in patterns:
            if pattern in href_lower:
                return provider

    return None


def extract_links_from_page(page) -> List[Dict]:
    """Extract all links and iframe sources from the page with video provider detection."""
    page_data = page.evaluate("""
        () => {
            const results = [];
            
            // Extract anchor links
            const anchors = document.querySelectorAll('a[href]');
            anchors.forEach(a => {
                results.push({
                    type: 'anchor',
                    text: a.textContent.trim().substring(0, 200),
                    href: a.href,
                    className: a.className || '',
                    parentClass: a.parentElement?.className || ''
                });
            });
            
            // Extract iframe sources
            const iframes = document.querySelectorAll('iframe[src]');
            iframes.forEach(iframe => {
                const src = iframe.src;
                if (src && !src.startsWith('about:') && !src.startsWith('javascript:')) {
                    results.push({
                        type: 'iframe',
                        text: iframe.title || iframe.getAttribute('aria-label') || 'Embedded iframe',
                        href: src,
                        className: iframe.className || '',
                        parentClass: iframe.parentElement?.className || ''
                    });
                }
            });
            
            return results;
        }
    """)

    unique_links = {}
    for link in page_data:
        href = link.get("href", "")
        if (
            not href
            or href.startswith("javascript:")
            or href == "#"
            or href.startswith("about:")
        ):
            continue

        if href in unique_links:
            continue

        video_provider = detect_video_provider(href)
        unique_links[href] = {
            "text": link["text"],
            "href": href,
            "link_type": link.get("type", "anchor"),
            "video_provider": video_provider,
        }

    return list(unique_links.values())
