"""
Kaltura transcript extraction.

Core functions used by the CLI (cli.py) to:
  1. Extract captions via the Kaltura caption API (primary path).
  2. Fall back to DOM/UI scraping when the API response arrives late.
  3. Deep-debug a single video page (--debug mode).
"""

import json
import re
import time
import requests
from typing import Optional


# ---------------------------------------------------------------------------
# Caption / transcript validation
# ---------------------------------------------------------------------------

_CSS_PATTERNS = [
    r"sourceMappingURL",
    r"\.plugin-button__",
    r"\.scss\.",
    r"sourceMappingURL=",
    r"base64,",
    r"\{[\s]*[a-z-]+[\s]*:",
    r"background-color:",
    r"@media\s",
    r"@import\s",
    r"webpack://",
    r"__webpack_require__",
]

_REJECT_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _CSS_PATTERNS]


def validate_transcript(text: str) -> tuple[bool, Optional[str]]:
    """Return *(is_valid, rejection_reason)* for candidate transcript text."""
    if not text or len(text) < 50:
        return False, "too_short"

    for pattern in _REJECT_PATTERNS:
        if pattern.search(text):
            return False, f"css_pattern_match:{pattern.pattern}"

    if text.count("{") > 5 or text.count(";") > 10:
        return False, "too_many_braces"

    alpha_ratio = sum(c.isalpha() for c in text) / max(len(text), 1)
    if alpha_ratio < 0.3:
        return False, "low_alpha_ratio"

    if len(text.split()) < 10:
        return False, "not_enough_words"

    return True, None


# ---------------------------------------------------------------------------
# Caption file parsing
# ---------------------------------------------------------------------------

def parse_vtt_to_text(vtt_content: str) -> str:
    """Extract plain transcript text from a VTT or SRT caption file."""
    lines = vtt_content.split("\n")
    transcript_lines: list[str] = []
    in_cue = False

    for line in lines:
        line = line.strip()
        if not line:
            in_cue = False
            continue
        if line.startswith("WEBVTT") or line.startswith("NOTE"):
            continue
        if re.match(r"^\d{2}:\d{2}:\d{2}", line) or re.match(r"^\d{2}:\d{2}", line):
            in_cue = True
            continue
        if "-->" in line:
            in_cue = True
            continue
        if in_cue:
            transcript_lines.append(line)

    return " ".join(transcript_lines)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def sanitize_filename(text: str) -> str:
    """Return a filesystem-safe filename stem (no extension) from *text*."""
    if not text:
        return "untitled_video"
    text = re.sub(r'[<>:"/\\|?*]', "", text)
    text = text.strip()[:100]
    return text or "untitled_video"


# ---------------------------------------------------------------------------
# DOM / UI extraction (fallback)
# ---------------------------------------------------------------------------

