import json
import os
import re
import sys
import time
import requests
from pathlib import Path
from urllib.parse import urlparse, unquote

LINKS_FILE = Path("links_output.json")
SESSION_FILE = Path("session.json")
TRANSCRIPTS_DIR = Path("transcripts")
METADATA_FILE = TRANSCRIPTS_DIR / "metadata.json"
LOGIN_TIMEOUT = 120


CSS_PATTERNS = [
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

REJECT_PATTERNS = [re.compile(p, re.IGNORECASE) for p in CSS_PATTERNS]


def validate_transcript(text: str) -> tuple:
    """Validate if text appears to be a real transcript.

    Returns (is_valid, rejection_reason)
    """
    if not text or len(text) < 50:
        return False, "too_short"

    text_lower = text.lower()

    for pattern in REJECT_PATTERNS:
        if pattern.search(text):
            return False, f"css_pattern_match:{pattern.pattern}"

    if text.count("{") > 5 or text.count(";") > 10:
        return False, "too_many_braces"

    alpha_count = sum(c.isalpha() for c in text)
    total_count = len(text)
    if total_count > 0 and alpha_count / total_count < 0.3:
        return False, "low_alpha_ratio"

    words = text.split()
    if len(words) < 10:
        return False, "not_enough_words"

    return True, None


def parse_vtt_to_text(vtt_content: str) -> str:
    """Parse VTT caption file and extract plain text."""
    lines = vtt_content.split("\n")
    transcript_lines = []
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
        if in_cue and line:
            transcript_lines.append(line)

    return " ".join(transcript_lines)


def load_session(context, session_file: Path) -> bool:
    """Load session cookies from file."""
    try:
        with open(session_file, "r") as f:
            cookies = json.load(f)
        context.add_cookies(cookies)
        return True
    except Exception as e:
        print(f"Failed to load session: {e}")
        return False


def sanitize_filename(text: str) -> str:
    """Create a safe filename from video title."""
    if not text:
        return "untitled_video"
    text = re.sub(r'[<>:"/\\|?*]', "", text)
    text = text.strip()[:100]
    return text or "untitled_video"


def wait_for_canvas_page(page, timeout=LOGIN_TIMEOUT):
    """Wait for Canvas page to load after SSO login."""
    start_time = time.time()

    while time.time() - start_time < timeout:
        current_url = page.url
        page_title = page.title()

        url_contains_courses = "instructure.com/courses/" in current_url.lower()
        title_not_login = "login" not in page_title.lower()

        canvas_selector_exists = page.evaluate("""() => {
            return document.querySelector('#content') !== null || 
                   document.querySelector('.user_content') !== null ||
                   document.querySelector('.ic-app') !== null;
        }""")

        if url_contains_courses and (title_not_login or canvas_selector_exists):
            return True

        time.sleep(2)

    return False


def extract_kaltura_transcript(page) -> tuple:
    """Extract transcript from Kaltura player page.

    Returns tuple of (transcript_text, transcript_source_type, selector_info, error_message)
    """
    transcript_text = None
    transcript_source = None
    selector_info = None
    error_msg = None

    page_title = page.title()
    current_url = page.url
    print(f"    Page URL: {current_url}")
    print(f"    Page title: {page_title}")

    try:
        result = page.evaluate("""() => {
            const results = {
                transcript: null,
                source: null,
                selector: null,
                vttUrl: null
            };
            
            const selectors = [
                '[class*="transcript"]',
                '[id*="transcript"]',
                '[class*="caption"]',
                '[role="tabpanel"]:not([aria-hidden="true"])',
                '[aria-label*="transcript"]',
                '[aria-label*="caption"]',
                '[data-testid*="transcript"]',
                '.kaltura-transcript',
                '.transcript-panel',
                '.captions-panel'
            ];
            
            for (const sel of selectors) {
                const els = document.querySelectorAll(sel);
                for (const el of els) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        const text = el.textContent?.trim();
                        if (text && text.length > 50 && text.length < 50000) {
                            const tagName = el.tagName.toLowerCase();
                            if (tagName !== 'script' && tagName !== 'style' && tagName !== 'noscript') {
                                results.transcript = text;
                                results.source = 'ui_panel';
                                results.selector = sel;
                                return results;
                            }
                        }
                    }
                }
            }
            
            const trackElements = document.querySelectorAll('track[kind="subtitles"], track[kind="captions"]');
            if (trackElements.length > 0) {
                results.vttUrl = trackElements[0].src;
                results.source = 'vtt_track';
                results.selector = 'track[kind="subtitles"]';
                return results;
            }
            
            const transcriptButtons = document.querySelectorAll('button, a, div[role="button"]');
            for (const btn of transcriptButtons) {
                const text = btn.textContent?.toLowerCase() || '';
                if (text.includes('transcript') || text.includes('cc') || text.includes('captions') || text.includes('subtitle')) {
                    const rect = btn.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        results.source = 'ui_button_clicked';
                        results.selector = btn.tagName + (btn.className ? '.' + btn.className.split(' ').join('.') : '');
                        return results;
                    }
                }
            }
            
            return results;
        }""")

        transcript_text = result.get("transcript")
        transcript_source = result.get("source")
        selector_info = result.get("selector")
        vtt_url = result.get("vttUrl")

        if vtt_url and not transcript_text:
            print(f"    Found VTT URL: {vtt_url}")
            try:
                vtt_response = requests.get(vtt_url, timeout=10)
                if vtt_response.status_code == 200:
                    transcript_text = parse_vtt_to_text(vtt_response.text)
                    transcript_source = "vtt_fetch"
                    selector_info = vtt_url
                    print(f"    Parsed VTT transcript, length: {len(transcript_text)}")
            except Exception as e:
                error_msg = f"VTT fetch failed: {str(e)}"
                print(f"    Warning: {error_msg}")

    except Exception as e:
        error_msg = f"Extraction error: {str(e)}"
        print(f"    Warning: {error_msg}")

    return transcript_text, transcript_source, selector_info, error_msg


def setup_network_capture(page):
    """Set up network monitoring for caption/transcript requests."""
    captured_urls = []

    def handle_response(response):
        url = response.url.lower()
        if any(
            ext in url for ext in [".vtt", ".srt", "caption", "transcript", "subtitle"]
        ):
            captured_urls.append(response.url)

    page.on("response", handle_response)
    return captured_urls


def extract_video_metadata(page) -> dict:
    """Extract video metadata from the page."""
    metadata = page.evaluate("""() => {
        const data = {
            title: null,
            description: null,
            duration: null,
            kaltura_entry_id: null
        };
        
        // Try to get title from page
        const titleEl = document.querySelector('h1, h2, [class*="title"], [itemprop="name"]');
        if (titleEl) {
            data.title = titleEl.textContent?.trim();
        }
        
        // Try to get from meta tags
        const metaTitle = document.querySelector('meta[property="og:title"]');
        if (metaTitle) {
            data.title = metaTitle.content || data.title;
        }
        
        // Look for Kaltura entry ID
        const scripts = document.querySelectorAll('script');
        for (const script of scripts) {
            const content = script.textContent || '';
            const re = /entryId["']?\s*:\s*["']?([a-z0-9_]+)/i;
            const match = content.match(re);
            if (match) {
                data.kaltura_entry_id = match[1];
            }
        }
        
        // Get duration if available
        const durationEl = document.querySelector('[class*="duration"], [class*="time"]');
        if (durationEl) {
            data.duration = durationEl.textContent?.trim();
        }
        
        return data;
    }""")

    return metadata


def process_kaltura_link(page, link_data: dict, session_context, browser):
    """Process a single Kaltura video link and extract transcript."""
    href = link_data.get("href", "")
    text = link_data.get("text", "").strip()

    print(f"\n{'=' * 60}")
    print(f"Processing: {text[:50]}...")
    print(f"URL: {href}")
    print(f"{'=' * 60}")

    result = {
        "title": text or "Unknown",
        "source_url": href,
        "provider": "kaltura",
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
            new_page.goto(href)
            new_page.wait_for_load_state("domcontentloaded", timeout=30000)
            time.sleep(3)
            current_url = new_page.url
            print(f"    Navigated to: {current_url}")

            transcript, source, selector, error = extract_kaltura_transcript(new_page)

            if transcript:
                is_valid, rejection_reason = validate_transcript(transcript)

                result["transcript_candidate_source"] = source
                result["transcript_candidate_selector"] = selector
                result["transcript_validation_passed"] = is_valid
                result["rejection_reason"] = rejection_reason

                if is_valid:
                    result["transcript_found"] = True
                    result["transcript_source_type"] = source
                    result["transcript_text"] = transcript
                    print(f"    ✓ Transcript found and validated! Source: {source}")
                else:
                    result["errors"].append(f"Transcript rejected: {rejection_reason}")
                    print(f"    ✗ Transcript found but rejected: {rejection_reason}")
            else:
                result["errors"].append(
                    error or "No transcript found via UI extraction"
                )
                print(f"    ✗ No transcript found in UI")

                print(f"    Checking network requests for captions...")
                captured = setup_network_capture(new_page)
                time.sleep(2)
                if captured:
                    result["transcript_candidate_source"] = "network_capture"
                    result["transcript_candidate_selector"] = str(captured)
                    print(f"    Found caption URL in network: {captured}")

                    for cap_url in captured:
                        if cap_url.endswith(".vtt"):
                            try:
                                vtt_resp = requests.get(cap_url, timeout=10)
                                if vtt_resp.status_code == 200:
                                    vtt_text = parse_vtt_to_text(vtt_resp.text)
                                    is_valid, rej_reason = validate_transcript(vtt_text)
                                    if is_valid:
                                        result["transcript_found"] = True
                                        result["transcript_source_type"] = "network_vtt"
                                        result["transcript_text"] = vtt_text
                                        result["transcript_validation_passed"] = True
                                        print(
                                            f"    ✓ Found and validated VTT from network!"
                                        )
                                        break
                                    else:
                                        result["errors"].append(
                                            f"Network VTT rejected: {rej_reason}"
                                        )
                            except Exception as ve:
                                result["errors"].append(
                                    f"Failed to fetch network VTT: {str(ve)}"
                                )
                else:
                    print(f"    ✗ No caption URLs in network requests")

            metadata = extract_video_metadata(new_page)
            if metadata.get("title") and not result["title"]:
                result["title"] = metadata["title"]
            entry_id = metadata.get("kaltura_entry_id")
            if entry_id and entry_id != "null":
                result["kaltura_entry_id"] = entry_id

            new_page.close()
        else:
            page.goto(href)
            page.wait_for_load_state("domcontentloaded", timeout=30000)
            time.sleep(3)

            current_url = page.url
            print(f"    Current URL: {current_url}")

            transcript, source, selector, error = extract_kaltura_transcript(page)

            if transcript:
                is_valid, rejection_reason = validate_transcript(transcript)

                result["transcript_candidate_source"] = source
                result["transcript_candidate_selector"] = selector
                result["transcript_validation_passed"] = is_valid
                result["rejection_reason"] = rejection_reason

                if is_valid:
                    result["transcript_found"] = True
                    result["transcript_source_type"] = source
                    result["transcript_text"] = transcript
                    print(f"    ✓ Transcript found and validated! Source: {source}")
                else:
                    result["errors"].append(f"Transcript rejected: {rejection_reason}")
                    print(f"    ✗ Transcript found but rejected: {rejection_reason}")

            metadata = extract_video_metadata(page)
            if metadata.get("title") and not result["title"]:
                result["title"] = metadata["title"]
            entry_id = metadata.get("kaltura_entry_id")
            if entry_id and entry_id != "null":
                result["kaltura_entry_id"] = entry_id

    except Exception as e:
        error_msg = f"Navigation/extraction error: {str(e)}"
        result["errors"].append(error_msg)
        print(f"    ✗ Error: {error_msg}")

    return result


def debug_kaltura_video(page, link_data: dict, session_context, browser):
    """Debug mode: deeply inspect a Kaltura video page for transcript/caption sources."""
    href = link_data.get("href", "")
    text = link_data.get("text", "").strip()

    print(f"\n{'=' * 60}")
    print(f"DEBUG: Analyzing {text[:50]}...")
    print(f"URL: {href}")
    print(f"{'=' * 60}")

    debug_info = {
        "title": text,
        "source_url": href,
        "final_url": None,
        "page_title": None,
        "frames": [],
        "network_urls": {
            "vtt": [],
            "srt": [],
            "caption": [],
            "transcript": [],
            "kaltura": [],
        },
        "text_tracks": [],
        "player_config": None,
        "transcript_buttons": [],
        "captions_buttons": [],
        "suggested_actions": [],
    }

    try:
        print(f"    Opening video page in debug mode...")

        if "external_tools/retrieve" in href:
            new_page = session_context.new_page()

            captured_urls = []

            def capture_response(response):
                url = response.url.lower()
                captured_urls.append(
                    {
                        "url": response.url,
                        "status": response.status,
                        "content_type": response.headers.get("content-type", ""),
                    }
                )

            new_page.on("response", capture_response)

            new_page.goto(href)
            new_page.wait_for_load_state("domcontentloaded", timeout=30000)
            time.sleep(3)

            debug_info["final_url"] = new_page.url
            debug_info["page_title"] = new_page.title()
            print(f"    Final URL: {debug_info['final_url']}")
            print(f"    Page title: {debug_info['page_title']}")

            print(f"    Capturing frames...")
            for frame in new_page.frames:
                frame_info = {
                    "url": frame.url,
                    "name": frame.name,
                }
                try:
                    frame_info["title"] = (
                        frame.title() if hasattr(frame, "title") else None
                    )
                except:
                    frame_info["title"] = None
                debug_info["frames"].append(frame_info)
                print(f"      Frame: {frame_info['url'][:80]}...")

            print(f"    Categorizing network URLs ({len(captured_urls)} total)...")
            for req in captured_urls:
                url_lower = req["url"].lower()
                if ".vtt" in url_lower:
                    debug_info["network_urls"]["vtt"].append(req)
                if ".srt" in url_lower:
                    debug_info["network_urls"]["srt"].append(req)
                if "caption" in url_lower:
                    debug_info["network_urls"]["caption"].append(req)
                if "transcript" in url_lower:
                    debug_info["network_urls"]["transcript"].append(req)
                if "kaltura" in url_lower:
                    debug_info["network_urls"]["kaltura"].append(req)

            for category, urls in debug_info["network_urls"].items():
                if urls:
                    print(f"      {category}: {len(urls)} URLs")

            print(f"    Looking for text tracks...")
            text_tracks = new_page.evaluate("""() => {
                const tracks = [];
                document.querySelectorAll('track[kind="subtitles"], track[kind="captions"]').forEach(track => {
                    tracks.push({
                        kind: track.kind,
                        src: track.src,
                        srclang: track.srclang,
                        label: track.label,
                    });
                });
                
                const iframes = [];
                document.querySelectorAll('iframe').forEach(iframe => {
                    iframes.push({
                        src: iframe.src,
                        title: iframe.title,
                    });
                });
                
                return { tracks, iframes };
            }""")
            debug_info["text_tracks"] = text_tracks.get("tracks", [])
            debug_info["iframes"] = text_tracks.get("iframes", [])
            print(f"      Text tracks found: {len(debug_info['text_tracks'])}")
            print(f"      IFrames found: {len(debug_info['iframes'])}")

            print(f"    Looking for transcript/caption buttons...")
            ui_elements = new_page.evaluate("""() => {
                const results = {
                    transcript_buttons: [],
                    captions_buttons: [],
                };
                
                const allElements = document.querySelectorAll('button, a, div[role="button"], [aria-label], [aria-controls]');
                allElements.forEach(el => {
                    const text = (el.textContent || '').toLowerCase();
                    const ariaLabel = (el.getAttribute('aria-label') || '').toLowerCase();
                    const ariaControls = (el.getAttribute('aria-controls') || '').toLowerCase();
                    
                    if (text.includes('transcript') || ariaLabel.includes('transcript') || ariaControls.includes('transcript')) {
                        results.transcript_buttons.push({
                            tag: el.tagName,
                            text: el.textContent?.trim().substring(0, 50),
                            ariaLabel: el.getAttribute('aria-label'),
                            ariaControls: el.getAttribute('aria-controls'),
                            className: el.className,
                            visible: el.offsetParent !== null,
                        });
                    }
                    
                    if (text.includes('cc') || text.includes('captions') || text.includes('subtitle') || 
                        ariaLabel.includes('cc') || ariaLabel.includes('captions')) {
                        results.captions_buttons.push({
                            tag: el.tagName,
                            text: el.textContent?.trim().substring(0, 50),
                            ariaLabel: el.getAttribute('aria-label'),
                            ariaControls: el.getAttribute('aria-controls'),
                            className: el.className,
                            visible: el.offsetParent !== null,
                        });
                    }
                });
                
                return results;
            }""")
            debug_info["transcript_buttons"] = ui_elements.get("transcript_buttons", [])
            debug_info["captions_buttons"] = ui_elements.get("captions_buttons", [])
            print(f"      Transcript buttons: {len(debug_info['transcript_buttons'])}")
            print(f"      Caption/CC buttons: {len(debug_info['captions_buttons'])}")

            for btn in debug_info["transcript_buttons"][:3]:
                print(
                    f"        - {btn['tag']}: {btn['text']} (visible: {btn['visible']})"
                )
            for btn in debug_info["captions_buttons"][:3]:
                print(
                    f"        - {btn['tag']}: {btn['text']} (visible: {btn['visible']})"
                )

            print(f"    Looking for player config/data...")
            player_data = new_page.evaluate("""() => {
                const data = {
                    entryId: null,
                    mediaId: null,
                    captions: [],
                };
                
                const scripts = document.querySelectorAll('script');
                for (const script of scripts) {
                    const content = script.textContent || '';
                    
                    const entryMatch = content.match(/entryId["']?\\s*:\\s*["']?([a-z0-9_]+)/i);
                    if (entryMatch) data.entryId = entryMatch[1];
                    
                    const mediaMatch = content.match(/mediaId["']?\\s*:\\s*["']?([a-z0-9_]+)/i);
                    if (mediaMatch) data.mediaId = mediaMatch[1];
                    
                    const captionsMatch = content.match(/"captions"\\s*:\\s*\\[(.*?)\\]/);
                    if (captionsMatch) {
                        data.captions = [captionsMatch[1]];
                    }
                }
                
                return data;
            }""")
            debug_info["player_config"] = player_data
            print(f"      Entry ID: {player_data.get('entryId')}")
            print(f"      Media ID: {player_data.get('mediaId')}")
            print(f"      Captions in config: {len(player_data.get('captions', []))}")

            if (
                debug_info["text_tracks"]
                or debug_info["transcript_buttons"]
                or debug_info["captions_buttons"]
            ):
                debug_info["suggested_actions"].append(
                    "Transcript/caption UI elements found - may need to click button first"
                )
            elif (
                debug_info["network_urls"]["vtt"]
                or debug_info["network_urls"]["caption"]
            ):
                debug_info["suggested_actions"].append(
                    "Caption URLs found in network - can fetch directly"
                )
            elif player_data.get("captions"):
                debug_info["suggested_actions"].append(
                    "Captions found in player config"
                )
            else:
                debug_info["suggested_actions"].append(
                    "No transcript/caption sources found - may not be available for this video"
                )

            new_page.close()
        else:
            print(f"    Direct link - not implemented in debug mode")

    except Exception as e:
        debug_info["error"] = str(e)
        print(f"    Error: {e}")

    return debug_info


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Kaltura Transcript Extractor")
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug mode for one video"
    )
    args = parser.parse_args()

    debug_mode = args.debug

    print("=" * 60)
    if debug_mode:
        print("Kaltura Transcript Extractor - DEBUG MODE")
    else:
        print("Kaltura Transcript Extractor")
    print("=" * 60)

    # Create transcripts directory
    TRANSCRIPTS_DIR.mkdir(exist_ok=True)

    # Load links from previous extraction
    if not LINKS_FILE.exists():
        print(f"Error: {LINKS_FILE} not found!")
        print("Please run main.py first to extract links from Canvas.")
        sys.exit(1)

    with open(LINKS_FILE, "r") as f:
        links_data = json.load(f)

    # Filter for Kaltura links
    kaltura_links = [
        link
        for link in links_data.get("links", [])
        if link.get("video_provider") == "kaltura"
    ]

    if not kaltura_links:
        print("No Kaltura video links found in links_output.json")
        sys.exit(0)

    print(f"\nFound {len(kaltura_links)} Kaltura video(s)")

    # Load session or prompt for login
    from playwright.sync_api import sync_playwright

    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()

    # Try to load session
    session_loaded = False
    if SESSION_FILE.exists():
        print(f"\nLoading saved session from {SESSION_FILE}")
        session_loaded = load_session(context, SESSION_FILE)
        if not session_loaded:
            print("Session expired or invalid. Please log in again.")

    if not session_loaded:
        print("\n--- Login Required ---")
        print("Please log into Canvas in the browser window.")

        # Navigate to Canvas to trigger login
        page = context.new_page()
        page.goto("https://<school>.instructure.com")

        success = wait_for_canvas_page(page)
        if not success:
            print("Login timeout. Please try again.")
            browser.close()
            playwright.stop()
            sys.exit(1)

        # Save session
        cookies = context.cookies()
        with open(SESSION_FILE, "w") as f:
            json.dump(cookies, f)
        print("Session saved.")
        page.close()

    if debug_mode:
        print("\n" + "=" * 60)
        print("DEBUG MODE: Analyzing first video only")
        print("=" * 60)

        page = context.new_page()
        debug_result = debug_kaltura_video(page, kaltura_links[0], context, browser)

        debug_file = TRANSCRIPTS_DIR / "kaltura_debug.json"
        with open(debug_file, "w", encoding="utf-8") as f:
            json.dump(debug_result, f, indent=2)

        print(f"\n--- Debug Summary ---")
        print(f"Saved to: {debug_file}")
        print(f"Final URL: {debug_result.get('final_url')}")
        print(f"Frames: {len(debug_result.get('frames', []))}")
        print(f"Text tracks: {len(debug_result.get('text_tracks', []))}")
        print(f"Transcript buttons: {len(debug_result.get('transcript_buttons', []))}")
        print(f"Caption buttons: {len(debug_result.get('captions_buttons', []))}")

        for action in debug_result.get("suggested_actions", []):
            print(f"  → {action}")

        try:
            input("\nPress Enter to close browser...")
        except (EOFError, KeyboardInterrupt):
            pass
        browser.close()
        playwright.stop()
        return

    # Process each Kaltura link
    results = []
    page = context.new_page()

    for i, link in enumerate(kaltura_links, 1):
        print(f"\n[{i}/{len(kaltura_links)}] Processing video...")

        result = process_kaltura_link(page, link, context, browser)
        results.append(result)

        # Save transcript if found
        if result["transcript_found"] and result.get("transcript_text"):
            safe_name = sanitize_filename(result["title"])
            transcript_file = TRANSCRIPTS_DIR / f"{safe_name}.txt"

            # Avoid overwriting if file exists
            counter = 1
            base_name = str(transcript_file)
            while transcript_file.exists():
                stem = Path(base_name).stem
                ext = Path(base_name).suffix
                transcript_file = TRANSCRIPTS_DIR / f"{stem}_{counter}{ext}"
                counter += 1

            with open(transcript_file, "w", encoding="utf-8") as f:
                f.write(result["transcript_text"])
            print(f"    ✓ Saved transcript to: {transcript_file.name}")
        else:
            print(f"    ✗ No transcript text to save")

        # Small delay between videos
        if i < len(kaltura_links):
            time.sleep(2)

    page.close()

    # Save metadata
    metadata_output = {
        "total_videos": len(results),
        "transcripts_found": sum(1 for r in results if r["transcript_found"]),
        "videos": results,
    }

    with open(METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(metadata_output, f, indent=2)

    print(f"\n{'=' * 60}")
    print("Summary")
    print(f"{'=' * 60}")
    print(f"Total videos processed: {len(results)}")
    print(f"Transcripts found: {metadata_output['transcripts_found']}")
    print(f"Metadata saved to: {METADATA_FILE}")
    print(f"Transcripts saved to: {TRANSCRIPTS_DIR}/")

    print("\n--- Results ---")
    for result in results:
        status = "✓" if result["transcript_found"] else "✗"
        print(f"  {status} {result['title'][:50]}...")
        if result["transcript_source_type"]:
            print(f"      Source: {result['transcript_source_type']}")
        for error in result.get("errors", []):
            print(f"      Error: {error}")

    try:
        input("\nPress Enter to close browser...")
    except (EOFError, KeyboardInterrupt):
        pass
    browser.close()
    playwright.stop()


if __name__ == "__main__":
    main()
