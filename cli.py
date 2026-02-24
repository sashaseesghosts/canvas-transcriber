#!/usr/bin/env python3
"""
canvas-transcriber — CLI entry point
=====================================
Extract transcripts from Canvas/Kaltura lecture videos.

Usage
-----
    python cli.py extract-page  <canvas-page-url>  [options]
    python cli.py crawl-course  <canvas-modules-url> [options]
    python cli.py extract-video [options]

Run ``python cli.py <command> --help`` for per-command options.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from config import HEADLESS, LINKS_FILE, LOGIN_TIMEOUT, OUTPUT_DIR, SESSION_FILE
from extractor import extract_links_from_modules_page, extract_links_from_page
from login import load_session, save_session, wait_for_canvas_login
from transcript_kaltura import (
    debug_kaltura_video,
    process_kaltura_link,
    sanitize_filename,
)


# ---------------------------------------------------------------------------
# Browser helpers
# ---------------------------------------------------------------------------

def _launch_browser(headless: bool = False):
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=headless)
    context = browser.new_context()
    return playwright, browser, context


def _authenticate(context, page, session_file: Path, login_timeout: int) -> bool:
    """Load saved session or wait for the user to complete SSO/MFA.

    Returns True when the browser is authenticated, False on failure.
    """
    if session_file.exists():
        ok = load_session(context, session_file)
        if ok:
            print(f"Loaded session from {session_file}")
            return True
        print("Saved session is expired or invalid — please log in again.")

    print("\n--- Login required ---")
    print("Please log in to Canvas in the browser window.")
    ok = wait_for_canvas_login(page, timeout=login_timeout)
    if not ok:
        return False
    save_session(context, session_file)
    return True


# ---------------------------------------------------------------------------
# Subcommand: extract-page / crawl-course
# ---------------------------------------------------------------------------

def cmd_extract_links(
    url: str,
    output: Path,
    session_file: Path,
    headless: bool,
    login_timeout: int,
) -> None:
    """Extract video links from a Canvas page or course and save to JSON."""
    playwright, browser, context = _launch_browser(headless=headless)
    try:
        page = context.new_page()
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")

        needs_login = any(
            k in page.url.lower() for k in ("login", "sso", "saml")
        )
        if needs_login:
            ok = _authenticate(context, page, session_file, login_timeout)
            if not ok:
                print("Authentication failed.")
                sys.exit(1)
            page.goto(url)
            page.wait_for_load_state("domcontentloaded")
        elif session_file.exists():
            load_session(context, session_file)

        print(f"\nPage:  {page.title()}")
        print(f"URL:   {page.url}")

        # Auto-detect modules page vs single page
        if "/modules" in page.url and "/modules/items" not in page.url:
            print("\n--- Modules page detected — crawling all module items ---")
            links = extract_links_from_modules_page(page, context)
        else:
            print("\n--- Extracting links from page ---")
            links = extract_links_from_page(page)

        output.parent.mkdir(parents=True, exist_ok=True)
        result = {
            "page_url": url,
            "page_title": page.title(),
            "links": links,
            "total_links": len(links),
            "video_links_count": sum(1 for l in links if l.get("video_provider")),
        }
        with open(output, "w") as f:
            json.dump(result, f, indent=2)

        print(f"\nTotal links:   {result['total_links']}")
        print(f"Video links:   {result['video_links_count']}")
        print(f"Saved to:      {output}")

        kaltura_n = sum(1 for l in links if l.get("video_provider") == "kaltura")
        if kaltura_n:
            print(f"\n{kaltura_n} Kaltura video(s) found.")
            print("Run:  python cli.py extract-video")

    finally:
        try:
            input("\nPress Enter to close the browser...")
        except (EOFError, KeyboardInterrupt):
            pass
        browser.close()
        playwright.stop()


# ---------------------------------------------------------------------------
# Subcommand: extract-video
# ---------------------------------------------------------------------------

def cmd_extract_video(
    links_file: Path,
    output_dir: Path,
    session_file: Path,
    headless: bool,
    login_timeout: int,
    debug: bool,
    retry_failed: bool,
) -> None:
    """Extract transcripts for all Kaltura videos in *links_file*."""
    if not links_file.exists():
        print(f"Error: {links_file} not found.")
        print("Run 'python cli.py extract-page <url>' or 'python cli.py crawl-course <url>' first.")
        sys.exit(1)

    with open(links_file) as f:
        links_data = json.load(f)

    kaltura_links = [
        l for l in links_data.get("links", [])
        if l.get("video_provider") == "kaltura"
    ]
    if not kaltura_links:
        print("No Kaltura video links found in links file.")
        sys.exit(0)

    metadata_file = output_dir / "metadata.json"

    if retry_failed and metadata_file.exists():
        with open(metadata_file) as f:
            prev = json.load(f)
        failed_urls = {
            v["source_url"] for v in prev.get("videos", [])
            if not v.get("transcript_found")
        }
        kaltura_links = [l for l in kaltura_links if l["href"] in failed_urls]
        print(f"Retrying {len(kaltura_links)} failed video(s) from previous run")
    else:
        print(f"Found {len(kaltura_links)} Kaltura video(s)")

    output_dir.mkdir(parents=True, exist_ok=True)

    # Base Canvas URL for login fallback
    canvas_url = links_data.get("page_url", "")

    playwright, browser, context = _launch_browser(headless=headless)
    try:
        # Authenticate
        if session_file.exists():
            ok = load_session(context, session_file)
            if ok:
                print(f"Loaded session from {session_file}")
            else:
                print("Saved session expired.")
                ok = False
        else:
            ok = False

        if not ok and canvas_url:
            login_page = context.new_page()
            print(f"Opening Canvas for login: {canvas_url}")
            login_page.goto(canvas_url)
            ok = wait_for_canvas_login(login_page, timeout=login_timeout)
            if not ok:
                print("Login timed out.")
                sys.exit(1)
            save_session(context, session_file)
            login_page.close()

        # Debug mode: deep-inspect first video only
        if debug:
            print("\n=== DEBUG MODE — analysing first video only ===")
            page = context.new_page()
            debug_result = debug_kaltura_video(page, kaltura_links[0], context, browser)
            debug_file = output_dir / "kaltura_debug.json"
            with open(debug_file, "w") as f:
                json.dump(debug_result, f, indent=2)
            print(f"\nDebug info saved to: {debug_file}")
            _print_debug_summary(debug_result)
            return

        # Normal extraction
        results: list[dict] = []
        page = context.new_page()

        for i, link in enumerate(kaltura_links, 1):
            print(f"\n[{i}/{len(kaltura_links)}]")
            result = process_kaltura_link(page, link, context, browser)
            results.append(result)

            if result["transcript_found"] and result.get("transcript_text"):
                # Place transcript in a module subdirectory when available
                module_name = link.get("module_name", "")
                subdir = output_dir / _safe_dir_name(module_name) if module_name else output_dir
                subdir.mkdir(parents=True, exist_ok=True)

                safe_name = sanitize_filename(result["title"])
                transcript_file = subdir / f"{safe_name}.txt"
                counter = 1
                while transcript_file.exists():
                    transcript_file = subdir / f"{safe_name}_{counter}.txt"
                    counter += 1

                with open(transcript_file, "w", encoding="utf-8") as f:
                    f.write(result["transcript_text"])

                result["transcript_path"] = str(transcript_file.relative_to(output_dir))
                result["transcript_preview"] = result["transcript_text"][:200]
                print(f"    ✓ Saved: {result['transcript_path']}")
            else:
                result["transcript_path"] = None
                result["transcript_preview"] = None

            if i < len(kaltura_links):
                time.sleep(1)

        page.close()

        # Write metadata — omit full transcript_text to keep the file compact
        meta_videos = [
            {k: v for k, v in r.items() if k != "transcript_text"}
            for r in results
        ]
        metadata = {
            "total_videos": len(results),
            "transcripts_found": sum(1 for r in results if r["transcript_found"]),
            "videos": meta_videos,
        }
        with open(metadata_file, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

        # Summary
        print(f"\n{'=' * 50}")
        print(f"Videos processed:   {metadata['total_videos']}")
        print(f"Transcripts found:  {metadata['transcripts_found']}")
        print(f"Output directory:   {output_dir}")
        print(f"Metadata:           {metadata_file}")
        for r in results:
            status = "✓" if r["transcript_found"] else "✗"
            print(f"  {status} {r['title'][:60]}")
            for e in r.get("errors", []):
                print(f"      ! {e}")

    finally:
        try:
            input("\nPress Enter to close the browser...")
        except (EOFError, KeyboardInterrupt):
            pass
        browser.close()
        playwright.stop()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_dir_name(name: str) -> str:
    s = re.sub(r"[/:&,]", " ", name)
    s = re.sub(r"\s+", "_", s.strip())
    return re.sub(r"_+", "_", s).strip("_")


def _print_debug_summary(d: dict) -> None:
    print(f"Final URL:          {d.get('final_url')}")
    print(f"Page title:         {d.get('page_title')}")
    print(f"Frames:             {len(d.get('frames', []))}")
    for fr in d.get("frames", []):
        n = len(fr.get("transcript_caption_elements", []))
        tag = f"  [{n} element(s)]" if n else ""
        print(f"  {fr['url'][:80]}{tag}")
    net = d.get("network_urls", {})
    print(
        f"Network  vtt={len(net.get('vtt', []))} "
        f"srt={len(net.get('srt', []))} "
        f"caption={len(net.get('caption', []))} "
        f"kaltura={len(net.get('kaltura', []))}"
    )
    print(f"Text tracks:        {len(d.get('text_tracks', []))}")
    print(f"Transcript buttons: {len(d.get('transcript_buttons', []))}")
    cfg = d.get("player_config") or {}
    print(f"Entry ID: {cfg.get('entryId')}  Media ID: {cfg.get('mediaId')}")
    cap_urls = cfg.get("captionUrls", [])
    if cap_urls:
        print(f"Caption URLs in scripts: {len(cap_urls)}")
        for u in cap_urls[:3]:
            print(f"  {u[:90]}")
    print("\nSuggested actions:")
    for a in d.get("suggested_actions", []):
        print(f"  -> {a}")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _shared_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--session-file", type=Path, default=SESSION_FILE, metavar="PATH",
        help=f"Session cookie file (default: {SESSION_FILE})",
    )
    p.add_argument(
        "--headless", action="store_true", default=HEADLESS,
        help="Run browser in headless mode (default: false)",
    )
    p.add_argument(
        "--login-timeout", type=int, default=LOGIN_TIMEOUT, metavar="SECS",
        help=f"Seconds to wait for SSO/MFA login (default: {LOGIN_TIMEOUT})",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="canvas-transcriber",
        description="Extract transcripts from Canvas/Kaltura lecture videos.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
commands:
  extract-page   Extract video links from a single Canvas page
  crawl-course   Crawl all module items in a Canvas course (/modules URL)
  extract-video  Download transcripts for detected Kaltura videos

environment variables (or .env file):
  CT_SESSION_FILE   Cookie file path        (default: session.json)
  CT_LINKS_FILE     Extracted links file    (default: links_output.json)
  CT_OUTPUT_DIR     Transcript directory    (default: transcripts)
  CT_LOGIN_TIMEOUT  SSO wait in seconds     (default: 180)
  CT_HEADLESS       Headless browser        (default: false)
""",
    )

    subs = parser.add_subparsers(dest="command", metavar="<command>")
    subs.required = True

    # extract-page
    p_ep = subs.add_parser(
        "extract-page",
        help="Extract video links from a single Canvas page",
    )
    _shared_args(p_ep)
    p_ep.add_argument("url", help="Canvas page URL")
    p_ep.add_argument(
        "--output", type=Path, default=LINKS_FILE, metavar="FILE",
        help=f"Output JSON file (default: {LINKS_FILE})",
    )

    # crawl-course
    p_cc = subs.add_parser(
        "crawl-course",
        help="Crawl all module items in a Canvas course",
    )
    _shared_args(p_cc)
    p_cc.add_argument("url", help="Canvas /modules page URL")
    p_cc.add_argument(
        "--output", type=Path, default=LINKS_FILE, metavar="FILE",
        help=f"Output JSON file (default: {LINKS_FILE})",
    )

    # extract-video
    p_ev = subs.add_parser(
        "extract-video",
        help="Download transcripts for Kaltura videos in the links file",
    )
    _shared_args(p_ev)
    p_ev.add_argument(
        "--links-file", type=Path, default=LINKS_FILE, metavar="FILE",
        help=f"Links JSON produced by extract-page/crawl-course (default: {LINKS_FILE})",
    )
    p_ev.add_argument(
        "--output-dir", type=Path, default=OUTPUT_DIR, metavar="DIR",
        help=f"Transcript output directory (default: {OUTPUT_DIR})",
    )
    p_ev.add_argument(
        "--debug", action="store_true",
        help="Deep-inspect first video and save kaltura_debug.json",
    )
    p_ev.add_argument(
        "--retry-failed", action="store_true",
        help="Retry only videos that failed in the previous run",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command in ("extract-page", "crawl-course"):
        cmd_extract_links(
            url=args.url,
            output=args.output,
            session_file=args.session_file,
            headless=args.headless,
            login_timeout=args.login_timeout,
        )
    elif args.command == "extract-video":
        cmd_extract_video(
            links_file=args.links_file,
            output_dir=args.output_dir,
            session_file=args.session_file,
            headless=args.headless,
            login_timeout=args.login_timeout,
            debug=args.debug,
            retry_failed=args.retry_failed,
        )


if __name__ == "__main__":
    main()