def extract_kaltura_transcript(page) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Try to read transcript text from the Kaltura player DOM.

    Returns *(text, source_type, selector_info, error_message)*.
    Any element may be None.
    """
    transcript_text: Optional[str] = None
    transcript_source: Optional[str] = None
    selector_info: Optional[str] = None
    error_msg: Optional[str] = None

    print(f"    Page URL:   {page.url}")
    print(f"    Page title: {page.title()}")

    try:
        result = page.evaluate("""() => {
            const out = { transcript: null, source: null, selector: null, vttUrl: null };

            const selectors = [
                '[class*="transcript"]', '[id*="transcript"]',
                '[class*="caption"]',
                '[role="tabpanel"]:not([aria-hidden="true"])',
                '[aria-label*="transcript"]', '[aria-label*="caption"]',
                '[data-testid*="transcript"]',
                '.kaltura-transcript', '.transcript-panel', '.captions-panel'
            ];

            for (const sel of selectors) {
                for (const el of document.querySelectorAll(sel)) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        const tag = el.tagName.toLowerCase();
                        if (tag === 'script' || tag === 'style' || tag === 'noscript') continue;
                        const text = el.textContent?.trim();
                        if (text && text.length > 50 && text.length < 50000) {
                            out.transcript = text;
                            out.source = 'ui_panel';
                            out.selector = sel;
                            return out;
                        }
                    }
                }
            }

            const tracks = document.querySelectorAll('track[kind="subtitles"], track[kind="captions"]');
            if (tracks.length > 0) {
                out.vttUrl  = tracks[0].src;
                out.source  = 'vtt_track';
                out.selector = 'track[kind="subtitles"]';
                return out;
            }

            for (const btn of document.querySelectorAll('button, a, div[role="button"]')) {
                const t = (btn.textContent || '').toLowerCase();
                if (t.includes('transcript') || t.includes('cc') ||
                    t.includes('captions') || t.includes('subtitle')) {
                    const rect = btn.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        out.source   = 'ui_button_found';
                        out.selector = btn.tagName + (btn.className ? '.' + btn.className.split(' ').join('.') : '');
                        return out;
                    }
                }
            }

            return out;
        }""")

        transcript_text = result.get("transcript")
        transcript_source = result.get("source")
        selector_info = result.get("selector")
        vtt_url = result.get("vttUrl")

        if vtt_url and not transcript_text:
            print(f"    Found VTT track URL: {vtt_url}")
            try:
                resp = requests.get(vtt_url, timeout=10)
                if resp.status_code == 200:
                    transcript_text = parse_vtt_to_text(resp.text)
                    transcript_source = "vtt_fetch"
                    selector_info = vtt_url
            except Exception as e:
                error_msg = f"VTT fetch failed: {e}"
                print(f"    Warning: {error_msg}")

    except Exception as e:
        error_msg = f"DOM extraction error: {e}"
        print(f"    Warning: {error_msg}")

    return transcript_text, transcript_source, selector_info, error_msg


# ---------------------------------------------------------------------------
# Video metadata
# ---------------------------------------------------------------------------

def extract_video_metadata(page) -> dict:
    """Return basic video metadata from the current page."""
    return page.evaluate("""() => {
        const data = { title: null, duration: null, kaltura_entry_id: null };

        const titleEl = document.querySelector('h1, h2, [class*="title"], [itemprop="name"]');
        if (titleEl) data.title = titleEl.textContent?.trim();

        const og = document.querySelector('meta[property="og:title"]');
        if (og) data.title = og.content || data.title;

        for (const script of document.querySelectorAll('script')) {
            const c = script.textContent || '';
            const m = c.match(/entryId["']?\\s*:\\s*["']?([a-z0-9_]+)/i);
            if (m) { data.kaltura_entry_id = m[1]; break; }
        }

        const dur = document.querySelector('[class*="duration"], [class*="time"]');
        if (dur) data.duration = dur.textContent?.trim();

        return data;
    }""")


# ---------------------------------------------------------------------------
# Primary extraction: Kaltura caption API via network interception
# ---------------------------------------------------------------------------

def process_kaltura_link(page, link_data: dict, session_context, browser) -> dict:
    """Extract the transcript for one Kaltura video link.

    Strategy:
    1. Intercept the ``caption_captionasset/getUrl`` API response that the
       Kaltura player fires during initialisation to get a signed caption
       serve URL, then fetch that URL directly.
    2. If the signed URL arrives after the polling window (some players
       initialise slowly), a second-chance fetch is attempted after the DOM
       scrape fallback.
    3. As a last resort, scrape the transcript panel from the DOM.

    Returns a result dict (without ``transcript_text`` stripped — callers
    decide what to persist in metadata vs. the .txt file).
    """
    href = link_data.get("href", "")
    text = link_data.get("text", "").strip()

    print(f"\n{'=' * 60}")
    print(f"Processing: {text[:60]}")
    print(f"URL: {href}")
    print(f"{'=' * 60}")

    result: dict = {
        "title": text or "Unknown",
        "source_url": href,
        "provider": "kaltura",
        "module_name": link_data.get("module_name", ""),
        "link_type": link_data.get("link_type", "anchor"),
        "transcript_found": False,
        "transcript_source_type": None,
        "transcript_candidate_source": None,
        "transcript_candidate_selector": None,
        "transcript_validation_passed": False,
        "rejection_reason": None,
        "transcript_text": None,
        "kaltura_entry_id": None,
        "errors": [],
    }

    try:
        print(f"    Opening video page...")

        if "external_tools/retrieve" in href:
            new_page = session_context.new_page()
            caption_serve_urls: list[str] = []

            def _capture(response) -> None:
                url_lower = response.url.lower()
                if "caption_captionasset" in url_lower and "geturl" in url_lower:
                    try:
                        data = json.loads(response.text())
                        if isinstance(data, list):
                            for item in data:
                                if isinstance(item, str) and len(item) > 10:
                                    caption_serve_urls.append(item)
                                    print(f"    [network] Caption serve URL intercepted")
                    except Exception:
                        pass

            new_page.on("response", _capture)
            new_page.goto(href)
            new_page.wait_for_load_state("domcontentloaded", timeout=30000)

            # Poll up to 15 s for the caption URL (fast path)
            wait_start = time.time()
            while not caption_serve_urls and (time.time() - wait_start) < 15:
                time.sleep(0.5)
            elapsed = time.time() - wait_start
            if caption_serve_urls:
                print(f"    Caption URL arrived after {elapsed:.1f}s")
            else:
                print(f"    No caption URL after {elapsed:.1f}s")

            print(f"    Final URL: {new_page.url}")

            # --- Primary: Kaltura caption API ---
            if caption_serve_urls:
                result = _fetch_caption_urls(caption_serve_urls, result, label="API")

            # --- Fallback: DOM scraping ---
            if not result["transcript_found"]:
                print(f"    Falling back to DOM extraction...")
                transcript, source, selector, error = extract_kaltura_transcript(new_page)
                if transcript:
                    is_valid, rejection_reason = validate_transcript(transcript)
                    result.update(
                        transcript_candidate_source=source,
                        transcript_candidate_selector=selector,
                        transcript_validation_passed=is_valid,
                        rejection_reason=rejection_reason,
                    )
                    if is_valid:
                        result.update(
                            transcript_found=True,
                            transcript_source_type=source,
                            transcript_text=transcript,
                        )
                        print(f"    ✓ Transcript via DOM fallback ({source})")
                    else:
                        result["errors"].append(f"DOM transcript rejected: {rejection_reason}")
                        print(f"    ✗ DOM transcript rejected: {rejection_reason}")
                else:
                    result["errors"].append(error or "No transcript found in DOM")
                    print(f"    ✗ No transcript in DOM")

            # --- Second chance: URL arrived during DOM scrape ---
            if not result["transcript_found"] and caption_serve_urls:
                print(f"    Caption URL arrived late — retrying API...")
                result["errors"].clear()
                result = _fetch_caption_urls(caption_serve_urls, result, label="late API")

            # Metadata
            meta = extract_video_metadata(new_page)
            if meta.get("title") and not result["title"]:
                result["title"] = meta["title"]
            if meta.get("kaltura_entry_id") not in (None, "null"):
                result["kaltura_entry_id"] = meta["kaltura_entry_id"]

            new_page.close()

        else:
            # Direct Kaltura link (not wrapped in Canvas external_tools)
            page.goto(href)
            page.wait_for_load_state("domcontentloaded", timeout=30000)
            time.sleep(3)

            print(f"    URL: {page.url}")
            transcript, source, selector, error = extract_kaltura_transcript(page)
            if transcript:
                is_valid, rejection_reason = validate_transcript(transcript)
                result.update(
                    transcript_candidate_source=source,
                    transcript_candidate_selector=selector,
                    transcript_validation_passed=is_valid,
                    rejection_reason=rejection_reason,
                )
                if is_valid:
                    result.update(
                        transcript_found=True,
                        transcript_source_type=source,
                        transcript_text=transcript,
                    )
                    print(f"    ✓ Transcript via DOM ({source})")
                else:
                    result["errors"].append(f"Transcript rejected: {rejection_reason}")
                    print(f"    ✗ Transcript rejected: {rejection_reason}")
            else:
                result["errors"].append(error or "No transcript found")

            meta = extract_video_metadata(page)
            if meta.get("title") and not result["title"]:
                result["title"] = meta["title"]
            if meta.get("kaltura_entry_id") not in (None, "null"):
                result["kaltura_entry_id"] = meta["kaltura_entry_id"]

    except Exception as e:
        msg = f"Navigation/extraction error: {e}"
        result["errors"].append(msg)
        print(f"    ✗ {msg}")

    return result


def _fetch_caption_urls(serve_urls: list[str], result: dict, label: str = "API") -> dict:
    """Try each *serve_url* in order; populate *result* on success."""
    print(f"    Trying Kaltura {label} ({len(serve_urls)} URL(s))...")
    for serve_url in serve_urls:
        try:
            resp = requests.get(serve_url, timeout=15)
            if resp.status_code == 200 and resp.text.strip():
                raw_text = parse_vtt_to_text(resp.text)
                is_valid, rejection_reason = validate_transcript(raw_text)
                result.update(
                    transcript_candidate_source=f"kaltura_{label.replace(' ', '_')}",
                    transcript_candidate_selector=serve_url,
                    transcript_validation_passed=is_valid,
                    rejection_reason=rejection_reason,
                )
                if is_valid:
                    result.update(
                        transcript_found=True,
                        transcript_source_type="kaltura_api_caption",
                        transcript_text=raw_text,
                    )
                    print(f"    ✓ Transcript via {label}! {len(raw_text):,} chars")
                    return result
                else:
                    result["errors"].append(f"{label} caption rejected: {rejection_reason}")
                    print(f"    ✗ {label} caption rejected: {rejection_reason}")
            else:
                result["errors"].append(f"{label} serve HTTP {resp.status_code}")
                print(f"    ✗ {label} serve returned {resp.status_code}")
        except Exception as e:
            result["errors"].append(f"{label} fetch error: {e}")
            print(f"    ✗ {label} fetch error: {e}")
    return result


# ---------------------------------------------------------------------------
# Debug mode
# ---------------------------------------------------------------------------

def _inspect_page_debug(new_page, debug_info: dict, captured_urls: list) -> None:
    """Populate *debug_info* with a full diagnostic snapshot of *new_page*."""
    debug_info["final_url"] = new_page.url
    debug_info["page_title"] = new_page.title()
    print(f"    Final URL:   {debug_info['final_url']}")
    print(f"    Page title:  {debug_info['page_title']}")

    try:
        new_page.wait_for_load_state("networkidle", timeout=8000)
        print(f"    Network idle reached")
    except Exception:
        print(f"    Network idle timeout — continuing")

    # Frames
    print(f"    Frames ({len(new_page.frames)} total)...")
    for frame in new_page.frames:
        frame_info: dict = {
            "url": frame.url,
            "name": frame.name,
            "title": None,
            "transcript_caption_elements": [],
        }
        try:
            frame_info["title"] = frame.title()
        except Exception:
            pass

        try:
            elements = frame.evaluate("""() => {
                const results = [];
                const els = document.querySelectorAll(
                    'button, a, div[role="button"], [aria-label], [aria-controls], ' +
                    '[class*="transcript"], [class*="caption"], [id*="transcript"], [id*="caption"]'
                );
                els.forEach(el => {
                    const tag = el.tagName.toUpperCase();
                    if (tag === 'STYLE' || tag === 'SCRIPT' || tag === 'NOSCRIPT') return;
                    const text = (el.textContent || '').toLowerCase();
                    const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                    const cls  = (el.className || '').toLowerCase();
                    const id   = (el.id || '').toLowerCase();
                    if (text.includes('transcript') || text.includes('captions') ||
                        text.includes('subtitle') ||
                        aria.includes('transcript') || aria.includes('caption') || aria.includes('cc') ||
                        cls.includes('transcript')  || cls.includes('caption') ||
                        id.includes('transcript')   || id.includes('caption')) {
                        const rect = el.getBoundingClientRect();
                        results.push({
                            tag: el.tagName, id: el.id || null,
                            text: el.textContent?.trim().substring(0, 100),
                            ariaLabel: el.getAttribute('aria-label'),
                            className: el.className,
                            visible: rect.width > 0 && rect.height > 0,
                        });
                    }
                });
                return results;
            }""")
            frame_info["transcript_caption_elements"] = elements
            if elements:
                print(f"      {frame.url[:70]}: {len(elements)} element(s)")
                for el in elements[:3]:
                    print(f"        [{el['tag']}] '{el['text'][:60]}' visible={el['visible']}")
        except Exception as fe:
            frame_info["transcript_caption_elements_error"] = str(fe)

        debug_info["frames"].append(frame_info)
        print(f"      Frame: {frame.url[:80]}")

    # Network
    print(f"    Network ({len(captured_urls)} responses)...")
    for req in captured_urls:
        url_lower = req["url"].lower()
        for key in ("vtt", "srt", "caption", "transcript", "kaltura"):
            if f".{key}" in url_lower or key in url_lower:
                debug_info["network_urls"].setdefault(key, []).append(req)
    for key, urls in debug_info["network_urls"].items():
        if urls:
            print(f"      {key}: {len(urls)}")
            for r in urls[:2]:
                print(f"        {r['url'][:90]} [{r['status']}]")

    # Text tracks / iframes
    dom = new_page.evaluate("""() => {
        const tracks = [];
        document.querySelectorAll('track[kind="subtitles"], track[kind="captions"]').forEach(t => {
            tracks.push({ kind: t.kind, src: t.src, srclang: t.srclang, label: t.label });
        });
        const iframes = [];
        document.querySelectorAll('iframe').forEach(f => {
            iframes.push({ src: f.src, title: f.title });
        });
        return { tracks, iframes };
    }""")
    debug_info["text_tracks"] = dom.get("tracks", [])
    debug_info["iframes"] = dom.get("iframes", [])
    print(f"    Text tracks: {len(debug_info['text_tracks'])}")
    print(f"    IFrames:     {len(debug_info['iframes'])}")

    # Transcript / caption buttons in main page
    ui = new_page.evaluate("""() => {
        const res = { transcript_buttons: [], captions_buttons: [] };
        document.querySelectorAll('button, a, div[role="button"], [aria-label], [aria-controls]').forEach(el => {
            const text = (el.textContent || '').toLowerCase();
            const aria = (el.getAttribute('aria-label') || '').toLowerCase();
            const ctrl = (el.getAttribute('aria-controls') || '').toLowerCase();
            if (text.includes('transcript') || aria.includes('transcript') || ctrl.includes('transcript'))
                res.transcript_buttons.push({ tag: el.tagName,
                    text: el.textContent?.trim().substring(0, 50),
                    ariaLabel: el.getAttribute('aria-label'), visible: el.offsetParent !== null });
            if (text.includes('cc') || text.includes('captions') || text.includes('subtitle') ||
                aria.includes('cc') || aria.includes('captions'))
                res.captions_buttons.push({ tag: el.tagName,
                    text: el.textContent?.trim().substring(0, 50),
                    ariaLabel: el.getAttribute('aria-label'), visible: el.offsetParent !== null });
        });
        return res;
    }""")
    debug_info["transcript_buttons"] = ui.get("transcript_buttons", [])
    debug_info["captions_buttons"] = ui.get("captions_buttons", [])
    print(f"    Transcript buttons: {len(debug_info['transcript_buttons'])}")
    print(f"    Caption/CC buttons: {len(debug_info['captions_buttons'])}")

    # Player config
    player = new_page.evaluate("""() => {
        const data = { entryId: null, mediaId: null, captions: [], captionUrls: [] };
        for (const s of document.querySelectorAll('script')) {
            const c = s.textContent || '';
            const em = c.match(/entryId["']?\\s*:\\s*["']?([a-z0-9_]+)/i);
            if (em) data.entryId = em[1];
            const mm = c.match(/mediaId["']?\\s*:\\s*["']?([a-z0-9_]+)/i);
            if (mm) data.mediaId = mm[1];
            const cm = c.match(/"captions"\\s*:\\s*\\[(.*?)\\]/);
            if (cm) data.captions = [cm[1]];
            data.captionUrls.push(
                ...(c.match(/https?:[^"'\\s]+\\.(?:vtt|srt)[^"'\\s]*/gi) || []),
                ...(c.match(/https?:[^"'\\s]*(?:caption|transcript|subtitle)[^"'\\s]*/gi) || [])
            );
        }
        data.captionUrls = [...new Set(data.captionUrls)];
        return data;
    }""")
    debug_info["player_config"] = player
    print(f"    Entry ID: {player.get('entryId')}  Media ID: {player.get('mediaId')}")
    cap_urls = player.get("captionUrls", [])
    if cap_urls:
        print(f"    Caption URLs in scripts: {len(cap_urls)}")
        for u in cap_urls[:3]:
            print(f"      {u[:90]}")

    # Suggested actions
    all_elements = sum(len(f.get("transcript_caption_elements", [])) for f in debug_info["frames"])
    if debug_info["text_tracks"] or debug_info["transcript_buttons"] or debug_info["captions_buttons"] or all_elements:
        debug_info["suggested_actions"].append(
            "Transcript/caption UI elements found — may need to click a button first"
        )
    if debug_info["network_urls"].get("vtt") or debug_info["network_urls"].get("caption"):
        debug_info["suggested_actions"].append(
            "Caption URLs in network traffic — can fetch directly"
        )
    if player.get("captions") or cap_urls:
        debug_info["suggested_actions"].append(
            "Caption/VTT URLs in page scripts — check player_config.captionUrls"
        )
    if not debug_info["suggested_actions"]:
        debug_info["suggested_actions"].append(
            "No transcript/caption sources detected — video likely has no captions"
        )


def debug_kaltura_video(page, link_data: dict, session_context, browser) -> dict:
    """Deep-inspect one Kaltura video page and return a diagnostic dict.

    Saves a ``kaltura_debug.json`` snapshot that helps diagnose why a video's
    transcript cannot be extracted.
    """
    href = link_data.get("href", "")
    text = link_data.get("text", "").strip()

    print(f"\n{'=' * 60}")
    print(f"DEBUG: {text[:60]}")
    print(f"URL: {href}")
    print(f"{'=' * 60}")

    debug_info: dict = {
        "title": text,
        "source_url": href,
        "final_url": None,
        "page_title": None,
        "frames": [],
        "network_urls": {},
        "text_tracks": [],
        "iframes": [],
        "player_config": None,
        "transcript_buttons": [],
        "captions_buttons": [],
        "suggested_actions": [],
    }

    try:
        new_page = session_context.new_page()
        captured_urls: list[dict] = []

        def _capture(response) -> None:
            captured_urls.append({
                "url": response.url,
                "status": response.status,
                "content_type": response.headers.get("content-type", ""),
            })

        new_page.on("response", _capture)
        new_page.goto(href)
        new_page.wait_for_load_state("domcontentloaded", timeout=30000)
        print(f"    DOM loaded — waiting for player to initialise...")
        time.sleep(5)

        _inspect_page_debug(new_page, debug_info, captured_urls)
        new_page.close()

    except Exception as e:
        debug_info["error"] = str(e)
        print(f"    Error: {e}")

    return debug_info
